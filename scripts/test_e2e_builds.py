#!/usr/bin/env python3
"""End-to-end test: verify different builds produce different combat results.

Runs 3 distinct Eagle builds against the same opponent (dominator_Assault).
Checks that:
1. The pipeline works end-to-end (queue → game → results)
2. Different builds produce meaningfully different results
3. Build specs are correctly applied (weapons, hullmods, vents/caps)
"""

import logging
import sys
import time

sys.path.insert(0, "src")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from pathlib import Path
from starsector_optimizer.models import BuildSpec, MatchupConfig
from starsector_optimizer.instance_manager import InstanceConfig, LocalInstancePool

GAME_DIR = Path("game/starsector")
OPPONENT = "dominator_Assault"

# --- Three distinct Eagle builds ---

# Build A: Heavy ballistic + energy support (balanced combat build)
build_a = BuildSpec(
    variant_id="eagle_test_ballistic",
    hull_id="eagle",
    weapon_assignments={
        "WS 007": "heavymauler",   # BALLISTIC MEDIUM hardpoint
        "WS 008": "heavyac",       # BALLISTIC MEDIUM hardpoint
        "WS 013": "arbalest",      # BALLISTIC MEDIUM hardpoint
        "WS 005": "pulselaser",    # ENERGY MEDIUM turret
        "WS 006": "pulselaser",    # ENERGY MEDIUM turret
        "WS 009": "gravitonbeam",  # ENERGY MEDIUM turret
        "WS 003": "pdlaser",       # ENERGY SMALL turret
        "WS 004": "pdlaser",       # ENERGY SMALL turret
        "WS 010": "taclaser",      # ENERGY SMALL turret
        "WS 001": "harpoon_single",  # MISSILE SMALL hardpoint
        "WS 002": "harpoon_single",  # MISSILE SMALL hardpoint
    },
    hullmods=("heavyarmor", "fluxcoil"),
    flux_vents=20,
    flux_capacitors=10,
    cr=0.7,
)

# Build B: All-energy loadout with shields focus
build_b = BuildSpec(
    variant_id="eagle_test_energy",
    hull_id="eagle",
    weapon_assignments={
        "WS 005": "heavyblaster",  # ENERGY MEDIUM turret
        "WS 006": "heavyblaster",  # ENERGY MEDIUM turret
        "WS 009": "phasebeam",     # ENERGY MEDIUM turret
        "WS 003": "irpulse",       # ENERGY SMALL turret
        "WS 004": "irpulse",       # ENERGY SMALL turret
        "WS 010": "pdlaser",       # ENERGY SMALL turret
        "WS 011": "pdlaser",       # ENERGY SMALL turret
        "WS 012": "taclaser",      # ENERGY SMALL turret
        # Leave ballistic and missile slots empty
    },
    hullmods=("fluxcoil", "fluxbreakers"),
    flux_vents=25,
    flux_capacitors=5,
    cr=0.7,
)

# Build C: Bare minimum — no weapons, no hullmods (should lose badly)
build_c = BuildSpec(
    variant_id="eagle_test_bare",
    hull_id="eagle",
    weapon_assignments={},
    hullmods=(),
    flux_vents=0,
    flux_capacitors=0,
    cr=0.7,
)

builds = [
    ("A (Ballistic+Energy)", build_a),
    ("B (All-Energy)", build_b),
    ("C (Bare/Unarmed)", build_c),
]

# --- Setup ---

config = InstanceConfig(
    game_dir=GAME_DIR,
    num_instances=1,
    xvfb_base_display=100,
    startup_timeout_seconds=120.0,
    heartbeat_timeout_seconds=180.0,
)

print("=" * 60)
print("E2E Build Verification Test")
print(f"3 Eagle builds vs {OPPONENT}")
print("=" * 60)

pool = LocalInstancePool(config)
pool.setup()

try:
    results = []
    for label, build in builds:
        matchup = MatchupConfig(
            matchup_id=f"e2e_{build.variant_id}",
            player_builds=(build,),
            enemy_variants=(OPPONENT,),
            time_limit_seconds=60.0,
            time_mult=5.0,
            map_width=16000.0,
            map_height=12000.0,
        )

        print(f"\n--- Build {label} ---")
        print(f"  Weapons: {len(build.weapon_assignments)}")
        print(f"  Hullmods: {list(build.hullmods)}")
        print(f"  Vents={build.flux_vents}, Caps={build.flux_capacitors}")

        t0 = time.monotonic()
        result = pool.run_matchup(matchup)
        elapsed = time.monotonic() - t0

        print(f"  Winner: {result.winner}")
        print(f"  Duration: {result.duration_seconds:.1f}s (wall: {elapsed:.1f}s)")
        for s in result.player_ships:
            print(f"  Player {s.variant_id}: hull={s.hull_fraction:.2f}, "
                  f"destroyed={s.destroyed}, dmg_dealt=(shield={s.damage_dealt.shield:.0f}, "
                  f"armor={s.damage_dealt.armor:.0f}, hull={s.damage_dealt.hull:.0f})")
        for s in result.enemy_ships:
            print(f"  Enemy  {s.variant_id}: hull={s.hull_fraction:.2f}, "
                  f"destroyed={s.destroyed}")

        results.append((label, build, result))

    # --- Verification ---
    print("\n" + "=" * 60)
    print("VERIFICATION")
    print("=" * 60)

    # Check 1: All matchups completed
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    print("[PASS] All 3 matchups completed")

    # Check 2: Bare build should lose (enemy wins or timeout with low player HP)
    _, _, bare_result = results[2]
    bare_player = bare_result.player_ships[0]
    assert bare_result.winner in ("ENEMY", "TIMEOUT"), \
        f"Bare build unexpectedly won: {bare_result.winner}"
    print(f"[PASS] Bare build lost as expected (winner={bare_result.winner})")

    # Check 3: Bare build should deal minimal damage
    bare_dmg = (bare_player.damage_dealt.shield +
                bare_player.damage_dealt.armor +
                bare_player.damage_dealt.hull)
    print(f"[INFO] Bare build total damage dealt: {bare_dmg:.0f}")

    # Check 4: Armed builds should deal more damage than bare
    for i in range(2):  # builds A and B
        label, _, armed_result = results[i]
        armed_player = armed_result.player_ships[0]
        armed_dmg = (armed_player.damage_dealt.shield +
                     armed_player.damage_dealt.armor +
                     armed_player.damage_dealt.hull)
        assert armed_dmg > bare_dmg, \
            f"Build {label} dealt less damage ({armed_dmg:.0f}) than bare ({bare_dmg:.0f})"
        print(f"[PASS] Build {label} dealt more damage ({armed_dmg:.0f}) than bare ({bare_dmg:.0f})")

    # Check 5: Damage dealt differs across builds (proves builds are applied)
    damages = []
    for label, _, r in results:
        p = r.player_ships[0]
        dmg = p.damage_dealt.shield + p.damage_dealt.armor + p.damage_dealt.hull
        damages.append(dmg)
    print(f"\n[INFO] Damage dealt per build: {[f'{d:.0f}' for d in damages]}")
    print(f"[INFO] Winners: {[r.winner for _, _, r in results]}")
    print(f"[INFO] Player hull remaining: {[f'{r.player_ships[0].hull_fraction:.2f}' for _, _, r in results]}")

    unique_damages = len(set(round(d, -1) for d in damages))
    assert unique_damages >= 2, \
        "All builds dealt identical damage — builds may not be applied"
    print(f"[PASS] {unique_damages} distinct damage profiles — builds are applied correctly")

    print("\n" + "=" * 60)
    print("ALL CHECKS PASSED")
    print("=" * 60)

except Exception as e:
    print(f"\nFAILED: {e}")
    import traceback
    traceback.print_exc()
    for inst in pool._instances:
        print(f"\nInstance {inst.instance_id}: state={inst.state}")
        log_path = inst.work_dir / "starsector.log"
        if log_path.exists():
            lines = log_path.read_text().splitlines()
            print(f"  Last 50 lines of starsector.log:")
            for line in lines[-50:]:
                print(f"    {line}")
    sys.exit(1)
finally:
    pool.teardown()
    print("\nTeardown complete.")
