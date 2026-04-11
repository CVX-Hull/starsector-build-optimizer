# Result Parser Specification

Parses combat result JSON (written by the Java harness) into Python dataclasses, and writes matchup queue JSON for the Java harness to consume. Defined in `src/starsector_optimizer/result_parser.py`.

## Functions

### `parse_combat_result(data: dict) -> CombatResult`

Parse a single result dict from the Java JSON output into a `CombatResult` dataclass.

**Field mapping:**

| JSON field | Python field | Notes |
|-----------|-------------|-------|
| `matchup_id` | `matchup_id` | |
| `winner` | `winner` | "PLAYER", "ENEMY", or "TIMEOUT" |
| `duration_seconds` | `duration_seconds` | |
| `player_ships[].fleet_member_id` | `fleet_member_id` | |
| `player_ships[].variant_id` | `variant_id` | |
| `player_ships[].hull_id` | `hull_id` | |
| `player_ships[].destroyed` | `destroyed` | |
| `player_ships[].hull_fraction` | `hull_fraction` | |
| `player_ships[].armor_fraction` | `armor_fraction` | |
| `player_ships[].cr_remaining` | `cr_remaining` | |
| `player_ships[].peak_time_remaining` | `peak_time_remaining` | |
| `player_ships[].disabled_weapons` | `disabled_weapons` | |
| `player_ships[].flameouts` | `flameouts` | |
| `player_ships[].damage_dealt` | `damage_dealt: DamageBreakdown` | `{shield, armor, hull, emp}` |
| `player_ships[].damage_taken` | `damage_taken: DamageBreakdown` | `{shield, armor, hull, emp}` |
| `player_ships[].flux_stats.overload_count` | `overload_count` | Nested under `flux_stats` in JSON, top-level in dataclass |
| `aggregate.player_ships_destroyed` | `player_ships_destroyed` | |
| `aggregate.enemy_ships_destroyed` | `enemy_ships_destroyed` | |
| `aggregate.player_ships_retreated` | `player_ships_retreated` | |
| `aggregate.enemy_ships_retreated` | `enemy_ships_retreated` | |

### `parse_results_file(path: Path) -> list[CombatResult]`

Read a `combat_harness_results.json.data` file and parse all results.

- Reads file as UTF-8 text
- Parses as JSON array
- Returns `[parse_combat_result(item) for item in array]`
- Empty array → empty list

### `write_queue_file(matchups: list[MatchupConfig], path: Path) -> None`

Write a list of `MatchupConfig` objects as a JSON array to the given path.

**MatchupConfig → JSON mapping:**

| Python field | JSON field |
|-------------|-----------|
| `matchup_id` | `matchup_id` |
| `player_builds` | `player_builds` (tuple of BuildSpec → list of dicts) |
| `enemy_variants` | `enemy_variants` (tuple → list) |
| `time_limit_seconds` | `time_limit_seconds` |
| `time_mult` | `time_mult` |
| `map_width` | `map_width` |
| `map_height` | `map_height` |

Each `BuildSpec` in `player_builds` is serialized as a dict with keys: `variant_id`, `hull_id`, `weapon_assignments` (dict), `hullmods` (list), `flux_vents` (int), `flux_capacitors` (int).

- Writes JSON with indent=2 for readability
- The caller is responsible for passing the correct path (including `.data` extension)

## Design Notes

- Extracted from `_parse_result()` in `tests/test_combat_harness_integration.py`
- `overload_count` is nested under `flux_stats` in the Java JSON but is a top-level field on `ShipCombatResult` — the parser handles this mapping
- No network I/O — pure file read/write + JSON parsing
