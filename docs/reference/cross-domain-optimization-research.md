# Cross-Domain Optimization Research

How other fields solve the same structural problem we face: high-dimensional mixed discrete optimization with expensive black-box evaluation and sparse rewards.

**Our problem signature:** ~76D mixed space (13 categorical with 5-50 options, 61 binary, 2 integer), ~30s/eval simulation, most of the space produces bad/zero-signal outcomes, strong pairwise interactions, hard OP/slot constraints, tiny good region.

---

## 1. Drug Discovery / Molecular Optimization

### Problem Mapping

| Our Problem | Drug Discovery Equivalent |
|---|---|
| Weapon/hullmod selection | Molecular fragment/functional group selection |
| Combat fitness (simulation) | Binding affinity (docking simulation or wet-lab assay) |
| OP budget constraint | Drug-likeness constraints (Lipinski rules, ADMET) |
| Weapon synergies | Fragment interactions, pharmacophore geometry |
| ~30s per eval | Minutes to hours per docking, days for synthesis+assay |

### What Actually Works

**Multi-fidelity Bayesian optimization** is the dominant paradigm. A 2025 paper in ACS Central Science (Bayesian Optimization over Multiple Experimental Fidelities) demonstrates using cheap docking scores as a low-fidelity proxy to guide expensive wet-lab experiments. The cheap fidelity filters out obviously bad candidates, focusing expensive evaluations on promising regions.

**Generate-then-optimize pipelines.** A generative model (VAE, diffusion model, or molecular grammar) creates a large diverse candidate pool, then BO with a novel acquisition function (qPMHI) selects the best batch for expensive evaluation. This separates diversity generation from optimization.

**Latent-space optimization** outperforms direct discrete optimization. The NeurIPS 2024 benchmark paper (Gonzalez-Duque et al., 2406.04739) found that optimizers working on learned latent representations "substantially outperform discrete-space methods on complex problems." However, this requires a pre-trained generative model -- a significant upfront investment.

**Practical budgets:** 100-300 evaluations is standard in the BO literature for molecular optimization. Multi-fidelity approaches effectively multiply this by using 10-100x cheap evaluations per expensive one.

### Transferable Techniques

- **Multi-fidelity with heuristic scorer as cheap fidelity.** We already have a heuristic scorer. Using it as a cheap proxy to pre-filter candidates before simulation is directly analogous to using docking scores before wet-lab assays. This is our strongest cross-domain validation.
- **Preferential BO (CheapVS framework).** Instead of optimizing a single scalar, the optimizer learns from pairwise preferences about trade-offs between properties. Could inform multi-objective combat optimization.
- **The "for low budgets, simpler baselines tend to perform as well or better" finding** from the NeurIPS benchmark is a critical warning against over-engineering the optimizer.

### Key References

- [Bayesian Optimization over Multiple Experimental Fidelities (2025)](https://pubs.acs.org/doi/10.1021/acscentsci.4c01991)
- [Survey and Benchmark of High-Dimensional BO of Discrete Sequences (NeurIPS 2024)](https://arxiv.org/abs/2406.04739)
- [Preferential Multi-Objective BO for Drug Discovery (2025)](https://arxiv.org/abs/2503.16841)

---

## 2. Chip Design / EDA

### Problem Mapping

| Our Problem | EDA Equivalent |
|---|---|
| Weapons assigned to slots | Components placed on die |
| Flux/OP budget | Timing/power/area constraints |
| Slot type compatibility | Pin compatibility, routing DRC |
| Combat simulation (~30s) | SPICE simulation (minutes-hours), P&R (hours) |
| Hullmod synergies | Circuit block interactions |

### What Actually Works

**Surrogate models replacing expensive simulation.** The dominant pattern is: build a fast ML proxy for the expensive simulator, optimize against the proxy, then validate the top candidates with the real simulator. NVIDIA uses surrogate models extensively -- their INSTA engine (Best Paper DAC'25) is a differentiable surrogate for static timing analysis.

**Bayesian optimization with feasibility awareness (BaCO).** The BaCO framework (ASPLOS 2024) is the most directly relevant method. Key insight: BaCO handles known constraints during acquisition function optimization, proposing only feasible configurations. For hidden/unknown constraints, it multiplies Expected Improvement by a learned feasibility probability. Result: 1.36-1.56x faster code than baselines with tiny search budgets. **The improvement becomes greater when the feasible set makes up a small fraction of all possible configurations** -- exactly our sparse-feasibility situation.

**RL for chip placement turned out to be overhyped.** Google's AlphaChip/Nature 2021 paper claimed RL beat human experts at chip placement. Independent evaluation by UC San Diego found that commercial tools (simulated annealing-based) were actually superior. After 3+ years, no external replication succeeded. The controversy is instructive: **for placement/assignment problems with strong constraints, classical optimization (SA, mathematical programming) often beats RL despite RL's hype.** This validates our choice of BO over RL.

**Hybrid RL + BO for tuning.** NVIDIA's 2025 work uses RL to navigate the coarse search space and BO to fine-tune within promising regions. A two-phase approach.

### Transferable Techniques

- **Feasibility-aware acquisition functions (BaCO pattern).** Multiply EI by P(feasible). We could learn P(feasible) from repair outcomes -- builds that required heavy repair are low-feasibility.
- **Constraint-aware search space pruning.** BaCO's known-constraint handling during acquisition optimization is essentially what our `repair_build()` does. Validating our architecture.
- **Transfer learning between related problems.** CATBench (2024) shows transfer learning across compiler targets. Analogous to transferring optimization knowledge between ship hulls.

### Key References

- [BaCO: Bayesian Compiler Optimization Framework (ASPLOS 2024)](https://dl.acm.org/doi/10.1145/3623278.3624770)
- [CATBench: Compiler Autotuning Benchmarking Suite (2024)](https://arxiv.org/abs/2406.17811)
- [The False Dawn: Reevaluating Google's RL for Chip Placement](https://arxiv.org/abs/2306.09633)
- [NVIDIA Hybrid RL for EDA (2025)](https://research.nvidia.com/labs/electronic-design-automation/papers/thomas_RL-tuning_todaes25.pdf)

---

## 3. Materials Science / Alloy Design

### Problem Mapping

| Our Problem | Alloy Design Equivalent |
|---|---|
| Discrete weapon choices | Discrete element selection (Ti, Al, V, Cr, ...) |
| Continuous vent/cap allocation | Continuous composition ratios |
| Multi-objective (DPS, EHP, range) | Multi-property (strength, ductility, corrosion resistance) |
| Combat simulation (~30s) | DFT/MD simulation (hours), synthesis+testing (days) |
| 76 dimensions | 5-15 elements x continuous compositions = 10-30D |

### What Actually Works

**Bayesian optimization is THE standard method** for alloy design under expensive evaluation. The field has converged on this more clearly than any other domain surveyed.

**Typical evaluation budgets: 50-200 experiments.** A 2024 study on high-entropy alloys optimized over FCC alloy space with ~50 BO iterations. Another explored only 0.15% of the feasible design space across 5 design-make-test-learn loops. These are the smallest budgets of any field surveyed, making their methods extremely sample-efficient.

**Expected Hypervolume Improvement (EHVI)** is the consensus acquisition function for multi-objective materials optimization. It outperforms scalarization approaches for balancing multiple properties.

**Hierarchical Gaussian Processes** (2025) model correlations between properties to share information across objectives, reducing total evaluations needed. One GP per property, with a hierarchical structure that captures cross-property correlations.

**Physics-informed surrogates** incorporate domain knowledge (thermodynamic models, phase diagrams) into the GP prior. This dramatically improves sample efficiency by encoding known physical constraints. Analogous to encoding game mechanics knowledge into our surrogate.

### Transferable Techniques

- **Domain-knowledge-informed priors.** Materials scientists encode thermodynamics into GP priors. We could encode game mechanics knowledge (e.g., "safety overrides + low-tech weapons is generally good") as prior mean functions. This is the strongest novel idea from this field.
- **EHVI for multi-objective optimization.** If we optimize against multiple opponents simultaneously, EHVI is the proven acquisition function.
- **The 50-200 evaluation budget norm** validates that BO can work with very few expensive evaluations, though their dimensionality (10-30D) is lower than ours (76D).

### Key References

- [Accelerated Multi-Objective Alloy Discovery through Efficient Bayesian Methods (2024)](https://arxiv.org/abs/2405.08900)
- [Hierarchical GP-Based BO for HEA Spaces (2025)](https://arxiv.org/abs/2410.04314)
- [Bayesian Optimization over Problem Formulation Space for Alloy Development (2025)](https://arxiv.org/abs/2502.05735)

---

## 4. Compiler Optimization / Autotuning

### Problem Mapping

| Our Problem | Autotuning Equivalent |
|---|---|
| Binary hullmod toggles | Binary compiler flags (-O2, -ffast-math) |
| Categorical weapon choice | Categorical algorithm selection (tiling strategy, vectorization) |
| Integer vents/caps | Integer parameters (tile sizes, unroll factors) |
| OP budget constraint | Code correctness constraints, memory limits |
| Combat simulation (~30s) | Benchmark execution (seconds-minutes) |

**This is our closest structural analog.** The search space structure (mixed binary+categorical+integer with constraints) matches almost exactly.

### What Actually Works

**Random Forests as surrogate (SMAC3).** SMAC3 uses Random Forest regression with uncertainty from tree-wise variance. This is robust to non-smooth, noisy, heterogeneous response surfaces and excels in hierarchical/irregular search spaces where GP-based methods struggle. For mixed spaces with conditional parameters, RF surrogates outperform GPs.

**BaCO's feasibility-aware BO** (detailed above in EDA section) was specifically designed for compiler autotuning. It handles permutation, ordered, and continuous parameter types along with known and unknown constraints. The key innovation is separating known constraints (handled during acquisition optimization) from hidden constraints (learned via a feasibility classifier).

**OpenTuner's ensemble approach.** OpenTuner runs multiple search algorithms simultaneously (GA, hill climbing, SA, PSO) and dynamically allocates evaluation budget to whichever is performing best. This meta-strategy avoids committing to a single algorithm. With enough parallelism, it is competitive with more sophisticated methods.

**TPE naturally handles mixed spaces.** The Tree-structured Parzen Estimator models conditional densities over high/low-performing configurations. It handles categorical and conditional parameters natively, scales linearly in data points, and is the backbone of both BOHB and Optuna. This confirms our current Optuna TPE choice.

**Evaluation budgets: 100-1000 evaluations** are typical in autotuning. BaCO achieves expert-level performance 2.9-3.9x faster than baselines.

### Transferable Techniques

- **Ensemble of search methods (OpenTuner pattern).** Run TPE, random search, and a local search method in parallel. Allocate budget to whichever finds improvements fastest. Low implementation cost, robust performance.
- **RF surrogate for mixed discrete spaces.** If GP-based BO struggles with our 76D mixed space, switching to an RF surrogate (as in SMAC3) is the proven alternative. RF handles categorical+integer natively without kernel engineering.
- **CATBench's benchmarking methodology.** Standardized benchmark suites with exotic search spaces (conditional, permutation, constrained) could inspire our own evaluation framework for comparing optimizer configurations.

### Key References

- [SMAC3: Versatile Bayesian Optimization Package](https://arxiv.org/abs/2109.09831)
- [BaCO Framework (ASPLOS 2024)](https://weiya711.github.io/publications/asplos2024-baco.pdf)
- [CATBench Benchmarking Suite (2024)](https://arxiv.org/abs/2406.17811)
- [Enhancing BO for Compiler Auto-tuning (PhD Thesis, 2025)](https://etheses.whiterose.ac.uk/id/eprint/37349/1/Zhao_J_Computer_PhD_2025.pdf)
- [OpenTuner Framework](https://www.cl.cam.ac.uk/~ey204/teaching/ACS/R244_2024_2025/papers/ansel_PACT_2014.pdf)

---

## 5. Neural Architecture Search (NAS)

### Problem Mapping

| Our Problem | NAS Equivalent |
|---|---|
| Choosing weapons per slot | Choosing operations per layer/edge |
| Hullmod toggles | Skip connections, normalization choices |
| Combat simulation (~30s) | Training + validation (minutes-days) |
| Build feasibility | Architecture validity constraints |
| ~76 dimensions | 20-100+ dimensions (operation choices per edge) |

### What Actually Works

**Weight sharing / one-shot methods** revolutionized NAS by reducing evaluation cost by 1000x. Instead of training each architecture from scratch, a single "supernet" is trained that shares weights across all architectures. Individual architectures are evaluated by inheriting supernet weights. This is conceptually similar to multi-fidelity BO -- the supernet provides a cheap proxy.

**Zero-cost proxies** push this further: predict architecture performance from the initial randomly-initialized network in milliseconds, with no training at all. Examples: Jacobian covariance, gradient norm, synflow. These proxies correlate with true performance well enough for ranking, even if absolute predictions are poor. **The ranking is what matters for optimization, not absolute accuracy.**

**Evolutionary algorithms dominate in practice.** Despite the theoretical appeal of BO and RL for NAS, evolutionary methods (aging evolution, regularized evolution) are the most robust in practice. They handle discrete spaces naturally, parallelize trivially, and avoid surrogate model failure modes. NAS-Bench studies confirmed this.

**The supernet ranking problem.** One-shot methods suffer from coupled weights causing inaccurate architecture rankings. Recent work (RD-NAS, SiGeo) addresses this with ranking distillation and loss landscape geometry. The lesson: cheap proxies are valuable but imperfect; a portfolio of proxies is more robust than any single one.

**Efficient Global NAS (2025)** applies standard BO (GP + EI) directly to the architecture search space, using graph kernels to model architecture similarity. With 200 evaluations, it matches methods that use 1000+.

### Transferable Techniques

- **Our heuristic scorer IS a zero-cost proxy.** The NAS community's investment in zero-cost proxies validates our heuristic-scorer-as-cheap-evaluation strategy. Key insight: the proxy only needs to get the *ranking* approximately right, not the absolute values.
- **Aging evolution as a complement to BO.** Maintain a population of builds, mutate the best recent ones, discard old ones. Simple, parallel, no surrogate model needed. Good for exploration when BO gets stuck.
- **Supernet-style shared evaluation.** If we could design combat scenarios where partial information transfers between similar builds (e.g., same weapons, different hullmods), we could amortize simulation cost. Probably too game-engine-dependent to implement, but worth noting.

### Key References

- [Advances in Neural Architecture Search (National Science Review, 2024)](https://academic.oup.com/nsr/article/11/8/nwae282/7740455)
- [Efficient Global NAS (2025)](https://arxiv.org/abs/2502.03553)
- [Systematic Review on NAS (2024)](https://link.springer.com/article/10.1007/s10462-024-11058-w)

---

## 6. Protein Engineering / Directed Evolution

### Problem Mapping

| Our Problem | Protein Engineering Equivalent |
|---|---|
| 13 categorical weapon slots | 20 amino acids x N positions |
| 61 binary hullmod toggles | Binary mutation on/off per position |
| Sparse reward (most builds bad) | Sparse fitness (most mutations deleterious) |
| Pairwise weapon synergies | Epistatic interactions between residues |
| ~30s simulation | Hours-days wet-lab assay |
| ~76 dimensions | 50-500 positions (larger, but sparser) |

**This field has the closest analogy to our sparse reward problem.** Most mutations destroy protein function, just as most random builds are terrible.

### What Actually Works

**Machine Learning-guided Directed Evolution (MLDE).** Train a sequence-function model on a small labeled set (~96-384 variants from one 96-well plate), predict fitness for the full combinatorial library, select the top predicted variants for the next round of expensive evaluation. Iterate. This is essentially surrogate-assisted BO with biological-domain surrogates.

**Active learning with uncertainty quantification (ALDE, 2025).** Active Learning-assisted Directed Evolution uses model uncertainty to balance exploitation (high predicted fitness) vs exploration (high uncertainty). This is exactly the acquisition function logic in BO, applied to protein space. The key finding: ALDE reduces total screening burden by 3-10x compared to traditional directed evolution.

**Smart library design for sparse landscapes.** The MODIFY algorithm (Nature Communications, 2024) co-optimizes predicted fitness AND sequence diversity when designing combinatorial libraries. Instead of random exploration, it biases the initial library toward evolutionarily plausible variants while maintaining diversity. This prevents wasting the budget on obviously dead variants.

**Practical budgets: 96-384 variants per round, 3-5 rounds.** Total: 300-2000 expensive evaluations. The 96-well plate is the standard screening format. This matches our expected simulation budget closely.

**Handling epistasis (variable interactions).** Protein engineers explicitly model epistatic interactions (non-additive effects between mutations). Simple additive models fail because the effect of mutation A depends on whether mutation B is present. Higher-order interaction models (pairwise, triple) capture this but require more data. The field has found that pairwise interaction models are usually sufficient -- triple and higher interactions rarely justify the extra data cost.

### Transferable Techniques

- **Pairwise interaction models for weapon/hullmod synergies.** Instead of treating each weapon independently, explicitly model pairwise interactions (weapon A + weapon B synergy). This is analogous to modeling epistatic effects. A pairwise model with 76 variables has ~2850 interaction terms -- learnable from a few hundred evaluations if most interactions are zero (sparsity assumption).
- **Smart initialization (MODIFY pattern).** Instead of random initial builds, bias toward builds that are "game-mechanically plausible" using domain knowledge. Our heuristic scorer already does this. The protein engineering literature validates this approach and shows it is worth 2-5x in sample efficiency.
- **Iterative design-test-learn loops.** The 3-5 round structure (design library -> evaluate -> update model -> design next library) is the operational pattern we should follow. Each round refines the surrogate model.
- **The "most mutations are deleterious" parallel** directly maps to our problem. Their solution: never evaluate a random variant -- always use a model to pre-screen. This is our heuristic scorer + repair pipeline.

### Key References

- [MODIFY: ML-Guided Co-Optimization of Fitness and Diversity (Nature Communications, 2024)](https://www.nature.com/articles/s41467-024-50698-y)
- [Active Learning-Assisted Directed Evolution (Nature Communications, 2025)](https://www.nature.com/articles/s41467-025-55987-8)
- [Machine Learning to Navigate Fitness Landscapes for Protein Engineering](https://pmc.ncbi.nlm.nih.gov/articles/PMC9177649/)
- [ML-Assisted Directed Evolution with Combinatorial Libraries (PNAS)](https://www.pnas.org/doi/10.1073/pnas.1901979116)

---

## Cross-Domain Methods Comparison

### Methods That Appear Across Multiple Fields

| Method | Drug Discovery | EDA | Materials | Autotuning | NAS | Protein Eng. |
|---|---|---|---|---|---|---|
| Bayesian Optimization (GP) | Yes | Yes | Primary | Yes | Yes | Yes |
| Random Forest surrogate | - | - | - | Primary (SMAC3) | - | Yes |
| Multi-fidelity evaluation | Primary | Yes | Yes | Yes | Primary (weight sharing) | - |
| Evolutionary algorithms | Secondary | Yes | - | Yes (OpenTuner) | Primary | Primary |
| Active learning / uncertainty | Yes | - | Yes | - | - | Primary |
| Feasibility-aware acquisition | Yes | Primary (BaCO) | - | Primary (BaCO) | - | - |
| Transfer learning | - | Yes | - | Yes (CATBench) | Yes | - |
| Pairwise interaction models | - | - | - | - | - | Yes (epistasis) |
| Domain-knowledge priors | - | - | Yes (physics) | Yes (known constraints) | - | Yes (evolution) |

### Typical Evaluation Budgets by Field

| Field | Budget | Eval Cost | Total Wall Time |
|---|---|---|---|
| Drug discovery (docking) | 100-300 BO iters | Minutes | Hours-days |
| Drug discovery (wet lab) | 50-100 compounds | Days per batch | Months |
| Chip design (P&R) | 50-200 configs | Hours | Days-weeks |
| Materials (simulation) | 50-200 evals | Hours (DFT) | Days-weeks |
| Materials (experimental) | 50-200 experiments | Days | Months |
| Compiler autotuning | 100-1000 evals | Seconds-minutes | Hours |
| NAS (full training) | 200-500 architectures | Hours-days | Weeks |
| NAS (weight sharing) | 1000-5000 (cheap) | Seconds | Hours |
| Protein engineering | 96-384 per round x 3-5 rounds | Hours-days | Weeks-months |
| **Our problem** | **Target: 200-500 sim evals** | **~30s** | **Hours** |

---

## Synthesis: Actionable Recommendations

### Already Validated by Cross-Domain Evidence

1. **Optuna TPE as primary optimizer.** Confirmed by autotuning community. TPE handles mixed discrete spaces natively, scales linearly, and is competitive with more complex methods at practical budgets.

2. **Heuristic scorer as cheap multi-fidelity proxy.** Validated by drug discovery (docking scores), NAS (zero-cost proxies), and protein engineering (ML pre-screening). All fields agree: use cheap evaluation to filter before expensive evaluation.

3. **Repair-based constraint handling.** Our `repair_build()` approach matches BaCO's known-constraint handling during acquisition optimization. The EDA/autotuning community confirms this is the right pattern for sparse feasibility regions.

4. **Iterative design-test-learn loops.** Every field converges on this pattern. Don't try to solve the problem in one shot -- iterate with model refinement.

### Novel Techniques to Consider

5. **Feasibility-weighted acquisition function (from BaCO/EDA).** Multiply the acquisition function value by P(build is good | features). Learn P(good) from simulation history. This goes beyond binary feasibility -- even among feasible builds, some regions are more likely to produce good combat results.

6. **Pairwise interaction features in the surrogate (from protein engineering).** Model weapon-weapon and weapon-hullmod pairwise effects explicitly. With 76 variables, a sparse pairwise model has O(2850) terms. Use L1 regularization to learn which interactions matter. This directly addresses our "strong pairwise interactions" challenge.

7. **Ensemble of search methods (from OpenTuner/autotuning).** Run TPE + random search + evolutionary mutation in parallel. Dynamically allocate budget to whichever is improving fastest. Low implementation cost, robust against any single method failing.

8. **Domain-knowledge prior mean (from materials science).** Encode game mechanics knowledge into the surrogate's prior. Instead of a zero prior mean, use the heuristic score as the GP prior mean function. The surrogate then only needs to learn the *residual* between heuristic and simulation.

9. **Smart initialization biased toward plausible builds (from protein engineering).** The MODIFY algorithm's insight: don't waste initial budget on random exploration. Bias the initial evaluation set toward builds that domain knowledge suggests are plausible, while maintaining diversity. Our heuristic scorer provides exactly this bias.

### Methods to Avoid

10. **Pure RL approaches.** The AlphaChip controversy confirms that for discrete placement/assignment problems with strong constraints, BO and evolutionary methods outperform RL. RL requires massive evaluation budgets (10K+) and reward engineering that doesn't justify itself at our scale.

11. **Latent-space optimization** (unless we build a generative model of builds). This works in drug discovery because large molecular datasets enable pre-training. We don't have enough existing build data to train a useful latent space.

12. **Over-engineering the optimizer at low budgets.** The NeurIPS 2024 discrete BO benchmark found that "for low budgets, simpler baselines tend to perform as well or better than most BO methods." With 200-500 evaluations, the difference between TPE and a more complex method may not justify the implementation cost.

---

## Specific Method Deep-Dives

### Bounce (NeurIPS 2023) -- For Reference

Bounce maps mixed variables (categorical, binary, ordinal, continuous) into nested embeddings of increasing dimensionality. It starts optimization in a low-dimensional subspace and progressively splits "bins" to increase resolution. Handles our exact variable types. However, per the NeurIPS 2024 benchmark, it doesn't consistently outperform simpler methods at low budgets.

### SAASBO -- Relevant for High-D

SAASBO (Sparse Axis-Aligned Subspace BO) uses sparsity-inducing priors (half-Cauchy on inverse lengthscales) with Hamiltonian Monte Carlo inference. It automatically identifies which dimensions matter. Tested on problems with hundreds of dimensions. Directly relevant to our 76D space. Available in BoTorch/Ax.

### COMBO -- Graph Kernel BO

COMBO models the combinatorial space as a graph (Cartesian product of per-variable subgraphs) and uses a diffusion kernel for the GP. The ARD diffusion kernel models high-order variable interactions. Horseshoe prior enables automatic variable selection. Theoretically elegant but may not scale to 76D.

### Walsh Surrogates -- For Binary Variables

Walsh decomposition provides an exact functional decomposition for pseudo-Boolean (binary) functions. Walsh surrogates model the objective as a sum of Walsh basis functions up to order k. For our 61 binary hullmod variables, a Walsh surrogate of order 2 (pairwise interactions) captures the most important structure with O(61^2) = ~1800 terms. This is specific to the binary portion of our space and could complement a GP/RF surrogate for the categorical portion.
