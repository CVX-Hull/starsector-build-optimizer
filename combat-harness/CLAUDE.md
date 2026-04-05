# Combat Harness Mod

Java mod for Starsector 0.98a that runs automated AI-vs-AI combat and exports results as JSON.

## Commands

- Build: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Deploy to game: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Launch game with mod: `cd game/starsector && ./starsector.sh`

## Architecture

One matchup per game launch. Flow:
1. Python writes `matchup.json` + `.variant` files to workdir
2. Game launches, player selects "Optimizer Arena" mission
3. MissionDefinition reads matchup.json, sets up fleets, attaches CombatHarnessPlugin
4. Plugin monitors combat, DamageTracker accumulates damage data
5. On combat end (or time limit): ResultWriter writes result.json atomically
6. System.exit(0)

## API Caveats

1. **`init(CombatEngineAPI)` is deprecated.** Always null-check engine in `advance()`. Don't assume `init()` fires before first `advance()`.

2. **DamageListener `source` types vary.** Can be `ShipAPI`, `DamagingProjectileAPI`, or `BeamAPI` — always use `instanceof` to resolve to the source ship.

3. **Time multiplier ceiling.** `engine.getTimeMult().modifyMult()` — keep <=5x, higher causes physics/collision issues.

4. **org.json has checked exceptions.** The game bundles an old `json.jar` where `put()`, `getString()`, `new JSONObject(String)` all throw checked `JSONException`. Modern org.json made these unchecked. Always add `throws JSONException` to methods that use org.json, or catch it explicitly. Do NOT assume modern unchecked semantics.

5. **MissionDefinition.java is Janino-compiled.** Compiled at runtime by the game's embedded Janino compiler. Keep it simple: no lambdas, no advanced generics, no var, no records. Use explicit for-loops with index variables, not enhanced for-each on arrays if Janino has trouble.

6. **Null-check API return values defensively.** `getFluxTracker()`, `getDisabledWeapons()`, `getVariant()`, `getHullSpec()` can theoretically return null for edge-case entities (drones, modules). Always provide fallback values.

7. **`getHullLevel()` is on `CombatEntityAPI`, not `ShipAPI` directly.** It's inherited — works on ShipAPI since ShipAPI extends CombatEntityAPI, but IDE autocomplete may not show it.

## Design Invariants

- matchup.json is the ONLY input; result.json is the ONLY output
- Result files are written atomically (write .tmp, rename)
- The plugin never modifies combat (no damage modification, no AI override)
- All config values have sane defaults (time_mult=3, time_limit=300, map=24000x18000)
- MissionDefinition gracefully handles missing matchup.json (shows error in briefing, doesn't crash)

## Pitfalls Encountered

- **org.json checked exceptions:** Wrote all code assuming modern unchecked org.json, hit 20+ compilation errors. The game's json.jar is ancient. Always verify the actual bundled library version.
- **JRE vs JDK:** The game ships a JRE (no javac). Need a system JDK for compilation. The game's JRE is Java 17, so target Java 17 compatibility.
- **Gradle version vs Java version:** Gradle 8.x doesn't support Java 26. Use Gradle 9.4+ with the system's Java 26 JDK.
