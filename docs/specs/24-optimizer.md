# Optimizer Specification

Optuna-based optimizer with heuristic warm-start and repair deduplication. Defined in `src/starsector_optimizer/optimizer.py`.

## Overview

The optimizer proposes ship builds via Optuna's TPE sampler, repairs them to feasibility, evaluates against a diverse opponent pool, and feeds fitness scores back. Key features:

- **Heuristic warm-start:** 50K random builds scored by heuristic, top-500 seed the study
- **Baldwinian recording:** Raw params recorded with repaired score via tell
- **Build cache:** Hash-based deduplication prevents wasted simulation budget

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
| `eval_batch_size` | `int` | `4` | Builds evaluated per batch. Set to num_instances // num_opponents for full utilization. |
| `study_storage` | `str \| None` | `None` | SQLite path or None for in-memory |

### `BuildCache`

Mutable class for hash-based deduplication of repaired builds.

| Method | Signature | Description |
|--------|-----------|-------------|
| `hash_build` | `(build: Build) -> str` | SHA-256 of sorted weapon_assignments + sorted hullmods + vents + caps |
| `get` | `(build: Build) -> float \| None` | Cached score or None |
| `put` | `(build: Build, score: float) -> None` | Store score |

## Functions

### `define_distributions(space) -> dict[str, BaseDistribution]`

Converts `SearchSpace` to Optuna distribution dict.

- `weapon_{slot_id}` → `CategoricalDistribution(weapon_options[slot_id])` (includes `"empty"`)
- `hullmod_{mod_id}` → `CategoricalDistribution([True, False])`
- `flux_vents` → `IntDistribution(0, max_vents)`
- `flux_capacitors` → `IntDistribution(0, max_capacitors)`

### `build_to_trial_params(build, space) -> dict`

Flattens `Build` to Optuna param dict:
- `weapon_{sid}` → weapon_id or `"empty"` for None
- `hullmod_{mid}` → `True` if mid in build.hullmods, else `False`
- `flux_vents` → build.flux_vents
- `flux_capacitors` → build.flux_capacitors

### `trial_params_to_build(params, hull_id) -> Build`

Reconstructs `Build` from flat param dict. Reverses the mapping — `"empty"` → None, `True` hullmod params → `frozenset`.

### `warm_start(study, hull, game_data, config) -> None`

1. `generate_diverse_builds(hull, game_data, n=config.warm_start_sample_n)`
2. Score each with `heuristic_score(build, hull, game_data).composite_score`
3. Sort descending, take top `config.warm_start_n`
4. Add each as a completed trial: `study.add_trial(create_trial(params, distributions, values=[score * config.warm_start_scale]))`

Warm-start trials use scaled-down heuristic scores (0.1x default) so they provide directional guidance without dominating TPE's density estimators.

### `evaluate_build(build, hull, game_data, instance_pool, opponent_pool, cache, eval_log_path=None) -> float`

1. `repaired = repair_build(build, hull, game_data)` (idempotent)
2. Check `cache.get(repaired)` → return if hit
3. `variant = generate_variant(repaired, hull, game_data)` with unique variant_id
4. `instance_pool.write_variant_to_all(variant, f"{variant_id}.variant")` — places variant in all instance work dirs
5. `opponents = get_opponents(opponent_pool, hull.hull_size)`
6. `matchups = generate_matchups(variant_id, opponents, ...)`
7. `results = instance_pool.evaluate(matchups)`
8. `fitness = compute_fitness(results, mode=config.fitness_mode)`
9. `cache.put(repaired, fitness)`
10. If `eval_log_path`: append JSONL record
11. Return fitness

### `preflight_check(hull_id, game_data, instance_pool, opponent_pool) -> None`

Validates all prerequisites before launching expensive simulation. Runs in <1 second. Raises `ValueError` with a descriptive message on failure. Called at the start of `optimize_hull`.

Checks:
1. `hull_id` exists in `game_data.hulls`
2. Combat harness mod deployed: `game_dir/mods/combat-harness/jars/combat-harness.jar` exists
3. `enabled_mods.json` exists and contains `combat_harness`
4. All opponent variant IDs in the pool resolve to `.variant` files under `game_dir/data/variants/`
5. `Xvfb` and `xdotool` are installed (found on PATH via `shutil.which`)

### `validate_variant(variant: dict, game_data: GameData) -> list[str]`

Validates a generated variant dict against game data. Returns list of error strings (empty = valid).

Checks:
1. Every hullmod ID in `hullMods` exists in `game_data.hullmods`
2. Every weapon ID in `weaponGroups` exists in `game_data.weapons`
3. `hullId` exists in `game_data.hulls`

Called by `evaluate_build` before writing variant files to instances.

### `optimize_hull(hull_id, game_data, instance_pool, opponent_pool, config) -> Study`

Main entry point.

1. `preflight_check(hull_id, game_data, instance_pool, opponent_pool)` — fail fast
2. Look up `hull = game_data.hulls[hull_id]`
3. `space = build_search_space(hull, game_data)`
4. `distributions = define_distributions(space)`
4. Create `TPESampler(multivariate=True, constant_liar=True, n_ei_candidates=config.n_ei_candidates, n_startup_trials=config.n_startup_trials)`
5. `study = optuna.create_study(sampler=sampler, direction="maximize", storage=config.study_storage, study_name=hull_id, load_if_exists=True)`
6. `warm_start(study, hull, game_data, config)`
7. `cache = BuildCache()`
8. Batched ask-tell loop (`sim_budget // eval_batch_size` batches):
   - Ask `eval_batch_size` trials from study (constant_liar handles pending trials)
   - For each trial: repair build, check cache (tell cached score immediately), generate variant + matchups for uncached
   - `instance_pool.evaluate(all_matchups)` — all uncached matchups in one batch
   - Map results back to builds by matchup_id prefix, compute fitness per build
   - Tell all trials their scores
   - On `InstanceError`: tell affected trials score=-1.0, log failure, continue
9. Return study

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
  "fitness": 0.18,
  "timestamp": "2026-04-05T14:32:15"
}
```

## Study Persistence

TPESampler is stateless — reconstructs from stored trials on every call. SQLite file transfer preserves all knowledge. Use `study_storage="sqlite:///study_{hull_id}.db"` for persistent studies that survive restarts and can be transferred to cloud machines.
