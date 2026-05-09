---
type: spec
status: shipped
last-validated: unvalidated
---

# Result Writer Specification

Constructs result JSON from tracked ship data and writes batch results via SettingsAPI. Defined in `combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`.

## Functions

### `static JSONObject buildMatchupResult(MatchupConfig config, List<ShipAPI> playerShips, List<ShipAPI> enemyShips, DamageTracker tracker, String winner, float duration, float effMaxFlux, float effFluxDissipation, float effArmorRating, float effHullHpPct, float ballisticRangeBonus, float shieldDamageTakenMult, JSONArray loadoutDiagnosticPlayer, JSONArray debugDumps)`

Build a single matchup result JSONObject from directly-tracked ShipAPI references. Does NOT use fleet manager (which accumulates across matchups in batched sessions).

- `winner`: `"PLAYER"`, `"ENEMY"`, or `"TIMEOUT"`
- `duration`: combat time for this matchup (relative, not cumulative engine time)
- Six engine-stat trailing floats — `effMaxFlux`, `effFluxDissipation`,
  `effArmorRating` (Phase 5D), `effHullHpPct`, `ballisticRangeBonus`,
  `shieldDamageTakenMult` (Phase-7-prep, 2026-04-19) — read at end of SETUP
  (see spec 13). Emitted under `setup_stats.player` via `buildSetupStatsJSON()`.
  **Emission rule**: the block is written only when all six values are finite.
  If any input is `Float.NaN` (the SETUP read failed — e.g., no player ships,
  null `MutableShipStats`) the `setup_stats` key is OMITTED from the result
  JSON. The Python parser treats absence as `engine_stats=None`, so a failed
  read silently degrades to the fallback path rather than raising. Rationale:
  the game's bundled org.json rejects NaN in `put()` — we cannot propagate
  NaN through the JSON layer even if we wanted to.
- `loadoutDiagnosticPlayer`: per-player-ship `JSONArray` of LoadoutDiagnostic
  objects (`spec_*` vs `live_*` for weapons / hullmods / flux vents / flux
  capacitors). **Always emitted** as `loadout_diagnostic.player` (empty array
  if unset) — Python parser fail-louds on missing key (spec 19).
- `debugDumps`: per-matchup `JSONArray` of `[SHIP_DUMP]` / `[FIGHT_TICK]`
  / `[WIN_DUMP]` lines accumulated by `recordDebug`. **Optional emission** —
  written under top-level `debug_dumps` only when non-empty. Python parser
  reads `data.get("debug_dumps") or ()` (forward-compat with older Java
  builds that emit no key). Only `[FIGHT_TICK]` is gated on
  `MatchupConfig.debugDumpsEnabled`; SETUP `[SHIP_DUMP]` and one-shot
  `[WIN_DUMP]` are always-on (bounded ~4-8 lines per matchup).
- Ship data extracted via `shipToJSON()` for each tracked ship
- Aggregate stats computed from the ship lists (destroyed = `!ship.isAlive()` count)

### `static JSONObject buildSetupStatsJSON(float effMaxFlux, float effFluxDissipation, float effArmorRating, float effHullHpPct, float ballisticRangeBonus, float shieldDamageTakenMult)`

Wraps the six SETUP-phase engine reads into:

```json
{"player": {
    "eff_max_flux": <float>, "eff_flux_dissipation": <float>,
    "eff_armor_rating": <float>, "eff_hull_hp_pct": <float>,
    "ballistic_range_bonus": <float>, "shield_damage_taken_mult": <float>
}}
```

Individual primitive params match the style of sibling helpers (`damageToJSON`, `fluxStatsToJSON`, `aggregateToJSON`). Only the player side is emitted — A2′ EB shrinkage regresses α̂ on the player build, not opponents.

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
