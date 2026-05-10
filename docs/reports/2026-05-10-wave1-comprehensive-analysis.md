---
type: report
status: draft
last-validated: 2026-05-10
supersedes: none
---

# Wave 1 — comprehensive post-hoc analysis (TWFE / EB / BT, pooled & per-cell)

## Abstract

We compare four candidate-selection estimators (raw mean, two-way fixed
effects, TWFE with empirical-Bayes shrinkage, Bradley–Terry MAP) on the
1,744 finalized non-pruned trials of Wave 1 (5 cells × 3 seeds, all
hammerhead/early). Pooled across cells, raw mean has **0/3 top-K
overlap** with each of the three principled rankers at k=3 and k=5,
confirming that opponent confounding (mean imbalance index 0.80–1.00)
makes raw-mean candidate selection unfit in this regime. The principled
rankers agree closely (TWFE↔EB Spearman ρ=0.9995, BT↔EB Pearson
r=0.898). At training-time α̂_EB, the production cell c2 (EB+Box-Cox)
does **not** beat either A0 (c0a) or A (c0b) baselines — both
bootstrapped CIs span zero — and is itself outperformed at the point
estimate by c1 (EB-only, Box-Cox off). c3 (production + warm-start)
trips the F2a Box-Cox-saturation gate (2.96%), exhibits pathological
imbalance (cell mean I=1.00 with one seed at I=1.12), and its three
seeds rank top-K builds inconsistently (Spearman ρ=0.56 with one seed
at ρ=−0.13). The in-flight cloud honest-evaluation will arbitrate the
training-time priors against out-of-sample build quality.

This report complements the headline gate-verdict report
[`2026-05-10-wave1-validation.md`](2026-05-10-wave1-validation.md). It
does not duplicate the §1 hard gates or the §2 group-B deconfounding
gates of the validation report; it is the ranker-and-pooling deep dive.

**Reproducibility.** Every numeric in this report is materialised in
`data/wave1-comprehensive/headline_numbers.json`; every chart is in
`data/wave1-comprehensive/charts/*.png` at 200 dpi; the producer is
`scripts/analysis/wave1_comprehensive_analysis.py`. Re-run via `uv run
python scripts/analysis/wave1_comprehensive_analysis.py` to refresh.

---

## 1. Methods

### 1.1 Data

The unit of analysis is a **(cell, seed, trial)** triple from
`data/logs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl`.
Each cell × seed pair is a single Optuna study using TPE proposer with
MedianPruner. Each finalized trial is a Build (hull, weapon assignment,
hullmod set, vents, capacitors) tested against an opponent fleet drawn
by the opponent-pool sampler; its outcome is reported as
`hp_differential` ∈ [−1, 1] per match (negative = player damaged more
than enemy, positive = enemy damaged more), aggregated across
`n_replicates` matches per opponent.

We restrict to `pruned=false` and `eb_fitness ≠ null` rows for ranker
estimation; pruner rates and saturation diagnostics use the full row
set including pruned trials. Build identity is the
12-character canonical hash of `(hull_id, sorted weapon_assignments,
sorted hullmods, vents, capacitors)` defined in `posthoc_ranker._BuildId`.
Across the 15 studies the analysis sample is **N = 1,744 builds, 17,440
build × opponent matchups**, with 0% within-cell duplicates (§12).

### 1.2 Estimators

We compare four ranker functions, all implemented in
`src/starsector_optimizer/posthoc_ranker.py`. Each takes the same set
of `TrialRecord` rows and returns the top-K by its scoring rule.

**Raw mean.** For build i with matches against opponents j ∈ M_i,
each contributing hp-differential y_{ij},

> R^raw_i  =  (1/|M_i|) Σ_{j ∈ M_i} y_{ij}.

This is the spec-30 baseline. It is unbiased only under random opponent
assignment, which the TPE+pruner pipeline does not provide.

**Two-way fixed effects (TWFE).** We decompose

> y_{ij}  =  μ + α_i + β_j + ε_{ij}

via the iterative within-transformation in
`deconfounding.twfe_decompose`. α̂_i is the build-quality residual; β̂_j
absorbs opponent strength. Standard error σ(α̂_i) is computed from the
within-cell residual variance scaled by 1/√n_i where n_i = |M_i|.
Reference: `docs/reference/phase5a-deconfounding-theory.md`.

**TWFE + Empirical Bayes (EB).** Treating α̂_i as a noisy estimate of a
true build effect α_i drawn from N(0, σ²_α), the posterior mean shrinks
α̂_i toward zero by

> α̂^EB_i  =  α̂_i · σ²_α / (σ²_α + σ²_{e,i}),

with σ²_α and σ²_{e,i} estimated by method-of-moments per cell
(`deconfounding.eb_shrinkage`, with a degenerate zero-column covariate
matrix so the regression prior collapses to the grand mean). Reference:
`docs/reference/phase5d-covariate-adjustment.md`. At uniform n_i this
contraction is rank-preserving (§5).

**Bradley–Terry MAP.** A bipartite logistic skill model
P(build_i beats opp_j) = σ(α^BT_i − β^BT_j), fit by L-BFGS with a
ridge prior N(0, 1/λ) and TIMEOUT matches counted as weighted draws
(weight 0.5). Inputs are WIN/DRAW/LOSS labels only — no
hp-differential magnitude. The fit returns α^BT_i for each build with
diagonal Fisher-information σ. Reference: `posthoc_ranker.py`,
`tests/test_posthoc_ranker.py` synthetic recovery.

### 1.3 Comparison statistics

For two top-K lists T_a and T_b drawn from the same build set:

- **Top-K Jaccard.** J(T_a, T_b) = |T_a ∩ T_b| / |T_a ∪ T_b|. Set
  identity, ignores within-K ordering. Reported with raw overlap count
  |T_a ∩ T_b| in parentheses where ambiguous.
- **Top-K overlap.** |T_a ∩ T_b|, an integer in [0, K].
- **Spearman ρ on top-K positions.** Pearson correlation of the rank
  positions of the union {T_a ∪ T_b} under each ranker. NaN when the
  union has < 2 builds rankable in both lists.
- **Full-pool Spearman ρ.** Same statistic over *all* commonly-ranked
  builds, not just the top-K. More stable, used when the top-K
  intersection is small.
- **Pearson r.** For continuous score comparisons (BT skill vs α̂_EB).

### 1.4 Diagnostics

- **Box-Cox saturation rate.** Per cell,
  > sat = |{i : eb_fitness_i ≥ 0.99}| / N_nonpruned.
  Threshold for the F2a doc gate (`phase5e-shape-revision.md`) is
  **1%**. Cells exceeding 1% are flagged.
- **Imbalance index** for a build × opponent count matrix C.
  > I = Var(C) / Mean(C),
  computed over flattened cells. I = 0 means uniform assignment;
  I → ∞ as assignment concentrates. We summarise per cell as the mean
  over the cell's three seeds. Empirically I > 0.5 indicates
  confounding severe enough that raw-mean rankings are unreliable.
- **Pruner rate.** N_pruned / N_total per (cell, seed). Optuna's
  default MedianPruner. Design band [0.10, 0.60]; outside the band is
  diagnostic-relevant but not a hard gate.
- **EB residual z-score.** For each build,
  > z_i = (α̂_i − α̂^EB_i) / σ(α̂_i).
  Under correct EB calibration, the empirical distribution of z_i has
  mean ≈ 0, std ≤ 1, and bounded tails. Heavy negative tail at low n_i
  indicates heteroscedastic shrinkage is operating as designed.
- **F1c bootstrap CI.** For each cell c we resample its TrialRecord
  set with replacement (bootstrap iterations B = 5,000, seed
  `BOOTSTRAP_SEED = 0xC0DE`), re-fit TWFE+EB on each resample, take
  the top-3 by α̂_EB, and average their α̂_EB scores. We report the
  paired Δ_b(c) = boot_c2[b] − boot_ctrl[b] and its 2.5 / 97.5
  percentile CI. Branch-name follows the validation-plan §7
  decision-tree map: F1c if point Δ < 0; F1e if CI crosses 0 or
  Δ < +0.02; PASS otherwise.

---

## 2. Pooled top-K ranker agreement (the headline finding)

**Method (§1.2).** All four rankers fit on the full 1,744-build pool.
**Statistic (§1.3).** Top-K Jaccard, overlap count, Spearman ρ on top-K
positions, full-pool Spearman ρ.

| Ranker pair               | k=3 overlap | k=5 overlap | k=10 overlap | Full-pool Spearman ρ |
|---|---:|---:|---:|---:|
| raw_mean ↔ twfe           | **0 / 3**  | **0 / 5**  | 2 / 10  | 0.768 |
| raw_mean ↔ twfe_eb        | **0 / 3**  | **0 / 5**  | 2 / 10  | 0.767 |
| raw_mean ↔ bradley_terry  | **0 / 3**  | **0 / 5**  | 1 / 10  | 0.702 |
| twfe ↔ twfe_eb            | 3 / 3      | 5 / 5      | 10 / 10 | **0.9995** |
| twfe ↔ bradley_terry      | 2 / 3      | 2 / 5      | 4 / 10  | 0.902 |
| twfe_eb ↔ bradley_terry   | 2 / 3      | 2 / 5      | 4 / 10  | 0.902 |

![Pooled top-K agreement](../../data/wave1-comprehensive/charts/02_top_k_agreement_pooled.png)

*Figure 1 — pooled top-5 Jaccard with overlap counts. Cells in this
4×4 matrix are J(top-5_a, top-5_b); annotations show J on top, overlap
count on bottom.*

**Reading**:

1. **Raw mean is structurally different from the three principled
   methods at the top of the ranking.** Zero overlap at k=3 and k=5 is
   the deconfounding theorem of `phase5a-deconfounding-theory` in
   action: raw mean confounds opponent draw with build skill, and the
   imbalance index of 0.80–1.00 (§9) is severe enough that the
   confounder dominates.
2. **TWFE+EB ≈ TWFE for ranking.** Where n_i is uniform across builds
   (§5), EB shrinkage is a near-uniform contraction and rank-preserving
   (full-pool ρ = 0.9995). EB still matters for *magnitude* (the F1c
   gate of §4); it does not change *which* builds are top-K.
3. **Bradley–Terry partially agrees with TWFE.** k=3 overlap is 2/3 and
   ρ = 0.90 — i.e. the structural skill model and the deconfounded
   mean converge on the same broad winners but rank #1 differently:
   - TWFE / TWFE+EB top-3: `256e0802f501`, `36538033d63b`, `0c63176968ff`
   - BT top-3: `36538033d63b`, `aea37deb2d5c`, `256e0802f501`

This empirical pattern matches the synthetic-recovery test in
`tests/test_posthoc_ranker.py` and confirms the theoretical prediction
in [`2026-05-10-posthoc-ranker-research.md`](2026-05-10-posthoc-ranker-research.md).
The pooled finding is the production-relevant one because honest-eval
selects top-K *across* a study set, not within a single seed.

---

## 3. Per-cell top-5 agreement

**Method.** §1.2 rankers fit per cell (3 seeds pooled).
**Statistic.** Top-5 Jaccard.

| Cell | n_trials | raw vs TWFE   | raw vs TWFE+EB | raw vs BT | TWFE+EB vs BT |
|---|---:|---:|---:|---:|---:|
| c0a | 359 | 0.25 (2/5)     | 0.25            | 0.11       | 0.43 (3/5)     |
| c0b | 328 | 0.43 (3/5)     | 0.43            | 0.11       | 0.11           |
| c1  | 363 | 0.11 (1/5)     | 0.11            | 0.11       | 0.43           |
| c2  | 390 | **0.00 (0/5)** | **0.00**        | 0.11       | 0.25           |
| c3  | 304 | 0.11 (1/5)     | 0.11            | 0.11       | 0.25           |

![Per-cell top-K agreement matrices](../../data/wave1-comprehensive/charts/01_top_k_agreement_per_cell.png)

*Figure 2 — per-cell top-5 Jaccard heatmaps. Each panel is one cell;
cells in each 4×4 matrix are J(top-5_a, top-5_b).*

**Reading**: every cell shows the raw_mean ↔ TWFE divergence the
pooled table predicts; c2 (the production config, EB + Box-Cox) is the
worst-case zero-overlap cell. This is a strong post-hoc argument that
the pre-2026-05-10 spec-30 raw-mean candidate selection was selecting
builds the principled rankers regard as junk in c2.

---

## 4. F1c training-time gate at α̂_EB

**Method (§1.4).** For each cell, take the top-3 builds by α̂_EB and
report the mean of their α̂_EB scores. Compute the analogous BT-skill
top-3 mean using BT ranking. **Statistic (§1.4).** Bootstrap-paired
Δ_b(c2 vs ctrl) with 5,000 iterations, percentile 95% CI.
**Threshold (§1.4).** F1c branch if point Δ < 0; F1e if CI spans 0 or
Δ < +0.02; PASS otherwise.

**Per-cell point estimates** (higher = stronger top builds):

| Cell                       | top-3 mean α̂_EB | top-3 mean BT skill |
|---|---:|---:|
| c0a (A0 plain TWFE)        | 0.168            | 2.34                 |
| c0b (A scalar-CV)          | 0.186            | 2.19                 |
| **c1 (EB-only)**           | **0.270**        | **2.57**             |
| c2 (production: EB+BoxCox) | 0.158            | 2.44                 |
| c3 (prod + warm-start = 50)| 0.142            | 2.07                 |

**c1 is the best cell** at the point estimate by α̂_EB *and* by BT
skill. c2 (production) is the **4th of 5** by α̂_EB.

**Bootstrap comparisons (B = 5,000)**:

| Comparison                  | Δ point     | 95% CI               | F1c branch       |
|---|---:|---|---|
| c2 vs c0a (EB+BoxCox − A0)  | **−0.010**  | [−0.069, +0.078]     | F1c (CI spans 0) |
| c2 vs c0b (EB+BoxCox − A)   | **−0.028**  | [−0.081, +0.052]     | F1c (CI spans 0) |

**Verdict at training-time α̂_EB**: identical to the validation
report's LOOO-ρ verdict — F1c, both Δs negative at the point estimate,
both 95% CIs span zero. The production config is **not statistically
distinguishable** from either baseline on this metric. The c1 result
is the surprise: the pure-EB cell (no Box-Cox) outperforms both plain
baselines and the full production stack at α̂_EB, suggesting Box-Cox
shaping may be actively *harming* the optimiser's training signal in
this regime.

The training-time α̂_EB ranking does not necessarily survive
out-of-sample evaluation. **Honest-eval will arbitrate**: the in-flight
cloud honest-eval (top-3 builds × 3 seeds = 9 candidates per cell, plus
9 random-baseline builds, 30 replicates each) is the principled
tie-breaker.

---

## 5. α̂ distribution + EB shrinkage diagnostic

### α̂ distribution per cell (TWFE point estimates)

**Method (§1.2).** TWFE α̂_i fit per cell.
**Statistic.** Per-cell summary moments + 5/95 percentile.

| Cell | n_builds | mean α̂   | std α̂        | min α̂  | max α̂        | p05    | p95    |
|---|---:|---:|---:|---:|---:|---:|---:|
| c0a | 359      | −0.001     | 0.166          | −0.54   | +0.71          | −0.25  | +0.25  |
| c0b | 328      | −0.001     | 0.174          | −0.51   | +0.56          | −0.28  | +0.31  |
| c1  | 363      | −0.001     | **0.183**      | −0.52   | **+0.78**      | −0.29  | +0.28  |
| c2  | 390      | −0.002     | 0.168          | −0.41   | +0.44          | −0.28  | +0.29  |
| c3  | 304      | −0.004     | 0.168          | −0.54   | +0.47          | −0.31  | +0.25  |

![α̂ distribution per cell](../../data/wave1-comprehensive/charts/03_alpha_distribution_per_cell.png)

*Figure 3 — TWFE α̂ distribution per cell (violin + box; mean shown as
white diamond). 3 seeds pooled within each cell.*

**Reading**:

- All mean α̂ ≈ 0 — TWFE intercept absorbs the grand mean as designed.
- **c1 has the widest spread** (std 0.183, max 0.78) → consistent with
  c1 being the best cell at the top-3 cut; c1 contains genuinely
  strong builds the others do not reach.
- **c2 and c3 have compressed maxima** (+0.44, +0.47 vs c1's +0.78).
  TWFE α̂ is derived from raw `hp_differential`, so the compression is
  in the underlying signal, not the Box-Cox shaping. This suggests the
  *combination* of EB + Box-Cox + warm-start in c3 (or Box-Cox in c2)
  is steering the TPE proposer away from the high-tail region of build
  space.

### EB shrinkage scatter (α̂ vs α̂_EB)

**Method.** Per cell, OLS-fit slope of α̂_EB on α̂.
**Statistic.** Slope b ∈ [0, 1]: 1 = no shrinkage, 0 = full collapse to
mean. Theoretically b = σ²_α / (σ²_α + σ²_{e,i}); empirically b is the
average over the cell's distribution of n_i.

| Cell | shrinkage slope b | n_builds | mean n_i (matches per build) |
|---|---:|---:|---:|
| c0a | 0.323             | 359      | 10.0 |
| c0b | 0.347             | 328      | 10.0 |
| c1  | **0.463**         | 363      | 9.95 |
| c2  | 0.372             | 390      | 9.85 |
| c3  | 0.351             | 304      | 9.09 |

![α̂ vs α̂_EB scatter per cell](../../data/wave1-comprehensive/charts/04_alpha_eb_shrinkage_scatter.png)

*Figure 4 — α̂ (x) vs α̂_EB (y) per cell, points coloured by n_i. The
y = x dashed line is the no-shrinkage reference; the slope b annotated
in each panel is the OLS-fit shrinkage factor.*

**Reading**: EB shrinks point estimates by a factor of ~0.32–0.46 —
meaningful contraction, indicating per-build standard errors are
substantial relative to between-build α̂ variance. c1's slope is the
largest because c1's between-build α̂ variance is also the largest
(see std column above).

**Why ranks barely change despite shrinkage**: at uniform n_i per
build, EB applies a near-uniform multiplicative contraction, preserving
the order. ρ(α̂, α̂_EB) ≈ 0.9995 across the pool (§2). Shrinkage
matters for the *magnitude* of Δs — and therefore for F1c bootstrap
CIs — but not for top-K identity.

### Stein-style EB residual diagnostic

**Method (§1.4).** z_i = (α̂_i − α̂^EB_i) / σ(α̂_i) per build.
**Statistic.** Mean, std, median \|z\|, max \|z\| per cell, plus n_i
range to qualify shrinkage homogeneity.

| Cell | mean z | std z  | median \|z\|  | max \|z\|  | n_i min / max |
|---|---:|---:|---:|---:|---|
| c0a | 0.000  | 0.824  | 0.61          | 3.53        | 10 / 10  |
| c0b | 0.000  | 0.809  | 0.50          | 2.60        | 10 / 10  |
| c1  | 0.000  | 0.734  | 0.49          | 3.15        | 9 / 10   |
| c2  | 0.000  | 0.795  | 0.55          | 2.09        | 7 / 10   |
| c3  | 0.002  | 0.810  | 0.59          | 2.61        | 6 / 10   |

**Reading**: residual z-scores are well-behaved — mean ≈ 0 (centered),
std slightly under 1 (EB legitimately shrinks variance below its
scale), no extreme outliers (max \|z\| ≤ 3.5). No EB pathology in any
cell. c2 and c3 show variable matchup counts because higher pruner
rates in their seeds end studies before all opponents have been faced
— relevant for the pruner discussion in §7.

---

## 6. Box-Cox saturation per cell

**Method (§1.4).** `eb_fitness ≥ 0.99` rate among finalized non-pruned
trials.
**Threshold (§1.4).** F2a fails at >1%.

| Cell                              | n_finalized | n_saturated | sat %      | F2a verdict |
|---|---:|---:|---:|---|
| c0a (no EB, no BoxCox)            | 359         | 0           | 0.00%      | PASS        |
| c0b (no EB, scalar-CV)            | 328         | 0           | 0.00%      | PASS        |
| c1  (EB on, no BoxCox)            | 363         | 2           | 0.55%      | PASS        |
| c2  (EB + BoxCox, production)     | 390         | 0           | 0.00%      | PASS        |
| **c3 (production + warm-start)**  | 304         | 9           | **2.96%**  | **FAIL**    |

![Box-Cox saturation per cell](../../data/wave1-comprehensive/charts/05_boxcox_saturation_per_cell.png)

*Figure 5 — % of finalized non-pruned trials with eb_fitness ≥ 0.99
per cell. Dashed line = 1% F2a doc-gate threshold; orange bars exceed
the gate.*

**Reading**: c3 trips F2a (population has degenerate-λ Box-Cox fits,
per `phase5e-shape-revision`). c2 (the same Box-Cox config without
warm-start) is fine — the warm-start mechanism in c3 may be amplifying
saturation by seeding the TPE with high-quality builds whose shaped
fitness floors at the ceiling.

**Note vs prior validation report**: the validation report
([§2 mech 8](2026-05-10-wave1-validation.md)) cites c2 saturation at
**4.78%** using a different definition (`fitness` post-shape, not
`eb_fitness`); under the eb-fitness definition used here c2 is 0%.
**Both metrics matter** — `fitness` saturates in c2 because Box-Cox is
the shaping that produces the ceiling, but `eb_fitness` rebases through
the TWFE+EB layer and recovers signal. The discrepancy is informative:
it is exactly the regime where the optimiser's loss surface (shaped
fitness) saturates while the deconfounded build-quality estimate
(`eb_fitness`) does not — i.e. Box-Cox is throwing away signal the
underlying data still contains.

---

## 7. Pruner rate per (cell, seed)

**Method (§1.4).** N_pruned / N_total per (cell, seed) over the full
JSONL row set.
**Threshold.** Design band [0.10, 0.60]; outside is diagnostic.

| Cell | seed 0      | seed 1   | seed 2     | cell mean | total trials |
|---|---:|---:|---:|---:|---:|
| c0a | 32.7%       | 37.1%    | 36.1%      | 35.4%     | 556          |
| c0b | **49.2%**   | 6.6%     | 33.1%      | 30.7%     | 473          |
| c1  | 6.8%        | 28.8%    | 21.5%      | 19.2%     | 449          |
| c2  | 13.7%       | 21.0%    | **0.7%**   | 12.2%     | 444          |
| c3  | 42.9%       | 18.2%    | 36.5%      | 32.7%     | 452          |

![Pruner rate per (cell, seed)](../../data/wave1-comprehensive/charts/06_pruner_rate_per_cell.png)

*Figure 6 — Optuna MedianPruner rate per (cell, seed). Shaded band =
design [0.10, 0.60].*

**Reading**:

- **Cross-seed variance is high**: c0b/seed0 prunes ~50% of trials
  while c0b/seed1 prunes 7% — TPE's exploration trajectory is highly
  seed-dependent in early hammerhead, consistent with the validation
  report's observation that pruner gating is non-trivial under
  non-stationary fitness.
- **c2 has the lowest pruner rate** (12.2% mean, with one seed at
  0.7%). Box-Cox shaping makes early-stage `fitness` look elevated, so
  the median pruner threshold is rarely crossed → fewer pruned trials
  → more finalized trials within the same budget. This is consistent
  with the validation report's observation that c2/c3 reach more
  finalized trials per dollar than c1.
- **c3 has the highest pruner rate (32.7%) despite using the same
  Box-Cox shaping as c2** — warm-start seeds the TPE distribution
  toward high-quality builds, which raises the pruner's running median,
  which prunes more of the subsequent low-quality exploration trials.
  Warm-start ≠ "free trials"; it shifts the pruner equilibrium.

---

## 8. Pruner × Box-Cox saturation cross-tab

**Method.** Per cell, pair (pruner_rate, saturation_rate_finalized) and
report Pearson r over the 5 cell-level points.
**Statistic.** Pearson r(pr, sat).

| Cell | finalized | finalized saturated | saturation %  | pruner rate |
|---|---:|---:|---:|---:|
| c0a | 359       | 0                   | 0.00%          | 35.4%        |
| c0b | 328       | 0                   | 0.00%          | 30.7%        |
| c1  | 363       | 2                   | 0.55%          | 19.2%        |
| c2  | 390       | 0                   | 0.00%          | 12.2%        |
| c3  | 304       | 9                   | **2.96%**      | 32.7%        |

Pearson r(pruner_rate, saturation_rate_finalized) over 5 cells = **+0.32**.

**Reading**: a *modest* positive coupling — cells that prune more also
saturate more in finalized trials. The coupling is weak (n=5), but the
direction makes sense: aggressive pruning concentrates the finalized
sample on builds that survived early intermediate-value reporting,
which biases toward high-fitness outcomes that may saturate at the
Box-Cox ceiling. **c3 dominates this signal** — without c3 the
correlation collapses. The mechanistic interpretation is that c3's
warm-start + Box-Cox combination is the pathological regime; c2
(Box-Cox without warm-start) does not exhibit it.

---

## 9. Confounding diagnostics — build × opponent imbalance

**Method (§1.4).** I = Var(C) / Mean(C) over the flattened cells of
the build × opponent count matrix.
**Statistic.** Per-cell mean of I across 3 seeds.
**Threshold.** Empirical: I > 0.5 indicates confounding severe enough
to make raw-mean rankings unreliable; I ≥ 1 indicates near-rank-1
imbalance.

| Cell    | seed 0 I | seed 1 I    | seed 2 I    | cell mean I |
|---|---:|---:|---:|---:|
| c0a     | 0.808    | 0.811       | 0.815       | 0.811       |
| c0b     | 0.783    | 0.815       | 0.804       | 0.800       |
| c1      | 0.821    | 0.819       | 0.823       | 0.821       |
| c2      | 0.834    | 0.851       | 0.845       | 0.844       |
| **c3**  | 0.890    | **0.986**   | **1.117**   | **0.998**   |

![Build × opponent matchup heatmap (c2/seed0)](../../data/wave1-comprehensive/charts/07_confounding_heatmap_seed0.png)

*Figure 7 — (a) build × opponent matchup-count heatmap for the
representative cell (c2/seed0): 113 builds × 52 opponents, 19% density,
I = 0.83. (b) Per-cell mean imbalance index, pooled over 3 seeds.*

**Reading**:

- **Confounding is severe in every cell** (I ≥ 0.78). This is the
  empirical justification for the entire `phase5a` TWFE pipeline — the
  raw-mean ↔ TWFE 0/3 disagreement we saw in §2 *must* exist with this
  level of imbalance.
- **c3 is pathological** — mean I = 1.0, with seed2 at I = 1.12. The
  metric exceeds 1 when an off-diagonal block of the matchup matrix is
  rank-deficient enough that variance overshoots the mean; in practice
  c3/seed2 has matchup patterns where most builds see the same handful
  of opponents. This is the warm-start side-effect: cluster the
  proposer in build-space → cluster the opponent assignment →
  rank-deficient design.
- **c2 has the most balanced production-config matchup matrix** (I =
  0.84) — but still bad enough that no honest-eval should be done off
  raw means alone in this regime.

---

## 10. Pooling — does within-cell aggregation help rank stability?

**Method.** For each cell, fit TWFE+EB on the cell-pooled data (3
seeds concatenated) and on each seed individually. Compare the
cell-pooled top-10 to each seed's top-10.
**Statistic.** Top-10 Jaccard + Spearman ρ on rank positions of the
union of the two top-10 sets.

| Cell        | per-seed Jaccard (mean)  | per-seed Spearman ρ (mean) |
|---|---:|---:|
| c0a         | 0.22                     | 0.79                          |
| c0b         | 0.21                     | 0.88                          |
| c1          | 0.20                     | 0.86                          |
| c2          | 0.21                     | **0.90**                      |
| c3          | 0.19                     | **0.56**                      |
| **overall** | **0.21**                 | **0.80**                      |

![Pooling rank stability — per-seed vs cell-pooled top-10](../../data/wave1-comprehensive/charts/08_pooling_rank_stability.png)

*Figure 8 — (a) per-seed top-10 Jaccard against the within-cell-pooled
top-10. (b) Spearman ρ over the union of top-10 ids. The c3/seed0
ρ = −0.13 negative bar is the headline anomaly.*

**Reading**:

- **Within a cell, single-seed top-10 and pooled top-10 agree only
  ~20% by exact membership** — i.e. seeds genuinely propose different
  builds, and pooling is *not* a no-op. This is true even though TWFE
  *ranking* is stable across seeds (ρ ≈ 0.80).
- **c3 fails the rank-stability check** — ρ = 0.56, with seed0
  individually showing ρ = **−0.13** (anti-correlated to the
  cell-pool!). c3/seed0 ranks builds in opposite order to c3/seed1+
  seed2 on a substantial fraction of the build set. Combined with the
  imbalance index of 1.12 in c3/seed2 (§9), c3 cannot be trusted as a
  single configuration — its three seeds are sampling three
  qualitatively different regions of build-space.
- **c2 is the most stable cell** (ρ = 0.90). Production-config
  Box-Cox + EB delivers consistent rankings across seeds even though
  it picks worse builds at the top-3 cut (§4).

**Implication for the validation plan**: c3's instability is a *new*
finding (the validation report's headline gates do not detect this
particular failure mode). The decision tree should treat c3 as an
"inconclusive" cell rather than a comparable one. A clean F1c-scoped
question — "does the production config beat the baseline?" — should
compare c2 against c0a/c0b only; c3 should be re-run with more seeds
or more trials per seed before being used for any production gate.

---

## 11. Bradley–Terry vs TWFE+EB — cross-method validation

**Method.** Pooled across all 1,744 builds, fit BT and TWFE+EB.
Compute the standardised disagreement \|Δz\| = \|z_BT − z_EB\| where
each z is the within-method z-score; flag builds with \|Δz\| > 1.
**Statistic.** Pearson r(BT skill, α̂_EB), top-K overlap, count of
\|Δz\| > 1 builds.

| Metric                                     | Value          |
|---|---|
| Pearson r(BT skill, TWFE+EB α̂)              | **+0.898**     |
| n_builds disagreeing by \|Δz\| > 1          | 58 (3.3%)      |
| Top-3 overlap                                | 2 / 3          |
| Top-5 overlap                                | 2 / 5          |
| Top-10 overlap                               | 4 / 10         |

![BT skill vs TWFE+EB α̂ scatter](../../data/wave1-comprehensive/charts/09_bt_vs_twfe_eb_alpha.png)

*Figure 9 — Bradley–Terry skill (logit units) vs TWFE+EB α̂ (residual
hp-differential, dimensionless), pooled across all builds. Orange
points are the 58 builds (3.3%) outside the standardised \|Δz\| > 1
band. Dashed black line is the OLS fit.*

**Reading**: BT (which uses *only* the WIN/DRAW/LOSS label per
matchup, no `hp_differential` magnitude) and TWFE+EB (which uses the
magnitude plus opponent-FE deconfounding) agree at r = 0.90 — strong
cross-method validation. The 3.3% of builds where they disagree by
more than 1σ are where label-only and magnitude-deconfounded views
diverge: typically these are builds that *win cleanly* but with low
margins, or *lose gracefully* with moderate margins (where margin
matters more than outcome). Honest-eval sees full hp_differential, so
it should agree with TWFE+EB on those 3.3% — but the broad concordance
lets us treat the BT top-K as a corroborating signal when TWFE+EB and
BT both flag a build.

---

## 12. Search coverage

**Method.** Distinct Build hashes per cell + cumulative-distinct vs
trial number per seed.
**Statistic.** Duplicate rate = 1 − n_distinct / n_finalized;
cumulative distinct curve relative to y = x reference.

| Cell | n_finalized | n_distinct_builds | duplicate rate |
|---|---:|---:|---:|
| c0a  | 359         | 359               | 0.00%          |
| c0b  | 328         | 328               | 0.00%          |
| c1   | 363         | 363               | 0.00%          |
| c2   | 390         | 390               | 0.00%          |
| c3   | 304         | 304               | 0.00%          |

![Search coverage — cumulative distinct builds](../../data/wave1-comprehensive/charts/10_search_coverage.png)

*Figure 10 — (a) finalized trials vs distinct builds per cell. (b)
cumulative distinct Build hashes vs trial number, with y = x reference.
The near-perfect overlap of the curves with y = x is the 0% duplicate
visual signature.*

**Reading**: the TPE proposer never re-proposes the same Build hash
within a cell — full 100% distinctness in finalized trials across all
configurations. This rules out cache-hit / duplicate-build artefacts
explaining any of the rank disagreements above; rank divergence is
real signal disagreement, not a sampling artefact.

---

## 13. Synthesis & decisions

What the data says, in one paragraph each:

**Raw mean is unfit for honest-eval candidate selection in this
regime.** Pooled top-3 overlap with all three principled rankers is
exactly 0. This is consistent with imbalance indices of 0.80–1.00
across cells — opponent confounding is at the level where a
deconfounded estimator is *required*, not optional. The honest-eval
pipeline now uses TWFE+EB by default (per task #108); raw mean is
retained only as a baseline.

**TWFE+EB is the right ranker for this dataset.** TWFE+EB ≈ TWFE for
ranking (ρ = 0.9995) but EB shrinkage delivers calibrated magnitudes
(the §4 F1c CIs are non-trivial because EB shrinks the noisy top-3
means). EB residual diagnostics (§5) are clean. BT corroborates at
r = 0.90.

**The production stack (c2: EB + Box-Cox) does not beat the EB-only
baseline (c1) at training time.** c1 leads c2 by Δα̂_EB = +0.112 at the
top-3 cut, with a similar lead in BT skill. The c2-vs-c0a / c0b F1c
gate is a null result (CIs span zero). This is *training-time* signal
only — honest-eval will tell us whether the c1 lead is genuine or an
artefact of c1's wider α̂ distribution (§5).

**c3 (production + warm-start) is broken-as-tested.** c3 trips Box-Cox
saturation at 2.96% (F2a fail), has the highest confounding imbalance
(1.00 mean, with a seed2 above 1.0 indicating rank-deficient design),
and has unstable per-seed rankings (ρ = 0.56 with one seed
anti-correlated). c3 should not be a comparable cell in the validation
plan's decision tree until it is re-run with more replicates per
configuration; the warm-start mechanism (mech 2) needs investigation
before Wave 2.

**Pooling within a cell is informative but not a replacement for
multi-seed.** Per-seed top-10 vs cell-pooled top-10 Jaccard is only
~20% across all cells — single-seed top-K in this regime is genuinely
unreliable. The TWFE+EB rank order is more stable across seeds than
the top-K membership, suggesting decisions should be made on rank
rather than on top-K identity until honest-eval lands.

---

## 14. Open questions for honest-eval

- Does c1's training-time α̂_EB lead survive out-of-sample? If yes →
  Box-Cox is actively harming the optimiser, and the production
  default should drop it.
- Do the TWFE+EB top-3 (`256e0802f501`, `36538033d63b`, `0c63176968ff`)
  beat the raw-mean top-3 (`5eb9c1a29a9a`, `e31d934a6943`,
  `4e1bf87ea37d`) in honest oracle scores? If yes → empirically
  validates the new TWFE+EB-by-default selection (task #108).
- Do c3's three seeds rank the same set of top builds? If not (ρ ≈ 0.56
  predicts not) → the warm-start study config produces a per-seed
  top-K that is essentially random within a high-quality envelope, and
  cannot be the production default.
- Does the random-feasible baseline cell sit near or below c0a? If
  comparable to c0a → optimisation is barely better than random in
  the hammerhead/early regime, and Wave 2's broader hull/regime sweep
  becomes the gating evidence.

These are the questions the in-flight honest-eval campaign
(`starsector-honest-eval-wave1-c0a-20260510T170431Z`) is designed to
answer. This report provides the training-time priors against which
those out-of-sample answers will be compared.

---

## Appendix A — File map

- Producer script: `scripts/analysis/wave1_comprehensive_analysis.py`
- Numeric headline JSON: `data/wave1-comprehensive/headline_numbers.json`
- Charts: `data/wave1-comprehensive/charts/01..10_*.png` (200 dpi)
- Underlying JSONL inputs:
  `data/logs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl`
- Ranker module: `src/starsector_optimizer/posthoc_ranker.py`
- Theory: `docs/reference/phase5a-deconfounding-theory.md`,
  `docs/reference/phase5d-covariate-adjustment.md`,
  `docs/reference/phase5e-shape-revision.md`
- Companion reports: `docs/reports/2026-05-10-wave1-validation.md`,
  `docs/reports/2026-05-10-posthoc-ranker-research.md`
