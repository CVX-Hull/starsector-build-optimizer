---
type: report
status: shipped
last-validated: 2026-07-11
---

# AWS Execution Shift and Cost Analysis

## Abstract

The 2026-05-16 seven-split report scoped AWS learned-batch execution out
"until there is a reproducibility or scale need that local execution cannot
satisfy." That condition is now met: the local Linux sim box is occupied, and
the redesigned evidence wave
([2026-07-11-phase7-methodology-review.md](2026-07-11-phase7-methodology-review.md)
§6) multiplies the cell count via repeated splits and feature profiles.
**Decision (2026-07-11): Phase 7 compute shifts to AWS.** This report
inventories the existing execution paths, records live-verified prices, and
gives a per-experiment-class cost model. Headline anchors: CPU
learned-surrogate jobs cost ≈$0.24 each on 4xlarge spot (a full 54-cell
ablation wave ≈$13); sim matchups cost ≈$0.001–0.0014 each on spot; full
cloud optimizer runs realized ≈$0.81–1.11 per 100 finalized trials with a
conservative forward model of ≈$3.2. One housekeeping finding: ~40 stale
worker AMIs are accruing ~$10–16/month.

## 1. Execution-path inventory

| Path | Entry point | Instances | Shape | Control plane |
|---|---|---|---|---|
| Optimizer campaigns (sim) | `scripts/cloud/launch_campaign.sh` → `CampaignManager` | c7a/c7i.2xlarge spot (8 vCPU, 2 JVMs/VM) | ≤24 workers/study, 2 regions | Redis reliable queue + per-study Flask over Tailscale |
| Honest-eval sweeps (sim) | `scripts/cloud/launch_wave1_honest_eval.sh` | same worker AMI | Plan C: 64 workers = 128 slots, 32/region | resumable ledger `data/honest_eval/<tag>/results.jsonl` |
| Phase 7 learned batch (CPU) | `scripts/cloud/launch_phase7_learned_batch.sh` → `phase7_learned_batch.py` | 16-vCPU 4xlarge spot mix (c7i/c7a/c6i/c6a/m7i/m7a); 2xlarge rejected in code (`src/starsector_optimizer/phase7_learned_batch.py:514-515`) | 1 worker per (split×model) job; us-east-1 only; 128 GB root | Bearer-token Flask :9131 over Tailscale; renewable leases (60 s renew / 300 s grace, 6 attempts) |
| Support | `bake_image.sh`, `probe.sh`, `teardown.sh`, `audit_amis.sh`/`cleanup_amis.sh`, `watch_eval_cleanup.sh` | — | — | 4-layer teardown (spec 22) |

Current learned-batch AMI: `ami-07ce0d6ab863c85c5` (us-east-1,
`examples/phase7-learned-batch.yaml:7`). Both checked-in learned-batch
configs have `execution_enabled: false` — a relaunch must flip it
deliberately. The bundle builder refuses dirty worktrees
(`scripts/cloud/phase7_learned_batch.py:96-99`), which is the reproducibility
argument for preferring AWS runs for report-grade artifacts.

Account state verified 2026-07-11: no project resources running; quota
L-34B43A08 (standard spot) = 640 vCPU in each of us-east-1/us-east-2. Two
non-project VPN instances exist on the account (t3.micro since 2025-10,
t3.medium since 2026-06-20, ≈$37/mo on-demand combined) — not this project's,
flagged for the account owner.

## 2. Prices (live spot medians 2026-07-11; on-demand = published list, not API-verified — IAM user lacks `pricing:GetProducts`)

| Type | Spot use1 | Spot use2 | On-demand | Realized (ledgers, 2026-05) |
|---|---|---|---|---|
| c7a.2xlarge | $0.166 | $0.147 | $0.411 | $0.135–0.247 |
| c7i.2xlarge | $0.144 | $0.133 | $0.357 | $0.176–0.204 |
| c7a.4xlarge | $0.340 | $0.311 | $0.821 | $0.390 |
| c7i.4xlarge | $0.294 | $0.261 | $0.714 | $0.325 |
| c6i.4xlarge | $0.307 | — | $0.680 | $0.314 |
| c6a.4xlarge | $0.298 | — | $0.612 | $0.299 |
| m7i.4xlarge | $0.337 | — | $0.806 | — |
| m7a.4xlarge | $0.411 | — | $0.927 | — |

Spot ≈ 40–48% of on-demand across the family. Realized-cost sources:
`data/campaigns/wave1-c*/ledger.jsonl`,
`data/phase7/learned_surrogate_batch_smoke_retry3_2026-05-12/ledger.jsonl`.

## 3. Cost model by experiment class

### 3.1 CPU learned-surrogate experiments (matrix / ablations / HPO)

Runtime evidence: local serial jobs 15.4–18.4 min
(`data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json`: 21 jobs in
6h26m); AWS end-to-end ≤37 min/job including bootstrap (2-job smoke ledger,
$0.4747 total). **Measured anchor ≈$0.24/job on 4xlarge spot.**

| Wave | Jobs | Fleet | Walltime | Spot | On-demand |
|---|---|---|---|---|---|
| Seven-split 21-cell matrix | 21 | 21 × 4xlarge (336 vCPU, fits quota) | ~40 min | ~$5 | ~$12 |
| Ablation wave (6 profiles × 3 splits × 3 models) | 54 | 9/batch × 6 batches (`feature_profile` is batch-level) | ~4 h serial batches | ~$13 (budget $30) | ~$32 |
| Full 6 × 7 × 3 matrix | 126 | 21/batch × 6 | ~4 h | ~$30 | ~$75 |
| Methodology-review re-run (≥10 split seeds × 21 cells) | ~210 | quota-bound batches | ~1 working day | ~$50–60 | ~$130 |

Recommended config: spot, c7i.4xlarge-first ordering (cheapest today).
Repeated-split waves (methodology review §6.1) scale linearly in job count —
still trivially affordable; walltime, not dollars, is the binding constraint.

### 3.2 Sim-based honest-eval sweeps

Throughput evidence: 144.3 matchups/hr/VM (Wave-1 cells,
[2026-05-10-wave1-validation.md](2026-05-10-wave1-validation.md) §6) and
~122 matchups/hr/VM observed at the 64-worker honest-eval fleet.
**Per-matchup ≈$0.0010–0.0014 spot / ≈$0.0029 on-demand.**

| Sweep | Matchups | Fleet | Walltime | Spot | On-demand |
|---|---|---|---|---|---|
| Plan-C sweep | ~38,000 | 64 × 2xlarge | ~6 h | ~$40–55 raw ($100 budget) | ~$110 |
| Wave-1 realized (87,480) | 87,480 | 64 | ~11.2 fleet-h | ~$95–120 **estimated** | ~$250 |
| Per-build panel (54 opp × 30 reps) | 1,620 | — | — | ~$1.7–2.3 | ~$4.7 |
| Opponent-panel widening wave (H5: e.g. 20 builds × 200 variants × 10 reps) | 40,000 | 64 | ~6 h | ~$45–60 | ~$115 |

Cost is ~constant in fleet size (workers buy walltime). Above 64 workers,
spot-pool fragmentation binds before quota (512 of 1,280 two-region vCPU).
**Gap**: the honest-eval path writes a results ledger but no cost ledger —
the Wave-1 realized figure is derived, not measured.

### 3.3 Full cloud optimizer runs (`--worker-pool cloud`)

Realized: Wave-1 totaled $25.01 for 5 cells; each cell (3 studies × 8 workers)
hit its $5.00 budget cap in 72–90 min with 453–618 finalized trials —
**≈$0.81–1.11 per 100 finalized trials realized**. The forward model in the
Wave-1 validation report (27.3 matchups/trial × per-matchup cost × retry and
preemption multipliers) gives **≈$3.2 per 100 trials** — a 3× disagreement
traceable to per-seed vs per-cell trial-count arithmetic. Budget with the
conservative figure until one instrumented run logs matchups-per-finalized-trial
and dollars in the same frame. The phase7-prep 8-hull campaign
(`examples/phase7-prep.yaml`: $70 budget) should stay gated on a single-hull
matchups-per-trial re-measurement (the wolf measurement never landed).

### 3.4 Fixed overheads

- AMI bake: ~$0.09 spot + ~$0.16 cross-region snapshot copy per bake.
- **Stale-AMI leak (action item)**: 40 worker AMIs (20/region,
  2026-04-18→05-12) + 43 snapshots at 8 GB each ≈ $10–16/month accruing; only
  3–4 AMIs are referenced by current YAMLs. `scripts/cloud/cleanup_amis.sh`
  (dry-run by default, YAML-reference guarded) exists for exactly this and has
  not been run since May.
- EBS during runs &lt;$1/wave; egress negligible (results are small JSON over
  Tailscale); head node $0 (workstation + Tailscale free tier).
- Interruption/retry: renewable leases bound learned-batch loss (worst
  observed pre-fix: $1.67 with 0/15 jobs, fixed-duration-lease bug since
  fixed); sim-side retries ≈7% cost multiplier + 1.03 preemption multiplier;
  keep the 1.5× budget-headroom convention.
- Learned-batch idle tail: fleet tears down after the last job merges — $1–2
  worst case per batch; tail-job walltime is unmeasured at scale (only the
  2-job smoke has clean AWS timing, and opponent-split RF jobs run 3–8×
  longer than average locally).

## 4. Unknowns needing live measurement

1. Realized honest-eval dollar spend (no cost ledger on that path; IAM user
   cannot read Cost Explorer — needs account owner or a `CostLedger` port to
   `evaluate_campaign.sh`).
2. The 3× matchups-per-trial accounting spread (blocks phase7-prep budgeting).
3. Learned-batch tail-job walltime at scale.
4. Non-Hammerhead (wolf) matchups-per-trial.
5. On-demand prices (published list only).

## 5. Recommendations

1. Run methodology-review wave items 1–5 (CPU-only) via the learned-batch
   path on spot, c7i.4xlarge-first, $30–60 budgets per wave.
2. Keep Plan C (64 workers, $100 budget) as the sim-sweep default; add a cost
   ledger to the honest-eval path before the next sweep.
3. Run `cleanup_amis.sh` (dry-run first) to stop the AMI leak.
4. Gate the 8-hull phase7-prep campaign on a single-hull cost re-measurement.
