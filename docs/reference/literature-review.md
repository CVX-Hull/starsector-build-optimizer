# Literature Review

Comprehensive survey of 40+ papers organized by topic. Each entry includes the key contribution, relevance assessment, and ArXiv ID where available.

---

## Table of Contents

1. [Mixed-Variable Bayesian Optimization](#1-mixed-variable-bayesian-optimization)
2. [Evolutionary Methods for Mixed Spaces](#2-evolutionary-methods-for-mixed-spaces)
3. [Multi-Fidelity Optimization](#3-multi-fidelity-optimization)
4. [Quality-Diversity Optimization](#4-quality-diversity-optimization)
5. [Constrained Optimization](#5-constrained-optimization)
6. [Neural Surrogates and Tabular Deep Learning](#6-neural-surrogates-and-tabular-deep-learning)
7. [Game Optimization and Balancing](#7-game-optimization-and-balancing)
8. [Noise Handling and Adaptive Replication](#8-noise-handling-and-adaptive-replication)
9. [Surveys and Benchmarks](#9-surveys-and-benchmarks)

---

## 1. Mixed-Variable Bayesian Optimization

### CASMOPOLITAN — Trust-Region BO for Mixed Spaces
- **ArXiv**: [2102.07188](https://arxiv.org/abs/2102.07188) (ICML 2021)
- **Authors**: Wan et al.
- **Key contribution**: Extends TuRBO's trust-region approach to discrete/mixed-integer problems. Uses Hamming distance for categorical trust regions with a Transformed Overlap (TO) kernel for the GP. Interleaves gradient-based continuous optimization with hill-climbing local search for categoricals.
- **Relevance**: HIGH. Trust-region approach well-suited for structured mixed spaces. Best sample efficiency when local structure exists.
- **Implementation**: [GitHub](https://github.com/xingchenwan/Casmopolitan) — research-quality Python, GPyTorch/BoTorch backend.
- **Limitations**: No native batch/parallel support; no native constraint handling.

### Bounce — Reliable Mixed-Space BO via Nested Embeddings
- **ArXiv**: [2307.00618](https://arxiv.org/abs/2307.00618) (NeurIPS 2023)
- **Authors**: Papenmeier, Nardi, Poloczek
- **Key contribution**: Uses sparse count-sketch embeddings that map high-dimensional mixed variables into lower-dimensional target space, progressively increasing dimensionality. Variables of same type only share bins. Prior observations remain valid across refinements (nested property).
- **Relevance**: MEDIUM. Elegant algorithm but **not suitable as primary optimizer** for our constrained problem. No constraint support (the internal binning/embedding makes repair interaction poorly defined), no PyPI package, research-quality code. Native batch parallelism (qEI) is a genuine strength. Could be used for benchmarking on heuristic proxy.
- **Implementation**: [GitHub](https://github.com/lpapenme/bounce) — Python ≥ 3.10, BoTorch/GPyTorch. Research code, Poetry-based, not packaged.
- **Limitations**: No constraint handling, no PyPI, research code quality.

### CoCaBO — Multi-Armed Bandits + GP for Mixed Variables
- **ArXiv**: [1906.08878](https://arxiv.org/abs/1906.08878) (AAAI 2020)
- **Authors**: Ru et al.
- **Key contribution**: Combines multi-armed bandits for categorical variables with GP-based BO for continuous ones. Overlapped kernel shares information across categories. Supports batch evaluation.
- **Relevance**: HIGH. Foundational mixed-variable BO with batch mode.

### COMBO — Combinatorial BO via Graph Cartesian Product
- **ArXiv**: [1902.00448](https://arxiv.org/abs/1902.00448) (NeurIPS 2019)
- **Authors**: Oh, Tomczak, Gavves, Welling
- **Key contribution**: Models each variable as a graph; uses graph Cartesian product for joint space. Graph Fourier Transform scales linearly. Handles purely combinatorial spaces.
- **Relevance**: HIGH. Foundational but scales poorly with many combinations.

### BODi — High-Dimensional Combinatorial BO
- **ArXiv**: [2303.01774](https://arxiv.org/abs/2303.01774) (AISTATS 2023)
- **Authors**: Deshwal et al.
- **Key contribution**: Dictionary-based ordinal embeddings to map discrete variables to continuous space. Binary wavelets for dictionary construction.
- **Relevance**: MEDIUM-HIGH. Can degrade when optima lack structure.

### MVRSM — ReLU Surrogate with Integer Guarantees
- **ArXiv**: [2006.04508](https://arxiv.org/abs/2006.04508) (GECCO 2021)
- **Authors**: Bliek, van Stein, Bäck
- **Key contribution**: Linear combination of random ReLU basis functions as surrogate. Piecewise-linear property guarantees local optima satisfy integer constraints by construction. O(1) per iteration (no growing covariance matrix). Scales to 238 mixed variables.
- **Relevance**: HIGH as a fast baseline. No uncertainty quantification, no native categoricals, no batch support.
- **Implementation**: [GitHub](https://github.com/lbliek/MVRSM) — minimal Python, ~200 lines core.

### PWAS — Piecewise Affine Surrogates for Mixed Variables
- **ArXiv**: [2302.04686](https://arxiv.org/abs/2302.04686)
- **Authors**: Bemporad, Mosca
- **Key contribution**: Piecewise affine surrogate for linearly constrained mixed-variable problems. Uses MILP solvers for exploration.
- **Relevance**: HIGH. Handles linear constraints on mixed variables — applicable to OP budget.

### TuRBO — Scalable Global Optimization via Local BO
- **ArXiv**: [1910.01739](https://arxiv.org/abs/1910.01739) (NeurIPS 2019)
- **Authors**: Eriksson et al.
- **Key contribution**: Local GP trust regions with bandit-based restart allocation. Foundation for CASMOPOLITAN and Bounce.
- **Relevance**: HIGH. Foundational architecture.

### MOCA-HESP — Meta-Algorithm for Mixed BO
- **ArXiv**: [2508.06847](https://arxiv.org/abs/2508.06847) (ECAI 2025)
- **Key contribution**: Wraps CASMOPOLITAN/Bounce with hyper-ellipsoid space partitioning and adaptive encoder selection.
- **Relevance**: MEDIUM. Reported improvements over standalone methods.

### Heat Kernels in Combinatorial BO
- **ArXiv**: [2510.26633](https://arxiv.org/abs/2510.26633)
- **Key contribution**: Shows CASMOPOLITAN's TO kernel is a special case of heat kernels on Hamming graphs. Proposes improved variants.
- **Relevance**: MEDIUM. Theoretical insight for kernel design.

### Optuna TPE — Tree-structured Parzen Estimator
- **ArXiv**: [2304.11127](https://arxiv.org/abs/2304.11127) (2023)
- **Authors**: Watanabe, Hutter
- **Key contribution**: Comprehensive study of TPE algorithm components. Multivariate mode builds joint Parzen estimator (Scott's rule bandwidth). `n^(-1/(4+d))` bandwidth scaling means TPE degrades at high dimensions.
- **Relevance**: CRITICAL — **our chosen primary optimizer framework**. Clean ask-tell API, constant_liar batch parallelism (acceptable at B=4-8), swappable samplers via OptunaHub. Our `repair_build()` eliminates the need for native constraint handling.
- **Implementation**: `pip install optuna` — production-ready, 10K+ GitHub stars.
- **Limitations**: Degrades past ~30D (kernel density estimation curse of dimensionality). Needs n_ei_candidates=256+ and n_startup_trials=100+ for our 50-70D space.

### c-TPE — Constrained TPE
- **ArXiv**: [2211.14411](https://arxiv.org/abs/2211.14411) (IJCAI 2023)
- **Authors**: Watanabe, Hutter
- **Key contribution**: Modifies TPE density estimation to properly handle inequality constraints. Separates feasible/infeasible trials in density modeling. More principled than simple penalty.
- **Relevance**: HIGH. Use via `constraints_func` to report OP budget violation to TPE, biasing away from infeasible regions.
- **Implementation**: Available as OptunaHub sampler.

### piBO — Prior-Informed Bayesian Optimization
- **ArXiv**: [2204.11051](https://arxiv.org/abs/2204.11051) (ICLR 2022)
- **Authors**: Hvarfner et al.
- **Key contribution**: Multiplies acquisition function by user-specified prior distribution over optimum location. **Convergence guaranteed at regular rates regardless of prior quality.** Even a wrong prior won't break convergence.
- **Relevance**: HIGH. Our heuristic scores can define the prior. Guarantee that bad heuristic won't hurt asymptotic performance.

### Warm Starting Bayesian Optimization
- **ArXiv**: [1608.03585](https://arxiv.org/abs/1608.03585) (2016)
- **Authors**: Poloczek, Wang, Frazier
- **Key contribution**: Multi-task GP jointly models current task and previous tasks. Transfers knowledge from cheap evaluations to expensive task.
- **Relevance**: HIGH. Foundation for using heuristic evaluations to warm-start simulation BO.

### Prior Mean Models for Faster BO
- **Reference**: Scientific Reports (2025), Nature
- **Key contribution**: Uses NN-trained prior mean function in GP for 73D particle accelerator optimization. Standard BO hadn't converged after 900 iterations; prior-mean BO converged in one step. Even correlation r~0.41 helped in early stages.
- **Relevance**: CRITICAL. Validates our heuristic-as-prior-mean approach. Our R²≈0.49 is sufficient for early speedup.

---

## 2. Evolutionary Methods for Mixed Spaces

### CatCMA with Margin — Joint Gaussian + Categorical Optimization
- **ArXiv**: [2504.07884](https://arxiv.org/abs/2504.07884) (GECCO 2025)
- **Authors**: Hamano, Nomura, Saito, Uchida, Shirakawa
- **Key contribution**: Joint multivariate Gaussian + categorical distribution updated via natural gradient. Novel "margin" mechanism for integer variables: lower AND upper bounds on marginal probabilities prevent premature convergence without inflating variance. Supports multi-objective (bi-objective via COMO-CatCMAwM).
- **Relevance**: VERY HIGH. Handles continuous + integer + categorical jointly. Outperforms BO at moderate dimensions (10+10+10). Clean ask-tell API. Population size maps naturally to parallel instances.
- **Implementation**: `pip install cmaes` ([GitHub](https://github.com/CyberAgentAILab/cmaes)) — production-ready, MIT license, 570+ commits.
- **Limitations**: Multi-objective currently bi-objective only. Less sample-efficient than BO at <100 evals. No built-in surrogate.

### Original CatCMA
- **ArXiv**: [2405.09962](https://arxiv.org/abs/2405.09962) (GECCO 2024)
- **Authors**: Hamano et al.
- **Key contribution**: Introduced joint Gaussian + categorical distribution with IGO-based natural gradient. CatCMA with Margin adds integer handling.
- **Relevance**: HIGH. Foundation for CatCMAwM.

### CMA-ES with Margin
- **ArXiv**: [2205.13482](https://arxiv.org/abs/2205.13482) (GECCO 2022)
- **Key contribution**: Lower-bounds marginal probability for integer variables in CMA-ES. Precursor to CatCMA margin mechanism.
- **Relevance**: MEDIUM. Foundational for integer handling.

---

## 3. Multi-Fidelity Optimization

### rMFBO — Robust Multi-Fidelity BO
- **ArXiv**: [2210.13937](https://arxiv.org/abs/2210.13937) (AISTATS 2023)
- **Authors**: Mikkola et al.
- **Key contribution**: Provides theoretical guarantee that multi-fidelity BO performs no worse than single-fidelity BO, with high controllable probability. Prevents misleading low-fidelity sources from hurting performance.
- **Relevance**: MEDIUM. Our heuristic R²≈0.49 is below the 0.75 threshold where MFBO reliably helps (per best-practices paper). We use heuristic-as-warm-start instead of full MFBO. rMFBO becomes relevant if heuristic calibration improves.

### MFES-HB — Multi-Fidelity Ensemble Surrogate + HyperBand
- **ArXiv**: [2012.03011](https://arxiv.org/abs/2012.03011) (AAAI 2021)
- **Authors**: Li et al.
- **Key contribution**: Builds ensemble surrogate from ALL fidelity levels (unlike BOHB which only uses highest). Product of Experts framework with learned weights per fidelity. Discordant sources are automatically downweighted.
- **Relevance**: MEDIUM. Designed for Hyperband-style continuous fidelity (training epochs). With our 2 discrete fidelity levels (instant heuristic vs full sim, no intermediate), the Hyperband bracket structure degenerates. Better suited if we had 3+ fidelity levels.
- **Implementation**: [GitHub](https://github.com/PKU-DAIR/MFES-HB)

### MF-MES — Multi-Fidelity Max-value Entropy Search
- **ArXiv**: [1901.08275](https://arxiv.org/abs/1901.08275) (ICML 2020)
- **Authors**: Takeno et al.
- **Key contribution**: Information-theoretic acquisition for multi-fidelity. Computes information gain from evaluating at any (x, fidelity) pair. Supports async parallel.
- **Relevance**: MEDIUM. Requires GP surrogate (we use TPE). Relevant if we switch to BoTorch-based optimization.
- **Implementation**: [GitHub](https://github.com/takeuchi-lab/MF-MES)

### DEHB — Differential Evolution + HyperBand
- **ArXiv**: [2105.09821](https://arxiv.org/abs/2105.09821) (IJCAI 2021)
- **Key contribution**: Replaces TPE with Differential Evolution in HyperBand. Strong with discrete/categorical. Up to 1000x faster than random search.
- **Relevance**: HIGH. Good when fidelities map to budget schedule.
- **Implementation**: [GitHub](https://github.com/automl/DEHB), also available as Optuna sampler.

### Deep Multi-Fidelity GPs
- **ArXiv**: [1903.07320](https://arxiv.org/abs/1903.07320)
- **Authors**: Cutajar et al.
- **Key contribution**: Extends AR1 multi-fidelity model to nonlinear inter-fidelity relationships via Deep GPs.
- **Relevance**: HIGH. Captures nonlinear relationship between heuristic scores and simulation outcomes.

### Multi-Fidelity BO Review
- **ArXiv**: [2311.13050](https://arxiv.org/abs/2311.13050)
- **Key contribution**: Comprehensive survey of GP-based multi-fidelity surrogates and acquisition functions.
- **Relevance**: HIGH. Essential reference for understanding the landscape.

### Multi-Fidelity Best Practices
- **ArXiv**: [2410.00544](https://arxiv.org/abs/2410.00544) (Nature Computational Science 2025)
- **Key contribution**: Practical recommendations for MFBO. Adaptive weighting critical when fidelity informativeness varies across search space. Cost ratio strongly determines benefit.
- **Relevance**: HIGH. Directly applicable guidance.

---

## 4. Quality-Diversity Optimization

### CMA-ME — CMA-ES + MAP-Elites
- **ArXiv**: [1912.02400](https://arxiv.org/abs/1912.02400) (GECCO 2020)
- **Authors**: Fontaine, Togelius, Nikolaidis, Hoover
- **Key contribution**: Replaces MAP-Elites random mutation with CMA-ES emitters. Three emitter types: optimizing, random direction, improvement. Doubles MAP-Elites performance.
- **Relevance**: HIGH. Foundation for our QD approach.

### CMA-MAE — MAP-Annealing
- **ArXiv**: [2205.10752](https://arxiv.org/abs/2205.10752)
- **Authors**: Fontaine, Nikolaidis
- **Key contribution**: Annealing threshold smoothly transitions from CMA-ES (pure optimization) to MAP-Elites (diversity). Avoids premature exploration.
- **Relevance**: VERY HIGH — **our recommended QD algorithm**.

### DSA-ME — Deep Surrogate Assisted MAP-Elites (Hearthstone)
- **ArXiv**: [2112.03534](https://arxiv.org/abs/2112.03534) (GECCO 2022)
- **Authors**: Zhang, Fontaine, Hoover, Nikolaidis
- **Key contribution**: Trains deep neural network online as surrogate for Hearthstone deckbuilding. MAP-Elites discovers diverse dataset improving surrogate; surrogate guides MAP-Elites toward promising decks.
- **Relevance**: CRITICAL — **our direct blueprint**. Replace "deck" with "ship build."

### SAIL — Surrogate-Assisted Illumination
- **ArXiv**: [1702.03713](https://arxiv.org/abs/1702.03713)
- **Authors**: Gaier, Asteroth, Mouret
- **Key contribution**: GP surrogate + MAP-Elites illumination. Several orders of magnitude fewer evaluations than standard MAP-Elites.
- **Relevance**: HIGH. Template for surrogate-assisted QD.

### MAP-Elites + Sliding Boundaries (Hearthstone Deckbuilding)
- **ArXiv**: [1904.10656](https://arxiv.org/abs/1904.10656) (GECCO 2019)
- **Authors**: Fontaine et al.
- **Key contribution**: Adaptive cell boundaries for MAP-Elites. Discovered diverse Hearthstone strategies (aggro, control, midrange, combo).
- **Relevance**: VERY HIGH. Direct game analogue. Sliding boundaries prevent empty-cell problem.

### Bayesian QD for Mixed Variables
- **ArXiv**: [2310.05955](https://arxiv.org/abs/2310.05955) (2024)
- **Authors**: Brevault, Balesdent
- **Key contribution**: GP surrogates with mixed-variable kernels for constrained QD. Handles continuous + discrete + categorical. Aerospace engineering application.
- **Relevance**: VERY HIGH. Directly applicable to our mixed-variable QD problem.

### CVT-MAP-Elites
- **ArXiv**: [1610.05729](https://arxiv.org/abs/1610.05729)
- **Key contribution**: Uses Centroidal Voronoi Tessellation instead of grid. Decouples archive size from dimensionality.
- **Relevance**: HIGH. Preferred archive type for >3 behavior dimensions.

---

## 5. Constrained Optimization

### SCBO — Scalable Constrained BO
- **Reference**: Eriksson & Poloczek, AISTATS 2021
- **Key contribution**: Extends TuRBO to constrained problems. Feasible-beats-infeasible ranking.
- **Relevance**: HIGH. Available in BoTorch.
- **Implementation**: [BoTorch tutorial](https://botorch.org/docs/tutorials/scalable_constrained_bo/)

### Constrained BO with Knowledge Gradient
- **ArXiv**: [2105.13245](https://arxiv.org/abs/2105.13245)
- **Key contribution**: cKG acquisition with convergence guarantee for constrained problems.
- **Relevance**: MEDIUM. Designed for expensive constraints (ours are cheap).

### COBALt — Active Learning of Unknown Constraints
- **ArXiv**: [2310.08751](https://arxiv.org/abs/2310.08751)
- **Key contribution**: Adaptive constraint boundary learning.
- **Relevance**: MEDIUM. Unnecessary for our cheap constraints.

### Arc Kernel for Conditional Parameter Spaces
- **ArXiv**: [1409.4011](https://arxiv.org/abs/1409.4011)
- **Authors**: Swersky, Duvenaud, Snoek, Hutter
- **Key contribution**: GP kernel that handles conditional/hierarchical parameter spaces.
- **Relevance**: HIGH. Needed if using GP-based BO with conditional hullmod parameters.

### Which Constraints Matter?
- **ArXiv**: [2512.17569](https://arxiv.org/abs/2512.17569)
- **Key contribution**: Classifies constraint types and handling strategies.
- **Relevance**: MEDIUM. Reference for constraint handling design.

### Lamarckian vs Baldwinian Repair in Multi-Objective Optimization
- **Reference**: Ishibuchi et al., EMO 2005 (Springer LNCS 3410)
- **Key contribution**: Compares Lamarckian (remember repaired genotype) vs Baldwinian (remember original genotype with repaired fitness) repair on knapsack problems. Baldwinian outperforms Lamarckian in EA context; partial Lamarckianism (5% rule) often best.
- **Relevance**: HIGH. For TPE/BO, Lamarckian is preferred (record repaired params via `add_trial`), because surrogate learns the feasible manifold directly. The EA-specific convergence concerns don't apply to density-based TPE.

### Decoder-Based EA for Constrained Optimization
- **Reference**: Koziel & Michalewicz, 1999 (Springer)
- **Key contribution**: Defines homomorphous mapping between n-dimensional cube and feasible space. Identifies many-to-one mapping (collision) problem and its consequences: wasted evaluations, surrogate confusion.
- **Relevance**: HIGH. Our `repair_build()` is a decoder/repair operator. The collision problem motivates our deduplication cache.

### Constraint-Handling Techniques Survey
- **Reference**: Coello Coello (2002), comprehensive survey
- **Key contribution**: Classifies constraint handling into penalty functions, repair methods, decoder methods, feasibility-preserving operators. Repair finds feasible solutions in 1 generation vs 7-72 for penalty.
- **Relevance**: HIGH. Validates our repair-first approach over penalty-only.

---

## 6. Neural Surrogates and Tabular Deep Learning

### TabPFN-2.5 — Prior-Fitted Network for Tabular Data
- **ArXiv**: [2511.08667](https://arxiv.org/abs/2511.08667) (Nature 2024)
- **Key contribution**: Transformer pre-trained on millions of synthetic datasets. In-context learning at inference — no training needed. 100% win rate vs default XGBoost on datasets ≤10K samples. Specialized regression checkpoints for <3000 samples.
- **Relevance**: CRITICAL — **our recommended Phase 1 surrogate**. Purpose-built for our 500-2000 sample regime.
- **Implementation**: [GitHub](https://github.com/PriorLabs/TabPFN)

### FT-Transformer — Feature Tokenizer + Transformer
- **ArXiv**: [2106.11959](https://arxiv.org/abs/2106.11959) (NeurIPS 2021)
- **Authors**: Gorishniy et al.
- **Key contribution**: Tokenizes each feature into embedding, applies Transformer self-attention. Outperforms MLP/ResNet tabular models.
- **Relevance**: HIGH. Our recommended Phase 3 (2000+ samples) architecture.

### Trees vs Deep Learning on Tabular Data
- **ArXiv**: [2207.08815](https://arxiv.org/abs/2207.08815) (NeurIPS 2022)
- **Authors**: Grinsztajn et al.
- **Key finding**: Tree models (XGBoost, CatBoost) remain competitive on heterogeneous mixed-type data. Neural nets win on homogeneous continuous features.
- **Relevance**: HIGH. Our heterogeneous features favor tree models initially.

### When Do Neural Nets Outperform Boosted Trees?
- **ArXiv**: [2305.02997](https://arxiv.org/abs/2305.02997) (NeurIPS 2023)
- **Authors**: McElfresh et al.
- **Key finding**: Dataset characteristics predict which model family wins. Difference is often negligible.
- **Relevance**: MEDIUM. Supports our ensemble approach.

### Entity Embeddings of Categorical Variables
- **ArXiv**: [1604.06737](https://arxiv.org/abs/1604.06737)
- **Authors**: Guo, Berkhahn
- **Key contribution**: Learn dense vector representations for categorical values. Similar items end up close in embedding space.
- **Relevance**: HIGH. Standard technique for encoding weapon IDs and hullmod selections.

### BNN Surrogates for Bayesian Optimization
- **ArXiv**: [2305.20028](https://arxiv.org/abs/2305.20028) (ICLR 2024)
- **Authors**: Li, Rudner, Wilson
- **Key findings**: Method ranking is problem-dependent. Deep kernel learning is competitive with full BNNs. Deep ensembles perform relatively poorly as BO surrogates.
- **Relevance**: HIGH. Informs our surrogate uncertainty quantification strategy.

### Deep Sets — Permutation-Invariant Architecture
- **ArXiv**: [1703.06114](https://arxiv.org/abs/1703.06114) (NeurIPS 2017)
- **Key contribution**: `rho(SUM(phi(x_i)))` for set-valued inputs. Each weapon-in-slot encoded as element.
- **Relevance**: MEDIUM. Potentially useful for encoding "set of equipped weapons."

### Set Transformer
- **ArXiv**: [1810.00825](https://arxiv.org/abs/1810.00825) (ICML 2019)
- **Key contribution**: Self-attention over set elements captures pairwise interactions (weapon synergies).
- **Relevance**: MEDIUM. For later phases with more data.

### Deep Ensembles
- **ArXiv**: [1612.01474](https://arxiv.org/abs/1612.01474)
- **Key contribution**: Train 5-10 models with different seeds; use variance as uncertainty.
- **Relevance**: HIGH. Practical uncertainty quantification for BO integration.

---

## 7. Game Optimization and Balancing

### Metagame Autobalancing
- **ArXiv**: [2006.04419](https://arxiv.org/abs/2006.04419) (IEEE CoG 2020)
- **Authors**: Hernandez et al.
- **Key contribution**: Simulation-based optimization matching designer-specified metagame graph. Uses CMA-ES optimizer.
- **Relevance**: VERY HIGH. Closest methodology to our approach (simulate → compute stats → optimize).

### Meta Discovery Framework (Pokemon Showdown)
- **ArXiv**: [2409.07340](https://arxiv.org/abs/2409.07340) (2024)
- **Authors**: Saravanan, Guzdial
- **Key contribution**: RL-trained battle agent + team builder + simulator predicts balance change impact. Team builder component is analogous to build optimization.
- **Relevance**: HIGH. Closest game analogue to our problem.

### RuleSmith — LLM + BO for Game Balancing
- **ArXiv**: [2602.06232](https://arxiv.org/abs/2602.06232) (2026)
- **Key contribution**: Combines LLM self-play with Bayesian optimization. Adaptive sampling for noisy game evaluations.
- **Relevance**: MEDIUM-HIGH. Novel LLM+BO approach.

### GEEvo — Game Economy Balancing with EA
- **ArXiv**: [2404.18574](https://arxiv.org/abs/2404.18574) (2024)
- **Key contribution**: Two-step evolutionary approach for game economies with simulation-based fitness.
- **Relevance**: MEDIUM.

### Efficient Evolutionary Methods for Game Agent Optimization
- **ArXiv**: [1901.00723](https://arxiv.org/abs/1901.00723)
- **Key finding**: **Surrogate-assisted methods significantly outperform direct evolutionary search** when evaluations are expensive.
- **Relevance**: HIGH. Validates our surrogate-based approach.

### StarCraft II Combat Prediction
- **Reference**: Expert Systems with Applications, 2021
- **Key finding**: CNNs on composition + battlefield features achieve ~90-95% binary win/loss accuracy.
- **Relevance**: HIGH. Precedent for combat outcome prediction accuracy.

### Early Prediction of Winner in StarCraft Matches
- **Reference**: Sanchez-Ruiz (2017), CIG Conference
- **Key contribution**: Time-dependent features (resource trajectories, army value over time) more predictive than static features. Adversarial features (damage dealt to enemy) outperform non-adversarial features (own economy). 80%+ accuracy with 200 training samples using adversarial features.
- **Relevance**: HIGH. Validates our heartbeat trajectory feature approach. HP differential at early checkpoints should be highly predictive.

### Empirical Game-Theoretic Analysis (EGTA) Survey
- **ArXiv**: [2403.04018](https://arxiv.org/abs/2403.04018) (2024)
- **Authors**: Wellman et al.
- **Key contribution**: Framework for analyzing strategic interactions via payoff matrices constructed from simulation. Covers Nash equilibria, dominated strategies, meta-game analysis.
- **Relevance**: HIGH. Our opponent pool + win-rate matrix is a lightweight EGTA approach.

### Policy Space Response Oracles (PSRO) Survey
- **ArXiv**: [2403.02227](https://arxiv.org/abs/2403.02227) (2024)
- **Key contribution**: Iterative framework: start with small strategy set, find equilibrium, generate best response, add to set. Converges to Nash.
- **Relevance**: MEDIUM. Overkill for our fixed opponent pool, but relevant if we want to co-evolve opponents.

### BO for Nash Equilibria in Black-Box Games
- **ArXiv**: [1804.10586](https://arxiv.org/abs/1804.10586) (2018)
- **Authors**: Al-Dujaili et al.
- **Key contribution**: GP surrogates approximate payoffs, then solve for equilibria on the surrogate. 50-200 evaluations for convergence on small games.
- **Relevance**: MEDIUM. For future minimax optimization if needed.

### Pareto Set Learning for Expensive Multi-Objective Optimization
- **ArXiv**: [2210.08495](https://arxiv.org/abs/2210.08495) (NeurIPS 2022)
- **Key contribution**: Learning the full Pareto front with GPs in expensive settings (50-200 evaluations). Returns diverse set of Pareto-optimal solutions.
- **Relevance**: HIGH. Could provide Pareto front of builds (win rate vs shield tanks, vs kiters, vs carriers).

---

## 8. Noise Handling and Adaptive Replication

### Heteroscedastic BO
- **ArXiv**: [1910.07779](https://arxiv.org/abs/1910.07779)
- **Authors**: Griffiths et al.
- **Key contribution**: Heteroscedastic GP with noise-penalizing acquisition functions. Finds inputs that are both high-performing AND low-variance.
- **Relevance**: VERY HIGH. Some builds have inherently more variable outcomes.

### Budget-Adaptive OCBA
- **ArXiv**: [2304.02377](https://arxiv.org/abs/2304.02377) (2023)
- **Key contribution**: Dynamically adjusts replication allocation. Maximizes Probability of Correct Selection under small budgets.
- **Relevance**: HIGH. Directly applicable to our adaptive replication strategy.

### Stochastic Kriging Tutorial
- **ArXiv**: [2502.05216](https://arxiv.org/abs/2502.05216) (2025)
- **Key contribution**: Tutorial on GP surrogates for stochastic simulations.
- **Relevance**: MEDIUM-HIGH. Foundational reference.

---

## 9. Surveys and Benchmarks

### MCBO Framework — Modular Combinatorial BO
- **ArXiv**: [2306.09803](https://arxiv.org/abs/2306.09803) (NeurIPS 2023)
- **Authors**: Dreczkowski, Grosnit, Bou Ammar (Huawei Noah's Ark)
- **Key contribution**: Modular framework: surrogate × acquisition function × acquisition optimizer × trust region. 4000+ experiments, 47 novel combinations + 7 existing solvers. Finding: trust regions matter enormously.
- **Relevance**: CRITICAL — **our recommended benchmarking framework**. Mix-and-match components for systematic comparison.
- **Implementation**: [GitHub](https://github.com/huawei-noah/HEBO/tree/master/MCBO) — MIT license, production-grade.

### High-Dimensional BO of Discrete Sequences
- **ArXiv**: [2406.04739](https://arxiv.org/abs/2406.04739) (NeurIPS 2024)
- **Key contribution**: Unified framework (poli/poli-baselines) for discrete BO benchmarks.
- **Relevance**: MEDIUM. Useful for understanding method landscape.

### Multi-Fidelity Methods for Optimization Survey
- **ArXiv**: [2402.09638](https://arxiv.org/abs/2402.09638)
- **Key contribution**: Broad survey of multi-fidelity approaches.
- **Relevance**: HIGH. Essential background.

### Multi-Objective BO with Mixed-Categorical Variables (Aeronautics)
- **ArXiv**: [2504.09930](https://arxiv.org/abs/2504.09930) (2025)
- **Key contribution**: Multi-objective BO for mixed-categorical variables in expensive engineering simulation.
- **Relevance**: VERY HIGH. Analogous problem structure.

---

## Novelty Assessment

**What has NOT been done in the literature:**
1. No automated build optimization for Starsector or structurally similar ship-fitting games
2. No QD-based exploration of game build archetypes with combat simulation
3. No heuristic-as-prior-mean combined with Optuna TPE for warm-starting expensive game simulation BO
4. No CatCMA-based emitter inside MAP-Elites for mixed-variable game build discovery
5. No opponent-pool-based fitness evaluation with WilcoxonPruner for sample-efficient build optimization

Our project would be novel work at the intersection of mixed-variable BO, quality-diversity, multi-fidelity optimization, and game build optimization. The opponent pool strategy with WilcoxonPruner is particularly novel — it addresses the RPS dynamics inherent in combat games while remaining sample-efficient.
