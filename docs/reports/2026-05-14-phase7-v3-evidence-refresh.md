---
type: report
status: superseded
last-validated: 2026-05-14
superseded-by: 2026-05-16-phase7-seven-split-evidence.md
supersedes:
  - 2026-05-11-phase7-matchup-surrogate-preliminary.md
  - 2026-05-12-phase7-learned-surrogate-experiment.md
---

# Phase 7 V3 Evidence Refresh

## Abstract

This report records the 2026-05-14 local Phase 7 evidence refresh under
feature schema v3 and the corrected spec-31 component fingerprint. The fixed
comparator ladder completed 30 split/model rows. The learned matrix completed
15 split/model rows in 4h09m. CatBoost is the best learned RMSE model on
held-out build, component, seed/cell, and forward-time splits. Tuned random
forest remains best on held-out opponent. Held-out-opponent transfer is still
the weak surface: tuned random forest improves RMSE slightly versus the v3
random-forest comparator (RMSE 0.586258 vs 0.591212; Spearman rho 0.245033
vs 0.216598), but rank signal remains low. The strongest non-opponent learned
RMSE is seed-cell CatBoost at 0.342288; the strongest component CatBoost RMSE
is 0.351108 with zero exact full-fingerprint train/test overlap. All
learned-surrogate claims in this report are exploratory because the existing
honest-eval ledger has already influenced model-development decisions.

This report supersedes the 2026-05-11 comparator report and the 2026-05-12
learned-surrogate draft for current-contract Phase 7 model evidence. Those
older reports remain historical v2 / legacy-component evidence.

## Methods

### Data

Unit of observation: one resolved `training_matchups` row from
`data/phase7/wave1_matchups.sqlite`, representing one
`(build_key, opponent_variant_id)` matchup from Wave 1 training logs.

Target variable: `training_matchups.target`, the per-opponent
`hp_differential` recovered from optimizer logs. Honest-eval rows are not fit
or tuned; they are used only for post-fit top-k diagnostics.

Feature schema: `FEATURE_SCHEMA_VERSION = 3`, feature profile `all`. This
includes aggregate build/opponent features, per-slot weapon/geometry features,
static `.ship` geometry, arc-pressure summaries, sparse component indicators,
and explicit matchup interactions. The learned artifact contains a merged
feature-family registry with 1,240 generated feature keys.

Training-log sample sizes by outer split:

| Split | Train N | Test N | Inner train N | Inner validation N |
|---|---:|---:|---:|---:|
| `build` | 17,075 | 4,287 | 13,636 | 3,439 |
| `component` | 17,059 | 4,303 | 13,625 | 3,434 |
| `forward-time` | 17,090 | 4,272 | 13,672 | 3,418 |
| `opponent` | 15,065 | 6,297 | 11,503 | 3,562 |
| `seed-cell` | 17,158 | 4,204 | 14,301 | 2,857 |

### Comparator Gate

Comparator artifact:
`data/phase7/wave1_comparator_gate_2026-05-14.json`.

Command:

```bash
uv run python scripts/analysis/phase7_baseline_surrogate.py \
  data/phase7/wave1_matchups.sqlite --split all --model all \
  --tree-count 80 --top-k 1,3,5 \
  > data/phase7/wave1_comparator_gate_2026-05-14.json
```

The comparator gate is a fixed scikit-learn ladder, not HPO:
`global_mean`, `opponent_mean`, `build_mean`, `twfe_additive`,
`ridge_hybrid`, and `random_forest`. The run emitted all five spec splits
crossed with all six comparator models, for 30 results.

### Learned Matrix

Learned artifact:
`data/phase7/learned_surrogate_v3_local_2026-05-14.json`.

Command:

```bash
uv run python scripts/analysis/phase7_learned_surrogate_experiment.py \
  data/phase7/wave1_matchups.sqlite \
  --split all --model all --top-k 1,3,5 \
  --comparator-json data/phase7/wave1_comparator_gate_2026-05-14.json \
  --honest-eval-usage exploratory_selection \
  --output data/phase7/learned_surrogate_v3_local_2026-05-14.json
```

Model families:

| Model | Implementation | HPO |
|---|---|---|
| `random_forest_tuned` | scikit-learn random forest over vectorized mixed features | random search, 24 trials |
| `catboost_regressor` | CatBoost native categorical regressor | random search, 24 trials |
| `sparse_pairwise_ridge` | count-sketched pairwise interactions plus ridge | random search, 24 trials |

Preprocessing and loss:

- `random_forest_tuned`: `DictVectorizer(sparse=True)` over schema-v3 feature
  records, `RandomForestRegressor(criterion="squared_error")`.
- `catboost_regressor`: pandas frame over schema-v3 feature records; string
  columns are categorical with missing category `"MISSING"`, numeric columns
  are coerced and zero-filled; CatBoost loss/eval metric is RMSE.
- `sparse_pairwise_ridge`: `DictVectorizer(sparse=True)`, identity features
  plus `PolynomialCountSketch(degree=2)`, then `Ridge`.

Default hyperparameters and HPO spaces:

| Model | Default | Random-search space |
|---|---|---|
| `random_forest_tuned` | `n_estimators=80`, `max_depth=None`, `min_samples_leaf=2`, `max_features=sqrt`, `bootstrap=True`, `max_samples=None` | `n_estimators in {200,400,800}`; `max_depth in {None,16,32,64}`; `min_samples_leaf in {1,2,4,8}`; `max_features in {sqrt,0.35,0.6,1.0}`; `max_samples in {None,0.65,0.85}` |
| `catboost_regressor` | `iterations=600`, `learning_rate=0.05`, `depth=6`, `l2_leaf_reg=3.0`, `random_strength=1.0`, `bagging_temperature=0.0` | `iterations in {300,600,1000}`; `learning_rate log-uniform [0.02,0.2]`; `depth in {4,6,8,10}`; `l2_leaf_reg log-uniform [1,30]`; `random_strength log-uniform [0.1,10]`; `bagging_temperature in {0,0.5,1,2}` |
| `sparse_pairwise_ridge` | `n_components=1024`, `alpha=10.0`, `degree=2`, `include_original_features=True` | `n_components in {512,1024,2048,4096}`; `alpha log-uniform [0.001,1000]`; `degree=2`; `include_original_features=True` |

Runtime settings: `hpo_jobs = 4`, `model_thread_count = 4`,
`hpo_seed = 23`, `split_seed = 17`, `holdout_fraction = 0.2`,
`train_fraction = 0.8`, and no row cap. Code provenance in the artifact is
`d05ce8f77811824fae44fe2171c112ca65553383+dirty` because this evidence pass
also fixed comparator diagnostic emission before the learned run completed.

The learned run uses a fixed matrix policy, not a single-winner promotion
policy. Claim boundary fields are: `claim_label = exploratory`,
`honest_eval_usage = exploratory_selection`, `primary_split = all`,
`primary_top_k = 1`, `promotion_metric = honest_eval_top_k_recall`, and
`deployment_artifact = none`.

HPO selection criterion is inner-validation RMSE, with Spearman rho and runtime
as tie-breakers in the producer. Random forest and CatBoost use model-specific
random-search spaces defined in the producer script; sparse pairwise ridge
tunes count-sketch/ridge parameters. Default-vs-tuned metrics are preserved in
the artifact for every learned result.

### Split Semantics

| Split | Claim |
|---|---|
| `build` | Transfer to unseen repaired player builds from the same broader build distribution. |
| `opponent` | Transfer to unseen exact opponent variants/builds. |
| `component` | Transfer away from selected full component fingerprints. |
| `seed-cell` | Transfer across campaign cells/proposal contexts. |
| `forward-time` | Predict later path-ordered optimizer proposals from earlier rows. |

The component split uses the current canonical full fingerprint:
`hull_id + slot_weapon_assignments + hullmods + flux_vents + flux_capacitors`.
The v3 comparator artifact records exact full-fingerprint overlap and
component-combination overlap at `k = 1, 2, 3`.

### Diagnostics & Thresholds

All result tables below are exploratory. No production promotion threshold is
claimed from this reused honest-eval ledger.

Primary statistical metrics are MAE, RMSE, and Spearman rho on outer held-out
training-log rows. Lower MAE/RMSE is better; higher Spearman rho is better.
Comparator deltas are learned minus matching v3 comparator, so negative RMSE
delta is better. Honest-eval top-k recall is post-fit diagnostic only.

Metric definitions:

```text
MAE = mean(|y_i - yhat_i|)
RMSE = sqrt(mean((y_i - yhat_i)^2))
Spearman rho = rank correlation between held-out targets and predictions
```

## Results

### V3 Comparator Gate

**Method (§Comparator Gate).** Fixed scikit-learn comparator ladder over
schema-v3 feature rows.

**Statistic (§Diagnostics & Thresholds).** Best comparator by RMSE per split,
plus honest-eval top-k diagnostic from the selected comparator.

**Threshold (§Diagnostics & Thresholds).** Exploratory baseline context only;
no learned-model promotion.

| Split | Best comparator | Train N | Test N | MAE | RMSE | Spearman rho | HE top-1 | HE top-3 | HE top-5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `build` | `random_forest` | 17,075 | 4,287 | 0.215851 | 0.351852 | 0.809154 | 1.000 | 0.333 | 0.600 |
| `component` | `random_forest` | 17,059 | 4,303 | 0.222362 | 0.364279 | 0.810146 | 1.000 | 0.333 | 0.400 |
| `forward-time` | `random_forest` | 17,090 | 4,272 | 0.238808 | 0.383786 | 0.789136 | 1.000 | 0.667 | 0.400 |
| `opponent` | `random_forest` | 15,065 | 6,297 | 0.452226 | 0.591212 | 0.216598 | 0.000 | 0.667 | 0.400 |
| `seed-cell` | `random_forest` | 17,158 | 4,204 | 0.220999 | 0.360472 | 0.804801 | 0.000 | 0.333 | 0.200 |

Reading: random forest remains the strongest fixed comparator on every split.
The corrected component split is harder than the old v2/legacy component
claim but still much closer to build/seed/forward transfer than to held-out
opponent transfer.

### Component Overlap Diagnostics

**Method (§Methods).** Component holdout uses the canonical full component
fingerprint and reports stricter train/test overlap diagnostics.

**Statistic (§Diagnostics & Thresholds).** Unique train/test counts, unique
overlap, and test-overlap fraction for exact fingerprints and component
combinations.

**Threshold (§Diagnostics & Thresholds).** Exact full-fingerprint overlap must
be zero for component-holdout transfer claims. k-combination overlap is a
diagnostic, not a failure condition.

| Diagnostic | Train unique | Test unique | Overlap unique | Test overlap fraction |
|---|---:|---:|---:|---:|
| Exact full fingerprint | 1,899 | 475 | 0 | 0.0000 |
| k=1 component combinations | 155 | 152 | 151 | 0.9934 |
| k=2 component combinations | 9,921 | 8,913 | 8,796 | 0.9869 |
| k=3 component combinations | 276,913 | 156,576 | 138,412 | 0.8840 |

Reading: the corrected component split has zero exact full-fingerprint
leakage. It still shares many lower-order components, which is expected for a
transfer-away-from-full-combinations test rather than a claim of transfer to
entirely novel component vocabularies.

### Learned Matrix

**Method (§Learned Matrix).** Fixed 5 split x 3 model-family learned matrix
with nested grouped HPO inside each outer split.

**Statistic (§Diagnostics & Thresholds).** Best learned model by RMSE per
split, with deltas against the matching v3 comparator.

**Threshold (§Diagnostics & Thresholds).** Exploratory model-development
evidence. Honest-eval diagnostics are not final promotion evidence.

| Split | Best learned model | Train N | Test N | MAE | RMSE | Spearman rho | RMSE delta | Rho delta | HE top-1 | HE top-3 | HE top-5 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `build` | `catboost_regressor` | 17,075 | 4,287 | 0.208988 | 0.339409 | 0.821211 | -0.012444 | +0.012057 | 1.000 | 0.333 | 0.600 |
| `component` | `catboost_regressor` | 17,059 | 4,303 | 0.217540 | 0.351108 | 0.821291 | -0.013170 | +0.011144 | 1.000 | 0.333 | 0.400 |
| `forward-time` | `catboost_regressor` | 17,090 | 4,272 | 0.228739 | 0.371156 | 0.805100 | -0.012630 | +0.015963 | 1.000 | 0.333 | 0.600 |
| `opponent` | `random_forest_tuned` | 15,065 | 6,297 | 0.449856 | 0.586258 | 0.245033 | -0.004954 | +0.028435 | 1.000 | 0.667 | 0.600 |
| `seed-cell` | `catboost_regressor` | 17,158 | 4,204 | 0.211647 | 0.342288 | 0.817363 | -0.018184 | +0.012562 | 0.000 | 0.333 | 0.400 |

Reading: CatBoost is the best current learned baseline for all non-opponent
splits. Tuned random forest remains best on the held-out-opponent split. The
opponent split improves slightly versus the v3 random-forest comparator, but
the absolute RMSE is still much worse than the other splits and Spearman rho
is only 0.245.

### Model-Family Pattern

**Method (§Learned Matrix).** Compare every learned model family across all
five outer splits.

**Statistic (§Diagnostics & Thresholds).** Outer-test RMSE.

**Threshold (§Diagnostics & Thresholds).** Exploratory family triage; no final
model selection claim.

| Split | `random_forest_tuned` | `catboost_regressor` | `sparse_pairwise_ridge` |
|---|---:|---:|---:|
| `build` | 0.348958 | 0.339409 | 0.424729 |
| `component` | 0.363055 | 0.351108 | 0.425645 |
| `forward-time` | 0.376890 | 0.371156 | 0.431898 |
| `opponent` | 0.586258 | 0.652268 | 2.020345 |
| `seed-cell` | 0.354932 | 0.342288 | 0.410643 |

Reading: the current sparse-pairwise ridge design is not competitive. It is
especially unstable on held-out opponents. The next sparse-interaction attempt
should change representation or regularization rather than treating this
implementation as a near miss.

## Synthesis

The current evidence supports three decisions:

1. Keep CatBoost as the default learned tabular baseline for schema-v3
   non-opponent transfer.
2. Keep tuned random forest as the held-out-opponent baseline until opponent
   representation improves.
3. Do not integrate a learned surrogate into optimizer selection yet. The
   held-out-opponent split remains the primary failure surface, and
   honest-eval reuse is exploratory.

The next modeling work should inspect held-out-opponent failures by opponent
hull, designation, score regime, campaign cell, and selected feature families.
If opponent transfer remains weak, the next implementation plan should add
outcome-free opponent-hull/family split builders and run ablations over
opponent parity, geometry, sparse component, and sparse-cross feature profiles.

## Supervised-Learning Checklist

| Item | Value |
|---|---|
| Unit | One resolved `training_matchups` row. |
| Target | `training_matchups.target`; honest-eval target is diagnostic only. |
| Prediction population | Recovered Wave 1 Hammerhead matchup rows in `wave1_matchups.sqlite`. |
| Features | Feature schema v3, profile `all`, 1,240 registry keys in learned artifact. |
| Partitions | `build`, `opponent`, `component`, `seed-cell`, `forward-time`. |
| HPO | 24 random-search trials per learned model/split, nested inside outer training rows. |
| Runtime parallelism | `hpo_jobs = 4`, `model_thread_count = 4`. |
| Leakage controls | No fitting, HPO, feature selection, or calibration on outer-test targets or honest-eval targets. |
| Claim label | Exploratory. |
| Final refit/deployment | `deployment_artifact = none`; no optimizer integration. |

## Appendix A. File Map

- Raw data:
  `data/phase7/wave1_matchups.sqlite`
- Comparator artifact:
  `data/phase7/wave1_comparator_gate_2026-05-14.json`
- Learned artifact:
  `data/phase7/learned_surrogate_v3_local_2026-05-14.json`
- Charts directory:
  none
- Comparator script:
  `scripts/analysis/phase7_baseline_surrogate.py`
- Learned script:
  `scripts/analysis/phase7_learned_surrogate_experiment.py`
- Spec:
  `docs/specs/31-phase7-matchup-data.md`
- Superseded reports:
  `docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md`,
  `docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md`
