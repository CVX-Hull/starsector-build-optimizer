#!/usr/bin/env python3
"""Debug Robot dismiss timing — take screenshots at each step."""

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.models import BuildSpec, MatchupConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    matchup = MatchupConfig(
        matchup_id="test_dismiss",
        player_builds=(BuildSpec(
            variant_id="test_build", hull_id="hammerhead",
            weapon_assignments={"WS 001": "heavyac", "WS 002": "heavyac"},
            hullmods=["heavyarmor"], flux_vents=10, flux_capacitors=5, cr=0.7,
        ),),
        enemy_variants=("buffalo_Standard",),
        time_limit_seconds=60, time_mult=5.0,
    )

    config = InstanceConfig(
        game_dir=Path("game/starsector"),
        num_instances=1,
        heartbeat_timeout_seconds=300.0,
    )
    pool = InstancePool(config)
    pool.setup()
    inst = pool._instances[0]
    display = f":{inst.display_num}"

    print("Running first matchup...")
    result = pool.run_matchup(0, matchup)
    print(f"Matchup done: {result.winner} ({result.duration_seconds:.0f}s game)")

    # Robot dismiss was launched BEFORE endCombat in the same frame
    # It has: 500ms wait → click Continue → 1500ms wait → click OK
    # Total: ~2.2s from endCombat to fully dismissed

    print("\nScreenshots every 500ms for 5s after matchup completion:")
    for i in range(10):
        time.sleep(0.5)
        fname = f"/tmp/dismiss_{i:02d}.png"
        os.system(f"DISPLAY={display} import -window root {fname} 2>/dev/null")
        done = inst.done_path.exists()
        print(f"  +{(i+1)*0.5:.1f}s  done_file={done}")

    print("\nScreenshots every 2s for 10 more seconds:")
    for i in range(5):
        time.sleep(2)
        fname = f"/tmp/dismiss_late_{i:02d}.png"
        os.system(f"DISPLAY={display} import -window root {fname} 2>/dev/null")
        print(f"  +{5 + (i+1)*2}s  screenshot taken")

    print(f"\nGame alive: {inst.game_process.poll() is None}")
    pool.teardown()
    print("Done. Check /tmp/dismiss_*.png")


if __name__ == "__main__":
    main()
