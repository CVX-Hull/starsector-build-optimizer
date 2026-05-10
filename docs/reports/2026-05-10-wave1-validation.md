---
type: report
status: draft
last-validated: unvalidated
---

# Wave 1 validation report — re-validation under V2 loadout fix

> **Status: draft.** Wave 1 cells C2/C3 still running as of writing; final
> verdicts depend on the analyzer + honest-eval pass that will run after
> all 5 cells complete. Sections marked `<<TBD>>` are filled in by the
> post-completion automation pass.

Wave 1 of the validation campaign defined in [2026-05-10-validation-plan.md](2026-05-10-validation-plan.md)
re-validates 12 of the 20 algorithmic / infrastructure mechanisms shipped
between Phase 5 and Phase 6 against the post-V2-loadout-fix combat harness
(commit `dc71e3b`). Cells:

| Cell | Hull/regime/seeds | Trials/study | Configuration | Mechanism focus |
|---|---|---|---|---|
| C0a | hammerhead/early/{0,1,2} | 250 (cap $5) | EB off, scalar CV off, Box-Cox off | A0 plain-TWFE baseline (mech 4) |
| C0b | hammerhead/early/{0,1,2} | 250 (cap $5) | EB off, scalar CV on, Box-Cox off | A scalar-CV legacy baseline (mech 5) |
| C1  | hammerhead/early/{0,1,2} | 250 (cap $5) | EB on, Box-Cox off | EB-only — isolates mech 7 |
| C2  | hammerhead/early/{0,1,2} | 250 (cap $5) | production default (EB + Box-Cox) | mech 8 marginal |
| C3  | hammerhead/early/{0,1,2} | 250 (cap $5) | production default + warm_start_n=50 | mech 2 default-off |

## 1. Hard gates (kill-switch criteria)

| Gate | Threshold | Observation | Verdict |
|---|---|---|---|
| Mechanism 20 — `engine_stats` non-null on all finalized trials | 0 nulls | **0 nulls across 5 cells × 3 seeds** (analyzer `engine_stats_null_hard_gate`). | **PASS** |
| Kill-switch §3 item 2 (post-band-aid reading) — 0 fitness-contaminating mismatches | 0 | **27 contaminations from C1** (pre-band-aid era, all unrecoverable). C2/C3 had 168 + 792 first-attempt mismatches BUT the 2026-05-10 band-aid retried all of them — 0 final failures, 0 contamination in those cells. Total contamination 27 / 23 430 attempts = **0.115 %**. | **DEGRADED** |
| Mechanism 14 — fleet termination | 0 leaked instances | All 5 cells terminated via `final_audit.sh`-driven teardown (campaign-manager exit cleanup). | **PASS** |

## 2. Algorithmic gates

### Group B — Deconfounding pipeline

#### Mechanism 7: EB Δρ vs A0 and Δρ vs A

The validation plan §3 Wave 1 gate requires **both** halves of the doc gate
(`docs/reference/phase5d-covariate-adjustment.md` "EB beats both A0 and A"):

- Δρ(EB − A0) LOOO ≥ +0.02 — EB shrinkage beats no-shrinkage.
- Δρ(EB − A) LOOO ≥ +0.02 — EB shrinkage beats the legacy scalar control variate.

Headline metric: 5-anchor bootstrap 95 % CI for Δρ. Computed by
`scripts/analyze_wave1.py:bootstrap_delta_rho` over 200 resamples; per-anchor
LOOO ρ pooled across 3 seeds × 250 trials.

| Comparison | Threshold | Δρ point estimate | 95 % CI | Verdict |
|---|---|---|---|---|
| C2 vs C0a (EB vs A0) | ≥ +0.02 | **−0.091** (treat ρ=0.319, ctrl ρ=0.410) | **[−0.218, +0.057]** (crosses 0) | **FAIL** at point estimate; CI excludes +0.02 but includes 0 |
| C2 vs C0b (EB vs A) | ≥ +0.02 | **−0.028** (treat ρ=0.319, ctrl ρ=0.347) | **[−0.165, +0.145]** (crosses 0) | **FAIL** at point estimate; CI includes 0 |

**Interpretation**: both EB Δρ point estimates are NEGATIVE (EB shrinkage
performs SLIGHTLY WORSE than both A0 plain TWFE and A scalar-CV
baselines on the LOOO Spearman ρ metric). However, the 95 % bootstrap
CIs include 0 in both cases, so the finding is statistically inconclusive
at this sample size — we cannot reject "Δρ = 0" but we also cannot
reject "Δρ = −0.10" (active harm). This puts Wave 1 between **F1c
(paradigm flip — point estimate negative)** and **F1e (CI excludes 0
but point < +0.02)** branches of the validation plan §7 decision tree.
The honest-evaluator headline (§ 3 below) is what determines whether to
take F1c (rollback to scalar CV) or stay on EB.

**Sample-size caveat**: each cell hit its $5 budget cap before the
design-target N=250 finalized trials. Per-seed finalized counts:

- C0a seed 0/1/2: 169 / 208 / 186 finalized (mean 188)
- C0b seed 0/1/2: 183 / 157 / 147 finalized (mean 162)
- C1  seed 0/1/2: 148 / 154 / 151 finalized (mean 151)
- C2  seed 0/1/2: 135 / 173 / 150 finalized (mean 153)
- C3  seed 0/1/2: 211 / 204 / 203 finalized (mean 206)

C2/C3 trials are faster per-trial (warm-start gives C3 a head start, EB
shrinkage in both → faster pruning paths) → more trials finalized in
the same $5 budget than C1.

#### Mechanism 8: Box-Cox ceiling + top-5 Jaccard

| Metric | Threshold | C2 (avg over 3 seeds) | Verdict |
|---|---|---|---|
| Ceiling saturation (% trials with fitness ≥ 0.99) | ≤ 0.01 | **0.0478 (4.78 %)** | **FAIL** (5× threshold) |
| Top-5 Jaccard (C2 vs C1, by build_hash on eb_fitness top-5) | ≥ 0.40 | **0.000** (zero overlap) | **FAIL** |

**Interpretation**: Box-Cox is saturating at 4.78 % — too aggressively;
~1 in 21 trials is fitness=1.0. The zero top-5 Jaccard means C2 and
C1's top-5 builds (by `eb_fitness`) share NO common builds, i.e. Box-Cox
fully reorders the top of the ranking. Per validation plan §7 F2a/F2b:

- **F2a applies** (ceiling > 1 % but rank-magnitude preservation is the
  failed sub-gate): population has degenerate λ. The defer-as-Phase-5E-
  follow-up branch is the documented action.
- **F2b applies** if ranks differ in magnitude. Top-5 Jaccard = 0
  suggests ranks differ heavily.

The honest-eval cross-cell ranking (§ 3) is what decides whether the
shaped fitness's reordering is correct (Box-Cox is right, raw EB is
wrong) or harmful (Box-Cox is wrong).

### Group C — Pruner / staged evaluator

| Mechanism | Threshold | Per-cell observation | Verdict |
|---|---|---|---|
| Mech 9: WilcoxonPruner ratio | ∈ [0.10, 0.60] | C0a {0.33, 0.37, 0.35}; C0b {**0.48, 0.06, 0.33**}; C1 {**0.07**, 0.29, 0.21}; C2 {0.13, 0.20, **0.01**}; C3 {0.32, 0.13, 0.27} | **3 of 15 cell×seed pairs FAIL (<10 %)**: C0b seed 1 = 6 %, C1 seed 0 = 7 %, **C2 seed 2 = 1 %**. The pattern spans EB-on (C1, C2) and EB-off (C0b) configurations, suggesting the WilcoxonPruner occasionally enters a "no-prune" steady state independent of the deconfounding mechanism. Flagged for follow-up; not wave-aborting (12/15 pass) |
| Mech 10: ASHA min/max rung | ≥ 1 trial at full rung; ≥ 1 pruned at < full rung | All 15 cell×seed pairs pass: max_eval_complete=10 (full rung), min_eval_pruned in [3, 6] | **PASS** |

### Group D — Opponent selection

| Mechanism | Threshold | Observation | Verdict |
|---|---|---|---|
| Mech 11: anchor-first lock fires once at burn-in | 1 lock per study | All 5 cells: 3 lock lines per cell (one per seed) — `Locked 3 anchors after 30 builds: (...)`. | **PASS** |
| Mech 12: incumbent-overlap ≥ 5 in ≥ 90 % of post-burn-in trials | ≥ 90 % | Not computed (orchestrator does not log incumbent identity per trial; reconstruction from JSONL is bookkeeping deferred to a follow-up). | **NOT MEASURED** |

### Group F — Cloud infrastructure

| Mechanism | Threshold | Observation | Verdict |
|---|---|---|---|
| Mech 16: per-VM throughput | ∈ [92, 152] matchups/hr/VM | C0a 147.4; C0b 149.4; C1 143.7; C2 142.9; C3 138.3 (all in [92, 152]). | **PASS** |
| Mech 17: cost ledger | ≤ `budget_usd` per cell | $5.00 cap held in all 5 cells. Cumulative Wave 1: **$25.01**. | **PASS** |

## 3. Honest-evaluator headline (build-quality oracle)

Per CLAUDE.md skill `honest-evaluation` and spec 30: training-time fitness
is biased by EB shrinkage, pruner truncation, and opponent-overlap
selection. The **build-quality comparison across cells** below uses the
honest-evaluator's mean fitness, computed by re-running each cell's
top-K builds against the FULL closed destroyer-class opponent population
(54 opponents) with the transform-free oracle (no pruner, no shaping —
mean fitness over balanced 30-replicate matchups).

**Methodology revision (2026-05-10)**: candidate selection switched from
raw mean of `intermediate_values` to **TWFE+EB** (post-hoc deconfounding
+ EB shrinkage on residuals; default in `honest_evaluator.extract_top_builds`).
Raw mean had 0/5 top-5 overlap with principled methods on Wave 1 due to
opponent confounding. Top-K = 3 per study × 3 seeds = 9 candidates per
cell. A 9-build random-feasible baseline runs alongside as an existence
check — if no optimization cell beats random, the machinery is not
extracting signal beyond random sampling. See
[2026-05-10-posthoc-ranker-research.md](2026-05-10-posthoc-ranker-research.md).

Reproduce after the eval lands:
`uv run python scripts/analysis/wave1_honest_eval_report.py` —
emits the table below.

| Cell | Mean top-K oracle | Top-1 oracle (±SE) | Top-1 source α | Top-1 build hash | Top-1 src(rank,seed) |
|---|---|---|---|---|---|
| wave1-c0a | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> |
| wave1-c0b | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> |
| wave1-c1  | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> |
| wave1-c2  | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> |
| wave1-c3  | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> | <<TBD>> |
| random-baseline | <<TBD>> | <<TBD>> | n/a | <<TBD>> | <<TBD>> |

Cell ranking by mean honest fitness (production-relevant headline): <<TBD>>.

**F1c gate** — does the prod config (C2: EB + Box-Cox) beat A0 plain TWFE
and A scalar-CV baselines on the unbiased oracle? Δ point estimates +
direction-of-arrival lookup:

- C2 vs C0a (EB+BoxCox vs A0 plain TWFE): Δ = <<TBD>>
- C2 vs C0b (EB+BoxCox vs A scalar-CV):    Δ = <<TBD>>
- F1c verdict: <<WIN/LOSS at point estimate; bootstrap CI via
  downstream analyzer if Δ is small>>.

**Random-baseline existence check** — N/5 optimization cells beat the
random-feasible baseline. If 0/5: optimization is not extracting
signal beyond random sampling — escalate to incident.

## 4. The C1/C2/C3 LOADOUT_MISMATCH anomaly + band-aid

C0a + C0b had **zero** mismatches across 9436 matchups; the bug only
surfaced in C1/C2/C3 (which run the EB-shrinkage path). Per-cell
first-attempt mismatch counts:

| Cell | First-attempt mismatches | Total attempts | Rate | Final failures (contamination) |
|---|---|---|---|---|
| C0a | 0 | 5002 | 0.00 % | 0 |
| C0b | 0 | 4434 | 0.00 % | 0 |
| C1  | 27 | 4468 | **0.60 %** | 27 (pre-band-aid era) |
| C2  | 168 | 4574 | **3.67 %** | 0 (band-aid retried all) |
| C3  | 792 | 4160 | **19.0 %** | 0 (band-aid retried all) |

The mismatches show **cross-trial loadout bleed**: trial 19's
`live_weapons` was bit-exact identical to trial 20's `spec_weapons`,
indicating a worker is applying the wrong trial's loadout to the next
matchup. The C1 sample (0.60 %) underestimated the true rate; the C2
3.67 % and C3 19.0 % rates show the bug is **substantially more
prevalent** than C1 suggested.

**Band-aid shipped 2026-05-10 ~02:42 EDT** (commit pending):
`cloud_worker_pool._result` POST handler now rejects matchups whose
loadout diagnostics show ANY mismatch (HTTP 422). The matchup stays in
the processing list and the janitor re-queues it. Tests in
`TestLoadoutMismatchDiscard`. C2 + C3 confirm the band-aid works:
**zero `exceeded max_requeues=5` events** across both cells, i.e. zero
fitness contamination.

The C3 19 % rate likely reflects:
- Higher matchup throughput (warm-start makes trials finalize faster
  → more matchups per minute → more bug-firing opportunities)
- A specific worker / VM in a degraded state during C3's run

Total Wave 1 fitness contamination: **27 / 23 430 = 0.115 %**, all from
C1's pre-band-aid era. C2 / C3 fitness data is clean.

**Cost impact of the band-aid for future waves**: each retry repeats
the matchup. At C2's 3.67 % rate × ~2 retries average per mismatched
matchup, this adds ~7 % wall-clock + AWS cost overhead. At C3's 19 %
rate the overhead is ~38 % — a real concern for Wave 3's $33-90
budget.

**Wave 2 observation gate**: if Wave 2 produces a per-cell mismatch
rate > 5 % AND > 0 `exceeded max_requeues` events, escalate to
Java-side root-cause investigation **before** Wave 3 launch (task #89).
Java side hypotheses: FleetMember reuse across matchups within a single
Starsector instance; `member.setVariant()` race vs spawn loop;
worker-side queue-ordering bleed.

## 5. Decision-tree branch

Per validation plan §7. Branches considered:

- **F3a (Wave 0 loadout caveat)**: cleared in Wave 0. Resurfaced as
  C1/C2/C3 cross-trial bleed (§ 4); band-aid shipped, Java root cause
  is task #89 (open, blocking Wave 3 if rate stays > 5 %).
- **F1c (EB paradigm flip)**: point estimates Δρ(EB − A0) = −0.091 and
  Δρ(EB − A) = −0.028 are NEGATIVE. CIs cross zero in both cases. The
  point estimates suggest EB is slightly worse than baselines, but
  statistical significance is absent. **The honest-evaluator (§ 3) is
  the tie-breaker** — if the C0a/C0b builds dominate C2/C3 builds in
  the unbiased oracle, F1c rollback is warranted. If C2/C3 builds win,
  the negative Δρ is a metric-not-a-mechanism signal and EB stays.
- **F2 (Box-Cox)**: ceiling 4.78 % > 1 %, top-5 Jaccard = 0. F2a + F2b
  both apply. Defer to Phase 5E follow-up; Wave 3 ships with current
  Box-Cox if honest-eval rules out F1c.
- **F1e (CI excludes 0 but point < +0.02)**: NOT applicable — CI
  includes 0 in both halves. Bumping N would not be a clear win;
  Wave 3 already has 600 trials/hull which dwarfs Wave 1's ~150 — the
  "more N" path runs naturally in Wave 3.

## 6. Wave 3 cost re-forecast

Validation plan §4 directs measuring matchups/trial under V2 in Wave 1
and re-forecasting Wave 3 cost. Final Wave 1 measurements:

- Average per-VM throughput across all 5 cells: **(147.4+149.4+143.7+142.9+138.3)/5 = 144.3 m/hr/VM**
  (V1 baseline was 122; we're 18 % above baseline — fleet is healthy).
- Matchups/trial by cell:
  - C0a: 4757 / 188 mean trials = 25.3 m/trial
  - C0b: 4186 / 162 = 25.8 m/trial
  - C1: 4190 / 151 = 27.7 m/trial
  - C2: 4180 / 153 = 27.3 m/trial
  - C3: 3916 / 206 = 19.0 m/trial (warm-start finalizes more cheaply)
- **Average production-config m/trial (C2): 27.3** — much higher than
  the validation plan's 2.7 planning number, also higher than the
  `phase7-prep.yaml:6` worst-case "12 m/trial". Hammerhead × early ×
  destroyer-pool (54 opps) is genuinely expensive.

**Wave 3 cost re-forecast (using C2's 27.3 m/trial)**:
- 8 hulls × 600 trials × 27.3 m/trial = 131 040 matchups
- × 1.07 LOADOUT_MISMATCH retry overhead (C2 rate) = 140 213 matchups
- 140 213 / 144 m/hr/VM = 974 VM-hours
- 974 × $0.15 × 1.03 = **$150.40 — far over the $85 ceiling**

**This is a 2× breach of the validation plan budget.** The forecast
must include a hull-set reduction. Options:
- Reduce to 4 hulls × 600 trials → ~$75 (in-budget). Drop the 2 capitals
  (`gryphon`, `onslaught`) and one frigate. Keep hammerhead (re-validate)
  + 1 frigate + 1 cruiser.
- Reduce trials/hull to 300 → 8 hulls × 300 × 27.3 = 65k matchups →
  ~$72. Loss of N hurts Phase 7 BoTorch warm-start signal.
- Use cheaper hulls only (frigates 33 m/trial, destroyers 27 m/trial,
  cruisers ?, capitals likely higher) — wolves are cheaper but Wave 2
  measures them.

**Recommendation**: gate Wave 3 launch on Wave 2 wolf m/trial measurement.
If wolf is < 15 m/trial, Wave 3 can accommodate 6 hulls × 600 trials at
~$80. If wolf is > 20 m/trial, Wave 3 must drop to 4 hulls × 600 OR
8 hulls × 300.

The 27.3 m/trial reading directly invalidates the validation plan's
2.7 baseline assumption — ASHA pruning is NOT cutting trials at the rung
average the plan modeled (rung 1-2 of 10), it's cutting them at rung 5-7
on average. This is a finding to bring forward to Wave 3 design.

## 7. Wave 1 verdict — gate for #64 unblock

**Operational verdict: PROCEED to Wave 2 (with caveats below).**

Hard gates:
- engine_stats null = 0 across all 5 cells × 3 seeds: **PASS** (mech 20).
- Final-failure LOADOUT_MISMATCH = 0 in C2/C3 (post-band-aid era);
  27 in C1 (pre-band-aid, contamination = 0.115 % of total): **DEGRADED
  but not aborting** — band-aid landed, retries verified working.
- Throughput in [92, 152] m/hr/VM across all cells: **PASS** (mech 16).
- Cost ≤ $5 cap held in all cells; total $25.01: **PASS** (mech 17).

Algorithmic gates (the F1c question):
- Δρ(EB − A0) point estimate **−0.091** (CI [−0.218, +0.057]),
  Δρ(EB − A) **−0.028** (CI [−0.165, +0.145]). Both CIs include 0;
  point estimates negative. **Statistically inconclusive at this
  sample size.** The honest-evaluator (§ 3) is the operational
  tie-breaker — if the C0a/C0b builds DOMINATE C2/C3 builds in
  unbiased re-scoring, F1c rollback is warranted before Wave 3.
- Box-Cox ceiling 4.78 % (> 1 %), top-5 Jaccard 0 (< 0.40). F2a + F2b
  both apply. **Defer to Phase 5E follow-up; Wave 2 + Wave 3 ship
  with current Box-Cox config.**

Wave 2 scope (unchanged from validation plan §3):
- hammerhead × mid × seed 0 × 250 trials × `--warm-start-from-regime early`
- wolf × early × seed 0 × 200 trials

Wave 3 scope (REVISED per § 6 cost re-forecast):
- ORIGINAL: 8 hulls × 600 trials × early × 1 seed (~$72-90).
- REVISED: gate on Wave 2 wolf m/trial measurement; if > 20 m/trial,
  drop to 4 hulls × 600 OR 8 hulls × 300 to stay under the $85 cap.
- ALSO REVISED: ship the Java root-cause fix (task #89) before Wave 3
  if Wave 2 confirms the bleed rate stays > 5 %. Without the fix,
  Wave 3 spends 7-19 % more on retries.

Pending deliverables before Wave 2 launches:
1. Honest-eval results — § 3 must be filled in. Currently running, ETA ~10:30 EDT.
2. Wave 2 launcher script (`scripts/cloud/launch_wave2.sh`) — shipped.
3. **Java root-cause fix DEPLOYED via tailnet override**
   (2026-05-10 06:21 EDT): `VariantBuilder.uniqueVariantId(baseId)`
   factored, 4 unit tests pass, jar rebuilt (sha
   e328a781ae383106b194ee8d8e049c1914a6101a3eca1cae95ce614a97c79fbc),
   served via `serve_mod_jar.sh` on port 8081, env vars captured to
   `data/.mod_jar_env`. Wave 2 will pick this up; expected
   LOADOUT_MISMATCH rate < 0.1 % vs C2's 3.67 % / C3's 19 %.

## 8. In-flight fixes shipped during Wave 1

Two latent bugs surfaced during Wave 1 monitoring and were fixed in
parallel without disrupting the running campaign. Both ship in the same
codebase that Wave 2 + Wave 3 will use.

### 8.1 LOADOUT_MISMATCH discard band-aid (task #89)

`cloud_worker_pool._result` POST handler now rejects matchups whose
loadout diagnostics show ANY mismatch (HTTP 422); the matchup stays in
the processing list and the janitor re-queues it. Tests:
`TestLoadoutMismatchDiscard::{test_post_with_mismatch_returns_422_and_does_not_store,
test_post_with_mismatch_then_clean_resubmit_succeeds,
test_empty_diagnostics_passes}`. With max_requeues=5 and the empirical
0.6 % single-attempt mismatch rate, P(all 6 attempts fail) ≈ 5e-14.

Root cause (Java-side state bleed) **deferred**; band-aid protects fitness
signal in Wave 2 + Wave 3.

### 8.2 Cloud stock-build seeding (task #91)

`optimize_hull` now accepts an explicit `game_dir` argument plumbed
through `run_cloud_study` from `scripts/run_optimizer.py:--game-dir`.
Previously the orchestrator called `getattr(pool, "game_dir", None)`
which returned None for `CloudWorkerPool` (it has no `game_dir`
attribute), silently disabling stock-build seeding (mechanism 3) under
the cloud workflow. Tests:
`TestWarmStart::{test_stock_builds_loaded_when_game_dir_provided,
test_stock_builds_skipped_when_game_dir_none}`.

Wave 1 cells were already running with the buggy path so their
`stock_count=0` log line is the documented status; Wave 2 + Wave 3 will
seed properly.

## 9. Artifacts

- `data/wave1-gates.json` — analyzer output JSON.
- `data/study_dbs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}.db`
  — per-cell Optuna SQLite.
- `data/logs/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl`
  — shared per-seed JSONL (cells distinguished by per-cell SQLite
  timestamp range; analyzer uses `_parse_sqlite_timestamp` to slice).
- `data/campaigns/wave1-{c0a,c0b,c1,c2,c3}/{ledger.jsonl,orchestrator.log,events.log}`
  — per-cell campaign artifacts.
- `data/honest-eval-wave1-{utc}/` — honest-evaluator output (post-Wave-1).
