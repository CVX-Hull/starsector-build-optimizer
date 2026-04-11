"""Optimizer — Optuna-based build optimization with heuristic warm-start.

Ask-tell loop with TPE/CatCMAwM sampler, Baldwinian repair recording,
and hash-based deduplication.

See spec 24 for design rationale.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import optuna
from optuna.distributions import CategoricalDistribution, IntDistribution
from optuna.trial import TrialState, create_trial

from .calibration import generate_diverse_builds
from .instance_manager import InstanceError, InstancePool
from .models import Build, CombatFitnessConfig, GameData, HullSize, ShipHull
from .combat_fitness import aggregate_combat_fitness
from .opponent_pool import (
    OpponentPool,
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
    fitness_mode: str = "mean"
    eval_batch_size: int = 8  # builds per batch; set to num_instances for full utilization
    engagement_threshold: float = 500.0
    sampler: str = "tpe"
    fixed_params: dict[str, bool | int | str] | None = None
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


def preflight_check(
    hull_id: str,
    game_data: GameData,
    instance_pool: InstancePool,
    opponent_pool: OpponentPool,
) -> None:
    """Validate all prerequisites before launching expensive simulation.

    Runs in <1 second. Raises ValueError with descriptive message on failure.
    """
    # Hull exists
    if hull_id not in game_data.hulls:
        raise ValueError(f"Hull '{hull_id}' not found in game data. "
                         f"Available: {sorted(list(game_data.hulls.keys())[:10])}...")

    hull = game_data.hulls[hull_id]
    game_dir = instance_pool._config.game_dir

    # Combat harness mod deployed
    mod_jar = game_dir / "mods" / "combat-harness" / "jars" / "combat-harness.jar"
    if not mod_jar.exists():
        raise ValueError(
            f"combat-harness mod not deployed at {mod_jar}. "
            f"Run: cd combat-harness && ./gradlew deploy"
        )

    # enabled_mods.json exists
    enabled_mods = game_dir / "mods" / "enabled_mods.json"
    if not enabled_mods.exists():
        raise ValueError(f"enabled_mods.json not found at {enabled_mods}")

    # Opponent variants exist
    opponents = get_opponents(opponent_pool, hull.hull_size)
    variants_dir = game_dir / "data" / "variants"
    for opp_id in opponents:
        # Variants can be flat or in subdirectories
        found = list(variants_dir.rglob(f"{opp_id}.variant"))
        if not found:
            raise ValueError(
                f"Opponent variant '{opp_id}' not found under {variants_dir}. "
                f"Check DEFAULT_OPPONENT_POOL variant IDs."
            )

    # Xvfb and xdotool installed
    for tool in ("Xvfb", "xdotool"):
        if shutil.which(tool) is None:
            raise ValueError(f"'{tool}' not found on PATH. Install it first.")

    logger.info("Preflight check passed for %s (%d opponents)", hull_id, len(opponents))


def validate_variant(variant: dict, game_data: GameData) -> list[str]:
    """Validate a generated variant dict against game data. Returns error strings."""
    errors = []

    hull_id = variant.get("hullId", "")
    if hull_id and hull_id not in game_data.hulls:
        errors.append(f"Unknown hull: {hull_id}")

    for mod_id in variant.get("hullMods", []):
        if mod_id not in game_data.hullmods:
            errors.append(f"Unknown hullmod: {mod_id}")

    for group in variant.get("weaponGroups", []):
        for slot_id, weapon_id in group.get("weapons", {}).items():
            if weapon_id not in game_data.weapons:
                errors.append(f"Unknown weapon: {weapon_id} in slot {slot_id}")

    return errors


def _create_sampler(config: OptimizerConfig) -> optuna.samplers.BaseSampler:
    """Create Optuna sampler instance from config."""
    if config.sampler == "tpe":
        return optuna.samplers.TPESampler(
            multivariate=True,
            constant_liar=True,
            n_ei_candidates=config.n_ei_candidates,
            n_startup_trials=config.n_startup_trials,
        )
    if config.sampler == "catcma":
        import optunahub

        mod = optunahub.load_module("samplers/catcmawm")
        return mod.CatCmawmSampler()
    raise ValueError(f"Unknown sampler: {config.sampler!r}")


def define_distributions(
    space: SearchSpace,
    fixed_params: dict[str, bool | int | str] | None = None,
) -> dict[str, optuna.distributions.BaseDistribution]:
    """Convert SearchSpace to Optuna distribution dict.

    When fixed_params is provided, those parameters are excluded from
    distributions — they are not suggested by the sampler.
    """
    dists: dict[str, optuna.distributions.BaseDistribution] = {}

    for slot_id, options in space.weapon_options.items():
        dists[f"weapon_{slot_id}"] = CategoricalDistribution(options)

    for mod_id in space.eligible_hullmods:
        dists[f"hullmod_{mod_id}"] = CategoricalDistribution([True, False])

    dists["flux_vents"] = IntDistribution(0, space.max_vents)
    dists["flux_capacitors"] = IntDistribution(0, space.max_capacitors)

    if fixed_params:
        dists = {k: v for k, v in dists.items() if k not in fixed_params}

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


def trial_params_to_build(
    params: dict,
    hull_id: str,
    fixed_params: dict[str, bool | int | str] | None = None,
) -> Build:
    """Reconstruct a Build from an Optuna param dict.

    When fixed_params is provided, those values are merged into params
    before reconstruction — fixed values always override sampler values.
    """
    if fixed_params:
        params = {**params, **fixed_params}

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
    game_dir: Path | None = None,
) -> None:
    """Seed the study with stock builds and top heuristic builds."""
    space = build_search_space(hull, game_data)
    distributions = define_distributions(space)
    stock_count = 0

    # Phase 1: Seed with stock builds (known-good, from game's .variant files)
    if game_dir is not None:
        from .variant import load_stock_builds
        stock_builds = load_stock_builds(game_dir, hull.id)
        for build in stock_builds:
            try:
                trial = create_trial(
                    params=build_to_trial_params(build, space),
                    distributions=distributions,
                    values=[config.warm_start_scale * 2.0],
                    state=TrialState.COMPLETE,
                )
                study.add_trial(trial)
                stock_count += 1
            except Exception:
                pass  # Stock build may not fit distributions exactly

    # Phase 2: Seed with top heuristic builds (diverse random)
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
        "Warm-started study with %d stock + %d heuristic trials (from %d candidates)",
        stock_count,
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
    errors = validate_variant(variant, game_data)
    if errors:
        logger.warning("Invalid variant %s: %s", variant_id, errors)
        return -1.0
    instance_pool.write_variant_to_all(variant, f"{variant_id}.variant")

    opponents = get_opponents(opponent_pool, hull.hull_size)
    matchups = generate_matchups(
        variant_id,
        opponents,
        matchup_id_prefix=f"{hull.id}_{trial_number:06d}",
    )
    results = instance_pool.evaluate(matchups)
    fitness = aggregate_combat_fitness(results, mode=config.fitness_mode, config=CombatFitnessConfig(engagement_threshold=config.engagement_threshold))

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
    preflight_check(hull_id, game_data, instance_pool, opponent_pool)
    hull = game_data.hulls[hull_id]
    space = build_search_space(hull, game_data)
    distributions = define_distributions(space, fixed_params=config.fixed_params)

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = _create_sampler(config)

    study = optuna.create_study(
        sampler=sampler,
        direction="maximize",
        storage=config.study_storage,
        study_name=hull_id,
        load_if_exists=True,
    )

    warm_start(study, hull, game_data, config, game_dir=instance_pool._config.game_dir)

    opponents = get_opponents(opponent_pool, hull.hull_size)
    cache = BuildCache()
    completed = 0

    while completed < config.sim_budget:
        batch_size = min(config.eval_batch_size, config.sim_budget - completed)

        # Ask batch_size trials (constant_liar handles pending trials)
        trials = [study.ask(distributions) for _ in range(batch_size)]
        builds = [
            repair_build(trial_params_to_build(t.params, hull_id, fixed_params=config.fixed_params), hull, game_data)
            for t in trials
        ]

        # Check cache, collect matchups for uncached builds
        all_matchups = []
        variant_map: dict[str, tuple[int, object, Build]] = {}
        for j, (trial, build) in enumerate(zip(trials, builds)):
            cached = cache.get(build)
            if cached is not None:
                study.tell(trial, cached)
                continue
            trial_num = completed + j
            vid = f"{hull_id}_opt_{trial_num:06d}"
            variant = generate_variant(build, hull, game_data, variant_id=vid)
            errors = validate_variant(variant, game_data)
            if errors:
                logger.warning("Invalid variant %s: %s", vid, errors)
                study.tell(trial, -1.0)
                continue
            instance_pool.write_variant_to_all(variant, f"{vid}.variant")
            matchups = generate_matchups(
                vid, opponents,
                matchup_id_prefix=f"{hull_id}_{trial_num:06d}",
            )
            all_matchups.extend(matchups)
            variant_map[vid] = (j, trial, build)

        # Evaluate all uncached matchups in one batch
        if all_matchups:
            try:
                results = instance_pool.evaluate(all_matchups)
                for vid, (j, trial, build) in variant_map.items():
                    prefix = vid.replace(f"{hull_id}_opt_", f"{hull_id}_")
                    build_results = [
                        r for r in results if r.matchup_id.startswith(prefix)
                    ]
                    fitness = (
                        aggregate_combat_fitness(build_results, mode=config.fitness_mode, config=CombatFitnessConfig(engagement_threshold=config.engagement_threshold))
                        if build_results
                        else -1.0
                    )
                    cache.put(build, fitness)
                    study.tell(trial, fitness)

                    if eval_log_path:
                        _append_eval_log(
                            eval_log_path, hull_id, completed + j,
                            build, build_results, fitness,
                        )
            except InstanceError:
                logger.error(
                    "Instance failure at trial %d, scoring batch as -1.0", completed
                )
                for vid, (j, trial, build) in variant_map.items():
                    study.tell(trial, -1.0)

        # Clean up optimizer variants to prevent accumulation slowing game startup
        instance_pool.clean_optimizer_variants()

        completed += batch_size

        if completed % 10 == 0 or completed >= config.sim_budget:
            best = study.best_value if study.best_trial else 0.0
            logger.info("Progress: %d/%d trials, best=%.3f",
                        completed, config.sim_budget, best)

    return study
