"""Tests for the Phase 7 evaluation-metric suite (spec 31)."""

import json
import math
from collections.abc import Set as AbstractSet
from typing import Any

import numpy as np
import pytest

from starsector_optimizer.phase7_eval import (
    EvalMetricsConfig,
    build_aggregate_rank_metrics,
    honest_eval_build_metrics,
    noise_floor_from_replicates,
    panel_target_stats,
    per_opponent_rank_metrics,
    resolve_noise_floor,
    skill_scores,
    two_way_cluster_bootstrap,
)
from starsector_optimizer.phase7_matchup_data import HonestEvalMatchupRow


CONFIG = EvalMetricsConfig()


def _as_dict(value: object) -> dict[str, Any]:
    """Narrow a metrics sub-structure (typed `object` in src) to a dict."""
    assert isinstance(value, dict)
    return value


def _as_list(value: object) -> list[Any]:
    """Narrow a metrics sub-structure (typed `object` in src) to a list."""
    assert isinstance(value, list)
    return value


def _replicate_rows(groups: dict[tuple[str, str], list[float]]) -> list[HonestEvalMatchupRow]:
    rows = []
    for (build, opponent), targets in groups.items():
        for idx, target in enumerate(targets):
            rows.append(HonestEvalMatchupRow("p", build, build, opponent, idx, target))
    return rows


def _ranked_panel(
    n_builds: int,
    n_opponents: int,
    *,
    invert_for: AbstractSet[str] = frozenset(),
    constant_opponents: dict[str, float] | None = None,
):
    """Panel where y_true increases with build index; predictions follow or invert."""
    builds, opponents, y_true, y_pred = [], [], [], []
    constant_opponents = constant_opponents or {}
    for o in range(n_opponents):
        opp = f"opp{o}"
        for b in range(n_builds):
            builds.append(f"b{b:02d}")
            opponents.append(opp)
            if opp in constant_opponents:
                y_true.append(constant_opponents[opp])
            else:
                y_true.append(float(b) + 0.1 * o)
            y_pred.append(-float(b) if opp in invert_for else float(b))
    return builds, opponents, np.asarray(y_true), np.asarray(y_pred)


class TestNoiseFloor:
    def test_median_within_group_sd_over_replicated_groups(self):
        rows = _replicate_rows(
            {
                ("b1", "o1"): [0.0, 1.0],  # sd ~0.7071
                ("b2", "o1"): [0.5, 0.5],  # sd 0
                ("b3", "o2"): [1.0],  # single replicate: excluded
            }
        )
        result = noise_floor_from_replicates(rows)
        assert result["n_groups"] == 2
        assert result["source"] == "honest_eval_replicates"
        assert result["noise_floor"] == pytest.approx(np.median([np.std([0.0, 1.0], ddof=1), 0.0]))

    def test_no_replicated_groups_returns_none(self):
        rows = _replicate_rows({("b1", "o1"): [1.0], ("b2", "o2"): [0.5]})
        result = noise_floor_from_replicates(rows)
        assert result["noise_floor"] is None
        assert result["n_groups"] == 0

    def test_resolution_order_override_then_replicates_then_fallback(self):
        rows = _replicate_rows({("b1", "o1"): [0.0, 1.0]})
        override_config = EvalMetricsConfig(noise_floor_override=0.2)
        assert resolve_noise_floor(override_config, rows)["noise_floor"] == 0.2
        assert resolve_noise_floor(override_config, rows)["source"] == "override"
        derived = resolve_noise_floor(CONFIG, rows)
        assert derived["source"] == "honest_eval_replicates"
        fallback = resolve_noise_floor(CONFIG, [])
        assert fallback["noise_floor"] == CONFIG.noise_floor_fallback
        assert fallback["source"] == "fallback"

    def test_nonpositive_resolved_floor_raises(self):
        with pytest.raises(ValueError):
            resolve_noise_floor(EvalMetricsConfig(noise_floor_override=0.0), [])
        # all-replicates-identical gives a 0.0 median: falls through to fallback
        rows = _replicate_rows({("b1", "o1"): [0.5, 0.5], ("b2", "o2"): [0.1, 0.1]})
        resolved = resolve_noise_floor(CONFIG, rows)
        assert resolved["noise_floor"] == CONFIG.noise_floor_fallback
        assert resolved["source"] == "fallback"


class TestPerOpponentRankMetrics:
    def test_perfect_ranking_scores_one_per_opponent(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 2)
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert out["included_opponents"] == 2
        assert out["mean_spearman"] == pytest.approx(1.0)
        assert out["mean_kendall"] == pytest.approx(1.0)
        for row in _as_dict(out["per_opponent"]).values():
            assert row["spearman"] == pytest.approx(1.0)

    def test_inverted_opponent_lowers_mean(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 2, invert_for={"opp1"})
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert out["mean_spearman"] == pytest.approx(0.0)

    def test_low_variance_opponents_excluded(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 3, constant_opponents={"opp2": -1.0})
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert out["included_opponents"] == 2
        assert out["excluded_low_variance"] == 1
        assert "opp2" not in _as_dict(out["per_opponent"])

    def test_small_n_opponents_excluded(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 1)
        builds = [*list(builds), "b00", "b01"]
        opponents = list(opponents) + ["tiny"] * 2
        y_true = np.concatenate([y_true, [0.0, 1.0]])
        y_pred = np.concatenate([y_pred, [0.0, 1.0]])
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert out["excluded_small_n"] == 1

    def test_replicate_rows_collapse_to_cell_means(self):
        # 3 replicates per (build, opponent) cell must behave exactly like
        # one row per cell: distinct-build gates, no replicate weighting.
        builds, opponents, y_true, y_pred = _ranked_panel(8, 2)
        replicated = per_opponent_rank_metrics(
            list(builds) * 3,
            list(opponents) * 3,
            np.tile(y_true, 3),
            np.tile(y_pred, 3),
            0.05,
            CONFIG,
        )
        single = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert replicated == single
        # A build seen against ONE opponent with many replicates must not
        # satisfy a 2-opponent panel gate via replicate counts.
        config = EvalMetricsConfig(min_opponents_per_build=2)
        out = build_aggregate_rank_metrics(
            ["lonely"] * 5,
            ["o0"] * 5,
            np.asarray([1.0] * 5),
            np.asarray([1.0] * 5),
            frozenset(),
            (1,),
            config,
        )
        assert out["n_builds"] == 0
        assert out["excluded_small_panel"] == 1

    def test_constant_predictions_yield_null_and_counter(self):
        builds, opponents, y_true, _ = _ranked_panel(8, 1)
        y_pred = np.zeros_like(y_true)
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert out["null_prediction_degenerate"] == 1
        assert _as_dict(out["per_opponent"])["opp0"]["spearman"] is None
        assert out["mean_spearman"] is None

    def test_sparse_kendall_quantizes_targets_into_ties(self):
        # Targets 0.0, 0.04, 1.0, 1.04 with floor 0.5: bins 0,0,2,2.
        opponents = ["o"] * 4
        y_true = np.asarray([0.0, 0.04, 1.0, 1.04])
        y_pred = np.asarray([0.0, -1.0, 2.0, 3.0])  # inverts the within-bin pair
        config = EvalMetricsConfig(min_builds_per_opponent=2)
        out = per_opponent_rank_metrics(
            [f"b{i}" for i in range(4)], opponents, y_true, y_pred, 0.5, config
        )
        row = _as_dict(out["per_opponent"])["o"]
        assert row["sparse_kendall"] is not None
        assert row["sparse_kendall"] > row["kendall"]

    def test_top_fraction_kendall_requires_min_rows(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 1)
        # top 10% of 8 rows = 1 row < min_top_fraction_rows -> None
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, CONFIG)
        assert _as_dict(out["per_opponent"])["opp0"]["top_fraction_kendall"] is None
        wide = EvalMetricsConfig(top_fraction=0.5)
        out = per_opponent_rank_metrics(builds, opponents, y_true, y_pred, 0.05, wide)
        assert _as_dict(out["per_opponent"])["opp0"]["top_fraction_kendall"] == pytest.approx(1.0)


class TestBuildAggregateRankMetrics:
    def test_perfect_ranking(self):
        builds, opponents, y_true, y_pred = _ranked_panel(6, 4)
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset(), (1, 3), CONFIG
        )
        assert out["spearman"] == pytest.approx(1.0)
        assert _as_dict(out["precision_at_k"])["1"] == 1.0
        assert _as_dict(out["regret_at_k"])["1"]["raw"] == pytest.approx(0.0)
        assert out["n_builds"] == 6

    def test_degenerate_opponents_excluded_from_aggregates(self):
        builds, opponents, y_true, y_pred = _ranked_panel(6, 4, constant_opponents={"opp3": -1.0})
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset({"opp3"}), (1,), CONFIG
        )
        # aggregates use only opp0..opp2; ranking still perfect
        assert out["spearman"] == pytest.approx(1.0)
        assert _as_dict(out["panel_sizes"])["max"] == 3

    def test_min_opponents_per_build_exclusion(self):
        builds, opponents, y_true, y_pred = _ranked_panel(4, 3)
        builds = [*list(builds), "lonely"]
        opponents = [*list(opponents), "opp0"]
        y_true = np.concatenate([y_true, [9.0]])
        y_pred = np.concatenate([y_pred, [9.0]])
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset(), (1,), CONFIG
        )
        assert out["excluded_small_panel"] == 1
        assert out["n_builds"] == 4

    def test_regret_measures_value_gap_not_rank_gap(self):
        # b0 best (agg 2.0), model prefers b2 (agg 1.0): regret@1 = 1.0
        builds = ["b0", "b1", "b2"] * 3
        opponents = [f"o{i}" for i in range(3) for _ in range(3)]
        y_true = np.asarray([2.0, 0.0, 1.0] * 3)
        y_pred = np.asarray([0.0, 1.0, 2.0] * 3)
        config = EvalMetricsConfig(min_opponents_per_build=3)
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset(), (1,), config
        )
        assert _as_dict(out["regret_at_k"])["1"]["raw"] == pytest.approx(1.0)
        assert _as_dict(out["regret_at_k"])["1"]["normalized"] == pytest.approx(0.5)

    def test_normalized_regret_none_on_degenerate_range(self):
        builds = ["b0", "b1"] * 3
        opponents = [f"o{i}" for i in range(3) for _ in range(2)]
        y_true = np.zeros(6)
        y_pred = np.asarray([0.0, 1.0] * 3)
        config = EvalMetricsConfig(min_opponents_per_build=3)
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset(), (1,), config
        )
        assert _as_dict(out["regret_at_k"])["1"]["raw"] == pytest.approx(0.0)
        assert _as_dict(out["regret_at_k"])["1"]["normalized"] is None
        assert out["spearman"] is None  # constant true aggregates

    def test_top_k_tie_break_is_deterministic(self):
        # Two predicted ties: ascending build key wins the top-1 slot.
        builds = ["a", "z"] * 3
        opponents = [f"o{i}" for i in range(3) for _ in range(2)]
        y_true = np.asarray([0.0, 1.0] * 3)
        y_pred = np.asarray([0.5, 0.5] * 3)
        config = EvalMetricsConfig(min_opponents_per_build=3)
        out = build_aggregate_rank_metrics(
            builds, opponents, y_true, y_pred, frozenset(), (1,), config
        )
        # top-1 predicted = "a" (tie broken by key); true top-1 = "z"
        assert _as_dict(out["precision_at_k"])["1"] == 0.0


class TestScalarMetrics:
    def test_skill_score_known_value(self):
        y_true = np.asarray([0.0, 1.0, 2.0, 3.0])
        y_pred = np.asarray([0.5, 1.5, 1.5, 2.5])
        out = skill_scores(y_true, y_pred, 1.5, CONFIG)
        assert out["mse_model"] == pytest.approx(0.25)
        assert out["mse_train_mean"] == pytest.approx(1.25)
        assert out["skill"] == pytest.approx(1 - 0.25 / 1.25)

    def test_skill_score_none_on_degenerate_denominator(self):
        y_true = np.asarray([1.0, 1.0, 1.0])
        out = skill_scores(y_true, np.asarray([1.0, 1.0, 2.0]), 1.0, CONFIG)
        assert out["skill"] is None

    def test_panel_target_stats_endpoint_mass(self):
        y = np.asarray([-1.0, -1.0, 1.0, 0.25])
        out = panel_target_stats(y)
        assert out["n"] == 4
        assert out["endpoint_mass_low"] == pytest.approx(0.5)
        assert out["endpoint_mass_high"] == pytest.approx(0.25)


class TestBootstrap:
    def test_deterministic_under_seed(self):
        builds, opponents, y_true, y_pred = _ranked_panel(10, 4)
        config = EvalMetricsConfig(bootstrap_resamples=50)
        a = two_way_cluster_bootstrap(
            builds, opponents, y_true, y_pred, 0.05, frozenset(), 1, config
        )
        b = two_way_cluster_bootstrap(
            builds, opponents, y_true, y_pred, 0.05, frozenset(), 1, config
        )
        assert a == b

    def test_ci_shape_and_coverage_of_strong_signal(self):
        builds, opponents, y_true, y_pred = _ranked_panel(10, 4)
        config = EvalMetricsConfig(bootstrap_resamples=50)
        out = two_way_cluster_bootstrap(
            builds, opponents, y_true, y_pred, 0.05, frozenset(), 1, config
        )
        for name in (
            "mean_per_opponent_spearman",
            "build_aggregate_spearman",
            "precision_at_k",
            "regret_at_k",
        ):
            stat = _as_dict(out[name])
            assert stat["n_finite"] <= config.bootstrap_resamples
            if stat["n_finite"]:
                assert stat["ci_low"] <= stat["ci_high"]
        # Perfect signal: spearman CI collapses at 1.0.
        assert _as_dict(out["mean_per_opponent_spearman"])["ci_high"] == pytest.approx(1.0)

    def test_degenerate_panel_reports_zero_finite_not_nan(self):
        builds = ["b0", "b1"] * 3
        opponents = [f"o{i}" for i in range(3) for _ in range(2)]
        y_true = np.zeros(6)  # every opponent below any noise floor
        y_pred = np.asarray([0.0, 1.0] * 3)
        config = EvalMetricsConfig(bootstrap_resamples=20)
        out = two_way_cluster_bootstrap(
            builds, opponents, y_true, y_pred, 0.05, frozenset(), 1, config
        )
        stat = _as_dict(out["mean_per_opponent_spearman"])
        assert stat["n_finite"] == 0
        assert stat["ci_low"] is None and stat["ci_high"] is None
        json.dumps(out)


class TestHonestEvalBuildMetrics:
    def test_chance_level_overlap_curve_and_train_overlap(self):
        builds, opponents, y_true, y_pred = _ranked_panel(6, 4)
        config = EvalMetricsConfig(bootstrap_resamples=25)
        out = honest_eval_build_metrics(
            builds,
            opponents,
            y_true,
            y_pred,
            degenerate_opponents=frozenset(),
            outer_train_build_keys=frozenset({"b00", "b01", "nonexistent"}),
            k_values=(1, 3),
            primary_k=1,
            config=config,
        )
        assert out["spearman"] == pytest.approx(1.0)
        assert _as_dict(out["chance_level"])["1"] == pytest.approx(1 / 6)
        overlap_curve = _as_list(out["overlap_curve"])
        assert len(overlap_curve) == 6
        assert overlap_curve[-1] == pytest.approx(1.0)
        assert out["outer_train_build_overlap"] == 2
        assert _as_dict(out["bootstrap"])["spearman"]["n_finite"] > 0

    def test_json_safe_with_degenerate_everything(self):
        builds = ["b0", "b1"] * 3
        opponents = [f"o{i}" for i in range(3) for _ in range(2)]
        y_true = np.full(6, -1.0)
        y_pred = np.zeros(6)
        config = EvalMetricsConfig(bootstrap_resamples=10)
        out = honest_eval_build_metrics(
            builds,
            opponents,
            y_true,
            y_pred,
            degenerate_opponents=frozenset({"o0", "o1", "o2"}),
            outer_train_build_keys=frozenset(),
            k_values=(1,),
            primary_k=1,
            config=config,
        )
        payload = json.dumps(out)
        assert "NaN" not in payload and "Infinity" not in payload


class TestJsonSafety:
    def test_all_metric_outputs_survive_json_round_trip(self):
        builds, opponents, y_true, y_pred = _ranked_panel(8, 3, constant_opponents={"opp2": -1.0})
        config = EvalMetricsConfig(bootstrap_resamples=10)
        blobs = [
            per_opponent_rank_metrics(
                builds, opponents, y_true, np.zeros_like(y_pred), 0.05, config
            ),
            build_aggregate_rank_metrics(
                builds, opponents, y_true, y_pred, frozenset({"opp2"}), (1, 3), config
            ),
            skill_scores(np.full(4, 1.0), np.full(4, 1.0), 1.0, config),
            panel_target_stats(np.asarray([-1.0, 1.0])),
            two_way_cluster_bootstrap(
                builds, opponents, y_true, y_pred, 0.05, frozenset({"opp2"}), 1, config
            ),
        ]
        for blob in blobs:
            round_tripped = json.loads(json.dumps(blob))
            assert isinstance(round_tripped, dict)
            assert not _contains_non_finite(round_tripped)


def _contains_non_finite(obj) -> bool:
    if isinstance(obj, dict):
        return any(_contains_non_finite(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_non_finite(v) for v in obj)
    if isinstance(obj, float):
        return not math.isfinite(obj)
    return False
