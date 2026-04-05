# Combat Harness Mod

Java mod for Starsector 0.98a that runs automated AI-vs-AI combat and exports results as JSON.

## Commands

- Build: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Deploy to game: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Launch game with mod: `cd game/starsector && ./starsector.sh`
- Build + test + deploy: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew clean jar test deploy`

## Architecture

One matchup per game launch. Flow:
1. Python writes `combat_harness_matchup.json.data` to `saves/common/`
2. Python writes `.variant` files to `game/starsector/data/variants/`
3. Game launches, player selects "Optimizer Arena" mission
4. MissionDefinition (compiled in JAR) reads matchup config via SettingsAPI
5. Plugin monitors combat, DamageTracker accumulates damage data
6. On combat end (or time limit): ResultWriter writes `combat_harness_result.json.data`
7. System.exit(0)

## File I/O — Security Sandbox

Starsector blocks ALL `java.io.File` access from mod code. All file I/O must use the game's SettingsAPI:

```java
// Read from saves/common/ (game appends .data to filename)
String text = Global.getSettings().readTextFileFromCommon("combat_harness_matchup.json");
// Reads: saves/common/combat_harness_matchup.json.data

// Write to saves/common/
Global.getSettings().writeTextFileToCommon("combat_harness_result.json", jsonStr);
// Writes: saves/common/combat_harness_result.json.data

// Check existence
Global.getSettings().fileExistsInCommon("combat_harness_matchup.json");
// Checks: saves/common/combat_harness_matchup.json.data
```

**Critical:** The game appends `.data` to ALL filenames in `saves/common/`. When Python writes input files, it must use the `.data` extension. When Python reads output files, it must look for the `.data` extension.

**No atomic writes possible** through SettingsAPI — the game handles writing internally.

**Use flat filenames** with `combat_harness_` prefix. Subdirectories in `saves/common/` may not work with the SettingsAPI.

## API Caveats

1. **`init(CombatEngineAPI)` is deprecated.** Always null-check engine in `advance()`.

2. **DamageListener `source` types vary.** `ShipAPI`, `DamagingProjectileAPI`, or `BeamAPI` — use `instanceof`.

3. **Time multiplier ceiling.** Keep <=5x, higher causes physics/collision issues.

4. **org.json has checked exceptions.** The game's `json.jar` is ancient — `put()`, `getString()`, `new JSONObject(String)` all throw checked `JSONException`. Always add `throws JSONException` or catch explicitly.

5. **MissionDefinition must be in the JAR.** Janino (the game's runtime compiler) cannot resolve classes from mod JARs. Put MissionDefinition in the JAR with package `data.missions.optimizer_arena` — the game detects "already loaded from jar file" and skips Janino compilation.

6. **Null-check API return values defensively.** `getFluxTracker()`, `getDisabledWeapons()`, `getVariant()`, `getHullSpec()` can return null for edge-case entities.

7. **`getHullLevel()` is on `CombatEntityAPI`, inherited by `ShipAPI`.** IDE autocomplete may not show it directly on ShipAPI.

8. **`engine.getShips()` may not include destroyed ships.** Use `fleetManager.getAllEverDeployedCopy()` to get all ships including destroyed/disabled ones.

9. **Mission descriptor requires `icon.jpg`.** The game crashes if the icon field is present but the file is missing, or if the field is absent. Always include both the field and the file.

## Design Invariants

- `combat_harness_matchup.json` (in saves/common/) is the ONLY input
- `combat_harness_result.json` (in saves/common/) is the ONLY output
- The plugin never modifies combat (no damage modification, no AI override)
- All config values have sane defaults (time_mult=3, time_limit=300, map=24000x18000)
- MissionDefinition gracefully handles missing matchup.json (shows error in briefing)

## Pitfalls Encountered

- **Security sandbox:** All `java.io.File` usage blocked. Must use `Global.getSettings()` SettingsAPI methods.
- **`.data` extension:** Game appends `.data` to all `saves/common/` filenames. Python must account for this.
- **Janino class resolution:** Loose `.java` scripts can't import JAR classes. MissionDefinition must be compiled in the JAR.
- **org.json checked exceptions:** Modern org.json is unchecked; game's version is checked. Hit 20+ compilation errors.
- **JRE vs JDK:** Game ships a JRE (no javac). Need system JDK for compilation. Game's JRE is Java 17.
- **Gradle version:** Gradle 8.x doesn't support Java 26. Use Gradle 9.4+ with Java 26 JDK.
- **Destroyed ships missing:** `engine.getShips()` drops destroyed ships. Use `getAllEverDeployedCopy()` from fleet manager.
- **Missing icon.jpg:** Game requires icon in mission descriptor. Crashes if absent.
