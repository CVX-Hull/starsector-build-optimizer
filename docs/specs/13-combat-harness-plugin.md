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
6. Record `spawnTime`. Set `contactMade = false`.
7. Transition to FIGHTING

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
   - Build result via `ResultWriter.buildMatchupResult()`
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
