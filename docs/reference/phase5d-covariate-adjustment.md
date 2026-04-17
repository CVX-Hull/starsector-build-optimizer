# Phase 5D — Covariate-Adjusted TWFE

> **Status**: Research complete, implementation planned. See `docs/reference/implementation-roadmap.md` for the roadmap slot.

Design for incorporating auxiliary per-matchup signals into the fitness estimate *without hand-tuned composite weights and without per-variable causal classification*. The shipped pipeline (`src/starsector_optimizer/combat_fitness.py` → `deconfounding.py` A1 → `optimizer.py` A2/A3) currently uses only the scalar `combat_fitness` outcome and ignores the rest of what the harness emits. This document specifies a bitter-lesson-compliant extension that folds those signals into the additive decomposition via OLS with **automatic two-stage covariate selection**: Stage 1 is a mechanical timing filter (pre-matchup vs post-matchup — one operational rule, not per-variable judgment), Stage 2 is post-double-selection lasso (Belloni, Chernozhukov & Hansen 2014) over the admissible set.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer pipeline. The stages as shipped are A1 TWFE decomposition → A2 single-channel control variate (heuristic) → A3 rank-shape-with-top-quartile-clamp. Phase 5D extends A1 to absorb multiple covariates; Phase 5E replaces A3 with Box-Cox warping. The two are orthogonal and compose cleanly. See `docs/reference/implementation-roadmap.md` for the full Phase 5 overview and `docs/reference/phase5a-deconfounding-theory.md` for the TWFE foundation.

---

## 1. Problem

The combat harness emits per-matchup auxiliary signals beyond the scalar `combat_fitness` score:

- `damage_dealt`, `damage_taken` (and hence `damage_efficiency = dealt / taken`)
- `overload_count_differential` (player minus enemy)
- `duration_seconds`
- `hp_differential`
- `armor_fraction_remaining`
- `peak_time_remaining`, `disabled_weapons_count`, `flameouts_count`

These are parsed in `src/starsector_optimizer/result_parser.py` but currently ignored by the fitness aggregation. The original Phase 5D proposal (see `docs/reference/phase5c-opponent-curriculum.md` §4.4 and the 2026-04-13 version of this roadmap) was to fold them into a **hand-weighted composite sum** alongside `combat_fitness`. That proposal was rejected on bitter-lesson grounds — choosing the weights is exactly the kind of human-knowledge injection Sutton 2019 warns against.

The question this doc answers: **can we use these signals without human weights?** The literature gives an unambiguous yes.

---

## 2. Accepted Design — Covariate-Adjusted TWFE

### 2.1 Model

Extend the current TWFE decomposition from

```
Y_ij = α_i + β_j + ε_ij
```

to

```
Y_ij = α_i + β_j + γᵀ X_ij + ε_ij
```

where `X_ij ∈ ℝ^k` is a per-matchup covariate vector and `γ ∈ ℝ^k` is the OLS projection of `Y` onto `X` after removing the build and opponent fixed effects. The Frisch-Waugh-Lovell theorem (Frisch & Waugh 1933, *Econometrica* 1(4); Lovell 1963, *JASA* 58(304)) guarantees that α̂ recovered this way equals α̂ from the joint OLS of the full model — the three-block alternating projection below converges to the same point as solving the full least-squares system directly.

### 2.2 Estimator

Three-block alternating projection, each block being a closed-form least-squares update:

```
Given α, β:   γ ← (X^T X + ridge · I)^{-1} X^T (Y − α1^T − 1β^T).flatten()
Given β, γ:   α_i ← mean_j(Y_ij − β_j − γ^T X_ij)   over observed matchups
Given α, γ:   β_j ← mean_i(Y_ij − α_i − γ^T X_ij)   over observed matchups
```

Iterate until `||α^(t+1) − α^(t)||_∞ < 10^{-4}` (or fixed `n_iters`). Same convergence properties as the existing A1 alternating projection in `twfe_decompose()`; the extra γ block adds a single small matrix solve per outer iteration. At n ≈ 10,000 observed matchups and k ≤ 8 covariates, this is sub-millisecond per solve.

### 2.3 Where it lives in the pipeline

This extension replaces **A1 + A2** with a single covariate-adjusted A1:

```
Current:   A1 TWFE(Y)         →  α_i
           A2 α_i − β̂_cv · (heuristic_i − heuristic_mean)   →  cv_fitness_i
           A3 rank-shape(cv_fitness)                        →  reported_fitness

Proposed:  A1' TWFE(Y, X)                     →  α_i (covariate-adjusted)
           A3 rank-shape(α_i)                  →  reported_fitness
```

The scalar heuristic control-variate (current A2, coefficient `_cv_beta` at `optimizer.py:787`) becomes column 0 of `X_ij`. Its OLS coefficient is part of γ and is learned, not fit separately. Any additional covariates become columns 1 through k−1 of X.

### 2.4 Automatic covariate selection — two-stage procedure

The single substantive question is **which signals go into X**. Blindly including every harness output is wrong: if a covariate is a *mechanical consequence* of build quality (a strong build trivially produces higher `damage_efficiency` against a weak opponent), conditioning on it partials out the very α_i signal we want to recover. Econometrics calls this **"bad control"** (Cinelli, Forney & Pearl 2022, "A Crash Course in Good and Bad Controls"). Four independent literature surveys (econometrics, causal ML, Bayesian sparsity, applied experimentation) converged on the same answer: **no purely-predictive selector detects bad controls** — lasso, horseshoe, SSVS, projpred, and BART will all dutifully retain a collider that correlates with Y. The bad-control decision must be made by the **candidate set**, not the **selector**.

The design below pushes the structural decision into a single mechanical rule (Stage 1), then lets compute handle the rest (Stage 2). No per-variable judgment required.

#### Stage 1 — Timing filter (mechanical; bitter-lesson compliant)

Partition every candidate signal by **when its value is known**:

| Signal | Origin | Stage 1 |
|---|---|---|
| `heuristic_i` (scalar) | pre-matchup (build-only) | ✓ admissible |
| Scorer component breakdown (flux economy, burst DPS, sustained DPS, effective armor/shields) | pre-matchup (from `ScorerResult`) | ✓ admissible |
| Opponent-side heuristic | pre-matchup (opponent-only) | ✓ admissible |
| Opponent identity features (hull size class, pool role) | pre-matchup | ✓ admissible |
| Schedule-position features (trial number, incumbent overlap count) | pre-matchup | ✓ admissible |
| `duration_seconds` | post-matchup | ✗ excluded |
| `peak_time_remaining` | post-matchup | ✗ excluded |
| `damage_efficiency` | post-matchup | ✗ excluded |
| `overload_count_differential` | post-matchup | ✗ excluded |
| `hp_differential` | post-matchup | ✗ excluded (already the outcome) |
| `armor_fraction_remaining` | post-matchup | ✗ excluded |

This is **one operational decision** ("is the value knowable before the matchup begins?"), not per-variable causal reasoning. It scales to any future harness output without elicitation and is the strategy every production A/B-testing system uses — CUPED (Deng et al. 2013), MLRATE (Guo et al. 2021), DoorDash CUPAC all restrict to pre-treatment features by policy, not case-by-case analysis. Post-matchup signals of scientific interest should be modeled as separate outcomes (a multi-objective setup, out of scope here), never as covariates of α_i.

#### Stage 2 — Automatic selection inside X_pre via post-double-selection lasso

With the timing rule holding the door, X_pre can be large (~10-20 channels) without bad-control risk. Within that pool, selection is data-driven via **post-double-selection lasso** (Belloni, Chernozhukov & Hansen 2014, *Review of Economic Studies* 81(2), arXiv:1201.0220; panel extension Belloni, Chernozhukov, Hansen & Kozbur 2016, arXiv:1312.7186):

1. Partial out fixed effects via within-transformation (demean each column of Y and X_pre by build i and opponent j — this is the FWL residualization already implicit in the alternating projection).
2. Run lasso `Ỹ ~ X̃_pre` with λ chosen by K-fold CV → selects set `S_Y` predictive of outcome.
3. For each column k in X_pre, run lasso `X̃_k ~ X̃_{-k}` → selects set `S_k` capturing which other covariates predict `X_k`.
4. Final adjustment set `S = S_Y ∪ (⋃_k S_k)` — the "post-double" union.
5. Refit the covariate-adjusted TWFE using only columns in S.

The union step is what makes this **double** selection: a covariate that predicts `X_k` but not Y directly must still be retained, because omitting it would bias γ̂_k through the correlation structure of X_pre itself. Plain single-lasso-on-Y is known to under-select and bias γ in panel settings — Belloni-Chernozhukov-Hansen prove PDS restores √n-rate inference on γ̂ under mild sparsity.

Cost: at ~10k-25k observations and k ≤ 20, Stage 2 is ~200 ms in scikit-learn (`LassoCV` + per-column refits). No GBM, no neural nets — plain L1 is sufficient at this scale.

#### Stage 3 (optional) — Double ML with cross-fitting if k grows large

If X_pre eventually exceeds ~50 channels (e.g., we start featurising opponent hulls with dense aspect vectors), Stage 2 risks selection instability. Replace with **Double/Debiased ML with multiway-cluster cross-fitting** (Chernozhukov et al. 2018, *Econometrics Journal* 21(1), arXiv:1608.00060; multiway-cluster extension Chiang, Kato, Ma & Sasaki 2022, *JASA*, arXiv:1909.03489):

- K-fold cross-fitting over matchups, blocked on both build and opponent to respect crossed dependence.
- On each held-out fold, estimate γ via Neyman-orthogonal residual-on-residual regression; fit `E[Y|X]` and `E[X_k|X_{-k}]` nuisances with lasso or GBM on the remaining folds.
- Aggregate γ across folds.

This is insurance, not required. The current Starsector harness has <20 natural pre-matchup channels; Stage 2 PDS is the right tool. Promote to Stage 3 only if diagnostics show λ-selected S varying dramatically across CV folds.

### 2.5 Optional diagnostic — Invariant Causal Prediction

The timing filter is the primary bad-control defense but relies on the convention that "pre-matchup ⇒ causally prior." For edge cases — a pre-matchup feature computed from aggregated past-match data whose distribution shifts with opponent — an **experimental** invariance check is available.

**Invariant Causal Prediction (ICP; Peters, Bühlmann & Meinshausen 2016, *JRSS-B* 78(5), arXiv:1501.01332):** Treat each opponent j as an environment. True causal parents of Y produce residuals `ε̂_S = Ỹ − X̃_S γ̂_S` whose distribution is **invariant across environments**. Mediators and colliders fail invariance because j directly perturbs them. Procedure:

1. For each candidate subset `S ⊂ X_pre`, fit the covariate-adjusted TWFE using only columns in S.
2. Test `H_0: residuals have the same distribution across all opponents` via a multi-sample Levene test (Bonferroni-corrected across S candidates).
3. The **accepted set** is the intersection of all non-rejected S — a confidence set of causal parents.

Any column retained by PDS-lasso but rejected by ICP is a candidate bad control worth manual inspection. Any column retained by both is high-confidence safe. Cost: exhaustive 2^k at k ≤ 15 is ~seconds; for larger k, use `nonlinearICP`'s greedy variant (Heinze-Deml, Peters & Meinshausen 2018, arXiv:1706.08576).

This is a **diagnostic**, not the primary selector. Ship-gating remains driven by held-out empirical lift (§3.3), not ICP agreement. ICP earns its keep when PDS selection includes a post-hoc-suspicious column and a principled rejection criterion is needed.

### 2.6 Connection to CUPED / CUPAC / DML

The design is the experiment-design community's CUPED pattern (Deng, Xu, Kohavi & Walker 2013, WSDM), where A/B experiments reduce variance by subtracting a regression-projected pre-experiment covariate — Bing reports ~50% variance reduction. DoorDash's CUPAC (Tang et al. 2020) extends CUPED to an ML-predicted scalar covariate. MLRATE (Guo et al. 2021, arXiv:2106.07263) generalises to arbitrary ML learners with cross-fitting. The modern semiparametric treatment is Double/Debiased ML (Chernozhukov et al. 2018), which gives Stage 3 its theoretical grounding. All three systems restrict to pre-treatment features by convention — the Stage 1 timing filter above is that convention, made explicit. At our scale (~10k-25k observations, k ≤ 20) Stage 2 plain OLS+PDS is sufficient; Stage 3 cross-fitting is available when X_pre outgrows it.

---

## 3. Integration plan

### 3.1 Code changes

- **`src/starsector_optimizer/deconfounding.py`** — extend `twfe_decompose(Y, ...)` to `twfe_decompose(Y, X, ...)` with the γ block. `ScoreMatrix.record()` grows a `covariates: np.ndarray` argument. `ScoreMatrix.build_alpha()` and `opponent_beta()` unchanged signatures; γ and S accessible via new `score_matrix.gamma` / `score_matrix.selected_covariates` properties.
- **`src/starsector_optimizer/deconfounding.py`** (new function) — `post_double_selection(Y_resid, X_resid, cv_folds=5) → set[int]` implementing the Stage 2 PDS lasso. Uses `sklearn.linear_model.LassoCV`. ~40 lines.
- **`src/starsector_optimizer/optimizer.py`** — delete `_apply_control_variate`, `_refit_control_variate`, `_cv_beta`, `_cv_heuristic_mean`. Their role is absorbed by γ. Update `_finalize_build` to pass the full X_pre vector through to `ScoreMatrix.record()`. Decision on S runs once at finalization, cached until next `record()`. No structural change to A3 downstream.
- **`src/starsector_optimizer/models.py`** — add to `TWFEConfig`: `use_pds: bool = True` (gate the Stage 2 selector on/off for A/B diagnostics), `pds_cv_folds: int = 5`. Candidate column identifiers come from a constant `X_PRE_CANDIDATES` tuple in `deconfounding.py` (not a user-tuned list — it's the mechanical output of Stage 1 applied to `CombatResult` plus `ScorerResult`).
- **`src/starsector_optimizer/combat_fitness.py`** — expose all pre-matchup candidates from the existing `CombatResult` + `ScorerResult` plumbing. No new signal collection; only routing.
- **`pyproject.toml`** — add `scikit-learn` to the runtime dependency set if not already present (check `LassoCV`).
- **`docs/specs/28-deconfounding.md`** — extend to describe the γ estimation step, Stage 1 timing filter, Stage 2 PDS lasso, and the optional ICP diagnostic path.

Estimated code size: ~140 lines in `deconfounding.py` (90 for the covariate-adjusted alternating projection + 40 for PDS + 10 for Stage-1 helper), ~30 lines deleted from `optimizer.py`, ~20 lines net of model and spec updates. Order-of-magnitude 2–3 days including tests and spec sync.

### 3.2 Tests

- Unit: synthetic data with known α, β, γ — verify recovery tolerance (fixture analogous to existing `test_deconfounding.py::test_twfe_decompose_known_values`).
- Unit: OLS equivalence — verify three-block alternating projection converges to the same γ as a single joint OLS via `numpy.linalg.lstsq`.
- Unit: ridge effect — with k > n-ranks of X, γ should remain finite.
- Unit: Stage 2 PDS recovers the active set on synthetic data where S_true ⊂ X_pre is known, with high probability across 100 random seeds.
- Unit: Stage 2 PDS on fully-noise X returns S = ∅ (no false positives) with λ chosen by CV.
- Integration: ablation on `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl`. Baselines are (a) current A1+A2, (b) covariate-adjusted A1' without PDS (full X_pre), (c) covariate-adjusted A1' with PDS. Compare α̂ rank correlation against a held-out-matchup-based "true" α (re-evaluation against all 50 opponents for a ~20-build sub-sample). Ship only if (c) ≥ (b) ≥ (a), confirming PDS helps or is neutral.

### 3.3 Ship gate

Phase 5D is **gated on an empirical validation** against the 2026-04-17 Hammerhead evaluation log:

- Fit both the current A1+A2 pipeline and the proposed A1' on the log.
- Measure rank correlation with "true" α on a held-out evaluation set.
- Require Δρ ≥ +0.02 to ship. Δρ < 0 is a blocker (indicates bad-control leakage or ill-conditioned covariate selection).

This gate prevents shipping covariate adjustment that merely looks principled without being empirically useful.

---

## 4. Rejected alternatives — with rationale

### 4.1 Hand-weighted composite fitness — REJECTED

**What it was.** Compute `damage_efficiency`, `overload_differential`, `duration_normalized_damage` per matchup; sum into a weighted composite alongside the existing tiered `combat_fitness`; use the composite as the fitness scalar.

**Why rejected.** The weights have to be picked by humans, and they encode a prior about which combat behaviours indicate quality. This is the exact anti-pattern the bitter lesson names (Sutton 2019, "The Bitter Lesson"). The covariate-adjusted TWFE above is a strict improvement — the regression coefficients are OLS-derived.

### 4.2 Per-frame Java harness tracking — REJECTED

**What it was.** Extend `CombatHarnessPlugin.java` with per-frame accumulators for time-weighted flux, cumulative overload duration, engagement-distance trajectories, and time-to-first-hull-damage; fold into a richer composite.

**Why rejected.** Same pathology as 4.1, compounded — the new intermediate signals don't just need weights, they inject strategy taxonomy ("kiting vs brawling detection") into the reward signal. The match outcome primitives already collected (win/loss, HP differential, duration, timeouts, overload count) are the correct target variables; richer composites can be derived from them via this Phase 5D's OLS-coefficient adjustment without any new engineered channels.

See also `docs/reference/phase5c-opponent-curriculum.md` §4.4 for the original rejection context.

### 4.3 Multi-information-source Bayesian optimisation (MISO) — REJECTED

**What it was.** Treat each auxiliary signal as a separate information source of build quality; aggregate via multi-output GP + knowledge-gradient-per-cost acquisition (Poloczek, Wang & Frazier 2017, NeurIPS).

**Why rejected.** MISO's structural assumption is that each source gives an independent noisy estimate of the *same* objective (e.g. cheap simulator vs expensive simulator). Our auxiliary signals are *correlated side-channels*, not alternative estimators of build quality. Forcing them into the MISO framework requires declaring a mapping from each channel to "implied build quality" — which is itself a hand-weighted composite in disguise. In addition, adopting MISO would require replacing Optuna TPE with a custom GP/KG backend — a Phase-7-scale sampler rewrite for modest marginal gain.

### 4.4 Heteroscedastic-TWFE with learned σ²(X) — DEFERRED

**What it was.** Generalise the error model from `ε ∼ N(0, σ²)` homoscedastic to `ε_ij ∼ N(0, σ²(X_ij))` with learned variance function, then fit TWFE by GLS (weighted least squares).

**Why deferred, not rejected.** Heteroscedasticity is real (glass-cannon builds have bimodal outcomes; peer-Hammerhead matchups produce near-deterministic timeouts). But modelling σ²(X) is orthogonal to the auxiliary-signal question and is a second-order refinement. Revisit after the covariate-adjusted design above is in production and diagnostics show residual heteroscedasticity that matters. References for the revisit: Binois, Gramacy & Ludkovski 2018, *JCGS* 27(4), arXiv:1611.05902 ("Practical Heteroskedastic Gaussian Process Modeling for Large Simulation Experiments"); Kersting et al. 2007, ICML ("Most Likely Heteroscedastic GP Regression").

---

## 5. References

**TWFE + FWL foundations**
- Frisch & Waugh (1933), "Partial Time Regressions as Compared with Individual Trends," *Econometrica* 1(4): 387–401.
- Lovell (1963), "Seasonal Adjustment of Economic Time Series and Multiple Regression Analysis," *JASA* 58(304): 993–1010.

**Control variates (classical)**
- Rubinstein & Marcus (1985), "Efficiency of Multivariate Control Variates in Monte Carlo Simulation," *Operations Research* 33(3): 661–677.
- Nelson (1990), "Control Variate Remedies," *Operations Research* 38(6): 974–992.
- Glasserman (2004), *Monte Carlo Methods in Financial Engineering*, Springer, chap. 4.
- Szechtman (2003), "Control Variate Techniques for Monte Carlo Simulation," *Winter Simulation Conference*.
- Lin (2013), "Agnostic Notes on Regression Adjustments to Experimental Data," *Annals of Applied Statistics* 7(1): 295–318, arXiv:1208.2301.

**Automatic covariate selection (Stage 2 + Stage 3)**
- Belloni, Chernozhukov & Hansen (2014), "Inference on Treatment Effects after Selection among High-Dimensional Controls," *Review of Economic Studies* 81(2): 608–650, arXiv:1201.0220.
- Belloni, Chernozhukov, Hansen & Kozbur (2016), "Inference in High-Dimensional Panel Models With an Application to Gun Control," arXiv:1312.7186. *(PDS lasso extended to TWFE panels — the Stage 2 citation.)*
- Chernozhukov, Chetverikov, Demirer, Duflo, Hansen, Newey & Robins (2018), "Double/Debiased Machine Learning for Treatment and Structural Parameters," *Econometrics Journal* 21(1): C1–C68, arXiv:1608.00060.
- Chiang, Kato, Ma & Sasaki (2022), "Multiway Cluster Robust Double/Debiased Machine Learning," *JASA*, arXiv:1909.03489. *(DML with multiway clustering — the Stage 3 citation.)*
- Zhang & Zhang (2014), "Confidence Intervals for Low-Dimensional Parameters in High-Dimensional Linear Models," *JRSS-B* 76(1), arXiv:1110.2563. *(Desparsified lasso, used if honest CIs on α̂_i are needed.)*

**Invariance-based bad-control detection (Stage 2.5 diagnostic)**
- Peters, Bühlmann & Meinshausen (2016), "Causal Inference using Invariant Prediction: Identification and Confidence Intervals," *JRSS-B* 78(5), arXiv:1501.01332.
- Heinze-Deml, Peters & Meinshausen (2018), "Invariant Causal Prediction for Nonlinear Models," arXiv:1706.08576.
- Rothenhäusler, Meinshausen, Bühlmann & Peters (2021), "Anchor Regression: Heterogeneous Data Meet Causality," *JRSS-B*, arXiv:1801.06229. *(Softer sibling of ICP — future generalisation if strict invariance proves too tight.)*

**Applied / production practice**
- Deng, Xu, Kohavi & Walker (2013), "Improving the Sensitivity of Online Controlled Experiments by Utilizing Pre-Experiment Data" (CUPED), *WSDM*.
- Guo, Coey, Konutgan, Li, Schoener & Goldman (2021), "Machine Learning for Variance Reduction in Online Experiments" (MLRATE), *ICML*, arXiv:2106.07263.
- Tang, Zhou et al. (2020), "Improving Experimental Power through Control Using Predictions as Covariates" (CUPAC), DoorDash Engineering Blog.

**Bad-control theory + bitter lesson**
- Cinelli, Forney & Pearl (2022), "A Crash Course in Good and Bad Controls," *Sociological Methods & Research*.
- Shi, Veitch & Blei (2020), "Invariant Representations for Counterfactual-Invariant Representations," arXiv:2005.01643. *(Out-of-sample leakage test as a mediator diagnostic.)*
- Sutton (2019), "The Bitter Lesson."

**Rejected alternatives (for citation completeness)**
- Poloczek, Wang & Frazier (2017), "Multi-Information Source Optimization," *NeurIPS*. *(MISO — rejected §4.3.)*

---

## 6. See also

- `docs/reference/phase5a-deconfounding-theory.md` — TWFE decomposition theory (6-field literature synthesis).
- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B foundational research.
- `docs/reference/phase5c-opponent-curriculum.md` — anchor + incumbent opponent selection (Phase 5C, shipped).
- `docs/reference/phase5e-shape-revision.md` — A3 rank-shape revision (Box-Cox).
- `docs/reference/implementation-roadmap.md` — phase overview and status.
- `docs/specs/28-deconfounding.md` — implemented TWFE spec (to be extended when 5D ships).
- `src/starsector_optimizer/deconfounding.py`, `src/starsector_optimizer/optimizer.py` — production code to modify.
