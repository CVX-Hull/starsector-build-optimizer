# Combat Harness Plugin Specification

Single-matchup-per-mission state machine that runs one AI-vs-AI combat, writes results, and ends the mission. Defined in `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`.

Extends `BaseEveryFrameCombatPlugin`. Attached by `MissionDefinition` via `api.addPlugin()`.

## State Machine

```
INIT → SETUP → FIGHTING → DONE → WAITING → (shutdown/timeout → exit)
```

One matchup per mission. After `endCombat()`, Robot dismisses results, game returns to title screen, TitleScreenPlugin detects new queue and auto-navigates to a fresh mission.

### State: INIT (first advance() call)
1. Load queue via `MatchupQueue.loadFromCommon()`
2. `engine.setDoNotEndCombat(true)` — prevent auto-end during setup
3. Transition to SETUP

### State: SETUP
1. Get `queue.get(0)` → `currentConfig` (single matchup per mission)
2. Apply time multiplier: `engine.getTimeMult().modifyMult("harness", config.timeMult)`
3. Create new DamageTracker, register via `engine.getListenerManager().addListener(tracker)`
4. Collect ships deployed by MissionDefinition (via `addToFleet()` — proper CR/AI behavior):
   - Iterate `engine.getShips()`, skip fighters
   - Owner 0 → playerShips, Owner 1 → enemyShips
5. Swap player ship loadout to real build spec:
   - `variant.clear()` + `addWeapon()`/`addMod()` for each weapon/hullmod
   - `setNumFluxVents()`, `setNumFluxCapacitors()`, `autoGenerateWeaponGroups()`
   - `ship.setCurrentCR(spec.cr)`, `ship.setCRAtDeployment(spec.cr)`
6. **Engine-computed SETUP stats read (6 fields):** After loadout
   swap, read the player ship's effective post-hullmod stats via
   the engine's authoritative `MutableShipStats` accessors and
   cache on plugin fields for emission in the result JSON:

   **Original 3 (Phase 5D, 2026-04-18):**
   - `currentEffMaxFlux = ship.getMutableStats().getFluxCapacity().getModifiedValue()`
   - `currentEffFluxDissipation = ship.getMutableStats().getFluxDissipation().getModifiedValue()`
   - `currentEffArmorRating = ship.getMutableStats().getArmorBonus().computeEffective(ship.getHullSpec().getArmorRating())`

   **Added 3 (Phase-7-prep, 2026-04-19):**
   - `currentEffHullHpPct = ship.getMutableStats().getHullBonus().computeEffective(hullSpec.getHitpoints()) / hullSpec.getHitpoints()`
     — ratio form (not raw HP) so variance is hull-size-invariant.
     Bimodal/trimodal driven by Reinforced Bulkheads (+40%) and
     Blast Doors (+20%).
   - `currentBallisticRangeBonus = ship.getMutableStats().getBallisticWeaponRangeBonus().computeEffective(1000f)`
     — multiplicative bonus against a 1000-range baseline; driven by
     Integrated Targeting Unit (+10/20/40/60% by hull size), Dedicated
     Targeting Core (0/0/15/25%), Unstable Injector (×0.85).
   - `currentShieldDamageTakenMult = ship.getMutableStats().getShieldDamageTakenMult().getModifiedValue()`
     — driven by Hardened Shields (-20/25%) and S-modded Front Shield
     Emitter. Stabilized Shields does NOT feed this (it modifies
     `ShieldUpkeepMult`).

   Null-check `getMutableStats()` and `getHullSpec()` — any null path
   stores `Float.NaN` (always-emit policy; parser handles NaN as
   malformed).

   **Why not read via `ArmorGridAPI`?** `ArmorGridAPI` cells reflect
   current damage state, not rated armor. `StatBonus.computeEffective(base)`
   is the canonical accessor because it applies flat + percent + mult
   bonuses to the base hull-spec rating.

   **API verified 2026-04-18** (original 3) **and 2026-04-19**
   (new 3 against vanilla 0.98a-RC8; see
   `docs/reference/phase5d-covariate-adjustment.md` §5 + the
   Phase-7-prep refactor plan).
7. Record `spawnTime`. Set `contactMade = false`.
8. Transition to FIGHTING

### State: FIGHTING
Per-frame:
1. **Camera:** Center viewport on midpoint of all tracked ships via `ViewportAPI.setExternalControl(true)` + `viewport.set()`.
2. **Heartbeat** every `HEARTBEAT_INTERVAL_FRAMES` (60) frames — enriched format with HP fractions and alive counts.
3. **Contact detection:** If `!contactMade`:
   - If `engine.isFleetsInContact()` → start combat timer (`matchupStartTime = now`), log contact
   - Else if `(now - spawnTime) > MAX_APPROACH_TIME (30s)` → force combat timer start (approach timeout for evasive AI)
4. **Custom win detection:** Count alive non-fighter ships per side from tracked lists. If one side has zero → other side wins. If both zero → TIMEOUT.
5. **Timeout check:** If `contactMade` and `(now - matchupStartTime) > timeLimitSeconds` → TIMEOUT
6. On end:
   - Build result via `ResultWriter.buildMatchupResult(..., currentEffMaxFlux, currentEffFluxDissipation, currentEffArmorRating, currentEffHullHpPct, currentBallisticRangeBonus, currentShieldDamageTakenMult)` — six trailing float args are the engine-computed SETUP stats from step SETUP.6
   - Write results array + done signal via `ResultWriter.writeAllResults()` + `ResultWriter.writeDoneSignal()`
   - Unregister DamageTracker
   - **Launch Robot dismiss thread** (must happen before `endCombat()` — see note below)
   - `engine.setDoNotEndCombat(false)` then `engine.endCombat(0f, winnerSide)`
7. Transition to DONE

**Timer logic:** The time limit only counts combat time, not approach time. Ships may take several seconds to fly toward each other after spawning. If one side is evasive and never engages, the 30-second approach timeout forces the combat timer to start anyway.

**Critical: Robot thread launch timing.** The Robot dismiss thread must be launched in the same frame as `endCombat()`, before the call. After `endCombat()`, the engine stops calling `advance()` almost immediately — if Robot launch is deferred to a later frame (e.g., in DONE state), it will never execute, leaving the game stuck on the mission results screen.

### State: DONE
Fallback transition — immediately moves to WAITING. Robot thread was already launched in FIGHTING.

### State: WAITING

Error recovery state. After `endCombat()`, the engine typically stops calling `advance()` within a few frames. WAITING provides shutdown signal handling and idle timeout in case Robot fails to dismiss results or the engine continues calling `advance()` unexpectedly.

Per-frame (while engine still calls `advance()`):
1. **Heartbeat** every `HEARTBEAT_INTERVAL_FRAMES` — zeros (0 HP, 0 alive) since no ships exist
2. **Shutdown signal check**: If `fileExistsInCommon("combat_harness_shutdown")` → delete signal, `System.exit(0)`
3. **Timeout**: Increment `waitingFrameCount`. If `> WAITING_TIMEOUT_FRAMES (3600, ~60s at 60fps)` → `System.exit(0)` for clean shutdown

**Constants:**
```java
private static final String SHUTDOWN_FILE = MatchupConfig.COMMON_PREFIX + "shutdown";
private static final int WAITING_TIMEOUT_FRAMES = 3600;
private static final int HEARTBEAT_INTERVAL_FRAMES = 60;
private static final float MAX_APPROACH_TIME = 30f;
```

## Custom Win Detection

```java
private int countAlive(List<ShipAPI> ships) {
    int count = 0;
    for (ShipAPI s : ships) {
        if (s.isAlive() && !s.isFighter()) count++;
    }
    return count;
}
```

With `setDoNotEndCombat(true)`, `engine.isCombatOver()` stays false. We detect matchup end ourselves.

## Error Handling

- Queue load failure (INIT) → log error, `System.exit(1)`
- Result write failure → log error, continue (best effort)
- Signal file deletion failure → log warning, continue (best effort)
- Always null-check engine in `advance()`

## Probe mode — ManifestDumper (2026-04-19)

In addition to combat simulation, the harness supports a **manifest
probe mode** triggered by a sentinel file
`combat_harness_manifest_request` in `saves/common/`. The probe runs
inside the `optimizer_arena` mission (not a separate mission) because
`HullModEffect.isApplicableToShip(ship)` requires a live `ShipAPI`,
which only exists post-combat-init.

**State machine extension:**
```
INIT → (sentinel detected) → PROBE_WAIT → PROBE_RUN → System.exit(0)
INIT → (no sentinel)         → SETUP → FIGHTING → DONE → WAITING (combat path)
```

### State: PROBE_WAIT
1. `advance()` bypasses the normal pause check for PROBE_WAIT /
   PROBE_RUN states — the engine otherwise keeps combat paused on
   the deployment screen.
2. On frame 1, call `engine.setPaused(false)` to force-unpause.
3. Call `forceDeployProbeReserves()` — the AI won't deploy 4 probe
   ships against a 1-ship enemy, so the plugin walks
   `fm.getReservesCopy()` and calls `spawnFleetMember` on each
   reserve to force every probe ship onto the field.
4. After all probe ships have spawned, transition to PROBE_RUN.

### State: PROBE_RUN
1. Collect spawned player-side ships by `HullSize` (FRIGATE,
   DESTROYER, CRUISER, CAPITAL_SHIP).
2. Call `ManifestDumper.dumpToCommon(sampleShipsBySize, constants)`.
3. `System.exit(0)` — exits the JVM cleanly so the orchestrator's
   headless launcher can detect the done sentinel and move on.

### MissionDefinition branching

The `optimizer_arena` mission's `defineMission` checks for the
sentinel file:
- If present: use `addProbeShipEmptyVariant(hull_id, size)` for each
  of wolf/hammerhead/eagle/onslaught — `createEmptyVariant` +
  `createFleetMember` + `addFleetMember`. Sets `CR = 0.7` via
  `setCurrentCR` + `setCRAtDeployment`. Sets `useDefaultAI = false`
  so the AI doesn't interfere with force-deployment.
- If absent: normal combat branch (stock variants + AI).

**Why empty variants?** Stock variants carry pre-installed hullmods.
`onslaught_Standard` ships with `dedicated_targeting_core`, which
would block ITU probing (DTC and ITU are mutually exclusive).
Empty variants isolate the single mod under test. Using
`createEmptyVariant` also avoids polluting `data/variants/` with
harness-generated variants.

### ManifestDumper API

`starsector.combatharness.ManifestDumper` is a static utility class:

```java
public static final int SCHEMA_VERSION = 1;
public static final int MAX_VENTS_PER_SHIP = 30;
public static final int MAX_CAPACITORS_PER_SHIP = 30;
public static final int MAX_LOGISTICS_HULLMODS = 2;
public static final float DEFAULT_CR = 0.7f;
// Damage multipliers by type — hardcoded vs 0.98a-RC8; bumped on
// engine change. settings.json reads can't cover this because
// the multipliers live in engine code, not a settings file.

public static void dumpToCommon(
    Map<HullSize, ShipAPI> sampleShips,
    Path commonDir
);
```

Emits to `saves/common/combat_harness_manifest.<section>.json.data`
in 4 files (constants, weapons, hullmods, hulls) plus a done
sentinel. `scripts/update_manifest.py` reads all 4 and merges into
`game/starsector/manifest.json`.

**Probe algorithm:**
1. Enumerate `Global.getSettings().getAllWeaponSpecs() /
   getAllHullModSpecs() / getAllShipHullSpecs()`.
2. For each hullmod `m`, for each `HullSize s` with a sample ship:
   call `m.getEffect().isApplicableToShip(sampleShips.get(s))`.
   Add `s` to `applicable_hull_sizes[m]` iff true.
   **Never use `getUnapplicableReason`** — its official contract
   (https://fractalsoftworks.com/starfarer.api) is that it's only
   valid after `isApplicableToShip` returns false; invoking it
   unconditionally returns stale fallback strings.
3. For each pair of applicable hullmods `(a, b)` on each hull size:
   clone the sample variant, `variant.addMod(b)`, call
   `a.getEffect().isApplicableToShip(clonedShip)`, and if false
   record `(a, b)` in `incompatible_with[a]`. `variant.removeMod(b)`
   between iterations. Dedup and emit symmetrically.
4. Ship skins (`.skin` files in `data/hulls/skins/`) are enumerated
   as distinct hull IDs with overrides resolved — the engine's
   `ShipHullSpecAPI.isRestoreToBase()` filtering exposes them.

### Schema bumping contract

If any of these change, bump `SCHEMA_VERSION` in the Java constants
AND `EXPECTED_SCHEMA_VERSION` in `game_manifest.py`:
- A field is added, removed, or renamed in `WeaponSpec`,
  `HullmodSpec`, `HullManifestEntry`, or `GameConstants`.
- The probe algorithm changes (e.g. new incompatibility detection
  logic).
- The enum vocabulary gains a member that affects existing parse
  paths (new enum members alone are forward-compat — see spec 29).

Version bumps are a single-commit event: Java constant + Python
constant + regenerated manifest + all affected downstream readers.
