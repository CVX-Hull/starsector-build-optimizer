# Result Writer Specification

Constructs result JSON from tracked ship data and writes batch results via SettingsAPI. Defined in `combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`.

## Functions

### `static JSONObject buildMatchupResult(MatchupConfig config, List<ShipAPI> playerShips, List<ShipAPI> enemyShips, DamageTracker tracker, String winner, float duration)`

Build a single matchup result JSONObject from directly-tracked ShipAPI references. Does NOT use fleet manager (which accumulates across matchups in batched sessions).

- `winner`: `"PLAYER"`, `"ENEMY"`, or `"TIMEOUT"`
- `duration`: combat time for this matchup (relative, not cumulative engine time)
- Ship data extracted via `shipToJSON()` for each tracked ship
- Aggregate stats computed from the ship lists (destroyed = `!ship.isAlive()` count)

### `static JSONObject shipToJSON(ShipAPI ship, DamageTracker tracker)`

Extract per-ship stats from a ShipAPI reference:
- `fleet_member_id`, `variant_id` (null-safe), `hull_id` (null-safe)
- `destroyed` (`!ship.isAlive()`), `hull_fraction`, `armor_fraction`
- `cr_remaining`, `peak_time_remaining`, `disabled_weapons` (null-safe), `flameouts`
- `damage_dealt/taken` from DamageTracker accumulators
- `flux_stats` from FluxTrackerAPI (null-safe)

### `static void writeAllResults(JSONArray results)`
Write the batch results array to `combat_harness_results.json` via SettingsAPI.

### `static void writeDoneSignal()`
Write `combat_harness_done` signal file (contains timestamp) via SettingsAPI.

### `static void writeHeartbeat(float elapsedTime)`
Write heartbeat to `combat_harness_heartbeat.txt` via SettingsAPI. Non-fatal on failure.

### Static JSON Helpers (JUnit-testable)

- `damageToJSON(shield, armor, hull, emp)` → `{"shield": N, "armor": N, ...}`
- `fluxStatsToJSON(currFlux, hardFlux, maxFlux, overloadCount)` → `{...}`
- `aggregateToJSON(playerDealt, enemyDealt, playerDestroyed, enemyDestroyed, playerRetreated, enemyRetreated)` → `{...}`
- `computeArmorFraction(ShipAPI)` → average armor grid cells / max
