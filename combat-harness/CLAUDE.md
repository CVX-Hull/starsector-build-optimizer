# Combat Harness Mod

Java mod for Starsector 0.98a that runs automated AI-vs-AI combat and exports results as JSON.

## Commands

- Build: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Deploy to game: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Launch game with mod: `cd game/starsector && ./starsector.sh`
- Build + test + deploy: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew clean jar test deploy`

## Architecture

Batched matchups per game launch. Flow:
1. Python writes `combat_harness_queue.json.data` to `saves/common/` (JSON array of matchup configs)
2. Python writes `.variant` files to `data/variants/`
3. Game launches → TitleScreenPlugin detects queue → MenuNavigator auto-navigates to Optimizer Arena
4. MissionDefinition (compiled in JAR) reads first matchup from queue via SettingsAPI
5. CombatHarnessPlugin state machine: INIT → SPAWNING → FIGHTING → CLEANING → ... → DONE
6. Per matchup: spawn ships, run combat with DamageTracker, custom win detection, entity cleanup
7. After all matchups: ResultWriter writes `combat_harness_results.json.data` + `combat_harness_done.data`
8. `System.exit(0)`

## File Protocol

| File | SettingsAPI Name | Written By | Purpose |
|------|-----------------|------------|---------|
| `saves/common/combat_harness_queue.json.data` | `combat_harness_queue.json` | Python | Batch of matchup configs |
| `saves/common/combat_harness_results.json.data` | `combat_harness_results.json` | Java | Array of combat results |
| `saves/common/combat_harness_done.data` | `combat_harness_done` | Java | Completion signal |
| `saves/common/combat_harness_heartbeat.txt.data` | `combat_harness_heartbeat.txt` | Java | Liveness (every ~1s) |

## File I/O — Security Sandbox

Starsector blocks ALL `java.io.File` access from mod code. All file I/O must use the game's SettingsAPI:

```java
// Read from saves/common/ (game appends .data to filename)
String text = Global.getSettings().readTextFileFromCommon("combat_harness_queue.json");
// Reads: saves/common/combat_harness_queue.json.data

// Write to saves/common/
Global.getSettings().writeTextFileToCommon("combat_harness_results.json", jsonStr);
// Writes: saves/common/combat_harness_results.json.data

// Check existence
Global.getSettings().fileExistsInCommon("combat_harness_queue.json");
// Checks: saves/common/combat_harness_queue.json.data
```

**Critical:** The game appends `.data` to ALL filenames in `saves/common/`. Python must write files WITH the `.data` extension. Use flat filenames with `combat_harness_` prefix.

## API Caveats

1. **`init(CombatEngineAPI)` is deprecated.** Always null-check engine in `advance()`.
2. **DamageListener `source` types vary.** `ShipAPI`, `DamagingProjectileAPI`, or `BeamAPI` — use `instanceof`.
3. **Time multiplier ceiling.** Keep <=5x, higher causes physics/collision issues.
4. **org.json has checked exceptions.** The game's `json.jar` is ancient — `put()`, `getString()`, `new JSONObject(String)` all throw checked `JSONException`.
5. **MissionDefinition must be in the JAR.** Janino can't resolve JAR classes. Package: `data.missions.optimizer_arena`.
6. **Null-check API return values defensively.** `getFluxTracker()`, `getDisabledWeapons()`, `getVariant()`, `getHullSpec()` can return null.
7. **`getHullLevel()` is on `CombatEntityAPI`, inherited by `ShipAPI`.** Check full inheritance chain.
8. **Track spawned ShipAPIs directly.** `getAllEverDeployedCopy()` accumulates across batched matchups. `engine.getShips()` drops destroyed ships.
9. **Mission descriptor requires `icon.jpg`.** Game crashes if missing.

## Design Invariants

- `combat_harness_queue.json` (in saves/common/) is the ONLY input
- `combat_harness_results.json` (in saves/common/) is the ONLY output
- `combat_harness_done` signals completion; Python polls for this, not results
- The plugin never modifies combat (no damage modification, no AI override)
- All config values have sane defaults (time_mult=3, time_limit=300, map=24000x18000)
- MissionDefinition gracefully handles missing queue (shows error in briefing)

## Pitfalls Encountered

- **Security sandbox:** All `java.io.File` usage blocked. Must use SettingsAPI.
- **`.data` extension:** Game appends `.data` to all `saves/common/` filenames.
- **Janino class resolution:** Loose `.java` scripts can't import JAR classes.
- **org.json checked exceptions:** Modern org.json is unchecked; game's version is checked.
- **JRE vs JDK:** Game ships a JRE (no javac). Need system JDK. Game's JRE is Java 17.
- **Gradle version:** Gradle 8.x doesn't support Java 26. Use Gradle 9.4+.
- **Fleet manager accumulation:** `getAllEverDeployedCopy()` accumulates across batched matchups.
- **Missing icon.jpg:** Game requires icon in mission descriptor. Crashes if absent.
