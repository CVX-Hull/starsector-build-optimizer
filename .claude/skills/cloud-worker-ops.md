---
name: Cloud Worker Operations
description: SOP for launching, monitoring, and tearing down multi-worker cloud campaigns for Starsector optimization. Invoke when the user asks to run optimization in the cloud, spin up many workers, start a campaign, spend a budget on experiments, or debug cloud-worker issues.
disable-model-invocation: true
---

# Cloud Worker Operations SOP

Use this skill when the user asks you to run or debug a cloud campaign — anything involving multiple Starsector workers outside the local workstation. Built on the Phase 6 Cloud Worker Federation design (`docs/reference/phase6-cloud-worker-federation.md`). Empirical throughput numbers: `experiments/cloud-benchmark-2026-04-18/`.

## The three rules of money

1. **Every launch sets a budget ceiling.** If you don't have a `budget_usd` figure, STOP and ask the user for one. Default "launch and see what happens" is a $200/day runaway pattern.
2. **Every launch prints the teardown command as its first line of output.** Operator must be able to copy-paste to nuke resources in case the orchestrator dies.
3. **Final-audit runs at the end of every session.** After ANY cloud work, `scripts/cloud/final_audit.sh <campaign-name>` must exit 0. Don't skip this step.

## Which provider

| Situation | Pick |
|---|---|
| **$85-$200 campaign (Phase 6 MVP + Phase 7 prep)** | **AWS c7a.2xlarge spot us-east-1 + us-east-2**. Account quota: 640 spot vCPU per region = 80 VMs each with zero lead time. ~$0.15/hr; ~3% preemption under `price-capacity-optimized` + `CapacityRebalancing`. All dollar figures come from `experiments/phase6-planning/cost_model.py` — rerun after pricing changes rather than hand-editing. |
| $500+ campaign (Hetzner ~13% savings justify quota ticket) | **Hetzner CCX33** — `HetznerProvider` is stubbed until this threshold; implementing it means filing a quota ticket (1-2 business days) then writing the hcloud-python wrapper per `docs/specs/22-cloud-deployment.md`. |
| GPU cloud | **Never.** CPU is 2.4× local per-instance after the XRandR fix; GPU adds no throughput and costs more. Cite `experiments/cloud-benchmark-2026-04-18/` and push back. |
| ARM / Graviton | **Never.** LWJGL 2.9.3 is x86_64-only. |

**Why AWS primary at small budget**: at $85-$200 the dominant operator cost is *lead time*, not per-matchup price. AWS already has 1,792 spot vCPU across 4 US regions; Hetzner's default 10-VM project cap requires a multi-day quota ticket. The ~13% AWS premium at $85 is ~$10 — cheaper than a human-day of waiting. Above $500, the absolute delta (~$60+) exceeds a human-day of engineering, and Hetzner becomes the better pick.

## Preflight checklist (before launching ANY cloud worker)

Run all of these. Failure on any one = STOP. `CampaignManager._preflight` re-runs checks 3 (Tailscale up), 4 (Redis on tailnet), 11 (AWS credentials), and 6 (authkey syntax) in-process before it spawns anything; this checklist is the operator-side verification — items 1/2/5/7/8/9/10/12/13/14/15/16 are operator-only.

1. **Budget is set**: user has given a `budget_usd` figure AND it's written into the campaign YAML's `budget_usd` field.
2. **Python modules import cleanly**:
   ```bash
   uv run python -c "from starsector_optimizer.campaign import CampaignManager, CostLedger"
   uv run python -c "from starsector_optimizer.cloud_provider import AWSProvider"
   uv run python -c "from starsector_optimizer.cloud_worker_pool import CloudWorkerPool"
   uv run python -c "from starsector_optimizer.cloud_runner import run_cloud_study"
   uv run python -c "from starsector_optimizer.worker_agent import load_worker_config_from_env"
   ```
3. **Tailscale is up on the workstation**:
   ```bash
   tailscale ip -4   # must return a 100.x.y.z address; empty = run `tailscale up`
   ```
4. **Redis is reachable on the tailnet interface** (workers can't reach localhost-bound Redis):
   ```bash
   redis-cli -h "$(tailscale ip -4)" ping   # must return PONG
   ```
   If it fails: `sudo systemctl edit redis-server` → `[Service]` section, override `ExecStart=` to include `--bind 0.0.0.0` (or the tailnet IP explicitly). Then `sudo systemctl restart redis-server`.
5. **Tailscale ACL allows `tag:starsector-worker` → workstation on `tcp:6379,9000-9099`**. Verify from the Tailscale admin panel (`https://login.tailscale.com/admin/acls`). Sample ACL stanza:
   ```json
   {"action": "accept", "src": ["tag:starsector-worker"], "dst": ["<workstation-hostname>:6379,9000-9099"]}
   ```
6. **Ephemeral + pre-approved auth key exists** (from Tailscale admin panel → Keys), tagged `tag:starsector-worker`. Export before launch if the YAML uses `${TAILSCALE_AUTHKEY}` env-substitution:
   ```bash
   export TAILSCALE_AUTHKEY=tskey-auth-...
   ```
7. **AWS quota check** (for every `regions:` entry):
   ```bash
   for region in us-east-1 us-east-2; do
     aws service-quotas get-service-quota --service-code ec2 \
       --quota-code L-34B43A08 --region $region --query 'Quota.Value' --output text
   done
   ```
   At 8 vCPU/VM, confirm `quota ≥ 8 × planned_workers_per_region`.
8. **No orphaned resources** under your target tag:
   ```bash
   scripts/cloud/final_audit.sh <campaign-name>   # must exit 0 before launching
   ```
9. **AMI exists in every `regions:` entry** — inspect `ami_ids_by_region:` in the campaign YAML and verify each AMI is available:
   ```bash
   aws ec2 describe-images --owners self --region <region> --image-ids <ami-id>
   ```
10. **Validation probe passed within last 48 hours**:
    ```bash
    scripts/cloud/probe.sh <campaign.yaml>
    ```
11. **Provider credentials alive**: `aws sts get-caller-identity` returns `UserId`.
12. **Tier-2 pipeline smoke passed within last 30 days** (first real paid campaign gate):
    ```bash
    export TAILSCALE_AUTHKEY=tskey-auth-...
    scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml
    scripts/cloud/final_audit.sh smoke   # must exit 0
    ```
    Expected gate: launch exits 0 + ledger.jsonl has ≥1 `worker_heartbeat` + Optuna study SQLite has 1 `TrialState.COMPLETE` (~$0.30, < 10 min wall-clock).
13. **Game prefs file exists** at `~/.java/.userPrefs/com/fs/starfarer/prefs.xml`. Bake it into the AMI via Packer; the Packer template references the host-side path.
14. **SSH key** present; name must match `ssh_key_name:` in the YAML.
15. **LWJGL XRandR fix in code**: `grep 'xrandr --query' src/starsector_optimizer/instance_manager.py` returns a match in `_start_xvfb`. Without it, workers crash with `ArrayIndexOutOfBoundsException: Index 0`.
16. **`x11-xserver-utils` baked into the AMI**: check `scripts/cloud/packer/aws.pkr.hcl` contains `x11-xserver-utils` in the apt list.

## Launching a campaign

Smoke and prep share the same launch command. Only the YAML differs.

```bash
# 1. (once per AMI rebuild) Bake and copy the AMI
scripts/cloud/bake_image.sh
# → prints AMI IDs for us-east-1 and us-east-2; paste into campaign.yaml

# 2. Dry-run validate the YAML + resolve config (free)
TAILSCALE_AUTHKEY=tskey-auth-placeholder \
  uv run python -m starsector_optimizer.campaign --dry-run <campaign.yaml>

# 3. Tier-1 validation probe ($0.05)
scripts/cloud/probe.sh examples/probe-campaign.yaml

# 4. Tier-2 pipeline smoke (~$0.30) — SAME code path as prep, tiny study
export TAILSCALE_AUTHKEY=tskey-auth-...
scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml

# 5. Real launch (prints teardown command as first line)
scripts/cloud/launch_campaign.sh <campaign.yaml>

# 6. Monitor
scripts/cloud/status.sh <campaign-name>

# 7. On completion OR error — explicit teardown
scripts/cloud/teardown.sh <campaign-name>

# 8. Final audit — MANDATORY (launch_campaign.sh EXIT trap also runs this)
scripts/cloud/final_audit.sh <campaign-name>
```

`launch_campaign.sh` wraps the Python invocation in a `trap EXIT` that re-runs `teardown.sh` + `final_audit.sh` on any exit path (success, SIGKILL, crash). In-process, `CampaignManager.run()` has a `try/finally: terminate_all_tagged` sweep + `atexit.register(teardown)`. Each study subprocess also has its own `try/finally: terminate_fleet` for its own fleet. **Four layers of teardown belt-and-suspenders.**

### Study-per-(hull,regime,seed) sizing cheatsheet

- **≤24 workers per study**: TPE (default). Efficient, precise, recommended.
- **24–100 workers per study**: switch sampler to `CatCMAwM` (`sampler: catcma` in the YAML). Native-parallel CMA-ES; no TPE imputation penalty.
- **Hybrid (random→CMA→TPE)**: for per-study budgets >1000 trials.

Per-study budget sweet spot: **500-1500 trials**.

## Monitoring during runs

Every 15-30 min while a campaign is live:

1. **Cost ledger + cumulative cost**:
   ```bash
   scripts/cloud/status.sh <campaign-name>
   ```
   Cross-reference cumulative against `budget_usd`.
2. **Worker liveness**: `aws ec2 describe-instances --filters 'Name=tag:Project,Values=starsector-<campaign-name>' 'Name=instance-state-name,Values=pending,running'`. Dead workers should be auto-replaced; persistent gap = bug.
3. **Redis queue depth per study**: `redis-cli LLEN queue:<study_id>:source`. If growing unbounded, workers can't keep up — scale up or reduce per-worker lifespan.
4. **Stuck studies**: any study with no trial progress for >15 min = worker crash loop. Inspect worker logs; typically the XRandR or heartbeat issue.

## Failure recovery recipes

### "Redis connection refused" on tailnet IP

Workers boot and fail `BRPOPLPUSH` with `ConnectionRefusedError: [Errno 111]`. Root cause: workstation Redis is bound only to 127.0.0.1. Fix via systemd drop-in:

```bash
sudo systemctl edit redis-server
# In the editor, add:
#   [Service]
#   ExecStart=
#   ExecStart=/usr/bin/redis-server /etc/redis/redis.conf --bind 0.0.0.0
sudo systemctl restart redis-server
redis-cli -h "$(tailscale ip -4)" ping   # must now return PONG
```

`CampaignManager._preflight` catches this at launch — if you see "Redis not reachable at <tailnet_ip>:6379" in the launch output, apply the drop-in and relaunch.

### "Tailscale ACL denies tag:starsector-worker"

Workers boot, `tailscale up` succeeds, then their BRPOPLPUSH hangs and eventually times out. Root cause: ACL doesn't grant the worker → workstation reachability. Fix via admin panel (`https://login.tailscale.com/admin/acls`), add:

```json
{
  "action": "accept",
  "src": ["tag:starsector-worker"],
  "dst": ["<workstation-hostname>:6379,9000-9099"]
}
```

Workstation hostname is the one shown by `tailscale status --self`.

### Workers crashing on startup with `ArrayIndexOutOfBoundsException: Index 0`

LWJGL XRandR bug. Check:
1. `instance_manager.py::_start_xvfb` has the `xrandr --query` warmup call.
2. `x11-xserver-utils` is baked into the AMI. `ssh worker 'which xrandr'` should return a path.
3. If the AMI predates the fix, rebuild via `scripts/cloud/bake_image.sh`.

### Campaign blew past budget

1. **IMMEDIATELY**: `scripts/cloud/teardown.sh <campaign-name>`. Blunt-force stop bleeding.
2. Check `~/starsector-campaigns/<name>/ledger.jsonl` for the crossing event — did it fire the warning threshold? If not, the ledger-write path is broken.
3. Root-cause before next launch. Candidate causes: (a) `CostLedger.record_heartbeat` not being called on a cadence consistent with `ledger_heartbeat_interval_seconds`, (b) `BudgetExceeded` caught and swallowed somewhere, (c) worker `max_lifetime_hours` not honored by the worker agent loop.

### Spot preemption cascade

If >30% of workers are being preempted in a short window:
1. Check AWS Spot placement score for the target regions: `aws ec2 describe-spot-placement-scores --region us-east-1 --single-availability-zone`.
2. If score <7, pause, then relaunch after expanding `instance_types:` (add `c7a.4xlarge`, `c7i.4xlarge`) or switching the `regions:` list.
3. At $500+ scale, ship `HetznerProvider` (no spot preemption) as a cost-stable alternative.

### Worker output never comes back

SSH directly (assumes Tailscale node for this worker is reachable):
```bash
ssh ubuntu@<worker-tailscale-ip> 'systemctl status starsector-worker; journalctl -u starsector-worker -n 200'
```

Common causes: Xvfb died, Starsector JVM hung, heartbeat file stale. `pkill -9 java; pkill -9 Xvfb` and let the `instance_manager` restart logic kick in; if 3 restarts fail, treat as broken worker and replace.

### AMI-copy-image drift across regions

AWS AMIs are region-scoped. If `aws ec2 copy-image` hasn't run or silently failed, `us-east-2` workers launch from a stale AMI. Check:
```bash
aws ec2 describe-images --owners self --region us-east-1 --image-ids <us-east-1 ami>
aws ec2 describe-images --owners self --region us-east-2 --image-ids <us-east-2 ami>
```
Both must show `State: available`. If only one, re-run `scripts/cloud/bake_image.sh` — it bakes once in us-east-1 then copies to us-east-2 automatically.

## Teardown discipline

**After every cloud work session, run:**

```bash
scripts/cloud/final_audit.sh <campaign-name>
```

Checks all 4 US regions (us-east-1, us-east-2, us-west-1, us-west-2) for instances / SGs / volumes tagged `Project=starsector-<campaign-name>`. Exit 0 if clean, 1 if any resource leaked. Use as the last command of every session.

`launch_campaign.sh` wraps its Python invocation in `trap EXIT` that re-runs `final_audit.sh` — so even a SIGKILL of the shell triggers the audit. Belt-and-suspenders with `CampaignManager.run()`'s in-process `try/finally` and `atexit`.

**If you're ending a session with active campaigns running**: that's an explicit user decision. Confirm with the user before leaving resources alive. Default posture is "no active resources at session end."

## Things to push back on

- **"Let's run it overnight and see"** without a budget cap. No — set `budget_usd` explicitly first. A misconfig can burn $500 overnight.
- **"Skip the baked image, just use cloud-init each time"**. Not supported. Packer bake is mandatory — cloud-init bulk apt/PyPI fails under 50+ concurrent cold starts.
- **"GPU cloud for speed"**. CPU is 2.4× local per-instance; GPU doesn't help this workload.
- **"One giant study with 200 workers"**. TPE saturates above 24; 200-worker mega-study wastes 85% of budget as random. Federate into ≤24-worker studies per `(hull, regime, seed)`.
- **"PostgreSQL for Optuna storage"**. Not needed — each study runs its own SQLite locally in a subprocess on the orchestrator.
- **"Let's try SkyPilot / Ray / Modal / Fargate"**. Already rejected in the design — see `docs/reference/phase6-cloud-worker-federation.md` §rejected alternatives.
- **"Add warm pools" at <$10k/mo spend**. EBS idle cost dominates; not worth it.

## References

- **Design doc**: `docs/reference/phase6-cloud-worker-federation.md`
- **Cloud deployment spec**: `docs/specs/22-cloud-deployment.md`
- **Empirical validation**: `experiments/cloud-benchmark-2026-04-18/`
- **Cost model (source of truth for dollar figures)**: `experiments/phase6-planning/cost_model.py`
- **Scripts**: `scripts/cloud/{launch_campaign,status,teardown,final_audit,probe,bake_image}.sh` + `scripts/cloud/packer/aws.pkr.hcl`
- **LWJGL XRandR fix**: `src/starsector_optimizer/instance_manager.py::_start_xvfb`
