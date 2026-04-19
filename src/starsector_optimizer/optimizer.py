"""Optimizer — Optuna-based build optimization with heuristic warm-start.

Ask-tell loop with TPE sampler, Baldwinian repair recording, and hash-based
deduplication. CatCMAwM was removed 2026-04-19 — the library requires a
non-empty continuous-variable space, and the Starsector search space is
fully categorical (weapon slot choices + hullmod booleans + two
IntDistribution counts for vents/caps). Any hull/regime trips
`x_space must be a two-dimensional array with shape (n, 2), but got (0,)`.
With CatCMAwM gone there's no benchmark comparison to make, so `random`
was removed too — TPE is the sole supported sampler on this codebase.
Phase 7 replaces the Optuna sampler entirely with a BoTorch composed
GP — see docs/reference/phase7-search-space-compression.md.

See spec 24 for design rationale.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random as _random
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import optuna
from optuna.distributions import CategoricalDistribution, IntDistribution
from optuna.trial import TrialState, create_trial
from scipy.stats import boxcox

from .calibration import generate_diverse_builds
from .evaluator_pool import EvaluatorPool
from .instance_manager import InstanceError
from .models import (
    Build, BuildSpec, CombatFitnessConfig, CombatResult, EBShrinkageConfig,
    EngineStats, GameData, HullSize, MatchupConfig,
    REGIME_EARLY, REGIME_PRESETS, RegimeConfig,
    ScorerResult, ShapeConfig, ShipHull, TWFEConfig,
)
from .combat_fitness import combat_fitness
from .deconfounding import ScoreMatrix, eb_shrinkage, triple_goal_rank
from .opponent_pool import (
    OpponentPool,
    get_opponents,
    hp_differential,
)
from .repair import is_feasible, repair_build
from .scorer import heuristic_score
from .search_space import (
    SearchSpace, _regime_admits_hullmod, _regime_admits_weapon,
    build_search_space,
)
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
    shape: ShapeConfig = field(default_factory=ShapeConfig)
    regime: RegimeConfig = field(default_factory=lambda: REGIME_EARLY)
    warm_start_from_regime: str | None = None
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
    pool: EvaluatorPool,
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
    game_dir = getattr(pool, "game_dir", None)
    if game_dir is None:
        # CloudWorkerPool has no local game_dir; skip the local-file checks.
        return

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
    space = build_search_space(hull, game_data, config.regime)
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

    # Phase 2: Seed with top heuristic builds (diverse random, regime-masked)
    builds = generate_diverse_builds(
        hull, game_data, n=config.warm_start_sample_n, regime=config.regime,
    )
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


@dataclass(frozen=True)
class _EBDiagnostics:
    """Per-trial EB-shrinkage uncertainty for post-hoc CI reconstruction.

    Logging these alongside `eb_fitness` lets analysis code compute the
    exact posterior CI `eb_fitness ± 1.96 · sqrt(sigma_sq_eb)` and audit
    the shrinkage weight `tau2 / (tau2 + sigma_sq_twfe)` per trial.

    For fallback paths (`n < eb_min_builds` or `var(alpha) ≈ 0`) the
    optimizer returns `None` for these diagnostics — meaning the run
    used raw α̂ with no shrinkage.
    """
    sigma_sq_twfe: float           # σ̂_i² from the TWFE decomposition
    sigma_sq_eb: float             # w_i · σ̂_i² = (τ̂² · σ̂_i²) / (τ̂² + σ̂_i²)
    tau2: float                    # τ̂² (between-trial prior variance)
    gamma: tuple[float, ...]       # regression coefficients (intercept first)
    kept_cov_columns: tuple[int, ...]  # X columns surviving zero-std filter


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
        pool: EvaluatorPool,
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
        self._pool = pool
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
        # A3: post-A2′ EB posterior means; the Box-Cox fit reads this list
        # on every _finalize_build call to re-fit λ and rescale.
        self._completed_fitness_values: list[float] = []
        # A2′ summary (reported at end of run)
        self._eb_activated_count: int = 0  # trials where twfe != eb (EB moved alpha)
        self._eb_shrinkage_magnitudes: list[float] = []  # |eb - twfe| per completed trial
        # A3 summary (reported at end of run)
        self._shape_lambda_history: list[float] = []  # non-passthrough λ values
        self._shape_passthrough_reasons: Counter[str] = Counter()
        self._shape_first_activation_logged: bool = False
        self._run_start_time: float = 0.0
        self._trials_pruned: int = 0
        # _trials_completed counts every study.tell() call (success, pruned,
        # OR failure_score from InstanceError). _trials_errored separates the
        # failure_score rows: those trials never produced a valid combat
        # result and therefore never land in _completed_records, so the EB
        # fit population (gated on len(_completed_records)) is smaller than
        # _trials_completed - _trials_pruned by exactly _trials_errored.
        # Logged separately so operators don't conflate "told" with "usable."
        self._trials_errored: int = 0

    def run(self) -> None:
        """Execute staged evaluation with parallel instance dispatch.

        Coordinator pattern: main thread owns all Optuna/optimizer state.
        Worker threads (one per instance) only do blocking I/O via run_matchup().
        All state mutations (_queue, _dispatched, _score_matrix, etc.) happen
        in the main thread between wait() calls — no locks needed.
        """
        import time
        from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED

        self._run_start_time = time.time()
        num_workers = self._pool.num_workers
        logger.info(
            "Starting staged evaluation: %d workers, %d active / %d total opponents",
            num_workers,
            min(self._config.active_opponents, len(self._opponents)),
            len(self._opponents),
        )

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            pending: dict[Future, _InFlightBuild] = {}

            self._fill_workers(executor, pending, num_workers)

            while pending:
                done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)

                for future in done:
                    ifb = pending.pop(future)
                    self._dispatched.discard(ifb.trial.number)

                    try:
                        result = future.result()
                    except InstanceError:
                        logger.error(
                            "Worker failed for trial %d, scoring as %s",
                            ifb.trial.number,
                            self._config.failure_score,
                        )
                        self._study.tell(ifb.trial, self._config.failure_score)
                        self._trials_completed += 1
                        self._trials_errored += 1
                        if ifb in self._queue:
                            self._queue.remove(ifb)
                        continue

                    self._handle_result(ifb, result)

                    if (self._trials_completed % self._config.log_interval == 0
                            or not pending):
                        best = (self._study.best_value
                                if self._study.best_trial else 0.0)
                        # `finalized` = trials with valid EB-fit data (the same
                        # set gating _apply_eb_shrinkage). Excludes pruned AND
                        # InstanceError-as-failure_score rows.
                        finalized = len(self._completed_records)
                        logger.info(
                            "Progress: %d finalized + %d pruned + %d errored "
                            "/ %d budget, in-flight=%d, best=%.3f",
                            finalized, self._trials_pruned, self._trials_errored,
                            self._config.sim_budget, len(self._queue), best,
                        )

                # Dispatch new work up to the worker cap
                self._fill_workers(executor, pending, num_workers)

        self._log_run_summary()

    def _log_run_summary(self) -> None:
        """Emit end-of-run summary: EB activation, shrinkage, throughput."""
        import time

        elapsed = time.time() - self._run_start_time
        finalized = len(self._completed_records)
        rate = (finalized / elapsed * 3600) if elapsed > 0 else 0.0
        eb_rate = (self._eb_activated_count / finalized) if finalized else 0.0
        mean_abs = (
            sum(self._eb_shrinkage_magnitudes) / len(self._eb_shrinkage_magnitudes)
            if self._eb_shrinkage_magnitudes else 0.0
        )
        logger.info(
            "Run summary: %d finalized + %d pruned + %d errored in %.1fs "
            "(%.1f finalized/hr); EB activated on %d/%d finalized (%.0f%%); "
            "mean |Δ|=%.4f",
            finalized, self._trials_pruned, self._trials_errored, elapsed, rate,
            self._eb_activated_count, finalized, eb_rate * 100.0, mean_abs,
        )
        lam_hist = self._shape_lambda_history
        pt_total = sum(self._shape_passthrough_reasons.values())
        if lam_hist:
            lam_mean = float(np.mean(lam_hist))
            lam_std = float(np.std(lam_hist))
        else:
            lam_mean = 0.0
            lam_std = 0.0
        pt_breakdown = ", ".join(
            f"{reason}={count}"
            for reason, count in self._shape_passthrough_reasons.most_common()
        ) or "none"
        logger.info(
            "A3 Box-Cox summary: %d Box-Cox trials (λ mean=%.3f, std=%.3f), "
            "%d passthrough (%s)",
            len(lam_hist), lam_mean, lam_std, pt_total, pt_breakdown,
        )

    def _track_eb_summary(self, twfe: float, eb: float) -> None:
        """Accumulate EB activation + magnitude stats for the end-of-run log."""
        diff = abs(eb - twfe)
        self._eb_shrinkage_magnitudes.append(diff)
        if diff > 1e-9:
            self._eb_activated_count += 1

    def _fill_workers(self, executor, pending, num_workers) -> None:
        """Dispatch matchups up to the worker concurrency cap. Pool owns
        worker-selection; we only throttle to num_workers simultaneously
        in flight so we don't over-subscribe the executor.
        """
        while len(pending) < num_workers:
            work = self._next_matchup()
            if work is None:
                break
            ifb, matchup = work
            logger.info(
                "Dispatch trial %d rung %d/%d vs %s",
                ifb.trial.number, ifb.rung + 1, len(ifb.opponents),
                ifb.opponents[ifb.next_opponent_index],
            )
            future = executor.submit(self._pool.run_matchup, matchup)
            pending[future] = ifb

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
            "  Trial %d rung %d/%d vs %s: %s (score=%.3f, mean=%.3f)",
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
        eb_fitness, eb_diag = self._apply_eb_shrinkage(
            ifb.trial.number, twfe_fitness,
        )
        # A3: Box-Cox output warping (falls through to min-max scaling below
        # shape.min_samples or on constant-population edge cases).
        self._completed_fitness_values.append(eb_fitness)
        shaped_fitness, shape_diag = _shape_fitness(
            eb_fitness, self._completed_fitness_values, self._config.shape,
        )
        self._track_shape_summary(shape_diag)

        self._cache.put(ifb.build, shaped_fitness)
        self._study.tell(ifb.trial, shaped_fitness)
        logger.info(
            "  Trial %d COMPLETE (twfe=%.3f, eb=%.3f, shaped=%.3f, λ=%s)",
            ifb.trial.number, twfe_fitness, eb_fitness, shaped_fitness,
            f"{shape_diag.lam:.2f}" if shape_diag.lam is not None
                else f"pt:{shape_diag.passthrough_reason}",
        )

        if self._eval_log_path:
            record = self._completed_records[ifb.trial.number]
            covariate = _build_covariate_vector(record)
            _append_eval_log(
                self._eval_log_path, self._hull_id, ifb.trial.number,
                ifb.build, ifb.completed_results, shaped_fitness,
                raw_fitness=eb_fitness,
                eb_fitness=eb_fitness,
                twfe_fitness=twfe_fitness,
                engine_stats=ifb.engine_stats,
                covariate_vector=covariate.tolist(),
                shape_lambda=shape_diag.lam,
                shape_passthrough_reason=shape_diag.passthrough_reason,
                regime=self._config.regime.name,
                pruned=False, opponents_total=len(ifb.opponents),
                opponent_order=list(ifb.opponents),
                eb_diagnostics=eb_diag,
            )
            self._track_eb_summary(twfe_fitness, eb_fitness)

    def _apply_eb_shrinkage(
        self, trial_number: int, twfe_fitness: float,
    ) -> tuple[float, _EBDiagnostics | None]:
        """A2′ — EB shrinkage of TWFE α̂ toward γ̂ᵀX regression prior.

        Returns ``(raw α̂, None)`` when ``len(_completed_records) <
        eb_min_builds`` (stability guard: OLS fit on too few FULLY-FINALIZED
        builds is unstable — ``score_matrix.n_builds`` counts any build with
        at least one scored matchup, which over-counts during concurrent-
        pool dispatch when partial results land before a trial finalizes)
        or when ``var(alpha) ≈ 0`` (eb_shrinkage returns alpha unchanged
        with ``tau2=0``). Otherwise returns ``(posterior mean, EBDiagnostics)``
        with the per-trial σ²_TWFE / σ²_EB / τ̂² / γ̂ needed to reconstruct
        credible intervals at analysis time.
        """
        eb_cfg = self._config.eb
        if len(self._completed_records) < eb_cfg.eb_min_builds:
            return twfe_fitness, None

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
        alpha_eb, gamma, tau2, kept = eb_shrinkage(alphas, sigma_sqs, X, eb_cfg)
        if eb_cfg.triple_goal:
            alpha_out = triple_goal_rank(alpha_eb, alphas)
        else:
            alpha_out = alpha_eb
        idx = indices.index(trial_number)
        sigma_sq_twfe = float(sigma_sqs[idx])
        # Posterior variance for a two-level Gaussian with weight
        # w = τ² / (τ² + σ²_i): σ²_post = w · σ²_i = (1 − w) · τ².
        if tau2 + sigma_sq_twfe > 0.0:
            sigma_sq_eb = float(tau2 * sigma_sq_twfe / (tau2 + sigma_sq_twfe))
        else:
            sigma_sq_eb = 0.0
        # tau2 == 0 signals the var(alpha)≈0 fallback in eb_shrinkage —
        # alpha was returned unchanged, so "no shrinkage applied."
        if tau2 == 0.0:
            return float(alpha_out[idx]), None
        diag = _EBDiagnostics(
            sigma_sq_twfe=sigma_sq_twfe,
            sigma_sq_eb=sigma_sq_eb,
            tau2=float(tau2),
            gamma=tuple(float(g) for g in gamma),
            kept_cov_columns=tuple(int(c) for c in kept),
        )
        return float(alpha_out[idx]), diag

    def _prune_build(self, ifb: _InFlightBuild) -> None:
        """Tell Optuna PRUNED, log partial results."""
        self._study.tell(ifb.trial, state=TrialState.PRUNED)
        self._trials_pruned += 1

        if self._eval_log_path:
            raw_mean = (
                sum(ifb.raw_scores) / len(ifb.raw_scores)
                if ifb.raw_scores else 0.0
            )
            _append_eval_log(
                self._eval_log_path, self._hull_id, ifb.trial.number,
                ifb.build, ifb.completed_results, raw_mean,
                regime=self._config.regime.name,
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

    def _track_shape_summary(self, diag: _ShapeDiag) -> None:
        """Accumulate A3 Box-Cox activation stats for the end-of-run log.

        Emits the one-shot "A3 Box-Cox activated" INFO log the first time
        the transform runs cleanly (both `diag.lam is not None` and no
        passthrough reason). The `transformed_constant` edge case
        populates both `lam` and `passthrough_reason` — we record λ for
        the history but still count the passthrough reason for the summary
        breakdown, and do not treat it as the first clean activation.
        """
        if diag.lam is not None:
            self._shape_lambda_history.append(diag.lam)
        if diag.passthrough_reason is not None:
            self._shape_passthrough_reasons[diag.passthrough_reason] += 1
        if (
            diag.lam is not None
            and diag.passthrough_reason is None
            and not self._shape_first_activation_logged
        ):
            logger.info(
                "A3 Box-Cox activated at n=%d completed builds (first λ=%.3f)",
                len(self._completed_fitness_values), diag.lam,
            )
            self._shape_first_activation_logged = True


@dataclass(frozen=True)
class _ShapeDiag:
    """Diagnostic side-band from `_shape_fitness`.

    `lam` carries the fitted Box-Cox λ when the transform ran, else None;
    `passthrough_reason` is populated exactly when `lam is None`.
    """
    lam: float | None
    passthrough_reason: str | None


def _shape_fitness(
    eb_fitness: float,
    completed_values: Sequence[float],
    config: ShapeConfig,
) -> tuple[float, _ShapeDiag]:
    """A3 — Box-Cox output warping of the EB posterior mean to [0, 1].

    Pure function. Fits λ via `scipy.stats.boxcox` on the positivised
    completed-values population, transforms the current `eb_fitness` under
    the same λ, and min-max rescales the transformed value into [0, 1]
    for JSONL schema stability. Monotone in `eb_fitness` (preserves Spearman
    ρ) and gradient-preserving at the tails — replaces the pre-5E quantile
    rank that discarded top-quartile magnitudes.

    Passthrough fallbacks:
      * `n <= 1` → return exactly 0.5 (preserves pre-5E n≤1 contract)
      * `n < config.min_samples` → min-max scaling against the population
      * `ptp(completed_values) < config.positivise_epsilon` → 0.5 (constant)

    Raises `ValueError` on non-finite `eb_fitness` — upstream NaN is an
    invariant violation in TWFE or EB shrinkage, not unknown game data,
    so warn-don't-crash does not apply.
    """
    if not math.isfinite(eb_fitness):
        raise ValueError(f"Non-finite eb_fitness: {eb_fitness}")

    values = np.asarray(completed_values, dtype=float)
    n = len(values)
    if n <= 1:
        return 0.5, _ShapeDiag(lam=None, passthrough_reason="n<1")

    eps = config.positivise_epsilon
    ptp = float(np.ptp(values))
    if n < config.min_samples:
        lo, hi = float(values.min()), float(values.max())
        if hi - lo < eps:
            return 0.5, _ShapeDiag(lam=None, passthrough_reason="constant")
        val = float(np.clip((eb_fitness - lo) / (hi - lo), 0.0, 1.0))
        return val, _ShapeDiag(lam=None, passthrough_reason="n<min_samples")
    if ptp < eps:
        return 0.5, _ShapeDiag(lam=None, passthrough_reason="constant")

    shift = float(values.min()) - eps
    positivised = values - shift
    transformed, lam = boxcox(positivised)
    # Clamp current eb_fitness to the population's positivised range before
    # transform — Box-Cox requires strictly-positive inputs, and outliers
    # outside the population range must saturate at the interval boundary
    # anyway. The pre-transform clamp is monotone-equivalent to the post-
    # transform clip and avoids NaN propagation for extreme `eb_fitness`.
    pos_current = float(np.clip(
        eb_fitness - shift, positivised.min(), positivised.max(),
    ))
    current = boxcox(np.array([pos_current]), lmbda=lam)
    lo, hi = float(transformed.min()), float(transformed.max())
    if hi - lo < eps:
        # Pathological λ collapsed the transformed population — report with
        # a distinct reason so the JSONL doesn't show "Box-Cox ran with λ=x"
        # paired with a constant 0.5 output.
        return 0.5, _ShapeDiag(
            lam=float(lam), passthrough_reason="transformed_constant",
        )
    val = float(np.clip(
        (float(current[0]) - lo) / (hi - lo), 0.0, 1.0,
    ))
    return val, _ShapeDiag(lam=float(lam), passthrough_reason=None)


def _enqueue_warm_start_from_regime(
    target_study: optuna.Study,
    source_storage: str,
    source_study_name: str,
    target_regime: RegimeConfig,
    top_m: int,
    hull: ShipHull,
    game_data: GameData,
    target_space: SearchSpace | None = None,
) -> tuple[int, int]:
    """Phase 5F cross-regime warm-start. Copy top-M completed trials from a
    prior-regime study on the same hull into `target_study` via
    `study.enqueue_trial()`, skipping those infeasible under `target_regime`.

    Feasibility is checked on the REPAIRED build (not on raw params) — repair
    is the canonical optimizer→domain boundary (CLAUDE.md Design Principle 3),
    and checking raw params would bypass slot-constraint + hullmod-incompat
    enforcement.

    Returns (enqueued, skipped_infeasible); enqueued + skipped ≤ total
    completed trials considered.
    """
    try:
        source_study = optuna.load_study(
            study_name=source_study_name, storage=source_storage,
        )
    except (KeyError, ValueError) as e:
        raise ValueError(
            f"Warm-start source study '{source_study_name}' not found in "
            f"storage '{source_storage}': {e}"
        ) from e

    completed = [
        t for t in source_study.trials
        if t.state == TrialState.COMPLETE and t.value is not None
    ]
    completed.sort(key=lambda t: t.value, reverse=True)
    top = completed[:top_m]

    enqueued = 0
    skipped = 0
    for trial in top:
        try:
            raw = trial_params_to_build(trial.params, hull.id)
            repaired = repair_build(raw, hull, game_data)
        except Exception as exc:
            logger.warning(
                "Warm-start: could not reconstruct build from trial %d: %s",
                trial.number, exc,
            )
            skipped += 1
            continue
        feasible_ok, _viol = is_feasible(repaired, hull, game_data)
        if not feasible_ok:
            logger.warning(
                "Warm-start: repaired build from trial %d is not feasible",
                trial.number,
            )
            skipped += 1
            continue
        # Regime mask: verify every hullmod and weapon on the repaired build
        # is admitted by the target regime.
        feasible = True
        for hm_id in repaired.hullmods:
            hm = game_data.hullmods.get(hm_id)
            if hm is None or not _regime_admits_hullmod(hm, target_regime):
                feasible = False
                break
        if feasible:
            for w_id in repaired.weapon_assignments.values():
                if w_id is None:
                    continue
                w = game_data.weapons.get(w_id)
                if w is None or not _regime_admits_weapon(w, target_regime):
                    feasible = False
                    break
        if not feasible:
            logger.warning(
                "Warm-start: trial %d (value=%.3f) uses components outside "
                "regime '%s'; skipping",
                trial.number, trial.value, target_regime.name,
            )
            skipped += 1
            continue
        # Re-encode the repaired build's params against the target regime's
        # search space — the source study's params may reference slot/weapon
        # choices that are structurally absent from the target's distributions
        # even when the components themselves pass the regime mask (e.g. a
        # per-slot weapon list differs between regimes).
        if target_space is not None:
            try:
                params = build_to_trial_params(repaired, target_space)
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "Warm-start: trial %d re-encode failed: %s", trial.number, exc,
                )
                skipped += 1
                continue
        else:
            params = dict(trial.params)
        target_study.enqueue_trial(params)
        enqueued += 1

    logger.info(
        "Warm-start from regime '%s': enqueued %d trials, skipped %d infeasible",
        source_study_name.split("__")[-1] if "__" in source_study_name
        else source_study_name,
        enqueued, skipped,
    )
    return enqueued, skipped


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
    twfe_fitness: float | None = None,
    engine_stats: "EngineStats | None" = None,
    covariate_vector: list[float] | None = None,
    shape_lambda: float | None = None,
    shape_passthrough_reason: str | None = None,
    regime: str = "endgame",
    pruned: bool = False,
    opponents_total: int = 0,
    opponent_order: list[str] | None = None,
    eb_diagnostics: "_EBDiagnostics | None" = None,
) -> None:
    """Append one JSONL record to the evaluation log.

    Completed builds write the full audit triple:
      - twfe_fitness: pre-shrinkage α̂ from TWFE decomposition
      - eb_fitness:   post-shrinkage posterior mean (γ̂ᵀX fused with α̂)
      - fitness / raw_fitness: post-A3 Box-Cox-shaped value fed to Optuna
      - shape_lambda / shape_passthrough_reason: A3 diagnostic — λ when
        Box-Cox ran, reason string when A3 fell through to min-max scaling
    plus `engine_stats` + `covariate_vector` so the EB computation is reproducible
    from the log alone, and `eb_diagnostics` (σ²_TWFE, σ²_EB, τ̂², γ̂, kept
    covariate columns) so posterior credible intervals can be reconstructed
    at analysis time. Pruned builds omit these EB/A3-specific fields.
    """
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
        "regime": regime,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if eb_fitness is not None:
        record["eb_fitness"] = eb_fitness
    if twfe_fitness is not None:
        record["twfe_fitness"] = twfe_fitness
    if engine_stats is not None:
        record["engine_stats"] = {
            "eff_max_flux": engine_stats.eff_max_flux,
            "eff_flux_dissipation": engine_stats.eff_flux_dissipation,
            "eff_armor_rating": engine_stats.eff_armor_rating,
        }
    if covariate_vector is not None:
        record["covariate_vector"] = [float(x) for x in covariate_vector]
    if eb_diagnostics is not None:
        record["eb_diagnostics"] = {
            "sigma_sq_twfe": eb_diagnostics.sigma_sq_twfe,
            "sigma_sq_eb": eb_diagnostics.sigma_sq_eb,
            "tau2": eb_diagnostics.tau2,
            "gamma": list(eb_diagnostics.gamma),
            "kept_cov_columns": list(eb_diagnostics.kept_cov_columns),
        }
    if not pruned:
        record["shape_lambda"] = (
            float(shape_lambda) if shape_lambda is not None else None
        )
        record["shape_passthrough_reason"] = shape_passthrough_reason
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def optimize_hull(
    hull_id: str,
    game_data: GameData,
    pool: EvaluatorPool,
    opponent_pool: OpponentPool,
    config: OptimizerConfig,
) -> optuna.Study:
    """Main optimization entry point. Returns the Optuna study."""
    preflight_check(hull_id, game_data, pool, opponent_pool)
    hull = game_data.hulls[hull_id]
    space = build_search_space(hull, game_data, config.regime)
    distributions = define_distributions(space, fixed_params=config.fixed_params)

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    sampler = _create_sampler(config)
    opponents = get_opponents(opponent_pool, hull.hull_size)
    n_active = min(config.active_opponents, len(opponents))
    pruner = _create_pruner(config)

    study_name = f"{hull_id}__{config.regime.name}"
    study = optuna.create_study(
        sampler=sampler,
        pruner=pruner,
        direction="maximize",
        storage=config.study_storage,
        study_name=study_name,
        load_if_exists=True,
    )

    if config.warm_start_from_regime is not None:
        if config.warm_start_from_regime == config.regime.name:
            raise ValueError(
                f"warm_start_from_regime='{config.warm_start_from_regime}' "
                f"equals current regime='{config.regime.name}' — self-seeding "
                f"is not supported; Optuna's load_if_exists handles resume."
            )
        if config.study_storage is None:
            raise ValueError(
                "warm_start_from_regime requires study_storage to be set "
                "(both the source and target studies share one backend)."
            )
        source_study_name = f"{hull_id}__{config.warm_start_from_regime}"
        _enqueue_warm_start_from_regime(
            target_study=study,
            source_storage=config.study_storage,
            source_study_name=source_study_name,
            target_regime=config.regime,
            top_m=config.warm_start_n,
            hull=hull,
            game_data=game_data,
            target_space=space,
        )

    game_dir = getattr(pool, "game_dir", None)
    warm_start(study, hull, game_data, config, game_dir=game_dir)

    evaluator = StagedEvaluator(
        study=study,
        hull=hull,
        hull_id=hull_id,
        game_data=game_data,
        pool=pool,
        opponent_pool=opponent_pool,
        cache=BuildCache(),
        config=config,
        distributions=distributions,
        eval_log_path=config.eval_log_path,
    )
    evaluator.run()

    return study
