#!/usr/bin/env python
"""Run comparator-gate baselines over a Phase 7 matchup SQLite DB."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from typing import Protocol, TypeVar
from collections.abc import Iterable, Mapping, Sequence

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
    opponent_feature_row,
)
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.models import Build
from starsector_optimizer.phase7_eval import (
    EvalMetricsConfig,
    build_aggregate_rank_metrics,
    honest_eval_build_metrics,
    panel_target_stats,
    per_opponent_rank_metrics,
    resolve_noise_floor,
    sample_sd,
    skill_scores,
    two_way_cluster_bootstrap,
)
from starsector_optimizer.phase7_matchup_data import (
    DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT,
    HonestEvalMatchupRow,
    SplitIds,
    TrainingMatchupRow,
    component_fingerprint_json,
    component_vocabulary,
    forward_time_split,
    held_out_build_split,
    held_out_component_vocabulary_split,
    held_out_opponent_family_split,
    held_out_opponent_hull_split,
    held_out_opponent_split,
    held_out_seed_cell_split,
    load_honest_eval_matchups,
    load_recovered_builds,
    load_training_matchups,
    reject_burned_split_seed,
)

DEFAULT_HOLDOUT_FRACTION = 0.2
DEFAULT_TRAIN_FRACTION = 0.8
DEFAULT_RANDOM_SEED = 101
DEFAULT_TREE_COUNT = 200
DEFAULT_MIN_SAMPLES_LEAF = 2
DEFAULT_RIDGE_ALPHA = 10.0
ALL_AVAILABLE_CORES = -1
DEFAULT_TOP_K_VALUES = (1, 3, 5)
SCORE_REGIME_LOSS_MAX = -0.5
SCORE_REGIME_WIN_MIN = 0.5
COMPONENT_OVERLAP_K_VALUES = (1, 2, 3)
COMPONENT_KEY_DEFINITION = (
    "canonical_full_component_fingerprint:hull_id+slot_weapon_assignments+"
    "hullmods+flux_vents+flux_capacitors"
)
COMPONENT_VOCAB_KEY_DEFINITION = "slot_agnostic_weapon_and_hullmod_vocabulary"
SPLIT_CHOICES = (
    "build",
    "opponent",
    "opponent-hull",
    "opponent-family",
    "component-vocab",
    "seed-cell",
    "forward-time",
)
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
    eval_metrics: EvalMetricsConfig = field(default_factory=EvalMetricsConfig)
    component_vocab_max_overshoot: float = DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT


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
    def fit(
        self,
        rows: Sequence[Row],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None: ...

    def predict(
        self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]
    ) -> PredictionResult: ...


class GlobalMeanModel:
    def __init__(self) -> None:
        self.mean = 0.0

    def fit(
        self,
        rows: Sequence[Row],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        self.mean = float(np.mean(targets))

    def predict(
        self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]
    ) -> PredictionResult:
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

    def fit(
        self,
        rows: Sequence[Row],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        self.global_mean = float(np.mean(targets))
        sums: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for row, target in zip(rows, targets, strict=True):
            group = self._group(row)
            sums[group] += float(target)
            counts[group] += 1
        self.group_means = {key: sums[key] / counts[key] for key in sums}

    def predict(
        self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]
    ) -> PredictionResult:
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

    def fit(
        self,
        rows: Sequence[Row],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
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

    def predict(
        self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]
    ) -> PredictionResult:
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

    def fit(
        self,
        rows: Sequence[Row],
        records: Sequence[Mapping[str, FeatureValue]],
        targets: np.ndarray,
    ) -> None:
        self.pipeline.fit(list(records), targets)

    def predict(
        self, rows: Sequence[Row], records: Sequence[Mapping[str, FeatureValue]]
    ) -> PredictionResult:
        return PredictionResult(np.asarray(self.pipeline.predict(list(records)), dtype=float), {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fit comparator-gate Phase 7 matchup baselines.")
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
    parser.add_argument(
        "--feature-profile", choices=FEATURE_PROFILES, default=DEFAULT_FEATURE_PROFILE
    )
    parser.add_argument("--noise-floor-override", type=float, default=None)
    parser.add_argument(
        "--bootstrap-resamples", type=int, default=EvalMetricsConfig().bootstrap_resamples
    )
    parser.add_argument(
        "--component-vocab-max-overshoot",
        type=float,
        default=DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT,
    )
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


def _split_rows(
    config: BaselineConfig,
) -> tuple[SplitIds, dict[str, Build], dict[str, object]]:
    """Build the outer split; returns (split, build_lookup, split_extras)."""
    reject_burned_split_seed(config.seed)
    build_lookup = {item.build_key: item.build for item in load_recovered_builds(config.db_path)}
    rows = list(load_training_matchups(config.db_path))
    if config.max_rows is not None:
        rows = rows[: config.max_rows]
    rows = [row for row in rows if row.build_key in build_lookup]
    extras: dict[str, object] = {}
    if config.split == "build":
        split = held_out_build_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "opponent":
        split = held_out_opponent_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "opponent-hull":
        opponent_hull_by_variant, _ = opponent_group_maps(config.game_dir, rows)
        split = held_out_opponent_hull_split(
            rows, opponent_hull_by_variant, config.holdout_fraction, config.seed
        )
    elif config.split == "opponent-family":
        _, opponent_family_by_variant = opponent_group_maps(config.game_dir, rows)
        split = held_out_opponent_family_split(
            rows, opponent_family_by_variant, config.holdout_fraction, config.seed
        )
    elif config.split == "component-vocab":
        vocab_split = held_out_component_vocabulary_split(
            rows,
            build_lookup,
            config.holdout_fraction,
            config.component_vocab_max_overshoot,
            config.seed,
        )
        split = vocab_split.split
        extras = {
            "held_out_components": list(vocab_split.held_out_components),
            "realized_test_fraction": vocab_split.realized_test_fraction,
        }
    elif config.split == "seed-cell":
        split = held_out_seed_cell_split(rows, config.holdout_fraction, config.seed)
    else:
        split = forward_time_split(rows, config.train_fraction)
    return split, build_lookup, extras


def opponent_group_maps(
    game_dir: Path,
    rows: Sequence[TrainingMatchupRow],
) -> tuple[dict[str, str], dict[str, str]]:
    game_data, _ = _load_context(game_dir)
    hull_by_variant: dict[str, str] = {}
    family_by_variant: dict[str, str] = {}
    family_fields = (
        "opponent_hull_size",
        "opponent_hull_designation",
        "opponent_hull_tech_manufacturer",
    )
    for variant_id in sorted({row.opponent_variant_id for row in rows}):
        features = opponent_feature_row(variant_id, game_dir, game_data)
        hull_id = str(features["opponent_hull_id"])
        missing = [field for field in family_fields if field not in features]
        if missing:
            raise ValueError(
                f"opponent variant {variant_id!r} is missing family field(s): {', '.join(missing)}"
            )
        hull_by_variant[variant_id] = hull_id
        family_by_variant[variant_id] = ":".join(str(features[field]) for field in family_fields)
    return hull_by_variant, family_by_variant


def split_metadata(config: BaselineConfig) -> dict[str, object]:
    if config.split == "build":
        return {
            "split_level": "build",
            "group_key_function": "build_key",
            "group_key_fields": ["training_matchups.build_key"],
            "supported_claim": (
                "Transfer to unseen repaired player builds drawn from the same "
                "broader build distribution."
            ),
            "claim_supported": "supported",
        }
    if config.split == "opponent":
        return {
            "split_level": "opponent",
            "group_key_function": "opponent_variant_id",
            "group_key_fields": ["training_matchups.opponent_variant_id"],
            "supported_claim": "Transfer to unseen exact opponent variants/builds.",
            "claim_supported": "supported",
        }
    if config.split == "opponent-hull":
        return {
            "split_level": "opponent-hull",
            "group_key_function": "opponent_hull_id_from_stock_variant",
            "group_key_fields": ["opponent_hull_id"],
            "supported_claim": (
                "Transfer to unseen opponent hulls using outcome-free stock variant descriptors."
            ),
            "claim_supported": "supported",
        }
    if config.split == "opponent-family":
        return {
            "split_level": "opponent-family",
            "group_key_function": "opponent_size_designation_manufacturer_family",
            "group_key_fields": [
                "opponent_hull_size",
                "opponent_hull_designation",
                "opponent_hull_tech_manufacturer",
            ],
            "supported_claim": (
                "Transfer to unseen outcome-free opponent "
                "hull-size/designation/manufacturer families."
            ),
            "claim_supported": "supported",
        }
    if config.split == "component-vocab":
        return {
            "split_level": "component-vocab",
            "group_key_function": "component_vocabulary_membership",
            "group_key_fields": [
                "Build.weapon_assignments",
                "Build.hullmods",
            ],
            "supported_claim": (
                "Transfer to builds containing weapon/hullmod IDs never seen in training."
            ),
            "claim_supported": "supported",
            "component_key_definition": COMPONENT_VOCAB_KEY_DEFINITION,
        }
    if config.split == "seed-cell":
        return {
            "split_level": "seed-cell",
            "group_key_function": "campaign_seed",
            "group_key_fields": ["training_matchups.campaign", "training_matchups.seed"],
            "supported_claim": "Transfer across campaign cells/proposal contexts.",
            "claim_supported": "supported",
        }
    if config.split == "forward-time":
        return {
            "split_level": "forward-time",
            "group_key_function": "source_path_trial_number_opponent_index_order",
            "group_key_fields": [
                "training_matchups.source_path",
                "training_matchups.trial_number",
                "training_matchups.opponent_index",
            ],
            "supported_claim": (
                "Forward deployment over later optimizer proposals in path-ordered source rows."
            ),
            "claim_supported": "supported",
        }
    raise ValueError(f"unknown split {config.split!r}")


def _component_tokens(build: Build) -> tuple[str, ...]:
    tokens = [f"hull:{build.hull_id}"]
    tokens.extend(
        f"weapon:{slot_id}:{weapon_id}"
        for slot_id, weapon_id in sorted(build.weapon_assignments.items())
    )
    tokens.extend(f"hullmod:{hullmod_id}" for hullmod_id in sorted(build.hullmods))
    tokens.append(f"flux_vents:{build.flux_vents}")
    tokens.append(f"flux_capacitors:{build.flux_capacitors}")
    return tuple(tokens)


def _k_combinations(tokens: Sequence[str], k: int) -> set[tuple[str, ...]]:
    if k > len(tokens):
        return set()
    return set(combinations(tokens, k))


_ItemT = TypeVar("_ItemT")


def _overlap_summary(train_items: set[_ItemT], test_items: set[_ItemT]) -> dict[str, int | float]:
    overlap = train_items & test_items
    return {
        "train_unique": len(train_items),
        "test_unique": len(test_items),
        "overlap_unique": len(overlap),
        "test_overlap_fraction": 0.0 if not test_items else len(overlap) / len(test_items),
    }


def _overlap_count(train_items: set[_ItemT], test_items: set[_ItemT]) -> int:
    return len(train_items & test_items)


def component_overlap_diagnostics(
    train_rows: Sequence[TrainingMatchupRow],
    test_rows: Sequence[TrainingMatchupRow],
    build_lookup: Mapping[str, Build],
) -> dict[str, object]:
    train_builds = [build_lookup[row.build_key] for row in train_rows]
    test_builds = [build_lookup[row.build_key] for row in test_rows]
    train_fingerprints = {component_fingerprint_json(build) for build in train_builds}
    test_fingerprints = {component_fingerprint_json(build) for build in test_builds}
    diagnostics: dict[str, object] = {
        "component_key_definition": COMPONENT_KEY_DEFINITION,
        "exact_full_fingerprint": _overlap_summary(train_fingerprints, test_fingerprints),
    }
    for k in COMPONENT_OVERLAP_K_VALUES:
        train_combos: set[tuple[str, ...]] = set()
        test_combos: set[tuple[str, ...]] = set()
        for build in train_builds:
            train_combos.update(_k_combinations(_component_tokens(build), k))
        for build in test_builds:
            test_combos.update(_k_combinations(_component_tokens(build), k))
        diagnostics[f"k_{k}_component_combinations"] = _overlap_summary(train_combos, test_combos)
    return diagnostics


def split_overlap_counts(
    train_rows: Sequence[TrainingMatchupRow],
    test_rows: Sequence[TrainingMatchupRow],
    build_lookup: Mapping[str, Build],
    opponent_hull_by_variant: Mapping[str, str] | None = None,
    opponent_family_by_variant: Mapping[str, str] | None = None,
    held_out_components: Sequence[str] | None = None,
) -> dict[str, int]:
    opponent_hull_by_variant = opponent_hull_by_variant or {}
    opponent_family_by_variant = opponent_family_by_variant or {}
    train_vocabulary: set[str] = set()
    for row in train_rows:
        if row.build_key in build_lookup:
            train_vocabulary.update(component_vocabulary(build_lookup[row.build_key]))
    train_components = {
        component_fingerprint_json(build_lookup[row.build_key])
        for row in train_rows
        if row.build_key in build_lookup
    }
    test_components = {
        component_fingerprint_json(build_lookup[row.build_key])
        for row in test_rows
        if row.build_key in build_lookup
    }
    counts = {
        "exact_build": _overlap_count(
            {row.build_key for row in train_rows},
            {row.build_key for row in test_rows},
        ),
        "exact_opponent": _overlap_count(
            {row.opponent_variant_id for row in train_rows},
            {row.opponent_variant_id for row in test_rows},
        ),
        "opponent_hull": _overlap_count(
            {
                opponent_hull_by_variant[row.opponent_variant_id]
                for row in train_rows
                if row.opponent_variant_id in opponent_hull_by_variant
            },
            {
                opponent_hull_by_variant[row.opponent_variant_id]
                for row in test_rows
                if row.opponent_variant_id in opponent_hull_by_variant
            },
        ),
        "opponent_family": _overlap_count(
            {
                opponent_family_by_variant[row.opponent_variant_id]
                for row in train_rows
                if row.opponent_variant_id in opponent_family_by_variant
            },
            {
                opponent_family_by_variant[row.opponent_variant_id]
                for row in test_rows
                if row.opponent_variant_id in opponent_family_by_variant
            },
        ),
        "hull_id": _overlap_count(
            {
                build_lookup[row.build_key].hull_id
                for row in train_rows
                if row.build_key in build_lookup
            },
            {
                build_lookup[row.build_key].hull_id
                for row in test_rows
                if row.build_key in build_lookup
            },
        ),
        "component_combination": _overlap_count(train_components, test_components),
        "campaign_cell": _overlap_count(
            {f"{row.campaign}:{row.seed}" for row in train_rows},
            {f"{row.campaign}:{row.seed}" for row in test_rows},
        ),
        "exact_matchup_group": _overlap_count(
            {f"{row.build_key}:{row.opponent_variant_id}" for row in train_rows},
            {f"{row.build_key}:{row.opponent_variant_id}" for row in test_rows},
        ),
    }
    if held_out_components is not None:
        # component-vocab forbidden-key count: held-out component IDs seen in
        # any train build. Must be zero for a valid vocabulary holdout. The
        # key is emitted only when a vocabulary holdout exists, so a 0 can
        # never be misread as a passed gate on other splits.
        counts["component_vocabulary"] = len(set(held_out_components) & train_vocabulary)
    return counts


def _feature_bundle(rows: Sequence[Row], build_lookup, config: BaselineConfig) -> FeatureBundle:
    game_data, manifest = _load_context(config.game_dir)
    records = []
    for row in rows:
        if row.build_key is None:
            continue
        cache_key = (
            str(config.game_dir),
            row.build_key,
            row.opponent_variant_id,
            config.feature_profile,
        )
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
        return PipelineModel(
            Pipeline(
                [
                    ("features", DictVectorizer(sparse=True)),
                    ("model", Ridge(alpha=config.ridge_alpha)),
                ]
            )
        )
    if name == "random_forest":
        return PipelineModel(
            Pipeline(
                [
                    ("features", DictVectorizer(sparse=True)),
                    (
                        "model",
                        RandomForestRegressor(
                            n_estimators=config.tree_count,
                            random_state=config.seed,
                            n_jobs=ALL_AVAILABLE_CORES,
                            min_samples_leaf=DEFAULT_MIN_SAMPLES_LEAF,
                        ),
                    ),
                ]
            )
        )
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


def _group_metric(
    rows: Sequence[Row],
    records: Sequence[Mapping[str, FeatureValue]],
    y_true: np.ndarray,
    pred: np.ndarray,
    group_key: str,
) -> dict[str, dict[str, float | int | None]]:
    grouped_true: dict[str, list[float]] = defaultdict(list)
    grouped_pred: dict[str, list[float]] = defaultdict(list)
    for row, record, target, prediction in zip(rows, records, y_true, pred, strict=True):
        if group_key == "score_regime":
            group = _score_regime(float(target))
        elif group_key == "campaign":
            group = getattr(row, "campaign", None) or "unknown"
        elif group_key == "opponent_variant_id":
            group = row.opponent_variant_id
        elif group_key == "opponent_family":
            group = ":".join(
                str(record.get(field, "unknown"))
                for field in (
                    "opponent_hull_size",
                    "opponent_hull_designation",
                    "opponent_hull_tech_manufacturer",
                )
            )
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
        "opponent_variant_id": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "opponent_variant_id"
        ),
        "opponent_family": _group_metric(
            bundle.rows, bundle.records, bundle.targets, pred, "opponent_family"
        ),
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
        "campaign": _group_metric(bundle.rows, bundle.records, bundle.targets, pred, "campaign"),
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


def degenerate_opponents_for_panel(
    rows: Sequence[Row],
    targets: np.ndarray,
    noise_floor: float,
) -> frozenset[str]:
    """Opponents whose within-opponent target SD sits below the noise floor.

    Computed once per (split, seed) from the full test panel and held fixed
    (spec 31: not recomputed per bootstrap resample).
    """
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, target in zip(rows, targets, strict=True):
        grouped[row.opponent_variant_id].append(float(target))
    return frozenset(
        opponent for opponent, values in grouped.items() if sample_sd(values) < noise_floor
    )


def _noise_floor_value(noise: Mapping[str, object]) -> float:
    """Extract the resolved floor; resolve_noise_floor guarantees a float."""
    floor = noise["noise_floor"]
    if not isinstance(floor, float):
        raise TypeError(f"noise_floor must be float, got {type(floor).__name__}")
    return floor


def evaluation_metric_suite(
    train: FeatureBundle,
    test: FeatureBundle,
    predictions: np.ndarray,
    config: BaselineConfig,
    primary_k: int,
    *,
    include_bootstrap: bool = True,
) -> dict[str, object]:
    """Spec 31 evaluation-metric suite over an outer test panel."""
    noise = resolve_noise_floor(config.eval_metrics, load_honest_eval_matchups(config.db_path))
    floor = _noise_floor_value(noise)
    opponents = [row.opponent_variant_id for row in test.rows]
    builds = [row_build_key(row) for row in test.rows]
    degenerate = degenerate_opponents_for_panel(test.rows, test.targets, floor)
    rank_metrics: dict[str, object] = {
        "per_opponent": per_opponent_rank_metrics(
            builds, opponents, test.targets, predictions, floor, config.eval_metrics
        ),
        "build_aggregate": build_aggregate_rank_metrics(
            builds,
            opponents,
            test.targets,
            predictions,
            degenerate,
            config.top_k_values,
            config.eval_metrics,
        ),
    }
    if include_bootstrap:
        rank_metrics["bootstrap"] = two_way_cluster_bootstrap(
            builds,
            opponents,
            test.targets,
            predictions,
            floor,
            degenerate,
            primary_k,
            config.eval_metrics,
        )
    return {
        "noise_floor": noise,
        "degenerate_opponents": sorted(degenerate),
        "rank_metrics": rank_metrics,
        "skill_scores": skill_scores(
            test.targets, predictions, float(np.mean(train.targets)), config.eval_metrics
        ),
        "panel_target_stats": panel_target_stats(test.targets),
    }


def honest_eval_diagnostic_for_model(
    model: BaselineModel,
    build_lookup,
    config: BaselineConfig,
    outer_train_build_keys: frozenset[str],
    primary_k: int,
) -> dict[str, object]:
    rows = [
        row
        for row in load_honest_eval_matchups(config.db_path)
        if row.build_key is not None and row.build_key in build_lookup
    ]
    if config.max_rows is not None:
        rows = rows[: config.max_rows]
    if not rows:
        return {"honest_eval_builds": 0}
    bundle = _feature_bundle(rows, build_lookup, config)
    pred = model.predict(bundle.rows, bundle.records).predictions
    honest_rows = [row for row in bundle.rows if isinstance(row, HonestEvalMatchupRow)]
    out: dict[str, object] = dict(top_k_recall(honest_rows, pred, config.top_k_values))
    noise = resolve_noise_floor(config.eval_metrics, honest_rows)
    floor = _noise_floor_value(noise)
    out["build_metrics"] = honest_eval_build_metrics(
        [row_build_key(row) for row in honest_rows],
        [row.opponent_variant_id for row in honest_rows],
        bundle.targets,
        pred,
        degenerate_opponents=degenerate_opponents_for_panel(honest_rows, bundle.targets, floor),
        outer_train_build_keys=outer_train_build_keys,
        k_values=config.top_k_values,
        primary_k=primary_k,
        config=config.eval_metrics,
    )
    out["noise_floor"] = noise
    return out


def run_one(config: BaselineConfig) -> dict[str, object]:
    split, build_lookup, split_extras = _split_rows(config)
    if not split.train or not split.test:
        raise SystemExit("selected split produced an empty train or test partition")
    train = _feature_bundle(split.train, build_lookup, config)
    test = _feature_bundle(split.test, build_lookup, config)
    model = make_model(config.model, config)
    model.fit(train.rows, train.records, train.targets)
    result = model.predict(test.rows, test.records)
    metrics = regression_metrics(test.targets, result.predictions)
    diagnostics: dict[str, object] = dict(result.diagnostics)
    train_training_rows = [row for row in split.train if isinstance(row, TrainingMatchupRow)]
    test_training_rows = [row for row in split.test if isinstance(row, TrainingMatchupRow)]
    opponent_hull_by_variant, opponent_family_by_variant = opponent_group_maps(
        config.game_dir, train_training_rows + test_training_rows
    )
    held_out_components = split_extras.get("held_out_components")
    if held_out_components is not None and not isinstance(held_out_components, list):
        raise TypeError(
            "split_extras['held_out_components'] must be a list, got "
            f"{type(held_out_components).__name__}"
        )
    diagnostics["overlap_counts"] = split_overlap_counts(
        train_training_rows,
        test_training_rows,
        build_lookup,
        opponent_hull_by_variant,
        opponent_family_by_variant,
        held_out_components=held_out_components,
    )
    if config.split == "component-vocab":
        diagnostics["component_overlap_diagnostics"] = component_overlap_diagnostics(
            train_training_rows,
            test_training_rows,
            build_lookup,
        )
    suite = evaluation_metric_suite(
        train, test, result.predictions, config, min(config.top_k_values)
    )
    outer_train_build_keys = frozenset(
        row.build_key for row in train_training_rows if row.build_key is not None
    )
    return {
        "db_path": str(config.db_path),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": config.feature_profile,
        "provenance": provenance(config),
        "split_metadata": {**split_metadata(config), **split_extras},
        "split": config.split,
        "model": config.model,
        "n_train": len(train.rows),
        "n_test": len(test.rows),
        **metrics,
        **suite,
        "diagnostics": diagnostics,
        "stratified": stratified_metrics(test, result.predictions),
        "honest_eval_top_k": honest_eval_diagnostic_for_model(
            model,
            build_lookup,
            config,
            outer_train_build_keys,
            min(config.top_k_values),
        ),
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
        "noise_floor_override": config.eval_metrics.noise_floor_override,
        "bootstrap_resamples": config.eval_metrics.bootstrap_resamples,
        "component_vocab_max_overshoot": config.component_vocab_max_overshoot,
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
                eval_metrics=config.eval_metrics,
                component_vocab_max_overshoot=config.component_vocab_max_overshoot,
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
        eval_metrics=EvalMetricsConfig(
            noise_floor_override=args.noise_floor_override,
            bootstrap_resamples=args.bootstrap_resamples,
        ),
        component_vocab_max_overshoot=args.component_vocab_max_overshoot,
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
    payload: object = (
        results[0]
        if len(results) == 1
        else {
            "db_path": str(config.db_path),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_profile": config.feature_profile,
            "provenance": provenance(config),
            "result_count": len(results),
            "results": results,
        }
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
