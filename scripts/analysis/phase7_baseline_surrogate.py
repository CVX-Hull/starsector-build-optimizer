#!/usr/bin/env python
"""Run comparator-gate baselines over a Phase 7 matchup SQLite DB."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline

from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.matchup_features import (
    DEFAULT_FEATURE_PROFILE,
    FEATURE_PROFILES,
    FEATURE_SCHEMA_VERSION,
    filter_feature_profile,
    matchup_feature_row,
)
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.phase7_matchup_data import (
    HonestEvalMatchupRow,
    SplitIds,
    TrainingMatchupRow,
    forward_time_split,
    held_out_build_split,
    held_out_component_combination_split,
    held_out_opponent_split,
    held_out_seed_cell_split,
    load_honest_eval_matchups,
    load_recovered_builds,
    load_training_matchups,
)

DEFAULT_HOLDOUT_FRACTION = 0.2
DEFAULT_TRAIN_FRACTION = 0.8
DEFAULT_RANDOM_SEED = 17
DEFAULT_TREE_COUNT = 200
DEFAULT_MIN_SAMPLES_LEAF = 2
DEFAULT_RIDGE_ALPHA = 10.0
ALL_AVAILABLE_CORES = -1
DEFAULT_TOP_K_VALUES = (1, 3, 5)
SCORE_REGIME_LOSS_MAX = -0.5
SCORE_REGIME_WIN_MIN = 0.5
SPLIT_CHOICES = ("build", "opponent", "component", "seed-cell", "forward-time")
MODEL_CHOICES = (
    "global_mean",
    "opponent_mean",
    "build_mean",
    "twfe_additive",
    "ridge_hybrid",
    "random_forest",
)


Row = TrainingMatchupRow | HonestEvalMatchupRow
FeatureValue = float | int | str
_FEATURE_CACHE: dict[tuple[str, str, str, str], dict[str, FeatureValue]] = {}


@lru_cache(maxsize=4)
def _load_context(game_dir: Path):
    return load_game_data(game_dir), GameManifest.load(game_dir / "manifest.json")


@dataclass(frozen=True)
class BaselineConfig:
    db_path: Path
    game_dir: Path
    split: str
    model: str
    holdout_fraction: float
    train_fraction: float
    seed: int
    tree_count: int
    ridge_alpha: float
    max_rows: int | None
    top_k_values: tuple[int, ...]
    progress: bool
    feature_profile: str = DEFAULT_FEATURE_PROFILE


@dataclass(frozen=True)
class FeatureBundle:
    rows: tuple[Row, ...]
    records: tuple[dict[str, FeatureValue], ...]
    targets: np.ndarray


@dataclass(frozen=True)
class PredictionResult:
    predictions: np.ndarray
    diagnostics: dict[str, int | float | str]


class BaselineModel(Protocol):
    def fit(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], targets: np.ndarray) -> None:
        ...

    def predict(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]) -> PredictionResult:
        ...


class GlobalMeanModel:
    def __init__(self) -> None:
        self.mean = 0.0

    def fit(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], targets: np.ndarray) -> None:
        self.mean = float(np.mean(targets))

    def predict(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]) -> PredictionResult:
        return PredictionResult(np.full(len(rows), self.mean), {})


class GroupMeanModel:
    def __init__(self, group_name: str) -> None:
        self.group_name = group_name
        self.global_mean = 0.0
        self.group_means: dict[str, float] = {}

    def _group(self, row: Row) -> str:
        if self.group_name == "build":
            return row_build_key(row)
        if self.group_name == "opponent":
            return row.opponent_variant_id
        raise ValueError(f"unknown group {self.group_name!r}")

    def fit(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], targets: np.ndarray) -> None:
        self.global_mean = float(np.mean(targets))
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for row, target in zip(rows, targets, strict=True):
            group = self._group(row)
            sums[group] += float(target)
            counts[group] += 1
        self.group_means = {key: sums[key] / counts[key] for key in sums}

    def predict(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]) -> PredictionResult:
        fallback_count = 0
        preds: list[float] = []
        for row in rows:
            group = self._group(row)
            if group in self.group_means:
                preds.append(self.group_means[group])
            else:
                fallback_count += 1
                preds.append(self.global_mean)
        return PredictionResult(
            np.asarray(preds, dtype=float),
            {f"{self.group_name}_fallback_count": fallback_count},
        )


class TwfeAdditiveModel:
    def __init__(self) -> None:
        self.global_mean = 0.0
        self.build_effects: dict[str, float] = {}
        self.opponent_effects: dict[str, float] = {}

    def fit(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], targets: np.ndarray) -> None:
        self.global_mean = float(np.mean(targets))
        build_sums: dict[str, float] = defaultdict(float)
        build_counts: dict[str, int] = defaultdict(int)
        opp_sums: dict[str, float] = defaultdict(float)
        opp_counts: dict[str, int] = defaultdict(int)
        for row, target in zip(rows, targets, strict=True):
            residual = float(target) - self.global_mean
            build_key = row_build_key(row)
            build_sums[build_key] += residual
            build_counts[build_key] += 1
            opp_sums[row.opponent_variant_id] += residual
            opp_counts[row.opponent_variant_id] += 1
        self.build_effects = {key: build_sums[key] / build_counts[key] for key in build_sums}
        self.opponent_effects = {key: opp_sums[key] / opp_counts[key] for key in opp_sums}

    def predict(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]) -> PredictionResult:
        build_fallback_count = 0
        opponent_fallback_count = 0
        preds: list[float] = []
        for row in rows:
            build_key = row_build_key(row)
            if build_key in self.build_effects:
                build_effect = self.build_effects[build_key]
            else:
                build_fallback_count += 1
                build_effect = 0.0
            if row.opponent_variant_id in self.opponent_effects:
                opponent_effect = self.opponent_effects[row.opponent_variant_id]
            else:
                opponent_fallback_count += 1
                opponent_effect = 0.0
            preds.append(self.global_mean + build_effect + opponent_effect)
        return PredictionResult(
            np.asarray(preds, dtype=float),
            {
                "build_fallback_count": build_fallback_count,
                "opponent_fallback_count": opponent_fallback_count,
            },
        )


class PipelineModel:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline

    def fit(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], targets: np.ndarray) -> None:
        self.pipeline.fit(list(records), targets)

    def predict(self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]) -> PredictionResult:
        return PredictionResult(np.asarray(self.pipeline.predict(list(records)), dtype=float), {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit comparator-gate Phase 7 matchup baselines."
    )
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument("--split", choices=(*SPLIT_CHOICES, "all"), default="build")
    parser.add_argument("--model", choices=(*MODEL_CHOICES, "all"), default="random_forest")
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_TRAIN_FRACTION)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--tree-count", type=int, default=DEFAULT_TREE_COUNT)
    parser.add_argument("--ridge-alpha", type=float, default=DEFAULT_RIDGE_ALPHA)
    parser.add_argument("--top-k", default=",".join(str(item) for item in DEFAULT_TOP_K_VALUES))
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--feature-profile", choices=FEATURE_PROFILES, default=DEFAULT_FEATURE_PROFILE)
    parser.add_argument("--no-progress", action="store_true")
    return parser


def parse_top_k_values(raw: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    if not values or any(item < 1 for item in values):
        raise ValueError("--top-k must contain positive integers")
    return tuple(sorted(set(values)))


def row_build_key(row: Row) -> str:
    if row.build_key is not None:
        return row.build_key
    if isinstance(row, HonestEvalMatchupRow):
        return row.build_id
    raise ValueError("training matchup row has no build_key")


def _split_rows(config: BaselineConfig) -> tuple[SplitIds, dict[str, object]]:
    build_lookup = {item.build_key: item.build for item in load_recovered_builds(config.db_path)}
    rows = list(load_training_matchups(config.db_path))
    if config.max_rows is not None:
        rows = rows[:config.max_rows]
    rows = [row for row in rows if row.build_key in build_lookup]
    if config.split == "build":
        split = held_out_build_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "opponent":
        split = held_out_opponent_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "component":
        split = held_out_component_combination_split(
            rows, build_lookup, config.holdout_fraction, config.seed
        )
    elif config.split == "seed-cell":
        split = held_out_seed_cell_split(rows, config.holdout_fraction, config.seed)
    else:
        split = forward_time_split(rows, config.train_fraction)
    return split, build_lookup


def _feature_bundle(rows: Sequence[Row], build_lookup, config: BaselineConfig) -> FeatureBundle:
    game_data, manifest = _load_context(config.game_dir)
    records = []
    for row in rows:
        if row.build_key is None:
            continue
        cache_key = (str(config.game_dir), row.build_key, row.opponent_variant_id, config.feature_profile)
        if cache_key not in _FEATURE_CACHE:
            _FEATURE_CACHE[cache_key] = filter_feature_profile(
                matchup_feature_row(
                    build_lookup[row.build_key],
                    row.opponent_variant_id,
                    config.game_dir,
                    game_data,
                    manifest,
                ),
                config.feature_profile,
            )
        records.append(_FEATURE_CACHE[cache_key])
    targets = np.asarray([row.target for row in rows if row.build_key is not None], dtype=float)
    kept_rows = tuple(row for row in rows if row.build_key is not None)
    return FeatureBundle(rows=kept_rows, records=tuple(records), targets=targets)


def make_model(name: str, config: BaselineConfig) -> BaselineModel:
    if name == "global_mean":
        return GlobalMeanModel()
    if name == "opponent_mean":
        return GroupMeanModel("opponent")
    if name == "build_mean":
        return GroupMeanModel("build")
    if name == "twfe_additive":
        return TwfeAdditiveModel()
    if name == "ridge_hybrid":
        return PipelineModel(Pipeline([
            ("features", DictVectorizer(sparse=True)),
            ("model", Ridge(alpha=config.ridge_alpha)),
        ]))
    if name == "random_forest":
        return PipelineModel(Pipeline([
            ("features", DictVectorizer(sparse=True)),
            ("model", RandomForestRegressor(
                n_estimators=config.tree_count,
                random_state=config.seed,
                n_jobs=ALL_AVAILABLE_CORES,
                min_samples_leaf=DEFAULT_MIN_SAMPLES_LEAF,
            )),
        ]))
    raise ValueError(f"unknown model {name!r}")


def _finite_spearman(y_true: np.ndarray, pred: np.ndarray) -> float | None:
    if len(y_true) < 2 or len(set(y_true.tolist())) < 2 or len(set(pred.tolist())) < 2:
        return None
    rho, _ = spearmanr(y_true, pred)
    return None if math.isnan(float(rho)) else float(rho)


def regression_metrics(y_true: np.ndarray, pred: np.ndarray) -> dict[str, float | None]:
    return {
        "mae": float(mean_absolute_error(y_true, pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, pred))),
        "spearman_rho": _finite_spearman(y_true, pred),
    }


def _score_regime(value: float) -> str:
    if value <= SCORE_REGIME_LOSS_MAX:
        return "loss"
    if value >= SCORE_REGIME_WIN_MIN:
        return "win"
    return "timeout_like"


def _group_metric(rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]], y_true: np.ndarray, pred: np.ndarray, group_key: str) -> dict[str, dict[str, float | int | None]]:
    grouped_true: dict[str, list[float]] = defaultdict(list)
    grouped_pred: dict[str, list[float]] = defaultdict(list)
    for row, record, target, prediction in zip(rows, records, y_true, pred, strict=True):
        if group_key == "score_regime":
            group = _score_regime(float(target))
        elif group_key == "campaign":
            group = getattr(row, "campaign", None) or "unknown"
        else:
            group = str(record.get(group_key, "unknown"))
        grouped_true[group].append(float(target))
        grouped_pred[group].append(float(prediction))
    out = {}
    for group in sorted(grouped_true):
        true_arr = np.asarray(grouped_true[group], dtype=float)
        pred_arr = np.asarray(grouped_pred[group], dtype=float)
        metrics = regression_metrics(true_arr, pred_arr)
        out[group] = {"n": len(true_arr), **metrics}
    return out


def stratified_metrics(bundle: FeatureBundle, pred: np.ndarray) -> dict[str, object]:
    return {
        "opponent_hull_size": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "opponent_hull_size"
        ),
        "opponent_designation": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "opponent_hull_designation"
        ),
        "opponent_tech_manufacturer": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "opponent_hull_tech_manufacturer"
        ),
        "score_regime": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "score_regime"
        ),
        "campaign": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "campaign"
        ),
    }


def top_k_recall(
    rows: Sequence[HonestEvalMatchupRow],
    predictions: np.ndarray,
    top_k_values: Sequence[int],
) -> dict[str, float | int | dict[str, float]]:
    observed: dict[str, list[float]] = defaultdict(list)
    predicted: dict[str, list[float]] = defaultdict(list)
    for row, prediction in zip(rows, predictions, strict=True):
        if row.build_key is None:
            continue
        observed[row.build_key].append(row.target)
        predicted[row.build_key].append(float(prediction))
    observed_mean = {key: float(np.mean(values)) for key, values in observed.items()}
    predicted_mean = {key: float(np.mean(values)) for key, values in predicted.items()}
    actual_order = sorted(observed_mean, key=lambda key: observed_mean[key], reverse=True)
    predicted_order = sorted(predicted_mean, key=lambda key: predicted_mean[key], reverse=True)
    out: dict[str, float | int | dict[str, float]] = {"honest_eval_builds": len(actual_order)}
    recalls: dict[str, float] = {}
    for k in top_k_values:
        bounded_k = min(k, len(actual_order))
        if bounded_k == 0:
            recalls[str(k)] = 0.0
            continue
        actual = set(actual_order[:bounded_k])
        predicted_set = set(predicted_order[:bounded_k])
        recalls[str(k)] = len(actual & predicted_set) / bounded_k
    out["top_k_recall"] = recalls
    return out


def honest_eval_top_k_for_model(model: BaselineModel, build_lookup, config: BaselineConfig) -> dict[str, object]:
    rows = [
        row for row in load_honest_eval_matchups(config.db_path)
        if row.build_key is not None and row.build_key in build_lookup
    ]
    if config.max_rows is not None:
        rows = rows[:config.max_rows]
    if not rows:
        return {"honest_eval_builds": 0}
    bundle = _feature_bundle(rows, build_lookup, config)
    pred = model.predict(bundle.rows, bundle.records).predictions
    return top_k_recall(
        [row for row in bundle.rows if isinstance(row, HonestEvalMatchupRow)],
        pred,
        config.top_k_values,
    )


def run_one(config: BaselineConfig) -> dict[str, object]:
    split, build_lookup = _split_rows(config)
    if not split.train or not split.test:
        raise SystemExit("selected split produced an empty train or test partition")
    train = _feature_bundle(split.train, build_lookup, config)
    test = _feature_bundle(split.test, build_lookup, config)
    model = make_model(config.model, config)
    model.fit(train.rows, train.records, train.targets)
    result = model.predict(test.rows, test.records)
    metrics = regression_metrics(test.targets, result.predictions)
    return {
        "db_path": str(config.db_path),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "provenance": provenance(config),
        "split": config.split,
        "model": config.model,
        "n_train": len(train.rows),
        "n_test": len(test.rows),
        **metrics,
        "diagnostics": result.diagnostics,
        "stratified": stratified_metrics(test, result.predictions),
        "honest_eval_top_k": honest_eval_top_k_for_model(model, build_lookup, config),
}


def provenance(config: BaselineConfig) -> dict[str, object]:
    return {
        "game_dir": str(config.game_dir),
        "seed": config.seed,
        "holdout_fraction": config.holdout_fraction,
        "train_fraction": config.train_fraction,
        "tree_count": config.tree_count,
        "ridge_alpha": config.ridge_alpha,
        "top_k_values": list(config.top_k_values),
        "max_rows": config.max_rows,
        "feature_profile": config.feature_profile,
    }


def _configs_to_run(config: BaselineConfig) -> Iterable[BaselineConfig]:
    splits = SPLIT_CHOICES if config.split == "all" else (config.split,)
    models = MODEL_CHOICES if config.model == "all" else (config.model,)
    for split in splits:
        for model in models:
            yield BaselineConfig(
                db_path=config.db_path,
                game_dir=config.game_dir,
                split=split,
                model=model,
                holdout_fraction=config.holdout_fraction,
                train_fraction=config.train_fraction,
                seed=config.seed,
                tree_count=config.tree_count,
                ridge_alpha=config.ridge_alpha,
                max_rows=config.max_rows,
                top_k_values=config.top_k_values,
                progress=config.progress,
                feature_profile=config.feature_profile,
            )


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, sec = divmod(int(seconds), 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minute:02d}m{sec:02d}s"
    if minute:
        return f"{minute}m{sec:02d}s"
    return f"{sec}s"


def _progress(message: str, enabled: bool) -> None:
    if enabled:
        print(f"[phase7-baseline] {message}", file=sys.stderr, flush=True)


def main() -> None:
    args = build_parser().parse_args()
    config = BaselineConfig(
        db_path=args.db_path,
        game_dir=args.game_dir,
        split=args.split,
        model=args.model,
        holdout_fraction=args.holdout_fraction,
        train_fraction=args.train_fraction,
        seed=args.seed,
        tree_count=args.tree_count,
        ridge_alpha=args.ridge_alpha,
        max_rows=args.max_rows,
        top_k_values=parse_top_k_values(args.top_k),
        progress=not args.no_progress,
        feature_profile=args.feature_profile,
    )
    configs = list(_configs_to_run(config))
    results = []
    started = time.monotonic()
    durations: list[float] = []
    _progress(
        f"starting {len(configs)} run(s): split={config.split} model={config.model} "
        f"db={config.db_path} feature_schema={FEATURE_SCHEMA_VERSION}",
        config.progress,
    )
    for idx, item in enumerate(configs, start=1):
        run_started = time.monotonic()
        _progress(
            f"{idx}/{len(configs)} start split={item.split} model={item.model}",
            config.progress,
        )
        results.append(run_one(item))
        duration = time.monotonic() - run_started
        durations.append(duration)
        mean_duration = sum(durations) / len(durations)
        remaining = mean_duration * (len(configs) - idx)
        elapsed = time.monotonic() - started
        _progress(
            f"{idx}/{len(configs)} done split={item.split} model={item.model} "
            f"duration={_format_duration(duration)} elapsed={_format_duration(elapsed)} "
            f"eta={_format_duration(remaining)}",
            config.progress,
        )
    payload: object = results[0] if len(results) == 1 else {
        "db_path": str(config.db_path),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "provenance": provenance(config),
        "result_count": len(results),
        "results": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
