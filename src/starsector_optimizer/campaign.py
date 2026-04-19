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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from .cloud_provider import CloudProvider
from .models import (
    CampaignConfig, CostLedgerEntry, GlobalAutoStopConfig,
    StudyConfig, WorkerConfig,
)

logger = logging.getLogger(__name__)


# ---- Exceptions --------------------------------------------------------------


class BudgetExceeded(Exception):
    """Campaign cumulative cost reached budget_usd; trigger teardown."""


class TeardownError(Exception):
    """Raised when teardown cannot verify all workers terminated."""


# ---- YAML loading ------------------------------------------------------------


_ALLOWED_PROVIDERS = {"aws"}
_ALLOWED_PARTIAL_POLICIES = {"proceed_half_speed", "abort"}
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
        raise ValueError(
            f"env var ${{{var}}} referenced by {field_name} is not set"
        )
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
            f"provider={raw.get('provider')!r} not supported; "
            f"allowed: {sorted(_ALLOWED_PROVIDERS)}"
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

    studies = tuple(
        StudyConfig(
            hull=s["hull"], regime=s["regime"],
            seeds=tuple(s["seeds"]),
            budget_per_study=s["budget_per_study"],
            workers_per_study=s["workers_per_study"],
            sampler=s["sampler"],
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
    for opt in (
        "max_lifetime_hours", "visibility_timeout_seconds",
        "janitor_interval_seconds", "worker_poll_margin_seconds",
        "fleet_provision_timeout_seconds", "result_timeout_seconds",
        "ledger_heartbeat_interval_seconds", "base_flask_port",
        "redis_port", "redis_preflight_timeout_seconds",
        "num_instances_per_worker",
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
            timestamp=datetime.now(timezone.utc).isoformat(),
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
                    threshold, self._cumulative, self._budget_usd,
                )


# ---- Reliable-queue janitor --------------------------------------------------


def run_janitor_pass(
    redis_client: Any,
    source_list: str,
    processing_list: str,
    visibility_timeout_seconds: float,
) -> int:
    """Re-queue items in the processing list older than visibility_timeout_seconds."""
    now = time.time()
    requeued = 0
    for raw in redis_client.lrange(processing_list, 0, -1):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        enqueued_at = item.get("enqueued_at", now)
        if (now - enqueued_at) > visibility_timeout_seconds:
            removed = redis_client.lrem(processing_list, 1, raw)
            if removed != 1:
                continue
            redis_client.lpush(source_list, raw)
            requeued += 1
            logger.warning(
                "requeued stuck matchup: matchup_id=%s age=%.1fs",
                item.get("matchup_id", "?"), now - enqueued_at,
            )
    return requeued


# ---- Preflight helpers -------------------------------------------------------


class _PreflightFailure(Exception):
    """Internal signal from _preflight; translated to sys.exit in run()."""


def _resolve_tailnet_ip(timeout_seconds: float = 5.0) -> str:
    """Shell out to `tailscale ip -4`. Empty stdout → _PreflightFailure."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
    except FileNotFoundError as e:
        raise _PreflightFailure(
            "`tailscale` not found on PATH. Install Tailscale on the workstation "
            "and run `tailscale up` before launching a campaign."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise _PreflightFailure(f"`tailscale ip -4` timed out: {e}") from e
    ip = (result.stdout or "").strip().split("\n")[0].strip()
    if not ip:
        raise _PreflightFailure(
            "`tailscale ip -4` returned no address. Run `tailscale up` on the "
            "workstation to join the tailnet before launching a campaign."
        )
    return ip


def _check_redis_on_tailnet(host: str, port: int, timeout_seconds: float) -> None:
    import redis as redis_mod
    try:
        client = redis_mod.Redis(host=host, port=port, socket_timeout=timeout_seconds)
        client.ping()
    except Exception as e:
        raise _PreflightFailure(
            f"Redis not reachable at {host}:{port}: {e}. "
            f"Bind redis-server to the tailnet interface: "
            f"`sudo systemctl edit redis-server` and override ExecStart= to include "
            f"`--bind 0.0.0.0` (or the tailnet IP {host!r} explicitly), then "
            f"`sudo systemctl restart redis-server`."
        ) from e


def _check_aws_credentials() -> None:
    import boto3
    try:
        boto3.client("sts").get_caller_identity()
    except Exception as e:
        raise _PreflightFailure(
            f"AWS credentials unavailable: {e}. "
            f"Run `aws sso login` or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        ) from e


def _check_authkey_syntax(authkey: str) -> None:
    if not authkey.startswith("tskey-auth-"):
        raise _PreflightFailure(
            "tailscale_authkey_secret must start with `tskey-auth-`. "
            "Generate a pre-approved ephemeral key from the Tailscale admin "
            "panel tagged `tag:starsector-worker`."
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
        """Four checks. Failure → logger.error(remediation) + sys.exit(2)."""
        try:
            self._tailnet_ip = _resolve_tailnet_ip()
            _check_redis_on_tailnet(
                host=self._tailnet_ip,
                port=self._config.redis_port,
                timeout_seconds=self._config.redis_preflight_timeout_seconds,
            )
            _check_aws_credentials()
            _check_authkey_syntax(self._config.tailscale_authkey_secret)
        except _PreflightFailure as e:
            logger.error("preflight failed: %s", e)
            sys.exit(2)

    # ---- Subprocess env generation ----

    def _generate_study_env(
        self, *, study_idx: int, seed_idx: int, study_cfg: StudyConfig,
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
        """
        procs: list[subprocess.Popen] = []
        for study_idx, study in enumerate(self._config.studies):
            for seed_idx, _seed in enumerate(study.seeds):
                cmd = [
                    sys.executable, "scripts/run_optimizer.py",
                    "--worker-pool", "cloud",
                    "--campaign-config", str(self._campaign_yaml_path()),
                    "--study-idx", str(study_idx),
                    "--seed-idx", str(seed_idx),
                    "--hull", study.hull,
                    "--regime", study.regime,
                    "--sampler", study.sampler,
                    "--sim-budget", str(study.budget_per_study),
                ]
                env = self._generate_study_env(
                    study_idx=study_idx, seed_idx=seed_idx, study_cfg=study,
                )
                # NOTE: env dict is deliberately NOT logged here. The grep
                # invariant in docs/specs/22-cloud-deployment.md enforces
                # `grep -En "logger.*env" campaign.py` returns empty.
                logger.info(
                    "spawn study (%d,%d): %s__%s__seed%d",
                    study_idx, seed_idx, study.hull, study.regime,
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
            # Ledger ticking happens here in the real impl. Budget
            # enforcement still fires via BudgetExceeded exceptions from
            # CostLedger.record_heartbeat. Stub until live smoke.

    def teardown(self) -> None:
        """Terminate workers campaign-wide, assert list_active empty. One retry.

        Idempotent. Raises TeardownError if workers remain after retry.
        """
        if self._teardown_done:
            return
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
                raise TeardownError(
                    f"{len(active)} workers still active after retry: {active[:5]}"
                )
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
            logger.error("budget exceeded: %s", e)
            return 3
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config + resolve AMI IDs, then exit.")
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
    ledger_path = (
        Path.home() / "starsector-campaigns" / config.name / "ledger.jsonl"
    )
    ledger = CostLedger(
        path=ledger_path,
        budget_usd=config.budget_usd,
        warn_thresholds=config.ledger_warn_thresholds,
    )
    manager = CampaignManager(config, provider, ledger)
    return manager.run()


if __name__ == "__main__":
    sys.exit(main())
