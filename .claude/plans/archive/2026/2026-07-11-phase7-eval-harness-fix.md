---
plan_type: implementation
status: implemented
created: 2026-07-11
approved: 2026-07-11
implemented: 2026-07-11
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-07-11-phase7-methodology-review.md
  - docs/reference/phase7-surrogate-methodology-gaps.md
  - docs/reports/2026-07-11-aws-cost-analysis.md
  - docs/roadmap.md
implementation_commit: d39869c
post_impl_audit: passed (see Post-implementation audit record)
superseded_by: null
---

# Phase 7 Evaluation-Harness Fix

## Goal

Implement roadmap item 1 of the Phase 7 surrogate evidence program: replace
the pooled, single-seed, single-inner-holdout evaluation harness with one that
measures the surrogate's actual downstream job (ranking builds within
opponents), quantifies uncertainty, and stops reusing the burned seed-17
partitions — then re-run the canonical learned-batch matrix under the fixed
harness on AWS.

Authoritative prescription:
[2026-07-11 methodology review §6 item 1](../../docs/reports/2026-07-11-phase7-methodology-review.md),
remedying findings C1 (per-opponent rank metrics + primary-metric
re-designation), C2 (component-vocabulary split), C3 (repeated splits,
cluster-bootstrap CIs, consistent comparators; the comparator tuning-budget
asymmetry is named as a residual caveat, not remedied), C4 (rotated seed
bank + reserved confirmatory seed + outer-split lineage in the artifact;
the full lockbox/Ladder machinery stays with roadmap item 6), H2 (skill
scores + panel stats), H3 (build-aggregate honest-eval metrics), M1 (grouped
inner k-fold CV, aligned model seeds), and two L1 warts in touched files
(`final_refit_policy` provenance string; `feature_schema_version` constant
leaking into feature vectors). L1's remaining two sub-findings (RF-80-vs-200
and delta-label wording) are report-text issues in already-superseded
reports — out of scope, no code owner.

## Context and source docs

- [Spec 31](../../docs/specs/31-phase7-matchup-data.md) owns the data/split/
  artifact contract this plan amends.
- Current harness: `scripts/analysis/phase7_baseline_surrogate.py` (metrics,
  split dispatch, comparator models), `scripts/analysis/
  phase7_learned_surrogate_experiment.py` (nested HPO, payload),
  `src/starsector_optimizer/phase7_matchup_data.py` (split builders),
  `src/starsector_optimizer/phase7_learned_batch.py` (AWS orchestration,
  contract validation, merge), `scripts/cloud/phase7_learned_batch.py`
  (CLI, bundle).
- Verified facts driving the design: `component_fingerprint_json` ==
  `_build_json` verbatim (C2); worker userdata drains a lease queue in a
  `while` loop (`phase7_learned_batch.py:907`), so target_workers < job count
  is an orchestration-config change, not new infrastructure; 69.8% of target
  variance is between-opponent (C1); honest-eval table holds 54×54×30
  replicates usable as a noise-model source; the seed-17 opponent-hull
  holdout had 4/5 variants with zero within-opponent variance, so degenerate
  panels WILL occur under rotated seeds and every metric must define
  null-on-degenerate behavior.

## Scope

### S1 — New library module `src/starsector_optimizer/phase7_eval.py`

Pure evaluation functions + one frozen config dataclass. All thresholds are
config fields (no magic numbers). The module never imports from `scripts/`;
k values and any script-level config arrive as parameters.

```python
@dataclass(frozen=True)
class EvalMetricsConfig:
    top_k_values: tuple[int, ...] = (1, 3, 5)
    min_builds_per_opponent: int = 5
    min_opponents_per_build: int = 3
    top_fraction: float = 0.1
    min_top_fraction_rows: int = 3
    bootstrap_resamples: int = 500
    bootstrap_seed: int = 7717
    bootstrap_ci_level: float = 0.95
    noise_floor_override: float | None = None
    noise_floor_fallback: float = 0.05
    degenerate_denominator_epsilon: float = 1e-12
```

Degenerate-value rule (applies to every function): any statistic whose
denominator (variance, MSE, range) falls below
`degenerate_denominator_epsilon`, or whose inputs are constant, is emitted as
`None` with a named exclusion/degeneracy counter — never `inf`/`NaN`. All
outputs are JSON-safe by construction and tested for it.

Functions (plain sequences/arrays + row metadata in, JSON-safe dicts out):

- `noise_floor_from_replicates(honest_eval_rows) -> dict` — median
  within-`(build_key, opponent_variant_id)` target SD over groups with ≥2
  replicates (algorithm-inherent minimum); returns `{"noise_floor": float |
  None, "n_groups": int, "source": "honest_eval_replicates"}`. Resolution
  order used by callers: `noise_floor_override` → replicate-derived →
  `noise_floor_fallback`; the resolved value and source are recorded in the
  artifact.
- `per_opponent_rank_metrics(opponents, y_true, y_pred, noise_floor, config)`
  — for each opponent with ≥ `min_builds_per_opponent` test rows AND
  within-opponent target SD ≥ noise_floor: Spearman ρ, Kendall τ-b, sparse
  Kendall τ (targets quantized to noise-floor bins before τ-b, per the NAS
  predictor-evaluation literature), and top-fraction Kendall τ (τ-b over rows
  in the observed top `top_fraction` of that opponent's targets when ≥
  `min_top_fraction_rows` such rows exist, else `None`). Opponents whose
  **prediction** vector is constant (e.g. the `opponent_mean` comparator)
  yield `None` correlations and are counted separately. Output: per-opponent
  table + aggregate mean/median over non-`None` values + counters:
  `included_opponents`, `excluded_low_variance`, `excluded_small_n`,
  `null_prediction_degenerate`.
- `build_aggregate_rank_metrics(builds, opponents, y_true, y_pred,
  degenerate_opponents, k_values, config)` — `degenerate_opponents` is the
  fixed set computed once per (split, seed) from the full test panel
  (within-opponent SD < noise floor); not recomputed per bootstrap resample.
  Aggregate observed and predicted targets per build = mean over that
  build's non-degenerate test opponents; builds with <
  `min_opponents_per_build` contributing opponents are excluded and counted.
  Output: Spearman, Kendall τ-b, `precision_at_k` (|top-k pred ∩ top-k
  true| / k), `regret_at_k` (best true aggregate − best true aggregate among
  top-k predicted; raw always; normalized-by-range emitted as `None` when the
  observed range is degenerate) for each k in `k_values`; per-build panel
  sizes (min/median/max) in the output. Top-k tie-break is deterministic:
  descending value, then ascending build key.
- `skill_scores(y_true, y_pred, train_target_mean)` — `1 −
  MSE(pred)/MSE(train-mean predictor)` plus both MSEs; `None` skill when the
  denominator MSE is degenerate.
- `panel_target_stats(y_true)` — n, mean, SD, endpoint mass (fractions
  exactly −1.0 / +1.0).
- `two_way_cluster_bootstrap(builds, opponents, y_true, y_pred, noise_floor,
  degenerate_opponents, primary_k, config)` — computes CIs for the four
  headline statistics (mean per-opponent Spearman, build-aggregate Spearman,
  precision@primary_k, regret@primary_k) via pigeonhole-style resampling
  that never feeds duplicated rows into a rank statistic (duplication
  manufactures ties and biases ρ/τ downward):
  1. Each resample draws a multiset **B\*** of builds and a multiset **O\***
     of opponents, both with replacement, seeded by `bootstrap_seed` +
     resample index.
  2. Mean per-opponent Spearman: for each opponent **copy** in O\*
     (multiplicity acts as a weight in the outer mean — legitimate at the
     aggregation level), compute ρ over the rows of that opponent restricted
     to `set(B*)`, each distinct build once; copies failing the
     min-n/variance/prediction-degeneracy guards drop out; statistic = mean
     of surviving values.
  3. Build-aggregate statistics: for each **distinct** build in `set(B*)`,
     aggregate = mean of its non-degenerate-opponent targets weighted by
     O\* multiplicity; rank statistics and precision/regret at caller-passed
     k over distinct builds with the deterministic tie-break.
  4. CI = percentile interval at `bootstrap_ci_level` over resamples with a
     finite statistic; the finite-resample count is reported.
  Known property, recorded in the spec: rank statistics on distinct clusters
  are mildly anti-conservative on the duplicated axis; CIs are descriptive
  spread, not calibrated SEs, and reports must present them as such.
- `honest_eval_build_metrics(...)` — H3 remedy: build-mean rank correlations
  (Spearman/Kendall), precision@k with chance level k/n alongside, regret@k,
  overlap curve over all k = 1..n, build-level bootstrap CIs, degenerate
  opponents excluded from aggregates, and `outer_train_build_overlap` (count
  of honest-eval builds whose build_key appears in outer-train — records that
  this diagnostic is NOT clean holdout).

### S2 — Splits and seed policy (`src/starsector_optimizer/phase7_matchup_data.py`)

- `BURNED_SPLIT_SEEDS: frozenset[int] = frozenset({17})` — single owner of
  the C4 seed-retirement rule. Both analysis scripts and batch validation
  reject burned seeds for outer splits with an error naming C4.
- `component_vocabulary(build) -> tuple[str, ...]` — slot-agnostic tokens
  `weapon:<id>` and `hullmod:<id>`. Hull ID and flux values excluded
  (single-hull DB; flux is numeric, not vocabulary). Relationship to the
  baseline script's slot-qualified `_component_tokens` (kept for
  k-combination overlap diagnostics) is documented in spec 31: two distinct,
  named component-key definitions with different jobs.
- `held_out_component_vocabulary_split(rows, build_lookup, holdout_fraction,
  max_overshoot_fraction, seed) -> ComponentVocabularySplit` where
  `ComponentVocabularySplit` is a frozen dataclass `{split: SplitIds,
  held_out_components: tuple[str, ...], realized_test_fraction: float}`.
  Algorithm: shuffle vocabulary with `random.Random(seed)`; move components
  into the held-out set one at a time; after each addition recompute the
  test-row set (rows whose build contains ≥1 held-out component); stop when
  test rows ≥ `holdout_fraction` of all rows. Invariant: no train build
  contains any held-out component. Raise `ValueError` when the vocabulary is
  exhausted first, when train or test ends empty, or when
  `realized_test_fraction > holdout_fraction + max_overshoot_fraction`
  (unbounded overshoot would make the 10-seed panels incomparable and
  contaminate the seed aggregate). Callers in the experiment script convert
  these `ValueError`s into structured insufficiency artifacts (S4) so a
  deterministic bad draw cannot burn batch retries.
- **Delete** `held_out_component_combination_split` (C2: it is bijectively
  the build split; keeping two identical stressors misrepresents evidence).
  `component_fingerprint_json` stays — overlap diagnostics still use it.
- `grouped_kfold(rows, groups, n_folds, seed) -> tuple[SplitIds, ...]` —
  shuffle unique groups with `random.Random(seed)`, deal round-robin into
  `n_folds` folds; fold i = test, rest = train. Raise `ValueError` if
  `n_folds < 2`; return `()` if unique groups < `n_folds` (caller emits
  `insufficient_inner_groups`, matching the existing spec behavior).

### S3 — Baseline script (`scripts/analysis/phase7_baseline_surrogate.py`)

- `SPLIT_CHOICES`: replace `"component"` with `"component-vocab"`; dispatch
  in `_split_rows` uses the new split and threads
  `held_out_components`/`realized_test_fraction` into split metadata.
- Seed policy: `DEFAULT_RANDOM_SEED` changes 17 → 101 (first bank seed);
  `--seed` values in `BURNED_SPLIT_SEEDS` raise `ValueError` naming C4.
- `BaselineConfig` gains `eval_metrics: EvalMetricsConfig` and
  `component_vocab_max_overshoot: float` fields; CLI exposes
  `--noise-floor-override` and `--component-vocab-max-overshoot`.
- `split_metadata`: new `component-vocab` entry (group key =
  `component_vocabulary_membership`, claim = "Transfer to builds containing
  weapon/hullmod IDs never seen in training", component key definition =
  `slot_agnostic_weapon_and_hullmod_vocabulary`).
- `split_overlap_counts`: new `component_vocabulary` count — number of
  held-out component IDs appearing in any train build (must be 0); this is
  the machine-checked leakage gate for the new split.
- `stratified_metrics`: add an `opponent_variant_id` stratum (per-opponent
  pooled regression metrics — the C1 stratum the review names).
- `run_one`: emit `rank_metrics` (S1 per-opponent + build-aggregate),
  `skill_scores`, `panel_target_stats`, and `noise_floor` resolution
  alongside the existing pooled metrics (pooled metrics stay — demoted, not
  removed; reports interpret them per C1).

### S4 — Learned experiment script (`scripts/analysis/phase7_learned_surrogate_experiment.py`)

- **Inner CV (M1)**: replace the single `inner_validation_split` with
  `inner_cv_folds` inner train/validation pairs built from outer-train rows
  only, matched to the outer stressor. New config field `inner_cv_folds:
  int = 3`. Fold construction by split type: grouped splits (build,
  opponent, opponent-hull, opponent-family, seed-cell) use `grouped_kfold`
  seeded `hpo_seed` — `insufficient_inner_groups` when it returns `()`;
  component-vocab is not a row partition, so it uses `inner_cv_folds`
  independent vocabulary-holdout draws within outer-train, draw i seeded
  `hpo_seed + i`, holdout fraction = the outer `holdout_fraction`, same
  overshoot bound; forward-time uses rolling-origin — `inner_cv_folds`
  ordered prefix/suffix origins within the outer-train prefix. HPO selection
  = minimize **mean** inner RMSE across folds (tie-breakers unchanged,
  computed on fold means).
- **Aligned model seeds (M1)**: every trial fit AND the final refit use the
  same model seed (`hpo_seed`); drop the `hpo_seed + idx` per-trial offset so
  the selected config's inner scores were produced under its shipping seed.
- **Outer dispatch + metadata**: component branch replaced by
  component-vocab in `run_one`/`baseline._split_rows` usage,
  `hierarchy_scorecard`'s three hardcoded dicts, `leakage_diagnostics`'
  forbidden-key map (component-vocab → `component_vocabulary` overlap
  count), and the `component_key_definition` value. Split-construction
  `ValueError`s (degenerate vocab draw) are caught and emitted as
  `_insufficient_result(config, "degenerate_component_vocab_split")`.
- **Inline comparators (C3)**: fit the six comparator-gate models (from the
  `baseline` module) on the job's exact outer split; record per-comparator
  metrics (pooled + rank), `best_comparator` (min RMSE among comparators
  with finite RMSE), `delta_vs_best_comparator`, and
  `delta_vs_matched_family` — matched family defined ONLY where a natural
  analog exists: `random_forest_tuned` → `random_forest`,
  `sparse_pairwise_ridge` → `ridge_hybrid`, `catboost_regressor` → `null`
  (headline is `delta_vs_best_comparator`; no more silent CatBoost→RF
  matching). **Delete** `load_comparator_context`, `_comparator_max_rows`,
  `_comparator_feature_profile`, `_comparator_feature_schema`,
  `_metric_delta`, the `--comparator-json` flag, and
  `LearnedExperimentConfig.comparator_json_path`: comparability is
  guaranteed by construction, not matched against a stale seed-17 artifact.
  Residual caveat recorded in spec: comparators run at fixed defaults while
  learned families get 24 HPO trials — `delta_vs_best_comparator` is a
  floor comparison, not a tuned-family comparison.
- **Primary-metric re-designation (C1/H3)**: `DEFAULT_PROMOTION_METRIC` =
  `"mean_per_opponent_spearman"` (outer-test); the honest-eval diagnostic's
  primary readout becomes build-aggregate Spearman with CI (top-k recall
  retained as a secondary continuity metric, reported with chance level).
  `claim_boundary` carries the new names; `primary_top_k` keeps governing
  precision/regret k.
- **Seed policy**: `DEFAULT_SPLIT_SEED` 17 → 101; `--split-seed` in
  `BURNED_SPLIT_SEEDS` raises `ValueError` naming C4.
- **Outer-split lineage (C4)**: new artifact object `outer_split_lineage`:
  `{split_seed, seed_bank_label, confirmatory_reserved_seed,
  reused_partition}` — `reused_partition: true` for forward-time (its
  deterministic partition absorbed all four burned waves; seeds cannot fix
  it, so reports must caveat it) and for any seed with prior-wave history.
  Reserved confirmatory seed = **151** (not in the 10-seed run bank; never
  run until a promotion-grade claim needs it).
- **Schema v2**: `EXPERIMENT_SCHEMA_VERSION = 2`. Per-result additions:
  `rank_metrics`, `skill_scores`, `panel_target_stats`, `noise_floor`,
  `comparator_inline`, `inner_cv` (fold count, per-fold sizes, per-trial
  mean/SD across folds), `outer_split_lineage`, bootstrap CIs inside
  `rank_metrics`.
- **Honest-eval diagnostic (H3)**: extend with `honest_eval_build_metrics`
  output.
- **L1 fixes**: `DEFAULT_FINAL_REFIT_POLICY =
  "fit_outer_train_only_no_deployment_artifact"`; stop emitting the constant
  `feature_schema_version` as a feature column (provenance-only).
- CLI: `--inner-cv-folds`, `--noise-floor-override`,
  `--bootstrap-resamples`, `--component-vocab-max-overshoot`;
  `--comparator-json` removed.

### S5 — Batch orchestration (`src/starsector_optimizer/phase7_learned_batch.py`, `scripts/cloud/phase7_learned_batch.py`, example YAMLs)

- `CANONICAL_SPLITS`: `component` → `component-vocab`.
- `CANONICAL_SPLIT_SEED_BANK: tuple[int, ...] = (101, 103, 107, 109, 113,
  127, 131, 137, 139, 149)` — single owner; YAML `split_seeds` must equal
  the bank when `publish_canonical: true`, must be non-empty, disjoint from
  `BURNED_SPLIT_SEEDS`, and exclude the reserved confirmatory seed 151.
  `RESERVED_CONFIRMATORY_SEED = 151` lives beside the bank.
- Config: `split_seeds: tuple[int, ...]` replaces the single `split_seed`;
  new `inner_cv_folds`, `noise_floor_override`, `bootstrap_resamples`,
  `component_vocab_max_overshoot` pass-throughs. `final_refit_policy`
  default/fallback strings updated in `LearnedBatchConfig`,
  `load_batch_config`, and both YAMLs (validate_job_payload string-compares
  config↔artifact, so every copy must move together).
- Job matrix: `splits × models × split_seeds`, except `forward-time`
  (deterministic → 1 instance per model, job id `{split}__{model}`); seeded
  job ids are `{split}__{model}__s{seed}`. Canonical full matrix: 6 × 3 ×
  10 + 1 × 3 = **183 jobs**.
- Lease payload + worker userdata: carry `split_seed` per job; the worker
  command uses the leased seed. `expected_provenance` in
  `validate_job_payload` takes the seed from the job spec, not batch-wide;
  `_common_key` drops `split_seed` (per-job identity now) and
  `comparator_json_path` (deleted) and gains the new pass-through fields;
  artifact seeds are validated ∈ config `split_seeds`.
- Bundle (`scripts/cloud/phase7_learned_batch.py`): remove
  `comparator_json_path` from `bundle_paths()` and its tests. `local_smoke`
  additionally bounds the new heavy knobs (`--bootstrap-resamples`,
  `--inner-cv-folds 2`) alongside its existing `--max-rows/--hpo-trials`
  overrides.
- Validation: `1 ≤ target_workers ≤ job_count` (queue drains; equality no
  longer required); `min_workers_to_start == target_workers` and the
  region-divisibility constraint (`target_workers % len(regions) == 0`) are
  **retained** — they govern provisioning, not the job matrix.
  `publish_canonical: true` requires the full splits × models × seed-bank
  matrix, and the canonical publish refuses to overwrite an existing
  canonical file whose `batch_name` differs (protects the 2026-05-12 v1
  artifact — its evidence is superseded-in-reading but must remain on disk).
- Canonical output path: NEW dated path
  `data/phase7/learned_surrogate_full_v2_2026-07.json` in the YAML; spec 31's
  hardcoded 2026-05-12 path is amended to state the path is config-owned and
  dated per wave.
- Contract: `_contract_ok`/`validate_job_payload` require
  `experiment_schema_version == 2` and the schema-v2 objects
  (`rank_metrics`, `skill_scores`, `panel_target_stats`,
  `comparator_inline`, `inner_cv`, `outer_split_lineage`).
- Merge: `merge_job_artifacts` groups results by `(split, model)` and adds a
  `seed_aggregate` block — per-metric mean, SD, min/max, and n_seeds over
  the headline metrics (pooled Spearman/RMSE, mean per-opponent Spearman,
  build-aggregate Spearman, precision@k, regret@k, skill score); SD is
  `null` when n_seeds == 1 (forward-time). The spec labels `seed_aggregate`
  as descriptive spread over overlapping resplits — not a calibrated SE
  (Nadeau–Bengio); reports must not divide it by √n.
- Update `examples/phase7-learned-batch.yaml` (seed bank, new fields, new
  canonical path) and `examples/phase7-learned-batch-smoke.yaml` (2 bank
  seeds, 1 split, 1 model).

### S6 — Spec 31 amendment (FIRST, before tests/implementation)

Rewrite the affected sections: split-builder signatures and claim table
(component-vocab replaces component; both component-key definitions named;
grouped_kfold added), seed policy (burned seeds, bank, reserved confirmatory
seed 151, outer_split_lineage object, forward-time reused-partition caveat),
baseline metrics section (rank metrics with degenerate-value rules, skill
scores, panel stats, per-opponent stratum, noise floor — including the
explicit carve-out that evaluation-side noise-floor derivation from
honest-eval replicate targets is permitted usage: targets define evaluation
resolution, are never fitted on, and the derivation is recorded in the
artifact), nested-validation procedure (inner CV folds by split type,
aligned seeds), comparator-inline contract (external comparator JSON
removed; tuning-budget-asymmetry caveat), bootstrap procedure (pigeonhole
semantics above, descriptive-CI framing), artifact schema v2 object list,
promotion-metric re-designation, batch job-matrix/seed-bank/target_workers/
publish/canonical-path rules, and merge homogeneity (common across artifacts
= everything except split/model/seed; seeds ∈ bank).

### S7 — Docs

- `docs/roadmap.md`: mark item 1 in progress → done at retirement; absorb
  follow-ups surfaced by review: M2's leakage diagnostics parked under
  item 2's ablation wave (our assignment — the review left them unowned),
  H1's two-part censored model added to Deferred (no current owner).
- `docs/project-overview.md` file↔spec map: add `phase7_eval.py`.
- No new report in this plan — the re-run report is written after the AWS
  batch completes (empirical-report skill governs it).

## Out of scope (deferred — with owners)

- Feature-profile ablations (item 2), FM/bilinear features (item 3),
  pairwise-ranking CatBoost (item 4), prequential replay (item 5),
  lockbox/Ladder acceptance + per-wave model-info sheets (item 6),
  opponent-panel data wave (item 7).
- H1 two-part censored model — no roadmap owner today; added to roadmap
  Deferred in S7.
- M2's four unimplemented leakage diagnostics (adversarial AUC,
  nearest-neighbor overlap, rare-combination overlap, sparse-ID ablation) —
  not in item 1's list; parked under item 2 in S7. Artifacts keep declaring
  them `not_applicable`.
- Comparator tuning-budget asymmetry (C3 residue) — named caveat in spec;
  a tuned-comparator arm would triple comparator cost and belongs to a
  later wave if a family claim ever needs it.
- Optuna inner HPO, LightGBM/XGBoost (spec 31 already defers).
- L1 report-text sub-findings (RF-80-vs-200, delta-label wording) — live in
  superseded reports; corrections would rewrite dated evidence.

## Critical files

| File | Change |
|---|---|
| `docs/specs/31-phase7-matchup-data.md` | amend contract (S6) |
| `src/starsector_optimizer/phase7_eval.py` | new (S1) |
| `src/starsector_optimizer/phase7_matchup_data.py` | vocab split, grouped_kfold, seed policy, delete old component split (S2) |
| `scripts/analysis/phase7_baseline_surrogate.py` | dispatch, seed policy, strata, leakage count, rank metrics in output (S3) |
| `scripts/analysis/phase7_learned_surrogate_experiment.py` | inner k-fold, aligned seeds, inline comparators, promotion metric, lineage, schema v2, L1 fixes (S4) |
| `src/starsector_optimizer/phase7_learned_batch.py` | seed matrix, bank constants, contract v2, provenance/merge changes, publish guard (S5) |
| `scripts/cloud/phase7_learned_batch.py` | bundle drops comparator JSON; local-smoke bounds new knobs (S5) |
| `examples/phase7-learned-batch.yaml`, `examples/phase7-learned-batch-smoke.yaml` | seed bank, new fields, new canonical path (S5) |
| `src/starsector_optimizer/matchup_features.py` | stop emitting `feature_schema_version` as a feature column (S4/L1) |
| `tests/test_phase7_eval.py` | new |
| `tests/test_phase7_matchup_data.py` | new split + kfold + seed-policy tests; delete old component-split tests |
| `tests/test_phase7_baseline_surrogate.py` | dispatch/strata/metrics/metadata tests (old component metadata assertions removed) |
| `tests/test_phase7_learned_surrogate_experiment.py` | inner-CV/seed/comparator/payload/lineage tests |
| `tests/test_phase7_learned_batch.py` | seed matrix/contract/provenance/merge/publish-guard/bundle tests |
| `tests/test_matchup_features.py` | feature-column change test |
| `docs/roadmap.md`, `docs/project-overview.md` | grooming (S7) |

## Public concepts and canonical owners

- Evaluation metric definitions + degenerate-value rules: spec 31
  (normative), `phase7_eval.py` (implementation). Empirical readings: future
  re-run report only.
- Seed policy (burned seeds / bank / reserved seed): `BURNED_SPLIT_SEEDS` +
  `CANONICAL_SPLIT_SEED_BANK` + `RESERVED_CONFIRMATORY_SEED` constants,
  specified in spec 31; scripts and batch validation both reference the
  constants (single source of truth).
- The review report stays the rationale owner; the spec owns the contract.

## Step-by-step implementation sequence

1. Spec 31 amendment (S6). Run `uv run python scripts/validate_docs.py`.
2. Tests for S1 + S2 (`test_phase7_eval.py`, `test_phase7_matchup_data.py`)
   — write failing, then implement `phase7_eval.py` and
   `phase7_matchup_data.py` changes; run the two test files.
3. Tests for S3 + S4 (+ `test_matchup_features.py` change), then implement
   the two analysis scripts + feature-column fix; run their test files.
4. Tests for S5, then implement batch + cloud-CLI changes + YAMLs; run
   their test files.
5. Full suite `uv run pytest tests/ -v`; design-invariants mechanical
   checks; stale-reference greps: `held_out_component_combination_split`,
   `load_comparator_context`, `comparator_json`,
   `refit_selected_model_on_all_training_rows_after_selection`,
   `"component"` split choice, `DEFAULT_SPLIT_SEED`, `split_seed: 17`.
6. Docs (S7) + doc-grooming triggers + `validate_docs.py`.
7. Post-impl audit (3 sub-agents per post-impl-audit skill); fix findings;
   regression test.
8. Local smoke: `scripts/cloud/phase7_learned_batch.py local-smoke` with the
   smoke YAML — validates the end-to-end job command and schema-v2 artifact
   contract without AWS.
9. **Launch checkpoint (user)**: present the final job count and cost
   estimate (183 jobs; prior anchor ≈$0.24/job at 24 single-fold trials —
   3-fold inner CV and inline comparators raise per-job cost ≈2–3×, so
   estimate ≈$90–130; knobs: `hpo_trials`, seed count) and get approval
   before `launch`. Predeclaration for the re-run: it is **exploratory
   harness validation** — no model-family promotion claim will be made from
   it; any future promotion-grade claim requires a predeclared family +
   endpoint and the reserved confirmatory seed (151). Then run the AWS batch
   per cloud-worker-ops SOP (source `.env` first), monitor, `merge`, and
   hand the merged artifact to the item-1 re-run report (separate task after
   this plan).

## Tests and mechanical gates

- New behavior → test mapping is 1:1 per spec requirements. Error/edge
  paths: invalid fractions; `n_folds < 2`; vocabulary exhaustion; empty
  partitions; overshoot bound exceeded; burned-seed rejection (both scripts
  + batch config); seed bank vs reserved-seed disjointness;
  `publish_canonical` with subset matrix; canonical-path overwrite guard;
  schema-v1 artifact rejection at merge; per-job seed provenance validation;
  bootstrap determinism (same seed → same CI); degenerate-opponent
  exclusion; constant-prediction `None` correlations; degenerate-denominator
  `None` skill/normalized-regret (JSON-safe — `json.dumps` round-trip test
  on payloads containing every degenerate case); min-panel exclusions;
  `insufficient_inner_groups`; `degenerate_component_vocab_split`
  insufficiency artifact.
- Full suite must pass; pre-commit hook (validate_active_plans,
  validate_docs, manifest gate — no Java/game-data changes here, so no
  manifest regen).

## Review findings and dispositions

Fresh-eye review (3 agents, 2026-07-11) returned 2 blockers, 9 major, 17
minor findings. All valid findings dispositioned by plan revision:

- **Blocker: degenerate-panel numerics (inf/NaN skill scores, zero-range
  regret)** → S1 degenerate-value rule + `degenerate_denominator_epsilon`
  config + JSON-safety tests.
- **Blocker: bootstrap row-duplication corrupts rank statistics** → S1
  bootstrap re-specified: multiplicity as aggregation weights / distinct
  clusters inside rank statistics; anti-conservatism + descriptive-CI
  framing recorded in spec.
- **Major: promotion metric never re-designated (C1's central point)** → S4
  primary-metric re-designation.
- **Major: canonical publish path collides with v1 artifact** → S5 new dated
  path + overwrite guard + spec amendment.
- **Major: seed 17 stays script default; enforcement batch-only** → S2
  `BURNED_SPLIT_SEEDS` single owner; script defaults → 101; rejection in
  both scripts + batch.
- **Major: C4 sub-remedies silently narrowed** → reserved confirmatory seed
  151 + `outer_split_lineage` artifact object added to scope; forward-time
  `reused_partition` annotation.
- **Major: batch provenance/merge coupling (split_seed in
  expected_provenance/_common_key; comparator path in bundle)** → S5
  explicit changes.
- **Major: component-vocab had no machine-checked leakage gate** → S3
  `component_vocabulary` overlap count + S4 forbidden-key mapping.
- **Major: vocab overshoot unguarded; degenerate draws burn batch retries**
  → S2 overshoot bound + S4 insufficiency-artifact conversion.
- **Major: unbalanced per-build panels in build aggregates** → S1
  `min_opponents_per_build` + panel-size reporting.
- **Major: constant-prediction NaN per-opponent correlations
  (opponent_mean)** → S1 prediction-degeneracy counter + `None` handling.
- **Minor (all adopted)**: CANONICAL_SPLITS update; baseline-test metadata
  updates; `final_refit_policy` string in all four locations + grep;
  local-smoke bounds new knobs; `phase7_eval` layering (k as parameter);
  CatBoost matched-family = null; forward-time job id + null SD in
  seed_aggregate; `min_top_fraction_rows` config field; inner-CV seeds and
  fractions pinned; region-divisibility constraint retained explicitly;
  seed-bank single constant + YAML validation; seed_aggregate labeled
  descriptive (Nadeau–Bengio); degenerate-opponent set fixed per
  (split, seed); deterministic top-k tie-break; two component-key
  definitions named and related in spec; M2 deferral justification corrected
  (parked on roadmap, our assignment); H1 ownership corrected (roadmap
  Deferred); noise-floor honest-eval-usage carve-out in spec; L1 scope
  honestly stated (two fixed in scope, two report-text items out).
- **Not adopted**: none — every finding was valid at least in part.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-11 12:23
- Findings: Phases 1–4 self-review — two ambiguities found and fixed before
  fresh-eye launch (inner-CV semantics for the non-partition component-vocab
  split; vague "if needed" in the cloud-CLI file row). Magic-number audit
  clean after revision (all thresholds in `EvalMetricsConfig` / config
  fields / named constants).
- Dispositions: both fixed in plan text.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-11 12:23
- Agents:
  - Pattern Consistency: findings (12 — bundle/provenance/merge coupling,
    seed defaults, leakage wiring, error semantics, canonical splits, token
    schemes, smoke overrides, layering, matched family, forward-time ids)
  - Spec Alignment: findings (11 — promotion metric, canonical path,
    divisibility, M2/H1 ownership, C4/C3 sub-remedies, merge homogeneity,
    component surfaces, L1 scope, noise-floor carve-out)
  - Engineering & Design Invariants: findings (15 — degenerate numerics +
    bootstrap semantics blockers, min-panel guards, overshoot, seed-17
    duplication, magic numbers, determinism pins, descriptive-CI framing)
- Findings: see "Review findings and dispositions".
- Dispositions: all resolved by plan revision (v2 of this file); none
  rejected.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

Per `post-impl-audit` skill: full suite, mechanical checks, 3 audit
sub-agents (plan-vs-code, invariants, spec alignment), doc grooming step 9.

## Post-implementation audit record (2026-07-11)

Three parallel audit sub-agents (plan-vs-code, engineering/design
invariants, spec alignment) ran after the first full implementation pass.
All confirmed findings were fixed in-session before commit:

- **Blocker (spec agent, empirically confirmed): worker artifacts omitted
  `dependency_extra` from provenance** — every live-batch result would have
  been 409'd by the control plane. Fixed: the experiment script stamps
  `DEFAULT_DEPENDENCY_EXTRA`; constant moved to `phase7_matchup_data.py`.
- **Major (all three agents): insufficiency artifacts were rejected by
  `validate_job_payload`, so the deterministic-bad-draw protection did not
  work end-to-end** — fixed: `INSUFFICIENCY_STATUSES` accepted at result
  time (identity/provenance validated, completed-result checks skipped);
  `merge_job_artifacts` refuses to merge while any exist, naming the jobs.
- **Major: over-broad `except ValueError` mislabeled config errors as
  degenerate vocab draws** — fixed: dedicated `ComponentVocabularyError`;
  config errors propagate.
- **Major: feature-row column change without version bump** —
  `FEATURE_SCHEMA_VERSION` 3 → 4; spec documents v4 = v3 minus the
  schema-version column.
- **Major: replicate rows corrupted panel gates and aggregate weighting** —
  fixed: `_collapse_cells` collapses (build, opponent) replicates to cell
  means at every metric entry point; gates count distinct entities.
- **Minors adopted**: sample-SD (ddof=1) consistency with the noise floor;
  epsilon-thresholded correlation degeneracy; empty-input skill guard;
  isfinite comparator filter; dead `EvalMetricsConfig.top_k_values` removed;
  explicit `primary_k` for the honest-eval bootstrap (no min-k drift between
  scripts); shared contract constants single-owned in
  `phase7_matchup_data.py` (schema version, promotion metric, refit policy,
  dependency extra); `forward_time_order_key` shared between outer split and
  rolling-origin inner folds; inner vocab-draw failures logged before
  mapping to `insufficient_inner_groups`; vocabulary restricted to builds in
  rows; `component_vocabulary` overlap count emitted only for vocab splits;
  combined `opponent_family` stratum added; spec wording aligned (bounded
  precision@k, 183-job matrix, `comparator_delta` object, seed_aggregates
  naming, single-region note, skipped-model policy, reused_partition
  semantics, HPO fold-SD scope, progress-flag removal).
- **Not adopted**: none rejected outright; L1 report-text items remain out
  of scope per the plan.

Verification after fixes: full suite 992 passed / 1 pre-existing platform
skip; `validate_docs.py` clean; both local-smoke jobs re-run green under the
fixed provenance (see step 8).

## Retirement checklist

- [x] status: implemented, dates, commit hash (d39869c)
- [x] post_impl_audit result recorded
- [x] move to `.claude/plans/archive/2026/`
- [x] groom `docs/roadmap.md` (item 1 implementation delivered; the 183-job
      AWS re-run remains the open sub-item, cost-gated on the user)
