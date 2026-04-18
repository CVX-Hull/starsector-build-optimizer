# Phase 5D — Covariate-Adjusted TWFE Validation

Simulation and real-data testing of the Phase 5D proposal
(`docs/reference/phase5d-covariate-adjustment.md`). Tests whether covariate-
adjusted TWFE with automatic selection (timing filter + PDS lasso + optional
ICP) beats the shipped A1+A2 pipeline on rank correlation with true build
quality. Tested on both synthetic data matched to the 2026-04-13 Hammerhead
run characteristics (368 builds × 54 opponents, anchor-heavy schedule, ~16%
timeout, exploit cluster at top) and the real Hammerhead evaluation log.

**Headline: Phase 5D fails the ship gate in both synthetic and real data, and the shipped scalar A2 CV is also slightly harmful on both.**

## TL;DR

| Estimator | Synthetic ρ(α̂, truth) (n=20) | Hammerhead LOOO ρ (3 probes) |
|---|---|---|
| **A0 — plain TWFE, no adjustment** | **0.407 ± 0.048** | **0.250** |
| A — shipped TWFE + scalar heuristic CV | 0.347 ± 0.045 | 0.236 |
| B — TWFE + multi-covariate CUPED (full X_pre) | 0.055 ± 0.041 | 0.118 |
| C — TWFE + PDS-selected multi-CUPED | 0.055 ± 0.041 | 0.118 |
| D — C + ICP invariance overlay | 0.077 ± 0.098 | 0.118 |

Paired Wilcoxon Δρ vs A0 (plain TWFE):

- **A − A0** = −0.060, p < 0.0001 *(shipped scalar CV hurts)*
- **C − A0** = −0.353, p < 0.0001 *(proposed 5D hurts — large effect)*
- **D − A0** = −0.331, p = 0.0001 *(ICP rescue insufficient — still hurts)*

On real Hammerhead, the ship-gate from `phase5d-covariate-adjustment.md §3.3`
required Δρ ≥ +0.02 to ship. Observed Δρ(C − A) = **−0.118**. **Gate fails by 7×.**

## Methodology

### Estimators tested

| Key | Estimator | Description |
|-----|-----------|-------------|
| A0 | plain TWFE | α̂, β̂ from `twfe_decompose`; no covariate adjustment. Reference. |
| A | scalar CV (shipped) | α̂ − β̂_cv · (heuristic_i − h̄). The current A2 step. |
| B | multi-CUPED full | α̂ − γ̂ᵀ (X_i − X̄), γ̂ from OLS on all X^build_pre (no selection). |
| C | multi-CUPED PDS | B with post-double-selection lasso choosing S ⊆ X^build_pre. |
| D | PDS + ICP | C with opponent-invariance filter on the selected set. |
| E | within + bad | Stage-1 violation: fit γ at matchup level with 3 post-matchup colliders admitted (duration, damage_eff, overload_diff). |
| F | within + bad + PDS | E with PDS selection on the cell-level covariates. |
| G | within + bad + PDS + ICP | F with ICP invariance filter. |

E/F/G are stress tests answering: if we had skipped the Stage-1 timing filter
and let post-matchup signals in, could data-driven selection still save us?

### Synthetic generative model

Parameters tuned to mirror the 2026-04-13 Hammerhead run:

- **368 builds × 54 opponents** (matches Hammerhead).
- **10 active opponents per build**, **3 anchors** locked first, **5 incumbent-overlap** from prev build (matches the 5C opponent selector).
- **Exploit cluster**: 90% of builds have `has_exploit=True` with quality ~ 0.8 + N(0, 0.3); exploit + trivial opponent gets +1.5 logit boost. Matches the Hammerhead exploit saturation.
- **Ceiling ±1.2**, noise σ=0.5. Produces ~16% timeout-equivalent ceiling hits.
- **X^build_pre** = 4 useful covariates (noisy proxies of q: `q + N(0, σ_k)` for σ_k ∈ {0.7, 0.5, 0.35, 0.9}) + 4 pure-noise columns (N(0,1)).
- **heuristic_i** = `q + N(0, 1.2)`, giving ρ(heuristic, q) ≈ 0.45 — matches production composite_score fidelity.

### Real Hammerhead replay

- **313 completed builds** from `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl` (non-pruned only; 55 pruned excluded to ensure ≥10 observations per build).
- **Y_ij** = `hp_differential` (clipped to [−1, 1]); TIMEOUT rows dampened to ×0.5.
- **X^build_pre** = 13 scorer components (`composite`, `total_dps`, `kinetic_dps`, `he_dps`, `energy_dps`, `flux_balance`, `flux_efficiency`, `effective_hp`, `armor_ehp`, `shield_ehp`, `range_coherence`, `damage_mix`, `op_efficiency`) + 3 extras (`n_hullmods`, `flux_vents`, `flux_capacitors`). All re-computed via `starsector_optimizer.scorer.heuristic_score` against the live `game/starsector/data` files.
- **Ship gate** = leave-one-opponent-out (LOOO) over the 3 most-sampled anchor opponents (`mule_Fighter_Support`, `condor_Attack`, `sunder_Support`; 368/368/367 matchups each). For each probe: drop its column, refit each estimator on the remaining 53 opponents, then measure Spearman ρ between the refit α̂ and the probe's raw Y across the 313 builds. The 3 per-probe ρ values are averaged.

## Results

### 1. Synthetic sweep (n=20 seeds)

Full table of `rho(α̂, truth)` with paired Wilcoxon against A0 and against A:

```
      rho_A0  rho_A  rho_B  rho_C  rho_D  rho_E  rho_F  rho_G
mean   0.407  0.347  0.055  0.055  0.077  0.237  0.237  0.407
std    0.048  0.045  0.041  0.041  0.098  0.058  0.058  0.048

Paired Wilcoxon vs A0:
  A-A0:  mean Δρ = -0.060   p = 0.0000
  B-A0:  mean Δρ = -0.353   p = 0.0000
  C-A0:  mean Δρ = -0.353   p = 0.0000
  D-A0:  mean Δρ = -0.331   p = 0.0001
  E-A0:  mean Δρ = -0.170   p = 0.0000
  F-A0:  mean Δρ = -0.170   p = 0.0000
  G-A0:  mean Δρ = +0.000   p = 1.0000

Paired Wilcoxon vs A (shipped scalar CV):
  B-A:   mean Δρ = -0.293   p = 0.0000
  C-A:   mean Δρ = -0.293   p = 0.0000
  D-A:   mean Δρ = -0.271   p = 0.0000
```

### 2. Selection quality on the synthetic covariate set (k=8; 4 useful + 4 noise)

| Metric | C (PDS) | D (PDS + ICP) |
|---|---|---|
| useful recall | **1.00** | 0.94 |
| noise FPR | **0.94** ← problem | 0.11 |
| mean \|S\| selected | 7.8 of 8 | 4.2 of 8 |

PDS at this sample size (n=368 builds × 8 build-level covariates) does
essentially no selection — the "double" step pulls in noise columns through
their correlations with other noise columns. **ICP fixes selection (0.11 noise
FPR), but ICP's improvement does not translate into rank-correlation gain
because the fundamental CUPED adjustment itself is harmful at this regime.**

### 3. Stage-1 timing filter: empirical value

E/F/G admit 3 post-matchup colliders (duration, damage_eff, overload_diff) as
within-TWFE matchup-level covariates:

| Estimator | Behavior | rho(α̂, truth) | Δ vs A0 |
|---|---|---|---|
| E (all bad-controls kept) | γ fit on all 3 colliders | 0.237 | **−0.170** |
| F (PDS) | PDS retains all 3 colliders (100% retention) | 0.237 | −0.170 |
| G (PDS + ICP) | ICP rejects all 3 colliders (0% retention) | 0.407 | 0.000 |

**PDS fails to filter colliders** — the double-lasso retains them because
they strongly predict Y. This is the exact bad-control failure mode the Phase
5D doc anticipated (§2.4): "no purely-predictive selector detects bad
controls." **ICP's invariance test correctly rejects all three**, recovering
the plain TWFE performance. This validates the optional ICP path (§2.5) as a
safety net, BUT the Stage-1 timing filter is strictly cheaper than ICP and
sufficient on its own — the filter's rule is a single operational decision,
while ICP's greedy subset enumeration is per-covariate.

### 4. Real Hammerhead data — ship gate (LOOO across 3 anchor probes)

```
probe_opp                A0       A        B       C       D
mule_Fighter_Support    0.270   0.262   0.106   0.106   0.106
condor_Attack           0.210   0.193   0.145   0.145   0.145
sunder_Support          0.269   0.253   0.103   0.103   0.103
-------------------- -------- -------- ------- ------- -------
mean                    0.250   0.236   0.118   0.118   0.118
```

- **Ship-gate result (Δρ(C − A) ≥ +0.02 required)**: **−0.118**, FAIL.
- PDS retained **16 of 16** build covariates — no selection at this sample size.
- ICP retained **16 of 16** — no invariance-based rejection either.
- `var(α̂)`: A0 = 0.025, A = 0.024, B/C/D = 0.014. The Phase 5D variant does reduce
  α̂ variance (as CUPED should), **but the variance reduction comes from
  absorbing genuine quality signal into γ̂**, not noise.

### 5. Sensitivity sweep (active_size × heuristic_noise, 128 seeds)

`sensitivity.py` varies `active_size ∈ {3, 5, 10, 20}` and the heuristic proxy
noise `σ ∈ {0.3, 0.6, 1.2, 2.5}`. Result table of Δρ vs plain TWFE:

```
 active  h_noise  rho_A0  rho_A  rho_C  d_A_A0  d_C_A0
      3      0.3   0.175 -0.028 -0.034  -0.202  -0.209
      3      0.6   0.175  0.063 -0.034  -0.112  -0.209
      3      1.2   0.175  0.133 -0.034  -0.042  -0.209
      3      2.5   0.175  0.163 -0.034  -0.011  -0.209
      5      0.3   0.257 -0.000  0.010  -0.257  -0.247
      5      0.6   0.257  0.111  0.010  -0.147  -0.247
      5      1.2   0.257  0.199  0.010  -0.058  -0.247
      5      2.5   0.257  0.238  0.010  -0.019  -0.247
     10      0.3   0.393  0.067  0.048  -0.326  -0.345
     10      0.6   0.393  0.219  0.048  -0.174  -0.345
     10      1.2   0.393  0.329  0.048  -0.064  -0.345
     10      2.5   0.393  0.376  0.048  -0.017  -0.345
     20      0.3   0.541  0.128  0.103  -0.414  -0.438
     20      0.6   0.541  0.315  0.103  -0.227  -0.438
     20      1.2   0.541  0.453  0.103  -0.088  -0.438
     20      2.5   0.541  0.515  0.103  -0.026  -0.438
```

**A0 dominates A in every cell; A0 dominates C in every cell**. The gap never
closes — even with very weak heuristic (σ=2.5) and very few observations per
build (3), CUPED never turns net-positive. See `sensitivity_heatmap.png`.

## Why CUPED fails here (root-cause analysis)

CUPED (Deng et al. 2013) assumes the control variate `X` is correlated with
**noise** in the outcome but **independent of the treatment effect**. In A/B
testing, pre-period user metrics are correlated with post-period user-specific
noise but uncorrelated with treatment randomization, so subtracting γ̂·X
reduces variance without biasing the treatment effect.

In our setting, the quantities map as:

- "outcome" = `α̂_i` (per-build TWFE fitness)
- "treatment effect" = true `q_i` (true build quality)
- "control variate" = `heuristic_i` or scorer components

Our control variates are **noisy proxies of q_i** — they are correlated with
the "treatment effect", not with the noise in α̂_i. Regressing α̂ on X and
subtracting the projection therefore removes real quality signal, not noise.
The stronger X's correlation with q, the worse the damage. The math is
quantified in the sensitivity table: tightening the heuristic noise from σ=2.5
to σ=0.3 drops A by 0.15-0.30 at every active_size.

The 6-field literature that motivated Phase 5D's base technique (IRT + causal
inference + A/B testing + …) was correct that FWL + CUPED + PDS is the right
pattern **when X is uncorrelated with the estimand**. The implementation of
that pattern in our optimizer setting is the mis-fit: our `heuristic_i` and
scorer components are auxiliary **estimators of q**, not orthogonal covariates.

## Recommendation

### Immediate: do not ship Phase 5D

Phase 5D as currently specified (multivariate CUPED at the build level, with
PDS or PDS+ICP selection) **systematically reduces rank-correlation with
truth**. Both synthetic (−0.35 Δρ, p<0.0001, n=20) and real Hammerhead
(−0.12 Δρ on LOOO ship-gate) confirm the regression is harmful at every point
tested.

### Secondary: consider removing the shipped scalar A2 CV

The shipped A1+A2 pipeline under-performs plain TWFE (A0) by 0.060 synthetic
(p<0.0001) and 0.014 real Hammerhead. The magnitude is small but directionally
consistent with the same root cause. Recommend benchmarking A vs A0 on a dedicated
run before touching — the shipped A2 uses the same heuristic_i that the
optimizer's warm-start already used, so removing it may be neutral rather than
a clear win, but it is NOT positive.

### If covariate adjustment is still desired, the viable paths are

1. **Find covariates uncorrelated with α.** Schedule-position (trial number,
   calendar time) or opponent-pool version are valid — they're correlated
   with noise in α̂ via temporal drift but not with build quality. These are
   small-gain but CUPED-safe.

2. **Reframe the objective.** If Phase 5D's goal is variance reduction of α̂
   for Optuna's acquisition function (not rank correlation with hidden truth),
   the ship-gate metric should measure **optimization convergence** (top-k
   recall at N trials, time-to-incumbent) rather than rank correlation. Under
   this framing the results above are inconclusive: lower var(α̂) could still
   help TPE if the bias is systematic and cancels out in ranking among
   high-α candidates. A dedicated Optuna-loop benchmark is required.

3. **Abandon CUPED in favor of shrinkage.** Empirical-Bayes shrinkage of α̂
   toward a heuristic-prior mean (Stein estimator, James-Stein) reduces
   variance without the orthogonality requirement. This is a different
   phase; flag as future work.

### On the Stage-1 / Stage-2 debate

The validation **does** confirm two subsidiary claims of the Phase 5D doc:

- **Stage-1 timing filter is empirically valuable.** Admitting post-matchup
  colliders costs Δρ = −0.17 (synthetic). The bitter-lesson-compliant "pre-
  vs-post matchup" rule is sufficient and does what it claims to do.
- **ICP is a working safety net.** When Stage-1 is violated, ICP rejects
  the 3 bad controls and fully recovers A0 performance (Δρ = 0.000 vs A0).
  PDS alone does not — it retains 100% of the colliders because they predict Y.

Neither finding changes the primary recommendation (do not ship the CUPED
adjustment itself), but both validate the structural decisions in §2.4 and
§2.5 of the Phase 5D doc. Those sections' conclusions survive; only the
§2.1-2.3 estimator (the γ regression itself) is refuted.

## Reproducibility

```bash
# Full sweep: synthetic (n=20 seeds) + Hammerhead replay
uv run python phase5d_validation.py

# Quick sanity run (n=5 seeds)
uv run python phase5d_validation.py --quick

# Hammerhead-only
uv run python phase5d_validation.py --real-only

# Sensitivity sweep (4×4 grid × 8 seeds = 128 runs)
uv run python sensitivity.py
```

## Files

- `phase5d_validation.py` — estimators + synthetic sweep + Hammerhead replay
- `sensitivity.py` — active_size × heuristic_noise regime sweep
- `results.csv` — per-seed synthetic metrics (n=20, 8 estimators)
- `sensitivity_results.csv` — sensitivity grid (16 cells × 8 seeds = 128 rows)
- `hammerhead_gate.csv` — LOOO per-probe × per-estimator ρ values
- `synthetic_comparison.png` — ρ(α̂, truth) box-whisker + selection quality
- `sensitivity_heatmap.png` — Δρ(A − A0) and Δρ(C − A0) over the regime grid
- `hammerhead_gate.png` — per-probe bars + mean-across-probes ρ
