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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import optuna
from optuna.trial import TrialState

from .campaign import (
    check_ami_tags_against_manifest, check_authkey_syntax,
    check_aws_credentials, load_campaign_config,
)
from .cloud_provider import AWSProvider
from .cloud_runner import _require_env, prepare_cloud_pool
from .combat_fitness import combat_fitness
from .evaluator_pool import EvaluatorPool
from .game_manifest import GameManifest
from .models import (
    Build,
    BuildSpec,
    CombatResult,
    HonestEvaluationConfig,
    MatchupConfig,
    ShipHull,
)
from .opponent_pool import discover_opponent_pool, get_opponents
from .parser import GameData, load_game_data
from .repair import repair_build

logger = logging.getLogger(__name__)

HONEST_EVAL_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class _BuildWithProvenance:
    """Internal — pairs a Build with the source-DB metadata."""
    build: Build
    source_campaign: str
    source_study_idx: int
    source_seed_idx: int
    source_rank: int
    source_value: float


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


def extract_top_builds(
    study_db_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    top_k: int,
) -> tuple[tuple[int, float, Build], ...]:
    """Open per-study SQLite, return (rank, value, Build) for top_k completed
    trials in descending value order.

    Raises:
        ValueError: study has fewer than top_k completed trials.
        RuntimeError: any selected trial's params fail repair_build —
            stale params are a data-corruption signal, not a soft error
            (see spec 30 §Error conditions, methodology §Why fail-loud).
    """
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    storage = f"sqlite:///{study_db_path}"
    # Per-study DB layout (see spec 22 §Per-study SQLite layout): one Optuna
    # study per file with name `{hull}__{regime}` (no sampler/seed suffix in
    # the study name — those are encoded in the *filename*).
    summaries = optuna.get_all_study_summaries(storage=storage)
    if not summaries:
        raise ValueError(f"no studies in DB: {study_db_path}")
    if len(summaries) != 1:
        # Per-study DB convention is one Optuna study per file. Multi-study
        # DBs are a data-integrity signal (legacy import? accidental copy?
        # cross-regime warm-start without filename split?) — silently
        # picking summaries[0] would change which build set goes into the
        # oracle without telling the operator.
        names = [s.study_name for s in summaries]
        raise RuntimeError(
            f"{study_db_path.name}: expected 1 study per DB, found "
            f"{len(summaries)}: {names}. Spec 22 §Per-study SQLite layout "
            f"requires one study per file. Investigate before re-running."
        )
    study = optuna.load_study(study_name=summaries[0].study_name, storage=storage)
    completed = [
        t for t in study.trials
        if t.state == TrialState.COMPLETE and t.value is not None
    ]
    if len(completed) < top_k:
        raise ValueError(
            f"{study_db_path.name}: only {len(completed)} completed trial(s); "
            f"top_k={top_k}"
        )
    completed.sort(key=lambda t: t.value, reverse=True)
    top = completed[:top_k]
    out: list[tuple[int, float, Build]] = []
    for rank, trial in enumerate(top, start=1):
        try:
            from .optimizer import trial_params_to_build
            raw = trial_params_to_build(trial.params, hull.id)
            repaired = repair_build(raw, hull, game_data, manifest)
        except Exception as exc:
            raise RuntimeError(
                f"{study_db_path.name}: trial {trial.number} (rank {rank}, "
                f"value {trial.value:.4f}) failed repair_build: {exc}. "
                f"This is a data-corruption signal (search-space drift or "
                f"repair regression). Investigate before re-running honest "
                f"eval — silently skipping would alter what 'top-k' means "
                f"and break cross-cell comparison."
            ) from exc
        out.append((rank, float(trial.value), repaired))
    return tuple(out)


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
) -> tuple[EvaluatedBuild, ...]:
    """Dispatch every (build × opp × replicate) matchup, retry failures up to
    config.max_retries_per_matchup, aggregate per-build mean fitness.

    Raises:
        ValueError: empty eval_pool, or empty builds_with_provenance.
        RuntimeError: a matchup failed after all retries.
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

    jobs: list[_Job] = []
    for bi, _ in enumerate(builds_with_provenance):
        for opp in eval_pool:
            for rep in range(config.replicates_per_matchup):
                jobs.append(_Job(build_idx=bi, opp=opp, rep=rep))

    # build_idx → list of per-matchup fitness scores
    scores_per_build: dict[int, list[float]] = {
        i: [] for i in range(len(builds_with_provenance))
    }

    def _build_id(bi: int) -> str:
        bp = builds_with_provenance[bi]
        return (
            f"honest__{bp.source_campaign}__s{bp.source_study_idx}"
            f"__seed{bp.source_seed_idx}__rank{bp.source_rank}"
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


def _validate_eval_tag_length(eval_tag: str) -> None:
    """AWS Launch Template names cap at 128 chars; AWSProvider then
    composes resource names like `{project_tag}__{fleet_name}` (= 2 ×
    eval_tag + 2). Cap eval_tag at 60 so the doubled form fits.
    """
    max_eval_tag = 60
    if len(eval_tag) > max_eval_tag:
        raise ValueError(
            f"eval_tag {eval_tag!r} is {len(eval_tag)} chars > {max_eval_tag}; "
            f"shorten the source campaign name (`{eval_tag[:60]}…` would "
            f"overflow AWS Launch Template name limits when doubled into "
            f"`{{project_tag}}__{{fleet_name}}`)."
        )


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
    namespaced `honest-eval-{first-campaign}-{utc}` so it cannot collide
    with the source campaign's still-existing AWS resources. See spec 30
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
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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
        db_dir = Path("data/study_dbs") / name
        if not db_dir.exists():
            raise ValueError(f"no study_dbs dir for campaign '{name}': {db_dir}")
        for db_path in sorted(db_dir.glob("*.db")):
            stem = db_path.stem
            try:
                seed_part = stem.rsplit("__seed", 1)[1]
                seed_idx = int(seed_part)
            except (IndexError, ValueError) as exc:
                # Spec 30 §Error conditions / methodology §Why fail-loud:
                # data-integrity signals must not be silently skipped.
                # An unrecognized DB filename in the campaign dir means
                # the operator's directory layout has drifted from the
                # convention `{hull}__{regime}__{sampler}__seed{N}.db`
                # — e.g. a stray .db copy, a mid-rename leftover, or a
                # legacy filename. Warn-and-skip would silently change
                # which builds the oracle considers; raising stops the
                # operator before they spend money on a partial set.
                raise RuntimeError(
                    f"unrecognized DB filename: {db_path}. Expected "
                    f"`{{hull}}__{{regime}}__{{sampler}}__seed{{N}}.db` "
                    f"per spec 22 §Per-study SQLite layout. Move or "
                    f"rename before re-running."
                ) from exc
            tops = extract_top_builds(
                db_path, hull, game_data, manifest, config.top_k_per_seed,
            )
            for rank, value, build in tops:
                builds_with_provenance.append(_BuildWithProvenance(
                    build=build, source_campaign=name,
                    source_study_idx=0,
                    source_seed_idx=seed_idx, source_rank=rank,
                    source_value=value,
                ))

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
    eval_tag = f"honest-eval-{args.campaign_name[0]}-{stamp}"
    _validate_eval_tag_length(eval_tag)

    # Required env vars (same set spec 22 _require_env loads, minus
    # STARSECTOR_PROJECT_TAG which honest-eval derives locally).
    tailnet_ip = _require_env("STARSECTOR_WORKSTATION_TAILNET_IP")
    bearer_token = _require_env("STARSECTOR_BEARER_TOKEN")
    tailscale_authkey = _require_env("STARSECTOR_TAILSCALE_AUTHKEY")
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

    # Cloud orchestration. honest-eval namespaces are SEPARATE from the
    # source campaign's project_tag/study_id/fleet_name to avoid collision
    # with any still-existing source-campaign resources (post-run dangling
    # SG / LT / EC2 tags). `terminate_all_tagged(tag)` would otherwise
    # sweep the wrong fleet.
    logger.info(
        "honest_eval cloud-pool: tag=%s workers=%d slots=%d port=%d",
        eval_tag, target_workers, total_matchup_slots, flask_port,
    )

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
    ) as pool:
        evaluated = evaluate_builds(
            builds_with_provenance, eval_pool, pool, config, hull,
        )

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
