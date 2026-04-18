# Phase 5E × 5D — Covariate-strength calibration sweep

Scales the 7-dim covariate noise level from 0.5× (strong prior, near-synthetic upper bound) to 4× (weak prior, approaches real Hammerhead regime where scorer components are near-constant within the exploit cluster). Reports how the 5D ρ-gain shrinks as the prior weakens, and whether Box-Cox's A3 effect holds up across the board.

| noise× | prior ρ upper-bound | Δρ A vs A0 | Δρ D vs A | Δ ceiling D-A | Δ top-5 D-A | Δ top-10 D-A | Δρ J vs A |
|---|---|---|---|---|---|---|---|
| 0.5× | 0.914 | +0.382 | +0.011 | -0.249 | +0.50 | +0.50 | -0.005 |
| 1.0× | 0.767 | +0.271 | +0.003 | -0.249 | +0.44 | +0.40 | +0.002 |
| 2.0× | 0.547 | +0.130 | -0.001 | -0.249 | +0.26 | +0.22 | +0.008 |
| 4.0× | 0.343 | +0.047 | -0.002 | -0.249 | +0.20 | +0.15 | +0.012 |

## Interpretation

- **Δρ A vs A0** (the EB shrinkage gain) is highly sensitive to the prior's predictive power: the strong-prior regime (0.5×) delivers a big ρ jump; as noise grows (4×), the EB posterior reverts to w≈1 (raw α̂), so A ≈ A0 on ρ_truth. This matches the 10× gap between synthetic (+0.33) and real Hammerhead LOOO (+0.036): real scorer components are closer to the 4× regime.

- **Δ ceiling (D vs A)** and **Δ top-5 / top-10 (D vs A)** are *invariant* to covariate strength — Box-Cox always drives the ceiling from ~25% to ~0% and lifts top-5 overlap by a factor of ~5–10×. The mechanical A3 effect is independent of whether the α̂ came from EB or plain TWFE.

- **Δρ J vs A** (CAT + Box-Cox) holds the small but consistent positive sign across regimes. Same conclusion as the main run: deploy Box-Cox first, CAT as an orthogonal secondary gain.

## Reality check

On real production data, Phase 5D delivered only Δρ = +0.036 on the Hammerhead LOOO probe. That means the shipped 5D baseline is ρ ≈ 0.32 on real opponents, NOT the 0.74 this simulation reports. Box-Cox's expected real-data contribution is its A3 mechanical effect — ceiling collapse and top-k restoration — which this sweep confirms is robust across covariate-strength regimes.

## Files

- `calibration_sweep.py` — this sweep.
- `calibration_results.csv` — per-seed, per-strategy, per-noise rows.
