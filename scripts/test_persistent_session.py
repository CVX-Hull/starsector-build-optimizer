#!/usr/bin/env python3
"""Test persistent session reuse — verify game stays alive between matchups."""

import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

from starsector_optimizer.instance_manager import InstanceConfig, InstancePool
from starsector_optimizer.models import MatchupConfig, BuildSpec


def make_matchup(name, enemy):
    return MatchupConfig(
        matchup_id=name,
        player_builds=(BuildSpec(
            variant_id="test_build",
            hull_id="hammerhead",
            weapon_assignments={"WS 001": "heavyac", "WS 002": "heavyac"},
            hullmods=["heavyarmor"],
            flux_vents=10,
            flux_capacitors=5,
            cr=0.7,
        ),),
        enemy_variants=(enemy,),
        time_limit_seconds=60,
        time_mult=5.0,
    )


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")

    config = InstanceConfig(game_dir=Path("game/starsector"), num_instances=1,
                            heartbeat_timeout_seconds=180.0)
    pool = InstancePool(config)
    pool.setup()

    enemies = ["buffalo_Standard", "bastillon_Standard", "berserker_Assault", "buffalo_Standard"]
    for i, enemy in enumerate(enemies):
        matchup = make_matchup(f"test_{i}", enemy)
        start = time.time()
        try:
            result = pool.run_matchup(0, matchup)
            elapsed = time.time() - start
            print(f"Matchup {i} vs {enemy}: {result.winner} in {elapsed:.1f}s "
                  f"(game={result.duration_seconds:.1f}s)")
        except Exception as e:
            elapsed = time.time() - start
            print(f"Matchup {i} vs {enemy}: FAILED in {elapsed:.1f}s - {e}")

    pool.teardown()
    print("Done!")


if __name__ == "__main__":
    main()
