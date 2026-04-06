# Optimization Theory for the Ship Build Problem

A first-principles analysis of why our problem is hard and what theory says about the best approaches.

## 1. Problem Formalization

Our problem: maximize f(x) where x is a ship build configuration.

**Decision variables:**
- Per weapon slot i: x_i in {empty, weapon_1, ..., weapon_k_i} (categorical, ~10-60 options per slot)
- Hullmod selection: h in {0,1}^m (binary, ~50 hullmods)
- Flux vents: v in {0, 1, ..., V_max} (integer, 10-50)
- Flux capacitors: c in {0, 1, ..., C_max} (integer, 10-50)
- Total: ~76 dimensions, search space size |X| ~ 10^40 to 10^80 depending on hull

**Constraint:** total OP cost of weapons + hullmods <= hull's ordnance points (budget/knapsack constraint)

**Objective f:** combat performance (win rate, damage efficiency, etc.) from expensive AI-vs-AI simulation.

**Key structural features:**
- f is noisy (stochastic combat outcomes)
- f is expensive (~30-60 seconds per evaluation)
- f is degenerate: >99% of random configurations produce useless builds (all weapons cost more than budget, nonsensical weapon mixes, etc.)
- Strong variable interactions (weapon synergies, hullmod-weapon interactions, flux economy coupling)

---

## 2. Combinatorial Optimization Under Budget Constraints

### 2.1. Our Problem as a Knapsack Variant

The ship build problem is a **multidimensional knapsack with interaction terms** (also called the Quadratic Knapsack Problem or QKP when interactions are pairwise). Each weapon and hullmod has an OP cost (weight) and contributes to fitness (value), but the value of a weapon depends on which other weapons and hullmods are present.

**Classical knapsack** (independent items): greedy by value/weight ratio gives a 2-approximation; FPTAS exists.

**Multidimensional knapsack** (multiple resource constraints): NP-hard. Frieze and Clarke gave a PTAS, but running time is exponential in the number of constraints.

**Quadratic knapsack** (pairwise interaction terms): dramatically harder. The best known polynomial-time approximation ratio is O(n^{2/5+epsilon}) [Taylor 2015, arXiv:1509.01866]. This is terrible -- it says that even for the *simplest* form of interactions (pairwise), finding good approximate solutions is provably hard in the worst case.

**Our problem has higher-order interactions** (not just pairwise): a weapon's value depends on the entire flux economy, which depends on ALL weapons and hullmods. This puts us firmly in the regime where no polynomial-time approximation guarantees exist unless P=NP.

**Actionable insight:** We cannot hope for theoretical guarantees. We must exploit *problem structure* that generic worst-case theory ignores.

### 2.2. Submodularity

A set function f is submodular if adding an element to a smaller set gives at least as much marginal gain as adding it to a larger set (diminishing returns). Submodular maximization subject to a matroid constraint has a clean (1 - 1/e)-approximation via greedy [Vondrak 2008].

**Is our fitness submodular?** Almost certainly NOT in general:
- Adding a second kinetic weapon might be MORE valuable than the first (the first weapon might not break shields alone, but two can)
- Safety Overrides + short-range weapons exhibit supermodularity (SO amplifies short-range builds)
- Flux economy creates threshold effects: adding a weapon that pushes you over the flux budget is catastrophically bad, violating diminishing returns

However, **parts of the problem exhibit approximate submodularity** within "build archetypes" -- once you commit to a playstyle (brawler, long-range, etc.), adding more weapons of the right type has diminishing returns. This suggests a **two-level approach**: enumerate archetypes, then do approximately-submodular optimization within each.

### 2.3. Partition Matroid Structure

Our slot constraints form a **partition matroid**: the ground set of weapons is partitioned by slot, and we pick at most one weapon per slot. Partition matroid constraints are the easiest matroid constraints. The key property: if we could decompose f as a sum of per-slot contributions, greedy would work perfectly. The difficulty is entirely in the *interactions between slots*.

**Actionable insight:** The matroid structure is not the bottleneck. The interaction structure is. We should focus effort on modeling interactions, not on handling slot constraints (which are trivially satisfied by any per-slot assignment).

---

## 3. Bandit Theory for Combinatorial Arms

### 3.1. Combinatorial Multi-Armed Bandits (CMAB)

In CMAB [Chen et al. 2016, arXiv:1610.06603], each round we select a "super-arm" (subset of base arms) and observe noisy rewards. Our problem maps naturally: each base arm is a weapon-in-slot or hullmod, and a super-arm is a full build.

**Regret bounds for CMAB (semi-bandit feedback):** O(m * log(T) / Delta_min) where m is the number of base arms and Delta_min is the gap between the best and second-best super-arm [Wang & Chen 2018, arXiv:1803.04623].

**Critical assumption violated:** CMAB theory assumes the reward function decomposes (at least approximately) as a sum of base arm rewards, or satisfies a "smoothness" condition like Lipschitz continuity in the arm means. Our reward function has strong nonlinearities (threshold effects, synergies) that violate these assumptions.

### 3.2. Thompson Sampling for Combinatorial Spaces

Thompson Sampling (TS) for CMAB [Wang & Chen 2018] maintains posterior distributions over base arm parameters, samples from them, and selects the super-arm maximizing the sampled objective. The key result: TS achieves O(m * log(K_max) * log(T) / Delta_min) regret with independent arms.

For **matroid bandits** specifically, the independence assumption across arms can be removed and the regret bound matches the lower bound.

**Budgeted CMAB** [Das et al. 2022, arXiv:2202.03704] extends this to settings where super-arms have costs and there's a budget constraint per round -- directly relevant to our OP constraint.

### 3.3. Why Pure Bandit Approaches Won't Work

**The fundamental problem:** with |X| ~ 10^40+ arms, even O(log |X|) exploration is infeasible. Bandit regret bounds scale with m (number of base arms, ~200-500 for us) but assume we can efficiently find the best super-arm given estimated arm values. With nonlinear reward functions, this inner optimization is itself NP-hard.

**Regret lower bounds:** For combinatorial bandits with m base arms and T rounds, the regret is at least Omega(sqrt(m * T)). With T ~ 10,000 evaluations and m ~ 300, this gives regret ~ 1,700 -- meaning we'd spend most of our budget on exploration rather than exploitation.

**Actionable insight:** Pure bandit theory gives us useful *principles* (Thompson sampling, UCB-style exploration) but we need to combine them with surrogate models that generalize across configurations, not treat each build as an independent arm.

---

## 4. Statistical Estimation with Sparse Signal

### 4.1. The Degenerate Region Problem

When >99% of randomly sampled builds are "degenerate" (infeasible, no weapons equipped, flux-starved), most evaluations produce near-identical scores. This is a form of **extreme class imbalance** in the regression setting.

**Imbalanced regression** [Yang et al. 2021, arXiv:2102.09554] studies this: standard models minimize MSE, which is dominated by the majority class (degenerate builds). Solutions include:
- Label Distribution Smoothing (LDS): re-weight training examples by inverse density of their label
- Feature Distribution Smoothing (FDS): calibrate model predictions in low-density label regions
- SMOTE-style oversampling of rare good builds

### 4.2. Rare Event Estimation

Our problem resembles **rare event simulation** -- we want to find configurations in a tiny "good" region of the space. Key theory:

**Importance sampling** [Dubourg et al. 2011, arXiv:1104.3476]: instead of sampling uniformly, sample from a distribution concentrated on the good region. The challenge is constructing this importance distribution without already knowing where the good region is.

**Adaptive importance sampling / cross-entropy method:** iteratively refine the sampling distribution:
1. Sample from current distribution
2. Keep the top-k% performers
3. Fit a new distribution to the survivors
4. Repeat

This is essentially the cross-entropy method / CMA-ES / estimation of distribution algorithm pattern. It's well-suited to our problem because it naturally handles the sparse signal issue.

### 4.3. Minimum Sample Complexity

**How many evaluations do we need to find a good build?**

If the good region has measure p of the total space, random search needs O(1/p) samples to find one good point. For our problem, if 1% of configurations are "reasonable" and 0.01% are "good", we need ~10,000 random samples just to find ONE good build.

But with structure exploitation:
- If we can decompose the problem into independent sub-problems of size d_i, we need O(sum(d_i)) instead of O(product(d_i))
- If we can identify a low-dimensional effective subspace (say, d_eff << 76), we need O(|X_eff|) where |X_eff| = product of options in the d_eff important dimensions

**Actionable insight:** The constraint repair mechanism (Phase 1) is essential -- it maps the degenerate region to the feasible boundary. Instead of wasting evaluations on clearly infeasible builds, repair projects every proposal into feasible space, dramatically increasing the fraction of informative evaluations.

---

## 5. Surrogate Modeling for Discrete Spaces

### 5.1. Random Forests vs GPs for Discrete Inputs

**Random forests** (as used in SMAC [Hutter et al. 2011]):
- Handle categorical variables natively (no encoding needed)
- Scale well to high dimensions
- Provide uncertainty estimates via variance across trees
- Weakness: piecewise-constant predictions, poor extrapolation

**Gaussian processes with combinatorial kernels:**
- Diffusion kernels on the combinatorial graph [COMBO, Oh et al. 2019, arXiv:1902.00448]: define a GP over the Cartesian product graph of all variables. The graph Fourier transform decomposes into per-variable Fourier transforms, making computation tractable. ARD + Horseshoe prior enables automatic variable selection.
- Heat kernels [Doumont et al. 2025, arXiv:2510.26633]: a unifying framework showing many combinatorial kernels are equivalent to heat kernels. Not sensitive to the location of optima (unlike some alternatives).
- Weakness: GPs scale O(n^3) in dataset size; need sparse approximations for >1000 observations.

**Continuous surrogates for discrete problems** [Karlsson et al. 2020, arXiv:2011.03431]: surprisingly, treating categorical variables as continuous (with rounding) works competitively. This validates our potential use of standard BO tools with appropriate encoding.

### 5.2. Handling Imbalanced Training Data

When most training points have label ~0 (degenerate builds), the surrogate will learn to predict 0 everywhere and have poor resolution among good builds. Solutions:

1. **Two-stage modeling:** First classifier (feasible vs degenerate), then regressor on non-degenerate builds only. The classifier handles the easy part; the regressor focuses on the interesting part.
2. **Log-transform the objective:** If scores span orders of magnitude, log-scaling compresses the range.
3. **Weighted loss:** Up-weight informative (non-degenerate) training points.
4. **Constrained BO:** Model the feasibility constraint explicitly as a separate GP/RF, then optimize EI * P(feasible). [This is exactly what we should do: model P(combat_score > 0) and E[score | score > 0] separately.]

### 5.3. Multi-Fidelity: Cheap Heuristic + Expensive Simulation

Our heuristic scorer (Phase 1) is a cheap oracle; combat simulation (Phase 2) is an expensive oracle. This is a textbook **multi-fidelity optimization** setup [Do & Zhang 2023, arXiv:2311.13050].

**Key multi-fidelity strategies:**
- **Auto-regressive GP:** f_expensive(x) = rho * f_cheap(x) + delta(x), where delta is an independent GP. Learns the correlation and residual.
- **Information-theoretic acquisition:** evaluate the cheap oracle more often and the expensive oracle selectively, allocating the simulation budget to builds where the cheap and expensive oracles are most likely to disagree.
- **Pre-screening:** use the heuristic to filter out clearly bad builds before spending simulation budget. If the heuristic identifies the top-1% of builds with 80% recall, we gain a 100x speedup in simulation budget efficiency.

**Actionable insight:** The heuristic scorer is not just a fallback -- it's a first-class fidelity level. The optimization loop should evaluate MOST builds with the heuristic only, and allocate simulation budget to the most uncertain/promising candidates.

---

## 6. Fitness Landscape Structure

### 6.1. NK Landscape Model

NK landscapes [Kauffman & Weinberger 1989] parameterize the ruggedness of combinatorial fitness landscapes:
- N = number of loci (dimensions), K = number of epistatic interactions per locus
- K=0: smooth, single-peaked landscape (trivially optimizable)
- K=N-1: random landscape (maximally rugged, no better than random search)
- Intermediate K: tunably rugged with multiple local optima

**Our problem's K value:** Each weapon slot's contribution depends on:
- The flux economy (all other weapons + vents/caps): K ~ 10-30
- Damage mix (other weapons of similar range): K ~ 5-15
- Hullmod effects: K ~ 3-8

This suggests **moderate epistasis (K ~ 10-30)**, placing us in the "difficult but structured" regime. Key prediction: the landscape has many local optima, but they cluster into basins corresponding to build archetypes.

**Computational complexity:** NK landscapes with K >= 2 are NP-hard to optimize and PLS-complete to find local optima. However, for moderate K, local optima networks have structure that can be exploited.

### 6.2. Neutrality

A landscape is "neutral" when many neighboring configurations have the same fitness. Our problem has EXTREME neutrality in the degenerate region (all builds that are obviously terrible score ~0) but NOT in the good region (small changes to a good build can significantly change combat performance).

**Implication:** Standard fitness landscape analysis (autocorrelation length, etc.) will be dominated by the flat degenerate region and will dramatically overestimate the landscape's smoothness. We must analyze the landscape CONDITIONAL on being in the good region.

### 6.3. Fitness Landscape Analysis Metrics

For our problem, useful metrics include:
- **Autocorrelation length** (in the good region): how correlated are the scores of builds that differ by one weapon swap? If high, local search works well.
- **Fitness-distance correlation (FDC):** do better builds cluster in configuration space? If yes, evolutionary approaches that maintain diversity will find them.
- **Number of local optima:** estimated by running many local searches from random starts. Determines whether we need global or local optimization.

**Actionable insight:** Before choosing an optimization algorithm, we should empirically estimate these landscape metrics using cheap heuristic evaluations. A few thousand heuristic evaluations can tell us whether the landscape (in the good region) is smooth enough for local search or requires global methods.

---

## 7. Decomposition Theory

### 7.1. ANOVA Decomposition

Any function f on a discrete product space X_1 x ... x X_d can be decomposed as:

f(x) = f_0 + sum_i f_i(x_i) + sum_{i<j} f_ij(x_i, x_j) + ... + f_{1..d}(x_1,...,x_d)

where each term captures the marginal/interaction effect at that order. The **total variance** decomposes correspondingly (Sobol indices).

**Hutter et al. 2014** (Functional ANOVA) showed that for hyperparameter optimization problems:
- Most performance variation is due to 1-3 hyperparameters
- Interactions beyond order 2 are typically negligible
- Random forests provide efficient marginal computation in O(n * d * #trees) time

### 7.2. Effective Dimensionality

**REMBO** [Wang et al. 2013, arXiv:1301.1942] exploits the observation that many high-dimensional problems have low **effective dimensionality** d_e << d: the objective only varies along a d_e-dimensional subspace. REMBO embeds a d_e-dimensional BO into the full space via random projection.

**For ship builds**, the effective dimensionality is likely:
- **Hull-dependent:** A frigate with 3 weapon slots has d_eff ~ 8-10 (3 weapons + hullmods + vents + caps). A capital with 20+ slots might have d_eff ~ 25-30.
- **Archetype-dependent:** Within a "brawler" archetype, the important dimensions are short-range weapon selection + SO + flux allocation. Long-range weapon slots become irrelevant (they should all be empty or PD).
- **Estimated d_eff for a typical cruiser: 15-25 out of 76 total dimensions.**

### 7.3. Detecting Low-Order Structure

We can empirically test for low-order structure:
1. Run functional ANOVA using the heuristic scorer on a random sample of 10,000+ builds
2. Compute first-order Sobol indices: which variables explain the most variance?
3. Compute second-order indices: which pairs have the strongest interactions?
4. If first + second order indices sum to >90% of variance, the problem is effectively low-order

**Actionable insight:** If functional ANOVA confirms low-order structure, we can:
- Focus optimization on the top-k most important variables
- Fix less important variables to reasonable defaults
- Use pairwise interaction models (quadratic surrogates) rather than full combinatorial models

---

## 8. Information-Theoretic Bounds

### 8.1. Fundamental Limits

Given B evaluations in a space of size |X|, what's the best we can find?

**Random search baseline:** With B evaluations, the best found value has expected rank |X| / (B+1) in the sorted population. For |X| = 10^40 and B = 10^4, this gives rank 10^36 -- essentially useless.

**With structure:** If the problem has effective dimensionality d_eff and each effective dimension has k options, then |X_eff| = k^{d_eff}. For d_eff = 20, k = 20: |X_eff| ~ 10^26. Still huge, but:
- If the landscape is smooth in the effective subspace, BO-style methods can find near-optimal points in O(d_eff^2) to O(d_eff^3) evaluations ~ 400-8000.
- If the landscape has L local optima with basin sizes ~ |X_eff|/L, we need O(L * d_eff) evaluations to find the global basin.

### 8.2. No Free Lunch and Its Irrelevance

The NFL theorem [Igel & Toussaint 2003, arXiv:cs/0303032] states that averaged over ALL possible objective functions, every algorithm performs equally. But this is vacuous for structured problems:
- NFL requires the function class to be closed under permutation -- meaning every function is equally likely
- Real optimization problems have MASSIVE structure (smoothness, decomposability, physics-based constraints)
- The sharpened NFL theorem shows that for any function class NOT closed under permutation, there exist algorithms that outperform random search

**NFL's useful message:** No algorithm is best for everything. We should choose algorithms matched to our problem's specific structure (moderate epistasis, budget constraints, multi-fidelity, archetype clustering).

### 8.3. When Structure Helps

Structure helps when it reduces the effective search space:
- **Budget constraint:** eliminates ~90% of configurations (most random assignments exceed OP budget)
- **Slot compatibility:** reduces per-slot options from "all weapons" to "compatible weapons" (already handled in search space builder)
- **Decomposability:** if d_eff = 20 instead of 76, the search space shrinks by factor ~10^30
- **Smoothness:** if the objective is Lipschitz in the good region, we can interpolate between evaluated points

**Total potential reduction:** from 10^40 (raw) to perhaps 10^8 (effective structured space) -- making optimization tractable with ~10^4 evaluations.

---

## 9. Synthesis: What Theory Tells Us To Do

### 9.1. The Hard Parts (ranked by difficulty)

1. **Sparse signal:** Most evaluations are uninformative. The degenerate region is vast.
   - Mitigation: constraint repair, heuristic pre-screening, feasibility modeling

2. **Variable interactions:** Weapon synergies and flux economy create high-order dependencies.
   - Mitigation: functional ANOVA to identify key interactions, archetype decomposition

3. **Expensive evaluations:** Each simulation costs 30-60 seconds.
   - Mitigation: multi-fidelity (heuristic + simulation), surrogate models, batch evaluation

4. **High dimensionality:** 76 variables, even with reduced effective dimensionality.
   - Mitigation: trust regions (TuRBO-style), random embeddings (REMBO), variable importance filtering

### 9.2. Recommended Algorithmic Framework

Based on the theory, the optimal approach is a **multi-phase, multi-fidelity pipeline:**

**Phase A: Landscape Characterization (cheap, ~10,000 heuristic evals)**
- Random sample builds, repair to feasibility, score with heuristic
- Functional ANOVA to identify important variables and interactions
- Estimate effective dimensionality, autocorrelation length, number of local optima
- Identify build archetypes via clustering

**Phase B: Heuristic-Guided Pre-screening (cheap, ~100,000 heuristic evals)**
- Per-archetype local search in heuristic space (iterated local search / evolutionary strategy)
- Maintain diverse population spanning multiple archetypes
- Result: a shortlist of ~100-500 promising builds

**Phase C: Multi-Fidelity Bayesian Optimization (expensive, ~1,000-5,000 simulation evals)**
- Train surrogate (random forest or GP with combinatorial kernel) on heuristic scores
- Use multi-fidelity acquisition: allocate simulation budget to builds where heuristic uncertainty is highest
- Trust region approach: maintain local models around the best-known builds, expand/contract based on success
- Thompson sampling for batch selection (run N simulations in parallel)

**Phase D: Local Refinement (expensive, ~500-1,000 simulation evals)**
- Around the best builds found in Phase C, do single-variable perturbation sweeps
- Estimate sensitivity of combat performance to each variable
- Final polishing via local search in simulation space

### 9.3. Expected Performance

With B = 5,000 simulation evaluations and the pipeline above:
- Phase A costs nothing (heuristic only)
- Phase B costs nothing (heuristic only)
- Phase C gets 4,000 evaluations focused on the promising 0.01% of the space
- Phase D gets 1,000 evaluations for fine-tuning

Compared to naive random search (which would need ~10^36 evaluations), this represents a potential speedup of ~10^32 by exploiting structure. In practice, we should expect to find builds in the top-1% of all possible builds within our evaluation budget, assuming the heuristic provides useful signal about the simulation outcome.

---

## 10. Key Papers and References

### Combinatorial Bayesian Optimization
- **BOCS:** Baptista & Poloczek, "Bayesian Optimization of Combinatorial Structures" (ICML 2018) [arXiv:1806.08838]
- **COMBO:** Oh et al., "Combinatorial Bayesian Optimization using the Graph Cartesian Product" (NeurIPS 2019) [arXiv:1902.00448]
- **Bounce:** Papenmeier et al., "Reliable High-Dimensional Bayesian Optimization for Combinatorial and Mixed Spaces" (NeurIPS 2023) [arXiv:2307.00618]
- **MCBO Benchmark:** Dreczkowski et al., "Framework and Benchmarks for Combinatorial and Mixed-variable Bayesian Optimization" (2023) [arXiv:2306.09803]
- **Heat Kernels:** Doumont et al., "Omnipresent Yet Overlooked: Heat Kernels in Combinatorial Bayesian Optimization" (2025) [arXiv:2510.26633]
- **PSR-BOCS:** Deshwal et al., "Scalable Combinatorial Bayesian Optimization with Tractable Statistical Models" (2020) [arXiv:2008.08177]
- **Continuous for Discrete:** Karlsson et al., "Continuous surrogate-based optimization algorithms are well-suited for expensive discrete problems" (2020) [arXiv:2011.03431]

### Combinatorial Bandits
- **CMAB General Rewards:** Chen et al., "Combinatorial Multi-Armed Bandit with General Reward Functions" (NIPS 2016) [arXiv:1610.06603]
- **TS for CMAB:** Wang & Chen, "Thompson Sampling for Combinatorial Semi-Bandits" (2018) [arXiv:1803.04623]
- **Budgeted CMAB:** Das et al., "Budgeted Combinatorial Multi-Armed Bandits" (AAMAS 2022) [arXiv:2202.03704]

### High-Dimensional BO
- **TuRBO:** Eriksson et al., "Scalable Global Optimization via Local Bayesian Optimization" (NeurIPS 2019) [arXiv:1910.01739]
- **REMBO:** Wang et al., "Bayesian Optimization in a Billion Dimensions via Random Embeddings" (JAIR 2016) [arXiv:1301.1942]

### Multi-Fidelity
- **MF-BO Survey:** Do & Zhang, "Multi-fidelity Bayesian Optimization: A Review" (AIAA Journal 2025) [arXiv:2311.13050]

### Surrogate Modeling
- **SMAC:** Hutter et al., "Sequential Model-Based Optimization for General Algorithm Configuration" (LION 2011) [cs.ubc.ca/~hutter/papers/10-TR-SMAC.pdf]
- **Functional ANOVA:** Hutter et al., "An Efficient Approach for Assessing Hyperparameter Importance" (ICML 2014) [proceedings.mlr.press/v32/hutter14.html]
- **Imbalanced Regression:** Yang et al., "Delving into Deep Imbalanced Regression" (ICML 2021) [arXiv:2102.09554]

### Fitness Landscapes
- **NK Model:** Kauffman & Weinberger, "The NK model of rugged fitness landscapes" (J. Theoretical Biology, 1989)
- **NK Analysis:** Buzas & Dinitz, "An analysis of NK and generalized NK landscapes" (2013) [arXiv:1302.3541]
- **Local Optima Networks:** Ochoa et al., "A Study of NK Landscapes' Basins and Local Optima Networks" (2008) [arXiv:0810.3484]
- **Comprehensive FLA Survey:** Pitzer & Affenzeller, "A Comprehensive Survey on Fitness Landscape Analysis" [Springer, 2012]

### Constraint Handling
- **Submodular + Matroid:** Vondrak, "Maximizing a Submodular Set Function subject to a Matroid Constraint" (2008) [theory.stanford.edu]
- **Submodular + Knapsack:** Iyer & Bilmes, "Submodular Optimization with Submodular Cover and Submodular Knapsack Constraints" (NIPS 2013) [arXiv:1311.2106]
- **Partition Matroid:** Do & Neumann, "Pareto Optimization for Subset Selection with Dynamic Partition Matroid Constraints" (2020) [arXiv:2012.08738]
- **Quadratic Knapsack:** Taylor, "Approximation of the Quadratic Knapsack Problem" (2015) [arXiv:1509.01866]
- **Feasibility-Driven BO:** "Feasibility-Driven Trust Region Bayesian Optimization" (2025) [arXiv:2506.14619]

### No Free Lunch
- **Sharpened NFL:** Igel & Toussaint, "Recent Results on No-Free-Lunch Theorems for Optimization" (2003) [arXiv:cs/0303032]

### Rare Events
- **Metamodel IS:** Dubourg et al., "Metamodel-based importance sampling for the simulation of rare events" (2011) [arXiv:1104.3476]
- **Deep IS:** Arief et al., "Certifiable Deep Importance Sampling for Rare-Event Simulation of Black-Box Systems" (2021) [arXiv:2111.02204]
