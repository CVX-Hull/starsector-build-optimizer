---
plan_type: implementation
status: approved
created: 2026-05-12
approved: 2026-05-12
implemented: null
owner: agent
related_docs:
  - AGENTS.md
  - docs/CONVENTIONS.md
  - docs/specs/22-cloud-deployment.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md
  - .claude/plans/active/2026-05-12-phase7-learned-baseline-experiment.md
implementation_commit: null
post_impl_audit: null
superseded_by: null
---

# Phase 7 AWS Learned-Surrogate Batch

## Goal

Run the Phase 7 learned-surrogate full experiment as a bounded AWS fan-out
batch: one independent job per canonical `(split, model)` pair, then validate
and merge the 15 job artifacts into the canonical full-run JSON expected by the
active learned-baseline plan.

Current amendment after the first failed live attempt: do not relaunch the
15-worker full matrix until a smaller AWS smoke matrix has completed with
validated job artifacts. The smoke path uses the same worker code, bundle,
AMI, dependency extra, provenance validation, budget ledger, and teardown
logic, but only a configured subset of splits and model families.

## Context And Source Docs

- Root workflow: `AGENTS.md`.
- Documentation rules: `docs/CONVENTIONS.md`.
- Cloud resource lifecycle and security: `docs/specs/22-cloud-deployment.md`.
- Learned-surrogate data, split, leakage, and artifact contract:
  `docs/specs/31-phase7-matchup-data.md`.
- Current learned-baseline active plan:
  `.claude/plans/active/2026-05-12-phase7-learned-baseline-experiment.md`.
- Draft learned-surrogate report:
  `docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md`.

## Scope

- Add an importable AWS batch module for Phase 7 learned-surrogate jobs plus a
  thin CLI wrapper.
- Fan out the existing learned runner across 15 independent jobs:
  5 canonical splits x 3 model families.
- Support explicit smoke/debug subsets through `splits` and `models` config
  fields, with `target_workers = len(splits) * len(models)`.
- Use the existing AWSProvider provisioning/teardown pattern without changing
  the combat-worker queue.
- Use a local Tailscale-reachable authenticated HTTP control plane for bundle
  download, job leases, events, and result upload.
- Add dry-run, local-smoke, launch, status, and merge commands.
- Add a budget ledger, watchdog checks, signal-safe teardown, and final audit
  instructions before any launch path can proceed.
- Add tests for job manifests, config validation, budget guards, authenticated
  routes, lease/retry behavior, user-data rendering, merge validation, and
  teardown calls.

## Out Of Scope

- No Starsector simulation worker queue changes.
- No S3, AWS Batch, ECS, or IAM instance-profile implementation in this plan.
- No optimizer integration.
- No external experiment tracker.
- No AMI rebake unless smoke proves boot-time dependency setup is too slow or
  unreliable. The first implementation uses the existing `surrogate` optional
  dependency set and records boot timing.

## Critical Files

- `src/starsector_optimizer/phase7_learned_batch.py`
- `scripts/cloud/phase7_learned_batch.py`
- `scripts/cloud/launch_phase7_learned_batch.sh`
- `tests/test_phase7_learned_batch.py`
- `examples/phase7-learned-batch.yaml`
- `docs/specs/22-cloud-deployment.md`
- `docs/specs/31-phase7-matchup-data.md`
- `docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md`
- `.claude/plans/active/2026-05-12-phase7-aws-learned-batch.md`
- `.claude/plans/active/2026-05-12-phase7-learned-baseline-experiment.md`

## Public Concepts And Canonical Owners

- Spec 22 owns AWS lifecycle, instance security, AMI tag preflight, teardown,
  cost guardrails, control-plane authentication, and final-audit requirements.
- Spec 31 owns the learned-surrogate experiment contract, job matrix,
  per-job artifact schema, leakage requirements, merge schema, and canonical
  full-run artifact semantics.
- The batch module owns distributed execution, lease/retry mechanics,
  authenticated local control-plane behavior, and merge validation.
- Existing AWSProvider owns EC2 fleet provisioning and targeted fleet teardown.
- The learned-surrogate report owns dated empirical results and interpretation.
- This plan owns temporary implementation sequencing until archived.

## AWS Quota And Instance Selection

Checked on 2026-05-12 with `AWS_PROFILE=starsector`.

- `L-34B43A08` Spot quota:
  - `us-east-1`: 640 standard spot vCPU.
  - `us-east-2`: 640 standard spot vCPU.
- Current tagged project usage: no active Phase 7 batch fleet found.
- Current unrelated active EC2 usage: one `t3.micro` and one `t3.medium` in
  `us-east-1`; none in `us-east-2`.
- Spot availability check found `c7i.4xlarge`, `c7a.4xlarge`,
  `c7i.2xlarge`, and `c7a.2xlarge` offered in both target regions.
- Spot price sample favored `us-east-2`, especially `c7i.4xlarge`
  at roughly `$0.20/hour`; later smoke/full configs used `us-east-1` because
  the available renewable-lease AMI was in that region.
- Cloud-run lesson: `AWSProvider.provision_fleet` currently floors
  `target_workers // len(regions)`, so a 15-worker batch across two regions
  would request only 14 workers. The default full-run config therefore uses
  one region. Multi-region fallback requires either provider remainder
  distribution or a target worker count divisible by the region count.

Final checked-in config policy:

- `regions: [us-east-1]`.
- `instance_types: [c7i.4xlarge, c7a.4xlarge, c6i.4xlarge, c6a.4xlarge,
  m7i.4xlarge, m7a.4xlarge]`.
- `target_workers: 15`.
- `min_workers_to_start: 15`.
- `execution_enabled: false`.
- Per-worker logical-core plan: `hpo_jobs: 4`, `model_thread_count: 4`.
- Target vCPU draw at full size: 240 vCPU, well below either region's 640 vCPU
  spot quota.
- Default hard budget: `$20.00`.
- Default max lifetime: 2 hours.
- Ledger heartbeat: 60 seconds.
- Partial fleet start is intentionally disabled until a later partial-batch
  merge/resume policy exists.

If `c7i/c7a.4xlarge` capacity is unavailable, do not silently fall back to
2xlarge instances with the 16-core parallelism settings. Either lower
parallelism in config or relaunch with an explicit 2xlarge-specific config.

## Implementation Status

Completed in the current implementation pass:

- approved baseline-plan amendment;
- spec 22 one-shot batch safety contract;
- spec 31 learned AWS batch artifact contract;
- importable batch config, job matrix, lease state, authenticated routes,
  UserData renderer, budget-ledger helper, and merge validator;
- live AWS launch lifecycle code for control-plane serving, full-fleet
  provisioning, status/event/result persistence, budget monitoring with
  one-shot warn thresholds, strict artifact validation, merge gating, layered
  teardown, and final audit;
- worker UserData now loops leases until the orchestrator tears down the batch,
  posts pre-lease and per-job events, and treats duplicate result uploads as
  idempotent;
- thin CLI with `dry-run`, `local-smoke`, `status`, `merge`, and fail-closed
  `launch --execute`;
- focused tests for the above;
- stale interrupted canonical artifact quarantined under
  `data/phase7/interrupted/`.
- first live AWS failure root-caused from EC2 console output:
  `phase7_learned_surrogate_experiment.py` imports
  `phase7_baseline_surrogate.py`, but the bundle originally omitted the helper
  script, causing every worker to fail after `experiment_start` with
  `FileNotFoundError`;
- bundle manifest now includes the baseline helper script;
- worker UserData now captures the experiment command's stdout/stderr to a
  per-job log and posts a bounded log tail plus exit code on
  `experiment_failed`;
- `examples/phase7-learned-batch-smoke.yaml` defines a 2-worker build-split
  smoke matrix before another full run.
- first 2-worker smoke on the rebaked AMI partially succeeded: CatBoost
  completed, uploaded, and validated; the random-forest worker was
  service-terminated while its job remained leased, then the remaining worker
  could not retry until lease expiry;
- live-batch control now requeues leases whose worker is no longer active and
  provisions replacement workers for pending work.
- second 2-worker smoke on the replacement-aware AMI confirmed clean teardown
  and another validated CatBoost artifact, but both Spot instances were
  service-terminated at the same timestamp; RF had been re-leased just before
  the second interruption, so the hardcoded two-attempt policy marked it failed
  without a model-level failure event;
- retry budget is now a config field (`max_job_attempts`) and smoke/full
  configs use six attempts so Spot worker loss does not masquerade as a
  deterministic experiment failure.
- third smoke revealed that a fixed lease TTL is still not principled for ML
  jobs: RF was re-leased while the original worker was still active. The smoke
  was stopped and audited clean. The protocol now uses renewable leases, so
  job duration is bounded by model completion, batch lifetime, and budget
  guardrails rather than by a fixed lease TTL.
- renewable-lease smoke on AMI `ami-07ce0d6ab863c85c5` completed both
  build-split jobs on attempt 1, merged the smoke artifact, and final-audited
  clean. RF renewed beyond the old fixed-lease failure point without duplicate
  assignment.

Closeout decision after the local full run:

- do not spend a 15-worker AWS run to duplicate
  `data/phase7/learned_surrogate_full_local_2026-05-12.json`;
- the AWS path is now infrastructure validation only, not the evidence source
  for the Phase 7 modeling decision;
- checked-in AWS configs are disabled with `execution_enabled: false`;
- no AWS artifact may publish a canonical full-run path unless
  `publish_canonical: true` and the full 15-job canonical matrix validates.

## Closed AWS Execution Path

Future AWS execution requires a new explicit reproducibility or infrastructure
validation goal. The operator must re-enable `execution_enabled`, run preflight,
and re-confirm budget/cleanup approvals before provisioning resources. The
previous "launch full matrix next" step is superseded by the completed local
full run and the renewable-lease smoke.

## Baseline Plan Interaction

The existing learned-baseline plan still owns the learned runner, smoke report,
and canonical report text. This AWS plan no longer satisfies that plan's
full-run artifact requirement; the local full artifact is the evidence source
for this report. Future AWS artifacts may be promoted only when all of the
following are true:

- all 15 canonical jobs complete successfully;
- every per-job artifact validates against the same source DB, comparator JSON,
  feature schema version, split contract, top-k set, seed/fraction settings,
  dependency set, source bundle SHA256, and leakage checklist;
- merge writes a batch-internal `merged.json`;
- `publish_canonical: true` is explicitly set;
- merge atomically promotes the canonical artifact to
  `data/phase7/learned_surrogate_full_2026-05-12.json`.

Partial batches may write only `.partial` or batch-internal artifacts; they
must not overwrite the canonical full-run path.

## Implementation Sequence

1. Amend the active learned-baseline plan so the AWS merged artifact is an
   approved full-run execution path instead of a competing plan.
2. Update spec 22 with the general batch-cloud requirements:
   - unique project tag and fleet name;
   - pre-launch teardown command printed first;
   - AWS credentials, authkey syntax, and AMI tag preflight;
   - authenticated local control-plane rule;
   - budget ledger and hard cutoff;
   - signal, exception, and `atexit` targeted teardown;
   - final audit before reporting completion.
3. Update spec 31 with the Phase 7 learned-batch execution contract:
   - 15-job matrix;
   - per-job command and output schema;
   - allowed split/model argument matrix;
   - merge validation and canonical promotion rules;
   - leakage checklist requirements.
4. Add `examples/phase7-learned-batch.yaml` with the default AWS policy above,
   source DB and comparator paths, budget cap, watchdog settings, and output
   directory:
   `data/phase7/learned_surrogate_batch_2026-05-12/`.
5. Add tests before implementation for:
   - generated 15-job manifest;
   - config validation for quota, region/AMI coverage, budget, output paths,
     parallelism, and canonical split/model sets;
   - bearer-token auth for every control-plane route;
   - lease exclusivity, expiry, retry cap, wrong job id, duplicate result, and
     completed-job immutability;
   - user-data security content;
   - merge of completed/partial/duplicate/failed job outputs;
   - budget ledger threshold and hard cutoff behavior;
   - teardown calls on normal completion and failure.
6. Implement `src/starsector_optimizer/phase7_learned_batch.py`:
   - frozen config dataclasses and YAML loader;
   - pure job-manifest generation;
   - control-plane app/state helpers;
   - user-data renderer;
   - budget ledger helpers;
   - merge validator;
   - AWS orchestration helpers.
7. Implement `scripts/cloud/phase7_learned_batch.py` as a thin CLI wrapper:
   - `dry-run`;
   - `local-smoke`;
   - `launch`;
   - `status`;
   - `merge`.
8. Implement worker user-data:
   - `set -euo pipefail` and `umask 077`;
   - write Tailscale authkey to a temp file, authenticate without SSH, then
     shred/remove the file;
   - never log secrets;
   - capture instance ID through IMDSv2;
   - disable baked `starsector-worker.service` before work;
   - download bundle from authenticated `/bundle`;
   - run `uv sync --frozen --extra surrogate`;
   - lease jobs in a loop until the orchestrator tears down the batch;
   - run `scripts/analysis/phase7_learned_surrogate_experiment.py` with only
     the canonical job's split/model/HPO/top-k/source/comparator arguments;
   - upload result/events to authenticated endpoints;
   - leave process lifetime bounded by the configured batch lifetime and
     orchestrator teardown.
9. Implement launch teardown:
   - use one batch-owned fleet with a unique project tag;
   - use `try/finally terminate_fleet`;
   - register `atexit` and signal handlers that terminate only resources with
     the batch project tag;
   - print the exact cleanup command before launching instances;
   - write `ledger.jsonl` and `status.json` in the batch output directory.
10. Implement merge:
   - read per-job artifacts from
     `data/phase7/learned_surrogate_batch_2026-05-12/results/`;
   - refuse to publish on missing/failed/duplicate/inconsistent artifacts;
   - emit batch-internal `merged.json`;
   - atomically promote only a fully valid merged artifact to
     `data/phase7/learned_surrogate_full_2026-05-12.json`.
11. Add report update:
   - document AWS batch method, quota/instance policy, and smoke/full status;
   - keep empirical claims draft until full merged output exists.
12. Run verification and post-implementation audit with fresh-eye sub-agents.

## Tests And Mechanical Gates

- `uv run pytest tests/test_phase7_learned_batch.py -q`
- `uv run pytest tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -q`
- `TAILSCALE_AUTHKEY=<authkey-or-placeholder> STARSECTOR_WORKSTATION_TAILNET_IP=<tailnet-ip-or-placeholder> uv run python scripts/cloud/phase7_learned_batch.py dry-run --config examples/phase7-learned-batch.yaml`
- `TAILSCALE_AUTHKEY=<authkey-or-placeholder> STARSECTOR_WORKSTATION_TAILNET_IP=<tailnet-ip-or-placeholder> uv run python scripts/cloud/phase7_learned_batch.py local-smoke --config examples/phase7-learned-batch.yaml --max-jobs 2`
- `bash -n scripts/cloud/launch_phase7_learned_batch.sh`
- `uv run python scripts/validate_active_plans.py`
- `git diff --check`
- Full suite before final commit: `uv run pytest tests/ -v`

## Deferred Items

- S3/object-storage transfer is deferred because the current AMI has no
  instance profile and the 37 MB DB is small enough for a Tailscale local
  bundle server.
- AMI rebake is deferred unless smoke proves boot-time dependency setup is too
  slow or unreliable. If rebake becomes necessary, tag the AMI with the
  manifest hash, mod jar hash, source bundle expectation, dependency lock hash,
  and creation date before launch.
- AWS Batch/ECS is deferred because existing repo infrastructure already owns
  EC2 fleet provisioning/teardown via AWSProvider.
- 2xlarge fallback is deferred until a separate config lowers per-worker
  `hpo_jobs * model_thread_count`.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-12
- Findings:
  - Missing budget ledger, hard cap, and final audit.
  - Teardown sequence under-specified for failure paths.
  - AMI tag and launch preflight missing.
  - Control-plane authentication missing.
  - Phase 7 contract and cloud-lifecycle ownership were mixed together.
  - Baseline active plan and AWS batch plan could both claim full-run
    authority.
  - A new `phase7-ml` optional extra would duplicate the existing `surrogate`
    dependency set.
- Dispositions:
  - Added budget, watchdog, ledger, and final-audit requirements.
  - Added unique project tag, fleet-level ownership, signal/`atexit`, and
    targeted teardown requirements.
  - Added credentials, authkey, AMI tag, and region-coverage preflights.
  - Added bearer-token requirements for all control-plane endpoints.
  - Split spec 22 and spec 31 ownership.
  - Added baseline-plan amendment and canonical-promotion rules.
  - Reused the existing `surrogate` dependency set.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via repo workflow.
- Reviewed at: 2026-05-12
- Agents:
  - Maxwell: plan consistency and lifecycle review.
  - Hooke: spec and documentation boundary review.
  - Dalton: design invariant and implementation-risk review.
- Findings:
  - Launch safety needed explicit budget, final audit, and cleanup controls.
  - Custom user-data must preserve existing cloud-worker security invariants.
  - Merge schema needed stronger provenance validation and non-overwrite rules.
  - Tests needed auth, wrong-route, duplicate-result, stale-lease, signal, and
    budget failure cases.
  - Full-run ownership needed an amendment in the already-approved baseline
    plan.
- Dispositions:
  - Incorporated all required safety, provenance, and test conditions into this
    approved plan.
  - Baseline-plan amendment is now the first implementation step.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Post-Implementation Audit Requirements

- Verify no stale local learned-surrogate process is left running.
- Verify every AWS code path has targeted teardown in `finally` and registered
  signal/`atexit` cleanup.
- Verify the cleanup command is printed before any fleet launch.
- Verify all control-plane endpoints reject missing or wrong bearer tokens.
- Verify batch output cannot overwrite canonical full-run output unless every
  canonical job validates.
- Verify failed/expired leases are visible in status output.
- Verify budget ledger hard cutoff terminates the batch-owned fleet.
- Verify report remains draft until full merged output exists.
- Run fresh-eye audit sub-agents before commit.

## Retirement Checklist

- Frontmatter `status` is changed to `implemented`.
- Frontmatter `implemented` is set to the completion date.
- Frontmatter `implementation_commit` is set to the final commit hash or
  `not_committed`.
- Frontmatter `post_impl_audit` is set to `passed` or linked to an audit
  record.
- Plan is moved to `.claude/plans/archive/2026/`.
