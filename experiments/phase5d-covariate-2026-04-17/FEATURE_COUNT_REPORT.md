# Feature-count × dataset-size sweep (Phase 5D)

**Date:** 2026-04-17
**Harness:** `feature_count_sweep.py`
**Runtime:** 4.8 min, 504 cells (6 seeds × 3 N × 7 p_useful × 4 p_noise)

## Question

Given realistic dataset budgets from the actual optimizer throughput, how does the HN empirical-Bayes estimator respond as the prior's feature count varies? Specifically: where is the sweet spot, where does `p/N` overfit kick in, and how much does noise-dilution cost?

## Data-collection budget context

Measured from `experiments/hammerhead-twfe-2026-04-13/optimizer.log`:

| Configuration | Throughput | 8h | 24h | 72h |
|---|---|---|---|---|
| 4 instances, WilcoxonPruner + ASHA | ~27 trials/hr | ~215 | ~650 | ~1950 |
| 4 instances, no pruner (overnight baseline) | ~6 trials/hr | ~48 | ~145 | ~430 |

The TWFE-pruner configuration is what's shipped. A 10h overnight gives ~270 builds, a 24h run gives ~650, a 3-day run gives ~1950. Sweep cells (N = 200, 368, 900) bracket this range.

## Sweep grid

- `p_useful` ∈ {0, 1, 2, 4, 8, 13, 20} — nested σ pool U(0.5, 1.3) sorted best→worst; k-th feature has σ_k fixed across cells.
- `p_noise` ∈ {0, 2, 6, 12} — independent N(0,1) columns (adversarial dilution).
- `n_builds` ∈ {200, 368, 900}.
- 6 seeds per cell.

The σ pool is calibrated against the production scorer: `composite_score` has σ ≈ 1.2 vs true quality (ρ ≈ 0.45), best individual components would sit around σ ≈ 0.5–0.8. This covers the realistic range.

## Findings

### 1. Pure-noise X is mildly harmful, not catastrophic

When `p_useful = 0`, adding noise columns costs 0.02–0.03 Δρ at most (N=368, p_noise=12: Δρ = −0.027). The OLS γ̂ absorbs spurious residual correlations, but the MoM τ² floor (`floor_tau2_frac=0.05 · Var(α̂)`) prevents over-shrinkage. HN degrades gracefully.

### 2. Gains saturate around p_useful ≈ 8–13

Mean Δρ(HN − A0) by p_useful, averaged over all p_noise and N:

| p_useful | Mean Δρ | Interpretation |
|---|---:|---|
| 0  | −0.008 | pure-noise cost |
| 1  | +0.150 | **big jump** from first informative feature |
| 2  | +0.222 | halving residual prior variance |
| 4  | +0.283 | current shipped HN covers this regime |
| 8  | +0.320 | +0.037 over p=4 |
| 13 | +0.346 | +0.026 over p=8 |
| 20 | +0.340 | flat/slight drop vs p=13 |

Diminishing returns set in at p ≈ 8. The 13th scorer component adds ~0.03 Δρ; the 14th–20th add nothing on average (and actively hurt at small N).

### 3. p/N overfit only bites at N=200, p ≥ 13

| N | p_useful=20, p_noise=12 | p_useful=13, p_noise=0 |
|---|---:|---:|
| 200 | +0.285 | +0.358 |
| 368 | +0.292 | +0.331 |
| 900 | +0.372 | +0.380 |

At N=200 with p_total=32, HN loses ~0.07 vs the p=13, p_noise=0 peak — still positive, still passes the ship gate, but the advantage halves. At N=900 the penalty disappears. Rule of thumb: keep `p_total ≤ N/15` for negligible overfit cost.

### 4. Noise dilution is ~1/4 as costly as the useful-feature gain

At N=368, p_useful=13:

| p_noise | Mean Δρ | Δ from p_noise=0 |
|---|---:|---:|
| 0  | +0.331 | 0 |
| 2  | +0.336 | +0.005 (noise) |
| 6  | +0.332 | +0.001 |
| 12 | +0.315 | −0.017 |

Twelve noise columns cost 0.017. So a "don't know if it's useful" feature has expected value ≈ 0.026 − 0.017/12 ≈ +0.024, which still clears the ship gate unless N is small.

### 5. Ship gate

72/84 (p_useful, p_noise, N) cells passed the Δρ ≥ +0.02 ship gate. All 12 failing cells had `p_useful = 0`. Any nonzero useful-feature count passes.

## Implications for Phase 5D HN

**Current shipped X**: 13 scorer components + 3 build-structure counts = 16 features. At Hammerhead-scale N ≈ 300-400, p_total = 16 → `p/N ≈ 0.045` → comfortable.

**If we add engine-computed `MutableShipStats` reads** (the Java-side proposal — effective speed/armor/HP/flux post-hullmod): p_total jumps to ≈ 22–25. At N=368 this is safe (p/N = 0.07, still within the 1/15 rule). At N=200 (short overnight) we'd see a ~0.05 Δρ penalty vs the p=13 peak, but still pass the gate.

**Practical recommendation**: plan for p_total ≤ 20. Either:
- Keep the current 13-scorer-component X and *swap* some hand-engineered ones for engine-computed equivalents (net feature count unchanged).
- Add engine-computed features *alongside* scorer components only if ship-runs are ≥ Hammerhead-scale (≥300 builds).
- Don't pile on 30+ one-hot weapon-tier / hullmod-category indicators at N=200 — the sweep suggests that regime is flat-to-harmful.

**Dataset-size recommendation**: N=368 is already past the point where additional features help more than proportionally. For the typical overnight-to-day ship-experiment cadence (200–650 builds), the current 16-feature X is close to optimal. Longer runs (N=900+) justify pushing p to ~20.

## Artifacts

- `feature_count_results.csv` — raw 504-row cell data
- `feature_count_heatmap.png` — 3 rows × 3 N columns: Δρ, w̄, ρ(HN, truth)
- `feature_count_curves.png` — Δρ line plots by (N, p_noise)
