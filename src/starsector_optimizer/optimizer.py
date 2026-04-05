"""Optimizer — Optuna-based build optimization with heuristic warm-start.

Ask-tell loop with TPE sampler, WilcoxonPruner for per-opponent pruning,
Lamarckian repair recording, and hash-based deduplication.

See spec 24 for design rationale.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import optuna
from optuna.distributions import CategoricalDistribution, IntDistribution
from optuna.trial import TrialState, create_trial

from .calibration import generate_diverse_builds
from .instance_manager import InstancePool
from .models import Build, GameData, ShipHull
from .opponent_pool import (
    OpponentPool,
    compute_fitness,
    generate_matchups,
    get_opponents,
    hp_differential,
)
from .repair import repair_build
from .scorer import heuristic_score
from .search_space import SearchSpace, build_search_space
from .variant import generate_variant, write_variant_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizerConfig:
    """Configuration for an optimization run."""

    sim_budget: int = 200
    warm_start_n: int = 500
    warm_start_sample_n: int = 50_000
    warm_start_scale: float = 0.1
    n_startup_trials: int = 100
    n_ei_candidates: int = 256
    p_threshold: float = 0.1
    fitness_mode: str = "mean"
    study_storage: str | None = None


class BuildCache:
    """Hash-based deduplication cache for repaired builds."""

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}

    def hash_build(self, build: Build) -> str:
        """Stable hash from weapon assignments + hullmods + vents + caps."""
        parts = (
            repr(sorted(build.weapon_assignments.items())),
            repr(sorted(build.hullmods)),
            repr(build.flux_vents),
            repr(build.flux_capacitors),
        )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    def get(self, build: Build) -> float | None:
        return self._cache.get(self.hash_build(build))

    def put(self, build: Build, score: float) -> None:
        self._cache[self.hash_build(build)] = score


def define_distributions(space: SearchSpace) -> dict[str, optuna.distributions.BaseDistribution]:
    """Convert SearchSpace to Optuna distribution dict."""
    dists: dict[str, optuna.distributions.BaseDistribution] = {}

    for slot_id, options in space.weapon_options.items():
        dists[f"weapon_{slot_id}"] = CategoricalDistribution(options)

    for mod_id in space.eligible_hullmods:
        dists[f"hullmod_{mod_id}"] = CategoricalDistribution([True, False])

    dists["flux_vents"] = IntDistribution(0, space.max_vents)
    dists["flux_capacitors"] = IntDistribution(0, space.max_capacitors)

    return dists


def build_to_trial_params(build: Build, space: SearchSpace) -> dict:
    """Flatten a Build to an Optuna-style param dict."""
    params: dict = {}

    for slot_id in space.weapon_options:
        wid = build.weapon_assignments.get(slot_id)
        params[f"weapon_{slot_id}"] = wid if wid is not None else "empty"

    for mod_id in space.eligible_hullmods:
        params[f"hullmod_{mod_id}"] = mod_id in build.hullmods

    params["flux_vents"] = build.flux_vents
    params["flux_capacitors"] = build.flux_capacitors

    return params


def trial_params_to_build(params: dict, hull_id: str) -> Build:
    """Reconstruct a Build from an Optuna param dict."""
    weapons: dict[str, str | None] = {}
    hullmods: set[str] = set()

    for key, value in params.items():
        if key.startswith("weapon_"):
            slot_id = key[len("weapon_"):]
            weapons[slot_id] = None if value == "empty" else value
        elif key.startswith("hullmod_"):
            mod_id = key[len("hullmod_"):]
            if value is True:
                hullmods.add(mod_id)

    return Build(
        hull_id=hull_id,
        weapon_assignments=weapons,
        hullmods=frozenset(hullmods),
        flux_vents=params.get("flux_vents", 0),
        flux_capacitors=params.get("flux_capacitors", 0),
    )


def warm_start(
    study: optuna.Study,
    hull: ShipHull,
    game_data: GameData,
    config: OptimizerConfig,
) -> None:
    """Generate diverse builds, score with heuristic, seed the study."""
    space = build_search_space(hull, game_data)
    distributions = define_distributions(space)

    builds = generate_diverse_builds(hull, game_data, n=config.warm_start_sample_n)
    scored = [
        (b, heuristic_score(b, hull, game_data).composite_score)
        for b in builds
    ]
    scored.sort(key=lambda x: -x[1])
    top = scored[: config.warm_start_n]

    for build, score in top:
        trial = create_trial(
            params=build_to_trial_params(build, space),
            distributions=distributions,
            values=[score * config.warm_start_scale],
            state=TrialState.COMPLETE,
        )
        study.add_trial(trial)

    logger.info(
        "Warm-started study with %d heuristic trials (from %d candidates)",
        len(top),
        len(builds),
    )


def evaluate_build(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
    instance_pool: InstancePool,
    opponent_pool: OpponentPool,
    cache: BuildCache,
    config: OptimizerConfig,
    trial_number: int = 0,
    eval_log_path: Path | None = None,
) -> float:
    """Repair, deduplicate, evaluate against opponent pool, log, return fitness."""
    repaired = repair_build(build, hull, game_data)

    cached = cache.get(repaired)
    if cached is not None:
        logger.debug("Cache hit for trial %d", trial_number)
        return cached

    variant_id = f"{hull.id}_opt_{trial_number:06d}"
    variant = generate_variant(repaired, hull, game_data, variant_id=variant_id)
    instance_pool.write_variant_to_all(variant, f"{variant_id}.variant")

    opponents = get_opponents(opponent_pool, hull.hull_size)
    matchups = generate_matchups(
        variant_id,
        opponents,
        matchup_id_prefix=f"{hull.id}_{trial_number:06d}",
    )
    results = instance_pool.evaluate(matchups)
    fitness = compute_fitness(results, mode=config.fitness_mode)

    cache.put(repaired, fitness)

    if eval_log_path:
        _append_eval_log(
            eval_log_path, hull.id, trial_number, repaired, results, fitness
        )

    return fitness


def _append_eval_log(
    path: Path,
    hull_id: str,
    trial_number: int,
    build: Build,
    results: list,
    fitness: float,
) -> None:
    """Append one JSONL record to the evaluation log."""
    record = {
        "hull_id": hull_id,
        "trial_number": trial_number,
        "build": {
            "hull_id": build.hull_id,
            "weapon_assignments": {
                k: v for k, v in build.weapon_assignments.items()
            },
            "hullmods": sorted(build.hullmods),
            "flux_vents": build.flux_vents,
            "flux_capacitors": build.flux_capacitors,
        },
        "opponent_results": [
            {
                "opponent": r.matchup_id.split("_vs_")[-1] if "_vs_" in r.matchup_id else r.matchup_id,
                "winner": r.winner,
                "duration_seconds": r.duration_seconds,
                "hp_differential": hp_differential(r),
            }
            for r in results
        ],
        "fitness": fitness,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def optimize_hull(
    hull_id: str,
    game_data: GameData,
    instance_pool: InstancePool,
    opponent_pool: OpponentPool,
    config: OptimizerConfig,
    eval_log_path: Path | None = None,
) -> optuna.Study:
    """Main optimization entry point. Returns the Optuna study."""
    hull = game_data.hulls[hull_id]
    space = build_search_space(hull, game_data)
    distributions = define_distributions(space)

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = optuna.samplers.TPESampler(
        multivariate=True,
        constant_liar=True,
        n_ei_candidates=config.n_ei_candidates,
        n_startup_trials=config.n_startup_trials,
    )
    pruner = optuna.pruners.WilcoxonPruner(p_threshold=config.p_threshold)

    study = optuna.create_study(
        sampler=sampler,
        pruner=pruner,
        direction="maximize",
        storage=config.study_storage,
        study_name=hull_id,
        load_if_exists=True,
    )

    warm_start(study, hull, game_data, config)

    cache = BuildCache()
    for i in range(config.sim_budget):
        trial = study.ask(distributions)
        raw_build = trial_params_to_build(trial.params, hull_id)
        repaired = repair_build(raw_build, hull, game_data)

        score = evaluate_build(
            repaired,
            hull,
            game_data,
            instance_pool,
            opponent_pool,
            cache,
            config,
            trial_number=i,
            eval_log_path=eval_log_path,
        )

        # Lamarckian: record repaired params
        repaired_trial = create_trial(
            params=build_to_trial_params(repaired, space),
            distributions=distributions,
            values=[score],
            state=TrialState.COMPLETE,
        )
        study.add_trial(repaired_trial)

        if (i + 1) % 10 == 0:
            logger.info(
                "Trial %d/%d: fitness=%.3f, best=%.3f",
                i + 1,
                config.sim_budget,
                score,
                study.best_value,
            )

    return study
