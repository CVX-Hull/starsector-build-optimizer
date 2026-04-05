#!/usr/bin/env python3
"""Run throughput estimation against real game data."""

import sys
sys.path.insert(0, "src")

from starsector_optimizer.parser import load_game_data
from starsector_optimizer.estimator import (
    budget_optimizer,
    compute_all_hull_stats,
    estimate_throughput,
    format_estimate_report,
    print_scenario_comparison,
    SimulationParams,
)

from pathlib import Path

GAME_DIR = Path("game/starsector")

print("Loading game data...")
game_data = load_game_data(GAME_DIR)
print(f"Loaded {len(game_data.hulls)} hulls, {len(game_data.weapons)} weapons, "
      f"{len(game_data.hullmods)} hullmods\n")

# Compute search space stats for all hulls
all_stats = compute_all_hull_stats(game_data)

# Filter to combat-relevant hulls (skip stations, modules, unnamed skins)
combat_stats = [s for s in all_stats if s.num_slots > 0 and s.hull_name.strip()]

# Show by hull size
for size in ["FRIGATE", "DESTROYER", "CRUISER", "CAPITAL_SHIP"]:
    size_stats = [s for s in combat_stats if s.hull_size.value == size]
    print(f"\n{size}: {len(size_stats)} hulls")

print(f"\nTotal combat hulls (with weapon slots): {len(combat_stats)}")

# Default scenario: 50 hulls, 1000 sims each, 8 instances, 5x speed
params = SimulationParams(
    time_mult=5.0,
    game_time_limit_seconds=180,
    num_instances=8,
    sims_per_hull=1000,
    num_hulls=len(combat_stats),
    batch_size=50,
)
estimate = estimate_throughput(params)

print("\n" + format_estimate_report(combat_stats, estimate))
print("\n" + print_scenario_comparison(num_hulls=len(combat_stats)))

# Also show with surrogate (30% of sims needed)
print("\n--- With Neural Surrogate (30% of sims) ---")
surrogate_params = SimulationParams(
    time_mult=5.0,
    game_time_limit_seconds=180,
    num_instances=8,
    sims_per_hull=300,
    num_hulls=len(combat_stats),
    batch_size=50,
)
surrogate_est = estimate_throughput(surrogate_params)
print(f"Total sims:      {surrogate_est.total_sims:,}")
print(f"Total hours:     {surrogate_est.total_hours:.1f}h")
for name, cost in sorted(surrogate_est.cost_estimates.items(), key=lambda x: x[1]):
    print(f"  {name}: ${cost:.2f}")

# Budget optimization
print("\n" + budget_optimizer(budget_usd=30, num_hulls=len(combat_stats), sims_per_hull=1000))
print("\n" + budget_optimizer(budget_usd=30, num_hulls=len(combat_stats), sims_per_hull=300))
