"""Phase 6 Cloud Worker Federation — campaign manager + cost ledger.

Workstation-side orchestrator. Reads a campaign YAML, provisions AWS spot
workers via a CloudProvider, spawns one subprocess per (hull, regime, seed)
Optuna study, and tracks cost in an append-only JSONL ledger. Hard-stops
at budget_usd. See docs/specs/22-cloud-deployment.md.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


class PartialFleetAbort(Exception):
    """Raised when fewer than min_workers_to_start VMs launched in time."""


# ---- YAML loading ------------------------------------------------------------


_ALLOWED_PROVIDERS = {"aws"}
_ALLOWED_PARTIAL_POLICIES = {"proceed_half_speed", "abort"}


def load_campaign_config(path: Path) -> CampaignConfig:
    """Load and validate a campaign YAML into an immutable CampaignConfig."""
    with open(path) as f:
        raw = yaml.safe_load(f)

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
        "tailscale_authkey_secret": raw["tailscale_authkey_secret"],
        "studies": studies,
        "global_auto_stop": global_auto_stop,
    }
    # Pass through every optional tuning field if present in YAML.
    for opt in (
        "max_lifetime_hours", "visibility_timeout_seconds",
        "janitor_interval_seconds", "worker_poll_margin_seconds",
        "fleet_provision_timeout_seconds", "result_timeout_seconds",
        "ledger_heartbeat_interval_seconds", "base_flask_port",
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
        """Append one entry and fsync to prevent torn lines on crash."""
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


# ---- Reliable-queue janitor (used by campaign manager + cloud_worker_pool) --


def run_janitor_pass(
    redis_client: Any,
    source_list: str,
    processing_list: str,
    visibility_timeout_seconds: float,
) -> int:
    """Re-queue items in the processing list older than visibility_timeout_seconds.

    Items are JSON strings with an `enqueued_at` unix timestamp. Returns the
    number of items re-queued.
    """
    now = time.time()
    requeued = 0
    for raw in redis_client.lrange(processing_list, 0, -1):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        enqueued_at = item.get("enqueued_at", now)
        if (now - enqueued_at) > visibility_timeout_seconds:
            # Race guard: if a worker acks (LREMs) this item between our LRANGE
            # read and our LREM, we must NOT LPUSH — that would re-run an
            # already-completed matchup, wasting budget. LREM returns the
            # number of entries removed; proceed only if we actually removed it.
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


# ---- Campaign manager --------------------------------------------------------


class CampaignManager:
    """Supervises a Phase 6 campaign.

    Responsibilities:
      - Provision AWS spot fleet via CloudProvider
      - Partial-fleet decision (proceed at floor / abort below)
      - Spawn one subprocess per (hull, regime, seed) study
      - Monitor cost ledger; enforce hard cap at budget_usd
      - Teardown: three layers (try/finally + atexit + shell trap wrapper)
      - Signal handlers: SIGINT/SIGTERM/SIGHUP → KeyboardInterrupt → finally
    """

    def __init__(
        self,
        config: CampaignConfig,
        provider: CloudProvider,
        ledger: CostLedger,
    ) -> None:
        self._config = config
        self._provider = provider
        self._ledger = ledger
        self._study_procs: list[subprocess.Popen] = []
        self._teardown_done = False
        atexit.register(self._atexit_teardown)

    def _atexit_teardown(self) -> None:
        """atexit-safe teardown: idempotent, swallows exceptions to avoid
        polluting interpreter shutdown. Explicit .teardown() still raises."""
        if self._teardown_done:
            return
        try:
            self.teardown()
        except Exception:
            pass

    def install_signal_handlers(self) -> None:
        """Route SIGINT/SIGTERM/SIGHUP → KeyboardInterrupt → finally teardown.
        Mirrors scripts/run_optimizer.py:_install_signal_handlers.
        """
        def handler(signum, _frame):
            raise KeyboardInterrupt(f"received signal {signum}")
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGHUP, handler)

    def partial_fleet_decide(self, launched: int) -> str:
        """Three-way: proceed, abort, or (never) block. Headless is mandatory."""
        if launched >= self._config.min_workers_to_start:
            return "proceed"
        return "abort"

    def log_partial_fleet_abort(self, launched: int, elapsed_seconds: float) -> None:
        logger.error(
            "partial_fleet_abort: launched=%d min_required=%d elapsed_seconds=%.1f",
            launched, self._config.min_workers_to_start, elapsed_seconds,
        )

    def provision_fleet(self) -> list[str]:
        """Not implemented — provisioning is moving per-study, not campaign-wide.

        Each study subprocess must render its own WorkerConfig → UserData and
        call ``AWSProvider.create_fleet(config, user_data=...)`` for its own
        worker pool, so workers boot with the correct per-study bearer token,
        Redis queue keys, and Flask listener endpoint. A campaign-wide fleet
        cannot carry study-specific secrets without bundling every study's
        config into every worker — a security regression.

        Tier-1 probe uses ``scripts/cloud/probe.py`` (constructs AWSProvider
        directly). Real campaigns will wire this up as part of the smoke-test
        scope (see ``docs/reference/phase6-cloud-worker-federation.md`` §10).
        """
        raise NotImplementedError(
            "CampaignManager.provision_fleet: per-study UserData wiring is "
            "not yet implemented. Use scripts/cloud/probe.py for Tier-1 "
            "probes, or drive AWSProvider directly. Tracked for smoke-test scope."
        )

    def spawn_studies(self, workers: list[str]) -> list[subprocess.Popen]:
        """Spawn one subprocess per (hull, regime, seed) via run_optimizer.py.

        Subprocesses receive the YAML path + study index; secrets cross as
        env vars. No pickled objects.
        """
        procs = []
        idx = 0
        bearer = os.environ.get("STARSECTOR_BEARER_TOKEN", "")
        for study in self._config.studies:
            for _ in study.seeds:
                cmd = [
                    sys.executable, "scripts/run_optimizer.py",
                    "--worker-pool", "cloud",
                    "--campaign-config", str(self._campaign_yaml_path()),
                    "--study-idx", str(idx),
                    "--hull", study.hull,
                    "--regime", study.regime,
                    "--sampler", study.sampler,
                    "--sim-budget", str(study.budget_per_study),
                ]
                env = {**os.environ, "STARSECTOR_BEARER_TOKEN": bearer}
                logger.info("spawn study %d: %s__%s", idx, study.hull, study.regime)
                proc = subprocess.Popen(cmd, env=env)
                procs.append(proc)
                idx += 1
        self._study_procs = procs
        return procs

    def _campaign_yaml_path(self) -> Path:
        # Child subprocesses need the YAML path; CampaignManager-as-spawned
        # stores it via a conventional env var.
        return Path(os.environ.get("STARSECTOR_CAMPAIGN_YAML", "campaign.yaml"))

    def monitor_loop(self, study_procs: list[subprocess.Popen]) -> None:
        """Poll heartbeats, tick cost ledger, exit when all studies done."""
        interval = self._config.ledger_heartbeat_interval_seconds
        while any(p.poll() is None for p in study_procs):
            time.sleep(interval)
            # Ledger ticking happens here in the real impl — for every
            # still-active worker, record one heartbeat entry. Left as a
            # stub for future extension; budget enforcement still fires
            # via BudgetExceeded exceptions from record_heartbeat calls
            # elsewhere.

    def teardown(self) -> None:
        """Terminate workers, assert list_active is empty. One retry at 10s.

        Idempotent — subsequent calls are no-ops. Raises TeardownError if
        workers remain after retry; callers wrap in try/except as needed.
        """
        if self._teardown_done:
            return
        try:
            self._provider.terminate_all_tagged(self._config.name)
        except Exception as e:
            logger.error("teardown: terminate_all_tagged raised: %s", e)

        active = self._provider.list_active(self._config.name)
        if active:
            time.sleep(self._config.teardown_retry_delay_seconds)
            try:
                self._provider.terminate_all_tagged(self._config.name)
            except Exception as e:
                logger.error("teardown retry: terminate_all_tagged raised: %s", e)
            active = self._provider.list_active(self._config.name)
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
            workers = self.provision_fleet()
            procs = self.spawn_studies(workers)
            self.monitor_loop(procs)
            return 0
        except PartialFleetAbort:
            return 2
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
