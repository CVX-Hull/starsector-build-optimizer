---
type: report
status: draft
last-validated: unvalidated
---

# Phase 7 Learned Surrogate Experiment

## Abstract

This draft records the first smoke execution of the Phase 7 learned-surrogate
runner. The smoke run completed all three planned model-family code paths on a
200-row capped held-out-build slice: tuned random forest, CatBoost regressor,
and sparse pairwise ridge. These numbers validate execution and reporting
plumbing only; they are not model-selection evidence because the comparator
artifact is full-data while this smoke run is row-capped. The full run is now
planned as a bounded AWS fan-out batch using one canonical `(split, model)` job
per worker.

## Methods

### Data

Unit of observation: one recovered training-log matchup row from
`training_matchups`. Source DB:
`data/phase7/wave1_matchups.sqlite`. Smoke filter: `--max-rows 200`, applied
before held-out split construction by the producer script. Target variable:
`training_matchups.target`, the per-opponent `hp_differential` recovered from
optimizer training logs. The smoke split produced `n_train = 166`,
`n_inner_train = 136`, `n_inner_validation = 30`, and `n_test = 34` for each
model family.

Comparator artifact:
`data/phase7/wave1_comparator_gate_2026-05-11.json`. That artifact was produced
without the 200-row cap, so all comparator deltas in this draft have
`comparison_status = row_filter_mismatch`.

### Estimators / Models

Producer script:
`scripts/analysis/phase7_learned_surrogate_experiment.py`.

Model families:

| Model | Implementation | Representation | Tuning policy |
|---|---|---|---|
| `random_forest_tuned` | scikit-learn `RandomForestRegressor` behind `DictVectorizer` | sparse mixed categorical/numeric feature dictionary | random search, 2 smoke trials |
| `catboost_regressor` | CatBoost `CatBoostRegressor` | pandas frame with frozen training columns and native categorical feature names | random search, 2 smoke trials |
| `sparse_pairwise_ridge` | scikit-learn `DictVectorizer` + `PolynomialCountSketch(degree=2)` + `Ridge` | sparse original features plus approximate pairwise interactions | random search, 2 smoke trials |

Metrics are MAE, RMSE, and Spearman rho, implemented by the producer script
through the comparator helper metrics. Lower MAE/RMSE is better; higher
Spearman rho is better.

### Statistical-Learning Setup

Prediction target population: held-out matchup rows under grouped validation,
not final honest-eval build quality. Feature schema:
`FEATURE_SCHEMA_VERSION = 2`, owned by
`docs/specs/31-phase7-matchup-data.md`.

Outer partition: `split = build`, using held-out build groups with
`holdout_fraction = 0.2` and `split_seed = 17`. Inner partition: held-out
build groups inside the outer training rows, using the same holdout fraction
and `hpo_seed = 23`. Model-selection criterion: minimize inner-validation
RMSE, then maximize Spearman rho, then minimize fit/predict runtime.

Leakage controls:

- outer-test targets are excluded from fitting and HPO;
- honest-eval targets are excluded from fitting, HPO, feature selection, and
  model-family selection;
- `build_key` and target-derived residual features are excluded from learned
  model feature vectors;
- honest-eval top-k recall is post-fit diagnostic only.

Run command:

```bash
uv run python scripts/analysis/phase7_learned_surrogate_experiment.py data/phase7/wave1_matchups.sqlite --split build --model all --max-rows 200 --hpo-trials 2 --top-k 1 --comparator-json data/phase7/wave1_comparator_gate_2026-05-11.json --output data/phase7/learned_surrogate_smoke_2026-05-12.json
```

Code provenance in the artifact:
`1780dadf4093da7237aa045e77444f365fdef336+dirty`.

The CLI enables progress output by default; the command omits only the
negative flag `--no-progress`.

### Full-Run Batch Plan

The full empirical run is authorized by
`.claude/plans/active/2026-05-12-phase7-aws-learned-batch.md`. The batch
matrix is 15 jobs: five canonical splits (`build`, `opponent`, `component`,
`seed-cell`, `forward-time`) crossed with three learned model families.

AWS quota and instance policy checked on 2026-05-12:

- Spot quota `L-34B43A08`: 640 standard spot vCPU in `us-east-1` and
  640 standard spot vCPU in `us-east-2`.
- Default region: `us-east-2`. Although `us-east-1` also has quota, the
  current provider floors worker count per region; a 15-worker batch across
  two regions would provision only 14 workers.
- Default instance types: `c7i.4xlarge`, then `c7a.4xlarge`.
- Target workers: 15, for 240 vCPU at full size.
- Per-worker parallelism: `hpo_jobs = 4`, `model_thread_count = 4`.
- Hard budget: `$20.00`; max lifetime: 2 hours.

The batch merge is not allowed to overwrite
`data/phase7/learned_surrogate_full_2026-05-12.json` until all 15 artifacts
validate with matching schema, provenance, source DB, comparator context,
top-k settings, dependency extra, bundle hash, and leakage checklist. Partial
AWS outputs are batch-internal diagnostics only.

### Comparison Statistics

This smoke report does not make formal comparator claims. The producer records
metric deltas against the comparator artifact, but every row is marked
`row_filter_mismatch` because the learned smoke run uses `max_rows = 200` and
the comparator artifact uses the full materialized DB.

### Diagnostics & Thresholds

Relevant gates:

- Spec 31 learned-baseline contract:
  `docs/specs/31-phase7-matchup-data.md`.
- Spec 22 one-shot AWS batch contract:
  `docs/specs/22-cloud-deployment.md`.
- Empirical-report standard:
  `docs/CONVENTIONS.md`.
- Active plan:
  `.claude/plans/active/2026-05-12-phase7-learned-baseline-experiment.md`.
- AWS batch plan:
  `.claude/plans/active/2026-05-12-phase7-aws-learned-batch.md`.

No pass/fail model-quality threshold is asserted in this draft.

## Results

### Smoke Execution

**Method (§Methods).** 200-row capped held-out-build smoke run with two HPO
trials per model.

**Statistic (§Methods).** MAE, RMSE, Spearman rho on the outer test partition.

**Threshold (§Diagnostics & Thresholds).** Execution must complete all planned
model-family paths and emit schema/provenance/comparator diagnostics.

| Model | Status | n train | n inner train | n inner val | n test | MAE | RMSE | Spearman rho | Comparator status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `random_forest_tuned` | completed | 166 | 136 | 30 | 34 | 0.300979 | 0.448068 | 0.739871 | row-filter mismatch |
| `catboost_regressor` | completed | 166 | 136 | 30 | 34 | 0.314903 | 0.475922 | 0.762153 | row-filter mismatch |
| `sparse_pairwise_ridge` | completed | 166 | 136 | 30 | 34 | 0.715848 | 0.938965 | 0.518616 | row-filter mismatch |

Reading: the runner exercises all planned model paths, including native
CatBoost categorical handling and the sparse pairwise ridge pipeline. The
row-capped data are too small for model selection; the only decision supported
here is that the smoke gate is operational.

## Synthesis & Decisions

The learned-surrogate producer passed its smoke gate once the implementation
changes are committed or rerun provenance is acceptable as `+dirty`. The AWS
batch wrapper has a passing dry-run and merge/control-plane unit coverage, but
live launch remains blocked until the serving loop, budget monitor, teardown,
status/result persistence, and final audit path are completed and audited.
Full-run evidence is still required before this report can ship or before the
active plan can be retired.

## Open Questions / Next Steps

- Complete and audit the AWS batch serving loop, then run the full experiment
  with all outer splits, all model families, `--hpo-trials 24`, and
  `--top-k 1,3,5`.
- Recompute the artifact after commit so code provenance points to the
  committed implementation without `+dirty`.
- The interrupted partial full-run artifact has been quarantined under
  `data/phase7/interrupted/` and must not be cited as full-run evidence.
- Use full-run comparator status, not smoke-run deltas, for model-development
  interpretation.
- Run post-implementation audit with fresh-eye sub-agents before shipping.

## Appendix — File Map

- Producer script:
  `scripts/analysis/phase7_learned_surrogate_experiment.py`.
- AWS batch wrapper:
  `scripts/cloud/phase7_learned_batch.py`.
- AWS batch config:
  `examples/phase7-learned-batch.yaml`.
- Raw data:
  `data/phase7/wave1_matchups.sqlite`.
- Smoke artifact:
  `data/phase7/learned_surrogate_smoke_2026-05-12.json`.
- Comparator artifact:
  `data/phase7/wave1_comparator_gate_2026-05-11.json`.
- Charts: none.
- Dependent reports:
  `docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md`,
  `docs/reports/2026-05-11-validation-to-phase7-roadmap.md`.
