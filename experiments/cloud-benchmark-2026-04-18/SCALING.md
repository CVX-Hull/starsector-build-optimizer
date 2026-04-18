# Scaling research — 2026-04-18

Synthesis of 4 parallel research agents exploring rapid-experiment cloud scale-out (30-100 workers, 30min-2hr turnaround). All 4 used WebSearch/WebFetch; tool counts 20/16/19/28.

## Decision table (30 workers × 2 hr)

| Rank | Provider | Instance | $/burst | Setup difficulty | Preemption | Why |
|---|---|---|---|---|---|---|
| 1 | **Hetzner CCX33** | 8 ded vCPU AMD Genoa, Ashburn VA | **$7.80** | Easy — existing `scripts/cloud/*.sh` | **None** (no spot tier) | Simplest; Ashburn ≈25ms from NC |
| 2 | **GCP n2d-std-8 Spot** | 8 vCPU AMD Milan | **$4.20** | Medium — quota bump to 240 vCPU needed | Low-med | Cheapest |
| 3 | **AWS c7a.2xlarge spot** (us-west-2) | 8 vCPU AMD Genoa | **$9.00** | Medium — already set up | 5-10% | Best tooling |

**Important**: keep AWS on **c7a**, not **c7i** — AMD Genoa is ~20% cheaper per single-thread-hr with 2-4× better spot availability in us-east-1. Move workload to **us-west-2** (31% cheaper AND more stable spot capacity).

## Levers (biggest wins first)

### 1. Pre-baked image + warm pool (5min → 30-45s spinup)
- Packer AMI/image with game + deps + uv + code. Rebuild only on game updates (~quarterly).
- Storage cost: ~$0.20/month for the AMI snapshot.
- **AWS Auto Scaling Warm Pool**: 30-50 stopped instances, restart in ~30s. Cost while stopped: EBS only (~$16/mo for 50 workers).
- **Skip FSR** — EBS Fast Snapshot Restore caps at 10 concurrent (volume creation credits), doesn't scale to 50.
- Hetzner: Packer + `hcloud` creates private image; 30s provision-to-SSH consistent, no warm-pool concept needed.

### 2. Spot diversification (AWS only)
- EC2 Fleet with **4+ instance types** (c7a.2xl, c7a.4xl, c7i.2xl, c7i.4xl) + **price-capacity-optimized** allocation + Capacity Rebalancing. Interruption drops from 20% (lowest-price) → **3%**.
- `GetSpotPlacementScores` API pre-launch: free, swings interruption rate from 15-20% to 3-5% by picking right AZ at burst time.

### 3. Optuna architecture at 30+ workers
- **Sampler ceiling**: TPE degrades above ~24-30 parallel (constant_liar imputation collapses KDE to random). Either cap TPE in-flight or switch to **CmaEsSampler** (current `CatCMAwM` variant is natively parallel — no code change needed).
- **Storage**: orchestrator-owned SQLite is fine if workers are pure evaluators (ask/tell stays local). For >100 workers writing directly: PostgreSQL + GrpcStorageProxy (1 per ~10 workers).
- **AVOID JournalStorage + GrpcStorageProxy** — broken as of v4.4 (Optuna issue #6084, May 2025 still open).
- **WilcoxonPruner works distributed**: state central, worker death → heartbeat timeout → `RetryFailedTrialCallback(max_retry=2, inherit_intermediate_values=True)`.

### 4. Distributed ask/tell pattern
- Keep orchestrator (workstation) owning study
- Redis/SQS dispatch `BuildSpec` → cloud workers → HTTP POST result back
- ~200 LOC wrapper around existing `InstancePool` → `RemoteInstancePool`
- Existing Java combat harness unchanged — still reads `saves/common/combat_harness_queue.json.data` locally on each worker.

## Non-obvious findings

1. **Hetzner's US egress was cut to 1 TB/month** in Dec 2024. Our 50 GB/mo rsync load is fine; just don't host egress-heavy things there.
2. **GCP Spot no longer has the 24h cap** — that was legacy Preemptible. `bulkInsert` with `locationPolicy=ANY` handles zone capacity automatically.
3. **AWS `GetSpotPlacementScores` API** (free, pre-launch) is the single biggest lever for spot stability — programmatically pick the best-capacity region at burst time.
4. **AMD Genoa is ~5% faster single-thread than Intel Sapphire Rapids** at ~15-20% lower spot price (c7a vs c7i). Disqualifies c7i as default.

## Don't-bother list

- **ARM (Graviton / c7g / Ampere)** — LWJGL 2.9.3 is x86_64 only. 1.87× slower single-thread anyway.
- **DigitalOcean / Vultr / Linode / Fly.io** — no spot tier, 2-3× AWS baseline cost.
- **Capacity Reservations** — charged at full on-demand rate whether used or not. Useless for bursty workloads.
- **Savings Plans** — need sustained steady-state usage, not burst.
- **AWS Batch** — adds 30-60s scheduler latency per job transition. Fine for matchups >1 min, wasteful otherwise. Direct EC2 Fleet is simpler.
- **Kubernetes / EKS / Fargate** — no speed win over baked AMI + warm pool; adds cluster overhead.
- **JournalStorage + GrpcProxy combo** — broken; see Optuna #6084.

## Recommended architecture (concrete)

```
Orchestrator (workstation, unchanged):
  study.ask() → Redis LPUSH "matchup-queue"
  Result HTTP POST → study.tell()
  Sampler: CmaEsSampler (or TPE capped ≤24 in-flight)
  Storage: local SQLite (or PostgreSQL if scaling past 100 workers)

Bake once:
  Packer template → AMI/image with game + deps + uv + code
  Re-bake on game update or dep change (~quarterly)

Burst launch (three paths, pick one):
  [Hetzner]  hcloud server create --image=<baked> --type=ccx33
             --location=ash × 30 (serial via API, ~15-20s/create)
             → 30 workers SSH-ready in ~5 min total
             $7.80/burst. No preemption.

  [AWS]      EC2 Fleet CreateFleet count=30,
             types=[c7a.2xlarge, c7a.4xlarge, c7i.2xlarge, c7i.4xlarge]
             allocation=price-capacity-optimized
             capacity-rebalance=on, region=us-west-2
             Pre-check with GetSpotPlacementScores
             → ~45-60s to SSH-ready × 30
             $9/burst. 3% interruption.
             + Warm pool if doing frequent bursts (~$16/mo EBS)

  [GCP]      bulkInsert count=30 locationPolicy=ANY
             us-central1 spot
             → ~60s × 30
             $4.20/burst. Need quota bump to 240 vCPU first.

Runtime (unchanged Java combat harness):
  Worker agent (Python) pulls MatchupConfig from Redis,
  writes combat_harness_queue.json.data locally, waits for
  _done.data, POSTs result JSON back to orchestrator.

Teardown:
  Terminate all tagged instances OR (warm pool) return to stopped.
```

## Cost at scale

| Frequency | Bursts/week | Per-burst | Monthly total |
|---|---|---|---|
| Exploratory (Hetzner) | 10 | $7.80 | $338 |
| Routine (GCP n2d spot) | 10 | $4.20 | $182 |
| Production runs (warm pool 50w, 5x weekly) | 5 | ~$15 | ~$325 |

Reference: keeping workstation on 24/7 ≈ $30-50/mo in electricity. Cloud bursts replace overnight occupation AND free up workstation for interactive work.

## Recommendation path

**Phase 1** (immediate, low-risk): stick with existing Hetzner-targeted scripts, bake a CCX33 image with `packer-plugin-hcloud`. Test at 5 workers, then 30 workers. $7.80/burst, zero preemption to handle yet.

**Phase 2** (once stable): add GCP path as cost-halver for large bursts. One-time quota bump + `bulkInsert` wrapper.

**Phase 3** (if budget/latency demands it): AWS warm-pool path. Only worth it if Hetzner/GCP don't fit some need.

**Skip**: GPU cloud entirely. Benchmark today proves CPU cloud is 2.4× FASTER per instance than local workstation — the "CPU too slow for LWJGL" concern is inverted.

## Sources (abridged; see agent transcripts for full URL list)

- Optuna 4.2 gRPC storage proxy benchmarks (300 workers): https://medium.com/optuna/distributed-optimization-in-optuna-and-grpc-storage-proxy-08db83f1d608
- Optuna issue #6084 (JournalStorage + GrpcProxy broken): https://github.com/optuna/optuna/issues/6084
- AWS price-capacity-optimized blog: https://aws.amazon.com/blogs/compute/introducing-price-capacity-optimized-allocation-strategy-for-ec2-spot-instances/
- GetSpotPlacementScores API: https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_GetSpotPlacementScores.html
- EC2 Auto Scaling Warm Pools: https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html
- Hetzner CCX33 US traffic reduction: https://news.ycombinator.com/item?id=42264427
- GCP Spot (no 24h cap): https://cloud.google.com/compute/docs/instances/spot
- runs-on.com instance benchmarks: https://runs-on.com/benchmarks/aws-ec2-instances/
