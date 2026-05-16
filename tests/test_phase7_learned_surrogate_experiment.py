"""Tests for Phase 7 learned-surrogate experiment helpers."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from starsector_optimizer.models import Build
from starsector_optimizer.phase7_matchup_data import SplitIds, TrainingMatchupRow


SCRIPT_PATH = Path("scripts/analysis/phase7_learned_surrogate_experiment.py")
SPEC = importlib.util.spec_from_file_location("phase7_learned_surrogate_experiment", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
learned = importlib.util.module_from_spec(SPEC)
sys.modules["phase7_learned_surrogate_experiment"] = learned
SPEC.loader.exec_module(learned)


def _config(**overrides):
    values = {
        "db_path": Path("data/phase7/wave1_matchups.sqlite"),
        "game_dir": Path("game/starsector"),
        "comparator_json_path": Path("data/phase7/wave1_comparator_gate_2026-05-14.json"),
        "split": "build",
        "model": "random_forest_tuned",
        "holdout_fraction": 0.2,
        "train_fraction": 0.8,
        "split_seed": 17,
        "hpo_seed": 23,
        "hpo_trials": 4,
        "hpo_jobs": 1,
        "model_thread_count": 1,
        "max_rows": 100,
        "top_k_values": (1, 3),
        "progress": False,
        "allow_missing_optional_models": False,
        "feature_profile": "all",
    }
    values.update(overrides)
    return learned.LearnedExperimentConfig(**values)


def _rows() -> list[TrainingMatchupRow]:
    return [
        TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),
        TrainingMatchupRow("p", "c0", 0, 1, "b0", "opp1", 0, 0.5, "finalized"),
        TrainingMatchupRow("p", "c1", 1, 2, "b1", "opp0", 0, -1.0, "finalized"),
        TrainingMatchupRow("p", "c1", 1, 3, "b1", "opp1", 0, -0.5, "finalized"),
    ]


def test_parser_exposes_learned_model_and_comparator_options():
    parser = learned.build_parser()
    text = parser.format_help()

    assert "catboost_regressor" in text
    assert "--comparator-json" in text
    assert "--hpo-trials" in text
    assert "--hpo-jobs" in text
    assert "--model-thread-count" in text
    assert "--feature-profile" in text
    assert "--honest-eval-usage" in text
    assert "--fresh-honest-eval-ledger-id" in text
    assert "--primary-top-k" in text
    assert "--output" in text


def test_all_configs_cover_opponent_hierarchy_splits_and_three_model_families():
    configs = learned.build_experiment_configs(_config(split="all", model="all"))

    assert len(configs) == 21
    assert {item.split for item in configs} == {
        "build",
        "opponent",
        "opponent-hull",
        "opponent-family",
        "component",
        "seed-cell",
        "forward-time",
    }
    assert {item.model for item in configs} == {
        "random_forest_tuned",
        "catboost_regressor",
        "sparse_pairwise_ridge",
    }


def test_hpo_spaces_are_explicit_and_model_specific():
    assert learned.HPO_SPACES["random_forest_tuned"]["n_estimators"] == [200, 400, 800]
    assert learned.HPO_SPACES["catboost_regressor"]["depth"] == [4, 6, 8, 10]
    assert learned.HPO_SPACES["sparse_pairwise_ridge"]["degree"] == 2
    assert learned.DEFAULT_HYPERPARAMETERS["sparse_pairwise_ridge"]["alpha"] == 10.0


def test_sample_hyperparameters_is_seed_reproducible():
    first = learned.sample_hyperparameters("random_forest_tuned", np.random.default_rng(23))
    second = learned.sample_hyperparameters("random_forest_tuned", np.random.default_rng(23))

    assert first == second


def test_trial_results_record_trial_index(monkeypatch):
    class DummyModel:
        def fit(self, rows, records, targets):
            return None

        def predict(self, rows, records):
            return learned.PredictionResult(np.asarray([0.0]), {})

    monkeypatch.setattr(learned, "make_model", lambda *args, **kwargs: DummyModel())
    bundle = learned.baseline.FeatureBundle(
        rows=tuple(_rows()[:1]),
        records=({"x": 1.0},),
        targets=np.asarray([1.0]),
    )

    _, trial, _ = learned._fit_score(
        "random_forest_tuned",
        learned.DEFAULT_HYPERPARAMETERS["random_forest_tuned"],
        bundle,
        bundle,
        23,
        1,
        trial_index=7,
    )

    assert trial.trial_index == 7


def test_config_provenance_includes_ml_context():
    provenance = learned.provenance(_config())

    assert provenance["target_variable"] == "training_matchups.target"
    assert provenance["split_seed"] == 17
    assert provenance["hpo_seed"] == 23
    assert provenance["hpo_jobs"] == 1
    assert provenance["model_thread_count"] == 1
    assert provenance["feature_profile"] == "all"
    assert provenance["honest_eval_usage"] == "diagnostic_only"
    assert provenance["primary_top_k"] == 1
    assert provenance["comparator_json_path"].endswith("wave1_comparator_gate_2026-05-14.json")


def test_final_claim_requires_fresh_honest_eval_ledger():
    with pytest.raises(ValueError, match="fresh"):
        learned.claim_boundary(_config(honest_eval_usage="final_claim"))


def test_artifact_contract_helpers_emit_registry_and_policies():
    cfg = _config(split="component", model="random_forest_tuned", honest_eval_usage="exploratory_selection")
    records = [{"weapon_range": 700.0, "slot_arc": 90.0}]
    protocol = learned.feature_selection_protocol(records, "all")
    claim = learned.claim_boundary(cfg)

    assert claim["target_variable"] == "training_matchups.target"
    assert claim["honest_eval_diagnostic_target"] == "honest_eval_top_k"
    assert claim["honest_eval_usage"] == "exploratory_selection"
    assert protocol["policy_type"] == "fixed_profile_no_selector"
    assert protocol["selected_feature_count"] == 2
    assert len(protocol["feature_family_registry_sha256"]) == 64
    assert protocol["feature_family_registry"]["weapon_range"]["family"] == "weapon_pressure"
    assert protocol["feature_family_registry"]["slot_arc"]["family"] == "slot_geometry"
    assert protocol["feature_family_registry"]["weapon_range"]["template"] == "raw_descriptor"
    assert protocol["feature_family_registry"]["weapon_range"]["parents"] == []
    assert protocol["feature_family_registry"]["weapon_range"]["leakage_risk"] == "low"
    assert learned.model_family_policy(cfg)["policy_type"] == "fixed_matrix"
    assert learned.deployment_policy(cfg)["candidate_universe"] == "source_db_builds"
    leakage = learned.leakage_diagnostics()
    assert set(leakage) == {
        "forbidden_key_overlap",
        "adversarial_validation_auc",
        "rare_combination_overlap",
        "nearest_neighbor_overlap",
        "sparse_id_ablation_delta",
    }
    assert leakage["forbidden_key_overlap"] == {
        "status": "not_applicable",
        "reason": "split_unavailable",
    }


def test_leakage_diagnostics_fail_on_forbidden_overlap():
    hierarchy = {
        "split_level": "opponent",
        "overlap_counts": {"exact_opponent": 1, "component_combination": 0},
    }

    leakage = learned.leakage_diagnostics(hierarchy)

    assert leakage["forbidden_key_overlap"] == {"status": "fail", "value": 1}


def test_leakage_diagnostics_uses_split_forbidden_key_only():
    hierarchy = {
        "split_level": "opponent",
        "overlap_counts": {
            "exact_opponent": 0,
            "component_combination": 99,
            "hull_id": 1,
        },
    }

    leakage = learned.leakage_diagnostics(hierarchy)

    assert leakage["forbidden_key_overlap"] == {"status": "pass", "value": 0}


def test_leakage_diagnostics_supports_opponent_hierarchy_splits():
    hull_leakage = learned.leakage_diagnostics({
        "split_level": "opponent-hull",
        "overlap_counts": {"exact_opponent": 0, "opponent_hull": 1},
    })
    family_leakage = learned.leakage_diagnostics({
        "split_level": "opponent-family",
        "overlap_counts": {"opponent_hull": 2, "opponent_family": 0},
    })

    assert hull_leakage["forbidden_key_overlap"] == {"status": "fail", "value": 1}
    assert family_leakage["forbidden_key_overlap"] == {"status": "pass", "value": 0}


def test_leakage_diagnostics_top_level_split_all_is_not_applicable():
    leakage = learned.leakage_diagnostics()

    assert leakage["forbidden_key_overlap"] == {
        "status": "not_applicable",
        "reason": "split_unavailable",
    }


def test_hierarchy_scorecard_component_overlap_has_exact_and_k_combinations(monkeypatch):
    split = SplitIds(
        train=(TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),),
        test=(TrainingMatchupRow("p", "c0", 0, 1, "b1", "opp1", 0, 0.5, "finalized"),),
    )
    build_lookup = {
        "b0": _make_build({"WS 001": "lightdualmg", "WS 002": "lightmg"}),
        "b1": _make_build({"WS 001": "lightdualmg", "WS 002": "railgun"}),
    }
    monkeypatch.setattr(
        learned.baseline,
        "opponent_group_maps",
        lambda game_dir, rows: (
            {"opp0": "wolf", "opp1": "enforcer"},
            {
                "opp0": "FRIGATE:Frigate:High Tech",
                "opp1": "DESTROYER:Destroyer:Low Tech",
            },
        ),
    )

    scorecard = learned.hierarchy_scorecard(_config(split="component"), split, build_lookup)

    diagnostics = scorecard["component_overlap_diagnostics"]
    assert diagnostics["exact_full_fingerprint"]["overlap_unique"] == 0
    assert diagnostics["k_1_component_combinations"]["overlap_unique"] == 5
    assert diagnostics["k_2_component_combinations"]["overlap_unique"] == 10
    assert diagnostics["k_3_component_combinations"]["overlap_unique"] == 10


def test_hierarchy_scorecard_reports_opponent_hierarchy_overlap_for_exact_opponent_split(monkeypatch):
    split = SplitIds(
        train=(TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0_a", 0, 1.0, "finalized"),),
        test=(TrainingMatchupRow("p", "c0", 0, 1, "b1", "opp0_b", 0, 0.5, "finalized"),),
    )
    build_lookup = {
        "b0": _make_build({"WS 001": "lightdualmg"}),
        "b1": _make_build({"WS 001": "railgun"}),
    }
    monkeypatch.setattr(
        learned.baseline,
        "opponent_group_maps",
        lambda game_dir, rows: (
            {"opp0_a": "wolf", "opp0_b": "wolf"},
            {
                "opp0_a": "FRIGATE:Frigate:High Tech",
                "opp0_b": "FRIGATE:Frigate:High Tech",
            },
        ),
    )

    scorecard = learned.hierarchy_scorecard(_config(split="opponent"), split, build_lookup)

    assert scorecard["overlap_counts"]["exact_opponent"] == 0
    assert scorecard["overlap_counts"]["opponent_hull"] == 1
    assert scorecard["overlap_counts"]["opponent_family"] == 1


def test_hierarchy_scorecard_names_opponent_family_fields(monkeypatch):
    split = SplitIds(
        train=(TrainingMatchupRow("p", "c0", 0, 0, "b0", "opp0", 0, 1.0, "finalized"),),
        test=(TrainingMatchupRow("p", "c0", 0, 1, "b1", "opp1", 0, 0.5, "finalized"),),
    )
    build_lookup = {
        "b0": _make_build({"WS 001": "lightdualmg"}),
        "b1": _make_build({"WS 001": "railgun"}),
    }
    monkeypatch.setattr(
        learned.baseline,
        "opponent_group_maps",
        lambda game_dir, rows: (
            {"opp0": "wolf", "opp1": "enforcer"},
            {
                "opp0": "FRIGATE:Frigate:High Tech",
                "opp1": "DESTROYER:Destroyer:Low Tech",
            },
        ),
    )

    scorecard = learned.hierarchy_scorecard(
        _config(split="opponent-family"), split, build_lookup
    )

    assert scorecard["group_key_function"] == "held_out_opponent_family_split"
    assert scorecard["forbidden_cross_split_keys"] == [
        "opponent_hull_size",
        "opponent_hull_designation",
        "opponent_hull_tech_manufacturer",
    ]
    assert scorecard["overlap_counts"]["opponent_family"] == 0


def _make_build(weapon_assignments):
    return Build(
        hull_id="hammerhead",
        weapon_assignments=weapon_assignments,
        hullmods=frozenset({"hardenedshieldemitter"}),
        flux_vents=5,
        flux_capacitors=1,
    )


def test_load_comparator_context_finds_matching_split(tmp_path):
    artifact = tmp_path / "comparator.json"
    artifact.write_text(json.dumps({
        "feature_schema_version": learned.FEATURE_SCHEMA_VERSION,
        "results": [
            {"split": "build", "model": "random_forest", "rmse": 2.0},
            {"split": "opponent", "model": "random_forest", "rmse": 3.0},
        ]
    }))

    context = learned.load_comparator_context(artifact, "build", "random_forest_tuned", max_rows=None)

    assert context["artifact_path"] == str(artifact)
    assert context["matching_result"]["split"] == "build"
    assert context["random_forest_result"]["rmse"] == 2.0
    assert context["comparison_status"] == "comparable"
    assert context["current_feature_schema_version"] == learned.FEATURE_SCHEMA_VERSION
    assert context["comparator_feature_schema_version"] == learned.FEATURE_SCHEMA_VERSION


def test_load_comparator_context_marks_schema_mismatch(tmp_path):
    artifact = tmp_path / "comparator.json"
    artifact.write_text(json.dumps({
        "feature_schema_version": 2,
        "results": [
            {"split": "build", "model": "random_forest", "rmse": 2.0},
        ],
    }))

    context = learned.load_comparator_context(artifact, "build", "random_forest_tuned", max_rows=None)

    assert context["comparison_status"] == "feature_schema_mismatch"
    assert context["comparator_feature_schema_version"] == 2


def test_missing_comparator_context_is_diagnostic(tmp_path):
    context = learned.load_comparator_context(
        tmp_path / "missing.json",
        "build",
        "random_forest_tuned",
        max_rows=None,
    )

    assert context["diagnostic"] == "comparator_missing"


def test_comparator_context_marks_row_filter_mismatch(tmp_path):
    artifact = tmp_path / "comparator.json"
    artifact.write_text(json.dumps({
        "results": [
            {
                "split": "build",
                "model": "random_forest",
                "rmse": 2.0,
                "provenance": {"max_rows": None},
            }
        ]
    }))

    context = learned.load_comparator_context(artifact, "build", "random_forest_tuned", max_rows=200)

    assert context["comparison_status"] == "row_filter_mismatch"
    assert context["current_max_rows"] == 200
    assert context["comparator_max_rows"] is None


def test_inner_split_uses_outer_training_rows_only(monkeypatch):
    rows = _rows()
    seen = {}

    def fake_split(inner_rows, holdout_fraction, seed):
        seen["rows"] = tuple(inner_rows)
        return SplitIds(train=tuple(inner_rows[:2]), test=tuple(inner_rows[2:]))

    monkeypatch.setattr(learned, "held_out_build_split", fake_split)

    split = learned.inner_validation_split(_config(split="build"), rows, {})

    assert split.train == tuple(rows[:2])
    assert split.test == tuple(rows[2:])
    assert seen["rows"] == tuple(rows)


def test_insufficient_inner_split_returns_diagnostic(monkeypatch):
    def fake_split(inner_rows, holdout_fraction, seed):
        return SplitIds(train=tuple(inner_rows), test=())

    monkeypatch.setattr(learned, "held_out_build_split", fake_split)

    result = learned.inner_validation_split(_config(split="build"), _rows(), {})

    assert result is None


def test_inner_split_propagates_opponent_descriptor_errors(monkeypatch):
    def broken_maps(game_dir, rows):
        raise ValueError("bad opponent descriptor")

    monkeypatch.setattr(learned.baseline, "opponent_group_maps", broken_maps)

    with pytest.raises(ValueError, match="bad opponent descriptor"):
        learned.inner_validation_split(_config(split="opponent-hull"), _rows(), {})


def test_catboost_missing_requires_explicit_skip(monkeypatch):
    monkeypatch.setattr(learned, "CatBoostRegressor", None)

    with pytest.raises(RuntimeError, match="uv sync --extra surrogate"):
        learned.make_model("catboost_regressor", learned.DEFAULT_HYPERPARAMETERS["catboost_regressor"], 23)

    skipped = learned.missing_optional_model_result(_config(model="catboost_regressor"))
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "missing_optional_dependency"


def test_sparse_pairwise_pipeline_constructs():
    model = learned.make_model(
        "sparse_pairwise_ridge",
        learned.DEFAULT_HYPERPARAMETERS["sparse_pairwise_ridge"],
        23,
        model_thread_count=1,
    )

    model.fit(
        _rows(),
        [{"weapon_id": "a", "range": 100.0}, {"weapon_id": "b", "range": 200.0}],
        np.asarray([1.0, -1.0]),
    )
    result = model.predict(_rows(), [{"weapon_id": "a", "range": 150.0}])
    assert result.predictions.shape == (1,)


def test_run_experiment_writes_incremental_checkpoint(monkeypatch, tmp_path):
    configs = [
        _config(split="build", model="random_forest_tuned"),
        _config(split="opponent", model="random_forest_tuned"),
    ]
    monkeypatch.setattr(learned, "build_experiment_configs", lambda config: configs)

    def fake_run_one(config):
        return {
            "split": config.split,
            "model": config.model,
            "status": "completed",
            "feature_family_registry": {
                f"{config.split}_feature": {
                    "family": "hull",
                    "template": "aggregate",
                    "parents": [],
                    "leakage_risk": "low",
                }
            },
            "feature_family_registry_sha256": "0" * 64,
            "comparator_context": {"comparison_status": "available"},
        }

    monkeypatch.setattr(learned, "run_one", fake_run_one)
    output = tmp_path / "checkpoint.json"

    payload = learned.run_experiment(_config(split="all"), checkpoint_path=output)

    written = json.loads(output.read_text())
    assert payload["status"] == "completed"
    assert payload["feature_family_registry"]["build_feature"]["family"] == "hull"
    assert payload["feature_family_registry"]["opponent_feature"]["template"] == "aggregate"
    assert len(payload["feature_family_registry_sha256"]) == 64
    assert payload["comparator_context"]["build:random_forest_tuned"]["comparison_status"] == "available"
    assert written["status"] == "running"
    assert written["result_count"] == 2
