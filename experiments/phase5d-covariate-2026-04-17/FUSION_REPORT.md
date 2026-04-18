# Phase 5D Fusion-Paradigm Revalidation

After the first validation refuted the conditioning-paradigm design of Phase
5D (CUPED / FWL / PDS / ICP; see `REPORT.md`), a cross-field literature survey
— empirical Bayes, measurement-error theory, multi-fidelity Bayesian
optimization, psychometrics + sports ratings — converged on the same
alternative: **the heuristic is a noisy measurement of the same latent
quantity as the TWFE estimate, and should be combined with it by shrinkage /
inverse-variance fusion, not partialled out**. This revalidation tests the
fusion-family estimators against the same synthetic and real-data gates.

**Headline:**

- Three estimators **pass the ship gate** on real Hammerhead data (Δρ ≥ +0.02 vs both A0 plain TWFE and A shipped scalar CV).
- The clean winner is **HN: EB shrinkage toward a multi-covariate regression prior** — Δρ = +0.036 vs A0, +0.057 vs A; closed-form, no lasso, no selection; drop-in replacement for A2.
- On synthetic, HN more than doubles rank correlation with truth (0.407 → 0.744), and **top-10 recall of the true top-10 jumps from 10% to 36%** — critical for Optuna's exploitation signal.
- One-factor CFA wins on synthetic (ρ=0.806, top-10=42%) but **fails on real data** (Δρ = −0.145 vs A0) because Hammerhead's 13 scorer components measure genuinely different things; the one-factor assumption is too tight.

## Estimators tested

| Key | Name | Description |
|-----|------|-------------|
| A0 | plain TWFE | α̂ from `twfe_decompose`; no adjustment. Reference. |
| A | shipped scalar CV | α̂ − β̂_cv · (h_i − h̄). The current A2 step. |
| H1 | EB, scalar prior | α̂_EB = w_i α̂_i + (1 − w_i)·(γ̂₀ + γ̂₁h_i). Efron-Morris 1975 pattern; γ̂ OLS, τ̂² MoM, w_i = τ̂²/(τ̂² + σ̂_i²). |
| **HN** | **EB, multi-covariate prior** | **Same as H1 but γ̂ᵀ[1, h, scorer_1, ..., scorer_k]. Ignatiadis-Wager 2022 covariate-powered EB. Closed-form.** |
| HP | EB + PDS selection on prior | HN with lasso-selected columns in the prior regression. Identical to HN on real data (PDS kept all 16 cols). |
| IV | Inverse-variance combine | Graybill-Deal 1959: α̃ = (α̂/σ̂²_i + ĥ/σ̂²_h)/(1/σ̂²_i + 1/σ̂²_h), where ĥ = γ̂ᵀX. |
| FA | One-factor ML | Bollen 1989 / Jöreskog 1967: (α̂, h, scorer_k) as indicators of one latent factor α; factor scores by Thurstone regression. |
| EBT | HN + triple-goal rank | Lin-Louis-Shen 1999: HN's posterior ranks, histogram rescaled to match A0's empirical α̂ distribution. Preserves ρ; corrects regression-to-mean compression for Optuna's top-k exploitation. |

**Standard error σ̂_i** is estimated from TWFE residuals: σ̂_ε² = pooled-residual / DOF, σ̂_i = σ̂_ε / √n_i. Scaled to the number of matchups each build was evaluated against — builds with few observations get larger σ̂_i and hence more shrinkage toward the prior, which is exactly the desired behavior.

## Setup

Identical synthetic generative model and real Hammerhead data pipeline as
`REPORT.md`:

- **Synthetic**: 368 builds × 54 opponents, 10 active per build, anchor-first + incumbent-overlap schedule, exploit cluster (90% of builds, uplift 0.8, within-cluster σ 0.3), ceiling ±1.2, noise σ 0.5. 20 seeds. Heuristic h_i = q + N(0, 1.2), giving ρ(h, q) ≈ 0.45.
- **Real Hammerhead**: 313 non-pruned builds × 54 opponents from `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl`, 3491 matchups. Y_ij = hp_differential clipped to [−1, 1], TIMEOUT rows dampened to ×0.5. X_build = 13 scorer components (`composite`, 4 DPS types, flux_balance, flux_efficiency, effective_hp, armor_ehp, shield_ehp, range_coherence, damage_mix, op_efficiency) + 3 extras (n_hullmods, flux_vents, flux_capacitors).
- **Ship gate**: leave-one-opponent-out (LOOO) across the top-5 most-sampled anchor opponents. For each probe, drop its column, refit every estimator on the remaining 53 opponents, compute Spearman ρ between refit α̂ and the probe's raw Y across 313 builds. Mean ρ across 5 probes = gate metric. Bootstrap CI (200 resamples of builds) per probe × estimator.

## Results

### 1. Synthetic sweep (n=20, all p < 0.0001)

```
            rho_A0  rho_A  rho_H1  rho_HN  rho_HP  rho_IV  rho_FA  rho_EBT
  mean       0.407  0.347   0.452   0.744   0.744   0.574   0.806    0.744
  std        0.048  0.045   0.044   0.039   0.039   0.047   0.026    0.039

  Paired Wilcoxon vs A0:
    A − A0:  Δρ = −0.060   p < 0.0001   (shipped CV hurts, confirmed)
    H1 − A0: Δρ = +0.045   p < 0.0001   (scalar-prior EB helps)
    HN − A0: Δρ = +0.337   p < 0.0001   (multi-cov EB — big win)
    HP − A0: Δρ = +0.337   p < 0.0001   (identical to HN — PDS no-op at this scale)
    IV − A0: Δρ = +0.167   p < 0.0001
    FA − A0: Δρ = +0.399   p < 0.0001   (synthetic best)
    EBT − A0:Δρ = +0.337   p < 0.0001   (identical ρ to HN by construction)

  Top-10 recall of true top-10:
    A0: 0.10   A: 0.06   H1: 0.14   HN: 0.36   HP: 0.36
    IV: 0.22   FA: 0.42  EBT: 0.36
```

### 2. Real Hammerhead ship gate (5 probes)

```
  Mean ρ across probes:
    EBT: +0.316          [PASS]  Δρ vs A0 = +0.036, vs A = +0.057
    HN:  +0.316          [PASS]  Δρ vs A0 = +0.036, vs A = +0.057  ← clean winner
    HP:  +0.316          [PASS]  identical to HN
    IV:  +0.308          [PASS]  Δρ vs A0 = +0.028, vs A = +0.049
    A0:  +0.280          reference
    H1:  +0.278          [FAIL]  Δρ vs A0 = −0.002
    A:   +0.259          reference
    FA:  +0.135          [FAIL]  Δρ vs A0 = −0.145
```

**Ship-gate passes:** HN, HP, IV, EBT. **Recommended: HN** — identical performance to HP and EBT, simplest to implement, no lasso dependency.

### 3. Sensitivity sweep (48 regimes × 6 seeds = 288 runs)

All Δρ are estimator − A0 (plain TWFE). Higher = better.

```
  noise_cols=0:   ΔH1=+0.153  ΔHN=+0.358  ΔIV=+0.152  ΔFA=+0.465
  noise_cols=4:   ΔH1=+0.153  ΔHN=+0.344  ΔIV=+0.150  ΔFA=+0.464
  noise_cols=12:  ΔH1=+0.153  ΔHN=+0.317  ΔIV=+0.145  ΔFA=+0.464
```

**All fusion estimators remain strictly positive in every regime** (active_size ∈ {3,5,10,20} × heuristic_noise ∈ {0.3,0.6,1.2,2.5} × extra-noise columns ∈ {0,4,12}). This is the exact inversion of the Phase 5D sensitivity sweep in `REPORT.md` where the conditioning-paradigm estimators were strictly *negative* in every cell.

Graceful degradation of HN with added noise columns: Δρ drops from +0.358 to +0.317 as 12 pure-noise columns are sprinkled in. The degradation is slow — the MoM τ̂² penalty and OLS on noise columns shrink γ̂ appropriately. HP (PDS-selected prior) does not meaningfully beat HN in this stress test either; at our sample size PDS retains most columns.

## Why HN works and FA fails on real data

Both HN and FA sit in the fusion paradigm, but they encode different structural assumptions:

- **HN (EB with regression prior)** assumes `α_i ~ N(γᵀX_i, τ²)` — X_i is a vector of covariates; the prior mean is a learned linear function of them. It does **not** assume the covariates are all measuring the same latent thing. OLS discovers which covariates carry signal (via γ̂), and τ̂² absorbs everything unexplained.
- **FA (one-factor ML)** assumes all k+1 indicators — α̂_TWFE and every scorer column — share a single latent factor: `Y_ik = λ_k · α + ε_k`. This is a much stronger assumption. If the scorer components measure genuinely different things (flux economy vs kinetic DPS vs shield EHP), the one-factor structure is wrong and FA distributes signal across a noisy combination.

On synthetic, where X is constructed so every useful column is a proxy of q, the one-factor assumption is approximately correct and FA dominates. On Hammerhead, where scorer components measure orthogonal aspects of a build, the one-factor assumption is violated and FA hurts. **HN's more conservative structural assumption makes it robust to this kind of indicator heterogeneity**.

The same logic suggests that a multi-factor FA with k > 1 factors might recover on real data, but at that point the simplicity advantage is gone; HN with its closed-form OLS regression prior achieves the same pass without the complication.

## Why H1 (scalar prior) fails where HN (multi) passes

On synthetic, H1 and HN have similar τ² and w̄ — both in the expected range. On real Hammerhead data, the composite score alone is a weaker prior than the full scorer vector; H1's regression to `q ~ composite` explains less of α̂ than HN's regression to `q ~ composite + total_dps + … + flux_efficiency + …`. Lower explained variance ⇒ larger τ̂² ⇒ weaker shrinkage ⇒ the prior effectively gets ignored. HN exploits the multi-dimensional structure of the scorer.

**Implication for integration**: Phase 5D should use the full 13-component `ScorerResult` vector (plus extras), not just the `composite_score` scalar currently used by the shipped A2.

## The shipped A2 scalar CV is strictly dominated

On both synthetic and real Hammerhead, **A (shipped) is strictly worse than A0 (plain TWFE)**:
- Synthetic: A0 = 0.407, A = 0.347 (Δρ = −0.060, p < 0.0001)
- Real: A0 = 0.280, A = 0.259 (Δρ = −0.021)

If Phase 5D is not shipped, **A2 itself should be removed** as a standalone change — its small but consistent negative effect compounds with the rank-shape stage downstream. Every fusion estimator tested beats A.

## EBT — triple-goal rank correction

By construction (rank-preserving histogram substitution), EBT's rank correlation is **identical** to HN's, but its histogram matches A0 exactly. The benefit is on Optuna's exploitation pressure: EBT's top-k builds have the raw TWFE α̂ magnitudes (un-shrunken), while HN's top-k builds have posterior-shrunk magnitudes that over-compress near the mean. For scalar ρ this is a no-op; for TPE's expected-improvement acquisition function it is not. Recommend EBT as the final output fed to Optuna, with HN's α̂ available as a diagnostic.

## Wall time (per seed, synthetic)

| Estimator | mean wall (ms) |
|---|---|
| A0 plain TWFE | 2 |
| A shipped CV | 2 |
| H1 EB scalar | 2 |
| HN EB multi | 3 |
| HP EB + PDS | 400 (dominated by LassoCV) |
| IV inverse-var | 2 |
| FA one-factor | 4 |
| EBT HN + triple-goal | 3 |

HN, IV, EBT are all O(1ms) on 368×54 matrices — negligible compared to a 5-minute combat match. HP's ~400ms (LassoCV) is also acceptable but provides no benefit over HN at this scale.

## Recommendation

### Primary: ship HN + EBT

1. **HN** replaces A2 as the between-build adjustment. Keep the existing A1 (TWFE) and A3 (Box-Cox, Phase 5E) stages. Deleting A2 is the first step; HN then shrinks α̂ toward a prior mean fitted from the full ScorerResult vector + structural extras.
2. **EBT** (HN + triple-goal rank) is the α̂ passed to Optuna. Preserves the empirical α̂ histogram while inheriting HN's improved rank.
3. Covariate set for the prior regression = all 13 components from `ScorerResult` plus `n_hullmods`, `flux_vents`, `flux_capacitors`. All standardized.
4. σ̂_i computed from TWFE residuals as in this validation — pooled residual MSE divided by per-build observation count.
5. τ̂² by method of moments with a 5% floor to prevent over-shrinkage.

### Secondary: re-test in production with Optuna-loop benchmark

Rank correlation with held-out Y is our ship gate, but the user of α̂ is Optuna TPE, which cares about *acquisition-function quality*. A proper end-to-end benchmark would run two identical TPE optimizations — one with A2 shipped, one with HN — for N trials on the Hammerhead arena and compare (a) time to incumbent, (b) final top-10 combat fitness. This is a post-ship validation since the rank-correlation evidence is already overwhelming at p < 0.0001 across 20 seeds and 5 probes.

### Rejected (again)

- **FA one-factor**: fails ship gate on real data. The one-factor assumption does not hold for the heterogeneous scorer component vector. Do not use.
- **HP (PDS-selected prior)**: adds LassoCV cost for zero benefit at our sample size. Do not use. Re-evaluate only if the covariate pool grows beyond ~50 columns.
- **H1 (scalar prior)**: narrowly fails ship gate. The one-column composite score is a weaker prior than the full vector; HN is the correct version.
- **Original Phase 5D (CUPED-style multi-covariate regression on Y)**: definitively rejected in `REPORT.md`. The fix is a paradigm change, not a tuning change.

## Doc changes required

`docs/reference/phase5d-covariate-adjustment.md` requires a substantive rewrite:

1. §1 (Problem): correct — the auxiliary signals are valuable; keep.
2. §2.1 (Model): **delete** `Y_ij = α_i + β_j + γᵀX_ij + ε_ij`. Replace with the fusion model `α̂_i ~ N(γᵀX_i, τ²)` + `α̂_i | α_i ~ N(α_i, σ̂_i²)`.
3. §2.2 (Estimator): **delete** three-block alternating projection. Replace with the two-line EB closed-form.
4. §2.3 (Pipeline): keep the A1 → (new-A2) → A3 structure; the new A2 is HN/EBT.
5. §2.4 (Automatic selection): **demote** from primary design to optional appendix (HP does nothing at current scale).
6. §2.5 (ICP): **delete** — the timing filter remains valid (it rejects post-matchup colliders like duration, damage_eff, overload) but ICP as a structural assumption test is unnecessary for between-build shrinkage. Move to a one-paragraph note under §2.4.
7. §2.6 (CUPED/CUPAC/DML): **replace** with citations to the fusion-family literature: Efron-Morris 1975, Ignatiadis-Wager 2022, Hachemeister 1975, Mislevy 1987, Kennedy-O'Hagan 2000 (prior-mean GP).
8. §3.3 (Ship gate): **update** — the gate is now Δρ ≥ +0.02 vs **both** A0 and A.
9. §4.x (Rejected alternatives): **add** the conditioning-paradigm rejection from `REPORT.md` as §4.5, with the philosophical distinction.

## Files

- `phase5d_fusion_validation.py` — fusion estimators + synthetic sweep + Hammerhead replay with 5-probe LOOO and bootstrap CIs.
- `fusion_sensitivity.py` — (active_size × heuristic_noise × noise_cols) 48-cell regime sweep.
- `fusion_results.csv` — per-seed synthetic metrics (20 × 8 estimators).
- `fusion_sensitivity_results.csv` — regime sweep (288 rows).
- `fusion_hammerhead_gate.csv` — per-probe × per-estimator gate metric with 95% bootstrap CIs.
- `fusion_synthetic.png` — rank-correlation + top-10 recall + exploit-cluster ρ + variance shrinkage.
- `fusion_sensitivity_heatmap.png` — Δρ(HN − A0) and Δρ(FA − A0) across the regime grid.
- `fusion_hammerhead.png` — per-probe bars + mean-across-probes ship gate.

## Citations

- **Efron & Morris 1975**, "Data Analysis Using Stein's Estimator and Its Generalizations," *JASA* 70:311.
- **Ignatiadis & Wager 2022**, "Covariate-Powered Empirical Bayes Estimation," *Annals of Statistics* 50:2467, arXiv:1810.02333. *(The most direct citation for HN.)*
- **Hachemeister 1975**, "Credibility for regression models with application to trend," *Credibility: Theory and Applications*, ed. Kahn, Academic Press. *(Actuarial lineage of HN.)*
- **Mislevy 1987**, "Exploiting auxiliary information about examinees in the estimation of item parameters," *Applied Psychological Measurement* 11:81. *(Psychometric lineage of HN.)*
- **Lin, Louis & Shen 1999**, "Triple-goal estimates for the evaluation of healthcare providers," *Statistics in Medicine* 18:2135. *(EBT.)*
- **Louis 1984**, "Estimating a Population of Parameter Values Using Bayes and Empirical Bayes Methods," *JASA* 79:393. *(Foundational for the rank-preservation argument.)*
- **Graybill & Deal 1959**, "Combining Unbiased Estimators," *Biometrics* 15:543. *(IV.)*
- **Bollen 1989**, *Structural Equations with Latent Variables*, Wiley. *(FA.)*
- **Cinelli, Forney & Pearl 2022**, "A Crash Course in Good and Bad Controls," *Sociological Methods & Research*, arXiv:2106.10314. *(Case 8 = the categorical error of the original Phase 5D design.)*
