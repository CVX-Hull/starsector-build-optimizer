# Phase 4 Research Findings

Consolidates all research conducted before Phase 4 implementation. Captures the reasoning behind key design decisions so future work can understand WHY, not just WHAT.

---

## 1. Optimizer Selection: Optuna TPE Over Bounce/SMAC3

### The Problem

Our search space is ~50-70 dimensions: 15-30 categorical weapon slots (5-50 options each), 20-40 binary hullmod flags, 2 integer variables (vents, caps). Complex constraints: slot compatibility, hullmod incompatibilities, OP budget. Evaluation: ~22-35s wall-clock per matchup. 4-8 parallel game instances.

### Candidates Evaluated

| Optimizer | Batch Parallel | Constraints | PyPI | Dims Tested | Verdict |
|---|---|---|---|---|---|
| **Bounce** | qEI (good) | None | No | Up to 125D | Rejected: no constraints, no package |
| **SMAC3** | Broken | ConfigSpace (best) | Yes | Up to 100D | Rejected: batch parallelism broken |
| **Optuna TPE** | constant_liar (OK at 4-8) | Via repair + constraints_func | Yes | 100s of dims | Selected: best practical fit |
| **CatCMAwM** | Natural (pop_size) | None | Yes (cmaes) | Up to 40D | Secondary: QD emitter, refinement |
| **HEBO/MCBO** | Via MCBO | None | Yes | Varies | Rejected: research code quality |
| **Ax/BoTorch** | qNEI (best) | Linear only | Yes | High (TuRBO) | Backup: if need better batch BO |

### Key Research Findings

**Bounce (NeurIPS 2023)**:
- No constraint support. The paper explicitly lists this as future work.
- The internal binning/embedding maps the raw space non-trivially, making repair operator interaction poorly defined.
- Research code at github.com/lpapenme/bounce, not on PyPI. Poetry-based.
- Strong batch qEI and GP noise model, but unusable without constraint support.

**SMAC3 (v2.3.1)**:
- Best-in-class ConfigSpace: EqualsCondition, ForbiddenClause, etc.
- But batch parallelism is broken. SMAC's own team (GitHub issue #1131) acknowledges parallel SMAC is "about as good as random search." No batch acquisition function like qEI.
- Since we have `repair_build()` that handles all constraints, ConfigSpace's advantage is moot.

**Optuna TPE**:
- Clean ask-tell API maps perfectly to our architecture.
- `constant_liar=True` handles pending evaluations for 4-8 parallel instances. Degrades ~1.5-2x vs sequential — acceptable.
- Swappable samplers via OptunaHub (CatCMAwM, TuRBO, c-TPE). No code changes needed.
- Needs tuning for high-D: `n_ei_candidates=256`, `n_startup_trials=100`.

### TPE Limitations at 50-70D

Scott's rule bandwidth: `n^(-1/(4+d))`. At d=60, converges negligibly. TPE needs **100-200 random trials** before meaningfully outperforming random search. Known failure modes:
1. Wastes budget exploring irrelevant categorical dimensions (GitHub issue #3826)
2. Even `multivariate=True` has diagonal kernel — misses weapon-slot interactions
3. Default `n_ei_candidates=24` is absurdly few for 60D space

---

## 2. Repair Operator + BO Interaction

### The Problem

Optimizer proposes raw build X (may violate constraints). `repair_build()` maps X → X' (feasible). Optimizer sees score(X') but proposed X. Two issues:

1. **Many-to-one collisions**: Many raw proposals repair to the same build. Wastes simulation budget.
2. **Landscape distortion**: Surrogate sees infeasible coordinates with repaired scores — creates plateaus.

### Lamarckian vs Baldwinian (Literature: Ishibuchi 2005)

- **Lamarckian**: Record (X', score(X')). TPE learns the feasible manifold directly.
- **Baldwinian**: Record (X, score(X')). Preserves genotype diversity.
- In EA context, Baldwinian wins (avoids premature convergence).
- **In BO/TPE context, Lamarckian wins**: TPE's density estimators model coordinates directly. Learning the feasible manifold is more useful than learning the raw+repair mapping.

### Recommended Strategy

1. **Lamarckian recording**: `study.add_trial(create_trial(repaired_params, score))`
2. **Deduplication cache**: Hash repaired builds, return cached score for collisions. Estimated 10-30% collision rate.
3. **Constraint function**: Report OP overshoot via `constraints_func` → biases TPE away from infeasible regions.
4. **n_startup_trials=100**: Enough random exploration before TPE kicks in at 50-70D.

---

## 3. Opponent Selection Strategy

### The Problem

Starsector has strong rock-paper-scissors dynamics:
- Kinetic: 200% to shields, 50% to armor
- HE: 50% to shields, 200% to armor
- Energy: 100% to everything

Single-opponent fitness → counter-builds, not robust builds.

### Options Evaluated

| Strategy | Sims/eval | Robustness | Budget Impact |
|---|---|---|---|
| Single opponent | 1 | Poor (RPS exploitation) | Cheapest |
| Fixed diverse pool (5-6) | 5-6 | Good | 5-6x cost per build |
| Elo/TrueSkill rating | 50-100+ per build | Excellent | Too expensive |
| Co-evolution | Varies | Best (but unstable) | Impractical (one-sided) |
| Nash equilibrium | N x M matrix | Theoretically optimal | Too expensive for $30 |

### Recommendation

**Fixed pool of 5-6 opponents** covering archetypes (shield tank, armor tank, kiter, carrier, phase, balanced). Fitness = average or minimax HP differential across pool.

**Budget math**: 5 opponents × 200 builds = 1000 sims. With WilcoxonPruner pruning bad builds after 2-3 opponents, effective cost drops to ~600 sims.

---

## 4. Multi-Fidelity: No Short Sim, Heuristic as Prior Mean

### Why No Short Sim

Phase 3.5 simulation proved short timeouts corrupt the optimizer:
- 60s timeout → 100% timeout rate for cruisers → flat fitness → +20% optimizer iterations
- Approach time alone is ~6s wall-clock at 5x speed
- Between-trial pruning via WilcoxonPruner handles budget efficiency — replaces the need for short sim

### Why Not Full MFBO

Best-practices paper (arXiv 2410.00544) recommends MFBO only when R² > 0.75 between fidelities. Our heuristic R² ≈ 0.49 — below threshold. MFBO can actually perform *worse* than single-fidelity when low-fidelity is unreliable.

### Heuristic as Prior Mean (Recommended)

Based on the particle accelerator BO paper (Scientific Reports 2025):
- Set GP prior mean = heuristic_score(x)
- GP learns residual f_sim(x) - f_heuristic(x), which is smoother
- 73D problem: standard BO hadn't converged after 900 iterations; prior-mean BO converged in one step
- Even correlation r~0.41 helped. Our r~0.7 should provide substantial speedup.
- piBO (ICLR 2022) guarantees convergence at regular rates regardless of prior quality

### Implementation via Optuna Warm-Start

Since we use Optuna TPE (not BoTorch GP), the prior-mean approach is implemented as warm-starting: evaluate 50K builds with heuristic, add top-500 to the study as initial trials. TPE's density estimators are biased toward heuristically-good regions.

---

## 5. Neural Surrogate Features (Phase 8 Preparation)

### Heartbeat Trajectory Features

Phase 3.5's enriched heartbeat (6 fields per ~1s) provides time-series data. Convert to fixed-length features for tabular models:

**Fixed checkpoints** (most predictive based on StarCraft literature):
- `player_hp_15s`, `enemy_hp_15s`, `hp_diff_15s`
- `player_hp_30s`, `enemy_hp_30s`, `hp_diff_30s`
- Same at 60s, 90s, 120s

**Summary statistics**:
- HP loss rate (linear slope)
- HP differential mean, std, final value
- Momentum reversals (sign changes in HP differential)
- Fight duration

Total: ~20 trajectory features per fight. All numeric, usable by any tabular model.

### TabPFN Limitations

TabPFN v2 (Nature 2024) degrades with >10 unique categories per feature. Our weapon IDs have 50+ unique values per slot. **Must convert to derived numeric features**, not raw weapon IDs:
- Per-slot: DPS, flux/s, range, damage type fraction
- Aggregate: total DPS, flux balance, EHP, range coherence
- This reduces 50-70D mixed → ~30-40D purely numeric

### Model Progression

| Sample Count | Model | Features | Expected R² |
|---|---|---|---|
| N < 300 | TabPFN v2 | Derived numeric only | ~0.6-0.7 |
| N = 300-1000 | CatBoost | Derived + raw categoricals | ~0.7-0.8 |
| N > 1000 | CatBoost ensemble | All features + trajectory | ~0.8-0.9 |

### Target Variable

**Continuous HP differential** (range -1.0 to +1.0), not binary win/loss. Preserves margin-of-victory information, provides smoother gradients for surrogate learning.

---

## 6. Budget Analysis

### Per Hull ($30 total budget)

| Stage | Sims | Wall-clock (8 inst) | Cost |
|---|---|---|---|
| Heuristic screening (50K builds) | 0 | ~10s | $0 |
| Warm-start (50 builds × 5 opponents) | 250 | ~50min | ~$3 |
| BO exploration (150 builds × ~3 opponents avg with WilcoxonPruner) | ~450 | ~1.5h | ~$5 |
| Racing (10 builds × 5 opponents × 5 replays) | 250 | ~50min | ~$3 |
| **Total per hull** | **~950** | **~3.5h** | **~$11** |

**With $30**: Optimize 2-3 hulls fully, or 5+ hulls with reduced racing.

### Cloud Provider

Hetzner CCX43: $0.22/hr, 8 instances. 8 instance-hours per hull × $0.22 = $1.76 compute. Total cost dominated by machine setup time, not compute.

---

## 7. Open Questions

1. **Heuristic calibration**: Can we improve R² above 0.75 by calibrating heuristic weights against simulation results? If yes, switch to full MFBO. Integration test confirmed the gap is large: heuristic top-3 all had negative sim fitness (logistics hullmod spam scores well heuristically but fails in combat). This is a Phase 8 task.

2. **Repair collision rate**: **Resolved — 0% collisions.** Measured: 50,000 random builds on Wolf (smallest hull, 70D), all unique after repair. 2,000 builds each on Eagle (77D) and Onslaught (86D) also 0% collisions. The search space is so vast that the greedy OP-drop repair produces sufficiently diverse outputs. The BuildCache is retained as a safety net but won't save sim budget in practice. No need for priority-list encoding.

3. **Opponent pool composition**: The 4-6 opponents per size are manually selected. How sensitive are results to pool composition? Test with 3 opponents vs 5 vs 7. Hull-size mismatches were fixed in audit; composition sensitivity not yet tested.

4. **Noise floor**: How much variance does the AI behavior introduce? If high, may need more replicates per matchup. If low, 1 replicate per opponent may suffice. Integration test showed some matchups are deterministic (both sides at full HP = non-engagement), while others have clear outcomes. Need multiple replays of the same matchup to measure variance.

5. **Heuristic rewards logistics hullmods**: The heuristic's `op_efficiency` and `effective_hp` metrics reward logistics mods (recovery_shuttles, efficiency_overhaul, reinforcedhull) that contribute nothing in arena combat but have real value in campaign gameplay. Excluding logistics from the search space would produce builds impractical for actual fleet use. The optimizer will naturally learn that combat mods produce better sim fitness — the warm-start heuristic scores are scaled to 0.1x so they're weak priors that real sim signal overwrites after 20-30 trials. Not a bug; working as designed.

---

## 8. Eagle 200-Trial Experiment Findings

### Setup
- 203 evaluations of Eagle hull against 6 cruiser opponents (dominator_Assault, dominator_XIV_Elite, aurora_Assault, heron_Attack, doom_Strike, eagle_Assault)
- 4 parallel Starsector instances via Xvfb, batch size 4
- TPE sampler with 500 warm-start builds, 300s timeout
- Total wall clock: 4.3 hours, throughput: 47.6 trials/hour

### Key Findings

**Timeout waste:** 30% of matchups (363/1218) hit the 300s timeout, consuming 56% of total combat time (30.2h out of 53.6h). Symmetric stalemates (both sides at near-zero HP loss rates due to shield/flux equilibrium) were the primary cause.

**heron_Attack is noise:** 74% timeout rate with near-zero HP differential (0.0% player wins, 0.0% player losses). The Eagle cannot engage a kiting carrier in 1v1. Removed from CRUISER opponent pool.

**Timeout strategy benchmark:** Replaying the evaluation log under different timeout strategies, a 200s flat cap saves **22.5% of combat time** at rho=0.958 Spearman rank correlation. Shorter timeouts corrupt rankings. Between-trial pruning via WilcoxonPruner (dropping bad builds after 2-3 opponents) is a more effective budget-saving strategy than mid-fight timeout manipulation.

**Note:** The 203-trial Eagle experiment data was invalidated by a combat harness bug (`spawnFleetMember()` caused ships to retreat). The qualitative findings above likely hold directionally but need re-validation with the fixed single-matchup-per-mission harness.

### Actions Taken
- Timeout strategy analysis informed between-trial pruning approach
- Removed `heron_Attack` from CRUISER `DEFAULT_OPPONENT_POOL` (spec 23)
- Added CatCMAwM as first-class sampler option via `--sampler catcma` (spec 24)
- Added parameter importance analysis via fANOVA and `--fix-params` support (spec 26)

### Signal Quality Analysis (Phase 5 Research Input)

Post-experiment analysis of the 203-trial evaluation log revealed several noise characteristics that motivated Phase 5 signal quality research:

**Per-opponent signal quality:**
- dominator_XIV_Elite has **negative correlation** with overall fitness (ρ = -0.225) — builds that do well against it tend to do worse overall
- doom_Strike has the highest within-outcome variance (TIMEOUT: std = 0.547)
- Inter-opponent correlations are near-zero (ρ = 0.0–0.2) — orthogonal but noisy

**Effect size:** Cohen's d = 3.30 between best build and median. The optimizer finds real signal, but the 0.4% win rate means it navigates "shades of losing" — fitness differences within the TIMEOUT margin tier.

**Leave-one-out opponent analysis:** Dropping dominator_XIV_Elite *improves* rank correlation with full fitness (0.578). Dropping doom_Strike hurts most (0.355).

These findings drove the Phase 5 research into opponent normalization, multi-fidelity evaluation (Hyperband over opponents), multi-objective decomposition, and curriculum learning. See `docs/reference/phase5-signal-quality.md` for the full Phase 5 research and recommendations.

---

## Sources

### Optimizer Comparison
- Bounce: arXiv 2307.00618 (NeurIPS 2023)
- SMAC3 batch issue: GitHub optuna/optuna#1131
- Optuna TPE components: arXiv 2304.11127
- c-TPE: arXiv 2211.14411 (IJCAI 2023)
- CatCMAwM: arXiv 2504.07884 (GECCO 2025)
- Constant liar: Ginsbourger et al. (2010), Optuna GitHub #2753
- Batch EI degradation: Azimi et al. (ICML 2012)

### Repair Operators
- Lamarckian vs Baldwinian: Ishibuchi et al. (EMO 2005)
- Decoder-based EA: Koziel & Michalewicz (1999)
- Constraint handling survey: Coello Coello (2002)
- Surrograte-assisted constrained EA: Springer (2024)

### Opponent Selection
- EGTA survey: arXiv 2403.04018
- PSRO survey: arXiv 2403.02227
- BO for Nash: arXiv 1804.10586
- Pareto set learning: arXiv 2210.08495 (NeurIPS 2022)

### Multi-Fidelity
- MFBO best practices: arXiv 2410.00544 (Nature Comp Sci 2025)
- Prior-mean BO: Scientific Reports (2025)
- piBO: arXiv 2204.11051 (ICLR 2022)
- Warm-starting BO: arXiv 1608.03585
- rMFBO: arXiv 2210.13937

### Surrogate Features
- TabPFN v2: Nature (2024), arXiv 2502.17361
- StarCraft prediction: Sanchez-Ruiz (2017)
- tsfresh: Neurocomputing (2018)

---

## 8. Cloud Deployment and Study Persistence

### Hetzner Cloud (CCX33 is the sweet spot)

| Machine | vCPUs | RAM | Game Instances | Cost/hr |
|---------|-------|-----|----------------|---------|
| CCX33 | 8 | 32GB | 8 | ~$0.11 |
| CCX43 | 16 | 64GB | 16 | ~$0.22 |

CCX33 is sufficient: Starsector is single-threaded per instance, Xvfb is near-zero CPU. 8 instances on 8 vCPUs works. Game directory is only **361MB** (not the 2GB earlier estimated), so rsync to cloud takes ~5-10 seconds.

**Multi-machine is strictly better than bigger machine:** 3 × CCX33 (24 instances, $0.33/hr) gives 3x the throughput for less than 1 × CCX53 (32 instances, $0.40/hr). Each machine runs independent hulls, no coordination overhead.

**Setup time: ~2 minutes per machine** (parallel). Cloud-init installs Xvfb/xdotool/libs, rsync sends game + optimizer + prefs.xml, `uv sync` installs Python deps.

### Optuna Study Persistence

**TPESampler is stateless by design.** Verified in source code: `sample_relative()` and `sample_independent()` reconstruct everything from `study._get_trials()` on every call. No cached model, no internal state. Transferring the SQLite file preserves all "knowledge."

**Local → Cloud workflow:**
1. Local: `study = optuna.create_study(storage="sqlite:///study.db")`
2. Add heuristic warm-start trials + small sim validation
3. `scp study.db cloud:/opt/optimizer/`
4. Cloud: `study = optuna.load_study(storage="sqlite:///study.db")` → heavy sim
5. `scp study.db` back to local for analysis

**constant_liar works correctly after transfer.** It operates on RUNNING trials — after transfer, all trials are COMPLETE or FAILED. Clean up any zombie RUNNING trials before resuming.

**Each hull gets its own study.** No cross-machine coordination needed. Results also logged to shared JSONL for Phase 8 surrogate training.

### Concrete Search Space Dimensions (from real game data)

| Hull | Size | Slots | Hullmods | Total Dims | Options/slot range |
|---|---|---|---|---|---|
| Wolf | Frigate | 6 | 62 | 70 | 9-16 |
| Hammerhead | Destroyer | 8 | 62 | 72 | 12-19 |
| Eagle | Cruiser | 13 | 62 | 77 | 9-16 |
| Dominator | Cruiser | 16 | 62 | 80 | 8-13 |
| Doom | Cruiser (phase) | 12 | 62 | 76 | 9-34 |
| Onslaught | Capital | 22 | 62 | 86 | 8-13 |

All hulls have exactly 62 eligible hullmods and 2 incompatible pairs. The 62 binary hullmod flags dominate dimensionality — even frigates have 70D. This means TPE's high-D limitations apply universally. The heuristic warm-start (top-500 from 50K random builds) is critical for all hull sizes.
