---
type: report
status: draft
last-validated: 2026-07-15
---

# Accounting-run stream — pre-registration & analysis ledger

**Status: pre-registration (draft).** This document is authored and committed
**before** the instrumented accounting run's proposal stream is collected. It
fixes the complete gate statistic, the oracle-coverage subset rule, the fresh
seeds, and the retained artifacts, so that neither the gate statistic nor the
oracle coverage can be chosen after readings exist (the roadmap item-3
stream-reuse discipline, `docs/roadmap.md:89-108`). No model-selection re-run
consults this stream before this predeclaration is committed. Later
consultations (new-family re-runs, item-6/7 promotion readings) are appended as
dated ledger entries below.

Owning plan: `.claude/plans/archive/2026/2026-07-14-instrumented-accounting-run.md`
(Scope A). Replay contract: spec 31 §"Prequential Replay Ablation". Prior
stream: [2026-07-14 wave-1 prequential replay](2026-07-14-phase7-prequential-replay.md).

## Stream identity

- **Cells**: nine hammerhead/early cells (`examples/accounting-hammerhead.yaml`,
  seeds **100–108**) + one-to-three wolf/early cells
  (`examples/accounting-wolf.yaml`, seeds **120–122**). 151 is spent; these are
  fresh, reserved here, not reused from any prior run.
- **Gate scope**: gate adequacy is defined **only** over the hammerhead subset.
  Wolf cells serve the accounting purpose and provide directional replay
  readings at most; cross-hull claims stay gated behind the item-4 wave.
- **Comparability**: this stream's eval logs feed the frozen matchup DB under
  the **same loader-skip / exclusion semantics** as the shipped wave-1 stream —
  instance-error trials appear on neither side of the replay join (spec 24 "the
  fifth path"). This is required for any Tier-1 comparison to the shipped run.

## Predeclared gate statistic

### Tier 1 — accounting + directional replay (no oracle coverage)

Mirrors the shipped wave-1 run so the two streams are comparable:

- **Statistic**: T1/T2 opponent-adjusted fidelity + zero-regret **median q\***
  with the `opponent_mean` build-blind null alongside.
- **Arm**: CatBoost opponent-adjusted (the only arm with positive T2 on the
  shipped stream).
- **Cutoffs**: spec-31 defaults — `min_train_trials`, stride `cutoff_stride`,
  `min_future_trials`.
- **Aggregation**: median over the hammerhead cells. Wolf = directional only.
- **Adequacy**: directional at this cell count (the shipped 15-cell stream could
  not discriminate the surrogate from the null under this statistic). Tier-1
  readings do **not** certify the gate.

### Tier 2 — gate-adequate replay (only if oracle coverage is funded)

- **Statistic**: continuous **oracle-value regret@k** under the CatBoost
  opponent-adjusted arm, aggregated as the median over the hammerhead cells.
- **Oracle-coverage subset-selection rule** (fixed here, before any reading): a
  **rank-stratified** sample of stream builds under the CatBoost
  opponent-adjusted arm — strata by predicted-rank quantile, a fixed number of
  builds drawn per stratum per cell. The exact per-stratum count is ratified
  with the spend at the plan gate and recorded as a ledger entry at that time;
  it is NOT chosen after readings exist.

The tier and the `budget_usd` caps are ratified by the user at the plan gate
(the D4 pattern). This plan does not launch.

## Retained artifacts (through item 7)

Items 6/7 re-fit the instrument offline on these; they must NOT be reaped
mid-program. The AWS cleanup tooling (`final_audit.sh`, `cleanup_amis.sh`,
`teardown.sh`) reaps only AWS resources — never `data/` — so the residual risk
is a manual `data/` cleanup or disk loss. Retention is over these paths (all
under the gitignored `data/`, listed here as the committed manifest):

```text
data/logs/accounting-hammerhead/*/evaluation_log.jsonl
data/logs/accounting-wolf/*/evaluation_log.jsonl
data/study_dbs/accounting-hammerhead/*.db
data/study_dbs/accounting-wolf/*.db
data/phase7/accounting_matchups.sqlite        # frozen matchup DB (materialized in Scope B)
```

## Analysis ledger

_(Append one dated entry per consultation of this stream — replay runs,
new-family re-fits, item-6/7 promotion readings. The predeclaration above is
entry 0.)_

- **2026-07-14 — entry 0 (predeclaration)**: statistics + seeds + subset rule +
  retained paths fixed before collection. No stream data exists yet.
- **2026-07-15 — entry 1 (spend-gate ratification — Tier 2, Package B)**: the
  user ratified **Tier 2** at the plan gate and set the `budget_usd` caps
  (`accounting-hammerhead.yaml` = $72, `accounting-wolf.yaml` = $19; conservative
  Tier-1 sim caps, directional pending V2 re-validation). The Tier-2 oracle
  coverage is funded at **K = 3 builds/cell**, realized under the rank-stratified
  rule of entry 0 as **3 predicted-rank strata × 1 build per stratum per cell**:
  strata are the tertiles of the CatBoost opponent-adjusted predicted rank within
  each hammerhead cell (bottom / middle / top third by predicted rank); exactly
  one build is drawn per stratum per cell, giving 3 oracle'd builds per cell ×
  9 hammerhead cells = **27 oracle-covered builds**. This per-stratum count is
  fixed here, before the stream is collected; the honest-eval oracle `budget_usd`
  is set at launch alongside the re-bake. No stream data exists yet — this entry
  precedes collection. Wolf cells receive **no** oracle coverage (accounting +
  directional replay only), unchanged from entry 0.
