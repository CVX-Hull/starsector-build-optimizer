---
type: report
status: shipped
last-validated: 2026-07-12
---

# Phase 7 — Learned-batch tail-walltime analysis (attempt-3 fleet)

## Abstract

The attempt-3 canonical re-run (36 × c6a.4xlarge spot, 183 jobs, $75.50)
kept the whole fleet up until the slowest worker drained: the fleet window
was 7.65 h but median per-worker busy time was 6.42 h, so the last 1.72 h
(22.5 % of walltime) was a drain phase in which idle workers accumulated
38.3 idle worker-hours ≈ **$10.51 (13.9 % of spend)** — recoverable by
scale-down-on-drain (terminate each worker when the queue is empty and its
last job finishes). The drain tail is caused entirely by
`random_forest_tuned` jobs (61.4 % of all compute; every one of the ten
longest jobs), whose walltime on opponent-side splits varies 4× (50–206
min). Duration-aware dispatch (longest-first) would additionally cut
makespan from 7.65 h to an estimated 6.77 h (−11.5 % walltime, near the
6.59 h lower bound). This analysis also corrects the attempt-3 report's
completion time (20:10 UTC, not ≈ 16:30) and closes the roadmap item
"measure learned-batch tail-job walltime at scale". It does not cover
surrogate quality (owned by the attempt-3 results report), honest-eval
fleet economics, or spot-price variability across regions/instance types.

## 1. Methods

### 1.1 Data

Unit of analysis: one batch job (a `split × model × seed` experiment cell)
from the attempt-3 output directory
`data/phase7/learned_surrogate_batch_v2_2026-07/` (gitignored, local).
N = 183 jobs across 36 workers (2–8 jobs per worker). Three sources:

- **`ledger.jsonl`** — 15,840 cost heartbeats (one per worker per ≈ 63 s),
  each carrying `timestamp`, `worker_id`, `delta_usd`, `cumulative_usd`.
  Defines the fleet window (first heartbeat 12:31:09 UTC, last 20:10:16
  UTC, all 36 workers spanning the full window) and the realized spend
  ($75.4957, matching `status.json`).
- **`events/<job_id>.jsonl`** — job lifecycle events
  (`lease_acquired` / `experiment_start` / `experiment_completed` /
  `result_uploaded`) with the worker's `instance_id`. These events carry
  **no timestamps** (fixed for future batches in this change — the control
  plane now stamps `received_at_utc` on receipt), so job placement in time
  must be estimated (§1.2).
- **`results/<job_id>.json`** — per-job artifact with `elapsed_seconds`
  (the experiment subprocess walltime measured on the worker).

### 1.2 Estimators

All computed by `scripts/analysis/phase7_tail_walltime.py`; exact values in
`data/phase7-tail-walltime/headline_numbers.json`.

- **Per-worker busy hours** `busy_i` = Σ `elapsed_seconds`/3600 over the
  worker's jobs (order-independent).
- **Fleet window** W = last − first ledger heartbeat = 7.652 h; **capacity**
  = 36 · W = 275.5 worker-hours; **utilization** = Σ busy / capacity.
- **Setup + overhead bound** s = W − max(busy_i) = 0.058 h (3.5 min). The
  makespan worker has no tail idle, so its residual bounds bundle download +
  `uv sync` + all per-job lease/upload overheads from above (≤ ~30 s/job at
  7 jobs). This near-zero residual is also the evidence that workers do not
  idle mid-run: the queue stays non-empty until the final drain, so
  **finish_i ≈ fleet start + s + busy_i** (uncertainty ± the per-job
  overheads, minutes at most).
- **Idle-tail worker-hours** = capacity − Σ busy − 36 · s (upper bound
  without the setup correction: capacity − Σ busy). **Idle-tail cost** =
  idle hours × effective rate, where the effective rate = realized spend /
  capacity = $0.2741 per worker-hour.

### 1.3 Statistical-learning setup

Not applicable — this report contains no model fitting; it is a
deterministic reconstruction from run logs.

### 1.4 Comparison statistics

- **LPT counterfactual makespan**: greedy longest-processing-time-first
  assignment of the 183 observed durations onto k workers (k ∈ {18, 24, 36,
  48}), plus the setup bound s. LPT is a standard 4/3-approximation for
  makespan scheduling; the reported values are achievable estimates given
  known-in-advance durations (per-cell durations are predictable from
  family × split, §2.2).
- **Makespan lower bound** (k = 36): max(Σ busy / 36, longest job) + s =
  6.59 h. No bootstrap or hypothesis tests; every number is a complete
  enumeration of the run's jobs, not a sample.

### 1.5 Diagnostics & thresholds

No predeclared numeric gate exists for this measurement (the roadmap item
asks for the measurement itself, to gate the fleet-teardown /
scale-down-on-drain design decision). The implicit decision rule applied in
§3: implement scale-down-on-drain if the measured idle-tail share of spend
is material (≳ 5 %) at the fleet scale we actually run.

## 2. Results

### 2.1 Fleet window, utilization, and the idle tail

**Method (§1.2).** Busy hours from per-job `elapsed_seconds`; window and
spend from the ledger; finish times estimated as start + s + busy.

| Quantity (n = 183 jobs, 36 workers)     |            Value |
|---|---:|
| fleet window (12:31:09 → 20:10:16 UTC)  |           7.65 h |
| capacity                                | 275.5 worker-h   |
| busy                                    | 235.0 worker-h   |
| utilization                             |           85.3 % |
| setup + overhead bound s                |          3.5 min |
| per-worker busy (min / median / max)    | 5.88 / 6.42 / 7.59 h |
| drain-phase length (first finish → teardown) | 1.72 h (22.5 % of walltime) |
| idle-tail worker-hours                  |           38.3 h |
| **idle-tail cost** (at $0.2741/worker-h) | **$10.51 (13.9 % of $75.50)** |

![Step chart of estimated busy workers over time: all 36 workers stay busy
for the first 5.9 hours, then the count falls in steps to zero at 7.65
hours; the shaded region between the curve and the 36-worker line is the
idle tail](../../data/phase7-tail-walltime/charts/01_drain_curve.png)

*Figure 1 — Estimated count of busy workers vs hours since fleet launch.
The shaded area (38.3 worker-hours) is capacity paid for but idle during
the drain; the dashed line marks fleet teardown at 7.65 h.*

**Reading.** Utilization is high (85.3 %) because the queue keeps every
worker busy until it empties — the only waste is the drain tail, and
teardown itself was prompt (the makespan worker's residual is 3.5 min).
But at 36 workers the drain costs a seventh of the budget: workers that
finish at 5.9 h idle for up to 1.7 h waiting for the slowest worker.
Scale-down-on-drain — each worker self-reports queue-empty and is
terminated after its last upload — recovers essentially all of the $10.51
with no walltime cost.

### 2.2 Job-walltime distribution — the tail is all tuned random forest

**Method (§1.1).** Per-job `elapsed_seconds`, grouped by split × model
family; complete enumeration.

| Job walltime (n = 183)  |  minutes |
|---|---:|
| p50                     |     50.7 |
| p90                     |    149.4 |
| p99                     |    200.4 |
| max                     |    205.6 |

| Busy-hours share by family (n = 61 jobs each) |    hours |   share |
|---|---:|---:|
| `random_forest_tuned`                         |   144.2 h |  61.4 % |
| `catboost_regressor`                          |    48.6 h |  20.7 % |
| `sparse_pairwise_ridge`                       |    42.2 h |  18.0 % |

![Horizontal box plots of job walltime for each split × model cell:
CatBoost cells cluster tightly near 40–54 minutes, ridge cells near 16–90
minutes, while tuned random-forest cells sit at 80–206 minutes with wide
boxes on the opponent-side splits](../../data/phase7-tail-walltime/charts/02_job_walltime_by_cell.png)

*Figure 2 — Job walltime (minutes) by split × model family, n per cell in
the axis labels. The long tail is exclusively `random_forest_tuned`; its
opponent-side splits are also the highest-variance cells.*

**Reading.** All ten longest jobs are `random_forest_tuned`, nine of them
on opponent-side splits (walltime 161–206 min vs a 147 min median on the
build split). CatBoost is strikingly uniform (40–54 min in every cell) —
tuned RF costs ~3× CatBoost per cell and supplies the entire scheduling
tail. Cell durations are predictable from family × split alone, which is
what makes duration-aware dispatch (§2.3) practical: enqueue the RF
opponent-split cells first.

### 2.3 Counterfactual scheduling and fleet sizing

**Statistic (§1.4).** LPT makespan on the observed durations + setup
bound; cost with scale-down = (Σ busy + k·s) × effective rate.

| Fleet size k (183 jobs)     | LPT makespan | est. cost w/ scale-down |
|---|---:|---:|
| 18                          |      13.32 h |                  $64.7 |
| 24                          |       9.99 h |                  $64.8 |
| **36 (actual fleet)**       |   **6.77 h** |              **$65.0** |
| 48                          |       5.17 h |                  $65.2 |
| — actual run (36, FIFO, no scale-down) | 7.65 h |          $75.50 |
| — lower bound (36)          |       6.59 h |                     — |

**Reading.** Two independent savings: (1) scale-down-on-drain cuts cost
from $75.50 to ≈ $65 at any fleet size — cost becomes ≈ Σ busy × rate,
nearly constant in k, confirming that workers buy walltime, not spend;
(2) longest-first dispatch cuts 36-worker makespan 7.65 → 6.77 h (−11.5 %),
within 3 % of the 6.59 h lower bound, because the 3.4 h longest job no
longer starts mid-run. The counterfactual walltimes assume durations known
at dispatch; the family × split predictability in §2.2 makes a static
longest-first queue order a good-enough proxy.

## 3. Synthesis & decisions

1. **Implement scale-down-on-drain before the next batch wave** (roadmap
   item 2's feature-profile ablations). Measured recovery: $10.51 / 13.9 %
   on attempt 3; the mechanism (drain tail ∝ per-worker job granularity ×
   fleet size) applies to every future batch at this scale. Clears the
   §1.5 materiality rule by ~3×. Added to the roadmap AWS section as an
   open action item.
2. **Order the dispatch queue longest-expected-first** when enqueuing
   (static family × split duration ranking; no runtime estimation needed).
   Optional but nearly free at enqueue time: −0.88 h walltime per run at
   36 workers. Folded into the same roadmap action item.
3. **Attempt-3 report corrected.** Its appendix said "completed ≈ 16:30
   UTC"; the ledger shows the last job finished ≈ 20:07 and heartbeats end
   20:10:16 UTC. Corrected in place with a dated note.
4. **Batch events now carry timestamps.** The event logs' missing
   timestamps forced the finish-time estimation in §1.2; the control plane
   now stamps `received_at_utc` on every job/worker event (implemented in
   this change with a test), so future tail analyses read exact timelines.
5. **Roadmap item closed**: "Measure learned-batch tail-job walltime at
   scale" — closed by this analysis; no new spend was needed.

## 4. Open questions / next steps

- Whether spot-price variance across launches changes the effective rate
  enough to alter the scale-down payoff (this run priced uniformly at
  ≈ $0.274/worker-h; the ledger's per-beat `delta_usd` was constant).
- Whether HPO-budget rebalancing for `random_forest_tuned` (fewer trials,
  or early-stopped trials) is worth it purely on compute grounds — RF is
  61 % of batch compute while losing to CatBoost on build-like splits
  ([seed-151 confirmatory](2026-07-12-phase7-seed151-confirmatory.md));
  the item-2 ablation wave should revisit which families still earn a
  61 %-of-fleet tuning budget.

## Appendix — file map

- Producer script: `scripts/analysis/phase7_tail_walltime.py`
  (`uv run python scripts/analysis/phase7_tail_walltime.py`; deterministic,
  no RNG).
- Raw data: `data/phase7/learned_surrogate_batch_v2_2026-07/`
  (gitignored, local — ledger, events, results).
- Charts + headline numbers (tracked in git):
  `data/phase7-tail-walltime/charts/01_drain_curve.png`,
  `data/phase7-tail-walltime/charts/02_job_walltime_by_cell.png`,
  `data/phase7-tail-walltime/headline_numbers.json`.
- Dependent reports:
  [attempt-3 surrogate results](2026-07-12-phase7-attempt3-surrogate-results.md)
  (the run this analysis instruments; completion time corrected there),
  [AWS cost analysis](2026-07-11-aws-cost-analysis.md) (defined the
  measurement item).
