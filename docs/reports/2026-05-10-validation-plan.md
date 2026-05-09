---
type: report
status: draft
last-validated: unvalidated
---

# Real-data validation campaign plan (post V2 loadout fix)

The V2 combat-harness loadout fix (commit `dc71e3b`, 2026-05-10) is verified
working under Tier-2 smoke. The V1-era empirical evidence is invalidated
([2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md))
because every prior matchup ran with `live_weapons={}` on the player ship
while `spec_weapons` had the optimizer-generated loadout — the actually-
realised fitness signal was hull-vs-naked-hull, not the design intent.
This document specifies a runnable campaign that re-validates the 20
shipped algorithmic and infrastructure mechanisms on real (no-bug) data.

The plan is sized to fit inside the existing $85 Phase 7 prep ceiling
(`docs/reference/phase6-cloud-worker-federation.md:7`). Wave 0 is gated
by infrastructure health; Wave 1 is the algorithmic-mechanism ablation
matrix; Wave 2 cross-validates regime + warm-start; Wave 3 is the planned
8-hull production prep run, gated on Waves 0–2 passing.

## 1. Hull selection

Recommendation: **Hammerhead (primary) + Wolf (secondary, smaller)**.

| Hull | Class | Slot count | log10 search space (operator estimate from CLAUDE.md inventory) | Opp pool size (hull-size match) | Role archetype |
|---|---|---|---|---|---|
| `hammerhead` | Destroyer | ~8 | ≈ 41–43 | ~15–25 destroyers | Kinetic/HE brawler |
| `wolf` | Frigate | ~5 | ≈ 33–37 | ~30 frigates | Flanker / energy striker |

`ship_data.csv` confirms the hull classes
(`game/starsector/data/hulls/ship_data.csv`).

**Hammerhead (primary)**: historical Phase 5 baseline (V1 LOOO ship-gate
cleared at Δρ=+0.036, `docs/reference/phase5d-covariate-adjustment.md:3`).
Re-validating on the same hull lets us cross-check V2 numbers against V1
qualitative claims (sign, ranking, mechanism contribution) even though V1
absolute numbers are invalidated. 8 weapon slots exercise loadout signal
without capital-tier (log10≥60) cardinality. Destroyer opp pool is large
enough for `n_anchors=3` (`models.py:464`) + `n_incumbent_overlap=5`
(`models.py:463`); 10 `active_opponents` (`optimizer.py:98`) covers
~50 % of pool. Destroyer TTK lands inside the 300 s matchup time limit
(`optimizer.py:93`) reliably.

**Wolf (secondary)**: cross-cuts hull size — frigate opp pool ~2× destroyer
pool, different anchor / overlap statistics. Confirms post-V2 the V1
frigate-gradient regression (`docs/reports/2026-04-19-phase7-prep-relaunch.md:18-20`,
where τ̂² collapsed ~10⁵×) is fixed once weapons actually fire. Cheap
per-trial (small hull, short TTK).

**Sample-size target**: Phase 5D EB requires `eb_min_builds=8`
(`models.py:479`) and design doc targets N≥200 for stable γ̂ at the
original p=7 covariates (`docs/reference/phase5d-covariate-adjustment.md:172-177`).
Post-Phase-7-prep p=10 (`optimizer.py:471-489`); operator-set bump to
**N ≥ 250 finalized builds per ablation cell** — flagged "operator-set,
recommend confirming".

## 2. Per-mechanism gates

Mechanisms are grouped by ablation-sharing — when two mechanisms are
exercised by the same campaign config, one trial log validates both.

### Group A — Sampler / warm-start (Wave 1 baseline)

| # | Mechanism | Assertion | Metric (source) | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 1 | TPE sampler | TPE finalized count > random-baseline count under same budget at fixed wall-clock | `study.trials` COMPLETE count vs heuristic-only random sample of same `sim_budget` (`optimizer.py:209-216`) | Operator-set: TPE composite max ≥ random composite max + 1 σ at N=200. **Recommend confirming** — no doc-derived threshold; project removed CatCMAwM and explicitly skipped the random-vs-TPE benchmark (`optimizer.py:6-12`) | 200 | TPE on (in Wave 1 cells) vs heuristic-only `--heuristic-only` reference run | `data/logs/<study_id>/evaluation_log.jsonl` `fitness` column |
| 2 | Heuristic warm-start (default off) | Wave 1's `warm_start_n=50` cell does NOT outperform `warm_start_n=0` in best-200-trial composite by > 1 σ — confirms the post-Phase-7-prep decision to default off (`optimizer.py:74-77`) | Best `fitness` after 200 finalized trials in each cell | Δ(best fitness) ≤ 1σ; if `warm_start_n=50` *wins* by > 2 σ, the post-Phase-7-prep decision needs revisit | 200 finalized × 3 seeds | `warm_start_n ∈ {0, 50}` × seeds 0,1,2 (Wave 1) | JSONL `fitness` |
| 3 | Stock-build seeding | Wave 1 study top-10 list contains ≥ 1 trial whose params match a stock variant within edit distance ≤ 2 weapon slots, or has explicitly displaced them — confirms stocks were enqueued and were comparable to discovered builds | Match stock builds (`variant.load_stock_builds`) against trial 0..N params; check overlap | Operator-set: ≥ 1 stock present in top-50 OR clear evidence of displacement (`warm_start` log line shows `stock_count > 0`) | 1 (smoke) | Single run | `optimizer.py:359-364` log line; JSONL trial 0..stock_count rows |

### Group B — Deconfounding pipeline (Wave 1 ablation, primary algorithmic gate)

This is the load-bearing group. Phase 5D (EB) and Phase 5E (Box-Cox)
share a single ablation matrix because both read from the same JSONL.

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 4 | TWFE α̂/β̂ decomposition | `twfe_fitness` is bounded and finite; `α̂` ∈ approximately the design tier ranges (`models.py:438-440`: wins [1.0,1.5], timeouts [-0.49,+0.49]) for ≥ 95 % of trials | `twfe_fitness` JSONL field | ≥ 95 % finite within range | 200 | Always-on | JSONL `twfe_fitness` |
| 5 | Scalar control variate (legacy) | `eb_min_builds` fallback path: when `len(_completed_records) < 8`, returned fitness equals raw α̂ (`optimizer.py:910-911`) | `eb_diagnostics` is `null` for the first 7 trials | All trials 0..6 have null `eb_diagnostics` | 7 | Always-on (early-trials) | JSONL `eb_diagnostics` |
| 6 | Triple-goal rank correction | `triple_goal=True` (default `models.py:478`) leaves Spearman ρ within ±0.005 of EB-only across the run | Compute Spearman(`twfe_fitness`, `eb_fitness`) at end of run | ρ delta ≤ 0.005 (rank-monotone by construction; this is a regression assertion) | 200 | `triple_goal ∈ {True, False}` × 3 seeds | JSONL `eb_fitness` |
| 7 | EB shrinkage of α̂ (10-cov) | **Δρ(EB-shrunk α̂ vs plain TWFE) ≥ +0.02 LOOO** at N≥250. Threshold from `docs/reference/phase5d-covariate-adjustment.md:299` (the original ship gate). **Re-validation required** because covariate set changed from p=7 to p=10 post-Phase-7-prep (`optimizer.py:471-489`) | Mean LOOO ρ across 3+ anchor opponents: hold one opponent out, retrain γ̂, evaluate ρ(α̂, held-out raw). EB run vs TWFE-only run on same trials | Δρ ≥ +0.02 vs A0 (plain TWFE). Strict gate from 5D doc | 250 finalized | `eb` on/off; off = scalar CV fallback. Drives the 3-seed ablation matrix | JSONL `eb_fitness`, `twfe_fitness`, `covariate_vector`, `eb_diagnostics`; analysis notebook computes LOOO ρ |
| 8 | Box-Cox output warping | **Ceiling saturation < 1 %** AND **top-5 identification overlap ≥ 0.40**. Thresholds from `docs/reference/phase5e-shape-revision.md:137-138` (production target was 25.3 % → 0.4 % ceiling and 0.03 → 0.43 top-5) | Ceiling: `% trials with fitness ≥ 0.99`. Top-5: Jaccard(top-5 by `fitness`, top-5 by raw `eb_fitness`) | Ceiling ≤ 0.01, Jaccard ≥ 0.40 | 200 | `shape.min_samples=8` (default) on; off = min-max passthrough | JSONL `fitness`, `shape_lambda`, `shape_passthrough_reason` |

### Group C — Pruner / staged evaluator (Wave 1 always-on)

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 9 | WilcoxonPruner | `_trials_pruned > 0` AND ratio ∈ [10 %, 60 %] of total at end of run (`optimizer.py:225-228`) | `_log_run_summary` "Run summary" line (`optimizer.py:680-686`) | 0.10 ≤ pruned/total ≤ 0.60 | 200 | Always-on; off-cell would change `pruner=NopPruner` (operator script change, **not in Wave 1**) | Orchestrator INFO log; JSONL `pruned: true` rows |
| 10 | ASHA staged evaluator | At least 1 trial reaches the full `len(opponents)` rung; ≥ 1 trial pruned at rung < `active_opponents` | `opponents_evaluated` and `opponents_total` JSONL fields | `max(opponents_evaluated) == active_opponents` AND `min(opponents_evaluated when pruned=true) < active_opponents` | 200 | Always-on with WilcoxonPruner | JSONL `opponents_evaluated`, `opponents_total`, `pruned` |

### Group D — Opponent selection (Wave 1 always-on)

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 11 | Anchor-first | After `anchor_burn_in=30` finalized trials (`models.py:465`), the `_compute_anchors` log fires once and emits exactly `n_anchors=3` opponent IDs | "Locked N anchors" INFO log (`optimizer.py:1075-1078`) | Log appears once at trial ≈ 30; first 3 entries of `opponent_order` for trial > 30 are stable | 30 | Always-on (turning off requires `n_anchors=0`) | Orchestrator INFO; JSONL `opponent_order` |
| 12 | Incumbent-overlap | After anchor lock, `n_incumbent_overlap=5` (`models.py:463`) of each post-30 trial's opponent set is drawn from current incumbent's set | Compute `\| opponent_order[i] ∩ incumbent.opponents \| ≥ 5` (excluding anchors) for trials > 30 | ≥ 90 % of post-burn-in trials satisfy | 50 (post-burn-in) | Always-on | JSONL `opponent_order` cross-referenced with running argmax(`fitness`) |

### Group E — Regime segmentation + warm-start (Wave 2)

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 13 | Regime segmentation | Hullmod tier ≤ 1 invariant: every JSONL `build.hullmods` entry on `early` regime row has tier ≤ 1 per `manifest.hullmods[*].tier` | Regex / manifest cross-check on JSONL output | 100 % conformance | 200 | `regime ∈ {early, mid}` × 1 seed (Wave 2) | JSONL `regime` field, `build.hullmods` |
| 13b | (Operator-set) Cross-regime warm-start carries forward incumbents | After running an `early` study and then an `mid` study with `--warm-start-from-regime early`, the mid study's first ≤ `warm_start_n` enqueued trials are reconstructed from the early-study top-M (`optimizer.py:1197-1307`) | Compare hashes of `early` top-M repaired builds vs `mid` study early-trial params | ≥ 80 % match | 200 | mid run with vs without `--warm-start-from-regime early` | INFO log "Warm-start from regime"; JSONL trial-0..M params |

### Group F — Cloud infrastructure (Wave 0 + Wave 1 always-on)

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 14 | AWS provider + spot fleet | `provision_fleet` succeeds within `fleet_provision_timeout_seconds=600` for every Wave; `terminate_fleet` reaps ≥ 95 % of provisioned instances on study end | CloudTrail / AWS describe-instances post-teardown | 0 leaked instances tagged with `Project=starsector-<campaign>` after Wave teardown | n/a | Always-on; SOP in `.claude/skills/cloud-worker-ops.md` | `final_audit.sh` exit code; AWS describe-instances |
| 15 | CloudWorkerPool + Redis | `result_timeout_seconds=900` not tripped on > 5 % of matchups; janitor `requeue_count` < `max_requeues=5` for ≥ 99 % of matchups | Orchestrator log; Redis SCAN `worker:<project_tag>:*:heartbeat` | < 5 % timeouts, < 1 % drop-path WARNs | 1000 matchups | Always-on; verified by Tier-2.5 multi-worker smoke (`smoke-campaign-multiworker.yaml:53`) | Orchestrator WARN/ERROR log; ledger.jsonl |
| 16 | Worker throughput (122 matchups/hr/VM target) | Wave 1 measured per-VM throughput is within ±25 % of 122 matchups/hr (`docs/reference/phase6-cloud-worker-federation.md:7`). **V1-measured; Wave 1 itself re-validates** | matchups completed / VM-hours from ledger | 92 ≤ measured ≤ 152 matchups/hr/VM | 1 wall-clock hour, ≥ 4 VMs | Always-on | ledger.jsonl + `study.trials` count |
| 17 | CostLedger | `budget_usd=70` cap is enforced — `BudgetExceeded` raises before $70 in cumulative spend | Sum `cost_usd` rows in `ledger.jsonl` | Final cumulative ≤ `budget_usd`; spurious abort if > 1.05× | n/a | Always-on; `CampaignManager._tick_ledger` (CLAUDE.md "Phase-7-prep refactor" section, "live") | `data/campaigns/<name>/ledger.jsonl` |

### Group G — Mod-side correctness (Wave 0 gate)

| # | Mechanism | Assertion | Metric | Pass | Min N | Ablation | Artifact |
|---|---|---|---|---|---|---|---|
| 18 | V2 loadout invariant | **0 LOADOUT_MISMATCH WARNs** in 10 random Wave-0 matchups | Java `[SHIP_DUMP]` line + orchestrator `LOADOUT_MISMATCH` parse | 0 mismatches across 10 sample matchups | 10 | Always-on; verified by `scripts/cloud/loadout_ab_test.py` ARMED-vs-NAKED at smoke time | Orchestrator INFO `LOADOUT_OK` count; WARN `LOADOUT_MISMATCH` count |
| 19 | Manifest-as-oracle | `manifest.constants.{game_version, mod_commit_sha}` matches AMI tags `GameVersion` / `ModCommitSha` (preflight assert per CLAUDE.md "Phase-7-prep refactor"). | `_preflight` log on each study subprocess | All studies log "manifest version match" or equivalent assert pass | n/a | Always-on | Orchestrator INFO log |
| 20 | engine_stats SETUP read | `engine_stats` is non-null for ≥ 99.5 % of Wave-1 finalized trials (failure mode is `AssertionError` per `optimizer.py:490-496`) | JSONL `engine_stats` field | ≥ 99.5 % of finalized rows have non-null `engine_stats` | 200 | Always-on | JSONL `engine_stats` |

**Instrumentation gaps surfaced** (flagged, not fixed):

- Mechanism 1 (TPE vs random): no built-in random sampler
  (`optimizer.py:209-216` accepts `tpe` only). Treat the TPE gate as a
  plausibility check (study top-N vs `--heuristic-only` top-N proxy).
- Mechanism 12: orchestrator doesn't log incumbent identity per trial.
  Analysis notebook reconstructs running argmax(`fitness`) from JSONL —
  bookkeeping, not a gap.

## 3. Campaign architecture (waves)

### Wave 0 — preflight

**Hulls**: hammerhead. **Regimes**: early. **Trials**: 1.
**Concurrency**: probe = 2 VMs (no Tailscale); smoke = 1 VM.

Steps:

1. `scripts/cloud/probe.sh examples/probe-campaign.yaml` —
   AWS provider + LT + SG roundtrip. Cost: ≈ $0.05.
2. `scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml` —
   Tier-2 single-matchup smoke. Cost: ≈ $0.30.
3. **V2 loadout invariant audit**: re-run `scripts/cloud/loadout_ab_test.py`
   ARMED hammerhead × 3 + NAKED hammerhead × 3, confirm:
   - ARMED damage ~ 20 k, ARMED winner=PLAYER × 3
   - NAKED damage = 0.0, NAKED winner=ENEMY × 3
   - 0 `LOADOUT_MISMATCH` WARNs across all 6 matchups
   Cost: ≈ $0.10 (6 matchups, ~5 min on a single c7a.2xlarge).
4. `scripts/cloud/launch_campaign.sh examples/smoke-campaign-multiworker.yaml` —
   Tier-2.5 concurrency smoke (3 workers × 20 trials).
   Confirms multi-worker dispatch + janitor requeue. Cost: ≈ $1.00.

**Wave 0 gate** (all must hold; abort campaign if any fails):

- All four `final_audit.sh` invocations exit 0.
- `ledger.jsonl` ≥ 1 `worker_heartbeat` row in each.
- 0 `LOADOUT_MISMATCH` warns across the AB test.
- ARMED/NAKED damage asymmetry as expected.
- Per-VM throughput in the multi-worker smoke ≥ 60 matchups/hr (1/2 of
  prod target — the smoke is too short for tight bounds).

**Wave 0 cumulative**: ~$1.45, ~30 min wall-clock (mostly VM provisioning + game
boot for each separate launch).

### Wave 1 — single-hull baseline + ablations (the core algorithmic gate)

**Hull**: hammerhead. **Regime**: early. **Seeds**: 0, 1, 2.
**Trials/study**: 250 (hits 5D's N≥250 floor with 10-cov set;
sized up from 200 since covariate count grew p=7→p=10).
**Concurrency per study**: 8 workers × 2 slots = 16 active matchups.

Ablation matrix — 4 cells × 3 seeds = 12 studies:

| Cell | EB shrinkage | Box-Cox A3 | Triple-goal rank | Heuristic warm-start | Purpose |
|---|---|---|---|---|---|
| C0 | off (scalar CV) | off (min-max) | n/a | 0 | Plain-TWFE A0 baseline (5D's "A0") |
| C1 | on | off | True | 0 | EB-only — isolates mechanism 7 |
| C2 | on | on | True | 0 | Production default — isolates mechanism 8 (Box-Cox marginal) |
| C3 | on | on | True | 50 | Heuristic warm-start ablation — isolates mechanism 2 (default-off decision) |

C0 disabled by setting `eb_min_builds = sim_budget + 1`
(operator-set, no command-line flag — patch in YAML or wrapper script).
C1 disables Box-Cox by setting `shape.min_samples = sim_budget + 1`.

**Wave 1 gate**:

- C2 vs C0: Δρ(EB - TWFE) LOOO ≥ +0.02 (mechanism 7 gate).
- C2 vs C1: Box-Cox ceiling saturation ≤ 0.01 AND top-5 Jaccard ≥ 0.40
  (mechanism 8 gate).
- C3 vs C2: |Δ best-fitness| ≤ 1 σ (mechanism 2 confirms default-off).
- All cells: WilcoxonPruner pruned ratio in [0.10, 0.60]; engine_stats
  non-null ≥ 99.5 %; per-VM throughput in [92, 152] matchups/hr/VM.
- ≥ 1 finalized trial with `opponents_evaluated == active_opponents`
  AND ≥ 1 finalized trial pruned with `opponents_evaluated < active_opponents`.

**Concurrency**: 12 studies running simultaneously is feasible inside the
shakedown-tested ceiling (`phase7-prep-shakedown.yaml` already validated
4 studies × 8 workers × 2 slots concurrently). Plan to run sequentially in
3 batches of 4 cells (one per seed) to keep peak fleet at 32 VMs and avoid
the SG-replication-lag retry path (`tests/test_cloud_provider.py::TestFleetProvisionSGPropagation`).

### Wave 2 — cross-regime + cross-hull validation

Two studies, run sequentially:

1. **Hammerhead × `early` × seed 0 × 250 trials** — already covered by
   Wave 1 C2; re-use that study's storage as warm-start source.
2. **Hammerhead × `mid` × seed 0 × 250 trials × `--warm-start-from-regime early`** —
   exercises mechanism 13b (cross-regime warm-start).
3. **Wolf × `early` × seed 0 × 200 trials** — frigate cross-cut, smaller
   opp pool, confirms post-V2 frigate gradient is non-degenerate
   (mechanism 4: τ̂² should not collapse).

Concurrency: 8 workers × 2 slots = 16 matchups per study, run sequentially.

**Wave 2 gate**:

- Hammerhead-mid initial top-M trials match early-study top-M (mechanism 13b).
- Hammerhead-mid build hullmods include some `tier ≥ 2` (regime mask
  expanded; mechanism 13).
- Wolf finalized count ≥ 150 (drop-out rate < 25 %); `twfe_fitness`
  variance > 1e-3 (i.e. the V1 frigate-gradient collapse is gone —
  operator-set lower bound, **recommend confirming**).

### Wave 3 — production prep (full Phase 7 prep run)

Only run if Waves 0–2 pass.

Use **`examples/phase7-prep.yaml` unchanged** (`budget_usd=70`,
8 hulls × early × 1 seed × 600 trials). This is the existing production
prep config and matches the Phase 7 BoTorch warm-start intent.

**Wave 3 gate**: same gates as Wave 1 applied per-hull (Δρ EB ≥ +0.02
on at least 5/8 hulls, Box-Cox ceiling ≤ 0.01 on all 8, throughput in
[92, 152]). One operator-set lenience: 5/8 hulls instead of 8/8 because
non-Hammerhead hulls have not been independently validated under V2;
flag failures as future work, not campaign-aborting.

### Kill switch (campaign-abort triggers, applies to any wave)

Abort and re-evaluate if any of these trip:

1. > 5 % of matchups return `winner=ENEMY, duration < 10s` (signature of
   the V1 retreat-bug regression — mechanism 18 broken).
2. `LOADOUT_MISMATCH` warn count > 0 across all matchups in any wave.
3. CostLedger cumulative spend hits 0.95 × `budget_usd` while < 60 % of
   trials finalized.
4. Per-VM throughput < 60 matchups/hr/VM sustained over 1 wall-clock hour
   (½ V1 baseline; suggests AMI / mod regression).
5. > 0.5 % `engine_stats=None` in JSONL output (mechanism 20 broken;
   per `optimizer.py:490-496` this should already raise hard).

## 4. Budget + time analysis

Anchor numbers (cite source per CLAUDE.md inventory):

- c7a.2xlarge spot ≈ $0.15/hr, 122 matchups/hr/VM, 2 slots/VM
  (`docs/reference/phase6-cloud-worker-federation.md:7`).
- ASHA-pruning-aware matchups/trial ≈ 2.7 (operator inventory; same as
  `examples/phase7-prep.yaml:6` "≈12 matchups/trial" hot-loop number,
  **but** that includes anchor burn-in. 2.7 is the per-rung-aware
  amortized average across pruned + completed trials and is the more
  conservative planning number).
- Per-trial cost ≈ 2.7 × $0.00123 = ~$0.00332.
- 3 % preemption surcharge (price-capacity-optimized strategy default).

| Wave | VMs | Trials × studies | Matchups | VM-hours | Spot cost (×1.03 preempt) | Cumulative |
|---|---|---|---|---|---|---|
| 0 (probe + smoke + AB + multi) | peak 3 | 1 + 1 + 6 + 20 ≈ 28 matchups (smoke); ~30 min total | ~30 | ~1.5 | **$0.23** + AWS minute-rate boot overhead ≈ $1.45 | $1.45 |
| 1 (4 cells × 3 seeds × 250 trials, 8 workers/study, sequential 3 batches) | peak 32 | 12 × 250 = 3000 trials | 3000 × 2.7 = 8100 | 8100 / 122 = 66.4 | 66.4 × $0.15 × 1.03 = **$10.27** | $11.72 |
| 2 (3 sequential studies, 8 workers, ~700 trials total) | peak 8 | 700 trials | 700 × 2.7 = 1890 | 1890 / 122 = 15.5 | 15.5 × $0.15 × 1.03 = **$2.40** | $14.12 |
| 3 (`phase7-prep.yaml`, 8 studies × 600 × 96 VMs) | peak 96 | 4800 trials | 4800 × 2.7 = 12960 (NB: prep-yaml comment uses 12 m/trial = 57600; the 2.7 number assumes heavier ASHA pruning) | 12960 / 122 = 106 | 106 × $0.15 × 1.03 = **$16.39** if 2.7 m/trial holds; **$70.79** at 12 m/trial | $30.51 (best-case) – $84.91 (Phase-7-prep budget cap) |

**Cumulative through Wave 3 best-case**: $30.51. **Cumulative at the
existing Phase 7 prep cap**: $84.91 (under the $85 ceiling).
The 2.7 vs 12 m/trial gap is the biggest source of forecast uncertainty.

**Recommendation**: launch Wave 1 first to **measure the actual
matchups/trial under V2 (which has different TTK distributions than V1)**,
then re-forecast Wave 3. Wave 1's $10.27 budget consumes 12 % of the
$85 cap and gives a ground-truth m/trial number.

**Wall-clock**:

- Wave 0: ~30 min sequential (VM boot dominates).
- Wave 1: 3 batches × (250 trials × 2.7 / (8 VMs × 122 matchups/hr)) ≈
  3 × 0.69 hr = 2.1 hr + 3 × ~5 min provisioning ≈ **2.4 hr**.
- Wave 2: 3 studies × ~0.6 hr each = **1.8 hr**.
- Wave 3: 8 studies in parallel × (600 × 2.7 / (12 × 122)) = 1.1 hr +
  provisioning → **1.5 hr**.
- **Campaign total**: **~6 hr active wall-clock**, with possible queue
  time between waves driven by gate review (recommend 24-hr review
  budget per gate; total 4–5 days calendar).

**Sensitivity** (±25 %):

- Trial count +25 % → Wave 1 → $12.84, Wave 3 cap → $88.50 over.
  Mitigation: drop one Wave 1 seed (3 → 2) saves $3.42.
- Per-trial throughput −25 % (i.e. 92 m/hr/VM) → Wave 1 → $13.69,
  Wave 3 → $94.39 over. Mitigation: as above, plus reduce Wave 3 to
  6 hulls × 600 trials = $63.59.
- Per-trial throughput +25 % → costs scale linearly down; under-budget
  by $20+ — no action needed.

## 5. Statistical-power notes (curse of dimensionality)

Per-hull search-space cardinality is in the operator inventory:
log10 ≈ 41–43 for Hammerhead, ~33–37 for Wolf. Even Wolf's 10³³ raw
cardinality is far beyond enumeration. Validation strategy is therefore
**ablation + seed replication on metric estimators**, not coverage.

### Required N per mechanism

- **EB shrinkage** (mech 7): `eb_min_builds=8` floor (`models.py:479`);
  N≥200 for stable γ̂ at original p=7
  (`docs/reference/phase5d-covariate-adjustment.md:172-177`, 72/84 cells
  cleared at p=8 knee); operator-set bump to **N≥250** for post-prep p=10.
- **Box-Cox** (mech 8): `min_samples=8` MLE floor (`models.py:494`); N≥200
  for stable saturation % (5E used n=313, `phase5e-shape-revision.md:123-131`).
- **LOOO probing** (mech 7 metric): requires ≥3 anchors
  (`anchor_burn_in=30` + `n_anchors=3`, `models.py:464-465`); per-anchor
  sample at N=250 is n=247.

### Δρ +0.02 statistical power

For Spearman ρ at α=0.05 two-tailed, `var(ρ̂) ≈ (1−ρ²)/(n−1)`. At ρ≈0.3
(mid 5E band) and Δρ=+0.02:

- n=200 → σ_ρ̂ ≈ 0.068 → single-seed power ≈ 9 %.
- n=250 → σ_ρ̂ ≈ 0.060 → power ≈ 11 %.
- 3 seeds via Stouffer's z → power ≈ 30 % (still marginal).
- **Production methodology**: V1 ship gate +0.036 was a *5-anchor
  bootstrap × 200-iter resample* (`docs/reference/phase5d-covariate-adjustment.md:301`),
  not raw Spearman ρ. Bootstrap pools across anchors — single-seed
  power calc is pessimistic. **Plan: report 5-anchor bootstrap CI as
  headline; single-seed ρ as secondary diagnostic**, matching V1.

## 6. Risk register + mitigations

| # | Risk | Early-warning signal | Mitigation |
|---|---|---|---|
| R1 | Spot preemption spike in us-east-1 | Wave 1 ledger shows > 5 % of provisioned VMs preempted within 30 min | Wave-1 yaml uses `regions: [us-east-1, us-east-2]` (already in `phase7-prep-shakedown.yaml:33-35`); add us-west-2 as 3rd region for Wave 3 if Wave 1 shows preempt > 10 %. Each region needs an AMI (extra `bake_image.sh` invocation; ~15 min each) |
| R2 | Redis OOM on workstation under 32-VM concurrency (Wave 1 batches) | `redis-cli INFO memory` shows `used_memory_peak_human > 50 % of system RAM` | Each VM publishes ~1 row/30 s; 32 VMs × 100 trials = 3200 rows = ~5 MB. Negligible at expected scale. Mitigation: Wave 1 batches sequentially (not parallel) keep peak at 32 VMs |
| R3 | AMI version drift (V2 fix not in latest AMI) | Wave 0 step 3 (loadout AB test) shows `LOADOUT_MISMATCH` | Re-bake before Wave 0: `scripts/cloud/bake_image.sh`. AMI tags `GameVersion` + `ModCommitSha` checked by mechanism 19 preflight (per CLAUDE.md "Phase-7-prep refactor", "Preflight (Commit G R6)") |
| R4 | Engine probe regression (manifest stale) | Wave 0 step 1 probe fails with manifest mismatch | `scripts/update_manifest.py --timeout 600` rerun before bake; gated by pre-commit hook per CLAUDE.md |
| R5 | Frigate τ̂² collapse persists under V2 (Wave 2 wolf cell) | Wolf JSONL `twfe_fitness` variance < 1e-3 | Treat as in-scope finding, not abort; defer Wave 3 wolf cell, document as Phase 7 dependency. The V1 collapse was attributed in part to the V1 loadout bug (no weapons firing) — V2 should fix it, but if it doesn't, it's a real signal |
| R6 | Tailscale ACL drift breaks worker→workstation Redis | Wave 0 multi-worker smoke shows worker `[FAIL] tailscale up` in CloudWatch | `docs/reference/phase6-deferred-audit-findings-2026-04-19.md` § "Additional findings" R2 (Tailscale ACL-as-code via Terraform) is deferred; manual ACL check before each wave |
| R7 | Concurrent SG-replication lag (>4 fleets) | Wave 1 batch-3 fleet provisioning fails with `InvalidGroup.NotFound` | Already mitigated in code (`AWSProvider._ensure_security_group` blocks on waiter, `_create_fleet_in_region` retries); regression test in `tests/test_cloud_provider.py::TestFleetProvisionSGPropagation` |
| R8 | Optuna SQLite lock contention (> 16 concurrent trials/study) | study DB write timeouts in subprocess logs | Optuna SQLite handles ~32 concurrent writes; Wave 1 stays at 16 (8 workers × 2 slots). For Wave 3 prep run, `phase7-prep.yaml` uses one DB per study (no contention) |
| R9 | Box-Cox MLE fails on degenerate populations | JSONL `shape_passthrough_reason` consistently `transformed_constant` | Already coded as fallback (`optimizer.py:1186-1191`). If > 25 % of trials hit this, downstream fitness collapses to 0.5 — gate would fail naturally. Real-world example: a hull whose τ̂² is too small (frigates pre-V2). Fix path: collect data, decide if hull is in-scope |
| R10 | Wave 1 Δρ < +0.02 on Hammerhead under V2 | Wave 1 LOOO bootstrap CI excludes +0.02 | See decision tree (§7) |

## 7. Decision tree for failure modes

### F1: Wave 1 EB Δρ < +0.02 (mechanism 7 fails)

- **F1a**: If C2 has Δρ < +0.02 vs C0 *but* C1 (EB-only no Box-Cox) has
  Δρ ≥ +0.02 vs C0 → Box-Cox is masking the EB win. Action: re-run C2
  with `triple_goal=False` (mechanism 6 ablation). If still flat,
  retain EB but reconfigure Box-Cox `min_samples` higher (50?).
- **F1b**: If both C1 and C2 have Δρ < +0.02 but ≥ 0 → EB is net-neutral.
  Action: investigate covariate set. The post-Phase-7-prep covariate
  bump (p=7 → p=10) added 3 engine-stat covariates that may be
  collinear with Python-raw covariates. Drop `op_used_fraction` first
  (newest, `optimizer.py:434-459`) and re-run on the *existing* JSONL
  (no new sim cost — analysis re-run only). This is a covariate-set
  re-tune, not a campaign re-run.
- **F1c**: If Δρ < 0 (active harm, like the V1 5D.v1 conditioning-paradigm
  refutation) → roll back to scalar control variate as default
  (`models.py:469-480` defaults change). Wave 3 would then run with
  EB explicitly off. This is the *known-precedented* rollback path
  (`docs/reference/phase5d-covariate-adjustment.md:9` documents the
  v1 → v2 paradigm flip).

### F2: Wave 1 Box-Cox ceiling > 1 % OR top-5 Jaccard < 0.40

- **F2a**: Ceiling high (> 1 %) but top-5 OK → A3 is firing but the
  population has degenerate λ. Action: re-fit with population trimmed
  to top 95 % (drop outliers). `optimizer.py:1129-1194` is the
  `_shape_fitness` function; trimming would be a one-line change but is
  out of scope for this validation plan — defer as Phase-5E follow-up.
- **F2b**: Top-5 Jaccard low → ceiling collapsed but rank-magnitude
  preservation is poor. Action: investigate Box-Cox λ history
  (`shape_lambda` JSONL). If λ ≈ 0 (log transform) consistently, this
  is the production case. If λ variable across trials, refit cadence
  may be the issue (currently every `_finalize_build` call,
  `docs/reference/phase5e-shape-revision.md:181` notes "research doc's
  every-N-trials cadence" was *not* shipped).

### F3: Wave 0 V2 loadout invariant fails (mechanism 18: any LOADOUT_MISMATCH)

- **F3a**: Single mismatch → AMI may be stale. Re-bake, re-run Wave 0
  step 3 only. Cost: ~15 min + $0.10.
- **F3b**: Persistent mismatch on rebake → `MissionDefinition.addToFleet`
  V2 path regressed. Halt campaign. Investigate
  `combat-harness/src/main/java/.../CombatHarnessPlugin.java` `doSetup`
  and the `member.setVariant(VariantBuilder.createVariant(spec), false, true)`
  call referenced in CLAUDE.md "Combat-harness loadout fix 2026-05-10
  (V2 — final)".

### F4: Frigate gradient (Wolf) still degenerate post-V2

- **F4a**: τ̂² < 1e-3 with V2 fix → indicates frigate-specific issue
  (e.g., AI mispiloting frigates in 1v1 sim, or opponent pool too easy
  for a frigate to win). Action: analyze winner distribution; if > 80 %
  player-wins, opponents too easy (need harder frigates in pool); if
  > 80 % timeouts, AI mispilot (deferred to Phase 7's
  AI-compatibility mode-collapse mechanism — `docs/reference/phase7-search-space-compression.md`
  §AI pilotability).
- **F4b**: τ̂² OK on Wolf but Δρ EB still < +0.02 → frigate covariates
  may differ from destroyer covariates (different OP economy,
  different shield arc). Defer Wolf-tuned covariate set to Phase 7
  as a per-hull-class tuning task; ship Hammerhead-tuned covariates
  for Wave 3.

### F5: Wave 3 cost overrun (mechanism 17 trips at 95 %)

- **F5a**: At 95 % of `budget_usd`, half trials complete → drop the
  last 2 hulls (`gryphon`, `onslaught` — capitals, longest TTK).
  This requires editing `phase7-prep.yaml` mid-run; CampaignManager
  does not support live YAML reload, so the abort cost is the spend
  to date. Gate value: stop *before* 95 % to leave headroom.
- **F5b**: At 95 %, < 30 % trials complete → throughput failure.
  Halt, investigate (mechanism 16 below 60 m/hr/VM) — see kill-switch §3.

---

This plan is runnable end-to-end against the current shipped codebase.
Wave 0 gates everything; Wave 1 is the load-bearing algorithmic
re-validation; Waves 2–3 extend coverage incrementally. Total
worst-case spend ($85) sits at the existing Phase 7 prep ceiling
without exceeding it. Operator-set thresholds are flagged for
confirmation before launch.
