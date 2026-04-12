# Throughput Estimator Specification

Computes wall-clock time and cost estimates for combat simulation campaigns. Used for capacity planning before launching Phase 3 instance manager.

Defined in `src/starsector_optimizer/estimator.py`.

## Inputs

### Per-Hull Search Space Statistics

Computed from actual game data via `build_search_space()`:

- Number of assignable weapon slots
- Options per slot (weapon count + empty)
- Raw weapon combination count (product of options per slot)
- Eligible hullmod count
- Hull size (determines max vents/capacitors)

### Simulation Parameters

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `time_mult` | 3.0 | 1.0-5.0 | Game speed multiplier |
| `game_time_limit_seconds` | 180 | 60-300 | Per-matchup game-time limit |
| `startup_seconds` | 35.0 | — | Game launch + menu navigation |
| `batch_size` | 50 | 1-200 | Matchups per game instance launch |
| `num_instances` | 1 | 1-64 | Parallel game instances |

### Evaluation Budget

| Parameter | Default | Notes |
|-----------|---------|-------|
| `sims_per_hull` | 1000 | Combat evaluations per hull (after heuristic screening) |
| `num_hulls` | 50 | Combat-relevant hulls to optimize |

### Cloud Pricing (GPU Required)

GPU instances required — CPU-only VMs (Hetzner CCX) are too slow due to software OpenGL rendering.

| Provider | $/hr (spot) | vCPUs | RAM | GPU | Instances |
|----------|-------------|-------|-----|-----|-----------|
| AWS g4dn.xlarge | 0.16 | 4 | 16 GB | T4 | ~4 |
| AWS g4dn.2xlarge | 0.25 | 8 | 32 GB | T4 | ~8 |
| AWS g4dn.4xlarge | 0.36 | 16 | 64 GB | T4 | ~12 |

## Computed Outputs

### `HullSpaceStats`

Per-hull statistics about search space size:

- `hull_id`, `hull_name`, `hull_size`
- `num_slots`: assignable weapon slots
- `options_per_slot`: list of option counts
- `weapon_combinations`: product of options (can be astronomically large)
- `num_eligible_hullmods`
- `max_vents`, `max_capacitors`

### `ThroughputEstimate`

Given simulation parameters:

- `wall_seconds_per_matchup`: `game_time_limit_seconds / time_mult`
- `matchups_per_hour_per_instance`: `3600 / wall_seconds_per_matchup`
- `startup_overhead_fraction`: `startup_seconds / (startup_seconds + batch_size * wall_seconds_per_matchup)`
- `effective_matchups_per_hour`: accounts for startup overhead and parallelism
- `total_sims`: `sims_per_hull * num_hulls`
- `total_hours`: `total_sims / effective_matchups_per_hour`
- `cost_estimates`: dict of provider name → total cost

### Functions

- `compute_hull_space_stats(hull, game_data) -> HullSpaceStats`
- `compute_all_hull_stats(game_data) -> list[HullSpaceStats]`
- `estimate_throughput(params) -> ThroughputEstimate`
- `format_estimate_report(stats, estimate) -> str`: Human-readable summary

## Design Decisions

- No early termination — alpha-strike builds can one-shot opponents, making HP-based early exit unreliable.
- `getTotalElapsedTime(false)` returns game time (accelerated by time_mult), so wall-clock = game_time / time_mult.
- Startup includes game launch (~25s) + menu navigation (~10s) = ~35s total. Amortized over batch_size matchups.
