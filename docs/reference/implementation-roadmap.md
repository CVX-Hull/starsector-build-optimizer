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
  T4: Cloud deployment (CPU spot) ─────────── Superseded by Phase 6 Cloud Worker Federation. 2026-04-18 benchmarks proved CPU cloud is 2.4× local per-instance throughput after LWJGL XRandR fix; GPU is not required

Phase 5A:  Signal Quality — Normalization ──── ✓ COMPLETE (TWFE deconfounding, control variate, rank shape)
Phase 5B:  Signal Quality — Multi-fidelity ── ✓ COMPLETE (WilcoxonPruner, ASHA scheduling)
Phase 5C:  Opponent Curriculum ─────────────── ✓ COMPLETE (TWFE replaced Elo; anchor-first, incumbent overlap)
Phase 5D:  EB Shrinkage of A2 (fusion) ────── ✓ COMPLETE 2026-04-18 — replaces A2 scalar CV with empirical-Bayes shrinkage of α̂ toward a 7-covariate heuristic prior (3 engine-computed MutableShipStats reads + 3 Python-raw offense/range aggregates + composite_score; HN + triple-goal rank correction). Fusion paradigm, not conditioning. Feature set sized by sweep + variance audit on the 2026-04-17 Hammerhead run — candidate engine features with near-zero per-hull-run variance (eff_hull_hp, eff_max_speed, eff_shield_damage_mult) were dropped in favor of features with empirically-validated spread
Phase 5E:  A3 Shape Revision (Box-Cox) ────── ✓ COMPLETE 2026-04-18 — scipy.stats.boxcox replaces quantile rank at A3; refit per-call on the post-5D EB posterior-mean population, min-max-rescaled to [0, 1]; ShapeConfig.min_samples=8 floor with min-max passthrough. Post-5D re-validation (experiments/signal-quality-5d-2026-04-18/): ceiling 25% → 0.5%, top-5 overlap 0.02 → 0.44 (14×), invariant across 4 covariate-strength regimes
Phase 5F:  Regime-Segmented Optimization ──── PLANNED — user-selectable progression tier (mask hullmods/weapons/hulls by rarity tags at search_space.py); CMDP feasibility alignment. Default `mid` reclaims ~80% of compute budget from the Hammerhead 89% exploit cluster
Phase 5G:  Adversarial Curriculum ─────────── DEFERRED — main-exploiter loop / PSRO; revisit post-5E/5F
Phase 6:   Cloud Worker Federation ─────────── PLANNED (NEXT) — study federation across many (hull, regime, seed) units; each study ≤24 workers to keep TPE in efficient zone; Hetzner CCX33 Ashburn primary + AWS c7a us-west-2 fallback; campaign manager with hard cost cap; pre-baked Packer images; auto-terminate on plateau. Unlocks $1000-scale campaigns at linear $→data scaling
Phase 7:   Structured Search-Space Repr ───── PLANNED — custom BoTorch GP: SAAS(hullmods) × transformed-overlap+attribute-Matérn(weapons) × slot-Matérn × opponent-features(smalls only) × gated-sentinel(conditional) + ICM residuals; πBO archetype priors; BOCA warm-start; ~2-4× sample efficiency. Renumbered from 6 — depends on Phase 6 cloud federation for multi-hull validation
Phase 8:   Quality-Diversity ───────────────── Build archetype mapping (pyribs). Renumbered from 7
Phase 9:   Neural Surrogate ───────────────── ML prediction (TabPFN/CatBoost). Renumbered from 8
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
Complete. 437 tests passing across all test files. Previously tested with a 203-trial Eagle campaign, but that data was invalidated by a combat harness bug (ships retreating due to `spawnFleetMember()` `directRetreat=true`). The harness has since been rewritten to single-matchup-per-mission and verified end-to-end.

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
   - Append-only JSONL log (feeds both TimeoutTuner and Phase 8 surrogate)
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
Improve the signal-to-noise ratio of combat fitness evaluations and increase evaluation budget efficiency through opponent normalisation, multi-fidelity evaluation, curriculum learning, covariate-adjusted fitness, and shape-preserving output transforms.

### Status

| Sub-phase | Scope | Status |
|---|---|---|
| 5A | TWFE decomposition + control variate + rank shape | ✓ COMPLETE (integrated in `StagedEvaluator`) |
| 5B | Sequential evaluation, WilcoxonPruner, ASHA scheduling | ✓ COMPLETE (integrated in `StagedEvaluator`) |
| 5C | Opponent curriculum — anchor-first + incumbent overlap + fixed pre-burn-in | ✓ COMPLETE |
| 5D | EB shrinkage of A2 — replaces scalar CV with empirical-Bayes shrinkage of α̂ toward a 7-covariate heuristic regression prior (HN + triple-goal). Covariate vector: 3 engine-computed `MutableShipStats` reads (Java SETUP emit: eff_max_flux, eff_flux_dissipation, eff_armor_rating) + 3 Python-raw (total_weapon_dps, engagement_range, kinetic_dps_fraction) + `composite_score` | ✓ COMPLETE (2026-04-18, fusion design ship-gate-validated at Δρ=+0.036/+0.057 p=16; conditioning-paradigm v1 refuted, see §4.5 of phase5d-covariate-adjustment.md). Post-ship TTK-signal investigation (2026-04-18, §7 of phase5d-covariate-adjustment.md): 8th-covariate extensions not shipped, routed to 5F as opt-in `eb_extra_covariates` |
| 5E | A3 shape revision — Box-Cox output warping replaces quantile rank. `scipy.stats.boxcox` fit on the `_completed_fitness_values` population every `_finalize_build` (~1ms at n=300), min-max-rescaled to `[0, 1]`; `ShapeConfig.min_samples=8` floor with min-max-scaling passthrough; non-finite input raises `ValueError`; new JSONL `shape_lambda`/`shape_passthrough_reason` + per-trial λ logger + end-of-run summary. CAT opponent selection remains deferred. | ✓ COMPLETE (2026-04-18, re-validated under 5D: ceiling 25% → 0.5%, top-5 overlap 0.02 → 0.44 (14×), invariant across covariate-strength regimes; see `experiments/signal-quality-5d-2026-04-18/`) |
| 5F | Regime-segmented optimization — user-selectable progression tier; hard mask on hullmods/weapons/hulls at `search_space.py`; CMDP feasibility alignment; default `mid` | PLANNED (research complete, 16-field literature sweep 2026-04-17) |
| 5G | Adversarial opponent curriculum — PSRO-style pool growth | DEFERRED (research complete) |

The first Hammerhead run (63 trials, 2026-04-13, `experiments/hammerhead-overnight-2026-04-13/`) drove the Phase 5C design. The second (900 trials, 2026-04-17, `experiments/hammerhead-twfe-2026-04-13/`) validated 5A–5C end-to-end but exposed an A3 rank-shape ceiling (25.3% of trials tied at fitness=1.0) and an 89% exploit-cluster concentration, motivating Phases 5D, 5E, 5F, and the deferred 5G.

### Research Documents
- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B foundational research (opponent normalisation, multi-fidelity).
- `docs/reference/phase5a-deconfounding-theory.md` — TWFE additive-decomposition synthesis (6-field literature consensus, foundation of 5A and 5C).
- `docs/reference/phase5c-opponent-curriculum.md` — Phase 5C design + rejected alternatives (Elo rotation, per-frame Java tracking, hullmod blacklist).
- `docs/reference/phase5d-covariate-adjustment.md` — Phase 5D design: empirical-Bayes shrinkage of A2 toward a heuristic-predicted regression prior (HN + triple-goal rank correction). Rejected alternatives include the original conditioning-paradigm design (CUPED / FWL / PDS lasso / ICP — refuted by `experiments/phase5d-covariate-2026-04-17/REPORT.md`), hand-weighted composite, MISO, one-factor CFA, and per-frame Java harness tracking.
- `docs/reference/phase5e-shape-revision.md` — Phase 5E design: Box-Cox replaces rank shape; rejected alternatives (CFS, EM-Tobit, full MAP-Elites); Phase 5G (adversarial curriculum) research.
- `docs/reference/phase5f-regime-segmented-optimization.md` — Phase 5F design: CMDP feasibility-aligned regime mask; one-run-per-regime; rejected alternatives (scalar penalty, archive-over-single-run, curriculum across regimes, multi-fidelity-by-tier, Pareto); 16-field 2026-04-17 literature sweep.
- `docs/reference/phase7-search-space-compression.md` — Phase 7 design: custom BoTorch GP composite kernel (SAAS on hullmods, transformed-overlap + 7-attribute Matérn on weapons, 5-dim slot-feature Matérn, opponent features on smalls only, gated-sentinel conditional, ICM per-item + per-slot residuals); πBO archetype priors; BOCA warm-start; rejected alternatives (Ma-Blaschko tree, HyperMapper off-the-shelf, NAS weight-sharing, BOCS binary monomials, GFlowNets, Hearthstone MESB, silent hard-fill smalls); 10-field 2026-04-17 literature sweep + compiler-autotuning deep-dive.
- `docs/reference/multi-fidelity-strategy.md` — Multi-fidelity strategy (heuristic vs simulation tiers).

### Key Findings

#### From first run (63 trials, 2026-04-13, `experiments/hammerhead-overnight-2026-04-13/`)

| Finding | Impact | Fix | Status |
|---|---|---|---|
| Only 10/54 opponents used (alphabetical selection, biased toward freighters/carriers) | Builds optimised against non-combat ships | Random selection from full pool; pre-burn-in fixed set + post-burn-in incumbent overlap + fill | ✓ Fixed (5C) |
| All opponents weighted equally in fitness | Beating a buffalo counts same as beating condor_Attack | TWFE decomposition — β_j captures opponent difficulty, α_i is schedule-adjusted | ✓ Fixed (5A) |
| Random opponent ordering | WilcoxonPruner only pruned 11% of trials | Anchor-first ordering + rung-based step IDs for full pruner overlap | ✓ Fixed (5C) |
| Frequent heartbeat timeouts (~every 3-5 min) | ~30-40% throughput loss on 4 instances | Robot pixel-polling + heartbeat touch-not-delete | ✓ Fixed |
| `combat_fitness` ignores damage breakdown, overloads, armor | Timeout quality poorly distinguished | Auxiliary signals (7-dim X: 3 engine-computed MutableShipStats + 3 Python-raw offense/range aggregates + composite_score) enter an EB prior on α̂_i via a between-build regression; shrinkage weights α̂_i vs γ̂ᵀX_i by relative precision | Planned (5D) |

#### From second run (900 trials, 2026-04-17, `experiments/hammerhead-twfe-2026-04-13/`)

| Finding | Impact | Fix | Status |
|---|---|---|---|
| 89% of completed builds cluster on a rare-faction-hullmod exploit (shrouded_lens, shrouded_mantle, fragment_coordinator, neural_integrator) providing passive AoE damage | TPE's posterior pulls toward the exploit cluster; ranking within cluster is noise; outputs require endgame content unreachable in normal play | User-selectable progression regime (Phase 5F) — hard mask on hullmods/weapons/hulls at `search_space.py` per rarity tags; CMDP feasibility alignment (Altman 1999, Huang & Ontañón 2020); default `mid` regime reclaims ~80% of compute for deployment-reachable builds. Adversarial pool growth remains as Phase 5G fallback if 5F does not resolve exploit concentration | Planned (5F); 5G deferred |
| A3 rank-shape compresses top quartile — 25.3% of trials tie at the ceiling with raw TWFE α ranging only 0.48–0.82; top-k identification overlap near zero | Optimiser cannot distinguish top-cluster winners; TPE has no gradient at the top | Replace rank-shape with Box-Cox output warping; 2026-04-18 re-validation under Phase 5D: ceiling 25% → 0.5%, top-5 overlap 0.02 → 0.44 (14×), invariant across covariate-strength regimes | ✓ Shipped 5E (2026-04-18) |
| Opponent pool lacks peers that can defeat strong exploit builds (peer Hammerhead variants force timeouts but not kills) | Matchup scores censor at HP-diff ≈ 1.0; pool-side ceiling correlated with build strength | CAT Fisher-info opponent selection + Sympson-Hetter exposure control (simulation +0.014 Δρ on top of Box-Cox, marginal) | Deferred (revisit post-5F) |
| Per-frame Java harness extensions (flux, overload duration, engagement distance) proposed as the original Phase 5D | Would inject hand-designed intermediate signals with hand-tuned weights | REJECTED (bitter lesson) — replaced by EB shrinkage toward a heuristic-predicted prior (5D) | ✗ Rejected |
| Hand-weighted composite fitness proposed as Phase 5D.1 | Weight choice encodes a human prior about combat-behaviour quality | REJECTED (bitter lesson) — replaced by EB shrinkage with OLS-learned γ̂ | ✗ Rejected |
| Covariate-adjusted TWFE via FWL + PDS lasso + ICP proposed as Phase 5D v1 | Treats heuristic as an exogenous covariate and partials it out of Y; but heuristic is a noisy proxy of the estimand α (Cinelli-Forney-Pearl 2022 "Case 8" bad control). Synthetic Δρ = −0.35 vs plain TWFE (p<0.0001, n=20); Hammerhead LOOO Δρ = −0.13, missed +0.02 ship gate by 7×. Closed form: ρ(α̂_CUPED, α) = √(1−R) where R = heuristic reliability, so stronger heuristic ⇒ more damage | REJECTED — replaced by fusion-paradigm EB shrinkage (HN + triple-goal). See `experiments/phase5d-covariate-2026-04-17/REPORT.md` and `FUSION_REPORT.md` | ✗ Rejected |
| Post-ship TTK-signal investigation (2026-04-18) — evaluated raw `duration_seconds`, pre-battle `log(effective_hp / total_dps)`, Weibull-AFT residual as 8th EB covariate, plus lexicographic ε-tiebreaker on `Y_ij` | Causal theory (Cinelli-Forney-Pearl Model 17; Eggers-Tuñón 2024 placebo test rejects admissibility at both n=485 p=0.032 and n=56 p=10⁻⁷). Empirically: calibration log (n=485) shows no gain (+0.004 NS); overnight log (n=56, production-sized) shows significant lift (+0.136 sig). Synthetic 162-scenario stress test confirms lift is archetype-dependent; Hammerhead (quick-kill) favorable; attrition hulls untested. Lex tiebreaker uniformly loses 0.005–0.12 Δρ | NOT SHIPPED — routed to Phase 5F as opt-in regime-conditioned covariate pending cross-hull validation. See `experiments/phase5d-ttk-signal-2026-04-18/RESULTS.md` and §7 of `phase5d-covariate-adjustment.md` | → 5F |

### Dependencies
- Phase 4 (optimizer integration, opponent pool, evaluation pipeline) ✓
- No new external libraries required

### Deliverables per sub-phase

**Phase 5A — Fitness Deconfounding ✓ COMPLETE**

Shipped in `src/starsector_optimizer/deconfounding.py` and `src/starsector_optimizer/optimizer.py`. Implements:
1. TWFE decomposition (additive model `Y_ij = α_i + β_j`)
2. Trimmed mean (drop worst 2 residuals) for RPS robustness
3. Control variate correction using heuristic scorer correlation (scalar A2 — to be replaced by empirical-Bayes shrinkage toward a multi-covariate regression prior in Phase 5D)
4. Rank-based fitness shaping with top-quartile ceiling (A3 — to be replaced by Box-Cox in Phase 5E)

**Phase 5B — Sequential Evaluation Pipeline ✓ COMPLETE**

Shipped in `src/starsector_optimizer/optimizer.py` (`StagedEvaluator`):
1. ASHA-style rung-priority scheduling
2. WilcoxonPruner for statistical early stopping
3. Mixed-build dispatching across parallel instances

**Phase 5C — Opponent Curriculum ✓ COMPLETE**

Shipped in `src/starsector_optimizer/optimizer.py` opponent-selection path:
1. TWFE-based opponent difficulty (β_j estimates from Phase 5A; replaces Elo approach rejected after simulation showed ρ(Elo, true difficulty) = 0.024 with improving builds)
2. Anchor-first ordering — top-3 discriminative opponents locked at the front of every trial after burn-in
3. Incumbent overlap — 5 opponents forced from incumbent's set for direct TWFE comparability
4. Fixed pre-burn-in opponent set — all early builds face the same opponents for maximum Wilcoxon step-ID overlap

Full design and rejected alternatives in `docs/reference/phase5c-opponent-curriculum.md`.

**Phase 5D — EB Shrinkage of A2 (COMPLETE, 2026-04-18)**

Two-level Gaussian hierarchical model replacing the shipped scalar A2:

```
Likelihood:  α̂_i | α_i  ~ N(α_i, σ̂_i²)      (α̂ from A1 TWFE; σ̂_i from pooled residual MSE / n_i)
Prior:       α_i | X_i   ~ N(γ̂ᵀ[1, X_i], τ̂²)  (γ̂ = OLS of α̂ on X_i; τ̂² by method of moments)
Posterior mean:
    α̂_EB_i = w_i · α̂_i + (1 − w_i) · γ̂ᵀ[1, X_i]
    w_i    = τ̂² / (τ̂² + σ̂_i²)
```

`X_i` is the 7-dim pre-matchup covariate vector — selected by feature-count × dataset-size sweep (`experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md`) **and** an empirical variance audit on the 2026-04-17 Hammerhead run (see §2.7 of `phase5d-covariate-adjustment.md`):

1. `eff_max_flux` — Java `MutableShipStats.getFluxCapacity().getModifiedValue()` at SETUP
2. `eff_flux_dissipation` — Java `MutableShipStats.getFluxDissipation().getModifiedValue()` at SETUP
3. `eff_armor_rating` — Java `MutableShipStats.getArmorBonus().computeEffective(hullSpec.getArmorRating())` at SETUP
4. `total_weapon_dps` — Python raw sum over equipped weapons (no type weighting)
5. `engagement_range` — Python `ScorerResult.engagement_range` (DPS-weighted mean)
6. `kinetic_dps_fraction` — Python `kinetic_dps / max(total_dps, ε)` (hard-flux pressure axis)
7. `composite_score` — Python `ScorerResult.composite_score` (calibrated heuristic scalar)

Engine-computed features 1–3 **replace** Python-side `compute_effective_stats` equivalents; the engine's hullmod-effect computation is authoritative. Candidate engine features `eff_hull_hp`, `eff_max_speed`, and `eff_shield_damage_mult` were **dropped after the variance audit** — in per-hull runs they are effectively hull-constants (0.3–3% of Hammerhead builds used the relevant modifiers) and add no signal. They return as candidates if the design generalizes to cross-hull aggregation. Closed-form, ~10 lines of NumPy, sub-millisecond per pass.

Applied downstream of α̂_EB: a **triple-goal rank correction** (Lin, Louis & Shen 1999) that preserves the EB rank ordering but substitutes the empirical TWFE α̂ histogram, preventing regression-to-the-mean compression from dulling Optuna's top-tail exploitation signal. Call the composed output `α̂_EBT`; that is the value fed to Phase 5E (Box-Cox).

**Stage-1 timing filter retained.** Only pre-matchup features enter `X_i`; post-matchup harness outputs (duration, damage_efficiency, overload_count_differential, armor_fraction_remaining) are excluded by construction. Defensive hygiene — OLS in the prior regression is attenuation-safe for mis-specified covariates, but the exclusion prevents the bad-control pattern from creeping back if the design is ever generalized to a within-TWFE regression.

**Cross-field equivalences.** The same closed-form appears under six independent names across fields: covariate-powered empirical Bayes (Ignatiadis-Wager 2022), Efron-Morris shrinkage with covariate target (1975), hierarchical Bayes BLUP with regression prior (Lindley-Smith 1972), Hachemeister credibility regression (1975, actuarial), Mislevy collateral-information IRT (1987, psychometrics), TrueSkill 2 with feature-based prior (Minka et al. 2018, games rating). The convergence across fields is the best available evidence this is the right formulation.

**Rejected alternatives:** covariate-adjusted TWFE via FWL + PDS + ICP (conditioning paradigm, refuted empirically — see `experiments/phase5d-covariate-2026-04-17/REPORT.md`), hand-weighted composite fitness, per-frame Java harness tracking, one-factor CFA (fails on real data due to indicator heterogeneity), MISO. Full design and rejection rationale in `docs/reference/phase5d-covariate-adjustment.md`.

**Phase 5E — A3 Shape Revision (✓ COMPLETE 2026-04-18)**

Replaced the quantile rank at `src/starsector_optimizer/optimizer.py::_shape_fitness` with Box-Cox output warping. `scipy.stats.boxcox` refits `λ̂` on the positivised `_completed_fitness_values` population on every `_finalize_build` call (~1ms at n=300; documented deviation from the research-doc's "refit every N trials" — batched refit saves nothing and adds a (λ, shift, min, max) cache-coherence burden against the rolling min-max rescale window). The current trial's `eb_fitness` is transformed under the same λ via the `boxcox(scalar, lmbda=λ)` overload and min-max rescaled to `[0, 1]` for JSONL schema stability. Monotone (preserves Spearman ρ), gradient-preserving at the tails. Below `ShapeConfig.min_samples=8` (analogy to `eb_min_builds`; plan-introduced floor) A3 falls through to min-max scaling. Non-finite input raises `ValueError` — upstream NaN is an invariant violation, fail fast. New JSONL `shape_lambda`/`shape_passthrough_reason`, per-trial λ logger, end-of-run "A3 Box-Cox summary" line.

Secondary (deferred): CAT Fisher-info opponent selection. Post-5D re-validation showed CAT + Box-Cox (strategy J) adds Δρ = +0.014 (p = 0.019) over Box-Cox alone, but CAT is an observation-side change with wider surface area. Revisit after Phase 5F regime-segmented optimization settles the opponent-selection architecture.

Rejected alternatives (same as before): CFS-weighted TWFE (regime-mismatched at 10-active-per-trial budget), EM-Tobit TWFE (Amemiya 1984 MSE-gain condition not met at observed ~12% censoring; additionally the 2026-04-18 revalidation showed Tobit + 5D regresses ρ by ~0.09 because Tobit α̂ distribution mismatches the OLS-fit EB prior), full MAP-Elites (2-4 orders of magnitude below viable budget). Full Wilcoxon-ranked simulation results in `docs/reference/phase5e-shape-revision.md` §3 and `experiments/signal-quality-5d-2026-04-18/REPORT.md`.

**Phase 5F — Regime-Segmented Optimization (PLANNED)**

User-selectable progression regime applied as a **hard mask** on the hullmod, weapon, and hull catalogues at `search_space.py` construction time — before `repair_build()` sees any candidate. Framed as CMDP feasibility alignment (Altman 1999 *Constrained MDPs*; Huang & Ontañón 2020 action-masking, arXiv:2006.14171), not reward-shaping. Directly addresses the 89% exploit-cluster concentration observed in the 2026-04-17 Hammerhead run.

```python
@dataclass(frozen=True)
class RegimeConfig:
    name: str                             # "early" | "mid" | "late" | "endgame"
    max_hullmod_tier: int
    exclude_hullmod_tags: frozenset[str]
    exclude_hull_ids: frozenset[str]
    exclude_weapon_tags: frozenset[str]
```

Four presets, each materialized pre-repair:

| Preset | Hullmod filter | Weapon filter | Hull filter |
|---|---|---|---|
| `early` | `tier ≤ 1 ∧ ¬codex_unlockable ∧ ¬no_drop ∧ ¬no_drop_salvage` | `¬rare_bp ∧ ¬codex_unlockable` | vanilla civilian + faction-common |
| `mid` **(default)** | `¬no_drop ∧ ¬no_drop_salvage` | `¬rare_bp` | vanilla + faction-reachable (no Onslaught_Mk.I) |
| `late` | `¬no_drop` | none | all except Onslaught_Mk.I + Remnant-only |
| `endgame` | none | none | none (current behaviour; QA / exploit-discovery mode) |

**One independent Optuna study per `(hull, regime)`.** This is the dominant pattern in comparable shipped bots: Slay-I / Bottled AI (per-ascension), Riot TFT (per-Set retrain), Raidbots / Ask Mr. Robot (per-raid-tier SimC run), MTG Pauper/Modern/Legacy optimizers, FIA class homologation. Cross-regime warm-start via `study.enqueue_trial()` is optional but not required for correctness.

**Default `mid`** is grounded in four converging arguments: (a) Csikszentmihalyi flow + Koster mastery-decay + Ryan-Rigby PENS (overpowered play collapses engagement); (b) Suits' lusory attitude + Caillois' agon + Paul 2020 (constraint is constitutive of meaningful play; unconstrained optimization empirically collapses variety); (c) Alex Mosolov's stated design intent (Codex Overhaul 2024-05-11: `codex_unlockable` is spoiler-avoidance, `no_drop` / `no_drop_salvage` are genuine acquisition gates); (d) compute efficiency — `mid` redirects ~80% of the Hammerhead trial budget from the exploit cluster to the deployment-reachable regime.

**Extension candidate** (not in initial ship): per-`RegimeConfig` `eb_extra_covariates` opt-in for archetype-dependent post-battle signals. The 2026-04-18 TTK-signal investigation (`experiments/phase5d-ttk-signal-2026-04-18/`, §7 of `phase5d-covariate-adjustment.md`) showed build-mean `duration_seconds` is a Case-17 bad control on paper (Eggers-Tuñón placebo rejects admissibility) but empirically delivers +0.136 Δρ on production-sized n=56 overnight-log replay and saturates to ~0 at n=485. Signal is expected to be archetype-dependent — Hammerhead's quick-kill profile makes its TTK a strong α-mediator; attrition archetypes untested. Integration: extend `_build_covariate_vector` to read a regime-defined set of extra features (pre-battle projected TTK `log(effective_hp / total_dps)` as the causally clean default; raw `duration` as opt-in gated by a continuous Eggers-Tuñón placebo monitor).

**Rejected alternatives** (full rationale in `phase5f-regime-segmented-optimization.md` §4):
- Scalar rarity penalty `U = fitness − λ·Σ rarity_i` — contaminates TWFE α̂ signal (Cinelli-Forney-Pearl 2022 bad-control pattern, same failure mode as 5D v1); no principled `λ` in the CCG/BO literature.
- Archive-over-single-run (MAP-Elites with rarity-tier descriptor) — needs 10⁵+ trials per Mouret-Clune 2015, SAIL reduces to 10³ per Gaier 2017 arXiv:1702.03713; at N≈300 per hull, one-run-per-regime dominates.
- Curriculum learning across regimes (Bengio 2009) — curriculum operates on training-data order within a fixed hypothesis space; our regimes *are* different hypothesis spaces.
- Multi-fidelity BO with tier as fidelity (BOCA, Kandasamy 2017 arXiv:1703.06240) — BOCA fidelity varies evaluation cost for fixed x; our input set changes with tier.
- Pareto / NSGA-II on (fitness, rarity) — user wants a single recommended build within a regime, not a Pareto front.
- Weitzman reservation-value / Pandora's Box Gittins Index (Xie et al. 2024 arXiv:2406.20062) — formally cleanest, but requires per-component fitness-contribution posteriors we cannot estimate at Hammerhead-scale N. Revisit post-5D EB shrinkage.

Full design, theoretical grounding (CMDP / Jaffe restricted-play / engagement literature / ludology), data audit, and rejected alternatives in `docs/reference/phase5f-regime-segmented-optimization.md`.

**Phase 5G — Adversarial Opponent Curriculum (DEFERRED)**

Research-complete in `docs/reference/phase5e-shape-revision.md` §2.1. Renumbered from 5F. Deferred until empirical evidence after 5E + 5F ships shows the pool ceiling is still the binding constraint. Candidate mechanisms: AlphaStar main-exploiter loop (parallel TPE with objective `−win_rate(incumbent)`, promote output into opponent pool), POET minimum-criterion opponent retirement (keep opponents only in 30–70% pool win-rate band), PSRO promotion.

### Implementation Plans

#### Phase 5D — EB Shrinkage of A2 (IMPLEMENTED 2026-04-18)

Implementation differs from the plan below in two file-placement details (see `docs/reference/phase5d-covariate-adjustment.md` §5):
- `EngineStats` lives in `models.py` (not `result_parser.py`) — project convention for frozen domain dataclasses.
- Covariate assembly lives in `optimizer.py::_build_covariate_vector` (not `combat_fitness.py`) — the vector needs optimizer-owned state.

Java accessors verified via `javap` on 2026-04-18: the API method is `getFluxCapacity()` (not `getMaxFlux()`) and armor uses `getArmorBonus().computeEffective(hullSpec.getArmorRating())`. NaN values cannot pass through the game's bundled org.json, so the `setup_stats` block is OMITTED on a failed SETUP read rather than emitting NaN (Python parser treats absence as `engine_stats=None`, same as pre-5D log replay).

Original implementation plan (historical reference):

```
Step 1: Java harness — emit setup_stats
  - combat-harness/.../CombatHarnessPlugin.java: at end of SETUP state,
    read MutableShipStats for eff_max_flux, eff_flux_dissipation,
    eff_armor_rating. Stash on matchup state. (Hull-HP, speed, shield-eff
    excluded — near-zero per-hull-run variance; see §2.7 variance audit.)
  - ResultWriter.java: extend JSON with "setup_stats": {...}.
  - Java unit + integration tests covering the 3-field emit.
  - Update specs 09-combat-protocol, 12-result-writer, 13-combat-harness-plugin.

Step 2: Python parser — EngineStats
  - src/starsector_optimizer/result_parser.py: add EngineStats dataclass
    (eff_max_flux, eff_flux_dissipation, eff_armor_rating); parse the
    setup_stats block.

Step 3: Extend src/starsector_optimizer/deconfounding.py
  - twfe_decompose returns (alpha, beta, sigma_i) — new sigma_i from pooled
    residual MSE / n_i per build.
  - New function eb_shrinkage(alpha, sigma_i, X_build, tau2_floor_frac=0.05)
    returning (alpha_eb, gamma, tau2). Method: OLS γ̂ of α̂ on [1, X], MoM
    τ̂², per-build w_i, convex-combine α̂ with γ̂ᵀX. ~40 lines NumPy.
  - New function triple_goal_rank(posterior, raw) — one-line histogram
    substitution preserving ranks of posterior.

Step 4: Delete A2 scalar control variate from src/starsector_optimizer/optimizer.py
  - Remove _apply_control_variate, _refit_control_variate, _cv_beta,
    _cv_heuristic_mean and their unit tests.
  - In _finalize_build: call eb_shrinkage then triple_goal_rank, feeding the
    result into the existing A3 (or Box-Cox under Phase 5E) stage.

Step 5: Route pre-matchup 7-dim X_i through src/starsector_optimizer/combat_fitness.py
  - Expose per-build X_i = [eff_max_flux, eff_flux_dissipation,
    eff_armor_rating, total_weapon_dps, engagement_range,
    kinetic_dps_fraction, composite_score]. Engine stats from parsed
    EngineStats; Python primitives from Build + ScorerResult.
  - Post-matchup harness outputs remain un-routed (Stage-1 timing rule).

Step 6: Add ShrinkageConfig to models.py (enable, tau2_floor_frac,
  triple_goal). Wire into OptimizerConfig. Update spec 28 (deconfounding)
  and spec 24 (optimizer).

Step 7: Ship gate — replay on 2026-04-17 Hammerhead eval log
  - Fit three pipelines on the log:
      (A0) plain TWFE
      (A)  A0 + shipped scalar CV
      (EB) A0 + eb_shrinkage + triple_goal_rank
  - LOOO across top-5 most-sampled anchor opponents. For each probe:
    drop its column, refit, measure Spearman ρ(refit α̂, probe raw Y) across
    all non-pruned builds. Mean across 5 probes = gate.
  - Ship EB only if Δρ(EB − A0) ≥ +0.02 AND Δρ(EB − A) ≥ +0.02. Both
    margins required — the shipped A itself is not a safe floor.
  - Reference values validated in experiments/phase5d-covariate-2026-04-17/
    phase5d_fusion_validation.py (p=16): A0=0.280, A=0.259, EB=0.316,
    Δρ=+0.036 vs A0, +0.057 vs A.
  - Projected at p=7 from feature_count_sweep.py (N≈368, synthetic):
    Δρ ≈ +0.31 — real-data value pending post-Java-emit replay.
```

#### Phase 5E — Box-Cox A3 (implemented 2026-04-18)

```
Replaced `_rank_fitness` in src/starsector_optimizer/optimizer.py with a
module-level pure function `_shape_fitness(eb_fitness, completed_values,
config)` returning `(float, _ShapeDiag)`:
  - Refit λ via scipy.stats.boxcox on every _finalize_build call — ~1ms at
    n=300; documented deviation from the research doc's "every N trials"
    (batched refit saves nothing, would add (λ, shift, min, max) cache
    coherence burden against the rolling min-max rescale window).
  - Transform via boxcox(scalar, lmbda=λ) overload; min-max rescale to
    [0, 1] for JSONL schema stability.
  - ShapeConfig(min_samples=8, positivise_epsilon=1e-6) — below min_samples
    A3 falls through to min-max scaling (monotone in eb_fitness, preserves
    warm-up ordering for TPE). Constant population → 0.5. Non-finite input
    raises ValueError (upstream NaN = invariant violation, fail fast).
  - New JSONL fields: shape_lambda + shape_passthrough_reason.
  - New logging: one-shot "A3 Box-Cox activated at n=…" INFO log, per-trial
    line with λ or pt:<reason>, end-of-run "A3 Box-Cox summary" line.
  - 14 unit tests + 1 end-to-end at tests/test_optimizer.py::TestShapeFitness
    all pass; full suite 497 passed.

Validation (experiments/signal-quality-5d-2026-04-18/):
  - Ceiling 25% → 0.5% (50× reduction), top-5 overlap 0.02 → 0.44 (14×),
    invariant across 4 covariate-strength regimes.
  - Δρ D vs A = +0.006 (near-zero by design; both transforms monotone —
    the win is mechanical at the top quartile, not ρ).
  - CAT Fisher-info opponent selection (J strategy) showed +0.014 ρ on
    top of Box-Cox but remains deferred (observation-side change; revisit
    post-5F).

Ship gate — TODO: re-run Hammerhead 1000-trial budget with 5D+5E enabled,
verify top-k identification overlap improves over the 2026-04-17 reference.
```

### Expected Impact

| Metric | Current (5A + 5B + 5C) | After 5D | After 5E (shipped 2026-04-18) | After 5D + 5E |
|--------|------------------------|----------|--------------------------------|----------------|
| Opponent coverage | 10/54 via curriculum | 10/54 | 10/54 | 10/54 |
| A3 ceiling saturation | 25.3% | 25.3% | 0.3% (post-5D sim, `signal-quality-5d-2026-04-18/`) | 0.3% |
| Top-5 identification overlap | 0.05 | 0.03 | **0.43 (14× over 5D, post-5D sim)** | 0.43 |
| ρ(predicted, true) — Spearman | 0.280 (Hammerhead LOOO baseline) | 0.316 (+0.036 from EB shrinkage, validated) | +0.006 over 5D (both monotone; the win is top-k/ceiling, not ρ) | ~0.32 on Hammerhead LOOO (pending post-ship replay) |
| Exploit-cluster internal spread ρ | 0.31 (simulation) | 0.68 (post-5D sim) | 0.69 (post-5D sim) | 0.69 |
| Timeout-tier discrimination | Flat | Per-build gradient from prior-mean regression γ̂ᵀX_i | Gradient preserved through A3 (not clamped) | Gradient + spread |

Phase 5E's contribution is mechanical — Box-Cox preserves the α̂-proportional gradient at the top quartile that quantile rank destroyed. ρ barely moves (both A3 transforms are monotone); top-k overlap and ceiling saturation are the metrics that track the real win. Phase 5D supplies the ρ gain; Phase 5E keeps that gain visible to TPE's `l(x)` at the top. Phase 5F (regime-segmented optimization) is orthogonal to all of 5A–5E and directly addresses the exploit-cluster concentration by restricting the optimizer's input space rather than changing the estimator. Phase 5G (adversarial opponent curriculum) stays deferred as an empirical next step if 5D+5E+5F does not fully resolve exploit convergence within the unrestricted `endgame` regime.

### Testing
- **Phase 5D**: unit tests for `eb_shrinkage` (synthetic known-γ recovery; degenerate limits `σ̂²→0`, `σ̂²→∞`, `τ̂²=0` floor; MoM identifiability) and `triple_goal_rank` (exact rank preservation, histogram equality). Integration ablation on `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl` with LOOO ship-gate per §3.3 of `phase5d-covariate-adjustment.md`.
- **Phase 5E**: `tests/test_optimizer.py::TestShapeFitness` (14 unit tests + 1 end-to-end, all passing); simulation re-validation at `experiments/signal-quality-5d-2026-04-18/` confirms ceiling 25% → 0.5%, top-5 0.02 → 0.44, invariant across 4 covariate-strength regimes (`calibration_sweep.py`). Production validation re-runs Hammerhead 1000-trial budget post-ship.
- **Phase 5F**: ship-gate = replay on `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl` with the `mid` mask applied — verify (a) top-10 composition no longer contains `shrouded_lens` / `fragment_coordinator` / `neural_integrator` builds, (b) within-regime TPE convergence trace at matched in-regime trial count is comparable to the unmasked run, (c) a forward sanity run at N=300 with `mid` and `endgame` presets produces visibly distinct top clusters (endgame dominates on raw fitness; mid dominates on composite_score among deployment-reachable builds).
- **Phase 5G**: research only. Promoting to implementation requires extending the synthetic generative model with RPS-counterable exploits — the current flat-uplift exploit cannot validate exploiter-loop gains.

---

## Phase 6: Cloud Worker Federation

### Goal
Spend $N of compute and get $N of useful data — linear $→data scaling from $10 validation runs to $1000+ catalog campaigns. Unlocks multi-hull × multi-regime studies that would take weeks on the workstation. Full design in `docs/reference/phase6-cloud-worker-federation.md`.

### Status
**PLANNED (NEXT).** Precondition validated 2026-04-18: CPU cloud instances (AWS c7i.2xlarge, Hetzner CCX33) run Starsector at 2.2-2.4× local per-instance throughput after the LWJGL XRandR warmup fix (`instance_manager.py::_start_xvfb` now runs `xrandr --query` after Xvfb socket is ready). The original "GPU required" claim in spec 22 was a misdiagnosis of the XRandR mode-enumeration bug.

### Dependencies
- Phase 3 (`InstancePool` — worker-local parallelism unchanged)
- Phase 4 (`StagedEvaluator` already async-friendly)
- Phase 5F (planned; `(hull, regime)` is the natural federation unit — cloud federation and 5F are mutually enabling)
- Python libs: `hcloud` (Hetzner), `boto3` (AWS), `redis`, `apache-libcloud` (provider abstraction), `packer` (image baking)

### Key design decisions

**Study federation is the #1 architectural lever.** Optuna TPE (with `constant_liar=True`) degrades to random-sampling above ~30 in-flight trials per study. A $1000 mega-study wastes ~85% of spend as random sampling. Federation: many independent studies keyed by `(hull, regime, seed)`, each with ≤24 workers. Outer parallelism via the campaign manager; inner parallelism stays in TPE's efficient regime. For BREADTH (which $1000 buys), federation beats any single-study scaling on a multimodal landscape.

**Provider choice: Hetzner CCX33 Ashburn primary, AWS c7a us-west-2 fallback.** Hetzner is ~11% cheaper per matchup ($0.00109 vs $0.00123), has zero spot preemption, and Ashburn VA is 25-35 ms from NC. Existing `scripts/cloud/*.sh` already target it. AWS fallback for capacity diversification and spot-fleet diversification when Hetzner is full. Skip GPU cloud (not needed), ARM (LWJGL x86_64 only), DigitalOcean/Vultr/Linode (no spot).

**Sampler per study**: TPE default at ≤24 workers. CatCMAwM (already supported via `--sampler=catcma`) above 24. Hybrid schedule (random → CatCMAwM → TPE) for studies needing both breadth and precision.

**Packer pre-baked images** cut worker cold-start from ~5 min to ~30s. At 200 workers, saves ~3 hours of wasted wall-clock per campaign. Images include `x11-xserver-utils` (needed for the XRandR warmup fix to work — see spec 22).

**Cost discipline is non-negotiable**: campaign YAML has `budget_usd` hard cap; manager auto-terminates at 100%. Per-worker `max_lifetime_hours` (default 4). Tag-based sweeper cron as orthogonal backstop. Teardown discipline enforced by `trap EXIT` in all launch scripts and by the `.claude/skills/cloud-worker-ops.md` SOP.

**Orchestrator topology**: workstation holds the single Optuna Study (no distributed storage). Workers are pure `BuildSpec → CombatResult` evaluators. Study state is local SQLite per `(hull, regime, seed)`; workers rsync `study.db` back periodically. No PostgreSQL / JournalStorage / GrpcStorageProxy complexity — explicitly rejected because JournalStorage + GrpcProxy is broken in Optuna 4.2-4.4 (issue #6084) and direct PostgreSQL adds ops burden.

### Deliverables

1. **`src/starsector_optimizer/campaign.py`** — campaign manager daemon (~500 LOC):
   - `CampaignConfig` (frozen dataclass from YAML)
   - `CampaignManager` (main orchestration loop, Redis-backed study queues)
   - `CostLedger` (append-only JSONL, real-time cap enforcement)
   - `PlateauDetector` (50-trial windowed best-fitness slope; auto-release workers)
   - `WorkerAllocator` (provider-agnostic via Libcloud)

2. **`src/starsector_optimizer/cloud_provider.py`** — provider abstraction ABC with Hetzner + AWS concrete subclasses; Mock for offline tests.

3. **`src/starsector_optimizer/worker_agent.py`** — on-worker Python script: connects to orchestrator Redis, runs existing `run_optimizer.py` with queue-backed `InstancePool`, periodic study.db rsync back, self-terminate on `max_lifetime_hours`.

4. **`scripts/cloud/bake_image.sh`** — Packer wrapper for both Hetzner and AWS. One command rebuilds the baked image on game or dependency update.

5. **`scripts/cloud/federation/`** — new subdirectory: `launch_campaign.sh`, `status.sh`, `teardown.sh`, `final_audit.sh`.

6. **`docs/specs/29-campaign.md`** — formal spec for `CampaignConfig`, YAML schema, protocol between orchestrator and workers.

7. **`.claude/skills/cloud-worker-ops.md`** — SOP for running campaigns. Invoked by future Claude sessions on any cloud-campaign ask.

8. **`experiments/cloud-campaign-validation/`** — three validation runs: $10 smoke → $100 breadth → $200 real Phase 5F regime validation.

### Testing
- Unit: plateau detector on synthetic traces, cost ledger monotonicity, `CloudProvider` mock interface compliance.
- Integration: $10 smoke test completes in <1 hour, study.db has ≥100 trials, cost logged within 5% of actual.
- Cost-cap test: inject fake $100/hr worker into ledger; manager must terminate within 1 minute of crossing budget.
- Preemption test (AWS): deliberately terminate a worker mid-study; another worker picks up the same trial within 2 minutes.
- Teardown audit: `final_audit.sh` reports zero tagged resources after any campaign exit.

### Expected impact
- 2-hour burst of 30 workers delivers ~360 builds (vs 56 builds in 8-hour workstation run) for **$7.80** (Hetzner) or **$9** (AWS spot).
- $1000 budget = ~49,000 builds = full Phase 5F validation (40 hulls × 4 regimes × ~300 builds) in 1-2 weeks of burst runs.
- Frees the workstation for interactive work (editing, analysis, ad-hoc experiments).
- Precondition for Phase 7 (BoTorch GP): cross-hull validation at 30+ hulls is not feasible on the workstation alone.

### Rejected alternatives (full rationale in `phase6-cloud-worker-federation.md` §2-3)
- **One mega-study with 100-200 workers**: TPE saturation wastes 85% of budget as random.
- **PostgreSQL + GrpcStorageProxy storage**: ops burden; not needed when federation keeps each study's SQLite local.
- **JournalStorage + GrpcStorageProxy**: broken combination (Optuna #6084).
- **Ray / Ray Tune**: no Hetzner support; custom node_provider is 100-LOC.
- **SkyPilot / dstack / Modal / Covalent**: no Hetzner support; GPU/AI-focused.
- **Kubernetes / EKS / Fargate**: overkill for single-operator bursts; cluster overhead dominates.
- **Apache Airflow / Prefect / Dagster**: pipeline schedulers, not bursty batch compute.
- **GPU cloud instances**: not needed — CPU is 2.4× local per-instance after XRandR fix.
- **AWS Batch array jobs**: ~30-60s scheduler latency per job transition; direct EC2 Fleet is simpler.
- **EBS Fast Snapshot Restore (FSR)**: volume creation credits cap at 10 concurrent; breaks at 50-worker bursts.
- **AWS Auto Scaling Warm Pools**: not cost-effective at $1000 scale (~$16/mo EBS idle); worth it at $10k+.

---

## Phase 7: Structured Search-Space Representation

### Goal
Replace the Phase 4 Optuna TPE/CatCMAwM surrogate with a custom BoTorch Gaussian Process whose kernel composes subspace-specific priors. Target 2–4× sample efficiency at the 200–500 trial regime by exploiting known structure (weapon attributes, slot geometry, hullmod sparsity, role archetypes) that is invariant to game updates.

### Dependencies
- Phase 1 (data layer — `WeaponAttributes`, `ShipHull`, `.ship` JSON parser)
- Phase 4 (ask-tell loop, Optuna `BaseSampler` plugin hooks)
- Phase 5A–C (TWFE fitness, pruner, opponent curriculum — unchanged; feed the GP)
- Phase 5F (regime-segmented `(hull, regime)` study unit — one GP per pair)
- Python libraries: botorch, gpytorch, pyro (for SAAS NUTS posterior)

### Key design decisions (from 10-field 2026-04-17 sweep + compiler-autotuning deep-dive)

**Composite product kernel.** Single GP, product structure across subspaces:

```
k(x, x') = k_hullmods(SAAS) · k_weapon_id(transformed-overlap) · k_weapon_attr(Matérn, 7-dim)
         · k_slot(Matérn, 5-dim) · k_opp_small(Matérn, 3-dim, smalls only)
         · k_op(Matérn, 3-dim) · 1_gated_sentinel(inactive-slot handling)
         + k_item_residual(ICM) + k_slot_residual(ICM)
```

Follows BaCO (Hellsten 2024 arXiv:2212.11142 Eq. 3–5) for product structure + gated-sentinel, SAAS (Eriksson-Jankowiak 2021 arXiv:2103.00349) for the hullmod-boolean sparsity prior, Garrido-Merchán & Hernández-Lobato 2020 for the transformed-overlap categorical kernel, and ICM (Bonilla 2007, Álvarez 2011 arXiv:1106.6251) for the per-item + per-slot residual structure. The residual structure mirrors the Phase 5D empirical-Bayes fusion paradigm (α̂_TWFE + covariate prior) — the same "combine noisy measurements of a latent by Bayes rule" pattern applied one level up.

**7-attribute weapon vector for game-update transfer.** `(damage_type, weapon_type, size, op_cost, sustained_dps, flux_per_second, range)` — every field present in `weapon_data.csv` + `.wpn` JSON for every Starsector version since 0.95a. A new weapon added in a future patch inherits the attribute-kernel prior zero-shot.

**5-dim slot-feature vector for cross-slot kernel similarity.** `(forward_projection, arc_width, is_turret, lateral_offset, longitudinal_offset)`, all sourced from `.ship` JSON. Pools observations across slots of similar geometry within a hull (3 forward hardpoints ≈ 3 similar decisions, not 3 independent); sets up multi-hull transfer for free.

**Opponent-conditional small slots via kernel inputs, not rules.** 3-dim opponent summary `(has_missiles_frac, has_fighters_frac, mean_armor_rating)` concatenated to small-slot posteriors only. Preserves the load-bearing empirical constraint from community meta (§2.8 of phase6 doc): smalls shift against missile boats / carriers / brawlers / phase flankers; medium and large slots are hull-conditional.

**πBO archetype priors — hull-conditional mixture with decay.** Nine community-stable archetype *modes* (SO brawler, long-range sniper, kinetic-HE brawler, broadside, turret-flex, burst-missile, PD-carrier, flanker, phase striker — stable across 0.95→0.98) as a Gaussian-mixture density over **normalized** attribute × slot × hullmod space. Per-hull feasibility mask computed at hull-load from `.ship` JSON + ship-system registry drops infeasible modes to zero weight (Wolf can't realize broadside or turret-flex; only Paragon in vanilla gets the ATC ship system needed for turret-flex). Initial weights uniform over feasible modes — no meta-hull coverage bias. Self-correcting mixture weights (Hvarfner 2023 arXiv:2304.00397) online-downweight modes disagreeing with per-hull data. Acquisition multiplied by `π(x)^(β/t)` (Hvarfner 2022 arXiv:2204.11051). β = 5 default; 2–5× speedup if priors are correct, ≤ 1.5× overhead if adversarial. Community meta supplies the vocabulary of modes, not the per-hull weights — generalizes cleanly to non-meta hulls (Gemini, Mudskipper) and mod/patch hulls without manual configuration.

**Hull-size normalization for cross-hull mode definitions.** Raw `range` and `op_cost` do not transfer across hull sizes (700 range is long for a frigate, short for a capital). Kernel inputs use `range / max_weapon_range_per_hull_size` and `op_cost / hull.ordnance_points`, plus `hull_size` (FRIGATE/DESTROYER/CRUISER/CAPITAL_SHIP) as a 4-level ordinal context feature. Archetype mode means defined in the normalized space work across all hull sizes without rescaling.

**AI pilotability is absorbed, not hardcoded.** Combat simulation is AI-vs-AI by construction (Phase 2 combat harness), so the optimizer's deployment distribution is "ship in a fleet piloted by the vanilla Starsector combat AI" — not "ship piloted by the player." The AI is known to mispilot several community-top archetypes (SO brawler, phase striker, burst-missile — see §2.10 of the phase6 doc for the full table) that were designed for player piloting. Rather than hardcode AI-compatibility flags per mode (rejected because AI behavior changes across patches and pilotability interacts with hull), the self-correcting mixture update empirically downweights modes the AI mispilots — AI-hostile modes produce worse-than-predicted fitness and their mixture weight collapses via the Hvarfner 2023 marginal-likelihood-ratio update. Player-piloted flagship optimization is out of scope (would require engine-level input injection) and is deferred indefinitely.

**BOCA warm-start pilot.** 30 Latin-hypercube-seeded trials at the start of each new `(hull, regime)` pair; random-forest importance scores empirical-Bayes-initialize SAAS lengthscale priors. Insurance against the cold-start problem; drops out after ~100 main-BO trials.

**BoTorch as Optuna sampler plugin.** Custom `BaseSampler` wrapping `SaasFullyBayesianSingleTaskGP` with a custom product kernel. Posterior inference via NUTS/HMC — 1–2 hour wallclock overhead over a 200-trial run, acceptable.

### Deliverables

1. **`src/starsector_optimizer/surrogate.py`** — custom BoTorch kernel module:
   - `CompositeShipBuildKernel` (product kernel from §3.1 of phase6 doc)
   - `SAASHullmodKernel` (half-Cauchy ARD over hullmod booleans)
   - `TransformedOverlapKernel` (Garrido-Merchán 2020)
   - `AttributeMaternKernel` (7-dim weapon attributes)
   - `SlotFeatureMaternKernel` (5-dim slot features)
   - `OpponentFeatureKernel` (3-dim, small-slot inputs only)
   - `GatedSentinelEncoder` (conditional slot handling)
   - `ItemResidualKernel`, `SlotResidualKernel` (ICM, shrinkage priors)

2. **`src/starsector_optimizer/botorch_sampler.py`** — Optuna `BaseSampler` subclass wrapping the composite GP.

3. **BOCA warm-start phase** in `optimizer.py`: 30-trial Latin-hypercube seed + RF-importance computation + SAAS prior initialization.

4. **πBO acquisition layer**: nine-mode GMM prior density, decay-weighted EI.

5. **Opponent feature plumbing**: `(has_missiles_frac, has_fighters_frac, mean_armor_rating)` computation per trial; threaded into small-slot kernel inputs only.

### Testing
- **Unit**: each kernel component in isolation (synthetic recovery tests — known SAAS sparsity, known transformed-overlap patterns, known attribute smoothness).
- **Integration**: BoTorch sampler ↔ existing Phase 4 ask-tell loop; TWFE (5A) feeds the GP without modification.
- **Ship gate (4 synthetic + 1 replay, from phase6 doc §5.1)**:
  1. Rank-correlation gate on held-out synthetic fitness: top-10 ρ ≥ 0.70 (baseline TPE ≈ 0.45).
  2. Cold-start gate: new hull reaches 2000-trial TPE top-10 within 500 Phase-6 trials.
  3. Game-update gate: 5 new synthetic weapons interpolated from attributes — posterior mean within 0.2 σ with zero observations.
  4. Addressability gate: small-slot posterior prefers IPDAI + Dual Flak on missile-heavy opponent pools at p < 0.05.
  5. Hammerhead replay on `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl` — compare top-10 composition and convergence trace.

### Rejected alternatives (full rationale in `phase7-search-space-compression.md` §4)

- **Keep Optuna TPE with attribute warm-start only** — insufficient (~1.3× vs ~3× for full kernel).
- **Ma-Blaschko additive tree kernel** — subsumed by gated-sentinel + SAAS.
- **NAS weight-sharing (DARTS / OFA)** — doesn't transfer (no trainable tensor to share).
- **HyperMapper / BaCO off-the-shelf** — missing SAAS on the hullmod subspace.
- **Pure SAASBO (no mixed-space kernel)** — bad categorical handling.
- **BOCS horseshoe monomials** — binary-only; our 150-level weapon categoricals break it.
- **GFlowNets** — need 10⁵+ evaluations.
- **Fantasy-sports ILP** — presumes pre-fit per-component projections (the thing we're building).
- **Hearthstone MESB behavior-descriptor search** — needs phenotype→genotype map.
- **Silent rule-based small-slot fills** — explicitly rejected by user (smalls are opponent-conditional).
- **REMBO / ALEBO** — random linear subspaces fail on within-group interactions.
- **Standalone BOCA pilot** — commit-forever risk; retained only as SAAS warm-start.

Full design, theoretical grounding (mixed-categorical BO, SAASBO, ICM, πBO, naval architecture, Starsector community meta, compiler-autotuning BaCO deep-dive), and rejected-alternative chain in `docs/reference/phase7-search-space-compression.md`.

### Expected impact
- 2–4× aggregate sample efficiency at N = 200–500 (conservative composition: SAASBO 2–5×, BaCO kernel 1.36–1.56×, πBO 2–5× if priors correct, slot-feature pooling 1.1–1.3×).
- At Hammerhead-scale 650 trials / 24 h, 3× efficiency ≈ current 3-day run output in 24 h.
- Game-update transfer zero-shot for new weapons with attribute profiles near existing ones.

---

## Phase 8: Quality-Diversity

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
   - Train correction model on Phase B sim results (TabPFN or CatBoost from Phase 8)
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

## Phase 9: Neural Surrogate

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

**Collects ~400 sim results** — enough to start Phase 8 surrogate training (TabPFN at N<300).

**Estimated time:** 1 day. Zero cloud cost.

#### Stage 3: Full Optimization (local 8 instances, or cloud GPU)

**Local is recommended** — GPU acceleration makes local execution fast and free. Cloud requires GPU instances (AWS g4dn); CPU-only VMs (Hetzner CCX) are too slow due to software OpenGL rendering (tested 2026-04-12).

**Local (8 instances on dev machine):**
```
Single machine: all hulls sequential (8 parallel game instances)
```

**Per hull (full pipeline):**
| Step | Sims | Wall-clock (8 inst) |
|---|---|---|
| Warm-start (50 builds × 5 opponents) | 250 | ~50min |
| BO exploration (150 builds × ~3 avg opponents) | ~450 | ~1.5h |
| Racing (10 builds × 5 opponents × 5 replays) | 250 | ~50min |
| **Total** | **~950** | **~3.5h** |

**Campaign options:**

| Scope | Hulls | Setup | Sims | Wall-clock | Cost |
|---|---|---|---|---|---|
| Priority hulls | 10 | Local (8 inst) | ~10K | ~35h | $0 |
| All cruisers+capitals | 40 | Local (8 inst) | ~38K | ~133h | $0 |
| All combat-relevant | 118 | 3 × g4dn.2xl | ~112K | ~47h | ~$35 |
| + QD validation (Phase 7) | 118 | 3 × g4dn.2xl | ~150K | ~63h | ~$47 |

**Machine setup (cloud):** ~2 minutes per machine (parallel). Game dir is 361MB.

#### Stage 4: Local — Analysis + Phase 8 Training

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
Local: load_study → analysis + Phase 8 training
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
9. **Phase 6** replaces the Phase 4 surrogate with a composite kernel matching ship-build structure — 2–4× sample efficiency, game-update transfer, addressable smalls retained
10. **Phase 7** discovers build archetypes (MAP-Elites) — the most exciting output but needs the pipeline
11. **Phase 8** improves efficiency (~70% sim reduction) via neural surrogate — optional, the system works without it

Each phase is independently testable and shippable. Phase 1 alone produces a useful heuristic analysis tool. Phases 1-3 produce a batch simulation framework. Phases 1-4 produce an optimizer. Phases 1-4 + T1-T3 + Phase 5 produce a complete build discovery system.

### Development Timeline Estimate

| Stage | Work | Dependencies | Duration |
|---|---|---|---|
| Stage 1: Local code dev | Phase 4-7 Python code, tests, heuristic validation | Phases 1-3.5 complete | 2-3 days |
| Stage 2: Local sim validation | End-to-end with 2 instances, opponent pool validation | Stage 1 | 1 day |
| Stage 3: Cloud full optimization | 10-118 hulls on Hetzner CCX33 | Stage 2, Hetzner account | 12-63h wall-clock |
| Stage 4: Analysis + surrogate | Phase 8 training, visualization | Stage 3 results | 1 day |

**Total: ~5 days of active development + 1-3 days of cloud compute wall-clock.**
