# Implementation Roadmap

Phased build plan with dependencies, technology choices, and build order.

---

## Phase Overview

```
Phase 1:   Data Layer + Heuristic ──────────── ✓ COMPLETE (300+ tests)
Phase 2:   Java Combat Harness Mod ─────────── ✓ COMPLETE (37 tests)
Phase 3:   Instance Manager ────────────────── ✓ COMPLETE (38 tests)
Phase 3.5: Timeout Tuning ─────────────────── ✓ COMPLETE (10 tests)
Phase 4:   Optimizer Integration ───────────── ✓ COMPLETE (407 tests)

Throughput (T1-T4): Cross-cutting ─────────── Persistent sessions, programmatic variants, ASHA scheduling
  T1: Programmatic variant creation (Java)     Eliminates .variant file I/O
  T2: Persistent game session (Java)           Eliminates JVM restart overhead
  T3: Mixed-build batching / StagedEvaluator   ASHA scheduling for Phase 5B
  T4: Cloud deployment (Docker + spot)         Linear scaling to 32-64 instances

Phase 5:   Signal Quality ─────────────────── Evaluation noise reduction + budget efficiency
Phase 6:   Quality-Diversity ───────────────── Build archetype mapping (pyribs)
Phase 7:   Neural Surrogate ───────────────── ML prediction (TabPFN/CatBoost)
```

**Throughput phases (T1-T4)** are cross-cutting infrastructure improvements documented in `docs/reference/throughput-optimization.md`. They can be implemented independently but Phase 5B (sequential evaluation) requires T1-T3. Research complete.

Each phase is independently useful and can be shipped/tested before proceeding.

---

## Phase 1: Data Layer + Heuristic Scorer ✓ COMPLETE

### Goal
Parse Starsector game data, build constraint-aware search spaces, implement heuristic scoring, and generate .variant files.

### Status
Complete. 300+ tests passing across 15 test files. All modules implemented with DDD+TDD.

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
   - Define Optuna parameter space (suggest_categorical per slot, suggest_int for vents/caps)
   - Compatible with any Optuna sampler (TPE, CatCMAwM, etc.)

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
Complete. 37 JUnit tests passing. Live-tested: Eagle vs Dominator Assault, result.json produced with full per-ship damage/flux/armor stats. Enriched heartbeat (6-field) protocol added in Phase 3.5.

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

## Phase 3: Instance Manager ✓ COMPLETE

### Goal
Launch and manage N parallel Starsector instances for batch combat evaluation.

### Status
Complete. 24 unit tests + 14 result parser tests passing. Integration-tested: 2 instances on Xvfb, Eagle vs Dominator (TIMEOUT at 180s) and Onslaught vs Lasher (PLAYER win at 47s).

### Implementation

**Modules:**
- `src/starsector_optimizer/result_parser.py` — Parse combat result JSON ↔ Python dataclasses, write queue files
- `src/starsector_optimizer/instance_manager.py` — `InstancePool` manages N parallel game instances

**Key classes:**
- `InstanceConfig` — Pool configuration (game_dir, num_instances, batch_size, timeouts)
- `InstancePool` — Main class: `setup()` → `evaluate(matchups)` → `teardown()`
- `GameInstance` — Tracks a single game instance (process handles, state, work directory)

**Per-instance work directory:** Symlinks (absolute paths via `.resolve()`) to shared game files, real directories for `saves/`, `data/config/`, `data/variants/` (including subdirs), `mods/`. Total ~4MB per instance.

**Health monitoring:** Poll heartbeat file mtime every 1s. Startup timeout 90s, heartbeat timeout 120s. Crash detection via process exit + no done signal. Auto-restart up to 3 times.

**Xvfb:** Each instance gets its own display (`:100`, `:101`, ...) at 1920x1080x24 to match MenuNavigator's calibrated Robot coordinates. Lock file cleanup before start.

**Launcher click:** xdotool polls for Swing launcher window, clicks "Play Starsector" at (297, 255). xdotool works on Swing; java.awt.Robot (inside JVM) handles LWJGL game UI.

**Game activation:** Stored in `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (user-global). Shared automatically across instances.

---

## Phase 3.5: Timeout Tuning ✓ COMPLETE

### Goal
Data-driven timeout tuning for combat matchups.

### Status
Complete. 10 timeout tuner tests passing. Enriched heartbeat confirmed in integration test (6-field format).

### Implementation

**Modules:**
- `src/starsector_optimizer/timeout_tuner.py` — `TimeoutTuner` with data-driven priors + Weibull AFT

**Timeout tuner:** Data-driven priors from GameData (no magic numbers): `approach_time(speeds) + combat_estimate(EHP, DPS) * safety_mult`. Blends with lifelines WeibullAFTFitter as data accumulates.

**Enriched heartbeat:** 6 fields: `<timestamp_ms> <elapsed> <player_hp> <enemy_hp> <player_alive> <enemy_alive>`

---

## Phase 4: Optimizer Integration ✓ COMPLETE

### Goal
Connect the Optuna optimizer framework to the evaluation pipeline with a diverse opponent pool, heuristic warm-starting, and proper constraint handling.

### Status
Complete. 407 tests passing across all test files. End-to-end tested: 203-trial Eagle optimization campaign (4.3h, 47.6 trials/hour). See `experiments/eagle_200/` for data and analysis notebooks.

### Dependencies
- Phase 1 (data layer, search space, repair operator, variant generator)
- Phase 2 + 3 (combat harness + instance manager) for simulation evaluation
- Python libraries: optuna, cmaes (for CatCMAwM sampler via OptunaHub), optunahub, scikit-learn (for fANOVA)

### Key Design Decisions (from Phase 4 Research)

**Optuna replaces Bounce/SMAC3 as primary optimizer.** Research findings:
- **Bounce**: No constraint support, no PyPI package, research-quality code. The internal binning/embedding makes repair operator interaction poorly defined.
- **SMAC3**: Best constraint expressiveness (ConfigSpace), but batch parallelism is broken — their own team acknowledges it's "about as good as random search." Dealbreaker for our 4-8 parallel instances.
- **Optuna TPE**: Clean ask-tell API, `constant_liar=True` for batch parallelism (acceptable at batch 4-8), native categorical+integer handling, swappable samplers via OptunaHub.

**No "short sim" fidelity level.** Phase 3.5 research proved:
- 60s timeout = 100% timeout rate for cruisers → flat fitness landscape → +20% optimizer iterations
- Short sims (15-30s) are even worse — approach time alone is ~6s wall-clock at 5x
- Between-trial pruning via WilcoxonPruner handles budget efficiency, so full sim IS the right fidelity

**Fixed diverse opponent pool, not single opponent.** Starsector has strong RPS dynamics (kinetic 200% vs shields, HE 200% vs armor). Single-opponent fitness produces counter-builds, not robust builds.

**Heuristic as GP prior mean (not full MFBO).** Our heuristic R² ≈ 0.49 with simulation, below the 0.75 threshold where MFBO reliably helps. The prior-mean approach is more robust: the GP learns `f_sim(x) - f_heuristic(x)`, which is smoother and easier to model.

### Deliverables

1. **Opponent pool definition**
   - 5-6 stock opponents covering Starsector archetypes:
     - Shield tank (e.g., Dominator with kinetic loadout)
     - Armor tank (e.g., Onslaught with HE loadout)
     - Fast kiter (e.g., Medusa or Hyperion)
     - Carrier (e.g., Heron or Astral)
     - Phase ship (e.g., Doom or Harbinger)
     - Balanced cruiser (e.g., Eagle or Fury)
   - Fitness = average win rate across pool (robust) or min win rate (minimax, anti-fragile)
   - Pool selected manually per hull size (frigates fight frigates, etc.)

2. **Evaluation dispatcher**
   - Accept build specification from optimizer
   - Apply `repair_build()` + deduplicate (cache repaired builds by hash)
   - Generate .variant files, queue matchups against all opponents in pool
   - Score = continuous HP differential (not binary win/loss) averaged across opponents
   - Log to shared JSONL (`evaluation_log.jsonl`)

3. **Optuna integration with sampler selection**
   - Default: `TPESampler(multivariate=True, constant_liar=True, n_ei_candidates=256, n_startup_trials=100)`
   - Alternative: `CatCmawmSampler` via OptunaHub (`--sampler catcma`) for cross-variable correlation modeling
   - Ask-tell loop: `study.ask()` → repair → evaluate against opponent pool → `study.add_trial()` with repaired params (Lamarckian)
   - Report OP budget violation via `constraints_func` (biases TPE away from infeasible regions)

4. **Parameter importance analysis**
   - fANOVA via `optuna.importance.get_param_importances()` to identify low-impact parameters
   - Fixed-parameter support: `--fix-params` freezes chosen parameters, reducing effective dimensionality
   - Workflow: initial run → analyze importance → freeze bottom-50% params → re-run in reduced space

5. **Heuristic warm-start pipeline**
   - Stage 1: Generate 50K-100K random builds via `generate_diverse_builds()`, score with heuristic (~seconds)
   - Stage 2: Select top-500 as initial study trials via `study.add_trial()` with heuristic scores
   - Stage 3: Run 20-50 simulation evaluations on top heuristic builds (calibrate GP / TPE model)
   - Stage 4: Full optimizer-guided simulation search with informed model

6. **Racing for final validation**
   - Top-10 candidates get 5-10 replays per opponent for variance reduction
   - Friedman test eliminates statistically inferior builds
   - Final output: ranked builds with confidence intervals and per-opponent matchup profiles

7. **Result logging and visualization**
   - Append-only JSONL log (feeds both TimeoutTuner and Phase 7 surrogate)
   - Per-trial: build spec, repaired build, all opponent scores, heartbeat trajectories
   - Convergence curves, per-opponent win rates, build comparison table

### Constraint Handling Strategy

Based on repair operator + BO literature review:

- **Lamarckian recording**: Use `study.add_trial(create_trial(repaired_params, score))` so TPE learns the feasible manifold directly. Avoids the landscape distortion of Baldwinian (raw params → repaired score).
- **Deduplication**: Hash repaired builds before simulation. Return cached score for collisions. Prevents wasting sim budget on repair convergence.
- **Constraint function**: Report OP overshoot to `TPESampler(constraints_func=...)` so TPE down-weights infeasible proposal regions (c-TPE approach).
- **n_startup_trials=100**: At 50-70D, TPE needs 100+ random trials before the density estimators have enough data. Default of 10 is far too low.

### Budget Math (per hull, $30 total)

| Activity | Sims | Wall-clock (8 inst) | Cost |
|---|---|---|---|
| Warm-start (50 builds × 5 opponents) | 250 | ~50min | ~$3 |
| BO exploration (150 builds × 5 opponents) | 750 | ~2.5h | ~$8 |
| Racing (10 builds × 5 opponents × 5 replays) | 250 | ~50min | ~$3 |
| **Total per hull** | **~950** | **~3.5h** | **~$11** |

With $30: optimize 2-3 hulls fully, or 5+ hulls with reduced racing.

### Testing
- Run Optuna TPE on heuristic-only (no simulation) to verify convergence on known-good builds
- Benchmark TPE vs random search on heuristic proxy — verify TPE outperforms by trial 200
- End-to-end: optimize a frigate (small search space) with 2 simulation instances
- Verify repair deduplication prevents wasted evaluations
- Compare optimizer results to known community builds

---

## Phase 5: Signal Quality

### Goal
Improve the signal-to-noise ratio of combat fitness evaluations and increase evaluation budget efficiency through opponent normalization, multi-fidelity evaluation, and richer objectives.

### Status
Research complete. See `docs/reference/phase5-signal-quality.md` for full research findings and recommended approach.

### Motivation (from Phase 4 Eagle Experiment)
The 203-trial Eagle experiment achieved Cohen's d = 3.30 (the optimizer finds real signal) but win rate was only 0.4% — the optimizer navigates "shades of losing." Per-opponent analysis revealed dominator_XIV_Elite has *negative* correlation with fitness (ρ = -0.225), inter-opponent correlations are near-zero, and within-outcome variance is high (doom_Strike TIMEOUT: std = 0.547). The evaluation pipeline spends equal budget on clearly bad and potentially good builds.

### Dependencies
- Phase 4 (optimizer integration, opponent pool, evaluation pipeline)
- Throughput optimization (Phases T1-T3) — Phase 5B requires persistent sessions + mixed-build batching for efficient sequential evaluation. See `docs/reference/throughput-optimization.md`.
- No new external libraries required (Optuna's built-in MedianPruner, NSGAIISampler)

### Deliverables (Phased)

**Phase A — Quick Signal Improvements (no pipeline change):**
1. **Opponent normalization** — Z-score per opponent using running statistics
2. **Control variate correction** — Adjust fitness using heuristic scorer correlation
3. **Rank-based fitness shaping** — Report quantile rank instead of raw composite score

**Phase B — Sequential Evaluation Pipeline (main architectural change):**
1. **Opponent ordering** — Most discriminating opponent first (learned from data)
2. **MedianPruner** — Intermediate reporting after each opponent via `trial.report()`, Optuna prunes bad builds (MedianPruner with `n_startup_trials=20` — 5 steps is too few for HyperbandPruner's bracket structure)
3. **Mixed-build batching** — ASHA-style rung-priority scheduling via `StagedEvaluator`. Each game launch gets matchups from different builds at different stages, maximizing instance utilization even with aggressive pruning.

**Phase C — Richer Objectives (optional, bigger conceptual change):**
1. **Multi-objective decomposition** — 3 objectives (engagement, damage efficiency, survivability) via NSGAIISampler
2. **Curriculum learning** — Start against weaker opponents, ramp difficulty as win rate improves

**Phase D — If Java Modification Is Feasible:**
1. **Common Random Numbers** — Seed-controlled combat for 80–94% variance reduction on paired comparisons

### Expected Impact

| Metric | Phase 4 Baseline | After Phase A | After Phase B |
|--------|-------------------|---------------|---------------|
| Evals per build | 5 (fixed) | 5 (cleaner) | ~2.5 avg |
| Signal quality (CoV) | 0.41 | ~0.25 | ~0.15 |
| Budget efficiency | 1× | 1× | 2–3× |

### Testing
- Replay 203-trial evaluation log under new scoring (normalization, control variate) — measure rank correlation preservation
- Benchmark Hyperband pruning accuracy: do pruned builds have lower full-evaluation fitness?
- Multi-objective: verify Pareto front covers diverse build strategies
- Curriculum: verify win rate increases against weaker opponents, builds transfer to harder opponents

---

## Phase 6: Quality-Diversity

### Goal
Discover the full map of viable build archetypes using MAP-Elites.

### Dependencies
- Phase 1 (data layer, heuristic scorer)
- Phase 4 (optimizer integration, opponent pool, evaluation dispatcher)
- Python libraries: ribs (pyribs), cmaes

### Deliverables

1. **Behavior descriptor computation**
   - Engagement range (DPS-weighted weapon range)
   - Survivability style (shield EHP fraction)
   - Damage type profile (kinetic DPS fraction)
   - Offense/defense ratio
   - All already computed by Phase 1 `ScorerResult` — no new code needed

2. **CMA-MAE with pyribs**
   - CVT archive (5000 cells, 4D)
   - Custom CatCMA emitter for mixed variables (wraps CatCMAwM from cmaes library)
   - Heuristic fitness function for Phase A
   - Phase 4's opponent pool reused for simulation fitness in Phase B

3. **Two-phase pipeline**
   - Phase A: Heuristic illumination (200K+ evals, ~1 hour, no simulation)
   - Phase B: Simulation validation of archive elites against opponent pool (2000-5000 evals)
   - Re-rank elites by simulation fitness; fills ~60-80% of heuristic-optimal cells

4. **Surrogate refinement (DSA-ME pattern)**
   - Train correction model on Phase B sim results (TabPFN or CatBoost from Phase 7)
   - Re-illuminate with `heuristic + correction` as fitness
   - Validate new/changed elites with simulation
   - 2-3 refinement rounds until archive stabilizes

5. **Visualization**
   - 2D heatmap slices of the 4D archive
   - Build cards for representative archetypes
   - Coverage metrics (% cells filled, mean fitness)
   - Per-opponent matchup profiles for each archetype (from opponent pool)

### Testing
- Run heuristic-only QD on a frigate (small space, fast iteration)
- Verify archive fills with diverse builds (not just copies of the same build)
- Verify behavior descriptors correctly separate known archetypes
- Compare discovered archetypes to community-known builds

---

## Phase 7: Neural Surrogate

### Goal
Train ML models that predict combat outcomes from build parameters, reducing simulation dependency by ~70%.

### Dependencies
- Phase 1 (data layer, feature engineering via `ScorerResult`)
- Phase 4 (accumulated simulation data — 500+ results in `evaluation_log.jsonl`)
- Phase 3.5 (heartbeat trajectories for trajectory feature extraction)
- Python libraries: tabpfn, catboost, scikit-learn

### Key Design Decisions (from Phase 4 Research)

**Target: continuous HP differential, not binary win/loss.** Regression on `final_player_hp - final_enemy_hp` (range -1.0 to +1.0) provides smoother gradients and preserves margin-of-victory information. A build that wins with 80% HP is better than one that wins with 5%.

**TabPFN for cold-start (N<300), CatBoost for scale (N>300).** TabPFN v2 excels at small-sample tabular regression but degrades with >10 unique categories per feature. Must convert weapon IDs to derived numeric features (DPS, flux, range per slot), not raw IDs. CatBoost handles raw categoricals natively and overtakes TabPFN after ~300 samples.

**Heartbeat trajectory features.** The enriched heartbeat data from Phase 3.5 provides time-series signal. Convert to fixed-length features: HP at checkpoints (t=15s, 30s, 60s, 90s), HP loss rates, momentum reversals (sign changes in HP differential). ~20 trajectory features per fight, usable by any tabular model.

### Deliverables

1. **Feature engineering pipeline**
   - Build features (derived numeric, NOT raw weapon IDs):
     - Per-slot: DPS, flux/s, range, damage type fraction
     - Aggregate: total DPS, flux balance, EHP, range coherence, damage mix
     - Hullmod: key binary flags (has_SO, has_heavy_armor, has_shield_shunt)
     - Flux allocation: vents, capacitors, vent_fraction
   - Enemy-relative features (DPS ratio, range advantage, EHP ratio)
   - Trajectory features (from heartbeat data):
     - HP fractions at fixed checkpoints (15s, 30s, 60s, 90s, 120s)
     - HP loss rates (linear slope over sliding windows)
     - HP differential mean, std, final value
     - Momentum reversals (sign changes in `player_hp - enemy_hp`)
     - Fight duration, whether ended by kill/timeout
   - Total: ~50-60 numeric features per (build, opponent) pair

2. **Model training**
   - TabPFN v2 (N<300, zero-config, 2.8s training, derived numeric features only)
   - CatBoost (N>300, native categorical handling, hyperparameter tuning)
   - Target: HP differential (continuous regression), per-opponent
   - Separate model per opponent in pool, or single model with opponent as feature

3. **Uncertainty quantification**
   - CatBoost: virtual ensembles (built-in `RMSEWithUncertainty` loss)
   - TabPFN: predictive distribution (built-in)
   - Cross-validation variance as calibration check

4. **Active learning loop**
   - Use surrogate uncertainty to select most informative builds to simulate
   - Prioritize "interesting" builds: close predicted fights, momentum reversals, novel trajectories
   - Retrain after each batch of new sim results
   - Track model accuracy over time (R², rank correlation vs held-out set)

5. **Integration with optimizer**
   - Surrogate as cheap pre-filter: score 1000 candidates, simulate only top-50
   - Composite: heuristic + surrogate correction (GP learns residual)
   - Surrogate prediction as early signal before full opponent pool evaluation completes

### Testing
- Train on 200 sim results, predict held-out 50: measure R² and rank correlation
- Compare TabPFN vs CatBoost at 200, 500, 1000 samples
- Verify trajectory features improve prediction vs build-only features
- Verify uncertainty is calibrated (high-uncertainty predictions have high actual variance)
- End-to-end: run optimizer using surrogate as pre-filter, verify it finds good builds faster

---

## Technology Stack Summary

| Component | Primary | Alternatives |
|---|---|---|
| Language (data/optimizer) | Python 3.10+ | — |
| Language (game mod) | Java 17 | — |
| Game data parsing | pandas, json | — |
| Search space definition | Optuna suggest_categorical/suggest_int | — |
| Primary optimizer | Optuna TPESampler | CatCMAwM (OptunaHub), BoTorch qNEI |
| Constraint handling | repair_build() + constraints_func (c-TPE) | — |
| Quality-diversity | pyribs + cmaes (CatCMAwM emitter) | QDax (GPU) |
| Multi-fidelity | Heuristic warm-start + full sim | BoTorch prior-mean GP (if R² improves) |
| Neural surrogate | TabPFN v2 (N<300), CatBoost (N>300) | RF ensemble |
| Instance management | Python subprocess + Xvfb | Docker (heavier) |
| Visualization | matplotlib, plotly | — |
| Data storage | JSONL (evaluation_log.jsonl), SQLite (Optuna) | — |

---

## Execution Plan: Local Development → Cloud Burst

### Hull Selection for Local Development

Three representative hulls, one per size class, covering different search space characteristics:

| Hull | Size | Slots | Dims | OP | Why |
|---|---|---|---|---|---|
| **Wolf** | Frigate | 6 | 70 | 55 | Smallest search space. Known SO brawler builds to validate against. 11 stock variants for opponent pool. |
| **Eagle** | Cruiser | 13 | 77 | 155 | Medium space. Integration-tested hull. Community has well-known builds. |
| **Onslaught** | Capital | 22 | 86 | 360 | Largest space. Stress-tests TPE at high dimensionality. Known dominant builds (2x Hephaestus + Gauss). |

All hulls have 62 eligible hullmods. Total dimensions = slots + hullmods + 2 (vents, caps).

### Opponent Pool (Stock Variants)

Selected from game's built-in .variant files per hull size class:

| Archetype | Frigate Opponent | Cruiser Opponent | Capital Opponent |
|---|---|---|---|
| Shield tank | Wolf_Assault | Dominator_Assault | Onslaught_Standard |
| Armor/HE tank | Hammerhead_Assault | Dominator_XIV_Elite | Onslaught_XIV_Elite |
| Fast kiter | Medusa_Attack | Medusa_CS | Eagle_Assault |
| Carrier | — (no frigate carriers) | Heron_Attack | Heron_Strike |
| Phase | Shade_Attack | Doom_Strike | — (no capital phase) |

Validate pool locally: run all opponents against each other (15-30 matchups, ~15 min on 2 instances). Verify they span archetypes and produce diverse outcomes.

### Phase 4-7 Development Workflow

#### Stage 1: Local — Code Development (no simulation cost)

All Phase 4-7 code written and tested against heuristic proxy:

1. **Optuna integration** — ask-tell loop, repair, deduplication
2. **Opponent pool** — definition, matchup generation, fitness aggregation
3. **Warm-start pipeline** — heuristic screening, study population
4. **Result logging** — JSONL writer, convergence plotting
5. **Surrogate features** — trajectory extraction, build feature engineering

**Validation on heuristic proxy:**
- Run TPE on Wolf (70D) with heuristic_score as objective — verify convergence by trial 200
- Benchmark TPE vs random search — verify statistically significant improvement
- Verify deduplication catches repair collisions

**Estimated time:** 2-3 days of development. Zero simulation cost.

#### Stage 2: Local — Small-Scale Simulation Validation (2 Xvfb instances)

Validate end-to-end with real combat:

1. **Opponent pool validation** — 30 matchups across opponent pool (~30 min)
2. **Wolf optimization** — 50 builds × 5 opponents = 250 sims (~2 hours)
3. **Eagle optimization** — 30 builds × 5 opponents = 150 sims (~1.5 hours)
4. **Verify results** — compare optimizer output to known community builds

**Collects ~400 sim results** — enough to start Phase 7 surrogate training (TabPFN at N<300).

**Estimated time:** 1 day. Zero cloud cost.

#### Stage 3: Cloud — Full Optimization (3 × CCX33 machines)

Transfer Optuna studies via SQLite file. Each machine handles one hull size class.

```
sim-worker-0: Frigate+Destroyer hulls (8 instances)
sim-worker-1: Cruiser hulls (8 instances)
sim-worker-2: Capital hulls (8 instances)
```

**Per hull (full pipeline):**
| Step | Sims | Wall-clock |
|---|---|---|
| Warm-start (50 builds × 5 opponents) | 250 | ~50min |
| BO exploration (150 builds × ~3 avg opponents) | ~450 | ~1.5h |
| Racing (10 builds × 5 opponents × 5 replays) | 250 | ~50min |
| **Total** | **~950** | **~3.5h** |

**Campaign options (within $30 budget):**

| Scope | Hulls | Machines | Sims | Wall-clock | Cost |
|---|---|---|---|---|---|
| Priority hulls | 10 | 1 × CCX33 | ~10K | ~12h | ~$1.30 |
| All cruisers+capitals | 40 | 3 × CCX33 | ~38K | ~16h | ~$5.30 |
| All combat-relevant | 118 | 3 × CCX33 | ~112K | ~47h | ~$15.50 |
| + QD validation (Phase 6) | 118 | 3 × CCX33 | ~150K | ~63h | ~$20.80 |

**Machine setup:** ~2 minutes per machine (parallel). Game dir is only 361MB.

#### Stage 4: Local — Analysis + Phase 7 Training

Collect results from cloud machines. With 10K+ sim results accumulated:
1. Train CatBoost surrogate on full dataset
2. Evaluate surrogate accuracy (R², rank correlation vs held-out)
3. If R² > 0.75: enable surrogate as pre-filter, re-run with 70% fewer sims
4. Generate visualizations: convergence curves, build comparison tables, matchup profiles

### Optuna Study Persistence

TPESampler is **stateless** — reconstructs its model from stored trials on every call. SQLite file transfer preserves all knowledge.

```
Local: create study.db → heuristic warm-start → small sim validation
  ↓ scp study.db
Cloud: load_study → heavy simulation (n_jobs=8)
  ↓ scp study.db + evaluation_log.jsonl
Local: load_study → analysis + Phase 7 training
```

Each hull gets its own study. No cross-machine coordination needed — machines run independent hulls.

### Cloud Deployment Automation

See `docs/specs/22-cloud-deployment.md` for deployment scripts, cloud-init config, and work distribution details.

**Key commands:**
```bash
# Deploy 3 machines
./scripts/cloud/deploy.sh 3 ccx33

# Run optimization (one hull list per machine)
./scripts/cloud/run_optimization.sh hulls_frigates.txt study_frigates.db

# Collect results
./scripts/cloud/collect.sh

# Teardown
./scripts/cloud/teardown.sh
```

---

## Build Order Rationale

1. **Phase 1 first** because everything depends on it, and it requires no game integration
2. **Phase 2 next** because it's the most novel engineering (Java mod)
3. **Phase 3** connects Phases 1 and 2 into a working pipeline
4. **Phase 3.5** adds timeout tuning (optimizer quality)
5. **Phase 4** adds intelligence (optimization) to the pipeline — Optuna framework
6. **Throughput T1-T3** before Phase 5 — Phase 5B (sequential evaluation) requires persistent sessions and mixed-build batching. Without them, sequential evaluation has 78% startup overhead and is worse than the current approach.
7. **Phase 5** improves signal quality and budget efficiency — the optimizer works without it but converges faster with it
8. **Throughput T4** (cloud) when local isn't enough — linear scaling to 32-64 instances
9. **Phase 6** discovers build archetypes (MAP-Elites) — the most exciting output but needs the pipeline
10. **Phase 7** improves efficiency (~70% sim reduction) via neural surrogate — optional, the system works without it

Each phase is independently testable and shippable. Phase 1 alone produces a useful heuristic analysis tool. Phases 1-3 produce a batch simulation framework. Phases 1-4 produce an optimizer. Phases 1-4 + T1-T3 + Phase 5 produce a complete build discovery system.

### Development Timeline Estimate

| Stage | Work | Dependencies | Duration |
|---|---|---|---|
| Stage 1: Local code dev | Phase 4-6 Python code, tests, heuristic validation | Phases 1-3.5 complete | 2-3 days |
| Stage 2: Local sim validation | End-to-end with 2 instances, opponent pool validation | Stage 1 | 1 day |
| Stage 3: Cloud full optimization | 10-118 hulls on Hetzner CCX33 | Stage 2, Hetzner account | 12-63h wall-clock |
| Stage 4: Analysis + surrogate | Phase 7 training, visualization | Stage 3 results | 1 day |

**Total: ~5 days of active development + 1-3 days of cloud compute wall-clock.**
