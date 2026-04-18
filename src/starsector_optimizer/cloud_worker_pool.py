"""CloudWorkerPool — EvaluatorPool backed by Redis queue + Flask listener.

Runs on the workstation. Enqueues MatchupConfig messages to Redis for
worker VMs to claim, and receives CombatResult POSTs back via an embedded
Flask listener. Implements the reliable-queue pattern (BRPOPLPUSH +
janitor) from docs/specs/22-cloud-deployment.md.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from .campaign import run_janitor_pass
from .evaluator_pool import EvaluatorPool
from .models import CombatResult, MatchupConfig

logger = logging.getLogger(__name__)


class WorkerTimeout(Exception):
    """A dispatched matchup did not receive a result within result_timeout_seconds."""


def _source_key(study_id: str) -> str:
    return f"queue:{study_id}:source"


def _processing_key(study_id: str) -> str:
    return f"queue:{study_id}:processing"


def _matchup_to_dict(matchup: MatchupConfig) -> dict[str, Any]:
    """Serialize MatchupConfig → dict that JSON-round-trips back."""
    from .result_parser import _matchup_to_dict as rp_serialize
    return rp_serialize(matchup)


def _dict_to_combat_result(data: dict[str, Any]) -> CombatResult:
    """Deserialize CombatResult from a POST body."""
    from .result_parser import parse_combat_result
    return parse_combat_result(data)


class CloudWorkerPool(EvaluatorPool):
    """EvaluatorPool that dispatches matchups to cloud workers via Redis.

    Workers pull from Redis, run the matchup locally (in their own
    LocalInstancePool), and POST the result back to a per-study Flask
    listener owned by this pool. run_matchup blocks until a result arrives
    or result_timeout_seconds elapses.
    """

    def __init__(
        self,
        *,
        study_id: str,
        redis_client: Any,
        flask_port: int,
        bearer_token: str,
        workers_per_study: int,
        result_timeout_seconds: float,
        visibility_timeout_seconds: float,
        janitor_interval_seconds: float,
        teardown_thread_join_seconds: float = 5.0,
    ) -> None:
        self._study_id = study_id
        self._redis = redis_client
        self._flask_port = flask_port
        self._bearer = bearer_token
        self._workers_per_study = workers_per_study
        self._result_timeout_seconds = result_timeout_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._janitor_interval_seconds = janitor_interval_seconds
        self._teardown_thread_join_seconds = teardown_thread_join_seconds

        self._source = _source_key(study_id)
        self._processing = _processing_key(study_id)

        self._results: dict[str, CombatResult] = {}
        self._seen: set[str] = set()                     # matchup_ids that have been POSTed
        self._results_lock = threading.Lock()
        self._result_events: dict[str, threading.Event] = {}

        self._stop_event = threading.Event()
        self._janitor_thread: threading.Thread | None = None
        self._server = None
        self._server_thread: threading.Thread | None = None

        self.app = self._build_app()

        # Pool concurrency cap matches workers_per_study.
        self._dispatch_semaphore = threading.BoundedSemaphore(workers_per_study)

    @property
    def num_workers(self) -> int:
        return self._workers_per_study

    # ---- Flask app ----

    def _build_app(self) -> Flask:
        app = Flask(__name__)
        app.config["PROPAGATE_EXCEPTIONS"] = True

        @app.post("/result")
        def _result():
            body = request.get_json(silent=True) or {}
            if body.get("bearer_token") != self._bearer:
                return jsonify({"error": "bad bearer"}), 401
            matchup_id = body.get("matchup_id")
            if not matchup_id:
                return jsonify({"error": "missing matchup_id"}), 400
            with self._results_lock:
                if matchup_id in self._seen:
                    return jsonify({"status": "duplicate"}), 409
                try:
                    self._results[matchup_id] = _dict_to_combat_result(
                        body.get("result", {})
                    )
                except Exception as e:
                    logger.error("failed to parse result: %s", e)
                    return jsonify({"error": "bad result"}), 400
                self._seen.add(matchup_id)
                event = self._result_events.get(matchup_id)
            if event is not None:
                event.set()
            return jsonify({"status": "ok"}), 200

        return app

    # ---- Pool lifecycle ----

    def setup(self) -> None:
        """Start Flask listener + janitor thread."""
        self._stop_event.clear()
        self._server = make_server("0.0.0.0", self._flask_port, self.app, threaded=True)
        self._server_thread = threading.Thread(
            target=self._server.serve_forever, name="cloud-worker-flask", daemon=True,
        )
        self._server_thread.start()
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop, name="cloud-worker-janitor", daemon=True,
        )
        self._janitor_thread.start()
        # Publish endpoint for workers to discover.
        self._redis.hset(
            f"study:{self._study_id}:endpoint",
            mapping={
                "port": self._server.server_port,
                "source_queue": self._source,
                "processing_queue": self._processing,
            },
        )
        logger.info("CloudWorkerPool up: study=%s port=%d",
                    self._study_id, self._server.server_port)

    def teardown(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.shutdown()
        if self._janitor_thread is not None:
            self._janitor_thread.join(timeout=self._teardown_thread_join_seconds)
        if self._server_thread is not None:
            self._server_thread.join(timeout=self._teardown_thread_join_seconds)

    # ---- run_matchup ----

    def run_matchup(self, matchup: MatchupConfig) -> CombatResult:
        """Enqueue + block up to result_timeout_seconds for a POST /result."""
        with self._dispatch_semaphore:
            return self._dispatch_and_wait(matchup)

    def _dispatch_and_wait(self, matchup: MatchupConfig) -> CombatResult:
        matchup_id = matchup.matchup_id
        event = threading.Event()
        with self._results_lock:
            self._result_events[matchup_id] = event

        payload = {
            "matchup_id": matchup_id,
            "enqueued_at": time.time(),
            "matchup": _matchup_to_dict(matchup),
        }
        self._redis.lpush(self._source, json.dumps(payload))

        got = event.wait(timeout=self._result_timeout_seconds)
        with self._results_lock:
            self._result_events.pop(matchup_id, None)
            result = self._results.pop(matchup_id, None)
        if not got or result is None:
            raise WorkerTimeout(
                f"matchup_id={matchup_id} did not receive result "
                f"within {self._result_timeout_seconds}s"
            )
        return result

    # ---- Janitor ----

    def _janitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                run_janitor_pass(
                    self._redis,
                    self._source,
                    self._processing,
                    self._visibility_timeout_seconds,
                )
            except Exception as e:
                logger.error("janitor pass failed: %s", e)
            self._stop_event.wait(timeout=self._janitor_interval_seconds)
