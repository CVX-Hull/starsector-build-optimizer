---
type: report
status: draft
last-validated: unvalidated
---

# Phase 7 Learned Surrogate Experiment

## Abstract

This draft records the Phase 7 learned-surrogate experiment state on
2026-05-12. The local full run completed all 15 canonical split/model jobs
against `data/phase7/wave1_matchups.sqlite`. CatBoost is the strongest RMSE
model on four of five splits; tuned random forest is strongest on the
held-out-opponent split. The held-out-opponent split remains weak in rank
signal, which is the main modeling concern for Phase 7.

The AWS fan-out path is now treated as infrastructure validation only. The
2-worker renewable-lease smoke completed and cleaned up all tagged resources,
but the local full artifact is the model-development authority for this report.
The full AWS config is disabled for execution unless a future operator
explicitly re-enables it for reproducibility testing.

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
`data/phase7/wave1_comparator_gate_2026-05-11.json`. The local full run is
comparable to that artifact because both use the full materialized DB. The
200-row local smoke is not comparable to the comparator artifact and reports
`comparison_status = row_filter_mismatch`.

### Estimators / Models

Producer script:
`scripts/analysis/phase7_learned_surrogate_experiment.py`.

Model families:

| Model | Implementation | Representation | Tuning policy |
|---|---|---|---|
| `random_forest_tuned` | scikit-learn `RandomForestRegressor` behind `DictVectorizer` | sparse mixed categorical/numeric feature dictionary | random search, 2 smoke trials / 24 full-run trials |
| `catboost_regressor` | CatBoost `CatBoostRegressor` | pandas frame with frozen training columns and native categorical feature names | random search, 2 smoke trials / 24 full-run trials |
| `sparse_pairwise_ridge` | scikit-learn `DictVectorizer` + `PolynomialCountSketch(degree=2)` + `Ridge` | sparse original features plus approximate pairwise interactions | random search, 2 smoke trials / 24 full-run trials |

Metrics are MAE, RMSE, and Spearman rho, implemented by the producer script
through the comparator helper metrics. Lower MAE/RMSE is better; higher
Spearman rho is better.

### Statistical-Learning Setup

Prediction target population: held-out matchup rows under grouped validation,
not final honest-eval build quality. Feature schema:
`FEATURE_SCHEMA_VERSION = 2`, owned by
`docs/specs/31-phase7-matchup-data.md`.

Outer partition: one of the five configured split families (`build`,
`opponent`, `component`, `seed-cell`, `forward-time`), using the corresponding
grouped or chronological holdout contract from spec 31 with
`holdout_fraction = 0.2` and `split_seed = 17`. Inner partition: the matching
inner split inside the outer training rows, using the same holdout fraction and
`hpo_seed = 23`. Model-selection criterion: minimize inner-validation RMSE,
then maximize Spearman rho, then minimize fit/predict runtime.

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

### Full-Run Execution

Local full-run artifact:
`data/phase7/learned_surrogate_full_local_2026-05-12.json`.

The run used the canonical 15-job matrix: five splits (`build`, `opponent`,
`component`, `seed-cell`, `forward-time`) crossed with three learned model
families. HPO settings were `hpo_trials = 24`, `hpo_jobs = 4`,
`model_thread_count = 4`, `split_seed = 17`, and `hpo_seed = 23`.

AWS batch execution was authorized by
`.claude/plans/active/2026-05-12-phase7-aws-learned-batch.md` for smoke
validation. A full cloud relaunch is no longer needed for the Phase 7 modeling
decision after the local full run completed.

AWS quota and instance policy checked on 2026-05-12:

- Spot quota `L-34B43A08`: 640 standard spot vCPU in `us-east-1` and
  640 standard spot vCPU in `us-east-2`.
- Current batch configs use `us-east-1` only. Multi-region learned-batch
  configs are rejected until replacement provisioning supports explicit
  per-region allocation.
- Instance types in the full config: `c7i.4xlarge`, `c7a.4xlarge`,
  `c6i.4xlarge`, `c6a.4xlarge`, `m7i.4xlarge`, and `m7a.4xlarge`.
- Full target workers: 15, for 240 vCPU at full size.
- Smoke target workers: 2, for 32 vCPU.
- Per-worker parallelism: `hpo_jobs = 4`, `model_thread_count = 4`.
- Hard budget: `$20.00`; max lifetime: 2 hours.
- Both checked-in AWS configs now set `execution_enabled: false`.

The full batch merge is not allowed to overwrite
`data/phase7/learned_surrogate_full_2026-05-12.json` until all 15 artifacts
validate with matching schema, provenance, source DB, comparator context,
top-k settings, dependency extra, bundle hash, and leakage checklist. Partial
AWS outputs are batch-internal diagnostics only.

### Comparison Statistics

The local full run is comparable to
`data/phase7/wave1_comparator_gate_2026-05-11.json` because both use the full
materialized DB. `comparator_delta` is computed as learned metric minus
comparator metric; lower MAE/RMSE deltas are better, higher Spearman deltas
are better.

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

### Local Full Run

**Method (§Methods).** Full uncapped learned-surrogate run over all canonical
split/model pairs.

**Statistic (§Methods).** MAE, RMSE, and Spearman rho on the outer test
partition. Comparator deltas use learned minus matching comparator.

**Threshold (§Diagnostics & Thresholds).** No pass/fail model-quality threshold
is asserted. The run must complete all 15 canonical split/model rows with clean
schema, provenance, comparator context, and leakage checks.

| Split | Best RMSE model | MAE | RMSE | Spearman rho | RMSE delta vs comparator | Rho delta vs comparator |
|---|---|---:|---:|---:|---:|---:|
| `build` | `catboost_regressor` | 0.212694 | 0.341793 | 0.819951 | -0.012423 | +0.013344 |
| `component` | `catboost_regressor` | 0.214998 | 0.353585 | 0.818841 | -0.006589 | +0.003702 |
| `seed-cell` | `catboost_regressor` | 0.214573 | 0.344720 | 0.810472 | -0.014942 | +0.007021 |
| `forward-time` | `catboost_regressor` | 0.232880 | 0.374717 | 0.798650 | -0.006224 | +0.008869 |
| `opponent` | `random_forest_tuned` | 0.454179 | 0.583311 | 0.261427 | -0.038852 | -0.006333 |

Interpretation: featureized learned models are useful for interpolation over
build/component/time/seed-cell stresses, with CatBoost the default next
baseline. Held-out-opponent generalization is still the weak point: RMSE
improves under tuned random forest, but rank signal remains low and does not
beat the comparator in Spearman rho.

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

### AWS Renewable-Lease Smoke

**Method (§Methods).** 2-worker AWS smoke over the held-out-build split and
two model families (`catboost_regressor`, `random_forest_tuned`) using the full
DB and full HPO settings from the batch config.

**Statistic (§Methods).** MAE, RMSE, Spearman rho on the outer test partition,
plus lease attempts and cleanup status from the batch status/final audit.

**Threshold (§Diagnostics & Thresholds).** Every planned smoke job must upload
a validated artifact, complete under renewable leases without duplicate
accepted results, merge only to batch-internal `merged.json`, and leave zero
tagged AWS resources after final audit.

| Model | Attempt | MAE | RMSE | Spearman rho | Cleanup |
|---|---:|---:|---:|---:|---|
| `catboost_regressor` | 1 | 0.213213 | 0.342093 | 0.820206 | final audit clean |
| `random_forest_tuned` | 1 | 0.214688 | 0.348235 | 0.814063 | final audit clean |

Reading: renewable leases fixed the false-abandonment failure seen in the
earlier smoke attempts. The smoke validates the AWS batch mechanics, not a new
model-selection conclusion; the local full run remains the authority for model
development.

## Synthesis & Decisions

The local full run is enough to guide modeling work: keep CatBoost and tuned
random forest, treat sparse pairwise ridge as noncompetitive in its current
form, and prioritize opponent representation/generalization before optimizer
integration.

AWS should be repaired incrementally only when the goal is infrastructure
reproducibility. The missing bundle dependency, renewable leases, and smoke
path have been validated; the full 15-worker AWS run should not be resumed for
this modeling decision because it would duplicate the completed local full run.

AWS failure chronology: the first smoke attempt found a missing bundled helper
script, the second exposed too-small retry accounting under Spot interruption,
and the third exposed fixed-duration leases as the wrong abstraction for
indeterminate model-training jobs. The final renewable-lease smoke results are
reported in the Results section above.

## Open Questions / Next Steps

- Treat `data/phase7/learned_surrogate_full_local_2026-05-12.json` as the
  Phase 7 learned-surrogate model-development artifact for this evidence pass.
- Recompute only if clean committed provenance becomes necessary for a later
  publication gate; do not spend AWS budget solely to duplicate this artifact.
- Keep AWS learned-batch configs disabled until a future infra-validation goal
  explicitly re-enables `execution_enabled`.
- The next code-level wave is feature schema v3: static `.ship` geometry,
  slot-arc pressure, built-in weapon aggregate parity, opponent parity
  features, and deterministic feature profiles for ablation. This is a
  substrate change only; it does not revise the v2 results in this report
  until a new local v3 experiment is run.
- The interrupted partial full-run artifact has been quarantined under
  `data/phase7/interrupted/` and must not be cited as full-run evidence.
- Run post-implementation audit with fresh-eye sub-agents before shipping.

## Appendix — File Map

- Producer script:
  `scripts/analysis/phase7_learned_surrogate_experiment.py`.
- AWS batch wrapper:
  `scripts/cloud/phase7_learned_batch.py`.
- AWS batch config:
  `examples/phase7-learned-batch.yaml`.
- Local full-run artifact:
  `data/phase7/learned_surrogate_full_local_2026-05-12.json`.
- AWS renewable-lease smoke artifact directory:
  `data/phase7/learned_surrogate_batch_smoke_retry3_2026-05-12/`. Its
  pre-fix `merged.json` advertises all canonical models; use the per-job
  artifacts and the report table above as the evidence record unless the
  batch-internal merge is regenerated with the patched schema.
- Canonical AWS full-run artifact:
  `data/phase7/learned_surrogate_full_2026-05-12.json` is not present and
  must not be inferred from the smoke.
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
