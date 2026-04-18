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
import time
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


def load_worker_config_from_env() -> WorkerConfig:
    """Read WorkerConfig from env vars (STARSECTOR_WORKER_* namespace)."""
    def env(name: str, default: str | None = None) -> str:
        value = os.environ.get(_ENV_PREFIX + name, default)
        if value is None:
            raise ValueError(f"missing env var: {_ENV_PREFIX + name}")
        return value
    return WorkerConfig(
        campaign_id=env("CAMPAIGN_ID"),
        worker_id=env("WORKER_ID"),
        study_id=env("STUDY_ID"),
        redis_host=env("REDIS_HOST"),
        redis_port=int(env("REDIS_PORT", "6379")),
        http_endpoint=env("HTTP_ENDPOINT"),
        bearer_token=env("BEARER_TOKEN"),
        max_lifetime_hours=float(env("MAX_LIFETIME_HOURS", "6.0")),
    )


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


def heartbeat(redis_client: Any, worker_id: str) -> None:
    redis_client.hset(
        f"worker:{worker_id}:heartbeat",
        mapping={"timestamp": time.time()},
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
    source = f"queue:{config.study_id}:source"
    processing = f"queue:{config.study_id}:processing"

    instance_config = InstanceConfig(
        game_dir=game_dir,
        num_instances=config.num_instances_per_worker,
    )
    started_at = time.monotonic()
    timeout = max(1, int(config.http_retry_max_seconds))  # poll timeout, not retry

    with LocalInstancePool(instance_config) as pool:
        while not should_exit(config, started_at):
            heartbeat(redis_client, config.worker_id)
            raw = redis_client.brpoplpush(source, processing, timeout=timeout)
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
                logger.error("auth failure — terminating worker")
                return 1
            except Exception as e:
                logger.error("matchup failed: %s", e)
                # Leave in processing list; janitor will re-queue after visibility timeout.
    logger.info("worker lifetime elapsed; exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
