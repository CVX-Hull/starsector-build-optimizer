---
name: Cloud Worker Operations
description: SOP for launching, monitoring, and tearing down multi-worker cloud campaigns for Starsector optimization. Invoke when the user asks to run optimization in the cloud, spin up many workers, start a campaign, spend a budget on experiments, or debug cloud-worker issues.
disable-model-invocation: true
---

# Cloud Worker Operations SOP

Use this skill when the user asks you to run or debug a cloud campaign — anything involving multiple Starsector workers outside the local workstation. Built on the Phase 6 Cloud Worker Federation design (`docs/reference/phase6-cloud-worker-federation.md`). The empirical validation that proves this path works is in `experiments/cloud-benchmark-2026-04-18/`.

## The three rules of money

1. **Every launch sets a budget ceiling.** If you don't have a `budget_usd` figure, STOP and ask the user for one. Default "launch and see what happens" is a $200/day runaway pattern.
2. **Every launch prints the teardown command as its first line of output.** Operator must be able to copy-paste to nuke resources in case the orchestrator dies.
3. **Final-audit runs at the end of every session.** After ANY cloud work, verify zero tagged resources remain. Don't skip this step.

## Which provider

| Situation | Pick |
|---|---|
| Default for any campaign | **Hetzner CCX33 Ashburn** — existing `scripts/cloud/*.sh` work out of the box; no spot preemption to handle; $0.13/hr |
| Hetzner out of capacity OR need multiple regions | AWS c7a.2xlarge us-west-2 spot (`scripts/cloud/aws/*.sh`); $0.15/hr spot, ~3% preemption |
| $500+ budget dominated by cost | GCP n2d-standard-8 spot; $0.07/hr but requires quota bump to 240 vCPU first |
| GPU cloud | **Never.** CPU is 2.4× local per-instance after the XRandR fix; GPU adds no throughput and costs more. If a user asks for GPU cloud, cite `experiments/cloud-benchmark-2026-04-18/RESULTS.md` and push back. |
| ARM / Graviton | **Never.** LWJGL 2.9.3 is x86_64-only. |

## Preflight checklist (before launching ANY cloud worker)

Run all of these. Failure on any one = STOP.

1. **Budget is set**: user has given a `budget_usd` figure AND that figure is written into the campaign YAML's `budget_usd` field.
2. **No orphaned resources**:
   ```bash
   hcloud server list             # should be empty or only expected servers
   aws ec2 describe-instances --region us-east-1 \
     --filters 'Name=tag:Project,Values=starsector-*' \
     --query 'Reservations[].Instances[?State.Name!=`terminated`].InstanceId' \
     --output text     # should be empty
   ```
3. **Image is baked** (only for production runs, not smoke tests): `hcloud image list --type=snapshot` or `aws ec2 describe-images --owners self`. If not baked, user can still run but accepts ~5 min/worker bootstrap.
4. **Provider credentials are alive**:
   ```bash
   hcloud context list                             # shows active context starred
   aws sts get-caller-identity                     # returns UserId
   ```
5. **Game prefs file exists** at `~/.java/.userPrefs/com/fs/starfarer/prefs.xml`. Without it, workers can't launch Starsector (unactivated game).
6. **SSH key exists** at `~/.ssh/starsector-opt` (private) and `~/.ssh/starsector-opt.pub` (public).
7. **LWJGL XRandR fix is in the code** — grep `src/starsector_optimizer/instance_manager.py` for `Warm XRandR`. If missing, CLOUD WORKERS WILL CRASH. Apply the fix before proceeding.
8. **`x11-xserver-utils` is in cloud-init** — grep `scripts/cloud/cloud-init.yaml` for `x11-xserver-utils`. Required for the XRandR warmup to actually execute.

## Launching a campaign

### For a smoke test or one-off ($10-20 budget)

Use the existing per-provider scripts directly — no campaign manager needed yet:
```bash
# Hetzner single-worker smoke
./scripts/cloud/deploy.sh 1 ccx33 ash,hil,fsn1,nbg1   # ~3-5 min
./scripts/cloud/run.sh <hull_id> 0 --sim-budget 20
./scripts/cloud/collect.sh 1
./scripts/cloud/teardown.sh 1                         # ALWAYS finish with teardown

# AWS single-worker smoke
./scripts/cloud/aws/deploy.sh c7i.2xlarge
./scripts/cloud/aws/run_benchmark.sh 2 20 <hull_id>
./scripts/cloud/aws/teardown.sh                       # cleans all tagged resources
```

**Location fallback (Hetzner)**: `deploy.sh` accepts a comma-separated list of locations and tries each in order until one has CCX33 capacity. Ashburn (`ash`) is geographically closest from NC but is frequently out of CCX33 stock — **put `hil` (Hillsboro) second**; it tends to have capacity when Ashburn doesn't. Observed 2026-04-18 during Phase 5E validation: `ash` returned `resource_unavailable` immediately while `hil` deployed in ~3 min.

**Post-deploy verification happens automatically**: `deploy.sh` now runs a smoke import after `uv sync` to confirm the Phase 5D + 5E pipeline is loadable on each worker. If verification fails, the script exits non-zero and tells you to run teardown + retry. The pre-2026-04-18 script would print "All machines deployed" despite downstream failures — that silent-success mode is gone.

### Iterating on code after deploy (`sync-code.sh`)

When you're iterating locally and want to push only code changes to existing workers — without recreating them — use `sync-code.sh`:
```bash
./scripts/cloud/sync-code.sh            # rsync src/ tests/ scripts/ to ALL workers
./scripts/cloud/sync-code.sh 0          # just sim-worker-0
./scripts/cloud/sync-code.sh all true   # force a uv sync too (deps drifted)
```
By default it detects `pyproject.toml` / `uv.lock` drift via `git diff` and runs `uv sync` when needed. Always finishes with a post-sync import smoke so broken syntax surfaces before the next long-running experiment launches. This is the canonical path for the "deploy once → iterate locally → push code only" workflow; during Phase 5E validation I had to hand-roll `rsync` because this script didn't exist yet.

### For a real campaign ($50-$1000 budget)

Once Phase 6 ships (campaign.py + federation/), the workflow is:
```bash
# 1. Define campaign
$EDITOR ~/starsector-campaigns/phase5f-val/campaign.yaml

# 2. Preflight + launch (prints teardown command as line 1)
./scripts/cloud/federation/launch_campaign.sh ~/starsector-campaigns/phase5f-val

# 3. Monitor
./scripts/cloud/federation/status.sh ~/starsector-campaigns/phase5f-val

# 4. ON COMPLETION OR ERROR — teardown
./scripts/cloud/federation/teardown.sh ~/starsector-campaigns/phase5f-val

# 5. Final audit — MANDATORY
./scripts/cloud/federation/final_audit.sh
```

Until Phase 6 ships, run campaigns by manually launching N workers in a loop and assigning each one a distinct `(hull, regime, seed)` tuple via `scripts/cloud/run.sh`. Keep N ≤ 24 per study (TPE saturation ceiling).

### Study-per-(hull,regime) sizing cheatsheet

- **≤24 workers per study**: TPE default. Efficient, precise, recommended.
- **24–100 workers per study**: switch sampler to `CatCMAwM` (`--sampler=catcma` in `run_optimizer.py`). Native-parallel CMA-ES; no TPE imputation penalty.
- **Hybrid (random→CMA→TPE)**: for per-study budgets >1000 trials where you want both breadth and precision. Campaign manager schedules sampler changes.

Per-study budget sweet spot: **500-1500 trials**. Above 1500, diminishing returns — enable plateau auto-terminate.

## Monitoring during runs

Every 15-30 min while a campaign is live:

1. **Cost ledger**: `tail ~/starsector-campaigns/<campaign>/ledger.jsonl | python -c 'import json,sys; total=sum(json.loads(l)["delta_usd"] for l in sys.stdin); print(f"${total:.2f}")'`. Cross-reference against `budget_usd`.
2. **Worker liveness**: `hcloud server list` / `aws ec2 describe-instances --filters 'Name=tag:Project,Values=<campaign-tag>'`. Dead workers should be auto-replaced; persistent gap = bug.
3. **Per-study progress**: `./scripts/cloud/federation/status.sh <campaign-dir>` shows best fitness, trial count, plateau signal per study.
4. **Redis queue depth** (per study): `redis-cli LLEN matchup-queue-<study_id>`. If growing unbounded, workers can't keep up; scale up or reduce per-worker lifespan.
5. **Stuck studies**: any study with no trial progress for >15 min = worker crash loop. Inspect worker logs; typically the XRandR or heartbeat issue.

## Failure recovery recipes

### Workers crashing on startup with `ArrayIndexOutOfBoundsException: Index 0`

LWJGL XRandR bug. Check:
1. `instance_manager.py::_start_xvfb` has the `xrandr --query` warmup call. If not, apply the fix from `docs/reference/phase6-cloud-worker-federation.md` §Non-obvious-notes.
2. `x11-xserver-utils` is installed on the worker (in cloud-init or baked image). `ssh worker 'which xrandr'` should return a path.
3. If baked image predates the fix, rebuild it.

### Campaign blew past budget

Something went wrong with the hard-cap mechanism OR workers didn't receive termination signal.

1. **IMMEDIATELY**: `./scripts/cloud/teardown.sh N` (Hetzner) and `./scripts/cloud/aws/teardown.sh` (AWS). Blunt-force stop bleeding.
2. Check `ledger.jsonl` for the crossing event — did it fire the warning threshold? If not, the ledger-write path is broken.
3. Check each cloud provider for orphans via `hcloud server list` and `aws ec2 describe-instances --filters 'Name=tag:Project,Values=starsector-*'`.
4. Root-cause the failure before next launch. Candidate causes: (a) ledger missed a worker's hourly tick due to clock skew, (b) auto-terminate threshold misconfigured, (c) worker `max_lifetime_hours` not honored.

### Spot preemption cascade (AWS only)

If >30% of workers are being preempted in a short window:
1. Check AWS Spot interruption rate for the AZ: `aws ec2 describe-spot-placement-scores --region us-east-1 --single-availability-zone`.
2. If score <7 for the target AZ, pause the campaign, then relaunch after changing to `us-west-2` or a different instance family mix (`c7a.2xlarge` + `c7i.2xlarge` + `c7a.4xlarge` + `c7i.4xlarge` in the Fleet template).
3. Consider switching to Hetzner for the remainder of this campaign — no preemption there.

### Worker output never comes back

Ssh into the worker directly:
```bash
ssh -i ~/.ssh/starsector-opt root@<IP>
tail -100 /opt/optimizer/run.log
ps -C java -o pid,etime,%cpu,cmd
```

Common causes: Xvfb died, Starsector JVM hung, heartbeat file stale. `pkill -9 java; pkill -9 Xvfb` and let the instance_manager restart logic kick in; if 3 restarts fail, treat as broken worker and rebuild/replace.

### SSH commands hang or return exit 255

Every ssh invocation from orchestration code must close stdin. Without it, ssh can inherit a live stdin from the parent shell and either hang indefinitely or exit 255 under background-mode execution. Always append `</dev/null`:
```bash
ssh $SSH_OPTS root@"$IP" 'pkill -9 java; echo done' </dev/null
```
All scripts in `scripts/cloud/` follow this pattern; if you're writing one-off ssh loops, match it.

### Deploy says "All N machines deployed" but `uv run ...` fails

Pre-2026-04-18 `deploy.sh` would report success even when the cloud-init uv install silently failed (runcmd runs without `set -e`). Two fixes are in place now:

1. `cloud-init.yaml` wraps the uv install in a script with `set -e` and retries up to 3× before touching `/tmp/cloud-init-done`. If the uv binary doesn't land, the done marker is never written and `deploy.sh` times out loudly instead of proceeding.
2. `deploy.sh` runs a post-install import smoke (`from starsector_optimizer.optimizer import _shape_fitness`) and hard-fails if it errors.

If either of these is ever bypassed, the failure mode to watch for is: deploy appears to succeed, but the first `ssh worker 'uv run ...'` returns `bash: line 1: /root/.local/bin/uv: No such file or directory`. Fix on a running worker by rerunning the install manually:
```bash
ssh $SSH_OPTS root@"$IP" 'curl -LsSf https://astral.sh/uv/install.sh | sh && /root/.local/bin/uv --version' </dev/null
ssh $SSH_OPTS root@"$IP" 'cd /opt/optimizer && /root/.local/bin/uv sync' </dev/null
```

## Teardown discipline

**After every cloud work session, run:**

```bash
./scripts/cloud/final_audit.sh
```

Exits 0 if every section is empty across both providers (Hetzner servers, AWS instances/SGs/keypairs/volumes in us-east-1 and us-west-2, all filtered to `starsector-*` tags); exits 1 and prints what leaked otherwise. Use this as the last command of every session — a non-zero exit is a signal to run the corresponding teardown before logging off.

The no-arg `./scripts/cloud/teardown.sh` form discovers every `sim-worker-*` on Hetzner via `hcloud server list` and deletes them. It's strictly safer than the legacy `teardown.sh N` form — if a previous deploy created 5 workers and the caller passes 3, the old form leaked two. Auto-discovery also verifies with a post-delete list to confirm zero remaining.

**If you're ending a session with active campaigns running**: that's an explicit user decision. Confirm with the user before leaving resources alive. Default posture is "no active resources at session end."

## Things to push back on

- **"Let's run it overnight and see"** without a budget cap. No — set `budget_usd` explicitly first. A misconfig can burn $500 overnight.
- **"Skip the baked image, just use cloud-init each time"**. OK for <10 workers. At 30+, bootstrap is ~15% of wall-clock; bake the image.
- **"GPU cloud for speed"**. CPU is 2.4× local per-instance; GPU doesn't help this workload. Show `experiments/cloud-benchmark-2026-04-18/RESULTS.md`.
- **"One giant study with 200 workers"**. TPE saturates above 30; 200-worker mega-study wastes 85% of budget as random. Federate into ≤24-worker studies.
- **"PostgreSQL for Optuna storage"**. Not needed when federation keeps each study's SQLite local. Skip the ops burden.
- **"Let's try SkyPilot / Ray / Modal / Fargate"**. None support Hetzner; all add complexity; `experiments/cloud-benchmark-2026-04-18/SCALING.md` documents the reasoning.
- **"Add warm pools" at <$10k/mo spend**. EBS idle cost dominates; not worth it.

## References

- **Design doc**: `docs/reference/phase6-cloud-worker-federation.md`
- **Scaling research** (provider comparison, Optuna parallelism, library comparison): `experiments/cloud-benchmark-2026-04-18/SCALING.md`
- **Empirical validation** (the 2026-04-18 AWS + Hetzner bench that overturned "GPU required"): `experiments/cloud-benchmark-2026-04-18/RESULTS.md`
- **Cloud deployment spec**: `docs/specs/22-cloud-deployment.md`
- **Existing scripts**:
  - Hetzner: `scripts/cloud/{deploy,sync-code,run,status,collect,teardown,final_audit}.sh`
  - AWS: `scripts/cloud/aws/{deploy,run_benchmark,teardown}.sh`
  - Cloud-init: `scripts/cloud/cloud-init.yaml`
- **LWJGL XRandR fix** (critical): `src/starsector_optimizer/instance_manager.py::_start_xvfb`
