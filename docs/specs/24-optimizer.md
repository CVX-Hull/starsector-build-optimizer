# Optimizer Specification

Optuna-based optimizer with heuristic warm-start, repair deduplication, and staged opponent evaluation with pruning. Defined in `src/starsector_optimizer/optimizer.py`.

## Overview

The optimizer proposes ship builds via Optuna's TPE sampler, repairs them to feasibility, evaluates against a diverse opponent pool with ASHA-style staged scheduling, and feeds fitness scores back. Key features:

- **Heuristic warm-start:** 50K random builds scored by heuristic, top-500 seed the study
- **Baldwinian recording:** Raw params recorded with repaired score via tell
- **Build cache:** Hash-based deduplication prevents wasted simulation budget
- **Staged evaluation:** Opponents evaluated incrementally with WilcoxonPruner — poor builds pruned early, freeing slots for new builds
- **Async parallel dispatch:** Each instance runs 1 matchup at a time via `InstancePool.run_matchup()`. A ThreadPoolExecutor coordinator dispatches work to all instances in parallel, processing results as they arrive (promote-on-arrival, async ASHA)

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
| `combat_fitness` | `CombatFitnessConfig` | `CombatFitnessConfig()` | Embedded fitness config (engagement threshold, tier weights, etc.). Single source of truth — no duplication. |
| `twfe` | `TWFEConfig` | `TWFEConfig()` | TWFE deconfounding + opponent selection parameters (ridge, iterations, trim, anchors, incumbent overlap). See spec 28. |
| `sampler` | `str` | `"tpe"` | Sampler algorithm: `"tpe"` or `"catcma"`. |
| `fixed_params` | `dict[str, bool \| int \| str] \| None` | `None` | Param name → fixed value. Fixed params are excluded from distributions, reducing effective dimensionality. |
| `study_storage` | `str \| None` | `None` | SQLite path or None for in-memory |
| `wilcoxon_p_threshold` | `float` | `0.1` | WilcoxonPruner p-value threshold for signed-rank test |
| `wilcoxon_n_startup_steps` | `int` | `2` | WilcoxonPruner minimum steps before pruning eligible |
| `matchup_time_limit` | `float` | `300.0` | Per-matchup time limit in seconds |
| `matchup_time_mult` | `float` | `5.0` | Game-time speed multiplier |
| `log_interval` | `int` | `10` | Log progress every N completed trials |
| `failure_score` | `float` | `-2.0` | Score assigned to failed/invalid builds (InstanceError or validation failure). Must be ≤ `CombatFitnessConfig.no_engagement_score` to prevent crashes from scoring above non-engagement. |
| `stock_build_scale_mult` | `float` | `2.0` | Multiplier for stock build warm-start values relative to heuristic (stock value = `warm_start_scale * stock_build_scale_mult`) |
| `cv_min_samples` | `int` | `30` | Evaluations before estimating control variate correlation |
| `cv_rho_threshold` | `float` | `0.3` | Minimum \|ρ\| to activate control variate correction |
| `cv_recalc_interval` | `int` | `10` | Re-estimate control variate parameters every N completions |
| `active_opponents` | `int` | `10` | Maximum opponents evaluated per build. If pool has fewer than K opponents, all are used. |
| `eval_log_path` | `Path \| None` | `None` | Path for JSONL evaluation log. If None, no log is written. |

### `BuildCache`

Mutable class for hash-based deduplication of repaired builds.

| Method | Signature | Description |
|--------|-----------|-------------|
| `hash_build` | `(build: Build) -> str` | SHA-256 of hull_id + sorted weapon_assignments + sorted hullmods + vents + caps (5 components; hull_id prevents cross-hull collisions) |
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
| `opponents` | `tuple[str, ...]` | required | Active opponent subset (anchors first, then shuffled; incumbent overlap forced after burn-in) |
| `heuristic_val` | `float` | `0.0` | Heuristic composite score for control variate (A2) |
| `completed_results` | `list[CombatResult]` | `[]` | Results accumulated so far |
| `raw_scores` | `list[float]` | `[]` | Per-opponent raw `combat_fitness()` values (parallel to `completed_results`) |
| `next_opponent_index` | `int` | `0` | Which opponent to evaluate next |

**Properties:**
- `rung` → `next_opponent_index` (ASHA rung = number of opponents evaluated)
- `is_complete` → `next_opponent_index >= len(opponents)`

### `StagedEvaluator`

Async ASHA-style staged evaluator with parallel instance dispatch and WilcoxonPruner. Uses coordinator-worker pattern: main thread owns all Optuna/optimizer state; worker threads (one per instance) do blocking I/O via `run_matchup()`.

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
- `_dispatched: set[int]` — `trial.number` of builds currently dispatched to an instance (prevents double-dispatch)
- `_trials_asked: int` — trials asked from Optuna
- `_trials_completed: int` — trials told to Optuna (COMPLETE + PRUNED)
- `_opponents: tuple[str, ...]` — opponent list for this hull size
- `_fitness_config: CombatFitnessConfig` — constructed from config.combat_fitness (7 fields; see spec 25)
- `_score_matrix: ScoreMatrix` — sparse build × opponent score accumulator for TWFE decomposition (see spec 28)
- `_incumbent_opponents: tuple[str, ...] | None` — opponents used by the best build so far (for forced overlap)
- `_incumbent_fitness: float` — fitness of the incumbent build
- `_anchors: tuple[str, ...]` — high-discrimination opponents locked at front of evaluation order (computed after burn-in, never changed)
- `_burn_in_scores: dict[str, list[float]]` — per-opponent raw scores during burn-in (cleared after anchor lock)
- `_burn_in_fitness: list[float]` — per-build TWFE fitness during burn-in (cleared after anchor lock)
- `_builds_evaluated: int` — count for burn-in threshold
- `_cv_pairs: list[tuple[float, float]]` — (heuristic_val, twfe_fitness) pairs for A2 correlation estimation
- `_cv_coefficient: float` — A2 control variate coefficient c = Cov(f,h)/Var(h)
- `_cv_heuristic_mean: float` — A2 running mean of heuristic scores
- `_cv_active: bool` — A2 active when |ρ| > cv_rho_threshold
- `_completed_fitness_values: list[float]` — post-A1/A2 fitness values for A3 rank shaping

**`run()` algorithm (async coordinator):**

1. Create `ThreadPoolExecutor(max_workers=num_instances)`.
2. Fill all instances with initial work via `_fill_instances()`: for each free instance, get matchup via `_next_matchup()`, submit to executor.
3. Event loop via `concurrent.futures.wait(FIRST_COMPLETED)`:
   - For each completed future: extract `(instance_id, ifb)` from pending map. Remove from `_dispatched`.
   - On `InstanceError`: score build as `failure_score`, free instance.
   - On success: route result via `_handle_result(ifb, result)` — compute raw fitness, update opponent stats, report intermediate value, check prune/complete.
   - After processing all done futures: call `_fill_instances()` to dispatch new work to freed instances.
4. Loop exits when `pending` is empty (all work done).

**`_next_matchup()`:** Returns the single highest-priority matchup. Phase 1: promote existing builds (highest rung first, skip `_dispatched`). Phase 2: ask Optuna for new trial (if `_trials_asked < sim_budget`). Returns None if no work available.

**`_handle_result(ifb, result)`:** Processes one matchup result. Compute raw `combat_fitness()` score, record into `_score_matrix.record(trial_number, opp_id, raw)`, store on `ifb.raw_scores`. Report raw fitness to Optuna: `trial.report(raw, step=rung_step)` where `rung_step` is the 0-based rung position (not opponent pool index). All trials share step IDs 0..N-1, giving WilcoxonPruner full paired overlap from the first trial. With anchor-first ordering, the first rung positions are the same high-discrimination opponents across trials. If complete: finalize via A1→A2→A3 pipeline. If `trial.should_prune()`: tell Optuna PRUNED. Otherwise: build stays in queue for next dispatch.

**`_fill_instances(executor, pending, free_instances)`:** Dispatch matchups to all free instances until no work or no free instances. Each instance gets exactly 1 matchup — finest pruning granularity.

**Instance parallelism:** Each instance independently cycles: dispatch → combat → result → dispatch. No synchronization barrier between instances. Pruning decisions are immediate after every opponent result. Concurrency = `instance_pool.num_instances`.

**Opponent Pool & Active Selection:** The discovered pool (typically 36-71 per hull size) is a reservoir. Each build evaluates up to `active_opponents` (default 10) from the pool. Opponent selection uses incumbent overlap + anchor-first ordering:

1. **Before burn-in** (`_builds_evaluated < twfe.anchor_burn_in`): fixed opponent set (seeded with `Random(0)`) for all trials. Using the same opponents ensures WilcoxonPruner has full step-ID overlap for paired comparisons from the very first trial. Per-trial `Random(trial_number)` shuffles the evaluation order for diversity.
2. **After burn-in**: Discriminative power per opponent is computed as |ρ(raw_scores_j, build_fitness_j)| using Spearman correlation (requires ≥ `twfe.min_disc_samples` per opponent). Top `twfe.n_anchors` opponents are locked as anchors. Burn-in state is cleared.
3. **Per trial** (after burn-in): Force anchors + `twfe.n_incumbent_overlap` opponents from the incumbent's set. Fill remaining slots from the full pool. Order: anchors first (steps 0..n_anchors−1), then rest shuffled via `random.Random(trial.number)`.

Anchors at the front give the WilcoxonPruner maximum early signal for pruning decisions. Incumbent overlap guarantees TWFE has direct build-vs-build comparisons through shared opponents. Step IDs are rung positions (0-based), so all trials share steps 0..N-1 regardless of which opponents they face — enabling paired comparisons from the first real trial.

### `ScoreMatrix` (from `deconfounding.py`)

See spec 28 for full documentation. The `StagedEvaluator` maintains a `ScoreMatrix` instance that accumulates raw `combat_fitness()` scores across all builds and opponents. Used for TWFE decomposition at build finalization.

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

Created via `_create_pruner(config)` factory:

`WilcoxonPruner(p_threshold=config.wilcoxon_p_threshold, n_startup_steps=config.wilcoxon_n_startup_steps)`. Uses a Wilcoxon signed-rank test to compare a trial's intermediate values against the best trial's values at the same steps. Step IDs are rung positions (0-based), so all trials share steps 0..N-1 regardless of which opponents they face. With anchor-first ordering post-burn-in, the first rung positions compare the same high-discrimination opponents across trials. No `n_opponents` parameter needed.

### Fitness Function — Signal Quality Pipeline (A1→A2→A3)

Raw per-matchup scores from `combat_fitness()` (spec 25) pass through three composable transformations before reaching Optuna:

```
raw combat_fitness score
  → record in ScoreMatrix
  → A1: TWFE decomposition (α_i + β_j) → trimmed_alpha
  → A2: control variate correction (if active)
  → A3: rank-based fitness shaping (finalization only)
  → study.tell()
```

**A1 — TWFE Deconfounding (spec 28):** Raw `combat_fitness()` scores are recorded into a `ScoreMatrix` instance via `record(trial_number, opp_id, raw)`. At build finalization, `build_alpha(trial_number, config.twfe)` decomposes the accumulated score matrix into build quality (α_i) and opponent difficulty (β_j), then applies trimmed mean (dropping `trim_worst` worst residuals per build). The α_i estimate is schedule-adjusted — comparable across builds that faced different opponent subsets. All observations (including from pruned builds) contribute to β estimates, improving α for subsequent builds.

**A2 — Control Variate Correction:** `fitness_adj = twfe_fitness - c * (heuristic_score - E[heuristic])`. Correlation ρ estimated from `(heuristic_val, twfe_fitness)` pairs after `cv_min_samples` completions. Applied only when |ρ| > `cv_rho_threshold`. Re-estimated every `cv_recalc_interval` completions. The `heuristic_score` is computed per-build at trial creation time in `_ask_new_trial` (cheap CPU-only computation). Note: TWFE alpha has different distributional properties than the prior z-scored mean, so CV correlation strength may differ.

**A3 — Rank-Based Fitness Shaping:** Final fitness converted to quantile rank in [0, 1] against the population of completed (post-A1/A2) fitness values. Spreads out the dense "shades of losing" cluster where most signal lives.

**Intermediate `trial.report()` values** use raw `combat_fitness()` scores reported at the rung position (0-based, no A1/A2/A3). `WilcoxonPruner` pairs values at the same rung across trials via signed-rank test — it does NOT mix intermediate report values with final `study.tell()` values. Raw scores are appropriate for the signed-rank test since it compares paired differences at the same evaluation position, and anchor-first ordering ensures the first rungs compare the same opponents post-burn-in.

**Failure scores** (`failure_score`, default -2.0) bypass all transformations — they are told directly to the study and are clearly below any rank in [0, 1].

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

### `optimize_hull(hull_id, game_data, instance_pool, opponent_pool, config) -> Study`

Main entry point.

1. `preflight_check(hull_id, game_data, instance_pool, opponent_pool)` — fail fast
2. Look up `hull = game_data.hulls[hull_id]`
3. `space = build_search_space(hull, game_data)`
4. `distributions = define_distributions(space, fixed_params=config.fixed_params)`
5. `optuna.logging.set_verbosity(optuna.logging.WARNING)` — suppress verbose Optuna output
6. `sampler = _create_sampler(config)` — creates TPE or CatCMAwM based on `config.sampler`
7. `pruner = _create_pruner(config)` — WilcoxonPruner with configured p-threshold and startup steps.
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
  "opponent_order": ["doom_Strike", "aurora_Assault", "dominator_Assault", "dominator_XIV_Elite", "eagle_Assault"],
  "raw_fitness": -0.35,
  "fitness": 0.23,
  "timestamp": "2026-04-11T14:32:15"
}
```

For completed builds, `fitness` is the rank-shaped value (quantile in [0, 1]) told to Optuna; `raw_fitness` is the TWFE α + CV-corrected value before rank shaping (for analysis). For pruned builds, both `fitness` and `raw_fitness` are the raw mean of observed combat_fitness scores at prune time (TWFE α is unstable with few observations; raw mean is used as a diagnostic). `opponents_evaluated < opponents_total` indicates early termination.

## Study Persistence

TPESampler is stateless — reconstructs from stored trials on every call. SQLite file transfer preserves all knowledge. Use `study_storage="sqlite:///study_{hull_id}.db"` for persistent studies that survive restarts and can be transferred to cloud machines.
