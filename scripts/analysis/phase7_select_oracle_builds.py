#!/usr/bin/env python3
"""Rank-stratified oracle-coverage build selector (roadmap item 3, Tier 2).

Offline, no sim spend. Implements the deterministic selection rule predeclared
in docs/reports/2026-07-14-accounting-stream-preregistration.md (entry 2) and
plan .claude/plans/active/2026-07-16-oracle-coverage-selection.md:

Per hammerhead cell (campaign=accounting-hammerhead, one seed = one cell):
  1. Fit the CatBoost opponent-adjusted arm (catboost_regressor,
     DEFAULT_HYPERPARAMETERS, hpo_seed=23, thread_count=1) in-sample on ALL of
     that cell's training_matchups rows (finalized + pruned; no row-kind filter).
  2. Score each DISTINCT build_key by the mean predicted target over that build's
     matchup rows (opponent-panel average).
  3. Sort distinct builds by (predicted_score, build_key) ascending → predicted
     rank; np.array_split the rank-ordered list into 3 near-equal contiguous
     groups = bottom/middle/top third by predicted rank (the SOLE strata rule).
  4. Pick the build at the median predicted score of each stratum (lower-middle
     index on even counts). Deterministic, no RNG.
  → 3 builds/cell × 9 cells = 27 oracle-covered builds.

The output JSON is consumed by `honest_evaluator --builds-file` (the oracle pass)
and by `phase7_materialize_matchups.py --honest-selector-json` (the build_id →
build_key join for the replay).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from starsector_optimizer.models import Build
from starsector_optimizer.phase7_matchup_data import (
    TrainingMatchupRow,
    canonical_build_dict,
    load_recovered_builds,
    load_training_matchups,
)


def _load_sibling_module(name: str, filename: str) -> Any:
    """Load an analysis sibling module by path (mirrors
    phase7_prequential_replay.py:52-71 so the CatBoost arm + featurizer are the
    exact same code the replay uses)."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    if sys.modules[name] is module:
        spec.loader.exec_module(module)
    else:  # pragma: no cover - loaded first by another importer
        module = sys.modules[name]
    return module


baseline = _load_sibling_module("_phase7_baseline_surrogate", "phase7_baseline_surrogate.py")
learned = _load_sibling_module(
    "_phase7_learned_surrogate_experiment", "phase7_learned_surrogate_experiment.py"
)
_replay = _load_sibling_module("_phase7_prequential_replay", "phase7_prequential_replay.py")

# --- named constants (no magic numbers in bodies) ---------------------------
TERTILE_COUNT = 3
ORACLE_ARM = "catboost_regressor"
SELECTOR_HPO_SEED = learned.DEFAULT_HPO_SEED  # 23
SELECTOR_THREAD_COUNT = 1  # fit determinism (with random_seed=23)
SELECTOR_STUDY_IDX = 0
ORACLE_SELECTION_SCHEMA_VERSION = 1
DEFAULT_CAMPAIGN = "accounting-hammerhead"
DEFAULT_SEEDS = tuple(range(100, 109))
# stratum name → honest-eval source_rank ordinal (unique per cell → unique
# honest _build_id across the 27).
STRATUM_ORDINALS: dict[str, int] = {"bottom": 1, "middle": 2, "top": 3}
_STRATA = ("bottom", "middle", "top")


@dataclass(frozen=True)
class ScoreInfo:
    predicted_score: float
    trial_number: int
    build: Build


@dataclass(frozen=True)
class SelectedBuild:
    source_campaign: str
    source_study_idx: int
    source_seed_idx: int
    source_rank: int
    stratum: str
    predicted_score: float
    predicted_rank_in_cell: int
    build_key: str
    trial_number: int
    build: Build


def _median_index(n: int) -> int:
    """Index of the median element of a length-`n` sorted stratum, taking the
    lower-middle on even counts (deterministic). Odd n → n//2; even n → n//2-1."""
    return n // 2 if n % 2 else n // 2 - 1


def _stratify_indices(n: int) -> list[list[int]]:
    """Split `range(n)` into TERTILE_COUNT near-equal contiguous groups via
    numpy.array_split (rank tertiles). Raises ValueError if n < TERTILE_COUNT
    (a cell with too few distinct builds is a data defect, not a degrade case)."""
    if n < TERTILE_COUNT:
        raise ValueError(f"cell has {n} distinct builds, need >= {TERTILE_COUNT} for tertiles")
    # int(i) so downstream indices / ranks are native Python ints (np.int64 is
    # not JSON-serializable and would break the output payload).
    return [[int(i) for i in group] for group in np.array_split(np.arange(n), TERTILE_COUNT)]


def rank_stratify_pick(
    scored: dict[str, ScoreInfo], *, campaign: str, seed: int
) -> list[SelectedBuild]:
    """Pure core: given {build_key: ScoreInfo} for one cell's distinct builds,
    rank ascending by (predicted_score, build_key), tertile, and pick the
    median-of-stratum build. Returns exactly TERTILE_COUNT SelectedBuilds."""
    ranked = sorted(scored.items(), key=lambda kv: (kv[1].predicted_score, kv[0]))
    groups = _stratify_indices(len(ranked))
    selected: list[SelectedBuild] = []
    for stratum, group in zip(_STRATA, groups, strict=True):
        pick_idx = group[_median_index(len(group))]
        build_key, info = ranked[pick_idx]
        selected.append(
            SelectedBuild(
                source_campaign=campaign,
                source_study_idx=SELECTOR_STUDY_IDX,
                source_seed_idx=seed,
                source_rank=STRATUM_ORDINALS[stratum],
                stratum=stratum,
                predicted_score=info.predicted_score,
                predicted_rank_in_cell=pick_idx + 1,
                build_key=build_key,
                trial_number=info.trial_number,
                build=info.build,
            )
        )
    return selected


def _baseline_config(frozen_db: Path, game_dir: Path) -> Any:
    """BaselineConfig for _feature_bundle. Only game_dir + feature_profile are
    consumed by the featurizer; the rest are required dataclass fields set to
    explicit inert values."""
    return baseline.BaselineConfig(
        db_path=frozen_db,
        game_dir=game_dir,
        split="train",  # inert here — _feature_bundle reads only game_dir + feature_profile
        model=ORACLE_ARM,  # inert
        holdout_fraction=0.0,  # inert
        train_fraction=1.0,  # inert
        seed=SELECTOR_HPO_SEED,  # inert
        tree_count=0,  # inert
        ridge_alpha=0.0,  # inert
        max_rows=None,  # inert
        top_k_values=(),  # inert
        progress=False,  # inert
        feature_profile=baseline.DEFAULT_FEATURE_PROFILE,
    )


def predicted_scores_for_cell(
    matchup_rows: list[TrainingMatchupRow],
    build_lookup: dict[str, Build],
    baseline_config: Any,
) -> dict[str, tuple[float, int]]:
    """Fit the CatBoost arm in-sample on all `matchup_rows`, predict per row, and
    return {build_key: (mean_predicted_target, min_trial_number)} over the cell's
    distinct builds (the opponent-panel average per build)."""
    bundle = baseline._feature_bundle(matchup_rows, build_lookup, baseline_config)
    model = learned.make_model(
        ORACLE_ARM,
        learned.DEFAULT_HYPERPARAMETERS[ORACLE_ARM],
        SELECTOR_HPO_SEED,
        model_thread_count=SELECTOR_THREAD_COUNT,
    )
    model.fit(bundle.rows, bundle.records, bundle.targets)
    _replay.force_deterministic_predict(model)
    predictions = model.predict(bundle.rows, bundle.records).predictions
    sums: dict[str, list[float]] = {}
    trials: dict[str, int] = {}
    for row, value in zip(bundle.rows, predictions, strict=True):
        sums.setdefault(row.build_key, []).append(float(value))
        prev = trials.get(row.build_key)
        trials[row.build_key] = row.trial_number if prev is None else min(prev, row.trial_number)
    return {bk: (float(np.mean(values)), trials[bk]) for bk, values in sums.items()}


def select_cell(
    matchup_rows: list[TrainingMatchupRow],
    build_lookup: dict[str, Build],
    baseline_config: Any,
    *,
    campaign: str,
    seed: int,
) -> list[SelectedBuild]:
    """Full per-cell selection: predicted scores → rank-stratify-pick."""
    scores = predicted_scores_for_cell(matchup_rows, build_lookup, baseline_config)
    scored = {
        bk: ScoreInfo(predicted_score=score, trial_number=trial, build=build_lookup[bk])
        for bk, (score, trial) in scores.items()
    }
    return rank_stratify_pick(scored, campaign=campaign, seed=seed)


def select_oracle_builds(
    *,
    frozen_db: Path,
    game_dir: Path,
    campaign: str,
    seeds: tuple[int, ...],
    prereg_commit: str,
) -> dict[str, Any]:
    """Select the rank-stratified oracle subset across all cells and assemble the
    output JSON payload."""
    recovered = load_recovered_builds(frozen_db)
    matchups = load_training_matchups(frozen_db)
    build_lookup: dict[str, Build] = {rb.build_key: rb.build for rb in recovered}
    baseline_config = _baseline_config(frozen_db, game_dir)

    builds_out: list[dict[str, Any]] = []
    tertile_sizes: dict[str, list[int]] = {}
    for seed in seeds:
        cell_rows = [r for r in matchups if r.campaign == campaign and r.seed == seed]
        if not cell_rows:
            raise ValueError(f"no training_matchups rows for {campaign} seed {seed}")
        selected = select_cell(
            cell_rows, build_lookup, baseline_config, campaign=campaign, seed=seed
        )
        n_distinct = len({r.build_key for r in cell_rows})
        tertile_sizes[str(seed)] = [len(g) for g in _stratify_indices(n_distinct)]
        for sb in selected:
            builds_out.append(
                {
                    "source_campaign": sb.source_campaign,
                    "source_study_idx": sb.source_study_idx,
                    "source_seed_idx": sb.source_seed_idx,
                    "source_rank": sb.source_rank,
                    "stratum": sb.stratum,
                    "predicted_score": sb.predicted_score,
                    "predicted_rank_in_cell": sb.predicted_rank_in_cell,
                    "build_key": sb.build_key,
                    "trial_number": sb.trial_number,
                    "build": canonical_build_dict(sb.build),
                }
            )
    return {
        "schema_version": ORACLE_SELECTION_SCHEMA_VERSION,
        "selector": f"{ORACLE_ARM}_opponent_adjusted",
        "hpo_seed": SELECTOR_HPO_SEED,
        "thread_count": SELECTOR_THREAD_COUNT,
        "tertile_rule": "numpy.array_split rank tertiles (median-of-stratum pick)",
        "source_frozen_db": str(frozen_db),
        "prereg_commit": prereg_commit,
        "campaign": campaign,
        "seeds": list(seeds),
        "tertile_sizes": tertile_sizes,
        "builds": builds_out,
    }


def _parse_seeds(raw: str) -> tuple[int, ...]:
    return tuple(int(s) for s in raw.split(",") if s.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-db", type=Path, required=True, help="Frozen matchup SQLite DB.")
    parser.add_argument("--game-dir", type=Path, default=Path("game/starsector"))
    parser.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    parser.add_argument(
        "--seeds",
        type=_parse_seeds,
        default=DEFAULT_SEEDS,
        help="Comma-separated cell seeds (default 100-108).",
    )
    parser.add_argument(
        "--prereg-commit",
        required=True,
        help="Git commit hash of the committed pre-registration ledger (entry 2).",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output JSON path.")
    args = parser.parse_args(argv)

    payload = select_oracle_builds(
        frozen_db=args.frozen_db,
        game_dir=args.game_dir,
        campaign=args.campaign,
        seeds=tuple(args.seeds),
        prereg_commit=args.prereg_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    n = len(payload["builds"])
    print(f"selected {n} oracle builds ({len(args.seeds)} cells) → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
