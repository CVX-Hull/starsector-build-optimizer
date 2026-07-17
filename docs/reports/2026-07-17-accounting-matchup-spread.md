---
type: report
status: shipped
last-validated: 2026-07-17
---

# Accounting — matchups-per-trial spread (hammerhead + wolf)

## Abstract

The matchups-per-trial accounting deliverable of roadmap item 3 (owning
plan `2026-07-14-instrumented-accounting-run.md`), from the instrumented
accounting stream. Resolves how many combat matchups an optimizer trial
actually costs — the never-cleanly-measured quantity behind cloud-cost and
throughput planning — separating **matchups dispatched** (the cost basis,
including retries and dispatched-but-unscored work) from **matchups scored**
(useful work), partitioned by trial kind, for the meta hammerhead destroyer
and the previously-never-measured non-meta wolf frigate. Offline extraction,
no sim spend (`scripts/analysis/accounting_extract.py`).

**Headline: a trial costs ~10 matchups when it completes and ~6 when
pruned; dispatched-but-unscored overhead is negligible on a clean run
(0.14% hammerhead, 0.26% wolf), with zero instance-error or worker-timeout
trials.** The instrumentation built to capture the "dispatched but never
scored" gap (spec 24 "the fifth path") confirms that gap is empirically
near-zero here — a reassurance, not a correction, for prior throughput
estimates that used scored counts as a cost proxy.

## Method

Per-trial `matchups_dispatched` (incremented at dispatch in
`_fill_workers`, incl. `RetryableMatchupError`/`WorkerTimeout` re-dispatches)
and `opponents_evaluated` (scored matchups), read from the eval-log rows for
the four logged trial kinds (completed / pruned / cache_hit=0 /
invalid_spec=0) and from the study DB `matchups_dispatched` user_attr for
the two terminal-failure kinds (instance_error / worker_timeout), which emit
no eval-log row (they would orphan the replay's bijective join — spec 24).
Partitioned per kind, per cell, and pooled; aggregate campaign total by
summation. Sources: `data/logs/accounting-{hammerhead,wolf}/…`,
`data/study_dbs/accounting-{hammerhead,wolf}/…`.

## Results

### Pooled, by trial kind

**Hammerhead (9 cells, 2,250 trials):**

| Kind | trials | dispatched (total) | scored (total) | dispatched/trial (median, max) |
|---|---:|---:|---:|---:|
| completed | 1,105 | 11,071 | 11,050 | 10, 13 |
| pruned | 1,120 | 6,635 | 6,631 | 6, 10 |
| cache_hit | 25 | 0 | 0 | 0, 0 |
| invalid_spec / instance_error / worker_timeout | 0 | 0 | 0 | — |
| **total** | **2,250** | **17,706** | **17,681** | overall median 9, mean 7.87 |

**Wolf (3 cells, 600 trials):**

| Kind | trials | dispatched (total) | scored (total) | dispatched/trial (median, max) |
|---|---:|---:|---:|---:|
| completed | 240 | 2,405 | 2,400 | 10, 11 |
| pruned | 290 | 1,894 | 1,888 | 7, 10 |
| cache_hit | 70 | 0 | 0 | 0, 0 |
| invalid_spec / instance_error / worker_timeout | 0 | 0 | 0 | — |
| **total** | **600** | **4,299** | **4,288** | overall median 8, mean 7.17 |

### Readings

1. **Completed trials cost the full opponent panel (~10 matchups).** Both
   hulls run the production 10-opponent panel; dispatched slightly exceeds
   scored (11,071 vs 11,050 hammerhead) — the retry instrumentation firing
   on transient re-dispatches, ~21 extra matchups over 1,105 completed
   trials.
2. **Pruned trials cost ~40% less** (hammerhead median 6 vs 10; wolf median
   7 vs 10): the pruner halts the panel early, the designed saving. Wolf
   prunes marginally later (median 7 vs 6) — a smaller opponent pool and
   faster TTK give the pruner slightly less to cut.
3. **Cache-hit trials cost zero matchups** (25 hammerhead, 70 wolf) — the
   dedup cache short-circuits re-proposed builds, as designed.
4. **Dispatched-but-unscored overhead is negligible:** 17,706 − 17,681 = 25
   matchups (0.14%) hammerhead, 11 (0.26%) wolf. **Zero** instance_error and
   zero worker_timeout trials — the terminal-failure paths the spec-24
   instrumentation was built to account for did not fire on this run. The
   cost basis (dispatched) and the useful-work count (scored) are
   effectively equal here; prior estimates that used scored counts as a cost
   proxy were not materially biased on a clean run.
5. **Wolf resolved.** The non-meta frigate — never measured before the
   re-groom folded it into this run — has the same ~10-completed / ~7-pruned
   matchups-per-trial profile as the meta destroyer, with a slightly higher
   cache-hit rate (70/600 = 11.7% vs 25/2,250 = 1.1%): the smaller wolf
   search space re-proposes builds more often.

## Caveat

These magnitudes describe a **clean** run (no instance churn, no worker
timeouts). The dispatched/scored gap is the quantity that grows under spot
reclamation and worker death; the spec-24 instrumentation now records it, so
a future noisy run's overhead is measurable rather than invisible. Dollar
and throughput conversions are out of scope (V2 cost model pending); this
report asserts matchup counts only.

## Appendix — file map

- Extractor: `scripts/analysis/accounting_extract.py`; raw outputs
  `data/phase7/accounting_accounting-hammerhead.json`,
  `data/phase7/accounting_accounting-wolf.json` (gitignored).
- Instrumentation: spec 24 (`matchups_dispatched` + terminal-reason
  discriminator). Companion replay reading:
  [2026-07-17 oracle-value replay](2026-07-17-phase7-oracle-value-replay.md).
  Owning plan: `2026-07-14-instrumented-accounting-run.md`.
