# Result Writer Specification

Collects final combat state and writes `result.json` via the game's SettingsAPI. Defined in `combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`.

## File Output

Results are written to `saves/common/combat_harness_result.json` via `Global.getSettings().writeTextFileToCommon()`. The game appends `.data` to the filename on disk.

Atomic writes (tmp + rename) are **not possible** through the SettingsAPI — the game handles writing internally.

## Functions

### `static void writeResult(CombatEngineAPI engine, DamageTracker tracker, MatchupConfig config, boolean timedOut)`

Main entry point. Collects all combat data and writes result.json.

1. Determine winner: `timedOut` → `"TIMEOUT"`, else `engine.getWinningSideId()` (0=PLAYER, 1=ENEMY)
2. Get combat duration: `engine.getTotalElapsedTime(false)`
3. Collect ships from both fleet managers via `collectShipsFromFleetManager()`
4. Compute aggregate stats from fleet managers
5. Build final JSONObject and write via `writeToCommon()`

### `static void collectShipsFromFleetManager(CombatFleetManagerAPI fm, DamageTracker tracker, JSONArray output)`

Iterate `fm.getAllEverDeployedCopy()` to collect all ships including destroyed/disabled ones. `engine.getShips()` may drop destroyed ships — the fleet manager tracks everything ever deployed.

Skips fighter wings and null ships.

### `static JSONObject shipToJSON(ShipAPI ship, DamageTracker tracker)`

Extract per-ship stats:

| Field | Source |
|-------|--------|
| `fleet_member_id` | `ship.getFleetMemberId()` |
| `variant_id` | `ship.getVariant().getHullVariantId()` (null-safe) |
| `hull_id` | `ship.getHullSpec().getHullId()` (null-safe) |
| `destroyed` | `!ship.isAlive()` |
| `hull_fraction` | `ship.getHullLevel()` (inherited from CombatEntityAPI) |
| `armor_fraction` | Average of armor grid cells / max armor per cell |
| `cr_remaining` | `ship.getCurrentCR()` |
| `peak_time_remaining` | `ship.getPeakTimeRemaining()` |
| `disabled_weapons` | `ship.getDisabledWeapons().size()` (null-safe) |
| `flameouts` | `ship.getNumFlameouts()` |
| `damage_dealt/taken` | From `tracker.getOrCreate(fleetMemberId)` |
| `flux_stats` | From `ship.getFluxTracker()` (null-safe) |

### `static void writeHeartbeat(float elapsedTime)`

Write heartbeat to `saves/common/combat_harness_heartbeat.txt` via SettingsAPI. Non-fatal on failure.

## Testable Helpers

These static methods are testable via JUnit without the game running:

- `damageToJSON(shield, armor, hull, emp)` — pure JSON construction
- `fluxStatsToJSON(currFlux, hardFlux, maxFlux, overloadCount)` — pure JSON construction
- `aggregateToJSON(...)` — pure JSON construction
