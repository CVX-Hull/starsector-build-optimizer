#!/usr/bin/env python
"""Run learned Phase 7 matchup surrogate experiments."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from hashlib import sha256
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
from starsector_optimizer.phase7_eval import EvalMetricsConfig
from starsector_optimizer.phase7_matchup_data import (
    CANONICAL_SPLIT_SEED_BANK,
    CANONICAL_SPLIT_SEED_BANK_LABEL,
    DEFAULT_DEPENDENCY_EXTRA,
    DEFAULT_FINAL_REFIT_POLICY,
    DEFAULT_INNER_CV_FOLDS,
    DEFAULT_PROMOTION_METRIC,
    EXPERIMENT_SCHEMA_VERSION,
    RESERVED_CONFIRMATORY_SEED,
    ComponentVocabularyError,
    SplitIds,
    TrainingMatchupRow,
    component_fingerprint_json,
    forward_time_order_key,
    grouped_kfold,
    held_out_component_vocabulary_split,
    reject_burned_split_seed,
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


DEFAULT_HOLDOUT_FRACTION = baseline.DEFAULT_HOLDOUT_FRACTION
DEFAULT_TRAIN_FRACTION = baseline.DEFAULT_TRAIN_FRACTION
DEFAULT_SPLIT_SEED = baseline.DEFAULT_RANDOM_SEED
DEFAULT_HPO_SEED = 23
DEFAULT_HPO_TRIALS = 24
DEFAULT_HPO_JOBS = 4
DEFAULT_MODEL_THREAD_COUNT = 4
SPLIT_CHOICES = baseline.SPLIT_CHOICES
MODEL_CHOICES = ("random_forest_tuned", "catboost_regressor", "sparse_pairwise_ridge")
# Matched comparator families exist only where a natural analog does; CatBoost
# has none and its headline is delta_vs_best_comparator (spec 31 / review C3).
MATCHED_COMPARATOR_FAMILY: dict[str, str | None] = {
    "random_forest_tuned": "random_forest",
    "sparse_pairwise_ridge": "ridge_hybrid",
    "catboost_regressor": None,
}
TARGET_VARIABLE = "training_matchups.target"
HONEST_EVAL_DIAGNOSTIC_TARGET = "honest_eval_top_k"
ALL_AVAILABLE_CORES = -1
HONEST_EVAL_USAGE_CHOICES = ("diagnostic_only", "exploratory_selection", "final_claim")
DEFAULT_HONEST_EVAL_USAGE = "diagnostic_only"
DEFAULT_PRIMARY_TOP_K = 1
DEFAULT_PROMOTION_THRESHOLD = 0.0
DEFAULT_CLAIM_LABEL = "exploratory"
DEFAULT_CANDIDATE_UNIVERSE = "source_db_builds"
DEFAULT_DEPLOYMENT_ARTIFACT = "none"

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
    honest_eval_usage: str = DEFAULT_HONEST_EVAL_USAGE
    fresh_honest_eval_ledger_id: str | None = None
    primary_top_k: int = DEFAULT_PRIMARY_TOP_K
    promotion_metric: str = DEFAULT_PROMOTION_METRIC
    promotion_threshold: float = DEFAULT_PROMOTION_THRESHOLD
    claim_label: str = DEFAULT_CLAIM_LABEL
    final_refit_policy: str = DEFAULT_FINAL_REFIT_POLICY
    candidate_universe: str = DEFAULT_CANDIDATE_UNIVERSE
    deployment_artifact: str = DEFAULT_DEPLOYMENT_ARTIFACT
    inner_cv_folds: int = DEFAULT_INNER_CV_FOLDS
    noise_floor_override: float | None = None
    bootstrap_resamples: int = EvalMetricsConfig().bootstrap_resamples
    component_vocab_max_overshoot: float = baseline.DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT
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
    parser.add_argument("--split", choices=(*SPLIT_CHOICES, "all"), default="build")
    parser.add_argument("--model", choices=(*MODEL_CHOICES, "all"), default="random_forest_tuned")
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_TRAIN_FRACTION)
    parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    parser.add_argument("--hpo-seed", type=int, default=DEFAULT_HPO_SEED)
    parser.add_argument("--hpo-trials", type=int, default=DEFAULT_HPO_TRIALS)
    parser.add_argument("--hpo-jobs", type=int, default=DEFAULT_HPO_JOBS)
    parser.add_argument("--model-thread-count", type=int, default=DEFAULT_MODEL_THREAD_COUNT)
    parser.add_argument("--inner-cv-folds", type=int, default=DEFAULT_INNER_CV_FOLDS)
    parser.add_argument("--noise-floor-override", type=float, default=None)
    parser.add_argument(
        "--bootstrap-resamples", type=int, default=EvalMetricsConfig().bootstrap_resamples
    )
    parser.add_argument(
        "--component-vocab-max-overshoot",
        type=float,
        default=baseline.DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT,
    )
    parser.add_argument("--top-k", default=",".join(str(item) for item in baseline.DEFAULT_TOP_K_VALUES))
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--feature-profile", choices=FEATURE_PROFILES, default=DEFAULT_FEATURE_PROFILE)
    parser.add_argument("--honest-eval-usage", choices=HONEST_EVAL_USAGE_CHOICES, default=DEFAULT_HONEST_EVAL_USAGE)
    parser.add_argument("--fresh-honest-eval-ledger-id", default=None)
    parser.add_argument("--primary-top-k", type=int, default=DEFAULT_PRIMARY_TOP_K)
    parser.add_argument("--promotion-metric", default=DEFAULT_PROMOTION_METRIC)
    parser.add_argument("--promotion-threshold", type=float, default=DEFAULT_PROMOTION_THRESHOLD)
    parser.add_argument("--claim-label", default=DEFAULT_CLAIM_LABEL)
    parser.add_argument("--final-refit-policy", default=DEFAULT_FINAL_REFIT_POLICY)
    parser.add_argument("--candidate-universe", default=DEFAULT_CANDIDATE_UNIVERSE)
    parser.add_argument("--deployment-artifact", default=DEFAULT_DEPLOYMENT_ARTIFACT)
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
        replace(config, split=split, model=model)
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
        eval_metrics=EvalMetricsConfig(
            noise_floor_override=config.noise_floor_override,
            bootstrap_resamples=config.bootstrap_resamples,
        ),
        component_vocab_max_overshoot=config.component_vocab_max_overshoot,
    )


def _inner_fold_groups(
    config: LearnedExperimentConfig,
    rows: Sequence[TrainingMatchupRow],
) -> list[str]:
    if config.split == "build":
        return [row.build_key for row in rows]
    if config.split == "opponent":
        return [row.opponent_variant_id for row in rows]
    if config.split == "opponent-hull":
        opponent_hull_by_variant, _ = baseline.opponent_group_maps(config.game_dir, rows)
        return [opponent_hull_by_variant[row.opponent_variant_id] for row in rows]
    if config.split == "opponent-family":
        _, opponent_family_by_variant = baseline.opponent_group_maps(config.game_dir, rows)
        return [opponent_family_by_variant[row.opponent_variant_id] for row in rows]
    if config.split == "seed-cell":
        return [f"{row.campaign}:{row.seed}" for row in rows]
    raise ValueError(f"split {config.split!r} does not use grouped inner folds")


def _rolling_origin_folds(
    rows: Sequence[TrainingMatchupRow],
    n_folds: int,
) -> tuple[SplitIds, ...]:
    """Rolling-origin inner folds for forward-time within the outer-train prefix."""
    ordered = sorted(rows, key=forward_time_order_key)
    blocks = np.array_split(np.arange(len(ordered)), n_folds + 1)
    if any(len(block) == 0 for block in blocks):
        return ()
    folds = []
    for fold_idx in range(n_folds):
        train_end = blocks[fold_idx][-1] + 1
        val_indices = blocks[fold_idx + 1]
        folds.append(
            SplitIds(
                train=tuple(ordered[:train_end]),
                test=tuple(ordered[val_indices[0]:val_indices[-1] + 1]),
            )
        )
    return tuple(folds)


def inner_cv_splits(
    config: LearnedExperimentConfig,
    rows: Sequence[TrainingMatchupRow],
    build_lookup: Mapping[str, object],
) -> tuple[SplitIds, ...]:
    """Inner train/validation folds from outer-training rows only (spec 31).

    Returns ``()`` when the outer-train rows cannot support the declared fold
    count; callers emit ``insufficient_inner_groups``.
    """
    if config.split == "forward-time":
        return _rolling_origin_folds(rows, config.inner_cv_folds)
    if config.split == "component-vocab":
        folds = []
        for fold_idx in range(config.inner_cv_folds):
            try:
                vocab_split = held_out_component_vocabulary_split(
                    rows,
                    build_lookup,
                    config.holdout_fraction,
                    config.component_vocab_max_overshoot,
                    config.hpo_seed + fold_idx,
                )
            except ComponentVocabularyError as exc:
                _progress(
                    f"inner vocabulary draw {fold_idx} degenerate: {exc}",
                    config.progress,
                )
                return ()
            folds.append(vocab_split.split)
        return tuple(folds)
    groups = _inner_fold_groups(config, rows)
    return grouped_kfold(rows, groups, config.inner_cv_folds, config.hpo_seed)


def inner_validation_metadata(config: LearnedExperimentConfig) -> dict[str, object]:
    metadata = dict(baseline.split_metadata(_baseline_config(config)))
    construction = {
        "forward-time": "rolling_origin",
        "component-vocab": "vocabulary_draws",
    }.get(config.split, "grouped_kfold")
    metadata.update(
        {
            "split_role": "inner_validation",
            "source_rows": "outer_training_rows_only",
            "seed": config.hpo_seed,
            "holdout_fraction": config.holdout_fraction,
            "inner_cv_folds": config.inner_cv_folds,
            "fold_construction": construction,
            "random_row_fallback": False,
            "fallback_behavior": "insufficient_inner_groups",
        }
    )
    if config.split == "forward-time":
        metadata["temporal_semantics"] = "blocked_prefix_suffix_within_outer_training_prefix"
    return metadata


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


def _fold_mean_metrics(
    fold_metrics: Sequence[Mapping[str, float | None]],
) -> dict[str, float | None]:
    """Mean of per-fold metrics; SD recorded for RMSE. None-fold metrics drop."""
    out: dict[str, float | None] = {}
    for key in ("mae", "rmse", "spearman_rho"):
        values = [m[key] for m in fold_metrics if m.get(key) is not None]
        out[key] = float(np.mean(values)) if values else None
    rmse_values = [m["rmse"] for m in fold_metrics if m.get("rmse") is not None]
    out["rmse_fold_sd"] = float(np.std(rmse_values)) if len(rmse_values) > 1 else None
    out["fold_count"] = len(fold_metrics)
    return out


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
    folds: Sequence[tuple[baseline.FeatureBundle, baseline.FeatureBundle]],
) -> dict[str, object]:
    started = time.monotonic()
    _progress(
        f"hpo start split={config.split} model={config.model} "
        f"trials={config.hpo_trials} hpo_jobs={config.hpo_jobs} "
        f"model_threads={config.model_thread_count} "
        f"inner_folds={len(folds)}",
        config.progress,
    )
    rng = np.random.default_rng(config.hpo_seed)
    params_by_trial = [
        sample_hyperparameters(config.model, rng)
        for _ in range(config.hpo_trials)
    ]
    completed: list[TrialResult] = []

    def run_trial(idx: int, params: Mapping[str, object]) -> TrialResult:
        # Aligned seeds (review M1): every trial fit uses the same model seed
        # as the final refit, so the selected config's inner scores were
        # produced under its shipping seed.
        fold_metrics: list[dict[str, float | None]] = []
        duration = 0.0
        for fold_train, fold_validation in folds:
            _, trial, _ = _fit_score(
                config.model,
                params,
                fold_train,
                fold_validation,
                config.hpo_seed,
                config.model_thread_count,
                idx,
            )
            fold_metrics.append(trial.metrics)
            duration += trial.duration_seconds
        return TrialResult(idx, dict(params), _fold_mean_metrics(fold_metrics), duration)

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
        "inner_cv_folds": len(folds),
        "model_seed_alignment": "all_trials_and_final_refit_use_hpo_seed",
        "selection_objective": "minimize_mean_inner_validation_rmse",
        "tie_breakers": ["maximize_mean_spearman_rho", "minimize_fit_predict_runtime"],
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


def run_inline_comparators(
    config: LearnedExperimentConfig,
    train: baseline.FeatureBundle,
    test: baseline.FeatureBundle,
) -> dict[str, dict[str, object]]:
    """Fit the six comparator-gate models on this job's exact outer split.

    Comparability by construction (review C3): same rows, same seed, same
    feature profile. Comparators run at fixed defaults — deltas are floor
    comparisons, not tuned-family comparisons.
    """
    bl_config = _baseline_config(config)
    results: dict[str, dict[str, object]] = {}
    for name in baseline.MODEL_CHOICES:
        started = time.monotonic()
        model = baseline.make_model(name, bl_config)
        model.fit(train.rows, train.records, train.targets)
        predictions = model.predict(test.rows, test.records).predictions
        suite = baseline.evaluation_metric_suite(
            train, test, predictions, bl_config,
            config.primary_top_k, include_bootstrap=False,
        )
        results[name] = {
            **baseline.regression_metrics(test.targets, predictions),
            "rank_metrics": suite["rank_metrics"],
            "skill_scores": suite["skill_scores"],
            "duration_seconds": time.monotonic() - started,
        }
    return results


def comparator_deltas(
    learned_metrics: Mapping[str, object],
    learned_rank_metrics: Mapping[str, object],
    model: str,
    comparators: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Deltas vs the split's best comparator and the matched family (if any)."""
    finite = {
        name: result
        for name, result in comparators.items()
        if isinstance(result.get("rmse"), float) and math.isfinite(result["rmse"])
    }
    best_name = min(finite, key=lambda name: float(finite[name]["rmse"])) if finite else None

    def _delta(comparator: Mapping[str, object]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key in ("mae", "rmse", "spearman_rho"):
            learned_value = learned_metrics.get(key)
            comparator_value = comparator.get(key)
            if learned_value is not None and comparator_value is not None:
                out[key] = float(learned_value) - float(comparator_value)
        learned_mean = learned_rank_metrics["per_opponent"]["mean_spearman"]
        comparator_mean = comparator["rank_metrics"]["per_opponent"]["mean_spearman"]
        if learned_mean is not None and comparator_mean is not None:
            out["mean_per_opponent_spearman"] = float(learned_mean) - float(comparator_mean)
        return out

    matched_name = MATCHED_COMPARATOR_FAMILY[model]
    return {
        "best_comparator": best_name,
        "delta_vs_best_comparator": _delta(comparators[best_name]) if best_name else None,
        "matched_family": matched_name,
        "delta_vs_matched_family": (
            _delta(comparators[matched_name])
            if matched_name is not None and matched_name in comparators
            else None
        ),
    }


def outer_split_lineage(config: LearnedExperimentConfig) -> dict[str, object]:
    """C4 reuse ledger, parallel to honest_eval_usage (spec 31)."""
    in_bank = config.split_seed in CANONICAL_SPLIT_SEED_BANK
    return {
        "split_seed": config.split_seed,
        "seed_bank_label": CANONICAL_SPLIT_SEED_BANK_LABEL if in_bank else "ad-hoc",
        "confirmatory_reserved_seed": RESERVED_CONFIRMATORY_SEED,
        # forward-time's deterministic partition predates the seed bank and
        # absorbed the burned evidence waves; reports must caveat it.
        "reused_partition": config.split == "forward-time",
    }


def _validate_claim_config(config: LearnedExperimentConfig) -> None:
    if config.honest_eval_usage == "final_claim" and not config.fresh_honest_eval_ledger_id:
        raise ValueError("honest_eval_usage=final_claim requires --fresh-honest-eval-ledger-id")
    if config.primary_top_k <= 0:
        raise ValueError("primary_top_k must be positive")
    if config.inner_cv_folds < 2:
        raise ValueError("inner_cv_folds must be >= 2")
    reject_burned_split_seed(config.split_seed)


def _honest_eval_lineage(db_path: Path) -> dict[str, object]:
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "select distinct source_path from honest_eval_matchups order by source_path"
            ).fetchall()
    except sqlite3.Error as exc:
        return {
            "status": "unavailable",
            "reason": exc.__class__.__name__,
            "source_paths": [],
            "ledger_id": None,
            "run_lineage": [],
        }
    source_paths = [str(row[0]) for row in rows if row and row[0]]
    return {
        "status": "available" if source_paths else "not_applicable",
        "source_paths": source_paths,
        "ledger_id": ";".join(source_paths) if source_paths else None,
        "run_lineage": source_paths,
    }


def claim_boundary(
    config: LearnedExperimentConfig,
    honest_eval_lineage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    _validate_claim_config(config)
    return {
        "target_variable": TARGET_VARIABLE,
        "honest_eval_diagnostic_target": HONEST_EVAL_DIAGNOSTIC_TARGET,
        "primary_split": config.split,
        "primary_top_k": config.primary_top_k,
        "promotion_metric": config.promotion_metric,
        "promotion_threshold": config.promotion_threshold,
        "higher_is_better": True,
        "claim_label": config.claim_label,
        "honest_eval_usage": config.honest_eval_usage,
        "fresh_honest_eval_ledger_id": config.fresh_honest_eval_ledger_id,
        "honest_eval_ledger_id": None if honest_eval_lineage is None else honest_eval_lineage.get("ledger_id"),
        "honest_eval_run_lineage": [] if honest_eval_lineage is None else honest_eval_lineage.get("run_lineage", []),
    }


def model_family_policy(config: LearnedExperimentConfig) -> dict[str, object]:
    return {
        "policy_type": "fixed_matrix",
        "candidate_model_families": list(MODEL_CHOICES),
        "selected_model_family": config.model,
        "selection_scope": "predeclared_fixed_matrix",
    }


def _feature_family_for_key(key: str) -> str:
    normalized = key.removeprefix("build_").removeprefix("opponent_")
    if "hullmod" in normalized:
        return "hullmod"
    if normalized.startswith("slot_") or "_slot_" in normalized or "geometry" in normalized or "arc_" in normalized:
        return "slot_geometry"
    if any(token in normalized for token in ("weapon", "dps", "damage", "range", "missile", "pd_", "beam")):
        return "weapon_pressure"
    if any(token in normalized for token in ("flux", "vent", "capacitor", "dissipation")):
        return "flux"
    if any(token in normalized for token in ("armor", "shield", "hull_points", "hitpoints", "ehp")):
        return "defense"
    if any(token in normalized for token in ("speed", "maneuver", "turn", "acceleration")):
        return "mobility"
    if key.startswith("opponent_"):
        return "opponent_aggregate"
    if key.startswith("build_"):
        return "hull"
    if key in {"campaign", "seed", "source_path", "trial_number", "row_kind", "source_kind"}:
        return "provenance_context"
    return "hull"


def _feature_template_for_key(key: str) -> str:
    if "__" in key:
        return "sparse_indicator"
    if "_minus_" in key or "_vs_" in key or "interaction" in key:
        return "interaction"
    if key.endswith("_id") or key.endswith("_type") or key.endswith("_size") or key.endswith("_designation"):
        return "categorical_residual"
    if any(token in key for token in ("mean", "total", "count", "sum", "min", "max", "std")):
        return "aggregate"
    if "norm" in key or "ratio" in key or "fraction" in key:
        return "normalized_ratio"
    return "raw_descriptor"


def feature_family_registry(records: Sequence[Mapping[str, FeatureValue]]) -> dict[str, dict[str, object]]:
    keys = sorted({key for record in records for key in record})
    excluded = set(feature_families((), DEFAULT_FEATURE_PROFILE)["excluded"])
    return {
        key: {
            "family": _feature_family_for_key(key),
            "template": _feature_template_for_key(key),
            "parents": (
                ["weapon_pressure", "opponent_aggregate"]
                if "_vs_" in key or "_minus_" in key or "interaction" in key
                else []
            ),
            "leakage_risk": _feature_leakage_risk(key, excluded),
        }
        for key in keys
    }


def _feature_leakage_risk(key: str, excluded: set[str]) -> str:
    if key in excluded or key.startswith("target_derived_") or key.startswith("twfe_"):
        return "high"
    template = _feature_template_for_key(key)
    if template == "sparse_indicator":
        return "medium"
    if template == "categorical_residual" and (
        key.endswith("_id")
        or key.endswith("variant_id")
        or "weapon_id" in key
        or "hullmod" in key
        or "opponent_" in key
    ):
        return "medium"
    return "low"


def _registry_digest(registry: Mapping[str, object]) -> str:
    raw = json.dumps(registry, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def feature_selection_protocol(
    records: Sequence[Mapping[str, FeatureValue]],
    feature_profile: str,
) -> dict[str, object]:
    registry = feature_family_registry(records)
    selected = sorted({item["family"] for item in registry.values() if isinstance(item.get("family"), str)})
    return {
        "policy_type": "fixed_profile_no_selector",
        "feature_profile": feature_profile,
        "feature_family_registry": registry,
        "feature_family_registry_sha256": _registry_digest(registry),
        "selected_feature_families": selected,
        "selected_feature_count": len(registry),
        "selector_family": "none",
        "selector_hyperparameters": {},
        "stability": "not_applicable",
        "heredity_policy": "not_applicable",
        "selection_scope": "no_feature_selection",
    }


def deployment_policy(config: LearnedExperimentConfig) -> dict[str, object]:
    return {
        "final_refit_policy": config.final_refit_policy,
        "candidate_universe": config.candidate_universe,
        "deployment_artifact": config.deployment_artifact,
    }


def _overlap_count(train_values: set[str], test_values: set[str]) -> int:
    return len(train_values & test_values)


def hierarchy_scorecard(
    config: LearnedExperimentConfig,
    split: SplitIds | None,
    build_lookup: Mapping[str, object],
    split_extras: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if split is None:
        return {
            "split_level": config.split,
            "status": "not_applicable",
            "reason": "split_unavailable",
            "group_key_function": "not_applicable",
            "group_key_fields": [],
            "claim_supported": "not_applicable",
            "forbidden_cross_split_keys": [],
            "overlap_counts": {},
            "component_key_definition": "not_applicable",
            "component_overlap_diagnostics": {
                "k1": {"status": "not_applicable", "reason": "split_unavailable"},
                "k2": {"status": "not_applicable", "reason": "split_unavailable"},
                "k3": {"status": "not_applicable", "reason": "split_unavailable"},
            },
        }
    split_extras = split_extras or {}
    train = tuple(row for row in split.train if isinstance(row, TrainingMatchupRow))
    test = tuple(row for row in split.test if isinstance(row, TrainingMatchupRow))
    opponent_hull_by_variant, opponent_family_by_variant = baseline.opponent_group_maps(
        config.game_dir, train + test
    )
    held_out_components = split_extras.get("held_out_components")
    overlap_counts = baseline.split_overlap_counts(
        train,
        test,
        build_lookup,
        opponent_hull_by_variant,
        opponent_family_by_variant,
        held_out_components=held_out_components,
    )
    component_diagnostics: object = (
        baseline.component_overlap_diagnostics(train, test, build_lookup)
        if config.split == "component-vocab"
        else {
            "status": "not_applicable",
            "reason": "not_component_split",
        }
    )
    return {
        "split_level": config.split,
        "group_key_function": {
            "build": "held_out_build_split",
            "opponent": "held_out_opponent_split",
            "opponent-hull": "held_out_opponent_hull_split",
            "opponent-family": "held_out_opponent_family_split",
            "component-vocab": "held_out_component_vocabulary_split",
            "seed-cell": "held_out_seed_cell_split",
            "forward-time": "forward_time_split",
        }.get(config.split, "unknown"),
        "group_key_fields": {
            "build": ["build_key"],
            "opponent": ["opponent_variant_id"],
            "opponent-hull": ["opponent_hull_id"],
            "opponent-family": [
                "opponent_hull_size",
                "opponent_hull_designation",
                "opponent_hull_tech_manufacturer",
            ],
            "component-vocab": ["weapon_assignments", "hullmods"],
            "seed-cell": ["campaign", "seed"],
            "forward-time": ["source_order"],
        }.get(config.split, []),
        "claim_supported": {
            "build": "held_out_build_transfer",
            "opponent": "held_out_opponent_transfer",
            "opponent-hull": "held_out_opponent_hull_transfer",
            "opponent-family": "held_out_opponent_family_transfer",
            "component-vocab": "held_out_component_vocabulary_transfer",
            "seed-cell": "held_out_campaign_seed_cell_transfer",
            "forward-time": "forward_time_transfer",
        }.get(config.split, "diagnostic"),
        "forbidden_cross_split_keys": {
            "build": ["build_key"],
            "opponent": ["opponent_variant_id"],
            "opponent-hull": ["opponent_hull_id"],
            "opponent-family": ["opponent_hull_size", "opponent_hull_designation", "opponent_hull_tech_manufacturer"],
            "component-vocab": ["component_vocabulary"],
            "seed-cell": ["campaign", "seed"],
            "forward-time": ["future_rows"],
        }.get(config.split, []),
        "overlap_counts": overlap_counts,
        "component_key_definition": (
            baseline.COMPONENT_VOCAB_KEY_DEFINITION
            if config.split == "component-vocab"
            else "not_applicable"
        ),
        "held_out_components": held_out_components,
        "realized_test_fraction": split_extras.get("realized_test_fraction"),
        "component_overlap_diagnostics": component_diagnostics,
    }


def leakage_diagnostics(hierarchy: Mapping[str, object] | None = None) -> dict[str, object]:
    overlaps = hierarchy.get("overlap_counts") if isinstance(hierarchy, Mapping) else None
    split_level = hierarchy.get("split_level") if isinstance(hierarchy, Mapping) else None
    forbidden_count_key = {
        "build": "exact_build",
        "opponent": "exact_opponent",
        "opponent-hull": "opponent_hull",
        "opponent-family": "opponent_family",
        "component-vocab": "component_vocabulary",
        "seed-cell": "campaign_cell",
        "forward-time": None,
    }.get(split_level)
    if split_level not in SPLIT_CHOICES:
        forbidden_status = {"status": "not_applicable", "reason": "split_unavailable"}
    elif forbidden_count_key is None:
        forbidden_status = {"status": "not_applicable", "reason": "no_forbidden_overlap_key"}
    else:
        forbidden_overlap = int(overlaps.get(forbidden_count_key, 0)) if isinstance(overlaps, Mapping) else 0
        forbidden_status = {
            "status": "pass" if forbidden_overlap == 0 else "fail",
            "value": forbidden_overlap,
        }
    return {
        "forbidden_key_overlap": forbidden_status,
        "adversarial_validation_auc": {"status": "not_applicable", "reason": "diagnostic_not_implemented"},
        "rare_combination_overlap": {"status": "not_applicable", "reason": "diagnostic_not_implemented"},
        "nearest_neighbor_overlap": {"status": "not_applicable", "reason": "diagnostic_not_implemented"},
        "sparse_id_ablation_delta": {"status": "not_applicable", "reason": "diagnostic_not_implemented"},
    }


def missing_optional_model_result(config: LearnedExperimentConfig) -> dict[str, object]:
    _validate_claim_config(config)
    honest_eval_lineage = _honest_eval_lineage(config.db_path)
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "claim_boundary": claim_boundary(config, honest_eval_lineage),
        "model_family_policy": model_family_policy(config),
        "feature_selection_protocol": feature_selection_protocol((), config.feature_profile),
        "deployment_policy": deployment_policy(config),
        "hierarchy_scorecard": hierarchy_scorecard(config, None, {}),
        "inner_validation_metadata": inner_validation_metadata(config),
        "leakage_diagnostics": leakage_diagnostics(),
        "outer_split_lineage": outer_split_lineage(config),
        "split": config.split,
        "model": config.model,
        "status": "skipped",
        "reason": "missing_optional_dependency",
        "message": str(_missing_catboost_error()),
        "provenance": provenance(config),
    }


def construct_splits(
    config: LearnedExperimentConfig,
) -> tuple[str, None] | tuple[None, tuple]:
    """Build the outer split and inner folds, or name why the cell cannot.

    Returns ``(insufficiency_status, None)`` for a structurally infeasible
    cell, else ``(None, (split, build_lookup, split_extras, inner_folds))``.
    Single owner of the insufficiency decision for both live runs and the
    launch-time feasibility preflight, so the two cannot drift.
    """
    try:
        split, build_lookup, split_extras = baseline._split_rows(_baseline_config(config))
    except ComponentVocabularyError:
        # A deterministic bad vocabulary draw must produce a structured
        # insufficiency artifact, not a crash that burns batch retries.
        # Config errors (burned seeds, invalid fractions) raise through.
        return "degenerate_component_vocab_split", None
    if not split.train or not split.test:
        return "empty_outer_split", None
    inner_folds = inner_cv_splits(config, split.train, build_lookup)
    if not inner_folds:
        return "insufficient_inner_groups", None
    return None, (split, build_lookup, split_extras, inner_folds)


def split_feasibility_report(
    configs: Sequence[LearnedExperimentConfig],
) -> list[dict[str, object]]:
    """Dry-run split construction for each config; report infeasible cells.

    Split draws are pure functions of local data, so infeasible cells can be
    caught in seconds before a fleet is provisioned (the 2026-07-11 batch
    spent its full runtime discovering 24 structurally infeasible
    component-vocab cells at merge time).
    """
    infeasible: list[dict[str, object]] = []
    for config in configs:
        status, _ = construct_splits(config)
        if status is not None:
            infeasible.append(
                {
                    "split": config.split,
                    "split_seed": config.split_seed,
                    "status": status,
                }
            )
    return infeasible


def run_one(config: LearnedExperimentConfig) -> dict[str, object]:
    _validate_claim_config(config)
    if config.model == "catboost_regressor" and CatBoostRegressor is None:
        if config.allow_missing_optional_models:
            return missing_optional_model_result(config)
        raise _missing_catboost_error()

    status, constructed = construct_splits(config)
    if status is not None:
        return _insufficient_result(config, status)
    split, build_lookup, split_extras, inner_folds = constructed

    fold_bundles = [
        (
            baseline._feature_bundle(fold.train, build_lookup, _baseline_config(config)),
            baseline._feature_bundle(fold.test, build_lookup, _baseline_config(config)),
        )
        for fold in inner_folds
    ]
    hpo = tune_hyperparameters(config, fold_bundles)

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
    suite = baseline.evaluation_metric_suite(
        outer_train,
        outer_test,
        final_prediction.predictions,
        _baseline_config(config),
        config.primary_top_k,
    )
    comparators = run_inline_comparators(config, outer_train, outer_test)
    deltas = comparator_deltas(
        final_trial.metrics, suite["rank_metrics"], config.model, comparators
    )
    outer_train_build_keys = frozenset(
        row.build_key
        for row in split.train
        if isinstance(row, TrainingMatchupRow) and row.build_key is not None
    )
    feature_protocol = feature_selection_protocol(outer_train.records, config.feature_profile)
    hierarchy = hierarchy_scorecard(config, split, build_lookup, split_extras)
    honest_eval_lineage = _honest_eval_lineage(config.db_path)
    claim = claim_boundary(config, honest_eval_lineage)
    family_policy = model_family_policy(config)
    deploy_policy = deployment_policy(config)
    leakage = leakage_diagnostics(hierarchy)
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "claim_boundary": claim,
        "model_family_policy": family_policy,
        "feature_selection_protocol": feature_protocol,
        "deployment_policy": deploy_policy,
        "hierarchy_scorecard": hierarchy,
        "inner_validation_metadata": inner_validation_metadata(config),
        "inner_cv": {
            "fold_count": len(fold_bundles),
            "fold_construction": inner_validation_metadata(config)["fold_construction"],
            "fold_sizes": [
                {"train": len(train.rows), "validation": len(validation.rows)}
                for train, validation in fold_bundles
            ],
        },
        "leakage_diagnostics": leakage,
        "outer_split_lineage": outer_split_lineage(config),
        "provenance": provenance(config),
        "split": config.split,
        "model": config.model,
        "status": "completed",
        "target_variable": TARGET_VARIABLE,
        "feature_families": feature_families(outer_train.records, config.feature_profile),
        "feature_family_registry": feature_protocol["feature_family_registry"],
        "feature_family_registry_sha256": feature_protocol["feature_family_registry_sha256"],
        "n_train": len(outer_train.rows),
        "n_test": len(outer_test.rows),
        "mae": final_trial.metrics["mae"],
        "rmse": final_trial.metrics["rmse"],
        "spearman_rho": final_trial.metrics["spearman_rho"],
        "noise_floor": suite["noise_floor"],
        "degenerate_opponents": suite["degenerate_opponents"],
        "rank_metrics": suite["rank_metrics"],
        "skill_scores": suite["skill_scores"],
        "panel_target_stats": suite["panel_target_stats"],
        "diagnostics": final_prediction.diagnostics,
        "stratified": baseline.stratified_metrics(outer_test, final_prediction.predictions),
        "honest_eval_top_k": baseline.honest_eval_diagnostic_for_model(
            final_model, build_lookup, _baseline_config(config),
            outer_train_build_keys, config.primary_top_k,
        ),
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
        "comparator_inline": comparators,
        "comparator_delta": deltas,
        "timing": {"fit_predict_seconds": final_trial.duration_seconds},
        "leakage_checklist": leakage_checklist(),
    }


def _insufficient_result(config: LearnedExperimentConfig, reason: str) -> dict[str, object]:
    _validate_claim_config(config)
    feature_protocol = feature_selection_protocol((), config.feature_profile)
    honest_eval_lineage = _honest_eval_lineage(config.db_path)
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "db_path": str(config.db_path),
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "claim_boundary": claim_boundary(config, honest_eval_lineage),
        "model_family_policy": model_family_policy(config),
        "feature_selection_protocol": feature_protocol,
        "deployment_policy": deployment_policy(config),
        "hierarchy_scorecard": hierarchy_scorecard(config, None, {}),
        "inner_validation_metadata": inner_validation_metadata(config),
        "leakage_diagnostics": leakage_diagnostics(),
        "outer_split_lineage": outer_split_lineage(config),
        "provenance": provenance(config),
        "split": config.split,
        "model": config.model,
        "status": reason,
        "target_variable": TARGET_VARIABLE,
        "feature_family_registry": feature_protocol["feature_family_registry"],
        "feature_family_registry_sha256": feature_protocol["feature_family_registry_sha256"],
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
    lineage = _honest_eval_lineage(config.db_path)
    return {
        "game_dir": str(config.game_dir),
        "target_variable": TARGET_VARIABLE,
        "split_seed": config.split_seed,
        "hpo_seed": config.hpo_seed,
        "hpo_trials": config.hpo_trials,
        "hpo_jobs": config.hpo_jobs,
        "model_thread_count": config.model_thread_count,
        "inner_cv_folds": config.inner_cv_folds,
        "noise_floor_override": config.noise_floor_override,
        "bootstrap_resamples": config.bootstrap_resamples,
        "component_vocab_max_overshoot": config.component_vocab_max_overshoot,
        "holdout_fraction": config.holdout_fraction,
        "train_fraction": config.train_fraction,
        "top_k_values": list(config.top_k_values),
        "max_rows": config.max_rows,
        "feature_profile": config.feature_profile,
        "honest_eval_usage": config.honest_eval_usage,
        "fresh_honest_eval_ledger_id": config.fresh_honest_eval_ledger_id,
        "honest_eval_ledger_id": lineage["ledger_id"],
        "honest_eval_run_lineage": lineage["run_lineage"],
        "primary_top_k": config.primary_top_k,
        "promotion_metric": config.promotion_metric,
        "promotion_threshold": config.promotion_threshold,
        "claim_label": config.claim_label,
        "final_refit_policy": config.final_refit_policy,
        "candidate_universe": config.candidate_universe,
        "deployment_artifact": config.deployment_artifact,
        "batch_job_id": config.batch_job_id,
        "batch_name": config.batch_name,
        "batch_fleet_name": config.batch_fleet_name,
        # Runtime dependency set the experiment requires (spec 31 merge
        # homogeneity); workers install it via `uv sync --extra`.
        "dependency_extra": DEFAULT_DEPENDENCY_EXTRA,
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
        "split_seed": config.split_seed,
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
    _validate_claim_config(config)
    honest_eval_lineage = _honest_eval_lineage(config.db_path)
    feature_protocol = feature_selection_protocol((), config.feature_profile)
    merged_registry: dict[str, dict[str, object]] = {}
    comparator_contexts: dict[str, object] = {}
    for result in results:
        registry = result.get("feature_family_registry")
        if isinstance(registry, Mapping):
            for key, value in registry.items():
                if isinstance(key, str) and isinstance(value, Mapping):
                    merged_registry[key] = dict(value)
        split = result.get("split")
        model = result.get("model")
        comparator_inline = result.get("comparator_inline")
        if isinstance(split, str) and isinstance(model, str) and comparator_inline is not None:
            comparator_contexts[f"{split}:{model}"] = comparator_inline
    if merged_registry:
        feature_protocol = {
            **feature_protocol,
            "feature_family_registry": merged_registry,
            "feature_family_registry_sha256": _registry_digest(merged_registry),
            "selected_feature_families": sorted({
                item["family"]
                for item in merged_registry.values()
                if isinstance(item.get("family"), str)
            }),
            "selected_feature_count": len(merged_registry),
        }
    return {
        "experiment_schema_version": EXPERIMENT_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "batch_job": batch_job_context(config),
        "claim_boundary": claim_boundary(config, honest_eval_lineage),
        "model_family_policy": model_family_policy(config),
        "feature_selection_protocol": feature_protocol,
        "deployment_policy": deployment_policy(config),
        "honest_eval_lineage": honest_eval_lineage,
        "hierarchy_scorecard": hierarchy_scorecard(config, None, {}),
        "leakage_diagnostics": leakage_diagnostics(),
        "outer_split_lineage": outer_split_lineage(config),
        "db_path": str(config.db_path),
        "status": status,
        "elapsed_seconds": time.monotonic() - started,
        "provenance": provenance(config),
        "model_families": list(MODEL_CHOICES if config.model == "all" else (config.model,)),
        "skipped_models": list(skipped),
        "feature_family_registry": feature_protocol["feature_family_registry"],
        "feature_family_registry_sha256": feature_protocol["feature_family_registry_sha256"],
        "comparator_inline": comparator_contexts,
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
        honest_eval_usage=args.honest_eval_usage,
        fresh_honest_eval_ledger_id=args.fresh_honest_eval_ledger_id,
        primary_top_k=args.primary_top_k,
        promotion_metric=args.promotion_metric,
        promotion_threshold=args.promotion_threshold,
        claim_label=args.claim_label,
        final_refit_policy=args.final_refit_policy,
        candidate_universe=args.candidate_universe,
        deployment_artifact=args.deployment_artifact,
        inner_cv_folds=args.inner_cv_folds,
        noise_floor_override=args.noise_floor_override,
        bootstrap_resamples=args.bootstrap_resamples,
        component_vocab_max_overshoot=args.component_vocab_max_overshoot,
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
