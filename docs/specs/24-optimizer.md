# Optimizer Specification

Optuna-based optimizer with heuristic warm-start, repair deduplication, and staged opponent evaluation with pruning. Defined in `src/starsector_optimizer/optimizer.py`.

## Overview

The optimizer proposes ship builds via Optuna's TPE sampler, repairs them to feasibility, evaluates against a diverse opponent pool with ASHA-style staged scheduling, and feeds fitness scores back. Key features:

- **Heuristic warm-start:** 50K random builds scored by heuristic, top-500 seed the study
- **Baldwinian recording:** Raw params recorded with repaired score via tell
- **Build cache:** Hash-based deduplication prevents wasted simulation budget
- **Staged evaluation:** Opponents evaluated incrementally with MedianPruner — poor builds pruned early, freeing slots for new builds
- **Mixed-build batching:** Each `InstancePool.evaluate()` call contains matchups from different builds at different stages, maximizing instance utilization

## Classes

### `OptimizerConfig`

Frozen dataclass configuring the optimization run.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `sim_budget` | `int` | `200` | Build evaluations (not total sims) |
| `warm_start_n` | `int` | `500` | Heuristic builds to seed the study |
| `warm_start_sample_n` | `int` | `50_000` | Random builds to generate for screening |
| `warm_start_scale` | `float` | `0.1` | Scale factor for heuristic scores |
| `n_startup_trials` | `int` | `100` | Random trials before TPE kicks in |
| `n_ei_candidates` | `int` | `256` | EI candidates per sample |
| `fitness_mode` | `str` | `"mean"` | `"mean"` or `"minimax"` |
| `eval_batch_size` | `int` | `8` | Max concurrent builds in staged evaluator. Set to num_instances for full utilization. |
| `engagement_threshold` | `float` | `500.0` | Minimum total permanent damage for "engaged" status in combat fitness. |
| `sampler` | `str` | `"tpe"` | Sampler algorithm: `"tpe"` or `"catcma"`. |
| `fixed_params` | `dict[str, bool \| int \| str] \| None` | `None` | Param name → fixed value. Fixed params are excluded from distributions, reducing effective dimensionality. |
| `study_storage` | `str \| None` | `None` | SQLite path or None for in-memory |
| `pruner_startup_trials` | `int` | `20` | MedianPruner n_startup_trials (no pruning until this many trials complete) |
| `pruner_warmup_steps` | `int` | `0` | MedianPruner n_warmup_steps (report at step 0+) |
| `matchup_time_limit` | `float` | `300.0` | Per-matchup time limit in seconds |
| `matchup_time_mult` | `float` | `5.0` | Game-time speed multiplier |
| `log_interval` | `int` | `10` | Log progress every N completed trials |
| `failure_score` | `float` | `-1.0` | Score assigned to failed/invalid builds (InstanceError or validation failure) |
| `stock_build_scale_mult` | `float` | `2.0` | Multiplier for stock build warm-start values relative to heuristic (stock value = `warm_start_scale * stock_build_scale_mult`) |
| `cv_min_samples` | `int` | `30` | Evaluations before estimating control variate correlation |
| `cv_rho_threshold` | `float` | `0.3` | Minimum \|ρ\| to activate control variate correction |
| `cv_recalc_interval` | `int` | `10` | Re-estimate control variate parameters every N completions |

### `BuildCache`

Mutable class for hash-based deduplication of repaired builds.

| Method | Signature | Description |
|--------|-----------|-------------|
| `hash_build` | `(build: Build) -> str` | SHA-256 of sorted weapon_assignments + sorted hullmods + vents + caps |
| `get` | `(build: Build) -> float \| None` | Cached score or None |
| `put` | `(build: Build, score: float) -> None` | Store score |

Only fully-evaluated builds are cached. Pruned builds are not cached — if re-proposed, they run staged evaluation again (the pruner may decide differently as the comparison distribution evolves).

### `_InFlightBuild`

Mutable dataclass tracking a build progressing through staged opponent evaluation. Private to `StagedEvaluator`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `trial` | `optuna.Trial` | required | Optuna trial handle |
| `build` | `Build` | required | Repaired build |
| `build_spec` | `BuildSpec` | required | Serializable build spec |
| `variant_id` | `str` | required | Unique variant ID (`{hull_id}_opt_{trial.number:06d}`) |
| `opponents` | `tuple[str, ...]` | required | Full opponent list |
| `completed_results` | `list[CombatResult]` | `[]` | Results accumulated so far |
| `raw_scores` | `list[float]` | `[]` | Per-opponent raw `combat_fitness()` values (parallel to `completed_results`) |
| `heuristic_val` | `float` | `0.0` | Heuristic composite score for control variate (A2) |
| `next_opponent_index` | `int` | `0` | Which opponent to evaluate next |

**Properties:**
- `rung` → `next_opponent_index` (ASHA rung = number of opponents evaluated)
- `is_complete` → `next_opponent_index >= len(opponents)`

### `StagedEvaluator`

Manages ASHA-style staged evaluation with mixed-build batching. Replaces the flat batch loop.

```python
class StagedEvaluator:
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
    ) -> None: ...

    def run(self) -> None: ...
```

**Internal state:**
- `_queue: list[_InFlightBuild]` — builds with pending opponents
- `_in_flight: dict[str, _InFlightBuild]` — matchup_id → build for result routing
- `_trials_asked: int` — trials asked from Optuna
- `_trials_completed: int` — trials told to Optuna (COMPLETE + PRUNED)
- `_opponents: tuple[str, ...]` — opponent list for this hull size
- `_fitness_config: CombatFitnessConfig` — from config.engagement_threshold
- `_opponent_stats: list[RunningStats]` — per-opponent running mean/std for A1 z-score normalization (index i = opponent i)
- `_cv_pairs: list[tuple[float, float]]` — (heuristic_val, z_fitness) pairs for A2 correlation estimation
- `_cv_coefficient: float` — A2 control variate coefficient c = Cov(f,h)/Var(h)
- `_cv_heuristic_mean: float` — A2 running mean of heuristic scores
- `_cv_active: bool` — A2 active when |ρ| > cv_rho_threshold
- `_completed_fitness_values: list[float]` — post-A1/A2 fitness values for A3 rank shaping

**`run()` algorithm:**

1. Loop while `_trials_completed < sim_budget` or `_queue` non-empty
2. Compose batch via `_compose_batch()`:
   - Phase 1: Promote existing builds (highest rung first — closest to completion)
   - Phase 2: Fill remaining slots with new trials from Optuna (up to `eval_batch_size`)
3. Send batch to `instance_pool.evaluate()`
4. Route results via `_route_results()`:
   - Match each result to its `_InFlightBuild` via `matchup_id`
   - A1: Compute raw `combat_fitness()` score, update `_opponent_stats[opp_idx]`, store on `ifb.raw_scores`
   - Report z-scored cumulative fitness to Optuna: `trial.report(z_fitness, step=opponent_index)`
   - If complete: finalize via A1→A2→A3 pipeline (rank-shape, cache, tell Optuna, log)
   - If `trial.should_prune()`: tell Optuna PRUNED, log
   - Otherwise: build stays in queue for next batch
5. On `InstanceError`: tell all affected in-flight builds -1.0

**Batch composition:** Each batch contains one matchup per in-flight build. This maximizes pruning granularity — after each opponent result, the build can be pruned. Builds at higher rungs (more opponents evaluated) are scheduled first per ASHA priority.

**Opponents are evaluated in fixed tuple order** from `get_opponents()`. This ensures `step=0` always means the same opponent across all trials, which is critical for `MedianPruner` comparison.

### `RunningStats`

Private utility class in `optimizer.py` implementing Welford's online algorithm for streaming mean/variance. Used by `StagedEvaluator` for A1 opponent normalization.

| Method/Property | Signature | Description |
|----------------|-----------|-------------|
| `update` | `(x: float) -> None` | Add observation, update running mean and M2 |
| `z_score` | `(x: float, min_samples: int = 2) -> float` | Z-score x against running stats. Returns 0.0 if n < min_samples or std < ε |
| `n` | `-> int` | Number of observations |
| `mean` | `-> float` | Running mean |
| `std` | `-> float` | Sample standard deviation (Bessel-corrected, n-1). Returns 0.0 if n < 2 |

## Functions

### `define_distributions(space, fixed_params=None) -> dict[str, BaseDistribution]`

Converts `SearchSpace` to Optuna distribution dict. When `fixed_params` is provided, parameters in the dict are excluded from distributions — they are not suggested by the sampler.

- `weapon_{slot_id}` → `CategoricalDistribution(weapon_options[slot_id])` (includes `"empty"`)
- `hullmod_{mod_id}` → `CategoricalDistribution([True, False])`
- `flux_vents` → `IntDistribution(0, max_vents)`
- `flux_capacitors` → `IntDistribution(0, max_capacitors)`

After building all distributions, any parameter whose name is in `fixed_params` is removed from the dict.

### `build_to_trial_params(build, space) -> dict`

Flattens `Build` to Optuna param dict:
- `weapon_{sid}` → weapon_id or `"empty"` for None
- `hullmod_{mid}` → `True` if mid in build.hullmods, else `False`
- `flux_vents` → build.flux_vents
- `flux_capacitors` → build.flux_capacitors

### `trial_params_to_build(params, hull_id, fixed_params=None) -> Build`

Reconstructs `Build` from flat param dict. Reverses the mapping — `"empty"` → None, `True` hullmod params → `frozenset`. When `fixed_params` is provided, merges fixed values into `params` before reconstruction — fixed params always override sampler-suggested values.

### `warm_start(study, hull, game_data, config, game_dir=None) -> None`

1. **Stock build seeding** (if `game_dir` provided): Load stock `.variant` files via `load_stock_builds(game_dir, hull.id)`. Add each as a completed trial with value `config.warm_start_scale * config.stock_build_scale_mult` (higher than random heuristic builds — stock builds are known-good starting points).
2. `generate_diverse_builds(hull, game_data, n=config.warm_start_sample_n)`
3. Score each with `heuristic_score(build, hull, game_data).composite_score`
4. Sort descending, take top `config.warm_start_n`
5. Add each as a completed trial: `study.add_trial(create_trial(params, distributions, values=[score * config.warm_start_scale]))`

Warm-start trials use scaled-down heuristic scores (0.1x default) so they provide directional guidance without dominating TPE's density estimators. Stock builds are seeded at 2x the scale to indicate higher confidence.

## Sampler Selection

The optimizer supports multiple Optuna samplers via a factory function.

### `_create_sampler(config) -> optuna.samplers.BaseSampler`

| Sampler | When to Use | Library | Config |
|---------|------------|---------|--------|
| `"tpe"` | Default. Good for high-D exploration with warm-start. | `optuna.samplers.TPESampler` | `multivariate=True`, `constant_liar=True`, `n_ei_candidates`, `n_startup_trials` from config |
| `"catcma"` | Refinement after TPE, or when cross-variable correlations matter. Better for reduced search spaces (after fixing params). | `optunahub` + `cmaes` | `CatCmawmSampler` loaded via `optunahub.load_module("samplers/catcmawm")` |

Unknown `sampler` value raises `ValueError`.

CatCMAwM uses population-based parallelism rather than constant_liar — it naturally handles batch evaluation.

### Pruner

`MedianPruner(n_startup_trials=config.pruner_startup_trials, n_warmup_steps=config.pruner_warmup_steps)` — the first `n_startup_trials` trials are never pruned, giving the pruner a baseline distribution. With only 5 opponent steps, Hyperband's bracket structure is inappropriate; MedianPruner is simpler and effective at this scale.

### Fitness Function — Signal Quality Pipeline (A1→A2→A3)

Raw per-matchup scores from `combat_fitness()` (spec 25) pass through three composable transformations before reaching Optuna:

```
raw combat_fitness score
  → A1: z-score per opponent (RunningStats)
  → aggregate (mean or minimax per fitness_mode)
  → A2: control variate correction (if active)
  → A3: rank-based fitness shaping (finalization only)
  → study.tell()
```

**A1 — Opponent Normalization:** Each opponent's raw scores are tracked by a `RunningStats` instance (Welford's online algorithm). When computing cumulative fitness, stored raw scores are z-scored against the current opponent statistics, then aggregated by `fitness_mode`. Stats are updated from ALL trials (including pruned) so the normalizer learns from every observation. Cold start: z_score returns 0.0 when n<2 per opponent — overlaps with `pruner_startup_trials` warm-up.

**A2 — Control Variate Correction:** `fitness_adj = z_fitness - c * (heuristic_score - E[heuristic])`. Correlation ρ estimated from `(heuristic_val, z_fitness)` pairs after `cv_min_samples` completions. Applied only when |ρ| > `cv_rho_threshold`. Re-estimated every `cv_recalc_interval` completions. The `heuristic_score` is computed per-build at trial creation time in `_ask_new_trial` (cheap CPU-only computation).

**A3 — Rank-Based Fitness Shaping:** Final fitness converted to quantile rank in [0, 1] against the population of completed (post-A1/A2) fitness values. Spreads out the dense "shades of losing" cluster where most signal lives.

**Intermediate `trial.report()` values** use A1 z-scored aggregates only (no A2/A3). `MedianPruner` compares report values at the same step across trials — it does NOT mix intermediate report values with final `study.tell()` values. The different scales (z-scores for intermediates, ranks for finals) are safe because the pruner only compares like with like.

**Failure scores** (-1.0) bypass all transformations — they are told directly to the study and are clearly below any rank in [0, 1].

### `preflight_check(hull_id, game_data, instance_pool, opponent_pool) -> None`

Validates all prerequisites before launching expensive simulation. Runs in <1 second. Raises `ValueError` with a descriptive message on failure. Called at the start of `optimize_hull`.

Checks:
1. `hull_id` exists in `game_data.hulls`
2. Combat harness mod deployed: `game_dir/mods/combat-harness/jars/combat-harness.jar` exists
3. `enabled_mods.json` exists and contains `combat_harness`
4. All opponent variant IDs in the pool resolve to `.variant` files under `game_dir/data/variants/`
5. `Xvfb` and `xdotool` are installed (found on PATH via `shutil.which`)

### `validate_build_spec(spec: BuildSpec, game_data: GameData) -> list[str]`

Validates a `BuildSpec` against game data. Returns list of error strings (empty = valid). Validation errors are fatal — the Java harness cannot construct a variant from missing game data.

Checks:
1. `spec.hull_id` exists in `game_data.hulls`
2. Every hullmod ID in `spec.hullmods` exists in `game_data.hullmods`
3. Every weapon ID in `spec.weapon_assignments` values exists in `game_data.weapons`

### `optimize_hull(hull_id, game_data, instance_pool, opponent_pool, config, eval_log_path=None) -> Study`

Main entry point.

1. `preflight_check(hull_id, game_data, instance_pool, opponent_pool)` — fail fast
2. Look up `hull = game_data.hulls[hull_id]`
3. `space = build_search_space(hull, game_data)`
4. `distributions = define_distributions(space, fixed_params=config.fixed_params)`
5. `optuna.logging.set_verbosity(optuna.logging.WARNING)` — suppress verbose Optuna output
6. `sampler = _create_sampler(config)` — creates TPE or CatCMAwM based on `config.sampler`
7. `pruner = MedianPruner(n_startup_trials=config.pruner_startup_trials, n_warmup_steps=config.pruner_warmup_steps)`
8. `study = optuna.create_study(sampler=sampler, pruner=pruner, direction="maximize", storage=config.study_storage, study_name=hull_id, load_if_exists=True)`
9. `warm_start(study, hull, game_data, config, game_dir=instance_pool.game_dir)`
10. `cache = BuildCache()`
11. Create `StagedEvaluator(study, hull, hull_id, game_data, instance_pool, opponent_pool, cache, config, distributions, eval_log_path)`
12. `evaluator.run()` — staged evaluation loop until `sim_budget` exhausted
13. Return study

## JSONL Evaluation Log

One line per build evaluation, appended to `data/evaluation_log.jsonl`:

```json
{
  "hull_id": "eagle",
  "trial_number": 42,
  "build": {
    "hull_id": "eagle",
    "weapon_assignments": {"WS0001": "heavymauler", "WS0002": null},
    "hullmods": ["heavyarmor", "hardenedshieldemitter"],
    "flux_vents": 20,
    "flux_capacitors": 10
  },
  "opponent_results": [
    {"opponent": "dominator_Assault", "winner": "PLAYER", "duration_seconds": 54.2, "hp_differential": 0.35},
    {"opponent": "medusa_CS", "winner": "ENEMY", "duration_seconds": 120.0, "hp_differential": -0.12}
  ],
  "opponents_evaluated": 2,
  "opponents_total": 5,
  "pruned": true,
  "raw_fitness": -0.35,
  "fitness": 0.23,
  "timestamp": "2026-04-11T14:32:15"
}
```

For completed builds, `fitness` is the rank-shaped value (quantile in [0, 1]) told to Optuna; `raw_fitness` is the z-scored + CV-corrected value before rank shaping (for analysis). For pruned builds, both `fitness` and `raw_fitness` are the z-scored cumulative at prune time (no rank shaping). `opponents_evaluated < opponents_total` indicates early termination.

## Study Persistence

TPESampler is stateless — reconstructs from stored trials on every call. SQLite file transfer preserves all knowledge. Use `study_storage="sqlite:///study_{hull_id}.db"` for persistent studies that survive restarts and can be transferred to cloud machines.
