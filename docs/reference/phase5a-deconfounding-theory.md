# Phase 5A — Deconfounding Theory (TWFE Foundation)

> **Status**: Research synthesis. The design it produced (TWFE decomposition) is shipped in `src/starsector_optimizer/deconfounding.py` and specified in `docs/specs/28-deconfounding.md`.

Research synthesis from 6 independent literature surveys (IRT/adaptive testing, game rating systems, causal inference, bandits/active learning, sports analytics, coevolutionary algorithms) addressing two core problems:

1. **Cross-subset comparability**: How to compare builds tested against different opponent subsets.
2. **Temporal confounding**: How to separate "build improved" from "opponent was easy" when build quality is non-stationary.

Reading this doc cold: this is the *theory* foundation for the Phase 5A fitness aggregator that ships in the optimizer. The downstream pieces — Phase 5C opponent selection, Phase 5D EB shrinkage of α̂ toward a heuristic prior, Phase 5E shape revision — all build on the TWFE model derived here. See `docs/reference/implementation-roadmap.md` for the full Phase 5 overview.

---

## 1. The Core Finding: Additive Decomposition

Six independent fields converge on the same model:

```
score(build_i, opponent_j) = α_i + β_j + ε_ij
```

Where α_i = build quality, β_j = opponent difficulty, ε_ij = residual noise.

| Field | Name | Reference |
|-------|------|-----------|
| Econometrics | Two-Way Fixed Effects (TWFE) | Mundlak (1978) |
| Sports analytics | Massey rating / offense-defense decomposition | Massey (1997) |
| Psychometrics | Rasch model / 1PL IRT | Rasch (1960) |
| Game theory | Bradley-Terry with continuous margins | Bradley & Terry (1952) |
| Recommendation systems | Matrix factorization (rank 1) | Koren et al. (2009) |
| Coevolution | DECA latent dimensions (rank 1 case) | de Jong & Bucci (2006) |

This convergence is strong evidence that the additive model is the right starting point. The estimation is a simple alternating least-squares regression on the sparse observation matrix. Anchor opponents guarantee identification (Mundlak condition).

### When additivity breaks: RPS interactions

Starsector has rock-paper-scissors dynamics (kinetic vs shields, HE vs armor). The additive model misses these interactions. The natural extension is low-rank matrix factorization:

```
score(build_i, opponent_j) = α_i + β_j + u_i^T v_j + ε_ij
```

Where u_i (build archetype vector, rank r) and v_j (opponent vulnerability vector, rank r) capture the RPS structure. SVD/ALS on the sparse observation matrix gives both the additive and interaction terms. DECA (de Jong & Bucci 2006) showed that r = 3-5 typically captures most variance in game interaction matrices.

---

## 2. The Deconfounding Solution: Asymmetric Whole-History Rating

The most complete solution comes from game rating theory: **Whole-History Rating (WHR, Coulom 2008)** with the **Scarff (2020) asymmetric adaptation** from climbing route difficulty estimation.

### The model

- **Builds** have time-varying ratings: `α_i(t) ~ N(α_i(t-1), w² · Δt)` (Wiener process, w² > 0)
- **Opponents** have static ratings: `β_j` (w² = 0, fixed difficulty)
- Outcome model: `P(build wins) = σ(α_i(t) - β_j)` (Bradley-Terry)

### Why this works

The Wiener process prior on builds encodes the assumption that build quality changes smoothly over time (optimizer convergence). The static prior on opponents encodes the assumption that opponent difficulty is constant. Joint MAP estimation naturally deconfounds the two:

- If later builds score higher against ALL opponents → build quality improved (α increases)
- If later builds score higher against SPECIFIC opponents → those opponents were easier (β decreases for those opponents)

### Estimation

Newton-Raphson per entity, exploiting block-tridiagonal Hessian structure for builds (one time-point per trial). Computational cost: O(total_matchups) per iteration, ~5-10 iterations to convergence. At 200 builds × 10 opponents = 2000 matchups, this runs in < 10ms.

### Implementation path

- `whole-history-rating` Python package (pip), or
- `climbing-ratings` package (natively supports asymmetric w²), or
- Direct implementation (~100 lines of NumPy for the Newton solver)

### Validation from simulation

Our simulation showed:
- Raw Elo: ρ(Elo, true difficulty) = 0.024 with improving builds (confounded)
- With static builds: ρ = 0.95 (deconfounded)

WHR should recover the static-build performance even with improving builds, because the Wiener process absorbs the trend.

---

## 3. Cross-Subset Comparability: Three Complementary Mechanisms

### 3a. Anchor opponents (from IRT test equating)

**The psychometric consensus**: 3 anchor items at 30% of the test is sufficient for scale equating (exceeds Angoff's 20% rule). Anchors must have:
- High discrimination (steep IRT information curve)
- Medium difficulty (near the center of the build quality distribution)
- Low differential item functioning across epochs (stable parameters)

**Concurrent calibration** (preferred over separate + transform): fit one model to the entire sparse build × opponent matrix. Anchors provide connectivity; the model handles missing data naturally.

### 3b. TWFE / Massey decomposition (from causal inference + sports analytics)

Given the sparse observation matrix Y (builds × opponents), estimate:

```
Y_ij = α_i + β_j + ε_ij
```

via alternating projection:
1. β_j = mean(Y_ij - α_i) across all builds that faced opponent j
2. α_i = mean(Y_ij - β_j) across all opponents build i faced
3. Iterate until convergence

The α_i estimates are the schedule-adjusted build quality scores — comparable across different opponent subsets.

**Regularization**: Ridge penalty on both α and β (equivalent to Bayesian normal prior). Cross-validate on held-out anchor observations.

### 3c. LURE debiasing (from active testing)

When opponent selection is non-random (adaptive), the naive mean is biased. The LURE estimator (Kossen et al. 2021) corrects:

```
fitness_i = (1/M) Σ_m v_m · z_m

where v_m = 1 + ((N-M)/(N-m)) · (1/((N-m+1)·p_m) - 1)
```

N = total opponents (54), M = evaluated (10), p_m = selection probability for opponent m.

Since we control the selection mechanism, p_m is known exactly — a major advantage over recommendation system settings where propensities must be estimated.

---

## 4. Opponent Selection: Three-Phase Strategy

### Phase 1: Cold-start (trials 1-20) — D-optimal design

Before any evaluation data exists, select opponents that maximize diversity in feature space. Compute opponent feature vectors from game data (total flux, weapon DPS by type, armor, hull HP, speed — ~6-10 features). Use greedy forward selection maximizing det(X'X).

All early builds face the **same** opponents for fair comparison. Cost: O(10 × 54 × p²) = microseconds.

### Phase 2: Burn-in (trials 20-50) — Fixed set + statistics accumulation

Continue evaluating the same 10 opponents. Accumulate:
- Per-opponent scores in ScoreMatrix (TWFE accumulator)
- Discriminative power: |ρ(z_scores_opp, overall_fitness)|
- The build × opponent score matrix (sparse)

After 50 builds, fit rank-3 ALS matrix completion to predict missing entries.

### Phase 3: Adaptive selection (trials 50+) — Active testing

For each new build:
1. **Force overlap** with incumbent's opponents (SMAC insight): 3-5 of the incumbent's opponents guarantee direct comparability
2. **Active testing**: select remaining opponents by highest predictive variance from the matrix completion model
3. **ε-greedy exploration**: with probability ε ≈ 0.1, select uniformly at random (ensures coverage)
4. **LURE debias** the resulting scores

### Coverage guarantee

With ε = 0.1 and 7 adaptive slots per build, ~0.7 opponents are randomly selected per trial. Over 200 trials, this covers ~140 random selections across 54 opponents — each opponent is seen ~2.6 times via exploration alone, plus concentrated evaluations from the active testing criterion. The SVD of the matrix completion model identifies which opponents contribute unique information, ensuring exploitation targets informative opponents.

---

## 5. Opponent Weighting: Not Elo, but Model-Derived

### Why raw Elo fails (confirmed by simulation)

Our simulation showed ρ(Elo, true difficulty) = 0.024 with improving builds. The confounding mechanism: as builds improve, all opponents' Elo drops, destroying the difficulty signal. This matches the IRT "Bolsinova variance inflation" warning — adaptive testing + dual parameter estimation creates systematic bias.

### Better alternatives (ranked by simplicity)

**Option A — TWFE-derived β_j weights** (simplest):
The opponent fixed effects β_j from the additive decomposition ARE the difficulty estimates, already deconfounded. Use softmax(β_j / temperature) as weights.

**Option B — WHR opponent ratings** (most principled):
The static opponent ratings from the asymmetric WHR model, which explicitly separates build improvement from opponent difficulty via the Wiener process prior.

**Option C — Nash/α-Rank weights** (game-theoretic):
Compute a full 54×54 stock-variant interaction matrix offline (one-time cost). Run α-Rank (Omidshafiei et al. 2019) or maxent Nash (Balduzzi et al. 2018) to get opponent importance weights. These weights reflect strategic importance (how much the opponent matters for the game meta), not just difficulty.

**Option D — SVD-derived importance** (from DECA):
The right singular vectors from SVD of the score matrix identify which opponents load on which latent dimensions. Weight opponents inversely proportional to their redundancy with already-selected opponents.

### Recommended: Option A for implementation, Option B as upgrade

Option A requires only the additive model we're already fitting. Option B is the principled upgrade if the additive model's deconfounding proves insufficient (testable by checking residual patterns).

---

## 6. Handling Intransitivity (RPS Dynamics)

### Detection

After accumulating the score matrix, compute SVD. If the top-1 singular value explains < 70% of variance, significant intransitivity exists. The remaining singular vectors represent the RPS dimensions.

### Fitness aggregation under intransitivity

Pure mean rewards specialists. Pure minimax is too conservative. The literature suggests:

**Trimmed mean** (coevolution consensus): Drop the worst 1-2 opponent scores before averaging. This is more robust than mean (doesn't reward exploiting one easy opponent) but less conservative than minimax (doesn't punish one bad RPS matchup).

**Weighted mean with Nash weights** (game-theoretic): Weight by Nash mixture importance. Strategically irrelevant opponents (that no rational player would pick) get zero weight.

**Multi-objective decomposition** (DECA): Project scores onto top-k SVD dimensions. Report a fitness vector, not a scalar. Use Optuna's multi-objective optimization (NSGA-II) to find Pareto-optimal builds. This is the most information-preserving approach but adds optimizer complexity.

### Recommended: Trimmed mean for now, multi-objective as Phase 7

The trimmed mean is a one-line change to the aggregation function. Multi-objective requires rethinking the Optuna integration but aligns with the Phase 5 doc's mention of "multi-objective decomposition."

---

## 7. Practical Architecture

### Data flow

```
New build arrives
    │
    ├─ Select 10 opponents (Phase 1/2/3 strategy above)
    │
    ├─ Evaluate in parallel via instance manager
    │
    ├─ Record raw scores in sparse matrix
    │
    ├─ Record in ScoreMatrix (TWFE accumulator)
    │
    ├─ Update TWFE model (alternating projection, ~1ms)
    │   ├─ α_i = build quality (schedule-adjusted)
    │   └─ β_j = opponent difficulty (deconfounded)
    │
    ├─ If adaptive selection was used: apply LURE debiasing
    │
    ├─ Report α_i to Optuna as the trial value
    │
    └─ Periodically (~every 50 trials):
        ├─ Re-fit ALS matrix completion (rank 3-5)
        ├─ Update SVD for opponent redundancy/importance
        ├─ Update discriminative power estimates
        └─ Optionally: re-fit WHR for publication-quality deconfounding
```

### Computational budget per trial

| Step | Cost | Method |
|------|------|--------|
| Opponent selection | ~100μs | D-optimal / active testing |
| Combat simulation | ~5 min | Starsector instances (dominant cost) |
| ScoreMatrix record | ~1μs | Append to accumulator |
| TWFE update | ~1ms | Alternating projection on sparse matrix |
| LURE debiasing | ~1μs | Arithmetic |
| ALS re-fit (periodic) | ~10ms | Rank-3 ALS on 200×54 sparse matrix |
| WHR re-fit (periodic) | ~10ms | Newton-Raphson |

All computational overhead is negligible compared to the 5-minute combat simulation.

---

## 8. What to Implement and in What Order

### Must-have (addresses both core problems)

1. **TWFE additive decomposition** — the α_i build quality estimates are the schedule-adjusted fitness values. This is a ~50-line module. Use α_i (not raw z-score mean) as the Optuna trial value. This single change solves cross-subset comparability.

2. **Anchor-first opponent ordering** — lock 3 high-discrimination opponents at steps 0-2 for early pruning. Our simulation showed this is directionally positive and risk-free.

3. **SMAC-style incumbent overlap** — when selecting opponents for a new build, always include the incumbent's opponents. This guarantees the TWFE model has direct build-vs-build comparisons through shared opponents.

### Should-have (improves signal quality)

4. **ALS matrix completion** — predict missing build × opponent scores. Enables active opponent selection and richer fitness estimation.

5. **Active opponent selection** — replace random selection with variance-maximizing selection from the matrix completion model, plus ε-greedy exploration for coverage. Apply LURE debiasing to correct for non-random selection.

6. **Trimmed mean aggregation** — drop worst 1-2 opponent scores. Handles RPS intransitivity without full multi-objective complexity.

### Nice-to-have (principled upgrades)

7. **Asymmetric WHR** — replace TWFE with full Wiener-process model for builds. Provides uncertainty estimates and handles non-linear build improvement trajectories.

8. **SVD-based opponent importance** — identify redundant opponents, weight by information content. Informs which opponents to retire from the active pool.

9. **Nash/α-Rank opponent weights** — game-theoretic importance weighting from offline stock-variant tournament.

---

## 9. Key References

### Rating systems & deconfounding
- Coulom (2008), "Whole-History Rating" — CGW. The core algorithm.
- Scarff (2020), "Estimation of Climbing Route Difficulty using WHR" — arXiv:2001.05388. Asymmetric WHR (time-varying climbers, static routes).
- Dangauthier et al. (2007), "TrueSkill Through Time" — NeurIPS. Batch smoothing variant.
- Minka et al. (2018), "TrueSkill 2" — Microsoft Research. Skill evolution bias.
- Glickman (2001), "Glicko-2" — uncertainty + volatility.

### Causal inference & comparability
- Mundlak (1978), "On the Pooling of Time Series and Cross Section Data" — Econometrica. TWFE identification.
- Athey et al. (2021), "Matrix Completion Methods for Causal Panel Data Models" — JASA. Nuclear norm regularization.
- Schnabel et al. (2016), "Recommendations as Treatments" — ICML. IPS debiasing for matrix factorization.

### Psychometrics & adaptive testing
- Kossen et al. (2021), "Active Testing: Sample-Efficient Model Evaluation" — ICML. LURE debiasing.
- Klinkenberg et al. (2011), "Computer Adaptive Practice of Maths Ability using a New Item Response Model for on the Fly Ability and Difficulty Estimation" — Computers & Education. Elo-IRT hybrid.
- Bolsinova et al. (2026), "Keeping Elo Alive" — variance inflation warning.
- Angoff (1984), anchor item guidelines.
- Kolen & Brennan (2014), "Test Equating, Scaling, and Linking" — textbook.

### Sports analytics
- Massey (1997), "Statistical Models Applied to the Rating of Sports Teams" — offense-defense decomposition.
- Colley (2002), "Colley's Bias Free College Football Ranking Method" — schedule-adjusted ranking.
- Bradley & Terry (1952), "Rank Analysis of Incomplete Block Designs" — the foundational model.

### Active learning & bandits
- Kossen et al. (2022), "Active Surrogate Estimators" — NeurIPS. Follow-up to active testing.
- Hutter et al. (2011), "SMAC" — LION. Intensification/instance selection.
- Lopez-Ibanez et al. (2016), "irace" — Operations Research Perspectives. F-race.
- Russo & Van Roy (2014), "Information-Directed Sampling" — NeurIPS.
- Hastie et al. (2015), "Matrix Completion and Low-Rank SVD via Fast ALS" — JMLR.

### Coevolution & game theory
- de Jong (2007), "IPCA" — Evolutionary Computation. Informative test identification.
- de Jong & Bucci (2006), "DECA" — GECCO. SVD-based dimension extraction.
- Balduzzi et al. (2018), "Re-evaluating Evaluation" — NeurIPS. Nash averaging.
- Omidshafiei et al. (2019), "α-Rank" — Scientific Reports. Evolutionary game evaluation.
- Bucci (2002), "A Mathematical Framework for the Study of Coevolution" — FOGA.

---

## A2 — superseded by Phase 5D (2026-04-18)

The original Phase 5A A2 stage — a scalar control variate `α̂_i − β̂_cv · (h_i − h̄)` using only `composite_score` — was replaced in Phase 5D by empirical-Bayes shrinkage over a 7-dim pre-matchup covariate vector (fusion paradigm: α̂_TWFE and γ̂ᵀX are treated as noisy measurements of the same latent α and combined by Bayes rule, never subtracted). The A1 TWFE theory in this document is unchanged. See `docs/reference/phase5d-covariate-adjustment.md` for the fusion derivation and `docs/specs/28-deconfounding.md` §EB Shrinkage (A2′) for the normative spec.
