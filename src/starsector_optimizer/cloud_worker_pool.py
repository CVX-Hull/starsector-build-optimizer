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
from .models import (
    CombatResult, DamageBreakdown, EngineStats, LoadoutDiagnostic, MatchupConfig,
    ShipCombatResult,
)

logger = logging.getLogger(__name__)


# HTTP 422 is returned by /result when the loadout-diagnostic fields show
# corruption. 422 is outside the worker's terminal-status set
# (200/409/401), which forces the worker to leave the matchup in the
# Redis processing list so the janitor can re-queue it. Lifting the
# literal to module scope makes the contract grep-able from worker_agent
# and the spec.
LOADOUT_MISMATCH_HTTP_STATUS = 422


# Mismatch-rate guardrail. The 2026-05-10 Wave-1 C2/C3 incident hit
# ~0.6% mismatch rate (Java cross-trial cache pollution); the post-V2
# rate is even lower. 5% × ≥100 samples is the principled abort line:
# well above empirical noise but tripped quickly if a regressed Java
# fix corrupts every matchup. MIN_SAMPLES=100 (not 50) keeps the
# binomial false-trip probability at the post-V2 noise floor below
# 0.1% — at p=0.006, P(≥6 discards in 100) ≈ 7e-5.
MISMATCH_ABORT_RATE = 0.05
MISMATCH_ABORT_MIN_SAMPLES = 100


# Number of in-flight matchup IDs to include in the stalled-progress WARN
# log. Bounded so the LRANGE is O(SAMPLE) rather than O(processing-list).
STALLED_PROGRESS_INFLIGHT_SAMPLE_COUNT = 10


# Stalled-progress detector. After this many seconds without any /result
# POST AND with at least one matchup still in the source queue, the
# janitor logs WARN with the in-flight matchup IDs (read from the Redis
# processing list) so an operator can tell at a glance whether the
# workers are stuck on specific builds vs simply slow vs dead. Set
# longer than result_timeout_seconds so a single slow matchup doesn't
# trip it; shorter than max_lifetime_hours so a real stall surfaces
# well before the spot fleet expires.
STALLED_PROGRESS_WARN_SECONDS = 600  # 10 min


# Mod-jar fleet-consistency check. Workers report `mod_jar_sha256` in
# their heartbeat (see worker_agent.heartbeat); the janitor scans
# heartbeats and logs WARN if more than one distinct SHA appears in
# the fleet (which means some workers picked up a tailnet override and
# others did not, OR the AMI's baked jar drifted between regions).
# Heterogeneous fleets produce results from different code paths and
# must not be silently combined.
HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS = 300  # 5 min — debounce


class WorkerTimeout(Exception):
    """A dispatched matchup did not receive a result within result_timeout_seconds."""


class LoadoutMismatchAbort(Exception):
    """Raised by run_matchup when the LOADOUT_MISMATCH discard rate exceeds
    `MISMATCH_ABORT_RATE` over `MISMATCH_ABORT_MIN_SAMPLES`+ observations.

    Empirically the post-V2 mismatch rate sits well below 1%; sustained
    rates above the threshold mean the Java fix has regressed (or the
    workers are running a stale jar). Aborting the run surfaces the
    regression instead of letting `max_requeues=5` retries silently drain
    cloud budget on every matchup.
    """


def _source_key(project_tag: str, study_id: str) -> str:
    return f"queue:{project_tag}:{study_id}:source"


def _processing_key(project_tag: str, study_id: str) -> str:
    return f"queue:{project_tag}:{study_id}:processing"


def _matchup_to_dict(matchup: MatchupConfig) -> dict[str, Any]:
    """Serialize MatchupConfig → dict that JSON-round-trips back."""
    from .result_parser import _matchup_to_dict as rp_serialize
    return rp_serialize(matchup)


def _dict_to_ship(data: dict[str, Any]) -> ShipCombatResult:
    """Reconstruct ShipCombatResult from a `dataclasses.asdict` dict.

    Deliberately separate from `result_parser._parse_ship`: that function
    parses the Java harness JSON where `overload_count` is nested under
    `flux_stats`, but the worker already parsed that into a flat
    ShipCombatResult and sent `dataclasses.asdict(...)` — which has
    `overload_count` at the top level (no `flux_stats` wrapper).
    """
    return ShipCombatResult(
        fleet_member_id=data["fleet_member_id"],
        variant_id=data["variant_id"],
        hull_id=data["hull_id"],
        destroyed=data["destroyed"],
        hull_fraction=data["hull_fraction"],
        armor_fraction=data["armor_fraction"],
        cr_remaining=data["cr_remaining"],
        peak_time_remaining=data["peak_time_remaining"],
        disabled_weapons=data["disabled_weapons"],
        flameouts=data["flameouts"],
        damage_dealt=DamageBreakdown(**data["damage_dealt"]),
        damage_taken=DamageBreakdown(**data["damage_taken"]),
        overload_count=data["overload_count"],
    )


def _all_loadouts_match(result: CombatResult) -> bool:
    """True iff every player ship's loadout diagnostic shows all four fields
    matching (weapons + hullmods + flux vents + flux caps). Empty diagnostic
    list (e.g. test fixtures, or pre-V2 paths that never emitted) returns
    True — only an explicit MISMATCH is treated as corruption.

    Used by the `/result` POST handler to reject corrupt matchups so they
    re-queue rather than feed false fitness data into the optimizer (Wave 1
    C1 cross-trial loadout bleed, ~0.6 % rate, 2026-05-10).
    """
    for d in result.player_loadout_diagnostics:
        if not (d.weapons_match and d.hullmods_match
                and d.flux_vents_match and d.flux_capacitors_match):
            return False
    return True


def _log_loadout_diagnostics(matchup_id: str, result: CombatResult) -> None:
    """Per-matchup loadout diagnostic emit. WARN on any mismatched field
    (weapons / hullmods / flux vents / flux caps); INFO on the all-match
    case so the success path is observable too.

    The asymmetric "WARN on mismatch, silent on success" earlier version
    made it hard to tell "diagnostic passed" from "diagnostic empty / never
    ran" — exactly the failure mode that hid the smoke #12 weapons-not-
    applied bug for so long. The success line is concise (one per matchup)
    so it doesn't drown the log under prep-scale runs (~50k matchups).
    """
    for d in result.player_loadout_diagnostics:
        all_match = (
            d.weapons_match
            and d.hullmods_match
            and d.flux_vents_match
            and d.flux_capacitors_match
        )
        if all_match:
            logger.info(
                "LOADOUT_OK matchup=%s ship=%s weapons=%d hullmods=%d "
                "flux=(%d,%d)",
                matchup_id, d.fleet_member_id,
                len(d.live_weapons), len(d.live_hullmods),
                d.live_flux_vents, d.live_flux_capacitors,
            )
            continue
        logger.warning(
            "LOADOUT_MISMATCH matchup=%s ship=%s "
            "weapons_match=%s hullmods_match=%s "
            "flux_vents_match=%s flux_capacitors_match=%s "
            "spec_weapons=%s live_weapons=%s "
            "spec_hullmods=%s live_hullmods=%s "
            "spec_flux=(%d,%d) live_flux=(%d,%d)",
            matchup_id, d.fleet_member_id,
            d.weapons_match, d.hullmods_match,
            d.flux_vents_match, d.flux_capacitors_match,
            d.spec_weapons, d.live_weapons,
            d.spec_hullmods, d.live_hullmods,
            d.spec_flux_vents, d.spec_flux_capacitors,
            d.live_flux_vents, d.live_flux_capacitors,
        )


def _dict_to_loadout_diagnostic(data: dict[str, Any]) -> LoadoutDiagnostic:
    """Reconstruct LoadoutDiagnostic from a `dataclasses.asdict` dict."""
    return LoadoutDiagnostic(
        fleet_member_id=data["fleet_member_id"],
        spec_weapons=dict(data["spec_weapons"]),
        live_weapons=dict(data["live_weapons"]),
        spec_hullmods=tuple(data["spec_hullmods"]),
        live_hullmods=tuple(data["live_hullmods"]),
        spec_flux_vents=int(data["spec_flux_vents"]),
        live_flux_vents=int(data["live_flux_vents"]),
        spec_flux_capacitors=int(data["spec_flux_capacitors"]),
        live_flux_capacitors=int(data["live_flux_capacitors"]),
        weapons_match=bool(data["weapons_match"]),
        hullmods_match=bool(data["hullmods_match"]),
        flux_vents_match=bool(data["flux_vents_match"]),
        flux_capacitors_match=bool(data["flux_capacitors_match"]),
    )


def _dict_to_combat_result(data: dict[str, Any]) -> CombatResult:
    """Reconstruct CombatResult from a POST body.

    The POST body carries `dataclasses.asdict(result)` from the worker —
    i.e. the CombatResult schema, NOT the raw Java harness JSON. Inverse
    of `asdict`; handles the nested `ShipCombatResult` + `DamageBreakdown`
    + `LoadoutDiagnostic` + optional `EngineStats` dataclasses.
    """
    engine_stats_raw = data.get("engine_stats")
    return CombatResult(
        matchup_id=data["matchup_id"],
        winner=data["winner"],
        duration_seconds=data["duration_seconds"],
        player_ships=tuple(_dict_to_ship(s) for s in data["player_ships"]),
        enemy_ships=tuple(_dict_to_ship(s) for s in data["enemy_ships"]),
        player_ships_destroyed=data["player_ships_destroyed"],
        enemy_ships_destroyed=data["enemy_ships_destroyed"],
        player_ships_retreated=data["player_ships_retreated"],
        enemy_ships_retreated=data["enemy_ships_retreated"],
        player_loadout_diagnostics=tuple(
            _dict_to_loadout_diagnostic(d) for d in data["player_loadout_diagnostics"]
        ),
        engine_stats=(
            EngineStats(**engine_stats_raw) if engine_stats_raw is not None else None
        ),
        debug_dumps=tuple(data.get("debug_dumps") or ()),
    )


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
        project_tag: str,
        redis_client: Any,
        flask_port: int,
        bearer_token: str,
        total_matchup_slots: int,
        result_timeout_seconds: float,
        visibility_timeout_seconds: float,
        janitor_interval_seconds: float,
        max_requeues: int,
        teardown_thread_join_seconds: float = 5.0,
    ) -> None:
        self._study_id = study_id
        self._project_tag = project_tag
        self._redis = redis_client
        self._flask_port = flask_port
        self._bearer = bearer_token
        self._total_matchup_slots = total_matchup_slots
        self._result_timeout_seconds = result_timeout_seconds
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._janitor_interval_seconds = janitor_interval_seconds
        self._max_requeues = max_requeues
        self._teardown_thread_join_seconds = teardown_thread_join_seconds

        self._source = _source_key(project_tag, study_id)
        self._processing = _processing_key(project_tag, study_id)

        self._results: dict[str, CombatResult] = {}
        self._seen: set[str] = set()                     # matchup_ids that have been POSTed
        self._results_lock = threading.Lock()
        self._result_events: dict[str, threading.Event] = {}

        # Mismatch-rate guardrail: count discards alongside successes.
        # `_mismatch_discard_count` increments per 422 response;
        # `run_matchup` checks the running rate and raises
        # LoadoutMismatchAbort if it exceeds MISMATCH_ABORT_RATE after
        # MISMATCH_ABORT_MIN_SAMPLES observations.
        self._mismatch_discard_count: int = 0

        # Stalled-progress detector. Updated by `/result` on every
        # accepted (200) or discarded (422) POST — the diagnostic
        # signal we care about is "did anything change", not "did a
        # matchup succeed". `_stalled_warn_emitted` debounces so we
        # log once per stall episode, not once per janitor tick.
        self._last_post_at: float = time.time()
        self._stalled_warn_emitted: bool = False

        # Mod-jar fleet consistency. Last time we emitted the WARN, so
        # we don't repeat every janitor tick during an ongoing stale
        # state. 0 = never emitted; otherwise float seconds since epoch.
        self._last_jar_warn_at: float = 0.0

        self._stop_event = threading.Event()
        self._janitor_thread: threading.Thread | None = None
        self._server = None
        self._server_thread: threading.Thread | None = None

        self.app = self._build_app()

        # Pool concurrency cap == total JVM slots across the fleet
        # (workers_per_study × matchup_slots_per_worker). StagedEvaluator's
        # ThreadPoolExecutor reads `num_workers` to size its thread count.
        self._dispatch_semaphore = threading.BoundedSemaphore(total_matchup_slots)

    @property
    def num_workers(self) -> int:
        return self._total_matchup_slots

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
                    parsed = _dict_to_combat_result(body.get("result", {}))
                except Exception:
                    logger.exception(
                        "failed to parse result body for matchup_id=%s",
                        matchup_id,
                    )
                    return jsonify({"error": "bad result"}), 400
                _log_loadout_diagnostics(matchup_id, parsed)
                # Pass-through harness debug dumps regardless of loadout
                # outcome — they're useful for diagnosing the mismatch
                # itself.
                if parsed.debug_dumps:
                    for line in parsed.debug_dumps:
                        logger.info("DEBUG_DUMP matchup=%s %s", matchup_id, line)
                # Either acceptance or discard counts as "progress": a
                # POST landed, so the workers are alive and reaching
                # the listener. Use a single update site below the
                # body-parse to cover both paths.
                self._last_post_at = time.time()
                self._stalled_warn_emitted = False
                if not _all_loadouts_match(parsed):
                    # Corrupt matchup: drop the result instead of feeding
                    # false data into fitness aggregation. Don't add to
                    # `_seen`, don't store, don't fire the event. Returning
                    # LOADOUT_MISMATCH_HTTP_STATUS (422) makes the worker
                    # raise (worker_agent.post_result only treats
                    # 200/409/401 as terminal); the matchup stays in the
                    # processing list and the janitor re-queues it after
                    # visibility_timeout. With max_requeues=5 and an
                    # empirical mismatch rate ~0.6 %, P(all 6 attempts
                    # mismatch) ≈ 5e-14. The abort guardrail in
                    # run_matchup catches the regression case where the
                    # rate exceeds MISMATCH_ABORT_RATE.
                    self._mismatch_discard_count += 1
                    logger.warning(
                        "discarding LOADOUT_MISMATCH matchup=%s "
                        "(running discards=%d, accepted=%d) — janitor "
                        "will re-queue (matchup stays in processing list)",
                        matchup_id, self._mismatch_discard_count, len(self._seen),
                    )
                    return jsonify({
                        "error": "loadout_mismatch",
                        "matchup_id": matchup_id,
                    }), LOADOUT_MISMATCH_HTTP_STATUS
                self._results[matchup_id] = parsed
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
        """Enqueue + block up to result_timeout_seconds for a POST /result.

        Pre-flight: if the running LOADOUT_MISMATCH discard rate has
        exceeded MISMATCH_ABORT_RATE over MISMATCH_ABORT_MIN_SAMPLES+
        observations, abort the run instead of letting per-matchup
        retries silently drain cloud budget. Empirically the post-V2
        rate is well below 1%, so a sustained 5%+ rate means the Java
        fix has regressed (or workers are running a stale jar).
        """
        self._check_mismatch_rate()
        with self._dispatch_semaphore:
            return self._dispatch_and_wait(matchup)

    def _check_mismatch_rate(self) -> None:
        """Raise LoadoutMismatchAbort if the running discard rate is too
        high. See module-level MISMATCH_ABORT_RATE / _MIN_SAMPLES.
        """
        with self._results_lock:
            discards = self._mismatch_discard_count
            accepted = len(self._seen)
        total = discards + accepted
        if total < MISMATCH_ABORT_MIN_SAMPLES:
            return
        rate = discards / total
        if rate <= MISMATCH_ABORT_RATE:
            return
        raise LoadoutMismatchAbort(
            f"LOADOUT_MISMATCH discard rate {rate:.1%} "
            f"({discards} discards / {total} total) exceeded "
            f"{MISMATCH_ABORT_RATE:.0%} threshold over "
            f"{MISMATCH_ABORT_MIN_SAMPLES}+ samples. The Java "
            f"unique-variant fix may have regressed, or workers are "
            f"running a stale combat-harness jar. Investigate before "
            f"resuming."
        )

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
                    self._max_requeues,
                )
                self._check_stalled_progress()
                self._check_mod_jar_consistency()
            except Exception:
                logger.exception("janitor pass failed for study=%s", self._study_id)
            self._stop_event.wait(timeout=self._janitor_interval_seconds)

    def _check_mod_jar_consistency(self) -> None:
        """Scan worker heartbeats for `mod_jar_sha256`; log WARN if
        more than one distinct SHA appears in the fleet. Diagnostic
        only — does NOT abort dispatch. Debounced via
        HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS so we don't repeat
        every janitor tick during an ongoing inconsistency.
        """
        if (time.time() - self._last_jar_warn_at) < HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS:
            return
        # Heartbeat key pattern: `worker:{project_tag}:{worker_id}:heartbeat`.
        # Scan all keys for our project_tag.
        try:
            cursor = 0
            keys: list[str] = []
            pattern = f"worker:{self._project_tag}:*:heartbeat"
            while True:
                cursor, batch = self._redis.scan(cursor=cursor, match=pattern, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
            if not keys:
                return
            shas: dict[str, list[str]] = {}
            for key in keys:
                hb = self._redis.hget(key, "mod_jar_sha256")
                # A heartbeat with NO `mod_jar_sha256` field is itself
                # a heterogeneity signal: it means the worker is
                # running pre-2026-05-10 code that didn't report the
                # SHA. Treat as a distinct bucket "missing" so the
                # WARN surfaces this case rather than silently
                # filtering it out.
                if hb is None:
                    sha = "missing"
                else:
                    sha = hb.decode() if isinstance(hb, (bytes, bytearray)) else hb
                shas.setdefault(sha, []).append(
                    key.decode() if isinstance(key, (bytes, bytearray)) else key
                )
        except Exception:
            logger.exception(
                "mod-jar consistency check: redis scan failed for study=%s",
                self._study_id,
            )
            return
        if len(shas) <= 1:
            return
        sha_summary = {sha: len(workers) for sha, workers in shas.items()}
        logger.warning(
            "heterogeneous mod-jar fleet for project=%s: %d distinct "
            "SHAs across %d workers — sha_counts=%s. Workers ran from "
            "different combat-harness jars; results may be inconsistent. "
            "Investigate before publishing findings.",
            self._project_tag, len(shas), sum(sha_summary.values()),
            sha_summary,
        )
        self._last_jar_warn_at = time.time()

    def _check_stalled_progress(self) -> None:
        """If no /result POST has been observed for STALLED_PROGRESS_WARN_SECONDS
        AND there is queue work pending (source or processing), log WARN
        with in-flight matchup IDs so an operator can tell whether
        workers are stuck on specific builds, dead, or unreachable.

        Diagnostic-only; does NOT change dispatch behavior. Debounced
        via `_stalled_warn_emitted` so we don't repeat the WARN on
        every janitor tick during an extended stall.
        """
        if self._stalled_warn_emitted:
            return
        idle_seconds = time.time() - self._last_post_at
        if idle_seconds < STALLED_PROGRESS_WARN_SECONDS:
            return
        try:
            # Bounded reads — LRANGE 0,SAMPLE-1 is O(SAMPLE), not O(N) of
            # the processing list. We don't need the full list, just a
            # sample for the WARN message; LLEN gives the total cheaply.
            source_len = self._redis.llen(self._source)
            processing_len = self._redis.llen(self._processing)
            sample = self._redis.lrange(
                self._processing, 0, STALLED_PROGRESS_INFLIGHT_SAMPLE_COUNT - 1,
            )
        except Exception:
            logger.exception(
                "stalled-progress check: redis read failed for study=%s",
                self._study_id,
            )
            return
        if source_len == 0 and processing_len == 0:
            return
        in_flight: list[str] = []
        for raw in sample:
            try:
                payload = json.loads(raw)
                in_flight.append(str(payload.get("matchup_id")))
            except Exception:
                in_flight.append("<unparseable>")
        logger.warning(
            "stalled progress: no /result POST for %.0fs (threshold=%ds) "
            "study=%s source_queue=%d processing=%d in_flight_sample=%s",
            idle_seconds, STALLED_PROGRESS_WARN_SECONDS,
            self._study_id, source_len, processing_len, in_flight,
        )
        self._stalled_warn_emitted = True
