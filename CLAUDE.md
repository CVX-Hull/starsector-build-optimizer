# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

- **Phase 1** (complete): Data layer — game data parsing, search space, constraint repair, heuristic scoring, variant generation.
- **Phase 2** (complete): Java combat harness mod — automated AI-vs-AI combat simulation with JSON result export.
- **Phase 3** (complete): Instance manager — N parallel Starsector instances via Xvfb, batch evaluation, health monitoring.
- **Phase 3.5** (complete): Stochastic curtailment (TTD-ratio extrapolation) + data-driven timeout tuning (Weibull AFT).
- **Phase 4** (complete): Optimizer integration — Optuna TPE, opponent pool, heuristic warm-start.

## Commands

- Run Python tests: `uv run pytest tests/ -v`
- Run single test file: `uv run pytest tests/test_parser.py -v`
- Run single test: `uv run pytest tests/test_models.py::test_weapon_sustained_dps -v`
- Build combat harness: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Run Java tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Deploy mod: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run optimizer (heuristic-only): `uv run python scripts/run_optimizer.py --hull eagle --game-dir game/starsector --heuristic-only`
- Game data location: `game/starsector/data/` (gitignored, not in repo)
- See `combat-harness/CLAUDE.md` for Java-specific instructions

## Workflow — DDD + TDD

For every module: write spec doc (`docs/specs/`) first, then tests, then implementation. Never implement without a spec and failing tests first.

## Design Principles

1. **Single source of truth for game knowledge.** All hardcoded hullmod effects, incompatibilities, and game constants live in `hullmod_effects.py`. Never duplicate hullmod logic in scorer, repair, or search_space.

2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult` are frozen dataclasses. Repair returns new instances. `Build.hullmods` is `frozenset`.

3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals (with `vent_fraction`, potentially infeasible) go through `repair_build()` to produce valid `Build` objects. Everything downstream of repair works with concrete, valid Builds.

4. **Data-driven over logic-driven.** Hullmod effects are a declarative `HULLMOD_EFFECTS` registry dict, not scattered if-else chains. Adding a hullmod effect = one dict entry.

5. **Forward compatibility — warn, don't crash.** Unknown enum values from future game versions: `from_str()` returns `None`, parser logs warning and skips the record. Never crash on unknown game data.

6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics. These become Phase 5 behavior descriptors and Phase 6 features without refactoring.

7. **Verify game facts against actual game files, never assume.** The game data files at `game/starsector/data/` are the ground truth. When working with game-specific knowledge (hullmod IDs, effect values, CSV column meanings, slot types, damage formulas, tag conventions), always verify against the actual data files before hardcoding or referencing. The reference docs in `docs/reference/` are secondary and may be stale. Specific pitfalls encountered:
   - Hullmod IDs are non-obvious (e.g., `hardenedshieldemitter` not `hardenedshields`, `frontshield` not `makeshift_shield_generator`). Look up in `data/hullmods/hull_mods.csv`.
   - The `type` column in `weapon_data.csv` is **damage type** (KINETIC, HE, ENERGY, FRAG), NOT weapon type. Weapon type (BALLISTIC/ENERGY/MISSILE) comes from `.wpn` files.
   - The `designation` column in `ship_data.csv` is a role string (e.g., "Battleship"), NOT hull size. Hull size comes from `hullSize` in `.ship` JSON files.
   - Hullmod effects like Safety Overrides have non-obvious formulas (range compression, not a hard cap). Check the wiki or game code when adding hullmod effects.
   - Tag conventions change between game versions (e.g., logistics detection uses `"Logistics"` in `uiTags`, not `"logistics"` in `tags`).

8. **Verify bundled library versions, never assume modern semantics.** The game bundles old versions of common libraries (e.g., `json.jar` is an ancient org.json with checked `JSONException` on `put()`/`getString()`). When writing Java code against game-bundled libraries, check the actual JAR for method signatures and exception types. Do not assume the modern version's API. Similarly, Starsector API methods inherited from parent interfaces (e.g., `getHullLevel()` on `CombatEntityAPI`) may not appear in the child interface's source — check the full inheritance chain.

9. **Cross-check spec against implementation field-by-field.** When a spec defines a JSON schema (e.g., result.json), every field in the schema must appear in both the Java writer and the Python dataclass. Schema drift between spec, Java, and Python is easy to miss — especially optional/aggregate fields like retreat counts. Reference the spec document during implementation, don't rely on memory.

10. **Starsector's security sandbox blocks `java.io.File`.** All mod file I/O must use `Global.getSettings()` methods: `readTextFileFromCommon(name)`, `writeTextFileToCommon(name, data)`, `fileExistsInCommon(name)`. Files go to `<starsector>/saves/common/` and the game **appends `.data`** to all filenames. Python must write files with the `.data` extension for the game to find them. Use flat filenames with a `combat_harness_` prefix (subdirectories may not work).

11. **Starsector compiles loose `.java` files via Janino, but Janino can't resolve JAR classes.** If a mission script imports classes from your mod JAR, Janino compilation fails. Solution: put the MissionDefinition class in the JAR with the correct package (`data.missions.<name>`) — the game detects "already loaded from jar file" and skips Janino compilation.

## Design Invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications
- `HULLMOD_EFFECTS`, `INCOMPATIBLE_PAIRS`, `HULL_SIZE_RESTRICTIONS` are the ONLY locations for hardcoded hullmod game knowledge
- All game constants (MAX_VENTS, damage multipliers, etc.) are in `hullmod_effects.py`, not scattered
- **No magic numbers in function bodies.** Timeouts, coordinates, polling intervals, thresholds, and batch sizes must live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CurtailmentMonitor` params) — never as literals in function bodies. This ensures values are discoverable, overridable in tests, and documented in one place.

## Project Layout

```
src/starsector_optimizer/          # Python modules
├── models.py                      # Dataclasses + enums (ShipHull, Weapon, Build, etc.)
├── hullmod_effects.py             # Game constants, hullmod effect registry
├── parser.py                      # CSV + loose JSON → model objects
├── search_space.py                # Per-hull weapon/hullmod compatibility
├── repair.py                      # Constraint enforcement (optimizer→domain boundary)
├── scorer.py                      # Heuristic scoring → ScorerResult
├── variant.py                     # Build → .variant JSON
├── calibration.py                 # Random build generation + feature extraction
├── estimator.py                   # Throughput + cost estimation for simulation campaigns
├── result_parser.py               # Parse combat result JSON ↔ Python dataclasses
├── instance_manager.py            # Manage N parallel Starsector game instances
├── curtailment.py                 # Stochastic curtailment (TTD-ratio extrapolation)
├── timeout_tuner.py               # Data-driven timeout prediction (Weibull AFT)
├── opponent_pool.py               # Diverse opponent pool per hull size
└── optimizer.py                   # Optuna integration, ask-tell loop, warm-start

combat-harness/                    # Java combat harness mod
├── CLAUDE.md                      # Java-specific instructions
├── build.gradle.kts               # Gradle build
├── src/main/java/starsector/combatharness/
│   ├── MatchupConfig.java         # Single matchup config POJO
│   ├── MatchupQueue.java          # Batch queue — reads JSON array from saves/common/
│   ├── DamageTracker.java         # DamageListener — per-ship damage accumulation
│   ├── ResultWriter.java          # Batch results + done signal via SettingsAPI
│   ├── CombatHarnessPlugin.java   # State machine: INIT→SPAWNING→FIGHTING→CLEANING→DONE
│   ├── CombatHarnessModPlugin.java # BaseModPlugin — mod entry point
│   ├── TitleScreenPlugin.java     # Auto-navigates to mission on title screen
│   └── MenuNavigator.java         # java.awt.Robot menu clicking (1920x1080 calibrated)
├── src/main/java/data/missions/optimizer_arena/
│   └── MissionDefinition.java     # Mission setup (compiled in JAR, not Janino)
└── mod/                           # Deployed to game/starsector/mods/combat-harness/
    └── mod_info.json

# I/O paths (game appends .data to all saves/common/ filenames):
#   Input:  saves/common/combat_harness_queue.json.data
#   Output: saves/common/combat_harness_results.json.data
#   Done:   saves/common/combat_harness_done.data
#   Health: saves/common/combat_harness_heartbeat.txt.data

docs/
├── specs/                         # DDD module specifications (drive implementation)
└── reference/                     # Background research and game mechanics reference
```
