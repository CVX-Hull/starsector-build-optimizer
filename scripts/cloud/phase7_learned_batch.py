#!/usr/bin/env python
"""CLI wrapper for the Phase 7 learned-surrogate AWS batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

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
    validate_batch_config,
)


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
        Path("pyproject.toml"),
        Path("uv.lock"),
        cfg.source_db_path,
        cfg.comparator_json_path,
        Path("game/starsector/manifest.json"),
    )


def create_bundle(config_path: Path, output_dir: Path) -> tuple[Path, str]:
    cfg = load_batch_config(config_path)
    bundle_path = output_dir / "bundle.tgz"
    output_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "w:gz") as tar:
        for path in bundle_paths(cfg):
            tar.add(path)
    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    return bundle_path, digest


def dry_run(config_path: Path) -> int:
    cfg = load_batch_config(config_path)
    validate_batch_config(cfg)
    jobs = generate_jobs(cfg)
    summary = {
        "name": cfg.name,
        "project_tag": cfg.project_tag,
        "regions": list(cfg.regions),
        "instance_types": list(cfg.instance_types),
        "target_workers": cfg.target_workers,
        "target_vcpu": cfg.target_workers * 16,
        "budget_usd": cfg.budget_usd,
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
        command.extend(["--max-rows", "200", "--hpo-trials", "1"])
        print(" ".join(command), flush=True)
        subprocess.run(command, check=True)
    return 0


def merge(config_path: Path) -> int:
    cfg = load_batch_config(config_path)
    payload = merge_job_artifacts(cfg)
    print(json.dumps({
        "status": "merged",
        "result_count": payload["result_count"],
        "canonical_output_path": str(cfg.canonical_output_path),
        "batch_output_path": str(cfg.output_dir / "merged.json"),
    }, indent=2, sort_keys=True))
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
    provider = AWSProvider(cfg.regions)
    check_aws_credentials()
    check_authkey_syntax(cfg.tailscale_authkey_secret)
    check_ami_tags_against_manifest(
        provider,
        cfg.ami_ids_by_region,
        GameManifest.load(),
        required_regions=cfg.regions,
    )

    cleanup = f"scripts/cloud/teardown.sh {cfg.name}"
    print(f"Cleanup command: {cleanup}", flush=True)
    if not execute:
        print("Launch preflight passed. Re-run with --execute to provision AWS resources.")
        return 0

    raise RuntimeError(
        "live launch is blocked until the control-plane serving loop is implemented and audited"
    )


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
