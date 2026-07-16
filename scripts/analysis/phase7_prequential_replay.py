"""Prequential replay ablation — spec 31 §"Prequential Replay Ablation".

Replays each wave-1 replay cell's proposal stream in arrival order (eval-log
timestamps), trains surrogate arms on past matchup rows only, scores upcoming
proposal blocks over their planned opponent panels, simulates a skip-bottom-q
gating policy, and computes the offline estimator arms A0/A1/A2/EB/A3 folded
from the retired Phase 5A debt (re-groom D2). Local, zero sim spend.

The methodology review M3 remedy: train on rows < t, score the next proposal
batch, measure rank fidelity and budget savings if the bottom-q
surrogate-ranked proposals were skipped. Drift-aware reporting per the
2026-07-12 adversarial-AUC evidence (forward-time shift).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau, spearmanr

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from starsector_optimizer.deconfounding import (
    eb_shrinkage,
    pooled_residual_variance,
    triple_goal_rank,
    trimmed_alpha,
    twfe_decompose,
)
from starsector_optimizer.models import EBShrinkageConfig, TWFEConfig
from starsector_optimizer.phase7_matchup_data import (
    EXPERIMENT_SCHEMA_VERSION,
    TrainingMatchupRow,
    load_honest_eval_matchups,
    load_recovered_builds,
    load_training_matchups,
)
from starsector_optimizer.scorer import heuristic_score

_BASELINE_PATH = Path(__file__).with_name("phase7_baseline_surrogate.py")
_BASELINE_SPEC = importlib.util.spec_from_file_location(
    "_phase7_baseline_surrogate", _BASELINE_PATH
)
assert _BASELINE_SPEC is not None and _BASELINE_SPEC.loader is not None
baseline = importlib.util.module_from_spec(_BASELINE_SPEC)
sys.modules.setdefault("_phase7_baseline_surrogate", baseline)
if sys.modules["_phase7_baseline_surrogate"] is baseline:
    _BASELINE_SPEC.loader.exec_module(baseline)
else:  # pragma: no cover - test harness loaded it first
    baseline = sys.modules["_phase7_baseline_surrogate"]

_LEARNED_PATH = Path(__file__).with_name("phase7_learned_surrogate_experiment.py")
_LEARNED_SPEC = importlib.util.spec_from_file_location(
    "_phase7_learned_surrogate_experiment", _LEARNED_PATH
)
assert _LEARNED_SPEC is not None and _LEARNED_SPEC.loader is not None
learned = importlib.util.module_from_spec(_LEARNED_SPEC)
sys.modules.setdefault("_phase7_learned_surrogate_experiment", learned)
if sys.modules["_phase7_learned_surrogate_experiment"] is learned:
    _LEARNED_SPEC.loader.exec_module(learned)
else:  # pragma: no cover - test harness loaded it first
    learned = sys.modules["_phase7_learned_surrogate_experiment"]


# ------------------------------------------------------ designed defaults ---

DEFAULT_MIN_TRAIN_TRIALS = 40
DEFAULT_CUTOFF_STRIDE = 10
DEFAULT_MIN_FUTURE_TRIALS = 10
DEFAULT_HORIZON_BUCKETS: tuple[tuple[int, int], ...] = ((0, 10), (10, 20), (20, 40))
DEFAULT_GATING_FRACTIONS: tuple[float, ...] = (0.1, 0.2, 0.3, 0.5)
DEFAULT_GATING_SENSITIVITY_FRACTION = 0.3
DEFAULT_TIE_BREAK_DRAWS = 32
DEFAULT_CV_VARIANCE_FLOOR = 1e-9
DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 331
DEFAULT_TOP_K_VALUES: tuple[int, ...] = (1, 3, 9)
DEFAULT_LEARNED_MODELS: tuple[str, ...] = ("catboost_regressor", "random_forest_tuned")
DEFAULT_MODEL_THREAD_COUNT = learned.DEFAULT_MODEL_THREAD_COUNT
DEFAULT_HPO_SEED = learned.DEFAULT_HPO_SEED

# Spec 31: gating simulation arms — the headline learned family, its learned
# sibling, and the mandatory build-blind null (review C1).
GATING_ARMS: tuple[str, ...] = ("catboost_regressor", "random_forest_tuned", "opponent_mean")
HEADLINE_ARM = "catboost_regressor"
HEADLINE_TOP_K = 3
HEADLINE_GAP_MODE = "measured"
GATING_TARGET_ARMS: tuple[str, ...] = ("A1", "A0", "EB")  # primary first
ESTIMATOR_ARMS: tuple[str, ...] = ("A0", "A1", "A2", "EB", "A3")
TRAIN_GAP_MODES: tuple[str, ...] = ("zero", "measured")
A3_TOP_QUANTILE_FRACTION = 0.25
EB_MIN_RANKABLE_BUILDS = 3  # eb_shrinkage's own n >= 3 contract (spec 28)
BOOTSTRAP_CI_PERCENTILES = (2.5, 97.5)  # 95% percentile interval
MIN_BOOTSTRAP_N = 3  # below this a resampled rank correlation is meaningless
CLAIM_LABEL = learned.DEFAULT_CLAIM_LABEL
HONEST_EVAL_USAGE = "exploratory_selection"
TAIL_BUCKET_LABEL = "tail"
PROGRESS_TAG = "[phase7-replay]"


def _progress(message: str, enabled: bool) -> None:
    if enabled:
        print(f"{PROGRESS_TAG} {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- config ---


@dataclass(frozen=True)
class ReplayConfig:
    db_path: Path
    game_dir: Path
    output: Path | None
    log_base_dir: Path
    study_db_root: Path
    min_train_trials: int = DEFAULT_MIN_TRAIN_TRIALS
    cutoff_stride: int = DEFAULT_CUTOFF_STRIDE
    min_future_trials: int = DEFAULT_MIN_FUTURE_TRIALS
    horizon_buckets: tuple[tuple[int, int], ...] = DEFAULT_HORIZON_BUCKETS
    gating_fractions: tuple[float, ...] = DEFAULT_GATING_FRACTIONS
    gating_sensitivity_fraction: float = DEFAULT_GATING_SENSITIVITY_FRACTION
    train_gap_modes: tuple[str, ...] = TRAIN_GAP_MODES
    tie_break_draws: int = DEFAULT_TIE_BREAK_DRAWS
    cv_variance_floor: float = DEFAULT_CV_VARIANCE_FLOOR
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED
    hpo_seed: int = DEFAULT_HPO_SEED
    model_thread_count: int = DEFAULT_MODEL_THREAD_COUNT
    top_k_values: tuple[int, ...] = DEFAULT_TOP_K_VALUES
    feature_profile: str = baseline.DEFAULT_FEATURE_PROFILE
    learned_models: tuple[str, ...] = DEFAULT_LEARNED_MODELS
    max_cells: int | None = None
    progress: bool = True
    twfe: TWFEConfig = field(default_factory=TWFEConfig)
    eb: EBShrinkageConfig = field(default_factory=EBShrinkageConfig)


@dataclass(frozen=True)
class LogTrial:
    """Decision-time metadata for one trial, read from the eval log."""

    timestamp: str
    pruned: bool
    planned_opponents: tuple[str, ...]
    covariate_vector: tuple[float, ...] | None


@dataclass(frozen=True)
class ReplayTrial:
    cell: str
    source_path: str
    trial_number: int
    timestamp: str
    pruned: bool
    build_key: str
    planned_opponents: tuple[str, ...]
    rows: tuple[TrainingMatchupRow, ...]
    covariate_vector: tuple[float, ...] | None


# ----------------------------------------------------- stream construction ---


def load_log_trials(
    source_paths: Sequence[str], log_base_dir: Path
) -> dict[tuple[str, int], LogTrial]:
    """Read decision-time trial metadata from the eval logs.

    Rows flagged cache_hit or invalid_spec never reached the matchup DB and
    are skipped; pruned rows are kept (real observations).
    """
    out: dict[tuple[str, int], LogTrial] = {}
    for source_path in sorted(set(source_paths)):
        log_path = log_base_dir / source_path
        if not log_path.exists():
            raise ValueError(
                f"eval log missing for source_path {source_path!r}: {log_path} "
                "— the replay stream requires the source logs beside the DB"
            )
        with log_path.open() as handle:
            for lineno, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{log_path}:{lineno}: malformed JSON — {exc}. "
                        "Data-integrity signal; investigate the producer."
                    ) from exc
                if record.get("cache_hit") or record.get("invalid_spec"):
                    continue
                covariates = record.get("covariate_vector")
                out[(source_path, int(record["trial_number"]))] = LogTrial(
                    timestamp=str(record["timestamp"]),
                    pruned=bool(record.get("pruned")),
                    planned_opponents=tuple(record["opponent_order"]),
                    covariate_vector=(
                        tuple(float(v) for v in covariates) if covariates is not None else None
                    ),
                )
    return out


def build_replay_cells(
    matchup_rows: Sequence[TrainingMatchupRow],
    log_trials: Mapping[tuple[str, int], LogTrial],
) -> dict[str, tuple[ReplayTrial, ...]]:
    """Join DB matchup rows with eval-log metadata into per-cell arrival streams.

    Join failure in either direction is a hard error (data-integrity signal):
    the DB was materialized from these logs, so any mismatch means the inputs
    have drifted apart.
    """
    rows_by_trial: dict[tuple[str, int], list[TrainingMatchupRow]] = {}
    for row in matchup_rows:
        rows_by_trial.setdefault((row.source_path, row.trial_number), []).append(row)

    missing_logs = sorted(set(rows_by_trial) - set(log_trials))
    if missing_logs:
        raise ValueError(
            f"{len(missing_logs)} DB trial(s) have no eval log row, first: "
            f"{missing_logs[0]!r} — the replay stream requires a total join"
        )
    missing_db = sorted(set(log_trials) - set(rows_by_trial))
    if missing_db:
        raise ValueError(
            f"{len(missing_db)} eval log trial(s) missing from the matchup DB, first: "
            f"{missing_db[0]!r} — the replay stream requires a total join"
        )

    cells: dict[str, list[ReplayTrial]] = {}
    for (source_path, trial_number), rows in rows_by_trial.items():
        log = log_trials[(source_path, trial_number)]
        first = rows[0]
        cell = f"{first.campaign}:{first.seed}"
        if log.pruned != (first.row_kind == "pruned"):
            raise ValueError(
                f"pruned flag mismatch for {source_path!r} trial {trial_number}: "
                f"log pruned={log.pruned}, DB row_kind={first.row_kind!r}"
            )
        cells.setdefault(cell, []).append(
            ReplayTrial(
                cell=cell,
                source_path=source_path,
                trial_number=trial_number,
                timestamp=log.timestamp,
                pruned=log.pruned,
                build_key=first.build_key,
                planned_opponents=log.planned_opponents,
                rows=tuple(sorted(rows, key=lambda r: r.opponent_index)),
                covariate_vector=log.covariate_vector,
            )
        )

    return {
        cell: tuple(sorted(trials, key=lambda t: (t.timestamp, t.trial_number)))
        for cell, trials in sorted(cells.items())
    }


def measured_inflight_gap(cell_trials: Sequence[ReplayTrial], config: ReplayConfig) -> int:
    """Median in-flight trial count at completion, from the cell's study DB.

    In-flight at trial t's completion = trials with datetime_start before and
    datetime_complete after that moment. This is the Ĝ used by the
    "measured" train-gap mode.

    Only trials that appear in the arrival stream (i.e. emitted an eval-log
    row) count. The two terminal-failure kinds — instance-error and
    exhausted-worker-timeout — are told `failure_score` as Optuna
    `state=COMPLETE` yet emit no eval-log row (spec 24 terminal-failure paths),
    so they are absent from `cell_trials`; counting them here would inflate Ĝ
    over the population the gap actually applies to. Restricting to
    stream trial numbers excludes both.
    """
    # source_path: data/logs/<campaign>/<study>/evaluation_log.jsonl
    parts = Path(cell_trials[0].source_path).parts
    campaign, study = parts[-3], parts[-2]
    study_db = config.study_db_root / campaign / f"{study}.db"
    if not study_db.exists():
        raise ValueError(
            f"study DB missing for cell {cell_trials[0].cell!r}: {study_db} — "
            "required to measure the in-flight gap (train_gap_modes includes 'measured')"
        )
    stream_numbers = {t.trial_number for t in cell_trials}
    con = sqlite3.connect(study_db)
    try:
        rows = con.execute(
            """
            select number, datetime_start, datetime_complete from trials
            where state = 'COMPLETE' and datetime_start is not null
              and datetime_complete is not null
            """
        ).fetchall()
    finally:
        con.close()
    # Stream-only: drop terminal-failure COMPLETE trials — instance-error AND
    # exhausted-worker-timeout (both COMPLETE in the study DB but absent from the
    # arrival stream, as they emit no eval-log row; spec 24 terminal-failure paths).
    intervals = [(start, complete) for number, start, complete in rows if number in stream_numbers]
    if not intervals:
        raise ValueError(f"study DB {study_db} has no completed trials with timestamps")
    counts = []
    for _, complete in intervals:
        inflight = sum(1 for start, done in intervals if start < complete and done > complete)
        counts.append(inflight)
    return int(np.median(np.asarray(counts)))


# ------------------------------------------------------- cutoffs / buckets ---


def cutoff_indices(n_trials: int, config: ReplayConfig) -> tuple[int, ...]:
    return tuple(
        range(
            config.min_train_trials,
            n_trials - config.min_future_trials + 1,
            config.cutoff_stride,
        )
    )


def bucket_assignments(n_future: int, config: ReplayConfig) -> dict[str, tuple[int, ...]]:
    """Map bucket labels to future-trial offsets (distance from the cutoff)."""
    out: dict[str, tuple[int, ...]] = {}
    covered = 0
    for lo, hi in config.horizon_buckets:
        out[f"{lo}-{hi}"] = tuple(range(min(lo, n_future), min(hi, n_future)))
        covered = max(covered, hi)
    out[TAIL_BUCKET_LABEL] = tuple(range(min(covered, n_future), n_future))
    return out


def training_trials(
    trials: Sequence[ReplayTrial],
    cutoff: int,
    gap: int,
    skipped: frozenset[int],
) -> tuple[ReplayTrial, ...]:
    """Trials available for training at a cutoff: the stream prefix minus the
    in-flight gap, minus gating-skipped trials."""
    prefix = trials[: max(cutoff - gap, 0)]
    return tuple(t for t in prefix if t.trial_number not in skipped)


def panel_rows(trial: ReplayTrial) -> tuple[TrainingMatchupRow, ...]:
    """Decision-time prediction rows over the trial's planned opponent panel.

    The target field is a placeholder — panel rows are only ever passed to
    model predict, never scored against.
    """
    return tuple(
        TrainingMatchupRow(
            source_path=trial.source_path,
            campaign=trial.cell.rsplit(":", 1)[0],
            seed=int(trial.cell.rsplit(":", 1)[1]),
            trial_number=trial.trial_number,
            build_key=trial.build_key,
            opponent_variant_id=opponent,
            opponent_index=index,
            target=0.0,
            row_kind="planned_panel",
        )
        for index, opponent in enumerate(trial.planned_opponents)
    )


# --------------------------------------------------------- estimator arms ---


@dataclass(frozen=True)
class ArmEstimates:
    """Per-arm estimates over rankable (finalized) builds, keyed trial_number."""

    values: dict[str, dict[int, float]]
    a3_tie_trials: tuple[int, ...]
    diagnostics: dict[str, Any]
    beta_by_opponent: dict[str, float]


def estimator_arm_estimates(
    trials: Sequence[ReplayTrial],
    composite_scores: Mapping[int, float],
    config: ReplayConfig,
) -> ArmEstimates:
    """Offline estimator arms A0/A1/A2/EB (values) + A3 tie group.

    Fits on all matchup rows (finalized + pruned), matching the live
    ScoreMatrix; ranks rankable (finalized) builds only (spec 31).
    """
    trial_index = {t.trial_number: i for i, t in enumerate(trials)}
    opp_index: dict[str, int] = {}
    for trial in trials:
        for row in trial.rows:
            opp_index.setdefault(row.opponent_variant_id, len(opp_index))
    matrix = np.full((max(len(trials), 1), max(len(opp_index), 1)), np.nan)
    for trial in trials:
        for row in trial.rows:
            matrix[trial_index[trial.trial_number], opp_index[row.opponent_variant_id]] = row.target

    alpha, beta = twfe_decompose(matrix, n_iters=config.twfe.n_iters, ridge=config.twfe.ridge)
    rankable = [t for t in trials if not t.pruned]

    a0 = {
        t.trial_number: trimmed_alpha(matrix[trial_index[t.trial_number]], beta, 0)
        for t in rankable
    }
    a1 = {
        t.trial_number: trimmed_alpha(
            matrix[trial_index[t.trial_number]], beta, config.twfe.trim_worst
        )
        for t in rankable
    }

    diagnostics: dict[str, Any] = {
        "n_trials": len(trials),
        "n_rankable": len(rankable),
        "n_opponents": len(opp_index),
    }

    # A2 — scalar control variate on the recomputed composite score.
    ordered = sorted(a1)
    alpha_vec = np.asarray([a1[t] for t in ordered])
    h_vec = np.asarray([composite_scores[t] for t in ordered])
    var_h = float(np.var(h_vec)) if len(h_vec) else 0.0
    if var_h <= config.cv_variance_floor:
        beta_cv = 0.0
    else:
        beta_cv = float(np.cov(alpha_vec, h_vec, ddof=0)[0, 1] / var_h)
    diagnostics["a2_beta_cv"] = beta_cv
    a2_vec = alpha_vec - beta_cv * (h_vec - h_vec.mean()) if len(h_vec) else alpha_vec
    a2 = {t: float(v) for t, v in zip(ordered, a2_vec, strict=True)}

    values: dict[str, dict[int, float]] = {"A0": a0, "A1": a1, "A2": a2}

    # EB — covariate shrinkage over rankable builds with logged covariates.
    if len(rankable) < EB_MIN_RANKABLE_BUILDS:
        diagnostics["eb_skip_reason"] = "insufficient_rankable_builds"
    else:
        sigma_eps_sq = pooled_residual_variance(matrix, alpha, beta)
        eb_alpha = np.asarray([a1[t.trial_number] for t in rankable])
        n_per = np.asarray(
            [int(np.sum(~np.isnan(matrix[trial_index[t.trial_number]]))) for t in rankable]
        )
        sigma_sq = sigma_eps_sq / np.maximum(n_per, 1)
        covariates = np.asarray([t.covariate_vector for t in rankable], dtype=float)
        # Zero-std covariate columns are expected within a cell (some logged
        # covariates are constant per hull/regime); the dropped set is
        # recorded in diagnostics instead of warned per call — same filter
        # rationale as posthoc_ranker.rank_twfe_eb.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="eb_shrinkage dropped zero-std X columns",
            )
            eb_values, _gamma, tau2, kept = eb_shrinkage(
                alpha=eb_alpha, sigma_sq=sigma_sq, X=covariates, config=config.eb
            )
        if config.eb.triple_goal:
            eb_values = triple_goal_rank(eb_values, eb_alpha)
        values["EB"] = {t.trial_number: float(v) for t, v in zip(rankable, eb_values, strict=True)}
        diagnostics["eb_tau2"] = float(tau2)
        diagnostics["eb_sigma_eps_sq"] = float(sigma_eps_sq)
        diagnostics["eb_kept_covariate_columns"] = [int(i) for i in kept]

    # A3 — top-quartile ceiling tie group over A2 (ranking otherwise ≡ A2).
    n_tie = max(math.ceil(len(a2) * A3_TOP_QUANTILE_FRACTION), 1) if a2 else 0
    a3_tie = tuple(sorted(deterministic_top_k(a2, n_tie))) if n_tie else ()

    return ArmEstimates(
        values=values,
        a3_tie_trials=a3_tie,
        diagnostics=diagnostics,
        beta_by_opponent={opp: float(beta[j]) for opp, j in opp_index.items()},
    )


def composite_scores(trials: Sequence[ReplayTrial], config: ReplayConfig) -> dict[int, float]:
    """Recompute composite_score per rankable build via the current scorer.

    A2 is a reconstruction, not a replication (spec 31): the current
    manifest-driven scorer is not the Phase-5A-era scorer.
    """
    game_data, _manifest = baseline._load_context(config.game_dir)
    builds = {b.build_key: b.build for b in load_recovered_builds(config.db_path)}
    out: dict[int, float] = {}
    for trial in trials:
        if trial.pruned:
            continue
        build = builds[trial.build_key]
        hull = game_data.hulls[build.hull_id]
        out[trial.trial_number] = heuristic_score(build, hull, game_data).composite_score
    return out


def deterministic_top_k(estimates: Mapping[int, float], k: int) -> tuple[int, ...]:
    """Top-k trial numbers by estimate, descending; float ties break by
    ascending trial number (full determinism)."""
    ordered = sorted(estimates, key=lambda t: (-estimates[t], t))
    return tuple(sorted(ordered[:k]))


def a3_top_k_draws(
    values: Mapping[int, float],
    tie_group: Sequence[int],
    k: int,
    draws: int,
    seed: int,
) -> tuple[tuple[int, ...], ...]:
    """Top-k selections under A3's ceiling with seeded random tie-breaking.

    Tie-group members share the ceiling value; each draw shuffles their
    relative order. Below-ceiling order follows the underlying values.
    """
    rng = np.random.default_rng(seed)
    tie = list(tie_group)
    rest = sorted((t for t in values if t not in set(tie)), key=lambda t: (-values[t], t))
    out = []
    for _ in range(draws):
        shuffled = list(tie)
        rng.shuffle(shuffled)
        ranking = shuffled + rest
        out.append(tuple(sorted(ranking[:k])))
    return tuple(out)


# --------------------------------------------------------------- fidelity ---


def _rank_corr(x: Sequence[float], y: Sequence[float]) -> tuple[float | None, float | None]:
    if len(x) < 2 or len(set(x)) < 2 or len(set(y)) < 2:
        return None, None
    rho, _ = spearmanr(np.asarray(x), np.asarray(y))
    tau, _ = kendalltau(np.asarray(x), np.asarray(y))
    return (
        None if math.isnan(float(rho)) else float(rho),
        None if math.isnan(float(tau)) else float(tau),
    )


def fidelity_record(
    future_trials: Sequence[ReplayTrial],
    predicted: Mapping[int, float],
    t2_target: Mapping[int, float],
) -> dict[str, Any]:
    """T1 (panel-matched raw, finalized only) + T2 (vs full-data A1 α̂)."""
    finalized = [t for t in future_trials if not t.pruned and t.trial_number in predicted]
    t1_pred = [predicted[t.trial_number] for t in finalized]
    t1_real = [float(np.mean([r.target for r in t.rows])) for t in finalized]
    t1_rho, t1_tau = _rank_corr(t1_pred, t1_real)

    t2_trials = [t for t in finalized if t.trial_number in t2_target]
    t2_pred = [predicted[t.trial_number] for t in t2_trials]
    t2_real = [t2_target[t.trial_number] for t in t2_trials]
    t2_rho, t2_tau = _rank_corr(t2_pred, t2_real)

    return {
        "t1_spearman": t1_rho,
        "t1_kendall": t1_tau,
        "t1_n": len(finalized),
        "t2_spearman": t2_rho,
        "t2_kendall": t2_tau,
        "t2_n": len(t2_trials),
    }


# ----------------------------------------------------------------- gating ---


def run_gating(
    trials: Sequence[ReplayTrial],
    cutoffs: Sequence[int],
    config: ReplayConfig,
    gap: int,
    fraction: float,
    predict_block: Callable[[Sequence[ReplayTrial], Sequence[ReplayTrial]], Mapping[int, float]],
    remove_skipped: bool,
) -> dict[str, Any]:
    """Walk cutoffs; skip the predicted bottom-q of each next block.

    Under remove_skipped=True (the spec's primary semantics) skipped trials'
    rows leave all later training sets.
    """
    skipped: list[int] = []
    for cutoff in cutoffs:
        block = trials[cutoff : cutoff + config.cutoff_stride]
        if not block:
            continue
        train = training_trials(
            trials, cutoff, gap, frozenset(skipped) if remove_skipped else frozenset()
        )
        scores = predict_block(train, block)
        n_skip = int(len(block) * fraction)
        if n_skip == 0:
            continue
        ranked = sorted(block, key=lambda t: (scores[t.trial_number], t.trial_number))
        skipped.extend(t.trial_number for t in ranked[:n_skip])
    skipped_set = set(skipped)
    return {
        "skipped_trials": sorted(skipped_set),
        "rows_saved": sum(len(t.rows) for t in trials if t.trial_number in skipped_set),
        "rows_total": sum(len(t.rows) for t in trials),
    }


def gating_regret(
    skipped_trials: Sequence[int],
    targets: Mapping[str, Mapping[int, float]],
    top_k_values: Sequence[int],
) -> dict[str, dict[str, int]]:
    """Count of true top-k builds (per gating-target arm) that were skipped."""
    skipped = set(skipped_trials)
    out: dict[str, dict[str, int]] = {}
    for arm, estimates in targets.items():
        out[arm] = {
            str(k): len(set(deterministic_top_k(estimates, k)) & skipped) for k in top_k_values
        }
    return out


def pruner_rows_avoided(trials: Sequence[ReplayTrial]) -> int:
    """Rows the incumbent pruner avoided: planned-panel size minus realized
    rows, over pruned trials (spec 31: NOT the pruned-row share)."""
    return sum(len(t.planned_opponents) - len(t.rows) for t in trials if t.pruned)


def q_star(per_q_regret: Mapping[str, Mapping[str, int]], top_k: int) -> float:
    """Max gating fraction with zero realized top-k regret; 0.0 if none."""
    eligible = [float(q) for q, regret in per_q_regret.items() if regret[str(top_k)] == 0]
    return max(eligible) if eligible else 0.0


# ------------------------------------------------------- oracle evaluation ---


def within_cell_concordance(
    arm_values: Mapping[str, Mapping[int, float]],
    oracle_means: Mapping[int, float],
    a3_tie_trials: Sequence[int],
) -> dict[str, dict[str, float | int]]:
    """Pairwise order agreement of arm estimates vs oracle build means.

    A3 pairs inside its tie group contribute the exact tie-break expectation
    (0.5) — equivalent to the infinite-draw mean of seeded random
    tie-breaking, and deterministic.
    """
    tie = set(a3_tie_trials)
    out: dict[str, dict[str, float | int]] = {}
    for arm, estimates in arm_values.items():
        common = sorted(set(estimates) & set(oracle_means))
        concordant = 0.0
        pairs = 0
        for i, a in enumerate(common):
            for b in common[i + 1 :]:
                if oracle_means[a] == oracle_means[b]:
                    continue
                pairs += 1
                if arm == "A3" and a in tie and b in tie:
                    concordant += 0.5
                    continue
                arm_order = estimates[a] - estimates[b]
                oracle_order = oracle_means[a] - oracle_means[b]
                if arm_order == 0.0:
                    concordant += 0.5
                elif (arm_order > 0) == (oracle_order > 0):
                    concordant += 1.0
        out[arm] = {"concordant": concordant, "pairs": pairs}
    return out


def campaign_oracle_spearman(
    cells: Mapping[str, Sequence[ReplayTrial]],
    full_arms_by_cell: Mapping[str, ArmEstimates],
    oracle_means_by_key: Mapping[str, float],
    config: ReplayConfig,
) -> dict[str, dict[str, Any]]:
    """Secondary oracle-recovery statistic (spec 31): campaign-level Spearman
    of pooled arm rankings vs oracle build means, under the pinned μ̂ + α̂
    cross-study alignment — each build's score is its arm estimate plus its
    own cell's mean opponent effect over the campaign's common opponent pool.

    Direction check only: n per campaign is the oracle panel's per-campaign
    build count; build-level bootstrap CIs; arms are not discriminable here.
    A3 scores are A2 with per-cell top-quartile values clamped to the
    ceiling (midrank ties).
    """
    by_campaign: dict[str, list[str]] = {}
    for cell in sorted(cells):
        by_campaign.setdefault(_campaign_of(cell), []).append(cell)

    out: dict[str, dict[str, Any]] = {}
    for campaign, members in by_campaign.items():
        common_opps = set.intersection(
            *(set(full_arms_by_cell[cell].beta_by_opponent) for cell in members)
        )
        if not common_opps:
            out[campaign] = {"status": "no_common_opponents"}
            continue
        arm_names = set.intersection(*(set(full_arms_by_cell[cell].values) for cell in members))
        per_arm: dict[str, Any] = {"common_opponents": len(common_opps)}
        for arm in sorted(arm_names | ({"A3"} if "A2" in arm_names else set())):
            aligned: list[float] = []
            oracle: list[float] = []
            for cell in members:
                arms = full_arms_by_cell[cell]
                base_arm = "A2" if arm == "A3" else arm
                values = dict(arms.values[base_arm])
                if arm == "A3" and arms.a3_tie_trials:
                    ceiling = min(values[t] for t in arms.a3_tie_trials if t in values)
                    values = {t: min(v, ceiling) for t, v in values.items()}
                beta_mean = float(np.mean([arms.beta_by_opponent[o] for o in sorted(common_opps)]))
                oracle_by_trial = {
                    t.trial_number: oracle_means_by_key[t.build_key]
                    for t in cells[cell]
                    if not t.pruned and t.build_key in oracle_means_by_key
                }
                for trial_number, oracle_mean in sorted(oracle_by_trial.items()):
                    if trial_number not in values:
                        continue
                    aligned.append(values[trial_number] + beta_mean)
                    oracle.append(oracle_mean)
            rho, _tau = _rank_corr(aligned, oracle)
            entry: dict[str, Any] = {"spearman": rho, "n_builds": len(aligned)}
            if rho is not None and len(aligned) >= MIN_BOOTSTRAP_N:
                rng = np.random.default_rng(config.bootstrap_seed)
                draws = []
                for _ in range(config.bootstrap_iterations):
                    idx = rng.integers(0, len(aligned), size=len(aligned))
                    d_rho, _ = _rank_corr([aligned[i] for i in idx], [oracle[i] for i in idx])
                    if d_rho is not None:
                        draws.append(d_rho)
                if draws:
                    entry["ci_low"] = float(np.percentile(draws, BOOTSTRAP_CI_PERCENTILES[0]))
                    entry["ci_high"] = float(np.percentile(draws, BOOTSTRAP_CI_PERCENTILES[1]))
            per_arm[arm] = entry
        out[campaign] = per_arm
    return out


def oracle_build_means(config: ReplayConfig) -> dict[str, float]:
    """Oracle panel build means keyed by build_key (honest-eval targets are a
    post-fit evaluation target here — spec 31 claim boundary)."""
    sums: dict[str, list[float]] = {}
    for row in load_honest_eval_matchups(config.db_path):
        if row.build_key is None:
            continue
        sums.setdefault(row.build_key, []).append(row.target)
    return {key: float(np.mean(values)) for key, values in sums.items()}


# ----------------------------------------------------- model fit / predict ---


def force_deterministic_predict(model: object) -> None:
    """Pin prediction determinism (spec 31): tree-vote-accumulating sklearn
    predictors go single-threaded; CatBoost parallelizes per sample and is
    deterministic under any thread count, so its wrapper is left alone."""
    pipeline = getattr(model, "pipeline", None)
    named_steps = getattr(pipeline, "named_steps", None)
    if named_steps and "model" in named_steps and hasattr(named_steps["model"], "n_jobs"):
        named_steps["model"].n_jobs = 1


@dataclass
class FeatureContext:
    """Per-run feature plumbing shared across cutoffs (records are cached by
    the baseline module's feature cache)."""

    baseline_config: Any
    build_lookup: Mapping[str, Any]


def _make_arm_model(arm: str, context: FeatureContext, config: ReplayConfig) -> Any:
    if arm in learned.MODEL_CHOICES:
        return learned.make_model(
            arm,
            learned.DEFAULT_HYPERPARAMETERS[arm],
            config.hpo_seed,
            model_thread_count=config.model_thread_count,
        )
    return baseline.make_model(arm, context.baseline_config)


def _fit_predict_scores(
    train_trials: Sequence[ReplayTrial],
    score_trials: Sequence[ReplayTrial],
    arm: str,
    config: ReplayConfig,
    context: FeatureContext,
) -> dict[int, float]:
    """Fit one arm on the training trials' realized rows; predict each score
    trial over its planned panel; return mean predicted target per trial."""
    train_rows = [row for trial in train_trials for row in trial.rows]
    train_bundle = baseline._feature_bundle(
        train_rows, context.build_lookup, context.baseline_config
    )
    model = _make_arm_model(arm, context, config)
    model.fit(train_bundle.rows, train_bundle.records, train_bundle.targets)
    force_deterministic_predict(model)

    all_panel_rows = [row for trial in score_trials for row in panel_rows(trial)]
    panel_bundle = baseline._feature_bundle(
        all_panel_rows, context.build_lookup, context.baseline_config
    )
    predictions = model.predict(panel_bundle.rows, panel_bundle.records).predictions
    scores: dict[int, list[float]] = {}
    for row, value in zip(panel_bundle.rows, predictions, strict=True):
        scores.setdefault(row.trial_number, []).append(float(value))
    return {trial: float(np.mean(values)) for trial, values in scores.items()}


def matchup_suite_record(
    train_trials: Sequence[ReplayTrial],
    block_trials: Sequence[ReplayTrial],
    arm: str,
    config: ReplayConfig,
    context: FeatureContext,
) -> dict[str, Any]:
    """Matchup-level evaluation-metric suite on the adjacent bucket's
    finalized trials' realized rows (bootstrap off — spec 31)."""
    train_rows = [row for trial in train_trials for row in trial.rows]
    test_rows = [row for trial in block_trials if not trial.pruned for row in trial.rows]
    if not train_rows or not test_rows:
        return {"status": "insufficient_rows"}
    train_bundle = baseline._feature_bundle(
        train_rows, context.build_lookup, context.baseline_config
    )
    test_bundle = baseline._feature_bundle(test_rows, context.build_lookup, context.baseline_config)
    model = _make_arm_model(arm, context, config)
    model.fit(train_bundle.rows, train_bundle.records, train_bundle.targets)
    force_deterministic_predict(model)
    predictions = model.predict(test_bundle.rows, test_bundle.records).predictions
    suite = baseline.evaluation_metric_suite(
        train_bundle,
        test_bundle,
        predictions,
        context.baseline_config,
        HEADLINE_TOP_K,
        include_bootstrap=False,
    )
    return {
        **baseline.regression_metrics(test_bundle.targets, predictions),
        "rank_metrics": suite["rank_metrics"],
        "skill_scores": suite["skill_scores"],
    }


# ------------------------------------------------------------ aggregation ---


def _campaign_of(cell: str) -> str:
    return cell.rsplit(":", 1)[0]


def stratified_cell_bootstrap(
    per_cell_values: Mapping[str, float],
    iterations: int,
    seed: int,
) -> dict[str, float | None]:
    """Campaign-stratified cluster bootstrap of the mean of per-cell values.

    Descriptive (spec 31): 15 clusters is at the edge of percentile coverage.
    """
    cells = sorted(per_cell_values)
    if not cells:
        return {"mean": None, "ci_low": None, "ci_high": None}
    by_campaign: dict[str, list[str]] = {}
    for cell in cells:
        by_campaign.setdefault(_campaign_of(cell), []).append(cell)
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(iterations):
        sample: list[float] = []
        for members in by_campaign.values():
            draws = rng.integers(0, len(members), size=len(members))
            sample.extend(per_cell_values[members[i]] for i in draws)
        means.append(float(np.mean(sample)))
    return {
        "mean": float(np.mean([per_cell_values[c] for c in cells])),
        "ci_low": float(np.percentile(means, BOOTSTRAP_CI_PERCENTILES[0])),
        "ci_high": float(np.percentile(means, BOOTSTRAP_CI_PERCENTILES[1])),
    }


# --------------------------------------------------------------- artifact ---


def _code_version() -> str:
    return learned._code_version()


def artifact_skeleton(config: ReplayConfig, inflight_gaps: Mapping[str, int]) -> dict[str, object]:
    config_echo = {
        key: (
            str(value)
            if isinstance(value, Path)
            else list(value)
            if isinstance(value, tuple)
            else value
        )
        for key, value in asdict(config).items()
        if key not in ("twfe", "eb")
    }
    config_echo["twfe"] = asdict(config.twfe)
    config_echo["eb"] = asdict(config.eb)
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": baseline.FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "db_path": str(config.db_path),
        "log_base_dir": str(config.log_base_dir),
        "study_db_root": str(config.study_db_root),
        "game_dir": str(config.game_dir),
        "code_version": _code_version(),
        "dependency_extra": learned.DEFAULT_DEPENDENCY_EXTRA,
        "hpo_seed": config.hpo_seed,
        "config": config_echo,
        "claim_boundary": {
            "claim_label": CLAIM_LABEL,
            "honest_eval_usage": HONEST_EVAL_USAGE,
            "target_variable": learned.TARGET_VARIABLE,
            "headline_arm": HEADLINE_ARM,
            "headline_gap_mode": HEADLINE_GAP_MODE,
            "headline_top_k": HEADLINE_TOP_K,
            "gating_target_primary": GATING_TARGET_ARMS[0],
            "caveats": [
                "filtering fidelity only: the counterfactual TPE trajectory under "
                "actual skips is unmeasurable from logs",
                "forward-deployment evidence over later proposals of the same "
                "studies; not novel-build or cross-hull evidence",
            ],
        },
        "reused_source_data": True,
        "inflight_gap_trials": dict(sorted(inflight_gaps.items())),
    }


# ------------------------------------------------------------ orchestration ---


def _fidelity_arms(config: ReplayConfig) -> tuple[str, ...]:
    """Arms scored in the fidelity pass: learned families + all comparator-gate
    families. The matchup-level metric suite runs on GATING_ARMS only (cost)."""
    return tuple(config.learned_models) + tuple(baseline.MODEL_CHOICES)


def run_cell(
    cell: str,
    trials: Sequence[ReplayTrial],
    config: ReplayConfig,
    context: FeatureContext,
    oracle_means_by_key: Mapping[str, float],
    inflight_gap: int,
    composites: Mapping[int, float],
    full_arms: ArmEstimates,
) -> dict[str, Any]:
    cutoffs = cutoff_indices(len(trials), config)
    gap_by_mode = {"zero": 0, "measured": inflight_gap}
    t2_target = full_arms.values["A1"]
    oracle_means = {
        t.trial_number: oracle_means_by_key[t.build_key]
        for t in trials
        if not t.pruned and t.build_key in oracle_means_by_key
    }

    fidelity: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    suite_records: dict[str, dict[str, list[dict[str, Any]]]] = {}
    gating: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    convergence: dict[str, list[dict[str, Any]]] = {arm: [] for arm in full_arms.values}

    for mode in config.train_gap_modes:
        gap = gap_by_mode[mode]
        fidelity[mode] = {}
        suite_records[mode] = {}
        for arm in _fidelity_arms(config):
            per_bucket: dict[str, list[dict[str, Any]]] = {}
            suite_rows: list[dict[str, Any]] = []
            for cutoff in cutoffs:
                train = training_trials(trials, cutoff, gap, frozenset())
                if not train:
                    continue
                future = trials[cutoff:]
                predicted = _fit_predict_scores(train, future, arm, config, context)
                buckets = bucket_assignments(len(future), config)
                for label, offsets in buckets.items():
                    bucket_trials = [future[i] for i in offsets]
                    if not bucket_trials:
                        continue
                    record = fidelity_record(bucket_trials, predicted, t2_target)
                    record["cutoff"] = cutoff
                    per_bucket.setdefault(label, []).append(record)
                if arm in GATING_ARMS:
                    adjacent = [future[i] for i in buckets[next(iter(buckets))]]
                    suite = matchup_suite_record(train, adjacent, arm, config, context)
                    suite["cutoff"] = cutoff
                    suite_rows.append(suite)
            fidelity[mode][arm] = per_bucket
            if arm in GATING_ARMS:
                suite_records[mode][arm] = suite_rows

        gating[mode] = {}
        for arm in GATING_ARMS:

            def _predict_block(
                train: Sequence[ReplayTrial],
                block: Sequence[ReplayTrial],
                _arm: str = arm,
            ) -> dict[int, float]:
                return _fit_predict_scores(train, block, _arm, config, context)

            per_q: dict[str, dict[str, Any]] = {}
            for fraction in config.gating_fractions:
                result = run_gating(
                    trials,
                    cutoffs,
                    config,
                    gap,
                    fraction,
                    _predict_block,
                    remove_skipped=True,
                )
                result["regret"] = gating_regret(
                    result["skipped_trials"],
                    {t: full_arms.values[t] for t in GATING_TARGET_ARMS if t in full_arms.values},
                    config.top_k_values,
                )
                result["oracle_skipped"] = {
                    str(t): oracle_means[t] for t in result["skipped_trials"] if t in oracle_means
                }
                per_q[str(fraction)] = result
            if arm == HEADLINE_ARM:
                sensitivity = run_gating(
                    trials,
                    cutoffs,
                    config,
                    gap,
                    config.gating_sensitivity_fraction,
                    _predict_block,
                    remove_skipped=False,
                )
                sensitivity["regret"] = gating_regret(
                    sensitivity["skipped_trials"],
                    {t: full_arms.values[t] for t in GATING_TARGET_ARMS if t in full_arms.values},
                    config.top_k_values,
                )
                per_q["keep_skipped_sensitivity"] = sensitivity
            gating[mode][arm] = per_q

    for cutoff in cutoffs:
        prefix = trials[:cutoff]
        prefix_composites = {
            t: composites[t] for t in composites if t in {p.trial_number for p in prefix}
        }
        prefix_arms = estimator_arm_estimates(prefix, prefix_composites, config)
        for arm, estimates in prefix_arms.values.items():
            full = full_arms.values.get(arm, {})
            common = sorted(set(estimates) & set(full))
            rho, _tau = _rank_corr([estimates[t] for t in common], [full[t] for t in common])
            convergence[arm].append({"cutoff": cutoff, "spearman_vs_full": rho})

    # Concordance covers the full ESTIMATOR_ARMS registry; A3 ranks via A2
    # (ceiling ties handled inside the concordance function).
    concordance_values = {
        arm: full_arms.values["A2" if arm == "A3" else arm]
        for arm in ESTIMATOR_ARMS
        if ("A2" if arm == "A3" else arm) in full_arms.values
    }
    concordance = within_cell_concordance(
        concordance_values,
        oracle_means,
        full_arms.a3_tie_trials,
    )

    a3_agreement: dict[str, float] = {}
    for k in config.top_k_values:
        a2_top = set(deterministic_top_k(full_arms.values["A2"], k))
        draws = a3_top_k_draws(
            full_arms.values["A2"],
            full_arms.a3_tie_trials,
            k,
            config.tie_break_draws,
            config.bootstrap_seed,
        )
        if a2_top and draws:
            a3_agreement[str(k)] = float(
                np.mean([len(set(draw) & a2_top) / len(a2_top) for draw in draws])
            )

    return {
        "campaign": _campaign_of(cell),
        "seed": int(cell.rsplit(":", 1)[1]),
        "n_trials": len(trials),
        "n_rankable": sum(1 for t in trials if not t.pruned),
        "n_rows": sum(len(t.rows) for t in trials),
        "cutoffs": list(cutoffs),
        "fidelity": fidelity,
        "matchup_suite": suite_records,
        "gating": gating,
        "pruner_reference_rows_avoided": pruner_rows_avoided(trials),
        "arm_estimates_full": {
            arm: {str(t): v for t, v in estimates.items()}
            for arm, estimates in full_arms.values.items()
        },
        "arm_diagnostics": full_arms.diagnostics,
        "a3_tie_trials": list(full_arms.a3_tie_trials),
        "arm_convergence": convergence,
        "a3_ceiling_topk_agreement": a3_agreement,
        "oracle_concordance": concordance,
        "oracle_build_means": {str(t): v for t, v in oracle_means.items()},
    }


def _aggregate(
    cells_payload: Mapping[str, Mapping[str, Any]], config: ReplayConfig
) -> dict[str, Any]:
    aggregates: dict[str, Any] = {}

    # Fidelity: per (gap mode, arm, bucket) — cell means, then stratified
    # cluster bootstrap over cells; support = contributing (cell, cutoff)
    # counts.
    fidelity_agg: dict[str, dict[str, dict[str, Any]]] = {}
    for mode in config.train_gap_modes:
        fidelity_agg[mode] = {}
        for arm in set().union(
            *(set(payload["fidelity"].get(mode, {})) for payload in cells_payload.values())
        ):
            per_bucket_agg: dict[str, Any] = {}
            bucket_labels = set()
            for payload in cells_payload.values():
                bucket_labels.update(payload["fidelity"].get(mode, {}).get(arm, {}))
            for label in sorted(bucket_labels):
                per_cell: dict[str, float] = {}
                support = 0
                for cell, payload in cells_payload.items():
                    records = payload["fidelity"].get(mode, {}).get(arm, {}).get(label, [])
                    values = [r["t1_spearman"] for r in records if r["t1_spearman"] is not None]
                    support += len(values)
                    if values:
                        per_cell[cell] = float(np.mean(values))
                per_bucket_agg[label] = {
                    **stratified_cell_bootstrap(
                        per_cell, config.bootstrap_iterations, config.bootstrap_seed
                    ),
                    "support_cell_cutoffs": support,
                    "cells": len(per_cell),
                }
            fidelity_agg[mode][arm] = per_bucket_agg
    aggregates["fidelity_t1_spearman"] = fidelity_agg

    # Headline: per-cell q* for the headline arm at the measured gap, plus
    # the opponent_mean null.
    headline: dict[str, Any] = {
        "arm": HEADLINE_ARM,
        "gap_mode": HEADLINE_GAP_MODE,
        "top_k": HEADLINE_TOP_K,
        "gating_target": GATING_TARGET_ARMS[0],
    }
    for name, arm in (("per_cell_q_star", HEADLINE_ARM), ("null_per_cell_q_star", "opponent_mean")):
        per_cell_q = {}
        for cell, payload in cells_payload.items():
            per_q = payload["gating"].get(HEADLINE_GAP_MODE, {}).get(arm, {})
            regrets = {
                q: result["regret"][GATING_TARGET_ARMS[0]]
                for q, result in per_q.items()
                if q != "keep_skipped_sensitivity" and GATING_TARGET_ARMS[0] in result["regret"]
            }
            if regrets:
                per_cell_q[cell] = q_star(regrets, HEADLINE_TOP_K)
        headline[name] = per_cell_q
        headline[name.replace("per_cell_q_star", "median_q_star")] = (
            float(np.median(list(per_cell_q.values()))) if per_cell_q else None
        )
    aggregates["headline"] = headline

    # Oracle recovery: pooled within-cell pairwise concordance per arm
    # (direction check only — cannot discriminate arms at this panel size).
    concordance_agg: dict[str, Any] = {}
    arm_names: set[str] = set()
    for payload in cells_payload.values():
        arm_names.update(payload["oracle_concordance"])
    for arm in sorted(arm_names):
        concordant = 0.0
        pairs = 0
        per_cell_frac: dict[str, float] = {}
        for cell, payload in cells_payload.items():
            stats = payload["oracle_concordance"].get(arm)
            if not stats or stats["pairs"] == 0:
                continue
            concordant += stats["concordant"]
            pairs += stats["pairs"]
            per_cell_frac[cell] = stats["concordant"] / stats["pairs"]
        concordance_agg[arm] = {
            "concordant": concordant,
            "pairs": pairs,
            "fraction": (concordant / pairs) if pairs else None,
            "cell_bootstrap": stratified_cell_bootstrap(
                per_cell_frac, config.bootstrap_iterations, config.bootstrap_seed
            ),
        }
    aggregates["oracle_recovery"] = {
        "pairwise_concordance": concordance_agg,
        "note": "direction check only; within-cell pairs, arms not discriminable",
    }

    return aggregates


def run_replay(config: ReplayConfig) -> dict[str, Any]:
    _progress(f"loading matchup rows from {config.db_path}", config.progress)
    rows = load_training_matchups(config.db_path)
    log_trials = load_log_trials(sorted({row.source_path for row in rows}), config.log_base_dir)
    cells = build_replay_cells(rows, log_trials)
    if config.max_cells is not None:
        cells = dict(list(cells.items())[: config.max_cells])

    build_lookup = {b.build_key: b.build for b in load_recovered_builds(config.db_path)}
    baseline_config = baseline.BaselineConfig(
        db_path=config.db_path,
        game_dir=config.game_dir,
        split="forward-time",
        model="random_forest",
        holdout_fraction=baseline.DEFAULT_HOLDOUT_FRACTION,
        train_fraction=baseline.DEFAULT_TRAIN_FRACTION,
        seed=config.hpo_seed,
        tree_count=baseline.DEFAULT_TREE_COUNT,
        ridge_alpha=baseline.DEFAULT_RIDGE_ALPHA,
        max_rows=None,
        top_k_values=config.top_k_values,
        progress=False,
        feature_profile=config.feature_profile,
    )
    context = FeatureContext(baseline_config=baseline_config, build_lookup=build_lookup)
    oracle_means_by_key = oracle_build_means(config)

    inflight_gaps: dict[str, int] = {}
    for cell, trials in cells.items():
        inflight_gaps[cell] = (
            measured_inflight_gap(trials, config) if "measured" in config.train_gap_modes else 0
        )

    composites_by_cell = {cell: composite_scores(trials, config) for cell, trials in cells.items()}
    full_arms_by_cell = {
        cell: estimator_arm_estimates(trials, composites_by_cell[cell], config)
        for cell, trials in cells.items()
    }

    payload = artifact_skeleton(config, inflight_gaps)
    cells_payload: dict[str, dict[str, Any]] = {}
    for index, (cell, trials) in enumerate(cells.items(), start=1):
        _progress(
            f"cell {index}/{len(cells)} {cell}: {len(trials)} trials, gap={inflight_gaps[cell]}",
            config.progress,
        )
        cells_payload[cell] = run_cell(
            cell,
            trials,
            config,
            context,
            oracle_means_by_key,
            inflight_gaps[cell],
            composites_by_cell[cell],
            full_arms_by_cell[cell],
        )
    payload["cells"] = cells_payload
    aggregates = _aggregate(cells_payload, config)
    aggregates["oracle_recovery"]["campaign_rank"] = campaign_oracle_spearman(
        cells, full_arms_by_cell, oracle_means_by_key, config
    )
    payload["aggregates"] = aggregates

    if config.output is not None:
        learned._write_json_payload(config.output, payload)
        _progress(f"wrote {config.output}", config.progress)
    return payload


# -------------------------------------------------------------------- CLI ---


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prequential replay ablation over the frozen wave-1 matchup DB."
    )
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--log-base-dir",
        type=Path,
        default=Path("."),
        help="Base directory against which DB source_path values resolve.",
    )
    parser.add_argument("--study-db-root", type=Path, default=Path("data/study_dbs"))
    parser.add_argument("--min-train-trials", type=int, default=DEFAULT_MIN_TRAIN_TRIALS)
    parser.add_argument("--cutoff-stride", type=int, default=DEFAULT_CUTOFF_STRIDE)
    parser.add_argument("--min-future-trials", type=int, default=DEFAULT_MIN_FUTURE_TRIALS)
    parser.add_argument("--hpo-seed", type=int, default=DEFAULT_HPO_SEED)
    parser.add_argument("--model-thread-count", type=int, default=DEFAULT_MODEL_THREAD_COUNT)
    parser.add_argument("--max-cells", type=int, default=None)
    parser.add_argument("--no-progress", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> ReplayConfig:
    return ReplayConfig(
        db_path=args.db_path,
        game_dir=args.game_dir,
        output=args.output,
        log_base_dir=args.log_base_dir,
        study_db_root=args.study_db_root,
        min_train_trials=args.min_train_trials,
        cutoff_stride=args.cutoff_stride,
        min_future_trials=args.min_future_trials,
        hpo_seed=args.hpo_seed,
        model_thread_count=args.model_thread_count,
        max_cells=args.max_cells,
        progress=not args.no_progress,
    )


def main() -> None:
    args = build_parser().parse_args()
    config = config_from_args(args)
    payload = run_replay(config)
    if config.output is None:
        print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
