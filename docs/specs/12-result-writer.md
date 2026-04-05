# Result Writer Specification

Collects final combat state and writes `result.json` atomically. Defined in `combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`.

## Functions

### `static void writeResult(CombatEngineAPI engine, DamageTracker tracker, MatchupConfig config, File outputDir, boolean timedOut)`

Main entry point. Collects all combat data and writes result.json.

1. Determine winner:
   - If `timedOut` → `"TIMEOUT"`
   - Else → `engine.getWinningSideId()`: 0 = `"PLAYER"`, 1 = `"ENEMY"`
2. Get combat duration: `engine.getTotalElapsedTime(false)`
3. Iterate `engine.getShips()`, classify into player (owner=0) and enemy (owner=1)
4. For each ship, call `shipToJSON()`
5. Compute aggregate stats from fleet managers
6. Build final JSONObject
7. Write atomically: `result.json.tmp` → rename to `result.json`

### `static JSONObject shipToJSON(ShipAPI ship, DamageTracker tracker, MatchupConfig config)`

Extract per-ship stats:

| Field | Source |
|-------|--------|
| `fleet_member_id` | `ship.getFleetMemberId()` |
| `variant_id` | `ship.getVariant().getHullVariantId()` |
| `hull_id` | `ship.getHullSpec().getHullId()` |
| `destroyed` | `!ship.isAlive()` |
| `hull_fraction` | `ship.getHullLevel()` |
| `armor_fraction` | Average of armor grid cells / max armor per cell |
| `cr_remaining` | `ship.getCurrentCR()` |
| `peak_time_remaining` | `ship.getPeakTimeRemaining()` |
| `disabled_weapons` | `ship.getDisabledWeapons().size()` |
| `flameouts` | `ship.getNumFlameouts()` |
| `damage_dealt` | From `tracker.getOrCreate(fleetMemberId)` dealt fields |
| `damage_taken` | From `tracker.getOrCreate(fleetMemberId)` taken fields |
| `flux_stats` | From `ship.getFluxTracker()` |

### `static float computeArmorFraction(ShipAPI ship)`

Compute average armor fraction across the armor grid:
```java
ArmorGridAPI grid = ship.getArmorGrid();
float maxPerCell = grid.getMaxArmorInCell();
if (maxPerCell <= 0) return 0f;
float[][] cells = grid.getGrid();
float total = 0f;
int count = 0;
for (float[] row : cells) {
    for (float cell : row) {
        total += cell / maxPerCell;
        count++;
    }
}
return count > 0 ? total / count : 0f;
```

### `static JSONObject damageToJSON(float shield, float armor, float hull, float emp)`

Helper to create `{"shield": N, "armor": N, "hull": N, "emp": N}`.

## Atomic Write

```java
File tmp = new File(outputDir, "result.json.tmp");
File result = new File(outputDir, "result.json");
try (FileWriter fw = new FileWriter(tmp)) {
    fw.write(json.toString(2));  // pretty-print with indent=2
}
tmp.renameTo(result);
```

## Testable Helpers

The `shipToJSON` method depends on `ShipAPI` (not unit-testable). The following are independently testable:

- `damageToJSON(shield, armor, hull, emp)` — pure JSON construction
- `computeArmorFraction` — depends on `ArmorGridAPI` but logic is simple
- JSON structure: create a full result JSONObject from test data, verify all fields present
