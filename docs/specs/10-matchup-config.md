# Matchup Config Specification

Java POJO for parsing and validating `matchup.json`. Defined in `combat-harness/src/main/java/starsector/combatharness/MatchupConfig.java`.

## Fields

| Field | Java Type | JSON Key | Required | Default |
|-------|-----------|----------|----------|---------|
| `matchupId` | `String` | `matchup_id` | yes | — |
| `playerVariants` | `String[]` | `player_variants` | yes | — |
| `enemyVariants` | `String[]` | `enemy_variants` | yes | — |
| `playerFlagship` | `String` | `player_flagship` | no | `null` |
| `timeLimitSeconds` | `float` | `time_limit_seconds` | no | `300.0f` |
| `timeMult` | `float` | `time_mult` | no | `3.0f` |
| `mapWidth` | `float` | `map_width` | no | `24000.0f` |
| `mapHeight` | `float` | `map_height` | no | `18000.0f` |

## Validation Rules

- `matchupId` must be non-null and non-empty
- `playerVariants` must be non-null and non-empty
- `enemyVariants` must be non-null and non-empty
- `timeMult` clamped to `[1.0, 5.0]`
- `timeLimitSeconds` must be > 0
- `mapWidth` and `mapHeight` must be > 0

## Functions

### `static MatchupConfig fromFile(File path)`
Read file contents, parse as JSONObject, delegate to `fromJSON()`. Throws `RuntimeException` on I/O errors.

### `static MatchupConfig fromJSON(JSONObject json)`
Parse JSON fields with defaults for optional values. Apply validation and clamping. Throws `IllegalArgumentException` for missing required fields or invalid values.

### `JSONObject toJSON()`
Serialize back to JSONObject for round-trip testing.

## JSON Parsing

Uses `org.json.JSONObject` (bundled with Starsector as `json.jar`). No external JSON library dependencies.
