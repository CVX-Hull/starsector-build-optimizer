"""Honest evaluator — re-score a campaign's top builds against the closed
opponent population with a transform-free oracle scorer.

Standing rule: invoked after every major optimization run before any report
publishes findings. Spec contract: docs/specs/30-honest-evaluator.md.
Methodology: docs/reference/honest-evaluation-methodology.md. SOP:
.claude/skills/honest-evaluation.md.
"""

from __future__ import annotations

import json
import logging
import math
import os
import signal
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .campaign import (
    check_ami_tags_against_manifest, check_authkey_syntax,
    check_aws_credentials, load_campaign_config, _flush_stale_campaign_keys,
)
from .cloud_provider import AWSProvider
from .cloud_runner import prepare_cloud_pool
from .combat_fitness import combat_fitness
from .evaluator_pool import EvaluatorPool
from .game_manifest import GameManifest
from .models import (
    Build,
    BuildSpec,
    CombatResult,
    HonestEvaluationConfig,
    MatchupConfig,
    CampaignConfig,
    ShipHull,
)
from .opponent_pool import discover_opponent_pool, get_opponents
from .parser import GameData, load_game_data
from .repair import repair_build

logger = logging.getLogger(__name__)

HONEST_EVAL_SCHEMA_VERSION = 1

# Ledger schema version — bump when ledger entry shape changes so resume
# code can refuse to mix old + new entries silently.
LEDGER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LedgerEntry:
    """One completed matchup, persisted to the append-only ledger.

    The triple `(build_id, opponent_variant_id, replicate_idx)` is the
    resume key — a future run with the same eval_tag skips matchups
    whose key already appears here. `fitness` is the
    `combat_fitness(result)` scalar; replaying the ledger reconstructs
    the in-memory `scores_per_build` dict without needing the worker
    fleet again.
    """
    schema_version: int
    matchup_id: str
    build_id: str
    opponent_variant_id: str
    replicate_idx: int
    fitness: float
    completed_at: str


def _ledger_dir(out_root: Path, eval_tag: str) -> Path:
    return out_root / "honest_eval" / eval_tag


def _ledger_path(out_root: Path, eval_tag: str) -> Path:
    return _ledger_dir(out_root, eval_tag) / "results.jsonl"


def _resume_key(build_id: str, opp: str, rep: int) -> tuple[str, str, int]:
    return (build_id, opp, rep)


def read_ledger(ledger_path: Path) -> dict[tuple[str, str, int], float]:
    """Parse `ledger_path` if it exists; return a {(build_id, opp, rep)
    → fitness} dict of completed matchups.

    Lines with the wrong schema_version are skipped with a warning;
    malformed lines raise — corruption is a data-integrity signal that
    must surface before resume rather than after.
    """
    if not ledger_path.exists():
        return {}
    completed: dict[tuple[str, str, int], float] = {}
    with ledger_path.open() as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"corrupt ledger line {ledger_path}:{lineno}: {exc}. "
                    f"Refusing to resume — investigate and either fix the "
                    f"line or move the ledger aside before re-running."
                ) from exc
            if data.get("schema_version") != LEDGER_SCHEMA_VERSION:
                logger.warning(
                    "ledger %s line %d: schema_version=%s (expected %d) — "
                    "skipping",
                    ledger_path, lineno,
                    data.get("schema_version"), LEDGER_SCHEMA_VERSION,
                )
                continue
            key = _resume_key(
                data["build_id"], data["opponent_variant_id"],
                int(data["replicate_idx"]),
            )
            completed[key] = float(data["fitness"])
    return completed


class _LedgerWriter:
    """Append-only JSONL writer with fsync per line. Mirrors the
    pattern in spec 22 §"Cost ledger" — torn-line risk is the failure
    mode this guards against, and the ~1 ms fsync overhead is
    negligible at honest-eval throughput (≪ 96 rows/min)."""

    def __init__(self, ledger_path: Path) -> None:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = ledger_path
        self._lock = threading.Lock()

    def append(self, entry: LedgerEntry) -> None:
        line = json.dumps(asdict(entry)) + "\n"
        with self._lock, self._path.open("a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


@dataclass(frozen=True)
class _BuildWithProvenance:
    """Internal — pairs a Build with the source-DB metadata."""
    build: Build
    source_campaign: str
    source_study_idx: int
    source_seed_idx: int
    source_rank: int
    source_value: float


# Synthetic source_campaign name for random-feasible baseline builds.
# Per auditor C (2026-05-10): without a baseline, even successful
# Wave 1 cell rankings can't answer the existence question — does ANY
# of the optimization machinery beat random feasible sampling? The
# baseline cell tags its builds with this name so summarize_by_cell
# treats it as just another cell in the per-cell table.
RANDOM_BASELINE_SOURCE_CAMPAIGN = "random-baseline"


def synthesize_random_baseline_builds(
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    n: int,
    seed: int = 0,
    regime: str = "early",
) -> tuple[_BuildWithProvenance, ...]:
    """Generate `n` random feasible builds via `generate_random_build` +
    `repair_build`, tagged with `source_campaign=RANDOM_BASELINE_SOURCE_CAMPAIGN`
    so they ride alongside campaign-derived cells through `evaluate_builds`
    and `summarize_by_cell`.

    Deterministic in `seed`: re-running honest-eval with the same seed
    produces the same baseline builds, so the ledger's resume
    contract still holds.
    """
    import numpy as np
    from .calibration import generate_random_build
    from .models import REGIME_PRESETS
    rng = np.random.default_rng(seed)
    regime_cfg = REGIME_PRESETS[regime]
    out: list[_BuildWithProvenance] = []
    for i in range(n):
        b = generate_random_build(
            hull, game_data, manifest, rng=rng, regime=regime_cfg,
        )
        out.append(_BuildWithProvenance(
            build=b,
            source_campaign=RANDOM_BASELINE_SOURCE_CAMPAIGN,
            source_study_idx=0,
            source_seed_idx=seed,
            source_rank=i + 1,
            source_value=float("nan"),  # no within-cell scoring exists
        ))
    return tuple(out)


@dataclass(frozen=True)
class EvaluatedBuild:
    build: Build
    source_campaign: str
    source_study_idx: int
    source_seed_idx: int
    source_rank: int
    source_value: float
    oracle_score: float
    oracle_se: float
    n_matchups_succeeded: int


@dataclass(frozen=True)
class CellSummary:
    cell_name: str
    n_builds_evaluated: int
    mean_top_k_oracle: float
    best_build_oracle: float
    best_build_se: float


@dataclass(frozen=True)
class HonestEvaluationResult:
    schema_version: int
    evaluated_builds: tuple[EvaluatedBuild, ...]
    cell_summaries: tuple[CellSummary, ...]
    pool_variant_ids: tuple[str, ...]
    pool_size: int
    config: HonestEvaluationConfig
    started_at: str
    finished_at: str


# ---- Public API --------------------------------------------------------------


RANKING_METHODS = ("twfe_eb", "twfe", "raw_mean", "bradley_terry")


def _build_rankers():
    """Single source of truth for `method` → ranker function. Imported
    lazily to avoid pulling scipy at module-load time for callers that
    only use evaluate_builds / summarize_by_cell."""
    from .posthoc_ranker import (
        rank_bradley_terry, rank_raw_mean, rank_twfe, rank_twfe_eb,
    )
    return {
        "raw_mean":      rank_raw_mean,
        "twfe":          rank_twfe,
        "twfe_eb":       rank_twfe_eb,
        "bradley_terry": rank_bradley_terry,
    }


def extract_top_builds(
    eval_log_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    top_k: int,
    *,
    method: str = "twfe_eb",
) -> tuple[tuple[int, float, Build], ...]:
    """Read per-study `evaluation_log.jsonl`, return (rank, score, Build) for
    top_k completed trials under the chosen ranking estimator.

    **Default = `twfe_eb`** (TWFE deconfounding + EB shrinkage on residuals).
    `posthoc_ranker` reuses `deconfounding.twfe_decompose` + `eb_shrinkage`,
    so the post-hoc estimator matches the online phase5a/5d pipeline.

    **Why JSONL, not SQLite.** TWFE / EB / Bradley–Terry all need the
    (build × opponent) score matrix. SQLite's `trial.intermediate_values`
    keys steps by opaque step-index, which loses the opponent identity
    needed to deconfound. The JSONL row carries `opponent_results`
    (opponent id + winner + hp_differential per match) — the only data
    source that supports principled post-hoc ranking. See
    `docs/reports/2026-05-10-posthoc-ranker-research.md` for the rationale
    and the empirical comparison that motivated this switch.

    **Why not raw mean (the prior default).** Raw mean has 0/5 top-5
    overlap with TWFE/EB/BT on Wave 1 (pooled and per-cell). The bias
    comes from opponent confounding: TPE+pruner schedules different
    builds against different opponent subsets, so per-trial means are
    contaminated by which opponents a build happened to face. `raw_mean`
    remains available as a `method=` choice for ablation/diagnostic.

    Args:
        eval_log_path: Path to `evaluation_log.jsonl` for a single
            `(hull, regime, sampler, seed)` study (one per study).
        method: One of RANKING_METHODS. Default `twfe_eb`.

    Raises:
        ValueError: top_k < 1, or fewer than top_k completed trials.
        FileNotFoundError: log path does not exist.
        RuntimeError: a logged build fails `repair_build` — stale build
            spec is a data-corruption signal (spec 30 §Error conditions).
    """
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    if method not in RANKING_METHODS:
        raise ValueError(
            f"unknown ranking method {method!r}; pick one of {RANKING_METHODS}"
        )
    if not eval_log_path.exists():
        raise FileNotFoundError(
            f"no evaluation_log.jsonl at {eval_log_path}. "
            f"Wave 1 logs must be migrated via "
            f"`scripts/migrate_wave1_eval_logs.py` before honest-eval; "
            f"Wave 2+ writes per-study logs natively (task #90)."
        )

    from .posthoc_ranker import load_records
    records = load_records([eval_log_path])
    if len(records) < top_k:
        raise ValueError(
            f"{eval_log_path.name}: only {len(records)} completed trial(s); "
            f"top_k={top_k}"
        )
    ranked = _build_rankers()[method](records, k=top_k)

    out: list[tuple[int, float, Build]] = []
    for rank, rb in enumerate(ranked, start=1):
        raw = rb.raw_build
        try:
            # The optimizer logs *post-repair* builds, so the JSONL spec
            # is already feasible. Re-run repair_build defensively to
            # catch search-space drift / manifest changes between the
            # campaign run and honest-eval (e.g. a hullmod went rare).
            candidate = Build(
                hull_id=raw["hull_id"],
                weapon_assignments=dict(raw["weapon_assignments"]),
                hullmods=frozenset(raw["hullmods"]),
                flux_vents=int(raw["flux_vents"]),
                flux_capacitors=int(raw["flux_capacitors"]),
            )
            repaired = repair_build(candidate, hull, game_data, manifest)
        except Exception as exc:
            raise RuntimeError(
                f"{eval_log_path.name}: rank {rank} build "
                f"({rb.build_id.short}, score={rb.score:.4f}) failed "
                f"repair_build: {exc}. This is a data-corruption signal "
                f"(search-space drift or repair regression). Investigate "
                f"before re-running honest eval — silently skipping would "
                f"alter 'top-k' meaning and break cross-cell comparison."
            ) from exc
        out.append((rank, float(rb.score), repaired))
    return tuple(out)


def report_method_disagreement(
    eval_log_path: Path,
    top_k: int,
    methods: tuple[str, ...] = RANKING_METHODS,
) -> dict[str, list[str]]:
    """Diagnostic: top-K build-hash list under each estimator. Logs a WARN
    when methods disagree on the top-K so the operator notices before
    spending money on the oracle pass. Used by `main()` pre-dispatch.

    Returns: `{method_name: [build_hash_short, ...]}` — same length per
    method, easy to diff visually in logs.
    """
    from .posthoc_ranker import load_records
    rankers = _build_rankers()
    records = load_records([eval_log_path])
    out = {}
    for m in methods:
        out[m] = [rb.build_id.short for rb in rankers[m](records, k=top_k)]
    return out


def discover_evaluation_pool(
    game_dir: Path,
    game_data: GameData,
    hull: ShipHull,
) -> tuple[str, ...]:
    """Return all stock variant ids of compatible (= same) hull-size for
    `hull`. Pure composition of existing primitives (spec 23).
    """
    pool = discover_opponent_pool(game_dir, game_data)
    return get_opponents(pool, hull.hull_size)


def evaluate_builds(
    builds_with_provenance: Sequence[_BuildWithProvenance],
    eval_pool: tuple[str, ...],
    pool: EvaluatorPool,
    config: HonestEvaluationConfig,
    hull: ShipHull,
    ledger_path: Path | None = None,
) -> tuple[EvaluatedBuild, ...]:
    """Dispatch every (build × opp × replicate) matchup, retry failures up to
    config.max_retries_per_matchup, aggregate per-build mean fitness.

    If `ledger_path` is provided, every successful matchup result is
    appended to the JSONL ledger with `flush()` + `os.fsync()` before we
    move on. On entry, an existing ledger is replayed: matchups already
    present skip dispatch and their stored fitness folds straight into
    the in-memory aggregation. This makes a SIGTERM / OOM / network
    partition mid-run survivable — the operator re-runs with the same
    eval_tag (via `--resume-from`) and only the missing matchups
    re-dispatch.

    Raises:
        ValueError: empty eval_pool, or empty builds_with_provenance.
        RuntimeError: a matchup failed after all retries, or the ledger
            references a build_id that doesn't appear in
            `builds_with_provenance` (a sign that --top-k or the
            campaign DBs changed between runs).
    """
    if not eval_pool:
        raise ValueError("eval_pool is empty — no compatible opponents")
    if not builds_with_provenance:
        raise ValueError("no builds to evaluate")

    # Build the work list. matchup_id format `{build_id}_vs_{opp}_rep{N}`
    # — the `_rep{N}` suffix is mandatory (spec 30 §Matchup-id uniqueness).
    # build_id encodes provenance so failures are traceable to the source DB.
    @dataclass(frozen=True)
    class _Job:
        build_idx: int  # index into builds_with_provenance
        opp: str
        rep: int
        attempt: int = 0

    def _build_id(bi: int) -> str:
        bp = builds_with_provenance[bi]
        return (
            f"honest__{bp.source_campaign}__s{bp.source_study_idx}"
            f"__seed{bp.source_seed_idx}__rank{bp.source_rank}"
        )

    # Replay the ledger: skip jobs already completed and pre-populate
    # scores_per_build with their fitnesses. A ledger entry whose
    # build_id no longer maps to any current build is a strong signal
    # that --top-k or the campaign DBs changed between runs — refuse to
    # silently mix old + new scores.
    completed_from_ledger: dict[tuple[str, str, int], float] = {}
    if ledger_path is not None:
        completed_from_ledger = read_ledger(ledger_path)
    build_id_to_idx = {_build_id(bi): bi for bi in range(len(builds_with_provenance))}
    scores_per_build: dict[int, list[float]] = {
        i: [] for i in range(len(builds_with_provenance))
    }
    for (bid, _opp, _rep), fit in completed_from_ledger.items():
        if bid not in build_id_to_idx:
            raise RuntimeError(
                f"ledger {ledger_path} references unknown build_id "
                f"{bid!r}. The current run's --top-k or campaign DBs "
                f"differ from when the ledger was written. Refusing to "
                f"resume — move the ledger aside or re-run with the "
                f"original parameters."
            )
        scores_per_build[build_id_to_idx[bid]].append(float(fit))

    jobs: list[_Job] = []
    skipped_from_ledger = 0
    for bi, _ in enumerate(builds_with_provenance):
        bid = _build_id(bi)
        for opp in eval_pool:
            for rep in range(config.replicates_per_matchup):
                if (bid, opp, rep) in completed_from_ledger:
                    skipped_from_ledger += 1
                    continue
                jobs.append(_Job(build_idx=bi, opp=opp, rep=rep))
    if skipped_from_ledger:
        logger.info(
            "honest_eval: replaying %d completed matchups from ledger %s; "
            "%d new matchups to dispatch",
            skipped_from_ledger, ledger_path, len(jobs),
        )

    ledger_writer = (
        _LedgerWriter(ledger_path) if ledger_path is not None else None
    )

    def _make_matchup(job: _Job) -> MatchupConfig:
        bp = builds_with_provenance[job.build_idx]
        b = bp.build
        # Spec 27 BuildSpec → MatchupConfig.player_builds[0]
        spec = BuildSpec(
            variant_id=f"{_build_id(job.build_idx)}__variant",
            hull_id=hull.id,
            weapon_assignments={
                slot: wid for slot, wid in b.weapon_assignments.items()
                if wid is not None
            },
            hullmods=tuple(sorted(b.hullmods)),
            flux_vents=b.flux_vents,
            flux_capacitors=b.flux_capacitors,
            # cr defaults to 0.7 in BuildSpec (the harness CR convention).
        )
        matchup_id = (
            f"{_build_id(job.build_idx)}_vs_{job.opp}_rep{job.rep}"
        )
        return MatchupConfig(
            matchup_id=matchup_id,
            player_builds=(spec,),
            enemy_variants=(job.opp,),
            time_limit_seconds=config.matchup_time_limit_seconds,
        )

    num_workers = pool.num_workers
    if num_workers < 1:
        raise ValueError(
            f"EvaluatorPool.num_workers={num_workers} — refusing to dispatch"
        )
    logger.info(
        "honest_eval: %d builds × %d opponents × %d replicates = %d matchups, "
        "concurrency=%d, max_retries=%d",
        len(builds_with_provenance), len(eval_pool),
        config.replicates_per_matchup, len(jobs), num_workers,
        config.max_retries_per_matchup,
    )

    # Concurrent dispatch loop — mirrors optimizer.py:610-637 pattern.
    # On exception: increment attempt, requeue if attempt <= max_retries,
    # else raise (preserves balanced-design — spec 30 §Failure handling).
    pending: dict = {}
    queue = list(jobs)
    completed = 0
    total = len(jobs)

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        while queue or pending:
            while queue and len(pending) < num_workers:
                job = queue.pop(0)
                fut = executor.submit(pool.run_matchup, _make_matchup(job))
                pending[fut] = job
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                job = pending.pop(fut)
                try:
                    result: CombatResult = fut.result()
                except Exception as exc:
                    next_attempt = job.attempt + 1
                    if next_attempt > config.max_retries_per_matchup:
                        raise RuntimeError(
                            f"matchup {_build_id(job.build_idx)}_vs_{job.opp}"
                            f"_rep{job.rep} failed after "
                            f"{config.max_retries_per_matchup} retries: {exc}. "
                            f"Honest eval halts — silently excluding this "
                            f"matchup would break the balanced-design "
                            f"guarantee that justifies mean-fitness as the "
                            f"oracle. Investigate worker logs for this "
                            f"matchup_id before re-running."
                        ) from exc
                    logger.warning(
                        "matchup %s rep%d attempt %d/%d failed: %s — retrying",
                        _build_id(job.build_idx), job.rep, next_attempt,
                        config.max_retries_per_matchup, exc,
                    )
                    queue.append(_Job(
                        build_idx=job.build_idx, opp=job.opp,
                        rep=job.rep, attempt=next_attempt,
                    ))
                    continue
                fitness = combat_fitness(result, config=config.fitness_config)
                if ledger_writer is not None:
                    ledger_writer.append(LedgerEntry(
                        schema_version=LEDGER_SCHEMA_VERSION,
                        matchup_id=result.matchup_id,
                        build_id=_build_id(job.build_idx),
                        opponent_variant_id=job.opp,
                        replicate_idx=job.rep,
                        fitness=fitness,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    ))
                scores_per_build[job.build_idx].append(fitness)
                completed += 1
                log_every = max(1, total // config.progress_log_buckets)
                if completed % log_every == 0:
                    logger.info(
                        "honest_eval: %d/%d matchups complete (%.0f%%)",
                        completed, total, 100.0 * completed / total,
                    )

    # Aggregate per-build mean + SEM.
    out: list[EvaluatedBuild] = []
    expected_n = len(eval_pool) * config.replicates_per_matchup
    for bi, bp in enumerate(builds_with_provenance):
        scores = scores_per_build[bi]
        # Sanity: balanced design means every build must have exactly N
        # successful matchups (failures retry-or-raise).
        if len(scores) != expected_n:
            raise RuntimeError(
                f"build {_build_id(bi)}: got {len(scores)} matchup results, "
                f"expected {expected_n}. Internal accounting bug — failures "
                f"should retry-to-N."
            )
        mean = sum(scores) / len(scores)
        if len(scores) > 1:
            var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
            sem = math.sqrt(var / len(scores))
        else:
            sem = 0.0
        out.append(EvaluatedBuild(
            build=bp.build,
            source_campaign=bp.source_campaign,
            source_study_idx=bp.source_study_idx,
            source_seed_idx=bp.source_seed_idx,
            source_rank=bp.source_rank,
            source_value=bp.source_value,
            oracle_score=mean,
            oracle_se=sem,
            n_matchups_succeeded=len(scores),
        ))
    return tuple(out)


def summarize_by_cell(
    evaluated: Sequence[EvaluatedBuild],
) -> tuple[CellSummary, ...]:
    """Group EvaluatedBuild by source_campaign, compute per-cell summary.
    Returned tuple is ordered descending by mean_top_k_oracle.
    """
    if not evaluated:
        return ()
    by_cell: dict[str, list[EvaluatedBuild]] = {}
    for eb in evaluated:
        by_cell.setdefault(eb.source_campaign, []).append(eb)
    summaries: list[CellSummary] = []
    for cell, builds in by_cell.items():
        oracle_scores = [b.oracle_score for b in builds]
        mean_top_k = sum(oracle_scores) / len(oracle_scores)
        best = max(builds, key=lambda b: b.oracle_score)
        summaries.append(CellSummary(
            cell_name=cell,
            n_builds_evaluated=len(builds),
            mean_top_k_oracle=mean_top_k,
            best_build_oracle=best.oracle_score,
            best_build_se=best.oracle_se,
        ))
    summaries.sort(key=lambda s: s.mean_top_k_oracle, reverse=True)
    return tuple(summaries)


# ---- CLI entry-point ---------------------------------------------------------


def _serialize_build(b: Build) -> dict:
    """Build → JSON-safe dict (Build has frozenset hullmods → list)."""
    return {
        "hull_id": b.hull_id,
        "weapon_assignments": dict(b.weapon_assignments),
        "hullmods": sorted(b.hullmods),
        "flux_vents": b.flux_vents,
        "flux_capacitors": b.flux_capacitors,
    }


def _serialize_evaluated_build(eb: EvaluatedBuild) -> dict:
    d = asdict(eb)
    d["build"] = _serialize_build(eb.build)
    return d


def write_outputs(
    result: HonestEvaluationResult,
    out_root: Path,
) -> None:
    """Write per-campaign honest_eval.json + a cross-campaign summary.
    Does NOT write to docs/reports/ (per spec 30 §CLI entry point — reports
    are hand-authored).
    """
    by_cell: dict[str, list[EvaluatedBuild]] = {}
    for eb in result.evaluated_builds:
        by_cell.setdefault(eb.source_campaign, []).append(eb)

    cfg_dict = asdict(result.config)
    # CombatFitnessConfig is nested — already a dict via asdict.

    for cell, builds in by_cell.items():
        cell_dir = out_root / "campaigns" / cell
        cell_dir.mkdir(parents=True, exist_ok=True)
        out_path = cell_dir / "honest_eval.json"
        out_path.write_text(json.dumps({
            "schema_version": result.schema_version,
            "campaign": cell,
            "config": cfg_dict,
            "pool_variant_ids": list(result.pool_variant_ids),
            "pool_size": result.pool_size,
            "evaluated_builds": [_serialize_evaluated_build(b) for b in builds],
            "started_at": result.started_at,
            "finished_at": result.finished_at,
        }, indent=2))
        logger.info("wrote %s", out_path)

    summary_dir = out_root / "campaigns"
    summary_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    summary_path = summary_dir / f"honest_eval_summary_{today}.json"
    summary_path.write_text(json.dumps({
        "schema_version": result.schema_version,
        "cells": [asdict(s) for s in result.cell_summaries],
        "pool_size": result.pool_size,
        "config": cfg_dict,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }, indent=2))
    logger.info("wrote %s", summary_path)


def _resolve_honest_eval_flask_port(campaign) -> int:
    """Default flask port for honest-eval = highest port in the per-study
    ACL range, reserved so it cannot collide with any per-study allocation.

    The cloud-worker tailnet ACL grants `tcp:9000-9099` to workstation;
    this is the *only* range workers can POST to. Per-study ports start
    at `base_flask_port` and grow upward by `study_idx * flask_ports_per_study
    + seed_idx`. Picking the top of the range guarantees both that
    workers can reach honest-eval's listener AND that no realistic
    per-study allocation collides (would require a campaign with
    100+ studies).
    """
    return campaign.base_flask_port + campaign.flask_ports_per_study - 1


MAX_EVAL_TAG_LEN = 63
"""AWS Launch Template names cap at 128 chars; AWSProvider composes
resource names like `{project_tag}__{fleet_name}` (= 2 × eval_tag + 2).
2 × 63 + 2 = 128, so 63 is the hard ceiling. Lifted to a module
constant so spec 30 and the validator can't drift."""


def _validate_eval_tag_length(eval_tag: str) -> None:
    """Reject `eval_tag` values that would overflow AWS Launch Template
    names once AWSProvider doubles them. Cap = `MAX_EVAL_TAG_LEN`.
    """
    if len(eval_tag) > MAX_EVAL_TAG_LEN:
        raise ValueError(
            f"eval_tag {eval_tag!r} is {len(eval_tag)} chars > "
            f"{MAX_EVAL_TAG_LEN}; shorten the source campaign name "
            f"(`{eval_tag[:MAX_EVAL_TAG_LEN]}…` would overflow AWS "
            f"Launch Template name limits when doubled into "
            f"`{{project_tag}}__{{fleet_name}}`)."
        )


def _install_signal_handlers() -> None:
    """Route SIGTERM/SIGHUP through Python cleanup.

    SIGINT already raises KeyboardInterrupt. Without this, `kill <pid>` uses
    the process-default SIGTERM action and can bypass the `with
    prepare_cloud_pool(...)` unwinder, leaving AWS workers alive.
    """
    def handler(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, handler)


def _teardown_arg_for_eval_tag(eval_tag: str) -> str:
    """Argument to pass to scripts/cloud/teardown.sh/final_audit.sh.

    Those scripts prepend `starsector-` internally. Honest-eval `eval_tag`
    values are already full Project tag values, so operator-facing commands
    must strip exactly one leading prefix.
    """
    prefix = "starsector-"
    if eval_tag.startswith(prefix):
        return eval_tag[len(prefix):]
    return eval_tag


def _adjust_campaign_for_honest_eval(
    campaign: CampaignConfig,
    *,
    total_matchups: int,
    total_matchup_slots: int,
    config: HonestEvaluationConfig,
) -> CampaignConfig:
    """Return campaign config with honest-eval-safe cloud timing.

    Source campaign YAMLs are tuned for training cells. Honest eval has a
    different shape: one large oracle sweep, caller-level retry on
    WorkerTimeout, and ledger-based resume. Inheriting a short training
    worker lifetime or Redis visibility window can silently turn a healthy
    oracle sweep into a requeue/duplicate storm.
    """
    if total_matchup_slots < 1:
        raise ValueError(
            f"total_matchup_slots={total_matchup_slots}; cannot size "
            f"honest-eval worker lifetime"
        )
    full_timeout_wall_hours = (
        total_matchups
        * (config.matchup_time_limit_seconds / MatchupConfig.time_mult)
        / total_matchup_slots
        / 3600.0
    )
    required_lifetime = max(
        config.cloud_min_lifetime_hours,
        full_timeout_wall_hours * config.cloud_lifetime_headroom,
    )
    required_visibility = (
        campaign.result_timeout_seconds * (config.max_retries_per_matchup + 1)
        + campaign.janitor_interval_seconds
    )
    adjusted = replace(
        campaign,
        max_lifetime_hours=max(campaign.max_lifetime_hours, required_lifetime),
        visibility_timeout_seconds=max(
            campaign.visibility_timeout_seconds, required_visibility,
        ),
    )
    if adjusted != campaign:
        logger.info(
            "honest_eval cloud timing adjusted: "
            "max_lifetime_hours %.2f -> %.2f, "
            "visibility_timeout_seconds %.1f -> %.1f "
            "(total_matchups=%d, slots=%d)",
            campaign.max_lifetime_hours, adjusted.max_lifetime_hours,
            campaign.visibility_timeout_seconds,
            adjusted.visibility_timeout_seconds,
            total_matchups, total_matchup_slots,
        )
    return adjusted


def _preflight_for_honest_eval(
    campaign,
    tailscale_authkey: str,
    manifest: GameManifest,
) -> None:
    """Subset of `CampaignManager._preflight` (spec 22) reused for
    honest-eval. Catches the three billing/correctness failure modes:

    1. Malformed authkey (workers boot but `tailscale up` fails silently)
    2. Stale AWS creds (`provision_fleet` 401s after partial spend)
    3. Manifest+AMI tag drift (workers run pre-G probe code against v2
       manifest → silent oracle corruption — see spec 22 §"Manifest +
       AMI tag preflight (2026-04-19)")

    All three gates delegate to public `campaign.check_*` helpers so
    `CampaignManager` and honest-eval cannot drift on remediation messages
    or exception types. NOT included (deferred): tailnet IP probe (we
    trust the env var operator-set in .env), Redis tailnet exposure
    (assumes a working devenv).

    All gates raise `PreflightFailure` (a `ValueError` subclass) on
    failure, so callers can `except PreflightFailure` for finer-grained
    handling or just `except ValueError` to treat preflight failure as a
    generic operator-input error.
    """
    check_authkey_syntax(tailscale_authkey)
    check_aws_credentials()
    # AWSProvider lookup happens at call time so tests can monkeypatch
    # `honest_evaluator.AWSProvider` to inject a fake.
    provider = AWSProvider(regions=campaign.regions)
    check_ami_tags_against_manifest(
        provider, campaign.ami_ids_by_region, manifest,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — invoked by scripts/cloud/evaluate_campaign.sh.

    Lifecycle: load game/campaign → extract top builds from per-study DBs
    → enumerate eval pool → preflight (authkey + AWS creds) → provision
    an isolated AWS fleet via `prepare_cloud_pool` → dispatch matchups →
    write JSON outputs → teardown fleet. The honest-eval fleet is
    namespaced `starsector-honest-eval-{first-campaign}-{utc}` so it
    cannot collide with the source campaign's still-existing AWS
    resources, and so `scripts/cloud/teardown.sh` (which prepends
    `starsector-`) finds it. See spec 30
    §CLI entry point and methodology §Replication count.
    """
    import argparse
    defaults = HonestEvaluationConfig()
    parser = argparse.ArgumentParser(
        description="Honest evaluator — re-score campaign top builds.",
    )
    parser.add_argument("--campaign-name", nargs="+", required=True)
    parser.add_argument("--top-k", type=int, default=defaults.top_k_per_seed)
    parser.add_argument(
        "--replicates", type=int, default=defaults.replicates_per_matchup,
    )
    parser.add_argument(
        "--max-retries", type=int, default=defaults.max_retries_per_matchup,
    )
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument("--hull", required=True)
    parser.add_argument("--out-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--campaign-config", type=Path, default=None,
        help="Path to source-campaign YAML for inheriting fleet config "
             "(regions, AMIs, instance types). Defaults to "
             "examples/{first-campaign-name}.yaml.",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help="Override fleet size for honest-eval. Default = max "
             "workers_per_study across the source campaign's studies.",
    )
    parser.add_argument(
        "--flask-port", type=int, default=None,
        help="Flask port for honest-eval listener. Default = top of the "
             "tailnet-ACL range "
             "(campaign.base_flask_port + flask_ports_per_study - 1, e.g. "
             "9099). Must be in [base_flask_port, base + flask_ports_per_study) "
             "or workers cannot POST results.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract top builds + enumerate pool + load "
                             "campaign config + run lightweight preflight "
                             "(authkey + STS), then exit without "
                             "provisioning any AWS resources. Useful for "
                             "validating inputs before paying.")
    parser.add_argument(
        "--resume-from", default=None,
        help="Reuse a prior eval_tag and resume from its ledger. The "
             "ledger lives at `{out_root}/honest_eval/{eval_tag}/results.jsonl`; "
             "matchups already present skip dispatch and their fitness "
             "folds into the in-memory aggregation. Use this to recover "
             "from a SIGTERM / OOM / network partition mid-run. Reuses "
             "the eval_tag for AWS resource naming, so any prior fleet "
             "must be torn down first (run scripts/cloud/teardown.sh).",
    )
    parser.add_argument(
        "--random-baseline-n", type=int, default=0,
        help="Number of random-feasible builds to add as a baseline cell "
             "(source_campaign='random-baseline'). Without this, the "
             "honest-eval can rank cells against each other but cannot "
             "answer 'does any optimization machinery beat random "
             "sampling?'. Recommended: same as top_k×n_seeds (= 9 for "
             "Wave 1 with --top-k 3 × 3 seeds). Adds ~$0.001×n×pool×reps "
             "to the run.",
    )
    parser.add_argument(
        "--random-baseline-seed", type=int, default=0,
        help="RNG seed for synthesize_random_baseline_builds. Deterministic: "
             "re-running with the same seed re-generates the same baseline "
             "builds, so --resume-from still works.",
    )
    parser.add_argument(
        "--ranking-method", default="twfe_eb", choices=list(RANKING_METHODS),
        help="Estimator used to pick top-K candidates from each study's "
             "evaluation_log.jsonl. Default `twfe_eb` (TWFE + EB shrinkage; "
             "phase5a + phase5d-without-X). `raw_mean` was the pre-2026-05-10 "
             "default but has 0/5 top-5 overlap with principled methods on "
             "Wave 1 — kept here only for ablation. See "
             "docs/reports/2026-05-10-posthoc-ranker-research.md.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _install_signal_handlers()

    game_data = load_game_data(args.game_dir)
    manifest = GameManifest.load()
    if args.hull not in game_data.hulls:
        raise ValueError(f"hull '{args.hull}' not in game data")
    hull = game_data.hulls[args.hull]

    config = HonestEvaluationConfig(
        top_k_per_seed=args.top_k,
        replicates_per_matchup=args.replicates,
        max_retries_per_matchup=args.max_retries,
    )

    builds_with_provenance: list[_BuildWithProvenance] = []
    for name in args.campaign_name:
        # Per-study evaluation_log.jsonl is the candidate-selection input
        # (SQLite `intermediate_values` lacks opponent identity, which
        # TWFE/EB need). Wave 1 logs were migrated to this layout via
        # scripts/migrate_wave1_eval_logs.py; Wave 2+ writes natively.
        log_root = Path("data/logs") / name
        if not log_root.exists():
            raise ValueError(
                f"no eval-log dir for campaign '{name}': {log_root}. "
                f"Wave 1 cells must be migrated via "
                f"scripts/migrate_wave1_eval_logs.py before honest-eval; "
                f"Wave 2+ writes per-study logs natively (task #90)."
            )
        for jsonl_path in sorted(log_root.glob("*/evaluation_log.jsonl")):
            stem = jsonl_path.parent.name
            try:
                seed_part = stem.rsplit("__seed", 1)[1]
                seed_idx = int(seed_part)
            except (IndexError, ValueError) as exc:
                # Spec 30 §Error conditions / methodology §Why fail-loud:
                # an unrecognized study-dir name is a data-integrity
                # signal — e.g. a stray copy, a mid-rename leftover, or
                # a legacy layout. Silently skipping would change which
                # builds the oracle considers without telling the operator.
                raise RuntimeError(
                    f"unrecognized log dir: {jsonl_path.parent}. Expected "
                    f"`{{hull}}__{{regime}}__{{sampler}}__seed{{N}}/"
                    f"evaluation_log.jsonl`. Move or rename before "
                    f"re-running."
                ) from exc
            disagreement = report_method_disagreement(
                jsonl_path, top_k=config.top_k_per_seed,
            )
            primary = disagreement[args.ranking_method]
            others = {m: l for m, l in disagreement.items()
                      if m != args.ranking_method}
            consensus_count = max(
                (sum(1 for m in others.values() if h in m) for h in primary),
                default=0,
            )
            logger.info(
                "honest_eval ranking [%s/%s]: method=%s top-%d=%s; "
                "other-method picks: %s",
                name, stem, args.ranking_method, config.top_k_per_seed,
                primary, others,
            )
            if consensus_count == 0 and len(primary) > 0:
                logger.warning(
                    "honest_eval [%s/%s]: ZERO agreement between "
                    "method=%s and the other estimators on top-%d. "
                    "Likely cause: heavy opponent confounding or a "
                    "near-tied top region. Inspect the JSONL before "
                    "spending budget.",
                    name, stem, args.ranking_method, config.top_k_per_seed,
                )
            tops = extract_top_builds(
                jsonl_path, hull, game_data, manifest,
                config.top_k_per_seed, method=args.ranking_method,
            )
            for rank, value, build in tops:
                builds_with_provenance.append(_BuildWithProvenance(
                    build=build, source_campaign=name,
                    source_study_idx=0,
                    source_seed_idx=seed_idx, source_rank=rank,
                    source_value=value,
                ))

    # Random-feasible baseline cell — answers the existence question
    # "does any of our optimization machinery beat random sampling?".
    # Without it, all-cells-tied on the oracle is uninterpretable.
    if args.random_baseline_n > 0:
        baseline = synthesize_random_baseline_builds(
            hull, game_data, manifest,
            n=args.random_baseline_n,
            seed=args.random_baseline_seed,
        )
        builds_with_provenance.extend(baseline)
        logger.info(
            "honest_eval: added %d random-feasible baseline builds "
            "(source_campaign=%s, seed=%d)",
            len(baseline), RANDOM_BASELINE_SOURCE_CAMPAIGN,
            args.random_baseline_seed,
        )

    eval_pool = discover_evaluation_pool(args.game_dir, game_data, hull)
    if not eval_pool:
        # Pre-provision check — surface the failure before the fleet
        # boots, not after `evaluate_builds` raises inside the `with`.
        raise ValueError(
            f"no compatible opponents for hull '{args.hull}' "
            f"(hull_size={hull.hull_size}). Investigate opponent-pool "
            f"discovery / hull-size lookup before re-running."
        )
    pool_preview = list(eval_pool[:5])
    if len(eval_pool) > 5:
        pool_preview.append("...")
    logger.info(
        "honest_eval prepared: %d builds × %d opponents × %d replicates "
        "= %d matchups; pool=%s",
        len(builds_with_provenance), len(eval_pool),
        config.replicates_per_matchup,
        len(builds_with_provenance) * len(eval_pool) * config.replicates_per_matchup,
        pool_preview,
    )

    # Source campaign's YAML provides fleet config (regions, AMIs, instance
    # types). Default convention: examples/{first-campaign-name}.yaml.
    campaign_yaml = (
        args.campaign_config
        or Path("examples") / f"{args.campaign_name[0]}.yaml"
    )
    if not campaign_yaml.exists():
        raise ValueError(
            f"campaign config not found: {campaign_yaml} "
            f"(use --campaign-config to override)"
        )
    campaign = load_campaign_config(campaign_yaml)

    target_workers = args.workers or max(
        s.workers_per_study for s in campaign.studies
    )
    total_matchup_slots = target_workers * campaign.matchup_slots_per_worker
    total_matchups = (
        len(builds_with_provenance)
        * len(eval_pool)
        * config.replicates_per_matchup
    )
    campaign = _adjust_campaign_for_honest_eval(
        campaign,
        total_matchups=total_matchups,
        total_matchup_slots=total_matchup_slots,
        config=config,
    )
    flask_port = args.flask_port or _resolve_honest_eval_flask_port(campaign)
    # Range guard: workers can only POST to ports in
    # [base_flask_port, base + flask_ports_per_study). Outside the range
    # the tailnet ACL drops the connection and matchups time out
    # silently while the fleet bills.
    port_min = campaign.base_flask_port
    port_max = campaign.base_flask_port + campaign.flask_ports_per_study
    if not (port_min <= flask_port < port_max):
        raise ValueError(
            f"flask_port={flask_port} outside the tailnet-ACL range "
            f"[{port_min}, {port_max}). Workers cannot reach this port; "
            f"matchups would time out silently. Pick a port in range "
            f"(default = {port_max - 1})."
        )

    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    if args.resume_from:
        eval_tag = args.resume_from
        # Reused tags are still subject to the AWS LT length cap, in
        # case the operator hand-typed a too-long string.
        _validate_eval_tag_length(eval_tag)
        ledger_preview = _ledger_path(args.out_root, eval_tag)
        if not ledger_preview.exists():
            raise ValueError(
                f"--resume-from {eval_tag!r}: ledger {ledger_preview} "
                f"does not exist. Either the prior run wrote no "
                f"ledger or the path drifted. Re-run without "
                f"--resume-from to start fresh."
            )
        # Refuse if the prior fleet is still up — `prepare_cloud_pool`
        # would otherwise silently double the fleet (existing instances
        # remain tagged with the eval_tag while new ones boot
        # alongside), and the operator pays for both. Telling the
        # operator to run teardown.sh first is the safe default.
        provider_for_preflight = AWSProvider(regions=campaign.regions)
        active = provider_for_preflight.list_active(eval_tag)
        if active:
            sample = ", ".join(
                f"{a['id']}@{a['region']}({a['state']})" for a in active[:5]
            )
            raise ValueError(
                f"--resume-from {eval_tag!r}: {len(active)} instance(s) "
                f"still tagged Project={eval_tag} are pending/running "
                f"(e.g. {sample}). Tear down the prior fleet first: "
                f"`scripts/cloud/teardown.sh "
                f"{_teardown_arg_for_eval_tag(eval_tag)}`. Resuming with "
                f"the prior fleet still up would double-bill and confuse "
                f"the orchestrator's heartbeat scan."
            )
    else:
        # `starsector-` prefix matches CampaignManager.project_tag and
        # scripts/cloud/teardown.sh, which prepends `starsector-` to its
        # argument. Without the prefix, teardown.sh silently misses
        # honest-eval fleets — exactly the leak that stranded 16
        # instances on 2026-05-10.
        eval_tag = f"starsector-honest-eval-{args.campaign_name[0]}-{stamp}"
        _validate_eval_tag_length(eval_tag)
    ledger_path = _ledger_path(args.out_root, eval_tag)

    # Auto-resolve env vars rather than requiring the operator to export
    # them by hand. CampaignManager generates these per study; the
    # standalone honest-eval CLI has no manager to do that, so we
    # resolve from the same primitive sources:
    #   - STARSECTOR_WORKSTATION_TAILNET_IP: shell out to `tailscale ip -4`
    #   - STARSECTOR_BEARER_TOKEN: fresh per-run UUID (auth is per-run anyway)
    #   - STARSECTOR_TAILSCALE_AUTHKEY: `.env` typically exports it as
    #     `TAILSCALE_AUTHKEY`; we accept either name.
    # Operator-supplied values still win — env vars are checked first.
    from .campaign import _resolve_tailnet_ip
    import uuid
    tailnet_ip = (
        os.environ.get("STARSECTOR_WORKSTATION_TAILNET_IP", "").strip()
        or _resolve_tailnet_ip()
    )
    bearer_token = (
        os.environ.get("STARSECTOR_BEARER_TOKEN", "").strip()
        or uuid.uuid4().hex
    )
    tailscale_authkey = (
        os.environ.get("STARSECTOR_TAILSCALE_AUTHKEY", "").strip()
        or os.environ.get("TAILSCALE_AUTHKEY", "").strip()
    )
    if not tailscale_authkey:
        raise ValueError(
            "Tailscale auth key not found. Set STARSECTOR_TAILSCALE_AUTHKEY "
            "or TAILSCALE_AUTHKEY (.env exports the latter by convention)."
        )
    debug_ssh_pubkey = os.environ.get("STARSECTOR_DEBUG_SSH_PUBKEY", "").strip()
    mod_jar_override_url = os.environ.get(
        "STARSECTOR_MOD_JAR_OVERRIDE_URL", "",
    ).strip()
    mod_jar_override_sha256 = os.environ.get(
        "STARSECTOR_MOD_JAR_OVERRIDE_SHA256", "",
    ).strip()

    # Preflight — authkey syntax + AWS STS + manifest+AMI tag drift.
    # Runs in dry-run too so operators discover problems before paying.
    _preflight_for_honest_eval(campaign, tailscale_authkey, manifest)

    if args.dry_run:
        logger.info(
            "dry-run: would provision %d workers (=%d matchup slots) on "
            "flask_port=%d using campaign config %s. Preflight passed. "
            "Skipping provision.",
            target_workers, total_matchup_slots, flask_port, campaign_yaml,
        )
        return 0

    # Redis queues are in-flight state, not the resume source of truth.
    # A killed/interrupted honest-eval run may leave source/processing
    # items behind under the same eval_tag; the ledger replay below is what
    # decides which matchups are complete.
    _flush_stale_campaign_keys(
        eval_tag, campaign.redis_port, campaign.redis_preflight_timeout_seconds,
    )

    # Cloud orchestration. honest-eval namespaces are SEPARATE from the
    # source campaign's project_tag/study_id/fleet_name to avoid collision
    # with any still-existing source-campaign resources (post-run dangling
    # SG / LT / EC2 tags). `terminate_all_tagged(tag)` would otherwise
    # sweep the wrong fleet.
    logger.info(
        "honest_eval cloud-pool: tag=%s workers=%d slots=%d port=%d",
        eval_tag, target_workers, total_matchup_slots, flask_port,
    )

    try:
        with prepare_cloud_pool(
            campaign=campaign,
            study_id=eval_tag,
            project_tag=eval_tag,
            fleet_name=eval_tag,
            flask_port=flask_port,
            target_workers=target_workers,
            total_matchup_slots=total_matchup_slots,
            tailnet_ip=tailnet_ip,
            bearer_token=bearer_token,
            tailscale_authkey=tailscale_authkey,
            debug_ssh_pubkey=debug_ssh_pubkey,
            mod_jar_override_url=mod_jar_override_url,
            mod_jar_override_sha256=mod_jar_override_sha256,
            sweep_project_on_exit=True,
        ) as pool:
            evaluated = evaluate_builds(
                builds_with_provenance, eval_pool, pool, config, hull,
                ledger_path=ledger_path,
            )
    except KeyboardInterrupt:
        logger.warning("honest_eval interrupted — cleanup complete")
        return 130

    finished_at = datetime.now(timezone.utc)
    summaries = summarize_by_cell(evaluated)
    result = HonestEvaluationResult(
        schema_version=HONEST_EVAL_SCHEMA_VERSION,
        evaluated_builds=tuple(evaluated),
        cell_summaries=summaries,
        pool_variant_ids=eval_pool,
        pool_size=len(eval_pool),
        config=config,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
    )
    write_outputs(result, args.out_root)
    logger.info("honest_eval complete: %d builds × %d cells",
                len(evaluated), len(summaries))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
