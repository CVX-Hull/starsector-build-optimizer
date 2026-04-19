"""Worker agent — runs on the cloud VM.

Pulls matchups from the per-study Redis queue over Tailscale, runs them
through a local LocalInstancePool, and POSTs the CombatResult back to
the workstation's study-subprocess Flask listener. Never touches Optuna
and never imports repair (orchestrator-side-only invariant).

See docs/specs/22-cloud-deployment.md.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import signal
import sys
import threading
import time
import typing
from pathlib import Path
from typing import Any

import redis
import requests

from .instance_manager import InstanceConfig, LocalInstancePool
from .models import MatchupConfig, WorkerConfig
from .result_parser import parse_combat_result

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """POST /result returned 401 — bearer token mismatch. Terminate worker."""


# ---- Signals -----------------------------------------------------------------


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGHUP, handler)


# ---- Config loading ----------------------------------------------------------


_ENV_PREFIX = "STARSECTOR_WORKER_"

# Type-dispatched coercion for env var string values. Unknown types raise
# TypeError so any future WorkerConfig field-type addition (bool, tuple, etc.)
# fails loudly rather than silently parsing wrong.
_COERCE: dict[type, Any] = {
    str: str,
    int: int,
    float: float,
}


def _coerce(target_type: type, raw: str) -> Any:
    try:
        fn = _COERCE[target_type]
    except KeyError:
        raise TypeError(
            f"load_worker_config_from_env: no coercion registered for "
            f"{target_type!r}. Add to _COERCE in worker_agent.py."
        ) from None
    return fn(raw)


def load_worker_config_from_env() -> WorkerConfig:
    """Read WorkerConfig from env vars (STARSECTOR_WORKER_* namespace).

    Iterates `dataclasses.fields(WorkerConfig)` + `typing.get_type_hints`
    (resolves PEP-563 string annotations). Missing required field →
    `ValueError`; unknown field type → `TypeError`. Every field the
    dataclass declares is read symmetrically with what `render_user_data`
    emits — no drift between writer and reader.
    """
    hints = typing.get_type_hints(WorkerConfig)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(WorkerConfig):
        env_key = f"{_ENV_PREFIX}{f.name.upper()}"
        raw = os.environ.get(env_key)
        if raw is None:
            has_default = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING
            )
            if not has_default:
                raise ValueError(
                    f"missing required env var: {env_key} "
                    f"(no dataclass default for WorkerConfig.{f.name})"
                )
            continue
        kwargs[f.name] = _coerce(hints[f.name], raw)
    return WorkerConfig(**kwargs)


# ---- Queue helpers -----------------------------------------------------------


def claim_matchup(
    redis_client: Any,
    source: str,
    processing: str,
    timeout: int,
) -> dict[str, Any] | None:
    """BRPOPLPUSH source → processing. Returns None on timeout."""
    raw = redis_client.brpoplpush(source, processing, timeout=timeout)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("bad queue item: %r", raw)
        redis_client.lrem(processing, 1, raw)
        return None


def ack_matchup(
    redis_client: Any,
    processing: str,
    raw_item: str,
) -> None:
    """Remove a claimed item from the processing list after POST succeeded."""
    redis_client.lrem(processing, 1, raw_item)


# ---- HTTP POST ---------------------------------------------------------------


def post_result(
    config: WorkerConfig,
    *,
    matchup_id: str,
    result: dict[str, Any],
) -> None:
    """POST result to orchestrator. Retries http_retry_count times on 5xx/network."""
    body = {
        "matchup_id": matchup_id,
        "result": result,
        "bearer_token": config.bearer_token,
    }
    backoff = config.http_retry_base_seconds
    last_error: Exception | None = None
    for attempt in range(config.http_retry_count + 1):
        try:
            response = requests.post(
                config.http_endpoint, json=body,
                timeout=config.http_post_timeout_seconds,
            )
            if response.status_code == 200:
                return
            if response.status_code == 409:
                # Already-received dedup: silently drop on worker side.
                logger.info("dedup 409 from orchestrator: matchup_id=%s", matchup_id)
                return
            if response.status_code == 401:
                raise AuthError(f"401 from orchestrator: matchup_id={matchup_id}")
            last_error = RuntimeError(
                f"POST /result status={response.status_code}"
            )
        except requests.RequestException as e:
            last_error = e
        if attempt < config.http_retry_count:
            logger.warning("POST retry %d/%d after: %s",
                           attempt + 1, config.http_retry_count, last_error)
            time.sleep(min(backoff, config.http_retry_max_seconds))
            backoff *= config.http_retry_backoff_multiplier
    raise RuntimeError(f"POST /result failed after {config.http_retry_count} retries: {last_error}")


# ---- Lifetime ----------------------------------------------------------------


def should_exit(config: WorkerConfig, started_at: float) -> bool:
    elapsed_hours = (time.monotonic() - started_at) / 3600.0
    return elapsed_hours >= config.max_lifetime_hours


def heartbeat(redis_client: Any, project_tag: str, worker_id: str) -> None:
    """Write worker liveness + CPU-load telemetry.

    `load_avg_*` comes from `os.getloadavg()` so the orchestrator can verify
    the configured `matchup_slots_per_worker` actually matches the box's
    capacity: on c7a.2xlarge (8 vCPU, 2 JVMs @ ~2.5 cores each), healthy
    load_1min should land around 5–7. A persistent load_1min > cpu_count
    indicates over-subscription; < 3 indicates under-utilization.
    """
    load_1, load_5, load_15 = os.getloadavg()
    redis_client.hset(
        f"worker:{project_tag}:{worker_id}:heartbeat",
        mapping={
            "timestamp": time.time(),
            "load_avg_1min": load_1,
            "load_avg_5min": load_5,
            "load_avg_15min": load_15,
            "cpu_count": os.cpu_count() or 0,
        },
    )


# ---- Main loop ---------------------------------------------------------------


def _load_matchup(matchup_dict: dict[str, Any]) -> MatchupConfig:
    """Deserialize a MatchupConfig from the queue payload.

    Missing required fields raise KeyError — orchestrator always produces
    well-formed payloads via result_parser._matchup_to_dict, so a missing
    field is a wire-format invariant violation, not a recoverable case.
    """
    from .models import BuildSpec
    player_builds = tuple(
        BuildSpec(
            variant_id=b["variant_id"],
            hull_id=b["hull_id"],
            weapon_assignments=dict(b["weapon_assignments"]),
            hullmods=tuple(b["hullmods"]),
            flux_vents=int(b["flux_vents"]),
            flux_capacitors=int(b["flux_capacitors"]),
            cr=float(b.get("cr", 0.7)),  # cr has a MatchupConfig default; optional
        )
        for b in matchup_dict["player_builds"]
    )
    return MatchupConfig(
        matchup_id=matchup_dict["matchup_id"],
        player_builds=player_builds,
        enemy_variants=tuple(matchup_dict["enemy_variants"]),
        time_limit_seconds=float(matchup_dict["time_limit_seconds"]),
        time_mult=float(matchup_dict["time_mult"]),
    )


def _consumer_loop(
    *,
    slot_idx: int,
    config: WorkerConfig,
    pool: LocalInstancePool,
    redis_client: Any,
    source: str,
    processing: str,
    started_at: float,
    stop_event: threading.Event,
    auth_failure_event: threading.Event,
    poll_timeout: int,
) -> None:
    """One Redis consumer: BRPOPLPUSH → pool.run_matchup → POST → ack.

    N of these run concurrently (one per matchup slot on this VM), sharing
    a single LocalInstancePool. The pool's internal free-instance queue
    serializes each run_matchup call onto a distinct JVM.
    """
    while not stop_event.is_set() and not auth_failure_event.is_set():
        if should_exit(config, started_at):
            return
        raw = redis_client.brpoplpush(source, processing, timeout=poll_timeout)
        if raw is None:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            redis_client.lrem(processing, 1, raw)
            continue
        try:
            matchup = _load_matchup(item["matchup"])
            result = pool.run_matchup(matchup)
            result_dict = dataclasses.asdict(result)
            post_result(
                config,
                matchup_id=item["matchup_id"],
                result=result_dict,
            )
            ack_matchup(redis_client, processing, raw)
        except AuthError:
            logger.error("slot %d: auth failure — signalling worker exit", slot_idx)
            auth_failure_event.set()
            return
        except Exception as e:
            logger.error("slot %d: matchup failed: %s", slot_idx, e)
            # Leave in processing list; janitor will re-queue after visibility timeout.


def _heartbeat_loop(
    *,
    config: WorkerConfig,
    redis_client: Any,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    """Write worker heartbeat (with CPU load) every interval_seconds."""
    while not stop_event.is_set():
        heartbeat(redis_client, config.project_tag, config.worker_id)
        stop_event.wait(timeout=interval_seconds)


_HEARTBEAT_INTERVAL_SECONDS = 30.0
_MAIN_LOOP_TICK_SECONDS = 1.0
_CONSUMER_JOIN_GRACE_SECONDS = 5.0
_HEARTBEAT_JOIN_TIMEOUT_SECONDS = 5.0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    _install_signal_handlers()

    config = load_worker_config_from_env()
    game_dir = Path(os.environ.get("STARSECTOR_GAME_DIR", "/opt/starsector"))

    redis_client = redis.Redis(
        host=config.redis_host, port=config.redis_port, decode_responses=True,
    )
    source = f"queue:{config.project_tag}:{config.study_id}:source"
    processing = f"queue:{config.project_tag}:{config.study_id}:processing"

    instance_config = InstanceConfig(
        game_dir=game_dir,
        num_instances=config.matchup_slots_per_worker,
    )
    started_at = time.monotonic()
    poll_timeout = max(1, int(config.http_retry_max_seconds))

    stop_event = threading.Event()
    auth_failure_event = threading.Event()

    with LocalInstancePool(instance_config) as pool:
        # Heartbeat thread writes CPU load so the orchestrator can verify
        # matchup_slots_per_worker fits the VM shape.
        hb_thread = threading.Thread(
            target=_heartbeat_loop,
            kwargs={
                "config": config,
                "redis_client": redis_client,
                "interval_seconds": _HEARTBEAT_INTERVAL_SECONDS,
                "stop_event": stop_event,
            },
            name="worker-heartbeat",
            daemon=True,
        )
        hb_thread.start()

        consumer_threads = [
            threading.Thread(
                target=_consumer_loop,
                kwargs={
                    "slot_idx": i,
                    "config": config,
                    "pool": pool,
                    "redis_client": redis_client,
                    "source": source,
                    "processing": processing,
                    "started_at": started_at,
                    "stop_event": stop_event,
                    "auth_failure_event": auth_failure_event,
                    "poll_timeout": poll_timeout,
                },
                name=f"worker-consumer-{i}",
                daemon=True,
            )
            for i in range(config.matchup_slots_per_worker)
        ]
        for t in consumer_threads:
            t.start()

        try:
            while not should_exit(config, started_at):
                if auth_failure_event.is_set():
                    logger.error("auth failure detected; terminating worker")
                    return 1
                time.sleep(_MAIN_LOOP_TICK_SECONDS)
        finally:
            stop_event.set()
            for t in consumer_threads:
                t.join(timeout=poll_timeout + _CONSUMER_JOIN_GRACE_SECONDS)
            hb_thread.join(timeout=_HEARTBEAT_JOIN_TIMEOUT_SECONDS)

    logger.info("worker lifetime elapsed; exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
