"""Phase 7 surrogate evaluation-metric suite (spec 31).

Rank-fidelity metrics for the learned-surrogate harness: per-opponent rank
correlations with noise-floor tie handling, build-aggregate ranking with
precision@k / regret@k, skill scores, panel target statistics, and two-way
cluster-bootstrap confidence intervals.

Degenerate-value rule: any statistic whose denominator (variance, MSE, range)
is degenerate, or whose inputs are constant, is emitted as ``None`` with a
named counter — never ``inf``/``NaN``. Every output survives ``json.dumps``.

This module never imports from ``scripts/``; caller-specific values (for
example the primary top-k) arrive as parameters.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
from scipy.stats import kendalltau, spearmanr

from .phase7_matchup_data import HonestEvalMatchupRow


@dataclass(frozen=True)
class EvalMetricsConfig:
    min_builds_per_opponent: int = 5
    min_opponents_per_build: int = 3
    top_fraction: float = 0.1
    min_top_fraction_rows: int = 3
    bootstrap_resamples: int = 500
    bootstrap_seed: int = 7717
    bootstrap_ci_level: float = 0.95
    noise_floor_override: float | None = None
    noise_floor_fallback: float = 0.05
    degenerate_denominator_epsilon: float = 1e-12


MIN_REPLICATES_PER_GROUP = 2  # algorithm-inherent: an SD needs two draws
MIN_CORRELATION_POINTS = 2  # algorithm-inherent: a correlation needs two points


def sample_sd(values: Sequence[float] | np.ndarray) -> float:
    """Sample SD (ddof=1) matching the noise floor's derivation; fewer than
    two observations is degenerate by definition and returns 0.0."""
    if len(values) < MIN_REPLICATES_PER_GROUP:
        return 0.0
    return float(np.std(np.asarray(values, dtype=float), ddof=1))


def _collapse_cells(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    """Collapse replicate rows to one (build, opponent) cell mean.

    Replicated panels (honest-eval: 30 replicates per cell) must not weight
    aggregates by replicate multiplicity, satisfy panel-size gates via
    replicates, or feed duplicate rows into rank statistics.
    """
    true_sums: dict[tuple[str, str], float] = defaultdict(float)
    pred_sums: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for build, opponent, truth, pred in zip(builds, opponents, y_true, y_pred, strict=True):
        cell = (build, opponent)
        true_sums[cell] += float(truth)
        pred_sums[cell] += float(pred)
        counts[cell] += 1
    cells = sorted(counts)
    return (
        [cell[0] for cell in cells],
        [cell[1] for cell in cells],
        np.asarray([true_sums[cell] / counts[cell] for cell in cells]),
        np.asarray([pred_sums[cell] / counts[cell] for cell in cells]),
    )


def noise_floor_from_replicates(
    rows: Sequence[HonestEvalMatchupRow],
) -> dict[str, object]:
    """Median within-(build, opponent) target SD over replicated groups."""
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        build = row.build_key or row.build_id
        grouped[(build, row.opponent_variant_id)].append(float(row.target))
    sds = [
        float(np.std(targets, ddof=1))
        for targets in grouped.values()
        if len(targets) >= MIN_REPLICATES_PER_GROUP
    ]
    return {
        "noise_floor": float(np.median(sds)) if sds else None,
        "n_groups": len(sds),
        "source": "honest_eval_replicates",
    }


def resolve_noise_floor(
    config: EvalMetricsConfig,
    honest_eval_rows: Sequence[HonestEvalMatchupRow],
) -> dict[str, object]:
    """Resolution order: override -> replicate-derived -> fallback.

    The resolved floor must be positive: it defines tie handling and opponent
    exclusion, and a nonpositive floor would disable both silently.
    """
    if config.noise_floor_override is not None:
        if config.noise_floor_override <= 0.0:
            raise ValueError(
                f"noise_floor_override must be > 0, got {config.noise_floor_override}"
            )
        return {"noise_floor": float(config.noise_floor_override), "source": "override"}
    derived = noise_floor_from_replicates(honest_eval_rows)
    floor = derived["noise_floor"]
    if isinstance(floor, float) and floor > 0.0:
        return derived
    if config.noise_floor_fallback <= 0.0:
        raise ValueError(
            f"noise_floor_fallback must be > 0, got {config.noise_floor_fallback}"
        )
    return {"noise_floor": float(config.noise_floor_fallback), "source": "fallback"}


def _finite_or_none(value: float) -> float | None:
    value = float(value)
    return value if np.isfinite(value) else None


def _safe_spearman(y_true: np.ndarray, y_pred: np.ndarray, epsilon: float) -> float | None:
    if len(y_true) < MIN_CORRELATION_POINTS:
        return None
    if np.ptp(y_true) < epsilon or np.ptp(y_pred) < epsilon:
        return None
    rho, _ = spearmanr(y_true, y_pred)
    return _finite_or_none(rho)


def _safe_kendall(y_true: np.ndarray, y_pred: np.ndarray, epsilon: float) -> float | None:
    if len(y_true) < MIN_CORRELATION_POINTS:
        return None
    if np.ptp(y_true) < epsilon or np.ptp(y_pred) < epsilon:
        return None
    tau, _ = kendalltau(y_true, y_pred)
    return _finite_or_none(tau)


def _opponent_row_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    noise_floor: float,
    config: EvalMetricsConfig,
) -> dict[str, object]:
    epsilon = config.degenerate_denominator_epsilon
    quantized = np.round(y_true / noise_floor)
    top_cut = np.quantile(y_true, 1.0 - config.top_fraction)
    top_mask = y_true >= top_cut
    top_metric = None
    if int(top_mask.sum()) >= config.min_top_fraction_rows:
        top_metric = _safe_kendall(y_true[top_mask], y_pred[top_mask], epsilon)
    return {
        "n": int(len(y_true)),
        "target_sd": sample_sd(y_true),
        "spearman": _safe_spearman(y_true, y_pred, epsilon),
        "kendall": _safe_kendall(y_true, y_pred, epsilon),
        "sparse_kendall": _safe_kendall(quantized, y_pred, epsilon),
        "top_fraction_kendall": top_metric,
    }


def _aggregate(values: Iterable[float | None]) -> tuple[float | None, float | None]:
    finite = [value for value in values if value is not None]
    if not finite:
        return None, None
    return float(np.mean(finite)), float(np.median(finite))


def per_opponent_rank_metrics(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    noise_floor: float,
    config: EvalMetricsConfig,
) -> dict[str, object]:
    """Within-opponent rank metrics over held-out builds (spec 31 / review C1).

    Replicate rows are collapsed to (build, opponent) cell means first, so
    panel gates count distinct builds and rank statistics never see
    duplicated rows.
    """
    builds, opponents, y_true, y_pred = _collapse_cells(builds, opponents, y_true, y_pred)
    indices: dict[str, list[int]] = defaultdict(list)
    for idx, opponent in enumerate(opponents):
        indices[opponent].append(idx)
    per_opponent: dict[str, dict[str, object]] = {}
    excluded_small_n = 0
    excluded_low_variance = 0
    null_prediction_degenerate = 0
    for opponent in sorted(indices):
        rows = np.asarray(indices[opponent])
        truths = y_true[rows]
        preds = y_pred[rows]
        if len(rows) < config.min_builds_per_opponent:
            excluded_small_n += 1
            continue
        if sample_sd(truths) < noise_floor:
            excluded_low_variance += 1
            continue
        metrics = _opponent_row_metrics(truths, preds, noise_floor, config)
        if metrics["spearman"] is None and np.ptp(preds) < config.degenerate_denominator_epsilon:
            null_prediction_degenerate += 1
        per_opponent[opponent] = metrics
    mean_spearman, median_spearman = _aggregate(
        row["spearman"] for row in per_opponent.values()
    )
    mean_kendall, median_kendall = _aggregate(
        row["kendall"] for row in per_opponent.values()
    )
    mean_sparse_kendall, median_sparse_kendall = _aggregate(
        row["sparse_kendall"] for row in per_opponent.values()
    )
    mean_top_fraction_kendall, median_top_fraction_kendall = _aggregate(
        row["top_fraction_kendall"] for row in per_opponent.values()
    )
    return {
        "noise_floor": float(noise_floor),
        "per_opponent": per_opponent,
        "included_opponents": len(per_opponent),
        "excluded_small_n": excluded_small_n,
        "excluded_low_variance": excluded_low_variance,
        "null_prediction_degenerate": null_prediction_degenerate,
        "mean_spearman": mean_spearman,
        "median_spearman": median_spearman,
        "mean_kendall": mean_kendall,
        "median_kendall": median_kendall,
        "mean_sparse_kendall": mean_sparse_kendall,
        "median_sparse_kendall": median_sparse_kendall,
        "mean_top_fraction_kendall": mean_top_fraction_kendall,
        "median_top_fraction_kendall": median_top_fraction_kendall,
    }


def _build_aggregates(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    degenerate_opponents: frozenset[str] | set[str],
    min_opponents_per_build: int,
    build_subset: set[str] | None = None,
    opponent_weights: Mapping[str, int] | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, int], int]:
    """Per-build weighted aggregates over non-degenerate opponents.

    ``opponent_weights`` carries bootstrap draw multiplicity (weights enter at
    the aggregation level only — never inside a rank statistic).
    """
    true_sums: dict[str, float] = defaultdict(float)
    pred_sums: dict[str, float] = defaultdict(float)
    weight_sums: dict[str, float] = defaultdict(float)
    panel_counts: dict[str, int] = defaultdict(int)
    for build, opponent, truth, pred in zip(builds, opponents, y_true, y_pred, strict=True):
        if opponent in degenerate_opponents:
            continue
        if build_subset is not None and build not in build_subset:
            continue
        weight = 1 if opponent_weights is None else opponent_weights.get(opponent, 0)
        if weight == 0:
            continue
        true_sums[build] += weight * float(truth)
        pred_sums[build] += weight * float(pred)
        weight_sums[build] += weight
        panel_counts[build] += 1
    true_agg: dict[str, float] = {}
    pred_agg: dict[str, float] = {}
    panels: dict[str, int] = {}
    excluded_small_panel = 0
    for build, weight in weight_sums.items():
        if panel_counts[build] < min_opponents_per_build:
            excluded_small_panel += 1
            continue
        true_agg[build] = true_sums[build] / weight
        pred_agg[build] = pred_sums[build] / weight
        panels[build] = panel_counts[build]
    return true_agg, pred_agg, panels, excluded_small_panel


def _top_k_order(aggregates: Mapping[str, float]) -> list[str]:
    """Deterministic ranking: descending value, then ascending build key."""
    return sorted(aggregates, key=lambda build: (-aggregates[build], build))


def _precision_regret(
    true_agg: Mapping[str, float],
    pred_agg: Mapping[str, float],
    k: int,
    epsilon: float,
) -> tuple[float, float, float | None]:
    true_order = _top_k_order(true_agg)
    pred_order = _top_k_order(pred_agg)
    bounded_k = min(k, len(true_order))
    true_top = set(true_order[:bounded_k])
    pred_top = pred_order[:bounded_k]
    precision = len(true_top & set(pred_top)) / bounded_k
    best_true = true_agg[true_order[0]]
    best_in_pred_top = max(true_agg[build] for build in pred_top)
    raw_regret = best_true - best_in_pred_top
    value_range = best_true - min(true_agg.values())
    normalized = raw_regret / value_range if value_range > epsilon else None
    return precision, raw_regret, normalized


def build_aggregate_rank_metrics(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    degenerate_opponents: frozenset[str] | set[str],
    k_values: Sequence[int],
    config: EvalMetricsConfig,
) -> dict[str, object]:
    """Build-level ranking metrics over per-build opponent-mean aggregates.

    Replicate rows are collapsed to (build, opponent) cell means first, so
    per-build panel sizes count distinct opponents.
    """
    builds, opponents, y_true, y_pred = _collapse_cells(builds, opponents, y_true, y_pred)
    true_agg, pred_agg, panels, excluded_small_panel = _build_aggregates(
        builds, opponents, y_true, y_pred,
        degenerate_opponents, config.min_opponents_per_build,
    )
    out: dict[str, object] = {
        "n_builds": len(true_agg),
        "excluded_small_panel": excluded_small_panel,
        "panel_sizes": (
            {
                "min": int(min(panels.values())),
                "median": float(np.median(list(panels.values()))),
                "max": int(max(panels.values())),
            }
            if panels
            else None
        ),
        "spearman": None,
        "kendall": None,
        "precision_at_k": {},
        "regret_at_k": {},
    }
    if not true_agg:
        return out
    epsilon = config.degenerate_denominator_epsilon
    ordered_builds = sorted(true_agg)
    true_values = np.asarray([true_agg[build] for build in ordered_builds])
    pred_values = np.asarray([pred_agg[build] for build in ordered_builds])
    out["spearman"] = _safe_spearman(true_values, pred_values, epsilon)
    out["kendall"] = _safe_kendall(true_values, pred_values, epsilon)
    precision_at_k: dict[str, float] = {}
    regret_at_k: dict[str, dict[str, float | None]] = {}
    for k in k_values:
        precision, raw, normalized = _precision_regret(
            true_agg, pred_agg, k, config.degenerate_denominator_epsilon
        )
        precision_at_k[str(k)] = precision
        regret_at_k[str(k)] = {"raw": raw, "normalized": normalized}
    out["precision_at_k"] = precision_at_k
    out["regret_at_k"] = regret_at_k
    return out


def skill_scores(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    train_target_mean: float,
    config: EvalMetricsConfig,
) -> dict[str, object]:
    """Skill relative to the training-mean predictor (review H2)."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    if len(y_true) == 0:
        return {"mse_model": None, "mse_train_mean": None, "skill": None}
    mse_model = float(np.mean((y_true - y_pred) ** 2))
    mse_train_mean = float(np.mean((y_true - float(train_target_mean)) ** 2))
    skill = (
        1.0 - mse_model / mse_train_mean
        if mse_train_mean > config.degenerate_denominator_epsilon
        else None
    )
    return {
        "mse_model": mse_model,
        "mse_train_mean": mse_train_mean,
        "skill": skill,
    }


def panel_target_stats(y_true: np.ndarray) -> dict[str, object]:
    """Panel context required whenever raw RMSE is reported (review H2)."""
    y_true = np.asarray(y_true, dtype=float)
    n = int(len(y_true))
    return {
        "n": n,
        "mean": float(np.mean(y_true)) if n else None,
        "sd": float(np.std(y_true)) if n else None,
        "endpoint_mass_low": float(np.mean(y_true == -1.0)) if n else None,
        "endpoint_mass_high": float(np.mean(y_true == 1.0)) if n else None,
    }


def _percentile_ci(
    values: list[float], ci_level: float
) -> dict[str, object]:
    if not values:
        return {"ci_low": None, "ci_high": None, "n_finite": 0}
    tail = (1.0 - ci_level) / 2.0
    return {
        "ci_low": float(np.quantile(values, tail)),
        "ci_high": float(np.quantile(values, 1.0 - tail)),
        "n_finite": len(values),
    }


def two_way_cluster_bootstrap(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    noise_floor: float,
    degenerate_opponents: frozenset[str] | set[str],
    primary_k: int,
    config: EvalMetricsConfig,
) -> dict[str, object]:
    """Pigeonhole-style two-way cluster bootstrap for the headline statistics.

    Builds and opponents are resampled with replacement. Draw multiplicity
    enters only at aggregation level (outer means, aggregate weights); rank
    statistics always run over distinct clusters, because duplicated rows
    manufacture ties that bias rank correlations downward. CIs are therefore
    descriptive spread, not calibrated standard errors.
    """
    builds, opponents, y_true, y_pred = _collapse_cells(builds, opponents, y_true, y_pred)
    epsilon = config.degenerate_denominator_epsilon
    unique_builds = sorted(set(builds))
    unique_opponents = sorted(set(opponents))
    opponent_indices: dict[str, list[int]] = defaultdict(list)
    for idx, opponent in enumerate(opponents):
        opponent_indices[opponent].append(idx)
    build_array = np.asarray(builds, dtype=object)

    samples: dict[str, list[float]] = {
        "mean_per_opponent_spearman": [],
        "build_aggregate_spearman": [],
        "precision_at_k": [],
        "regret_at_k": [],
    }
    for resample_idx in range(config.bootstrap_resamples):
        rng = np.random.default_rng(config.bootstrap_seed + resample_idx)
        drawn_builds = set(
            rng.choice(unique_builds, size=len(unique_builds), replace=True).tolist()
        )
        opponent_draw = Counter(
            rng.choice(unique_opponents, size=len(unique_opponents), replace=True).tolist()
        )
        # Mean per-opponent Spearman: opponent multiplicity weights the mean.
        weighted_sum = 0.0
        weight_total = 0
        for opponent, weight in opponent_draw.items():
            if opponent in degenerate_opponents:
                continue
            rows = [
                idx
                for idx in opponent_indices[opponent]
                if build_array[idx] in drawn_builds
            ]
            if len(rows) < config.min_builds_per_opponent:
                continue
            truths = y_true[rows]
            if sample_sd(truths) < noise_floor:
                continue
            rho = _safe_spearman(truths, y_pred[rows], epsilon)
            if rho is None:
                continue
            weighted_sum += weight * rho
            weight_total += weight
        if weight_total:
            samples["mean_per_opponent_spearman"].append(weighted_sum / weight_total)
        # Build aggregates: opponent multiplicity weights the aggregate.
        true_agg, pred_agg, _, _ = _build_aggregates(
            builds, opponents, y_true, y_pred,
            degenerate_opponents, config.min_opponents_per_build,
            build_subset=drawn_builds, opponent_weights=opponent_draw,
        )
        if true_agg:
            ordered = sorted(true_agg)
            rho = _safe_spearman(
                np.asarray([true_agg[b] for b in ordered]),
                np.asarray([pred_agg[b] for b in ordered]),
                epsilon,
            )
            if rho is not None:
                samples["build_aggregate_spearman"].append(rho)
            precision, raw_regret, _ = _precision_regret(
                true_agg, pred_agg, primary_k, config.degenerate_denominator_epsilon
            )
            samples["precision_at_k"].append(precision)
            samples["regret_at_k"].append(raw_regret)
    return {
        name: {**_percentile_ci(values, config.bootstrap_ci_level), "k": primary_k}
        if name in ("precision_at_k", "regret_at_k")
        else _percentile_ci(values, config.bootstrap_ci_level)
        for name, values in samples.items()
    }


def honest_eval_build_metrics(
    builds: Sequence[str],
    opponents: Sequence[str],
    y_true: np.ndarray,
    y_pred: np.ndarray,
    degenerate_opponents: frozenset[str] | set[str],
    outer_train_build_keys: frozenset[str] | set[str],
    k_values: Sequence[int],
    primary_k: int,
    config: EvalMetricsConfig,
) -> dict[str, object]:
    """Honest-eval diagnostic at the build level (review H3).

    NOT clean holdout: ``outer_train_build_overlap`` records how many
    honest-eval builds also appear in outer-train. Replicate rows are
    collapsed to (build, opponent) cell means first.
    """
    builds, opponents, y_true, y_pred = _collapse_cells(builds, opponents, y_true, y_pred)
    epsilon = config.degenerate_denominator_epsilon
    true_agg, pred_agg, _, excluded_small_panel = _build_aggregates(
        builds, opponents, y_true, y_pred,
        degenerate_opponents, config.min_opponents_per_build,
    )
    n_builds = len(true_agg)
    out: dict[str, object] = {
        "n_builds": n_builds,
        "excluded_small_panel": excluded_small_panel,
        "outer_train_build_overlap": len(set(true_agg) & set(outer_train_build_keys)),
        "spearman": None,
        "kendall": None,
        "precision_at_k": {},
        "chance_level": {},
        "regret_at_k": {},
        "overlap_curve": [],
        "bootstrap": {},
    }
    if not true_agg:
        return out
    ordered = sorted(true_agg)
    true_values = np.asarray([true_agg[b] for b in ordered])
    pred_values = np.asarray([pred_agg[b] for b in ordered])
    out["spearman"] = _safe_spearman(true_values, pred_values, epsilon)
    out["kendall"] = _safe_kendall(true_values, pred_values, epsilon)
    for k in k_values:
        precision, raw, normalized = _precision_regret(
            true_agg, pred_agg, k, config.degenerate_denominator_epsilon
        )
        out["precision_at_k"][str(k)] = precision
        out["chance_level"][str(k)] = min(k, n_builds) / n_builds
        out["regret_at_k"][str(k)] = {"raw": raw, "normalized": normalized}
    true_order = _top_k_order(true_agg)
    pred_order = _top_k_order(pred_agg)
    out["overlap_curve"] = [
        len(set(true_order[:k]) & set(pred_order[:k])) / k
        for k in range(1, n_builds + 1)
    ]
    # Build-level bootstrap: honest-eval panels are opponent-balanced, so the
    # build axis carries the sampling variance of interest here.
    spearman_samples: list[float] = []
    precision_samples: list[float] = []
    regret_samples: list[float] = []
    build_list = sorted(true_agg)
    for resample_idx in range(config.bootstrap_resamples):
        rng = np.random.default_rng(config.bootstrap_seed + resample_idx)
        drawn = sorted(set(rng.choice(build_list, size=len(build_list), replace=True).tolist()))
        if len(drawn) < MIN_CORRELATION_POINTS:
            continue
        sub_true = {b: true_agg[b] for b in drawn}
        sub_pred = {b: pred_agg[b] for b in drawn}
        rho = _safe_spearman(
            np.asarray([sub_true[b] for b in drawn]),
            np.asarray([sub_pred[b] for b in drawn]),
            epsilon,
        )
        if rho is not None:
            spearman_samples.append(rho)
        precision, raw, _ = _precision_regret(
            sub_true, sub_pred, primary_k, config.degenerate_denominator_epsilon
        )
        precision_samples.append(precision)
        regret_samples.append(raw)
    out["bootstrap"] = {
        "spearman": _percentile_ci(spearman_samples, config.bootstrap_ci_level),
        "precision_at_k": {
            **_percentile_ci(precision_samples, config.bootstrap_ci_level),
            "k": primary_k,
        },
        "regret_at_k": {
            **_percentile_ci(regret_samples, config.bootstrap_ci_level),
            "k": primary_k,
        },
    }
    return out
