"""Phase 7 learned-surrogate AWS batch helpers."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, Sequence

import yaml
from flask import Flask, jsonify, request, send_file


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
    ledger_heartbeat_interval_seconds: float
    ledger_warn_thresholds: tuple[float, ...]
    tailscale_authkey_secret: str
    control_plane_host: str
    control_plane_port: int
    output_dir: Path
    canonical_output_path: Path
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
    dependency_extra: str = DEFAULT_DEPENDENCY_EXTRA


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
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] | None = None


class BatchState:
    """In-memory lease/result state for a single batch control plane."""

    def __init__(
        self,
        jobs: Sequence[LearnedBatchJob],
        *,
        lease_ttl_seconds: float,
        max_attempts: int,
    ) -> None:
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._lease_ttl_seconds = lease_ttl_seconds
        self._max_attempts = max_attempts
        self._states = {job.job_id: _JobState(job=job, events=[]) for job in jobs}
        self._lock = Lock()

    def lease(self, *, now: float | None = None, worker_id: str) -> JobLease | None:
        now = time.time() if now is None else now
        with self._lock:
            for state in self._states.values():
                if state.status == "completed":
                    continue
                expired = (
                    state.status == "leased"
                    and state.lease_expires_at is not None
                    and state.lease_expires_at <= now
                )
                if state.status != "pending" and not expired:
                    continue
                if state.attempt >= self._max_attempts:
                    state.status = "failed"
                    continue
                state.attempt += 1
                state.status = "leased"
                state.worker_id = worker_id
                state.lease_expires_at = now + self._lease_ttl_seconds
                return JobLease(
                    job_id=state.job.job_id,
                    split=state.job.split,
                    model=state.job.model,
                    attempt=state.attempt,
                    worker_id=worker_id,
                    lease_expires_at=state.lease_expires_at,
                )
        return None

    def record_result(
        self,
        job_id: str,
        payload: Mapping[str, Any],
        *,
        worker_id: str,
        attempt: int,
        now: float | None = None,
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
        ledger_heartbeat_interval_seconds=float(expanded["ledger_heartbeat_interval_seconds"]),
        ledger_warn_thresholds=tuple(float(v) for v in expanded["ledger_warn_thresholds"]),
        tailscale_authkey_secret=str(expanded["tailscale_authkey_secret"]),
        control_plane_host=str(expanded["control_plane_host"]),
        control_plane_port=int(expanded["control_plane_port"]),
        output_dir=Path(expanded["output_dir"]),
        canonical_output_path=Path(expanded["canonical_output_path"]),
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
        dependency_extra=str(expanded.get("dependency_extra", DEFAULT_DEPENDENCY_EXTRA)),
    )
    validate_batch_config(cfg)
    return cfg


def validate_batch_config(config: LearnedBatchConfig) -> None:
    if not config.name or not config.project_tag.startswith("starsector-"):
        raise ValueError("name and project_tag are required")
    if not config.regions:
        raise ValueError("at least one region is required")
    missing_regions = [region for region in config.regions if region not in config.ami_ids_by_region]
    if missing_regions:
        raise ValueError(f"AMI IDs missing for regions: {', '.join(missing_regions)}")
    if config.target_workers != len(CANONICAL_SPLITS) * len(CANONICAL_MODELS):
        raise ValueError("target_workers must equal the 15-job canonical matrix")
    if config.target_workers % len(config.regions) != 0:
        raise ValueError(
            "target_workers must be divisible by region count because AWSProvider "
            "currently floors per-region targets"
        )
    if not (1 <= config.min_workers_to_start <= config.target_workers):
        raise ValueError("min_workers_to_start must be between 1 and target_workers")
    if config.budget_usd <= 0 or config.max_lifetime_hours <= 0:
        raise ValueError("budget_usd and max_lifetime_hours must be positive")
    if config.ledger_heartbeat_interval_seconds <= 0:
        raise ValueError("ledger_heartbeat_interval_seconds must be positive")
    if config.dependency_extra != DEFAULT_DEPENDENCY_EXTRA:
        raise ValueError("dependency_extra must use the existing surrogate extra")
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


def generate_jobs(config: LearnedBatchConfig) -> tuple[LearnedBatchJob, ...]:
    result_dir = config.output_dir / "results"
    return tuple(
        LearnedBatchJob(
            job_id=f"{split}__{model}",
            split=split,
            model=model,
            output_path=result_dir / f"{split}__{model}.json",
        )
        for split in CANONICAL_SPLITS
        for model in CANONICAL_MODELS
    )


def build_job_command(config: LearnedBatchConfig, job: LearnedBatchJob) -> list[str]:
    return [
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
        "--output",
        str(job.output_path),
    ]


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
            )
            if status["status"] == "accepted":
                _atomic_write_json(job.output_path, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(status), 200 if status["status"] == "accepted" else 409

    @app.post("/event/<job_id>")
    def event(job_id: str) -> tuple[Any, int]:
        payload = request.get_json(silent=True) or {}
        try:
            state.record_event(job_id, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"status": "accepted"}), 200

    @app.get("/status")
    def status() -> Any:
        return jsonify(state.status())

    return app


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
    top_k = shlex.quote(",".join(str(value) for value in config.top_k_values))
    source_db = shlex.quote(str(config.source_db_path))
    game_dir = shlex.quote(str(config.game_dir))
    comparator_json = shlex.quote(str(config.comparator_json_path))
    dependency_extra = shlex.quote(config.dependency_extra)
    control_url = shlex.quote(control_plane_url)
    return f"""#!/usr/bin/env bash
set -euo pipefail
umask 077

TS_AUTHKEY_FILE=$(mktemp)
cleanup_secret() {{
  shred -u "$TS_AUTHKEY_FILE" || rm -f "$TS_AUTHKEY_FILE"
}}
trap cleanup_secret EXIT
printf '%s' {json.dumps(config.tailscale_authkey_secret)} > "$TS_AUTHKEY_FILE"
tailscale up --auth-key=file:"$TS_AUTHKEY_FILE" --advertise-tags=tag:starsector-worker --accept-dns=false
cleanup_secret
trap - EXIT

systemctl disable --now starsector-worker.service || true

IMDS_TOKEN=$(curl --silent --fail -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
INSTANCE_TYPE=$(curl --silent --fail -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/instance-type)

mkdir -p /opt/phase7-batch
cd /opt/phase7-batch
curl --silent --fail -H "Authorization: Bearer {bearer_token}" -o bundle.tgz {control_url}/bundle
printf '%s  bundle.tgz\\n' {json.dumps(bundle_sha256)} | sha256sum --check
tar -xzf bundle.tgz
uv sync --frozen --extra {dependency_extra}

LEASE=$(curl --silent --fail -X POST \\
  -H "Authorization: Bearer {bearer_token}" \\
  -H "Content-Type: application/json" \\
  -d "{{\\"worker_id\\":\\"$INSTANCE_ID\\",\\"region\\":\\"$REGION\\",\\"instance_type\\":\\"$INSTANCE_TYPE\\"}}" \\
  {control_url}/lease)
JOB_ID=$(python -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<< "$LEASE")
SPLIT=$(python -c 'import json,sys; print(json.load(sys.stdin)["split"])' <<< "$LEASE")
MODEL=$(python -c 'import json,sys; print(json.load(sys.stdin)["model"])' <<< "$LEASE")
OUTPUT="data/phase7/aws-job-$JOB_ID.json"

timeout {max_seconds} uv run python scripts/analysis/phase7_learned_surrogate_experiment.py \\
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
  --output "$OUTPUT"

curl --silent --fail -X POST \\
  -H "Authorization: Bearer {bearer_token}" \\
  -H "X-Worker-Id: $INSTANCE_ID" \\
  -H "X-Lease-Attempt: $(python -c 'import json,sys; print(json.load(sys.stdin)[\"attempt\"])' <<< "$LEASE")" \\
  -H "Content-Type: application/json" \\
  --data-binary "@$OUTPUT" \\
  {control_url}/result/"$JOB_ID"
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
            "worker_id": inst.get("instance_id", "unknown"),
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _leakage_ok(result: Mapping[str, Any]) -> bool:
    checklist = result.get("leakage_checklist")
    return isinstance(checklist, dict) and all(bool(value) for value in checklist.values())


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
    }
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            raise ValueError(f"job {job.job_id} provenance field {key!r} mismatch")
    provenance.setdefault("dependency_extra", config.dependency_extra)
    if provenance["dependency_extra"] != config.dependency_extra:
        raise ValueError(f"job {job.job_id} dependency extra mismatch")
    if bundle_sha256 is not None:
        provenance["bundle_sha256"] = bundle_sha256
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
    )
    missing = [field for field in required_result_fields if field not in result]
    if missing:
        raise ValueError(f"job {job.job_id} result missing fields: {', '.join(missing)}")
    if result.get("status") != "completed":
        raise ValueError(f"job {job.job_id} result is not complete")
    if result.get("split") != job.split or result.get("model") != job.model:
        raise ValueError(f"job {job.job_id} result does not match job identity")
    if not _leakage_ok(result):
        raise ValueError(f"job {job.job_id} leakage checklist failed or missing")
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
        "db_path": first["db_path"],
        "model_families": list(CANONICAL_MODELS),
        "provenance": provenance,
        "result_count": len(results),
        "skipped_models": [],
        "results": results,
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    merged_path = config.output_dir / "merged.json"
    _atomic_write_json(merged_path, merged)
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
