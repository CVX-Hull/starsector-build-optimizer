# Matchup Config Specification

Java POJO for a single matchup configuration. Defined in `combat-harness/src/main/java/starsector/combatharness/MatchupConfig.java`.

Used within `MatchupQueue` (see spec 15) — the queue file contains an array of these objects.

## Fields

| Field | Java Type | JSON Key | Required | Default |
|-------|-----------|----------|----------|---------|
| `matchupId` | `String` | `matchup_id` | yes | — |
| `playerVariants` | `String[]` | `player_variants` | yes | — |
| `enemyVariants` | `String[]` | `enemy_variants` | yes | — |
| `timeLimitSeconds` | `float` | `time_limit_seconds` | no | `300.0f` |
| `timeMult` | `float` | `time_mult` | no | `3.0f` |
| `mapWidth` | `float` | `map_width` | no | `24000.0f` |
| `mapHeight` | `float` | `map_height` | no | `18000.0f` |

## Validation Rules

- `matchupId` must be non-null and non-empty
- `playerVariants` and `enemyVariants` must be non-null and non-empty
- `timeMult` clamped to `[1.0, 5.0]`
- `timeLimitSeconds` must be > 0
- `mapWidth` and `mapHeight` must be > 0

## Functions

### `static MatchupConfig fromJSON(JSONObject json)`
Parse JSON fields with defaults for optional values. Apply validation and clamping. Throws `IllegalArgumentException` / `JSONException`.

### `JSONObject toJSON()`
Serialize back to JSONObject for round-trip testing.

## Constants

`COMMON_PREFIX = "combat_harness_"` — shared prefix for all `saves/common/` filenames.

## JSON Parsing

Uses `org.json.JSONObject` (bundled with Starsector). Old version with checked `JSONException` on all operations.
