# Combat Harness Plugin Specification

EveryFrameCombatPlugin that orchestrates combat monitoring, data collection, and result writing. Defined in `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`.

## Class

Extends `BaseEveryFrameCombatPlugin`. No-arg constructor (config is loaded from `saves/common/` via SettingsAPI in `init()`).

## State

```java
private CombatEngineAPI engine;
private MatchupConfig config;
private DamageTracker damageTracker;
private boolean resultsWritten = false;
private int frameCount = 0;
```

## Lifecycle

### `init(CombatEngineAPI engine)`

1. Store engine reference (null-check)
2. Load config: `MatchupConfig.loadFromCommon()` (reads from `saves/common/`)
3. Apply time multiplier: `engine.getTimeMult().modifyMult("harness", config.timeMult)`
4. Create DamageTracker, register: `engine.getListenerManager().addListener(damageTracker)`
5. Log startup

### `advance(float amount, List<InputEventAPI> events)`

1. If `engine == null` or `engine.isPaused()` or `config == null` → return
2. `frameCount++`
3. If `frameCount % 60 == 0` → `ResultWriter.writeHeartbeat(elapsed)` (via SettingsAPI)
4. Check combat end:
   - `engine.isCombatOver()` → normal end
   - `engine.getTotalElapsedTime(false) > config.timeLimitSeconds` → timeout
5. If combat ended AND `!resultsWritten`:
   a. `ResultWriter.writeResult(engine, damageTracker, config, timedOut)` (via SettingsAPI)
   b. `resultsWritten = true`
   c. Log results
   d. `System.exit(0)` (or `System.exit(1)` on write failure)

## Error Handling

- If `matchup.json` missing or invalid → log error, do not crash (config stays null, advance() returns early)
- If result writing fails → log error, `System.exit(1)`
- Always null-check engine before accessing it in `advance()`
