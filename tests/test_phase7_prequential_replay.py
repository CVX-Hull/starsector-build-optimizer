"""Tests for scripts/analysis/phase7_prequential_replay.py (spec 31
§"Prequential Replay Ablation").

Conventions mirror test_phase7_learned_surrogate_experiment.py: the script
is loaded via importlib, pure logic is tested with hand-built rows and
monkeypatched fit seams, and one integration test exercises a synthetic
sqlite DB + fixture eval logs.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

from starsector_optimizer.deconfounding import (
    pooled_residual_variance,
    twfe_decompose,
)
from starsector_optimizer.phase7_matchup_data import TrainingMatchupRow

_SCRIPT_PATH = (
    Path(__file__).parent.parent / "scripts" / "analysis" / "phase7_prequential_replay.py"
)
_SPEC = importlib.util.spec_from_file_location("_phase7_prequential_replay", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
replay = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("_phase7_prequential_replay", replay)
_SPEC.loader.exec_module(replay)


# ------------------------------------------------------------- fixtures ---


def _config(tmp_path: Path | None = None, **overrides):
    base = tmp_path or Path(".")
    defaults = {
        "db_path": base / "phase7.sqlite",
        "game_dir": Path("game/starsector"),
        "output": None,
        "log_base_dir": base,
        "study_db_root": base / "study_dbs",
        "progress": False,
    }
    defaults.update(overrides)
    return replay.ReplayConfig(**defaults)


def _row(
    source_path: str = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl",
    campaign: str = "wave1-c0a",
    seed: int = 0,
    trial_number: int = 0,
    build_key: str | None = None,
    opponent: str = "opp_a",
    opponent_index: int = 0,
    target: float = 0.0,
    row_kind: str = "finalized",
) -> TrainingMatchupRow:
    return TrainingMatchupRow(
        source_path=source_path,
        campaign=campaign,
        seed=seed,
        trial_number=trial_number,
        build_key=build_key if build_key is not None else f"build{trial_number:04d}",
        opponent_variant_id=opponent,
        opponent_index=opponent_index,
        target=target,
        row_kind=row_kind,
    )


def _log_trial(
    timestamp: str = "2026-05-10T00:00:00+00:00",
    pruned: bool = False,
    planned_opponents: tuple[str, ...] = ("opp_a", "opp_b"),
    covariate_vector: tuple[float, ...] | None = (1.0, 2.0),
):
    return replay.LogTrial(
        timestamp=timestamp,
        pruned=pruned,
        planned_opponents=planned_opponents,
        covariate_vector=covariate_vector if not pruned else None,
    )


def _trial_fixture(
    n_trials: int = 6,
    cell_path: str = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl",
    campaign: str = "wave1-c0a",
    seed: int = 0,
    pruned_trials: frozenset[int] = frozenset(),
    opponents: tuple[str, ...] = ("opp_a", "opp_b", "opp_c"),
):
    """n_trials trials; trial t arrives at second t; target = t + opponent idx/10."""
    rows: list[TrainingMatchupRow] = []
    logs: dict[tuple[str, int], object] = {}
    for t in range(n_trials):
        pruned = t in pruned_trials
        realized = opponents[:1] if pruned else opponents
        for idx, opp in enumerate(realized):
            rows.append(
                _row(
                    source_path=cell_path,
                    campaign=campaign,
                    seed=seed,
                    trial_number=t,
                    opponent=opp,
                    opponent_index=idx,
                    target=float(t) + idx / 10.0,
                    row_kind="pruned" if pruned else "finalized",
                )
            )
        logs[(cell_path, t)] = _log_trial(
            timestamp=f"2026-05-10T00:00:{t:02d}+00:00",
            pruned=pruned,
            planned_opponents=opponents,
            covariate_vector=None if pruned else (float(t), 1.0),
        )
    return rows, logs


# --------------------------------------------------- stream construction ---


class TestStreamConstruction:
    def test_orders_by_timestamp_then_trial_number(self):
        path = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
        rows = [
            _row(trial_number=8),
            _row(trial_number=4),
            _row(trial_number=5),
        ]
        logs = {
            (path, 8): _log_trial(timestamp="2026-05-10T00:00:01+00:00"),
            (path, 4): _log_trial(timestamp="2026-05-10T00:00:03+00:00"),
            (path, 5): _log_trial(timestamp="2026-05-10T00:00:02+00:00"),
        }
        cells = replay.build_replay_cells(rows, logs)
        (trials,) = cells.values()
        assert [t.trial_number for t in trials] == [8, 5, 4]

    def test_timestamp_tie_breaks_by_trial_number(self):
        path = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
        rows = [_row(trial_number=2), _row(trial_number=1)]
        ts = "2026-05-10T00:00:01+00:00"
        logs = {
            (path, 2): _log_trial(timestamp=ts),
            (path, 1): _log_trial(timestamp=ts),
        }
        (trials,) = replay.build_replay_cells(rows, logs).values()
        assert [t.trial_number for t in trials] == [1, 2]

    def test_missing_log_trial_is_hard_error(self):
        rows = [_row(trial_number=0), _row(trial_number=1)]
        path = rows[0].source_path
        logs = {(path, 0): _log_trial()}
        with pytest.raises(ValueError, match="eval log"):
            replay.build_replay_cells(rows, logs)

    def test_missing_db_trial_is_hard_error(self):
        rows = [_row(trial_number=0)]
        path = rows[0].source_path
        logs = {(path, 0): _log_trial(), (path, 7): _log_trial()}
        with pytest.raises(ValueError, match="matchup DB"):
            replay.build_replay_cells(rows, logs)

    def test_pruned_trials_included_with_planned_panel(self):
        rows, logs = _trial_fixture(n_trials=3, pruned_trials=frozenset({1}))
        (trials,) = replay.build_replay_cells(rows, logs).values()
        pruned = [t for t in trials if t.pruned]
        assert len(pruned) == 1
        assert pruned[0].planned_opponents == ("opp_a", "opp_b", "opp_c")
        assert len(pruned[0].rows) == 1  # realized subset
        assert pruned[0].covariate_vector is None

    def test_cells_are_isolated(self):
        rows_a, logs_a = _trial_fixture(
            cell_path="data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl",
            campaign="wave1-c0a",
            seed=0,
        )
        rows_b, logs_b = _trial_fixture(
            cell_path="data/logs/wave1-c0a/study_seed1/evaluation_log.jsonl",
            campaign="wave1-c0a",
            seed=1,
        )
        cells = replay.build_replay_cells(rows_a + rows_b, {**logs_a, **logs_b})
        assert set(cells) == {"wave1-c0a:0", "wave1-c0a:1"}
        for trials in cells.values():
            assert len({t.source_path for t in trials}) == 1

    def test_gappy_trial_numbers_survive(self):
        path = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
        rows = [_row(trial_number=3), _row(trial_number=53)]
        logs = {
            (path, 3): _log_trial(timestamp="2026-05-10T00:00:01+00:00"),
            (path, 53): _log_trial(timestamp="2026-05-10T00:00:02+00:00"),
        }
        (trials,) = replay.build_replay_cells(rows, logs).values()
        assert [t.trial_number for t in trials] == [3, 53]


class TestEvalLogLoading:
    def test_reads_jsonl_and_skips_cache_and_invalid(self, tmp_path):
        log_dir = tmp_path / "data" / "logs" / "wave1-c0a" / "study_seed0"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "evaluation_log.jsonl"
        rel = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
        entries = [
            {
                "trial_number": 0,
                "timestamp": "2026-05-10T00:00:00+00:00",
                "pruned": False,
                "opponent_order": ["opp_a", "opp_b"],
                "covariate_vector": [1.0, 2.0],
            },
            {"trial_number": 1, "cache_hit": True, "timestamp": "x", "opponent_order": []},
            {"trial_number": 2, "invalid_spec": True, "timestamp": "x", "opponent_order": []},
            {
                "trial_number": 3,
                "timestamp": "2026-05-10T00:00:01+00:00",
                "pruned": True,
                "opponent_order": ["opp_a", "opp_b"],
            },
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        loaded = replay.load_log_trials([rel], tmp_path)
        assert set(loaded) == {(rel, 0), (rel, 3)}
        assert loaded[(rel, 0)].covariate_vector == (1.0, 2.0)
        assert loaded[(rel, 3)].pruned is True
        assert loaded[(rel, 3)].covariate_vector is None


# ------------------------------------------------------- cutoffs/buckets ---


class TestCutoffsAndBuckets:
    def test_cutoff_grid(self):
        config = _config(min_train_trials=40, cutoff_stride=10, min_future_trials=10)
        assert replay.cutoff_indices(75, config) == (40, 50, 60)

    def test_cutoff_grid_short_cell_is_empty(self):
        config = _config(min_train_trials=40, cutoff_stride=10, min_future_trials=10)
        assert replay.cutoff_indices(45, config) == ()

    def test_cutoff_boundary_inclusive(self):
        config = _config(min_train_trials=40, cutoff_stride=10, min_future_trials=10)
        assert replay.cutoff_indices(50, config) == (40,)

    def test_bucket_labels_and_assignment(self):
        config = _config(horizon_buckets=((0, 10), (10, 20), (20, 40)))
        buckets = replay.bucket_assignments(45, config)
        assert list(buckets) == ["0-10", "10-20", "20-40", "tail"]
        assert buckets["0-10"] == tuple(range(0, 10))
        assert buckets["20-40"] == tuple(range(20, 40))
        assert buckets["tail"] == tuple(range(40, 45))

    def test_bucket_assignment_truncates_to_available(self):
        config = _config(horizon_buckets=((0, 10), (10, 20), (20, 40)))
        buckets = replay.bucket_assignments(15, config)
        assert buckets["0-10"] == tuple(range(0, 10))
        assert buckets["10-20"] == tuple(range(10, 15))
        assert buckets["20-40"] == ()
        assert buckets["tail"] == ()

    def test_train_gap_excludes_most_recent(self):
        rows, logs = _trial_fixture(n_trials=10)
        (trials,) = replay.build_replay_cells(rows, logs).values()
        train = replay.training_trials(trials, cutoff=6, gap=2, skipped=frozenset())
        assert [t.trial_number for t in train] == [0, 1, 2, 3]

    def test_train_gap_larger_than_cutoff_yields_empty(self):
        rows, logs = _trial_fixture(n_trials=10)
        (trials,) = replay.build_replay_cells(rows, logs).values()
        assert replay.training_trials(trials, cutoff=3, gap=5, skipped=frozenset()) == ()

    def test_training_trials_removes_skipped(self):
        rows, logs = _trial_fixture(n_trials=10)
        (trials,) = replay.build_replay_cells(rows, logs).values()
        train = replay.training_trials(trials, cutoff=6, gap=0, skipped=frozenset({1, 3}))
        assert [t.trial_number for t in train] == [0, 2, 4, 5]


# ------------------------------------------------------------ panel rows ---


class TestPanelRows:
    def test_panel_rows_use_planned_opponents_for_pruned_trial(self):
        rows, logs = _trial_fixture(n_trials=3, pruned_trials=frozenset({1}))
        (trials,) = replay.build_replay_cells(rows, logs).values()
        pruned = next(t for t in trials if t.pruned)
        panel = replay.panel_rows(pruned)
        assert [r.opponent_variant_id for r in panel] == ["opp_a", "opp_b", "opp_c"]
        assert all(r.build_key == pruned.build_key for r in panel)


# -------------------------------------------------------- estimator arms ---


def _arm_trials(
    matrix: np.ndarray,
    opponents: tuple[str, ...],
    pruned_trials: frozenset[int] = frozenset(),
    covariates: dict[int, tuple[float, ...]] | None = None,
):
    """Build ReplayTrials from a dense (trial × opponent) matrix (NaN = missing)."""
    path = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
    rows: list[TrainingMatchupRow] = []
    logs: dict[tuple[str, int], object] = {}
    for t in range(matrix.shape[0]):
        pruned = t in pruned_trials
        for j, opp in enumerate(opponents):
            if np.isnan(matrix[t, j]):
                continue
            rows.append(
                _row(
                    trial_number=t,
                    opponent=opp,
                    opponent_index=j,
                    target=float(matrix[t, j]),
                    row_kind="pruned" if pruned else "finalized",
                )
            )
        cov = (covariates or {}).get(t)
        logs[(path, t)] = replay.LogTrial(
            timestamp=f"2026-05-10T00:00:{t:02d}+00:00",
            pruned=pruned,
            planned_opponents=opponents,
            covariate_vector=None if pruned else (cov if cov is not None else (1.0, float(t))),
        )
    (trials,) = replay.build_replay_cells(rows, logs).values()
    return list(trials)


class TestEstimatorArms:
    def test_a0_untrimmed_vs_a1_trimmed_differ_exactly_by_trim(self):
        rng = np.random.default_rng(0)
        matrix = rng.normal(size=(8, 6))
        # Give trial 0 one catastrophic outlier the A1 trim should drop.
        matrix[0, 0] = -50.0
        trials = _arm_trials(matrix, tuple(f"opp{j}" for j in range(6)))
        estimates = replay.estimator_arm_estimates(trials, dict.fromkeys(range(8), 0.0), _config())
        a0 = estimates.values["A0"]
        a1 = estimates.values["A1"]
        assert a0[0] < a1[0]  # outlier dropped only under A1
        # Non-outlier build: A0 and A1 differ only via the trim of its own row.
        assert set(a0) == set(a1) == set(range(8))

    def test_a2_scalar_cv_matches_hand_computation(self):
        rng = np.random.default_rng(1)
        matrix = rng.normal(size=(6, 5))
        trials = _arm_trials(matrix, tuple(f"opp{j}" for j in range(5)))
        composites = {t: float(t) * 2.0 for t in range(6)}
        config = _config()
        estimates = replay.estimator_arm_estimates(trials, composites, config)
        a1 = estimates.values["A1"]
        a2 = estimates.values["A2"]
        alpha = np.asarray([a1[t] for t in range(6)])
        h = np.asarray([composites[t] for t in range(6)])
        beta_cv = float(np.cov(alpha, h, ddof=0)[0, 1] / np.var(h))
        expected = alpha - beta_cv * (h - h.mean())
        np.testing.assert_allclose([a2[t] for t in range(6)], expected, rtol=1e-9)

    def test_a2_degenerate_composite_variance_falls_back_to_a1(self):
        rng = np.random.default_rng(2)
        matrix = rng.normal(size=(5, 4))
        trials = _arm_trials(matrix, tuple(f"opp{j}" for j in range(4)))
        composites = dict.fromkeys(range(5), 3.0)  # zero variance
        estimates = replay.estimator_arm_estimates(trials, composites, _config())
        assert estimates.values["A2"] == estimates.values["A1"]
        assert estimates.diagnostics["a2_beta_cv"] == 0.0

    def test_a3_tie_group_is_top_quartile_of_a2(self):
        rng = np.random.default_rng(3)
        matrix = rng.normal(size=(8, 5))
        trials = _arm_trials(matrix, tuple(f"opp{j}" for j in range(5)))
        estimates = replay.estimator_arm_estimates(trials, dict.fromkeys(range(8), 0.0), _config())
        a2 = estimates.values["A2"]
        ordered = sorted(a2, key=lambda t: -a2[t])
        assert set(estimates.a3_tie_trials) == set(ordered[:2])  # ceil(8 * 0.25) = 2

    def test_eb_arm_uses_logged_covariates_and_ranks_finalized_only(self):
        rng = np.random.default_rng(4)
        matrix = rng.normal(size=(7, 5))
        trials = _arm_trials(
            matrix,
            tuple(f"opp{j}" for j in range(5)),
            pruned_trials=frozenset({2}),
            covariates={t: (float(t), float(t % 2)) for t in range(7)},
        )
        estimates = replay.estimator_arm_estimates(trials, dict.fromkeys(range(7), 0.0), _config())
        for arm in ("A0", "A1", "A2", "EB"):
            assert 2 not in estimates.values[arm]
            assert set(estimates.values[arm]) == {0, 1, 3, 4, 5, 6}
        # EB differs from A1 (shrinkage toward the covariate prior moved something).
        assert any(
            abs(estimates.values["EB"][t] - estimates.values["A1"][t]) > 1e-12
            for t in estimates.values["EB"]
        )

    def test_eb_skipped_below_minimum_rankable(self):
        rng = np.random.default_rng(5)
        matrix = rng.normal(size=(3, 4))
        trials = _arm_trials(
            matrix, tuple(f"opp{j}" for j in range(4)), pruned_trials=frozenset({0})
        )
        estimates = replay.estimator_arm_estimates(trials, dict.fromkeys(range(3), 0.0), _config())
        assert "EB" not in estimates.values
        assert estimates.diagnostics["eb_skip_reason"] == "insufficient_rankable_builds"

    def test_pruned_rows_feed_the_fit_matrix(self):
        # The pruned build's row must still influence beta estimation: give
        # the pruned trial an extreme score on one opponent and check that
        # opponent's beta moves relative to excluding the pruned trial.
        matrix = np.asarray(
            [
                [0.0, 0.1, 0.2],
                [0.1, 0.0, 0.1],
                [5.0, np.nan, np.nan],  # pruned trial, extreme on opp0
                [0.2, 0.1, 0.0],
            ]
        )
        opponents = ("opp0", "opp1", "opp2")
        with_pruned = _arm_trials(matrix, opponents, pruned_trials=frozenset({2}))
        without = _arm_trials(matrix[[0, 1, 3]], opponents)
        est_with = replay.estimator_arm_estimates(
            with_pruned, dict.fromkeys(range(4), 0.0), _config()
        )
        est_without = replay.estimator_arm_estimates(
            without, dict.fromkeys(range(3), 0.0), _config()
        )
        assert est_with.values["A1"][0] != pytest.approx(est_without.values["A1"][0])


class TestTopKSelection:
    def test_deterministic_top_k_breaks_float_ties_by_trial_number(self):
        estimates = {5: 1.0, 3: 1.0, 1: 0.5}
        assert replay.deterministic_top_k(estimates, 2) == (3, 5)

    def test_a3_tie_break_draws_are_seed_deterministic(self):
        values = {t: float(t) for t in range(8)}
        tie_group = (5, 6, 7)
        draws_a = replay.a3_top_k_draws(values, tie_group, k=2, draws=16, seed=9)
        draws_b = replay.a3_top_k_draws(values, tie_group, k=2, draws=16, seed=9)
        assert draws_a == draws_b
        assert len(draws_a) == 16
        for selection in draws_a:
            assert len(selection) == 2
            assert set(selection) <= set(tie_group)  # k < tie group size


# ------------------------------------------------------------- fidelity ---


class TestFidelity:
    def test_t1_restricted_to_finalized_and_t2_to_target_support(self):
        rows, logs = _trial_fixture(n_trials=8, pruned_trials=frozenset({5}))
        (trials,) = replay.build_replay_cells(rows, logs).values()
        future = trials[4:]
        pred = {t.trial_number: float(-t.trial_number) for t in future}
        t2_target = {t.trial_number: float(t.trial_number) for t in future if not t.pruned}
        record = replay.fidelity_record(future, pred, t2_target)
        assert record["t1_n"] == len(future) - 1  # pruned trial excluded
        assert record["t2_n"] == len(future) - 1
        assert record["t1_spearman"] == pytest.approx(-1.0)
        assert record["t2_spearman"] == pytest.approx(-1.0)
        assert record["t1_kendall"] == pytest.approx(-1.0)

    def test_degenerate_fidelity_is_none(self):
        rows, logs = _trial_fixture(n_trials=3)
        (trials,) = replay.build_replay_cells(rows, logs).values()
        future = trials[2:]
        pred = {future[0].trial_number: 1.0}
        record = replay.fidelity_record(future, pred, {future[0].trial_number: 1.0})
        assert record["t1_spearman"] is None
        assert record["t2_spearman"] is None


# ---------------------------------------------------------------- gating ---


class TestGating:
    def _cell(self, n_trials=30):
        rows, logs = _trial_fixture(n_trials=n_trials)
        (trials,) = replay.build_replay_cells(rows, logs).values()
        return trials

    def test_bottom_q_skipped_and_removed_from_training(self):
        trials = self._cell(30)
        config = _config(min_train_trials=10, cutoff_stride=10, min_future_trials=10)
        seen_train: list[tuple[int, ...]] = []

        def predict_block(train, block):
            seen_train.append(tuple(t.trial_number for t in train))
            # Rank ascending by trial number → lowest numbers get skipped.
            return {t.trial_number: float(t.trial_number) for t in block}

        result = replay.run_gating(
            trials,
            cutoffs=replay.cutoff_indices(len(trials), config),
            config=config,
            gap=0,
            fraction=0.2,
            predict_block=predict_block,
            remove_skipped=True,
        )
        # Blocks [10,20) and [20,30): bottom 20% of 10 = 2 skipped each.
        assert result["skipped_trials"] == [10, 11, 20, 21]
        # Second cutoff's training set must exclude the first block's skips.
        assert 10 not in seen_train[1] and 11 not in seen_train[1]
        assert result["rows_saved"] == sum(
            len(t.rows) for t in trials if t.trial_number in {10, 11, 20, 21}
        )
        assert result["rows_total"] == sum(len(t.rows) for t in trials)

    def test_keep_skipped_rows_sensitivity_keeps_training_rows(self):
        trials = self._cell(30)
        config = _config(min_train_trials=10, cutoff_stride=10, min_future_trials=10)
        seen_train: list[tuple[int, ...]] = []

        def predict_block(train, block):
            seen_train.append(tuple(t.trial_number for t in train))
            return {t.trial_number: float(t.trial_number) for t in block}

        replay.run_gating(
            trials,
            cutoffs=replay.cutoff_indices(len(trials), config),
            config=config,
            gap=0,
            fraction=0.2,
            predict_block=predict_block,
            remove_skipped=False,
        )
        assert 10 in seen_train[1] and 11 in seen_train[1]

    def test_regret_counts_true_top_k_skipped(self):
        trials = self._cell(30)
        config = _config(min_train_trials=10, cutoff_stride=10, min_future_trials=10)

        def predict_block(train, block):
            # Adversarial: skip the highest trial numbers.
            return {t.trial_number: -float(t.trial_number) for t in block}

        result = replay.run_gating(
            trials,
            cutoffs=replay.cutoff_indices(len(trials), config),
            config=config,
            gap=0,
            fraction=0.2,
            predict_block=predict_block,
            remove_skipped=True,
        )
        targets = {"A1": {t: float(t) for t in range(30)}}
        regret = replay.gating_regret(result["skipped_trials"], targets, (1, 3, 9))
        # Skipped: {18,19,28,29}; true top-3 = {27,28,29} → 2 skipped;
        # true top-9 = {21..29} → 2 skipped; top-1 = {29} → 1 skipped.
        assert result["skipped_trials"] == [18, 19, 28, 29]
        assert regret["A1"] == {"1": 1, "3": 2, "9": 2}

    def test_pruner_reference_counts_rows_not_run(self):
        rows, logs = _trial_fixture(n_trials=4, pruned_trials=frozenset({1, 2}))
        (trials,) = replay.build_replay_cells(rows, logs).values()
        # Planned panel = 3 opponents; pruned trials realized 1 row each.
        assert replay.pruner_rows_avoided(trials) == 4

    def test_headline_q_star(self):
        per_q_regret = {
            "0.1": {"3": 0},
            "0.2": {"3": 0},
            "0.3": {"3": 1},
            "0.5": {"3": 2},
        }
        assert replay.q_star(per_q_regret, top_k=3) == 0.2

    def test_headline_q_star_zero_when_all_fractions_regret(self):
        per_q_regret = {"0.1": {"3": 1}, "0.2": {"3": 2}}
        assert replay.q_star(per_q_regret, top_k=3) == 0.0


# --------------------------------------------------- shared σ̂_ε² helper ---


class TestPooledResidualVariance:
    def test_matches_inline_formula(self):
        rng = np.random.default_rng(7)
        matrix = rng.normal(size=(6, 4))
        matrix[0, 1] = np.nan
        alpha, beta = twfe_decompose(matrix)
        observed = ~np.isnan(matrix)
        pred = alpha[:, None] + beta[None, :]
        diff = np.where(observed, matrix - pred, 0.0)
        denom = max(int(observed.sum()) - (6 + 4 - 1), 1)
        expected = float(np.sum(diff * diff)) / denom
        assert pooled_residual_variance(matrix, alpha, beta) == pytest.approx(expected)

    def test_empty_matrix_returns_zero(self):
        matrix = np.full((2, 2), np.nan)
        assert pooled_residual_variance(matrix, np.zeros(2), np.zeros(2)) == 0.0


# ------------------------------------------------------ oracle evaluation ---


class TestOracleEvaluation:
    def test_pairwise_concordance_counts_and_a3_half_credit(self):
        arm_values = {"A1": {0: 3.0, 1: 2.0, 2: 1.0}}
        oracle = {0: 0.9, 1: 0.5, 2: 0.7}  # true order: 0 > 2 > 1
        stats = replay.within_cell_concordance(arm_values, oracle, a3_tie_trials=())
        # Pairs: (0,1) concordant, (0,2) concordant, (1,2) discordant → 2/3.
        assert stats["A1"] == {"concordant": 2.0, "pairs": 3}

    def test_a3_pairs_inside_tie_group_get_half(self):
        arm_values = {"A3": {0: 3.0, 1: 2.0, 2: 1.0}}
        oracle = {0: 0.9, 1: 0.5, 2: 0.7}
        stats = replay.within_cell_concordance(arm_values, oracle, a3_tie_trials=(0, 1))
        # (0,1) inside tie group → 0.5; (0,2) → 1; (1,2) → 0. Total 1.5/3.
        assert stats["A3"] == {"concordant": 1.5, "pairs": 3}


# ---------------------------------------------- determinism and artifact ---


class TestDeterminismHelpers:
    def test_force_deterministic_predict_sets_rf_n_jobs(self):
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.pipeline import Pipeline

        pipeline = Pipeline(
            [
                ("features", DictVectorizer(sparse=True)),
                ("model", RandomForestRegressor(n_estimators=2, n_jobs=-1)),
            ]
        )

        class Wrapper:
            def __init__(self):
                self.pipeline = pipeline

        replay.force_deterministic_predict(Wrapper())
        assert pipeline.named_steps["model"].n_jobs == 1

    def test_force_deterministic_predict_ignores_non_pipeline(self):
        class NoPipeline:
            pass

        replay.force_deterministic_predict(NoPipeline())  # must not raise


class TestArtifact:
    def test_config_echo_and_provenance_keys(self):
        config = _config()
        payload = replay.artifact_skeleton(config, inflight_gaps={"wave1-c0a:0": 3})
        for key in (
            "experiment_schema_version",
            "feature_schema_version",
            "feature_profile",
            "db_path",
            "log_base_dir",
            "study_db_root",
            "game_dir",
            "code_version",
            "dependency_extra",
            "hpo_seed",
            "config",
            "claim_boundary",
            "reused_source_data",
            "inflight_gap_trials",
        ):
            assert key in payload, key
        assert payload["reused_source_data"] is True
        assert payload["claim_boundary"]["claim_label"] == "exploratory"
        assert payload["claim_boundary"]["honest_eval_usage"] == "exploratory_selection"
        assert payload["config"]["min_train_trials"] == config.min_train_trials

    def test_artifact_has_no_wall_clock_fields(self):
        config = _config()
        payload = replay.artifact_skeleton(config, inflight_gaps={})
        flat = json.dumps(payload, sort_keys=True)
        for forbidden in ("duration_seconds", "wall_clock", "generated_at"):
            assert forbidden not in flat


# ----------------------------------------------------------- integration ---


def _write_fixture_db(path: Path, rows: list[TrainingMatchupRow]) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            create table training_matchups (
                source_path text not null,
                campaign text,
                seed integer,
                trial_number integer not null,
                build_key text not null,
                opponent_variant_id text not null,
                opponent_index integer not null,
                target real not null,
                row_kind text not null,
                primary key (source_path, trial_number, opponent_index)
            )
            """
        )
        con.execute(
            """
            create table honest_eval_matchups (
                source_path text not null,
                build_id text not null,
                build_key text,
                opponent_variant_id text not null,
                replicate_idx integer not null,
                target real not null,
                primary key (source_path, build_id, opponent_variant_id, replicate_idx)
            )
            """
        )
        con.execute(
            """
            create table recovered_builds (
                row_key text primary key,
                build_key text not null,
                source_kind text not null,
                campaign text,
                study text,
                seed integer,
                rank integer,
                trial_number integer,
                score real,
                source_path text not null,
                build_json text not null
            )
            """
        )
        con.executemany(
            "insert into training_matchups values (?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.source_path,
                    r.campaign,
                    r.seed,
                    r.trial_number,
                    r.build_key,
                    r.opponent_variant_id,
                    r.opponent_index,
                    r.target,
                    r.row_kind,
                )
                for r in rows
            ],
        )
        build_json = json.dumps(
            {
                "hull_id": "hammerhead",
                "weapon_assignments": {},
                "hullmods": [],
                "flux_vents": 0,
                "flux_capacitors": 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        con.executemany(
            "insert into recovered_builds values (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"row{key}",
                    key,
                    "exact_logged_build",
                    "wave1-c0a",
                    "study_seed0",
                    0,
                    None,
                    trial,
                    None,
                    rows[0].source_path,
                    build_json,
                )
                for trial, key in sorted({(r.trial_number, r.build_key) for r in rows})
            ],
        )
        # Oracle rows for two builds of the cell.
        oracle_rows = []
        for build_key, mean in (("build0001", 0.8), ("build0003", 0.2)):
            for rep in range(2):
                oracle_rows.append(
                    ("honest/results.jsonl", build_key, build_key, "opp_a", rep, mean)
                )
        con.executemany("insert into honest_eval_matchups values (?,?,?,?,?,?)", oracle_rows)
        con.commit()
    finally:
        con.close()


class TestIntegration:
    def test_run_replay_end_to_end_with_stubbed_models(self, tmp_path, monkeypatch):
        n_trials = 24
        cell_rel = "data/logs/wave1-c0a/study_seed0/evaluation_log.jsonl"
        rows, logs = _trial_fixture(n_trials=n_trials, pruned_trials=frozenset({7}))
        db_path = tmp_path / "phase7.sqlite"
        _write_fixture_db(db_path, rows)
        log_path = tmp_path / Path(cell_rel)
        log_path.parent.mkdir(parents=True)
        entries = []
        for (_path, trial_number), log in sorted(logs.items(), key=lambda kv: kv[0][1]):
            entries.append(
                {
                    "trial_number": trial_number,
                    "timestamp": log.timestamp,
                    "pruned": log.pruned,
                    "opponent_order": list(log.planned_opponents),
                    **(
                        {"covariate_vector": list(log.covariate_vector)}
                        if log.covariate_vector is not None
                        else {}
                    ),
                }
            )
        log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        config = _config(
            tmp_path,
            db_path=db_path,
            min_train_trials=8,
            cutoff_stride=6,
            min_future_trials=6,
            horizon_buckets=((0, 6),),
            gating_fractions=(0.2,),
            gating_sensitivity_fraction=0.2,
            bootstrap_iterations=50,
            tie_break_draws=4,
            learned_models=("catboost_regressor",),
            output=tmp_path / "artifact.json",
        )

        def fake_fit_predict(train_trials, score_trials, arm, cfg, feature_context):
            return {t.trial_number: -float(t.trial_number) for t in score_trials}

        def fake_composites(trials, cfg):
            return {t.trial_number: float(t.trial_number % 5) for t in trials if not t.pruned}

        def fake_gap(cell, cfg):
            return 2

        def fake_suite(train_trials, block_trials, arm, cfg, feature_context):
            return {"stubbed": True}

        monkeypatch.setattr(replay, "_fit_predict_scores", fake_fit_predict)
        monkeypatch.setattr(replay, "composite_scores", fake_composites)
        monkeypatch.setattr(replay, "measured_inflight_gap", fake_gap)
        monkeypatch.setattr(replay, "matchup_suite_record", fake_suite)

        payload_one = replay.run_replay(config)
        payload_two = replay.run_replay(config)
        assert json.dumps(payload_one, sort_keys=True) == json.dumps(payload_two, sort_keys=True)

        cell = payload_one["cells"]["wave1-c0a:0"]
        assert cell["n_trials"] == n_trials
        assert cell["cutoffs"] == [8, 14]
        assert set(payload_one["inflight_gap_trials"]) == {"wave1-c0a:0"}
        for gap_mode in ("zero", "measured"):
            assert gap_mode in cell["fidelity"]
            assert "catboost_regressor" in cell["fidelity"][gap_mode]
        gating = cell["gating"]["measured"]["catboost_regressor"]["0.2"]
        assert "skipped_trials" in gating and "regret" in gating
        assert "A1" in gating["regret"]
        assert cell["pruner_reference_rows_avoided"] == 2
        assert "arm_convergence" in cell
        assert "oracle_recovery" in payload_one["aggregates"]
        assert "headline" in payload_one["aggregates"]
        assert (tmp_path / "artifact.json").exists()

        flat = json.dumps(payload_one, sort_keys=True)
        assert "duration_seconds" not in flat
