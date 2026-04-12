# Opponent Pool Specification

Defines diverse opponent sets per hull size class for robust fitness evaluation. Defined in `src/starsector_optimizer/opponent_pool.py`.

## Motivation

Starsector has strong rock-paper-scissors dynamics: kinetic weapons deal 200% damage to shields (50% to armor), HE deals 50% to shields (200% to armor). A single-opponent fitness function produces counter-builds that exploit one weakness but fail against other archetypes. A diverse opponent pool produces robust, generalizable builds.

## Classes

### `OpponentPool`

Frozen dataclass mapping hull sizes to stock variant IDs.

| Field | Type | Description |
|-------|------|-------------|
| `pools` | `dict[HullSize, tuple[str, ...]]` | Maps hull size to opponent variant IDs |

## Functions

### `discover_stock_variant_ids(game_dir) -> list[tuple[str, str]]`

Defined in `variant.py`. Scans `game_dir/data/variants/` recursively for all `.variant` files. Returns `(variant_id, hull_id)` pairs. Uses `load_variant_file()` to handle Starsector's loose JSON format. Excludes optimizer-generated variants (containing `_opt_`, `_val_`, `_inttest_` in the filename stem). Silently skips malformed files.

### `discover_opponent_pool(game_dir, game_data) -> OpponentPool`

Builds the opponent pool dynamically from all stock variants in the game data directory.

| Parameter | Type | Description |
|-----------|------|-------------|
| `game_dir` | `Path` | Path to Starsector installation root |
| `game_data` | `GameData` | Parsed game data (for hull_id → hull_size mapping) |

Algorithm:
1. Call `discover_stock_variant_ids(game_dir)` to find all stock variant files
2. For each `(variant_id, hull_id)`: look up `game_data.hulls[hull_id]`. Skip variants whose hull is not in `game_data.hulls`.
3. Filter out non-opponent hulls via `_is_valid_opponent(hull)`:
   - `fleet_pts <= 0` → fighter wings and drones (require carriers, can't fight standalone)
   - `hints` contains `STATION`, `HIDE_IN_CODEX`, or `MODULE` → stations and station modules
   - `tags` contains `threat` or `dweller` → Remnant threat units and Shrouded entities (non-standard AI)
4. Group valid variant IDs by hull size, sort alphabetically within each group
5. Warn if any hull size has fewer than 2 opponents (minimum for meaningful pruning)
6. Return `OpponentPool` with non-empty hull sizes only

Discovery is scoped to `game_dir/data/variants/` — does not scan `mods/` directories. Civilian/logistics ships (CIVILIAN hint) are kept — they provide easy opponents for the difficulty gradient, and B1 ordering deprioritizes them if uninformative. The discovered pool is a reservoir (typically 36-71 per hull size); active selection of a subset happens in the optimizer via `OptimizerConfig.active_opponents`.

### `get_opponents(pool, hull_size) -> tuple[str, ...]`

Returns opponent variant IDs for a hull size. Raises `KeyError` if hull size not in pool.

### `generate_matchups(player_build, opponents, matchup_id_prefix, ...) -> list[MatchupConfig]`

Creates one `MatchupConfig` per opponent.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `player_build` | `BuildSpec` | required | The optimizer-generated build specification |
| `opponents` | `tuple[str, ...]` | required | Opponent variant IDs from pool |
| `matchup_id_prefix` | `str` | required | Prefix for matchup IDs |
| `time_limit_seconds` | `float` | `300.0` | Game-time limit per matchup |
| `time_mult` | `float` | `5.0` | Time acceleration |

Matchup ID format: `{prefix}_vs_{opponent_id}`.

Each `MatchupConfig` has `player_builds=(player_build,)` and `enemy_variants=(opponent_id,)`.

### `hp_differential(result) -> float`

Computes normalized HP differential from a single `CombatResult`.

Algorithm: `mean(hull_fraction for player ships) - mean(hull_fraction for enemy ships)`.

- Destroyed ships have `hull_fraction = 0.0`
- If either `player_ships` or `enemy_ships` is empty, returns 0.0 (guard clause before division).
- Range: -1.0 (total loss) to +1.0 (total victory)
- Timeout draws: based on remaining HP fractions

### `compute_fitness(results, mode="mean") -> float`

Aggregates HP differentials across all matchup results.

| Mode | Computation | Use |
|------|-------------|-----|
| `"mean"` | `mean(hp_differential(r) for r in results)` | Default. Rewards generalists. |
| `"minimax"` | `min(hp_differential(r) for r in results)` | Rewards robust builds with no bad matchups. |

Raises `ValueError` if `results` is empty.
