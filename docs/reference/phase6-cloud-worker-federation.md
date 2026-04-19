# Phase 6 — Cloud Worker Federation

Status: **INFRASTRUCTURE SHIPPED** (2026-04-18; per-study fleet ownership added same day). `AWSProvider` ships with the per-fleet API (`provision_fleet` / `terminate_fleet` / `terminate_all_tagged` sweep backstop), two-tag scheme (`Project`+`Fleet`), `cloud_userdata.render_user_data` with IMDSv2 WORKER_ID override, `CostLedger`, Packer AMI template, Tier-1 `probe.sh` / `probe.py` (updated to new API), and Tier-2 wiring: `CampaignManager` is a pure supervisor (`_preflight` + `spawn_studies` + `monitor_loop` + campaign-wide-sweep teardown), and each study subprocess (`scripts/run_optimizer.py --worker-pool cloud`) owns its own fleet lifecycle — provisioning AND teardown. Tier-2 pipeline smoke defined in §11 is **code-ready; pending live operational authorization**. Sampler benchmark and Phase 7 prep campaign deferred to operational sessions against the smoke-validated pipeline. Renumbers the former Phase 6 (Structured Search-Space Representation) to Phase 7 — cloud federation is infrastructure that Phase 7's multi-week BoTorch build depends on for validation at scale.

## Budget staging

Phase 6 ships against an **$85 combined Phase 6 shakedown + sampler benchmark + Phase 7 prep-data budget** (larger campaigns come after Phase 7). All dollar figures in this doc are computed by `experiments/phase6-planning/cost_model.py` from the pinned provider constants (AWS c7a.2xlarge spot $0.15/hr, 122 matchups/hr/VM, 3% preemption); rerun the script after any pricing / throughput update rather than hand-editing numbers here.

**Line items** (source: `cost_model.py` `budget_rollup()`):

| Line item | Cost |
| --- | --- |
| Validation probe (2 VMs × 2 regions × 15 min) | $0.15 |
| Pipeline smoke (Tier-2.0 single-matchup ~$0.30 + Tier-2.5 ~$0.80 multi-worker per §11) | $1.20 |
| Sampler benchmark (2 hulls × 3 samplers × 1 hr) | $14.83 |
| Prep campaign (8 hulls × `early` × 600 trials, incl 3% preemption) | $60.79 |
| **Subtotal** | **$76.97** |
| Slack (reruns, retries, headroom) | $5.00 |
| **Recommended budget** | **$85.00** |

- **AWS primary** (c7a.2xlarge spot, us-east-1 + us-east-2) rather than Hetzner, because existing AWS account vCPU quota is already ample (1,280 spot vCPU across us-east-1 + us-east-2 = room for ~160 eight-vCPU instances, vs Hetzner default 10-VM project cap that requires a 1-2 day quota-upgrade ticket). AWS's ~$0.0012/matchup vs Hetzner's ~$0.0011 (13% premium) is covered by the budget. Hetzner stays documented as a post-Phase-7 scale-up path where its price advantage matters against $500+/$1000 spend.
- **Sampler benchmark runs BEFORE the prep campaign.** The winning sampler drives the `sampler:` field of the prep campaign YAML. Decision criteria are pinned in §10 before the benchmark launches (no hindsight rules).
- **Packer pre-bake is in scope even at $85** (reversal from an early scoping sketch that deferred it). Rationale in §5. AWS AMIs are region-scoped, so the build is done in us-east-1 and `aws ec2 copy-image`'d to us-east-2 (~5 min, one-time).
- **Two-region spread** across us-east-1 + us-east-2 for spot-pool diversity. EC2 Fleet with `price-capacity-optimized` allocation + `CapacityRebalancing` drops preemption rate to ~3% (validated in the 2026-04-18 benchmark).
- **Prep campaign target**: 8 hulls × `early` × 1 seed × 600 trials ≈ 48,000 matchups ≈ **$60.79**. Produces the cross-hull early-regime data Phase 7's attribute/mode kernels need to validate transfer claims.
- **Why early regime, not endgame**: late-regime optima largely recover published community meta (e.g. `shrouded_lens` / `fragment_coordinator` archetypes the 2026-04-17 Hammerhead run concentrated on); early regime is where the optimizer has room to find genuinely novel builds *and* is a stricter Phase 7 kernel test under a tighter feasible set. Endgame follow-up is a cheap (~$15-20) supplementary run if Phase 7 development shows a gap.
- **Wall-clock**: ~4.1 hr prep (script: `prep_scenario().hours_per_study`) + ~1 hr benchmark + ~2 hr smoke, across 1-2 calendar days.
- **Sensitivity** (from `cost_model.py sensitivity_analysis()`): dropping prep to 500 trials/hull saves $10 ($50.66 vs $60.79); dropping to 7 hulls saves ~$7.60. If $85 is too aggressive, 500 trials × 8 hulls fits comfortably in $75.

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

### §3 Provider abstraction (boto3 direct; Libcloud deferred)

Two CPU providers are empirically validated: **AWS c7a.2xlarge spot** (shipped in Phase 6 MVP) and **Hetzner CCX33** (`HetznerProvider` stub until $500+ campaigns).

Per the 2026-04-18 benchmarks:

| Provider | $/matchup | Preemption | Setup | Pick when |
|---|---|---|---|---|
| **AWS c7a.2xlarge us-east-1+us-east-2 spot** | ~$0.00123 | ~3% with price-capacity-optimized | shipped `AWSProvider` (boto3 direct) | **default for Phase 6 MVP** — ample existing quota (640 spot vCPU/region × 2 regions), no multi-day ticket wait |
| Hetzner CCX33 | **$0.00109** | none | `HetznerProvider` stubbed — implement at $500+ | $500+ campaigns where 13% cost savings matter; requires quota upgrade from default 10 VMs (1-2 business day ticket) |
| GCP n2d-std-8 spot | ~$0.0005 | ~5% | not implemented | very large campaigns ($500+) where cost dominates |

**Why AWS primary at $85**: the original Hetzner-first framing optimized for lowest $/matchup, but at $85 the dominant operator cost is the 1-2 day wait for Hetzner's quota-upgrade ticket. The user's AWS account already has 1,792 spot vCPU across 4 US regions (640 each in us-east-1 / us-east-2, 256 each in us-west-1/2) — room for ~224 concurrent 8-vCPU spot instances with zero lead time. At $85 budget (covering AWS's 13% premium plus the $14.83 sampler benchmark), we trade ~$10 of cost for eliminating a multi-day external blocker. The trade inverts above $500 where the cost delta exceeds a human-day of engineering time.

**Library choice: boto3 direct**, behind the `CloudProvider` ABC. Phase 6 ships AWS only; an Apache Libcloud wrapper can slot behind the ABC later without refactoring callers if cross-provider unification becomes valuable. Libcloud was considered but dropped from MVP — it's a unified-API abstraction with zero users while Hetzner is stubbed, and boto3 is fewer moving parts.

**Skip entirely**: SkyPilot, dstack, Modal, Covalent (no Hetzner support — all are GPU/AI-focused); Ray `ray up` (100-LOC custom node_provider.py required for Hetzner); Kubernetes (overkill for single-operator bursts). Full rejected-alternatives chain in `experiments/cloud-benchmark-2026-04-18/SCALING.md`.

### §3.5 Availability and region strategy

**AWS (primary for Phase 6 MVP).** Account quota verified 2026-04-18:

| Region | Spot vCPU | On-Demand vCPU | 8-vCPU spot VMs available |
| --- | --- | --- | --- |
| us-east-1 | 640 | 640 | 80 |
| us-east-2 | 640 | 1920 | 80 |
| us-west-2 | 256 | 256 | 32 |
| us-west-1 | 256 | 256 | 32 |
| **Total** | **1792** | **3072** | **~224** |

Either us-east-1 or us-east-2 alone can host the 96-VM target. **Default deployment spreads across us-east-1 + us-east-2** (48 VMs each) for two-region spot-pool diversity — mitigates correlated preemption if one region's c7a.2xlarge pool tightens.

AWS failure modes are different from Hetzner:
- **Spot preemption mid-run** (~3% with `price-capacity-optimized` + `CapacityRebalancing`) — handled by Redis visibility-timeout + idempotent `(study_id, trial_number, opponent_id)` keys (§7).
- **Spot-pool depletion at launch** — mitigated by EC2 Fleet with diversified instance-type list (`c7a.2xlarge` + `c7i.2xlarge` + `c7a.4xlarge` + `c7i.4xlarge`; the 4xlarge variants fit 4 JVMs per VM at the same per-matchup cost).
- **AMIs are region-scoped** (unlike Hetzner global snapshots). Build the Packer AMI in us-east-1, `aws ec2 copy-image --source-region us-east-1 --source-image-id ami-... --region us-east-2` to replicate (~3-5 min, one-time).

**Pre-flight validation probe** (runs 24h before the campaign; costs ~$1):
1. Launch 2 spot instances per target region from the production AMI.
2. Confirm boot + `starsector.sh` launch test + `uv run python -c 'from starsector_optimizer.optimizer import _shape_fitness'`.
3. Record per-region health signal in `probe_report.json`.
4. Tear down.

Catches AMI-copy errors and region-scoped cloud-init divergences before the campaign commits $60.79 of spend.

**Graceful degradation**: if day-of spot-pool depletion limits provisioning to 48-60 VMs instead of 96, the campaign manager allocates remaining workers round-robin across studies and accepts ~2× wall-clock. It does **not** reduce per-study budget (changes the experimental design) and it does **not** abort. Partial fleet is a latency problem, not a correctness problem. `min_workers_to_start` default 48 is the hard floor; below that the manager waits or aborts.

---

**Hetzner (documented fallback for $500+ scale-up).** CCX33 capacity is per-datacenter; Hetzner does not publish inventory. Validated during the 2026-04-18 Phase 5E run (fell back `ash` → `hil` on `resource_unavailable`):

- Bursts of 20-50 CCX33 per location are reliable.
- 100+ per location intermittently fails with `resource_unavailable` at provision time.
- Once provisioned, a CCX33 is not preempted — capacity risk is concentrated entirely at provision time.
- Snapshots are **globally scoped** (one build, usable in any location — unlike AWS AMIs).
- Project VM quota is account-wide (default 10; upgrade to ~100 is typically approved within 1-2 business days via ticket stating multi-location intent).

For Hetzner campaigns, the ordered-locations YAML field (default `[ash, hil, hel1, fsn1, nbg1]`) rotates on `resource_unavailable` or 429. Quota-ticket wording, ready to paste:

> We run burst CPU-only workloads (automated game-simulation research) in ~3-4 hour windows. Requesting project VM quota of 100 CCX33, which we will spread across Ashburn / Hillsboro / Helsinki / Falkenstein so no single datacenter gets hit with more than ~25 concurrent instances at any time.

### §4 Campaign manager (`src/starsector_optimizer/campaign.py`)

New Python daemon. Reads a YAML config, spawns worker pool, assigns workers to studies, tracks cost, auto-shuts down at budget cap.

**Campaign YAML (reference, subject to spec finalization):**
```yaml
campaign:
  name: phase7-prep-early-2026-0X
  budget_usd: 70.0
  provider: aws
  regions: [us-east-1, us-east-2]           # ordered; EC2 Fleet diversifies AZ within each
  instance_types: [c7a.2xlarge, c7i.2xlarge, c7a.4xlarge, c7i.4xlarge]  # diversify for spot-pool
  spot_allocation_strategy: price-capacity-optimized
  capacity_rebalancing: true
  max_concurrent_workers: 96                # 12 VMs × 8 studies at 2 JVMs/VM (48 per region)

  # Graceful degradation — accept partial fleet rather than abort
  min_workers_to_start: 48                  # hard floor; below this, wait or abort
  partial_fleet_policy: proceed_half_speed

  # Auto-termination across studies
  global_auto_stop:
    on_budget: hard    # hard-stop at budget_usd
    on_plateau: true   # stop study if best-fitness plateau

studies:
  # Phase 7 prep: 8 hulls spanning F→D→C→CAP, all early regime, 1 seed each.
  - {hull: wolf,           regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: lasher,         regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: hammerhead,     regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: sunder,         regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: eagle,          regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: dominator,      regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: falcon,         regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
  - {hull: onslaught_mk1,  regime: early, seeds: [0], budget_per_study: 600, workers_per_study: 12, sampler: tpe}
```

**Execution model:**
1. Campaign manager bakes (or pulls) a pre-baked provider image with game + deps.
2. `CampaignManager._preflight` (workstation-side): `tailscale ip -4` non-empty, `redis.ping()` on tailnet IP (or userspace-mode `tailscale serve` proxy verified), **SCAN+DEL `queue:<project_tag>:*` + `worker:<project_tag>:*` to flush any stale state from a prior run with the same name**, `aws sts get-caller-identity` alive, `tailscale_authkey_secret` starts with `tskey-auth-`. Failure → clear remediation + non-zero exit BEFORE any subprocess is spawned.
3. CampaignManager spawns one subprocess per `(study_idx, seed_idx)` pair with `--worker-pool cloud`. Each subprocess gets per-study env: `STARSECTOR_BEARER_TOKEN` (fresh `secrets.token_urlsafe(32)`), `STARSECTOR_WORKSTATION_TAILNET_IP`, `STARSECTOR_TAILSCALE_AUTHKEY`, `STARSECTOR_PROJECT_TAG=starsector-<name>`. Env dicts are NEVER logged.
4. **Study subprocess owns its fleet**: constructs `WorkerConfig` → renders UserData → `provider.provision_fleet(fleet_name=study_id, project_tag=project_tag, ...)` → `CloudWorkerPool.setup()` starts Flask listener + janitor → runs `optimize_hull`. On any exit path: `finally: provider.terminate_fleet(fleet_name=study_id, project_tag=project_tag)`.
5. Each worker VM joins Tailscale via cloud-init (authkey on stdin, never argv), writes `/etc/starsector-worker.env` with every `WorkerConfig` field, overrides `worker_id` from IMDSv2 (EC2 instance ID, via `sed -i` + append so there's exactly one env line), then `systemctl start starsector-worker.service`.
6. Each worker runs `worker_agent.py` (NOT `run_optimizer.py` — the optimizer runs only on the orchestrator). Reads `WorkerConfig` from env via `dataclasses.fields(WorkerConfig)` iteration + `typing.get_type_hints()` coercion. Spawns **`matchup_slots_per_worker` Redis consumer threads** sharing one `LocalInstancePool(num_instances=matchup_slots_per_worker)` — each thread does `BRPOPLPUSH source→processing → pool.run_matchup → POST /result → LREM processing`, with the pool's internal free-instance queue serializing each `run_matchup` onto a distinct JVM. Without threading the VM would use only 1 JVM regardless of `num_instances`. A dedicated heartbeat thread writes `worker:<project_tag>:<worker_id>:heartbeat` every 30s with `timestamp` + `load_avg_{1,5,15}min` + `cpu_count` so the orchestrator can verify the fleet shape fits the box. Redis queue keys are namespaced by `project_tag` (`queue:<project_tag>:<study_id>:source` + `:processing`) so cross-campaign state leakage is impossible.
7. Redis processing-list janitor runs on the study subprocess every `janitor_interval_seconds` and re-queues items stuck longer than `visibility_timeout_seconds`. No study.db ever leaves the workstation.
8. CampaignManager `monitor_loop` tracks cost in a ledger (summed active worker-hours × rate). Hard-caps at `budget_usd`; soft-warns at 50%/80%/95% (configurable via `ledger_warn_thresholds`).
9. Per-study auto-terminate on absolute `budget_per_study` trial cap. Plateau detection (3 consecutive 50-trial buckets with slope < ε) is deferred to a follow-up commit — not load-bearing for "does the infra work."
10. **Teardown in four layers**: (i) study subprocess `finally: terminate_fleet` (targeted); (ii) `CampaignManager.run()` `finally: terminate_all_tagged(project_tag)` (campaign-wide sweep backstop); (iii) `atexit.register(self.teardown)` (crash paths bypassing `finally`); (iv) `launch_campaign.sh` `trap EXIT` runs `teardown.sh` + `final_audit.sh` (SIGKILL, host reboot).

**State on orchestrator (single file tree):**
```
~/starsector-campaigns/<campaign-name>/
├── campaign.yaml                      (user-provided)
├── manifest.json                      (study metadata, manager state)
├── ledger.jsonl                       (append-only cost events)
├── studies/
│   ├── hammerhead-early-seed0/
│   │   ├── study.db                   (Optuna SQLite, orchestrator-local)
│   │   ├── evaluation_log.jsonl       (per-trial audit trail)
│   │   └── status.json                (running/plateau/done)
│   ├── hammerhead-early-seed1/
│   │   └── ...
│   ...
└── logs/
    └── campaign.log                   (manager daemon log)
```

### §5 Pre-baked provider images (in scope from day 1, even at $85)

Bootstrap cost on cold cloud-init: ~5 min per worker (apt + rsync 551 MB game + uv sync + XRandR warmup). Packer pre-bake drops this to ~45s on AWS, ~30s on Hetzner.

**Dollar savings at $85 / 96 VMs are small** (~$1) — but that is the wrong framing. The load-bearing arguments that put Packer in scope from day 1:

1. **Tail-latency reduction**: at 96 VMs with independent 5-min cold starts, the *last* VM to boot gates campaign start — and burst provisioning amplifies transient apt / download failures into correlated retries. Packer's deterministic ~45s boot collapses this tail.
2. **Reliability at burst**: bulk 429s from apt repos, PyPI, or the game-data rsync source are a real failure mode at 50+ concurrent cloud-inits. Packer removes every one of those external dependencies from the hot path.
3. **Cheap retry after a broken worker**: with Packer, replacing a dead VM costs ~45s of wall-clock, not 5 min. Matters disproportionately during the tail of a campaign when most studies have terminated and a single stuck study is gating exit. Also critical for spot-preemption recovery on AWS.
4. **One-time investment amortizes**: ~30 min to write the Packer template + build + test-boot; rebuild quarterly on game or major dep updates (~5 min). Storage cost: ~$0.06/month for a 4 GB AMI.
5. **The $10k+ campaign doesn't want a different image path.** Building Packer in for the $85 run means the $500 and $1000 campaigns inherit a tested image with zero extra work.

**Provider specifics**:
- **AWS (primary)**: `amazon-ebs` Packer builder. Output: private AMI, referenced in Launch Template or EC2 Fleet config. Cold-start: ~45s. AMIs are **region-scoped** — build in us-east-1, then `aws ec2 copy-image --source-region us-east-1 --source-image-id ami-... --region us-east-2` (~3-5 min, one-time; repeat on every Packer rebuild). Note: the copy produces a different AMI ID in the target region; both IDs are recorded in the campaign's `ami_ids_by_region:` YAML field.
- **Hetzner (documented fallback)**: `packer-plugin-hcloud` builder. Output: a private snapshot, reference by ID in `hcloud server create --image=<id>`. Snapshots are **globally scoped** (unlike AWS AMIs) — one build, usable in any location.

**Multi-region test-boot** (AWS): the validation probe VMs from §3.5 double as image validation — one VM per target region boots from that region's AMI ID and runs the post-build smoke test (`starsector.sh` launch + `_shape_fitness` import). Catches region-scoped cloud-init divergences or AMI-copy corruption before the campaign commits $60.79.

**Skip warm pools** at $1000 scale (EBS costs ~$16/mo for 50 stopped instances — not worth it for weekly bursts). Worth it at $10k+ scale.

**Skip FSR** (EBS Fast Snapshot Restore) — volume creation credits cap at 10 concurrent, breaks at 50-worker bursts. Lazy-loading is fine for 551 MB (and Packer bakes it into the image anyway).

### §6 Cost discipline (non-negotiable at scale)

**Hard-stop mechanisms** (Phase 6 MVP ships 1+2; 3+4 are deferred as orthogonal operational backstops):
1. Per-campaign `budget_usd` hard cap in `campaign.yaml` — `CostLedger.record_heartbeat` raises `BudgetExceeded` at 100%; `CampaignManager.run()`'s `finally` block triggers teardown.
2. Per-worker lifetime cap (default **6 hours**; 6 covers the prep campaign's 4.1-hour wall-clock per `cost_model.py::prep_scenario`) — workers self-terminate.
3. **Deferred**: tag-based sweeper cron every 15 min. Operational backstop layer above the three in-process layers.
4. **Deferred**: CloudWatch billing alarm (AWS). Independent provider-side budget.

**Teardown discipline** (three layers, all active):
- In-process `try/finally` in `CampaignManager.run()` calls `provider.terminate_all_tagged` + asserts `list_active == []` with one retry.
- `atexit.register(self.teardown)` in `CampaignManager.__init__` catches crash paths that bypass `finally` (swallows exceptions; idempotent via `_teardown_done` flag).
- `launch_campaign.sh` wraps the Python invocation with `trap EXIT` that re-runs `final_audit.sh` unconditionally.

**Rejected**: "trust the user to clean up" — one forgotten 200-worker campaign burns a $200/day idle cost.

### §7 Spot preemption & idempotency

Matchup dispatch protocol (workers ↔ orchestrator):
1. Worker `BRPOPLPUSH`es a matchup from its study's source Redis list onto the processing list.
2. Worker runs the matchup through its local `LocalInstancePool` (2 JVMs per c7a.2xlarge VM).
3. Worker POSTs the result to the study-subprocess Flask listener with body `{matchup_id, result, bearer_token}`.
4. Orchestrator dedups by `matchup_id` — first POST → 200; subsequent → 409; bad bearer → 401. `matchup_id` is `f"{study_id}__{trial_number}__{opponent_id}"` and is globally unique across all studies.
5. Worker `LREM`s the processing-list entry on 200/409. Janitor thread on orchestrator re-queues processing-list entries older than `visibility_timeout_seconds` (default 120s).

Preemption scenarios:
- **Worker dies mid-matchup**: `visibility_timeout_seconds` elapses, janitor `LPUSH`es back onto source. Next worker picks it up. Matchup re-runs — idempotent via the `matchup_id` dedup.
- **Worker dies after matchup, before POST**: same flow. Result is computed twice; only one POST ever succeeds (409 on the second).
- **Orchestrator dies**: study state is on orchestrator disk. On restart, `load_if_exists=True` resumes. Workers reconnect via Redis (queue survives).

Preemption rate targets:
- AWS c7a.2xlarge us-east-2 with price-capacity-optimized + Capacity Rebalancing: ~3%.
- Hetzner CCX33: 0% (no spot tier; stubbed-until-$500+).

Re-run overhead <10% of budget. Acceptable.

### §8 Scaling targets

AWS c7a.2xlarge spot at ~$0.15/hr, 2 JVMs per VM (3-cores-per-JVM sizing rule), per-VM throughput ~122 matchups/hr → $0.00123/matchup. Budget tiers:

**$85 (Phase 6 shakedown + sampler benchmark + Phase 7 prep, current target)** — all figures computed by `experiments/phase6-planning/cost_model.py`; full breakdown in the Budget staging section at top.
- Sampler benchmark: **2 hulls × 3 samplers × 1 hr ≈ $14.83** (precedes prep; selects the best sampler).
- Prep campaign: **8 hulls × `early` × 1 seed × 600 trials = 48,000 matchups ≈ $60.79** (incl 3% preemption).
- Smoke + probe + slack: $1.35 + $5.00.
- Wall-clock: ~1 hr benchmark + ~4.1 hr prep (parallel across 8 studies), across 1-2 calendar days.
- Binding constraint: TPE saturates at 24 workers per study (§1) → 12 VMs/study cap; adding more VMs past 96 helps nothing until there are more studies.
- Why early regime: late/endgame recovers community meta; early is the zone where novel-build discovery is load-bearing and also the tighter Phase 7 kernel test (tighter feasible set → stronger transfer claim). Endgame follow-up is ~$15-20 supplementary if Phase 7 development reveals a gap.

**$500+ (first real Phase 7 validation campaign, future)** — ~400,000 matchups on AWS / ~460,000 on Hetzner. This is the threshold where Hetzner's ~13% cost advantage (~$60 saved at $500) exceeds the human-day of engineering overhead to file the Hetzner quota-upgrade ticket and switch provider. Re-evaluate: AWS for latency, Hetzner for total spend.

**$1000 (full Phase 5F regime sweep, future)** — ~490,000 matchups on Hetzner ≈ ~49,000 builds. Allocation modes:
- **Mode A (go wide)**: 200 hulls × 1 regime × 250 builds → broad catalog coverage.
- **Mode B (go deep + regime)**: 40 hulls × 4 regimes × 300 builds → comprehensive for priority hulls.
- **Mode C (ensemble)**: 15 hulls × 4 regimes × 5 seeds × 150 builds → robust uncertainty estimates.

Per-study sweet spot is 500-1500 builds (diminishing returns above 1500). Total studies at $1000: **50-100**.

### §9 Diminishing-returns auto-termination

Per-study plateau detector: best-fitness trace binned into 50-trial windows. If the last 3 bins all have slope < 0.01 Δfitness per trial, study terminates early. Releases workers for reallocation.

Calibration from existing `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl`: plateau typically emerges around trial 800-1200 for Hammerhead-default search-space. Budget 1500 gives most studies enough to plateau naturally.

**Rejected**: fixed per-study budget. Wastes spend on studies that converged at 500 trials and burns ceiling on studies that are still improving at 1500.

### §10 Sampler benchmark — runs BEFORE the prep campaign

The prep campaign's `sampler:` field is determined by an empirical bake-off, not by assumption. Ordered this way so the Phase 7 prep data uses the best sampler we can identify within the budget.

**Scope** (2 hulls × 3 samplers × 1 hr each, matched parallelism + one scaling test):

| Sampler | Workers | VMs | Trials (1 hr) | Cost / hull | Cost (2 hulls) |
| --- | --- | --- | --- | --- | --- |
| TPE-24 (status quo) | 24 | 12 | ~146 | $1.85 | $3.71 |
| CatCMAwM-24 (matched) | 24 | 12 | ~146 | $1.85 | $3.71 |
| CatCMAwM-48 (scaling test) | 48 | 24 | ~293 | $3.71 | $7.42 |
| **Total** | | | | | **$14.83** |

Hybrid (`random → CatCMAwM → TPE`) is **excluded** from this benchmark — at a 1-hour budget the TPE-exploit stage doesn't get ≥200 trials to be meaningful. Revisit hybrid only once a long-budget run justifies its stage pacing.

**Hull selection**: 1 destroyer + 1 cruiser from the prep campaign's 8-hull list (e.g. `hammerhead` + `eagle`). Benefits:
- Cross-references directly against the prep campaign's TPE data at the same hulls.
- Two different fitness landscapes guard against a single-hull artifact (e.g. Hammerhead's quick-kill regime vs Eagle's attrition regime).

**Decision criteria (pinned before launch — no hindsight rules)**:
1. **Primary metric**: best Box-Cox-warped fitness at 1 hour per hull.
2. **Tiebreaker**: convergence rate — best fitness at T=30 min. Signals which sampler reaches "good" fastest, which matters most for the 8-hull prep where per-study budget could run short.
3. **Default to TPE-24** if a challenger doesn't clearly win on ≥1 hull AND stays within 5% on the other — status quo bias is appropriate since TPE is the better-validated path in this codebase.
4. **CatCMAwM-48 tiebreak**: if it wins wall-clock *and* matches TPE-24 on fitness within 3%, switch the prep campaign to 24 VMs/study × 8 studies = 192 VMs (within AWS quota of 224 after the us-west-1/2 dip). Halves prep wall-clock ~4.1 hr → ~2.1 hr at the same VM-hours / cost.

**Write the decision as a short REPORT.md** in `experiments/phase6-sampler-bench-2026-0X/` before launching the prep. Include: sampler × hull fitness table, 30-min + 60-min snapshots, the decision rule as applied, and the `sampler:` YAML value chosen for the prep.

### §11 Tier-2 pipeline smoke gate

**Purpose**: one real matchup makes the full round-trip workstation → Tailscale → Redis queue → cloud worker → `LocalInstancePool` (JVM) → Flask `POST /result` → orchestrator. Validates the intersection of every subsystem that Tier-1 probe skipped.

**Same code path as prep** — smoke and prep are both `launch_campaign.sh <yaml>`. No separate smoke driver ships.

**Pre-launch ops (operator, not code)**:
1. Tailscale running on the workstation — either `tailscale up` system-wide or `scripts/cloud/devenv-up.sh` for rootless userspace mode (no sudo, no kernel TUN). The CampaignManager preflight accepts both paths.
2. Tailscale tailnet policy file (`https://login.tailscale.com/admin/acls/file`) grants `tag:starsector-worker` → workstation on `tcp:6379` and `tcp:9000-9099`. Use grants syntax — Tailscale made grants GA as the preferred policy language; see `.claude/skills/cloud-worker-ops.md` preflight item 5 for the exact stanza. The editor has a **"Convert to grants"** button that rewrites legacy `acls` blocks.
3. Tailscale admin panel → generate an ephemeral + pre-approved auth key tagged `tag:starsector-worker`. Export as `TAILSCALE_AUTHKEY` (or drop into `.env` and `set -a; source .env; set +a`).
4. Redis reachable by workers over the tailnet. Kernel mode: `sudo systemctl edit redis-server`, override `ExecStart=` with `--bind 0.0.0.0` (or the tailnet IP explicitly). Userspace mode: `devenv-up.sh` sets up the `tailscale serve --tcp=6379 tcp://127.0.0.1:6379` proxy for you. Either path, the preflight confirms.

**Launch**: `set -a; source .env; set +a; scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml` (see `examples/smoke-campaign.yaml`: 1 study × `hammerhead` × `early` × `seeds=[0]` × `budget_per_study=2` × `workers_per_study=1` × `budget_usd: 2.0`).

**Gate criteria (ALL must hold)**:
- `launch_campaign.sh examples/smoke-campaign.yaml` exits 0.
- `scripts/cloud/final_audit.sh smoke` exits 0 (zero leaked resources across all 4 US regions).
- `~/starsector-campaigns/smoke/ledger.jsonl` contains ≥ 1 `worker_heartbeat` event.
- The Optuna study's SQLite (at the subprocess's `--study-db` path) contains exactly 1 `TrialState.COMPLETE`.

**Tier-2.5 multi-worker variant (post-Tier-2.0 pass)**: same code path, `examples/smoke-campaign-multiworker.yaml`. `workers_per_study: 3`, `matchup_slots_per_worker: 2` (default), `budget_per_study: 20`, `max_concurrent_workers: 3`, `budget_usd: 3.0`. Exercises the **total concurrency path** (pool semaphore sized to `workers × matchup_slots_per_worker = 6`), the threaded worker consumer loop (each VM drives 2 concurrent matchups), janitor re-queue under concurrent dispatch, POST dedup under duplicate results, backpressure. Additional gate: worker `load_avg_1min` (from the heartbeat hash) lands in `[3, 8]` — under-load or over-subscription indicates the fleet shape doesn't match `matchup_slots_per_worker`. Inspect via `redis-cli HGETALL worker:<project_tag>:<worker_id>:heartbeat`.

**Expected cost**: Tier-2.0 ~$0.30; Tier-2.5 ~$1.00. Fits the $1.20 "Pipeline smoke" line item in §Budget staging.

## Day-1 ordered actions

Ordered by lead time. The AWS-primary direction removes the Hetzner quota-ticket blocker that previously sat at position 1; no external blockers now gate campaign code.

1. **Build Packer AMI in us-east-1** (~30 min). Bake the combat harness, game files, `uv sync`, and the XRandR warmup fix into a private AMI. Tested via a post-build launch hook.
2. **`aws ec2 copy-image` to us-east-2** (~3-5 min, one-time). AWS AMIs are region-scoped; replicate to the second target region. Re-run on every Packer rebuild.
3. **Validation probe** (`scripts/cloud/probe.sh` + `scripts/cloud/probe.py`). Tier 1: exercises `AWSProvider.provision_fleet` (LaunchTemplate + SecurityGroup creation + instance launch + two-tag propagation) + `terminate_fleet` + `final_audit.sh`. Scope is fleet lifecycle only — **does not** SSH in, join Tailscale, hit Redis, or run a matchup (those are the pipeline smoke's job, Tier 2). Instance count is whatever `max_concurrent_workers // len(regions)` comes out to in the probe YAML. Cost: ~$0.05 for two c7a.2xlarge spot instances × 3-5 min wall-clock. Run 24h before any paid campaign.
4. **Campaign manager + orchestrator** (~500 LOC, the main Phase 6 implementation work). EC2 Fleet with `price-capacity-optimized` + CapacityRebalancing, Redis-backed study queues, graceful degradation. See Deliverables.
5. **Pipeline smoke** (~2 hr, ~$1.20): 1 study × 8 workers × 1 region. Confirms the full pipeline (orchestrator ↔ worker Redis BRPOPLPUSH + Flask POST, janitor re-queue, cost ledger, three-layer teardown, preemption replay).
6. **Sampler benchmark** (~1 hr, ~$14.83): 2 hulls × 3 samplers per §10. Writes REPORT.md with the chosen `sampler:` value.
7. **Run Phase 7 prep campaign** (~4.1 hr at 96 VMs; ~$60.79) using the benchmark-selected sampler.
8. **Final audit + Phase 7 handoff**: `final_audit.sh`, archive campaign output under `experiments/phase7-prep-early-2026-0X/`, write short REPORT.md for Phase 7 developer consumption.

## Dependencies

- **Phase 5D** (complete) — EB shrinkage works with current 2-JVM workers; no dependency on federation.
- **Phase 5F** (complete 2026-04-18) — `(hull, regime)` is the natural federation unit. Phase 5F's `RegimeConfig` + regime-scoped study naming is already in place; the campaign YAML only needs to reference `regime: early|mid|late|endgame` per study entry.
- **None of Phase 7/8/9** — those come later; Phase 6 must ship first since they all need scale-out validation.

**Repo-level dependencies:**
- `LocalInstancePool` (renamed from `InstancePool` in Phase 6) — worker-local parallelism stays as-is on cloud VMs (workers run `LocalInstancePool(num_instances=2)` per c7a.2xlarge). The ABC gains a sibling `CloudWorkerPool` that `StagedEvaluator` can consume without knowing which backend drives the matchup.
- Existing `StagedEvaluator` (Phase 4) — already async-friendly; only the dispatch queue source changes from local file to Redis.
- Existing `scripts/cloud/*.sh` (2026-04-12 infrastructure) — reused and extended.
- `src/starsector_optimizer/optimizer.py::StagedEvaluator` — refactored to accept the `EvaluatorPool` ABC instead of the concrete `InstancePool`; the pool owns worker-selection internally.

## Deliverables

1. **`src/starsector_optimizer/campaign.py`** — campaign manager (~400 LOC):
   - `CampaignConfig` + `StudyConfig` + `WorkerConfig` + `CostLedgerEntry` + `GlobalAutoStopConfig` (frozen dataclasses in `models.py`; `__repr__` redacts secrets)
   - `CampaignManager` (pure supervisor: `_preflight` → `spawn_studies` → `monitor_loop` → `terminate_all_tagged` sweep backstop). Does NOT own fleet lifecycle.
   - `CostLedger` (append-only JSONL + `fsync` per row + `BudgetExceeded` at hard cap)
   - Plateau detector deferred to follow-up commit
   - Entry point: `python -m starsector_optimizer.campaign <yaml>`
   - Campaign YAML `tailscale_authkey_secret` supports `${VAR}` env-substitution (field-scoped)

2. **`src/starsector_optimizer/cloud_provider.py`** — provider abstraction (boto3 direct, no Libcloud in MVP):
   - `CloudProvider` (ABC): `provision_fleet(*, fleet_name, project_tag, regions, ami_ids_by_region, instance_types, ssh_key_name, spot_allocation_strategy, target_workers, user_data) → list[str]`, `terminate_fleet(*, fleet_name, project_tag) → int` (targeted), `terminate_all_tagged(project_tag) → int` (campaign-wide sweep), `list_active(project_tag) → list[dict]`, `get_spot_price(region, instance_type) → float`
   - **Two-tag scheme**: every resource tagged `Project=<project_tag>` AND `Fleet=<fleet_name>`. LT/SG names are `f"{project_tag}__{fleet_name}"` (unique per study).
   - `AWSProvider` — boto3-direct EC2 Fleet with `price-capacity-optimized`; SG deletion retries past ENI-detach race (`_SG_DELETE_DEADLINE_SECONDS`).
   - `HetznerProvider` — stub; every method raises `NotImplementedError` until $500+ campaigns
   - Tests use `moto` for AWS mocking; no `MockProvider` class ships

3. **`src/starsector_optimizer/worker_agent.py`** — on-worker Python script:
   - Connects to orchestrator Redis over Tailscale; env-var-loaded `WorkerConfig`
   - Pulls matchups via `BRPOPLPUSH`, runs them through local `LocalInstancePool`, POSTs result to study-subprocess Flask listener
   - Self-terminates on `max_lifetime_hours`
   - Never imports `repair` (orchestrator-side boundary, enforced by test_worker_agent_does_not_import_repair)

4. **`src/starsector_optimizer/cloud_worker_pool.py`** — `CloudWorkerPool` implements `EvaluatorPool`:
   - Per-study Flask listener on `config.base_flask_port + study_idx` with exactly one route: `POST /result`
   - Redis reliable-queue (BRPOPLPUSH source→processing + janitor thread)
   - `run_matchup(matchup)` enqueues + blocks on `threading.Event` up to `result_timeout_seconds`
   - Bearer-token auth; dedup by `matchup_id`

5. **`scripts/cloud/bake_image.sh`** — Packer wrapper. Builds AMI in us-east-1 then `aws ec2 copy-image` to us-east-2.

6. **`scripts/cloud/`** — operator scripts: `launch_campaign.sh`, `status.sh`, `teardown.sh`, `final_audit.sh`, `probe.sh`, `bake_image.sh`, `packer/aws.pkr.hcl`. No `scripts/cloud/federation/` subdir; scripts live flat under `scripts/cloud/`.

7. **`docs/specs/22-cloud-deployment.md`** — rewritten for Phase 6 architecture (previously covered pre-Phase-6 Hetzner prototype). Preserves operationally load-bearing material: cloud-init package list, LWJGL/XRandR narrative, six Lessons Learned items. No new spec 29 needed.

8. **`.claude/skills/cloud-worker-ops.md`** — skill / SOP for running campaigns. Invoked by future Claude sessions when the user asks to run or debug cloud campaigns. Includes: preflight checks, launch commands, monitoring, cost ceiling discipline, teardown verification, failure recovery recipes.

9. **`experiments/phase6-planning/cost_model.py`** — source-of-truth cost model. All dollar figures in this doc are computed from pinned constants in that file (AWS/Hetzner pricing, per-VM throughput, TPE saturation, JVM sizing, AWS quota). Rerun after any pricing / throughput update.

10. **`experiments/cloud-campaign-validation/`** — validation runs staged to the $85 budget (script-verified):
   - **Pipeline smoke** (~$1.20; 1 study × 8 workers × 2 hr): validates orchestrator ↔ worker Redis pipeline, cost ledger, teardown, spot-preemption replay. Gate before the benchmark.
   - **Sampler benchmark** (~$14.83; 2 hulls × 3 samplers × 1 hr): runs TPE-24 / CatCMAwM-24 / CatCMAwM-48 bake-off per §10. Selects the `sampler:` value for the prep. Archived to `experiments/phase6-sampler-bench-2026-0X/` with a REPORT.md documenting the decision.
   - **Phase 7 prep campaign** (~$60.79; 8 hulls × `early` × 1 seed × ~600 trials, ~4.1 hr at 96 VMs across us-east-1 + us-east-2): produces the cross-hull early-regime data Phase 7's attribute/mode kernels need. Archived to `experiments/phase7-prep-early-2026-0X/` with a REPORT.md summarizing per-hull convergence + mode clustering signal.
   - (Future, $500+ budget) regime sweep on Hetzner where the ~13% cost advantage offsets the quota-ticket overhead.

## Testing

- **Unit**: `CampaignManager` plateau detector (synthetic traces), cost ledger monotonicity, `CloudProvider` mock passes a reference scenario.
- **Integration**: $10 smoke test must complete within 1 hour and produce a populated `study.db` with ≥100 trials.
- **Cost-cap test**: inject a fake $100/hr worker into ledger; manager must terminate within 1 minute of crossing budget.
- **Preemption test** (AWS only): deliberately terminate a worker mid-study; another worker should pick up the same trial within 2 minutes and complete it.
- **Teardown audit**: after any campaign, `final_audit.sh` must report zero tagged resources.

## Non-obvious implementation notes

1. **Don't put the study.db on NFS.** Optuna explicitly warns SQLite does not handle NFS locks correctly. Keep study.db local to the orchestrator subprocess; workers never touch Optuna.

2. **Avoid `optuna.integration.RayTuneSearch` / `optuna.integration.Dask`.** They assume ownership of worker lifecycle. Our manager owns lifecycle; Optuna should see a flat local study.

3. **Ask/tell over Redis must preserve `TPESampler`'s state.** Approach: orchestrator holds the single `Study` object; workers only produce raw `CombatResult`; orchestrator calls `study.ask()` and `study.tell()` from a single thread. Workers never touch Optuna directly. This avoids the distributed-storage dance.

4. **The XRandR warmup fix must be in the baked image.** Without it, LWJGL crashes on first Starsector launch. The `_start_xvfb` patch (already in `instance_manager.py` as of 2026-04-18) plus `x11-xserver-utils` package (baked into the AMI via `scripts/cloud/packer/aws.pkr.hcl`) together prevent this. Validation: baked image passes `xvfb-run xrandr --query` + `uv run python -c 'from starsector_optimizer.worker_agent import main'` in Packer's post-build hook; AMI tag is set only on zero exit.

5. **Tailscale/WireGuard over public IPs for worker↔orchestrator.** Prevents exposing Redis to the internet. Free tier of Tailscale covers 100 devices.

6. **Per-study seed should propagate into heuristic warm-start.** Currently `warm_start()` is deterministic from the hull data — seeds need to be plumbed through so independent seed studies don't all warm-start to the same 500 heuristic trials.

## References

- Benchmark validating CPU-cloud viability + XRandR fix: `experiments/cloud-benchmark-2026-04-18/RESULTS.md`
- Scaling research (providers, Optuna at scale, library comparison): `experiments/cloud-benchmark-2026-04-18/SCALING.md`
- Cloud deployment spec (updated 2026-04-18): `docs/specs/22-cloud-deployment.md`
- Optuna 4.2 gRPC storage proxy (300-worker benchmark): https://medium.com/optuna/distributed-optimization-in-optuna-and-grpc-storage-proxy-08db83f1d608
- Optuna JournalStorage + GrpcProxy broken (avoid this combo): https://github.com/optuna/optuna/issues/6084
- boto3 (shipped cloud SDK): https://boto3.amazonaws.com/ — Apache Libcloud deferred until cross-provider unification ($500+ scale)
- Packer Hetzner plugin: https://developer.hashicorp.com/packer/integrations/hetznercloud/hcloud
- EC2 price-capacity-optimized allocation: https://aws.amazon.com/blogs/compute/introducing-price-capacity-optimized-allocation-strategy-for-ec2-spot-instances/
