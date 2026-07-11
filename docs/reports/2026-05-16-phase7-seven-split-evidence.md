---
type: report
status: shipped
last-validated: 2026-05-16
supersedes:
  - 2026-05-14-phase7-v3-evidence-refresh.md
---

# Phase 7 Seven-Split Evidence

## Abstract

This report records the local Phase 7 seven-split evidence wave. The
comparator gate completed 42 split/model rows on 2026-05-15; the learned
matrix completed 21 split/model rows on 2026-05-16 in 6h26m. CatBoost remains
the best learned RMSE model on build, component, seed-cell, and forward-time
splits. Tuned random forest is best on exact-opponent, opponent-family, and
opponent-hull splits. The hierarchy result is the main new finding:
opponent-family transfer is materially harder than non-opponent transfer and
easier than exact-opponent transfer, while opponent-hull has high rank signal
but only 385 test rows and is not better than the fixed ridge comparator by
RMSE. This report does not promote optimizer integration; all learned claims
remain exploratory because the existing honest-eval ledger has influenced
model-development decisions.

## 1. Methods

### 1.1 Data

Unit of observation: one resolved `training_matchups` row from
`data/phase7/wave1_matchups.sqlite`, representing one
`(build_key, opponent_variant_id)` matchup recovered from Wave 1 training
logs.

Target variable: `training_matchups.target`, the per-opponent
`hp_differential` recovered from optimizer logs. Honest-eval rows are not fit
or tuned; they are post-fit top-k diagnostics only.

Feature schema: `FEATURE_SCHEMA_VERSION = 3`, feature profile `all`. This
includes aggregate build/opponent features, per-slot weapon/geometry features,
static `.ship` geometry, arc-pressure summaries, sparse component indicators,
and explicit matchup interactions. Exact feature keys and feature-family
metadata are owned by spec 31 and the learned artifact.

Training-log sample sizes by outer split:

| Split | Train N | Test N | Inner train N | Inner validation N |
|---|---:|---:|---:|---:|
| `build` | 17,075 | 4,287 | 13,636 | 3,439 |
| `opponent` | 15,065 | 6,297 | 11,503 | 3,562 |
| `opponent-hull` | 20,977 | 385 | 17,603 | 3,374 |
| `opponent-family` | 17,822 | 3,540 | 15,325 | 2,497 |
| `component` | 17,059 | 4,303 | 13,625 | 3,434 |
| `seed-cell` | 17,158 | 4,204 | 14,301 | 2,857 |
| `forward-time` | 17,090 | 4,272 | 13,672 | 3,418 |

Honest-eval diagnostic lineage: the Phase 7 DB's honest-eval rows come from
`data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`.
That ledger is reused for post-fit diagnostics only and is labeled
`exploratory_selection`.

### 1.2 Comparator Gate

Comparator artifact:
`data/phase7/wave1_comparator_gate_2026-05-15.json`.

Command:

```bash
uv run python scripts/analysis/phase7_baseline_surrogate.py \
  data/phase7/wave1_matchups.sqlite --split all --model all \
  --tree-count 80 --top-k 1,3,5 \
  > data/phase7/wave1_comparator_gate_2026-05-15.json
```

The comparator producer is
`scripts/analysis/phase7_baseline_surrogate.py`. The comparator gate is a
fixed scikit-learn ladder:
`global_mean`, `opponent_mean`, `build_mean`, `twfe_additive`,
`ridge_hybrid`, and `random_forest`. It ran seven split levels crossed with
six comparator models for 42 rows. The random-forest comparator used
`n_estimators=80`, `min_samples_leaf=2`, `random_state=17`, and `n_jobs=-1`.
The ridge comparator used `alpha=10.0`.

### 1.3 Learned Matrix

Learned artifact:
`data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json`.

Command:

```bash
uv run python scripts/analysis/phase7_learned_surrogate_experiment.py \
  data/phase7/wave1_matchups.sqlite \
  --split all --model all --top-k 1,3,5 \
  --comparator-json data/phase7/wave1_comparator_gate_2026-05-15.json \
  --honest-eval-usage exploratory_selection \
  --output data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json
```

The learned producer is
`scripts/analysis/phase7_learned_surrogate_experiment.py`. Model families:

| Model | Implementation | HPO |
|---|---|---|
| `random_forest_tuned` | scikit-learn random forest over vectorized mixed features | random search, 24 trials |
| `catboost_regressor` | CatBoost native categorical regressor | random search, 24 trials |
| `sparse_pairwise_ridge` | count-sketched pairwise interactions plus ridge | random search, 24 trials |

Default hyperparameters and random-search spaces:

| Model | Default | Random-search space |
|---|---|---|
| `random_forest_tuned` | `n_estimators=80`, `max_depth=None`, `min_samples_leaf=2`, `max_features=sqrt`, `bootstrap=True`, `max_samples=None` | `n_estimators in {200,400,800}`; `max_depth in {None,16,32,64}`; `min_samples_leaf in {1,2,4,8}`; `max_features in {sqrt,0.35,0.6,1.0}`; `max_samples in {None,0.65,0.85}` |
| `catboost_regressor` | `iterations=600`, `learning_rate=0.05`, `depth=6`, `l2_leaf_reg=3.0`, `random_strength=1.0`, `bagging_temperature=0.0` | `iterations in {300,600,1000}`; `learning_rate log-uniform [0.02,0.2]`; `depth in {4,6,8,10}`; `l2_leaf_reg log-uniform [1,30]`; `random_strength log-uniform [0.1,10]`; `bagging_temperature in {0,0.5,1,2}` |
| `sparse_pairwise_ridge` | `n_components=1024`, `alpha=10.0`, `degree=2`, `include_original_features=True` | `n_components in {512,1024,2048,4096}`; `alpha log-uniform [0.001,1000]`; `degree=2`; `include_original_features=True` |

Runtime settings: `hpo_jobs = 4`, `model_thread_count = 4`,
`hpo_seed = 23`, `split_seed = 17`, `holdout_fraction = 0.2`,
`train_fraction = 0.8`, and no row cap.

The learned run uses a fixed matrix policy, not a single-winner promotion
policy. Claim boundary fields are `claim_label = exploratory`,
`honest_eval_usage = exploratory_selection`, `primary_split = all`,
`primary_top_k = 1`, `promotion_metric = honest_eval_top_k_recall`, and
`deployment_artifact = none`.

Inner validation is built from outer-training rows only, using the same
stressor as the outer split. The `forward-time` inner split uses the same
path-ordered blocked prefix/suffix semantics inside the outer-training prefix.
No split used random-row fallback. The producer records this contract in each
result's `inner_validation_metadata` object.

### 1.4 Split Semantics

| Split | Claim |
|---|---|
| `build` | Transfer to unseen repaired player builds from the same broader build distribution. |
| `opponent` | Transfer to unseen exact opponent variants/builds. |
| `opponent-hull` | Transfer to unseen opponent hull IDs derived from stock variants. |
| `opponent-family` | Transfer to unseen coarse opponent hull-size/designation/manufacturer families. |
| `component` | Transfer away from selected full component fingerprints. |
| `seed-cell` | Transfer across campaign cells/proposal contexts. |
| `forward-time` | Predict later path-ordered optimizer proposals from earlier rows. |

The component split uses the canonical full fingerprint:
`hull_id + slot_weapon_assignments + hullmods + flux_vents + flux_capacitors`.

### 1.5 Statistical-Learning Setup

| Item | Value |
|---|---|
| Unit | One resolved `training_matchups` row. |
| Target | `training_matchups.target`; honest-eval target is diagnostic only. |
| Prediction population | Recovered Wave 1 Hammerhead matchup rows in `wave1_matchups.sqlite`. |
| Features | Feature schema v3, profile `all`, feature-family registry embedded in the learned artifact. |
| Preprocessing | Model-specific vectorization or CatBoost categorical handling inside each training fold. |
| Partitions | `build`, `opponent`, `opponent-hull`, `opponent-family`, `component`, `seed-cell`, `forward-time`. |
| HPO | 24 random-search trials per learned model/split, nested inside outer training rows. |
| Leakage controls | No fitting, HPO, feature selection, or calibration on outer-test targets or honest-eval targets. |
| Model-selection criterion | Inner-validation RMSE, with Spearman rho and runtime as tie-breakers in the producer. |
| Claim label | Exploratory. |
| Final refit/deployment | `deployment_artifact = none`; no optimizer integration. |

### 1.6 Comparison Statistics

Metrics are implemented by the comparator and learned producer scripts.

```text
MAE = mean(abs(y_i - yhat_i))
RMSE = sqrt(mean((y_i - yhat_i)^2))
Spearman rho = rank correlation between held-out targets and predictions
```

Comparator deltas are learned metric minus matching comparator metric for the
same split and model context. Negative MAE/RMSE delta is better; positive
Spearman rho delta is better. Top-k recall fits on eligible training-log rows,
predicts resolved honest-eval rows, aggregates predictions by `build_key`, and
reports overlap with the observed honest-eval top-k build set.

### 1.7 Diagnostics & Thresholds

Primary metrics are MAE, RMSE, and Spearman rho on outer held-out
training-log rows. The diagnostic contract is owned by spec 31; report
structure is owned by `docs/CONVENTIONS.md`.

Required artifact diagnostics were present: `claim_boundary`,
`model_family_policy`, `feature_selection_protocol`, `hierarchy_scorecard`,
`leakage_diagnostics`, and `deployment_policy`. Forbidden-key overlap passed
with zero overlap for every non-forward split and is `not_applicable` for the
forward-time split. The artifact includes explicit
`diagnostic_not_implemented` limitations for adversarial-validation AUC,
nearest-neighbor overlap, rare-combination overlap, and sparse-ID ablation
delta. Those are missing diagnostics, not evidence of absence of leakage.

Honest-eval top-k recall is a post-fit diagnostic only. No production
promotion threshold is claimed from this reused ledger.

## 2. Results

### 2.1 Comparator Gate

**Method (§1.2).** Fixed scikit-learn comparator ladder over all
seven schema-v3 split levels.
**Statistic (§1.6).** Best comparator by RMSE per split.
**Threshold (§1.7).** Exploratory baseline context only.

| Split | Best comparator | Train N | Test N | MAE | RMSE | Spearman rho |
|---|---|---:|---:|---:|---:|---:|
| `build` | `random_forest` | 17,075 | 4,287 | 0.215851 | 0.351852 | 0.809154 |
| `opponent` | `random_forest` | 15,065 | 6,297 | 0.452226 | 0.591212 | 0.216598 |
| `opponent-hull` | `ridge_hybrid` | 20,977 | 385 | 0.173788 | 0.204556 | 0.839747 |
| `opponent-family` | `random_forest` | 17,822 | 3,540 | 0.346727 | 0.487640 | 0.551762 |
| `component` | `random_forest` | 17,059 | 4,303 | 0.222362 | 0.364279 | 0.810146 |
| `seed-cell` | `random_forest` | 17,158 | 4,204 | 0.220999 | 0.360472 | 0.804801 |
| `forward-time` | `random_forest` | 17,090 | 4,272 | 0.238808 | 0.383786 | 0.789136 |

Reading: the fixed comparator ladder still places exact-opponent transfer as
the weakest surface. The new hierarchy levels separate the stressor: coarse
opponent-family holdout is much harder than non-opponent transfer, while the
selected opponent-hull split has high rank signal and very low comparator
RMSE on a small 385-row test set.

### 2.2 Learned Matrix

**Method (§1.3).** Fixed seven split x three model-family learned
matrix with nested grouped HPO inside each outer split.
**Statistic (§1.6).** Best learned model by RMSE per
split, with deltas against the matching comparator.
**Threshold (§1.7).** Exploratory model-development
evidence.

| Split | Best learned model | Train N | Test N | MAE | RMSE | Spearman rho | RMSE delta | Rho delta |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `build` | `catboost_regressor` | 17,075 | 4,287 | 0.208988 | 0.339409 | 0.821211 | -0.012444 | +0.012057 |
| `opponent` | `random_forest_tuned` | 15,065 | 6,297 | 0.449856 | 0.586258 | 0.245033 | -0.004954 | +0.028435 |
| `opponent-hull` | `random_forest_tuned` | 20,977 | 385 | 0.252026 | 0.309415 | 0.835346 | +0.071640 | -0.001638 |
| `opponent-family` | `random_forest_tuned` | 17,822 | 3,540 | 0.325722 | 0.476071 | 0.514787 | -0.011570 | -0.036975 |
| `component` | `catboost_regressor` | 17,059 | 4,303 | 0.217540 | 0.351108 | 0.821291 | -0.013170 | +0.011144 |
| `seed-cell` | `catboost_regressor` | 17,158 | 4,204 | 0.211647 | 0.342288 | 0.817363 | -0.018184 | +0.012562 |
| `forward-time` | `catboost_regressor` | 17,090 | 4,272 | 0.228739 | 0.371156 | 0.805100 | -0.012630 | +0.015963 |

Reading: CatBoost remains the best learned RMSE model for non-opponent
transfer. Tuned random forest is the best learned RMSE model for all opponent
hierarchy splits. The learned model improves exact-opponent RMSE only
slightly versus the comparator. For opponent-family, tuned random forest
improves RMSE but loses Spearman rho versus the comparator, so the result is
not a clean ranking improvement. For opponent-hull, tuned random forest is
worse than the fixed ridge comparator by RMSE and slightly worse in rho.

### 2.3 Model-Family Pattern

**Method (§1.3).** Compare every learned model family across all
seven outer splits.
**Statistic (§1.6).** Outer-test RMSE and Spearman rho.
**Threshold (§1.7).** Exploratory family triage.

| Split | `random_forest_tuned` RMSE / rho | `catboost_regressor` RMSE / rho | `sparse_pairwise_ridge` RMSE / rho |
|---|---:|---:|---:|
| `build` | 0.348958 / 0.814085 | 0.339409 / 0.821211 | 0.424729 / 0.745201 |
| `opponent` | 0.586258 / 0.245033 | 0.652268 / 0.273336 | 2.020345 / -0.152076 |
| `opponent-hull` | 0.309415 / 0.835346 | 0.474985 / 0.836696 | 0.670414 / 0.826795 |
| `opponent-family` | 0.476071 / 0.514787 | 0.902788 / 0.252438 | 0.534588 / 0.511965 |
| `component` | 0.363055 / 0.810341 | 0.351108 / 0.821291 | 0.425645 / 0.752191 |
| `seed-cell` | 0.354932 / 0.804966 | 0.342288 / 0.817363 | 0.410643 / 0.739054 |
| `forward-time` | 0.376890 / 0.792412 | 0.371156 / 0.805100 | 0.431898 / 0.725088 |

Reading: the sparse pairwise ridge path remains noncompetitive in its current
form. It is stable but weak on non-opponent splits and unstable on exact
opponent transfer. CatBoost is the default tabular learned baseline for
non-opponent transfer. Tuned random forest is the conservative opponent
transfer baseline.

### 2.4 Component Overlap Diagnostics

**Method (§1.4).** Component holdout uses the canonical full
component fingerprint and reports train/test overlap diagnostics.
**Statistic (§1.7).** Unique train/test counts, unique
overlap, and test-overlap fraction for exact fingerprints and component
combinations.
**Threshold (§1.7).** Exact full-fingerprint overlap must
be zero for component-holdout transfer claims.

| Diagnostic | Train unique | Test unique | Overlap unique | Test overlap fraction |
|---|---:|---:|---:|---:|
| Exact full fingerprint | 1,899 | 475 | 0 | 0.0000 |
| k=1 component combinations | 155 | 152 | 151 | 0.9934 |
| k=2 component combinations | 9,921 | 8,913 | 8,796 | 0.9869 |
| k=3 component combinations | 276,913 | 156,576 | 138,412 | 0.8840 |

Reading: the component split has zero exact full-fingerprint leakage. Lower
order component overlap remains high, so this is transfer away from full
component fingerprints, not transfer to entirely novel component vocabularies.

### 2.5 Hierarchy Read

**Method (§1.4).** Compare the exact-opponent,
opponent-family, and opponent-hull holdouts under the same feature schema and
learned matrix.
**Statistic (§1.6).** Best learned RMSE and Spearman rho.
**Threshold (§1.7).** Exploratory hierarchy diagnostic;
not a promotion gate.

| Split | Test N | Best learned model | RMSE | Spearman rho | Interpretation |
|---|---:|---|---:|---:|---|
| `opponent` | 6,297 | `random_forest_tuned` | 0.586258 | 0.245033 | Exact unseen variants remain the weakest ranking surface. |
| `opponent-family` | 3,540 | `random_forest_tuned` | 0.476071 | 0.514787 | Coarse archetype holdout is intermediate and still a real transfer stressor. |
| `opponent-hull` | 385 | `random_forest_tuned` | 0.309415 | 0.835346 | High rank signal, but small test surface and worse RMSE than fixed ridge comparator. |

Reading: opponent representation remains the next modeling bottleneck. The
family split is the most useful immediate diagnostic because it has enough
test rows to be informative and is not as pathological as exact-opponent
holdout. The hull split should not be over-read until the held-out group
selection is repeated or expanded; the current test panel is small.

## 3. Synthesis & Decisions

Current decisions:

- Keep CatBoost as the default learned baseline for build, component,
  seed-cell, and forward-time transfer.
- Keep tuned random forest as the conservative baseline for opponent transfer
  hierarchy splits.
- Treat sparse pairwise ridge as noncompetitive in its current representation.
- Do not integrate a learned surrogate into optimizer selection yet.
- Prioritize opponent-family diagnostics, feature-profile ablations, and
  nested feature-family selection before model-assisted search.

The main roadmap implication is that Phase 7 should focus on representation
and selection, not optimizer acquisition. The next evidence wave should test
whether aggregate, opponent-parity, geometry, sparse-component, sparse-cross,
and all-feature profiles explain the opponent-family gap without relying on
memorized sparse fingerprints.

## 4. Open Questions / Next Steps

> **Superseded as a next-step list (2026-07-11):** the live next-wave plan is
> [docs/roadmap.md](../roadmap.md), redesigned by the
> [methodology review](2026-07-11-phase7-methodology-review.md), which also
> revises how this report's metrics should be read. The AWS out-of-scope
> decision below was lifted the same day (see the
> [AWS cost analysis](2026-07-11-aws-cost-analysis.md)).

- Run feature-profile ablations on `opponent-family`, `opponent`, and
  `opponent-hull`, with family-level selection treated as part of the
  estimator.
- Inspect stratified errors by opponent designation, manufacturer, score
  regime, and campaign cell for the opponent-family split.
- Redesign sparse interaction modeling before spending more HPO on the current
  sparse pairwise ridge path.
- Implement the missing leakage diagnostics listed in §1.7 before using
  seven-split results for a promotion-grade model-selection claim.
- Treat the current honest-eval ledger as exploratory for future model
  development; final learned-surrogate promotion requires a fresh ledger or an
  explicit exploratory label.
- Keep AWS learned-batch execution out of scope until there is a reproducibility
  or scale need that local execution cannot satisfy.

## Appendix - File Map

- Comparator artifact:
  `data/phase7/wave1_comparator_gate_2026-05-15.json`
- Learned artifact:
  `data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json`
- Raw data:
  `data/phase7/wave1_matchups.sqlite`
- Producer scripts:
  `scripts/analysis/phase7_baseline_surrogate.py`,
  `scripts/analysis/phase7_learned_surrogate_experiment.py`
- Charts directory:
  none
- Dependent spec:
  `docs/specs/31-phase7-matchup-data.md`
- Superseded report:
  `docs/reports/2026-05-14-phase7-v3-evidence-refresh.md`
