"""Optimizer — Optuna-based build optimization with heuristic warm-start.

Ask-tell loop with TPE/CatCMAwM sampler, Baldwinian repair recording,
and hash-based deduplication.

See spec 24 for design rationale.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random as _random
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import optuna
from optuna.distributions import CategoricalDistribution, IntDistribution
from optuna.trial import TrialState, create_trial

from .calibration import generate_diverse_builds
from .instance_manager import InstanceError, InstancePool
from .models import Build, BuildSpec, CombatFitnessConfig, CombatResult, EBShrinkageConfig, EngineStats, GameData, HullSize, MatchupConfig, ScorerResult, ShipHull, TWFEConfig
from .combat_fitness import combat_fitness
from .deconfounding import ScoreMatrix, eb_shrinkage, triple_goal_rank
from .opponent_pool import (
    OpponentPool,
    get_opponents,
    hp_differential,
)
from .repair import repair_build
from .scorer import heuristic_score
from .search_space import SearchSpace, build_search_space
from .variant import build_to_build_spec

logger = logging.getLogger(__name__)

_EPSILON = 1e-12  # Guard for near-zero std in z-scoring and correlation estimation


@dataclass(frozen=True)
class OptimizerConfig:
    """Configuration for an optimization run."""

    sim_budget: int = 200
    warm_start_n: int = 500
    warm_start_sample_n: int = 50_000
    warm_start_scale: float = 0.1
    n_startup_trials: int = 100
    n_ei_candidates: int = 256
    combat_fitness: CombatFitnessConfig = field(default_factory=CombatFitnessConfig)
    twfe: TWFEConfig = field(default_factory=TWFEConfig)
    eb: EBShrinkageConfig = field(default_factory=EBShrinkageConfig)
    sampler: str = "tpe"
    fixed_params: dict[str, bool | int | str] | None = None
    study_storage: str | None = None
    wilcoxon_p_threshold: float = 0.1
    wilcoxon_n_startup_steps: int = 2
    matchup_time_limit: float = 300.0
    matchup_time_mult: float = 5.0
    log_interval: int = 10
    failure_score: float = -2.0
    stock_build_scale_mult: float = 2.0
    active_opponents: int = 10
    eval_log_path: Path | None = None


class BuildCache:
    """Hash-based deduplication cache for repaired builds."""

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}

    def hash_build(self, build: Build) -> str:
        """Stable hash from hull_id + weapon assignments + hullmods + vents + caps."""
        parts = (
            build.hull_id,
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
    game_dir = instance_pool.game_dir

    # Combat harness mod deployed
    mod_jar = game_dir / "mods" / "combat-harness" / "jars" / "combat-harness.jar"
    if not mod_jar.exists():
        raise ValueError(
            f"combat-harness mod not deployed at {mod_jar}. "
            f"Run: cd combat-harness && ./gradlew deploy"
        )

    # enabled_mods.json exists and contains combat_harness
    enabled_mods = game_dir / "mods" / "enabled_mods.json"
    if not enabled_mods.exists():
        raise ValueError(f"enabled_mods.json not found at {enabled_mods}")
    enabled_mods_data = json.loads(enabled_mods.read_text())
    if "combat_harness" not in enabled_mods_data.get("enabledMods", []):
        raise ValueError(
            f"combat_harness not found in enabledMods array in {enabled_mods}. "
            f"Enable it via the Starsector launcher."
        )

    # Opponent variants exist
    opponents = get_opponents(opponent_pool, hull.hull_size)
    variants_dir = game_dir / "data" / "variants"
    for opp_id in opponents:
        # Variants can be flat or in subdirectories
        found = list(variants_dir.rglob(f"{opp_id}.variant"))
        if not found:
            raise ValueError(
                f"Opponent variant '{opp_id}' not found under {variants_dir}. "
                f"Check opponent pool variant IDs."
            )

    # Xvfb and xdotool installed
    for tool in ("Xvfb", "xdotool"):
        if shutil.which(tool) is None:
            raise ValueError(f"'{tool}' not found on PATH. Install it first.")

    logger.info("Preflight check passed for %s (%d opponents)", hull_id, len(opponents))


def validate_build_spec(spec: BuildSpec, game_data: GameData) -> list[str]:
    """Validate a BuildSpec against game data. Returns error strings."""
    errors = []

    if spec.hull_id not in game_data.hulls:
        errors.append(f"Unknown hull: {spec.hull_id}")

    for mod_id in spec.hullmods:
        if mod_id not in game_data.hullmods:
            errors.append(f"Unknown hullmod: {mod_id}")

    for slot_id, weapon_id in spec.weapon_assignments.items():
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


def _create_pruner(config: OptimizerConfig) -> optuna.pruners.BasePruner:
    """Create WilcoxonPruner for between-opponent statistical pruning.

    WilcoxonPruner performs a signed-rank test between the current trial
    and the best trial, using per-opponent raw scores at stable step IDs.
    """
    return optuna.pruners.WilcoxonPruner(
        p_threshold=config.wilcoxon_p_threshold,
        n_startup_steps=config.wilcoxon_n_startup_steps,
    )


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
                    values=[config.warm_start_scale * config.stock_build_scale_mult],
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


@dataclass
class _InFlightBuild:
    """Tracks a build progressing through staged opponent evaluation.

    Mutable controller record — NOT a domain model. Design Principle 2
    immutability applies to frozen domain classes (`Build`, `EffectiveStats`,
    `ScorerResult`), not to transient per-build evaluation state.
    """

    trial: optuna.Trial
    build: Build
    build_spec: BuildSpec
    variant_id: str
    opponents: tuple[str, ...]
    scorer_result: ScorerResult  # full heuristic output; replaces heuristic_val
    engine_stats: EngineStats | None = None  # from first result with non-null
    completed_results: list[CombatResult] = field(default_factory=list)
    raw_scores: list[float] = field(default_factory=list)
    next_opponent_index: int = 0

    @property
    def rung(self) -> int:
        """ASHA rung = number of opponents already evaluated."""
        return self.next_opponent_index

    @property
    def is_complete(self) -> bool:
        return self.next_opponent_index >= len(self.opponents)


@dataclass(frozen=True)
class _EBRecord:
    """Narrow finalized-build record for Phase 5D EB shrinkage.

    Drops `completed_results`, `raw_scores`, `build_spec`, and the Optuna
    `trial` handle relative to `_InFlightBuild` (~10× memory reduction at
    2000-trial scale).
    """
    trial_number: int
    scorer_result: ScorerResult
    engine_stats: EngineStats | None


def _build_covariate_vector(record: _EBRecord) -> np.ndarray:
    """Assemble the 7-dim covariate vector for EB shrinkage (§2.7 order).

    Order:
        0. eff_max_flux          (engine, or Python fallback)
        1. eff_flux_dissipation  (engine, or Python fallback)
        2. eff_armor_rating      (engine, or Python fallback)
        3. total_weapon_dps      (raw unweighted sum from ScorerResult.total_dps)
        4. engagement_range      (DPS-weighted mean from ScorerResult)
        5. kinetic_dps_fraction  (kinetic_dps / max(total_dps, ε))
        6. composite_score       (calibrated heuristic scalar)
    """
    sr = record.scorer_result
    total_dps = sr.total_dps
    kin_frac = sr.kinetic_dps / max(total_dps, _EPSILON)
    if record.engine_stats is not None:
        es = record.engine_stats
        eff_flux, eff_diss, eff_arm = (
            es.eff_max_flux, es.eff_flux_dissipation, es.eff_armor_rating,
        )
    else:
        # Test-fixture / replay-only fallback: in deployed production runs,
        # Java always emits setup_stats so this branch never triggers.
        # Mixing Java- and Python-sourced rows in one X matrix introduces a
        # measurement-source confound; `eb_min_builds` gates early rows but
        # a long-lived miss (Java mod absent) would bias γ̂. Flagged at WARN.
        fallback = sr.effective_stats
        eff_flux, eff_diss, eff_arm = (
            fallback.flux_capacity,
            fallback.flux_dissipation,
            fallback.armor_rating,
        )
        import warnings as _w
        _w.warn(
            f"engine_stats missing for trial {record.trial_number}; "
            "using Python compute_effective_stats fallback (replay/test only)",
            UserWarning,
            stacklevel=2,
        )
    return np.array([
        eff_flux, eff_diss, eff_arm,
        total_dps, sr.engagement_range, kin_frac, sr.composite_score,
    ])


class StagedEvaluator:
    """Async ASHA-style staged evaluator with parallel instance dispatch.

    Coordinator-worker pattern: main thread owns all Optuna/optimizer state;
    worker threads (one per instance) do blocking I/O via run_matchup().
    All state mutations happen in the main thread between wait() calls.
    Pruning decisions are immediate after every opponent result.
    """

    def __init__(
        self,
        study: optuna.Study,
        hull: ShipHull,
        hull_id: str,
        game_data: GameData,
        instance_pool: InstancePool,
        opponent_pool: OpponentPool,
        cache: BuildCache,
        config: OptimizerConfig,
        distributions: dict[str, optuna.distributions.BaseDistribution],
        eval_log_path: Path | None = None,
    ) -> None:
        self._study = study
        self._hull = hull
        self._hull_id = hull_id
        self._game_data = game_data
        self._instance_pool = instance_pool
        self._cache = cache
        self._config = config
        self._distributions = distributions
        self._eval_log_path = eval_log_path
        self._opponents = get_opponents(opponent_pool, hull.hull_size)
        self._fitness_config = config.combat_fitness
        self._queue: list[_InFlightBuild] = []
        self._dispatched: set[int] = set()  # trial.number of builds on an instance
        self._trials_asked = 0
        self._trials_completed = 0
        # A1: TWFE score matrix — accumulates raw combat_fitness for decomposition
        self._score_matrix = ScoreMatrix()
        # Opponent selection state
        self._incumbent_opponents: tuple[str, ...] | None = None
        self._incumbent_fitness: float = float("-inf")
        self._anchors: tuple[str, ...] = ()
        self._burn_in_scores: dict[str, list[float]] = {}
        self._burn_in_fitness: list[float] = []
        self._builds_evaluated: int = 0
        # A2′: EB shrinkage — per-build covariate cache for every finalized build
        self._completed_records: dict[int, _EBRecord] = {}
        # A3: Completed fitness values for rank-based shaping
        self._completed_fitness_values: list[float] = []

    def run(self) -> None:
        """Execute staged evaluation with parallel instance dispatch.

        Coordinator pattern: main thread owns all Optuna/optimizer state.
        Worker threads (one per instance) only do blocking I/O via run_matchup().
        All state mutations (_queue, _dispatched, _score_matrix, etc.) happen
        in the main thread between wait() calls — no locks needed.
        """
        from collections import deque
        from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED

        num_inst = self._instance_pool.num_instances
        logger.info(
            "Starting staged evaluation: %d instances, %d active / %d total opponents",
            num_inst,
            min(self._config.active_opponents, len(self._opponents)),
            len(self._opponents),
        )

        with ThreadPoolExecutor(max_workers=num_inst) as executor:
            pending: dict[Future, tuple[int, _InFlightBuild]] = {}
            free_instances: deque[int] = deque(range(num_inst))

            self._fill_instances(executor, pending, free_instances)

            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)

                for future in done:
                    inst_id, ifb = pending.pop(future)
                    self._dispatched.discard(ifb.trial.number)

                    try:
                        result = future.result()
                    except InstanceError:
                        logger.error(
                            "Instance %d failed for trial %d, scoring as %s",
                            inst_id, ifb.trial.number,
                            self._config.failure_score,
                        )
                        self._study.tell(ifb.trial, self._config.failure_score)
                        self._trials_completed += 1
                        if ifb in self._queue:
                            self._queue.remove(ifb)
                        free_instances.append(inst_id)
                        continue

                    self._handle_result(ifb, result)
                    free_instances.append(inst_id)

                    if (self._trials_completed % self._config.log_interval == 0
                            or not pending):
                        best = (self._study.best_value
                                if self._study.best_trial else 0.0)
                        logger.info(
                            "Progress: %d/%d trials, best=%.3f, pending=%d",
                            self._trials_completed, self._config.sim_budget,
                            best, len(pending),
                        )

                # Dispatch new work to all freed instances
                self._fill_instances(executor, pending, free_instances)

    def _fill_instances(self, executor, pending, free_instances) -> None:
        """Dispatch matchups to all free instances."""
        while free_instances:
            work = self._next_matchup()
            if work is None:
                break
            ifb, matchup = work
            inst_id = free_instances.popleft()
            logger.info(
                "Dispatch trial %d rung %d/%d vs %s → instance %d",
                ifb.trial.number, ifb.rung + 1, len(ifb.opponents),
                ifb.opponents[ifb.next_opponent_index], inst_id,
            )
            future = executor.submit(
                self._instance_pool.run_matchup, inst_id, matchup,
            )
            pending[future] = (inst_id, ifb)

    def _next_matchup(self) -> tuple[_InFlightBuild, MatchupConfig] | None:
        """Pick the highest-priority matchup to dispatch next."""
        # Phase 1: Promote existing builds (highest rung first)
        for ifb in sorted(self._queue, key=lambda x: -x.rung):
            if ifb.trial.number not in self._dispatched:
                self._dispatched.add(ifb.trial.number)
                return ifb, self._make_matchup(ifb)

        # Phase 2: Ask for new trial
        while self._trials_asked < self._config.sim_budget:
            ifb = self._ask_new_trial()
            if ifb is None:
                continue  # cache hit, already told Optuna
            self._queue.append(ifb)
            self._dispatched.add(ifb.trial.number)
            return ifb, self._make_matchup(ifb)

        return None

    def _handle_result(self, ifb: _InFlightBuild, result: CombatResult) -> None:
        """Process one matchup result: record in score matrix, check prune/complete."""
        opp_id = ifb.opponents[ifb.next_opponent_index]
        raw = combat_fitness(result, config=self._fitness_config)
        self._score_matrix.record(ifb.trial.number, opp_id, raw)
        ifb.raw_scores.append(raw)
        ifb.completed_results.append(result)
        ifb.next_opponent_index += 1
        # Capture engine_stats from first non-null result (same player ship
        # across all opponents, so subsequent reads would be idempotent).
        if ifb.engine_stats is None and result.engine_stats is not None:
            ifb.engine_stats = result.engine_stats

        raw_mean = sum(ifb.raw_scores) / len(ifb.raw_scores)
        # Report raw score at rung position (0-based).  Every trial evaluates
        # the same number of opponents, so all trials share step IDs 0..N-1.
        # With anchor-first ordering (post-burn-in), the first rung positions
        # are the same high-discrimination opponents across trials, giving
        # WilcoxonPruner the strongest paired signal for early pruning.
        rung_step = ifb.rung - 1  # rung already incremented by next_opponent_index += 1
        ifb.trial.report(raw, step=rung_step)

        logger.info(
            "  Trial %d rung %d/%d vs %s: %s (raw=%.3f, mean=%.3f)",
            ifb.trial.number, ifb.rung, len(ifb.opponents),
            opp_id, result.winner, raw, raw_mean,
        )

        if ifb.is_complete:
            self._finalize_build(ifb)
            self._queue.remove(ifb)
            self._trials_completed += 1
        elif ifb.trial.should_prune():
            self._prune_build(ifb)
            self._queue.remove(ifb)
            self._trials_completed += 1
            logger.info("  Trial %d PRUNED at rung %d",
                        ifb.trial.number, ifb.rung)
        # else: stays in queue, _next_matchup will pick it up

    def _ask_new_trial(self) -> _InFlightBuild | None:
        """Ask Optuna for a new trial, repair, check cache.

        Returns None if the build was resolved immediately (cache hit or
        invalid spec) — the caller should not add it to the queue.
        """
        trial = self._study.ask(self._distributions)
        self._trials_asked += 1

        build = repair_build(
            trial_params_to_build(
                trial.params, self._hull_id,
                fixed_params=self._config.fixed_params,
            ),
            self._hull, self._game_data,
        )

        cached = self._cache.get(build)
        if cached is not None:
            logger.debug("Cache hit for trial %d", trial.number)
            self._study.tell(trial, cached)
            self._trials_completed += 1
            return None

        variant_id = f"{self._hull_id}_opt_{trial.number:06d}"
        build_spec = build_to_build_spec(
            build, self._hull, self._game_data, variant_id,
        )
        errors = validate_build_spec(build_spec, self._game_data)
        if errors:
            logger.warning("Invalid build spec %s: %s", variant_id, errors)
            self._study.tell(trial, self._config.failure_score)
            self._trials_completed += 1
            return None

        scorer_result = heuristic_score(build, self._hull, self._game_data)
        active = self._select_opponents(trial.number)
        return _InFlightBuild(
            trial=trial,
            build=build,
            build_spec=build_spec,
            variant_id=variant_id,
            opponents=tuple(active),
            scorer_result=scorer_result,
        )

    def _finalize_build(self, ifb: _InFlightBuild) -> None:
        """Compute final fitness via A1→A2′→A3 pipeline, tell Optuna, cache, log."""
        # A1: TWFE decomposition — schedule-adjusted build quality
        twfe_fitness = self._score_matrix.build_alpha(
            ifb.trial.number, self._config.twfe,
        )
        self._builds_evaluated += 1
        self._update_incumbent(ifb, twfe_fitness)
        self._update_burn_in(ifb, twfe_fitness)
        # Cache a narrow EB record BEFORE shrinkage so this build is in the fit.
        self._completed_records[ifb.trial.number] = _EBRecord(
            trial_number=ifb.trial.number,
            scorer_result=ifb.scorer_result,
            engine_stats=ifb.engine_stats,
        )
        # A2′: EB shrinkage + optional triple-goal rank correction
        eb_fitness = self._apply_eb_shrinkage(ifb.trial.number, twfe_fitness)
        # A3: rank-based shaping
        self._completed_fitness_values.append(eb_fitness)
        ranked_fitness = self._rank_fitness(eb_fitness)

        self._cache.put(ifb.build, ranked_fitness)
        self._study.tell(ifb.trial, ranked_fitness)
        logger.info(
            "  Trial %d COMPLETE (twfe=%.3f, eb=%.3f, ranked=%.3f)",
            ifb.trial.number, twfe_fitness, eb_fitness, ranked_fitness,
        )

        if self._eval_log_path:
            _append_eval_log(
                self._eval_log_path, self._hull_id, ifb.trial.number,
                ifb.build, ifb.completed_results, ranked_fitness,
                raw_fitness=eb_fitness,
                eb_fitness=eb_fitness,
                pruned=False, opponents_total=len(ifb.opponents),
                opponent_order=list(ifb.opponents),
            )

    def _apply_eb_shrinkage(
        self, trial_number: int, twfe_fitness: float,
    ) -> float:
        """A2′ — EB shrinkage of TWFE α̂ toward γ̂ᵀX regression prior.

        Returns raw α̂ unchanged when `score_matrix.n_builds < eb_min_builds`
        (stability guard: OLS fit on too few builds is unstable).
        """
        eb_cfg = self._config.eb
        if self._score_matrix.n_builds < eb_cfg.eb_min_builds:
            return twfe_fitness

        indices: list[int] = list(self._completed_records.keys())
        alphas = np.array([
            self._score_matrix.build_alpha(i, self._config.twfe) for i in indices
        ])
        sigma_sqs = np.array(
            [self._score_matrix.build_sigma_sq(i) for i in indices]
        )
        X = np.vstack([
            _build_covariate_vector(self._completed_records[i]) for i in indices
        ])
        alpha_eb, _, _, _ = eb_shrinkage(alphas, sigma_sqs, X, eb_cfg)
        if eb_cfg.triple_goal:
            alpha_out = triple_goal_rank(alpha_eb, alphas)
        else:
            alpha_out = alpha_eb
        return float(alpha_out[indices.index(trial_number)])

    def _prune_build(self, ifb: _InFlightBuild) -> None:
        """Tell Optuna PRUNED, log partial results."""
        self._study.tell(ifb.trial, state=TrialState.PRUNED)

        if self._eval_log_path:
            raw_mean = (
                sum(ifb.raw_scores) / len(ifb.raw_scores)
                if ifb.raw_scores else 0.0
            )
            _append_eval_log(
                self._eval_log_path, self._hull_id, ifb.trial.number,
                ifb.build, ifb.completed_results, raw_mean,
                pruned=True, opponents_total=len(ifb.opponents),
                opponent_order=list(ifb.opponents),
            )

    def _select_opponents(self, trial_number: int) -> list[str]:
        """Select and order opponents for a new trial.

        Uses incumbent overlap + anchor-first ordering after burn-in.
        Before burn-in: random selection and order.
        """
        active_size = min(self._config.active_opponents, len(self._opponents))
        rng = _random.Random(trial_number)
        twfe_cfg = self._config.twfe

        if self._anchors and self._incumbent_opponents is not None:
            # Post-burn-in: force anchors + incumbent overlap, fill remainder
            forced_set: set[str] = set()
            forced: list[str] = []
            # 1. Anchors always included (high-discrimination, stable step IDs)
            for a in self._anchors:
                if len(forced) < active_size:
                    forced.append(a)
                    forced_set.add(a)
            # 2. Incumbent overlap (SMAC-style direct comparability)
            inc_pool = [o for o in self._incumbent_opponents if o not in forced_set]
            rng.shuffle(inc_pool)
            n_overlap = min(twfe_cfg.n_incumbent_overlap, len(inc_pool),
                            active_size - len(forced))
            for o in inc_pool[:n_overlap]:
                forced.append(o)
                forced_set.add(o)
            # 3. Fill remainder from full pool
            remaining = [o for o in self._opponents if o not in forced_set]
            rng.shuffle(remaining)
            active = forced + remaining[:active_size - len(forced)]
            # Anchors are already at the front; shuffle only the non-anchor tail
            rest_part = active[len(self._anchors):]
            rng.shuffle(rest_part)
            active = list(self._anchors) + rest_part
        else:
            # Pre-burn-in: fixed opponent set for all trials.
            # Same 10 opponents every time ensures WilcoxonPruner has full
            # step-ID overlap for paired comparisons from the very first trial.
            fixed_rng = _random.Random(0)
            pool = list(self._opponents)
            fixed_rng.shuffle(pool)
            active = pool[:active_size]
            rng.shuffle(active)

        return active

    def _update_incumbent(
        self, ifb: _InFlightBuild, twfe_fitness: float,
    ) -> None:
        """Track the best build's opponents for forced overlap."""
        if twfe_fitness > self._incumbent_fitness:
            self._incumbent_fitness = twfe_fitness
            self._incumbent_opponents = ifb.opponents

    def _update_burn_in(
        self, ifb: _InFlightBuild, twfe_fitness: float,
    ) -> None:
        """Accumulate burn-in data; lock anchors after threshold."""
        twfe_cfg = self._config.twfe
        if self._anchors:
            return  # anchors already locked

        build_idx = self._builds_evaluated - 1  # 0-indexed
        for opp_id, raw in zip(ifb.opponents, ifb.raw_scores):
            self._burn_in_scores.setdefault(opp_id, []).append((build_idx, raw))
        self._burn_in_fitness.append((build_idx, twfe_fitness))

        if self._builds_evaluated >= twfe_cfg.anchor_burn_in:
            self._compute_anchors()
            # Clear burn-in state (no longer needed)
            self._burn_in_scores.clear()
            self._burn_in_fitness.clear()

    def _compute_anchors(self) -> None:
        """Compute discriminative power per opponent and lock top-N as anchors."""
        from scipy.stats import spearmanr

        twfe_cfg = self._config.twfe
        # Index burn-in fitness by build_idx for alignment
        fitness_by_build = dict(self._burn_in_fitness)

        disc: dict[str, float] = {}
        for opp_id, indexed_scores in self._burn_in_scores.items():
            if len(indexed_scores) < twfe_cfg.min_disc_samples:
                disc[opp_id] = 0.0
                continue
            # Align: only use builds where both opp score and fitness exist
            opp_vals = []
            fitness_vals = []
            for build_idx, raw in indexed_scores:
                if build_idx in fitness_by_build:
                    opp_vals.append(raw)
                    fitness_vals.append(fitness_by_build[build_idx])
            if len(opp_vals) < twfe_cfg.min_disc_samples:
                disc[opp_id] = 0.0
                continue
            corr, _ = spearmanr(opp_vals, fitness_vals)
            disc[opp_id] = abs(corr) if not (corr != corr) else 0.0  # NaN check

        sorted_opps = sorted(disc.keys(), key=lambda o: disc[o], reverse=True)
        self._anchors = tuple(sorted_opps[:twfe_cfg.n_anchors])
        logger.info(
            "Locked %d anchors after %d builds: %s",
            len(self._anchors), self._builds_evaluated, self._anchors,
        )

    def _make_matchup(self, ifb: _InFlightBuild) -> MatchupConfig:
        """Create one matchup for the next opponent."""
        opp = ifb.opponents[ifb.next_opponent_index]
        matchup_id = f"{ifb.variant_id}_vs_{opp}"
        return MatchupConfig(
            matchup_id=matchup_id,
            player_builds=(ifb.build_spec,),
            enemy_variants=(opp,),
            time_limit_seconds=self._config.matchup_time_limit,
            time_mult=self._config.matchup_time_mult,
        )

    def _rank_fitness(self, fitness: float) -> float:
        """A3: Convert fitness to quantile rank in [0, 1]."""
        n = len(self._completed_fitness_values)
        if n <= 1:
            return 0.5
        rank = sum(1 for v in self._completed_fitness_values if v <= fitness)
        return rank / n


def _append_eval_log(
    path: Path,
    hull_id: str,
    trial_number: int,
    build: Build,
    results: list[CombatResult],
    fitness: float,
    *,
    raw_fitness: float | None = None,
    eb_fitness: float | None = None,
    pruned: bool = False,
    opponents_total: int = 0,
    opponent_order: list[str] | None = None,
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
        "opponents_evaluated": len(results),
        "opponents_total": opponents_total,
        "opponent_order": opponent_order or [],
        "pruned": pruned,
        "raw_fitness": raw_fitness if raw_fitness is not None else fitness,
        "fitness": fitness,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if eb_fitness is not None:
        record["eb_fitness"] = eb_fitness
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def optimize_hull(
    hull_id: str,
    game_data: GameData,
    instance_pool: InstancePool,
    opponent_pool: OpponentPool,
    config: OptimizerConfig,
) -> optuna.Study:
    """Main optimization entry point. Returns the Optuna study."""
    preflight_check(hull_id, game_data, instance_pool, opponent_pool)
    hull = game_data.hulls[hull_id]
    space = build_search_space(hull, game_data)
    distributions = define_distributions(space, fixed_params=config.fixed_params)

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = _create_sampler(config)
    opponents = get_opponents(opponent_pool, hull.hull_size)
    n_active = min(config.active_opponents, len(opponents))
    pruner = _create_pruner(config)

    study = optuna.create_study(
        sampler=sampler,
        pruner=pruner,
        direction="maximize",
        storage=config.study_storage,
        study_name=hull_id,
        load_if_exists=True,
    )

    warm_start(study, hull, game_data, config, game_dir=instance_pool.game_dir)

    evaluator = StagedEvaluator(
        study=study,
        hull=hull,
        hull_id=hull_id,
        game_data=game_data,
        instance_pool=instance_pool,
        opponent_pool=opponent_pool,
        cache=BuildCache(),
        config=config,
        distributions=distributions,
        eval_log_path=config.eval_log_path,
    )
    evaluator.run()

    return study
