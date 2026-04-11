# Matchup Config Specification

Java POJO for a single matchup configuration. Defined in `combat-harness/src/main/java/starsector/combatharness/MatchupConfig.java`.

Used within `MatchupQueue` (see spec 15) — the queue file contains an array of these objects.

## Fields

| Field | Java Type | JSON Key | Required | Default |
|-------|-----------|----------|----------|---------|
| `matchupId` | `String` | `matchup_id` | yes | — |
| `playerBuilds` | `BuildSpec[]` | `player_builds` | yes | — |
| `enemyVariants` | `String[]` | `enemy_variants` | yes | — |
| `timeLimitSeconds` | `float` | `time_limit_seconds` | no | `300.0f` |
| `timeMult` | `float` | `time_mult` | no | `3.0f` |
| `mapWidth` | `float` | `map_width` | no | `24000.0f` |
| `mapHeight` | `float` | `map_height` | no | `18000.0f` |

Player builds are specified as inline `BuildSpec` objects. The Java harness constructs `ShipVariantAPI` objects programmatically from these specs. Enemy variants remain as stock variant ID strings loaded from `.variant` files at game startup.

## BuildSpec Inner Class

| Field | Java Type | JSON Key | Required | Default |
|-------|-----------|----------|----------|---------|
| `variantId` | `String` | `variant_id` | yes | — |
| `hullId` | `String` | `hull_id` | yes | — |
| `weaponAssignments` | `Map<String, String>` | `weapon_assignments` | yes | — |
| `hullmods` | `String[]` | `hullmods` | yes | — |
| `fluxVents` | `int` | `flux_vents` | no | `0` |
| `fluxCapacitors` | `int` | `flux_capacitors` | no | `0` |
| `cr` | `float` | `cr` | no | `0.7` |

All fields `public final`. `weaponAssignments` wrapped in `Collections.unmodifiableMap()`. Keys are weapon slot IDs (e.g. `"WS 001"`), values are weapon IDs. Empty slots are omitted (no null values). Can be empty `{}`.

### BuildSpec Validation Rules

- `variantId` and `hullId` must be non-null and non-empty
- `fluxVents` and `fluxCapacitors` must be >= 0
- `cr` clamped to `[0.0, 1.0]`

## Validation Rules

- `matchupId` must be non-null and non-empty
- `playerBuilds` and `enemyVariants` must be non-null and non-empty
- `timeMult` clamped to `[1.0, 5.0]`
- `timeLimitSeconds` must be > 0
- `mapWidth` and `mapHeight` must be > 0

## Functions

### `static MatchupConfig fromJSON(JSONObject json)`
Parse JSON fields with defaults for optional values. Parse `player_builds` as JSONArray of BuildSpec objects. Apply validation and clamping. Throws `IllegalArgumentException` / `JSONException`.

### `JSONObject toJSON()`
Serialize back to JSONObject for round-trip testing.

### `static BuildSpec buildSpecFromJSON(JSONObject json)`
Parse a single build spec from JSON. `weapon_assignments` parsed by iterating JSONObject keys.

## Constants

`COMMON_PREFIX = "combat_harness_"` — shared prefix for all `saves/common/` filenames.

## JSON Example

```json
{
    "matchup_id": "eagle_000042_vs_dominator_Assault",
    "player_builds": [
        {
            "variant_id": "eagle_opt_000042",
            "hull_id": "eagle",
            "weapon_assignments": {"WS 001": "heavymauler", "WS 002": "hveldriver"},
            "hullmods": ["hardenedshieldemitter", "heavyarmor"],
            "flux_vents": 20,
            "flux_capacitors": 10
        }
    ],
    "enemy_variants": ["dominator_Assault"],
    "time_limit_seconds": 300,
    "time_mult": 5.0,
    "map_width": 24000,
    "map_height": 18000
}
```

## JSON Parsing

Uses `org.json.JSONObject` (bundled with Starsector). Old version with checked `JSONException` on all operations.
