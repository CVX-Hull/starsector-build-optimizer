#!/usr/bin/env python
"""Run learned Phase 7 matchup surrogate experiments."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.kernel_approximation import PolynomialCountSketch
from sklearn.linear_model import Ridge
from sklearn.pipeline import FeatureUnion, Pipeline

from starsector_optimizer.matchup_features import (
    DEFAULT_FEATURE_PROFILE,
    FEATURE_PROFILES,
    FEATURE_SCHEMA_VERSION,
)
from starsector_optimizer.phase7_matchup_data import (
    SplitIds,
    TrainingMatchupRow,
    forward_time_split,
    held_out_build_split,
    held_out_component_combination_split,
    held_out_opponent_split,
    held_out_seed_cell_split,
)


try:
    from catboost import CatBoostRegressor
except ImportError:  # pragma: no cover - exercised by monkeypatch tests.
    CatBoostRegressor = None


_BASELINE_PATH = Path(__file__).with_name("phase7_baseline_surrogate.py")
_BASELINE_SPEC = importlib.util.spec_from_file_location("_phase7_baseline_surrogate", _BASELINE_PATH)
assert _BASELINE_SPEC is not None and _BASELINE_SPEC.loader is not None
baseline = importlib.util.module_from_spec(_BASELINE_SPEC)
sys.modules.setdefault("_phase7_baseline_surrogate", baseline)
_BASELINE_SPEC.loader.exec_module(baseline)


EXPERIMENT_SCHEMA_VERSION = 1
DEFAULT_HOLDOUT_FRACTION = baseline.DEFAULT_HOLDOUT_FRACTION
DEFAULT_TRAIN_FRACTION = baseline.DEFAULT_TRAIN_FRACTION
DEFAULT_SPLIT_SEED = baseline.DEFAULT_RANDOM_SEED
DEFAULT_HPO_SEED = 23
DEFAULT_HPO_TRIALS = 24
DEFAULT_COMPARATOR_JSON = Path("data/phase7/wave1_comparator_gate_2026-05-11.json")
SPLIT_CHOICES = baseline.SPLIT_CHOICES
MODEL_CHOICES = ("random_forest_tuned", "catboost_regressor", "sparse_pairwise_ridge")
TARGET_VARIABLE = "training_matchups.target"
ALL_AVAILABLE_CORES = -1

HPO_SPACES: dict[str, dict[str, object]] = {
    "random_forest_tuned": {
        "n_estimators": [200, 400, 800],
        "max_depth": [None, 16, 32, 64],
        "min_samples_leaf": [1, 2, 4, 8],
        "max_features": ["sqrt", 0.35, 0.6, 1.0],
        "bootstrap": True,
        "max_samples": [None, 0.65, 0.85],
    },
    "catboost_regressor": {
        "iterations": [300, 600, 1000],
        "learning_rate": {"distribution": "log_uniform", "low": 0.02, "high": 0.2},
        "depth": [4, 6, 8, 10],
        "l2_leaf_reg": {"distribution": "log_uniform", "low": 1.0, "high": 30.0},
        "random_strength": {"distribution": "log_uniform", "low": 0.1, "high": 10.0},
        "bagging_temperature": [0.0, 0.5, 1.0, 2.0],
    },
    "sparse_pairwise_ridge": {
        "n_components": [512, 1024, 2048, 4096],
        "alpha": {"distribution": "log_uniform", "low": 0.001, "high": 1000.0},
        "degree": 2,
        "include_original_features": True,
    },
}

DEFAULT_HYPERPARAMETERS: dict[str, dict[str, object]] = {
    "random_forest_tuned": {
        "n_estimators": baseline.DEFAULT_TREE_COUNT,
        "max_depth": None,
        "min_samples_leaf": baseline.DEFAULT_MIN_SAMPLES_LEAF,
        "max_features": "sqrt",
        "bootstrap": True,
        "max_samples": None,
    },
    "catboost_regressor": {
        "iterations": 600,
        "learning_rate": 0.05,
        "depth": 6,
        "l2_leaf_reg": 3.0,
        "random_strength": 1.0,
        "bagging_temperature": 0.0,
    },
    "sparse_pairwise_ridge": {
        "n_components": 1024,
        "alpha": 10.0,
        "degree": 2,
        "include_original_features": True,
    },
}


FeatureValue = float | int | str


@dataclass(frozen=True)
class LearnedExperimentConfig:
    db_path: Path
    game_dir: Path
    comparator_json_path: Path | None
    split: str
    model: str
    holdout_fraction: float
    train_fraction: float
    split_seed: int
    hpo_seed: int
    hpo_trials: int
    hpo_jobs: int
    model_thread_count: int
    max_rows: int | None
    top_k_values: tuple[int, ...]
    progress: bool
    allow_missing_optional_models: bool
    feature_profile: str = DEFAULT_FEATURE_PROFILE
    batch_job_id: str | None = None
    batch_name: str | None = None
    batch_fleet_name: str | None = None


@dataclass(frozen=True)
class PredictionResult:
    predictions: np.ndarray
    diagnostics: dict[str, int | float | str]


@dataclass(frozen=True)
class TrialResult:
    trial_index: int
    hyperparameters: dict[str, object]
    metrics: dict[str, float | None]
    duration_seconds: float


class LearnedModel(Protocol):
    def fit(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        ...

    def predict(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
    ) -> PredictionResult:
        ...


class PipelineModel:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline

    def fit(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        self.pipeline.fit(list(records), targets)

    def predict(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
    ) -> PredictionResult:
        return PredictionResult(np.asarray(self.pipeline.predict(list(records)), dtype=float), {})


class CatBoostModel:
    def __init__(self, params: Mapping[str, object], hpo_seed: int) -> None:
        if CatBoostRegressor is None:
            raise _missing_catboost_error()
        self.params = {
            **params,
            "loss_function": "RMSE",
            "eval_metric": "RMSE",
            "allow_writing_files": False,
            "verbose": False,
            "random_seed": hpo_seed,
        }
        self.model = CatBoostRegressor(**self.params)
        self.categorical_columns: list[str] = []
        self.columns: list[str] = []

    def _frame(self, records: Sequence[Mapping[str, FeatureValue]], *, fit: bool) -> pd.DataFrame:
        frame = pd.DataFrame(list(records))
        if fit:
            self.columns = list(frame.columns)
            self.categorical_columns = [
                column for column in frame.columns
                if frame[column].dtype == object or frame[column].map(lambda item: isinstance(item, str)).any()
            ]
        else:
            frame = frame.reindex(columns=self.columns)
        for column in self.categorical_columns:
            frame[column] = frame[column].fillna("MISSING").astype(str)
        numeric_columns = [column for column in frame.columns if column not in self.categorical_columns]
        for column in numeric_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        return frame

    def fit(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        frame = self._frame(records, fit=True)
        self.model.fit(frame, targets, cat_features=self.categorical_columns)

    def predict(
        self,
        rows: Sequence[TrainingMatchupRow],
        records: Sequence[Mapping[str, FeatureValue]],
    ) -> PredictionResult:
        frame = self._frame(records, fit=False)
        return PredictionResult(np.asarray(self.model.predict(frame), dtype=float), {})


class SparseIdentity(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None):
        return self

    def transform(self, X):
        return X


def _missing_catboost_error() -> RuntimeError:
    return RuntimeError(
        "catboost_regressor requires the optional surrogate dependency set. "
        "Run `uv sync --extra surrogate` or pass --allow-missing-optional-models."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit learned Phase 7 matchup surrogate baselines."
    )
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument("--comparator-json", type=Path, default=DEFAULT_COMPARATOR_JSON)
    parser.add_argument("--split", choices=(*SPLIT_CHOICES, "all"), default="build")
    parser.add_argument("--model", choices=(*MODEL_CHOICES, "all"), default="random_forest_tuned")
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_TRAIN_FRACTION)
    parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    parser.add_argument("--hpo-seed", type=int, default=DEFAULT_HPO_SEED)
    parser.add_argument("--hpo-trials", type=int, default=DEFAULT_HPO_TRIALS)
    parser.add_argument("--hpo-jobs", type=int, default=1)
    parser.add_argument("--model-thread-count", type=int, default=ALL_AVAILABLE_CORES)
    parser.add_argument("--top-k", default=",".join(str(item) for item in baseline.DEFAULT_TOP_K_VALUES))
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--feature-profile", choices=FEATURE_PROFILES, default=DEFAULT_FEATURE_PROFILE)
    parser.add_argument("--batch-job-id", default=None)
    parser.add_argument("--batch-name", default=None)
    parser.add_argument("--batch-fleet-name", default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--allow-missing-optional-models", action="store_true")
    return parser


def parse_top_k_values(raw: str) -> tuple[int, ...]:
    return baseline.parse_top_k_values(raw)


def build_experiment_configs(config: LearnedExperimentConfig) -> list[LearnedExperimentConfig]:
    splits = SPLIT_CHOICES if config.split == "all" else (config.split,)
    models = MODEL_CHOICES if config.model == "all" else (config.model,)
    return [
        LearnedExperimentConfig(
            db_path=config.db_path,
            game_dir=config.game_dir,
            comparator_json_path=config.comparator_json_path,
            split=split,
            model=model,
            holdout_fraction=config.holdout_fraction,
            train_fraction=config.train_fraction,
            split_seed=config.split_seed,
            hpo_seed=config.hpo_seed,
            hpo_trials=config.hpo_trials,
            hpo_jobs=config.hpo_jobs,
            model_thread_count=config.model_thread_count,
            max_rows=config.max_rows,
            top_k_values=config.top_k_values,
            progress=config.progress,
            allow_missing_optional_models=config.allow_missing_optional_models,
            feature_profile=config.feature_profile,
            batch_job_id=config.batch_job_id,
            batch_name=config.batch_name,
            batch_fleet_name=config.batch_fleet_name,
        )
        for split in splits
        for model in models
    ]


def _baseline_config(config: LearnedExperimentConfig) -> baseline.BaselineConfig:
    return baseline.BaselineConfig(
        db_path=config.db_path,
        game_dir=config.game_dir,
        split=config.split,
        model="random_forest",
        holdout_fraction=config.holdout_fraction,
        train_fraction=config.train_fraction,
        seed=config.split_seed,
        tree_count=baseline.DEFAULT_TREE_COUNT,
        ridge_alpha=baseline.DEFAULT_RIDGE_ALPHA,
        max_rows=config.max_rows,
        top_k_values=config.top_k_values,
        progress=config.progress,
        feature_profile=config.feature_profile,
    )


def inner_validation_split(
    config: LearnedExperimentConfig,
    rows: Sequence[TrainingMatchupRow],
    build_lookup: Mapping[str, object],
) -> SplitIds | None:
    try:
        if config.split == "build":
            split = held_out_build_split(rows, config.holdout_fraction, config.hpo_seed)
        elif config.split == "opponent":
            split = held_out_opponent_split(rows, config.holdout_fraction, config.hpo_seed)
        elif config.split == "component":
            split = held_out_component_combination_split(rows, build_lookup, config.holdout_fraction, config.hpo_seed)
        elif config.split == "seed-cell":
            split = held_out_seed_cell_split(rows, config.holdout_fraction, config.hpo_seed)
        elif config.split == "forward-time":
            split = forward_time_split(rows, config.train_fraction)
        else:
            raise ValueError(f"unknown split {config.split!r}")
    except ValueError:
        return None
    return split if split.train and split.test else None


def _sample_value(spec: object, rng: np.random.Generator) -> object:
    if isinstance(spec, list):
        return spec[int(rng.integers(0, len(spec)))]
    if isinstance(spec, dict) and spec.get("distribution") == "log_uniform":
        low = math.log(float(spec["low"]))
        high = math.log(float(spec["high"]))
        return float(math.exp(rng.uniform(low, high)))
    return spec


def sample_hyperparameters(model: str, rng: np.random.Generator) -> dict[str, object]:
    if model not in HPO_SPACES:
        raise ValueError(f"unknown model {model!r}")
    return {name: _sample_value(spec, rng) for name, spec in HPO_SPACES[model].items()}


def make_model(
    model: str,
    hyperparameters: Mapping[str, object],
    hpo_seed: int,
    *,
    model_thread_count: int = ALL_AVAILABLE_CORES,
) -> LearnedModel:
    if model == "random_forest_tuned":
        params = dict(hyperparameters)
        return PipelineModel(Pipeline([
            ("features", DictVectorizer(sparse=True)),
            ("model", RandomForestRegressor(
                n_estimators=int(params["n_estimators"]),
                max_depth=params["max_depth"],
                min_samples_leaf=int(params["min_samples_leaf"]),
                max_features=params["max_features"],
                bootstrap=bool(params["bootstrap"]),
                max_samples=params["max_samples"],
                random_state=hpo_seed,
                n_jobs=model_thread_count,
                criterion="squared_error",
            )),
        ]))
    if model == "catboost_regressor":
        return CatBoostModel({**hyperparameters, "thread_count": model_thread_count}, hpo_seed)
    if model == "sparse_pairwise_ridge":
        params = dict(hyperparameters)
        transformer = FeatureUnion([
            ("identity", SparseIdentity()),
            ("pairwise", PolynomialCountSketch(
                degree=int(params["degree"]),
                n_components=int(params["n_components"]),
                random_state=hpo_seed,
            )),
        ])
        return PipelineModel(Pipeline([
            ("features", DictVectorizer(sparse=True)),
            ("interactions", transformer),
            ("model", Ridge(alpha=float(params["alpha"]), random_state=hpo_seed)),
        ]))
    raise ValueError(f"unknown model {model!r}")


def _fit_score(
    model_name: str,
    hyperparameters: Mapping[str, object],
    train: baseline.FeatureBundle,
    test: baseline.FeatureBundle,
    hpo_seed: int,
    model_thread_count: int,
    trial_index: int = -1,
) -> tuple[LearnedModel, TrialResult, PredictionResult]:
    started = time.monotonic()
    model = make_model(
        model_name,
        hyperparameters,
        hpo_seed,
        model_thread_count=model_thread_count,
    )
    model.fit(train.rows, train.records, train.targets)
    prediction = model.predict(test.rows, test.records)
    duration = time.monotonic() - started
    metrics = baseline.regression_metrics(test.targets, prediction.predictions)
    return model, TrialResult(trial_index, dict(hyperparameters), metrics, duration), prediction


def _trial_sort_key(trial: TrialResult) -> tuple[float, float, float]:
    rmse = trial.metrics["rmse"]
    rho = trial.metrics["spearman_rho"]
    return (
        float("inf") if rmse is None else float(rmse),
        float("inf") if rho is None else -float(rho),
        trial.duration_seconds,
    )


def _metric_text(value: object) -> str:
    if value is None:
        return "null"
    try:
        return f"{float(value):.6g}"
    except (TypeError, ValueError):
        return str(value)


def tune_hyperparameters(
    config: LearnedExperimentConfig,
    train: baseline.FeatureBundle,
    validation: baseline.FeatureBundle,
) -> dict[str, object]:
    started = time.monotonic()
    _progress(
        f"hpo start split={config.split} model={config.model} "
        f"trials={config.hpo_trials} hpo_jobs={config.hpo_jobs} "
        f"model_threads={config.model_thread_count} "
        f"inner_train={len(train.rows)} inner_validation={len(validation.rows)}",
        config.progress,
    )
    rng = np.random.default_rng(config.hpo_seed)
    params_by_trial = [
        sample_hyperparameters(config.model, rng)
        for _ in range(config.hpo_trials)
    ]
    completed: list[TrialResult] = []

    def run_trial(idx: int, params: Mapping[str, object]) -> TrialResult:
        _, trial, _ = _fit_score(
            config.model,
            params,
            train,
            validation,
            config.hpo_seed + idx,
            config.model_thread_count,
            idx,
        )
        return trial

    def record_trial(trial: TrialResult) -> None:
        completed.append(trial)
        best = min(completed, key=_trial_sort_key)
        mean_duration = sum(item.duration_seconds for item in completed) / len(completed)
        remaining = mean_duration * (config.hpo_trials - len(completed)) / max(config.hpo_jobs, 1)
        elapsed = time.monotonic() - started
        _progress(
            f"hpo {len(completed)}/{config.hpo_trials} split={config.split} "
            f"model={config.model} trial={trial.trial_index} "
            f"trial_rmse={_metric_text(trial.metrics.get('rmse'))} "
            f"trial_rho={_metric_text(trial.metrics.get('spearman_rho'))} "
            f"best_trial={best.trial_index} "
            f"best_rmse={_metric_text(best.metrics.get('rmse'))} "
            f"best_rho={_metric_text(best.metrics.get('spearman_rho'))} "
            f"trial_duration={baseline._format_duration(trial.duration_seconds)} "
            f"elapsed={baseline._format_duration(elapsed)} "
            f"eta={baseline._format_duration(remaining)}",
            config.progress,
        )

    if config.hpo_jobs == 1:
        for idx, params in enumerate(params_by_trial):
            record_trial(run_trial(idx, params))
    else:
        with ThreadPoolExecutor(max_workers=config.hpo_jobs) as executor:
            futures = [
                executor.submit(run_trial, idx, params)
                for idx, params in enumerate(params_by_trial)
            ]
            for future in as_completed(futures):
                record_trial(future.result())
    trials = sorted(completed, key=lambda item: item.trial_index)
    best = min(trials, key=_trial_sort_key)
    return {
        "search_method": "random_search",
        "search_space": HPO_SPACES[config.model],
        "trial_budget": config.hpo_trials,
        "hpo_jobs": config.hpo_jobs,
        "model_thread_count": config.model_thread_count,
        "selection_objective": "minimize_inner_validation_rmse",
        "tie_breakers": ["maximize_spearman_rho", "minimize_fit_predict_runtime"],
        "trials": [
            {
                "trial_index": trial.trial_index,
                "hyperparameters": trial.hyperparameters,
                "metrics": trial.metrics,
                "duration_seconds": trial.duration_seconds,
            }
            for trial in trials
        ],
        "selected_hyperparameters": best.hyperparameters,
        "inner_validation_metrics": best.metrics,
    }


def load_comparator_context(
    path: Path | None,
    split: str,
    model: str,
    *,
    max_rows: int | None = None,
    feature_profile: str = DEFAULT_FEATURE_PROFILE,
    feature_schema_version: int = FEATURE_SCHEMA_VERSION,
) -> dict[str, object]:
    if path is None or not path.exists():
        return {
            "artifact_path": None if path is None else str(path),
            "diagnostic": "comparator_missing",
        }
    data = json.loads(path.read_text())
    artifact_feature_schema = data.get("feature_schema_version") if isinstance(data, dict) else None
    rows = data.get("results", [data]) if isinstance(data, dict) else []
    split_rows = [
        row for row in rows
        if isinstance(row, dict) and row.get("split") == split
    ]
    random_forest = next((row for row in split_rows if row.get("model") == "random_forest"), None)
    matching = random_forest
    if model == "sparse_pairwise_ridge":
        matching = next((row for row in split_rows if row.get("model") == "ridge_hybrid"), random_forest)
    comparator_max_rows = _comparator_max_rows(matching)
    comparator_feature_profile = _comparator_feature_profile(matching)
    comparator_feature_schema = _comparator_feature_schema(matching, artifact_feature_schema)
    comparison_status = "comparable"
    if matching is None:
        comparison_status = "missing"
    elif comparator_feature_schema is not None and comparator_feature_schema != feature_schema_version:
        comparison_status = "feature_schema_mismatch"
    elif comparator_max_rows != max_rows:
        comparison_status = "row_filter_mismatch"
    elif comparator_feature_profile is not None and comparator_feature_profile != feature_profile:
        comparison_status = "feature_profile_mismatch"
    return {
        "artifact_path": str(path),
        "diagnostic": "ok" if matching is not None else "comparator_missing",
        "comparison_status": comparison_status,
        "current_max_rows": max_rows,
        "comparator_max_rows": comparator_max_rows,
        "current_feature_schema_version": feature_schema_version,
        "comparator_feature_schema_version": comparator_feature_schema,
        "current_feature_profile": feature_profile,
        "comparator_feature_profile": comparator_feature_profile,
        "matching_result": matching,
        "random_forest_result": random_forest,
    }


def _comparator_max_rows(row: Mapping[str, object] | None) -> int | None:
    if row is None:
        return None
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        return None
    value = provenance.get("max_rows")
    return int(value) if value is not None else None


def _comparator_feature_profile(row: Mapping[str, object] | None) -> str | None:
    if row is None:
        return None
    provenance = row.get("provenance")
    if isinstance(provenance, dict) and isinstance(provenance.get("feature_profile"), str):
        return str(provenance["feature_profile"])
    value = row.get("feature_profile")
    return str(value) if isinstance(value, str) else None


def _comparator_feature_schema(
    row: Mapping[str, object] | None,
    artifact_feature_schema: object,
) -> int | None:
    if row is None:
        return None
    value = row.get("feature_schema_version")
    if isinstance(value, int):
        return value
    if isinstance(artifact_feature_schema, int):
        return artifact_feature_schema
    return None


def _metric_delta(metrics: Mapping[str, object], comparator: Mapping[str, object] | None) -> dict[str, float] | None:
    if comparator is None:
        return None
    out = {}
    for key in ("mae", "rmse", "spearman_rho"):
        if key in metrics and key in comparator and metrics[key] is not None and comparator[key] is not None:
            out[key] = float(metrics[key]) - float(comparator[key])
    return out


def missing_optional_model_result(config: LearnedExperimentConfig) -> dict[str, object]:
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "split": config.split,
        "model": config.model,
        "status": "skipped",
        "reason": "missing_optional_dependency",
        "message": str(_missing_catboost_error()),
        "provenance": provenance(config),
    }


def run_one(config: LearnedExperimentConfig) -> dict[str, object]:
    if config.model == "catboost_regressor" and CatBoostRegressor is None:
        if config.allow_missing_optional_models:
            return missing_optional_model_result(config)
        raise _missing_catboost_error()

    split, build_lookup = baseline._split_rows(_baseline_config(config))
    if not split.train or not split.test:
        return _insufficient_result(config, "empty_outer_split")
    inner = inner_validation_split(config, split.train, build_lookup)
    if inner is None:
        return _insufficient_result(config, "insufficient_inner_groups")

    inner_train = baseline._feature_bundle(inner.train, build_lookup, _baseline_config(config))
    inner_validation = baseline._feature_bundle(inner.test, build_lookup, _baseline_config(config))
    hpo = tune_hyperparameters(config, inner_train, inner_validation)

    outer_train = baseline._feature_bundle(split.train, build_lookup, _baseline_config(config))
    outer_test = baseline._feature_bundle(split.test, build_lookup, _baseline_config(config))
    default_model, default_trial, default_prediction = _fit_score(
        config.model,
        DEFAULT_HYPERPARAMETERS[config.model],
        outer_train,
        outer_test,
        config.hpo_seed,
        config.model_thread_count,
    )
    final_model, final_trial, final_prediction = _fit_score(
        config.model,
        hpo["selected_hyperparameters"],
        outer_train,
        outer_test,
        config.hpo_seed,
        config.model_thread_count,
    )
    comparator = load_comparator_context(
        config.comparator_json_path,
        config.split,
        config.model,
        max_rows=config.max_rows,
        feature_profile=config.feature_profile,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )
    matching = comparator.get("matching_result") if isinstance(comparator.get("matching_result"), dict) else None
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "provenance": provenance(config),
        "split": config.split,
        "model": config.model,
        "status": "completed",
        "target_variable": TARGET_VARIABLE,
        "feature_families": feature_families(outer_train.records, config.feature_profile),
        "n_train": len(outer_train.rows),
        "n_inner_train": len(inner_train.rows),
        "n_inner_validation": len(inner_validation.rows),
        "n_test": len(outer_test.rows),
        **final_trial.metrics,
        "diagnostics": final_prediction.diagnostics,
        "stratified": baseline.stratified_metrics(outer_test, final_prediction.predictions),
        "honest_eval_top_k": baseline.honest_eval_top_k_for_model(final_model, build_lookup, _baseline_config(config)),
        "hpo": hpo,
        "default_result": {
            "hyperparameters": DEFAULT_HYPERPARAMETERS[config.model],
            "metrics": default_trial.metrics,
            "duration_seconds": default_trial.duration_seconds,
            "diagnostics": default_prediction.diagnostics,
        },
        "default_vs_tuned_delta": {
            key: final_trial.metrics[key] - default_trial.metrics[key]
            for key in ("mae", "rmse")
            if final_trial.metrics[key] is not None and default_trial.metrics[key] is not None
        },
        "comparator_context": comparator,
        "comparator_delta": _metric_delta(final_trial.metrics, matching),
        "timing": {"fit_predict_seconds": final_trial.duration_seconds},
        "leakage_checklist": leakage_checklist(),
    }


def _insufficient_result(config: LearnedExperimentConfig, reason: str) -> dict[str, object]:
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "provenance": provenance(config),
        "split": config.split,
        "model": config.model,
        "status": reason,
        "target_variable": TARGET_VARIABLE,
        "leakage_checklist": leakage_checklist(),
    }


def feature_families(
    records: Sequence[Mapping[str, FeatureValue]],
    feature_profile: str = DEFAULT_FEATURE_PROFILE,
) -> dict[str, object]:
    keys = sorted({key for record in records for key in record})
    return {
        "feature_profile": feature_profile,
        "column_count": len(keys),
        "prefixes": sorted({key.split("_", 2)[0] for key in keys}),
        "columns": keys,
        "excluded": [
            "build_key",
            "target_derived_build_mean",
            "target_derived_opponent_mean",
            "twfe_residual",
            "honest_eval_target",
        ],
    }


def leakage_checklist() -> dict[str, bool]:
    return {
        "outer_test_targets_excluded_from_fit": True,
        "honest_eval_targets_excluded_from_fit": True,
        "feature_selection_inside_inner_fold": True,
        "build_key_excluded_from_feature_vectors": True,
    }


def provenance(config: LearnedExperimentConfig) -> dict[str, object]:
    return {
        "game_dir": str(config.game_dir),
        "comparator_json_path": None if config.comparator_json_path is None else str(config.comparator_json_path),
        "target_variable": TARGET_VARIABLE,
        "split_seed": config.split_seed,
        "hpo_seed": config.hpo_seed,
        "hpo_trials": config.hpo_trials,
        "hpo_jobs": config.hpo_jobs,
        "model_thread_count": config.model_thread_count,
        "holdout_fraction": config.holdout_fraction,
        "train_fraction": config.train_fraction,
        "top_k_values": list(config.top_k_values),
        "max_rows": config.max_rows,
        "feature_profile": config.feature_profile,
        "batch_job_id": config.batch_job_id,
        "batch_name": config.batch_name,
        "batch_fleet_name": config.batch_fleet_name,
        "code_version": _code_version(),
    }


def batch_job_context(config: LearnedExperimentConfig) -> dict[str, object] | None:
    if config.batch_job_id is None:
        return None
    return {
        "job_id": config.batch_job_id,
        "batch_name": config.batch_name,
        "fleet_name": config.batch_fleet_name,
        "split": config.split,
        "model": config.model,
    }


def _code_version() -> str:
    source_version_file = Path(".phase7_source_version")
    if source_version_file.exists():
        try:
            payload = json.loads(source_version_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        version = payload.get("code_version")
        if isinstance(version, str) and version:
            return version
    git_dir = Path(".git")
    head = git_dir / "HEAD"
    if not head.exists():
        return "unknown"
    raw = head.read_text().strip()
    if raw.startswith("ref: "):
        ref = git_dir / raw.removeprefix("ref: ")
        version = ref.read_text().strip() if ref.exists() else "unknown"
    else:
        version = raw or "unknown"
    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return version
    if status.returncode == 0 and status.stdout.strip():
        return f"{version}+dirty"
    return version


def _experiment_payload(
    config: LearnedExperimentConfig,
    results: Sequence[Mapping[str, object]],
    skipped: Sequence[Mapping[str, object]],
    *,
    status: str,
    started: float,
) -> dict[str, object]:
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "db_path": str(config.db_path),
        "status": status,
        "elapsed_seconds": time.monotonic() - started,
        "provenance": provenance(config),
        "model_families": list(MODEL_CHOICES if config.model == "all" else (config.model,)),
        "skipped_models": list(skipped),
        "result_count": len(results),
        "results": list(results),
    }


def _write_json_payload(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n")


def run_experiment(
    config: LearnedExperimentConfig,
    *,
    checkpoint_path: Path | None = None,
) -> dict[str, object]:
    configs = build_experiment_configs(config)
    results = []
    skipped = []
    started = time.monotonic()
    durations: list[float] = []
    _progress(
        f"starting {len(configs)} run(s): split={config.split} model={config.model} "
        f"db={config.db_path} feature_schema={FEATURE_SCHEMA_VERSION}",
        config.progress,
    )
    for idx, item in enumerate(configs, start=1):
        run_started = time.monotonic()
        _progress(f"{idx}/{len(configs)} start split={item.split} model={item.model}", config.progress)
        result = run_one(item)
        results.append(result)
        if result.get("status") == "skipped":
            skipped.append({"split": item.split, "model": item.model, "reason": result.get("reason")})
        if checkpoint_path is not None:
            payload = _experiment_payload(
                config,
                results,
                skipped,
                status="running",
                started=started,
            )
            _write_json_payload(checkpoint_path, payload)
            _progress(f"checkpoint wrote output={checkpoint_path} results={len(results)}/{len(configs)}", config.progress)
        duration = time.monotonic() - run_started
        durations.append(duration)
        mean_duration = sum(durations) / len(durations)
        remaining = mean_duration * (len(configs) - idx)
        elapsed = time.monotonic() - started
        _progress(
            f"{idx}/{len(configs)} done split={item.split} model={item.model} "
            f"duration={baseline._format_duration(duration)} elapsed={baseline._format_duration(elapsed)} "
            f"eta={baseline._format_duration(remaining)}",
            config.progress,
        )
    return _experiment_payload(config, results, skipped, status="completed", started=started)


def _progress(message: str, enabled: bool) -> None:
    if enabled:
        print(f"[phase7-learned] {message}", file=sys.stderr, flush=True)


def main() -> None:
    args = build_parser().parse_args()
    config = LearnedExperimentConfig(
        db_path=args.db_path,
        game_dir=args.game_dir,
        comparator_json_path=args.comparator_json,
        split=args.split,
        model=args.model,
        holdout_fraction=args.holdout_fraction,
        train_fraction=args.train_fraction,
        split_seed=args.split_seed,
        hpo_seed=args.hpo_seed,
        hpo_trials=args.hpo_trials,
        hpo_jobs=args.hpo_jobs,
        model_thread_count=args.model_thread_count,
        max_rows=args.max_rows,
        top_k_values=parse_top_k_values(args.top_k),
        progress=not args.no_progress,
        allow_missing_optional_models=args.allow_missing_optional_models,
        feature_profile=args.feature_profile,
        batch_job_id=args.batch_job_id,
        batch_name=args.batch_name,
        batch_fleet_name=args.batch_fleet_name,
    )
    result = run_experiment(config, checkpoint_path=args.output)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output is not None:
        _write_json_payload(args.output, result)
        _progress(f"wrote output={args.output}", not args.no_progress)
    else:
        print(payload)


if __name__ == "__main__":
    main()
