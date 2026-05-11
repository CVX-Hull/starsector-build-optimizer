---
type: report
status: draft
last-validated: 2026-05-10
---

# Wave 1 Honest-Eval - Live Preliminary Snapshot

## Abstract

This is a read-only in-flight snapshot of the resumed Wave 1 honest-eval
run `starsector-honest-eval-wave1-c0a-20260510T170431Z`, captured on
2026-05-10 at approximately 22:21 EDT. The run is still live and
progressing: 54,494 unique ledger rows are present out of 87,480 expected
matchups. Resume accounting is clean, with no duplicate physical ledger
rows. Current-run health is mostly clean: `LOADOUT_OK` equals accepted
result posts, and there are no observed loadout mismatches, duplicate
posts, result-envelope mismatches, corrupt results, tracebacks, budget
errors, or preflight failures. The current log contains no literal
`WorkerTimeout` exception lines, but caller-level 900 s result timeouts
did occur for the c1 partial panels diagnosed in section 5.1. The one
caveat is 16 stuck-matchup requeues, all with `requeue_count=1`; accepted
result posts continued after those warnings, so this is a live-watch item
rather than a stop-the-run finding.

The provisional quality read has evolved since the earlier snapshot. c0a
and c0b remain complete, c1 is nearly complete, and c2 is partially
observed. Among complete build panels, c0a still has the best individual
panel and the strongest complete-cell mean so far. c1 has one strong
near-complete partial panel, but incomplete panels are not valid
cell-level evidence. c3 and random-baseline have not entered the ledger
yet. This report does not replace the final honest-eval verdict.

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
- stuck-matchup requeue lines
- `LOADOUT_MISMATCH`
- result-envelope or matchup-id mismatch
- corrupt result, traceback, worker timeout, budget, or preflight errors
- divergence between `LOADOUT_OK` lines and accepted `HTTP 200` result posts

## 2. Progress

**Method (section 1.1).** Unique resume keys are deduplicated by the ledger key.

| metric | value |
|---|---:|
| physical ledger rows | 54,494 |
| unique resume keys | 54,494 |
| duplicate physical rows | 0 |
| total expected matchups | 87,480 |
| total progress | 62.3% |
| new rows since resume baseline | 24,971 |
| remaining matchups | 32,986 |
| observed builds | 34 / 54 |
| complete builds | 30 / 54 |
| partial builds | 4 |
| current-run ETA at observed HTTP 200 rate | 3.6 h |

**Reading.** Resume accounting is clean. The run is materially past the
halfway point, and the ledger is still being appended. The ETA is based
only on the current log's observed accepted-result rate and should be
treated as a moving estimate.

## 3. Cell Coverage

**Method (section 1.2).** Complete-build means use only panels with all 1,620
expected matchup results.

| cell | builds observed | complete builds | results | coverage | complete-build mean oracle | observed-build mean oracle |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c0a | 9 | 9 | 14,580 | 100.0% | -0.0906 | -0.0906 |
| wave1-c0b | 9 | 9 | 14,580 | 100.0% | -0.1042 | -0.1042 |
| wave1-c1 | 9 | 7 | 14,570 | 99.9% | -0.1766 | -0.1130 |
| wave1-c2 | 7 | 5 | 10,764 | 73.8% | -0.2101 | -0.1774 |

**Reading.** c0a and c0b remain the only fully complete cells. c1 is
nearly complete but has two panels short of full coverage, so the cell
mean is still not a final verdict. c2 has five complete panels and two
partial panels; its current complete-panel mean trails both c0a and c0b.
c3 and random-baseline have not entered the ledger yet.

## 4. Complete-Build Ranking

**Method (section 1.2).** Ranking is by completed build-panel oracle score.

| rank | cell | seed | source rank | n | oracle mean | SE | min | max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | wave1-c0a | 2 | 1 | 1,620 | +0.1104 | 0.0250 | -1.0000 | +1.5000 |
| 2 | wave1-c0b | 2 | 3 | 1,620 | +0.0610 | 0.0248 | -1.0000 | +1.5000 |
| 3 | wave1-c0b | 2 | 2 | 1,620 | +0.0401 | 0.0243 | -1.0000 | +1.5000 |
| 4 | wave1-c1 | 1 | 3 | 1,620 | +0.0190 | 0.0247 | -1.0000 | +1.5000 |
| 5 | wave1-c0a | 0 | 2 | 1,620 | -0.0010 | 0.0239 | -1.0000 | +1.5000 |
| 6 | wave1-c0b | 2 | 1 | 1,620 | -0.0189 | 0.0240 | -1.0000 | +1.5000 |
| 7 | wave1-c0a | 0 | 1 | 1,620 | -0.0265 | 0.0247 | -2.0000 | +1.5000 |
| 8 | wave1-c0a | 2 | 3 | 1,620 | -0.0368 | 0.0252 | -1.0000 | +1.5000 |
| 9 | wave1-c0b | 0 | 3 | 1,620 | -0.0984 | 0.0282 | -1.0000 | +1.5000 |
| 10 | wave1-c1 | 0 | 1 | 1,620 | -0.1051 | 0.0242 | -1.0000 | +1.5000 |
| 11 | wave1-c0a | 1 | 2 | 1,620 | -0.1087 | 0.0279 | -1.0000 | +1.5000 |
| 12 | wave1-c0b | 1 | 1 | 1,620 | -0.1301 | 0.0253 | -1.0000 | +1.5000 |
| 13 | wave1-c0a | 2 | 2 | 1,620 | -0.1425 | 0.0232 | -1.0000 | +1.5000 |
| 14 | wave1-c2 | 1 | 2 | 1,620 | -0.1570 | 0.0279 | -1.0000 | +1.5000 |
| 15 | wave1-c0a | 0 | 3 | 1,620 | -0.1619 | 0.0250 | -1.0000 | +1.5000 |

**Reading.** The current top completed panel is still c0a/seed2/rank1.
c0b contributes the second and third completed panels, while c1 now has
one completed panel in the top five. The top-panel view is therefore
more mixed than the complete-cell mean, which reinforces that the final
report should include both mean top-K and top-1 oracle views.

## 5. Active Partial Panels

**Method (section 1.2).** Partial panels are shown for monitoring only.

| cell | seed | source rank | n | coverage | observed mean | SE |
|---|---:|---:|---:|---:|---:|---:|
| wave1-c1 | 1 | 1 | 1,616 | 99.8% | +0.2440 | 0.0245 |
| wave1-c1 | 1 | 2 | 1,614 | 99.6% | -0.0245 | 0.0286 |
| wave1-c2 | 0 | 1 | 1,614 | 99.6% | -0.1264 | 0.0251 |
| wave1-c2 | 2 | 1 | 1,050 | 64.8% | -0.0648 | 0.0337 |

**Reading.** The near-complete c1/seed1/rank1 panel is currently strong,
but it remains a monitoring value until all opponents and replicates are
present. It should not be promoted into the complete-build ranking until
the panel reaches 1,620 rows.

### 5.1 c1 missing-row diagnosis

The incomplete c1 panels are not missing random coverage. The absent rows
are exactly the c1 stuck-matchup requeues:

| build_id | missing rows | missing matchup IDs |
|---|---:|---|
| `honest__wave1-c1__s0__seed1__rank1` | 4 | `medusa_Attack_rep3`, `medusa_Attack_rep16`, `medusa_CS_rep27`, `medusa_CS_rep28` |
| `honest__wave1-c1__s0__seed1__rank2` | 6 | `hammerhead_Overdriven_rep7`, `hammerhead_Overdriven_rep8`, `hammerhead_Tutorial_rep17`, `manticore_Balanced_rep0`, `shrike_Support_rep24`, `sunder_CS_rep29` |

The orchestrator log shows those ten matchups timed out at the
caller-level 900 s `result_timeout_seconds` between 20:26 and 20:39 EDT.
The Redis janitor later requeued the same processing-list payloads around
21:13-21:25 EDT with age approximately 3,660-3,715 s, and the requeued
workers posted clean `LOADOUT_OK` results shortly afterward. Those clean
results are visible in the log but absent from the append-only ledger.

**Root cause.** The Flask `/result` handler accepts and stores a clean
result even when no dispatcher thread is currently waiting on that
`matchup_id`. The honest-eval ledger write happens later in
`evaluate_builds`, after `pool.run_matchup()` returns. In this race, the
original `pool.run_matchup()` caller had already timed out and removed its
event, so the accepted late result was retained inside the live
`CloudWorkerPool` process but never returned to the ledger writer. This is
a pool retry race, not a c1-specific build or opponent problem.

**Fix status.** The code path has been patched after this snapshot so a
retry consumes a retained late result before enqueueing duplicate work,
and timeout cleanup returns a result if it arrived during the wait/cleanup
race. The currently running evaluator process was started before that
patch, so its final outcome still needs to be watched. If it exits with
incomplete panels, resume with the patched code and the ledger should
dispatch only the remaining missing matchups.

## 6. Existing-Report Gates

**Method (section 1.3).** Gates mirror the final report scaffold and the stall
checkpoint report.

- F1c C2 vs C0a complete-panel delta: -0.1196.
- F1c C2 vs C0b complete-panel delta: -0.1060.
- Random-baseline existence check: not estimable yet; random-baseline panels have not completed.
- Stall checkpoint regression: the prior loadout/result-envelope failure signatures remain absent. Stuck-matchup requeues are present and should be watched, but the run is still accepting results.

## 7. Current Orchestrator Health

**Method (section 1.3).** Counts are from the current orchestrator log only, not
from the pre-resume log.

| metric | value |
|---|---:|
| HTTP 200 result posts | 24,987 |
| LOADOUT_OK lines | 24,987 |
| HTTP 409 duplicate posts | 0 |
| requeue lines | 16 |
| LOADOUT_MISMATCH lines | 0 |
| matchup_id mismatch lines | 0 |
| ResultEnvelopeMismatch lines | 0 |
| corrupt result lines | 0 |
| ERROR lines | 0 |
| Traceback lines | 0 |
| literal WorkerTimeout exception lines | 0 |
| BudgetExceeded lines | 0 |
| preflight failed lines | 0 |
| observed HTTP 200 rate | 151.9 / min |

### 7.1 15-minute result bins

The first and last buckets may be partial wall-clock buckets.

| local time bucket | results | nominal rate/min |
|---|---:|---:|
| 19:30 | 1,070 | 71.3 |
| 19:45 | 2,479 | 165.3 |
| 20:00 | 2,269 | 151.3 |
| 20:15 | 2,530 | 168.7 |
| 20:30 | 2,235 | 149.0 |
| 20:45 | 3,024 | 201.6 |
| 21:00 | 2,086 | 139.1 |
| 21:15 | 2,005 | 133.7 |
| 21:30 | 2,031 | 135.4 |
| 21:45 | 2,233 | 148.9 |
| 22:00 | 2,013 | 134.2 |
| 22:15 | 1,012 | 67.5 |

**Reading.** Accepted result posts continue at production scale. The 16
requeue warnings occurred around 21:13-21:25 and 22:14-22:15 local time,
all for stuck matchups with `requeue_count=1`. Because no duplicate posts
or result-envelope mismatches appeared and the ledger kept advancing, this
does not match the previous hard-stall failure mode. It remains a final
audit item: after completion, verify that every requeued matchup has
exactly one accepted ledger result.

## 8. Synthesis & Decisions

The run should continue. The current evidence does not justify stopping
or restarting: resume keys are clean, loadout validation is clean, and
accepted results are still arriving. The only new operational watch item
is the small number of stuck-matchup requeues and the c1 late-result race
described in section 5.1.

The build-quality read remains preliminary. c0a leads the complete-cell
mean among fully complete cells, c0b has two of the top three complete
panels, c1 has a strong near-complete partial panel, and c2's complete
panels currently trail c0a/c0b. No decision should be made about c3 or
random-baseline until those panels exist.

The final honest-eval analysis should retain the same structure as the
existing report scaffold: mean top-K oracle by cell, top-1 oracle by cell,
F1c deltas for C2 versus c0a/c0b, random-baseline existence, and
stall-regression diagnostics. It should add an explicit requeue audit:
for each requeued `matchup_id`, confirm whether the ledger contains one
and only one completed row.

## 9. Open Questions / Next Steps

- Continue monitoring until all 87,480 matchups complete.
- Re-run the partial analyzer if progress slows or the requeue count grows.
- At completion, generate the normal per-cell `honest_eval.json` outputs
  and replace this draft with a full final honest-eval report.
- In the final audit, reconcile the 16 currently observed requeue warnings
  against ledger completion and duplicate-key accounting.
- If the live process completes with c1 still short, resume with the
  patched pool code rather than manually editing the ledger.
- If any future snapshot shows 409s, loadout mismatches, result-envelope
  mismatches, corrupt results, tracebacks, worker timeouts, budget errors,
  or preflight failures, treat it as a stall-regression incident and
  inspect worker logs before resuming further.

## Appendix - File Map

- Producer: `scripts/analysis/wave1_honest_eval_partial.py`
- Ledger: `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Current orchestrator log: `data/honest_eval/orchestrator-20260510T233515Z.log`
- Prior stall report: [2026-05-10-wave1-honest-eval-stall-checkpoint.md](2026-05-10-wave1-honest-eval-stall-checkpoint.md)
- Final-report scaffold producer: `scripts/analysis/wave1_honest_eval_report.py`
