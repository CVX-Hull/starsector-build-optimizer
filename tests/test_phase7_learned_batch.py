import json
import hashlib
import importlib.util
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from starsector_optimizer.phase7_learned_batch import (
    CANONICAL_MODELS,
    CANONICAL_SPLITS,
    BatchLaunchFailed,
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
    run_live_batch,
    validate_job_payload,
    validate_batch_config,
    write_status_snapshot,
)
from starsector_optimizer.matchup_features import FEATURE_SCHEMA_VERSION
from starsector_optimizer.phase7_matchup_data import (
    CANONICAL_SPLIT_SEED_BANK,
    RESERVED_CONFIRMATORY_SEED,
    SEEDLESS_SPLITS,
)


SEEDED_SPLIT_COUNT = len(CANONICAL_SPLITS) - len(SEEDLESS_SPLITS)
CANONICAL_JOB_COUNT = len(CANONICAL_MODELS) * (
    SEEDED_SPLIT_COUNT * len(CANONICAL_SPLIT_SEED_BANK) + len(SEEDLESS_SPLITS)
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
        ssh_key_name="starsector-probe",
        spot_allocation_strategy="price-capacity-optimized",
        target_workers=CANONICAL_JOB_COUNT,
        min_workers_to_start=CANONICAL_JOB_COUNT,
        budget_usd=20.0,
        max_lifetime_hours=2.0,
        max_job_attempts=4,
        lease_renewal_interval_seconds=60.0,
        lease_grace_seconds=300.0,
        pending_instance_grace_seconds=300.0,
        ledger_heartbeat_interval_seconds=60.0,
        ledger_warn_thresholds=(0.5, 0.8, 0.95),
        tailscale_authkey_secret="tskey-auth-test",
        control_plane_host="100.64.0.1",
        control_plane_port=9131,
        output_dir=tmp_path / "batch",
        canonical_output_path=tmp_path / "full.json",
        publish_canonical=True,
        execution_enabled=True,
        source_db_path=Path("data/phase7/wave1_matchups.sqlite"),
        game_dir=Path("game/starsector"),
        hpo_trials=24,
        hpo_jobs=4,
        model_thread_count=4,
        top_k_values=(1, 3, 5),
        split_seeds=CANONICAL_SPLIT_SEED_BANK,
        hpo_seed=23,
        holdout_fraction=0.2,
        train_fraction=0.8,
        honest_eval_usage="exploratory_selection",
        primary_top_k=1,
        promotion_metric="mean_per_opponent_spearman",
        promotion_threshold=0.0,
        claim_label="exploratory",
        final_refit_policy="fit_outer_train_only_no_deployment_artifact",
        candidate_universe="source_db_builds",
        deployment_artifact="none",
        dependency_extra="surrogate",
    )


class FakeServer:
    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeProvider:
    def __init__(self, *, instance_count: int = CANONICAL_JOB_COUNT, active=None, price: float = 0.01) -> None:
        self.instance_count = instance_count
        self.active = active
        self.price = price
        self.provision_calls = []
        self.terminate_fleet_calls = 0
        self.terminate_all_calls = 0

    def provision_fleet(self, **kwargs):
        self.provision_calls.append(kwargs)
        return [f"i-{idx}" for idx in range(self.instance_count)]

    def list_active(self, project_tag):
        if self.active is not None:
            return self.active
        return [
            {
                "id": f"i-{idx}",
                "region": "us-east-2",
                "instance_type": "c7i.4xlarge",
            }
            for idx in range(self.instance_count)
        ]

    def get_spot_price(self, region, instance_type):
        return self.price

    def terminate_fleet(self, *, fleet_name, project_tag):
        self.terminate_fleet_calls += 1
        return self.instance_count

    def terminate_all_tagged(self, project_tag):
        self.terminate_all_calls += 1
        return 0


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


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
ssh_key_name: starsector-probe
spot_allocation_strategy: price-capacity-optimized
target_workers: 21
min_workers_to_start: 21
budget_usd: 20.0
max_lifetime_hours: 2.0
max_job_attempts: 4
lease_renewal_interval_seconds: 60.0
lease_grace_seconds: 300.0
pending_instance_grace_seconds: 300.0
ledger_heartbeat_interval_seconds: 60.0
ledger_warn_thresholds: [0.5, 0.8, 0.95]
tailscale_authkey_secret: ${TAILSCALE_AUTHKEY}
control_plane_host: ${STARSECTOR_WORKSTATION_TAILNET_IP}
control_plane_port: 9131
output_dir: batch
canonical_output_path: full.json
publish_canonical: true
execution_enabled: true
source_db_path: data/phase7/wave1_matchups.sqlite
game_dir: game/starsector
hpo_trials: 24
hpo_jobs: 4
model_thread_count: 4
top_k: [1, 3, 5]
split_seeds: [101, 103, 107, 109, 113, 127, 131, 137, 139, 149]
hpo_seed: 23
holdout_fraction: 0.2
train_fraction: 0.8
honest_eval_usage: exploratory_selection
primary_top_k: 1
promotion_metric: mean_per_opponent_spearman
promotion_threshold: 0.0
claim_label: exploratory
final_refit_policy: fit_outer_train_only_no_deployment_artifact
candidate_universe: source_db_builds
deployment_artifact: none
dependency_extra: surrogate
""",
        encoding="utf-8",
    )

    cfg = load_batch_config(path)

    assert cfg.tailscale_authkey_secret == "tskey-auth-from-env"
    assert cfg.control_plane_host == "100.64.0.9"
    assert cfg.max_job_attempts == 4
    assert cfg.lease_renewal_interval_seconds == 60.0
    assert cfg.lease_grace_seconds == 300.0
    assert cfg.pending_instance_grace_seconds == 300.0
    assert cfg.honest_eval_usage == "exploratory_selection"
    validate_batch_config(cfg)


def test_generate_jobs_has_canonical_matrix(tmp_path):
    jobs = generate_jobs(make_config(tmp_path))

    assert len(jobs) == CANONICAL_JOB_COUNT
    expected = set()
    for split in CANONICAL_SPLITS:
        for model in CANONICAL_MODELS:
            if split in SEEDLESS_SPLITS:
                expected.add(f"{split}__{model}")
            else:
                expected.update(
                    f"{split}__{model}__s{seed}" for seed in CANONICAL_SPLIT_SEED_BANK
                )
    assert {job.job_id for job in jobs} == expected
    seedless = [job for job in jobs if job.split in SEEDLESS_SPLITS]
    assert {job.split_seed for job in seedless} == {CANONICAL_SPLIT_SEED_BANK[0]}


def test_generate_jobs_supports_explicit_smoke_matrix(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        split_seeds=(101,),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
    )

    jobs = generate_jobs(cfg)

    assert [job.job_id for job in jobs] == [
        "build__random_forest_tuned__s101",
        "build__catboost_regressor__s101",
    ]
    validate_batch_config(cfg)


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
    assert "--feature-profile" in command
    assert command[command.index("--feature-profile") + 1] == cfg.feature_profile
    assert "--honest-eval-usage" in command
    assert command[command.index("--honest-eval-usage") + 1] == cfg.honest_eval_usage
    assert "--primary-top-k" in command
    assert command[command.index("--primary-top-k") + 1] == str(cfg.primary_top_k)
    assert "--allow-missing-optional-models" not in command


def test_user_data_propagates_claim_boundary_flags(tmp_path):
    cfg = make_config(tmp_path)

    out = render_phase7_learned_batch_user_data(
        cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    assert "--honest-eval-usage exploratory_selection" in out
    assert "--primary-top-k 1" in out
    assert "--promotion-metric mean_per_opponent_spearman" in out
    assert "--final-refit-policy fit_outer_train_only_no_deployment_artifact" in out
    assert '--split-seed "$SPLIT_SEED"' in out
    assert "--inner-cv-folds" in out
    assert "--bootstrap-resamples" in out
    assert "--candidate-universe source_db_builds" in out
    assert "--deployment-artifact none" in out


def _experiment_flags_in_user_data(out: str) -> set[str]:
    start = out.index("phase7_learned_surrogate_experiment.py")
    end = out.index("RUN_PID=$!", start)
    return set(re.findall(r"--[a-z][a-z-]*", out[start:end]))


@pytest.mark.parametrize("with_optional_flags", (False, True))
def test_user_data_experiment_flags_match_build_job_command(tmp_path, with_optional_flags):
    """The worker userdata renders the experiment command independently of
    build_job_command; the preflight probes the latter, so the two flag sets
    must never drift."""
    cfg = make_config(tmp_path)
    if with_optional_flags:
        cfg = replace(
            cfg, noise_floor_override=0.5, fresh_honest_eval_ledger_id="ledger-1"
        )
    job = generate_jobs(cfg)[0]
    command_flags = {token for token in build_job_command(cfg, job) if token.startswith("--")}

    out = render_phase7_learned_batch_user_data(
        cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    assert _experiment_flags_in_user_data(out) == command_flags


def test_check_split_feasibility_refuses_infeasible_cells(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()
    experiment = cli._load_experiment_module()
    monkeypatch.setattr(
        experiment,
        "split_feasibility_report",
        lambda configs: [
            {
                "split": "component-vocab",
                "split_seed": 109,
                "status": "degenerate_component_vocab_split",
            }
        ],
    )

    with pytest.raises(RuntimeError, match=r"component-vocab\(seed 109\): degenerate_component_vocab_split"):
        cli.check_split_feasibility(cfg)


def test_check_split_feasibility_dry_runs_each_unique_cell_once(tmp_path, monkeypatch, capsys):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()
    experiment = cli._load_experiment_module()
    received = []

    def record(configs):
        received.extend(configs)
        return []

    monkeypatch.setattr(experiment, "split_feasibility_report", record)

    cli.check_split_feasibility(cfg)

    jobs = generate_jobs(cfg)
    unique_cells = {(job.split, job.split_seed) for job in jobs}
    assert len(received) == len(unique_cells)
    probed = {(c.split, c.split_seed) for c in received}
    assert probed == unique_cells
    for c in received:
        # The preflight parses the rendered job command through the
        # experiment script's own parser, so every worker knob — including
        # ones no hand-written mirror ever listed — must round-trip.
        assert c.component_vocab_max_overshoot == cfg.component_vocab_max_overshoot
        assert c.inner_cv_folds == cfg.inner_cv_folds
        assert c.holdout_fraction == cfg.holdout_fraction
        assert c.train_fraction == cfg.train_fraction
        assert c.bootstrap_resamples == cfg.bootstrap_resamples
        assert c.hpo_seed == cfg.hpo_seed
        assert c.db_path == cfg.source_db_path
        assert c.max_rows is None
        assert c.progress is False
        assert c.allow_missing_optional_models is True
    assert "Split feasibility preflight passed" in capsys.readouterr().out


def test_check_split_feasibility_probes_the_rendered_job_command(tmp_path, monkeypatch):
    """A worker knob changed only in the batch config must reach the probe
    through build_job_command parsing, with no preflight-side mirror edit."""
    cfg = replace(make_config(tmp_path), component_vocab_max_overshoot=0.42)
    cli = load_batch_cli_module()
    experiment = cli._load_experiment_module()
    received = []

    def record(configs):
        received.extend(configs)
        return []

    monkeypatch.setattr(experiment, "split_feasibility_report", record)

    cli.check_split_feasibility(cfg)

    assert received
    assert all(c.component_vocab_max_overshoot == 0.42 for c in received)


def test_bundle_paths_include_runtime_inputs(tmp_path):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()

    paths = set(cli.bundle_paths(cfg))

    assert cfg.source_db_path in paths
    assert Path("scripts/analysis/phase7_baseline_surrogate.py") in paths
    assert Path("game/starsector/data") in paths
    assert Path("game/starsector/manifest.json") in paths


def test_create_bundle_contains_runtime_inputs(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("unused", encoding="utf-8")
    cli = load_batch_cli_module()
    monkeypatch.setattr(cli, "load_batch_config", lambda path: cfg)
    monkeypatch.setattr(cli, "current_source_version", lambda: "abc123")
    monkeypatch.chdir(Path.cwd())

    bundle, digest = cli.create_bundle(config_path, tmp_path)

    assert len(digest) == 64
    assert bundle.is_absolute()
    import tarfile
    with tarfile.open(bundle, "r:gz") as tar:
        names = set(tar.getnames())
    assert str(cfg.source_db_path) in names
    assert "scripts/analysis/phase7_baseline_surrogate.py" in names
    assert "game/starsector/manifest.json" in names
    assert cli.SOURCE_VERSION_ARCNAME in names


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

    with pytest.raises(ValueError, match="exactly one region"):
        validate_batch_config(bad)


def test_config_validation_rejects_partial_fleet_threshold(tmp_path):
    cfg = make_config(tmp_path)
    bad = cfg.__class__(**{**cfg.__dict__, "min_workers_to_start": 8})

    with pytest.raises(ValueError, match="min_workers_to_start must equal target_workers"):
        validate_batch_config(bad)


def test_config_validation_rejects_job_matrix_mismatch_and_unknown_values(tmp_path):
    cfg = make_config(tmp_path)
    bad_count = replace(
        cfg,
        splits=("build",),
        models=("random_forest_tuned",),
        target_workers=CANONICAL_JOB_COUNT,
        min_workers_to_start=CANONICAL_JOB_COUNT,
    )
    with pytest.raises(ValueError, match="between 1 and the job count"):
        validate_batch_config(bad_count)

    bad_split = replace(
        cfg,
        splits=("not-a-split",),
        models=("random_forest_tuned",),
        target_workers=1,
        min_workers_to_start=1,
    )
    with pytest.raises(ValueError, match="unknown split"):
        validate_batch_config(bad_split)

    bad_model = replace(
        cfg,
        splits=("build",),
        models=("not-a-model",),
        target_workers=1,
        min_workers_to_start=1,
    )
    with pytest.raises(ValueError, match="unknown model"):
        validate_batch_config(bad_model)


def test_config_validation_rejects_unsorted_budget_warn_thresholds(tmp_path):
    cfg = make_config(tmp_path)
    bad = cfg.__class__(**{**cfg.__dict__, "ledger_warn_thresholds": (0.8, 0.5)})

    with pytest.raises(ValueError, match="ledger_warn_thresholds must be sorted"):
        validate_batch_config(bad)


def test_config_validation_rejects_final_claim_without_fresh_ledger(tmp_path):
    cfg = make_config(tmp_path)
    bad = replace(cfg, honest_eval_usage="final_claim", fresh_honest_eval_ledger_id=None)

    with pytest.raises(ValueError, match="fresh_honest_eval_ledger_id"):
        validate_batch_config(bad)


def test_control_plane_requires_bearer_token_for_all_routes(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
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
        f"/lease/{lease_payload['job_id']}/renew",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-1",
            "X-Lease-Attempt": str(lease_payload["attempt"]),
        },
        json={"worker_id": "i-1", "attempt": lease_payload["attempt"]},
    ).status_code == 200
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


def test_control_plane_persists_events_and_status_counts_events(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
    app = create_control_plane_app(
        state,
        bundle_path=bundle,
        bearer_token="secret",
        config=cfg,
    )
    client = app.test_client()
    job = generate_jobs(cfg)[0]

    response = client.post(
        f"/event/{job.job_id}",
        headers={"Authorization": "Bearer secret"},
        json={"event": "worker_started"},
    )

    assert response.status_code == 200
    assert (cfg.output_dir / "events" / f"{job.job_id}.jsonl").exists()
    status = client.get("/status", headers={"Authorization": "Bearer secret"}).get_json()
    first = next(row for row in status["jobs"] if row["job_id"] == job.job_id)
    assert first["event_count"] == 1
    worker_response = client.post(
        "/worker-event",
        headers={"Authorization": "Bearer secret"},
        json={"event": "bootstrap_start", "instance_id": "i-1"},
    )
    assert worker_response.status_code == 200
    assert (cfg.output_dir / "events" / "worker-events.jsonl").exists()


def test_lease_duplicate_result_and_wrong_job_handling(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
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


def test_requeue_missing_workers_waits_for_lease_grace(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
    lost = state.lease(now=100.0, worker_id="i-lost")
    live = state.lease(now=100.0, worker_id="i-live")
    assert lost is not None
    assert live is not None

    early = state.requeue_missing_workers({"i-live"}, now=120.0)
    assert early == ()

    requeued = state.requeue_missing_workers({"i-live"}, now=161.0)

    assert requeued == (lost.job_id,)
    rows = {row["job_id"]: row for row in state.status()["jobs"]}
    assert rows[lost.job_id]["status"] == "pending"
    assert rows[lost.job_id]["attempt"] == 1
    assert rows[lost.job_id]["last_requeue_reason"] == "missing_worker_lease_expired"
    assert rows[live.job_id]["status"] == "leased"


def test_lease_renewal_extends_long_running_job_ownership(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
    lease = state.lease(now=100.0, worker_id="i-1")
    assert lease is not None

    renewed = state.renew_lease(
        lease.job_id,
        worker_id="i-1",
        attempt=lease.attempt,
        now=150.0,
    )

    assert renewed["status"] == "renewed"
    rows = {row["job_id"]: row for row in state.status()["jobs"]}
    assert rows[lease.job_id]["lease_expires_at"] == 210.0
    payload = {"result_count": 1, "results": [{"split": lease.split, "model": lease.model}]}
    assert state.record_result(
        lease.job_id,
        payload,
        worker_id="i-1",
        attempt=lease.attempt,
        now=205.0,
    )["status"] == "accepted"


def test_lease_does_not_steal_unrenewed_job_without_controller_requeue(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=5.0, max_attempts=2)
    first = state.lease(now=100.0, worker_id="i-1")
    second = state.lease(now=106.0, worker_id="i-2")
    assert first is not None
    assert second is not None
    assert second.job_id != first.job_id

    requeued = state.requeue_unrenewed_leases(now=106.0)
    assert requeued == (first.job_id,)
    retry = state.lease(now=107.0, worker_id="i-2")
    assert retry is not None
    assert retry.job_id == first.job_id
    assert retry.attempt == 2


def test_result_rejects_wrong_worker_and_expired_unrenewed_lease(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=5.0, max_attempts=2)
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
    assert 'timeout "$JOB_TIMEOUT"' in out
    assert 'UV_BIN="/home/ubuntu/.local/bin/uv"' in out
    assert '"$UV_BIN" sync --frozen --extra surrogate' in out
    assert "JOB_ID=$(python3 -c" in out
    assert 'worker_failed_before_lease_${BOOTSTRAP_STEP}' in out
    assert "trap on_failure ERR" in out
    assert "post_event \"lease_acquired\"" in out
    assert "post_event \"worker_failed\"" in out
    assert "post_event_with_log \"experiment_failed\"" in out
    assert "lease_renewal_lost" in out
    assert "RUN_PID=$!" in out
    assert "/lease/\"$JOB_ID\"/renew" in out
    assert "shutdown -h +" in out
    assert "log_tail" in out
    assert "post_worker_event \"bootstrap_start\"" in out
    assert "/worker-event" in out


def test_user_data_wait_reaps_do_not_fire_err_trap(tmp_path):
    """Regression: bash ERR traps fire on failing simple commands even under
    `set +e`, so a bare `wait` on the SIGTERM'd renew loop (exit 143) invoked
    on_failure after every successful experiment — posting worker_failed and
    shutting the instance down before the result upload could land
    (2026-07-11 batch, 0/183 results accepted)."""
    cfg = make_config(tmp_path)
    out = render_phase7_learned_batch_user_data(
        cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    assert 'wait "$RUN_PID" || RUN_CODE=$?' in out
    assert 'wait "$RENEW_PID" >/dev/null 2>&1 || RENEW_CODE=$?' in out
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("wait "):
            assert "||" in stripped, f"unprotected wait fires the ERR trap: {line!r}"


def test_user_data_retries_result_upload_before_declaring_failure(tmp_path):
    """A completed experiment is the most expensive artifact on the worker;
    the upload curl must not be a bare simple command that fires the ERR trap
    (and discards the result) on one transient failure."""
    cfg = make_config(tmp_path)
    out = render_phase7_learned_batch_user_data(
        cfg,
        control_plane_url="http://100.64.0.1:9131",
        bearer_token="secret-token",
        bundle_sha256="a" * 64,
    )

    assert f"seq 1 {cfg.result_upload_attempts}" in out
    assert '&& { UPLOAD_CODE=0; break; } || UPLOAD_CODE=$?' in out
    assert f"sleep {cfg.result_upload_retry_seconds}" in out
    assert 'post_event_with_log "result_upload_failed" "$LOG" "$UPLOAD_CODE"' in out
    # The retry loop owns the upload failure path; the success event must be
    # gated on UPLOAD_CODE, not sequenced after a bare curl.
    assert out.index('[[ "$UPLOAD_CODE" != "0" ]]') < out.index('post_event "result_uploaded"')


def test_config_validation_bounds_result_upload_retries(tmp_path):
    cfg = make_config(tmp_path)

    with pytest.raises(ValueError, match="result_upload_attempts"):
        validate_batch_config(replace(cfg, result_upload_attempts=0))
    with pytest.raises(ValueError, match="result_upload_retry_seconds"):
        validate_batch_config(replace(cfg, result_upload_retry_seconds=0.0))
    with pytest.raises(ValueError, match="shorter than lease grace"):
        validate_batch_config(
            replace(cfg, result_upload_attempts=100, result_upload_retry_seconds=10.0)
        )


def test_bash_err_trap_fires_under_set_plus_e_unless_wait_is_protected():
    """Pin the bash semantics the userdata relies on: the ERR trap fires for a
    failing bare `wait` even under `set +e`, and an `|| CODE=$?` list both
    suppresses the trap and captures the real exit status."""
    script = """
set -euo pipefail
trapped=0
on_failure() { trapped=1; }
trap on_failure ERR
sleep 30 & PID=$!
set +e
kill "$PID" >/dev/null 2>&1 || true
CODE=0
wait "$PID" >/dev/null 2>&1 || CODE=$?
set -e
echo "trapped=$trapped code=$CODE"
"""
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "trapped=0 code=143"


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


def test_write_status_snapshot_contains_counts_and_budget(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)

    payload = write_status_snapshot(
        cfg,
        state,
        phase="running",
        instance_ids=("i-1",),
        active_instances=({"id": "i-1", "region": "us-east-2"},),
        cumulative_usd=1.25,
        timestamp="2026-05-12T00:00:00Z",
    )

    assert payload["state"]["counts"]["pending"] == CANONICAL_JOB_COUNT
    assert payload["cumulative_usd"] == 1.25
    assert json.loads((cfg.output_dir / "status.json").read_text())["phase"] == "running"


def one_job_payload(
    cfg: LearnedBatchConfig, split: str, model: str, split_seed: int | None = None
) -> dict:
    if split_seed is None:
        split_seed = cfg.split_seeds[0]
    job_id = (
        f"{split}__{model}"
        if split in SEEDLESS_SPLITS
        else f"{split}__{model}__s{split_seed}"
    )
    registry = {
        "a_x": {"family": "a", "template": "a_x", "parents": [], "leakage_risk": "low"},
    }
    registry_sha = hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "experiment_schema_version": 2,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_profile": cfg.feature_profile,
        "batch_job": {
            "job_id": job_id,
            "batch_name": cfg.name,
            "fleet_name": cfg.fleet_name,
            "split": split,
            "model": model,
            "split_seed": split_seed,
        },
        "db_path": str(cfg.source_db_path),
        "model_families": [model],
        "provenance": {
            "game_dir": str(cfg.game_dir),
            "split_seed": split_seed,
            "hpo_seed": cfg.hpo_seed,
            "hpo_trials": cfg.hpo_trials,
            "hpo_jobs": cfg.hpo_jobs,
            "model_thread_count": cfg.model_thread_count,
            "inner_cv_folds": cfg.inner_cv_folds,
            "noise_floor_override": cfg.noise_floor_override,
            "bootstrap_resamples": cfg.bootstrap_resamples,
            "component_vocab_max_overshoot": cfg.component_vocab_max_overshoot,
            "holdout_fraction": cfg.holdout_fraction,
            "train_fraction": cfg.train_fraction,
            "top_k_values": list(cfg.top_k_values),
            "feature_profile": cfg.feature_profile,
            "honest_eval_usage": cfg.honest_eval_usage,
            "fresh_honest_eval_ledger_id": cfg.fresh_honest_eval_ledger_id,
            "primary_top_k": cfg.primary_top_k,
            "promotion_metric": cfg.promotion_metric,
            "promotion_threshold": cfg.promotion_threshold,
            "claim_label": cfg.claim_label,
            "final_refit_policy": cfg.final_refit_policy,
            "candidate_universe": cfg.candidate_universe,
            "deployment_artifact": cfg.deployment_artifact,
            "batch_job_id": job_id,
            "batch_name": cfg.name,
            "batch_fleet_name": cfg.fleet_name,
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
                "target_variable": "training_matchups.target",
                "claim_boundary": {
                    "target_variable": "training_matchups.target",
                    "honest_eval_diagnostic_target": "honest_eval_top_k",
                    "primary_split": split,
                    "primary_top_k": cfg.primary_top_k,
                    "promotion_metric": cfg.promotion_metric,
                    "promotion_threshold": cfg.promotion_threshold,
                    "higher_is_better": True,
                    "claim_label": cfg.claim_label,
                    "honest_eval_usage": cfg.honest_eval_usage,
                    "fresh_honest_eval_ledger_id": cfg.fresh_honest_eval_ledger_id,
                },
                "model_family_policy": {
                    "policy_type": "fixed_matrix",
                    "candidate_model_families": list(CANONICAL_MODELS),
                    "selected_model_family": model,
                    "selection_scope": "predeclared_fixed_matrix",
                },
                "feature_families": {
                    "feature_profile": cfg.feature_profile,
                    "column_count": 2,
                    "prefixes": ["a"],
                    "columns": ["a_x", "a_y"],
                },
                "feature_selection_protocol": {
                    "policy_type": "fixed_profile_no_selector",
                    "feature_profile": cfg.feature_profile,
                    "feature_family_registry": registry,
                    "feature_family_registry_sha256": registry_sha,
                    "selected_feature_families": ["a"],
                    "selected_feature_count": 1,
                    "selector_family": "none",
                    "selector_hyperparameters": {},
                    "stability": "not_applicable",
                    "heredity_policy": "not_applicable",
                    "selection_scope": "no_feature_selection",
                },
                "deployment_policy": {
                    "final_refit_policy": cfg.final_refit_policy,
                    "candidate_universe": cfg.candidate_universe,
                    "deployment_artifact": cfg.deployment_artifact,
                },
                "hierarchy_scorecard": {
                    "split_level": split,
                    "group_key_function": "held_out_build_split",
                    "group_key_fields": ["build_key"],
                    "claim_supported": "held_out_build_transfer",
                    "forbidden_cross_split_keys": ["build_key"],
                    "overlap_counts": {"exact_opponent": 0},
                    "component_overlap_diagnostics": {
                        "k1": {"status": "not_applicable", "reason": "fixture"},
                        "k2": {"status": "not_applicable", "reason": "fixture"},
                        "k3": {"status": "not_applicable", "reason": "fixture"},
                    },
                },
                "leakage_diagnostics": {
                    "forbidden_key_overlap": {"status": "pass", "value": 0},
                    "adversarial_validation_auc": {"status": "not_applicable", "reason": "fixture"},
                    "rare_combination_overlap": {"status": "not_applicable", "reason": "fixture"},
                    "nearest_neighbor_overlap": {"status": "not_applicable", "reason": "fixture"},
                    "sparse_id_ablation_delta": {"status": "not_applicable", "reason": "fixture"},
                },
                "n_train": 100,
                "n_test": 25,
                "mae": 0.1,
                "rmse": 0.2,
                "spearman_rho": 0.3,
                "hpo": {"selected_hyperparameters": {}, "inner_validation_metrics": {}},
                "timing": {"fit_seconds": 0.1},
                "honest_eval_top_k": {"top_k": [1, 3, 5]},
                "rank_metrics": {
                    "per_opponent": {"mean_spearman": 0.4, "included_opponents": 10},
                    "build_aggregate": {
                        "spearman": 0.5,
                        "precision_at_k": {"1": 1.0},
                        "regret_at_k": {"1": {"raw": 0.0, "normalized": 0.0}},
                    },
                    "bootstrap": {
                        "mean_per_opponent_spearman": {"ci_low": 0.3, "ci_high": 0.5, "n_finite": 20},
                    },
                },
                "skill_scores": {"mse_model": 0.04, "mse_train_mean": 0.5, "skill": 0.92},
                "panel_target_stats": {"n": 25, "mean": 0.0, "sd": 0.7, "endpoint_mass_low": 0.4, "endpoint_mass_high": 0.1},
                "noise_floor": {"noise_floor": 0.05, "source": "fallback"},
                "inner_cv": {"fold_count": cfg.inner_cv_folds, "fold_construction": "grouped_kfold", "fold_sizes": []},
                "outer_split_lineage": {
                    "split_seed": split_seed,
                    "seed_bank_label": "2026-07-bank-a",
                    "confirmatory_reserved_seed": RESERVED_CONFIRMATORY_SEED,
                    "reused_partition": split in SEEDLESS_SPLITS,
                },
                "leakage_checklist": {
                    "outer_test_targets_excluded_from_fit": True,
                    "honest_eval_targets_excluded_from_fit": True,
                    "feature_selection_inside_inner_fold": True,
                    "build_key_excluded_from_feature_vectors": True,
                },
                "comparator_inline": {
                    "random_forest": {
                        "mae": 0.2,
                        "rmse": 0.3,
                        "spearman_rho": 0.2,
                        "rank_metrics": {"per_opponent": {"mean_spearman": 0.2}},
                    },
                },
                "comparator_delta": {
                    "best_comparator": "random_forest",
                    "delta_vs_best_comparator": {"rmse": -0.1},
                    "matched_family": "random_forest",
                    "delta_vs_matched_family": {"rmse": -0.1},
                },
            }
        ],
    }


def test_validate_job_payload_rejects_running_or_missing_bundle(tmp_path):
    cfg = make_config(tmp_path)
    job = generate_jobs(cfg)[0]
    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["provenance"].pop("bundle_sha256")

    with pytest.raises(ValueError, match="bundle_sha256"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["status"] = "running"
    with pytest.raises(ValueError, match="not complete"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload.pop("experiment_schema_version")
    with pytest.raises(ValueError, match="experiment schema"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["comparator_inline"] = "bad"
    with pytest.raises(ValueError, match="inline comparator"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["feature_schema_version"] = FEATURE_SCHEMA_VERSION - 1
    with pytest.raises(ValueError, match="feature schema version mismatch"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["batch_job"]["job_id"] = "stale__artifact"
    with pytest.raises(ValueError, match="batch job field 'job_id'"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["provenance"]["bundle_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="bundle_sha256 mismatch"):
        validate_job_payload(cfg, job, payload, bundle_sha256="b" * 64)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["provenance"].pop("dependency_extra")
    with pytest.raises(ValueError, match="dependency extra missing"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["leakage_checklist"] = {}
    with pytest.raises(ValueError, match="leakage checklist"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["leakage_diagnostics"]["forbidden_key_overlap"] = {
        "status": "fail",
        "value": 1,
    }
    with pytest.raises(ValueError, match="leakage diagnostics"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["provenance"]["code_version"] = "abc123+dirty"
    with pytest.raises(ValueError, match="code version"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["comparator_delta"] = {}
    with pytest.raises(ValueError, match="comparator deltas"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["outer_split_lineage"]["split_seed"] = 999
    with pytest.raises(ValueError, match="lineage seed"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["rmse"] = float("nan")
    with pytest.raises(ValueError, match="metric 'rmse'"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0].pop("target_variable")
    with pytest.raises(ValueError, match="target variable"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["target_variable"] = "honest_eval_fitness"
    with pytest.raises(ValueError, match="target variable mismatch"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["feature_profile"] = "geometry"
    with pytest.raises(ValueError, match="feature profile"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["feature_families"]["feature_profile"] = "geometry"
    with pytest.raises(ValueError, match="feature family profile"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0].pop("claim_boundary")
    with pytest.raises(ValueError, match="missing fields"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["claim_boundary"]["honest_eval_usage"] = "final_claim"
    payload["results"][0]["claim_boundary"]["fresh_honest_eval_ledger_id"] = None
    with pytest.raises(ValueError, match="claim boundary field 'honest_eval_usage'"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["claim_boundary"]["honest_eval_usage"] = "diagnostic_only"
    with pytest.raises(ValueError, match="claim boundary field 'honest_eval_usage'"):
        validate_job_payload(cfg, job, payload)

    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    payload["results"][0]["feature_selection_protocol"]["feature_family_registry_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="registry digest"):
        validate_job_payload(cfg, job, payload)


def test_control_plane_persists_accepted_result_for_merge(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
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
    payload = one_job_payload(cfg, leased["split"], leased["model"], leased["split_seed"])
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


def test_control_plane_does_not_persist_rejected_or_duplicate_result(tmp_path):
    cfg = make_config(tmp_path)
    bundle = tmp_path / "bundle.tgz"
    bundle.write_bytes(b"bundle")
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)
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
    output_path = cfg.output_dir / "results" / f"{leased['job_id']}.json"
    payload = one_job_payload(cfg, leased["split"], leased["model"], leased["split_seed"])

    wrong_worker = client.post(
        f"/result/{leased['job_id']}",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-other",
            "X-Lease-Attempt": str(leased["attempt"]),
        },
        json=payload,
    )

    assert wrong_worker.status_code == 409
    assert not output_path.exists()

    accepted = client.post(
        f"/result/{leased['job_id']}",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-1",
            "X-Lease-Attempt": str(leased["attempt"]),
        },
        json=payload,
    )
    assert accepted.status_code == 200
    original = json.loads(output_path.read_text(encoding="utf-8"))
    duplicate_payload = one_job_payload(cfg, leased["split"], leased["model"], leased["split_seed"])
    duplicate_payload["results"][0]["rmse"] = 0.99

    duplicate = client.post(
        f"/result/{leased['job_id']}",
        headers={
            "Authorization": "Bearer secret",
            "X-Worker-Id": "i-1",
            "X-Lease-Attempt": str(leased["attempt"]),
        },
        json=duplicate_payload,
    )

    assert duplicate.status_code == 200
    assert json.loads(output_path.read_text(encoding="utf-8")) == original


def test_merge_requires_all_jobs_and_promotes_atomically(tmp_path):
    cfg = make_config(tmp_path)
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
            encoding="utf-8",
        )

    merged = merge_job_artifacts(cfg)

    assert merged["result_count"] == CANONICAL_JOB_COUNT
    assert merged["claim_boundary"]["honest_eval_usage"] == cfg.honest_eval_usage
    assert merged["feature_selection_protocol"]["policy_type"] == "fixed_profile_no_selector"
    assert merged["deployment_policy"]["candidate_universe"] == cfg.candidate_universe
    assert (cfg.output_dir / "merged.json").exists()
    assert cfg.canonical_output_path.exists()


def test_merge_refuses_partial_batch_without_canonical_overwrite(tmp_path):
    cfg = make_config(tmp_path)
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    job = generate_jobs(cfg)[0]
    (result_dir / f"{job.job_id}.json").write_text(
        json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing"):
        merge_job_artifacts(cfg)
    assert not cfg.canonical_output_path.exists()


def test_subset_merge_stays_batch_internal_and_reports_subset_models(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        split_seeds=(101,),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
        lease_grace_seconds=5.0,
    )
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
            encoding="utf-8",
        )

    merged = merge_job_artifacts(cfg)

    assert merged["result_count"] == 2
    assert merged["model_families"] == ["random_forest_tuned", "catboost_regressor"]
    assert (cfg.output_dir / "merged.json").exists()
    assert not cfg.canonical_output_path.exists()


def test_merge_rechecks_canonical_publication_guard(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=True,
    )
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="canonical publication"):
        merge_job_artifacts(cfg)
    assert not cfg.canonical_output_path.exists()


def complete_all_jobs(cfg: LearnedBatchConfig, state: BatchState) -> None:
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    while True:
        lease = state.lease(now=100.0, worker_id="i-worker")
        if lease is None:
            break
        payload = one_job_payload(cfg, lease.split, lease.model, lease.split_seed)
        (result_dir / f"{lease.job_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        state.record_result(
            lease.job_id,
            payload,
            worker_id="i-worker",
            attempt=lease.attempt,
            now=101.0,
        )


def test_run_live_batch_merges_and_tears_down_on_completion(tmp_path):
    cfg = replace(make_config(tmp_path), root_volume_size_gb=128)
    provider = FakeProvider()
    server = FakeServer()
    final_audits = []

    merged = run_live_batch(
        cfg,
        provider=provider,
        bundle_path=tmp_path / "bundle.tgz",
        bundle_sha256="b" * 64,
        bearer_token="secret",
        poll_interval_seconds=1.0,
        server_factory=lambda *args, **kwargs: server,
        sleep_fn=lambda seconds: None,
        final_audit_fn=lambda config: final_audits.append(config.name),
        on_poll=lambda state: complete_all_jobs(cfg, state),
    )

    assert merged["result_count"] == CANONICAL_JOB_COUNT
    assert cfg.canonical_output_path.exists()
    assert server.shutdown_called
    assert provider.terminate_fleet_calls == 1
    assert provider.terminate_all_calls == 1
    assert provider.provision_calls[0]["root_volume_size_gb"] == 128
    assert final_audits == [cfg.name]


def test_run_live_batch_teardown_continues_after_terminate_fleet_error(tmp_path):
    cfg = make_config(tmp_path)
    provider = FakeProvider()
    server = FakeServer()
    provider.terminate_fleet = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fleet boom"))

    with pytest.raises(BatchLaunchFailed, match="fleet boom"):
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            poll_interval_seconds=1.0,
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=lambda seconds: None,
            on_poll=lambda state: complete_all_jobs(cfg, state),
        )

    assert server.shutdown_called
    assert provider.terminate_all_calls == 1
    status = json.loads((cfg.output_dir / "status.json").read_text())
    assert status["phase"] == "teardown_complete"
    assert "fleet boom" in status["message"]


def test_run_live_batch_budget_exceeded_tears_down(tmp_path):
    cfg = make_config(tmp_path)
    cfg = cfg.__class__(**{**cfg.__dict__, "budget_usd": 0.001})
    provider = FakeProvider(
        active=[{"id": "i-1", "region": "us-east-2", "instance_type": "c7i.4xlarge"}],
        price=100.0,
    )
    server = FakeServer()

    with pytest.raises(BudgetExceeded):
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            poll_interval_seconds=60.0,
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=lambda seconds: None,
        )

    assert server.shutdown_called
    assert provider.terminate_fleet_calls == 1
    assert provider.terminate_all_calls == 1
    assert json.loads((cfg.output_dir / "status.json").read_text())["phase"] == "teardown_complete"


def test_run_live_batch_preserves_primary_failure_when_teardown_also_fails(tmp_path):
    cfg = make_config(tmp_path)
    provider = FakeProvider(
        active=[{"id": "i-1", "region": "us-east-2", "instance_type": "c7i.4xlarge"}],
        price=100.0,
    )
    provider.terminate_fleet = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fleet boom"))
    server = FakeServer()

    with pytest.raises(BudgetExceeded):
        run_live_batch(
            replace(cfg, budget_usd=0.001),
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            poll_interval_seconds=60.0,
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=lambda seconds: None,
        )

    status = json.loads((cfg.output_dir / "status.json").read_text())
    assert "fleet boom" in status["message"]


def test_run_live_batch_treats_initial_instances_as_pending_until_visible(tmp_path):
    cfg = replace(make_config(tmp_path), pending_instance_grace_seconds=10.0)
    server = FakeServer()
    clock = FakeClock(start=100.0)

    class LaggingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(instance_count=CANONICAL_JOB_COUNT)
            self.list_calls = 0

        def list_active(self, project_tag):
            self.list_calls += 1
            if self.list_calls == 1:
                return []
            return super().list_active(project_tag)

    provider = LaggingProvider()

    merged = run_live_batch(
        cfg,
        provider=provider,
        bundle_path=tmp_path / "bundle.tgz",
        bundle_sha256="b" * 64,
        bearer_token="secret",
        poll_interval_seconds=1.0,
        server_factory=lambda *args, **kwargs: server,
        sleep_fn=clock.sleep,
        now_fn=clock.time,
        on_poll=lambda state: (
            complete_all_jobs(cfg, state) if provider.list_calls else None
        ),
    )

    assert merged["result_count"] == CANONICAL_JOB_COUNT
    assert [call["target_workers"] for call in provider.provision_calls] == [CANONICAL_JOB_COUNT]


def test_run_live_batch_fails_when_pending_instances_never_become_active(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        pending_instance_grace_seconds=2.0,
        max_lifetime_hours=1.0,
    )
    server = FakeServer()
    clock = FakeClock(start=100.0)
    provider = FakeProvider(instance_count=CANONICAL_JOB_COUNT, active=[])

    with pytest.raises(BatchLaunchFailed, match="pending workers"):
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            poll_interval_seconds=1.0,
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=clock.sleep,
            now_fn=clock.time,
        )

    assert [call["target_workers"] for call in provider.provision_calls] == [CANONICAL_JOB_COUNT]
    assert provider.terminate_fleet_calls == 1
    assert provider.terminate_all_calls == 1


def test_run_live_batch_replaces_missing_worker_and_completes(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        split_seeds=(101,),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
        lease_grace_seconds=5.0,
    )
    server = FakeServer()
    leases = {}
    clock = FakeClock(start=100.0)

    class ReplacementProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(instance_count=2)
            self.replacement_launched = False

        def provision_fleet(self, **kwargs):
            self.provision_calls.append(kwargs)
            if len(self.provision_calls) == 1:
                return ["i-lost", "i-live"]
            self.replacement_launched = True
            return ["i-replacement"]

        def list_active(self, project_tag):
            active = [
                {"id": "i-live", "region": "us-east-2", "instance_type": "c7i.4xlarge"},
            ]
            if self.replacement_launched:
                active.append({
                    "id": "i-replacement",
                    "region": "us-east-2",
                    "instance_type": "c7i.4xlarge",
                })
            return active

    provider = ReplacementProvider()

    def on_poll(state: BatchState) -> None:
        if not leases:
            first = state.lease(now=100.0, worker_id="i-lost")
            second = state.lease(now=100.0, worker_id="i-live")
            assert first is not None
            assert second is not None
            leases[first.job_id] = first
            leases[second.job_id] = second
            return
        live = next((lease for lease in leases.values() if lease.worker_id == "i-live"), None)
        rows = {row["job_id"]: row for row in state.status()["jobs"]}
        if live is not None and rows[live.job_id]["status"] == "leased":
            state.renew_lease(
                live.job_id,
                worker_id="i-live",
                attempt=live.attempt,
                now=clock.time(),
            )
        if provider.replacement_launched:
            result_dir = cfg.output_dir / "results"
            result_dir.mkdir(parents=True, exist_ok=True)
            rows = {row["job_id"]: row for row in state.status()["jobs"]}
            for job_id, row in rows.items():
                if row["status"] == "completed":
                    continue
                if row["status"] == "pending":
                    lease = state.lease(now=110.0, worker_id="i-replacement")
                    assert lease is not None
                else:
                    lease = leases[job_id]
                payload = one_job_payload(cfg, lease.split, lease.model, lease.split_seed)
                (result_dir / f"{lease.job_id}.json").write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
                state.record_result(
                    lease.job_id,
                    payload,
                    worker_id=lease.worker_id,
                    attempt=lease.attempt,
                    now=clock.time(),
                )

    merged = run_live_batch(
        cfg,
        provider=provider,
        bundle_path=tmp_path / "bundle.tgz",
        bundle_sha256="b" * 64,
        bearer_token="secret",
        poll_interval_seconds=1.0,
        server_factory=lambda *args, **kwargs: server,
        sleep_fn=clock.sleep,
        now_fn=clock.time,
        on_poll=on_poll,
    )

    assert merged["result_count"] == 2
    assert [call["target_workers"] for call in provider.provision_calls] == [2, 1]
    assert server.shutdown_called


def test_run_live_batch_logs_budget_warn_threshold_once(tmp_path, caplog):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        split_seeds=(101,),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
        budget_usd=1.0,
        ledger_warn_thresholds=(0.5,),
    )
    provider = FakeProvider(instance_count=2, price=21.0)
    server = FakeServer()

    with caplog.at_level("WARNING"):
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            poll_interval_seconds=60.0,
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=lambda seconds: None,
            on_poll=lambda state: complete_all_jobs(cfg, state),
        )

    messages = [record.message for record in caplog.records]
    assert sum("batch budget threshold crossed" in message for message in messages) == 1


def test_run_live_batch_rejects_too_small_partial_fleet(tmp_path):
    cfg = make_config(tmp_path)
    provider = FakeProvider(instance_count=1)
    server = FakeServer()

    with pytest.raises(BatchLaunchFailed, match="minimum"):
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=tmp_path / "bundle.tgz",
            bundle_sha256="b" * 64,
            bearer_token="secret",
            server_factory=lambda *args, **kwargs: server,
            sleep_fn=lambda seconds: None,
        )

    assert server.shutdown_called
    assert provider.terminate_fleet_calls == 1
    assert provider.terminate_all_calls == 1


def test_cli_launch_execute_runs_live_batch_after_preflight(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()
    calls = []

    monkeypatch.setattr(cli, "load_batch_config", lambda path: cfg)
    monkeypatch.setattr(cli, "check_aws_credentials", lambda: calls.append("aws"))
    monkeypatch.setattr(cli, "check_authkey_syntax", lambda authkey: calls.append(("auth", authkey)))
    monkeypatch.setattr(
        cli,
        "check_ami_tags_against_manifest",
        lambda provider, amis, manifest, required_regions: calls.append(("ami", tuple(required_regions))),
    )
    monkeypatch.setattr(cli, "check_amis_available", lambda provider, config: calls.append("ami-available"))
    monkeypatch.setattr(cli, "check_key_pairs_available", lambda provider, config: calls.append("keypair"))
    monkeypatch.setattr(cli, "check_split_feasibility", lambda config: calls.append("split-feasibility"))
    monkeypatch.setattr(cli, "GameManifest", type("GM", (), {"load": staticmethod(object)}))
    monkeypatch.setattr(cli, "AWSProvider", lambda regions: FakeProvider())
    monkeypatch.setattr(cli, "create_bundle", lambda path, out: (tmp_path / "bundle.tgz", "b" * 64))
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "token")
    monkeypatch.setattr(cli.atexit, "register", lambda fn: calls.append("atexit"))
    monkeypatch.setattr(cli.signal, "signal", lambda signum, handler: calls.append(("signal", signum)))
    monkeypatch.setattr(
        cli,
        "run_live_batch",
        lambda config, **kwargs: calls.append(("live", kwargs["bundle_sha256"], kwargs["bearer_token"])),
    )

    assert cli.launch(tmp_path / "config.yaml", execute=True) == 0

    assert "aws" in calls
    assert ("auth", cfg.tailscale_authkey_secret) in calls
    assert ("ami", cfg.regions) in calls
    assert "ami-available" in calls
    assert "keypair" in calls
    assert "split-feasibility" in calls
    assert "atexit" in calls
    assert ("live", "b" * 64, "token") in calls


def test_cli_launch_execute_disabled_refuses_before_aws_provider(tmp_path, monkeypatch):
    cfg = replace(make_config(tmp_path), execution_enabled=False)
    cli = load_batch_cli_module()

    monkeypatch.setattr(cli, "load_batch_config", lambda path: cfg)
    monkeypatch.setattr(
        cli,
        "AWSProvider",
        lambda regions: (_ for _ in ()).throw(AssertionError("AWSProvider touched")),
    )

    with pytest.raises(RuntimeError, match="execution_enabled is false"):
        cli.launch(tmp_path / "config.yaml", execute=True)


def test_check_amis_available_rejects_pending_image(tmp_path):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()

    class Client:
        def describe_images(self, ImageIds):
            return {"Images": [{"ImageId": ImageIds[0], "State": "pending"}]}

    class Provider:
        def _client(self, region):
            return Client()

    with pytest.raises(RuntimeError, match="pending"):
        cli.check_amis_available(Provider(), cfg)


def test_check_key_pairs_available_rejects_missing_key(tmp_path):
    cfg = make_config(tmp_path)
    cli = load_batch_cli_module()

    class Client:
        def describe_key_pairs(self, Filters):
            return {"KeyPairs": []}

    class Provider:
        def _client(self, region):
            return Client()

    with pytest.raises(RuntimeError, match="key pair"):
        cli.check_key_pairs_available(Provider(), cfg)


def test_config_validation_rejects_burned_and_reserved_seeds(tmp_path):
    cfg = make_config(tmp_path)

    with pytest.raises(ValueError, match="C4"):
        validate_batch_config(
            replace(cfg, split_seeds=(17, 101), publish_canonical=False, target_workers=1, min_workers_to_start=1)
        )
    with pytest.raises(ValueError, match="reserved confirmatory seed"):
        validate_batch_config(
            replace(
                cfg,
                split_seeds=(101, RESERVED_CONFIRMATORY_SEED),
                publish_canonical=False,
                target_workers=1,
                min_workers_to_start=1,
            )
        )
    with pytest.raises(ValueError, match="non-empty"):
        validate_batch_config(
            replace(cfg, split_seeds=(), publish_canonical=False, target_workers=1, min_workers_to_start=1)
        )


def test_publish_canonical_requires_full_seed_bank(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        split_seeds=(101, 103),
        target_workers=10,
        min_workers_to_start=10,
    )

    with pytest.raises(ValueError, match="seed-bank"):
        validate_batch_config(cfg)


def test_build_job_command_uses_per_job_seed_and_eval_flags(tmp_path):
    cfg = make_config(tmp_path)
    jobs = generate_jobs(cfg)
    seeded = next(job for job in jobs if job.split not in SEEDLESS_SPLITS)
    command = build_job_command(cfg, seeded)

    assert command[command.index("--split-seed") + 1] == str(seeded.split_seed)
    assert command[command.index("--inner-cv-folds") + 1] == str(cfg.inner_cv_folds)
    assert command[command.index("--bootstrap-resamples") + 1] == str(cfg.bootstrap_resamples)
    assert "--comparator-json" not in command


def test_merge_emits_seed_aggregates_with_descriptive_spread(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build", "forward-time"),
        models=("random_forest_tuned",),
        split_seeds=(101, 103),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
    )
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
            encoding="utf-8",
        )

    merged = merge_job_artifacts(cfg)

    aggregates = merged["seed_aggregates"]
    build_agg = aggregates["build:random_forest_tuned"]
    assert build_agg["n_seeds"] == 2
    assert build_agg["rmse"]["mean"] == pytest.approx(0.2)
    assert build_agg["rmse"]["sd"] == pytest.approx(0.0)
    assert build_agg["mean_per_opponent_spearman"]["n_finite"] == 2
    forward_agg = aggregates["forward-time:random_forest_tuned"]
    assert forward_agg["n_seeds"] == 1
    assert forward_agg["rmse"]["sd"] is None
    assert merged["split_seeds"] == [101, 103]


def test_canonical_publish_refuses_to_overwrite_other_batch(tmp_path):
    cfg = make_config(tmp_path)
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    for job in generate_jobs(cfg):
        (result_dir / f"{job.job_id}.json").write_text(
            json.dumps(one_job_payload(cfg, job.split, job.model, job.split_seed)),
            encoding="utf-8",
        )
    cfg.canonical_output_path.write_text(
        json.dumps({"provenance": {"batch": {"name": "some-prior-wave"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="prior-wave evidence"):
        merge_job_artifacts(cfg)

    # Re-publishing the same batch's own canonical artifact is allowed.
    cfg.canonical_output_path.write_text(
        json.dumps({"provenance": {"batch": {"name": cfg.name}}}),
        encoding="utf-8",
    )
    merged = merge_job_artifacts(cfg)
    assert merged["result_count"] == CANONICAL_JOB_COUNT


def test_validate_job_payload_rejects_seed_outside_config_list(tmp_path):
    cfg = make_config(tmp_path)
    job = generate_jobs(cfg)[0]
    rogue_job = replace(
        job, split_seed=997, job_id=f"{job.split}__{job.model}__s997"
    )
    payload = one_job_payload(cfg, job.split, job.model, 997)

    with pytest.raises(ValueError, match="not in the config seed list"):
        validate_job_payload(cfg, rogue_job, payload)


def test_lease_carries_split_seed(tmp_path):
    cfg = make_config(tmp_path)
    state = BatchState(generate_jobs(cfg), lease_grace_seconds=60.0, max_attempts=2)

    lease = state.lease(now=100.0, worker_id="i-1")

    assert lease is not None
    assert isinstance(lease.split_seed, int)
    assert lease.split_seed in cfg.split_seeds


def insufficiency_payload(cfg: LearnedBatchConfig, job) -> dict:
    payload = one_job_payload(cfg, job.split, job.model, job.split_seed)
    result = payload["results"][0]
    result["status"] = "degenerate_component_vocab_split"
    for key in (
        "mae", "rmse", "spearman_rho", "rank_metrics", "skill_scores",
        "panel_target_stats", "noise_floor", "inner_cv", "comparator_inline",
        "comparator_delta", "hpo", "timing", "n_train", "n_test",
    ):
        result.pop(key, None)
    return payload


def test_validate_job_payload_accepts_insufficiency_artifact(tmp_path):
    cfg = make_config(tmp_path)
    job = generate_jobs(cfg)[0]
    payload = insufficiency_payload(cfg, job)

    accepted = validate_job_payload(cfg, job, payload)

    assert accepted["results"][0]["status"] == "degenerate_component_vocab_split"


def test_merge_refuses_batches_containing_insufficiency_artifacts(tmp_path):
    cfg = replace(
        make_config(tmp_path),
        splits=("build",),
        models=("random_forest_tuned", "catboost_regressor"),
        split_seeds=(101,),
        target_workers=2,
        min_workers_to_start=2,
        publish_canonical=False,
    )
    result_dir = cfg.output_dir / "results"
    result_dir.mkdir(parents=True)
    jobs = generate_jobs(cfg)
    (result_dir / f"{jobs[0].job_id}.json").write_text(
        json.dumps(one_job_payload(cfg, jobs[0].split, jobs[0].model, jobs[0].split_seed)),
        encoding="utf-8",
    )
    (result_dir / f"{jobs[1].job_id}.json").write_text(
        json.dumps(insufficiency_payload(cfg, jobs[1])),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="insufficiency artifacts"):
        merge_job_artifacts(cfg)
    assert not (cfg.output_dir / "merged.json").exists()
