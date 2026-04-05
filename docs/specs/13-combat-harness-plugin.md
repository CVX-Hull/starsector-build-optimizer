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
3. Apply time multiplier from first matchup config
4. Transition to SPAWNING

### State: SPAWNING
1. Get `queue.get(currentIndex)` → `currentConfig`
2. Spawn player ships: `engine.getFleetManager(FleetSide.PLAYER).spawnShipOrWing(variantId, location, facing)` → store returned ShipAPI in `playerShips` list
3. Spawn enemy ships: same for ENEMY side → store in `enemyShips` list
4. Create new DamageTracker, register via `engine.getListenerManager().addListener(tracker)`
5. Record `matchupStartTime = engine.getTotalElapsedTime(false)`
6. Transition to FIGHTING

**Ship positions:** Player ships at `(-2000, offset)` facing 0° (right). Enemy ships at `(2000, offset)` facing 180° (left). Offset vertically by 800 units for multiple ships.

### State: FIGHTING
Per-frame:
1. Update heartbeat every 60 frames
2. **Custom win detection:** Count alive non-fighter ships per side from tracked lists. If one side has zero → other side wins.
3. **Timeout check:** `(engine.getTotalElapsedTime(false) - matchupStartTime) > currentConfig.timeLimitSeconds`
4. On end: determine winner ("PLAYER"/"ENEMY"/"TIMEOUT"), compute duration, build result via `ResultWriter.buildMatchupResult()`, add to `allResults` array
5. Transition to CLEANING

### State: CLEANING
1. Remove all entities: iterate `engine.getShips()`, `engine.getProjectiles()`, `engine.getMissiles()` → `engine.removeEntity()` each
2. Unregister old DamageTracker: `engine.getListenerManager().removeListener(tracker)`
3. Clear `playerShips` and `enemyShips` lists
4. Wait 3 frames (`cleanupFramesLeft` counter) for engine to process removals
5. Increment `currentIndex`
6. If more matchups → SPAWNING. If done → DONE.

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
- Ship spawn failure → log warning, skip matchup, record error in result
- Result write failure → log error, `System.exit(1)`
- Always null-check engine in `advance()`
