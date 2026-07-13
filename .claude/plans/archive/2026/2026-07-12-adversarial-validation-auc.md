---
plan_type: implementation
status: implemented
created: 2026-07-12
approved: 2026-07-12
implemented: 2026-07-12
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-07-11-phase7-methodology-review.md
  - docs/reports/2026-07-12-phase7-attempt3-surrogate-results.md
  - docs/roadmap.md
  - .claude/skills/design-invariants.md
  - .claude/skills/empirical-report.md
implementation_commit: fbf5177
post_impl_audit: passed
superseded_by: null
---

# Adversarial-Validation AUC Diagnostic (M2, first of four)

## Goal

Implement the adversarial-validation AUC leakage diagnostic — the first of
the four M2 diagnostics currently stamped
`not_applicable / diagnostic_not_implemented` — so that:

1. every completed learned-experiment artifact carries a **computed**
   train/test distinguishability measurement on the exact feature space the
   surrogate saw, and
2. a local evidence sweep over the frozen wave-1 DB produces a dated report
   that qualifies the build-split reading flagged by methodology review M2:
   *'"build transfer rho 0.82" should be read as interpolation within a
   TPE-concentrated cloud until nearest-neighbor-distance-stratified metrics
   exist.'* Adversarial AUC is the first and cheapest of those diagnostics
   (attempt-3 report recommendation 5 ties it to the §2.4 discussion); it
   qualifies the interpolation reading but does not fully discharge M2 —
   distance-stratified metrics remain the fuller remedy and stay out of
   scope here.

No new simulation data; no AWS spend. Runs entirely locally.

## Context and source docs

- Methodology review M2 (CONFIRMED gap): the four unimplemented diagnostics
  are precisely the ones that would qualify the strong build-split numbers.
  Roadmap (Deferred → "M2 leakage diagnostics") assigns adversarial-validation
  AUC first, parked under item 2's ablation wave — implementing it now means
  the item-2 wave stamps a real diagnostic instead of
  `diagnostic_not_implemented`.
- Spec 31 §"Learned Baseline Experiment" already names the diagnostic in the
  canonical-run contract ("adversarial-validation AUC by hierarchy level")
  and in the artifact-contract list; both currently permit the
  `not_applicable` stamp. Spec 31 §"Library Usage Policy" sanctions
  scikit-learn for exactly this kind of auditable diagnostic.
- Current code: `leakage_diagnostics()` in
  `scripts/analysis/phase7_learned_surrogate_experiment.py` stamps all four
  entries `not_applicable`; the batch gate `_leakage_diagnostics_pass`
  (`src/starsector_optimizer/phase7_learned_batch.py`) accepts only statuses
  `pass` / `not_applicable`; `_contract_ok` requires only that each entry be
  a dict with a `status` key.
- A scratchpad prototype against the frozen wave-1 DB (2026-07-12, this
  session) validated the design empirically; its three load-bearing results
  are folded into the design below (grouped CV is mandatory; serial
  `predict_proba` is required for determinism; per-fold AUC is unstable on
  coarse-grouped splits).

## Design

### What is measured

Per experiment cell, after the outer feature bundles are built: label every
outer-train row 0 and every outer-test row 1, using the **cell's own feature
records** (post feature-profile, the same dict records the surrogate models
vectorize — so the measurement is distinguishability in the model's actual
feature space; note the records exclude build-side identity (`build_key`)
but do include opponent identity features such as `opponent_variant_id` /
`opponent_hull_id`). Vectorize with `DictVectorizer(sparse=True)` fit on the
combined records (diagnostic-only classifier; nothing flows back into the
surrogate). Compute out-of-fold predicted probabilities via **grouped**
stratified CV (below) and report the pooled out-of-fold ROC AUC.

- AUC ≈ 0.5 → test rows are indistinguishable from train rows → measured
  transfer is interpolation inside the training cloud (the M2 qualifier for
  the build split).
- AUC → 1.0 → the split imposes real distribution shift → the cell measures
  extrapolation. On grouped splits (opponent-family, component-vocab) high
  AUC is *by design* (held-out component one-hots are zero-variance in
  train; held-out opponents differ in their feature block), not a defect —
  which is why this diagnostic is **descriptive, not pass/fail**.

Spec's "by hierarchy level" is satisfied per-cell: each artifact is exactly
one split level, so its AUC *is* the AUC at that level. The spec amendment
will state this reading explicitly.

### Grouped CV (mandatory, not optional)

Rows are not i.i.d.: feature records are a pure function of
`(build_key, opponent_variant_id, feature_profile)`, so rows cluster by
build and by opponent, and on every split each cluster on the split's
assignment side shares a single class label. Row-level CV therefore lets
the classifier fingerprint clusters it saw during fit and score their
sibling rows — measuring **group memorization, not distribution shift**
(prototype: row-level CV yields AUC 1.0000 on the build split; grouped CV
yields ≈ 0.5 on the same cell). The adversarial CV must be
`StratifiedGroupKFold` with groups chosen so the classifier never fits on
any row of a group it scores:

| split family | CV group unit | `group_unit` string |
|---|---|---|
| build | `row.build_key` | `build_key` |
| component-vocab | `row.build_key` (label is a function of build content) | `build_key` |
| forward-time | `row.build_key` (builds are time-contiguous; row-level CV would memorize builds) | `build_key` |
| opponent | `row.opponent_variant_id` | `opponent_variant_id` |
| opponent-hull | hull via `baseline.opponent_group_maps` | `opponent_hull_id` |
| opponent-family | family via `baseline.opponent_group_maps` | `opponent_family` |
| seed-cell | `f"{row.campaign}:{row.seed}"` (builds nest inside cells) | `campaign_seed_cell` |

Rationale, spec-recorded: the group unit is the outer split's assignment
unit, coarsened to `build_key` where the assignment unit (vocabulary,
`source_order`) is finer than the build-level feature clustering. Grouping
also prevents the opponent-ID one-hots from trivially saturating the
opponent-split AUC (the classifier never sees a scored variant's ID during
fit).

Fold count: `min(default_cv_folds, min over both classes of distinct group
count)`. If that is < 2, the entry is
`{"status": "not_applicable", "reason": "insufficient_groups_for_grouped_cv"}`
(cannot occur on canonical cells; keeps the producer total).

### Classifier, determinism, and parameters (designed constants)

`RandomForestClassifier` (sklearn — sanctioned; pattern-consistent with the
RF comparator), parameters in a module-level designed constant
`ADVERSARIAL_VALIDATION_PARAMS` in the experiment script:

- `n_estimators: 100`, `min_samples_leaf: 5` (smoothing + bounded runtime;
  an adversarial probe does not need the learned-RF's 200 trees),
- `cv_folds: 5` (the default, reduced per the group rule above),
- fit-time `n_jobs` = `config.model_thread_count` (existing thread-budget
  policy; per-tree seeding makes fit deterministic regardless of `n_jobs`),
- **`predict_proba` runs with `n_jobs=1`** (`clf.set_params(n_jobs=1)`
  after fit): parallel prediction accumulates tree votes in
  thread-completion order, and float non-associativity flips near-tied
  ranks — the prototype observed AUC inequality across thread counts on
  near-0.5 cells. Serial aggregation restores exact reproducibility, which
  the merge coherence invariant depends on. Prediction cost is negligible
  next to fit.
- `random_state` for both the fold shuffle and the classifier =
  `config.split_seed` — deliberately **not** `hpo_seed` (every surrogate
  fit uses `hpo_seed`): the entry must be a pure function of cell identity
  `(partition, feature_profile, split_seed)`, independent of model and HPO
  configuration, so it is identical across models within a cell and across
  workers. The spec amendment records this rationale so a future
  seed-alignment change does not silently break the coherence keying.

Prototype-measured runtime is a few seconds to ~1 min per cell on the
wave-1 DB — small against CatBoost HPO cells; acceptable for batch workers
and for a 60-cell local sweep.

### Artifact entry shape

```json
"adversarial_validation_auc": {
  "status": "computed",
  "value": 0.63,
  "per_fold_auc": [0.61, null, 0.66],
  "cv_folds": 3,
  "fold_construction": "stratified_group_kfold",
  "group_unit": "build_key",
  "n_train": 17000,
  "n_test": 4200,
  "n_train_groups": 1900,
  "n_test_groups": 470,
  "classifier": {"family": "random_forest_classifier",
                  "n_estimators": 100, "min_samples_leaf": 5},
  "seed": 101,
  "separation_band": "weak_separation"
}
```

`value` is the **pooled out-of-fold ROC AUC** (every row predicted exactly
once), spec-pinned. Per-fold AUC is descriptive detail only: on
coarse-grouped splits a fold's test side may hold one or two groups, making
per-fold AUC wildly unstable (prototype observed per-fold values of 0.0 and
1.0 around a pooled 0.56); a fold whose scored rows carry a single class
has no defined AUC and records `null`. The bands apply to `value`. The
group counts let readers judge stability.

### Status and reason vocabulary (shared ownership)

New diagnostic status `computed` for descriptive (non-gating) diagnostics.
The status string and the adversarial reason strings are owned by
`phase7_matchup_data.py` (the shared experiment-contract constants owner),
imported by both the experiment script and the batch module so the two
layers cannot drift:

- `DIAGNOSTIC_COMPUTED_STATUS = "computed"`
- `ADVERSARIAL_REASON_INSUFFICIENT_GROUPS = "insufficient_groups_for_grouped_cv"`
  (the only legitimate escape on a completed result)
- `ADVERSARIAL_REASON_NO_BUNDLES = "outer_feature_bundles_not_built"`
  (insufficiency artifacts and skipped-optional-model results — the
  diagnostic is implemented but those paths never build feature bundles;
  chosen, not impossible, and now stated honestly)
- `ADVERSARIAL_REASON_RESULT_SPECIFIC = "result_specific_diagnostic"`
  (top-level payload stamp — the diagnostic lives per-result; this replaces
  the now-false `diagnostic_not_implemented` at the top level and in the
  merged artifact's top-level copy)

`diagnostic_not_implemented` remains only on the three genuinely
unimplemented diagnostics, everywhere.

Designed interpretation bands (spec-documented; labels, not pass/fail),
`ADVERSARIAL_AUC_BANDS` beside the params:

- `value < 0.55` → `indistinguishable`
- `0.55 ≤ value < 0.70` → `weak_separation`
- `value ≥ 0.70` → `strong_separation`

Reports interpret bands per split family (low AUC on the build split ⇒
interpolation regime; high AUC on grouped splits ⇒ working as designed).

Confirmatory-artifact reconciliation: spec 31 currently requires
"pass, warning, or fail semantics before the run" for confirmatory
artifacts. The amendment revises that sentence to distinguish **gated**
diagnostics (pass/warning/fail, e.g. forbidden-key overlap) from
**descriptive** diagnostics (`computed` + spec-predeclared interpretation
bands); for descriptive diagnostics the bands, fixed in the spec before the
run, are the predeclared semantics a confirmatory artifact carries.

### Contract and merge changes

- `_leakage_diagnostics_pass`: **per-entry** accepted-status sets — a
  global union would let a `forbidden_key_overlap: fail` artifact sneak
  through as `computed`:
  - `forbidden_key_overlap`: `{pass, not_applicable}` (unchanged),
  - `adversarial_validation_auc`: `{computed, not_applicable}`,
  - the three unimplemented diagnostics: `{not_applicable}` (implementing
    one later must consciously extend the validator).
- `_contract_ok` (completed results only): the `adversarial_validation_auc`
  entry must be either `status == "computed"` with a finite `value` in
  [0, 1], or `status == "not_applicable"` with
  `reason == ADVERSARIAL_REASON_INSUFFICIENT_GROUPS` (exact constant — any
  other reason, including the old `diagnostic_not_implemented` stamp and
  loose fixture reasons, is rejected on completed results).
- **Within-cell coherence at merge** (mirrors the realized-split digest
  invariant): across the completed results sharing `(split, split_seed)`,
  the tuple `(status, value, reason)` of the adversarial entry must be
  identical — the entry is a pure function of (partition, profile, seed),
  so any mixture (including `computed` vs `not_applicable`) or value
  inequality exposes cross-worker nondeterminism. Implemented as a sibling
  validator `_validate_adversarial_auc_coherence(results)` called in
  `merge_job_artifacts` immediately after
  `_validate_realized_split_digests` (same position: after the
  insufficiency-refusal gate, so only completed results reach it). Unlike
  the digest invariant this compares empirical float output, so the spec
  amendment states the remediation explicitly: a mismatch is a
  nondeterminism tripwire (dependency drift, heterogeneous numeric
  behavior), not data corruption — investigate the environment and re-run
  the affected cell; do not hand-edit artifacts.
- `EXPERIMENT_SCHEMA_VERSION` 3 → 4 (`phase7_matchup_data.py`): the
  completed-artifact contract changed shape. No v3 wave artifacts exist
  (the v3 bump shipped 2026-07-12 with no wave run since), so the bump is
  cheap; version-equality checks continue to isolate older artifacts, and
  the next canonical wave's fresh dated path note in spec 31 moves to v4.

### Producer wiring

- New function `adversarial_validation_entry(train, test, groups, config)`
  in the experiment script (near `leakage_diagnostics`), taking the outer
  `FeatureBundle`s and per-row group keys; a helper
  `adversarial_cv_groups(split, rows, game_dir)` computes the group keys
  per the table above (reusing `baseline.opponent_group_maps` — no
  duplicated grouping logic).
- `leakage_diagnostics(hierarchy=None, adversarial_validation=None,
  adversarial_unavailable_reason=ADVERSARIAL_REASON_NO_BUNDLES)` — a
  provided entry replaces the stamp; when absent, the adversarial entry is
  `not_applicable` with the given reason. Call sites: `run_one` completed
  path passes the computed entry; insufficiency and
  missing-optional-model paths use the default (`NO_BUNDLES`); the
  top-level payload call passes `RESULT_SPECIFIC`.
- `run_one` computes the entry right after `outer_train`/`outer_test` are
  built. The other three M2 diagnostics keep their `not_implemented`
  stamps (explicitly out of scope).

### Evidence sweep and report

- Scratchpad driver (session scratchpad, not committed) importing
  `construct_splits`, `baseline._feature_bundle`,
  `adversarial_cv_groups`, and `adversarial_validation_entry`: iterate the
  canonical cell set — 5 seeded split families × effective seed panel +
  component-vocab (9 seeds) + forward-time (1) = 60 cells — on the frozen
  wave-1 DB at the canonical feature profile. No model fits; JSON output
  to scratchpad, values copied into the report.
- Dated report `docs/reports/2026-07-12-phase7-adversarial-auc-evidence.md`
  (per `empirical-report` skill): per-family AUC distributions
  (mean/min/max over seeds), the build-split reading against the bands,
  contrast with grouped splits, and the explicit qualification of the
  interpolation reading. Citations kept distinct: the "rho 0.82 /
  TPE-concentrated cloud" sentence is methodology review M2; attempt-3
  §2.4 is the component-vocabulary discussion whose recommendation 5
  requested this diagnostic. The report must state the opponent-identity
  feature caveat (opponent splits' AUC reflects the opponent feature
  block; grouping prevents trivial ID saturation) and the per-fold
  instability caveat. Cross-link from the attempt-3 report is **not**
  edited (reports are dated evidence); roadmap + spec point forward.

## Scope

1. `phase7_matchup_data.py`: `EXPERIMENT_SCHEMA_VERSION` 3 → 4; new
   diagnostic status/reason constants.
2. Experiment script: `ADVERSARIAL_VALIDATION_PARAMS`,
   `ADVERSARIAL_AUC_BANDS`, `adversarial_cv_groups()`,
   `adversarial_validation_entry()`, `leakage_diagnostics` parameters,
   `run_one` wiring, top-level payload reason, sklearn classifier imports.
3. Batch module: `_leakage_diagnostics_pass` per-entry status sets,
   `_contract_ok` completed-result requirement,
   `_validate_adversarial_auc_coherence` at merge.
4. Spec 31 amendment (before implementation, per ddd-tdd): diagnostic
   definition (grouped CV + group-unit table + rationale), designed
   constants/bands, determinism note (serial predict) + `split_seed`
   rationale, `computed` status semantics + confirmatory-sentence
   revision, reason vocabulary, pooled-OOF `value` definition + per-fold
   nullability, per-cell "by hierarchy level" reading, artifact-contract
   list entry, merge-checklist bullets (contract + coherence + remediation
   guidance), schema version 4 at **all five** mentions (constants list
   ~line 393, exposed-constant comment ~781, JSON-output field list ~950,
   merge bullet ~1203, canonical-path note ~1244).
5. Tests (failing first): see gate list below.
6. Evidence sweep + dated report (indexed in `docs/reports/INDEX.md`).
7. Roadmap grooming: Deferred M2 bullet — AUC shipped, three diagnostics
   remain parked under item 2's wave.

## Out of scope

- Nearest-neighbor overlap, rare-combination overlap, sparse-ID ablation
  (remain `diagnostic_not_implemented`; parked under item 2's wave).
- Nearest-neighbor-distance-stratified rank metrics (the fuller M2 remedy
  named by the review; a later item).
- A first-class CLI for the diagnostic sweep (scratchpad driver suffices;
  promote only if a second consumer appears).
- Tuning the adversarial classifier or additional classifier families.
- Retiring `sparse_pairwise_ridge` from the canonical matrix (separate
  follow-up already noted at the uniqueness-plan retirement).

## Critical files

- `src/starsector_optimizer/phase7_matchup_data.py`
- `src/starsector_optimizer/phase7_learned_batch.py`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `docs/specs/31-phase7-matchup-data.md`
- `tests/test_phase7_matchup_data.py`
- `tests/test_phase7_learned_surrogate_experiment.py`
- `tests/test_phase7_learned_batch.py`
- `docs/roadmap.md`
- `docs/reports/INDEX.md`
- `docs/reports/2026-07-12-phase7-adversarial-auc-evidence.md` (new)

## Public concepts and canonical owners

- Diagnostic definition, grouped-CV rule, bands, `computed` semantics,
  reason vocabulary, schema v4: spec 31.
- `EXPERIMENT_SCHEMA_VERSION`, status/reason constants:
  `phase7_matchup_data.py` (existing shared-constants owner).
- Producer function + designed parameter constants: experiment script.
- Merge/contract enforcement: `phase7_learned_batch.py`.
- Empirical AUC values: the dated report only (empirical-numbers rule).

## Implementation sequence

1. Amend spec 31.
2. Failing tests: experiment-script unit tests → batch contract/merge tests.
3. Implement producer (constants, group helper, entry function,
   `leakage_diagnostics` parameters, `run_one` wiring).
4. Implement batch-side enforcement + schema bump; update fixtures.
5. Quality gates + full pytest.
6. Evidence sweep on wave-1 DB (background); write report.
7. Post-impl audit, roadmap grooming, plan retirement.

## Tests and mechanical gates

- Experiment script:
  - **grouped-memorization guard**: fixture where every group has a unique
    signature feature and groups are assigned to train/test at random (no
    distribution shift) → grouped CV stays in the `indistinguishable`
    band; the same fixture through row-level CV would inflate — this is
    the regression test for the core design decision.
  - genuinely shifted group distributions → `strong_separation`.
  - determinism: same inputs + seed, different fit thread counts →
    identical entry (serial-predict guarantee).
  - fewer than 2 groups in a class → `not_applicable` /
    `insufficient_groups_for_grouped_cv`.
  - entry shape: all documented keys present; `per_fold_auc` length ==
    `cv_folds` with `null` for single-class folds; folds reduced when the
    smaller class has < 5 groups; `group_unit` matches the split table.
  - `adversarial_cv_groups` returns the documented unit per split family.
  - `leakage_diagnostics(adversarial_validation=...)` passes the entry
    through; default call stamps `not_applicable` with the `NO_BUNDLES`
    reason; explicit `RESULT_SPECIFIC` reason honored; the three
    unimplemented entries keep `diagnostic_not_implemented`.
  - schema-version test 3 → 4 rename.
- Batch module:
  - `_leakage_diagnostics_pass`: accepts `computed` only on the
    adversarial entry; rejects `computed` on `forbidden_key_overlap` and
    on the three unimplemented entries; still rejects
    `forbidden_key_overlap: fail`.
  - `validate_job_payload` rejects a completed result whose adversarial
    entry is `diagnostic_not_implemented` or a loose `not_applicable`
    reason; accepts `computed` with finite in-range value; rejects
    out-of-range/non-finite value.
  - merge rejects within-cell value inequality AND within-cell status
    mixture (`computed` vs `not_applicable`); accepts identical entries.
  - fixture `one_job_payload` stamps a `computed` entry + schema 4.
- `uv run pytest tests/ -v`; `uv run ruff check . && uv run ruff format
  --check . && uv run mypy && uv run deptry .`;
  `uv run python scripts/validate_docs.py`.
- Real-data sanity: the sweep driver runs one cell (build split, seed 101)
  on the wave-1 DB and produces a well-formed `computed` entry before the
  full sweep is launched.
- Report verification per CONVENTIONS §"Empirical-report writing
  standard" (supervised-learning checklist where applicable).

## Review findings and dispositions

Consolidated from the Plan Review Gate self-review, the three fresh-eye
sub-agents, and the scratchpad prototype (all 2026-07-12). Every finding
below is resolved in the Design/Scope/Tests sections above.

1. (Prototype + Design auditor, VALID) Row-level CV measures group
   memorization, not shift — AUC 1.0000 on the build split. → Grouped CV
   is now the core design (§Grouped CV), with the group-unit table,
   memorization-guard regression test, and group counts in the entry.
2. (Prototype, VALID) Parallel `predict_proba` breaks exact-float
   determinism across thread counts. → Serial predict after parallel fit;
   determinism test pinned across thread counts.
3. (Prototype, VALID) Per-fold AUC unstable/undefined on coarse-grouped
   splits. → `value` pinned to pooled OOF AUC; nullable `per_fold_auc`;
   group counts recorded; fold count = min over both classes' group
   counts.
4. (Pattern + Design auditors, VALID) Global status-set union weakens the
   forbidden-key gate. → Per-entry accepted-status sets, with negative
   tests.
5. (Spec auditor, VALID) Spec-amendment checklist missed the fifth
   schema-version mention (JSON-output field list ~line 950). → All five
   locations enumerated in Scope item 4.
6. (Spec auditor, VALID) Misquoted M2; overstated what this diagnostic
   discharges. → Goal now quotes M2 verbatim and states the
   qualifies-but-does-not-discharge relationship; distance-stratified
   metrics named in Out of scope.
7. (Spec + Pattern auditors, VALID) `computed` unreconciled with the
   confirmatory pass/warning/fail sentence. → Amendment revises the
   sentence: gated vs descriptive diagnostics; bands are the predeclared
   confirmatory semantics.
8. (Spec + Pattern + Design auditors) Top-level/merged artifact would
   self-contradict (`diagnostic_not_implemented` beside `computed`);
   reason string overloaded. → Reason vocabulary split
   (`NO_BUNDLES` / `RESULT_SPECIFIC` / `INSUFFICIENT_GROUPS`), owned by
   `phase7_matchup_data.py`; `not_implemented` reserved for the three
   unimplemented diagnostics.
9. (Spec + Pattern + Design auditors) Coherence validator undefined for
   status mixtures; float-equality remediation unstated. → Coherence
   compares the full `(status, value, reason)` tuple; mixture = failure;
   spec states the nondeterminism-tripwire remediation.
10. (Pattern auditor) "identifiers already excluded" inaccurate (opponent
    IDs are in the records). → Corrected in §What is measured; grouping
    prevents ID saturation; report caveat required.
11. (Pattern auditor) Loose `not_applicable` reasons would pass the new
    contract. → `_contract_ok` requires the exact
    `INSUFFICIENT_GROUPS` constant on completed results.
12. (Pattern auditor) `split_seed` vs `hpo_seed` divergence undocumented.
    → Rationale recorded in §Classifier and in the spec amendment.
13. (Pattern auditor) Status vocabulary two-literal drift risk. → Shared
    constants in `phase7_matchup_data.py`.
14. (Pattern auditor) §2.4/M2 citation conflation. → Citations separated
    in §Evidence sweep and report.
15. (Design auditor) Pooled-OOF vs mean-of-folds ambiguity. → Pinned:
    pooled OOF (every row predicted once; per-fold is unstable on
    coarse-grouped splits), spec-documented.
16. (Spec auditor, MINOR) `report-writing` skill does not exist. →
    `empirical-report` referenced in frontmatter and Evidence section.
17. (Spec auditor, MINOR) Insufficiency-path rationale wrong for
    `insufficient_inner_groups` (outer partition exists). → Reworded: the
    path never builds feature bundles by choice; reason string
    (`outer_feature_bundles_not_built`) states it honestly.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 (phases 1–4 self-review; two ambiguities fixed
  pre-launch: coherence-validator placement pinned to a named sibling
  function; real-data sanity method pinned to the sweep driver)
- Findings: see "Review findings and dispositions" (items 1–3 originate
  from the prototype run the self-review commissioned).
- Dispositions: all resolved in the plan body.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12
- Agents:
  - Pattern Consistency: findings (9 — 2 valid-concern, 7 minor; all
    resolved above)
  - Spec Alignment: findings (9 — 3 valid-concern, 6 minor; all resolved
    above)
  - Engineering & Design Invariants: findings (6 — 2 valid-concern,
    4 minor; all resolved above)
- Findings: consolidated and deduplicated in "Review findings and
  dispositions".
- Dispositions: all resolved in the plan body; no deferrals.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit results (2026-07-12)

Three sub-agents (implementation-vs-plan, spec-vs-implementation,
invariants/mechanical) + mechanical greps. All contract surfaces verified
against the spec; the mutation check confirmed the memorization-guard test
fails under row-level CV; the report's table numbers reproduce exactly
from the raw sweep JSON. Findings and dispositions:

- **Fixed** (report): the cross-seed size-stability sentence was false
  against the raw data (grouped-split n_test varies up to 25× across
  seeds) — replaced with the true per-cell ranges, which reinforce the
  few-groups caveat; "feature schema v3" → v4; "AUC ≥ 0.993" rounding
  overreach → "> 0.99"; the row-level 1.0000 figure now states its
  provenance (prototype + mutation check, not the sweep JSON).
- **Fixed** (code): `adversarial_cv_groups` now fails loudly on rows
  missing the group attribute instead of `getattr(..., None)` silently
  collapsing groups; the seed-cell key is single-sourced via
  `seed_cell_group_key` (used by both the outer splitter and the
  diagnostic); hull/family lookups reuse `_lookup_opponent_group`
  (descriptive error instead of bare KeyError); an explicit
  groups/records alignment guard added; the per-fold null branch
  extracted to `_fold_auc` and unit-tested (StratifiedGroupKFold empirics
  showed the branch is unreachable-in-practice defense — tested directly
  instead of via a contrived fixture); boolean `value` rejection pinned
  by test.
- **Fixed** (docs): roadmap M2 bullet groomed (delivered at retirement as
  sequenced); stale pre-existing feature-schema "v3" phrasing in spec 31
  reworded (boy-scout).
- **No change**: producer signature refined from the plan's sketch
  (records + explicit seed/threads instead of config coupling — pure
  function, needed by the sweep driver); `run_one` computes the entry
  late in the function (pure function of the same inputs, no behavioral
  difference); report's plan link points at the archive path (true after
  this retirement).

Verification after fixes: full suite 1062 passed + 1 skipped; ruff,
ruff-format, mypy, deptry, validate_docs all clean.

## Post-implementation audit requirements

- 3 sub-agents (implementation-vs-plan, spec-vs-implementation,
  invariants/mechanical) + design-invariants grep checklist.
- Verify the merged-contract change is exercised by tests on both accept
  and reject paths, including the status-mixture rejection.
- Verify no empirical AUC numbers leaked into spec 31 or reference docs.
- Verify the memorization-guard test fails under row-level CV (mutation
  check: swap `StratifiedGroupKFold` for `StratifiedKFold` and confirm the
  test catches it) — this is the test protecting the core design decision.

## Retirement checklist

- [x] All scope items implemented and verified.
- [x] Evidence report filed and indexed in `docs/reports/INDEX.md`.
- [x] Roadmap groomed (M2 bullet updated).
- [x] Frontmatter updated (`implemented`, `implementation_commit`,
      `post_impl_audit`).
- [x] Moved to `.claude/plans/archive/2026/`.
