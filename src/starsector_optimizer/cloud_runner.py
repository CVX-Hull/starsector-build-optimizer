"""Cloud-path study runner (extracted from scripts/run_optimizer.py for testing).

Each study subprocess owns its fleet end-to-end: reads env vars, renders
UserData, provisions the fleet, runs the Optuna study inside a
`with CloudWorkerPool`, and tears down the fleet in `finally`.

`scripts/run_optimizer.py --worker-pool cloud` is a thin wrapper around
`run_cloud_study` so the cloud path can be unit-tested without spawning
a subprocess.

The fleet+pool plumbing is factored into `prepare_cloud_pool` (a context
manager) so it can be reused from `honest_evaluator.main` — see
docs/specs/30-honest-evaluator.md §CLI entry point.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterator

import redis

from .campaign import check_ami_tags_against_manifest, load_campaign_config
from .cloud_provider import AWSProvider
from .cloud_userdata import render_user_data
from .cloud_worker_pool import CloudWorkerPool
from .game_manifest import GameManifest
from .models import CampaignConfig, WorkerConfig
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


def resolve_study_id(campaign, study_idx: int, seed_idx: int) -> str:
    """Canonical study_id: `{hull}__{regime}__{sampler}__seed{seed_value}`.

    Identical to the `study_id` computed inside `run_cloud_study`. Exposed
    as a pure function so downstream consumers (e.g. `run_optimizer.py`'s
    eval-log directory selection) disambiguate by the same string.
    Concurrent shakedown-style configs (N studies × same
    hull/regime/sampler, distinct seed VALUES, all `seed_idx=0`) produce
    distinct paths only when `seed_value` — not `seed_idx` — is used.
    """
    study_cfg = campaign.studies[study_idx]
    seed = study_cfg.seeds[seed_idx]
    return f"{study_cfg.hull}__{study_cfg.regime}__{study_cfg.sampler}__seed{seed}"


def _probe_flask_port_free(port: int) -> None:
    """Raise RuntimeError if `port` (this study's Flask result-port) is already
    bound. Called in-subprocess before provisioning so a port collision fails
    immediately with a diagnosable message instead of surfacing as a raw
    EADDRINUSE from `make_server` after the fleet is already provisioned. Probes
    `0.0.0.0` without SO_REUSEADDR to match the server's bind."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        raise RuntimeError(
            f"Flask result-port {port} already in use ({e}); a concurrent "
            f"campaign sharing base_flask_port or a stale listener holds it. "
            f"Aborting before provisioning — use distinct base_flask_port ranges."
        ) from e
    finally:
        sock.close()


@contextmanager
def prepare_cloud_pool(
    *,
    campaign: CampaignConfig,
    study_id: str,
    project_tag: str,
    fleet_name: str,
    flask_port: int,
    target_workers: int,
    total_matchup_slots: int,
    tailnet_ip: str,
    bearer_token: str,
    tailscale_authkey: str,
    debug_ssh_pubkey: str = "",
    mod_jar_override_url: str = "",
    mod_jar_override_sha256: str = "",
    sweep_project_on_exit: bool = False,
) -> Iterator[CloudWorkerPool]:
    """Provision an AWS fleet, enter a CloudWorkerPool, yield it, then tear
    down in reverse order.

    Lifecycle (preserved by the `try/finally` + `with pool:` nesting):

        provision_fleet  →  pool.__enter__  →  (caller work) →
        pool.__exit__    →  terminate_fleet

    Pool `__exit__` shuts the Flask listener + janitor BEFORE we ask AWS
    to terminate workers, so no in-flight POST hits a torn-down listener.

    Used by:
      - `run_cloud_study` (one-fleet-per-study orchestration)
      - `honest_evaluator.main` (post-campaign re-scoring fleet)

    Distinct callers MUST pass distinct `(study_id, project_tag, fleet_name,
    flask_port)` tuples to avoid Redis-key / AWS-tag / port collisions
    against any other in-flight pool. honest-eval uses a separate
    `honest-eval-…-<utc>` namespace for exactly this reason — see
    spec 30 §CLI entry point.

    `sweep_project_on_exit=True` adds a project-wide
    `terminate_all_tagged(project_tag)` backstop after the normal
    fleet-specific termination. Only use it when this pool owns a unique
    project tag. Normal campaign study subprocesses share one campaign tag,
    so they must leave the option disabled and rely on `CampaignManager` for
    the campaign-wide sweep.
    """
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
        worker_cfg,
        tailscale_authkey=tailscale_authkey,
        debug_ssh_pubkey=debug_ssh_pubkey,
        mod_jar_override_url=mod_jar_override_url,
        mod_jar_override_sha256=mod_jar_override_sha256,
    )

    # Fail fast + diagnosably if this study's Flask result-port is already held
    # (a concurrent campaign sharing base_flask_port, or a stale listener) —
    # BEFORE provisioning a fleet that would otherwise be torn down when
    # make_server (cloud_worker_pool.setup) hits EADDRINUSE. This converts the
    # 2026-07-15 silent fleet-waste into an immediate, named failure.
    _probe_flask_port_free(flask_port)

    provider = AWSProvider(regions=campaign.regions)
    check_ami_tags_against_manifest(
        provider,
        campaign.ami_ids_by_region,
        GameManifest.load(),
        required_regions=campaign.regions,
    )
    try:
        instance_ids = provider.provision_fleet(
            fleet_name=fleet_name,
            project_tag=project_tag,
            regions=campaign.regions,
            ami_ids_by_region=campaign.ami_ids_by_region,
            instance_types=campaign.instance_types,
            ssh_key_name=campaign.ssh_key_name,
            spot_allocation_strategy=campaign.spot_allocation_strategy,
            target_workers=target_workers,
            user_data=user_data,
            # `capacity_rebalance` (param) mirrors `capacity_rebalancing` (field);
            # honored only under fleet_type="maintain". provision_timeout bounds
            # the maintain async instance-poll.
            fleet_type=campaign.fleet_type,
            capacity_rebalance=campaign.capacity_rebalancing,
            provision_timeout_seconds=campaign.fleet_provision_timeout_seconds,
        )
        if not instance_ids:
            raise RuntimeError(f"provision_fleet returned no instances for fleet_name={fleet_name}")
        # Enforce min_workers_to_start / partial_fleet_policy (previously dead
        # config in the cloud path). Mirrors phase7_learned_batch.py:1542.
        if len(instance_ids) < campaign.min_workers_to_start:
            msg = (
                f"fleet {fleet_name} provisioned {len(instance_ids)} worker(s), "
                f"below min_workers_to_start={campaign.min_workers_to_start}"
            )
            if campaign.partial_fleet_policy == "abort":
                raise RuntimeError(msg + " (partial_fleet_policy=abort)")
            logger.warning("%s; proceeding (partial_fleet_policy=proceed_half_speed)", msg)
        actual_matchup_slots = len(instance_ids) * campaign.matchup_slots_per_worker
        if actual_matchup_slots != total_matchup_slots:
            logger.warning(
                "cloud pool capacity adjusted from requested slots=%d to "
                "actual slots=%d based on %d provisioned worker(s)",
                total_matchup_slots,
                actual_matchup_slots,
                len(instance_ids),
            )
        redis_client = redis.Redis(
            host="localhost",
            port=campaign.redis_port,
            decode_responses=True,
        )
        pool = CloudWorkerPool(
            study_id=study_id,
            project_tag=project_tag,
            redis_client=redis_client,
            flask_port=flask_port,
            bearer_token=bearer_token,
            total_matchup_slots=actual_matchup_slots,
            result_timeout_seconds=campaign.result_timeout_seconds,
            visibility_timeout_seconds=campaign.visibility_timeout_seconds,
            janitor_interval_seconds=campaign.janitor_interval_seconds,
            max_requeues=campaign.max_requeues,
        )
        with pool:
            yield pool
    finally:
        # A partial-region terminate_fleet failure (FleetTeardownError under
        # maintain) must NOT skip the project sweep below — that sweep is the
        # backstop for a still-relaunching maintain fleet. Capture, run the
        # sweep, then re-raise so the failure still surfaces to the caller.
        teardown_error: Exception | None = None
        try:
            provider.terminate_fleet(fleet_name=fleet_name, project_tag=project_tag)
        except Exception as e:
            teardown_error = e
            logger.error(
                "terminate_fleet(fleet=%s, project=%s) raised; continuing to sweep: %s",
                fleet_name,
                project_tag,
                e,
            )
        if sweep_project_on_exit:
            try:
                provider.terminate_all_tagged(project_tag)
            except Exception as e:
                logger.error(
                    "project sweep: terminate_all_tagged(%s) raised: %s",
                    project_tag,
                    e,
                )
            try:
                active = provider.list_active(project_tag)
            except Exception as e:
                logger.error(
                    "project sweep: list_active(%s) raised: %s",
                    project_tag,
                    e,
                )
                active = []
            if active:
                time.sleep(campaign.teardown_retry_delay_seconds)
                try:
                    provider.terminate_all_tagged(project_tag)
                except Exception as e:
                    logger.error(
                        "project sweep retry: terminate_all_tagged(%s) raised: %s",
                        project_tag,
                        e,
                    )
                try:
                    active = provider.list_active(project_tag)
                except Exception as e:
                    logger.error(
                        "project sweep retry: list_active(%s) raised: %s",
                        project_tag,
                        e,
                    )
                    active = []
                if active:
                    logger.error(
                        "project sweep: %d worker(s) still active for Project=%s after retry: %s",
                        len(active),
                        project_tag,
                        active[:5],
                    )
        if teardown_error is not None:
            raise teardown_error


def run_cloud_study(
    *,
    campaign_yaml_path: Path,
    study_idx: int,
    seed_idx: int,
    hull_id: str,
    hull: Any,
    game_data: Any,
    manifest: Any,
    opponent_pool: Any,
    optimizer_config: Any,
    game_dir: Path | None = None,
) -> Any:
    """Provision fleet → run Optuna study → terminate fleet (in `finally`).

    Returns the Optuna Study object. Raises whatever the inner study raises,
    with teardown guaranteed via `prepare_cloud_pool`'s context manager.

    `manifest` is the authoritative `GameManifest` (schema v2, Commit G) —
    `optimize_hull` requires it for per-hull applicability + conditional
    exclusion reads. `scripts/run_optimizer.py` loads it once from
    `game/starsector/manifest.json` and forwards it through here.
    """
    tailnet_ip = _require_env("STARSECTOR_WORKSTATION_TAILNET_IP")
    bearer_token = _require_env("STARSECTOR_BEARER_TOKEN")
    tailscale_authkey = _require_env("STARSECTOR_TAILSCALE_AUTHKEY")
    project_tag = _require_env("STARSECTOR_PROJECT_TAG")

    campaign = load_campaign_config(campaign_yaml_path)
    study_cfg = campaign.studies[study_idx]
    # Include sampler in study_id so two studies that differ only in sampler
    # don't collide on fleet_name / LT / SG / Redis-key names. Defensive
    # hygiene — with TPE as the only allowed sampler (post-2026-04-19)
    # collisions can't occur in practice, but the invariant is cheap to
    # preserve and future-proofs the naming if new samplers are added.
    study_id = resolve_study_id(campaign, study_idx, seed_idx)

    # Flask port: one per (study_idx, seed_idx) pair. Ceiling per study is
    # `CampaignConfig.flask_ports_per_study` so the port budget matches the
    # ACL range (`tcp:9000-9099`) documented in cloud-worker-ops.md.
    flask_port = campaign.base_flask_port + study_idx * campaign.flask_ports_per_study + seed_idx

    # Pool concurrency == total JVM slots across the fleet. The pool doesn't
    # distinguish VMs from JVMs — it only holds N free slots.
    total_matchup_slots = study_cfg.workers_per_study * campaign.matchup_slots_per_worker

    # Optional debug SSH access. Operator generates an ED25519 keypair on
    # the workstation, exports the pubkey here, and SSHes into a hung worker
    # with the matching private key. This is the *primary* operator-SSH
    # path: Tailscale SSH (`--ssh` on `tailscale up`) was tried smoke #8
    # 2026-05-09 and rejected — tailscaled hijacks port 22 and gates via
    # the tailnet ACL, which a default-permissive personal tailnet still
    # leaves silent-deny for SSH. See test_tailscale_ssh_NOT_enabled.
    # Empty string = no debug pubkey injected; production runs should
    # leave it unset.
    debug_ssh_pubkey = os.environ.get("STARSECTOR_DEBUG_SSH_PUBKEY", "").strip()
    # Optional Java-only fast-iteration override: when both vars are set,
    # workers fetch the combat-harness jar from the operator's workstation
    # over the tailnet at boot and overlay the AMI-baked copy. Use
    # `scripts/cloud/serve_mod_jar.sh` to publish + print the env vars.
    # Both must be set together; `_validate_jar_override` raises ValueError
    # on a half-set pair.
    mod_jar_override_url = os.environ.get(
        "STARSECTOR_MOD_JAR_OVERRIDE_URL",
        "",
    ).strip()
    mod_jar_override_sha256 = os.environ.get(
        "STARSECTOR_MOD_JAR_OVERRIDE_SHA256",
        "",
    ).strip()

    with prepare_cloud_pool(
        campaign=campaign,
        study_id=study_id,
        project_tag=project_tag,
        fleet_name=study_id,
        flask_port=flask_port,
        target_workers=study_cfg.workers_per_study,
        total_matchup_slots=total_matchup_slots,
        tailnet_ip=tailnet_ip,
        bearer_token=bearer_token,
        tailscale_authkey=tailscale_authkey,
        debug_ssh_pubkey=debug_ssh_pubkey,
        mod_jar_override_url=mod_jar_override_url,
        mod_jar_override_sha256=mod_jar_override_sha256,
    ) as pool:
        return optimize_hull(
            hull_id,
            game_data,
            pool,
            opponent_pool,
            optimizer_config,
            manifest,
            game_dir=game_dir,
        )
