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

### `DEFAULT_OPPONENT_POOL`

Module-level constant. Single `OpponentPool` instance with curated opponents covering archetypes: shield tank, armor tank, kiter, carrier, phase.

Variant IDs match filenames in `data/variants/` (without `.variant` extension):

| Hull Size | Opponents |
|-----------|-----------|
| FRIGATE | `wolf_Assault`, `lasher_Assault`, `hyperion_Attack`, `shade_Assault` |
| DESTROYER | `hammerhead_Elite`, `medusa_Attack`, `enforcer_Assault`, `sunder_Assault` |
| CRUISER | `dominator_Assault`, `dominator_XIV_Elite`, `aurora_Assault`, `heron_Attack`, `doom_Strike`, `eagle_Assault` |
| CAPITAL_SHIP | `onslaught_Standard`, `onslaught_xiv_Elite`, `legion_xiv_Elite`, `astral_Elite`, `conquest_Elite` |

## Functions

### `get_opponents(pool, hull_size) -> tuple[str, ...]`

Returns opponent variant IDs for a hull size. Raises `KeyError` if hull size not in pool.

### `generate_matchups(player_variant_id, opponents, matchup_id_prefix, ...) -> list[MatchupConfig]`

Creates one `MatchupConfig` per opponent.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `player_variant_id` | `str` | required | The optimizer's generated variant ID |
| `opponents` | `tuple[str, ...]` | required | Opponent variant IDs from pool |
| `matchup_id_prefix` | `str` | required | Prefix for matchup IDs |
| `time_limit_seconds` | `float` | `300.0` | Game-time limit per matchup |
| `time_mult` | `float` | `5.0` | Time acceleration |

Matchup ID format: `{prefix}_vs_{opponent_id}`.

Each `MatchupConfig` has `player_variants=(player_variant_id,)` and `enemy_variants=(opponent_id,)`.

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
