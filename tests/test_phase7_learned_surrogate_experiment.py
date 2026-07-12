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
        "split": "build",
        "model": "random_forest_tuned",
        "holdout_fraction": 0.2,
        "train_fraction": 0.8,
        "split_seed": 101,
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


def test_parser_exposes_learned_model_and_eval_options():
    parser = learned.build_parser()
    text = parser.format_help()

    assert "catboost_regressor" in text
    assert "--comparator-json" not in text
    assert "--hpo-trials" in text
    assert "--hpo-jobs" in text
    assert "--model-thread-count" in text
    assert "--inner-cv-folds" in text
    assert "--noise-floor-override" in text
    assert "--bootstrap-resamples" in text
    assert "--component-vocab-max-overshoot" in text
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
        "component-vocab",
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
    assert provenance["split_seed"] == 101
    assert provenance["hpo_seed"] == 23
    assert provenance["hpo_jobs"] == 1
    assert provenance["model_thread_count"] == 1
    assert provenance["inner_cv_folds"] == learned.DEFAULT_INNER_CV_FOLDS
    assert provenance["feature_profile"] == "all"
    assert provenance["honest_eval_usage"] == "diagnostic_only"
    assert provenance["honest_eval_ledger_id"]
    assert provenance["honest_eval_run_lineage"]
    assert provenance["primary_top_k"] == 1
    assert provenance["promotion_metric"] == "mean_per_opponent_spearman"
    assert provenance["final_refit_policy"] == "fit_outer_train_only_no_deployment_artifact"
    assert "comparator_json_path" not in provenance


def test_burned_split_seed_rejected():
    with pytest.raises(ValueError, match="C4"):
        learned.claim_boundary(_config(split_seed=17))


def test_outer_split_lineage_marks_bank_and_reused_partition():
    lineage = learned.outer_split_lineage(_config(split_seed=101))
    assert lineage["seed_bank_label"] == "2026-07-bank-a"
    assert lineage["confirmatory_reserved_seed"] == 151
    assert lineage["reused_partition"] is False

    ad_hoc = learned.outer_split_lineage(_config(split_seed=997))
    assert ad_hoc["seed_bank_label"] == "ad-hoc"

    confirmatory = learned.outer_split_lineage(_config(split_seed=151))
    assert confirmatory["seed_bank_label"] == "reserved-confirmatory"

    forward = learned.outer_split_lineage(_config(split="forward-time"))
    assert forward["reused_partition"] is True


def test_default_model_is_catboost_after_seed151_ratification():
    parser = learned.build_parser()
    args = parser.parse_args(["db.sqlite"])
    assert args.model == learned.DEFAULT_MODEL == "catboost_regressor"


def test_experiment_schema_version_is_two():
    assert learned.EXPERIMENT_SCHEMA_VERSION == 2


def test_final_claim_requires_fresh_honest_eval_ledger():
    with pytest.raises(ValueError, match="fresh"):
        learned.claim_boundary(_config(honest_eval_usage="final_claim"))


def test_artifact_contract_helpers_emit_registry_and_policies():
    cfg = _config(
        split="component-vocab",
        model="random_forest_tuned",
        honest_eval_usage="exploratory_selection",
    )
    records = [{"weapon_range": 700.0, "slot_arc": 90.0, "build_hullmod__heavyarmor": 1}]
    protocol = learned.feature_selection_protocol(records, "all")
    claim = learned.claim_boundary(
        cfg,
        {
            "ledger_id": "data/honest_eval/example/results.jsonl",
            "run_lineage": ["data/honest_eval/example/results.jsonl"],
        },
    )

    assert claim["target_variable"] == "training_matchups.target"
    assert claim["honest_eval_diagnostic_target"] == "honest_eval_top_k"
    assert claim["honest_eval_usage"] == "exploratory_selection"
    assert claim["honest_eval_ledger_id"] == "data/honest_eval/example/results.jsonl"
    assert claim["honest_eval_run_lineage"] == ["data/honest_eval/example/results.jsonl"]
    assert protocol["policy_type"] == "fixed_profile_no_selector"
    assert protocol["selected_feature_count"] == 3
    assert len(protocol["feature_family_registry_sha256"]) == 64
    assert protocol["feature_family_registry"]["weapon_range"]["family"] == "weapon_pressure"
    assert protocol["feature_family_registry"]["slot_arc"]["family"] == "slot_geometry"
    assert protocol["feature_family_registry"]["weapon_range"]["template"] == "raw_descriptor"
    assert protocol["feature_family_registry"]["weapon_range"]["parents"] == []
    assert protocol["feature_family_registry"]["weapon_range"]["leakage_risk"] == "low"
    assert (
        protocol["feature_family_registry"]["build_hullmod__heavyarmor"]["template"]
        == "sparse_indicator"
    )
    assert (
        protocol["feature_family_registry"]["build_hullmod__heavyarmor"]["leakage_risk"] == "medium"
    )
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
    hull_leakage = learned.leakage_diagnostics(
        {
            "split_level": "opponent-hull",
            "overlap_counts": {"exact_opponent": 0, "opponent_hull": 1},
        }
    )
    family_leakage = learned.leakage_diagnostics(
        {
            "split_level": "opponent-family",
            "overlap_counts": {"opponent_hull": 2, "opponent_family": 0},
        }
    )

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

    scorecard = learned.hierarchy_scorecard(
        _config(split="component-vocab"),
        split,
        build_lookup,
        {"held_out_components": ["weapon:railgun"], "realized_test_fraction": 0.5},
    )

    diagnostics = scorecard["component_overlap_diagnostics"]
    assert diagnostics["exact_full_fingerprint"]["overlap_unique"] == 0
    assert diagnostics["k_1_component_combinations"]["overlap_unique"] == 5
    assert diagnostics["k_2_component_combinations"]["overlap_unique"] == 10
    assert diagnostics["k_3_component_combinations"]["overlap_unique"] == 10
    assert scorecard["group_key_function"] == "held_out_component_vocabulary_split"
    assert scorecard["forbidden_cross_split_keys"] == ["component_vocabulary"]
    assert scorecard["component_key_definition"] == "slot_agnostic_weapon_and_hullmod_vocabulary"
    assert scorecard["held_out_components"] == ["weapon:railgun"]
    # "weapon:railgun" is held out but present in the TRAIN build b0? No —
    # b0 has lightdualmg/lightmg, so the forbidden count must be zero.
    assert scorecard["overlap_counts"]["component_vocabulary"] == 0


def test_leakage_diagnostics_component_vocab_uses_vocabulary_overlap():
    leakage = learned.leakage_diagnostics(
        {
            "split_level": "component-vocab",
            "overlap_counts": {"component_vocabulary": 0, "component_combination": 4},
        }
    )

    assert leakage["forbidden_key_overlap"] == {"status": "pass", "value": 0}

    failing = learned.leakage_diagnostics(
        {
            "split_level": "component-vocab",
            "overlap_counts": {"component_vocabulary": 2},
        }
    )
    assert failing["forbidden_key_overlap"] == {"status": "fail", "value": 2}


def test_hierarchy_scorecard_reports_opponent_hierarchy_overlap_for_exact_opponent_split(
    monkeypatch,
):
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

    scorecard = learned.hierarchy_scorecard(_config(split="opponent-family"), split, build_lookup)

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


def test_matched_comparator_families_are_explicit():
    assert learned.MATCHED_COMPARATOR_FAMILY == {
        "random_forest_tuned": "random_forest",
        "sparse_pairwise_ridge": "ridge_hybrid",
        "catboost_regressor": None,
    }


def _comparator_result(rmse, mean_spearman):
    return {
        "mae": rmse,
        "rmse": rmse,
        "spearman_rho": 0.5,
        "rank_metrics": {"per_opponent": {"mean_spearman": mean_spearman}},
    }


def test_comparator_deltas_use_best_and_matched_family():
    comparators = {
        "random_forest": _comparator_result(0.5, 0.4),
        "ridge_hybrid": _comparator_result(0.4, 0.3),
        "opponent_mean": _comparator_result(0.6, None),
    }
    learned_metrics = {"mae": 0.3, "rmse": 0.3, "spearman_rho": 0.7}
    learned_rank = {"per_opponent": {"mean_spearman": 0.6}}

    deltas = learned.comparator_deltas(
        learned_metrics, learned_rank, "random_forest_tuned", comparators
    )

    assert deltas["best_comparator"] == "ridge_hybrid"
    assert deltas["delta_vs_best_comparator"]["rmse"] == pytest.approx(-0.1)
    assert deltas["delta_vs_best_comparator"]["mean_per_opponent_spearman"] == pytest.approx(0.3)
    assert deltas["matched_family"] == "random_forest"
    assert deltas["delta_vs_matched_family"]["rmse"] == pytest.approx(-0.2)


def test_comparator_deltas_catboost_has_no_matched_family():
    comparators = {"random_forest": _comparator_result(0.5, 0.4)}
    deltas = learned.comparator_deltas(
        {"mae": 0.3, "rmse": 0.3, "spearman_rho": 0.7},
        {"per_opponent": {"mean_spearman": 0.6}},
        "catboost_regressor",
        comparators,
    )

    assert deltas["matched_family"] is None
    assert deltas["delta_vs_matched_family"] is None
    assert deltas["best_comparator"] == "random_forest"


def test_inner_cv_splits_use_grouped_kfold_on_outer_training_rows(monkeypatch):
    rows = _rows()
    seen = {}

    def fake_kfold(inner_rows, groups, n_folds, seed):
        seen["rows"] = tuple(inner_rows)
        seen["groups"] = tuple(groups)
        seen["n_folds"] = n_folds
        seen["seed"] = seed
        return (SplitIds(train=tuple(inner_rows[:2]), test=tuple(inner_rows[2:])),)

    monkeypatch.setattr(learned, "grouped_kfold", fake_kfold)

    folds = learned.inner_cv_splits(_config(split="build"), rows, {})

    assert len(folds) == 1
    assert seen["rows"] == tuple(rows)
    assert seen["groups"] == tuple(row.build_key for row in rows)
    assert seen["n_folds"] == learned.DEFAULT_INNER_CV_FOLDS
    assert seen["seed"] == 23


def test_inner_cv_splits_insufficient_groups_returns_empty(monkeypatch):
    monkeypatch.setattr(learned, "grouped_kfold", lambda *args, **kwargs: ())

    assert learned.inner_cv_splits(_config(split="build"), _rows(), {}) == ()


def test_inner_cv_splits_forward_time_uses_rolling_origin():
    rows = [
        TrainingMatchupRow("p", "c0", 0, i, f"b{i}", "opp0", 0, float(i), "finalized")
        for i in range(8)
    ]

    folds = learned.inner_cv_splits(_config(split="forward-time", inner_cv_folds=3), rows, {})

    assert len(folds) == 3
    for fold in folds:
        assert fold.train and fold.test
        assert max(row.trial_number for row in fold.train) < min(
            row.trial_number for row in fold.test
        )
    # Origins roll forward: training prefixes strictly grow.
    train_sizes = [len(fold.train) for fold in folds]
    assert train_sizes == sorted(train_sizes)


def test_inner_cv_splits_component_vocab_degenerate_draw_returns_empty(monkeypatch):
    from starsector_optimizer.phase7_matchup_data import ComponentVocabularyError

    def broken_split(*args, **kwargs):
        raise ComponentVocabularyError("component vocabulary exhausted")

    monkeypatch.setattr(learned, "held_out_component_vocabulary_split", broken_split)

    result = learned.inner_cv_splits(_config(split="component-vocab"), _rows(), {})

    assert result == ()


def test_run_one_converts_degenerate_vocab_draw_to_insufficiency(monkeypatch):
    from starsector_optimizer.phase7_matchup_data import ComponentVocabularyError

    def broken_split_rows(config):
        raise ComponentVocabularyError("component vocabulary holdout overshoot")

    monkeypatch.setattr(learned.baseline, "_split_rows", broken_split_rows)
    monkeypatch.setattr(
        learned,
        "_honest_eval_lineage",
        lambda db_path: {
            "status": "not_applicable",
            "source_paths": [],
            "ledger_id": None,
            "run_lineage": [],
        },
    )

    result = learned.run_one(_config(split="component-vocab"))

    assert result["status"] == "degenerate_component_vocab_split"
    assert result["outer_split_lineage"]["split_seed"] == 101


def test_run_one_propagates_config_errors_unconverted(monkeypatch):
    def broken_split_rows(config):
        raise ValueError("holdout_fraction must be in (0, 1)")

    monkeypatch.setattr(learned.baseline, "_split_rows", broken_split_rows)

    with pytest.raises(ValueError, match="holdout_fraction"):
        learned.run_one(_config(split="component-vocab"))


def test_split_feasibility_report_names_infeasible_cells(monkeypatch):
    from starsector_optimizer.phase7_matchup_data import ComponentVocabularyError

    def broken_split_rows(config):
        raise ComponentVocabularyError("component vocabulary holdout overshoot")

    monkeypatch.setattr(learned.baseline, "_split_rows", broken_split_rows)

    report = learned.split_feasibility_report([_config(split="component-vocab", split_seed=109)])

    assert report == [
        {
            "split": "component-vocab",
            "split_seed": 109,
            "status": "degenerate_component_vocab_split",
        }
    ]


def test_split_feasibility_report_empty_for_feasible_cells(monkeypatch):
    monkeypatch.setattr(
        learned, "construct_splits", lambda config: (None, ("split", {}, {}, ("fold",)))
    )

    assert learned.split_feasibility_report([_config(split="build")]) == []


def test_inner_validation_metadata_documents_grouped_outer_training_contract():
    metadata = learned.inner_validation_metadata(_config(split="opponent-family"))

    assert metadata["split_role"] == "inner_validation"
    assert metadata["source_rows"] == "outer_training_rows_only"
    assert metadata["group_key_function"] == "opponent_size_designation_manufacturer_family"
    assert metadata["inner_cv_folds"] == learned.DEFAULT_INNER_CV_FOLDS
    assert metadata["fold_construction"] == "grouped_kfold"
    assert metadata["random_row_fallback"] is False
    assert metadata["fallback_behavior"] == "insufficient_inner_groups"


def test_forward_time_inner_validation_metadata_documents_blocking():
    metadata = learned.inner_validation_metadata(_config(split="forward-time"))

    assert metadata["source_rows"] == "outer_training_rows_only"
    assert metadata["fold_construction"] == "rolling_origin"
    assert metadata["temporal_semantics"] == "blocked_prefix_suffix_within_outer_training_prefix"


def test_inner_cv_propagates_opponent_descriptor_errors(monkeypatch):
    def broken_maps(game_dir, rows):
        raise ValueError("bad opponent descriptor")

    monkeypatch.setattr(learned.baseline, "opponent_group_maps", broken_maps)

    with pytest.raises(ValueError, match="bad opponent descriptor"):
        learned.inner_cv_splits(_config(split="opponent-hull"), _rows(), {})


def test_tune_hyperparameters_aligns_model_seed_and_averages_folds(monkeypatch):
    seen_seeds = []
    rmse_by_call = iter([1.0, 3.0, 4.0, 2.0])  # trial0: folds (1,3); trial1: folds (4,2)

    def fake_fit_score(model, params, train, test, seed, threads, trial_index=-1):
        seen_seeds.append(seed)
        metrics = {"mae": 0.0, "rmse": next(rmse_by_call), "spearman_rho": 0.5}
        return None, learned.TrialResult(trial_index, dict(params), metrics, 0.1), None

    monkeypatch.setattr(learned, "_fit_score", fake_fit_score)
    bundle = learned.baseline.FeatureBundle(
        rows=tuple(_rows()[:1]), records=({"x": 1.0},), targets=np.asarray([1.0])
    )
    config = _config(hpo_trials=2, hpo_jobs=1)

    hpo = learned.tune_hyperparameters(config, [(bundle, bundle), (bundle, bundle)])

    # Aligned seeds (M1): every trial fit uses the shipping seed.
    assert set(seen_seeds) == {23}
    assert hpo["selection_objective"] == "minimize_mean_inner_validation_rmse"
    assert hpo["inner_cv_folds"] == 2
    # Trial 0 mean rmse = 2.0 < trial 1 mean rmse = 3.0.
    assert hpo["inner_validation_metrics"]["rmse"] == pytest.approx(2.0)
    assert hpo["trials"][0]["metrics"]["fold_count"] == 2


def test_catboost_missing_requires_explicit_skip(monkeypatch):
    monkeypatch.setattr(learned, "CatBoostRegressor", None)

    with pytest.raises(RuntimeError, match="uv sync --extra surrogate"):
        learned.make_model(
            "catboost_regressor", learned.DEFAULT_HYPERPARAMETERS["catboost_regressor"], 23
        )

    skipped = learned.missing_optional_model_result(_config(model="catboost_regressor"))
    assert skipped["status"] == "skipped"
    assert skipped["claim_boundary"]["honest_eval_ledger_id"]
    assert skipped["claim_boundary"]["honest_eval_run_lineage"]
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
            "comparator_inline": {"random_forest": {"rmse": 0.5}},
        }

    monkeypatch.setattr(learned, "run_one", fake_run_one)
    output = tmp_path / "checkpoint.json"

    payload = learned.run_experiment(_config(split="all"), checkpoint_path=output)

    written = json.loads(output.read_text())
    assert payload["status"] == "completed"
    assert payload["feature_family_registry"]["build_feature"]["family"] == "hull"
    assert payload["feature_family_registry"]["opponent_feature"]["template"] == "aggregate"
    assert len(payload["feature_family_registry_sha256"]) == 64
    assert payload["comparator_inline"]["build:random_forest_tuned"]["random_forest"]["rmse"] == 0.5
    assert payload["outer_split_lineage"]["confirmatory_reserved_seed"] == 151
    assert written["status"] == "running"
    assert written["result_count"] == 2
