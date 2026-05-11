---
type: report
status: draft
last-validated: 2026-05-10
---

# Wave 1 Honest-Eval - Live Preliminary Snapshot

## Abstract

This is a read-only in-flight snapshot of the resumed Wave 1 honest-eval
run `starsector-honest-eval-wave1-c0a-20260510T170431Z`, captured on
2026-05-10 at approximately 23:31 EDT after restarting with the
late-result retry fix and freshly baked worker AMIs. The run is still
live and progressing: 60,228 unique ledger rows are present out of
87,480 expected matchups. Resume accounting remains clean, with no
duplicate physical ledger rows.

The important change from the prior live snapshot is that the c1/c2
partial-panel issue is now resolved. The fixed resume replayed 59,654
completed matchups from the ledger, dispatched only the missing work, and
completed both c1 and c2. The previously missing c1 requeue targets now
have ledger rows. Current-run health is clean: `LOADOUT_OK` equals
accepted result posts, with no duplicate posts, requeues, loadout
mismatches, result-envelope mismatches, corrupt results, tracebacks,
worker timeouts, budget errors, or preflight failures in the fixed-run
log.

The provisional quality read has also changed. Among completed panels,
c1/seed1/rank1 is now the strongest observed build panel at +0.2433.
At the cell level, c0a still has the best complete-cell mean among the
four fully complete non-warm-start cells, followed by c0b, c1, then c2.
c2 still trails both baselines. c3 is partially observed and
random-baseline has not completed, so neither should be used for a final
decision yet. This report does not replace the final honest-eval verdict.

## 1. Methods

### 1.1 Data

The input ledger is:

`data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`

The fixed-resume orchestrator log is:

`data/honest_eval/orchestrator-20260511T032626Z.log`

The full design is 54 builds x 54 opponents x 30 replicates = 87,480
matchups, with 1,620 matchups per complete build panel. Each ledger row
is one completed matchup keyed by `(build_id, opponent_variant_id,
replicate_idx)`, with `fitness` equal to the honest-eval combat fitness
scalar.

### 1.2 Estimators

For each build panel, this snapshot computes the arithmetic mean of
ledger `fitness` values as the provisional oracle score and the standard
error of that mean. For a cell, "complete-build mean oracle" is the mean
of completed build-panel oracle scores.

### 1.3 Diagnostics

This snapshot checks the stall signatures from
[2026-05-10-wave1-honest-eval-stall-checkpoint.md](2026-05-10-wave1-honest-eval-stall-checkpoint.md):

- duplicate or rejected result posts (`HTTP 409`)
- stuck-matchup requeue lines
- `LOADOUT_MISMATCH`
- result-envelope or matchup-id mismatch
- corrupt result, traceback, worker timeout, budget, or preflight errors
- divergence between `LOADOUT_OK` lines and accepted `HTTP 200` result posts

## 2. Progress

**Method (section 1.1).** Unique resume keys are deduplicated by the ledger key.

| metric | value |
|---|---:|
| physical ledger rows | 60,228 |
| unique resume keys | 60,228 |
| duplicate physical rows | 0 |
| total expected matchups | 87,480 |
| total progress | 68.8% |
| new rows since resume baseline | 30,705 |
| remaining matchups | 27,252 |
| observed builds | 38 / 54 |
| complete builds | 37 / 54 |
| partial builds | 1 |
| current-run ETA at observed HTTP 200 rate | 3.5 h |

**Reading.** Resume accounting is clean. The fixed run is appending new
rows and has already closed the c1/c2 holes that motivated the restart.
The ETA is based only on the fixed-resume log's accepted-result rate and
should be treated as a moving estimate.

## 3. Cell Coverage

**Method (section 1.2).** Complete-build means use only panels with all
1,620 expected matchup results.

| cell | builds observed | complete builds | results | coverage | complete-build mean oracle | observed-build mean oracle |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c0a | 9 | 9 | 14,580 | 100.0% | -0.0906 | -0.0906 |
| wave1-c0b | 9 | 9 | 14,580 | 100.0% | -0.1042 | -0.1042 |
| wave1-c1 | 9 | 9 | 14,580 | 100.0% | -0.1131 | -0.1131 |
| wave1-c2 | 9 | 9 | 14,580 | 100.0% | -0.1413 | -0.1413 |
| wave1-c3 | 2 | 1 | 1,908 | 13.1% | -0.0370 | +0.3016 |

**Reading.** c0a, c0b, c1, and c2 are now fully complete. c1's prior
partial-panel caveat is resolved. c2's complete-cell mean still trails
both baselines. c3 has one complete panel and one active partial panel;
the observed c3 mean is prefix-biased and not yet comparable.
Random-baseline panels have not completed.

## 4. Complete-Build Ranking

**Method (section 1.2).** Ranking is by completed build-panel oracle score.

| rank | cell | seed | source rank | n | oracle mean | SE | min | max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | wave1-c1 | 1 | 1 | 1,620 | +0.2433 | 0.0245 | -1.0000 | +1.5000 |
| 2 | wave1-c0a | 2 | 1 | 1,620 | +0.1104 | 0.0250 | -1.0000 | +1.5000 |
| 3 | wave1-c0b | 2 | 3 | 1,620 | +0.0610 | 0.0248 | -1.0000 | +1.5000 |
| 4 | wave1-c0b | 2 | 2 | 1,620 | +0.0401 | 0.0243 | -1.0000 | +1.5000 |
| 5 | wave1-c2 | 2 | 1 | 1,620 | +0.0302 | 0.0282 | -1.0000 | +1.5000 |
| 6 | wave1-c1 | 1 | 3 | 1,620 | +0.0190 | 0.0247 | -1.0000 | +1.5000 |
| 7 | wave1-c0a | 0 | 2 | 1,620 | -0.0010 | 0.0239 | -1.0000 | +1.5000 |
| 8 | wave1-c0b | 2 | 1 | 1,620 | -0.0189 | 0.0240 | -1.0000 | +1.5000 |
| 9 | wave1-c1 | 1 | 2 | 1,620 | -0.0255 | 0.0285 | -1.0000 | +1.5000 |
| 10 | wave1-c0a | 0 | 1 | 1,620 | -0.0265 | 0.0247 | -2.0000 | +1.5000 |
| 11 | wave1-c2 | 2 | 2 | 1,620 | -0.0324 | 0.0252 | -1.0000 | +1.5000 |
| 12 | wave1-c0a | 2 | 3 | 1,620 | -0.0368 | 0.0252 | -1.0000 | +1.5000 |
| 13 | wave1-c3 | 0 | 1 | 1,620 | -0.0370 | 0.0241 | -1.0000 | +1.5000 |
| 14 | wave1-c2 | 2 | 3 | 1,620 | -0.0937 | 0.0238 | -1.0000 | +1.5000 |
| 15 | wave1-c0b | 0 | 3 | 1,620 | -0.0984 | 0.0282 | -1.0000 | +1.5000 |

**Reading.** The top-panel view now strongly supports the earlier
training-log signal that c1 produced at least one excellent candidate:
c1/seed1/rank1 is more than 0.13 oracle points ahead of the next completed
panel. The cell-mean view is more conservative because the other c1
panels are much weaker. The final report should therefore retain both
top-1 and mean top-K summaries.

## 5. Active Partial Panels

**Method (section 1.2).** Partial panels are shown for monitoring only.

| cell | seed | source rank | n | coverage | observed mean | SE |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c3 | 0 | 2 | 288 | 17.8% | +0.6402 | 0.0628 |

**Reading.** The only remaining partial panel is c3. Its observed score
is still dominated by dispatch-prefix effects and should not be compared
against complete panels.

### 5.1 c1 missing-row resolution

The prior snapshot diagnosed ten missing c1 rows caused by a late-result
retry race in `CloudWorkerPool`: clean results arrived after the original
`run_matchup()` caller timed out, but before a retry caller was available
to return the retained result to the ledger writer. The fix consumes
retained late results before enqueueing duplicate work and returns a
result if it arrives during timeout cleanup.

The current fixed resume used freshly baked AMIs carrying worker-source
digest `fd2e7e0991435c7d9b5649f679f16e3482d368b3051a175e589826b3d5994adb`.
It replayed the existing ledger, dispatched the remaining missing work,
and completed the c1 panels. The previously missing c1 targets, including
`sunder_CS_rep29`, `shrike_Support_rep24`, and
`manticore_Balanced_rep0`, appeared in the fixed-run log and are now in
the ledger.

## 6. Existing-Report Gates

**Method (section 1.3).** Gates mirror the final report scaffold and the
stall checkpoint report.

- F1c C2 vs C0a complete-panel delta: -0.0508.
- F1c C2 vs C0b complete-panel delta: -0.0372.
- Random-baseline existence check: not estimable yet; random-baseline panels have not completed.
- Stall checkpoint regression: the prior loadout/result-envelope failure signatures remain absent.
- Late-result retry regression: the c1/c2 short panels closed under the fixed resume.

## 7. Current Orchestrator Health

**Method (section 1.3).** Counts are from the fixed-resume orchestrator log
only, not from pre-fix resume logs.

| metric | value |
|---|---:|
| HTTP 200 result posts | 574 |
| LOADOUT_OK lines | 574 |
| HTTP 409 duplicate posts | 0 |
| requeue lines | 0 |
| LOADOUT_MISMATCH lines | 0 |
| matchup_id mismatch lines | 0 |
| ResultEnvelopeMismatch lines | 0 |
| corrupt result lines | 0 |
| ERROR lines | 0 |
| Traceback lines | 0 |
| WorkerTimeout lines | 0 |
| BudgetExceeded lines | 0 |
| preflight failed lines | 0 |
| observed HTTP 200 rate | 130.5 / min |

### 7.1 15-minute result bins

The first and last buckets may be partial wall-clock buckets.

| local time bucket | results | nominal rate/min |
|---|---:|---:|
| 23:15 | 349 | 23.3 |
| 23:30 | 225 | 15.0 |

**Reading.** The fixed resume is healthy. The first bucket includes
provisioning and worker warm-up; accepted posts are now flowing. No
requeue, duplicate, or mismatch signatures have appeared in the fixed-run
log.

## 8. Synthesis & Decisions

The run should continue. The fixed resume has already validated the main
operational hypothesis: the c1/c2 partial panels were recoverable by
restarting with the late-result retry fix and current worker AMIs. The
watchdog is armed for the active evaluator PIDs, and the wrapper's final
audit remains the resource-cleanup backstop.

The build-quality read is still preliminary but sharper than before:

- c1 has the current best individual completed panel.
- c0a has the best complete-cell mean among c0a/c0b/c1/c2.
- c0b remains close to c0a by cell mean and contributes two upper-table panels.
- c2 still trails c0a and c0b by complete-cell mean, so the honest-eval evidence continues to argue against promoting c2 as a production default on current data.
- c3 and random-baseline remain unresolved.

The final honest-eval analysis should retain the same structure as the
existing report scaffold: mean top-K oracle by cell, top-1 oracle by cell,
F1c deltas for c2 versus c0a/c0b, random-baseline existence, c3/warm-start
assessment, and stall-regression diagnostics.

## 9. Open Questions / Next Steps

- Continue monitoring until all 87,480 matchups complete.
- Re-run the partial analyzer if progress slows or the requeue count grows.
- At completion, generate the normal per-cell `honest_eval.json` outputs
  and replace this draft with a full final honest-eval report.
- In the final audit, confirm duplicate-key accounting remains zero and
  that the fixed-run log stays free of loadout, envelope, timeout, budget,
  and preflight errors.
- Do not interpret c3 or random-baseline until their panels complete.

## Appendix - File Map

- Producer: `scripts/analysis/wave1_honest_eval_partial.py`
- Ledger: `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Fixed-resume orchestrator log: `data/honest_eval/orchestrator-20260511T032626Z.log`
- Prior pre-fix resume log: `data/honest_eval/orchestrator-20260510T233515Z.log`
- Prior stall report: [2026-05-10-wave1-honest-eval-stall-checkpoint.md](2026-05-10-wave1-honest-eval-stall-checkpoint.md)
- Final-report scaffold producer: `scripts/analysis/wave1_honest_eval_report.py`
