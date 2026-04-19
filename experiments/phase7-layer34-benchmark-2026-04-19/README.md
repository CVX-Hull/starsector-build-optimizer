# Phase 7 Layer 3 + Layer 4 benchmark — 2026-04-19

Validates the two advanced items from the Phase 7 fix plan using synthetic
landscapes matched to the aborted-run empirical `(τ̂², σ̂²)` distribution.

- **Layer 3**: class-blocked heteroscedastic GP on mixed-hull training
  data. Claim: recovers per-hull noise accurately; preserves posterior
  quality on low-noise hulls that a homoscedastic GP would inflate.
- **Layer 4**: TurBO-style trust-region BO with adaptive restart. Claim:
  on plateau-with-peak landscapes (analogue of flat frigate regions
  surrounded by a few winning builds), the trust-region restart finds
  the peak faster than vanilla EI over a global candidate pool.

Benchmarks use `numpy` + `scipy` only — no torch/botorch dependency, so
the claims are reproducible in the current repo environment.

## Layer 3 results (5 seeds × 100 points per hull × 4 hulls)

Synthetic hulls matched to the observed τ² / σ² pattern:

| hull | τ² (between-build) | σ² (observation noise) |
|------|--------------------|------------------------|
| frigate_flat (lasher-like) | 2e-7 | 6e-5 |
| frigate_weak (wolf-like) | 1e-4 | 2e-3 |
| destroyer_signal (hammerhead-like) | 8e-4 | 3e-2 |
| cruiser_signal (sunder-like) | 1.3e-3 | 3e-2 |

### Per-hull σ² recovery (log-orders of error vs ground truth)

| hull | homoscedastic fit | err | heteroscedastic fit | err |
|------|-------------------|-----|---------------------|-----|
| cruiser_signal (true 3e-2) | 1.34e-2 | 0.35 | 2.97e-2 | **0.00** |
| destroyer_signal (true 3e-2) | 1.34e-2 | 0.35 | 3.41e-2 | **0.06** |
| frigate_flat (true 6e-5) | 1.34e-2 | **2.35** | 6.37e-5 | **0.03** |
| frigate_weak (true 2e-3) | 1.34e-2 | 0.83 | 2.15e-3 | **0.03** |

Homoscedastic collapses to a single estimate (1.34e-2) that is wrong
everywhere, most catastrophically on frigate_flat (2.35 orders off —
the GP thinks flat-frigate observations are 220× noisier than they
actually are). Heteroscedastic recovers every hull's true noise level
to within 0.06 log-orders.

### Predictive RMSE and held-out log-likelihood

| hull | homo RMSE | het RMSE | Δ | homo LL | het LL | Δ |
|------|-----------|----------|---|---------|--------|---|
| cruiser_signal | 0.0318 | 0.0312 | −0.0007 | +0.173 | +0.298 | +0.125 |
| destroyer_signal | 0.0267 | 0.0260 | −0.0006 | +0.155 | +0.297 | +0.142 |
| frigate_flat | 0.0064 | **0.0005** | **−0.0059** | +1.135 | **+3.391** | **+2.256** |
| frigate_weak | 0.0134 | 0.0094 | −0.0040 | +1.074 | +1.665 | +0.592 |

Largest win on frigate_flat: 13× lower RMSE (0.0005 vs 0.0064) and
2.26 units higher log-likelihood (e^2.26 ≈ 9.6× likelihood ratio).
The hetGP correctly identifies that frigate_flat's true function is
nearly constant, so its mean prediction is nearly zero — whereas the
homoscedastic GP, forced into a compromise noise estimate, produces
noisy point predictions that hurt on every hull.

### Layer 3 verdict

**Adopt.** Heteroscedastic noise structure recovers the observed
per-hull variance spread without any manual filtering. This is the
mathematical mechanism that lets Phase 7 keep popular-but-flat frigate
hulls in the training set without them polluting the posterior for
working hulls.

## Layer 4 results

_(benchmark running in background, results to be appended)_

## Files

```
src/
├── synthetic.py       Landscape generators matched to observed τ²/σ²
├── gp.py              HomoscedasticGP + HeteroscedasticGP (scipy-only)
└── bo.py              vanilla_bo + turbo_bo (EI acquisition, trust-region restart)
benchmark_layer3.py    Layer 3 driver (per-hull σ² recovery + RMSE + LL)
benchmark_layer4.py    Layer 4 driver (plateau-peak, regret curves)
layer3_raw.csv         Per-hull per-seed metrics
layer3_agg.csv         Mean-aggregated across seeds
layer4_traces.csv      Best-so-far trace per (method, seed, iter)
README.md              This file
```
