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

## Phase 1: Data Layer + Heuristic Scorer

### Goal
Parse Starsector game data, build constraint-aware search spaces, implement heuristic scoring, and generate .variant files.

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

## Phase 2: Java Combat Harness Mod

### Goal
A Starsector mod that runs automated combat matchups and exports results as JSON.

### Dependencies
- Java 17 (Starsector 0.98a runtime)
- Starsector API (starfarer.api.jar from game installation)
- Starsector game installation

### Deliverables

1. **Mod skeleton**
   - mod_info.json
   - Settings registration for EveryFrameCombatPlugin
   - Build system (Gradle or Maven compiling against starfarer.api.jar)

2. **Queue processor**
   - Reads matchup queue from JSON config file
   - Each matchup: player variant IDs, enemy variant IDs, time limit, replicate count
   - Auto-starts missions from queue

3. **Combat harness plugin** (EveryFrameCombatPlugin)
   - On init: set time multiplier, register damage listener
   - On advance: check combat end / time limit
   - On combat end: collect results, write to file, advance to next matchup
   - Heartbeat file (updated each frame) for instance health monitoring

4. **Damage tracker** (DamageListener)
   - Per-weapon damage tracking (kinetic, HE, energy, frag, EMP)
   - Per-ship: hull remaining, armor remaining, flux stats
   - Total damage dealt/received by fleet side
   - Overload count and duration
   - Time-to-kill

5. **Result exporter**
   - Write JSON result file per matchup/replicate
   - Include all tracked metrics
   - Atomic write (write to temp, rename) to prevent partial reads

6. **Dev mode integration**
   - F8 to reload data files for quick iteration
   - Configurable time multiplier
   - Toggle between single matchup and batch mode

### Testing
- Manual: load mod, enter simulator, verify combat runs and results appear
- Verify damage totals match combat log
- Test with known matchup outcomes (e.g., Paragon vs Lasher should always win)
- Stress test: queue 100 matchups, verify all complete

---

## Phase 3: Instance Manager

### Goal
Launch and manage N parallel Starsector instances for batch combat evaluation.

### Dependencies
- Linux (Xvfb for virtual displays)
- Python (subprocess management, file I/O)
- Starsector installation
- Combat harness mod (Phase 2)

### Deliverables

1. **Instance launcher**
   - Start Xvfb virtual display per instance
   - Create per-instance working directory (symlink game, copy mod data)
   - Launch Starsector with correct DISPLAY and memory settings
   - JVM vmparams configuration (heap size, GC settings)

2. **Health monitor**
   - Watch heartbeat files (updated by combat harness mod)
   - Detect hung instances (no heartbeat for >60s)
   - Detect crashed instances (process exit)
   - Auto-restart failed instances

3. **Work distributor**
   - Write matchup queue files to per-instance work directories
   - Balance load across instances (round-robin or shortest-queue)
   - Track which matchups are assigned to which instance

4. **Result collector**
   - Watch for result JSON files in per-instance work directories
   - Parse and aggregate results
   - Return results to optimizer
   - Handle partial results (instance crashed mid-batch)

5. **Resource management**
   - Memory monitoring (total usage across instances)
   - Configurable instance count
   - Graceful shutdown (complete current matchups, then stop)

### Testing
- Launch 2 instances, verify both run matchups independently
- Kill an instance, verify manager detects and restarts
- Verify results are correctly collected and attributed
- Memory usage stays within bounds
- Scale test: 16 instances on a 32GB machine

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
