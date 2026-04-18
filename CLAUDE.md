# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

- **Phase 1** (complete): Data layer — game data parsing, search space, constraint repair, heuristic scoring, variant generation.
- **Phase 2** (complete): Java combat harness mod — automated AI-vs-AI combat simulation with JSON result export.
- **Phase 3** (complete): Instance manager — N parallel Starsector instances via Xvfb, batch evaluation, health monitoring.
- **Phase 3.5** (complete): Data-driven timeout tuning (Weibull AFT).
- **Phase 4** (complete): Optimizer integration — Optuna TPE/CatCMAwM, opponent pool, heuristic warm-start, parameter importance.
- **Phase 5** (5A–5C complete; 5D and 5E planned; 5F deferred): Signal quality.
  - **5A** TWFE deconfounding + control variate + rank shape; **5B** WilcoxonPruner + ASHA; **5C** anchor-first + incumbent-overlap opponent selection — all shipped and documented in `docs/reference/phase5-signal-quality.md`, `phase5a-deconfounding-theory.md`, `phase5c-opponent-curriculum.md`.
  - **5D** (planned) — EB shrinkage of A2: replaces the scalar control variate with empirical-Bayes shrinkage of α̂ toward an 8-covariate heuristic regression prior (HN + triple-goal rank correction; closed-form two-level Gaussian model). **Fusion paradigm** — `α̂_TWFE` and the 8 prior features are treated as noisy measurements of the same latent α and combined by Bayes rule. Covariate set: 4 engine-computed `MutableShipStats` reads (eff_max_flux, eff_flux_dissipation, eff_armor_rating, eff_hull_hp, emitted by a new Java-side SETUP hook in `CombatHarnessPlugin`) + 3 Python raw primitives (total_weapon_dps, n_hullmods, op_used_fraction) + `composite_score`. Feature count selected by sweep (`experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md`) at the p≈8 diminishing-returns knee; safe at N≥200 (8h overnight) to N≈2000 (3-day run) given the 27 trials/hr throughput. The original conditioning-paradigm v1 (CUPED / FWL / PDS lasso / ICP) was refuted empirically: synthetic Δρ = −0.35 vs plain TWFE (p<0.0001, n=20), Hammerhead LOOO Δρ = −0.13, missed +0.02 ship gate by 7×. Root cause: bad-control pattern (Cinelli-Forney-Pearl 2022 "Case 8": the scorer components are noisy proxies of the estimand α, not orthogonal covariates of Y). The replacement design passes the same gate at Δρ = +0.036 vs A0 and +0.057 vs A (at p=16; p=8 projected ≈ +0.32 on synthetic). See `docs/reference/phase5d-covariate-adjustment.md` and `experiments/phase5d-covariate-2026-04-17/`.
  - **5E** (planned) — Box-Cox replaces the top-quartile-clamped rank shape at A3. Simulation-validated (Δρ = +0.070, ceiling 25.3% → 0.4%) in `experiments/signal-quality-2026-04-17/`; see `docs/reference/phase5e-shape-revision.md`.
  - **5F** (deferred) — adversarial opponent curriculum (PSRO-style pool growth). Research complete; revisit post-5E if exploit convergence persists.

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

## Workflow Gates

For every module: spec first (`docs/specs/`), then tests, then implementation. The four skills in `.claude/skills/` enforce quality at each gate:

| Gate | When | Skill |
|------|------|-------|
| **Planning** | Before any non-trivial implementation | Enter plan mode. Follow `ddd-tdd` lifecycle. |
| **Plan review** | Before calling ExitPlanMode | Run the `plan-review` checklist: self-review + 3 parallel audit sub-agents. |
| **Implementation** | During coding | Follow `ddd-tdd` step 3: one concern per change, verify after each module. |
| **Post-implementation** | After all implementation tasks complete | Run the `post-impl-audit` checklist: mechanical checks + 3 parallel audit sub-agents. |
| **Invariant check** | When reviewing any code change | Reference `design-invariants` for the full checklist. |

For Starsector Java modding specifics (sandbox, file I/O, Janino, combat plugin patterns), see `.claude/skills/starsector-modding.md`.

## Design Principles

1. **Single source of truth for game knowledge.** All hardcoded hullmod effects, incompatibilities, and game constants live in `hullmod_effects.py`. Never duplicate hullmod logic in scorer, repair, or search_space.

2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult`, `CombatFitnessConfig`, `ImportanceResult` are frozen dataclasses. Repair returns new instances. `Build.hullmods` is `frozenset`.

3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals (potentially infeasible) go through `repair_build()` to produce valid `Build` objects. Everything downstream of repair works with concrete, valid Builds.

4. **Data-driven over logic-driven.** Hullmod effects are a declarative `HULLMOD_EFFECTS` registry dict, not scattered if-else chains. Adding a hullmod effect = one dict entry.

5. **Forward compatibility — warn, don't crash.** Unknown enum values from future game versions: `from_str()` returns `None`, parser logs warning and skips the record. Never crash on unknown game data.

6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics. These become Phase 6 behavior descriptors and Phase 7 features without refactoring.

7. **Verify game facts against actual game files, never assume.** The game data files at `game/starsector/data/` are the ground truth. Specific pitfalls: hullmod IDs are non-obvious (check `hull_mods.csv`), `weapon_data.csv` `type` is damage type not weapon type, `ship_data.csv` `designation` is a role string not hull size. See `.claude/skills/starsector-modding.md` for the full list.

## Design Invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications
- `HULLMOD_EFFECTS`, `INCOMPATIBLE_PAIRS`, `HULL_SIZE_RESTRICTIONS` are the ONLY locations for hardcoded hullmod game knowledge
- All game constants (MAX_VENTS, damage multipliers, etc.) are in `hullmod_effects.py`, not scattered
- **No magic numbers in function bodies.** Timeouts, coordinates, polling intervals, thresholds, and batch sizes must live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`) — never as literals in function bodies.

For the full mechanical checklist with runnable grep commands, see `.claude/skills/design-invariants.md`.

## Project Layout

```
src/starsector_optimizer/          # Python modules
├── models.py                      # Dataclasses + enums (ShipHull, Weapon, Build, BuildSpec, CombatFitnessConfig, etc.)
├── hullmod_effects.py             # Game constants, hullmod effect registry
├── parser.py                      # CSV + loose JSON → model objects
├── search_space.py                # Per-hull weapon/hullmod compatibility
├── repair.py                      # Constraint enforcement (optimizer→domain boundary)
├── scorer.py                      # Heuristic scoring → ScorerResult
├── variant.py                     # Build ↔ .variant JSON / BuildSpec (generate, load, stock builds, build_to_build_spec)
├── calibration.py                 # Random build generation + feature extraction
├── estimator.py                   # Throughput + cost estimation for simulation campaigns
├── result_parser.py               # Parse combat result JSON ↔ Python dataclasses
├── instance_manager.py            # Manage N parallel Starsector game instances
├── timeout_tuner.py               # Data-driven timeout prediction (Weibull AFT)
├── combat_fitness.py              # Hierarchical composite combat fitness score
├── opponent_pool.py               # Diverse opponent pool per hull size
├── deconfounding.py               # TWFE decomposition for schedule-adjusted build quality
├── importance.py                  # Parameter importance analysis (fANOVA) + fixed params
└── optimizer.py                   # Optuna integration, ask-tell loop, warm-start

combat-harness/                    # Java combat harness mod
├── CLAUDE.md                      # Java-specific instructions
├── build.gradle.kts               # Gradle build
├── src/main/java/starsector/combatharness/
│   ├── MatchupConfig.java         # Single matchup config POJO + BuildSpec inner class
│   ├── MatchupQueue.java          # Batch queue — reads JSON array from saves/common/
│   ├── VariantBuilder.java        # Programmatic ShipVariantAPI construction from BuildSpec
│   ├── DamageTracker.java         # DamageListener — per-ship damage accumulation
│   ├── ResultWriter.java          # Batch results + done signal via SettingsAPI
│   ├── CombatHarnessPlugin.java   # State machine: INIT→SETUP→FIGHTING→DONE→WAITING
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

.claude/skills/                    # Quality gate skills
├── ddd-tdd.md                     # Spec → test → impl → verify lifecycle
├── design-invariants.md           # Full invariant checklist with mechanical checks
├── plan-review.md                 # Pre-approval review (self-review + 3 audit agents)
├── post-impl-audit.md             # Post-implementation verification (checks + 3 audit agents)
└── starsector-modding.md          # Java modding pitfalls (sandbox, file I/O, Janino, etc.)
```
