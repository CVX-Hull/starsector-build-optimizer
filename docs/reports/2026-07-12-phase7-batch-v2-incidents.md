---
type: report
status: shipped
last-validated: 2026-07-12
---

# Phase 7 Learned-Batch v2 Re-Run Incidents: ERR-Trap Worker Loss and Component-Vocab Overshoot Infeasibility

## Abstract

The 183-job AWS re-run of the Phase 7 canonical surrogate matrix (spec 31 v2
harness) lost its first two attempts to infrastructure defects before any
science landed. Attempt 1 (2026-07-11, $24.32) produced **0 of 183 accepted
results**: a bash ERR trap fired on the reap of an intentionally SIGTERM'd
process even under `set +e`, so every worker declared failure and shut down
after each *successful* experiment, racing its own result upload. Attempt 2
(2026-07-11 → 07-12, $92.28) completed all 183 jobs but the merge refused to
publish: **24 component-vocab cells** returned structured insufficiency
because the designed overshoot cap of 0.15 was structurally infeasible on the
wave-1 vocabulary — a local probe shows achievable held-out test fractions
are quantized at ~0.23 / ~0.33 / ~0.49, so only 2 of 10 canonical seeds could
draw legal splits. Both root causes are fixed (commits `02b3835`, `2c159ea`,
`2d8526a`): waits folded into `|| CODE=$?` lists, the cap amended to 0.35
with a per-cell `realized_test_fraction` reading obligation, and a
launch-time split-feasibility preflight that re-detects the attempt-2 failure
in seconds on local data. This report covers only the incidents, their
mechanisms, and the feasibility evidence; the surrogate results themselves
are the subject of the forthcoming attempt-3 merge report.

## Methods

### Data

- **Unit of analysis (incidents):** control-plane batch runs of the
  `phase7-learned-batch-v2-202607` campaign; per-run evidence is the budget
  ledger (`ledger.jsonl`), worker/job event logs (`events/*.jsonl`), the
  orchestrator log (`control-plane-launch.log`), and per-job artifacts
  (`results/*.json`). Attempt archives (local, gitignored):
  `data/phase7/learned_surrogate_batch_v2_2026-07.failed-attempt-1/` and
  `data/phase7/learned_surrogate_batch_v2_2026-07.overshoot-015/`.
- **Unit of analysis (feasibility probe):** component-vocab split draws on
  the wave-1 source DB `data/phase7/wave1_matchups.sqlite` — N = 21,362
  matchup rows over 2,374 builds whose combined component vocabulary
  (weapon/hullmod IDs, per `component_vocabulary()` in
  `src/starsector_optimizer/phase7_matchup_data.py`) has **62 items**; the
  two most ubiquitous hullmods each appear in > 50% of builds
  (`hullmod:armoredweapons` 1,261/2,374; `hullmod:blast_doors` 1,226/2,374).
- **Job matrix:** 183 jobs = 3 models × (5 seeded splits × 10 canonical
  seeds + forward-time), spec 31 canonical matrix, 36 spot workers.

### Definitions

- **Realized test fraction:** the component-vocab split draws vocabulary
  items into the holdout in random seed order until test rows ≥
  `holdout_fraction` (0.20); the realized fraction is whatever the final
  draw produces. Implementation:
  `held_out_component_vocabulary_split()` in
  `src/starsector_optimizer/phase7_matchup_data.py`. A draw whose realized
  fraction exceeds `holdout_fraction + component_vocab_max_overshoot` raises
  `ComponentVocabularyError`.
- **Structured insufficiency:** a worker converts exactly these split
  construction failures into artifacts with envelope `status: completed` and
  `results[0].status` ∈ {`degenerate_component_vocab_split`,
  `empty_outer_split`, `insufficient_inner_groups`}; the merge refuses to
  publish while any exist (spec 31 §Seed policy).
- **Feasibility probe:** for each canonical seed, replay the outer draw and
  the three inner-fold draws (seeds `hpo_seed + fold_idx` = 23+0..2 on
  outer-train rows) exactly as `_split_rows` + `inner_cv_splits` construct
  them, and record where the first `ComponentVocabularyError` occurs.
  Producer script archived at
  `data/phase7/learned_surrogate_batch_v2_2026-07.overshoot-015/vocab_feasibility_probe.py`;
  re-run 2026-07-12 for the numbers below.

### Diagnostics & thresholds

- Designed overshoot cap at incident time: **0.15** (spec 31, designed
  2026-07-11); amended to **0.35** (spec 31, 2026-07-12). Allowed realized
  band is `[holdout, holdout + cap]` = [0.20, 0.35] before, [0.20, 0.55]
  after.
- Merge publication gate: zero insufficiency artifacts across all 183 jobs
  (spec 31 §Learned AWS Batch Artifacts).

## Results

### Incident 1 — ERR-trap worker loss (attempt 1)

**Method (§Data).** Ledger + event counts from the attempt-1 archive.

| Quantity | Value |
|---|---:|
| Window (UTC) | 2026-07-11 18:29 → 20:48 |
| Cost | $24.32 |
| Leases acquired | 80 |
| `experiment_completed` events | 40 |
| `worker_failed` events | 40 |
| Accepted results | **0 / 183** |
| Truncated uploads rejected (HTTP 400) | 7 |

**Reading.** Commit `55a77e9` had removed `|| true` from the
`wait "$RENEW_PID"` reap of the intentionally SIGTERM'd lease-renewal loop.
Bash fires an ERR trap on any failing simple command **even under `set +e`**
(the trap shares errexit's exemption list — if-conditions, `&&`/`||` lists,
negation — not its on/off state), so exit 143 from the reap invoked
`on_failure` after every successful experiment: the worker posted
`worker_failed` and ran `shutdown -h now`, racing its own result upload
(7 uploads were truncated mid-flight and rejected as invalid JSON; the rest
never started). The trap body also clobbered `$?`, which made the exit-70
lost-lease branch unreachable. The mechanism was confirmed with a standalone
bash reproduction before any relaunch. Fix (`02b3835`): fold each wait into
`CODE=0; wait "$PID" || CODE=$?`, which both suppresses the trap and captures
the true exit status; pinned by an executable bash test and a rendered-script
sweep asserting no unprotected `wait` remains
(`tests/test_phase7_learned_batch.py`).

### Incident 2 — component-vocab overshoot infeasibility (attempt 2)

**Method (§Data, §Definitions).** Artifact statuses + ledger from the
attempt-2 archive.

| Quantity | Value |
|---|---:|
| Window (UTC) | 2026-07-11 21:13 → 2026-07-12 07:37 |
| Cost | $92.28 |
| Artifacts returned | 183 / 183 |
| `completed` results | 159 |
| `insufficient_inner_groups` | 21 |
| `degenerate_component_vocab_split` | 3 |
| Publishable | **no** (merge refused) |

**Reading.** All 24 insufficiency artifacts are component-vocab cells, and
they are seed-deterministic across the three models (8 seeds × 3 models):
seed 109 failed the outer draw; seeds 101, 107, 113, 127, 131, 137, 149
failed an inner-fold draw. Split construction is a pure function of the
local source DB, so this was discoverable before provisioning — the batch
spent ~10.4 hours and $92 discovering it on AWS instead.

### Feasibility probe — quantized realized test fractions

**Method (§Definitions).** Per-seed replay at the incident knobs
(holdout 0.20, cap 0.15), N = 21,362 rows / 2,374 builds / 62 vocab items.

| Seed | Outer draw | First failing draw | Realized fraction at failure |
|---:|---|---|---:|
| 101 | ok | inner 1 | 0.486 |
| 103 | **feasible** (0.332, 2 items held out) | — | — |
| 107 | ok | inner 1 | 0.490 |
| 109 | **fail** | outer | 0.491 |
| 113 | ok | inner 1 | 0.507 |
| 127 | ok | inner 1 | 0.512 |
| 131 | ok | inner 1 | 0.493 |
| 137 | ok | inner 1 | 0.358 |
| 139 | **feasible** (0.234, 2 items held out) | — | — |
| 149 | ok | inner 1 | 0.490 |

Knob sweep (fraction of the 10-seed bank fully feasible — outer + all 3
inner draws):

| holdout | cap | Outer feasible | Fully feasible |
|---:|---:|---:|---:|
| 0.20 | 0.15 | 9/10 | **2/10** |
| 0.20 | 0.25 | 9/10 | 3/10 |
| 0.20 | 0.35 | 10/10 | **10/10** |
| 0.20 | 0.50 | 10/10 | 10/10 |
| 0.15 | 0.15 | 7/10 | 2/10 |
| 0.15 | 0.35 | 10/10 | 8/10 |
| 0.25 | 0.35 | 10/10 | 10/10 |

**Reading.** The greedy draw's achievable test fractions are quantized by
the coarse, heavy-tailed vocabulary: each additional held-out item drags in
every row of every build containing it, and with 62 items — two of them in
more than half of all builds — the achievable fractions cluster near ~0.23,
~0.33, and ~0.49. The 0.15 cap admitted only the first two clusters, which
most seeds' draws never land in; 0.35 (band up to 0.55) admits all three and
is the smallest probed cap that makes the whole bank feasible at the 0.20
holdout. The failure was structural, not stochastic: no re-seeding of the
existing bank fixes 0.15.

## Synthesis & decisions

1. **Overshoot cap amended 0.15 → 0.35** (`2c159ea`; spec 31, dataclass
   default, example YAMLs). Consequence stated in spec 31: component-vocab
   panels now span realized test fractions from ~0.23 to ~0.51, so
   **consumers must read `realized_test_fraction` per cell** rather than
   assuming the nominal 0.20, and treat it as a covariate when aggregating
   across seeds or comparing against other split families. A draw biased
   toward rare items could hit 0.20 exactly but was rejected: it would
   silently change the claim from "transfer to unseen components" to
   "transfer to unseen *rare* components".
2. **Launch-time split-feasibility preflight** (`2d8526a`): every unique
   (split, seed) cell is dry-run through the same `construct_splits` path
   the workers execute, before any AWS resource is provisioned. Verified on
   wave-1 data: the 0.35 config passes all 61 cells; the 0.15 config is
   refused naming the same 8 cells attempt 2 spent its runtime discovering.
   The post-impl audit then removed the preflight's hand-mirrored config: it
   now parses the rendered job command through the experiment script's own
   parser, with a rendered-userdata ↔ job-command flag-parity test.
3. **Worker shell hardened** (`02b3835` + audit follow-up): all waits
   ERR-trap-safe; the result upload — previously a bare curl that one
   transient failure would turn into a discarded finished experiment — now
   retries (`result_upload_attempts` × `result_upload_retry_seconds`,
   designed defaults 5 × 10 s, validated to fit inside the lease grace
   window) before posting `result_upload_failed`.
4. **Process:** post-impl audit and a stale-AWS-resource sweep are now
   standing pre-launch gates alongside the AMI-digest and feasibility
   preflights.

## Open questions / next steps

- Attempt 3 (launched 2026-07-12 12:31 UTC with the amended cap) merged
  cleanly the same day — 183/183, zero insufficiency, $75.50; surrogate
  evidence:
  [2026-07-12-phase7-attempt3-surrogate-results.md](2026-07-12-phase7-attempt3-surrogate-results.md).
- The wave-1 vocabulary (62 items) is the binding constraint. If wave-2 data
  widens the vocabulary substantially, the achievable-fraction quantization
  relaxes and a tighter cap could be re-designed; revisit before reusing the
  0.35 default on richer data.
- Fleet workers idle once the queue drains below the worker count
  (scale-down-on-drain); noted for the Phase 7.5 infra item.

## Appendix — file map

- **Producer script:**
  `data/phase7/learned_surrogate_batch_v2_2026-07.overshoot-015/vocab_feasibility_probe.py`
  (archived copy; local-only).
- **Raw data:** attempt archives
  `data/phase7/learned_surrogate_batch_v2_2026-07.failed-attempt-1/` and
  `…/learned_surrogate_batch_v2_2026-07.overshoot-015/` (ledgers, event
  logs, 183 attempt-2 artifacts; gitignored, local-only).
- **Charts:** none.
- **Dependent reports:** [2026-07-12-phase7-attempt3-surrogate-results.md](2026-07-12-phase7-attempt3-surrogate-results.md) (the attempt-3 results this report deliberately excludes);
  [2026-07-11-phase7-methodology-review.md](2026-07-11-phase7-methodology-review.md)
  (motivated the re-run);
  [2026-07-11-aws-cost-analysis.md](2026-07-11-aws-cost-analysis.md)
  (fleet-sizing baseline).
