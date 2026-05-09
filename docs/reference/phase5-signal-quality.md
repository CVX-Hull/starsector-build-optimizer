---
type: reference
status: shipped
last-validated: unvalidated
---

# Phase 5: Signal Quality — Research Findings and Recommended Approach

Improving the signal-to-noise ratio of combat fitness evaluations. This document is self-contained — it explains the problem, summarizes cross-domain research, and recommends a phased implementation plan.

> **Empirical-claims status (2026-05-10):** All Phase 5 internal-sim measurements in this document have been stripped pending re-validation under the V2 combat-harness loadout fix. Design rationale, literature citations, and architecture decisions are unaffected. See [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md) for the re-validation tracker.

---

## 1. Background

### How the Optimizer Works (Phase 4)

The optimizer discovers effective ship builds for Starsector via Bayesian optimization:

1. **Propose** a build (weapon loadout, hullmods, flux allocation) via Optuna TPE (CatCMAwM removed 2026-04-19 — see `docs/specs/24-optimizer.md` §`_create_sampler`)
2. **Repair** to feasibility (OP budget, hullmod compatibility)
3. **Evaluate** by running AI-vs-AI combat simulation against 5 diverse opponents
4. **Score** using a hierarchical composite fitness: wins [1.0, 1.5], timeouts [-0.49, +0.49], losses [-1.0, -0.5]
5. **Aggregate** per-opponent scores into a single mean fitness
6. **Report** to Optuna, which uses the score to guide future proposals

Each evaluation requires ~5 matchups × ~10 seconds each = ~50 seconds of wall-clock time across 4–8 parallel game instances. The search space is ~70 dimensions (13 weapon slots × options each, 62 binary hullmod flags, 2 integer flux variables).

### The Problem: Signal Quality

The qualitative observation motivating Phase 5 is that the optimizer finds real signal but extracts it inefficiently. Win rates are extremely low (most builds lose every matchup), so the bulk of the fitness gradient lives in hull-fraction margin differences within the TIMEOUT tier — small differences in kill-progress that are easily overwhelmed by combat stochasticity.

**The optimizer navigates "shades of losing."** Almost all builds lose every matchup. The fitness signal comes entirely from hull-fraction margin differences within the TIMEOUT tier.

### Per-Opponent Analysis

The 5 opponents (after removing the noise-only heron_Attack in Phase 4) test orthogonal capabilities but contribute very differently to signal quality. Qualitative findings that informed Phase 5C's anchor-first + incumbent-overlap selection:

- Some opponents have **negative correlation with overall fitness** — builds that do well against them tend to do worse overall. These actively hurt ranking quality.
- Some opponents have **high within-outcome variance** (bimodal: either the build survives or gets destroyed by, e.g., a mine).
- **Inter-opponent correlations are near-zero**. Each opponent tests genuinely orthogonal capabilities, but this means averaging them adds independent noise.
- **Leave-one-out** rank-correlation analysis shows that dropping the negatively-correlated opponent improves rank quality, while dropping the highest-variance opponent hurts most.

Specific magnitudes (win rates, Cohen's d, ρ values, leave-one-out deltas) are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

---

## 2. Research Summary

We surveyed methods from four domains: noisy optimization, game AI evaluation, variance reduction statistics, and multi-objective/surrogate-assisted optimization. Below are the methods most applicable to our problem, filtered through the **Bitter Lesson** constraint: methods must be general, scale with compute, and not require per-hull hand-tuning.

### 2.1 Variance Reduction (Statistics / Simulation Literature)

**Opponent Normalization (z-score per opponent)**
For each opponent, maintain running mean μ and standard deviation σ of fitness scores. Normalize each matchup result: `z = (score - μ) / σ`. Aggregate z-scores instead of raw scores. This removes between-opponent difficulty bias — aurora_Assault (mean=-0.79) and eagle_Assault (mean=-0.22) become comparable.
- *Ref: Nelson (2010), "Variance Reduction" in Handbooks in OR/MS*
- *Effort: Trivial. Extra evals: None.*

**Control Variates (heuristic scorer as proxy)**
Given expensive simulation fitness Y and cheap heuristic score Z with known mean E[Z]: `Y_adj = Y - c * (Z - E[Z])`, where `c = Cov(Y,Z)/Var(Z)`. Variance reduction is `(1 - ρ²) × Var(Y)`. We already compute heuristic scores for warm-start; using them as a control variate costs nothing.
- *Ref: Law (2015), "Simulation Modeling and Analysis", Ch. 11*
- *Effort: Low. Extra evals: None.*

**Common Random Numbers (CRN)**
When comparing builds A and B against the same opponent, use identical random seeds. The difference `fitness(A) - fitness(B)` has much lower variance than individual fitnesses because shared randomness cancels. Published variance-reduction figures from Glasserman & Yao apply to well-behaved simulations; feasibility for Starsector is uncertain because combat AI consumes RNG in build-dependent patterns that may cause stream drift.
- *Ref: Glasserman & Yao, "Guidelines for Common Random Numbers"*
- *Effort: Medium-high (requires Java-side seed control). Feasibility uncertain.*

### 2.2 Evaluation Budget Efficiency (Noisy Optimization Literature)

**Multi-Fidelity / Successive Halving (Hyperband)**
Evaluate builds against opponents sequentially. After each opponent, report intermediate fitness. Optuna's `HyperbandPruner` kills unpromising builds early; the "fidelity" dimension is number of opponents evaluated.
- *Ref: Li et al. (2018), "Hyperband" — JMLR*
- *Effort: Medium (pipeline restructuring). Extra evals: Negative (saves budget).*

**Racing / Adaptive Resampling (F-Race / OCBA)**
Evaluate sequentially, statistically eliminate inferior builds early. F-Race uses Friedman's test; OCBA maximizes probability of correct selection by allocating more replays to uncertain builds. Conceptually overlaps with Hyperband but frames the problem as hypothesis testing.
- *Ref: Birattari et al. (2002), "A Racing Algorithm for Configuring Metaheuristics" — GECCO; Chen et al. (2000), OCBA — Discrete Event Dynamic Systems*
- *Impact: Medium-high. Effort: Medium.*

### 2.3 Richer Objectives (Multi-Objective / Reward Shaping Literature)

**Multi-Objective Decomposition**
Instead of collapsing per-opponent telemetry into one composite score, report 3–4 objectives to Optuna: (a) kill-progress margin, (b) survival (hull fraction preserved), (c) damage output, (d) flux management quality. Use Optuna's built-in `NSGAIISampler` or MOTPE. The Pareto front naturally explores different strategies (tanky, DPS, kiting) without requiring hand-crafted weights.
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
Low base win rates make the problem too hard for direct gradient signal. Start optimization against weaker opponents (stock builds of the same hull size). As the rolling win rate exceeds a threshold, introduce harder opponents. The optimizer always faces a tractable challenge with abundant gradient signal.
- *Ref: Narvekar et al. (2020), "Curriculum Learning for RL" — JMLR*
- *Effort: Medium. Extra evals: None (reuses same budget).*

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

Proposed: [Phase 5F: regime mask → search_space catalogue]
          build → opponent₁ → normalize → report intermediate
                → opponent₂ → normalize → report intermediate → prune?
                → opponent₃ → normalize → report intermediate → prune?
                → opponent₄ → normalize → report intermediate
                → opponent₅ → normalize → report final
```

Phase 5F operates *upstream* of this pipeline, at `search_space.py::build_search_space` construction time: the hullmod + weapon catalogues handed to `repair_build` and Optuna's distributions are already regime-masked. Phase 5A–5E (TWFE / EB / Box-Cox) run unchanged on the already-regime-scoped per-study data. Opponent selection is explicitly NOT regime-aware — the opponent pool draws from the full hull-size-matched set so every build faces the full adversary distribution (open-world framing).

This single architectural change enables opponent normalization, Hyperband pruning, opponent ordering, and curriculum learning — all within Optuna's existing infrastructure.

**Throughput prerequisite:** Sequential evaluation requires persistent game sessions and mixed-build ASHA scheduling (Throughput Phases T1-T3) to be efficient. Without them, single-matchup game launches have 78% startup overhead. See `docs/reference/throughput-optimization.md` for the full throughput research.

### Phase A: Quick Signal Improvements (no pipeline change)

Modify scoring and aggregation only. No changes to evaluation flow.

**A1. Opponent Normalization**
Maintain running mean/std per opponent. Z-score each matchup result before averaging. Removes between-opponent difficulty bias automatically. Adapts as opponent statistics shift.

**A2′. Empirical-Bayes Shrinkage** *(replaced scalar control variate in Phase 5D, 2026-04-18)*
Fuses TWFE α̂ with a regression prior on 7 pre-matchup covariates (3 engine-computed `MutableShipStats` reads + 3 Python-raw offense/range aggregates + `composite_score`) via a closed-form two-level Gaussian model. Posterior mean `α̂_EB_i = w_i · α̂_i + (1−w_i) · γ̂ᵀ[1, X_i]` with per-build precision weights `w_i = τ̂²/(τ̂² + σ̂_i²)`, followed by Lin-Louis-Shen triple-goal rank correction to restore the raw histogram. See spec 28 §EB Shrinkage and `phase5d-covariate-adjustment.md`. The original Phase 5A scalar control variate `fitness_adj = fitness_sim - c * (heuristic_score - E[heuristic])` shipped but was superseded by 5D — fusion paradigm beats conditioning on noisy proxies of the estimand (Cinelli-Forney-Pearl 2022 "Case 8").

**A3. Box-Cox Output Warping** *(replaced quantile rank in Phase 5E, 2026-04-18)*
Report Box-Cox-shaped value to Optuna instead of quantile rank. `scipy.stats.boxcox` fits `λ̂` by MLE on the positivised `_completed_fitness_values` population every `_finalize_build` call, then min-max rescales to `[0, 1]`. Monotone (preserves Spearman ρ) while restoring α̂-proportional gradient at the tails — quantile rank was also monotone but discarded magnitude information, compressing the top quartile into an evenly-spaced grid that TPE's `l(x)` could not exploit. Below `ShapeConfig.min_samples=8` (by analogy to `eb_min_builds`) A3 falls through to min-max scaling. See [../specs/24-optimizer.md](../specs/24-optimizer.md) §A3 and [phase5e-shape-revision.md](phase5e-shape-revision.md).

*Empirical magnitude (ceiling-saturation reduction, top-k overlap lift): pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).*

### Phase B: Sequential Evaluation Pipeline (main change)

Restructure evaluation to process opponents one at a time with intermediate reporting.

**B1. Opponent Ordering**
Sort opponents by historical discriminative power: `Var_across_builds(z_score_i)` for opponent i. The opponent that best separates good from bad builds goes first. Ordering learned from data, updates automatically across runs.

**B2. Hyperband Pruning**
Use Optuna's `HyperbandPruner`. After each opponent, report the running normalized mean as an intermediate value. Optuna eliminates builds whose intermediate performance is in the bottom percentile. Bad builds terminated after 1–2 opponents.

**B3. Batch Evaluation Adaptation**
With sequential opponent evaluation, a single "batch" now evaluates one opponent across multiple builds simultaneously (utilizing all parallel instances), rather than all opponents for fewer builds. This maintains instance utilization while enabling per-opponent pruning.

*Expected design effect: pruning reduces average evaluation cost per build by terminating bad builds after 1–2 opponents. Empirical magnitude pending re-validation; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).*

### Phase C: Adaptive Opponent Pool (implemented)

**C1. Multi-Objective Decomposition** (deferred)
Deferred — requires fundamental changes to the A1→A2→A3 pipeline (scalar fitness assumptions), conflicts with MedianPruner (compares scalars), and the 3 proposed objectives are hand-designed decompositions at odds with the bitter lesson.

**C2. Adaptive Opponent Pool** (implemented)
Two-layer design inspired by racing algorithms (irace) and the bitter lesson. Layer 1: discover ALL stock variants from the game data as a reservoir (36-71 per hull size after filtering fighters, stations, and special entities via ship_data.csv hints/tags). Layer 2: each build evaluates only the top `active_opponents` (default 10) from the B1 discriminative power ordering. Initial ordering is a random shuffle for exploration; B1 recomputes periodically to optimize within the active set. No hand-designed curriculum, difficulty labels, or thresholds.

*Expected design effect: wider fitness gradient from difficulty diversity. Bounded `active_opponents` keeps throughput tractable vs an exhaustive pool. Empirical impact pending re-validation; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).*

**Instance parallelism:** Async coordinator-worker pattern (ThreadPoolExecutor + wait(FIRST_COMPLETED)) dispatches 1 matchup per instance, processes results as they arrive (promote-on-arrival, async ASHA). N instances run in parallel; pruning decisions are immediate after every opponent result.

### Phase D: If Java Modification Is Feasible

**D1. Common Random Numbers (CRN)**
Add `random_seed` field to `MatchupConfig`. Seed Java's RNG in `CombatHarnessPlugin` before combat. Use identical seeds when comparing builds against the same opponent. Published variance-reduction figures from Glasserman & Yao apply if stream stability holds; Starsector's combat AI may consume RNG in build-dependent patterns (different weapons → different number of random calls), causing stream drift that degrades the correlation. Requires experimentation.

---

## 4. Expected Combined Impact

The combined design effect of Phase A + B + C is (a) cleaner per-build fitness via opponent normalization, EB shrinkage, and Box-Cox warping; (b) reduced average evaluation cost via Hyperband pruning; (c) wider fitness gradient via the adaptive opponent pool. None of the methods require per-hull tuning.

Quantified magnitudes for the per-phase deltas (CoV reduction, budget-efficiency multiplier, win-rate gains under curriculum) are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md).

---

## 5. Interaction with Other Phases

**Phase 4 (Optimizer)**: Signal quality improvements sit directly in the evaluation/scoring layer. The ask-tell loop, repair, deduplication, and warm-start are unchanged. OptimizerConfig gains new fields for the scoring enhancements.

**Phase 6 (Structured Search-Space Representation)**: Cleaner fitness from TWFE + EB shrinkage is the `y` signal fed to the Phase 6 composite-kernel GP. The surrogate's sample-efficiency gains compose with signal-quality gains — a cleaner estimator + a better-structured surrogate both help the same trial budget go further.

**Phase 7 (Quality-Diversity)**: Better signal quality directly benefits MAP-Elites archive construction. Cleaner fitness → more accurate elites → better coverage. Multi-fidelity evaluation can be shared.

**Phase 8 (Neural Surrogate)**: The richer per-opponent telemetry from multi-objective decomposition provides better features for surrogate training. Control variate correction provides a principled way to combine surrogate and simulation predictions.

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
