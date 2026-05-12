import json
import importlib.util
from pathlib import Path

import pytest

from starsector_optimizer.phase7_learned_batch import (
    CANONICAL_MODELS,
    CANONICAL_SPLITS,
    BatchState,
    BudgetExceeded,
    LearnedBatchConfig,
    build_job_command,
    create_control_plane_app,
    generate_jobs,
    load_batch_config,
    merge_job_artifacts,
    record_budget_heartbeat,
    render_phase7_learned_batch_user_data,
    validate_job_payload,
    validate_batch_config,
)


def load_batch_cli_module():
    path = Path("scripts/cloud/phase7_learned_batch.py")
    spec = importlib.util.spec_from_file_location("phase7_learned_batch_cli", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_config(tmp_path: Path) -> LearnedBatchConfig:
    return LearnedBatchConfig(
        name="phase7-test",
        project_tag="starsector-phase7-test",
        fleet_name="learned-surrogate",
        regions=("us-east-2",),
        ami_ids_by_region={
            "us-east-2": "ami-22222222222222222",
            "us-east-1": "ami-11111111111111111",
        },
        instance_types=("c7i.4xlarge", "c7a.4xlarge"),
        ssh_key_name="starsector-worker",
        spot_allocation_strategy="price-capacity-optimized",
        target_workers=15,
        min_workers_to_start=8,
        budget_usd=20.0,
        max_lifetime_hours=2.0,
        ledger_heartbeat_interval_seconds=60.0,
        ledger_warn_thresholds=(0.5, 0.8, 0.95),
        tailscale_authkey_secret="tskey-auth-test",
        control_plane_host="100.64.0.1",
        control_plane_port=9131,
        output_dir=tmp_path / "batch",
        canonical_output_path=tmp_path / "full.json",
        source_db_path=Path("data/phase7/wave1_matchups.sqlite"),
        game_dir=Path("game/starsector"),
        comparator_json_path=Path("data/phase7/wave1_comparator_gate_2026-05-11.json"),
        hpo_trials=24,
        hpo_jobs=4,
        model_thread_count=4,
        top_k_values=(1, 3, 5),
        split_seed=17,
        hpo_seed=23,
        holdout_fraction=0.2,
        train_fraction=0.8,
        dependency_extra="surrogate",
    )


def test_load_batch_config_expands_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TAILSCALE_AUTHKEY", "tskey-auth-from-env")
    monkeypatch.setenv("STARSECTOR_WORKSTATION_TAILNET_IP", "100.64.0.9")
    path = tmp_path / "config.yaml"
    path.write_text(
        """
name: phase7-test
project_tag: starsector-phase7-test
fleet_name: learned-surrogate
regions: [us-east-2]
ami_ids_by_region:
  us-east-2: ami-22222222222222222
  us-east-1: ami-11111111111111111
instance_types: [c7i.4xlarge, c7a.4xlarge]
ssh_key_name: starsector-worker
spot_allocation_strategy: price-capacity-optimized
target_workers: 15
min_workers_to_start: 8
budget_usd: 20.0
max_lifetime_hours: 2.0
ledger_heartbeat_interval_seconds: 60.0
ledger_warn_thresholds: [0.5, 0.8, 0.95]
tailscale_authkey_secret: ${TAILSCALE_AUTHKEY}
control_plane_host: ${STARSECTOR_WORKSTATION_TAILNET_IP}
control_plane_port: 9131
output_dir: batch
canonical_output_path: full.json
source_db_path: data/phase7/wave1_matchups.sqlite
game_dir: game/starsector
comparator_json_path: data/phase7/wave1_comparator_gate_2026-05-11.json
hpo_trials: 24
hpo_jobs: 4
model_thread_count: 4
top_k: [1, 3, 5]
split_seed: 17
hpo_seed: 23
holdout_fraction: 0.2
train_fraction: 0.8
dependency_extra: surrogate
""",
        encoding="utf-8",
    )

    cfg = load_batch_config(path)

    assert cfg.tailscale_authkey_secret == "tskey-auth-from-env"
    assert cfg.control_plane_host == "100.64.0.9"
    validate_batch_config(cfg)


def test_generate_jobs_has_canonical_matrix(tmp_path):
    jobs = generate_jobs(make_config(tmp_path))

    assert len(jobs) == len(CANONICAL_SPLITS) * len(CANONICAL_MODELS)
    assert {job.job_id for job in jobs} == {
        f"{split}__{model}" for split in CANONICAL_SPLITS for model in CANONICAL_MODELS
    }


def test_build_job_command_is_single_split_single_model_and_no_unsafe_flags(tmp_path):
    cfg = make_config(tmp_path)
    job = generate_jobs(cfg)[0]
    command = build_job_command(cfg, job)

    assert "--split all" not in " ".join(command)
    assert "--model all" not in " ".join(command)
    assert "--split" in command
    assert command[command.index("--split") + 1] == job.split
    assert "--model" in command
    assert command[command.index("--model") + 1] == job.model
    assert "--output" in command
    assert "--allow-missing-optional-models" not in command
    assert "honest" not in " ".join(command).lower()


def test_bundle_paths_include_runtime_inputs(tmp_path):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()

    paths = set(cli.bundle_paths(cfg))

    assert cfg.source_db_path in paths
    assert cfg.comparator_json_path in paths
    assert Path("game/starsector/manifest.json") in paths


def test_config_validation_rejects_oversubscribed_or_ambiguous_fallback(tmp_path):
    cfg = make_config(tmp_path)
    bad = cfg.__class__(**{**cfg.__dict__, "instance_types": ("c7i.2xlarge",)})

    with pytest.raises(ValueError, match="2xlarge"):
        validate_batch_config(bad)


def test_config_validation_rejects_region_count_that_loses_workers(tmp_path):
    cfg = make_config(tmp_path)
    bad = cfg.__class__(
        **{
            **cfg.__dict__,
            "regions": ("us-east-2", "us-east-1"),
        }
    )

    with pytest.raises(ValueError, match="divisible"):
        validate_batch_config(bad)


def test_control_plane_requires_bearer_token_for_all_routes(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_ttl_seconds=60.0, max_attempts=2)
    app = create_control_plane_app(state, bundle_path=bundle, bearer_token="secret")
    client = app.test_client()

    assert client.get("/status").status_code == 401
    assert client.get("/bundle", headers={"Authorization": "Bearer wrong"}).status_code == 401
    leased = client.post(
        "/lease",
        headers={"Authorization": "Bearer secret"},
        json={"worker_id": "i-1"},
    )
    assert leased.status_code == 200
    lease_payload = leased.get_json()
    assert client.post(
        f"/result/{lease_payload['job_id']}",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-1",
            "X-Lease-Attempt": str(lease_payload["attempt"]),
        },
        json={
            "result_count": 1,
            "results": [{"split": lease_payload["split"], "model": lease_payload["model"]}],
        },
    ).status_code == 200


def test_lease_duplicate_result_and_wrong_job_handling(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_ttl_seconds=60.0, max_attempts=2)
    lease = state.lease(now=100.0, worker_id="i-1")
    assert lease is not None
    payload = {"result_count": 1, "results": [{"split": lease.split, "model": lease.model}]}

    assert state.record_result(
        lease.job_id,
        payload,
        worker_id="i-1",
        attempt=1,
        now=110.0,
    )["status"] == "accepted"
    assert state.record_result(
        lease.job_id,
        payload,
        worker_id="i-1",
        attempt=1,
        now=111.0,
    )["status"] == "duplicate"

    other = next(job for job in generate_jobs(cfg) if job.job_id != lease.job_id)
    with pytest.raises(ValueError, match="not leased"):
        state.record_result(other.job_id, payload, worker_id="i-1", attempt=1, now=112.0)


def test_result_rejects_wrong_worker_and_expired_lease(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_ttl_seconds=5.0, max_attempts=2)
    lease = state.lease(now=100.0, worker_id="i-1")
    assert lease is not None
    payload = {"result_count": 1, "results": [{"split": lease.split, "model": lease.model}]}

    with pytest.raises(ValueError, match="different worker"):
        state.record_result(lease.job_id, payload, worker_id="i-2", attempt=1, now=101.0)
    with pytest.raises(ValueError, match="lease expired"):
        state.record_result(lease.job_id, payload, worker_id="i-1", attempt=1, now=106.0)


def test_user_data_preserves_security_invariants(tmp_path):
    cfg = make_config(tmp_path)
    out = render_phase7_learned_batch_user_data(
        cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    assert "set -euo pipefail" in out
    assert "umask 077" in out
    assert "--auth-key=file:" in out
    assert "shred -u" in out
    assert "--ssh" not in out
    assert "starsector-worker.service" in out
    assert "latest/api/token" in out
    assert "timeout 7200" in out
    assert "uv sync --frozen --extra surrogate" in out


def test_budget_heartbeat_writes_ledger_and_raises_at_cap(tmp_path):
    cfg = make_config(tmp_path)
    ledger = tmp_path / "ledger.jsonl"

    record_budget_heartbeat(
        cfg,
        ledger_path=ledger,
        running_instances=[
            {"instance_id": "i-1", "region": "us-east-2", "instance_type": "c7i.4xlarge"}
        ],
        spot_prices={("us-east-2", "c7i.4xlarge"): 0.20},
        interval_seconds=60.0,
        cumulative_usd=19.99,
        timestamp="2026-05-12T00:00:00Z",
    )
    assert ledger.read_text(encoding="utf-8").strip()

    with pytest.raises(BudgetExceeded):
        record_budget_heartbeat(
            cfg,
            ledger_path=ledger,
            running_instances=[
                {"instance_id": "i-1", "region": "us-east-2", "instance_type": "c7i.4xlarge"}
            ],
            spot_prices={("us-east-2", "c7i.4xlarge"): 1.00},
            interval_seconds=120.0,
            cumulative_usd=19.99,
            timestamp="2026-05-12T00:02:00Z",
        )


def one_job_payload(cfg: LearnedBatchConfig, split: str, model: str) -> dict:
    return {
        "experiment_schema_version": 1,
        "feature_schema_version": 2,
        "db_path": str(cfg.source_db_path),
        "model_families": [model],
        "provenance": {
            "game_dir": str(cfg.game_dir),
            "comparator_json_path": str(cfg.comparator_json_path),
            "split_seed": cfg.split_seed,
            "hpo_seed": cfg.hpo_seed,
            "hpo_trials": cfg.hpo_trials,
            "hpo_jobs": cfg.hpo_jobs,
            "model_thread_count": cfg.model_thread_count,
            "holdout_fraction": cfg.holdout_fraction,
            "train_fraction": cfg.train_fraction,
            "top_k_values": list(cfg.top_k_values),
            "max_rows": None,
            "code_version": "abc123",
            "dependency_extra": cfg.dependency_extra,
            "bundle_sha256": "b" * 64,
        },
        "result_count": 1,
        "skipped_models": [],
        "results": [
            {
                "status": "completed",
                "split": split,
                "model": model,
                "mae": 0.1,
                "rmse": 0.2,
                "spearman_rho": 0.3,
                "hpo": {"selected_params": {}},
                "timing": {"fit_seconds": 0.1},
                "leakage_checklist": {
                    "outer_test_targets_excluded_from_fit": True,
                    "honest_eval_targets_excluded_from_fit": True,
                },
                "comparator_context": {"diagnostic": "ok"},
            }
        ],
    }


def test_validate_job_payload_rejects_running_or_missing_bundle(tmp_path):
    cfg = make_config(tmp_path)
    job = generate_jobs(cfg)[0]
    payload = one_job_payload(cfg, job.split, job.model)
    payload["provenance"].pop("bundle_sha256")

    with pytest.raises(ValueError, match="bundle_sha256"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model)
    payload["results"][0]["status"] = "running"
    with pytest.raises(ValueError, match="not complete"):
        validate_job_payload(cfg, job, payload)


def test_control_plane_persists_accepted_result_for_merge(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_ttl_seconds=60.0, max_attempts=2)
    app = create_control_plane_app(
        state,
        bundle_path=bundle,
        bearer_token="secret",
        config=cfg,
        bundle_sha256="b" * 64,
    )
    client = app.test_client()
    leased = client.post(
        "/lease",
        headers={"Authorization": "Bearer secret"},
        json={"worker_id": "i-1"},
    ).get_json()
    payload = one_job_payload(cfg, leased["split"], leased["model"])
    payload["provenance"].pop("bundle_sha256")

    response = client.post(
        f"/result/{leased['job_id']}",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-1",
            "X-Lease-Attempt": str(leased["attempt"]),
        },
        json=payload,
    )

    assert response.status_code == 200
    assert (cfg.output_dir / "results" / f"{leased['job_id']}.json").exists()


def test_merge_requires_all_jobs_and_promotes_atomically(tmp_path):
    cfg = make_config(tmp_path)
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model)),
            encoding="utf-8",
        )

    merged = merge_job_artifacts(cfg)

    assert merged["result_count"] == 15
    assert (cfg.output_dir / "merged.json").exists()
    assert cfg.canonical_output_path.exists()


def test_merge_refuses_partial_batch_without_canonical_overwrite(tmp_path):
    cfg = make_config(tmp_path)
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    job = generate_jobs(cfg)[0]
    (result_dir / f"{job.job_id}.json").write_text(
        json.dumps(one_job_payload(cfg, job.split, job.model)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing"):
        merge_job_artifacts(cfg)
    assert not cfg.canonical_output_path.exists()
