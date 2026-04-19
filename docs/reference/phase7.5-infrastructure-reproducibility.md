# Phase 7.5: Infrastructure & Reproducibility

Deferred until after Phase 7 ships. Designed to reduce the operational tax of running experiments — both for the current author and for anyone who later wants to reproduce a result — without changing the algorithmic surface.

## Problem

The Phase 6 Cloud Worker Federation infrastructure works, but the end-to-end "launch an experiment" workflow carries a lot of implicit state:

- **Eight+ shell scripts** under `scripts/cloud/` (`bake_image.sh`, `launch_campaign.sh`, `teardown.sh`, `final_audit.sh`, `probe.sh`, `status.sh`, `devenv-up.sh`, `devenv-down.sh`), each with its own arguments and invocation order. Remembering which script goes with which phase of the workflow is tribal knowledge.
- **AMI-bake-per-code-change**: Python source under `src/starsector_optimizer/` is baked into `/opt/starsector-optimizer/src/` by Packer. Every non-trivial code change to the worker or to shared dataclasses forces a ~8-minute AMI rebuild, a manual copy of the new AMI ID into every campaign YAML, a `CLAUDE.md` update, and a follow-up `aws ec2 copy-image` to us-east-2. This cycle dominates iteration wall-clock.
- **Environment-specific hardcoded values**: VPC ID and subnet ID in `scripts/cloud/packer/aws.pkr.hcl`, SSH keypair name `starsector-probe` in the campaign YAMLs, tailnet authkey assumptions. None of these are parameterized; a fork operates by find-and-replace.
- **Licensed game asset**: Starsector's 551 MB of game files + per-user `prefs.xml` license key are gitignored and cannot be redistributed. Anyone reproducing the pipeline must bring their own copy.
- **Manual Tailscale admin work**: tagOwners + grants + authkey generation all happen through the web admin panel. No declarative source of truth.
- **Ad-hoc pass/fail gates**: `final_audit.sh` returns 0/1 based on AWS resource leaks, but the actual "did the smoke succeed" check (ledger has ≥1 heartbeat, Optuna SQLite has 1 `TrialState.COMPLETE`, worker `load_avg_1min ∈ [3, 8]`) is performed by reading log tails.
- **Observability is log archaeology**: diagnosing an under-utilized pool required grepping `Progress: in-flight=N` lines out of INFO output rather than reading a metric dashboard.

The result: a successful run depends on ~16 tribal-knowledge steps before the first `launch_campaign.sh` invocation, and each iteration loop carries a ~10-minute fixed cost.

## Scope

A forked engineer, starting from a clean machine, should be able to reach "running my own Tier-2 smoke" by cloning the repo, placing their licensed game files into a declared directory, and issuing one top-level command. Every other piece of state — AWS infra, tailnet grants, worker image, dependency versions — should be declared as code or as a machine-checkable prerequisite.

**In scope:**

1. Unified CLI replacing the script zoo.
2. Worker containerization eliminating the AMI-bake-per-code-change cycle.
3. Terraform modules for static AWS infrastructure (VPC, subnet, IAM, SSH keypair, base SGs).
4. Tailscale Terraform provider for tagOwners + grants + authkeys.
5. Declarative campaign lifecycle (Flyte or equivalent) replacing the bash-EXIT-trap teardown chain.
6. Ray Tune as the execution substrate replacing `StagedEvaluator` + `CloudWorkerPool` + heartbeat/dispatch plumbing.
7. `repro check` preflight that machine-verifies every prerequisite with actionable remediation.
8. `REPRODUCE.md` top-level operator handbook with reference top-K outputs from a canonical run for statistical comparison.

**Out of scope (strict-reproducibility limits):**

- Bit-identical reproducibility. Combat simulation is stochastic in RNG-seed × floating-point-nondeterminism × AWS-spot-arrival-order × Optuna-TPE-worker-latency-dependence. We aim for ecological reproducibility: substantively similar trial distributions + top-K builds within statistical noise.
- Redistribution of licensed Starsector game files. Out-of-band via operator instructions.

## Proposed stack

### Execution + scheduling: Ray Tune

Ray Tune is the natural upgrade target. It subsumes:

- `StagedEvaluator` rung logic (native `ASHAScheduler`)
- `CloudWorkerPool` dispatch semaphore + `num_workers` (Ray actor pool)
- Worker heartbeat / health tracking (built-in)
- Spot interruption handling (Ray's native fault tolerance)
- Optuna integration via `OptunaSearch`

Estimated code deletion: ~2000 LOC across `evaluator_pool.py`, `cloud_worker_pool.py`, `worker_agent.py`, and the reliable-queue plumbing in `campaign.py`. The remaining bespoke code is the fitness function (combat simulation call → `CombatResult`), the scorer, and the Phase 5 deconfounding stack — none of which Ray touches.

Caveat: Starsector JVM + Xvfb startup costs still live on the worker. Ray does not remove them; it only removes the glue.

### Worker packaging: Docker + ECR

The worker runs in a container. `Dockerfile` + `pyproject.toml` + `uv.lock` are the source of truth for the worker binary. Image tag is the unit of versioning; pushing to ECR is the unit of deployment.

Impact:

- AMI becomes a stable boot environment only (Ubuntu + Docker + Ray client + Xvfb + Starsector game files). Code changes do not trigger AMI rebuild.
- Local development matches production byte-for-byte via `docker run <image-tag>`.
- Rolling back to a known-good build is `docker pull <prior-tag>`.

The game files remain baked into the AMI (legal constraint on redistribution). Everything else moves into the container.

### Infrastructure: Terraform + Tailscale Terraform provider

Terraform modules under `terraform/`:

- `terraform/aws/` — VPC, subnet, IAM role with spot + EC2 Fleet permissions, SSH keypair, security group base rules, S3 bucket for intermediate artifacts.
- `terraform/tailscale/` — tagOwners block, grants block, ephemeral tailnet authkey resource.

A fork runs `terraform apply -var-file=my.tfvars`. Terraform outputs get interpolated into the campaign YAMLs via a post-apply script. No hardcoded VPC/subnet/authkey anywhere in the repo.

### Workflow: Flyte (or Prefect 2) flow

The campaign lifecycle — devenv up → probe → smoke → sampler benchmark → prep campaign → teardown → final audit — becomes a declarative DAG in Python:

```python
@workflow
def tier2_smoke(workers_per_study: int = 3):
    image = build_and_push_image()
    infra = terraform_apply("aws", "tailscale")
    devenv = bring_up_devenv(infra.tailnet_key)
    try:
        probe = run_probe(image=image)
        smoke = run_campaign(yaml="smoke-campaign.yaml", image=image,
                             workers_per_study=workers_per_study)
        gate = smoke_gate_check(smoke)
    finally:
        final_audit()
        tear_down_devenv(devenv)
    return gate
```

Flyte handles retries, artifact lineage, state persistence across failures, dashboard visibility, and a cancel path that respects in-flight work. The bash `trap EXIT` teardown chain goes away.

### Declarative local dev: devcontainer + Nix flake

- `.devcontainer/devcontainer.json` boots a matching VS Code environment against the same base image the workers use.
- `flake.nix` produces bit-identical worker images across users. `nix build .#worker-image` is the deterministic alternative to Docker Hub tag drift.

### Preflight: `repro check`

```
$ uv run starsector-repro check
[OK]  aws sts get-caller-identity → 561543707907
[OK]  spot quota us-east-1 → 640 vCPU (need ≥24)
[OK]  game files at game/starsector/ → 551 MB, hash OK
[OK]  prefs.xml → present, license valid
[OK]  tailscale up → 100.64.x.y
[OK]  tailscale serve 6379 → 127.0.0.1:6379
[OK]  redis 127.0.0.1:6379 → PONG
[OK]  Java 26 → /usr/lib/jvm/java-26-openjdk
[FAIL] uv.lock hash → differs from committed lock
       → run: uv lock
[FAIL] packer validate aws.pkr.hcl → subnet-01745d2ce8253cc8b not in vpc-08c429b59b0728a0c
       → update terraform/aws/terraform.tfvars with your VPC ID; re-run terraform apply
```

Every failure line tells the operator exactly what to do. No guessing, no grep through CLAUDE.md.

### Gate: `smoke-gate.py`

Writes `gate.json` with per-criterion pass/fail:

```json
{
  "campaign_name": "smoke",
  "passed": true,
  "criteria": {
    "launch_exit_zero": true,
    "final_audit_clean": true,
    "ledger_heartbeats_at_least_one": {"passed": true, "count": 14},
    "optuna_trials_complete_at_least_one": {"passed": true, "count": 3},
    "worker_load_avg_in_range": {"passed": true, "per_worker": [5.2, 7.1, 4.8]}
  },
  "reference_comparison": {
    "top_5_jaccard_vs_reference_run": 0.6,
    "mean_composite_score_diff": 0.012
  }
}
```

Machine-readable, composable, diff-able across runs.

### Top-level command: `just`

```bash
$ just
Available recipes:
    check            # Run repro check (preflight)
    docker-build     # Build and push worker image to ECR
    infra-up         # Terraform apply AWS + Tailscale
    infra-down       # Terraform destroy
    devenv-up        # Rootless tailscaled + redis (local)
    devenv-down      # Stop dev env
    probe            # Tier-1 probe ($0.05, fleet lifecycle only)
    smoke TIER       # Tier-2.0 or Tier-2.5 smoke
    bench            # Sampler benchmark
    prep HULL        # Prep campaign for one hull
    teardown NAME    # Force-teardown a named campaign
    audit NAME       # final_audit for a named campaign
    reproduce        # End-to-end canonical run for REPRODUCE.md validation
```

One entry point, self-documenting via `just --help`. Recipes compose Flyte workflows + shell + Terraform.

## Phased delivery

The 8 deliverables split into three tiers by dependency + effort:

### Tier A — "smoother daily driver" (1–2 weeks, preserves current architecture)

1. **`just` CLI + recipes wrapping existing scripts.** Solves command discovery without changing execution.
2. **Worker Dockerfile + ECR push pipeline.** Eliminates the 80% of AMI rebakes triggered by Python-only changes.
3. **`repro check` command.** One-shot prerequisite verification.
4. **Terraform module for static AWS infra** (VPC, subnet, SG base, SSH key, IAM). Outputs get interpolated into YAMLs.

At this point a fork can bootstrap against their own AWS account without grep-through-CLAUDE.md.

### Tier B — "declarative workflow" (2–4 weeks, touches the orchestrator)

5. **Flyte or Prefect flow** replacing `launch_campaign.sh` + `bash trap EXIT`. Campaign lifecycle becomes a Python DAG.
6. **Tailscale Terraform provider** for tagOwners + grants + authkey. Tailnet setup becomes `terraform apply`.
7. **`smoke-gate.py` with `gate.json` output.** Structured pass/fail replaces log archaeology.

### Tier C — "execution substrate migration" (4–8 weeks, rewrites the pool)

8. **Ray Tune adoption.** Replaces `StagedEvaluator` + `CloudWorkerPool` + `worker_agent.py` main loop + reliable-queue plumbing. ~2000 LOC deletion. Best pursued in parallel with Phase 7 SAASBO (which also needs an execution substrate), possibly as Phase 7's delivery vehicle rather than a separate phase.

### Tier D — "canonical reproducibility" (1–2 weeks, capstone)

9. **Nix flake for worker image.** Bit-identical builds across users.
10. **`REPRODUCE.md` + canonical reference run.** Publishes top-K elites + their composite score distributions for a named hull × regime. Future runs diff against this for ecological-reproducibility validation.

Tiers A + B alone land "fork-and-go in 30 minutes from a fresh machine." Tier C is optional but is the point at which the line "the operational stack looks like any other ML experiment platform" becomes defensible. Tier D is the capstone that publishes actual numbers.

## Success criteria

- A forked engineer on an unfamiliar laptop reaches a passing Tier-2 smoke in under 30 minutes of human time, given only the repo URL and a pointer to a legally obtained Starsector copy.
- Pure-Python changes to worker code trigger zero AMI rebuilds.
- Teardown is guaranteed on every exit path without operator attention (including SIGKILL of the orchestrator, IDE crash, network partition).
- Canonical reference run's top-10 Jaccard similarity vs. a fresh run exceeds 0.5, and mean composite score diff stays within 0.02, across at least three independent trials.
- No script in `scripts/cloud/` other than the thin wrappers invoked by `just`.

## Rejected alternatives

- **Kubernetes + KubeRay**: too heavyweight for the single-tenant hobby-scale workload; Ray Tune on raw EC2 Fleet is lighter and gives the same API surface. Revisit at ≥$10k/mo spend.
- **Airflow / Dagster**: designed for DAG-of-different-tasks scheduling; Flyte is a better fit because the primitive here is "same task repeated with different inputs."
- **W&B Launch / SageMaker Pipelines / Azure ML Pipelines**: managed offerings lock in to one cloud; the project deliberately targets AWS + future Hetzner so vendor neutrality at the workflow layer matters.
- **Snakemake / Nextflow**: bioinformatics-flavored; fine technically but the community's idioms don't match.
- **In-repo bash refactor into one big `run.sh`**: papers over the tribal knowledge without moving the reproducibility bar.
- **Just Nix (no Docker)**: Nix's learning curve + community-small-but-expert dynamic means slower onboarding for collaborators vs. Docker. Ship Docker first, add Nix flake as a parallel supported path.

## References

- Ray Tune Optuna integration: [docs.ray.io/en/latest/tune/examples/optuna_example.html](https://docs.ray.io/en/latest/tune/examples/optuna_example.html)
- Flyte: [flyte.org](https://flyte.org)
- Tailscale Terraform provider: [registry.terraform.io/providers/tailscale/tailscale](https://registry.terraform.io/providers/tailscale/tailscale)
- Ephemeral Ray clusters on AWS: [docs.ray.io/en/latest/cluster/vms/getting-started.html](https://docs.ray.io/en/latest/cluster/vms/getting-started.html)
- Nix + Python dev environments: [nix.dev/tutorials/first-steps/dev-environment.html](https://nix.dev/tutorials/first-steps/dev-environment.html)
- The `just` command runner: [just.systems](https://just.systems)
