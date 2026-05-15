"""Phase 7 learned-surrogate AWS batch helpers."""

from __future__ import annotations

import json
import hashlib
import logging
import math
import os
import re
import secrets
import shlex
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

import yaml
from flask import Flask, jsonify, request, send_file
from werkzeug.serving import make_server

from starsector_optimizer.matchup_features import (
    DEFAULT_FEATURE_PROFILE,
    FEATURE_PROFILES,
    FEATURE_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

CANONICAL_SPLITS: tuple[str, ...] = (
    "build",
    "opponent",
    "component",
    "seed-cell",
    "forward-time",
)
CANONICAL_MODELS: tuple[str, ...] = (
    "random_forest_tuned",
    "catboost_regressor",
    "sparse_pairwise_ridge",
)
DEFAULT_DEPENDENCY_EXTRA = "surrogate"


class BudgetExceeded(RuntimeError):
    """Raised when the configured hard budget would be exceeded."""


class BatchLaunchFailed(RuntimeError):
    """Raised when the live batch cannot reach a publishable completion."""


@dataclass(frozen=True)
class LearnedBatchConfig:
    name: str
    project_tag: str
    fleet_name: str
    regions: tuple[str, ...]
    ami_ids_by_region: dict[str, str]
    instance_types: tuple[str, ...]
    ssh_key_name: str
    spot_allocation_strategy: str
    target_workers: int
    min_workers_to_start: int
    budget_usd: float
    max_lifetime_hours: float
    max_job_attempts: int
    lease_renewal_interval_seconds: float
    lease_grace_seconds: float
    pending_instance_grace_seconds: float
    ledger_heartbeat_interval_seconds: float
    ledger_warn_thresholds: tuple[float, ...]
    tailscale_authkey_secret: str
    control_plane_host: str
    control_plane_port: int
    output_dir: Path
    canonical_output_path: Path
    publish_canonical: bool
    execution_enabled: bool
    source_db_path: Path
    game_dir: Path
    comparator_json_path: Path
    hpo_trials: int
    hpo_jobs: int
    model_thread_count: int
    top_k_values: tuple[int, ...]
    split_seed: int
    hpo_seed: int
    holdout_fraction: float
    train_fraction: float
    honest_eval_usage: str = "diagnostic_only"
    fresh_honest_eval_ledger_id: str | None = None
    primary_top_k: int = 1
    promotion_metric: str = "honest_eval_top_k_recall"
    promotion_threshold: float = 0.0
    claim_label: str = "exploratory"
    final_refit_policy: str = "refit_selected_model_on_all_training_rows_after_selection"
    candidate_universe: str = "source_db_builds"
    deployment_artifact: str = "none"
    feature_profile: str = DEFAULT_FEATURE_PROFILE
    dependency_extra: str = DEFAULT_DEPENDENCY_EXTRA
    root_volume_size_gb: int | None = None
    splits: tuple[str, ...] = CANONICAL_SPLITS
    models: tuple[str, ...] = CANONICAL_MODELS


@dataclass(frozen=True)
class LearnedBatchJob:
    job_id: str
    split: str
    model: str
    output_path: Path


@dataclass(frozen=True)
class JobLease:
    job_id: str
    split: str
    model: str
    attempt: int
    worker_id: str
    lease_expires_at: float


@dataclass
class _JobState:
    job: LearnedBatchJob
    status: str = "pending"
    attempt: int = 0
    worker_id: str | None = None
    lease_expires_at: float | None = None
    last_requeue_reason: str | None = None
    last_requeued_at: float | None = None
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None


class BatchState:
    """In-memory lease/result state for a single batch control plane."""

    def __init__(
        self,
        jobs: Sequence[LearnedBatchJob],
        *,
        lease_grace_seconds: float,
        max_attempts: int,
    ) -> None:
        if lease_grace_seconds <= 0:
            raise ValueError("lease_grace_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._lease_grace_seconds = lease_grace_seconds
        self._max_attempts = max_attempts
        self._states = {job.job_id: _JobState(job=job, events=[]) for job in jobs}
        self._lock = Lock()

    def lease(self, *, now: float | None = None, worker_id: str) -> JobLease | None:
        now = time.time() if now is None else now
        with self._lock:
            for state in self._states.values():
                if state.status == "completed":
                    continue
                if state.status != "pending":
                    continue
                if state.attempt >= self._max_attempts:
                    state.status = "failed"
                    continue
                state.attempt += 1
                state.status = "leased"
                state.worker_id = worker_id
                state.lease_expires_at = now + self._lease_grace_seconds
                state.last_requeue_reason = None
                return JobLease(
                    job_id=state.job.job_id,
                    split=state.job.split,
                    model=state.job.model,
                    attempt=state.attempt,
                    worker_id=worker_id,
                    lease_expires_at=state.lease_expires_at,
                )
        return None

    def renew_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        attempt: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            state = self._states.get(job_id)
            if state is None:
                raise ValueError(f"unknown job id: {job_id}")
            if state.status == "completed":
                return {"status": "completed"}
            if state.status != "leased":
                raise ValueError(f"job {job_id} is not leased")
            if state.worker_id != worker_id:
                raise ValueError(f"job {job_id} is leased to a different worker")
            if state.attempt != attempt:
                raise ValueError(f"job {job_id} lease attempt mismatch")
            if state.lease_expires_at is not None and now > state.lease_expires_at:
                raise ValueError(f"job {job_id} lease expired")
            state.lease_expires_at = now + self._lease_grace_seconds
            return {"status": "renewed", "lease_expires_at": state.lease_expires_at}

    def record_result(
        self,
        job_id: str,
        payload: Mapping[str, Any],
        *,
        worker_id: str,
        attempt: int,
        now: float | None = None,
        before_accept: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        now = time.time() if now is None else now
        with self._lock:
            state = self._states.get(job_id)
            if state is None:
                raise ValueError(f"unknown job id: {job_id}")
            if state.status == "completed":
                return {"status": "duplicate"}
            if state.status != "leased":
                raise ValueError(f"job {job_id} is not leased")
            if state.worker_id != worker_id:
                raise ValueError(f"job {job_id} is leased to a different worker")
            if state.attempt != attempt:
                raise ValueError(f"job {job_id} lease attempt mismatch")
            if state.lease_expires_at is not None and now > state.lease_expires_at:
                raise ValueError(f"job {job_id} lease expired")
            result = dict(payload)
            for result_row in result.get("results", []):
                if result_row.get("split") != state.job.split:
                    raise ValueError(f"result split does not match job {job_id}")
                if result_row.get("model") != state.job.model:
                    raise ValueError(f"result model does not match job {job_id}")
            if before_accept is not None:
                before_accept()
            state.result = result
            state.status = "completed"
            return {"status": "accepted"}

    def record_event(self, job_id: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            state = self._states.get(job_id)
            if state is None:
                raise ValueError(f"unknown job id: {job_id}")
            assert state.events is not None
            state.events.append(dict(payload))

    def requeue_missing_workers(
        self,
        active_worker_ids: set[str],
        *,
        now: float | None = None,
    ) -> tuple[str, ...]:
        """Requeue missing-worker leases only after their renewal grace expires."""
        now = time.time() if now is None else now
        requeued: list[str] = []
        with self._lock:
            for state in self._states.values():
                if state.status != "leased" or state.worker_id in active_worker_ids:
                    continue
                if state.lease_expires_at is None or state.lease_expires_at > now:
                    continue
                if state.attempt >= self._max_attempts:
                    state.status = "failed"
                    state.last_requeue_reason = "missing_worker_attempts_exhausted"
                else:
                    state.status = "pending"
                    state.last_requeue_reason = "missing_worker_lease_expired"
                    requeued.append(state.job.job_id)
                state.last_requeued_at = now
                state.worker_id = None
                state.lease_expires_at = None
        return tuple(requeued)

    def requeue_unrenewed_leases(self, *, now: float | None = None) -> tuple[str, ...]:
        """Requeue leased jobs whose worker stopped renewing its ownership."""
        now = time.time() if now is None else now
        requeued: list[str] = []
        with self._lock:
            for state in self._states.values():
                if (
                    state.status != "leased"
                    or state.lease_expires_at is None
                    or state.lease_expires_at > now
                ):
                    continue
                if state.attempt >= self._max_attempts:
                    state.status = "failed"
                    state.last_requeue_reason = "lease_renewal_attempts_exhausted"
                else:
                    state.status = "pending"
                    state.last_requeue_reason = "lease_renewal_expired"
                    requeued.append(state.job.job_id)
                state.last_requeued_at = now
                state.worker_id = None
                state.lease_expires_at = None
        return tuple(requeued)

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = []
            for state in self._states.values():
                rows.append({
                    "job_id": state.job.job_id,
                    "split": state.job.split,
                    "model": state.job.model,
                    "status": state.status,
                    "attempt": state.attempt,
                    "worker_id": state.worker_id,
                    "lease_expires_at": state.lease_expires_at,
                    "last_requeue_reason": state.last_requeue_reason,
                    "last_requeued_at": state.last_requeued_at,
                    "event_count": len(state.events or ()),
                })
            return {
                "jobs": rows,
                "counts": {
                    status: sum(1 for item in rows if item["status"] == status)
                    for status in ("pending", "leased", "completed", "failed")
                },
            }

    def job(self, job_id: str) -> LearnedBatchJob:
        state = self._states.get(job_id)
        if state is None:
            raise ValueError(f"unknown job id: {job_id}")
        return state.job


class ControlPlaneServer:
    """Small wrapper around Werkzeug so launch code can stop Flask cleanly."""

    def __init__(self, app: Flask, *, host: str, port: int) -> None:
        self._server = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="phase7-learned-batch-control-plane",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5.0)


def _expand_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        name = value[2:-1]
        if name not in os.environ:
            raise ValueError(f"environment variable {name} is required")
        return os.environ[name]
    return value


def load_batch_config(path: Path | str) -> LearnedBatchConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("batch config must be a mapping")
    expanded = {key: _expand_env(value) for key, value in raw.items()}
    cfg = LearnedBatchConfig(
        name=str(expanded["name"]),
        project_tag=str(expanded["project_tag"]),
        fleet_name=str(expanded["fleet_name"]),
        regions=tuple(expanded["regions"]),
        ami_ids_by_region=dict(expanded["ami_ids_by_region"]),
        instance_types=tuple(expanded["instance_types"]),
        ssh_key_name=str(expanded["ssh_key_name"]),
        spot_allocation_strategy=str(expanded["spot_allocation_strategy"]),
        target_workers=int(expanded["target_workers"]),
        min_workers_to_start=int(expanded["min_workers_to_start"]),
        budget_usd=float(expanded["budget_usd"]),
        max_lifetime_hours=float(expanded["max_lifetime_hours"]),
        max_job_attempts=int(expanded.get("max_job_attempts", 4)),
        lease_renewal_interval_seconds=float(
            expanded.get("lease_renewal_interval_seconds", 60.0)
        ),
        lease_grace_seconds=float(expanded.get("lease_grace_seconds", 300.0)),
        pending_instance_grace_seconds=float(
            expanded.get("pending_instance_grace_seconds", 300.0)
        ),
        ledger_heartbeat_interval_seconds=float(expanded["ledger_heartbeat_interval_seconds"]),
        ledger_warn_thresholds=tuple(float(v) for v in expanded["ledger_warn_thresholds"]),
        tailscale_authkey_secret=str(expanded["tailscale_authkey_secret"]),
        control_plane_host=str(expanded["control_plane_host"]),
        control_plane_port=int(expanded["control_plane_port"]),
        output_dir=Path(expanded["output_dir"]),
        canonical_output_path=Path(expanded["canonical_output_path"]),
        publish_canonical=bool(expanded.get("publish_canonical", False)),
        execution_enabled=bool(expanded.get("execution_enabled", True)),
        source_db_path=Path(expanded["source_db_path"]),
        game_dir=Path(expanded["game_dir"]),
        comparator_json_path=Path(expanded["comparator_json_path"]),
        hpo_trials=int(expanded["hpo_trials"]),
        hpo_jobs=int(expanded["hpo_jobs"]),
        model_thread_count=int(expanded["model_thread_count"]),
        top_k_values=tuple(int(v) for v in expanded["top_k"]),
        split_seed=int(expanded["split_seed"]),
        hpo_seed=int(expanded["hpo_seed"]),
        holdout_fraction=float(expanded["holdout_fraction"]),
        train_fraction=float(expanded["train_fraction"]),
        honest_eval_usage=str(expanded.get("honest_eval_usage", "diagnostic_only")),
        fresh_honest_eval_ledger_id=expanded.get("fresh_honest_eval_ledger_id"),
        primary_top_k=int(expanded.get("primary_top_k", 1)),
        promotion_metric=str(expanded.get("promotion_metric", "honest_eval_top_k_recall")),
        promotion_threshold=float(expanded.get("promotion_threshold", 0.0)),
        claim_label=str(expanded.get("claim_label", "exploratory")),
        final_refit_policy=str(
            expanded.get("final_refit_policy", "refit_selected_model_on_all_training_rows_after_selection")
        ),
        candidate_universe=str(expanded.get("candidate_universe", "source_db_builds")),
        deployment_artifact=str(expanded.get("deployment_artifact", "none")),
        feature_profile=str(expanded.get("feature_profile", DEFAULT_FEATURE_PROFILE)),
        dependency_extra=str(expanded.get("dependency_extra", DEFAULT_DEPENDENCY_EXTRA)),
        root_volume_size_gb=(
            None
            if expanded.get("root_volume_size_gb") is None
            else int(expanded["root_volume_size_gb"])
        ),
        splits=tuple(str(v) for v in expanded.get("splits", CANONICAL_SPLITS)),
        models=tuple(str(v) for v in expanded.get("models", CANONICAL_MODELS)),
    )
    validate_batch_config(cfg)
    return cfg


def validate_batch_config(config: LearnedBatchConfig) -> None:
    if not config.name or not config.project_tag.startswith("starsector-"):
        raise ValueError("name and project_tag are required")
    if not config.regions:
        raise ValueError("at least one region is required")
    if len(config.regions) != 1:
        raise ValueError(
            "Phase 7 learned batch currently supports exactly one region; "
            "replacement provisioning needs per-region allocation before multi-region use"
        )
    missing_regions = [region for region in config.regions if region not in config.ami_ids_by_region]
    if missing_regions:
        raise ValueError(f"AMI IDs missing for regions: {', '.join(missing_regions)}")
    if not config.splits or not config.models:
        raise ValueError("splits and models must be non-empty")
    unknown_splits = [split for split in config.splits if split not in CANONICAL_SPLITS]
    if unknown_splits:
        raise ValueError(f"unknown split(s): {', '.join(unknown_splits)}")
    unknown_models = [model for model in config.models if model not in CANONICAL_MODELS]
    if unknown_models:
        raise ValueError(f"unknown model(s): {', '.join(unknown_models)}")
    if config.feature_profile not in FEATURE_PROFILES:
        raise ValueError(f"unknown feature_profile: {config.feature_profile}")
    if len(set(config.splits)) != len(config.splits):
        raise ValueError("splits must not contain duplicates")
    if len(set(config.models)) != len(config.models):
        raise ValueError("models must not contain duplicates")
    job_count = len(config.splits) * len(config.models)
    if config.target_workers != job_count:
        raise ValueError("target_workers must equal len(splits) * len(models)")
    if config.target_workers % len(config.regions) != 0:
        raise ValueError(
            "target_workers must be divisible by region count because AWSProvider "
            "currently floors per-region targets"
        )
    if not (1 <= config.min_workers_to_start <= config.target_workers):
        raise ValueError("min_workers_to_start must be between 1 and target_workers")
    if config.min_workers_to_start != config.target_workers:
        raise ValueError(
            "min_workers_to_start must equal target_workers until the live "
            "worker loop has enough evidence to justify partial fleets"
        )
    if config.honest_eval_usage not in ("diagnostic_only", "exploratory_selection", "final_claim"):
        raise ValueError("honest_eval_usage must be diagnostic_only, exploratory_selection, or final_claim")
    if config.honest_eval_usage == "final_claim" and not config.fresh_honest_eval_ledger_id:
        raise ValueError("honest_eval_usage=final_claim requires fresh_honest_eval_ledger_id")
    if config.primary_top_k <= 0:
        raise ValueError("primary_top_k must be positive")
    if config.budget_usd <= 0 or config.max_lifetime_hours <= 0:
        raise ValueError("budget_usd and max_lifetime_hours must be positive")
    if config.max_job_attempts <= 0:
        raise ValueError("max_job_attempts must be positive")
    if config.lease_renewal_interval_seconds <= 0 or config.lease_grace_seconds <= 0:
        raise ValueError("lease renewal interval and grace must be positive")
    if config.lease_renewal_interval_seconds < 1.0:
        raise ValueError("lease renewal interval must be at least one second")
    if not float(config.lease_renewal_interval_seconds).is_integer():
        raise ValueError("lease renewal interval must be an integer number of seconds")
    if config.lease_renewal_interval_seconds >= config.lease_grace_seconds:
        raise ValueError("lease renewal interval must be shorter than lease grace")
    if config.pending_instance_grace_seconds <= 0:
        raise ValueError("pending_instance_grace_seconds must be positive")
    if config.ledger_heartbeat_interval_seconds <= 0:
        raise ValueError("ledger_heartbeat_interval_seconds must be positive")
    if not all(0.0 < value < 1.0 for value in config.ledger_warn_thresholds):
        raise ValueError("ledger_warn_thresholds must be fractions in (0, 1)")
    if tuple(sorted(config.ledger_warn_thresholds)) != config.ledger_warn_thresholds:
        raise ValueError("ledger_warn_thresholds must be sorted")
    if config.dependency_extra != DEFAULT_DEPENDENCY_EXTRA:
        raise ValueError("dependency_extra must use the existing surrogate extra")
    if config.root_volume_size_gb is not None and config.root_volume_size_gb < 8:
        raise ValueError("root_volume_size_gb must be at least 8 when set")
    if config.hpo_jobs <= 0 or config.model_thread_count <= 0:
        raise ValueError("hpo_jobs and model_thread_count must be positive")
    if any("2xlarge" in item for item in config.instance_types):
        raise ValueError("2xlarge instance types require a separate lower-parallelism config")
    if config.hpo_jobs * config.model_thread_count > 16:
        raise ValueError("hpo_jobs * model_thread_count must fit the 16-vCPU worker plan")
    if not all(0.0 < value < 1.0 for value in (config.holdout_fraction, config.train_fraction)):
        raise ValueError("fractions must be in (0, 1)")
    if tuple(sorted(config.top_k_values)) != config.top_k_values:
        raise ValueError("top_k values must be sorted")
    canonical_matrix = (
        config.splits == CANONICAL_SPLITS and config.models == CANONICAL_MODELS
    )
    if config.publish_canonical and not canonical_matrix:
        raise ValueError("publish_canonical is allowed only for the full canonical matrix")


def generate_jobs(config: LearnedBatchConfig) -> tuple[LearnedBatchJob, ...]:
    result_dir = config.output_dir / "results"
    return tuple(
        LearnedBatchJob(
            job_id=f"{split}__{model}",
            split=split,
            model=model,
            output_path=result_dir / f"{split}__{model}.json",
        )
        for split in config.splits
        for model in config.models
    )


def build_job_command(config: LearnedBatchConfig, job: LearnedBatchJob) -> list[str]:
    command = [
        "uv",
        "run",
        "python",
        "scripts/analysis/phase7_learned_surrogate_experiment.py",
        str(config.source_db_path),
        "--game-dir",
        str(config.game_dir),
        "--comparator-json",
        str(config.comparator_json_path),
        "--split",
        job.split,
        "--model",
        job.model,
        "--holdout-fraction",
        str(config.holdout_fraction),
        "--train-fraction",
        str(config.train_fraction),
        "--split-seed",
        str(config.split_seed),
        "--hpo-seed",
        str(config.hpo_seed),
        "--hpo-trials",
        str(config.hpo_trials),
        "--hpo-jobs",
        str(config.hpo_jobs),
        "--model-thread-count",
        str(config.model_thread_count),
        "--top-k",
        ",".join(str(value) for value in config.top_k_values),
        "--feature-profile",
        config.feature_profile,
        "--honest-eval-usage",
        config.honest_eval_usage,
        "--primary-top-k",
        str(config.primary_top_k),
        "--promotion-metric",
        config.promotion_metric,
        "--promotion-threshold",
        str(config.promotion_threshold),
        "--claim-label",
        config.claim_label,
        "--final-refit-policy",
        config.final_refit_policy,
        "--candidate-universe",
        config.candidate_universe,
        "--deployment-artifact",
        config.deployment_artifact,
        "--batch-job-id",
        job.job_id,
        "--batch-name",
        config.name,
        "--batch-fleet-name",
        config.fleet_name,
        "--output",
        str(job.output_path),
    ]
    if config.fresh_honest_eval_ledger_id is not None:
        command.extend(["--fresh-honest-eval-ledger-id", config.fresh_honest_eval_ledger_id])
    return command


def _authorized(expected: str) -> bool:
    header = request.headers.get("Authorization", "")
    return secrets.compare_digest(header, f"Bearer {expected}")


def create_control_plane_app(
    state: BatchState,
    *,
    bundle_path: Path,
    bearer_token: str,
    config: LearnedBatchConfig | None = None,
    bundle_sha256: str | None = None,
) -> Flask:
    app = Flask(__name__)

    @app.before_request
    def _require_bearer() -> tuple[Any, int] | None:
        if not _authorized(bearer_token):
            return jsonify({"error": "unauthorized"}), 401
        return None

    @app.get("/bundle")
    def bundle() -> Any:
        return send_file(bundle_path)

    @app.post("/lease")
    def lease() -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        worker_id = str(payload.get("worker_id") or request.headers.get("X-Worker-Id") or "unknown")
        leased = state.lease(worker_id=worker_id)
        if leased is None:
            return jsonify({"status": "empty"}), 204
        return jsonify(leased.__dict__), 200

    @app.post("/lease/<job_id>/renew")
    def renew_lease(job_id: str) -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        worker_id = str(payload.get("worker_id") or request.headers.get("X-Worker-Id") or "")
        try:
            attempt = int(payload.get("attempt") or request.headers.get("X-Lease-Attempt", "0"))
        except ValueError:
            return jsonify({"error": "invalid lease attempt"}), 400
        try:
            status = state.renew_lease(
                job_id,
                worker_id=worker_id,
                attempt=attempt,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(status), 200

    @app.post("/result/<job_id>")
    def result(job_id: str) -> tuple[Any, int]:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "invalid JSON"}), 400
        worker_id = str(request.headers.get("X-Worker-Id", ""))
        try:
            attempt = int(request.headers.get("X-Lease-Attempt", "0"))
        except ValueError:
            return jsonify({"error": "invalid lease attempt"}), 400
        try:
            job = state.job(job_id)
            if config is not None:
                payload = validate_job_payload(
                    config,
                    job,
                    payload,
                    bundle_sha256=bundle_sha256,
                )
            status = state.record_result(
                job_id,
                payload,
                worker_id=worker_id,
                attempt=attempt,
                before_accept=(
                    lambda: _atomic_write_json(job.output_path, payload)
                    if config is not None else None
                ),
            )
            if status["status"] == "accepted" and config is None:
                _atomic_write_json(job.output_path, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(status), 200

    @app.post("/event/<job_id>")
    def event(job_id: str) -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        try:
            state.record_event(job_id, payload)
            if config is not None:
                _append_jsonl(config.output_dir / "events" / f"{job_id}.jsonl", payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"status": "accepted"}), 200

    @app.get("/status")
    def status() -> Any:
        return jsonify(state.status())

    @app.post("/worker-event")
    def worker_event() -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        if config is not None:
            _append_jsonl(config.output_dir / "events" / "worker-events.jsonl", payload)
        return jsonify({"status": "accepted"}), 200

    return app


def start_control_plane_server(
    config: LearnedBatchConfig,
    state: BatchState,
    *,
    bundle_path: Path,
    bearer_token: str,
    bundle_sha256: str,
) -> ControlPlaneServer:
    app = create_control_plane_app(
        state,
        bundle_path=bundle_path,
        bearer_token=bearer_token,
        config=config,
        bundle_sha256=bundle_sha256,
    )
    server = ControlPlaneServer(
        app,
        host="0.0.0.0",
        port=config.control_plane_port,
    )
    server.start()
    return server


def render_phase7_learned_batch_user_data(
    config: LearnedBatchConfig,
    *,
    control_plane_url: str,
    bearer_token: str,
    bundle_sha256: str,
) -> str:
    if len(bundle_sha256) != 64:
        raise ValueError("bundle_sha256 must be a SHA-256 hex digest")
    max_seconds = int(config.max_lifetime_hours * 3600)
    renewal_interval = int(config.lease_renewal_interval_seconds)
    max_missed_renewals = max(
        1,
        int(config.lease_grace_seconds // config.lease_renewal_interval_seconds),
    )
    top_k = shlex.quote(",".join(str(value) for value in config.top_k_values))
    source_db = shlex.quote(str(config.source_db_path))
    game_dir = shlex.quote(str(config.game_dir))
    comparator_json = shlex.quote(str(config.comparator_json_path))
    dependency_extra = shlex.quote(config.dependency_extra)
    control_url = shlex.quote(control_plane_url)
    fresh_ledger_flag = (
        ""
        if config.fresh_honest_eval_ledger_id is None
        else f"    --fresh-honest-eval-ledger-id {shlex.quote(config.fresh_honest_eval_ledger_id)} \\\n"
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
umask 077
JOB_ID=""
RUN_PID=""
BOOTSTRAP_STEP="start"
UV_BIN="/home/ubuntu/.local/bin/uv"
export PATH="/home/ubuntu/.local/bin:$PATH"

post_event() {{
  if [[ -n "$JOB_ID" ]]; then
    curl --silent --fail -X POST \\
      -H "Authorization: Bearer {bearer_token}" \\
      -H "Content-Type: application/json" \\
      -d "{{\\"event\\":\\"$1\\",\\"instance_id\\":\\"${{INSTANCE_ID:-unknown}}\\",\\"region\\":\\"${{REGION:-unknown}}\\",\\"instance_type\\":\\"${{INSTANCE_TYPE:-unknown}}\\"}}" \\
      {control_url}/event/"$JOB_ID" >/dev/null || true
  fi
}}
post_event_with_log() {{
  if [[ -n "$JOB_ID" ]]; then
    python3 - "$1" "$2" "$3" "$JOB_ID" "${{INSTANCE_ID:-unknown}}" "${{REGION:-unknown}}" "${{INSTANCE_TYPE:-unknown}}" <<'PY'
import json
import sys
import urllib.request
from pathlib import Path

event, log_path, exit_code, job_id, instance_id, region, instance_type = sys.argv[1:8]
try:
    log_tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-12000:]
except OSError as exc:
    log_tail = f"<log unavailable: {{exc}}>"
payload = {{
    "event": event,
    "exit_code": int(exit_code),
    "instance_id": instance_id,
    "region": region,
    "instance_type": instance_type,
    "log_path": log_path,
    "log_tail": log_tail,
}}
request = urllib.request.Request(
    f"{json.loads(json.dumps(control_plane_url))}/event/{{job_id}}",
    data=json.dumps(payload).encode("utf-8"),
    headers={{
        "Authorization": f"Bearer {json.loads(json.dumps(bearer_token))}",
        "Content-Type": "application/json",
    }},
    method="POST",
)
try:
    urllib.request.urlopen(request, timeout=30).read()
except Exception:
    pass
PY
  fi
}}
post_worker_event() {{
  curl --silent --fail -X POST \\
    -H "Authorization: Bearer {bearer_token}" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"event\\":\\"$1\\",\\"instance_id\\":\\"${{INSTANCE_ID:-unknown}}\\",\\"region\\":\\"${{REGION:-unknown}}\\",\\"instance_type\\":\\"${{INSTANCE_TYPE:-unknown}}\\"}}" \\
    {control_url}/worker-event >/dev/null || true
}}
renew_lease_loop() {{
  local renewal_failures=0
  while true; do
    sleep {renewal_interval}
    if [[ -z "$JOB_ID" ]]; then
      continue
    fi
    if curl --silent --fail -X POST \\
      -H "Authorization: Bearer {bearer_token}" \\
      -H "X-Worker-Id: $INSTANCE_ID" \\
      -H "X-Lease-Attempt: $ATTEMPT" \\
      -H "Content-Type: application/json" \\
      -d "{{\\"worker_id\\":\\"$INSTANCE_ID\\",\\"attempt\\":$ATTEMPT}}" \\
      {control_url}/lease/"$JOB_ID"/renew >/dev/null; then
      renewal_failures=0
    else
      renewal_failures=$((renewal_failures + 1))
      post_event "lease_renewal_failed"
      if (( renewal_failures >= {max_missed_renewals} )); then
        post_event "lease_renewal_lost"
        if [[ -n "$RUN_PID" ]]; then
          kill -TERM "$RUN_PID" >/dev/null 2>&1 || true
        fi
        exit 70
      fi
    fi
  done
}}
on_failure() {{
  if [[ -n "$JOB_ID" ]]; then
    post_event "worker_failed"
  else
    post_worker_event "worker_failed_before_lease_${{BOOTSTRAP_STEP}}"
  fi
  shutdown -h now >/dev/null 2>&1 || true
}}
trap on_failure ERR
post_worker_event "bootstrap_start"

BOOTSTRAP_STEP="tailscale_secret"
TS_AUTHKEY_FILE=$(mktemp)
cleanup_secret() {{
  shred -u "$TS_AUTHKEY_FILE" || rm -f "$TS_AUTHKEY_FILE"
}}
trap cleanup_secret EXIT
printf '%s' {json.dumps(config.tailscale_authkey_secret)} > "$TS_AUTHKEY_FILE"
BOOTSTRAP_STEP="tailscale_up"
tailscale up --auth-key=file:"$TS_AUTHKEY_FILE" --advertise-tags=tag:starsector-worker --accept-dns=false
cleanup_secret
trap - EXIT

BOOTSTRAP_STEP="disable_default_worker"
systemctl disable --now starsector-worker.service || true

BOOTSTRAP_STEP="instance_metadata"
IMDS_TOKEN=$(curl --silent --fail -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
INSTANCE_TYPE=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/instance-type)
shutdown -h +$((({max_seconds} + 299) / 60)) >/dev/null 2>&1 || true

BOOTSTRAP_STEP="bundle_download"
mkdir -p /opt/phase7-batch
cd /opt/phase7-batch
curl --silent --fail -H "Authorization: Bearer {bearer_token}" -o bundle.tgz {control_url}/bundle
post_worker_event "bundle_downloaded"
BOOTSTRAP_STEP="bundle_sha256"
printf '%s  bundle.tgz\\n' {json.dumps(bundle_sha256)} | sha256sum --check
BOOTSTRAP_STEP="bundle_extract"
tar -xzf bundle.tgz
BOOTSTRAP_STEP="uv_sync"
"$UV_BIN" sync --frozen --extra {dependency_extra}
post_worker_event "uv_synced"

BOOTSTRAP_STEP="lease_loop"
DEADLINE=$((SECONDS + {max_seconds}))
while (( SECONDS < DEADLINE )); do
  LEASE=$(curl --silent -w '\\n%{{http_code}}' --fail -X POST \\
    -H "Authorization: Bearer {bearer_token}" \\
    -H "Content-Type: application/json" \\
    -d "{{\\"worker_id\\":\\"$INSTANCE_ID\\",\\"region\\":\\"$REGION\\",\\"instance_type\\":\\"$INSTANCE_TYPE\\"}}" \\
    {control_url}/lease || true)
  LEASE_CODE=$(tail -n 1 <<< "$LEASE")
  LEASE_BODY=$(sed '$d' <<< "$LEASE")
  if [[ "$LEASE_CODE" == "204" || -z "$LEASE_BODY" ]]; then
    sleep 30
    continue
  fi
  if [[ "$LEASE_CODE" != "200" ]]; then
    sleep 30
    continue
  fi
  JOB_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<< "$LEASE_BODY")
  SPLIT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["split"])' <<< "$LEASE_BODY")
  MODEL=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["model"])' <<< "$LEASE_BODY")
  ATTEMPT=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["attempt"])' <<< "$LEASE_BODY")
  OUTPUT="data/phase7/aws-job-$JOB_ID.json"
  LOG="data/phase7/aws-job-$JOB_ID.log"
  mkdir -p data/phase7
  REMAINING=$((DEADLINE - SECONDS - {renewal_interval}))
  if (( REMAINING < {renewal_interval} )); then
    post_worker_event "worker_deadline_too_close_for_new_job"
    break
  fi
  JOB_TIMEOUT=$REMAINING
  post_event "lease_acquired"
  post_event "experiment_start"
  renew_lease_loop &
  RENEW_PID=$!

  set +e
  timeout "$JOB_TIMEOUT" "$UV_BIN" run python scripts/analysis/phase7_learned_surrogate_experiment.py \\
    {source_db} \\
    --game-dir {game_dir} \\
    --comparator-json {comparator_json} \\
    --split "$SPLIT" \\
    --model "$MODEL" \\
    --holdout-fraction {config.holdout_fraction} \\
    --train-fraction {config.train_fraction} \\
    --split-seed {config.split_seed} \\
    --hpo-seed {config.hpo_seed} \\
    --hpo-trials {config.hpo_trials} \\
    --hpo-jobs {config.hpo_jobs} \\
    --model-thread-count {config.model_thread_count} \\
    --top-k {top_k} \\
    --feature-profile {shlex.quote(config.feature_profile)} \\
    --honest-eval-usage {shlex.quote(config.honest_eval_usage)} \\
    --primary-top-k {config.primary_top_k} \\
    --promotion-metric {shlex.quote(config.promotion_metric)} \\
    --promotion-threshold {config.promotion_threshold} \\
    --claim-label {shlex.quote(config.claim_label)} \\
    --final-refit-policy {shlex.quote(config.final_refit_policy)} \\
    --candidate-universe {shlex.quote(config.candidate_universe)} \\
    --deployment-artifact {shlex.quote(config.deployment_artifact)} \\
{fresh_ledger_flag}\
    --batch-job-id "$JOB_ID" \\
    --batch-name {shlex.quote(config.name)} \\
    --batch-fleet-name {shlex.quote(config.fleet_name)} \\
    --output "$OUTPUT" >"$LOG" 2>&1 &
  RUN_PID=$!
  wait "$RUN_PID"
  RUN_CODE=$?
  kill "$RENEW_PID" >/dev/null 2>&1 || true
  wait "$RENEW_PID" >/dev/null 2>&1
  RENEW_CODE=$?
  RUN_PID=""
  set -e
  if [[ "$RENEW_CODE" == "70" ]]; then
    post_event_with_log "experiment_failed_lost_lease" "$LOG" "$RENEW_CODE"
    shutdown -h now >/dev/null 2>&1 || true
    exit "$RENEW_CODE"
  fi
  if [[ "$RUN_CODE" != "0" ]]; then
    post_event_with_log "experiment_failed" "$LOG" "$RUN_CODE"
    shutdown -h now >/dev/null 2>&1 || true
    exit "$RUN_CODE"
  fi
  post_event "experiment_completed"

  curl --silent --fail -X POST \\
    -H "Authorization: Bearer {bearer_token}" \\
    -H "X-Worker-Id: $INSTANCE_ID" \\
    -H "X-Lease-Attempt: $ATTEMPT" \\
    -H "Content-Type: application/json" \\
    --data-binary "@$OUTPUT" \\
    {control_url}/result/"$JOB_ID"
  post_event "result_uploaded"
  JOB_ID=""
done
"""


def record_budget_heartbeat(
    config: LearnedBatchConfig,
    *,
    ledger_path: Path,
    running_instances: Sequence[Mapping[str, str]],
    spot_prices: Mapping[tuple[str, str], float],
    interval_seconds: float,
    cumulative_usd: float,
    timestamp: str,
) -> float:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    total = cumulative_usd
    hours = interval_seconds / 3600.0
    rows = []
    for inst in running_instances:
        region = inst.get("region", "unknown")
        instance_type = inst.get("instance_type", "unknown")
        price = float(spot_prices.get((region, instance_type), 0.0))
        delta = price * hours
        total += delta
        rows.append({
            "timestamp": timestamp,
            "event_type": "batch_worker_heartbeat",
            "worker_id": inst.get("instance_id", inst.get("id", "unknown")),
            "region": region,
            "instance_type": instance_type,
            "hours_elapsed": hours,
            "delta_usd": delta,
            "cumulative_usd": total,
        })
    with ledger_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    if total >= config.budget_usd:
        raise BudgetExceeded(
            f"batch budget exceeded: cumulative_usd={total:.4f} budget_usd={config.budget_usd:.4f}"
        )
    return total


def write_status_snapshot(
    config: LearnedBatchConfig,
    state: BatchState,
    *,
    phase: str,
    instance_ids: Sequence[str] = (),
    active_instances: Sequence[Mapping[str, Any]] = (),
    cumulative_usd: float = 0.0,
    message: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    payload = {
        "phase": phase,
        "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "name": config.name,
        "project_tag": config.project_tag,
        "fleet_name": config.fleet_name,
        "instance_ids": list(instance_ids),
        "active_instances": [dict(item) for item in active_instances],
        "budget_usd": config.budget_usd,
        "cumulative_usd": cumulative_usd,
        "state": state.status(),
    }
    if message is not None:
        payload["message"] = message
    _atomic_write_json(config.output_dir / "status.json", payload)
    return payload


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _provision_batch_workers(
    config: LearnedBatchConfig,
    *,
    provider: Any,
    bundle_sha256: str,
    bearer_token: str,
    target_workers: int,
) -> list[str]:
    return provider.provision_fleet(
        fleet_name=config.fleet_name,
        project_tag=config.project_tag,
        regions=config.regions,
        ami_ids_by_region=config.ami_ids_by_region,
        instance_types=config.instance_types,
        ssh_key_name=config.ssh_key_name,
        spot_allocation_strategy=config.spot_allocation_strategy,
        target_workers=target_workers,
        user_data=render_phase7_learned_batch_user_data(
            config,
            control_plane_url=f"http://{config.control_plane_host}:{config.control_plane_port}",
            bearer_token=bearer_token,
            bundle_sha256=bundle_sha256,
        ),
        root_volume_size_gb=config.root_volume_size_gb,
    )


def run_live_batch(
    config: LearnedBatchConfig,
    *,
    provider: Any,
    bundle_path: Path,
    bundle_sha256: str,
    bearer_token: str,
    poll_interval_seconds: float | None = None,
    server_factory: Any = start_control_plane_server,
    sleep_fn: Any = time.sleep,
    now_fn: Any = time.time,
    timestamp_fn: Any = _utc_now,
    final_audit_fn: Any | None = None,
    on_poll: Any | None = None,
) -> dict[str, Any]:
    """Run the live AWS batch lifecycle and return the merged payload.

    The caller owns preflight. This function owns the provision → serve →
    monitor → merge → teardown lifecycle and is deliberately provider-shaped
    so tests can pass a fake provider.
    """
    jobs = generate_jobs(config)
    state = BatchState(
        jobs,
        lease_grace_seconds=config.lease_grace_seconds,
        max_attempts=config.max_job_attempts,
    )
    poll_interval = (
        config.ledger_heartbeat_interval_seconds
        if poll_interval_seconds is None
        else poll_interval_seconds
    )
    cumulative_usd = 0.0
    warned_budget_thresholds: set[float] = set()
    instance_ids: list[str] = []
    pending_instance_ids: dict[str, float] = {}
    terminal_message: str | None = None
    primary_exception: BaseException | None = None
    server = None
    start = now_fn()
    last_budget_heartbeat_at = start - poll_interval
    try:
        server = server_factory(
            config,
            state,
            bundle_path=bundle_path,
            bearer_token=bearer_token,
            bundle_sha256=bundle_sha256,
        )
        write_status_snapshot(config, state, phase="starting", timestamp=timestamp_fn())
        instance_ids = _provision_batch_workers(
            config,
            provider=provider,
            bundle_sha256=bundle_sha256,
            bearer_token=bearer_token,
            target_workers=config.target_workers,
        )
        if len(instance_ids) < config.min_workers_to_start:
            raise BatchLaunchFailed(
                f"only {len(instance_ids)} workers provisioned; "
                f"minimum is {config.min_workers_to_start}"
            )
        pending_instance_ids.update({instance_id: start for instance_id in instance_ids})
        while True:
            now = now_fn()
            if now - start > config.max_lifetime_hours * 3600.0:
                raise TimeoutError("batch exceeded max_lifetime_hours")
            if cumulative_usd >= config.budget_usd:
                raise BudgetExceeded(
                    f"batch budget exceeded before replacement provisioning: "
                    f"cumulative_usd={cumulative_usd:.4f} budget_usd={config.budget_usd:.4f}"
                )
            if on_poll is not None:
                on_poll(state)
            active = provider.list_active(config.project_tag)
            active_worker_ids = {
                str(item.get("instance_id") or item.get("id"))
                for item in active
                if item.get("instance_id") or item.get("id")
            }
            for worker_id in active_worker_ids:
                pending_instance_ids.pop(worker_id, None)
            pre_requeue_status = state.status()
            missing_leased_worker_ids = {
                str(row["worker_id"])
                for row in pre_requeue_status["jobs"]
                if (
                    row["status"] == "leased"
                    and row["worker_id"] is not None
                    and row["worker_id"] not in active_worker_ids
                )
            }
            requeued = state.requeue_missing_workers(active_worker_ids, now=now)
            if requeued:
                logger.warning(
                    "requeued jobs from missing workers: %s",
                    ", ".join(requeued),
                )
                for worker_id in missing_leased_worker_ids:
                    pending_instance_ids.pop(worker_id, None)
            unrenewed = state.requeue_unrenewed_leases(now=now)
            if unrenewed:
                logger.warning(
                    "requeued jobs from stale lease renewals: %s",
                    ", ".join(unrenewed),
                )
                for worker_id in missing_leased_worker_ids:
                    pending_instance_ids.pop(worker_id, None)
            expired_pending = [
                worker_id
                for worker_id, launched_at in pending_instance_ids.items()
                if now - launched_at > config.pending_instance_grace_seconds
            ]
            if expired_pending:
                raise BatchLaunchFailed(
                    "pending workers did not become active before grace: "
                    + ", ".join(sorted(expired_pending)[:5])
                )
            status = state.status()
            counts = status["counts"]
            spot_prices = {
                (item.get("region", "unknown"), item.get("instance_type", "unknown")):
                provider.get_spot_price(
                    item.get("region", "unknown"),
                    item.get("instance_type", "unknown"),
                )
                for item in active
            }
            budget_interval = max(0.0, now - last_budget_heartbeat_at)
            last_budget_heartbeat_at = now
            cumulative_usd = record_budget_heartbeat(
                config,
                ledger_path=config.output_dir / "ledger.jsonl",
                running_instances=active,
                spot_prices=spot_prices,
                interval_seconds=budget_interval,
                cumulative_usd=cumulative_usd,
                timestamp=timestamp_fn(),
            )
            budget_fraction = cumulative_usd / config.budget_usd
            for threshold in config.ledger_warn_thresholds:
                if budget_fraction >= threshold and threshold not in warned_budget_thresholds:
                    warned_budget_thresholds.add(threshold)
                    logger.warning(
                        "batch budget threshold crossed: threshold=%.2f cumulative=%.4f budget=%.4f",
                        threshold,
                        cumulative_usd,
                        config.budget_usd,
                    )
            unfinished = counts["pending"] + counts["leased"]
            effective_active_count = len(active) + len(pending_instance_ids)
            replacement_count = max(
                0,
                min(config.target_workers, unfinished) - effective_active_count,
            )
            if replacement_count and counts["pending"]:
                replacements = _provision_batch_workers(
                    config,
                    provider=provider,
                    bundle_sha256=bundle_sha256,
                    bearer_token=bearer_token,
                    target_workers=replacement_count,
                )
                instance_ids.extend(replacements)
                pending_instance_ids.update(
                    {instance_id: now for instance_id in replacements}
                )
                active = provider.list_active(config.project_tag)
                active_worker_ids = {
                    str(item.get("instance_id") or item.get("id"))
                    for item in active
                    if item.get("instance_id") or item.get("id")
                }
                for worker_id in active_worker_ids:
                    pending_instance_ids.pop(worker_id, None)
            write_status_snapshot(
                config,
                state,
                phase="running",
                instance_ids=instance_ids,
                active_instances=active,
                cumulative_usd=cumulative_usd,
                timestamp=timestamp_fn(),
            )
            if counts["failed"]:
                raise BatchLaunchFailed(f"{counts['failed']} batch job(s) failed")
            if counts["completed"] == len(jobs):
                merged = merge_job_artifacts(config)
                write_status_snapshot(
                    config,
                    state,
                    phase="merged",
                    instance_ids=instance_ids,
                    active_instances=active,
                    cumulative_usd=cumulative_usd,
                    timestamp=timestamp_fn(),
                )
                terminal_message = "merged"
                return merged
            if not active and not pending_instance_ids and counts["completed"] < len(jobs):
                raise BatchLaunchFailed(
                    "no active workers remain before all jobs completed"
                )
            sleep_fn(poll_interval)
    except BaseException as exc:
        primary_exception = exc
        terminal_message = str(exc)
        raise
    finally:
        teardown_errors: list[str] = []
        write_status_snapshot(
            config,
            state,
            phase="teardown",
            instance_ids=instance_ids,
            cumulative_usd=cumulative_usd,
            message=terminal_message,
            timestamp=timestamp_fn(),
        )
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                teardown_errors.append(f"server shutdown failed: {exc}")
        try:
            provider.terminate_fleet(
                fleet_name=config.fleet_name,
                project_tag=config.project_tag,
            )
        except Exception as exc:
            teardown_errors.append(f"terminate_fleet failed: {exc}")
        try:
            provider.terminate_all_tagged(config.project_tag)
        except Exception as exc:
            teardown_errors.append(f"terminate_all_tagged failed: {exc}")
        if final_audit_fn is not None:
            try:
                final_audit_fn(config)
            except Exception as exc:
                teardown_errors.append(f"final audit failed: {exc}")
        final_message = terminal_message
        if teardown_errors:
            joined = "; ".join(teardown_errors)
            final_message = joined if final_message is None else f"{final_message}; {joined}"
        write_status_snapshot(
            config,
            state,
            phase="teardown_complete",
            instance_ids=instance_ids,
            cumulative_usd=cumulative_usd,
            message=final_message,
            timestamp=timestamp_fn(),
        )
        if teardown_errors and primary_exception is None:
            raise BatchLaunchFailed("; ".join(teardown_errors)) from primary_exception
        if teardown_errors:
            logger.error("batch teardown completed with errors: %s", "; ".join(teardown_errors))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _leakage_ok(result: Mapping[str, Any]) -> bool:
    required = {
        "outer_test_targets_excluded_from_fit",
        "honest_eval_targets_excluded_from_fit",
        "feature_selection_inside_inner_fold",
        "build_key_excluded_from_feature_vectors",
    }
    checklist = result.get("leakage_checklist")
    return (
        isinstance(checklist, dict)
        and required.issubset(checklist.keys())
        and all(bool(checklist[key]) for key in required)
    )


def _leakage_diagnostics_pass(result: Mapping[str, Any]) -> bool:
    leakage = result.get("leakage_diagnostics")
    if not isinstance(leakage, dict):
        return False
    required = (
        "forbidden_key_overlap",
        "adversarial_validation_auc",
        "rare_combination_overlap",
        "nearest_neighbor_overlap",
        "sparse_id_ablation_delta",
    )
    for key in required:
        diagnostic = leakage.get(key)
        if not isinstance(diagnostic, dict):
            return False
        status = diagnostic.get("status")
        if status not in ("pass", "not_applicable"):
            return False
    return True


def _contract_ok(result: Mapping[str, Any]) -> bool:
    claim = result.get("claim_boundary")
    model_policy = result.get("model_family_policy")
    feature_protocol = result.get("feature_selection_protocol")
    deployment = result.get("deployment_policy")
    hierarchy = result.get("hierarchy_scorecard")
    leakage = result.get("leakage_diagnostics")
    registry = feature_protocol.get("feature_family_registry") if isinstance(feature_protocol, dict) else None
    registry_digest = feature_protocol.get("feature_family_registry_sha256") if isinstance(feature_protocol, dict) else None
    return (
        isinstance(claim, dict)
        and claim.get("target_variable") == "training_matchups.target"
        and claim.get("honest_eval_usage") in ("diagnostic_only", "exploratory_selection", "final_claim")
        and isinstance(model_policy, dict)
        and model_policy.get("policy_type") == "fixed_matrix"
        and isinstance(feature_protocol, dict)
        and feature_protocol.get("policy_type") == "fixed_profile_no_selector"
        and isinstance(registry, dict)
        and isinstance(registry_digest, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", registry_digest) is not None
        and isinstance(hierarchy, dict)
        and isinstance(hierarchy.get("split_level"), str)
        and isinstance(hierarchy.get("group_key_fields"), list)
        and isinstance(hierarchy.get("forbidden_cross_split_keys"), list)
        and isinstance(hierarchy.get("overlap_counts"), dict)
        and isinstance(hierarchy.get("component_overlap_diagnostics"), dict)
        and isinstance(deployment, dict)
        and isinstance(leakage, dict)
        and all(
            isinstance(leakage.get(key), dict) and "status" in leakage[key]
            for key in (
                "forbidden_key_overlap",
                "adversarial_validation_auc",
                "rare_combination_overlap",
                "nearest_neighbor_overlap",
                "sparse_id_ablation_delta",
            )
        )
    )


def _require_64_hex(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError(f"{field} must be a 64-character SHA-256 hex digest")
    return value


def validate_job_payload(
    config: LearnedBatchConfig,
    job: LearnedBatchJob,
    payload: Mapping[str, Any],
    *,
    bundle_sha256: str | None = None,
) -> dict[str, Any]:
    artifact = dict(payload)
    if not isinstance(artifact.get("experiment_schema_version"), int):
        raise ValueError(f"job {job.job_id} experiment schema version missing")
    if not isinstance(artifact.get("feature_schema_version"), int):
        raise ValueError(f"job {job.job_id} feature schema version missing")
    if artifact["feature_schema_version"] != FEATURE_SCHEMA_VERSION:
        raise ValueError(f"job {job.job_id} feature schema version mismatch")
    batch_job = artifact.get("batch_job")
    if not isinstance(batch_job, dict):
        raise ValueError(f"job {job.job_id} batch job identity missing")
    expected_batch_job = {
        "job_id": job.job_id,
        "batch_name": config.name,
        "fleet_name": config.fleet_name,
        "split": job.split,
        "model": job.model,
    }
    for key, expected in expected_batch_job.items():
        if batch_job.get(key) != expected:
            raise ValueError(f"job {job.job_id} batch job field {key!r} mismatch")
    if artifact.get("status") not in (None, "completed"):
        raise ValueError(f"job {job.job_id} artifact is not complete")
    if artifact.get("result_count") != 1:
        raise ValueError(f"job {job.job_id} must contain exactly one result")
    if artifact.get("skipped_models") not in ([], None):
        raise ValueError(f"job {job.job_id} skipped a required model")
    if artifact.get("model_families") != [job.model]:
        raise ValueError(f"job {job.job_id} model_families mismatch")
    if artifact.get("db_path") != str(config.source_db_path):
        raise ValueError(f"job {job.job_id} source DB mismatch")
    if artifact.get("feature_profile") != config.feature_profile:
        raise ValueError(f"job {job.job_id} feature profile mismatch")
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"job {job.job_id} provenance missing")
    expected_provenance = {
        "game_dir": str(config.game_dir),
        "comparator_json_path": str(config.comparator_json_path),
        "split_seed": config.split_seed,
        "hpo_seed": config.hpo_seed,
        "hpo_trials": config.hpo_trials,
        "hpo_jobs": config.hpo_jobs,
        "model_thread_count": config.model_thread_count,
        "holdout_fraction": config.holdout_fraction,
        "train_fraction": config.train_fraction,
        "top_k_values": list(config.top_k_values),
        "feature_profile": config.feature_profile,
        "honest_eval_usage": config.honest_eval_usage,
        "fresh_honest_eval_ledger_id": config.fresh_honest_eval_ledger_id,
        "primary_top_k": config.primary_top_k,
        "promotion_metric": config.promotion_metric,
        "promotion_threshold": config.promotion_threshold,
        "claim_label": config.claim_label,
        "final_refit_policy": config.final_refit_policy,
        "candidate_universe": config.candidate_universe,
        "deployment_artifact": config.deployment_artifact,
        "batch_job_id": job.job_id,
        "batch_name": config.name,
        "batch_fleet_name": config.fleet_name,
    }
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            raise ValueError(f"job {job.job_id} provenance field {key!r} mismatch")
    if "dependency_extra" not in provenance:
        raise ValueError(f"job {job.job_id} dependency extra missing")
    if provenance["dependency_extra"] != config.dependency_extra:
        raise ValueError(f"job {job.job_id} dependency extra mismatch")
    if bundle_sha256 is not None:
        recorded_bundle = provenance.get("bundle_sha256")
        if recorded_bundle is None:
            provenance["bundle_sha256"] = bundle_sha256
        elif recorded_bundle != bundle_sha256:
            raise ValueError(f"job {job.job_id} bundle_sha256 mismatch")
    _require_64_hex(provenance.get("bundle_sha256"), field="bundle_sha256")
    result_rows = artifact.get("results")
    if not isinstance(result_rows, list) or len(result_rows) != 1:
        raise ValueError(f"job {job.job_id} result payload malformed")
    result = result_rows[0]
    required_result_fields = (
        "status",
        "split",
        "model",
        "mae",
        "rmse",
        "spearman_rho",
        "hpo",
        "timing",
        "comparator_context",
        "leakage_checklist",
        "claim_boundary",
        "model_family_policy",
        "feature_selection_protocol",
        "deployment_policy",
        "hierarchy_scorecard",
        "leakage_diagnostics",
    )
    missing = [field for field in required_result_fields if field not in result]
    if missing:
        raise ValueError(f"job {job.job_id} result missing fields: {', '.join(missing)}")
    if result.get("status") != "completed":
        raise ValueError(f"job {job.job_id} result is not complete")
    code_version = provenance.get("code_version")
    if not isinstance(code_version, str) or code_version == "unknown" or code_version.endswith("+dirty"):
        raise ValueError(f"job {job.job_id} code version is not clean")
    for field in ("mae", "rmse", "spearman_rho"):
        value = result.get(field)
        if not isinstance(value, int | float) or not math.isfinite(float(value)):
            raise ValueError(f"job {job.job_id} metric {field!r} is not finite")
    for field in ("n_train", "n_inner_train", "n_inner_validation", "n_test"):
        value = result.get(field)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"job {job.job_id} count {field!r} malformed")
    if result.get("target_variable") != "training_matchups.target":
        raise ValueError(f"job {job.job_id} target variable mismatch")
    if not isinstance(result.get("feature_families"), dict):
        raise ValueError(f"job {job.job_id} feature families malformed")
    if result["feature_families"].get("feature_profile") != config.feature_profile:
        raise ValueError(f"job {job.job_id} feature family profile mismatch")
    if not isinstance(result.get("hpo"), dict) or "selected_hyperparameters" not in result["hpo"]:
        raise ValueError(f"job {job.job_id} HPO payload malformed")
    if not isinstance(result.get("timing"), dict):
        raise ValueError(f"job {job.job_id} timing payload malformed")
    if not isinstance(result.get("honest_eval_top_k"), dict):
        raise ValueError(f"job {job.job_id} honest-eval top-k payload malformed")
    comparator_context = result.get("comparator_context")
    if not isinstance(comparator_context, dict):
        raise ValueError(f"job {job.job_id} comparator context malformed")
    if (
        comparator_context.get("diagnostic") != "ok"
        or comparator_context.get("comparison_status") != "comparable"
    ):
        raise ValueError(f"job {job.job_id} comparator context is not comparable")
    if result.get("split") != job.split or result.get("model") != job.model:
        raise ValueError(f"job {job.job_id} result does not match job identity")
    if not _leakage_ok(result):
        raise ValueError(f"job {job.job_id} leakage checklist failed or missing")
    if not _leakage_diagnostics_pass(result):
        raise ValueError(f"job {job.job_id} leakage diagnostics failed or missing")
    if not _contract_ok(result):
        raise ValueError(f"job {job.job_id} artifact contract fields failed or missing")
    claim = result["claim_boundary"]
    if claim.get("primary_split") != job.split:
        raise ValueError(f"job {job.job_id} claim boundary split mismatch")
    expected_claim = {
        "target_variable": "training_matchups.target",
        "honest_eval_diagnostic_target": "honest_eval_top_k",
        "primary_top_k": config.primary_top_k,
        "promotion_metric": config.promotion_metric,
        "promotion_threshold": config.promotion_threshold,
        "claim_label": config.claim_label,
        "honest_eval_usage": config.honest_eval_usage,
        "fresh_honest_eval_ledger_id": config.fresh_honest_eval_ledger_id,
    }
    for key, expected in expected_claim.items():
        if claim.get(key) != expected:
            raise ValueError(f"job {job.job_id} claim boundary field {key!r} mismatch")
    policy = result["model_family_policy"]
    if policy.get("selected_model_family") != job.model:
        raise ValueError(f"job {job.job_id} model family policy mismatch")
    feature_protocol = result["feature_selection_protocol"]
    if feature_protocol.get("feature_profile") != config.feature_profile:
        raise ValueError(f"job {job.job_id} feature selection profile mismatch")
    registry = feature_protocol.get("feature_family_registry")
    expected_digest = hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if feature_protocol.get("feature_family_registry_sha256") != expected_digest:
        raise ValueError(f"job {job.job_id} feature family registry digest mismatch")
    deployment = result["deployment_policy"]
    expected_deployment = {
        "final_refit_policy": config.final_refit_policy,
        "candidate_universe": config.candidate_universe,
        "deployment_artifact": config.deployment_artifact,
    }
    for key, expected in expected_deployment.items():
        if deployment.get(key) != expected:
            raise ValueError(f"job {job.job_id} deployment policy field {key!r} mismatch")
    if claim.get("honest_eval_usage") == "final_claim" and not claim.get("fresh_honest_eval_ledger_id"):
        raise ValueError(f"job {job.job_id} final claim missing fresh honest-eval ledger")
    artifact["provenance"] = provenance
    return artifact


def _common_key(payload: Mapping[str, Any], config: LearnedBatchConfig) -> tuple[Any, ...]:
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("payload provenance missing")
    return (
        payload.get("experiment_schema_version"),
        payload.get("feature_schema_version"),
        payload.get("db_path"),
        provenance.get("game_dir"),
        provenance.get("comparator_json_path"),
        provenance.get("split_seed"),
        provenance.get("hpo_seed"),
        provenance.get("hpo_trials"),
        provenance.get("hpo_jobs"),
        provenance.get("model_thread_count"),
        provenance.get("holdout_fraction"),
        provenance.get("train_fraction"),
        tuple(provenance.get("top_k_values", ())),
        provenance.get("feature_profile"),
        provenance.get("honest_eval_usage"),
        provenance.get("fresh_honest_eval_ledger_id"),
        provenance.get("primary_top_k"),
        provenance.get("promotion_metric"),
        provenance.get("promotion_threshold"),
        provenance.get("claim_label"),
        provenance.get("final_refit_policy"),
        provenance.get("candidate_universe"),
        provenance.get("deployment_artifact"),
        provenance.get("code_version"),
        provenance.get("dependency_extra", config.dependency_extra),
        provenance.get("bundle_sha256"),
    )


def merge_job_artifacts(config: LearnedBatchConfig) -> dict[str, Any]:
    jobs = generate_jobs(config)
    result_dir = config.output_dir / "results"
    missing = [job.job_id for job in jobs if not (result_dir / f"{job.job_id}.json").exists()]
    if missing:
        raise ValueError(f"missing batch job artifacts: {', '.join(missing[:5])}")

    payloads: list[dict[str, Any]] = []
    common: tuple[Any, ...] | None = None
    results: list[dict[str, Any]] = []
    for job in jobs:
        payload = _read_json(result_dir / f"{job.job_id}.json")
        payload = validate_job_payload(config, job, payload)
        result = payload["results"][0]
        key = _common_key(payload, config)
        if common is None:
            common = key
        elif key != common:
            raise ValueError(f"job {job.job_id} provenance does not match batch")
        payloads.append(payload)
        results.append(dict(result))

    first = payloads[0]
    first_result = first["results"][0]
    provenance = dict(first["provenance"])
    provenance["batch"] = {
        "name": config.name,
        "project_tag": config.project_tag,
        "fleet_name": config.fleet_name,
        "job_count": len(jobs),
        "source_artifact_dir": str(result_dir),
    }
    merged = {
        "experiment_schema_version": first["experiment_schema_version"],
        "feature_schema_version": first["feature_schema_version"],
        "feature_profile": first["feature_profile"],
        "db_path": first["db_path"],
        "model_families": list(config.models),
        "claim_boundary": first.get("claim_boundary") or first_result.get("claim_boundary"),
        "model_family_policy": first.get("model_family_policy") or first_result.get("model_family_policy"),
        "feature_selection_protocol": first.get("feature_selection_protocol") or first_result.get("feature_selection_protocol"),
        "deployment_policy": first.get("deployment_policy") or first_result.get("deployment_policy"),
        "hierarchy_scorecard": first.get("hierarchy_scorecard") or first_result.get("hierarchy_scorecard"),
        "leakage_diagnostics": first.get("leakage_diagnostics") or first_result.get("leakage_diagnostics"),
        "provenance": provenance,
        "result_count": len(results),
        "skipped_models": [],
        "results": results,
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    merged_path = config.output_dir / "merged.json"
    _atomic_write_json(merged_path, merged)
    if config.publish_canonical:
        canonical_matrix = (
            config.splits == CANONICAL_SPLITS and config.models == CANONICAL_MODELS
        )
        if not canonical_matrix or len(jobs) != len(CANONICAL_SPLITS) * len(CANONICAL_MODELS):
            raise ValueError(
                "canonical publication requires the full canonical split/model matrix"
            )
        _atomic_write_json(config.canonical_output_path, merged)
    return merged


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
        tmp_name = fh.name
    Path(tmp_name).replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(payload), sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
