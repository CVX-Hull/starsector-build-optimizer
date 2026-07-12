"""Phase 6 Cloud Worker Federation — campaign manager + cost ledger.

Workstation-side orchestrator. Reads a campaign YAML, preflights the
workstation environment (Tailscale + Redis + AWS credentials + authkey
syntax), spawns one subprocess per `(study, seed)` pair, and tracks
cost in an append-only JSONL ledger. Hard-stops at `budget_usd`.

CampaignManager is a pure supervisor: it never calls `provision_fleet`.
Fleet ownership lives in the study subprocess (`scripts/run_optimizer.py
--worker-pool cloud`) — see `starsector_optimizer.cloud_runner`.
At campaign teardown, CampaignManager calls `provider.terminate_all_tagged`
as a sweep backstop for any fleet a crashed subprocess failed to reap.

See docs/specs/22-cloud-deployment.md.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable

import yaml

from .cloud_provider import CloudProvider
from .models import (
    CampaignConfig,
    CostLedgerEntry,
    GlobalAutoStopConfig,
    StudyConfig,
)

# Wall-clock seconds per hour — used by the ledger tick to convert
# heartbeat interval seconds into fractional hours for billing.
# Named constant (not a magic number) per CLAUDE.md Design Invariants.
_SECONDS_PER_HOUR: float = 3600.0

logger = logging.getLogger(__name__)


# ---- Exceptions --------------------------------------------------------------


class BudgetExceeded(Exception):
    """Campaign cumulative cost reached budget_usd; trigger teardown.

    This is a **designed termination**, not an error: the operator pinned a
    hard $-cap and the run hit it. `CampaignManager.run()` exits 0 on this
    path so wrapper scripts (e.g. `launch_wave1.sh` running 5 cells back-to-
    back, each independently capped at the same budget) treat budget-exhaust
    as cell-complete and proceed to the next cell. Failures of other kinds
    (preflight, exceptions in monitor_loop, KeyboardInterrupt) still produce
    distinct non-zero exit codes; only this designed termination maps to 0.
    """


class TeardownError(Exception):
    """Raised when teardown cannot verify all workers terminated."""


# ---- YAML loading ------------------------------------------------------------


_ALLOWED_PROVIDERS = {"aws"}
_ALLOWED_PARTIAL_POLICIES = {"proceed_half_speed", "abort"}
_ALLOWED_SAMPLERS = {"tpe"}
# AWS LT names accept [a-zA-Z0-9().-/_]{3,128}. We constrain campaign names
# tighter to leave room for the `starsector-<name>__<fleet_name>` composition
# and avoid shell-metacharacters that would leak into subprocess arguments.
_NAME_REGEX = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


def _expand_env_var(value: str, *, field_name: str) -> str:
    """Resolve `${VAR}` env substitution for a single YAML string field.

    Field-scoped on purpose: only `tailscale_authkey_secret` supports this;
    no global YAML expansion. Missing var → clear ValueError with the var name.
    """
    if not (value.startswith("${") and value.endswith("}")):
        return value
    var = value[2:-1]
    resolved = os.environ.get(var)
    if resolved is None:
        raise ValueError(f"env var ${{{var}}} referenced by {field_name} is not set")
    return resolved


def load_campaign_config(path: Path) -> CampaignConfig:
    """Load and validate a campaign YAML into an immutable CampaignConfig.

    Validates `name` against _NAME_REGEX (AWS LT naming compatibility) and
    expands `${VAR}` in `tailscale_authkey_secret` from `os.environ`.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not _NAME_REGEX.match(str(raw.get("name", ""))):
        raise ValueError(
            f"invalid campaign name {raw.get('name')!r}: must match "
            f"{_NAME_REGEX.pattern} (AWS LT naming + no shell metacharacters)"
        )
    if raw.get("provider") not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"provider={raw.get('provider')!r} not supported; allowed: {sorted(_ALLOWED_PROVIDERS)}"
        )
    if raw.get("partial_fleet_policy") not in _ALLOWED_PARTIAL_POLICIES:
        raise ValueError(
            f"partial_fleet_policy={raw.get('partial_fleet_policy')!r} invalid; "
            f"allowed: {sorted(_ALLOWED_PARTIAL_POLICIES)}"
        )
    if raw["min_workers_to_start"] > raw["max_concurrent_workers"]:
        raise ValueError(
            f"min_workers_to_start={raw['min_workers_to_start']} exceeds "
            f"max_concurrent_workers={raw['max_concurrent_workers']}"
        )

    for s in raw["studies"]:
        if s["sampler"] not in _ALLOWED_SAMPLERS:
            raise ValueError(
                f"study sampler={s['sampler']!r} invalid; allowed: {sorted(_ALLOWED_SAMPLERS)}"
            )
        wsr = s.get("warm_start_from_regime")
        if wsr is not None and wsr == s["regime"]:
            raise ValueError(
                f"study {s['hull']}__{s['regime']}: "
                f"warm_start_from_regime={wsr!r} cannot equal regime "
                f"(Optuna's load_if_exists already self-seeds within a "
                f"single study; specify a different source regime or omit)"
            )
    studies = tuple(
        StudyConfig(
            hull=s["hull"],
            regime=s["regime"],
            seeds=tuple(s["seeds"]),
            budget_per_study=s["budget_per_study"],
            workers_per_study=s["workers_per_study"],
            sampler=s["sampler"],
            active_opponents=s.get("active_opponents"),
            warm_start_from_regime=s.get("warm_start_from_regime"),
        )
        for s in raw["studies"]
    )

    global_auto_stop_raw = raw.get("global_auto_stop") or {}
    global_auto_stop = GlobalAutoStopConfig(
        on_budget=global_auto_stop_raw.get("on_budget", "hard"),
        on_plateau=global_auto_stop_raw.get("on_plateau", True),
    )

    tailscale_authkey = _expand_env_var(
        raw["tailscale_authkey_secret"],
        field_name="tailscale_authkey_secret",
    )

    kwargs: dict[str, Any] = {
        "name": raw["name"],
        "budget_usd": float(raw["budget_usd"]),
        "provider": raw["provider"],
        "regions": tuple(raw["regions"]),
        "instance_types": tuple(raw["instance_types"]),
        "spot_allocation_strategy": raw["spot_allocation_strategy"],
        "capacity_rebalancing": bool(raw["capacity_rebalancing"]),
        "max_concurrent_workers": int(raw["max_concurrent_workers"]),
        "min_workers_to_start": int(raw["min_workers_to_start"]),
        "partial_fleet_policy": raw["partial_fleet_policy"],
        "ami_ids_by_region": dict(raw["ami_ids_by_region"]),
        "ssh_key_name": raw["ssh_key_name"],
        "tailscale_authkey_secret": tailscale_authkey,
        "studies": studies,
        "global_auto_stop": global_auto_stop,
    }
    # Pass through every optional tuning field if present in YAML.
    # Keep this list in lockstep with `CampaignConfig` field additions —
    # adding a field to the dataclass without listing it here silently
    # drops operator YAML overrides (audit finding V1 / 2026-04-19).
    for opt in (
        "max_lifetime_hours",
        "visibility_timeout_seconds",
        "janitor_interval_seconds",
        "worker_poll_margin_seconds",
        "fleet_provision_timeout_seconds",
        "result_timeout_seconds",
        "ledger_heartbeat_interval_seconds",
        "base_flask_port",
        "redis_port",
        "redis_preflight_timeout_seconds",
        "matchup_slots_per_worker",
        "teardown_retry_delay_seconds",
        "teardown_thread_join_seconds",
        "flask_ports_per_study",
        "game_dir",
        # Phase-7-prep additions (spot-price cache TTL, janitor hard cap,
        # heartbeat staleness multiplier).
        "spot_price_cache_ttl_seconds",
        "max_requeues",
        "heartbeat_stale_multiplier",
    ):
        if opt in raw:
            kwargs[opt] = raw[opt]
    if "ledger_warn_thresholds" in raw:
        kwargs["ledger_warn_thresholds"] = tuple(raw["ledger_warn_thresholds"])

    return CampaignConfig(**kwargs)


# ---- Cost ledger -------------------------------------------------------------


class CostLedger:
    """Append-only JSONL cost ledger with fsync discipline.

    One row per active worker per ledger_heartbeat_interval_seconds. Hard-
    stops the campaign when cumulative cost reaches budget_usd.
    """

    def __init__(
        self,
        path: Path,
        budget_usd: float,
        warn_thresholds: tuple[float, ...] = (0.5, 0.8, 0.95),
    ) -> None:
        self._path = path
        self._budget_usd = budget_usd
        self._warn_thresholds = tuple(sorted(warn_thresholds))
        self._cumulative = 0.0
        self._warned: set[float] = set()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def cumulative_usd(self) -> float:
        return self._cumulative

    def record_heartbeat(
        self,
        *,
        worker_id: str,
        region: str,
        instance_type: str,
        hours_elapsed: float,
        rate_usd_per_hr: float,
        event_type: str = "worker_heartbeat",
    ) -> CostLedgerEntry:
        delta = hours_elapsed * rate_usd_per_hr
        self._cumulative += delta
        entry = CostLedgerEntry(
            timestamp=datetime.now(UTC).isoformat(),
            event_type=event_type,
            worker_id=worker_id,
            region=region,
            instance_type=instance_type,
            hours_elapsed=hours_elapsed,
            delta_usd=delta,
            cumulative_usd=self._cumulative,
        )
        self._append(entry)
        self._maybe_warn()
        if self._cumulative >= self._budget_usd:
            raise BudgetExceeded(
                f"cumulative_usd={self._cumulative:.2f} >= budget_usd={self._budget_usd:.2f}"
            )
        return entry

    def _append(self, entry: CostLedgerEntry) -> None:
        line = json.dumps(asdict(entry)) + "\n"
        with open(self._path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def _maybe_warn(self) -> None:
        fraction = self._cumulative / self._budget_usd if self._budget_usd else 0.0
        for threshold in self._warn_thresholds:
            if fraction >= threshold and threshold not in self._warned:
                self._warned.add(threshold)
                logger.warning(
                    "budget threshold crossed: threshold=%.2f cumulative=%.2f budget=%.2f",
                    threshold,
                    self._cumulative,
                    self._budget_usd,
                )


# ---- Reliable-queue janitor --------------------------------------------------


def run_janitor_pass(
    redis_client: Any,
    source_list: str,
    processing_list: str,
    visibility_timeout_seconds: float,
    max_requeues: int,
) -> int:
    """Re-queue items in the processing list older than visibility_timeout_seconds.

    Resets `enqueued_at` so the re-queued item gets a fresh visibility window
    (prevents the M1 ping-pong bug where slow-but-healthy matchups get
    re-queued every janitor interval forever). Tracks `requeue_count` per
    item and drops items exceeding `max_requeues` with an ERROR log so
    pathological matchups surface rather than loop silently.
    """
    now = time.time()
    requeued = 0
    for raw in redis_client.lrange(processing_list, 0, -1):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        enqueued_at = item.get("enqueued_at", now)
        if (now - enqueued_at) <= visibility_timeout_seconds:
            continue
        removed = redis_client.lrem(processing_list, 1, raw)
        if removed != 1:
            continue
        requeue_count = int(item.get("requeue_count", 0)) + 1
        if requeue_count > max_requeues:
            logger.error(
                "matchup %s exceeded max_requeues=%d; dropping",
                item.get("matchup_id", "?"),
                max_requeues,
            )
            continue
        item["enqueued_at"] = now
        item["requeue_count"] = requeue_count
        redis_client.lpush(source_list, json.dumps(item))
        requeued += 1
        logger.warning(
            "requeued stuck matchup: matchup_id=%s age=%.1fs requeue_count=%d",
            item.get("matchup_id", "?"),
            now - enqueued_at,
            requeue_count,
        )
    return requeued


# ---- Preflight helpers -------------------------------------------------------


# devenv-up.sh (rootless) writes its userspace-mode tailscaled socket here.
# Preflight auto-detects it so CLI calls target the right daemon without
# the user having to export anything.
_DEFAULT_USERSPACE_TS_SOCKET = (
    Path.home() / ".local/state/starsector-cloud/tailscale/tailscaled.sock"
)
_TS_CLI_TIMEOUT_SECONDS = 5.0


class PreflightFailure(ValueError):
    """Raised by preflight helpers (`check_*` module-level functions and
    `CampaignManager._preflight`). ValueError subclass so callers (e.g.
    `honest_evaluator._preflight_for_honest_eval`) that propagate as
    plain `ValueError` work without rewrapping. `CampaignManager.run`
    catches this and translates to `sys.exit(2)`.
    """


def _tailscale_socket_args() -> list[str]:
    """Resolve which tailscaled socket `tailscale` CLI calls should target.

    Detection order:
      1. ``STARSECTOR_TAILSCALE_SOCKET`` env var (explicit override).
      2. Default rootless path written by ``scripts/cloud/devenv-up.sh``.

    When neither applies, returns ``[]`` and CLI calls go to the system socket
    (kernel-mode tailscaled installed via distro package).
    """
    env_sock = os.environ.get("STARSECTOR_TAILSCALE_SOCKET")
    if env_sock:
        return ["--socket", env_sock]
    if _DEFAULT_USERSPACE_TS_SOCKET.is_socket():
        return ["--socket", str(_DEFAULT_USERSPACE_TS_SOCKET)]
    return []


def _resolve_tailnet_ip(timeout_seconds: float = _TS_CLI_TIMEOUT_SECONDS) -> str:
    """Shell out to `tailscale ip -4`. Empty stdout → PreflightFailure."""
    cmd = ["tailscale", *_tailscale_socket_args(), "ip", "-4"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as e:
        raise PreflightFailure(
            "`tailscale` not found on PATH. Install Tailscale and run "
            "`scripts/cloud/devenv-up.sh` (rootless) or `tailscale up` "
            "(kernel-mode) before launching a campaign."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise PreflightFailure(f"`tailscale ip -4` timed out: {e}") from e
    ip = (result.stdout or "").strip().split("\n")[0].strip()
    if not ip:
        raise PreflightFailure(
            "`tailscale ip -4` returned no address. Run "
            "`scripts/cloud/devenv-up.sh` (rootless) or `tailscale up` "
            "(kernel-mode) to join the tailnet before launching a campaign."
        )
    return ip


def _tailscale_serve_exposes_port(port: int) -> bool:
    """True iff `tailscale serve status` forwards ``port`` → ``127.0.0.1:port``.

    Userspace-mode tailscaled cannot bind the tailnet IP to a kernel
    interface, so workers reach workstation services through `tailscale
    serve` TCP proxies. This check verifies the proxy mapping is in place.
    """
    cmd = ["tailscale", *_tailscale_socket_args(), "serve", "status"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TS_CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    haystack = (result.stdout or "") + (result.stderr or "")
    return f"127.0.0.1:{port}" in haystack


def _check_redis_reachable(
    tailnet_ip: str,
    port: int,
    timeout_seconds: float,
) -> None:
    """Verify Redis is running AND reachable by workers over the tailnet.

    Two supported configurations:
      * kernel-mode Tailscale — Redis bound to the tailnet IP locally.
      * userspace-mode Tailscale — Redis bound to 127.0.0.1 and exposed via
        ``tailscale serve --tcp=<port> tcp://127.0.0.1:<port>``.

    Step 1 (universal) confirms redis-server is up: ping ``127.0.0.1:<port>``.
    Step 2 confirms tailnet exposure: either a tailnet-IP self-loopback ping
    succeeds (kernel mode) OR ``tailscale serve status`` lists the proxy
    mapping (userspace mode).
    """
    import redis as redis_mod

    try:
        redis_mod.Redis(
            host="127.0.0.1",
            port=port,
            socket_timeout=timeout_seconds,
        ).ping()
    except Exception as e:
        raise PreflightFailure(
            f"Redis not reachable on 127.0.0.1:{port}: {e}. "
            f"Start redis-server; see scripts/cloud/devenv-up.sh for a "
            f"rootless recipe."
        ) from e

    try:
        redis_mod.Redis(
            host=tailnet_ip,
            port=port,
            socket_timeout=timeout_seconds,
        ).ping()
        return
    except Exception:
        pass

    if _tailscale_serve_exposes_port(port):
        return

    raise PreflightFailure(
        f"Redis responds on 127.0.0.1:{port} but is not reachable over the "
        f"tailnet. Either (kernel-mode) bind redis-server to {tailnet_ip!r} "
        f"or (userspace-mode) run "
        f"`tailscale serve --bg --tcp={port} tcp://127.0.0.1:{port}`. "
        f"`scripts/cloud/devenv-up.sh` handles both rootlessly."
    )


def _flush_stale_campaign_keys(
    project_tag: str,
    port: int,
    timeout_seconds: float,
) -> int:
    """Delete any ``queue:{project_tag}:*`` + ``worker:{project_tag}:*`` keys.

    Prevents a re-launched campaign with the same name from inheriting stale
    processing-list items (which the janitor would otherwise re-dispatch as
    phantom matchups) or stale worker-heartbeat hashes. SCAN is used instead
    of KEYS so a crowded Redis doesn't block the preflight.
    """
    import redis as redis_mod

    client = redis_mod.Redis(
        host="127.0.0.1",
        port=port,
        socket_timeout=timeout_seconds,
    )
    deleted = 0
    for pattern in (f"queue:{project_tag}:*", f"worker:{project_tag}:*"):
        for key in client.scan_iter(match=pattern, count=1000):
            client.delete(key)
            deleted += 1
    if deleted:
        logger.info("preflight: flushed %d stale Redis keys for %s", deleted, project_tag)
    return deleted


def check_aws_credentials() -> None:
    """Verify AWS credentials are valid via STS `get_caller_identity`.

    Raises `PreflightFailure` (a `ValueError` subclass) on failure.
    Public so `honest_evaluator._preflight_for_honest_eval` can reuse the
    same gate as `CampaignManager._preflight`.
    """
    import boto3

    try:
        boto3.client("sts").get_caller_identity()
    except Exception as e:
        raise PreflightFailure(
            f"AWS credentials unavailable: {e}. "
            "Use the project AWS profile from `.env` (AWS_PROFILE=starsector) "
            "or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY for the dedicated "
            "EC2-only IAM user."
        ) from e


def check_authkey_syntax(authkey: str) -> None:
    """Validate a Tailscale authkey starts with `tskey-auth-`.

    Raises `PreflightFailure` (a `ValueError` subclass) on malformed input.
    Public so `honest_evaluator._preflight_for_honest_eval` can reuse the
    same gate as `CampaignManager._preflight`.
    """
    if not authkey.startswith("tskey-auth-"):
        raise PreflightFailure(
            "tailscale_authkey_secret must start with `tskey-auth-`. "
            "Generate a pre-approved ephemeral key from the Tailscale admin "
            "panel tagged `tag:starsector-worker`."
        )


_WORKER_SOURCE_INPUT_PATHS: tuple[str, ...] = (
    "src",
    "pyproject.toml",
    "uv.lock",
    "scripts/cloud/bake_image.sh",
    "scripts/cloud/packer",
)

_AMI_DIRTY_INPUT_PATHS: tuple[str, ...] = (
    *_WORKER_SOURCE_INPUT_PATHS,
    "game/starsector/manifest.json",
)


def _git_object_id(path: str) -> str:
    try:
        cp = subprocess.run(
            ["git", "rev-parse", f"HEAD:{path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except Exception as e:
        raise PreflightFailure(
            f"Unable to determine git object id for {path!r}: {e}. "
            "Run cloud campaigns from a complete git checkout."
        ) from e
    oid = cp.stdout.strip()
    if not oid:
        raise PreflightFailure(f"Unable to determine git object id for {path!r}: empty output.")
    return oid


def worker_source_sha256() -> str:
    """Return a stable digest for source inputs copied into worker AMIs.

    This intentionally does not use whole-repo `HEAD`: documentation-only or
    campaign-YAML commits do not change the bytes baked into the Python worker
    environment, and must not force an AMI rebake.
    """
    h = hashlib.sha256()
    for path in _WORKER_SOURCE_INPUT_PATHS:
        h.update(path.encode())
        h.update(b"\0")
        h.update(_git_object_id(path).encode())
        h.update(b"\0")
    return h.hexdigest()


def manifest_sha256(path: Path | str = Path("game/starsector/manifest.json")) -> str:
    """Return SHA-256 for the committed game manifest JSON file."""
    manifest_path = Path(path)
    try:
        return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except Exception as e:
        raise PreflightFailure(
            f"Unable to hash manifest at {manifest_path}: {e}. "
            "Regenerate the manifest and re-bake the AMI."
        ) from e


def _worker_source_dirty_status() -> str:
    """Return porcelain status for files that determine AMI provenance."""
    cp = subprocess.run(
        ["git", "status", "--porcelain", "--", *_AMI_DIRTY_INPUT_PATHS],
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    return cp.stdout.strip()


def _expected_worker_source_sha() -> str:
    """Reject cloud launches from uncommitted worker-source changes.

    `WorkerSourceSha` names the committed source inputs copied into the AMI.
    Dirty worker or manifest inputs would let an orchestrator run code or game
    data the workers cannot reproducibly have.
    """
    sha = worker_source_sha256()
    try:
        dirty = _worker_source_dirty_status()
    except Exception as e:
        raise PreflightFailure(
            f"Unable to verify clean worker-source checkout: {e}. "
            "Run cloud campaigns from a git checkout."
        ) from e
    if not dirty:
        return sha
    if os.environ.get("STARSECTOR_ALLOW_DIRTY_AMI_LAUNCH") == "1":
        return f"{sha}-dirty"
    sample = "; ".join(dirty.splitlines()[:5])
    raise PreflightFailure(
        "AMI input checkout has uncommitted changes in files copied or "
        f"hashed into the AMI: {sample}. Commit or stash before launching so "
        "WorkerSourceSha matches both the orchestrator and workers. "
        "Set STARSECTOR_ALLOW_DIRTY_AMI_LAUNCH=1 only for disposable "
        "debugging runs."
    )


def check_ami_tags_against_manifest(
    provider: CloudProvider,
    ami_ids_by_region: dict[str, str],
    manifest: Any,
    *,
    required_regions: tuple[str, ...] | list[str] | None = None,
) -> None:
    """Assert every AMI's game, manifest, mod, and worker-source tags
    match the workstation inputs. Raises
    `PreflightFailure` (a `ValueError`) on mismatch.

    Public so `honest_evaluator._preflight_for_honest_eval` can reuse the
    same gate that protects campaign launch — without the check, an
    operator who regenerated the manifest after the AMI was baked would
    silently corrupt the oracle (worker variants no longer match
    orchestrator's `pool_variant_ids`). See spec 22 §"Manifest + AMI
    tag preflight".

    Providers that don't implement `describe_ami_tag` (e.g. the Hetzner
    stub) cause this function to log a warning and return silently —
    matches `CampaignManager._check_manifest_and_ami_tags`'s historical
    behavior so non-AWS preflight paths aren't hard-broken by adopting
    this helper.
    """
    if required_regions is not None:
        missing = [r for r in required_regions if r not in ami_ids_by_region]
        if missing:
            raise PreflightFailure(
                "ami_ids_by_region is missing AMI IDs for configured "
                f"region(s): {', '.join(missing)}. Re-run "
                "scripts/cloud/bake_image.sh and paste every regional AMI "
                "into the campaign YAML."
            )
    worker_source_sha: str | None = None
    manifest_sha = manifest_sha256()
    for region, ami_id in ami_ids_by_region.items():
        try:
            ami_gv = provider.describe_ami_tag(
                ami_id=ami_id,
                region=region,
                tag_key="GameVersion",
            )
            ami_manifest_sha = provider.describe_ami_tag(
                ami_id=ami_id,
                region=region,
                tag_key="ManifestSha256",
            )
            ami_sha = provider.describe_ami_tag(
                ami_id=ami_id,
                region=region,
                tag_key="ModCommitSha",
            )
            ami_worker_sha = provider.describe_ami_tag(
                ami_id=ami_id,
                region=region,
                tag_key="WorkerSourceSha",
            )
        except AttributeError:
            logger.warning(
                "provider %s lacks describe_ami_tag; skipping AMI tag check",
                type(provider).__name__,
            )
            return
        except Exception as e:
            raise PreflightFailure(
                f"describe_ami_tag({ami_id}, {region}) failed: {e}. "
                f"The AMI may be missing or in a different account."
            ) from e
        if worker_source_sha is None:
            worker_source_sha = _expected_worker_source_sha()
        if ami_gv != manifest.constants.game_version:
            raise PreflightFailure(
                f"AMI {ami_id} in {region} tagged GameVersion={ami_gv!r} "
                f"but manifest.game_version={manifest.constants.game_version!r}. "
                f"Re-bake AMI after running scripts/update_manifest.py; "
                f"see .claude/skills/cloud-worker-ops.md."
            )
        if ami_manifest_sha != manifest_sha:
            raise PreflightFailure(
                f"AMI {ami_id} in {region} tagged ManifestSha256="
                f"{ami_manifest_sha!r} but local manifest sha256 is "
                f"{manifest_sha!r}. Re-bake AMI after regenerating or "
                "editing game/starsector/manifest.json."
            )
        # ModCommitSha dual-check (Commit G R6). Gradle's `generateBuildInfo`
        # task stamps the git SHA into the jar; ManifestDumper embeds it
        # into manifest.constants.mod_commit_sha; Packer reads that value
        # back out for the AMI tag. Either value being empty/unknown means
        # the chain broke upstream — refuse to launch.
        mfst_sha = manifest.constants.mod_commit_sha
        if not mfst_sha or mfst_sha == "unknown":
            raise PreflightFailure(
                f"manifest.constants.mod_commit_sha={mfst_sha!r} — the "
                "combat-harness jar was built without git-SHA wiring. "
                "Run `cd combat-harness && ./gradlew clean deploy` from "
                "a git checkout, then `scripts/update_manifest.py`."
            )
        if ami_sha != mfst_sha:
            raise PreflightFailure(
                f"AMI {ami_id} in {region} tagged ModCommitSha={ami_sha!r} "
                f"but manifest.mod_commit_sha={mfst_sha!r}. Re-bake AMI "
                f"after `./gradlew deploy` + `scripts/update_manifest.py`; "
                f"stale-mod AMI would run pre-G probe code against v2 schema."
            )
        if ami_worker_sha != worker_source_sha:
            raise PreflightFailure(
                f"AMI {ami_id} in {region} tagged WorkerSourceSha="
                f"{ami_worker_sha!r} but current worker-source digest is "
                f"{worker_source_sha!r}. Re-bake AMI from the committed "
                "source before launching or resuming cloud workers; "
                "JAR overlay does not update Python worker code."
            )


# ---- Campaign manager --------------------------------------------------------


class CampaignManager:
    """Pure supervisor: preflight → spawn subprocess per (study, seed) → monitor
    → campaign-wide teardown sweep. Does NOT own fleet lifecycle (each study
    subprocess provisions + tears down its own fleet).

    Teardown has four layers from innermost to outermost:
      1. Study subprocess `try/finally` → `provider.terminate_fleet`
      2. `CampaignManager.run()` `try/finally` → `provider.terminate_all_tagged`
      3. `atexit.register(self.teardown)` — crash paths that bypass `finally`
      4. `launch_campaign.sh`'s `trap EXIT` — shell-level SIGKILL recovery
    """

    def __init__(
        self,
        config: CampaignConfig,
        provider: CloudProvider,
        ledger: CostLedger,
        *,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self._config = config
        self._provider = provider
        self._ledger = ledger
        self._token_factory = token_factory
        self._tailnet_ip: str | None = None
        self._study_procs: list[subprocess.Popen] = []
        self._teardown_done = False
        # Ledger-tick state. _redis is populated by _preflight, reused by
        # _tick_ledger; _spot_price_cache holds (rate, fetched_at) per
        # (region, instance_type) refreshed past spot_price_cache_ttl_seconds;
        # _last_tick_ts[worker_id] -> last tick wall time so delta is
        # capped at min(interval, now - last_tick) per tick.
        self._redis: Any = None
        self._spot_price_cache: dict[tuple[str, str], tuple[float, float]] = {}
        self._last_tick_ts: dict[str, float] = {}
        atexit.register(self._atexit_teardown)

    @property
    def _project_tag(self) -> str:
        return f"starsector-{self._config.name}"

    def _atexit_teardown(self) -> None:
        """atexit-safe teardown: idempotent, swallows exceptions."""
        if self._teardown_done:
            return
        try:
            self.teardown()
        except Exception:
            pass

    def install_signal_handlers(self) -> None:
        """Route SIGINT/SIGTERM/SIGHUP → KeyboardInterrupt → finally teardown."""

        def handler(signum, _frame):
            raise KeyboardInterrupt(f"received signal {signum}")

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGHUP, handler)

    # ---- Preflight ----

    def _preflight(self) -> None:
        """Run launch preflight + create long-lived redis client. Failure exits 2."""
        try:
            self._tailnet_ip = _resolve_tailnet_ip()
            _check_redis_reachable(
                tailnet_ip=self._tailnet_ip,
                port=self._config.redis_port,
                timeout_seconds=self._config.redis_preflight_timeout_seconds,
            )
            _flush_stale_campaign_keys(
                project_tag=self._project_tag,
                port=self._config.redis_port,
                timeout_seconds=self._config.redis_preflight_timeout_seconds,
            )
            check_aws_credentials()
            check_authkey_syntax(self._config.tailscale_authkey_secret)
            self._check_manifest_and_ami_tags()
            # Stash a long-lived Redis client for the ledger tick (SCAN +
            # HGETALL per tick). 127.0.0.1 works for both kernel-mode
            # tailscale (tailnet-IP bound locally) and userspace-mode
            # (tailscale serve TCP proxy to localhost).
            import redis

            self._redis = redis.Redis(
                host="127.0.0.1",
                port=self._config.redis_port,
                socket_timeout=self._config.redis_preflight_timeout_seconds,
                decode_responses=True,
            )
        except PreflightFailure as e:
            logger.error("preflight failed: %s", e)
            sys.exit(2)

    def _check_manifest_and_ami_tags(self) -> None:
        """Load the committed manifest, run the AMI-tag cross-check, stash
        the manifest for later spawn use. Delegates the cross-check to
        `check_ami_tags_against_manifest` so honest-eval reuses the same
        gate (spec 30 §Preflight).
        """
        from .game_manifest import GameManifest

        manifest = GameManifest.load()
        check_ami_tags_against_manifest(
            self._provider,
            self._config.ami_ids_by_region,
            manifest,
            required_regions=self._config.regions,
        )
        self._manifest = manifest

    # ---- Subprocess env generation ----

    def _generate_study_env(
        self,
        *,
        study_idx: int,
        seed_idx: int,
        study_cfg: StudyConfig,
    ) -> dict[str, str]:
        """Per-study env dict. Never logged (grep invariant enforces)."""
        assert self._tailnet_ip is not None, "_preflight must run before _generate_study_env"
        return {
            **os.environ,
            "STARSECTOR_WORKSTATION_TAILNET_IP": self._tailnet_ip,
            "STARSECTOR_BEARER_TOKEN": self._token_factory(32),
            "STARSECTOR_TAILSCALE_AUTHKEY": self._config.tailscale_authkey_secret,
            "STARSECTOR_PROJECT_TAG": self._project_tag,
            "STARSECTOR_CAMPAIGN_YAML": str(self._campaign_yaml_path()),
        }

    def _campaign_yaml_path(self) -> Path:
        return Path(os.environ.get("STARSECTOR_CAMPAIGN_YAML", "campaign.yaml"))

    # ---- Spawn ----

    def spawn_studies(self) -> list[subprocess.Popen]:
        """Spawn one subprocess per (study_idx, seed_idx) pair.

        Subprocesses receive the YAML path + both indexes; secrets cross as
        env vars. No pickled objects.

        Per-study SQLite persistence: every spawned study writes its Optuna
        trials to `data/study_dbs/<campaign>/<study_id>.db` (parent dir
        created on demand). One DB per (campaign, study_idx, seed) tuple
        keeps Optuna's `{hull}__{regime}` study-name namespace from
        colliding across ablation cells or seeds, and gives cross-regime
        warm-start a stable on-disk source.
        """
        from starsector_optimizer.cloud_runner import resolve_study_id

        procs: list[subprocess.Popen] = []
        for study_idx, study in enumerate(self._config.studies):
            for seed_idx, _seed in enumerate(study.seeds):
                study_id = resolve_study_id(self._config, study_idx, seed_idx)
                study_db_path = Path("data/study_dbs") / self._config.name / f"{study_id}.db"
                study_db_path.parent.mkdir(parents=True, exist_ok=True)
                cmd = [
                    sys.executable,
                    "scripts/run_optimizer.py",
                    "--worker-pool",
                    "cloud",
                    "--campaign-config",
                    str(self._campaign_yaml_path()),
                    "--study-idx",
                    str(study_idx),
                    "--seed-idx",
                    str(seed_idx),
                    "--hull",
                    study.hull,
                    "--regime",
                    study.regime,
                    "--sampler",
                    study.sampler,
                    "--sim-budget",
                    str(study.budget_per_study),
                    "--game-dir",
                    self._config.game_dir,
                    "--study-db",
                    str(study_db_path),
                ]
                if study.active_opponents is not None:
                    cmd += ["--active-opponents", str(study.active_opponents)]
                if study.warm_start_from_regime is not None:
                    cmd += ["--warm-start-from-regime", study.warm_start_from_regime]
                env = self._generate_study_env(
                    study_idx=study_idx,
                    seed_idx=seed_idx,
                    study_cfg=study,
                )
                # NOTE: env dict is deliberately NOT logged here. The grep
                # invariant in docs/specs/22-cloud-deployment.md enforces
                # `grep -En "logger.*env" campaign.py` returns empty.
                logger.info(
                    "spawn study (%d,%d): %s__%s__%s__seed%d",
                    study_idx,
                    seed_idx,
                    study.hull,
                    study.regime,
                    study.sampler,
                    study.seeds[seed_idx],
                )
                proc = subprocess.Popen(cmd, env=env)
                procs.append(proc)
        self._study_procs = procs
        return procs

    # ---- Monitor / teardown ----

    def monitor_loop(self, study_procs: list[subprocess.Popen]) -> None:
        """Poll heartbeats, tick cost ledger, exit when all studies done."""
        interval = self._config.ledger_heartbeat_interval_seconds
        while any(p.poll() is None for p in study_procs):
            time.sleep(interval)
            self._tick_ledger()

    def _tick_ledger(self) -> None:
        """Iterate live worker heartbeats, attribute spot-price cost to each.

        Reads `worker:<project_tag>:*:heartbeat` hashes from Redis, filters
        out heartbeats older than `heartbeat_stale_multiplier × interval` as
        dead, fetches spot price per (region, instance_type) via the
        orchestrator-local cache, and records one ledger row per live worker
        per tick. `BudgetExceeded` propagates out of `record_heartbeat`
        → out of this method → caught by `run()`'s `except BudgetExceeded`.
        """
        if self._redis is None:
            return
        interval = self._config.ledger_heartbeat_interval_seconds
        stale_cutoff = interval * self._config.heartbeat_stale_multiplier
        now = time.time()
        pattern = f"worker:{self._project_tag}:*:heartbeat"
        for key in self._redis.scan_iter(match=pattern):
            try:
                hash_data = self._redis.hgetall(key)
            except Exception as e:
                logger.warning("ledger_tick: hgetall %s failed: %s", key, e)
                continue
            if not hash_data:
                continue
            try:
                hb_ts = float(hash_data.get("timestamp", 0))
            except (TypeError, ValueError):
                continue
            if now - hb_ts > stale_cutoff:
                continue
            # worker_id extracted from the key: worker:<project>:<worker>:heartbeat
            parts = key.split(":") if isinstance(key, str) else []
            worker_id = parts[2] if len(parts) >= 4 else hash_data.get("worker_id", "unknown")
            region = hash_data.get("region", "unknown")
            instance_type = hash_data.get("instance_type", "unknown")
            rate = self._get_spot_price_cached(region, instance_type, now)
            last_tick = self._last_tick_ts.get(worker_id, now - interval)
            hours_elapsed = min(interval, now - last_tick) / _SECONDS_PER_HOUR
            self._last_tick_ts[worker_id] = now
            self._ledger.record_heartbeat(
                worker_id=worker_id,
                region=region,
                instance_type=instance_type,
                hours_elapsed=hours_elapsed,
                rate_usd_per_hr=rate,
            )

    def _get_spot_price_cached(
        self,
        region: str,
        instance_type: str,
        now: float,
    ) -> float:
        """Return cached spot price, refreshing past ttl."""
        ttl = self._config.spot_price_cache_ttl_seconds
        key = (region, instance_type)
        cached = self._spot_price_cache.get(key)
        if cached is not None and (now - cached[1]) <= ttl:
            return cached[0]
        try:
            rate = self._provider.get_spot_price(region, instance_type)
        except Exception as e:
            logger.warning(
                "get_spot_price(%s, %s) failed: %s — using 0.0",
                region,
                instance_type,
                e,
            )
            rate = 0.0
        self._spot_price_cache[key] = (rate, now)
        return rate

    def _terminate_study_procs(self) -> None:
        """SIGTERM all live study subprocesses, wait, then SIGKILL survivors.

        Without this, study Popen children orphan to init when this process
        exits — they hold the per-study Flask `/result` listener (werkzeug
        non-daemon thread, slow to shut down) + Redis connections, and at
        worst block port-budget for the next cell's listener (`base_flask_port
        + study_idx * flask_ports_per_study`). Idempotent: dead procs are
        skipped, empty list is no-op.

        SIGTERM first because each subprocess installs a handler that raises
        KeyboardInterrupt → its `with CloudWorkerPool` __exit__ runs →
        per-study `finally: terminate_fleet` runs (idempotent w.r.t. the
        campaign-wide sweep we'll do next). Falls back to SIGKILL after
        `study_proc_terminate_timeout_seconds` for any subprocess whose
        non-daemon threads (werkzeug, blocking AWS calls) prevent clean exit.
        """
        if not self._study_procs:
            return
        timeout = self._config.study_proc_terminate_timeout_seconds
        live = [p for p in self._study_procs if p.poll() is None]
        if not live:
            return
        logger.info(
            "teardown: SIGTERM %d study subprocess(es), wait up to %.0fs",
            len(live),
            timeout,
        )
        for p in live:
            try:
                p.terminate()
            except Exception as e:
                logger.warning("SIGTERM pid=%s failed: %s", p.pid, e)
        deadline = time.time() + timeout
        for p in live:
            remaining = max(0.0, deadline - time.time())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                logger.warning("wait pid=%s failed: %s", p.pid, e)
        survivors = [p for p in live if p.poll() is None]
        if survivors:
            logger.warning(
                "teardown: SIGKILL %d study subprocess(es) that didn't exit "
                "within %.0fs (likely stuck werkzeug or blocked AWS call)",
                len(survivors),
                timeout,
            )
            for p in survivors:
                try:
                    p.kill()
                    p.wait(timeout=5.0)
                except Exception as e:
                    logger.warning("SIGKILL pid=%s failed: %s", p.pid, e)

    def teardown(self) -> None:
        """Terminate study subprocs + cloud workers campaign-wide, assert
        list_active empty. One retry on cloud sweep.

        Idempotent. Raises TeardownError if workers remain after retry.
        """
        if self._teardown_done:
            return
        # Step 1: terminate study subprocesses BEFORE the cloud sweep so each
        # subprocess gets a chance to run its own per-study terminate_fleet
        # (which is the closest-to-AWS-state caller). The cloud sweep below
        # is the safety net for stuck subprocesses.
        self._terminate_study_procs()
        tag = self._project_tag
        try:
            self._provider.terminate_all_tagged(tag)
        except Exception as e:
            logger.error("teardown: terminate_all_tagged raised: %s", e)

        active = self._provider.list_active(tag)
        if active:
            time.sleep(self._config.teardown_retry_delay_seconds)
            try:
                self._provider.terminate_all_tagged(tag)
            except Exception as e:
                logger.error("teardown retry: terminate_all_tagged raised: %s", e)
            active = self._provider.list_active(tag)
            if active:
                self._teardown_done = True
                raise TeardownError(f"{len(active)} workers still active after retry: {active[:5]}")
        self._teardown_done = True
        logger.info("teardown complete: campaign=%s", self._config.name)

    def run(self) -> int:
        """Main entry point. Returns process exit code."""
        self.install_signal_handlers()
        try:
            self._preflight()
            procs = self.spawn_studies()
            self.monitor_loop(procs)
            return 0
        except BudgetExceeded as e:
            logger.info("budget cap reached (designed termination): %s", e)
            return 0
        except KeyboardInterrupt:
            logger.warning("interrupted — unwinding teardown")
            return 130
        finally:
            try:
                self.teardown()
            except TeardownError as e:
                logger.error("teardown failed: %s", e)


# ---- CLI entry point ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run a Phase 6 cloud campaign.")
    parser.add_argument("campaign_yaml", type=Path)
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate config + resolve AMI IDs, then exit."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_campaign_config(args.campaign_yaml)
    os.environ["STARSECTOR_CAMPAIGN_YAML"] = str(args.campaign_yaml.resolve())

    if args.dry_run:
        logger.info("dry-run OK: %s", config)
        return 0

    from .cloud_provider import AWSProvider

    provider = AWSProvider(regions=config.regions)
    # Project-relative campaign artifacts. Resolved from the module path
    # (parents[2] = src/starsector_optimizer → src → project_root) so the
    # location is invariant under operator cwd. `launch_campaign.sh` and
    # `scripts/cloud/status.sh` use the same convention.
    project_root = Path(__file__).resolve().parents[2]
    ledger_path = project_root / "data" / "campaigns" / config.name / "ledger.jsonl"
    ledger = CostLedger(
        path=ledger_path,
        budget_usd=config.budget_usd,
        warn_thresholds=config.ledger_warn_thresholds,
    )
    manager = CampaignManager(config, provider, ledger)
    return manager.run()


if __name__ == "__main__":
    sys.exit(main())
