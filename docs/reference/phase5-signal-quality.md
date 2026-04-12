# Phase 5: Signal Quality — Research Findings and Recommended Approach

Improving the signal-to-noise ratio of combat fitness evaluations. This document is self-contained — it explains the problem, summarizes cross-domain research, and recommends a phased implementation plan.

---

## 1. Background

### How the Optimizer Works (Phase 4)

The optimizer discovers effective ship builds for Starsector via Bayesian optimization:

1. **Propose** a build (weapon loadout, hullmods, flux allocation) via Optuna TPE/CatCMAwM
2. **Repair** to feasibility (OP budget, hullmod compatibility)
3. **Evaluate** by running AI-vs-AI combat simulation against 5 diverse opponents
4. **Score** using a hierarchical composite fitness: wins [1.0, 1.1], timeouts [-0.5, +0.5], losses [-1.0, -0.85]
5. **Aggregate** per-opponent scores into a single mean fitness
6. **Report** to Optuna, which uses the score to guide future proposals

Each evaluation requires ~5 matchups × ~10 seconds each = ~50 seconds of wall-clock time across 4–8 parallel game instances. The search space is ~70 dimensions (13 weapon slots × options each, 62 binary hullmod flags, 2 integer flux variables).

### The Problem: Signal Quality

A 203-trial Eagle cruiser experiment (4.3 hours, 47.6 trials/hour) revealed that the optimizer finds real signal but extracts it inefficiently:

| Metric | Value | Implication |
|--------|-------|-------------|
| Win rate | 0.4% (4/1015 matchups) | The primary signal (wins) barely exists |
| Cohen's d (best vs median) | 3.30 | The optimizer IS finding real signal — best build is 3.3σ above median |
| Fitness range | [-0.65, +0.38] | Everything lives in the engagement-score band, not the win tier |
| Coefficient of variation | 0.41 | High noise relative to signal |
| Builds evaluated >1 time | 0 | Cannot measure within-build replay variance |

**The optimizer navigates "shades of losing."** Almost all builds lose every matchup. The fitness signal comes entirely from engagement quality differences within the TIMEOUT/STOPPED tier — small differences in HP differential that are easily overwhelmed by combat stochasticity.

### Per-Opponent Analysis

The 5 opponents (after removing the noise-only heron_Attack in Phase 4) test orthogonal capabilities but contribute very differently to signal quality:

| Opponent | Mean HP Diff | Std | ρ with Fitness | Outcome Entropy |
|----------|-------------|-----|----------------|-----------------|
| aurora_Assault | -0.792 | 0.340 | 0.567 | 1.356 bits |
| dominator_Assault | -0.626 | 0.360 | 0.314 | 1.242 bits |
| dominator_XIV_Elite | -0.625 | 0.321 | **-0.225** | 1.277 bits |
| doom_Strike | -0.695 | 0.499 | 0.564 | 1.163 bits |
| eagle_Assault | -0.222 | 0.424 | 0.291 | 1.256 bits |

Key findings:
- **dominator_XIV_Elite has negative correlation with fitness** (ρ = -0.225). Builds that do well against it tend to do worse overall — it actively hurts ranking quality.
- **doom_Strike has the highest within-outcome variance** (STOPPED outcomes: std = 0.547). Bimodal: either the build survives or gets destroyed by mines.
- **Inter-opponent correlations are near-zero** (ρ = 0.0–0.2). Each opponent tests genuinely orthogonal capabilities, but this means averaging them adds independent noise.
- **Leave-one-out**: Dropping dominator_XIV_Elite improves rank correlation to 0.578 (from 1.000 full). Dropping doom_Strike hurts most (0.355).

---

## 2. Research Summary

We surveyed methods from four domains: noisy optimization, game AI evaluation, variance reduction statistics, and multi-objective/surrogate-assisted optimization. Below are the methods most applicable to our problem, filtered through the **Bitter Lesson** constraint: methods must be general, scale with compute, and not require per-hull hand-tuning.

### 2.1 Variance Reduction (Statistics / Simulation Literature)

**Opponent Normalization (z-score per opponent)**
For each opponent, maintain running mean μ and standard deviation σ of fitness scores. Normalize each matchup result: `z = (score - μ) / σ`. Aggregate z-scores instead of raw scores. This removes between-opponent difficulty bias — aurora_Assault (mean=-0.79) and eagle_Assault (mean=-0.22) become comparable.
- *Ref: Nelson (2010), "Variance Reduction" in Handbooks in OR/MS*
- *Impact: High. Effort: Trivial (~20 lines). Extra evals: None.*

**Control Variates (heuristic scorer as proxy)**
Given expensive simulation fitness Y and cheap heuristic score Z with known mean E[Z]: `Y_adj = Y - c * (Z - E[Z])`, where `c = Cov(Y,Z)/Var(Z)`. Variance reduction = `(1 - ρ²) × Var(Y)`. At ρ=0.7, variance drops to 51%. We already compute heuristic scores for warm-start; using them as a control variate costs nothing.
- *Ref: Law (2015), "Simulation Modeling and Analysis", Ch. 11*
- *Impact: High (if ρ > 0.5). Effort: Low. Extra evals: None.*

**Common Random Numbers (CRN)**
When comparing builds A and B against the same opponent, use identical random seeds. The difference `fitness(A) - fitness(B)` has much lower variance than individual fitnesses because shared randomness cancels. Empirical studies show 80–94% variance reduction on differences.
- *Ref: Glasserman & Yao, "Guidelines for Common Random Numbers"*
- *Impact: Very high. Effort: Medium-high (requires Java-side seed control). Feasibility uncertain — Starsector AI uses RNG in ways that may cause stream drift between different weapon loadouts.*

### 2.2 Evaluation Budget Efficiency (Noisy Optimization Literature)

**Multi-Fidelity / Successive Halving (Hyperband)**
Evaluate builds against opponents sequentially. After each opponent, report intermediate fitness. Optuna's `HyperbandPruner` kills unpromising builds early — bad builds die after 1–2 opponents (saving 60–80% of evaluation cost), good builds get full 5-opponent evaluation. The "fidelity" dimension is number of opponents evaluated.
- *Ref: Li et al. (2018), "Hyperband" — JMLR*
- *Impact: Very high (2–3× budget efficiency). Effort: Medium (pipeline restructuring). Extra evals: Negative (saves budget).*

**Racing / Adaptive Resampling (F-Race / OCBA)**
Evaluate sequentially, statistically eliminate inferior builds early. F-Race uses Friedman's test; OCBA maximizes probability of correct selection by allocating more replays to uncertain builds. Conceptually overlaps with Hyperband but frames the problem as hypothesis testing.
- *Ref: Birattari et al. (2002), "A Racing Algorithm for Configuring Metaheuristics" — GECCO; Chen et al. (2000), OCBA — Discrete Event Dynamic Systems*
- *Impact: Medium-high. Effort: Medium.*

### 2.3 Richer Objectives (Multi-Objective / Reward Shaping Literature)

**Multi-Objective Decomposition**
Instead of collapsing per-opponent telemetry into one composite score, report 3–4 objectives to Optuna: (a) engagement score, (b) damage efficiency ratio, (c) survivability (HP preserved), (d) flux management quality. Use Optuna's built-in `NSGAIISampler` or MOTPE. The Pareto front naturally explores different strategies (tanky, DPS, kiting) without requiring hand-crafted weights.
- *Ref: Optuna multi-objective tutorial; helper-objective literature (Jensen, 2005)*
- *Impact: High. Effort: Low (Optuna supports natively). Extra evals: None.*

**Rank-Based Fitness Shaping**
Report quantile rank instead of raw fitness. Ranks are invariant to score distribution shape and reduce outlier influence. Spreads out the dense "shades of losing" cluster. CMA-ES does this internally; NES and OpenAI ES use it explicitly.
- *Ref: Wierstra et al. (2014), "Natural Evolution Strategies" — JMLR*
- *Impact: Medium. Effort: Trivial. Extra evals: None.*

**CVaR Aggregation (Robust Optimization)**
Replace mean-across-opponents with Conditional Value-at-Risk: the mean of the worst K opponents. CVaR_40% = mean of worst 2 out of 5. Encourages builds without exploitable weaknesses. Smoothly interpolates between mean (α=100%) and worst-case (α→0%).
- *Ref: Rockafellar & Uryasev (2000), "Optimization of CVaR" — Journal of Risk*
- *Impact: Medium. Effort: Trivial. Extra evals: None.*

### 2.4 Problem Difficulty (Game AI / RL Literature)

**Curriculum Learning (Automated Opponent Difficulty)**
The 0.4% win rate means the problem is too hard. Start optimization against weaker opponents (stock builds of the same hull size). As the rolling win rate exceeds a threshold (e.g., 30%), introduce harder opponents. The optimizer always faces a tractable challenge with abundant gradient signal.
- *Ref: Narvekar et al. (2020), "Curriculum Learning for RL" — JMLR*
- *Impact: Very high (win rate 0.4% → 20–40%). Effort: Medium. Extra evals: None (reuses same budget).*

**PFSP-Style Opponent Selection**
From AlphaStar (Vinyals et al., 2019): select opponents proportional to how much the current agent struggles against them. Maximizes information per evaluation. In our case: for re-evaluation or curriculum progression, prioritize opponents where the build's performance is most uncertain.
- *Ref: Vinyals et al. (2019), "Grandmaster Level in StarCraft II Using Multi-Agent RL" — Nature*
- *Impact: Medium. Effort: Low.*

**Score-Based Bayesian Skill Rating**
Model each build's "skill" as a Gaussian distribution updated on continuous HP differential (not just win/loss). Provides uncertainty estimates that guide re-evaluation. Extends TrueSkill/Glicko to continuous margins.
- *Ref: Nguyen et al. (2012), "Score-Based Bayesian Skill Learning" — ECML*
- *Impact: Medium. Effort: Medium. Most benefit already captured by opponent normalization.*

### 2.5 Methods Evaluated but Not Recommended

| Method | Why Not |
|--------|---------|
| Custom GP-based BO (BoTorch) | Doesn't scale to 70D mixed space; heavy implementation and maintenance |
| Heteroscedastic noise GPs | Requires BoTorch, doesn't scale to 70D |
| Hand-curated opponents per hull | Doesn't generalize across hulls or game versions (Bitter Lesson) |
| Nash averaging | Overkill for 5 opponents; normalization captures most of the benefit |
| MAP-Elites / CMA-MAE | Different paradigm (exploration, not exploitation); worth a separate phase |
| TrueSkill / Glicko rating models | Complex; most benefit comes from simpler opponent normalization |
| Antithetic variates | Doubles simulation cost; nonlinear response function limits effectiveness |

---

## 3. Recommended Approach

### Design Principle: Bitter Lesson Compliance

Rich Sutton's Bitter Lesson: general methods that scale with computation beat hand-crafted approaches in the long run. Every method below must be:
- **General**: Works for any hull, any opponent pool, any game version
- **Automatic**: No per-hull parameter tuning
- **Scalable**: Benefits increase with more compute (more instances, more evaluations)

### Architecture Change: Sequential Opponent Evaluation

The current pipeline evaluates all 5 opponents in parallel and averages. The proposed pipeline evaluates sequentially, enabling adaptive budget allocation:

```
Current:  build → [all 5 opponents] → average → single score → Optuna

Proposed: build → opponent₁ → normalize → report intermediate
                → opponent₂ → normalize → report intermediate → prune?
                → opponent₃ → normalize → report intermediate → prune?
                → opponent₄ → normalize → report intermediate
                → opponent₅ → normalize → report final
```

This single architectural change enables opponent normalization, Hyperband pruning, opponent ordering, and curriculum learning — all within Optuna's existing infrastructure.

**Throughput prerequisite:** Sequential evaluation requires persistent game sessions and mixed-build ASHA scheduling (Throughput Phases T1-T3) to be efficient. Without them, single-matchup game launches have 78% startup overhead. See `docs/reference/throughput-optimization.md` for the full throughput research.

### Phase A: Quick Signal Improvements (no pipeline change)

Modify scoring and aggregation only. No changes to evaluation flow.

**A1. Opponent Normalization**
Maintain running mean/std per opponent. Z-score each matchup result before averaging. Removes between-opponent difficulty bias automatically. Adapts as opponent statistics shift.

**A2. Control Variate Correction**
`fitness_adj = fitness_sim - c * (heuristic_score - E[heuristic])`. Estimate correlation ρ from first 50 evaluations. Apply correction only if ρ > 0.4 (otherwise noise increases). E[heuristic] estimated from warm-start population.

**A3. Rank-Based Fitness Shaping**
Report quantile rank to Optuna instead of raw composite score. Spreads out the dense losing cluster where most signal lives.

*Expected impact: CoV reduction from 0.41 to ~0.25. No extra evaluations.*

### Phase B: Sequential Evaluation Pipeline (main change)

Restructure evaluation to process opponents one at a time with intermediate reporting.

**B1. Opponent Ordering**
Sort opponents by historical discriminative power: `Var_across_builds(z_score_i)` for opponent i. The opponent that best separates good from bad builds goes first. Ordering learned from data, updates automatically across runs.

**B2. Hyperband Pruning**
Use Optuna's `HyperbandPruner`. After each opponent, report the running normalized mean as an intermediate value. Optuna eliminates builds whose intermediate performance is in the bottom percentile. Bad builds terminated after 1–2 opponents.

**B3. Batch Evaluation Adaptation**
With sequential opponent evaluation, a single "batch" now evaluates one opponent across multiple builds simultaneously (utilizing all parallel instances), rather than all opponents for fewer builds. This maintains instance utilization while enabling per-opponent pruning.

*Expected impact: 2–3× budget efficiency. Average evaluation cost drops from 5 matchups to ~2.5 per build.*

### Phase C: Adaptive Opponent Pool (implemented)

**C1. Multi-Objective Decomposition** (deferred)
Deferred — requires fundamental changes to the A1→A2→A3 pipeline (scalar fitness assumptions), conflicts with MedianPruner (compares scalars), and the 3 proposed objectives are hand-designed decompositions at odds with the bitter lesson.

**C2. Adaptive Opponent Pool** (implemented)
Two-layer design inspired by racing algorithms (irace) and the bitter lesson. Layer 1: discover ALL stock variants from the game data as a reservoir (36-71 per hull size after filtering fighters, stations, and special entities via ship_data.csv hints/tags). Layer 2: each build evaluates only the top `active_opponents` (default 10) from the B1 discriminative power ordering. Initial ordering is a random shuffle for exploration; B1 recomputes periodically to optimize within the active set. No hand-designed curriculum, difficulty labels, or thresholds.

*Expected impact: Wider fitness gradient from difficulty diversity. 10 opponents per build maintains throughput (vs 71 exhaustive). Budget efficiency from pruning over 10 steps.*

**Instance parallelism:** Async coordinator-worker pattern (ThreadPoolExecutor + wait(FIRST_COMPLETED)) dispatches 1 matchup per instance, processes results as they arrive (promote-on-arrival, async ASHA). N instances run in parallel; pruning decisions are immediate after every opponent result.

### Phase D: If Java Modification Is Feasible

**D1. Common Random Numbers (CRN)**
Add `random_seed` field to `MatchupConfig`. Seed Java's RNG in `CombatHarnessPlugin` before combat. Use identical seeds when comparing builds against the same opponent. Paired comparisons have 80–94% lower variance.

*Feasibility uncertain: Starsector's combat AI may consume RNG in build-dependent patterns (different weapons → different number of random calls), causing stream drift that degrades the correlation. Requires experimentation.*

---

## 4. Expected Combined Impact

| Metric | Current (Phase 4) | After Phase A | After Phase B | After Phase C |
|--------|-------------------|---------------|---------------|---------------|
| Evals per build | 5 (fixed) | 5 (cleaner) | ~2.5 avg | ~2.5 avg |
| Signal quality (CoV) | 0.41 | ~0.25 | ~0.15 | ~0.10 |
| Budget efficiency | 1× | 1× | 2–3× | 2–3× |
| Win rate | 0.4% | 0.4% | 0.4% | 20–40% (curriculum) |
| Per-hull tuning | None | None | None | None |
| Objectives reported | 1 | 1 | 1 | 3 |

Phases A+B together provide ~2–3× more optimization progress per wall-clock hour while making the signal cleaner. Phase C further improves exploration quality and makes the optimization landscape more navigable.

---

## 5. Interaction with Other Phases

**Phase 4 (Optimizer)**: Signal quality improvements sit directly in the evaluation/scoring layer. The ask-tell loop, repair, deduplication, and warm-start are unchanged. OptimizerConfig gains new fields for the scoring enhancements.

**Phase 6 (Quality-Diversity)**: Better signal quality directly benefits MAP-Elites archive construction. Cleaner fitness → more accurate elites → better coverage. Multi-fidelity evaluation can be shared.

**Phase 7 (Neural Surrogate)**: The richer per-opponent telemetry from multi-objective decomposition provides better features for surrogate training. Control variate correction provides a principled way to combine surrogate and simulation predictions.

---

## 6. Key References

### Variance Reduction
- Nelson, B.L. (2010). "Variance Reduction." Handbooks in OR/MS.
- Law, A.M. (2015). "Simulation Modeling and Analysis." Ch. 11.
- Glasserman & Yao. "Guidelines and Guarantees for CRN."

### Multi-Fidelity / Racing
- Li, L. et al. (2018). "Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization." JMLR.
- Falkner, S. et al. (2018). "BOHB: Robust and Efficient Hyperparameter Optimization at Scale." ICML.
- Birattari, M. et al. (2002). "A Racing Algorithm for Configuring Metaheuristics." GECCO.
- Chen, C.H. et al. (2000). "Simulation Budget Allocation." Discrete Event Dynamic Systems.

### Multi-Objective / Fitness Shaping
- Wierstra, D. et al. (2014). "Natural Evolution Strategies." JMLR.
- Salimans, T. et al. (2017). "Evolution Strategies as a Scalable Alternative to RL." arXiv:1703.03864.
- Rockafellar, R.T. & Uryasev, S. (2000). "Optimization of CVaR." Journal of Risk.
- Jensen, M.T. (2005). "Helper-objectives: Using MOEAs for single-objective optimization."

### Game AI / Curriculum
- Narvekar, S. et al. (2020). "Curriculum Learning for RL." JMLR.
- Vinyals, O. et al. (2019). "Grandmaster Level in StarCraft II." Nature.
- Nguyen, T.T. et al. (2012). "Score-Based Bayesian Skill Learning." ECML.
- Balduzzi, D. et al. (2018). "Re-evaluating Evaluation." NeurIPS.

### Surrogate-Assisted
- Survey of surrogate-assisted EAs (2024). Springer.
- SMAC3 sampler on OptunaHub (RF-based surrogate).

### Design Philosophy
- Sutton, R.S. (2019). "The Bitter Lesson." — General methods that scale with computation beat hand-crafted approaches.
