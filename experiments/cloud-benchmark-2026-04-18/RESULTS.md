# Cloud benchmark — 2026-04-18

## Bottom line

**CPU-only cloud is fully viable. The 2026-04-12 "GPU required" conclusion was a misdiagnosed LWJGL bug.**

Both AWS c7i.2xlarge spot and Hetzner CCX33 match or exceed local per-instance throughput (2.2–2.4× the 12-core workstation baseline). Near parity between providers; pick on cost, latency, and preemption tolerance.

## Root cause of the historical "GPU required" claim

LWJGL 2.x's `LinuxDisplay.getAvailableDisplayModes` throws `ArrayIndexOutOfBoundsException: Index 0` when Xvfb's XRandR extension has not populated its mode list. Xvfb does not finalize XRandR state until a client queries it — first query from LWJGL returns an empty mode array and crashes.

The 2026-04-12 Hetzner test observed "26s game-time in 120s wall-clock" — most likely because Starsector was crashing on every first launch and the measurement picked up either a degraded fallback path or a restart-loop. The real CPU throughput was never measured.

**Fix** (implemented 2026-04-18 in `src/starsector_optimizer/instance_manager.py::_start_xvfb`): after waiting for the Xvfb socket, run `xrandr --query` once as a client. This warms the XRandR extension's internal state. Requires `x11-xserver-utils` in cloud-init (added).

After the fix: AWS and Hetzner both launch Starsector on first try and run at full speed.

## Benchmark results (same config, same opponents, same random seed)

Identical workload: `--hull hammerhead --num-instances 2 --sim-budget 6 --active-opponents 10`. Both completed 6 builds (5 finalized + 1 pruned, 57 matchups total).

| Metric | Local (reference) | AWS c7i.2xlarge | Hetzner CCX33 |
|---|---|---|---|
| CPU | 12-core + RTX 4090 | 8 vCPU Intel Xeon 8488C (SPR) | 8 vCPU AMD EPYC Milan |
| Region | N/A | us-east-1 | Ashburn VA (ash) |
| Spot price | $0/hr | $0.158/hr | $0.13/hr (no spot) |
| Wall-clock (6 builds) | — (per-instance baseline) | **1599s (26.65 min)** | **1713s (28.55 min)** |
| Matchups | — | **57** | **57** |
| Matchups/hr/instance | **27** | **64.2** | **59.9** |
| vs local baseline | 1.0× | **2.38×** | **2.22×** |
| Cost per run | $0 | $0.070 | $0.062 |
| **Cost per matchup** | $0 (elec) | **$0.00123** | **$0.00109** |

Throughput difference (AWS ~7% faster than Hetzner) is within noise for a 6-build sample. Both are ~2.3× local workstation per-instance.

## What this means for rapid experiments

Translating to the "30 workers × 2 hr burst" target from the scaling research:

| Scenario | Hetzner CCX33 × 30 × 2hr | AWS c7i/c7a × 30 × 2hr |
|---|---|---|
| Instances | 30 | 30 (spot fleet) |
| Matchups delivered | ~3,600 | ~3,850 |
| Builds delivered (~10 matchups/build) | ~360 | ~385 |
| Cost | **$7.80** | **$9** |
| Preemption risk | **none** | ~3% with price-capacity-optimized |
| Setup complexity | Existing `scripts/cloud/*.sh` already works | Already set up during this bench |

A one-day workstation run today delivers ~56 builds (8 hr × 4 instances × 1.75 builds/hr/inst). A **2-hour Hetzner burst delivers ~360 builds — 6.4× the output at $7.80**.

## Provider recommendation

**Hetzner CCX33 in Ashburn (ash)** is the pick for this workload:
- Cheapest per matchup ($0.00109 vs AWS $0.00123, 11% less)
- No preemption (simplest failure model — worker dies only on hardware failure)
- Existing `scripts/cloud/deploy.sh` already targets it — zero new code
- Dedicated vCPU (not shared/burst), stable perf
- Ashburn is 25-35 ms from NC (fast SSH, fast rsync)

**AWS c7a.2xlarge us-west-2 spot** is the fallback if:
- Hetzner capacity hits limits
- Need `GetSpotPlacementScores` / EC2 Fleet diversification for large bursts
- Need regions Hetzner doesn't cover

**Skip entirely**: all GPU instances (not needed), ARM/Graviton (LWJGL x86_64 only), DigitalOcean/Vultr/Linode (no spot, 2-3× cost).

## Spec and memory updates made

- `docs/specs/22-cloud-deployment.md` — rewrote §GPU Requirement and §Cloud Machine Sizing with the corrected "CPU-only works" finding and benchmark numbers
- `scripts/cloud/cloud-init.yaml` — added `x11-xserver-utils` to packages list
- `src/starsector_optimizer/instance_manager.py::_start_xvfb` — added `xrandr --query` warmup after Xvfb socket is ready
- `~/.claude/projects/.../memory/project_cloud_gpu_required.md` — replaced "GPU required" with "CPU viable after XRandR fix"

## Teardown proof

```
$ hcloud server list
ID   NAME   STATUS ...  (empty)

$ aws ec2 describe-instances --region us-east-1 \
    --filters Name=tag:Project,Values=starsector-bench-20260418 \
    --query 'Reservations[].Instances[?State.Name!=`terminated`]' --output text
(empty)

$ aws ec2 describe-security-groups --region us-east-1 \
    --filters Name=tag:Project,Values=starsector-bench-20260418 \
    --query 'SecurityGroups[]' --output text
(empty)

$ aws ec2 describe-key-pairs --region us-east-1 \
    --filters Name=tag:Project,Values=starsector-bench-20260418 \
    --query 'KeyPairs[]' --output text
(empty)
```

Zero resources remaining, zero cost accruing.

## Total campaign cost

- AWS c7i.2xlarge: 1599s × $0.158/hr = **$0.070**
- Hetzner CCX33: 1713s × $0.13/hr = **$0.062**
- **Total spent on this investigation: ~$0.14**

## Files

- `scripts/cloud/aws/deploy.sh` — AWS spot launch + provision
- `scripts/cloud/aws/teardown.sh` — AWS tag-filter cleanup
- `scripts/cloud/aws/run_benchmark.sh` — AWS bench wrapper (wrapper had a pipe-SIGPIPE issue; metadata collected manually)
- `scripts/cloud/deploy.sh`, `teardown.sh` — existing Hetzner scripts, unchanged
- `experiments/cloud-benchmark-2026-04-18/run_hetzner.sh` — Hetzner bench wrapper
- `experiments/cloud-benchmark-2026-04-18/analyze.py` — throughput + cost analyzer
- `experiments/cloud-benchmark-2026-04-18/results-c7i.2xlarge/` — AWS run artifacts
- `experiments/cloud-benchmark-2026-04-18/results-ccx33/` — Hetzner run artifacts
- `experiments/cloud-benchmark-2026-04-18/SCALING.md` — companion: scale-out research (30-100 workers)

## Next steps (suggested)

1. **Commit the `instance_manager.py` fix** (critical — applies to local runs on other Linux boxes too, not just cloud).
2. **Update `CLAUDE.md`** Phase 5/6 notes if there's anything about GPU-required.
3. **Decide on Phase 6 cloud worker architecture** (see `SCALING.md`): Apache Libcloud or raw `hcloud-python`, Redis dispatch queue, preserve `InstancePool` as the per-worker abstraction.
4. **One-off test at 10 Hetzner CCX33 in parallel** to validate the hcloud API rate-limit headroom and linear throughput scaling before committing to the architecture.
5. **Consider baking a Hetzner snapshot image** with game + deps + code pre-installed (one-time `hcloud image create`; cuts per-worker bootstrap from 2 min to ~30s).
