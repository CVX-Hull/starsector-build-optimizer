"""Tests for Phase 7 comparator-gate baseline helpers."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from starsector_optimizer.models import Build
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
    config = _baseline_config(split="all", model="all")

    configs = list(baseline._configs_to_run(config))

    assert len(configs) == 42
    assert "replicate" not in {item.split for item in configs}
    assert "component-vocab" in {item.split for item in configs}
    assert "component" not in {item.split for item in configs}
    assert "opponent-hull" in {item.split for item in configs}
    assert "opponent-family" in {item.split for item in configs}
    assert "random_forest" in {item.model for item in configs}


def test_provenance_shape():
    config = _baseline_config(max_rows=12)

    provenance = baseline.provenance(config)

    assert provenance["tree_count"] == 80
    assert provenance["top_k_values"] == [1, 3]
    assert provenance["feature_profile"] == "all"
    assert provenance["bootstrap_resamples"] == 500
    assert provenance["component_vocab_max_overshoot"] == pytest.approx(0.35)


def test_split_metadata_names_component_vocab_key_definition():
    config = _baseline_config(split="component-vocab")

    metadata = baseline.split_metadata(config)

    assert metadata["split_level"] == "component-vocab"
    assert metadata["group_key_function"] == "component_vocabulary_membership"
    assert metadata["component_key_definition"] == "slot_agnostic_weapon_and_hullmod_vocabulary"


def test_default_seed_is_first_bank_seed_not_burned():
    assert baseline.DEFAULT_RANDOM_SEED == 101


def test_split_rows_rejects_burned_seed():
    config = _baseline_config(split="build", seed=17, db_path=Path("does-not-exist.sqlite"))

    with pytest.raises(ValueError, match="C4"):
        baseline._split_rows(config)


def test_group_metric_supports_opponent_variant_stratum():
    rows = _training_rows()
    grouped = baseline._group_metric(
        rows,
        [{}, {}, {}],
        np.asarray([1.0, 0.5, -1.0]),
        np.asarray([0.9, 0.4, -0.8]),
        "opponent_variant_id",
    )

    assert set(grouped) == {"opp0", "opp1", "opp2"}
    assert grouped["opp0"]["n"] == 1


def _baseline_config(**overrides):
    values = {
        "db_path": Path("db.sqlite"),
        "game_dir": Path("game/starsector"),
        "split": "build",
        "model": "global_mean",
        "holdout_fraction": 0.2,
        "train_fraction": 0.8,
        "seed": 101,
        "tree_count": 80,
        "ridge_alpha": 10.0,
        "max_rows": None,
        "top_k_values": (1, 3),
        "progress": False,
    }
    values.update(overrides)
    return baseline.BaselineConfig(**values)


def test_split_metadata_names_opponent_hierarchy_groups():
    config = _baseline_config(split="opponent-family")

    metadata = baseline.split_metadata(config)

    assert metadata["split_level"] == "opponent-family"
    assert metadata["group_key_function"] == "opponent_size_designation_manufacturer_family"
    assert "opponent_hull_designation" in metadata["group_key_fields"]


def test_opponent_group_maps_requires_family_fields(monkeypatch):
    monkeypatch.setattr(baseline, "_load_context", lambda game_dir: (object(), object()))
    monkeypatch.setattr(
        baseline,
        "opponent_feature_row",
        lambda variant_id, game_dir, game_data: {"opponent_hull_id": "wolf"},
    )

    with pytest.raises(ValueError, match="missing family field"):
        baseline.opponent_group_maps(Path("game/starsector"), _training_rows()[:1])


def test_component_overlap_diagnostics_reports_exact_and_k_combinations():
    train = [
        TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),
    ]
    test = [
        TrainingMatchupRow("p", "c0", 0, 1, "b1", "opp1", 0, 0.5, "finalized"),
    ]
    build_lookup = {
        "b0": _make_build({"WS 001": "lightdualmg", "WS 002": "lightmg"}),
        "b1": _make_build({"WS 001": "lightdualmg", "WS 002": "railgun"}),
    }

    diagnostics = baseline.component_overlap_diagnostics(train, test, build_lookup)

    assert diagnostics["exact_full_fingerprint"]["overlap_unique"] == 0
    assert diagnostics["k_1_component_combinations"]["overlap_unique"] == 5
    assert diagnostics["k_1_component_combinations"]["test_unique"] == 6
    assert diagnostics["k_2_component_combinations"]["overlap_unique"] == 10
    assert diagnostics["k_2_component_combinations"]["test_unique"] == 15
    assert diagnostics["k_3_component_combinations"]["overlap_unique"] == 10
    assert diagnostics["k_3_component_combinations"]["test_unique"] == 20


def test_split_overlap_counts_reports_stricter_hierarchy_counts():
    train = [
        TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),
    ]
    test = [
        TrainingMatchupRow("p", "c1", 1, 1, "b1", "opp1", 0, 0.5, "finalized"),
    ]
    build_lookup = {
        "b0": _make_build({"WS 001": "lightdualmg"}),
        "b1": _make_build({"WS 001": "lightdualmg"}),
    }

    counts = baseline.split_overlap_counts(
        train,
        test,
        build_lookup,
        opponent_hull_by_variant={"opp0": "wolf", "opp1": "wolf"},
        opponent_family_by_variant={
            "opp0": "FRIGATE:Frigate:High Tech",
            "opp1": "FRIGATE:Frigate:High Tech",
        },
        held_out_components=["weapon:lightdualmg", "weapon:railgun"],
    )

    assert counts["exact_build"] == 0
    assert counts["exact_opponent"] == 0
    assert counts["opponent_hull"] == 1
    assert counts["opponent_family"] == 1
    assert counts["hull_id"] == 1
    assert counts["component_combination"] == 1
    # weapon:lightdualmg appears in the train build b0; railgun does not.
    assert counts["component_vocabulary"] == 1
    assert counts["campaign_cell"] == 0
    assert counts["exact_matchup_group"] == 0


def _make_build(weapon_assignments):
    return Build(
        hull_id="hammerhead",
        weapon_assignments=weapon_assignments,
        hullmods=frozenset({"hardenedshieldemitter"}),
        flux_vents=5,
        flux_capacitors=1,
    )
