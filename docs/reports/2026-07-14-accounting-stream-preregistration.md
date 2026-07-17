---
type: report
status: shipped
last-validated: 2026-07-17
---

# Accounting-run stream — pre-registration & analysis ledger

**Status: pre-registration, now shipped with its first consultation (entry 3,
2026-07-17).** The predeclaration (entries 0–2) was authored and committed
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
data/phase7/accounting_matchups.sqlite         # frozen matchup DB (materialized in Scope B)
data/phase7/accounting_oracle_builds.json      # rank-stratified 27 (entry 2 selector; the build_id↔build_key bridge for the replay join)
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
- **2026-07-16 — entry 2 (deterministic selection rule)**: entry 1 fixed the
  *count* (3 tertiles × 1/stratum × 9 cells = 27); this entry fixes the exact
  *mechanism*, on methodological grounds and **without consulting the fitted
  predicted-score distribution or any oracle reading** (the stream is collected
  and the frozen matchup DB `data/phase7/accounting_matchups.sqlite` exists, but
  no predicted scores were inspected in choosing this rule). Implemented by
  `scripts/analysis/phase7_select_oracle_builds.py`
  (plan [2026-07-16-oracle-coverage-selection.md](../../.claude/plans/archive/2026/2026-07-16-oracle-coverage-selection.md)),
  committed together with this entry before first execution; the selector records
  this doc's git commit hash (`prereg_commit`) in its output JSON so
  "fixed-before-selection" is verifiable.
  - **Ranking arm**: `catboost_regressor` (opponent-adjusted) —
    `learned.DEFAULT_HYPERPARAMETERS["catboost_regressor"]`,
    `learned.DEFAULT_HPO_SEED = 23`, `thread_count = 1`. Per hammerhead cell an
    **in-sample** model is fit on **all** of that cell's `training_matchups` rows
    (finalized + pruned; no row-kind filter — matches the replay arm
    `_fit_predict_scores`).
  - **Selection population**: the **distinct `build_key`** values within each
    cell (a build re-proposed across trials is one unit). Per-build predicted
    score = **mean predicted target over that build's matchup rows** in the cell.
  - **Strata (sole rule)**: sort distinct builds by `(predicted_score,
    build_key)` ascending; `numpy.array_split` the rank-ordered list into 3
    near-equal **contiguous** groups = bottom / middle / top third **by predicted
    rank**. No score-value quantiles.
  - **Intra-stratum pick**: the build at the **median predicted score of its
    stratum** (lower-middle index on even counts) in that stratum's
    `(predicted_score, build_key)`-sorted order. Deterministic, no RNG.
  - **Provenance / join**: honest-eval `source_rank` = stratum ordinal
    (bottom=1, middle=2, top=3); `(source_campaign="accounting-hammerhead",
    source_study_idx=0, source_seed_idx=seed, source_rank)` is unique across the
    27. Because ordinary stream trials carry `recovered_builds.rank = None`, the
    oracle `build_id → build_key` join is resolved **from the selector JSON**
    (which carries both), via the materializer's `--honest-selector-json`, not
    the native `honest_build_id_to_key`.
  - **Degenerate cells**: a cell with `< 3` distinct builds is a data defect →
    the selector fails loud (`ValueError`), not silently degrades. (Each
    hammerhead cell has hundreds of distinct builds.)
  No oracle reading exists yet — this entry precedes the oracle pass.
- **2026-07-17 — entry 3 (Tier-2 reading — first consultation of the oracle'd
  stream)**: the oracle pass (27 builds × 54 opponents × 30 replicates, all at
  full 1,620/1,620 coverage, zero failures) completed 2026-07-17; the frozen DB
  was re-materialized with `--honest-ledger` + `--honest-selector-json`
  (`honest_eval_matchups` 43,740 rows, 0 unresolved; `training_matchups`
  byte-identical to the pre-oracle materialization) and the prequential replay
  run under the predeclared statistic. Full reading:
  [2026-07-17-phase7-oracle-value-replay.md](2026-07-17-phase7-oracle-value-replay.md);
  accounting: [2026-07-17-accounting-matchup-spread.md](2026-07-17-accounting-matchup-spread.md).
  **Verdict: the Tier-2 coverage did not certify the surrogate — it confirmed
  the shipped "gating value not established" against an independent oracle.**
  The CatBoost selection arm's predicted-score-vs-oracle Spearman is +0.34
  (p = 0.08, n = 27, carried by the coarse pruned-bottom-vs-finalized-top
  separation) but ≈ 0 (+0.01, n = 13) among rankable/deployable builds;
  gating median q\* = 0.3 = the build-blind null; T2 opponent-adjusted drift
  reproduces (CatBoost the only positive arm near-horizon, collapsing beyond
  ~20 trials). The one positive oracle signal is the **TWFE α̂ estimator arm**
  (campaign Spearman +0.50–0.58, n = 13, marginal CIs) — validating the
  gating/honest-eval **target**, not the surrogate. **Pre-registration fidelity
  note**: the entry-0 phrase "oracle-value regret@k under the CatBoost arm" does
  not map verbatim to a shipped-tool output; the reading realizes it three
  faithful ways (literal CatBoost-vs-oracle Spearman, CatBoost gating Δ-oracle,
  tool-native estimator-arm recovery), all agreeing in direction, and the
  mapping was fixed by the spec-31 contract (predating the stream), not chosen
  after readings existed. A spec-31 amendment naming an explicit gating-arm
  oracle-regret statistic is filed as a follow-up. This entry is the first
  post-collection consultation; later new-family re-fits append as further
  entries.
