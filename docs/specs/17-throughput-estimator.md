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

### Cloud Pricing (CPU-only, validated 2026-04-18)

CPU spot instances are fully viable. The 2026-04-12 "GPU required" conclusion was a misdiagnosis of an LWJGL 2.x XRandR bug fixed in `instance_manager.py::_start_xvfb` by warming the XRandR extension with `xrandr --query` after Xvfb's socket is ready. See spec 22 for the full root cause narrative. GPU instances are not required and are not part of the Phase 6 design.

| Provider | $/hr (spot) | vCPUs | RAM | Instances (2 JVMs @ ≤3 vCPU each) |
|----------|-------------|-------|-----|------------------------------------|
| AWS c7a.2xlarge | $0.15 | 8 AMD Genoa | 16 GB | 2 |
| AWS c7i.2xlarge | $0.158 | 8 Intel SPR | 16 GB | 2 |
| AWS c7a.4xlarge / c7i.4xlarge | ~$0.27 | 16 | 32 GB | 5 |
| Hetzner CCX33 | $0.13 | 8 AMD Milan | 32 GB | 2 (no preemption tier; deferred per spec 22) |

Throughput validated at 64 matchups/hr/instance on c7i.2xlarge vs 27/hr/instance on the 12-core workstation — 2.4× per-instance uplift at ~$0.001/matchup. See `experiments/cloud-benchmark-2026-04-18/` and `experiments/phase6-planning/cost_model.py` for the pinned dollar figures.

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
