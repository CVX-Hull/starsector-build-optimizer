#!/usr/bin/env python
"""Run a first grouped-split baseline over a Phase 7 matchup SQLite DB."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline

from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.matchup_features import matchup_feature_row
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.phase7_matchup_data import (
    SplitIds,
    forward_time_split,
    held_out_build_split,
    held_out_component_combination_split,
    held_out_opponent_split,
    held_out_replicate_split,
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
ALL_AVAILABLE_CORES = -1


@dataclass(frozen=True)
class BaselineConfig:
    db_path: Path
    game_dir: Path
    split: str
    holdout_fraction: float
    train_fraction: float
    seed: int
    tree_count: int
    max_rows: int | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit a RandomForest baseline on Phase 7 matchup features."
    )
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument(
        "--split",
        choices=("build", "opponent", "replicate", "component", "seed-cell", "forward-time"),
        default="build",
    )
    parser.add_argument("--holdout-fraction", type=float, default=DEFAULT_HOLDOUT_FRACTION)
    parser.add_argument("--train-fraction", type=float, default=DEFAULT_TRAIN_FRACTION)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--tree-count", type=int, default=DEFAULT_TREE_COUNT)
    parser.add_argument("--max-rows", type=int, default=None)
    return parser


def _split_rows(config: BaselineConfig) -> tuple[SplitIds, dict[str, object]]:
    build_lookup = {item.build_key: item.build for item in load_recovered_builds(config.db_path)}
    if config.split == "replicate":
        honest_rows = [
            row for row in load_honest_eval_matchups(config.db_path)
            if row.build_key is not None and row.build_key in build_lookup
        ]
        if config.max_rows is not None:
            honest_rows = honest_rows[:config.max_rows]
        return (
            held_out_replicate_split(
                honest_rows, config.holdout_fraction, config.seed
            ),
            build_lookup,
        )

    rows = list(load_training_matchups(config.db_path))
    if config.max_rows is not None:
        rows = rows[:config.max_rows]
    rows = [row for row in rows if row.build_key in build_lookup]
    if config.split == "build":
        split = held_out_build_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "opponent":
        split = held_out_opponent_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "replicate":
        split = held_out_replicate_split(rows, config.holdout_fraction, config.seed)
    elif config.split == "component":
        split = held_out_component_combination_split(
            rows, build_lookup, config.holdout_fraction, config.seed
        )
    elif config.split == "seed-cell":
        split = held_out_seed_cell_split(rows, config.holdout_fraction, config.seed)
    else:
        split = forward_time_split(rows, config.train_fraction)
    return split, build_lookup


def _feature_matrix(rows, build_lookup, config: BaselineConfig):
    game_data = load_game_data(config.game_dir)
    manifest = GameManifest.load(config.game_dir / "manifest.json")
    records = [
        matchup_feature_row(
            build_lookup[row.build_key],
            row.opponent_variant_id,
            config.game_dir,
            game_data,
            manifest,
        )
        for row in rows
    ]
    targets = np.asarray([row.target for row in rows], dtype=float)
    return records, targets


def _make_model(records: list[dict[str, object]], tree_count: int, seed: int) -> Pipeline:
    return Pipeline([
        ("features", DictVectorizer(sparse=True)),
        ("model", RandomForestRegressor(
            n_estimators=tree_count,
            random_state=seed,
            n_jobs=ALL_AVAILABLE_CORES,
            min_samples_leaf=DEFAULT_MIN_SAMPLES_LEAF,
        )),
    ])


def main() -> None:
    args = build_parser().parse_args()
    config = BaselineConfig(
        db_path=args.db_path,
        game_dir=args.game_dir,
        split=args.split,
        holdout_fraction=args.holdout_fraction,
        train_fraction=args.train_fraction,
        seed=args.seed,
        tree_count=args.tree_count,
        max_rows=args.max_rows,
    )
    split, build_lookup = _split_rows(config)
    if not split.train or not split.test:
        raise SystemExit("selected split produced an empty train or test partition")

    x_train, y_train = _feature_matrix(split.train, build_lookup, config)
    x_test, y_test = _feature_matrix(split.test, build_lookup, config)
    model = _make_model(x_train, config.tree_count, config.seed)
    model.fit(x_train, y_train)
    pred = model.predict(x_test)

    print(json.dumps({
        "split": config.split,
        "train_rows": len(split.train),
        "test_rows": len(split.test),
        "mae": float(mean_absolute_error(y_test, pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_test, pred))),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
