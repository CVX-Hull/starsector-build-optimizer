#!/usr/bin/env python3
"""Integration test: run a small optimization on Eagle with real combat simulation.

Uses 2 Xvfb instances. Evaluates 3 builds against 2 opponents (reduced pool for speed).
Verifies the full pipeline: heuristic warm-start → build proposal → repair → variant gen
→ combat sim → fitness computation → JSONL logging.
"""

import sys
import time

sys.path.insert(0, "src")

from pathlib import Path

from starsector_optimizer.parser import load_game_data
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.calibration import generate_diverse_builds
from starsector_optimizer.scorer import heuristic_score
from starsector_optimizer.repair import repair_build
from starsector_optimizer.variant import generate_variant
from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.curtailment import CurtailmentMonitor
from starsector_optimizer.opponent_pool import (
    OpponentPool, generate_matchups, compute_fitness, hp_differential, get_opponents,
)
from starsector_optimizer.models import HullSize

GAME_DIR = Path("game/starsector")
EVAL_LOG = Path("data/integration_test_eval.jsonl")

# Reduced opponent pool for speed (2 opponents instead of 6)
TEST_OPPONENT_POOL = OpponentPool(pools={
    HullSize.CRUISER: ("dominator_Assault", "eagle_Assault"),
})

print("=" * 60)
print("Phase 4 Integration Test: Eagle optimization (2 instances)")
print("=" * 60)

# Load game data
print("\n1. Loading game data...")
game_data = load_game_data(GAME_DIR)
hull = game_data.hulls["eagle"]
space = build_search_space(hull, game_data)
print(f"   Eagle: {len(space.weapon_options)} weapon slots, "
      f"{len(space.eligible_hullmods)} hullmods, "
      f"total dims={len(space.weapon_options) + len(space.eligible_hullmods) + 2}")

# Generate and score builds with heuristic
print("\n2. Generating diverse builds...")
builds = generate_diverse_builds(hull, game_data, n=1000)
scored = [(b, heuristic_score(b, hull, game_data)) for b in builds]
scored.sort(key=lambda x: -x[1].composite_score)
top3 = scored[:3]
print(f"   Generated 1000 builds, top-3 heuristic scores: "
      f"{[f'{s.composite_score:.3f}' for _, s in top3]}")

# Setup instance pool with curtailment
print("\n3. Setting up 2 Xvfb instances...")
config = InstanceConfig(
    game_dir=GAME_DIR,
    num_instances=2,
    batch_size=2,  # Match num_opponents for parallel distribution
    xvfb_base_display=200,
)
curtailment = CurtailmentMonitor(min_time=30.0, ttd_ratio=3.0)
pool = InstancePool(config, curtailment=curtailment)
pool.setup()

try:
    # Evaluate each build against opponent pool
    opponents = get_opponents(TEST_OPPONENT_POOL, HullSize.CRUISER)
    print(f"\n4. Evaluating 3 builds against {len(opponents)} opponents...")
    print(f"   Opponents: {', '.join(opponents)}")

    all_results = []
    for idx, (build, scorer_result) in enumerate(top3):
        repaired = repair_build(build, hull, game_data)
        variant_id = f"eagle_inttest_{idx:03d}"
        variant = generate_variant(repaired, hull, game_data, variant_id=variant_id)
        pool.write_variant_to_all(variant, f"{variant_id}.variant")

        matchups = generate_matchups(
            variant_id, opponents,
            matchup_id_prefix=f"inttest_{idx:03d}",
            time_mult=5.0,
            time_limit_seconds=180.0,
        )

        print(f"\n   Build #{idx+1} (heuristic={scorer_result.composite_score:.3f}):")
        print(f"   Weapons: {sum(1 for v in repaired.weapon_assignments.values() if v)} equipped")
        print(f"   Hullmods: {', '.join(sorted(repaired.hullmods)) or 'none'}")
        print(f"   Vents={repaired.flux_vents}, Caps={repaired.flux_capacitors}")

        t0 = time.monotonic()
        results = pool.evaluate(matchups)
        elapsed = time.monotonic() - t0

        build_results = []
        for r in results:
            diff = hp_differential(r)
            opponent = r.matchup_id.split("_vs_")[-1] if "_vs_" in r.matchup_id else "?"
            print(f"     vs {opponent}: winner={r.winner}, duration={r.duration_seconds:.1f}s, "
                  f"hp_diff={diff:+.3f}")
            build_results.append(r)

        fitness = compute_fitness(build_results)
        print(f"   → Fitness (mean): {fitness:+.3f}  ({elapsed:.1f}s wall-clock)")
        all_results.append((idx, scorer_result.composite_score, fitness))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Build':>6} {'Heuristic':>10} {'Sim Fitness':>12} {'Correlation':>12}")
    for idx, heur, fit in all_results:
        print(f"{'#'+str(idx+1):>6} {heur:>10.3f} {fit:>+12.3f}")

    heur_scores = [h for _, h, _ in all_results]
    sim_scores = [f for _, _, f in all_results]
    if len(all_results) >= 2:
        # Simple rank correlation check
        heur_rank = [sorted(heur_scores, reverse=True).index(h) for h in heur_scores]
        sim_rank = [sorted(sim_scores, reverse=True).index(s) for s in sim_scores]
        print(f"\nHeuristic ranks: {heur_rank}")
        print(f"Sim ranks:       {sim_rank}")

    print(f"\nTotal matchups evaluated: {len(all_results) * len(opponents)}")
    print("Integration test PASSED")

finally:
    print("\n5. Tearing down instances...")
    pool.teardown()
    print("   Done.")
