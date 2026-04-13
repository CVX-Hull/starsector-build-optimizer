# Combat Harness Mod

Java mod for Starsector 0.98a that runs automated AI-vs-AI combat and exports results as JSON.

## Commands

- Build: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Deploy to game: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Launch game with mod: `cd game/starsector && ./starsector.sh`
- Build + test + deploy: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew clean jar test deploy`

## Architecture

Single matchup per mission cycle. Flow:
1. Python writes `combat_harness_queue.json.data` to `saves/common/` (JSON array with 1 matchup config)
2. Game launches → TitleScreenPlugin detects queue on title screen → MenuNavigator auto-navigates to Optimizer Arena (resets on every return to title screen for persistent session reuse)
3. MissionDefinition (compiled in JAR) adds placeholder ships via `addToFleet()` for proper deployment/CR/AI
4. CombatHarnessPlugin swaps player ship loadout in-place from `BuildSpec` data (variant `clear()` + `addWeapon`/`addMod`)
5. Plugin state machine: INIT → SETUP → FIGHTING → DONE → WAITING
6. After matchup: ResultWriter writes results + done signal, Robot dismiss thread launched, then `endCombat()` called (Robot must launch before endCombat — engine stops calling advance() immediately after)
7. TitleScreenPlugin detects queue (new or same) → auto-navigates to mission → fresh MissionDefinition cycle

### Why single-matchup-per-mission (not batched spawning)
`spawnFleetMember()` mid-combat causes ships to retreat — the engine sets `directRetreat=true` at a level below the public API. No combination of `setDirectRetreat(false)`, `clearTasks()`, `reassign()`, `preventFullRetreat`, `setCanForceShipsToEngageWhenBattleClearlyLost`, no-op admiral AI, or per-frame `setRetreating(false,false)` overrides this. Only `addToFleet()` in MissionDefinition produces ships with proper AI behavior. See `docs/reference/tech-debt.md` for details.

## File Protocol

| File | SettingsAPI Name | Written By | Purpose |
|------|-----------------|------------|---------|
| `saves/common/combat_harness_queue.json.data` | `combat_harness_queue.json` | Python | Batch of matchup configs |
| `saves/common/combat_harness_results.json.data` | `combat_harness_results.json` | Java | Array of combat results |
| `saves/common/combat_harness_done.data` | `combat_harness_done` | Java | Completion signal |
| `saves/common/combat_harness_heartbeat.txt.data` | `combat_harness_heartbeat.txt` | Java | Liveness (every ~1s) |
| `saves/common/combat_harness_shutdown.data` | `combat_harness_shutdown` | Python | Signal: exit cleanly |

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
10. **`endCombat()` stops `advance()` immediately.** The engine stops calling `advance()` within the same or next frame after `endCombat()`. Any post-combat work must happen in the same frame, before the `endCombat()` call.
9. **Mission descriptor requires `icon.jpg`.** Game crashes if missing.

## Design Invariants

- `combat_harness_queue.json` (in saves/common/) is the ONLY input
- `combat_harness_results.json` (in saves/common/) is the ONLY output
- `combat_harness_done` signals completion; Python polls for this, not results
- The plugin never modifies combat (no damage modification, no custom AI)
- Single matchup per mission — `endCombat()` + Robot dismiss + TitleScreenPlugin restart between matchups
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
- **`spawnFleetMember()` retreat bug:** Ships spawned mid-combat via `spawnFleetMember()` always have `directRetreat=true`. No API call overrides this — the engine re-sets it below the public API level. Workaround: use `addToFleet()` in MissionDefinition (proper deployment) + in-place variant swap via `variant.clear()` + `addWeapon()`/`addMod()`.
- **`spawnShipOrWing()` with programmatic variants:** `createEmptyVariant()` does NOT register variants for `spawnShipOrWing()` lookup. Only `.variant` files loaded at startup are registered.
- **xdotool vs LWJGL:** `xdotool` click events do NOT work on LWJGL/OpenGL windows. Only `java.awt.Robot` (from inside the JVM) can interact with in-game UI. xdotool only works on the Swing launcher window.
- **`endCombat()` stops `advance()` immediately:** After calling `engine.endCombat()`, the engine stops calling the plugin's `advance()` method within the same or next frame. Any work that must happen after combat (e.g., launching Robot dismiss thread) must be done in the same frame, before the `endCombat()` call.
- **Title screen plugin `triggered` flag must reset:** Global plugins that use a `triggered` boolean to run once on the title screen must reset it when `GameState != TITLE`. Otherwise the plugin only fires on the first mission per game launch, breaking persistent session reuse.
- **Robot dismiss uses pixel-color polling:** After `endCombat()`, there's a ~1.5s white-flash transition before the results dialog renders. `dismissResults()` polls a 40x40 pixel region around the Continue button location using `Robot.createScreenCapture()`, checking for Starsector's cyan UI color (hue 185-210 in HSB). Clicks once the button color is detected (typically 1-3s). Falls back to blind click after 15s timeout. `Robot.createScreenCapture()` is NOT blocked by the security sandbox.
