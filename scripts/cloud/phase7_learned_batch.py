#!/usr/bin/env python
"""CLI wrapper for the Phase 7 learned-surrogate AWS batch."""

from __future__ import annotations

import argparse
import atexit
import dataclasses
import hashlib
import importlib.util
import io
import json
import secrets
import signal
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from starsector_optimizer.campaign import (
    check_ami_tags_against_manifest,
    check_authkey_syntax,
    check_aws_credentials,
)
from starsector_optimizer.cloud_provider import AWSProvider
from starsector_optimizer.game_manifest import GameManifest
from starsector_optimizer.phase7_learned_batch import (
    build_job_command,
    generate_jobs,
    load_batch_config,
    merge_job_artifacts,
    order_jobs_for_dispatch,
    run_live_batch,
    validate_batch_config,
)
from starsector_optimizer.phase7_matchup_data import SEEDLESS_SPLITS, SPLIT_SEED_EXCLUSIONS


SOURCE_VERSION_ARCNAME = ".phase7_source_version"

# local-smoke bounds: keep the end-to-end contract check fast by shrinking
# every expensive knob, not just rows/trials.
SMOKE_MAX_ROWS = 200
SMOKE_HPO_TRIALS = 1
SMOKE_INNER_CV_FOLDS = 2
SMOKE_BOOTSTRAP_RESAMPLES = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or inspect the Phase 7 learned-surrogate AWS batch."
    )
    parser.add_argument(
        "command",
        choices=("dry-run", "local-smoke", "launch", "status", "merge"),
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-jobs", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    return parser


def bundle_paths(cfg) -> tuple[Path, ...]:
    return (
        Path("src"),
        Path("scripts/analysis/phase7_learned_surrogate_experiment.py"),
        Path("scripts/analysis/phase7_baseline_surrogate.py"),
        Path("pyproject.toml"),
        Path("uv.lock"),
        cfg.source_db_path,
        Path("game/starsector/data"),
        Path("game/starsector/manifest.json"),
    )


def create_bundle(config_path: Path, output_dir: Path) -> tuple[Path, str]:
    cfg = load_batch_config(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = (output_dir / "bundle.tgz").resolve()
    source_version = current_source_version()
    with tarfile.open(bundle_path, "w:gz") as tar:
        for path in bundle_paths(cfg):
            tar.add(path)
        payload = json.dumps({"code_version": source_version}, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(SOURCE_VERSION_ARCNAME)
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    return bundle_path, digest


def current_source_version() -> str:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("refusing to create publishable Phase 7 bundle from a dirty worktree")
    return head


def _load_experiment_module():
    name = "phase7_learned_surrogate_experiment"
    if name in sys.modules:
        return sys.modules[name]
    script_path = Path(__file__).resolve().parents[1] / "analysis" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load experiment module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclass field-type resolution requires the module to be importable
    # by name (PEP 563 annotations look it up in sys.modules).
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def check_split_feasibility(cfg) -> None:
    """Dry-run every (split, seed) cell locally before provisioning a fleet.

    Split construction is a pure function of the local source DB, so a
    structurally infeasible cell (which the workers would report as a
    structured insufficiency artifact, blocking the merge) is a preflight
    failure, not a discovery to make with a running fleet.
    """
    experiment = _load_experiment_module()
    parser = experiment.build_parser()
    seen: set[tuple[str, int]] = set()
    configs = []
    # Values are the dynamically loaded experiment module's config dataclass,
    # invisible to static typing here.
    first_config_by_split: dict[str, Any] = {}
    for job in generate_jobs(cfg):
        cell = (job.split, job.split_seed)
        if cell in seen:
            continue
        seen.add(cell)
        # Parse the actual rendered job command through the experiment
        # script's own parser + config builder, so the preflight probes the
        # exact config a worker constructs — no hand-mirrored field list that
        # could drift. Split construction ignores the model, so one job per
        # (split, seed) cell covers all models. progress/allow-missing are
        # display / model-availability knobs with no effect on splits.
        command = build_job_command(cfg, job)
        script_index = next(index for index, token in enumerate(command) if token.endswith(".py"))
        args = parser.parse_args(command[script_index + 1 :])
        config = experiment.config_from_args(args)
        probe = dataclasses.replace(config, progress=False, allow_missing_optional_models=True)
        configs.append(probe)
        first_config_by_split.setdefault(job.split, probe)
    # Exclusions that removed a configured seed must still correspond to a
    # real realized-split collision (spec 31 stale-exclusion self-check).
    excluded_probes = [
        dataclasses.replace(first_config_by_split[split], split_seed=seed)
        for split in cfg.splits
        if split not in SEEDLESS_SPLITS and split in first_config_by_split
        for seed in sorted(SPLIT_SEED_EXCLUSIONS.get(split, frozenset()) & set(cfg.split_seeds))
    ]
    infeasible = experiment.split_feasibility_report(
        configs, excluded_probe_configs=excluded_probes
    )
    if infeasible:
        details = ", ".join(
            f"{cell['split']}(seed {cell['split_seed']}): {cell['status']}"
            + (f" [{cell['detail']}]" if cell.get("detail") else "")
            for cell in infeasible
        )
        raise RuntimeError(
            f"{len(infeasible)} structurally infeasible split cell(s); "
            f"adjust seeds/config before provisioning: {details}"
        )
    print(
        f"Split feasibility preflight passed ({len(seen)} cells, "
        f"{len(excluded_probes)} exclusion probe(s)).",
        flush=True,
    )


def check_amis_available(provider, cfg) -> None:
    for region in cfg.regions:
        ami_id = cfg.ami_ids_by_region[region]
        client = provider._client(region)
        response = client.describe_images(ImageIds=[ami_id])
        images = response.get("Images", [])
        if not images:
            raise RuntimeError(f"AMI {ami_id} not found in {region}")
        state = images[0].get("State")
        if state != "available":
            raise RuntimeError(
                f"AMI {ami_id} in {region} is {state!r}; wait until it is 'available'"
            )


def check_key_pairs_available(provider, cfg) -> None:
    for region in cfg.regions:
        client = provider._client(region)
        response = client.describe_key_pairs(
            Filters=[{"Name": "key-name", "Values": [cfg.ssh_key_name]}],
        )
        if not response.get("KeyPairs"):
            raise RuntimeError(f"EC2 key pair {cfg.ssh_key_name!r} does not exist in {region}")


def dry_run(config_path: Path) -> int:
    cfg = load_batch_config(config_path)
    validate_batch_config(cfg)
    # Dispatch order (longest-expected-first), so the operator preview
    # matches the actual lease queue.
    jobs = order_jobs_for_dispatch(generate_jobs(cfg))
    summary = {
        "name": cfg.name,
        "project_tag": cfg.project_tag,
        "regions": list(cfg.regions),
        "instance_types": list(cfg.instance_types),
        "target_workers": cfg.target_workers,
        "target_vcpu": cfg.target_workers * 16,
        "budget_usd": cfg.budget_usd,
        "feature_profile": cfg.feature_profile,
        "job_count": len(jobs),
        "jobs": [job.__dict__ | {"output_path": str(job.output_path)} for job in jobs],
        "teardown_command": f"scripts/cloud/teardown.sh {cfg.name}",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def local_smoke(config_path: Path, max_jobs: int) -> int:
    cfg = load_batch_config(config_path)
    jobs = generate_jobs(cfg)[:max_jobs]
    for job in jobs:
        command = build_job_command(cfg, job)
        command.extend(
            [
                "--max-rows",
                str(SMOKE_MAX_ROWS),
                "--hpo-trials",
                str(SMOKE_HPO_TRIALS),
                "--inner-cv-folds",
                str(SMOKE_INNER_CV_FOLDS),
                "--bootstrap-resamples",
                str(SMOKE_BOOTSTRAP_RESAMPLES),
            ]
        )
        print(" ".join(command), flush=True)
        subprocess.run(command, check=True)
    return 0


def merge(config_path: Path) -> int:
    cfg = load_batch_config(config_path)
    payload = merge_job_artifacts(cfg)
    canonical_output_path = str(cfg.canonical_output_path) if cfg.publish_canonical else None
    print(
        json.dumps(
            {
                "status": "merged",
                "result_count": payload["result_count"],
                "canonical_output_path": canonical_output_path,
                "batch_output_path": str(cfg.output_dir / "merged.json"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def status(config_path: Path) -> int:
    cfg = load_batch_config(config_path)
    status_path = cfg.output_dir / "status.json"
    if not status_path.exists():
        print(json.dumps({"status": "missing", "path": str(status_path)}))
        return 1
    print(status_path.read_text(encoding="utf-8"))
    return 0


def launch(config_path: Path, *, execute: bool) -> int:
    cfg = load_batch_config(config_path)
    if execute and not cfg.execution_enabled:
        raise RuntimeError(
            f"execution_enabled is false for {config_path}; refusing to provision AWS resources"
        )
    provider = AWSProvider(cfg.regions)
    check_aws_credentials()
    check_authkey_syntax(cfg.tailscale_authkey_secret)
    check_ami_tags_against_manifest(
        provider,
        cfg.ami_ids_by_region,
        GameManifest.load(),
        required_regions=cfg.regions,
    )
    check_amis_available(provider, cfg)
    check_key_pairs_available(provider, cfg)
    check_split_feasibility(cfg)

    cleanup_command = f"scripts/cloud/teardown.sh {cfg.name}"
    print(f"Cleanup command: {cleanup_command}", flush=True)
    if not execute:
        print("Launch preflight passed. Re-run with --execute to provision AWS resources.")
        return 0

    bundle_path, bundle_sha256 = create_bundle(config_path, cfg.output_dir)
    bearer_token = secrets.token_urlsafe(32)
    cleanup_done = False

    def cleanup() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        try:
            provider.terminate_fleet(fleet_name=cfg.fleet_name, project_tag=cfg.project_tag)
        except Exception as exc:
            print(f"cleanup warning: terminate_fleet failed: {exc}", file=sys.stderr)
        try:
            provider.terminate_all_tagged(cfg.project_tag)
        except Exception as exc:
            print(f"cleanup warning: terminate_all_tagged failed: {exc}", file=sys.stderr)

    def handle_signal(signum, frame) -> None:
        del frame
        raise KeyboardInterrupt(f"received signal {signum}")

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    def final_audit(config) -> None:
        subprocess.run(["scripts/cloud/final_audit.sh", config.project_tag], check=True)

    try:
        run_live_batch(
            cfg,
            provider=provider,
            bundle_path=bundle_path,
            bundle_sha256=bundle_sha256,
            bearer_token=bearer_token,
            final_audit_fn=final_audit,
        )
    finally:
        cleanup_done = True
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "dry-run":
            code = dry_run(args.config)
        elif args.command == "local-smoke":
            code = local_smoke(args.config, args.max_jobs)
        elif args.command == "merge":
            code = merge(args.config)
        elif args.command == "status":
            code = status(args.config)
        elif args.command == "launch":
            code = launch(args.config, execute=args.execute)
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(f"phase7 learned batch failed: {exc}", file=sys.stderr)
        raise
    raise SystemExit(code)


if __name__ == "__main__":
    main()
