# Combat Harness Plugin Specification

State machine that cycles through a batch of matchups in a single combat session. Defined in `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`.

Extends `BaseEveryFrameCombatPlugin`. Attached by `MissionDefinition` via `api.addPlugin()`.

## State Machine

```
INIT → SPAWNING → FIGHTING → CLEANING → SPAWNING → ... → DONE
```

### State: INIT (first advance() call)
1. Load queue via `MatchupQueue.loadFromCommon()`
2. `engine.setDoNotEndCombat(true)` — keep combat alive across matchups
3. Transition to SPAWNING

### State: SPAWNING
1. Get `queue.get(currentIndex)` → `currentConfig`
2. Apply time multiplier: `engine.getTimeMult().modifyMult("harness", config.timeMult)`
3. Create new DamageTracker, register via `engine.getListenerManager().addListener(tracker)`
4. If first matchup: remove placeholder ships (MissionDefinition adds stock placeholders for the deployment screen), then spawn real builds via `spawnFleetMember()`
5. All matchups (including first):
   - Player ships: construct via `VariantBuilder.createFleetMember(buildSpec)`, spawn via `fleetManager.spawnFleetMember(member, location, facing, 0f)`, ensure CR via `ensureCombatReady()`, store returned ShipAPIs
   - Enemy ships: spawn via `fleetManager.spawnShipOrWing(variantId, location, facing)`, store returned ShipAPIs
6. Record `spawnTime`. Set `contactMade = false`.
7. Transition to FIGHTING

**Ship positions:** Player ships at `(-2000, offset)` facing 0°. Enemy ships at `(2000, offset)` facing 180°. Offset by 800 units vertically for multiple ships.

### State: FIGHTING
Per-frame:
1. **Camera:** Center viewport on midpoint of all tracked ships via `ViewportAPI.setExternalControl(true)` + `viewport.set()`. This ensures the fight is visible.
2. **Heartbeat** every 60 frames
3. **Contact detection:** If `!contactMade`:
   - If `engine.isFleetsInContact()` → start combat timer (`matchupStartTime = now`), log contact
   - Else if `(now - spawnTime) > 30s` → force combat timer start (approach timeout for evasive AI)
4. **Custom win detection:** Count alive non-fighter ships per side from tracked lists. If one side has zero → other side wins.
5. **Timeout check:** If `contactMade` and `(now - matchupStartTime) > timeLimitSeconds` → TIMEOUT
6. On end: build result via `ResultWriter.buildMatchupResult()`, add to `allResults` array
7. Transition to CLEANING

**Timer logic:** The time limit only counts combat time, not approach time. Ships may take several seconds to fly toward each other after spawning. If one side is evasive and never engages, the 30-second approach timeout forces the combat timer to start anyway.

### State: CLEANING
1. Remove all entities: iterate `engine.getShips()`, `engine.getProjectiles()`, `engine.getMissiles()` → `engine.removeEntity()` each
2. Unregister old DamageTracker: `engine.getListenerManager().removeListener(tracker)`
3. Clear tracked ship lists
4. Wait 3 frames (`cleanupFramesLeft` counter) for engine to process removals
5. If more matchups → SPAWNING. If done → DONE.

### State: DONE
1. `ResultWriter.writeAllResults(allResults)` — write all results as JSON array
2. `ResultWriter.writeDoneSignal()` — write done signal file
3. Log completion
4. `System.exit(0)`

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

- Queue load failure → log error, `System.exit(1)`
- Ship spawn failure → log warning, skip ship
- Result write failure → log error, `System.exit(1)`
- Always null-check engine in `advance()`
