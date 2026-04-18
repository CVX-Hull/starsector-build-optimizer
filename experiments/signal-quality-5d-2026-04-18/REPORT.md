# Signal-Quality Validation — Phase 5E given 5D (2026-04-18)

## Bottom line

**The 5D shipped baseline absorbs most of the ρ_truth gain that used to belong to Box-Cox, but Box-Cox still delivers the A3 ceiling fix and is load-bearing for Optuna TPE exploitation.** Phase 5D lifts ρ_truth from 0.463 (A0 pre-5D) to 0.737 (A 5D; Δ = +0.273, p = 0.000). Stacking Box-Cox on top adds only Δρ = +0.006 (p = 0.546) — but that number is the wrong lens.

The right lens is **top-k overlap**: Top-5 goes from 0.03 (A) to 0.43 (D) — a **14× improvement** in identifying the best five builds. Top-10 goes from 0.07 to 0.46. Ceiling saturation drops from 25.3% to 0.3%. The mechanism is unchanged from 2026-04-17: rank-shape-with-clamp ties every top-quartile build at 1.0, so TPE's exploitation phase is blind among the top 25% — ρ_truth is dominated by the bulk of the distribution and doesn't see the tie.

Winner among {D, H, I, J, K}: **J: CAT + 5D + Box-Cox** (ρ_truth = 0.751 ± 0.041). All Box-Cox strategies (D, J) deliver the 14× top-5 improvement; J adds the CAT observation-side gain on top for a small but statistically-significant ρ improvement over A. Tobit variants (I, K) **regress** to ρ ≈ 0.66 because Tobit's α̂ distribution doesn't align with the EB prior fit on OLS α̂ — a 5D-specific interaction that did not appear in the pre-5D run.

## Setup

300 builds × 50 opponents × 10 active per trial × 20 seeds, same generative model as `signal-quality-2026-04-17`: 90% exploit cluster (uplift +0.8, within-cluster σ=0.3), extra logit boost vs trivial opponents, outcomes clipped at ±1.2. Empirical censoring rate: **10.6%** of observed cells.

Each build carries a 7-dim covariate vector `X_i` in the production `_build_covariate_vector` ordering (eff_max_flux, eff_flux_dissipation, eff_armor_rating, total_weapon_dps, engagement_range, kinetic_dps_fraction, composite_score). Each feature is `load·quality + archetype_bias + N(0, σ_f)` with σ_f calibrated so the EB posterior gives a production-like gain over the pre-5D baseline. `composite_score` is the strongest predictor, `engagement_range` / `kinetic_dps_fraction` are near-null archetype proxies — matching the variance audit in §2.7 of phase5d-covariate-adjustment.md.

## Headline metrics

| Strategy | n | ρ vs truth | ρ α vs truth | Exploit-spread ρ | Ceiling % | Top-5 | Top-10 | Top-25 | Mean wall |
|---|---|---|---|---|---|---|---|---|---|
| A0: pre-5D (TWFE+rank) | 20 | 0.463±0.050 | 0.473±0.050 | 0.368±0.059 | 0.253 | 0.05 | 0.09 | 0.18 | 0.08s |
| A: 5D baseline (EB+TG+rank) | 20 | 0.737±0.030 | 0.747±0.029 | 0.683±0.036 | 0.253 | 0.03 | 0.07 | 0.24 | 0.14s |
| D: 5D + Box-Cox | 20 | 0.743±0.031 | 0.743±0.031 | 0.689±0.037 | 0.003 | 0.43 | 0.46 | 0.46 | 0.15s |
| H: CAT + 5D + rank | 20 | 0.744±0.036 | 0.752±0.037 | 0.692±0.038 | 0.253 | 0.07 | 0.11 | 0.27 | 0.16s |
| I: Tobit + 5D + Box-Cox | 20 | 0.656±0.051 | 0.656±0.051 | 0.588±0.056 | 0.004 | 0.29 | 0.34 | 0.39 | 0.60s |
| J: CAT + 5D + Box-Cox | 20 | 0.751±0.041 | 0.751±0.041 | 0.700±0.044 | 0.004 | 0.43 | 0.40 | 0.46 | 0.17s |
| K: CAT + Tobit + 5D + Box-Cox | 20 | 0.655±0.065 | 0.655±0.065 | 0.586±0.078 | 0.004 | 0.30 | 0.31 | 0.39 | 0.61s |

## Paired Wilcoxon — key comparisons

| Comparison | metric | n | mean Δ | p |
|---|---|---|---|---|
| A (5D) vs A0 (pre-5D) | rho_truth | 20 | +0.273 | 0.000 |
| D (5D+Box-Cox) vs A0 | rho_truth | 20 | +0.280 | 0.000 |
| D vs A (5D baseline) | rho_truth | 20 | +0.006 | 0.546 |
| D vs A | rho_alpha_truth | 20 | -0.004 | 0.452 |
| H (CAT) vs A | rho_truth | 20 | +0.007 | 0.294 |
| J (CAT+Box-Cox) vs A | rho_truth | 20 | +0.014 | 0.019 |
| J vs D | rho_truth | 20 | +0.008 | 0.277 |
| K vs J | rho_truth | 20 | -0.096 | 0.000 |
| I (Tobit+Box-Cox) vs D | rho_truth | 20 | -0.088 | 0.000 |

## Paired Wilcoxon — all strategies vs A (5D baseline)

| Strategy | metric | n | mean Δ | p |
|---|---|---|---|---|
| A0: pre-5D (TWFE+rank) | rho_truth | 20 | -0.273 | 0.000 |
| D: 5D + Box-Cox | rho_truth | 20 | +0.006 | 0.546 |
| H: CAT + 5D + rank | rho_truth | 20 | +0.007 | 0.294 |
| I: Tobit + 5D + Box-Cox | rho_truth | 20 | -0.081 | 0.000 |
| J: CAT + 5D + Box-Cox | rho_truth | 20 | +0.014 | 0.019 |
| K: CAT + Tobit + 5D + Box-Cox | rho_truth | 20 | -0.082 | 0.000 |
| A0: pre-5D (TWFE+rank) | rho_alpha_truth | 20 | -0.274 | 0.000 |
| D: 5D + Box-Cox | rho_alpha_truth | 20 | -0.004 | 0.452 |
| H: CAT + 5D + rank | rho_alpha_truth | 20 | +0.005 | 0.409 |
| I: Tobit + 5D + Box-Cox | rho_alpha_truth | 20 | -0.092 | 0.000 |
| J: CAT + 5D + Box-Cox | rho_alpha_truth | 20 | +0.004 | 0.245 |
| K: CAT + Tobit + 5D + Box-Cox | rho_alpha_truth | 20 | -0.092 | 0.000 |
| A0: pre-5D (TWFE+rank) | exploit_spread_rho | 20 | -0.316 | 0.000 |
| D: 5D + Box-Cox | exploit_spread_rho | 20 | +0.006 | 0.701 |
| H: CAT + 5D + rank | exploit_spread_rho | 20 | +0.009 | 0.312 |
| I: Tobit + 5D + Box-Cox | exploit_spread_rho | 20 | -0.095 | 0.000 |
| J: CAT + 5D + Box-Cox | exploit_spread_rho | 20 | +0.017 | 0.019 |
| K: CAT + Tobit + 5D + Box-Cox | exploit_spread_rho | 20 | -0.098 | 0.000 |
| A0: pre-5D (TWFE+rank) | ceiling_pct | 20 | +0.000 | nan |
| D: 5D + Box-Cox | ceiling_pct | 20 | -0.250 | 0.000 |
| H: CAT + 5D + rank | ceiling_pct | 20 | +0.000 | nan |
| I: Tobit + 5D + Box-Cox | ceiling_pct | 20 | -0.249 | 0.000 |
| J: CAT + 5D + Box-Cox | ceiling_pct | 20 | -0.249 | 0.000 |
| K: CAT + Tobit + 5D + Box-Cox | ceiling_pct | 20 | -0.249 | 0.000 |

## Interpretation

### 1. Does Phase 5D itself show up in the synthetic?

A vs A0 (5D EB + triple-goal vs plain TWFE, both feeding rank-shape): Δ=+0.273, p=0.000 on ρ_truth. Raw α: ρ_α_truth moves from 0.473 (A0) to 0.747 (A). The EB step pulls noisy α̂_TWFE toward the covariate-based prior, and triple-goal preserves the raw histogram so the rank ordering is the one that actually moves.

**Caveat — synthetic massively overstates the Hammerhead LOOO gain.** The Phase 5D fusion validation (`experiments/phase5d-covariate-2026-04-17/FUSION_REPORT.md`) recorded HN ρ = 0.744 vs A0 ρ = 0.407 on *its* synthetic (Δρ = +0.337), mirroring the magnitude here, but only HN ρ = 0.316 vs A0 ρ = 0.280 on the real Hammerhead LOOO probe (Δρ = +0.036) — **10× smaller than either synthetic reports**. The discrepancy is structural, not a bug in either harness: the synthetic exploit cluster has 90% of builds at `q = 0.8 + N(0, 0.3)` where the covariates still correlate linearly with `q` (noise is additive, not feature-collapsing); in real Hammerhead, the exploit cluster is 89% of builds sharing rare-faction hullmods whose damage mechanics bypass flux/armor/DPS entirely, so the scorer components are *near-constant within the cluster* and EB has almost no within-cluster signal to lift. The synthetic also uses direct Spearman ρ; production uses leave-one-opponent-out, which is strictly pessimistic against any method (including 5D) that fits globally. Neither of these weaken the 5E conclusion: Box-Cox acts downstream of α̂_EBT at the A3 shape step and is indifferent to how strong the α̂ is — it fixes a mechanical ceiling-clamp pathology that exists under every α-stage configuration.

### 2. Does Box-Cox still add value on top of 5D?

Yes, but the story is hidden from ρ_truth. D vs A on ρ_truth: Δ=+0.006, p=0.546; on ρ_α: Δ=-0.004, p=0.452. The near-zero ρ delta is expected — D and A share the same α̂_EBT stage, so rank ordering of the full distribution is identical. What Box-Cox changes is the **shape** of that distribution above the clamp: ceiling fraction collapses from 25.3% (A) to 0.3% (D), and top-k overlap — the metric that actually matters for TPE's l(x)/g(x) ratio at the top quartile — jumps:

| Metric | A (5D, clamp) | D (5D + Box-Cox) | ratio |
|---|---|---|---|
| Top-5 overlap  | 0.03  | 0.43  | 14.3× |
| Top-10 overlap | 0.07 | 0.46 | 7.1× |
| Top-25 overlap | 0.24 | 0.46 | 1.9× |

Triple-goal substitutes α̂_TWFE's histogram back into the posterior, so α̂_EBT is still continuous on [-1.2, 1.2]; the rank-shape clamp still zeroes out top-quartile gradient exactly as it did pre-5D. Box-Cox dissolves the ceiling the same way it did in the 2026-04-17 run. **This is the load-bearing finding for Phase 5E**: even when ρ_truth barely moves, Box-Cox is the only way the optimizer gets a real gradient among the top 25% of builds.

### 3. Does CAT opponent selection still compose?

H (CAT + rank) vs A: Δ=+0.007, p=0.294. J (CAT + Box-Cox) vs D: Δ=+0.008, p=0.277; J vs A: Δ=+0.014, p=0.019. CAT is an observation-side change; its contribution is orthogonal to the α-stage 5D change and to the A3 Box-Cox change. Directional sign is preserved from the pre-5D run.

### 4. Does Tobit estimator help?

No — Tobit now *hurts*. I vs D: Δ=-0.088, p=0.000 on ρ_truth. K vs J: Δ=-0.096, p=0.000. Both Tobit variants collapse from ρ ≈ 0.75 to ρ ≈ 0.66 — a ~0.09 regression that is significant at p < 0.001. This is a **new 5D-specific pathology** not seen in the 2026-04-17 run. Mechanism: in production `apply_5d()` the pooled σ̂_ε² is computed from OLS residuals (this matches the production path where `ScoreMatrix._ensure_decomposed` always runs plain TWFE). When Tobit produces a *different* α̂ vector but we still ask EB to shrink it using OLS-derived σ̂_i², the precision weights are mis-specified relative to the Tobit α̂'s actual error structure, and the posterior pulls Tobit α̂ in the wrong direction. At 10.6% censoring Tobit was already at the Amemiya break-even (pre-5D verdict: no effect); adding the mismatched EB step tips it into active harm. Keep Tobit deferred.

## Production recommendation

Keep the same Phase 5E recommendation as the 2026-04-17 run, with the top-k overlap metric as the justification instead of ρ_truth:

1. **Replace A3 rank-shape-with-top-quartile-clamp with Box-Cox output warping.** The expected production benefit is not ρ_truth (which this synthetic shows moves only +0.006) but **top-5 identification from 0.03 to 0.43 (14×)** and ceiling saturation from 25.3% to 0.3%. TPE's top-quartile exploitation stops being blind.
2. **CAT Fisher-info opponent selection remains a viable secondary enhancement** (J vs A Δ = +0.014, p = 0.019 — smallest significant gain in the grid). Deploy Box-Cox first; revisit CAT once 5E is settled and production ρ can be re-measured on a shipped 5D+5E log.
3. **EM-Tobit is now actively deferred**, not just non-helpful: the I/K variants regress by ~0.09 ρ because Tobit α̂ doesn't align with the OLS-fit EB prior. If Tobit ever ships, σ̂_i² would need to be re-derived from Tobit residuals.

## Companion: covariate-strength calibration

`calibration_sweep.py` scales the covariate noise multiplier from 0.5× (prior ρ ≈ 0.91) to 4× (prior ρ ≈ 0.34, closest to the real Hammerhead regime) and measures how the 5D and Box-Cox gains track. Key result — the 5D ρ gain scales with prior strength as expected (+0.38 at 0.5× → +0.05 at 4×, recovering the production Δρ = +0.036), but Box-Cox's ceiling collapse (−0.25) and top-k overlap boost (+0.15–0.50) are **invariant** across the whole range. At the weakest-prior regime (4×, matching real Hammerhead): Δρ A vs A0 = +0.047, Δ top-5 D vs A = +0.20, Δ top-10 D vs A = +0.15. See `calibration_report.md` for the full table.

## Files

- `signal_validation_5d.py` — this experiment.
- `results.csv` — per-seed, per-strategy metrics.
- `comparison.png` — four-panel bar chart.
- `ceiling_saturation.png` — ceiling fraction per strategy.
- `calibration_sweep.py` — robustness-check across 4 noise regimes.
- `calibration_results.csv` — per-regime, per-seed results.
- `calibration_report.md` — regime-by-regime gain table.
