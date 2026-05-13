"""Tests for Phase 7 comparator-gate baseline helpers."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from starsector_optimizer.phase7_matchup_data import HonestEvalMatchupRow, TrainingMatchupRow


SCRIPT_PATH = Path("scripts/analysis/phase7_baseline_surrogate.py")
SPEC = importlib.util.spec_from_file_location("phase7_baseline_surrogate", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
baseline = importlib.util.module_from_spec(SPEC)
sys.modules["phase7_baseline_surrogate"] = baseline
SPEC.loader.exec_module(baseline)


def _training_rows() -> list[TrainingMatchupRow]:
    return [
        TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),
        TrainingMatchupRow("p", "c0", 0, 1, "b0", "opp1", 0, 0.5, "finalized"),
        TrainingMatchupRow("p", "c0", 0, 2, "b1", "opp2", 0, -1.0, "finalized"),
    ]


def test_parse_top_k_values_rejects_empty_or_non_positive():
    assert baseline.parse_top_k_values("3,1,3") == (1, 3)
    with pytest.raises(ValueError):
        baseline.parse_top_k_values("0")


def test_group_mean_model_uses_train_only_fallback():
    rows = _training_rows()
    model = baseline.GroupMeanModel("opponent")
    model.fit(rows[:2], [{}, {}], np.asarray([1.0, 0.5]))

    result = model.predict(rows, [{}, {}, {}])

    assert result.predictions.tolist() == [1.0, 0.5, 0.75]
    assert result.diagnostics == {"opponent_fallback_count": 1}


def test_twfe_additive_reports_unseen_fallbacks():
    rows = _training_rows()
    model = baseline.TwfeAdditiveModel()
    model.fit(rows[:1], [{}], np.asarray([1.0]))

    result = model.predict(rows, [{}, {}, {}])

    assert len(result.predictions) == 3
    assert result.diagnostics["build_fallback_count"] == 1
    assert result.diagnostics["opponent_fallback_count"] == 2


def test_regression_metrics_handles_constant_rank():
    metrics = baseline.regression_metrics(
        np.asarray([1.0, 1.0]),
        np.asarray([0.5, 0.5]),
    )

    assert metrics["mae"] == pytest.approx(0.5)
    assert metrics["rmse"] == pytest.approx(0.5)
    assert metrics["spearman_rho"] is None


def test_top_k_recall_aggregates_by_build():
    rows = [
        HonestEvalMatchupRow("h", "id0", "b0", "opp", 0, 1.0),
        HonestEvalMatchupRow("h", "id0", "b0", "opp", 1, 1.0),
        HonestEvalMatchupRow("h", "id1", "b1", "opp", 0, -1.0),
        HonestEvalMatchupRow("h", "id1", "b1", "opp", 1, -1.0),
    ]

    result = baseline.top_k_recall(rows, np.asarray([0.1, 0.1, 0.9, 0.9]), (1, 2))

    assert result["honest_eval_builds"] == 2
    assert result["top_k_recall"] == {"1": 0.0, "2": 1.0}


def test_build_parser_help_constructs():
    parser = baseline.build_parser()
    text = parser.format_help()
    assert "--model" in text
    assert "--feature-profile" in text
    assert "global_mean" in text
    assert "replicate" not in text


def test_all_configs_include_random_forest_without_replicate():
    config = baseline.BaselineConfig(
        db_path=Path("db.sqlite"),
        game_dir=Path("game/starsector"),
        split="all",
        model="all",
        holdout_fraction=0.2,
        train_fraction=0.8,
        seed=17,
        tree_count=80,
        ridge_alpha=10.0,
        max_rows=None,
        top_k_values=(1, 3),
        progress=False,
    )

    configs = list(baseline._configs_to_run(config))

    assert len(configs) == 30
    assert "replicate" not in {item.split for item in configs}
    assert "random_forest" in {item.model for item in configs}


def test_provenance_shape():
    config = baseline.BaselineConfig(
        db_path=Path("db.sqlite"),
        game_dir=Path("game/starsector"),
        split="build",
        model="global_mean",
        holdout_fraction=0.2,
        train_fraction=0.8,
        seed=17,
        tree_count=80,
        ridge_alpha=10.0,
        max_rows=12,
        top_k_values=(1, 3),
        progress=False,
    )

    provenance = baseline.provenance(config)

    assert provenance["tree_count"] == 80
    assert provenance["top_k_values"] == [1, 3]
    assert provenance["feature_profile"] == "all"
