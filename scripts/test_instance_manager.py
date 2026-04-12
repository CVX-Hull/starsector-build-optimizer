#!/usr/bin/env python3
"""Integration test for the instance manager — launches real game instances."""

import logging
import sys
import time
sys.path.insert(0, "src")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from pathlib import Path
from starsector_optimizer.models import MatchupConfig
from starsector_optimizer.instance_manager import InstanceConfig, InstancePool

GAME_DIR = Path("game/starsector")

# Single instance, single matchup — simplest possible test
config = InstanceConfig(
    game_dir=GAME_DIR,
    num_instances=1,
    xvfb_base_display=100,
    startup_timeout_seconds=120.0,  # generous for first test
    heartbeat_timeout_seconds=180.0,
)

matchups = [
    MatchupConfig(
        matchup_id="integration_001",
        player_variants=("eagle_Assault",),
        enemy_variants=("dominator_Assault",),
        time_limit_seconds=180,
        time_mult=3.0,
    ),
    MatchupConfig(
        matchup_id="integration_002",
        player_variants=("onslaught_Elite",),
        enemy_variants=("lasher_CS",),
        time_limit_seconds=180,
        time_mult=3.0,
    ),
]

print(f"Setting up instance pool ({config.num_instances} instance)...")
pool = InstancePool(config)
pool.setup()

print(f"Work dir: {pool._instances[0].work_dir}")
print(f"Display: :{pool._instances[0].display_num}")
print()

try:
    print(f"Submitting {len(matchups)} matchups...")
    start = time.monotonic()
    results = []
    for i, m in enumerate(matchups):
        result = pool.run_matchup(i % pool.num_instances, m)
        results.append(result)
    elapsed = time.monotonic() - start

    print(f"\n{'='*60}")
    print(f"Completed in {elapsed:.1f}s")
    print(f"Results: {len(results)}")
    for r in results:
        print(f"  {r.matchup_id}: winner={r.winner}, duration={r.duration_seconds:.1f}s")
        for s in r.player_ships:
            print(f"    Player {s.variant_id}: destroyed={s.destroyed}, hull={s.hull_fraction:.2f}")
        for s in r.enemy_ships:
            print(f"    Enemy  {s.variant_id}: destroyed={s.destroyed}, hull={s.hull_fraction:.2f}")
    print(f"{'='*60}")
except Exception as e:
    print(f"\nFAILED: {e}")
    # Print instance state for debugging
    for inst in pool._instances:
        print(f"\nInstance {inst.instance_id}: state={inst.state}")
        print(f"  Work dir: {inst.work_dir}")
        log_path = inst.work_dir / "starsector.log"
        if log_path.exists():
            # Print last 30 lines of game log
            lines = log_path.read_text().splitlines()
            print(f"  Last 30 lines of starsector.log:")
            for line in lines[-30:]:
                print(f"    {line}")
    raise
finally:
    pool.teardown()
    print("\nTeardown complete.")
