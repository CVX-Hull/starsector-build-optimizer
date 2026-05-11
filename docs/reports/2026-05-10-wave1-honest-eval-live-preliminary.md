---
type: report
status: draft
last-validated: 2026-05-10
---

# Wave 1 Honest-Eval - Live Preliminary Snapshot

## Abstract

This is a read-only in-flight snapshot of the resumed Wave 1 honest-eval
run `starsector-honest-eval-wave1-c0a-20260510T170431Z`, captured on
2026-05-10 at approximately 19:54 EDT. The run is healthy at this checkpoint:
31,980 unique ledger rows, no duplicate physical rows, `LOADOUT_OK`
equal to accepted result posts, and zero observed stall signatures in the
current orchestrator log. The only provisional build-quality conclusion
available so far is that the completed c0a and c0b panels are close on
mean oracle score, with c0a ahead at this checkpoint. c1 is partial;
c2, c3, and random-baseline are not yet estimable.

This report does not replace the final honest-eval verdict. The ledger is
prefix-ordered by dispatch, so incomplete-cell rankings are biased until
all 87,480 matchups complete and the normal honest-eval outputs are
written.

## 1. Methods

### 1.1 Data

The input ledger is:

`data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`

The current-run orchestrator log is:

`data/honest_eval/orchestrator-20260510T233515Z.log`

Each ledger row is one completed matchup keyed by
`(build_id, opponent_variant_id, replicate_idx)`, with `fitness` equal to
the honest-eval combat fitness scalar. The full design is 54 builds x 54
opponents x 30 replicates = 87,480 matchups, with 1,620 matchups per
complete build panel.

### 1.2 Estimators

For each build panel, this snapshot computes the arithmetic mean of
ledger `fitness` values as the provisional oracle score and the standard
error of that mean. For a cell, "complete-build mean oracle" is the mean
of completed build-panel oracle scores. This is the same aggregation
shape used by the final honest-eval report, but only complete panels are
interpretable during the live run.

### 1.3 Diagnostics

This snapshot checks the stall signatures from
[2026-05-10-wave1-honest-eval-stall-checkpoint.md](2026-05-10-wave1-honest-eval-stall-checkpoint.md):

- duplicate or rejected result posts (`HTTP 409`)
- requeue lines
- `LOADOUT_MISMATCH`
- result-envelope or matchup-id mismatch
- corrupt result, traceback, worker timeout, budget, or preflight errors
- divergence between `LOADOUT_OK` lines and accepted `HTTP 200` result posts

## 2. Progress

**Method (section 1.1).** Unique resume keys are deduplicated by the ledger key.

| metric | value |
|---|---:|
| physical ledger rows | 31,980 |
| unique resume keys | 31,980 |
| duplicate physical rows | 0 |
| total expected matchups | 87,480 |
| total progress | 36.6% |
| new rows since resume baseline | 2,457 |
| remaining matchups | 55,500 |
| observed builds | 20 / 54 |
| complete builds | 19 / 54 |
| partial builds | 1 |
| current-run ETA at observed HTTP 200 rate | 6.7 h |

**Reading.** Resume accounting is clean: no duplicate ledger rows are
present. The ETA is based only on the current log's observed accepted
result rate and should be treated as a moving estimate.

## 3. Cell Coverage

**Method (section 1.2).** Complete-build means use only panels with all 1,620
expected matchup results.

| cell | builds observed | complete builds | results | coverage | complete-build mean oracle | observed-build mean oracle |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c0a | 9 | 9 | 14,580 | 100.0% | -0.0906 | -0.0906 |
| wave1-c0b | 9 | 9 | 14,580 | 100.0% | -0.1042 | -0.1042 |
| wave1-c1 | 2 | 1 | 2,820 | 19.3% | -0.1051 | -0.1638 |

**Reading.** c0a and c0b are complete and are the only cells that can be
compared as cells in this snapshot. c0a is ahead of c0b by +0.0136 mean
oracle. c1 has one complete panel and one partial panel, so it is not yet
a cell-level verdict. c2, c3, and random-baseline have not entered the
ledger yet.

## 4. Complete-Build Ranking

**Method (section 1.2).** Ranking is by completed build-panel oracle score.

| rank | cell | seed | source rank | n | oracle mean | SE | min | max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | wave1-c0a | 2 | 1 | 1,620 | +0.1104 | 0.0250 | -1.0000 | +1.5000 |
| 2 | wave1-c0b | 2 | 3 | 1,620 | +0.0610 | 0.0248 | -1.0000 | +1.5000 |
| 3 | wave1-c0b | 2 | 2 | 1,620 | +0.0401 | 0.0243 | -1.0000 | +1.5000 |
| 4 | wave1-c0a | 0 | 2 | 1,620 | -0.0010 | 0.0239 | -1.0000 | +1.5000 |
| 5 | wave1-c0b | 2 | 1 | 1,620 | -0.0189 | 0.0240 | -1.0000 | +1.5000 |
| 6 | wave1-c0a | 0 | 1 | 1,620 | -0.0265 | 0.0247 | -2.0000 | +1.5000 |
| 7 | wave1-c0a | 2 | 3 | 1,620 | -0.0368 | 0.0252 | -1.0000 | +1.5000 |
| 8 | wave1-c0b | 0 | 3 | 1,620 | -0.0984 | 0.0282 | -1.0000 | +1.5000 |
| 9 | wave1-c1 | 0 | 1 | 1,620 | -0.1051 | 0.0242 | -1.0000 | +1.5000 |
| 10 | wave1-c0a | 1 | 2 | 1,620 | -0.1087 | 0.0279 | -1.0000 | +1.5000 |
| 11 | wave1-c0b | 1 | 1 | 1,620 | -0.1301 | 0.0253 | -1.0000 | +1.5000 |
| 12 | wave1-c0a | 2 | 2 | 1,620 | -0.1425 | 0.0232 | -1.0000 | +1.5000 |
| 13 | wave1-c0a | 0 | 3 | 1,620 | -0.1619 | 0.0250 | -1.0000 | +1.5000 |
| 14 | wave1-c0a | 1 | 1 | 1,620 | -0.1712 | 0.0276 | -1.0000 | +1.5000 |
| 15 | wave1-c0b | 1 | 2 | 1,620 | -0.1752 | 0.0257 | -1.0000 | +1.5000 |

**Reading.** The current top completed panel is c0a/seed2/rank1. The
next four completed panels are mostly c0b/seed2, so the top-build view is
more favorable to c0b than the cell-mean view. This is exactly why the
final report should include both top-1 and mean-top-K oracle tables.

## 5. Active Partial Panel

**Method (section 1.2).** Partial panels are shown for monitoring only.

| cell | seed | source rank | n | coverage | observed mean | SE |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c1 | 0 | 2 | 1,200 | 74.1% | -0.2224 | 0.0270 |

**Reading.** This partial c1 panel should not be used for ranking because
its opponent/replicate coverage is incomplete.

## 6. Existing-Report Gates

**Method (section 1.3).** Gates mirror the final report scaffold and the stall
checkpoint report.

- F1c C2-vs-baseline gate: not estimable yet; C2 complete panels are not present.
- Random-baseline existence check: not estimable yet; random-baseline panels have not completed.
- Stall checkpoint regression: no current evidence of the previous collapse signatures.

## 7. Current Orchestrator Health

**Method (section 1.3).** Counts are from the current orchestrator log only, not
from the pre-resume log.

| metric | value |
|---|---:|
| HTTP 200 result posts | 2,457 |
| LOADOUT_OK lines | 2,457 |
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
| observed HTTP 200 rate | 137.4 / min |

### 7.1 15-minute result bins

The first and last buckets may be partial wall-clock buckets.

| local time bucket | results | nominal rate/min |
|---|---:|---:|
| 19:30 | 1,070 | 71.3 |
| 19:45 | 1,387 | 92.5 |

**Reading.** The important signal is not the bucket rate, because the log
starts inside the 19:30 bucket. The exact elapsed-window rate is 137.4
accepted result posts per minute, with no stall-regression signatures.

## 8. Synthesis & Decisions

The run should continue. The current health checks are clean, and the
timing fixes described in the stall checkpoint are holding through this
snapshot. The final honest-eval analysis should retain the same structure
as the existing report scaffold: mean top-K oracle by cell, top-1 oracle
by cell, F1c deltas for C2 versus c0a/c0b, random-baseline existence, and
stall-regression diagnostics.

The early build-quality read is conservative: c0a is slightly ahead of
c0b by completed-cell mean, while c0b contributes several of the top
individual completed panels. Nothing can be concluded yet about c1, c2,
c3, or random-baseline.

## 9. Open Questions / Next Steps

- Rerun the partial analyzer periodically while the live run continues.
- Once all 87,480 matchups complete, generate the normal per-cell
  `honest_eval.json` outputs and replace this draft with a full final
  honest-eval report.
- If any future snapshot shows 409s, requeues, loadout mismatches, or
  result-envelope mismatches, treat it as a stall-regression incident and
  inspect worker logs before resuming further.

## Appendix - File Map

- Producer: `scripts/analysis/wave1_honest_eval_partial.py`
- Ledger: `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Current orchestrator log: `data/honest_eval/orchestrator-20260510T233515Z.log`
- Prior stall report: [2026-05-10-wave1-honest-eval-stall-checkpoint.md](2026-05-10-wave1-honest-eval-stall-checkpoint.md)
- Final-report scaffold producer: `scripts/analysis/wave1_honest_eval_report.py`
