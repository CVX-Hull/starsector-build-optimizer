# Combat Harness Plugin Specification

EveryFrameCombatPlugin that orchestrates combat monitoring, data collection, and result writing. Defined in `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`.

## Class

Extends `BaseEveryFrameCombatPlugin`. Constructed with a `File workdir` parameter by the MissionDefinition.

## State

```java
private CombatEngineAPI engine;
private MatchupConfig config;
private DamageTracker damageTracker;
private boolean resultsWritten = false;
private int frameCount = 0;
private File workdir;
```

## Lifecycle

### `init(CombatEngineAPI engine)`

1. Store engine reference
2. Load config: `MatchupConfig.fromFile(new File(workdir, "matchup.json"))`
3. Apply time multiplier: `engine.getTimeMult().modifyMult("harness", config.timeMult)`
4. Create DamageTracker, register: `engine.getListenerManager().addListener(damageTracker)`
5. Log: "Combat Harness initialized for matchup {matchup_id}"

### `advance(float amount, List<InputEventAPI> events)`

1. If `engine == null` or `engine.isPaused()` → return
2. `frameCount++`
3. If `frameCount % 60 == 0` → write heartbeat file (`System.currentTimeMillis() + " " + engine.getTotalElapsedTime(false)`)
4. Check combat end condition:
   - `engine.isCombatOver()` → normal end
   - `engine.getTotalElapsedTime(false) > config.timeLimitSeconds` → timeout
5. If combat ended AND `!resultsWritten`:
   a. Determine `timedOut` flag
   b. `ResultWriter.writeResult(engine, damageTracker, config, workdir, timedOut)`
   c. `resultsWritten = true`
   d. Log: "Results written for matchup {matchup_id}"
   e. `System.exit(0)`

## Heartbeat File

Written to `workdir/heartbeat.txt` every 60 frames:
```
<timestamp_ms> <elapsed_seconds>
```

## Error Handling

- If `matchup.json` is missing or invalid → log error, do not crash the game
- If result writing fails → log error, attempt `System.exit(1)`
- Always null-check engine before accessing it in `advance()`
