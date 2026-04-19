"""Cloud-path study runner (extracted from scripts/run_optimizer.py for testing).

Each study subprocess owns its fleet end-to-end: reads env vars, renders
UserData, provisions the fleet, runs the Optuna study inside a
`with CloudWorkerPool`, and tears down the fleet in `finally`.

`scripts/run_optimizer.py --worker-pool cloud` is a thin wrapper around
`run_cloud_study` so the cloud path can be unit-tested without spawning
a subprocess.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import redis

from .campaign import load_campaign_config
from .cloud_provider import AWSProvider
from .cloud_userdata import render_user_data
from .cloud_worker_pool import CloudWorkerPool
from .models import WorkerConfig
from .optimizer import optimize_hull

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    """Fetch an env var; raise ValueError (not KeyError) with remediation.

    Used at the cloud-path entry to surface CampaignManager contract breaks
    clearly: every required var should have been set by CampaignManager
    before spawning this subprocess.
    """
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"{name} not set; CampaignManager should have set it — "
            f"are you launching via `scripts/cloud/launch_campaign.sh`?"
        )
    return value


def run_cloud_study(
    *,
    campaign_yaml_path: Path,
    study_idx: int,
    seed_idx: int,
    hull_id: str,
    hull: Any,
    game_data: Any,
    opponent_pool: Any,
    optimizer_config: Any,
) -> Any:
    """Provision fleet → run Optuna study → terminate fleet (in `finally`).

    Returns the Optuna Study object. Raises whatever the inner study raises,
    with teardown guaranteed via `finally`.
    """
    tailnet_ip = _require_env("STARSECTOR_WORKSTATION_TAILNET_IP")
    bearer_token = _require_env("STARSECTOR_BEARER_TOKEN")
    tailscale_authkey = _require_env("STARSECTOR_TAILSCALE_AUTHKEY")
    project_tag = _require_env("STARSECTOR_PROJECT_TAG")

    campaign = load_campaign_config(campaign_yaml_path)
    study_cfg = campaign.studies[study_idx]
    seed = study_cfg.seeds[seed_idx]
    study_id = f"{study_cfg.hull}__{study_cfg.regime}__seed{seed}"
    fleet_name = study_id

    # Flask port: one per (study_idx, seed_idx) pair. Ceiling per study is
    # `CampaignConfig.flask_ports_per_study` so the port budget matches the
    # ACL range (`tcp:9000-9099`) documented in cloud-worker-ops.md.
    flask_port = (
        campaign.base_flask_port
        + study_idx * campaign.flask_ports_per_study
        + seed_idx
    )

    # Pool concurrency == total JVM slots across the fleet. The pool doesn't
    # distinguish VMs from JVMs — it only holds N free slots.
    total_matchup_slots = (
        study_cfg.workers_per_study * campaign.matchup_slots_per_worker
    )

    worker_cfg = WorkerConfig(
        campaign_id=campaign.name,
        study_id=study_id,
        project_tag=project_tag,
        redis_host=tailnet_ip,
        redis_port=campaign.redis_port,
        http_endpoint=f"http://{tailnet_ip}:{flask_port}/result",
        bearer_token=bearer_token,
        max_lifetime_hours=campaign.max_lifetime_hours,
        matchup_slots_per_worker=campaign.matchup_slots_per_worker,
        # worker_id intentionally defaulted (""); IMDSv2 overrides at VM boot.
    )
    user_data = render_user_data(
        worker_cfg, tailscale_authkey=tailscale_authkey,
    )

    provider = AWSProvider(regions=campaign.regions)
    redis_client = redis.Redis(
        host="localhost", port=campaign.redis_port, decode_responses=True,
    )
    pool = CloudWorkerPool(
        study_id=study_id,
        project_tag=project_tag,
        redis_client=redis_client,
        flask_port=flask_port,
        bearer_token=bearer_token,
        total_matchup_slots=total_matchup_slots,
        result_timeout_seconds=campaign.result_timeout_seconds,
        visibility_timeout_seconds=campaign.visibility_timeout_seconds,
        janitor_interval_seconds=campaign.janitor_interval_seconds,
    )

    try:
        provider.provision_fleet(
            fleet_name=fleet_name,
            project_tag=project_tag,
            regions=campaign.regions,
            ami_ids_by_region=campaign.ami_ids_by_region,
            instance_types=campaign.instance_types,
            ssh_key_name=campaign.ssh_key_name,
            spot_allocation_strategy=campaign.spot_allocation_strategy,
            target_workers=study_cfg.workers_per_study,
            user_data=user_data,
        )
        with pool:
            return optimize_hull(
                hull_id, game_data, pool, opponent_pool, optimizer_config,
            )
    finally:
        # pool.__exit__ (above) shuts Flask + janitor BEFORE we terminate
        # the fleet, so no worker POST can arrive at a torn-down listener.
        provider.terminate_fleet(fleet_name=fleet_name, project_tag=project_tag)
