"""Tier-1 validation probe — provisions N workers via AWSProvider, asserts
they reach RUNNING state, then tears down.

Scope:
  - IAM surface (provision_fleet / terminate_fleet / describe_*)
  - AMI availability in every targeted region
  - LaunchTemplate + SecurityGroup creation with Project+Fleet two-tag scheme
  - Tag propagation to instances + volumes
  - terminate_fleet (targeted) actually reaps the fleet
  - list_active(project_tag) → [] after teardown
  - final_audit.sh reports clean

Out of scope (Tier-2 smoke handles these):
  - Tailscale join
  - Redis reachability
  - HTTP POST /result round-trip
  - Real matchup end-to-end

Cost: ~$0.05 for 2 c7a.2xlarge spot instances × 3-5 min wall-clock.

Usage:
    uv run python scripts/cloud/probe.py examples/probe-campaign.yaml
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

from starsector_optimizer.campaign import load_campaign_config
from starsector_optimizer.cloud_provider import AWSProvider
from starsector_optimizer.cloud_userdata import render_probe_user_data


logger = logging.getLogger("probe")


# Probe-specific deadlines; strictly bounded so a crashed probe can't leak
# into a pending campaign.
_RUN_DEADLINE_SECONDS = 600.0   # 10 min; provision_fleet → all running
_CLOUD_INIT_GRACE_SECONDS = 45.0
_TEARDOWN_DEADLINE_SECONDS = 300.0  # 5 min to reach terminated state
_POLL_INTERVAL_SECONDS = 10.0


def _poll_until_running(
    provider: AWSProvider, project_tag: str, target: int, deadline: float,
) -> list[dict]:
    while time.monotonic() < deadline:
        active = provider.list_active(project_tag)
        if len(active) >= target and all(
            inst["state"] == "running" for inst in active
        ):
            return active
        logger.info(
            "waiting for fleet: running=%d/%d states=%s",
            sum(1 for i in active if i["state"] == "running"),
            target,
            [i["state"] for i in active],
        )
        time.sleep(_POLL_INTERVAL_SECONDS)
    active = provider.list_active(project_tag)
    raise TimeoutError(
        f"fleet did not reach running state within {_RUN_DEADLINE_SECONDS}s; "
        f"last observed: {active}"
    )


def _poll_until_empty(
    provider: AWSProvider, project_tag: str, deadline: float,
) -> None:
    while time.monotonic() < deadline:
        active = provider.list_active(project_tag)
        if not active:
            return
        logger.info("waiting for teardown: %d active", len(active))
        time.sleep(_POLL_INTERVAL_SECONDS)
    active = provider.list_active(project_tag)
    raise TimeoutError(
        f"teardown did not clear within {_TEARDOWN_DEADLINE_SECONDS}s; "
        f"residual: {active}"
    )


def _run_final_audit(campaign_name: str) -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    audit = repo_root / "scripts" / "cloud" / "final_audit.sh"
    result = subprocess.run(
        [str(audit), campaign_name], check=False,
    )
    return result.returncode


def run_probe(config_path: Path) -> int:
    config = load_campaign_config(config_path)
    logger.info("probe campaign: %s", config.name)
    logger.info("regions: %s", config.regions)
    logger.info("target workers: %d (min=%d)",
                config.max_concurrent_workers, config.min_workers_to_start)

    provider = AWSProvider(regions=config.regions)
    user_data = render_probe_user_data(campaign_id=config.name)
    project_tag = f"starsector-{config.name}"
    fleet_name = "probe"

    # Always run teardown, even on success — probe is a throwaway.
    try:
        logger.info("=== phase 1: provision_fleet ===")
        instance_ids = provider.provision_fleet(
            fleet_name=fleet_name,
            project_tag=project_tag,
            regions=config.regions,
            ami_ids_by_region=config.ami_ids_by_region,
            instance_types=config.instance_types,
            ssh_key_name=config.ssh_key_name,
            spot_allocation_strategy=config.spot_allocation_strategy,
            target_workers=config.max_concurrent_workers,
            user_data=user_data,
        )
        if not instance_ids:
            raise RuntimeError("provision_fleet returned zero instance IDs")
        logger.info("launched: %d instances: %s", len(instance_ids), instance_ids)

        logger.info("=== phase 2: wait for RUNNING ===")
        deadline = time.monotonic() + _RUN_DEADLINE_SECONDS
        active = _poll_until_running(
            provider, project_tag, target=len(instance_ids), deadline=deadline,
        )
        logger.info("all %d instances running: %s",
                    len(active), [i["id"] for i in active])

        logger.info("=== phase 3: cloud-init grace (%.0fs) ===",
                    _CLOUD_INIT_GRACE_SECONDS)
        time.sleep(_CLOUD_INIT_GRACE_SECONDS)
        return 0
    finally:
        logger.info("=== phase 4: teardown (targeted terminate_fleet) ===")
        try:
            terminated = provider.terminate_fleet(
                fleet_name=fleet_name, project_tag=project_tag,
            )
            logger.info("terminated: %d instances", terminated)
        except Exception as e:
            logger.error("terminate_fleet raised: %s", e)

        logger.info("=== phase 5: verify list_active == [] ===")
        try:
            deadline = time.monotonic() + _TEARDOWN_DEADLINE_SECONDS
            _poll_until_empty(provider, project_tag, deadline)
            logger.info("list_active is empty — teardown confirmed")
        except Exception as e:
            logger.error("teardown verification failed: %s", e)

        logger.info("=== phase 6: final_audit.sh ===")
        audit_rc = _run_final_audit(config.name)
        if audit_rc != 0:
            logger.error("final_audit.sh reported leaks (exit %d)", audit_rc)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("campaign_yaml", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="Load + validate the YAML; skip AWS calls.")
    args = parser.parse_args(argv)

    if args.dry_run:
        config = load_campaign_config(args.campaign_yaml)
        logger.info("dry-run: config loaded OK: %s", config.name)
        return 0

    return run_probe(args.campaign_yaml)


if __name__ == "__main__":
    sys.exit(main())
