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

The first AWS fan-out attempt did not produce model artifacts. EC2 console
output root-caused the failure: the worker bundle omitted
`scripts/analysis/phase7_baseline_surrogate.py`, which is imported by
`phase7_learned_surrogate_experiment.py` at startup. The next AWS step is a
2-worker smoke matrix, not a 15-worker relaunch.

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

### Full-Run Execution

Local full-run artifact:
`data/phase7/learned_surrogate_full_local_2026-05-12.json`.

The run used the canonical 15-job matrix: five splits (`build`, `opponent`,
`component`, `seed-cell`, `forward-time`) crossed with three learned model
families. HPO settings were `hpo_trials = 24`, `hpo_jobs = 4`,
`model_thread_count = 4`, `split_seed = 17`, and `hpo_seed = 23`.

AWS batch execution is authorized by
`.claude/plans/active/2026-05-12-phase7-aws-learned-batch.md`, but the full
cloud relaunch is gated on a smaller smoke config:
`examples/phase7-learned-batch-smoke.yaml`.

AWS quota and instance policy checked on 2026-05-12:

- Spot quota `L-34B43A08`: 640 standard spot vCPU in `us-east-1` and
  640 standard spot vCPU in `us-east-2`.
- Default region: `us-east-2`. Although `us-east-1` also has quota, the
  current provider floors worker count per region; a 15-worker batch across
  two regions would provision only 14 workers.
- Default instance types: `c7i.4xlarge`, then `c7a.4xlarge`.
- Full target workers: 15, for 240 vCPU at full size.
- Smoke target workers: 2, for 32 vCPU.
- Per-worker parallelism: `hpo_jobs = 4`, `model_thread_count = 4`.
- Hard budget: `$20.00`; max lifetime: 2 hours.

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

## Synthesis & Decisions

The local full run is enough to guide modeling work: keep CatBoost and tuned
random forest, treat sparse pairwise ridge as noncompetitive in its current
form, and prioritize opponent representation/generalization before optimizer
integration.

AWS should be repaired incrementally. The immediate fix is the missing bundle
dependency plus better worker diagnostics. The next cloud run should use
`examples/phase7-learned-batch-smoke.yaml` with two workers. Only after the
smoke produces validated artifacts should the full 15-worker batch resume.

Update from the first rebaked smoke attempt: the missing-bundle failure is
fixed. Both workers downloaded the bundle, ran `uv sync`, leased jobs, and
started experiments. The CatBoost job completed and uploaded a validated
artifact (`RMSE = 0.342093`, `Spearman rho = 0.820206`). The random-forest
worker was service-terminated before upload while its job remained leased, so
the batch could not retry immediately. The controller now requeues jobs whose
leased worker disappears from the active AWS set and provisions a replacement
worker for pending work.

Update from the second smoke attempt: the CatBoost job again completed and
uploaded a validated artifact (`RMSE = 0.342093`, `Spearman rho = 0.820206`;
artifact `data/phase7/learned_surrogate_batch_smoke_retry_2026-05-12/results/build__catboost_regressor.json`).
Both Spot instances were then service-terminated at
`2026-05-12T20:20:55Z`. The RF job had been re-leased to the completed
CatBoost worker shortly before that interruption, so the hardcoded two-attempt
controller policy marked it failed without any model-level failure event. The
retry budget is now explicit as `max_job_attempts`; both smoke and full configs
use six attempts before a job is treated as failed.

Update from the third smoke attempt: the RF job was re-leased while its
original worker was still active, because the controller treated a fixed
30-minute lease expiry as job abandonment. That is not a valid assumption for
HPO/model-training jobs. The smoke was stopped and audited clean. The batch
protocol now uses renewable leases: workers renew job ownership while the
model process is alive, and the controller requeues only after AWS worker loss
or missed renewals beyond `lease_grace_seconds`.

## Open Questions / Next Steps

- Commit the renewable-lease change, then rebake/update the AMI.
- Export `AWS_PROFILE`, `TAILSCALE_AUTHKEY`, and
  `STARSECTOR_WORKSTATION_TAILNET_IP`.
- Run clean smoke preflight without provisioning:
  `uv run python scripts/cloud/phase7_learned_batch.py launch --config examples/phase7-learned-batch-smoke.yaml`.
- Run the live smoke through the trap wrapper:
  `scripts/cloud/launch_phase7_learned_batch.sh --config examples/phase7-learned-batch-smoke.yaml`.
- If the smoke passes, run the live full experiment through the trap wrapper:
  `scripts/cloud/launch_phase7_learned_batch.sh --config examples/phase7-learned-batch.yaml`.
- Monitor `data/phase7/learned_surrogate_batch_2026-05-12/status.json` and
  `ledger.jsonl`; if interrupted, run `scripts/cloud/teardown.sh
  phase7-learned-batch-20260512` and `scripts/cloud/final_audit.sh
  phase7-learned-batch-20260512`.
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
