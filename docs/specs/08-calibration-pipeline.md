# Calibration Pipeline Specification

Random build generation and feature extraction. Defined in `src/starsector_optimizer/calibration.py`.

## Functions

### generate_random_build(hull, game_data, rng) → Build
Random weapon per slot (70% fill, 30% empty), random hullmod subset (20% each), random vent_fraction. Apply `repair_build`.

### generate_diverse_builds(hull, game_data, n, seed=42) → list[Build]
Generate n valid builds using `generate_random_build`.

### compute_build_features(build, hull, game_data) → dict[str, float]
Extract all ScorerResult metrics as flat dict.

### calibrate_weights(features, scores) → dict[str, float]
Stub: returns DEFAULT_WEIGHTS. Will fit regression when simulation data available.

## Default Weights
```python
DEFAULT_WEIGHTS = {
    "total_dps": 0.25, "flux_efficiency": 0.20, "flux_balance": 0.20,
    "effective_hp": 0.15, "range_coherence": 0.10, "damage_mix": 0.10
}
```
