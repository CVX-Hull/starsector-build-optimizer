---
type: reference
status: shipped
last-validated: unvalidated
---

# Phase 6 — Cloud Worker Federation

Status: **INFRASTRUCTURE SHIPPED** (2026-04-18; per-study fleet ownership added same day). `AWSProvider` ships with the per-fleet API (`provision_fleet` / `terminate_fleet` / `terminate_all_tagged` sweep backstop), two-tag scheme (`Project`+`Fleet`), `cloud_userdata.render_user_data` with IMDSv2 WORKER_ID override, `CostLedger`, Packer AMI template, Tier-1 `probe.sh` / `probe.py` (updated to new API), and Tier-2 wiring: `CampaignManager` is a pure supervisor (`_preflight` + `spawn_studies` + `monitor_loop` + campaign-wide-sweep teardown), and each study subprocess (`scripts/run_optimizer.py --worker-pool cloud`) owns its own fleet lifecycle — provisioning AND teardown. Tier-2 pipeline smoke defined in §11 was live-validated 2026-05-09. Sampler benchmark and Phase 7 prep campaign deferred. Renumbers the former Phase 6 (Structured Search-Space Representation) to Phase 7 — cloud federation is infrastructure that Phase 7's multi-week BoTorch build depends on for validation at scale.

> **Empirical-claims status (2026-05-10):** Throughput rates (matchups/hr/VM, trials/hr), cloud-vs-local speedup ratios, $/matchup figures, dollar budgets derived from those rates, and preemption rates were measured on V1 sim and are pending re-validation under V2. The V2 setup-time overhead (per-matchup `setVariant` + `LoadoutDiagnostic`) is non-zero and will shift the cost model. See [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md). Architecture decisions (study federation, per-study fleet ownership, two-region spot, EC2 Fleet allocation strategy) are unaffected.

## Budget staging

Phase 6 ships against a combined Phase 6 shakedown + sampler benchmark + Phase 7 prep-data budget (larger campaigns come after Phase 7). The cost model is parameterized by provider constants (AWS c7a.2xlarge spot price, observed throughput rate, observed preemption rate); the post-V2 budget model is captured in [../reports/2026-05-10-validation-plan.md](../reports/2026-05-10-validation-plan.md) and should be re-derived there after any pricing / throughput update rather than hand-edited into this doc.

**Line items**:

- Validation probe (small, ~tens of cents)
- Pipeline smoke (Tier-2.0 single-matchup + Tier-2.5 multi-worker per §11)
- Sampler benchmark — SKIPPED (see §10); CatCMAwM is structurally incompatible with the all-categorical search space.
- Prep campaign (8 hulls × `early` regime × 1 seed × 600 trials ≈ 48,000 matchups, with preemption headroom)
- Slack (reruns, retries, headroom)

Specific dollar figures and wall-clock estimates from V1 throughput measurements are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). The structural sizing argument (the prep campaign dominates total cost; sampler benchmark and smoke are rounding) is unaffected.

- **AWS primary** (c7a.2xlarge spot, us-east-1 + us-east-2) rather than Hetzner, because existing AWS account vCPU quota is already ample (1,280 spot vCPU across us-east-1 + us-east-2 = room for ~160 eight-vCPU instances, vs Hetzner default 10-VM project cap that requires a 1-2 day quota-upgrade ticket). AWS carries a small per-matchup premium over Hetzner that is covered by the budget. Hetzner stays documented as a post-Phase-7 scale-up path where its price advantage matters against larger spend.
- **Packer pre-bake is in scope** (reversal from an early scoping sketch that deferred it). Rationale in §5. AWS AMIs are region-scoped, so the build is done in us-east-1 and `aws ec2 copy-image`'d to us-east-2 (~5 min, one-time).
- **Two-region spread** across us-east-1 + us-east-2 for spot-pool diversity. EC2 Fleet with `price-capacity-optimized` allocation + `CapacityRebalancing` keeps preemption rate low.
- **Prep campaign target**: 8 hulls × `early` × 1 seed × 600 trials ≈ 48,000 matchups. Produces the cross-hull early-regime data Phase 7's attribute/mode kernels need to validate transfer claims.
- **Why early regime, not endgame**: late-regime optima largely recover published community meta (e.g. `shrouded_lens` / `fragment_coordinator` archetypes the V1 Hammerhead run concentrated on); early regime is where the optimizer has room to find genuinely novel builds *and* is a stricter Phase 7 kernel test under a tighter feasible set.

## Goal

Spend $N of compute and get $N of useful optimization data — i.e., **linear $→data scaling** from $10 validation runs to $1000+ hull-coverage campaigns. Frees the workstation for interactive work by moving bulk simulation to cloud CPU spot instances.

## Why now (not deferred)

1. **Phase 5D-post findings** — cloud CPU is faster than local per-instance throughput after the LWJGL XRandR warmup fix (specific multiplier pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md)). The original "GPU required" blocker in [../specs/22-cloud-deployment.md](../specs/22-cloud-deployment.md) was a misdiagnosis; the actual bug was Xvfb's XRandR extension not populating its mode list until a client queries it.

2. **Phase 5E, 5F, Phase 7 all need scale.** Phase 5E Box-Cox validation wants large per-hull build counts. Phase 5F regime-segmented exploration multiplies that across regimes and hulls. Phase 7 BoTorch GP validation needs cross-hull transfer data. Cloud parallelism is the only realistic path to those data volumes.

3. **Optuna sampler ceiling.** TPE degrades above ~30 concurrent workers per study (`constant_liar` imputation collapses KDE to random sampling). Spending most of a budget on a single mega-study wastes much of it on random sampling at the front of TPE's startup. Federation into many smaller studies keeps each sampler in its efficient zone.

4. **Operator burden grows nonlinearly.** At 3 workers on the workstation (current), ad-hoc bash is fine. At 30+ cloud workers, ad-hoc bash kills entire budgets via runaway costs (no auto-stop) or orphaned resources (no teardown discipline).

## Non-goals

- Not a migration away from the workstation. Local runs remain the default for interactive work and for 1-2 worker debugging.
- Not GPU cloud. CPU spot is faster than local throughput at low $/matchup (specific magnitudes pending re-validation under V2); GPU adds no throughput for LWJGL 2.x software-rendered 2D games.
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

TPE is the sole supported sampler per study. Constant-liar imputation (`constant_liar=True`) extends its usable parallelism to the ~24-worker regime; above that, per-study scaling plateaus and the federation strategy absorbs the rest (more studies, not more workers per study).

**CatCMAwM was removed 2026-04-19** (see §10 and `docs/specs/24-optimizer.md` §`_create_sampler`). The `cmaes.CatCMAwM` library requires ≥1 continuous variable; our search space is fully categorical + integer, so CatCMAwM fails `x_space must be shape (n, 2), got (0,)` at every study start. Hybrid schedules (random → CatCMAwM → TPE) depended on the CatCMAwM stage and are therefore also removed. Phase 7 replaces the Optuna sampler surface entirely (BoTorch composed-kernel GP); any cross-stage scheme belongs to that redesign, not to Phase 6.

Rejected alternatives (still applicable for any future sampler work):
- **Facebook Ax** — native batch BO, but ~3 days of port + loses existing `WilcoxonPruner` / warm-start infrastructure. Not worth it when Phase 7 (BoTorch GP) supersedes this anyway.
- **GPSampler with `constant_liar`** — Optuna 4.2 added `constant_liar` to GPSampler, but NUTS posterior inference is ~1-2 hr per 200-trial run; too slow for short studies.
- **Raising `n_ei_candidates` alone** — extends TPE's usable range from ~30 to maybe 50 workers per study, not 100+. Cheap band-aid, not a structural fix.

### §3 Provider abstraction (boto3 direct; Libcloud deferred)

Two CPU providers are evaluated: **AWS c7a.2xlarge spot** (shipped in Phase 6 MVP) and **Hetzner CCX33** (`HetznerProvider` stub until larger-scale campaigns). Specific $/matchup, preemption rates, and other empirical comparisons were measured under V1 sim and are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

Qualitative provider verdicts (architecture-grade, unaffected by V1 invalidation):

- **AWS c7a.2xlarge us-east-1+us-east-2 spot** — default for Phase 6 MVP. Ample existing quota (1,280 spot vCPU across us-east-1 + us-east-2, room for ~160 eight-vCPU instances), no multi-day ticket wait. Carries a small per-matchup premium over Hetzner.
- **Hetzner CCX33** — `HetznerProvider` stubbed until larger-scale campaigns where the per-matchup cost advantage matters; requires quota upgrade from default 10 VMs (1-2 business day ticket).
- **GCP n2d-std-8 spot** — not implemented. Potentially relevant for very large campaigns where cost dominates.

**Why AWS primary**: at small-budget scale the dominant operator cost is the multi-day wait for Hetzner's quota-upgrade ticket. AWS's existing quota means zero lead time. The trade inverts at larger scale where the per-matchup cost delta exceeds a human-day of engineering time.

**Library choice: boto3 direct**, behind the `CloudProvider` ABC. Phase 6 ships AWS only; an Apache Libcloud wrapper can slot behind the ABC later without refactoring callers if cross-provider unification becomes valuable. Libcloud was considered but dropped from MVP — it's a unified-API abstraction with zero users while Hetzner is stubbed, and boto3 is fewer moving parts.

**Skip entirely**: SkyPilot, dstack, Modal, Covalent (no Hetzner support — all are GPU/AI-focused); Ray `ray up` (100-LOC custom node_provider.py required for Hetzner); Kubernetes (overkill for single-operator bursts).

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
- **Spot preemption mid-run** — kept low by `price-capacity-optimized` + `CapacityRebalancing`; handled at the application layer by Redis visibility-timeout + idempotent `(study_id, trial_number, opponent_id)` keys (§7). Specific preemption-rate measurement pending re-validation under V2.
- **Spot-pool depletion at launch** — mitigated by EC2 Fleet with diversified instance-type list (`c7a.2xlarge` + `c7i.2xlarge` + `c7a.4xlarge` + `c7i.4xlarge`; the 4xlarge variants fit 4 JVMs per VM at the same per-matchup cost).
- **AMIs are region-scoped** (unlike Hetzner global snapshots). Build the Packer AMI in us-east-1, `aws ec2 copy-image --source-region us-east-1 --source-image-id ami-... --region us-east-2` to replicate (~3-5 min, one-time).

**Pre-flight validation probe** (runs 24h before the campaign; costs ~$1):
1. Launch 2 spot instances per target region from the production AMI.
2. Confirm boot + `starsector.sh` launch test + `uv run python -c 'from starsector_optimizer.optimizer import _shape_fitness'`.
3. Record per-region health signal in `probe_report.json`.
4. Tear down.

Catches AMI-copy errors and region-scoped cloud-init divergences before the campaign commits the prep-run spend.

**Graceful degradation**: if day-of spot-pool depletion limits provisioning to 48-60 VMs instead of 96, the campaign manager allocates remaining workers round-robin across studies and accepts ~2× wall-clock. It does **not** reduce per-study budget (changes the experimental design) and it does **not** abort. Partial fleet is a latency problem, not a correctness problem. `min_workers_to_start` default 48 is the hard floor; below that the manager waits or aborts.

---

**Hetzner (documented fallback for $500+ scale-up).** CCX33 capacity is per-datacenter; Hetzner does not publish inventory. Operational characteristics observed during pre-V2 Hetzner runs (fell back `ash` → `hil` on `resource_unavailable`):

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
data/campaigns/<campaign-name>/
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

### §5 Pre-baked provider images (in scope from day 1)

Bootstrap cost on cold cloud-init: several minutes per worker (apt + rsync ~550 MB game + uv sync + XRandR warmup). Packer pre-bake drops this to under a minute. Specific seconds-figures pending re-validation under V2.

**Dollar savings at small scale are small** — but that is the wrong framing. The load-bearing arguments that put Packer in scope from day 1:

1. **Tail-latency reduction**: at 96 VMs with independent 5-min cold starts, the *last* VM to boot gates campaign start — and burst provisioning amplifies transient apt / download failures into correlated retries. Packer's deterministic ~45s boot collapses this tail.
2. **Reliability at burst**: bulk 429s from apt repos, PyPI, or the game-data rsync source are a real failure mode at 50+ concurrent cloud-inits. Packer removes every one of those external dependencies from the hot path.
3. **Cheap retry after a broken worker**: with Packer, replacing a dead VM costs ~45s of wall-clock, not 5 min. Matters disproportionately during the tail of a campaign when most studies have terminated and a single stuck study is gating exit. Also critical for spot-preemption recovery on AWS.
4. **One-time investment amortizes**: ~30 min to write the Packer template + build + test-boot; rebuild quarterly on game or major dep updates (~5 min). Storage cost: ~$0.06/month for a 4 GB AMI.
5. **The $10k+ campaign doesn't want a different image path.** Building Packer in for the $85 run means the $500 and $1000 campaigns inherit a tested image with zero extra work.

**Provider specifics**:
- **AWS (primary)**: `amazon-ebs` Packer builder. Output: private AMI, referenced in Launch Template or EC2 Fleet config. Cold-start: ~45s. AMIs are **region-scoped** — build in us-east-1, then `aws ec2 copy-image --source-region us-east-1 --source-image-id ami-... --region us-east-2` (~3-5 min, one-time; repeat on every Packer rebuild). Note: the copy produces a different AMI ID in the target region; both IDs are recorded in the campaign's `ami_ids_by_region:` YAML field.
- **Hetzner (documented fallback)**: `packer-plugin-hcloud` builder. Output: a private snapshot, reference by ID in `hcloud server create --image=<id>`. Snapshots are **globally scoped** (unlike AWS AMIs) — one build, usable in any location.

**Multi-region test-boot** (AWS): the validation probe VMs from §3.5 double as image validation — one VM per target region boots from that region's AMI ID and runs the post-build smoke test (`starsector.sh` launch + `_shape_fitness` import). Catches region-scoped cloud-init divergences or AMI-copy corruption before the campaign commits the prep-run spend.

**Skip warm pools** at $1000 scale (EBS costs ~$16/mo for 50 stopped instances — not worth it for weekly bursts). Worth it at $10k+ scale.

**Skip FSR** (EBS Fast Snapshot Restore) — volume creation credits cap at 10 concurrent, breaks at 50-worker bursts. Lazy-loading is fine for 551 MB (and Packer bakes it into the image anyway).

### §6 Cost discipline (non-negotiable at scale)

**Hard-stop mechanisms** (Phase 6 MVP ships 1+2; 3+4 are deferred as orthogonal operational backstops):
1. Per-campaign `budget_usd` hard cap in `campaign.yaml` — `CostLedger.record_heartbeat` raises `BudgetExceeded` at 100%; `CampaignManager.run()`'s `finally` block triggers teardown.
2. Per-worker lifetime cap (default **6 hours**; design-set to cover the prep campaign's wall-clock with headroom — see the post-V2 budget model in [../reports/2026-05-10-validation-plan.md](../reports/2026-05-10-validation-plan.md)) — workers self-terminate.
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

Preemption rate targets are kept low by `price-capacity-optimized` allocation + Capacity Rebalancing on AWS, and Hetzner has no spot tier (no preemption). Specific preemption-rate measurements pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). The application-layer protocol (Redis visibility-timeout + idempotent `matchup_id` dedup) bounds re-run overhead to a small fraction of budget regardless.

### §8 Scaling targets

The cost model is parameterized by AWS c7a.2xlarge spot price ($0.15/hr at the Phase 6 ship date), JVMs per VM, and observed per-VM throughput. Specific $/matchup, total-matchup-per-budget figures, and wall-clock estimates are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). The structural budget tiers are:

**Phase 6 shakedown + Phase 7 prep (current target)** — line items in the Budget staging section at top: validation probe + pipeline smoke + prep campaign (8 hulls × `early` × 1 seed × 600 trials = 48,000 matchups) + slack. Wall-clock parallel across 8 studies. Binding constraint: TPE saturates at 24 workers per study (§1) → 12 VMs/study cap; adding more VMs past the per-study cap × N-studies helps nothing until there are more studies. Why early regime: late/endgame recovers community meta; early is the zone where novel-build discovery is load-bearing and also the tighter Phase 7 kernel test.

**Larger Phase 7 validation campaign (future)** — at the threshold where Hetzner's per-matchup cost advantage exceeds the human-day of engineering overhead to file the quota-upgrade ticket and switch provider, re-evaluate: AWS for latency, Hetzner for total spend.

**Full Phase 5F regime sweep (future, larger budget)** — allocation modes:
- **Mode A (go wide)**: 200 hulls × 1 regime × 250 builds → broad catalog coverage.
- **Mode B (go deep + regime)**: 40 hulls × 4 regimes × 300 builds → comprehensive for priority hulls.
- **Mode C (ensemble)**: 15 hulls × 4 regimes × 5 seeds × 150 builds → robust uncertainty estimates.

Per-study sweet spot is 500-1500 builds (diminishing returns above 1500).

### §9 Diminishing-returns auto-termination

Per-study plateau detector: best-fitness trace binned into 50-trial windows. If the last 3 bins all have slope < 0.01 Δfitness per trial, study terminates early. Releases workers for reallocation.

The plateau-emergence trial range for Hammerhead-default search-space was characterised on V1 logs and is pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). The 1500-trial per-study budget gives most studies enough room to plateau naturally regardless of where the plateau emerges.

**Rejected**: fixed per-study budget. Wastes spend on studies that converged early and burns ceiling on studies that are still improving.

### §10 Sampler benchmark — SKIPPED 2026-04-19

**Outcome**: skipped before launch. Prep uses TPE directly.

The originally-planned TPE-24 vs CatCMAwM-24 vs CatCMAwM-48 bake-off was abandoned after a first attempt (32 VMs × 3 samplers × 2 seeds on hammerhead) surfaced a structural incompatibility: `cmaes.CatCMAwM._init_gaussian` requires a non-empty `x_space` of shape `(n, 2)`, but the Starsector search space — `CategoricalDistribution` for weapon slots and hullmod booleans, `IntDistribution` for `flux_vents` and `flux_capacitors` — produces zero continuous variables on every hull / regime. CatCMAwM raises `ValueError: x_space must be a two-dimensional array with shape (n, 2), but got shape (0,)` during its first `sample_relative` call. See `src/starsector_optimizer/optimizer.py` docstring and `docs/specs/24-optimizer.md` §`_create_sampler`.

CatCMAwM was removed (`_create_sampler` no longer accepts it; `_ALLOWED_SAMPLERS = {"tpe"}`). A `random` baseline was considered and also dropped — with no Bayesian alternative to race against, TPE-vs-random is a foregone conclusion at 200+ trials. Phase 7 replaces the Optuna sampler surface entirely (BoTorch composed-kernel GP per `docs/reference/phase7-search-space-compression.md`), so any interim Optuna tuning has a short shelf life.

The aborted benchmark attempt surfaced three concurrent-dispatch correctness bugs that were all fixed with regression tests before moving on:
- **`InvalidGroup.NotFound` race in `create_fleet`**. Under N studies provisioning simultaneously, Fleet's internal service replication lagged `describe_security_groups` visibility. Fix: boto3 `security_group_exists` waiter after `create_security_group` + retry on transient `InvalidGroup.NotFound` errors in `_create_fleet_in_region` (predicate is `any(transient)` not `all(transient)` — permanent per-AZ rejections like `us-east-1e` not stocking `c7a.2xlarge` commonly co-occur with transient SG errors on other AZs, and we want to retry so the non-1e AZs succeed). Regression: `tests/test_cloud_provider.py::TestFleetProvisionSGPropagation` (4 cases).
- **EB shrinkage guard race in `_apply_eb_shrinkage`**. Guard read `score_matrix.n_builds` (trials with ≥1 matchup result) whereas the OLS fit consumes `_completed_records` (fully-finalized trials). Under 32 concurrent slots the guard passes while `len(_completed_records) == 1`, and `eb_shrinkage` raises `ValueError: n >= 3 builds, got 1`. Fix: guard on `len(_completed_records)`. Regression: the existing 2-slot smoke never triggered this; Phase 7 prep at 24 slots/study would have.
- **study_id / eval_log_path collision**. `study_id = f"{hull}__{regime}__seed{seed}"` collided across sampler variants with the same (hull, regime, seed), and the shared `data/evaluation_log.jsonl` had no `sampler` field so per-sampler attribution was impossible. Fix: study_id now includes sampler (`f"{hull}__{regime}__{sampler}__seed{seed}"`) and `scripts/run_optimizer.py` writes per-study directories (`data/logs/<study_id>/evaluation_log.jsonl`) uniformly for local and cloud runs. Regression: `tests/test_run_optimizer_cloud.py`.

Budget: the aborted benchmark consumed under one dollar of live spend across two partial provisioning attempts (instances ran briefly before teardown). The sampler-benchmark line item returns to the Phase 6 slack pool.

Additional concurrency hazards identified during the audit pass but deferred (unreachable in current code path, or not observed in practice) are captured in [../reports/2026-04-19-phase6-deferred-audit.md](../reports/2026-04-19-phase6-deferred-audit.md). That doc also proposes a **Tier-3 concurrency shakedown** as a gate between Tier-2.5 smoke and Phase 7 prep — the session's four fixes would all have been caught by such a stage without the prep-scale cost exposure. Revisit when Phase 7 prep is scheduled.

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
- `data/campaigns/smoke/ledger.jsonl` contains ≥ 1 `worker_heartbeat` event.
- The Optuna study's SQLite (at the subprocess's `--study-db` path) contains exactly 1 `TrialState.COMPLETE`.

**Tier-2.5 multi-worker variant (post-Tier-2.0 pass)**: same code path, `examples/smoke-campaign-multiworker.yaml`. `workers_per_study: 3`, `matchup_slots_per_worker: 2` (default), `budget_per_study: 20`, `max_concurrent_workers: 3`, `budget_usd: 3.0`. Exercises the **total concurrency path** (pool semaphore sized to `workers × matchup_slots_per_worker = 6`), the threaded worker consumer loop (each VM drives 2 concurrent matchups), janitor re-queue under concurrent dispatch, POST dedup under duplicate results, backpressure. Additional gate: worker `load_avg_1min` (from the heartbeat hash) lands in `[3, 8]` — under-load or over-subscription indicates the fleet shape doesn't match `matchup_slots_per_worker`. Inspect via `redis-cli HGETALL worker:<project_tag>:<worker_id>:heartbeat`.

**Expected cost**: Tier-2.0 + Tier-2.5 fit the "Pipeline smoke" line item in §Budget staging. Specific dollar figures pending re-validation under V2.

## Day-1 ordered actions

Ordered by lead time. The AWS-primary direction removes the Hetzner quota-ticket blocker that previously sat at position 1; no external blockers now gate campaign code.

1. **Build Packer AMI in us-east-1** (~30 min). Bake the combat harness, game files, `uv sync`, and the XRandR warmup fix into a private AMI. Tested via a post-build launch hook.
2. **`aws ec2 copy-image` to us-east-2** (~3-5 min, one-time). AWS AMIs are region-scoped; replicate to the second target region. Re-run on every Packer rebuild.
3. **Validation probe** (`scripts/cloud/probe.sh` + `scripts/cloud/probe.py`). Tier 1: exercises `AWSProvider.provision_fleet` (LaunchTemplate + SecurityGroup creation + instance launch + two-tag propagation) + `terminate_fleet` + `final_audit.sh`. Scope is fleet lifecycle only — **does not** SSH in, join Tailscale, hit Redis, or run a matchup (those are the pipeline smoke's job, Tier 2). Cost is small (a couple of c7a.2xlarge spot instances for a few minutes); specific $-figure pending re-validation under V2. Run before any paid campaign.
4. **Campaign manager + orchestrator** — the main Phase 6 implementation work. EC2 Fleet with `price-capacity-optimized` + CapacityRebalancing, Redis-backed study queues, graceful degradation. See Deliverables.
5. **Pipeline smoke**: 1 study × ~8 workers × 1 region. Confirms the full pipeline (orchestrator ↔ worker Redis BRPOPLPUSH + Flask POST, janitor re-queue, cost ledger, three-layer teardown, preemption replay).
6. **Sampler benchmark** — SKIPPED 2026-04-19 (see §10).
7. **Run Phase 7 prep campaign** at the target VM count using TPE.
8. **Final audit + Phase 7 handoff**: `final_audit.sh`, archive campaign output, write short REPORT.md for Phase 7 developer consumption.

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

9. **Cost model script** — source-of-truth cost model. Dollar figures in cloud-campaign discussion derive from pinned constants in that file (AWS/Hetzner pricing, per-VM throughput, TPE saturation, JVM sizing, AWS quota). Rerun after any pricing / throughput update. Specific magnitudes pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

10. **Validation runs**:
   - **Pipeline smoke**: validates orchestrator ↔ worker Redis pipeline, cost ledger, teardown, spot-preemption replay. Gate before the benchmark.
   - **Sampler benchmark** — SKIPPED 2026-04-19 (see §10). Prep uses TPE directly.
   - **Phase 7 prep campaign**: 8 hulls × `early` × 1 seed × ~600 trials at the target VM count across us-east-1 + us-east-2. Produces the cross-hull early-regime data Phase 7's attribute/mode kernels need.
   - (Future, larger budget) regime sweep on Hetzner where the per-matchup cost advantage offsets the quota-ticket overhead.

## Testing

- **Unit**: `CampaignManager` plateau detector (synthetic traces), cost ledger monotonicity, `CloudProvider` mock passes a reference scenario.
- **Integration**: smoke campaign must complete within bounded wall-clock and produce a populated `study.db` with the expected trial count.
- **Cost-cap test**: inject a fake high-rate worker into ledger; manager must terminate within bounded time of crossing budget.
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

- Cloud deployment spec: [../specs/22-cloud-deployment.md](../specs/22-cloud-deployment.md)
- V1 invalidation that retired the original `experiments/cloud-benchmark-2026-04-18/` and `experiments/phase6-planning/` directories: [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md)
- Optuna 4.2 gRPC storage proxy (300-worker benchmark): https://medium.com/optuna/distributed-optimization-in-optuna-and-grpc-storage-proxy-08db83f1d608
- Optuna JournalStorage + GrpcProxy broken (avoid this combo): https://github.com/optuna/optuna/issues/6084
- boto3 (shipped cloud SDK): https://boto3.amazonaws.com/ — Apache Libcloud deferred until cross-provider unification at scale-up
- Packer Hetzner plugin: https://developer.hashicorp.com/packer/integrations/hetznercloud/hcloud
- EC2 price-capacity-optimized allocation: https://aws.amazon.com/blogs/compute/introducing-price-capacity-optimized-allocation-strategy-for-ec2-spot-instances/
