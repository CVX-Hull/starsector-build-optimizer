---
plan_type: implementation
status: implemented
created: 2026-07-13
approved: 2026-07-13
implemented: 2026-07-14
owner: agent
related_docs:
  - docs/roadmap.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/specs/28-deconfounding.md
  - docs/reports/2026-07-11-phase7-methodology-review.md
  - docs/reports/2026-07-12-phase7-adversarial-auc-evidence.md
  - docs/reports/2026-07-13-roadmap-regroom.md
  - docs/reports/2026-05-11-wave1-honest-eval-final.md
implementation_commit: 7a0b64f (audit hardening 37cfb94; evidence + report in the retirement commit)
post_impl_audit: passed (see §Post-implementation audit record)
superseded_by: null
---

# Prequential replay ablation (roadmap item 2)

## Goal

Build and run the decision-relevant optimizer-integration gate for the
Phase 7 surrogate (methodology review M3 remedy): replay the wave-1
proposal stream in arrival order; at each cutoff train surrogate arms on
past matchup rows only and score upcoming proposal blocks; report
(a) rank fidelity of predictions on future proposals, **drift-aware**
(as a function of temporal distance, per the forward-time AUC 0.820
finding), (b) **budget savings at fixed top-k regret** under a
skip-bottom-q gating policy, and (c) the folded **Phase 5A estimator
arms** (re-groom D2) — five arms **A0/A1/A2/EB/A3** — as offline
estimator variants over the same stream. Two pinned deviations from
D2's "same incumbent definition" wording, surfaced here and again at
fold discharge (roadmap closure + report): the arms operate on the
`hp_differential` target rather than the incumbent's `combat_fitness`
(not exactly recoverable from logs — see data fact 3), and the shipped
covariate-EB path is added as its own arm (it *is* the deployed
incumbent estimator, so the fold is incomplete without it). Local, zero
sim spend. Output: one deterministic JSON artifact + one empirical
report. This replay is one of the two predeclared inputs to the Phase 7
BoTorch go/no-go (the other is the offline MCBO bake-off, out of scope
here).

## Context and source docs

- Mandate: `docs/roadmap.md` Active item 2; fold rationale
  [re-groom D2](../../../docs/reports/2026-07-13-roadmap-regroom.md).
- M3 definition: [methodology review §4](../../../docs/reports/2026-07-11-phase7-methodology-review.md)
  — "train on rows &lt; t, score the next proposal batch, measure rank
  fidelity and budget savings if the bottom-q surrogate-ranked proposals
  were skipped."
- Drift-aware requirement: [adversarial-AUC evidence §Open questions](../../../docs/reports/2026-07-12-phase7-adversarial-auc-evidence.md).
- Estimator-arm provenance: Phase 5A stage list
  (`docs/reference/implementation-roadmap.md` §Phase 5A), spec 28
  (correction form + shipped EB path),
  `docs/reference/phase5-signal-quality.md` (scalar-CV estimator form),
  honest-eval final (c0a–c3 verdict).
- Contracts: spec 31 (artifact/claim machinery); spec 30 /
  `posthoc_ranker.py` (offline ranking precedent on `hp_differential`,
  incl. cross-study pooling docstring precedent).

### Data facts this design rests on (verified 2026-07-13 in-session; to be re-owned by the spec amendment/report Methods)

1. The frozen DB `data/phase7/wave1_matchups.sqlite` has **no timestamp**;
   its PK order `(source_path, trial_number, opponent_index)` is Optuna
   *suggestion* order. True arrival order lives in the 15 eval logs
   (`data/logs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl`),
   whose `timestamp` field is populated on every row and is
   **non-monotonic in `trial_number`** (parallel workers).
2. `training_matchups` (21,362 rows, 2,374 trials, 15 replay cells)
   carries `target = hp_differential` per matchup,
   `row_kind ∈ {finalized, pruned}` (3,922 pruned rows present).
   `recovered_builds` maps `build_key → build_json` 1:1 for all 2,374
   trial builds. Finalized trials realized 10 opponents; pruned trials
   4–9.
3. The live incumbent recorded `combat_fitness(result)` into the score
   matrix (`optimizer.py:839-840`), which is **not exactly recoverable**
   from the logs (needs damage totals for the no-engagement branch; win
   rows only reconstruct under a total-destruction assumption). The
   logged per-trial `twfe_fitness`/`eb_fitness` ARE the as-deployed
   incumbent trace and are kept as such.
4. **Oracle panel structure**: `honest_eval_matchups` (same DB) holds
   54 builds × 54 opponents × 30 replicates. The 54 builds are
   5 wave campaigns × 3 seeds × **top-3 per seed-study** (45,
   incumbent-selected) + 9 `random-baseline` builds (outside the
   replay). A replay cell therefore owns **≤ 3** oracle'd builds.
5. Trial numbers are gappy per cell (c2 starts at 3, c3 at 53); iterate
   observed trials, never assume contiguity.
6. Monotone transforms are rank-vacuous offline: Box-Cox (5E) and the A3
   rank shape reorder nothing; A3's only measurable offline effect is its
   **top-quartile ceiling** (erases within-top-quartile order → ties).
7. **`opponent_order` logs the full planned 10-opponent panel for every
   trial, including all 630 pruned trials** (verified across all 15
   logs) — the planned panel is decision-time information.
8. `covariate_vector`/`engine_stats` are logged **only on finalized
   trials** (finalize-time emission); engine-truth covariates cannot be
   recomputed in Python (design invariant: Java emission is the source
   of hullmod-adjusted stats).

## Terminology (pinned)

- **Replay cell** = (campaign, seed); 15 cells; the replay unit. Streams
  are never pooled across replay cells.
- **Oracle panel** = the 54-build honest-eval panel (data fact 4);
  campaign-level, spans 3 seed-studies per campaign.
- **Rankable builds** = finalized trials of a replay cell (the only
  builds the incumbent could select and the only ones with logged
  covariates). Arms **fit on all rows (finalized + pruned)** — matching
  live `ScoreMatrix` behavior — but **rank rankable builds only**; this
  equalizes ranking support across all five arms.

## Scope

Two halves sharing one replay stream, one script, one artifact.

### Half 1 — surrogate prequential replay (M3)

- **Stream order**: within a replay cell, trials sorted by eval-log
  `(timestamp, trial_number)` (unique, no ties). Rows trained on are the
  DB's `training_matchups` rows joined by `(source_path, trial_number)`;
  join failure in either direction is a hard `ValueError`
  (data-integrity signal convention). Pruned trials are included in
  training (real observations the deployed trainer would have).
- **Cutoff grid**: cutoffs at trial index `min_train_trials` (default 40)
  then every `cutoff_stride` (default 10) while ≥ `min_future_trials`
  (default 10) remain. All in a frozen `ReplayConfig`.
- **In-flight optimism control**: `train_gap_trials` (default 0)
  excludes the G most recent pre-cutoff trials from training; the sweep
  runs G=0 (optimistic) and G=Ĝ where Ĝ = per-cell median in-flight
  count measured from the study DBs'
  (`datetime_start`, `datetime_complete`) overlaps
  (`data/study_dbs/wave1-*/...db`), recorded in the artifact.
- **Prediction target (decision-time only)**: a trial's predicted score
  = mean predicted target over its **planned `opponent_order` panel**
  (10 opponents, logged pre-outcome for every trial — data fact 7;
  features are pure functions of (build, opponent), so this is free and
  deployable). Predicted scores never use realized row sets.
- **Surrogate arms** (per cutoff, fit on past rows only): the two
  canonical learned families at `DEFAULT_HYPERPARAMETERS`
  (`catboost_regressor`, `random_forest_tuned`; **no per-cutoff HPO** —
  predeclared: nested HPO per cutoff is cost-prohibitive and
  winner's-curse-prone at these inner sizes, cf. M1/C3), plus the six
  comparator-gate families via `run_inline_comparators` (the
  `opponent_mean` build-blind baseline is the mandatory null, per C1).
  Feature profile `all`, feature schema v4.
- **Drift-aware fidelity**: future trials bucketed by temporal distance
  in trials ahead of the cutoff: `horizon_buckets` default
  `((0,10), (10,20), (20,40))` plus an unbounded tail bucket. Per
  (replay cell, cutoff, bucket, arm), two predeclared fidelity targets:
  - **T1 — panel-matched raw fidelity** (deployment-faithful):
    Spearman + Kendall between predicted score (planned panel) and
    realized raw mean, **restricted to finalized trials** (realized
    panel = planned panel, always 10 rows → no pruner-composition
    leak, comparable means).
  - **T2 — opponent-adjusted fidelity**: Spearman + Kendall between
    predicted score and the trial build's **full-data A1 α̂** (rankable
    builds; opponent effects removed — the C1 control).
  - **matchup-level** (secondary): per-opponent rank metrics via
    `evaluation_metric_suite` on the adjacent bucket only,
    `include_bootstrap=False` at per-cutoff level (cost); noise-floor
    resolution via honest-eval replicates stays within the spec 31
    diagnostic carve-out.
  - Aggregation: pool per-(cell, cutoff) values, report mean per bucket
    with a **campaign-stratified cluster bootstrap over cells**
    (resampling unit = replay cell, stratified by campaign, n=15,
    predeclared iteration count + seed in config; labeled descriptive —
    15 clusters is at the edge of percentile coverage). Per-bucket
    support counts (cells × cutoffs contributing) are reported; bucket
    means are not compared without their support (later cutoffs cannot
    populate deep buckets).
- **Gating policy simulation** (budget savings): walking cutoffs in
  order, rank the next block (`[t, t+cutoff_stride)`) by predicted
  score; skip the bottom `q` fraction, `q ∈ gating_fractions` (default
  `(0.1, 0.2, 0.3, 0.5)`). **Skipped trials' rows are removed from all
  later training sets** (faithful counterfactual training data); a
  keep-skipped-rows sensitivity runs at the single predeclared
  `gating_sensitivity_fraction` (default 0.3). Accumulate per cell:
  - **savings**: matchup rows not run = Σ over skipped trials of their
    realized row counts, on the same denominator as the incumbent
    reference;
  - **incumbent pruner reference**: rows *not run* by the pruner =
    Σ(`opponents_total` − `opponents_evaluated`) over pruned trials
    (NOT pruned-row share), at zero additional skips;
  - **regret**: which of the cell's final top-k rankable builds
    (k ∈ {1, 3, 9}) were skipped, under the **A1 gating target**
    (primary) with A0/EB sensitivity; plus Δ oracle value where a
    skipped build is one of the cell's ≤ 3 oracle'd builds.
  - **Predeclared headline statistic** (single, no forking):
    `catboost_regressor`, G=Ĝ, per-cell q\* = max q ∈ gating_fractions
    with zero realized top-3 regret under the A1 target, aggregated as
    the **median over the 15 replay cells**, with the full per-cell
    distribution shown and the `opponent_mean` null reported alongside.
    Every other (arm, G, q, k, target) combination is sensitivity,
    labeled as such.
  - Predeclared caveat (spec + report): the replay measures filtering
    fidelity on the logged stream; it cannot measure the counterfactual
    TPE trajectory had proposals actually been skipped.

### Half 2 — folded 5A estimator arms (re-groom D2)

All arms are per-replay-cell functions of the same matchup matrix
(build × opponent, values = `hp_differential`), computed at every cutoff
and at full data; fit on all rows, rank rankable builds only
(§Terminology). **Target-scale decision (pinned)**: arms operate on
`hp_differential`, NOT reconstructed `combat_fitness` — precedent: spec
30 / `posthoc_ranker.py`; exact data with zero reconstruction error;
consistent with the surrogate target. Consequence: replayed arm values
are not numerically comparable to the logged `twfe_fitness`
(combat-fitness scale); the logged trace is reported as the as-deployed
incumbent, not re-derived. The A0↔A1 boundary is pinned explicitly:
**A0 = untrimmed (`trim_worst=0`)**, **A1 = trimmed (`trim_worst=2`,
the live default)** — the retired experiment's exact boundary is
unrecoverable (deleted dir), so this plan's definition is normative
going forward and the spec amendment states it.

| Arm | Definition | Implementation |
|---|---|---|
| A0 | TWFE α̂, untrimmed | `twfe_decompose` + `trimmed_alpha(..., trim_worst=0)` |
| A1 | + trimmed mean (worst 2) | `trimmed_alpha(..., trim_worst=2)` (live default) |
| A2 | + scalar control variate on `composite_score` | pinned below |
| EB | A1 + covariate EB shrinkage (+ triple-goal) | `eb_shrinkage` with the logged 10-dim `covariate_vector` (finalized-only — data fact 8), current `EBShrinkageConfig` **field values**; the arm calls the pure function directly — the `enabled` deployment guard is not consulted |
| A3 | A2 + top-quartile ceiling rank shape | ranking ≡ A2 except ceiling ties; measured only via top-k selection under seeded random tie-breaking (mean over `tie_break_draws` draws) |

**A2 pinned definition** (the shipped code was deleted in 5D; spec 28
carries only the correction form, the estimator form is in
`phase5-signal-quality.md` — this plan's definition is normative, same
treatment as A0/A1): `α̂_A2,i = α̂_A1,i − β̂_cv·(h_i − h̄)` over rankable
builds, where `h_i` = `composite_score` recomputed from `build_json`
via the **current** manifest-driven heuristic scorer,
`β̂_cv = Cov(α̂_A1, h)/Var(h)` (OLS slope, both over rankable builds),
with `β̂_cv = 0` when `Var(h) ≤ cv_variance_floor` (designed constant in
`ReplayConfig`). Caveat carried to the report: today's scorer is not
the Phase-5A-era scorer (and Phase-7-prep declared `composite_score`
inadmissible as a live covariate), so A2 is a **reconstruction, not a
replication**.

**EB inputs**: per-build `sigma_sq = σ̂_ε²/n_i` uses spec 28's pooled
residual MSE (`n_params = n_builds + n_opps − 1`). That formula
currently exists in three copies (`deconfounding.ScoreMatrix`, twice in
`posthoc_ranker.py`); this plan **extracts one shared helper in
`deconfounding.py` and retrofits `posthoc_ranker.py`** (boy-scout
dedup, engineering principle 1) rather than adding a fourth copy.

Evaluations (all predeclared):
1. **Oracle recovery** (full data; direction check only — explicitly
   stated as unable to discriminate arms at this n):
   - primary: **within-replay-cell pairwise concordance** — each cell's
     ≤ 3 oracle'd builds give ≤ 3 within-cell pairs (≈ 45 pairs across
     15 cells); fraction of pairs where the arm's order matches the
     oracle-mean order, per arm, with a binomial CI (clustered by cell).
   - secondary: campaign-level Spearman over 9 builds under the pinned
     cross-study alignment **`μ̂ + α̂` (predicted mean vs the common
     opponent pool)** — the `posthoc_ranker` pooling precedent; n=9,
     bootstrap CIs, caveats verbatim (incumbent-selected builds,
     cross-study alignment assumption).
2. **Prequential convergence**: Spearman(arm@cutoff, arm@full-data) per
   (cell, arm, cutoff) — how fast each arm's ranking stabilizes.
3. **Gating-target sensitivity**: Half 1's regret recomputed under A0
   and EB as the "true top-k" definition (A1 primary).

## Out of scope

- The offline MCBO bake-off and any BoTorch work (separate roadmap gate
  input).
- Any new sim data; any AWS launch; the b1/b2 ablation wave (folded, D1).
- New model families (LightGBM/XGBoost etc. — spec 31 requires a
  separate amendment) and per-cutoff HPO.
- Reconstructing per-matchup `combat_fitness` (pinned out; see Half 2).
- The go/no-go decision itself — this delivers the instrument and its
  first readings; synthesis happens at the Phase 7 gate with the MCBO
  bake-off.
- Nearest-neighbor / distance-stratified rank metrics (M2 residue,
  deferred to item-5 re-baseline per roadmap).

## Critical files

| File | Change |
|---|---|
| `docs/specs/31-phase7-matchup-data.md` | Amend: replay artifact contract (see step 1) |
| `scripts/analysis/phase7_prequential_replay.py` | New: replay driver (config, stream construction, fits, gating sim, arms, artifact writer) |
| `tests/test_phase7_prequential_replay.py` | New: unit + synthetic-DB integration tests |
| `src/starsector_optimizer/deconfounding.py` | Extract shared pooled-residual-variance helper (used by ScoreMatrix + posthoc_ranker + replay) |
| `src/starsector_optimizer/posthoc_ranker.py` | Retrofit both inline σ̂_ε² copies onto the shared helper |
| `src/starsector_optimizer/models.py` | Fix stale `EBShrinkageConfig` docstring ("7-covariate" → 10-dim; boy-scout, seam touched by this plan) |
| `docs/reports/<sweep-date>-phase7-prequential-replay.md` | New report (dated at sweep time) |
| `docs/roadmap.md`, `docs/reports/INDEX.md` | Grooming on retirement |

## Public concepts and canonical owners

- **Prequential replay stream / replay cell / cutoff / horizon bucket /
  gating policy / rankable builds**: new concepts, owned by spec 31
  after amendment.
- **Estimator arms A0/A1/A2/EB/A3**: spec 31 names the offline arm
  registry (incl. the normative A0/A1 boundary and A2 pinning); spec 28
  stays the owner of the underlying estimator math.
- **Empirical readings**: the dated report owns all numbers (CONVENTIONS
  rule); spec carries only designed defaults (carve-out).

## Design decisions pinned (with rationale)

1. **Arrival order from eval-log timestamps**, per replay cell —
   suggestion order is counterfactual to deployment; completion order is
   what a deployed trainer sees. Optimism from in-flight trials is
   handled by `train_gap_trials` (G=0 and G=Ĝ arms), not ignored.
2. **Decision-time prediction panels**: predicted scores use planned
   `opponent_order` only; fidelity target T1 restricted to finalized
   trials; T2 opponent-adjusted. No realized-row-set information enters
   any gating decision.
3. **`hp_differential` target for arms** (see Half 2 pinned decision).
4. **No per-cutoff HPO**; `DEFAULT_HYPERPARAMETERS` only.
5. **Exploratory claim labels**: `claim_label="exploratory"`,
   `honest_eval_usage="exploratory_selection"` (oracle values are used
   as evaluation targets for arm comparison — more than diagnostic, less
   than final; confirmatory promotion is explicitly not claimed). No
   split seeds are drawn — the stream is deterministic and seedless; the
   artifact stamps a replay-specific reuse field
   **`reused_source_data: true`** (wave-1 DB heavily re-analyzed) rather
   than overloading spec 31's `reused_partition` (whose defined meaning
   is forward-time-specific).
6. **All tunables in `ReplayConfig`** (frozen dataclass):
   min_train_trials, cutoff_stride, min_future_trials, horizon_buckets,
   gating_fractions, gating_sensitivity_fraction, train_gap_trials,
   tie_break_draws, cv_variance_floor, bootstrap iterations/seed,
   hpo_seed, thread counts. No magic numbers in bodies.
7. **Script placement + loading**: `scripts/analysis/`, importlib-loads
   baseline/learned scripts (established pattern), reuses
   `_feature_bundle`, `make_model` (both scripts),
   `evaluation_metric_suite`, `load_training_matchups`,
   `load_recovered_builds`,
   `deconfounding.{twfe_decompose,trimmed_alpha,eb_shrinkage,triple_goal_rank}`
   + the new shared pooled-residual-variance helper. No new fitting
   code. *Implementation note (deviation from the draft reuse list)*:
   `run_inline_comparators`/`_fit_score` are not called directly — both
   are coupled to `LearnedExperimentConfig` and targeted test bundles,
   while the replay predicts over target-free planned panels; the replay
   calls the same underlying `make_model` fit/predict primitives through
   a thin local loop instead.
8. **Determinism contract (byte-identical artifact on re-run)**:
   single-threaded predict for learned families (mirrors the spec 31
   adversarial-AUC precedent — parallel tree-vote accumulation is
   float-non-associative); `duration_seconds`/timing fields stripped
   from inline-comparator results before artifact assembly; no
   wall-clock values in artifact content (sweep date lives in the
   filename, passed via CLI); all RNGs (bootstrap, tie-break) seeded
   from config; sorted-keys JSON via the `_write_json_payload` pattern;
   `[phase7-replay]` progress tags to stderr.
9. **Mandatory provenance stamps** (spec 31 rule): module
   `experiment_schema_version`, `feature_schema_version`,
   `feature_profile`, source DB path, eval-log root, `code_version`,
   `dependency_extra`, hpo_seed, full `ReplayConfig` echo, Ĝ
   measurements.

## Step-by-step implementation sequence

1. **Spec 31 amendment** (before tests): new §"Prequential replay
   ablation" —
   - stream definition (per-cell arrival order via eval-log timestamp
     join, join hard-error contract), **with a reconciliation paragraph
     against the existing forward-time split** (suggestion-order,
     lexicographic cell concatenation): two distinct temporal semantics,
     cross-referenced, each with its use case;
   - terminology (replay cell / oracle panel / rankable builds),
     cutoff/horizon/gating semantics incl. skipped-rows-removed
     training semantics and the corrected pruner reference definition;
   - decision-time prediction panel rule (planned `opponent_order`);
   - arm registry with the normative A0/A1 boundary, the pinned A2
     estimator + `cv_variance_floor`, the EB pure-function note, A3
     tie handling;
   - claim boundary (exploratory; `exploratory_selection`;
     forward-deployment caveat; counterfactual-TPE caveat;
     `reused_source_data` field);
   - predeclared headline statistic verbatim (arm, G, q\*-rule, k,
     target, aggregation functional, mandatory null);
   - artifact schema (provenance stamps of design decision 9;
     per-(cell,cutoff,bucket,arm) fidelity records for T1/T2;
     gating curves; arm evaluations; Ĝ);
   - determinism contract (design decision 8).
   Add the script to the implementation-file list.
2. **Tests first** (`tests/test_phase7_prequential_replay.py`),
   mirroring `test_phase7_learned_surrogate_experiment.py` conventions
   (importlib load, `_config()`/`_rows()` factories, monkeypatched fit
   seams, DummyModel):
   - stream construction: ordering by (timestamp, trial_number); join
     hard-errors (missing DB trial, missing log trial); pruned included
     in training; gappy trial numbers handled; per-cell isolation.
   - cutoff grid + horizon bucketing edge cases (short cells, tail
     bucket, min_future guard) + per-bucket support accounting.
   - train_gap exclusion correctness.
   - prediction panel: planned `opponent_order` used for all trials
     (incl. pruned); T1 restricted to finalized trials; T2 target =
     full-data A1 α̂ over rankable builds.
   - gating sim: bottom-q selection, skipped-rows removed from later
     training (and kept under the sensitivity flag), savings + pruner
     reference accounting, top-k regret bookkeeping, tie-break
     determinism given seed.
   - estimator arms: A0 vs A1 differ exactly by trim; A2 formula +
     variance-floor guard against hand-computed fixture; A3 ties = top
     quartile; EB invoked with logged covariates on rankable builds
     (fixture); fit-on-all-rows/rank-finalized-only support rule.
   - shared σ̂_ε² helper: deconfounding + posthoc_ranker equivalence
     (existing posthoc_ranker tests keep passing).
   - artifact: schema keys incl. provenance stamps, no timing/wall-clock
     fields, determinism (two runs byte-identical).
   - one integration test on a synthetic sqlite DB + fixture eval logs
     (tmp_path), using session game fixtures where features are needed.
3. **Implement** the helper extraction + driver to green tests; run
   targeted pytest per step.
4. **Local sweep** (task #52): full 15-cell run; measure Ĝ; produce
   artifact `data/phase7/prequential_replay_<date>.json`. Charts only if
   the report needs them per the `empirical-report` skill; producers
   would be checked-in scripts under `scripts/analysis/`.
5. **Report** per `empirical-report` skill (Methods-before-Results,
   statistical-learning setup, predeclared statistics, Reading
   paragraphs, file-map appendix) + honest-evaluation discipline (no
   cross-cell build-ranking claims from within-cell scores; oracle
   caveats; the D2 deviations of the Goal restated where the fold is
   discharged).
6. **Gates + audit + grooming** (task #53): full pytest, ruff/mypy/
   deptry, validate_docs; 3-agent post-impl audit; roadmap item-2
   closure (D2 deviation noted); INDEX update; plan retirement.

## Tests and mechanical gates

- `uv run pytest tests/test_phase7_prequential_replay.py -v` green, then
  full `uv run pytest tests/`.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run deptry .`
- `uv run python scripts/validate_docs.py`
- Grep gates: no TODO/FIXME/HACK introduced; no test skips; no magic
  numbers in new function bodies (spot-check against `ReplayConfig`).

## Review findings and dispositions

Consolidated from the three fresh-eye agents (2026-07-13); every
finding below is folded into the body above.

| # | Finding (severity) | Disposition |
|---|---|---|
| 1 | Gating/fidelity used realized matchup rows — pruner-outcome leak + C1 confound re-import (blocking; agents B, C) | Predicted scores now use planned `opponent_order` only (verified logged for all 2,374 trials); fidelity split into T1 (finalized-only, panel-matched) + T2 (opponent-adjusted vs full-data A1 α̂) |
| 2 | Headline statistic permitted ~64 forking paths (blocking; agent C) | Headline fully pinned: catboost_regressor, G=Ĝ, per-cell max-q at zero top-3 regret under A1, median over 15 cells, per-cell distribution + opponent_mean null shown; all else labeled sensitivity |
| 3 | "Cell" meant three things; oracle panel is top-3-per-seed not top-9-per-campaign; random-baseline outside replay (major; agents A, B, C) | Terminology section pins replay cell / oracle panel / rankable builds; data fact 4 corrected; gating Δ-oracle clause now "≤ 3 oracle'd builds" |
| 4 | Oracle recovery pooled α̂ across independent studies with no alignment rule, on n=9 (major; agents B, C) | Primary = within-cell pairwise concordance (≈45 pairs); secondary = campaign-level under pinned μ̂+α̂ alignment; both declared direction-checks unable to discriminate arms |
| 5 | Skipped-trials' rows silently stayed in later training (major; agent C) | Pinned: removed from later training; keep-rows sensitivity at gating_sensitivity_fraction=0.3 |
| 6 | EB covariates exist only for finalized trials (~1/3 missing) (major; agent C) | Rankable-builds rule: all arms fit on all rows, rank finalized builds only — equal support, matches live behavior, respects the Java-emission invariant |
| 7 | Byte-identity vs threaded predict + timing fields (major; agents B, C) | Determinism contract: single-threaded predict, timing fields stripped, no wall-clock in artifact |
| 8 | A2 β̂_cv estimator undefined; wrong owner cited (minor; agents B, A) | A2 pinned normatively (OLS slope, cv_variance_floor guard, current-scorer reconstruction caveat) |
| 9 | σ̂_ε² formula exists in 3 copies; a 4th loomed (minor; agents B, A) | Shared helper extracted in deconfounding.py; posthoc_ranker retrofitted |
| 10 | EBShrinkageConfig.enabled ambiguity post-D3 (minor; agent B) | Arm table states pure-function call, `enabled` not consulted |
| 11 | `reused_partition` semantics overloaded (minor; agent B) | Replay-specific `reused_source_data: true` field |
| 12 | Pruner reference mis-defined as pruned-row share (minor; agents A, C) | Corrected to Σ(opponents_total − opponents_evaluated) over pruned trials |
| 13 | D2 "same incumbent definition" deviation unsurfaced; EB arm addition unacknowledged (minor; agents A, B) | Goal names five arms + both deviations; restated at fold discharge (report + roadmap closure) |
| 14 | Provenance stamps not enumerated (minor; agent B) | Design decision 9 enumerates them; tests assert |
| 15 | Bootstrap exchangeability across heterogeneous campaigns (minor; agent C) | Campaign-stratified resampling, descriptive label, per-campaign means |
| 16 | Horizon-bucket support bias near stream end (minor; agent C) | Support counts reported; no unsupported cross-bucket comparisons |
| 17 | Stale EBShrinkageConfig docstring "7-covariate" (minor pre-existing; agent A) | Boy-scout fix in Critical files |
| 18 | Broken relative links from plans/active depth (minor; agent A) | Fixed (`../../../docs/...`) |
| 19 | Two temporal semantics would coexist in spec 31 unreconciled (minor; agent A) | Step-1 reconciliation paragraph |
| 20 | In-session data facts inline in plan (minor; agent C); `optimizer.py:840` line nit (agent B); report-date placeholder + charts vagueness (self-review) | Data facts marked for re-owning by spec/report Methods; line ref fixed; report dated at sweep time; charts decision rule named |

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-13 (self-review Phases 1–4 + consolidation of sub-agent findings)
- Findings: see table above (Phases 1–4 contributed finding 20's self-review items; all others from Phase 5 agents)
- Dispositions: all folded into the plan body; none deferred
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-13
- Agents:
  - Pattern Consistency: findings (8; consolidated rows 3, 8, 9, 12, 13, 17, 18, 19) — all dispositioned
  - Spec Alignment: findings (10; consolidated rows 1, 3, 4, 7, 8, 9, 10, 11, 13, 14, 20) — all dispositioned
  - Engineering & Design Invariants: findings (11; consolidated rows 1–7, 12, 15, 16, 20) — all dispositioned
- Findings: see consolidated table above
- Dispositions: all folded into the plan body; none deferred
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

3 independent audit agents (pattern consistency / spec alignment /
invariants) over the diff; mechanical grep gates; verification that the
artifact reproduces byte-identically on re-run; report checked against
CONVENTIONS §empirical-report standard including the
supervised-learning checklist.

## Post-implementation audit record (2026-07-14)

3 agents ran over commit `7a0b64f` + working tree. **Code verdict:
clean** — no leakage (gating path traced end-to-end), no invariant
violations, helper retrofit behavior-preserving, all report numbers
reproduced except the findings below. All findings fixed in
`37cfb94` + the evidence commit:

- (blocking, report) "never regrets at q = 0.1" was false (1/15 cells
  regrets; regret non-monotone in q under remove-semantics) → §4 + Synthesis
  rewritten; per-cell regret row added to the table.
- (blocking, report) pooled T2 "monotone decay" was substantially a
  bucket-support artifact → balanced-panel computation added to the
  checked-in producer (`t2_balanced_panel`), Figure 1 recaptioned,
  reading revised to flat-within-~40-then-collapse, tail
  range-restriction alternative stated.
- (major, process) reproduction claim published ahead of evidence →
  commit held until the same-code single-cell byte-compare and the
  clean-tree canonical sweep completed; appendix rewritten to describe
  the actual comparisons.
- (major) chart script duplicated the stratified bootstrap → reuses the
  replay module's single owner.
- (minors, all fixed) "80 trees" → 200 (4 sites); §3 aggregation
  convention aligned to cell means; support line marked arm-specific;
  spec CI wording aligned; ESTIMATOR_ARMS wired; dead config field
  removed; percentile/min-n literals named; chart panel letters/tail
  label; stray spec indent.
- (declined, with rationale) `keep_skipped_sensitivity` omitting
  `oracle_skipped`: spec does not require Δ-oracle for the sensitivity
  run and it is derivable from the artifact's per-cell
  `oracle_build_means`; adding it would have invalidated the in-flight
  canonical sweep for a shape nit.

## Retirement checklist

- [x] status: implemented; implemented date; implementation_commit
- [x] post_impl_audit recorded
- [x] moved to `.claude/plans/archive/2026/`
- [x] roadmap item 2 marked delivered; replay report named the pre-wave
      T2 baseline for the item-4 data wave; D2 fold deviations noted at
      discharge (report Goal + §5 + roadmap closure)
- [x] reports INDEX updated (2026-07-14 row); dependent-report links in
      the report's appendix

Retirement notes: determinism verified (same-config single-cell double
run byte-identical; full-sweep shared-field equality across audit-fix
commits). The canonical artifact's `code_version` stamps
`37cfb94+dirty` — the dirt was uncommitted documentation only; code
files matched the commit.
