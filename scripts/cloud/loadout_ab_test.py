"""A/B loadout test — proves weapons + hullmods are applied combat-side.

Hand-crafts two hammerhead builds with IDENTICAL flux config but very
different weapons + hullmods, runs each N times against the SAME enemy,
and prints damage_dealt / winner / duration per matchup. Bypasses Optuna /
TPE entirely — calls `pool.run_matchup` directly with hardcoded specs so
the proposal step doesn't introduce variance.

  Build A (ARMED-SHIELDED):
    8 weapons (heavymortars + swarmers + lightdualac), shields hardened.
    Should: deal real damage, survive ~30+ seconds.

  Build B (UNARMED-SHIELDLESS):
    0 weapons (all slots empty), shield_shunt removes shields entirely.
    Should: deal zero damage, die fast (one harbinger reaper kills naked hull).

If A's `damage_dealt > 0` and B's `damage_dealt == 0`, weapons are applied.
If A survives much longer than B, hullmods are applied.
Multiple runs of each build prove the difference is reproducible (not RNG).

Usage:
    set -a; source .env; set +a
    eval "$(scripts/cloud/serve_mod_jar.sh --env)"
    scripts/cloud/serve_mod_jar.sh &  # foreground http.server
    uv run python scripts/cloud/loadout_ab_test.py

Env vars needed (same as launch_campaign.sh): TAILSCALE_AUTHKEY,
STARSECTOR_WORKSTATION_TAILNET_IP, STARSECTOR_PROJECT_TAG,
optionally STARSECTOR_MOD_JAR_OVERRIDE_{URL,SHA256}.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import redis

from starsector_optimizer.campaign import check_ami_tags_against_manifest
from starsector_optimizer.cloud_provider import AWSProvider
from starsector_optimizer.cloud_userdata import render_user_data
from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.models import BuildSpec, MatchupConfig, WorkerConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("loadout_ab_test")

# ----------------------------------------------------------------------------
# Hand-crafted builds. Hammerhead destroyer (8 slots: 2 medium-ballistic
# hardpoints + 2 small-missile hardpoints + 4 small-hybrid turrets).
# ----------------------------------------------------------------------------

HULL = "hammerhead"
ENEMY = "harbinger_Strike"
# n=10 per arm satisfies the validation-plan single-intermittent-failure
# detection target (1 − 0.7^10 ≈ 97 % vs ~66 % at n=3 for a hypothetical
# 30 %-rate flake). Override for fast debug runs via STARSECTOR_AB_RUNS.
RUNS_PER_BUILD = int(os.environ.get("STARSECTOR_AB_RUNS", "10"))

BUILD_ARMED = BuildSpec(
    variant_id="hammerhead_AB_ARMED",
    hull_id=HULL,
    weapon_assignments={
        "WS 001": "heavymortar",      # M ballistic HE
        "WS 002": "heavymortar",      # M ballistic HE
        "WS 003": "swarmer",          # S missile PD
        "WS 004": "swarmer",          # S missile PD
        "WS 005": "lightdualac",      # S ballistic kinetic
        "WS 006": "lightdualac",      # S ballistic kinetic
        "WS 007": "vulcan",           # S ballistic PD
        "WS 008": "vulcan",           # S ballistic PD
    },
    hullmods=("hardenedshieldemitter", "reinforcedhull"),
    flux_vents=6,
    flux_capacitors=6,
)

BUILD_UNARMED = BuildSpec(
    variant_id="hammerhead_AB_NAKED",
    hull_id=HULL,
    weapon_assignments={},  # ZERO weapons — every slot empty
    hullmods=("shield_shunt",),  # shield_shunt removes shields entirely
    flux_vents=6,                # IDENTICAL flux config to ARMED — flux is the control
    flux_capacitors=6,
)


def _make_matchup(build: BuildSpec, run_idx: int) -> MatchupConfig:
    matchup_id = f"{build.variant_id}_run{run_idx}_vs_{ENEMY}"
    return MatchupConfig(
        matchup_id=matchup_id,
        player_builds=(build,),
        enemy_variants=(ENEMY,),
        # Long enough to settle: hammerhead vs harbinger ~50-200s real-time.
        # time_mult=5 means in-game-time = 5*real, so 300s in-game = 60s real.
        time_limit_seconds=300.0,
        time_mult=5.0,
        # This script is the loadout regression target — opt in to
        # FIGHT_TICK so [SHIP_DUMP_F] timelines are available if a
        # matchup ends in <2s with hp_diff=0 (the smoke-#15 retreat
        # signature). Bounded volume — 6 matchups total.
        debug_dumps_enabled=True,
    )


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        logger.error("missing env var: %s", name)
        sys.exit(1)
    return val


def _summarize(matchup: MatchupConfig, result) -> dict:
    """One-line digest of a CombatResult — what proves loadouts applied."""
    pship = result.player_ships[0] if result.player_ships else None
    eship = result.enemy_ships[0] if result.enemy_ships else None
    pd = pship.damage_dealt if pship else None
    ed = eship.damage_dealt if eship else None
    return {
        "matchup": matchup.matchup_id,
        "winner": result.winner,
        "duration": round(result.duration_seconds, 1),
        "player_dealt_total": round(
            (pd.shield + pd.armor + pd.hull) if pd else 0.0, 1,
        ),
        "player_dealt_breakdown":
            f"shield={round(pd.shield, 1)} armor={round(pd.armor, 1)} hull={round(pd.hull, 1)}"
            if pd else "(no player ship)",
        "player_hull_remaining": round(pship.hull_fraction, 3) if pship else "?",
        "enemy_hull_remaining": round(eship.hull_fraction, 3) if eship else "?",
        "enemy_dealt_total": round(
            (ed.shield + ed.armor + ed.hull) if ed else 0.0, 1,
        ),
    }


def main() -> int:
    tailnet_ip = _require_env("STARSECTOR_WORKSTATION_TAILNET_IP")
    project_tag = _require_env("STARSECTOR_PROJECT_TAG")
    tailscale_authkey = _require_env("STARSECTOR_TAILSCALE_AUTHKEY")
    region = os.environ.get("STARSECTOR_AB_REGION", "us-east-1")
    ami_id = os.environ.get(
        "STARSECTOR_AB_AMI",
        "ami-07470878a86badf73",  # current worker AMI with source/manifest tags
    )
    mod_jar_url = os.environ.get("STARSECTOR_MOD_JAR_OVERRIDE_URL", "").strip()
    mod_jar_sha = os.environ.get("STARSECTOR_MOD_JAR_OVERRIDE_SHA256", "").strip()
    debug_pubkey = os.environ.get("STARSECTOR_DEBUG_SSH_PUBKEY", "").strip()

    # Per-run isolation — fresh study/fleet name each invocation so a leftover
    # SG from a prior aborted run doesn't collide.
    suffix = secrets.token_hex(3)
    study_id = f"loadout_ab_{suffix}"
    bearer_token = secrets.token_urlsafe(32)
    flask_port = 9050  # one-off, well within tailnet ACL range 9000-9099

    worker_cfg = WorkerConfig(
        campaign_id="loadout-ab",
        study_id=study_id,
        project_tag=project_tag,
        redis_host=tailnet_ip,
        redis_port=6379,
        http_endpoint=f"http://{tailnet_ip}:{flask_port}/result",
        bearer_token=bearer_token,
        max_lifetime_hours=0.5,
        matchup_slots_per_worker=2,
    )
    user_data = render_user_data(
        worker_cfg,
        tailscale_authkey=tailscale_authkey,
        debug_ssh_pubkey=debug_pubkey,
        mod_jar_override_url=mod_jar_url,
        mod_jar_override_sha256=mod_jar_sha,
    )

    provider = AWSProvider(regions=(region,))
    check_ami_tags_against_manifest(
        provider,
        {region: ami_id},
        GameManifest.load(),
        required_regions=(region,),
    )
    redis_client = redis.Redis(host="localhost", port=6379, decode_responses=True)
    pool = CloudWorkerPool(
        study_id=study_id,
        project_tag=project_tag,
        redis_client=redis_client,
        flask_port=flask_port,
        bearer_token=bearer_token,
        total_matchup_slots=2,             # 1 VM × 2 slots = 2 concurrent matchups
        result_timeout_seconds=600.0,
        visibility_timeout_seconds=300.0,
        janitor_interval_seconds=60.0,
        max_requeues=3,
    )

    matchups = (
        [_make_matchup(BUILD_ARMED, i) for i in range(RUNS_PER_BUILD)]
        + [_make_matchup(BUILD_UNARMED, i) for i in range(RUNS_PER_BUILD)]
    )

    try:
        logger.info("provisioning 1 c7a.2xlarge spot in %s (study_id=%s)", region, study_id)
        provider.provision_fleet(
            fleet_name=study_id,
            project_tag=project_tag,
            regions=(region,),
            ami_ids_by_region={region: ami_id},
            instance_types=("c7a.2xlarge",),
            ssh_key_name="starsector-probe",
            spot_allocation_strategy="price-capacity-optimized",
            target_workers=1,
            user_data=user_data,
        )
        with pool:
            logger.info("dispatching %d matchups (2 concurrent slots)", len(matchups))
            results: list[tuple[MatchupConfig, object]] = []
            with ThreadPoolExecutor(max_workers=2) as ex:
                futures = {ex.submit(pool.run_matchup, m): m for m in matchups}
                for fut in as_completed(futures):
                    m = futures[fut]
                    try:
                        r = fut.result()
                        results.append((m, r))
                        logger.info("got result %s", m.matchup_id)
                    except Exception:
                        logger.exception("matchup %s failed", m.matchup_id)

        print()
        print("=" * 100)
        print("LOADOUT A/B TEST — combat-based evidence (weapons + hullmods applied)")
        print("=" * 100)
        for build_label, build in (("ARMED-SHIELDED", BUILD_ARMED),
                                    ("UNARMED-SHIELDLESS", BUILD_UNARMED)):
            print()
            print(f"--- BUILD {build_label} ({build.variant_id}) ---")
            print(f"  weapons: {len(build.weapon_assignments)} mounted")
            print(f"  hullmods: {list(build.hullmods)}")
            print(f"  flux: vents={build.flux_vents}, caps={build.flux_capacitors}")
            for m, r in results:
                if not m.matchup_id.startswith(build.variant_id):
                    continue
                s = _summarize(m, r)
                print(f"  {s['matchup']}")
                print(f"    winner={s['winner']:7s}  duration={s['duration']:6.1f}s")
                print(f"    player_hull_remaining={s['player_hull_remaining']}  enemy_hull_remaining={s['enemy_hull_remaining']}")
                print(f"    player_dealt_total={s['player_dealt_total']:8.1f}  ({s['player_dealt_breakdown']})")
                print(f"    enemy_dealt_total ={s['enemy_dealt_total']:8.1f}")
        print()
        return 0
    finally:
        logger.info("tearing down fleet %s", study_id)
        try:
            provider.terminate_fleet(
                fleet_name=study_id, project_tag=project_tag,
            )
        except Exception:
            logger.exception("terminate_fleet failed for study=%s", study_id)


if __name__ == "__main__":
    sys.exit(main())
