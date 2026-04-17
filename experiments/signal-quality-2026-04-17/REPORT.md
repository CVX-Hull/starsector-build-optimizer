# Signal-Quality Validation — 2026-04-17 (extended)

Synthetic experiment validating eleven signal-quality strategies against ground-truth build quality. Each strategy was evaluated on 20 independent random seeds (300 builds, 50 opponents, 10 active per trial). The generative model mirrors the 900-trial Hammerhead exploit cluster: 90% of builds carry an exploit feature (uplift +0.8) with within-cluster variance N(0, 0.3); exploit builds vs trivial opponents receive an extra logit boost so matchups saturate the ±1.2 ceiling. Observed cell-censoring rate at this ceiling: **11.6%** of observed cells. *Note: the ceiling was tightened from the original ±1.5 to ±1.2 after the first run produced only ~4% censoring — too low to exercise Tobit's censored-MLE correction. The tighter ceiling raises censoring to ~12%, close to the Hammerhead run's observed ceiling-saturation rate, and should give Tobit a fair test.*

## What changed vs the previous run

The previous run (8 strategies) found D (TWFE + Box-Cox) as the winner but could not disentangle whether the advantage came from the Box-Cox shape (A3) or from avoiding the `rank_shape`-induced top-quartile clamp. To resolve this, three new strategies were added (I, J, K) and a new *estimator-alone* metric (`rho_alpha_truth`) was introduced.

- **ρ_truth** (existing) — Spearman ρ of the final `pred` vs truth: estimator × A3-shape *jointly*.
- **ρ_alpha_truth** (new) — Spearman ρ of the raw α (pre-A3) vs truth: the *estimator alone*. NaN for strategies that never produce a scalar α (E, G).

Rule of thumb: **if D beats A on ρ_truth but they tie on ρ_alpha_truth, the A3 shape is the bottleneck, not the estimator.**

## Headline metrics

| Strategy | n | ρ vs truth | ρ α vs truth | Exploit-spread ρ | Ceiling % | Top-5 | Top-10 | Top-25 | Mean wall |
|---|---|---|---|---|---|---|---|---|---|
| A: Baseline (TWFE+rank) | 20 | 0.401±0.072 | 0.411±0.072 | 0.308±0.073 | 0.253 | 0.05 | 0.08 | 0.17 | 5.69s |
| B: CFS-weighted TWFE | 20 | -0.013±0.054 | 0.000±0.000 | -0.009±0.060 | 0.253 | 0.01 | 0.04 | 0.09 | 0.05s |
| C: EM-Tobit TWFE | 20 | 0.463±0.041 | 0.465±0.039 | 0.371±0.044 | 0.253 | 0.07 | 0.09 | 0.18 | 0.52s |
| D: TWFE + Box-Cox | 20 | 0.471±0.057 | 0.471±0.057 | 0.382±0.059 | 0.004 | 0.11 | 0.15 | 0.26 | 0.09s |
| E: TWFE + Dom Novelty | 20 | 0.419±0.045 | NaN | 0.335±0.044 | 0.124 | 0.04 | 0.10 | 0.21 | 0.08s |
| F: B+C+E combined | 20 | 0.419±0.064 | 0.457±0.071 | 0.336±0.067 | 0.124 | 0.08 | 0.09 | 0.19 | 0.52s |
| G: Main-exploiter loop | 20 | 0.416±0.085 | 0.419±0.083 | 0.320±0.082 | 0.253 | 0.06 | 0.16 | 0.29 | 0.06s |
| H: CAT Fisher info | 20 | 0.499±0.071 | 0.506±0.071 | 0.412±0.067 | 0.253 | 0.02 | 0.09 | 0.21 | 0.10s |
| I: Tobit + Box-Cox | 20 | 0.463±0.048 | 0.463±0.048 | 0.377±0.054 | 0.004 | 0.15 | 0.20 | 0.26 | 0.53s |
| J: Box-Cox + CAT | 20 | 0.485±0.076 | 0.485±0.076 | 0.395±0.087 | 0.004 | 0.13 | 0.20 | 0.27 | 0.11s |
| K: Tobit+Box-Cox+CAT | 20 | 0.472±0.081 | 0.472±0.081 | 0.383±0.096 | 0.004 | 0.12 | 0.14 | 0.24 | 0.53s |

- Best **ρ vs truth**: **H: CAT Fisher info** (0.499 ± 0.071; Δ vs A +0.098, Δ vs D +0.028).
- Best **ρ α vs truth (estimator alone)**: **H: CAT Fisher info** (0.506 ± 0.071).
- Best **exploit-spread ρ**: **H: CAT Fisher info** (0.412 ± 0.067).
- Lowest **ceiling saturation**: **D: TWFE + Box-Cox** (0.004).

## Paired Wilcoxon — new strategies vs baseline A and vs D

| Strategy | metric | vs | n | mean Δ | p |
|---|---|---|---|---|---|
| I: Tobit + Box-Cox | rho_truth | A | 20 | +0.063 | 0.006 |
| I: Tobit + Box-Cox | rho_truth | D | 20 | -0.008 | 0.546 |
| J: Box-Cox + CAT | rho_truth | A | 20 | +0.084 | 0.003 |
| J: Box-Cox + CAT | rho_truth | D | 20 | +0.014 | 0.622 |
| K: Tobit+Box-Cox+CAT | rho_truth | A | 20 | +0.071 | 0.008 |
| K: Tobit+Box-Cox+CAT | rho_truth | D | 20 | +0.001 | 1.000 |
| I: Tobit + Box-Cox | rho_alpha_truth | A | 20 | +0.052 | 0.012 |
| I: Tobit + Box-Cox | rho_alpha_truth | D | 20 | -0.008 | 0.546 |
| J: Box-Cox + CAT | rho_alpha_truth | A | 20 | +0.074 | 0.009 |
| J: Box-Cox + CAT | rho_alpha_truth | D | 20 | +0.014 | 0.622 |
| K: Tobit+Box-Cox+CAT | rho_alpha_truth | A | 20 | +0.061 | 0.015 |
| K: Tobit+Box-Cox+CAT | rho_alpha_truth | D | 20 | +0.001 | 1.000 |
| I: Tobit + Box-Cox | exploit_spread_rho | A | 20 | +0.068 | 0.006 |
| I: Tobit + Box-Cox | exploit_spread_rho | D | 20 | -0.005 | 0.729 |
| J: Box-Cox + CAT | exploit_spread_rho | A | 20 | +0.086 | 0.009 |
| J: Box-Cox + CAT | exploit_spread_rho | D | 20 | +0.012 | 0.622 |
| K: Tobit+Box-Cox+CAT | exploit_spread_rho | A | 20 | +0.075 | 0.007 |
| K: Tobit+Box-Cox+CAT | exploit_spread_rho | D | 20 | +0.001 | 0.985 |

Additionally, K − J (ρ_truth): mean Δ = -0.013, p = 0.596 — this tests whether Tobit adds value *on top of* the CAT + Box-Cox stack.

## Paired Wilcoxon — all strategies vs baseline A (full)

| Strategy | metric | n | mean Δ | p |
|---|---|---|---|---|
| B: CFS-weighted TWFE | rho_truth | 20 | -0.413 | 0.000 |
| C: EM-Tobit TWFE | rho_truth | 20 | +0.062 | 0.000 |
| D: TWFE + Box-Cox | rho_truth | 20 | +0.070 | 0.000 |
| E: TWFE + Dom Novelty | rho_truth | 20 | +0.018 | 0.261 |
| F: B+C+E combined | rho_truth | 20 | +0.018 | 0.277 |
| G: Main-exploiter loop | rho_truth | 20 | +0.016 | 0.475 |
| H: CAT Fisher info | rho_truth | 20 | +0.098 | 0.000 |
| I: Tobit + Box-Cox | rho_truth | 20 | +0.063 | 0.006 |
| J: Box-Cox + CAT | rho_truth | 20 | +0.084 | 0.003 |
| K: Tobit+Box-Cox+CAT | rho_truth | 20 | +0.071 | 0.008 |
| B: CFS-weighted TWFE | rho_alpha_truth | 20 | -0.411 | 0.000 |
| C: EM-Tobit TWFE | rho_alpha_truth | 20 | +0.054 | 0.002 |
| D: TWFE + Box-Cox | rho_alpha_truth | 20 | +0.060 | 0.001 |
| E: TWFE + Dom Novelty | rho_alpha_truth | 0 | +nan | nan |
| F: B+C+E combined | rho_alpha_truth | 20 | +0.046 | 0.024 |
| G: Main-exploiter loop | rho_alpha_truth | 20 | +0.007 | 0.784 |
| H: CAT Fisher info | rho_alpha_truth | 20 | +0.095 | 0.000 |
| I: Tobit + Box-Cox | rho_alpha_truth | 20 | +0.052 | 0.012 |
| J: Box-Cox + CAT | rho_alpha_truth | 20 | +0.074 | 0.009 |
| K: Tobit+Box-Cox+CAT | rho_alpha_truth | 20 | +0.061 | 0.015 |
| B: CFS-weighted TWFE | exploit_spread_rho | 20 | -0.318 | 0.000 |
| C: EM-Tobit TWFE | exploit_spread_rho | 20 | +0.063 | 0.002 |
| D: TWFE + Box-Cox | exploit_spread_rho | 20 | +0.074 | 0.000 |
| E: TWFE + Dom Novelty | exploit_spread_rho | 20 | +0.027 | 0.154 |
| F: B+C+E combined | exploit_spread_rho | 20 | +0.028 | 0.097 |
| G: Main-exploiter loop | exploit_spread_rho | 20 | +0.011 | 0.674 |
| H: CAT Fisher info | exploit_spread_rho | 20 | +0.104 | 0.001 |
| I: Tobit + Box-Cox | exploit_spread_rho | 20 | +0.068 | 0.006 |
| J: Box-Cox + CAT | exploit_spread_rho | 20 | +0.086 | 0.009 |
| K: Tobit+Box-Cox+CAT | exploit_spread_rho | 20 | +0.075 | 0.007 |
| B: CFS-weighted TWFE | ceiling_pct | 20 | +0.000 | nan |
| C: EM-Tobit TWFE | ceiling_pct | 20 | +0.000 | nan |
| D: TWFE + Box-Cox | ceiling_pct | 20 | -0.250 | 0.000 |
| E: TWFE + Dom Novelty | ceiling_pct | 20 | -0.129 | 0.000 |
| F: B+C+E combined | ceiling_pct | 20 | -0.130 | 0.000 |
| G: Main-exploiter loop | ceiling_pct | 20 | +0.000 | nan |
| H: CAT Fisher info | ceiling_pct | 20 | +0.000 | nan |
| I: Tobit + Box-Cox | ceiling_pct | 20 | -0.249 | 0.000 |
| J: Box-Cox + CAT | ceiling_pct | 20 | -0.250 | 0.000 |
| K: Tobit+Box-Cox+CAT | ceiling_pct | 20 | -0.249 | 0.000 |

## Δ vs baseline A (mean across seeds)

| Strategy | Δ ρ vs truth | Δ ρ α vs truth | Δ exploit-spread | Δ ceiling % |
|---|---|---|---|---|
| A: Baseline (TWFE+rank) | +0.000 | +0.000 | +0.000 | +0.000 |
| B: CFS-weighted TWFE | -0.413 | -0.411 | -0.318 | +0.000 |
| C: EM-Tobit TWFE | +0.062 | +0.054 | +0.063 | +0.000 |
| D: TWFE + Box-Cox | +0.070 | +0.060 | +0.074 | -0.250 |
| E: TWFE + Dom Novelty | +0.018 | nan | +0.027 | -0.129 |
| F: B+C+E combined | +0.018 | +0.046 | +0.028 | -0.129 |
| G: Main-exploiter loop | +0.016 | +0.007 | +0.011 | +0.000 |
| H: CAT Fisher info | +0.098 | +0.095 | +0.104 | +0.000 |
| I: Tobit + Box-Cox | +0.063 | +0.052 | +0.068 | -0.249 |
| J: Box-Cox + CAT | +0.084 | +0.074 | +0.086 | -0.250 |
| K: Tobit+Box-Cox+CAT | +0.071 | +0.061 | +0.075 | -0.249 |

## Answers to the research questions

### 1. Does EM-Tobit help when A3 no longer clamps?

Comparing **I (Tobit + Box-Cox)** to **D (OLS-TWFE + Box-Cox)** on the same Box-Cox shape: Δ=-0.008, p=0.546 on ρ_truth; on ρ_alpha_truth: Δ=-0.008, p=0.546. Raw α means — D: 0.471, I: 0.463.

**No meaningful effect** (trend Δ=-0.008, p=0.546 — not significant at this sample size).

### 2. Does CAT compose with Box-Cox?

**J (Box-Cox + CAT)** vs **D (Box-Cox, random selection)**: Δ=+0.014, p=0.622 on ρ_truth.

**No meaningful effect** (trend Δ=+0.014, p=0.622 — not significant at this sample size).
J is directionally positive on all three outcome metrics vs D (ρ_truth Δ=+0.014, p=0.622). The n=20 sample size does not clear α=0.05 for J−D, but J beats D uniformly on mean. The important production comparison is J vs H: H has a slightly higher mean ρ_truth (0.499 vs 0.485) but its ceiling saturation is 25.3% (because H lacks Box-Cox), whereas J's is 0.4%. The ranking-information gain from CAT alone (H) is real, but H still flat-compresses the top quartile — which is what *Box-Cox* was introduced to fix. So J retains the best of both: CAT's observation win **and** Box-Cox's top-end preservation.

### 3. Does the full stack (K) beat Box-Cox alone (D)?

**K (Tobit + Box-Cox + CAT)** vs **D**: Δ=+0.001, p=1.000 on ρ_truth. K vs J (adding Tobit on top of the CAT+Box-Cox stack): Δ=-0.013, p=0.596.

**No — no effect** (Δ≈0).
Adding Tobit on top of J does not help (K ≈ J within noise); combined with the I−D result, this confirms that the Tobit estimator is not contributing positively at this censoring rate, whatever opponent selector it's paired with.

## Expected vs observed orderings

Theory predicts (small, censoring-dependent):
- ρ_alpha_truth: Tobit > OLS, i.e. {C, I, K} > {A, B, D, F, H, J}.
- ρ_truth: I > D (Tobit helps when A3 is not clamping).
- ρ_truth: J > D (CAT helps Box-Cox).
- ρ_truth: K > J (Tobit on top of CAT+Box-Cox).

Observed mean deltas:
- Raw α means (pre-A3): D=0.471, I=0.463, J=0.485, K=0.472. The Tobit-minus-OLS gap on raw α is *negative* in both comparisons: I−D = -0.008 and K−J = -0.013.

### Anomaly discussion — why does Tobit lose to OLS here?

The expectation from the survey was that EM-Tobit would beat OLS-TWFE on raw α, because OLS treats the ceiling-clipped values as if they were uncensored observations (biasing β upward for high-variance opponents and, via α = mean(Y − β), α downward for strong builds).

In this experiment, at 11.6% censoring with a ±1.2 clip, the Tobit imputation step pushes censored cells to μ + σ·φ(z)/(1−Φ(z)), where μ is the current estimate of α_i + β_j. When the top of the exploit cluster is tightly packed (within-cluster σ = 0.3 in the generative model), the imputed values for the strongest builds end up *noisier* than the clipped observations — Tobit replaces a known bias with a variance penalty that exceeds it. The effect is strongest at the top of the ranking, where exploit-cluster builds have many saturated cells; censored-MLE re-separates them only if their un-censored distribution has enough signal to recover. With opponents whose discrimination is fixed at 1.0 and build qualities on a ~1σ range, the imputation does more damage than good. See Amemiya (1984) for the general condition: Tobit's MSE gain over OLS is roughly ∝ (censoring fraction) × (signal-to-σ ratio at the ceiling) — both modest here.

CAT, in contrast, produces a *consistent* win on ρ_truth and ρ_alpha_truth over A, and matches or narrowly exceeds D. The intuition is that the variance-ranking opponent selector naturally avoids trivial opponents (whose outcome variance is near zero because the exploit almost always wins), so it reallocates matchups to informative difficulty levels. This win is orthogonal to the aggregation step and composes fine with Box-Cox.

## Final production recommendation

**Recommended winner from {D, I, J, K}: J: Box-Cox + CAT (ρ_truth = 0.485 ± 0.076).**

J (Box-Cox + CAT) has the highest mean ρ_truth and the highest exploit-spread ρ among {D, I, J, K}. Its edge over D on ρ_truth is Δ=+0.014 with p=0.622 (directional but not significant at n=20), and it outperforms D on all three outcome metrics (ρ_truth, ρ_alpha_truth, exploit-spread). Because the CAT selector is an *observation-time* change — it lives in the scheduler and adds no compute to α-fitting — its marginal complexity cost is low. Note that H (CAT alone) has a numerically higher ρ_truth (0.499 vs 0.485, difference not significant), but H's ceiling saturation is 25.3% vs ~0% for J because H retains the production rank_shape clamp. **Recommended: deploy J — Box-Cox aggregation + CAT Fisher-info opponent selection.** This combines the CAT gain (observation side) with the Box-Cox gain (aggregation side) and avoids the top-quartile clamp that motivated the original signal-quality investigation. Context: the top four candidates span 0.022 ρ_truth (max - min). All four outperform baseline A by +0.07 to +0.10 ρ_truth. The decision between them is sensitive to the censoring regime — at 11.6% censoring, Tobit did not pay off here; at higher censoring it could. Re-evaluate on real Hammerhead data after deploying the chosen A3 change to confirm the ordering holds.

## Failures / caveats

- Strategy G (main-exploiter loop) operates on a 150-build subset, so its overlap metrics are computed against a smaller truth pool — compare it cautiously to the others.
- EM-Tobit's advantage depends on the censoring fraction. With the current ±1.2 ceiling, 11.6% of observed cells hit the ceiling. Even at this rate, the Tobit imputation did *not* improve over OLS in our runs — see the anomaly discussion below.
- CAT (H, J, K) concentrates matchups on high-variance opponents. In this generative model, high-variance ≈ opponents near the decision boundary, which is also where raw outcomes are noisiest — the selector trades one form of precision for another.
- Strategy E reports `rho_alpha_truth = NaN` by design: its output is a behavior-local rank, not a global α.

## Files

- `signal_validation.py` — this experiment.
- `results.csv` — per-seed, per-strategy metrics (220 rows).
- `comparison.png` — main bar chart (4 panels including ρ_α).
- `exploit_dispersion.png` — violin plot of exploit-cluster ρ.
- `ceiling_saturation.png` — ceiling fraction per strategy.
