# Heuristic Scorer Specification

Static build quality metrics. Defined in `src/starsector_optimizer/scorer.py`.

Uses `compute_effective_stats()` from `hullmod_effects.py` — never duplicates hullmod logic.

## Functions

### heuristic_score(build, hull, game_data) → ScorerResult
Computes all metrics and weighted composite score.

## Metrics
- **flux_balance**: weapon_flux / dissipation (ideal 0.4-0.8)
- **total_dps**: sum of all weapon sustained DPS
- **kinetic/he/energy_dps**: DPS by damage type
- **flux_efficiency**: total_dps / total_weapon_flux
- **effective_hp**: hull HP + armor EHP + shield EHP (with hullmod effects)
- **range_coherence**: 1 - cv(ranges) for non-PD weapons
- **damage_mix**: rewards kinetic+HE combination
- **engagement_range**: DPS-weighted mean weapon range
- **op_efficiency**: (total_dps + ehp_factor) / op_used
- **composite_score**: weighted sum of normalized metrics
