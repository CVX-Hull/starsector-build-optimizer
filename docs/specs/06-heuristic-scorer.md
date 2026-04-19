# Heuristic Scorer Specification

Static build quality metrics. Defined in `src/starsector_optimizer/scorer.py`.

Pure weapon-data arithmetic. The scorer does **not** apply hullmod
effects — that's the engine's job, surfaced via `EngineStats` from
the Java SETUP hook (spec 13). `compute_effective_stats` was
deleted with `hullmod_effects.py` 2026-04-19; see spec 29 for the
manifest-as-oracle invariant.

## Functions

### heuristic_score(build, hull, game_data, manifest) → ScorerResult
Computes all metrics and weighted composite score. Reads weapon
stats from `manifest.weapons` and hull stats from `hull` directly.

## Metrics
- **flux_balance**: `total_weapon_flux / hull.flux_dissipation`
  (ideal 0.4–0.8).
- **total_dps**: sum of `w.sustained_dps` over all assigned weapons.
- **kinetic/he/energy_dps**: DPS by damage type.
- **flux_efficiency**: `total_dps / total_weapon_flux`.
- **effective_hp**: `hull.hitpoints + armor_ehp + shield_ehp` — raw
  hull numbers, no hullmod adjustment (EngineStats carries the
  hullmod-adjusted numbers for the EB prior).
- **range_coherence**: `1 - cv(ranges)` for non-PD weapons.
- **damage_mix**: rewards kinetic + HE combination.
- **engagement_range**: DPS-weighted mean weapon range.
- **op_efficiency**: `(total_dps + ehp_factor) / op_used`, where
  `op_used` comes from `compute_op_cost(build, hull, manifest)`.
- **composite_score**: weighted sum of normalized metrics.

## Covariate-vector role

Of the above, only 3 feed the 10-dim EB covariate vector
(spec 24, spec 28): `total_dps` (as `total_weapon_dps`),
`engagement_range`, `kinetic_dps_fraction`. The remaining
aggregates are retained for warm-start ranking and notebook use.

`composite_score` was dropped from the covariate vector 2026-04-19
because 11–22% of |γ̂| was flowing through its structurally
drift-prone weighted combination (the pre-manifest
`hullmod_effects.py` registry under-modeled game rules).
`composite_score` itself is still emitted for backward-compatible
notebook code; production Optuna objectives and the EB prior
don't reference it.
