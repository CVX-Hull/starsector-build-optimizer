# Implementation Roadmap

Phased build plan with dependencies, technology choices, and build order.

---

## Phase Overview

```
Phase 1: Data Layer + Heuristic ──────────────── No game integration needed
Phase 2: Java Combat Harness Mod ─────────────── Requires Starsector + Java
Phase 3: Instance Manager ────────────────────── Requires Linux + Xvfb
Phase 4: Optimizer Integration ───────────────── Connects everything
Phase 5: Quality-Diversity ───────────────────── Build archetype mapping
Phase 6: Neural Surrogate ───────────────────── ML-powered prediction
```

Each phase is independently useful and can be shipped/tested before proceeding.

---

## Phase 1: Data Layer + Heuristic Scorer ✓ COMPLETE

### Goal
Parse Starsector game data, build constraint-aware search spaces, implement heuristic scoring, and generate .variant files.

### Status
Complete. 210 tests passing across 9 test files. All modules implemented with DDD+TDD.

### Dependencies
- Python 3.10+
- numpy, pandas (data parsing)
- Access to Starsector game data files (ship_data.csv, weapon_data.csv, hull_mods.csv, *.ship)

### Deliverables

1. **Game data parser**
   - Parse ship_data.csv → ShipHull objects
   - Parse weapon_data.csv → Weapon objects
   - Parse hull_mods.csv → HullMod objects
   - Parse *.ship files → WeaponSlot definitions per hull
   - Support loading mod data directories

2. **Search space builder**
   - Given a hull, generate the set of compatible weapons per slot
   - Generate eligible hullmods (filtering incompatibilities)
   - Define ConfigSpace with conditionals (for SMAC3)
   - Define parameter list (for Bounce, CatCMA)

3. **Repair operator**
   - Greedy OP budget repair (drop lowest value-per-OP item)
   - Hullmod incompatibility enforcement
   - Logistics hullmod limit enforcement
   - Vent/cap allocation from remaining OP

4. **Heuristic scorer**
   - Flux balance score
   - DPS by damage type
   - Flux efficiency
   - Effective HP (armor + shield + hull)
   - Range coherence
   - Composite weighted score

5. **Variant generator**
   - Convert build specification → .variant JSON
   - Assign weapons to groups (autofire by default)
   - Handle built-in weapons/hullmods

6. **Calibration pipeline**
   - Generate diverse build samples
   - (Once simulation is available) Fit regression from static metrics to sim outcomes

### Testing
- Unit tests for parser (compare parsed values to known wiki values)
- Unit tests for constraint enforcement (verify no infeasible builds pass repair)
- Heuristic scorer sanity checks (known-good builds score higher than known-bad)
- Variant file validation (load generated variants in game's refit screen)

---

## Phase 2: Java Combat Harness Mod ✓ COMPLETE

### Goal
A Starsector mod that runs one automated AI-vs-AI combat matchup per game launch and exports results as JSON. Phase 3 (Instance Manager) handles orchestration and parallelism.

### Status
Complete. 21 JUnit tests passing. Live-tested: Eagle vs Dominator Assault, result.json produced with full per-ship damage/flux/armor stats.

### Design Decisions
- **One matchup per launch.** No API to chain missions. Game exits after writing results.
- **File I/O via SettingsAPI.** Starsector's security sandbox blocks `java.io.File`. All reads/writes go through `Global.getSettings().readTextFileFromCommon()`/`writeTextFileToCommon()`, which operate on `saves/common/`. The game appends `.data` to all filenames.
- **MissionDefinition compiled in JAR.** Janino (the game's runtime compiler) cannot resolve imports from mod JARs. MissionDefinition is compiled with package `data.missions.optimizer_arena` — the game detects "already loaded from jar file" and skips Janino.
- **Ship data via fleet manager.** `engine.getShips()` may drop destroyed ships. Use `getAllEverDeployedCopy()` from fleet manager to include all ships.

### Dependencies
- Java 17 (Starsector 0.98a bundled JRE for runtime, system JDK 17+ for compilation)
- Starsector API (starfarer.api.jar)
- Gradle 9.4+ build system
- Starsector game installation (activated)

### Deliverables

1. **Mod skeleton** (`combat-harness/`)
   - Gradle project, mod_info.json, BaseModPlugin, mission registration
   - Deploy task copies to game/starsector/mods/

2. **Matchup config parser** (MatchupConfig.java)
   - Reads from `saves/common/combat_harness_matchup.json.data` via SettingsAPI
   - Validation with sane defaults

3. **Combat harness plugin** (CombatHarnessPlugin.java)
   - Time acceleration, DamageTracker registration, combat end detection
   - Writes result via SettingsAPI, then System.exit(0)

4. **Damage tracker** (DamageTracker.java)
   - Per-ship damage accumulation via DamageListener
   - Source identification via instanceof

5. **Result writer** (ResultWriter.java)
   - Writes to `saves/common/combat_harness_result.json.data` via SettingsAPI
   - Per-ship stats from fleet manager (including destroyed ships)

6. **Mission definition** (MissionDefinition.java — compiled in JAR)
   - Loads config via SettingsAPI, sets up both fleets with AI control

### Testing
- 21 JUnit tests: MatchupConfig (10), DamageTracker (7), ResultWriter (4)
- Live game test verified: combat runs at 3x speed, result.json with damage stats, game exits cleanly

---

## Phase 3: Instance Manager

### Goal
Launch and manage N parallel Starsector instances for batch combat evaluation.

### Implementation

**Modules:**
- `src/starsector_optimizer/result_parser.py` — Parse combat result JSON ↔ Python dataclasses, write queue files
- `src/starsector_optimizer/instance_manager.py` — `InstancePool` manages N parallel game instances

**Key classes:**
- `InstanceConfig` — Pool configuration (game_dir, num_instances, batch_size, timeouts)
- `InstancePool` — Main class: `setup()` → `evaluate(matchups)` → `teardown()`
- `GameInstance` — Tracks a single game instance (process handles, state, work directory)

**Per-instance work directory:** Symlinks to shared game files, real directories for `saves/`, `data/config/`, `data/variants/`, `mods/`. Total ~4MB per instance.

**Health monitoring:** Poll heartbeat file mtime every 1s. Startup timeout 90s, heartbeat timeout 120s. Crash detection via process exit + no done signal. Auto-restart up to 3 times.

**Xvfb:** Each instance gets its own display (`:100`, `:101`, ...) at 1920x1080x24 to match MenuNavigator's calibrated Robot coordinates.

**Game activation:** Stored in `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (user-global). Shared automatically across instances.

### Testing
- 22 unit tests (mocked subprocess, tmp_path work directories)
- 14 result parser tests
- Integration: Launch 2 instances locally, verify both return results

---

## Phase 4: Optimizer Integration

### Goal
Connect the optimizer engines (Bounce, SMAC3, CatCMA) to the evaluation pipeline.

### Dependencies
- Phase 1 (data layer, search space, repair operator, variant generator)
- Phase 2 + 3 (combat harness + instance manager) for simulation evaluation
- Python libraries: bounce, smac, cmaes, optuna

### Deliverables

1. **Evaluation dispatcher**
   - Accept build specification from optimizer
   - Apply repair operator
   - Route to appropriate fidelity level (heuristic / short sim / full sim)
   - For simulation: generate variant, queue matchup, await result
   - Return score to optimizer

2. **Bounce integration**
   - Define Bounce parameter space from hull search space
   - Batch evaluation (ask 16, evaluate in parallel, tell results)
   - Track best found build

3. **SMAC3 integration**
   - Define ConfigSpace with conditionals and forbidden clauses
   - Configure SMAC facade with n_workers
   - Repair operator in objective function wrapper

4. **CatCMA integration**
   - Define z_space (integers) and c_space (categoricals)
   - Ask-tell loop with population_size = n_instances
   - OP budget penalty in fitness

5. **Multi-fidelity pipeline**
   - Phase 1 screening: heuristic on large sample
   - Phase 2 screening: short sim on top candidates
   - Phase 3 optimization: Bounce/SMAC3 with full sim
   - Phase 4 validation: racing on top candidates

6. **Result logging and visualization**
   - Log all evaluations (build spec, fidelity, score, metrics)
   - Convergence curves (best score over evaluations)
   - Build comparison table (top N builds side by side)

### Testing
- Run Bounce on heuristic-only (no simulation) to verify optimizer integration
- Run SMAC3 with heuristic to verify ConfigSpace conditionals work
- End-to-end: optimize a frigate (small search space) with 2 simulation instances
- Verify repair operator produces feasible builds consistently
- Compare optimizer results to known community builds

---

## Phase 5: Quality-Diversity

### Goal
Discover the full map of viable build archetypes using MAP-Elites.

### Dependencies
- Phase 1 (data layer, heuristic scorer)
- Phase 4 (optimizer integration) for simulation-based QD
- Python libraries: ribs (pyribs), cmaes

### Deliverables

1. **Behavior descriptor computation**
   - Engagement range (DPS-weighted weapon range)
   - Survivability style (shield EHP fraction)
   - Damage type profile (kinetic DPS fraction)
   - Offense/defense ratio

2. **CMA-MAE with pyribs**
   - CVT archive (5000 cells, 4D)
   - Custom CatCMA emitter for mixed variables
   - Heuristic fitness function

3. **Two-phase pipeline**
   - Phase A: Heuristic illumination (200K+ evals, ~1 hour)
   - Phase B: Simulation validation (2000-5000 evals, ~2-5 days)

4. **Surrogate refinement (DSA-ME pattern)**
   - Train neural network on sim results
   - Re-illuminate with corrected fitness
   - Validate new elites

5. **Visualization**
   - 2D heatmap slices of the 4D archive
   - Build cards for representative archetypes
   - Coverage metrics (% cells filled, mean fitness)

### Testing
- Run heuristic-only QD on a frigate (small space, fast iteration)
- Verify archive fills with diverse builds (not just copies of the same build)
- Verify behavior descriptors correctly separate known archetypes
- Compare discovered archetypes to community-known builds

---

## Phase 6: Neural Surrogate

### Goal
Train ML models that predict combat outcomes from build parameters, reducing simulation dependency.

### Dependencies
- Phase 1 (data layer, feature engineering)
- Phase 4 (accumulated simulation data — 500+ results)
- Python libraries: tabpfn, catboost, scikit-learn, pytorch (optional)

### Deliverables

1. **Feature engineering pipeline**
   - Raw build features (weapon IDs, hullmod flags, vent/cap counts)
   - Derived features (total DPS, flux balance, EHP, range profile, damage fractions)
   - Enemy-relative features (DPS ratio, range advantage, matchup quality)

2. **Model training**
   - TabPFN-2.5 (Phase 1 surrogate, 0-500 samples)
   - Random Forest ensemble (uncertainty via inter-tree variance)
   - CatBoost (500-2000 samples, native categorical handling)
   - Multi-output: separate models per outcome (win rate, TTK, damage)

3. **Uncertainty quantification**
   - RF variance (inter-tree disagreement)
   - TabPFN predictive distribution
   - Ensemble variance (across model types)

4. **Active learning loop**
   - Use surrogate uncertainty to select most informative builds to simulate
   - Retrain after each batch of new sim results
   - Track model accuracy over time (R², rank correlation)

5. **Integration with optimizer**
   - Use surrogate as Fidelity 0.5 (between heuristic and simulation)
   - Composite surrogate: heuristic + neural correction
   - BO acquisition functions use surrogate uncertainty

### Testing
- Train on 200 sim results, predict held-out 50: measure R² and rank correlation
- Compare TabPFN vs CatBoost vs RF at 200, 500, 1000, 2000 samples
- Verify uncertainty is calibrated (builds with high predicted uncertainty have high actual variance)
- End-to-end: run optimizer using surrogate as fitness, verify it finds good builds

---

## Technology Stack Summary

| Component | Primary | Alternatives |
|---|---|---|
| Language (data/optimizer) | Python 3.10+ | — |
| Language (game mod) | Java 17 | — |
| Game data parsing | pandas, json | — |
| Search space definition | ConfigSpace (SMAC3) | Optuna search space |
| Primary optimizer | Bounce | SMAC3, CatCMAwM |
| Quality-diversity | pyribs + cmaes | QDax (GPU), qdpy |
| Multi-fidelity | MFES-HB, BoTorch MF-KG | rMFBO |
| Neural surrogate | TabPFN, CatBoost, RF | FT-Transformer |
| Benchmarking | MCBO framework | — |
| Instance management | Python subprocess + Xvfb | Docker (heavier) |
| Visualization | matplotlib, plotly | — |
| Data storage | JSON files, SQLite | PostgreSQL (overkill) |

---

## Build Order Rationale

1. **Phase 1 first** because everything depends on it, and it requires no game integration
2. **Phase 2 next** because it's the most novel engineering (Java mod)
3. **Phase 3** connects Phases 1 and 2 into a working pipeline
4. **Phase 4** adds intelligence (optimization) to the pipeline
5. **Phase 5** is the most exciting output (build archetype maps) but needs the pipeline
6. **Phase 6** improves efficiency but is optional — the system works without it

Each phase is independently testable and shippable. Phase 1 alone produces a useful heuristic analysis tool. Phases 1-3 produce a batch simulation framework. Phases 1-4 produce an optimizer. Phases 1-5 produce a complete build discovery system.
