# Matchup Config Specification

Java POJO for parsing and validating matchup config. Defined in `combat-harness/src/main/java/starsector/combatharness/MatchupConfig.java`.

## File I/O

Config is read from `saves/common/` via the game's SettingsAPI (not `java.io.File`):
- `loadFromCommon()` reads `combat_harness_matchup.json` (game resolves to `saves/common/combat_harness_matchup.json.data`)
- `existsInCommon()` checks if the file exists

The `COMMON_PREFIX` constant (`combat_harness_`) is shared with `ResultWriter` for consistent naming.

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
- `playerVariants` and `enemyVariants` must be non-null and non-empty
- `timeMult` clamped to `[1.0, 5.0]`
- `timeLimitSeconds` must be > 0
- `mapWidth` and `mapHeight` must be > 0

## Functions

### `static MatchupConfig loadFromCommon()`
Read matchup config from `saves/common/` via `Global.getSettings().readTextFileFromCommon()`. Throws `RuntimeException` on I/O or parse errors.

### `static boolean existsInCommon()`
Check if matchup config file exists via `Global.getSettings().fileExistsInCommon()`.

### `static MatchupConfig fromJSON(JSONObject json)`
Parse JSON fields with defaults for optional values. Apply validation and clamping. Throws `IllegalArgumentException` for invalid values. Throws `JSONException` for JSON parsing errors.

### `JSONObject toJSON()`
Serialize back to JSONObject for round-trip testing.

## JSON Parsing

Uses `org.json.JSONObject` (bundled with Starsector as `json.jar`). Note: this is an old version with checked `JSONException` on all operations.
