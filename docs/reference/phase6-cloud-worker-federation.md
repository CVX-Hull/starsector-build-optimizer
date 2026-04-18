# Phase 6 — Cloud Worker Federation

Status: **PLANNED** (2026-04-18). Renumbers the former Phase 6 (Structured Search-Space Representation) to Phase 7 — cloud federation is infrastructure that Phase 7's multi-week BoTorch build depends on for validation at scale.

## Goal

Spend $N of compute and get $N of useful optimization data — i.e., **linear $→data scaling** from $10 validation runs to $1000+ hull-coverage campaigns. Frees the workstation for interactive work by moving bulk simulation to cloud CPU spot instances.

## Why now (not deferred)

1. **Phase 5D-post findings** — cloud CPU is 2.2-2.4× local per-instance throughput after the LWJGL XRandR warmup fix (benchmark in `experiments/cloud-benchmark-2026-04-18/`). The original "GPU required" blocker in `docs/specs/22-cloud-deployment.md` was a misdiagnosis; the actual bug was Xvfb's XRandR extension not populating its mode list until a client queries it.

2. **Phase 5E, 5F, Phase 7 all need scale.** Phase 5E Box-Cox validation wants ≥1000 builds per hull. Phase 5F regime-segmented exploration multiplies that by 4 regimes × tens of hulls. Phase 7 BoTorch GP validation needs cross-hull transfer data (30+ hulls). Running these on the workstation is ~8 hours per study, serial; on cloud it's 30 min per study, parallel.

3. **Optuna sampler ceiling.** TPE degrades above ~30 concurrent workers per study (`constant_liar` imputation collapses KDE to random sampling). Spending $1000 on one mega-study wastes 85% of the budget as random sampling. Federation into many smaller studies keeps each sampler in its efficient zone.

4. **Operator burden grows nonlinearly.** At 3 workers on the workstation (current), ad-hoc bash is fine. At 30+ cloud workers, ad-hoc bash kills entire budgets via runaway costs (no auto-stop) or orphaned resources (no teardown discipline).

## Non-goals

- Not a migration away from the workstation. Local runs remain the default for interactive work and for 1-2 worker debugging.
- Not GPU cloud. CPU spot is 2.4× local throughput at $0.0011/matchup; GPU adds no throughput for LWJGL 2.x software-rendered 2D games.
- Not a multi-user platform. Designed for single-developer operator discipline, not team collaboration.
- Not Phase 7 / 8 / 9 work. Cloud federation is the substrate; those phases bring new algorithms.

## Key design decisions

### §1 Study federation (the #1 architectural lever)

**Don't run one mega-study. Run many independent studies in parallel.**

The Optuna TPE sampler (with `constant_liar=True, multivariate=True`) is efficient at ≤24 concurrent in-flight trials. Above that, the pessimistic imputation of pending trials collapses the KDE into effectively random sampling (Optuna FAQ; also benchmarked in Optuna 4.2 gRPC proxy release notes at `https://medium.com/optuna/distributed-optimization-in-optuna-and-grpc-storage-proxy-08db83f1d608`).

**Federation unit: `(hull, regime, seed)`.** Each combination is a fully independent study:
- Own SQLite study.db (local to orchestrator, no RDB/PostgreSQL needed)
- Own worker pool (typically 4-8 workers, never >24)
- Own budget, own termination criterion (auto-terminate on best-fitness plateau)

**Outer parallelism** is the campaign manager launching N studies concurrently. **Inner parallelism** is each study running its own ≤24-worker ask/tell loop. Both stay in their efficient zone.

**Why this is better than scaling TPE up:**
- For BREADTH (which is what $1000 of compute buys): 50 independent studies × 20 trials/study finds more distinct high-fitness basins than 1 study × 1000 trials (multimodal landscape; Bengio's "gradient escape" argument applies to TPE's KDE attractors).
- For DEPTH (per `(hull, regime)` refinement): each study stays in TPE's ~24-worker regime.
- No storage backend contention. No PostgreSQL ops burden. Each study is a single file.

### §2 Sampler strategy per study

TPE is the default for ≤24 workers per study. Above that (if a single study needs faster turnaround), use `CatCMAwM` (already supported via `--sampler=catcma` since Phase 4). CMA-ES is natively parallel — it generates a batch of λ candidates per generation, evaluates them concurrently, updates the distribution. No `constant_liar` imputation needed.

**Hybrid schedule** (`--sampler=hybrid`, new in this phase):
```
Stage 0: warm-start (7 stock + 500 heuristic from 50k candidates)   ← already free
Stage 1: random exploration     (100 trials,  any parallelism)      ← fills search space
Stage 2: CatCMAwM refinement    (400 trials,  up to 50 workers)     ← native batch
Stage 3: TPE exploitation       (200 trials,  ≤24 workers)          ← precision
```
Early stages need breadth (parallel-friendly), late stages need precision (TPE's KDE beats CMA's Gaussian around the optimum). `study.sampler = newsampler` between stages; `n_ei_candidates=100` on TPE to extend its usable worker range modestly.

Rejected alternatives:
- **Facebook Ax** — native batch BO, but ~3 days of port + loses existing `WilcoxonPruner` / warm-start infrastructure. Not worth it when Phase 7 (BoTorch GP) supersedes this anyway.
- **GPSampler with `constant_liar`** — Optuna 4.2 added `constant_liar` to GPSampler, but NUTS posterior inference is ~1-2 hr per 200-trial run; too slow for short studies.
- **Raising `n_ei_candidates` alone** — extends TPE's usable range from ~30 to maybe 50 workers per study, not 100+. Cheap band-aid, not a structural fix.

### §3 Provider abstraction (Libcloud primary, raw SDK fallback)

Two CPU providers are empirically validated: **Hetzner CCX33** (Ashburn VA, $0.13/hr, no spot tier) and **AWS c7i.2xlarge spot** (us-east-1, $0.158/hr).

Per the 2026-04-18 benchmarks:

| Provider | $/matchup | Preemption | Setup | Pick when |
|---|---|---|---|---|
| Hetzner CCX33 | **$0.00109** | none | existing `scripts/cloud/*.sh` | default for most campaigns |
| AWS c7a.2xlarge us-west-2 | ~$0.00110 | ~3% with price-capacity-optimized | existing `scripts/cloud/aws/*.sh` | Hetzner capacity hits limit, or need regions Hetzner lacks |
| GCP n2d-std-8 spot | ~$0.0005 | ~5% | needs quota bump to 240 vCPU | very large campaigns ($500+) where cost dominates |

**Library choice: Apache Libcloud** — the only mature multi-cloud Python SDK covering both AWS and Hetzner via a unified `NodeDriver.create_node` / `destroy_node` API. Active but seeking-maintainers (March 2025 call-to-action; https://libcloud.apache.org/blog/2025/03/19/call-to-action-the-future-of-libcloud.html) — tolerable risk because AWS and Hetzner APIs are stable.

**Fallback: Pulumi-Python** if Libcloud is archived. Declarative state + `pulumi destroy` nukes bursts cleanly.

**Skip entirely**: SkyPilot, dstack, Modal, Covalent (no Hetzner support — all are GPU/AI-focused); Ray `ray up` (100-LOC custom node_provider.py required for Hetzner); Kubernetes (overkill for single-operator bursts). Full rejected-alternatives chain in `experiments/cloud-benchmark-2026-04-18/SCALING.md`.

### §4 Campaign manager (`src/starsector_optimizer/campaign.py`)

New Python daemon. Reads a YAML config, spawns worker pool, assigns workers to studies, tracks cost, auto-shuts down at budget cap.

**Campaign YAML (reference, subject to spec finalization):**
```yaml
campaign:
  name: phase5f-hammerhead-regime-sweep
  budget_usd: 100.0
  provider: hetzner
  region: ash
  max_concurrent_workers: 30
  worker_type: ccx33

  # Auto-termination across studies
  global_auto_stop:
    on_budget: hard    # hard-stop at budget_usd
    on_plateau: true   # stop study if best-fitness plateau

studies:
  - hull: hammerhead
    regime: early
    seeds: [0, 1, 2]      # 3 independent studies per (hull, regime)
    budget_per_study: 500
    workers_per_study: 8
    sampler: tpe
  - hull: hammerhead
    regime: mid
    seeds: [0, 1, 2]
    budget_per_study: 500
    workers_per_study: 8
    sampler: tpe
  # ... etc
```

**Execution model:**
1. Campaign manager bakes (or pulls) a pre-baked provider image with game + deps.
2. Launches `max_concurrent_workers` instances via Libcloud.
3. Maintains per-study queue (Redis list keyed by study_id).
4. Each worker connects to orchestrator over Tailscale/WireGuard, pulls its assigned study's work queue.
5. Workers run the existing `scripts/run_optimizer.py --hull X --study-db X.db` locally, but with ask/tell delegated to a Redis queue protocol (thin wrapper around existing `StagedEvaluator`).
6. Worker periodically `rsync study.db` back to orchestrator. On preemption, next worker for that study restores from last sync and continues (`load_if_exists=True`).
7. Orchestrator tracks cost in a ledger (summed active worker-hours × rate). Hard-caps at `budget_usd`; soft-warns at 50%, 80%, 95%.
8. Per-study auto-terminate on best-fitness plateau: 3 consecutive 50-trial buckets with slope < ε. Releases workers to other studies.

**State on orchestrator (single file tree):**
```
~/starsector-campaigns/<campaign-name>/
├── campaign.yaml                      (user-provided)
├── manifest.json                      (study metadata, manager state)
├── ledger.jsonl                       (append-only cost events)
├── studies/
│   ├── hammerhead-early-seed0/
│   │   ├── study.db                   (Optuna SQLite, rsync'd from worker)
│   │   ├── evaluation_log.jsonl       (per-trial audit trail)
│   │   └── status.json                (running/plateau/done)
│   ├── hammerhead-early-seed1/
│   │   └── ...
│   ...
└── logs/
    └── campaign.log                   (manager daemon log)
```

### §5 Pre-baked provider images (~80 savings per 200-worker burst)

Bootstrap cost today: ~5 min per worker (cloud-init + rsync 551 MB game + uv sync). At 200 workers, this is ~3 hours of wasted wall-clock (parallelism capped by uplink). Pre-bake via Packer:

- **Hetzner**: `packer-plugin-hcloud` + `amazon-ebs`-style builder. Output: a private snapshot, reference by ID in `hcloud server create --image=<id>`. Rebuild on game updates or major dep changes (quarterly). Storage: ~$0.03/month for a 4 GB image. Cold-start: ~30s.
- **AWS**: `amazon-ebs` Packer builder. Output: private AMI. Reference in Launch Template. Cold-start: ~45s.
- **Skip warm pools** at $1000 scale (EBS costs ~$16/mo for 50 stopped instances — not worth it for weekly bursts). Worth it at $10k+ scale.
- **Skip FSR** (EBS Fast Snapshot Restore) — volume creation credits cap at 10 concurrent, breaks at 50-worker bursts. Lazy-loading is fine for 551 MB.

### §6 Cost discipline (non-negotiable at scale)

**Hard-stop mechanisms** (all active simultaneously):
1. Per-campaign `budget_usd` hard cap in `campaign.yaml` — manager daemon refuses new worker launches at 95%, terminates all at 100%.
2. Per-worker lifetime cap (default 4 hours) — workers self-terminate via `shutdown -h`.
3. Tag-based sweeper cron — runs every 15 min, terminates any worker tagged `Project=starsector-campaign-*` that has been alive longer than `max_lifetime_hours`.
4. CloudWatch billing alarm (AWS) / Hetzner spending alert — orthogonal backstop.

**Teardown discipline:**
- Every campaign launch prints the teardown command in its first log line. Copy-pasteable.
- Campaign manager exit handler (SIGTERM/SIGINT/SIGHUP) calls teardown on all tagged resources.
- Final-audit script confirms zero tagged resources after campaign exit.
- The existing `scripts/cloud/teardown.sh` (Hetzner) and `scripts/cloud/aws/teardown.sh` (AWS) remain authoritative reference for tag-based cleanup.

**Rejected**: "trust the user to clean up" — one forgotten 200-worker campaign burns a $200/day idle cost.

### §7 Spot preemption & idempotency

Matchup dispatch protocol (workers ↔ orchestrator):
1. Worker pulls `(study_id, trial_number, opponent_id)` from its study's Redis queue.
2. Worker writes `combat_harness_queue.json.data` locally, runs combat, writes result to local eval_log.jsonl.
3. Worker POSTs result to orchestrator HTTP endpoint with the same `(study_id, trial_number, opponent_id)` key.
4. Orchestrator dedups — if already received for this key, drops silently.

Preemption scenarios:
- **Worker dies mid-matchup**: study's Redis visibility-timeout (120s) returns the work item. Next worker picks it up. Matchup re-runs — idempotent because `trial_number` is fixed.
- **Worker dies after matchup, before POST**: study's queue visibility-timeout still fires. Next worker re-runs the matchup. Result is computed twice but only one is told to study (dedup at orchestrator).
- **Orchestrator dies**: study state is on orchestrator disk. On restart, manager reads `manifest.json`, resumes. Workers reconnect via Redis (queue survives).

Preemption rate targets:
- Hetzner: 0% (no spot tier).
- AWS c7a us-west-2 with price-capacity-optimized + Capacity Rebalancing: ~3%.
- GCP n2d-std-8 spot: ~5%.

All three keep re-run overhead <10% of budget. Acceptable.

### §8 Scaling targets

$1000 budget at Hetzner $0.13/hr = 7692 instance-hours. At 64 matchups/hr/instance = **492,000 matchups** = **~49,000 builds** (at 10 matchups/build).

Allocation modes:
- **Mode A (go wide)**: all 200 hulls × 1 regime × 250 builds → broad catalog coverage.
- **Mode B (go deep + regime, recommended for Phase 5F validation)**: 40 hulls × 4 regimes × 300 builds → comprehensive for priority hulls.
- **Mode C (ensemble)**: 15 hulls × 4 regimes × 5 seeds × 150 builds → robust uncertainty estimates.

Per-study sweet spot is 500-1500 builds (diminishing returns above 1500). Total studies at $1000: **50-100**.

### §9 Diminishing-returns auto-termination

Per-study plateau detector: best-fitness trace binned into 50-trial windows. If the last 3 bins all have slope < 0.01 Δfitness per trial, study terminates early. Releases workers for reallocation.

Calibration from existing `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl`: plateau typically emerges around trial 800-1200 for Hammerhead-default search-space. Budget 1500 gives most studies enough to plateau naturally.

**Rejected**: fixed per-study budget. Wastes spend on studies that converged at 500 trials and burns ceiling on studies that are still improving at 1500.

## Dependencies

- **Phase 5D** (complete) — EB shrinkage works with current 2-JVM workers; no dependency on federation.
- **Phase 5F** (planned) — `(hull, regime)` is the natural federation unit. Phase 5F's `RegimeConfig` mask needs to be part of the campaign YAML schema.
- **None of Phase 7/8/9** — those come later; Phase 6 must ship first since they all need scale-out validation.

**Repo-level dependencies:**
- Existing `InstancePool` (Phase 3) — worker-local parallelism stays as-is. Only its **launcher** gets a cloud variant.
- Existing `StagedEvaluator` (Phase 4) — already async-friendly; only the dispatch queue source changes from local file to Redis.
- Existing `scripts/cloud/*.sh` (2026-04-12 infrastructure) — reused and extended.
- Existing `src/starsector_optimizer/optimizer.py` — no changes needed; it's already parameterized over `InstancePool`.

## Deliverables

1. **`src/starsector_optimizer/campaign.py`** — campaign manager daemon (~500 LOC):
   - `CampaignConfig` (frozen dataclass, loaded from YAML)
   - `CampaignManager` (main orchestration loop)
   - `CostLedger` (append-only JSONL + real-time sum)
   - `PlateauDetector` (per-study auto-stop)
   - `WorkerAllocator` (pool management, provider-agnostic via Libcloud)
   - Entry point: `scripts/run_campaign.py`

2. **`src/starsector_optimizer/cloud_provider.py`** — provider abstraction:
   - `CloudProvider` (ABC): `create_workers(n, image_id, tag) → list[WorkerHandle]`, `destroy_workers(handles)`, `list_workers(tag)`, `get_cost_rate_per_hour(worker_type)`
   - `HetznerProvider` — wraps `hcloud-python`
   - `AWSProvider` — wraps `boto3` (migrate to Libcloud post-MVP)
   - `MockProvider` — for offline tests

3. **`src/starsector_optimizer/worker_agent.py`** — on-worker Python script:
   - Connects to orchestrator Redis
   - Runs the existing `run_optimizer.py` with queue-backed `InstancePool`
   - Periodically rsyncs study.db back
   - Self-terminates on `max_lifetime_hours`

4. **`scripts/cloud/bake_image.sh`** — Packer wrapper for both Hetzner + AWS. One command rebuilds the baked image on game or dep update.

5. **`scripts/cloud/federation/`** — new subdirectory:
   - `launch_campaign.sh` (wraps `run_campaign.py`)
   - `status.sh` (campaign-wide live status)
   - `teardown.sh` (nuke all resources for a campaign)
   - `final_audit.sh` (post-campaign resource leak check)

6. **`docs/specs/29-campaign.md`** — formal spec for `CampaignConfig`, `CampaignManager`, YAML schema, protocol between orchestrator and workers.

7. **`.claude/skills/cloud-worker-ops.md`** — skill / SOP for running campaigns. Invoked by future Claude sessions when the user asks to run or debug cloud campaigns. Includes: preflight checks, launch commands, monitoring, cost ceiling discipline, teardown verification, failure recovery recipes.

8. **`experiments/cloud-campaign-validation/`** — three validation runs:
   - $10 smoke test: 1 study × 8 workers × 1 hour (Hetzner). Validates linear throughput.
   - $100 breadth test: 10 studies × 8 workers × ~2 hours (Hetzner). Validates federation + cost ledger.
   - $200 real campaign: Phase 5F regime validation on 5 hulls × 4 regimes × 1 seed × 500 trials. Produces the actual Phase 5F evidence the user is waiting for.

## Testing

- **Unit**: `CampaignManager` plateau detector (synthetic traces), cost ledger monotonicity, `CloudProvider` mock passes a reference scenario.
- **Integration**: $10 smoke test must complete within 1 hour and produce a populated `study.db` with ≥100 trials.
- **Cost-cap test**: inject a fake $100/hr worker into ledger; manager must terminate within 1 minute of crossing budget.
- **Preemption test** (AWS only): deliberately terminate a worker mid-study; another worker should pick up the same trial within 2 minutes and complete it.
- **Teardown audit**: after any campaign, `final_audit.sh` must report zero tagged resources.

## Non-obvious implementation notes

1. **Don't put the study.db on NFS.** Optuna explicitly warns SQLite does not handle NFS locks correctly. Keep study.db local to orchestrator, rsync from workers.

2. **Avoid `optuna.integration.RayTuneSearch` / `optuna.integration.Dask`.** They assume ownership of worker lifecycle. Our manager owns lifecycle; Optuna should see a flat local study.

3. **Ask/tell over Redis must preserve `TPESampler`'s state.** Approach: orchestrator holds the single `Study` object; workers only produce raw `CombatResult`; orchestrator calls `study.ask()` and `study.tell()` from a single thread. Workers never touch Optuna directly. This avoids the distributed-storage dance.

4. **The XRandR warmup fix must be in the baked image.** Without it, LWJGL crashes on first Starsector launch. The `_start_xvfb` patch (already in `instance_manager.py` as of 2026-04-18) plus `x11-xserver-utils` package (in `cloud-init.yaml`) together prevent this. Validation: baked image passes `./starsector.sh` launch test in Packer's post-build hook.

5. **Tailscale/WireGuard over public IPs for worker↔orchestrator.** Prevents exposing Redis to the internet. Free tier of Tailscale covers 100 devices.

6. **Per-study seed should propagate into heuristic warm-start.** Currently `warm_start()` is deterministic from the hull data — seeds need to be plumbed through so independent seed studies don't all warm-start to the same 500 heuristic trials.

## References

- Benchmark validating CPU-cloud viability + XRandR fix: `experiments/cloud-benchmark-2026-04-18/RESULTS.md`
- Scaling research (providers, Optuna at scale, library comparison): `experiments/cloud-benchmark-2026-04-18/SCALING.md`
- Cloud deployment spec (updated 2026-04-18): `docs/specs/22-cloud-deployment.md`
- Optuna 4.2 gRPC storage proxy (300-worker benchmark): https://medium.com/optuna/distributed-optimization-in-optuna-and-grpc-storage-proxy-08db83f1d608
- Optuna JournalStorage + GrpcProxy broken (avoid this combo): https://github.com/optuna/optuna/issues/6084
- Apache Libcloud (primary library choice): https://libcloud.apache.org/
- Packer Hetzner plugin: https://developer.hashicorp.com/packer/integrations/hetznercloud/hcloud
- EC2 price-capacity-optimized allocation: https://aws.amazon.com/blogs/compute/introducing-price-capacity-optimized-allocation-strategy-for-ec2-spot-instances/
