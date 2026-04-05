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

- `init(CombatEngineAPI)` is deprecated — always null-check engine in `advance()`
- DamageListener `source` can be ShipAPI, DamagingProjectileAPI, or BeamAPI — use instanceof
- `engine.getTimeMult().modifyMult()` — keep <=5x, higher causes physics issues
- Starsector uses org.json (from json.jar), NOT Gson or Jackson
- MissionDefinition.java is compiled by Janino at runtime — keep it simple, avoid advanced Java features

## Design Invariants

- matchup.json is the ONLY input; result.json is the ONLY output
- Result files are written atomically (write .tmp, rename)
- The plugin never modifies combat (no damage modification, no AI override)
- All config values have sane defaults (time_mult=3, time_limit=300, map=24000x18000)
