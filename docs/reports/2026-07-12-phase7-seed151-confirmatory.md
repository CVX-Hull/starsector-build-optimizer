---
type: report
status: shipped
last-validated: 2026-07-12
---

# Phase 7 — Seed-151 confirmatory check: CatBoost over tuned random forest (build split)

## Abstract

The reserved confirmatory seed (151) was spent on the predeclared endpoint
from the [attempt-3 results report](2026-07-12-phase7-attempt3-surrogate-results.md)
(decision 1): `catboost_regressor` vs `random_forest_tuned` on the build
split, judged on `mean_per_opponent_spearman`. CatBoost wins, 0.4645 vs
0.3996 (Δ = +0.0649, paired partition, n_test = 4,204 rows / 39 included
opponents), a margin at the attempt-3 per-seed median (≈ +0.07, 10/10
seeds). The ratification decision rule (Δ > 0) is met; CatBoost is now the
learned script's default model family, with the claim boundary unchanged —
build-like splits only, no opponent-side transfer claim. This report covers
only the single-cell confirmatory comparison; it does not revisit the full
canonical matrix, opponent-side transfer, or leakage diagnostics (all owned
by the attempt-3 report).

## 1. Methods

### 1.1 Data

Unit of analysis: one finalized matchup row (`training_matchups.target`,
capped hp-differential) from `data/phase7/wave1_matchups.sqlite` — the same
source DB, filters, and featurization (feature profile `all`, schema v3) as
the attempt-3 canonical matrix. Outer split: `build` level (group key
`build_key`), holdout fraction 0.2, train fraction 0.8, split seed **151**
(the reserved confirmatory seed, first and only use). Realized partition:
n_train = 17,158, n_test = 4,204 rows; 475 test builds (panel sizes 3–10,
median 9). Both model runs share the identical partition — verified by the
model-independent inline comparators producing identical values in both
artifacts.

### 1.2 Estimators

Both families exactly as in the canonical matrix
(`scripts/analysis/phase7_learned_surrogate_experiment.py`, schema v2):
`catboost_regressor` (CatBoost 1.2.10, RMSE objective) and
`random_forest_tuned` (scikit-learn `RandomForestRegressor`), each tuned by
random-search HPO with 24 trials, HPO seed 23, grouped 3-fold inner CV
(groups = `build_key`), 4 concurrent HPO jobs × 4 model threads. The six
inline comparators (global/build/opponent means, TWFE additive, fixed-default
random forest, ridge hybrid) are computed on the same partition.

### 1.3 Statistical-learning setup

Identical to the attempt-3 canonical matrix (see that report's Methods §1
for the full labeled table): target `training_matchups.target`; leakage
controls per spec 31 (`build_key` and target-derived columns excluded from
features; grouped inner CV; forbidden-key overlap check); model-selection
criterion inside HPO is inner-CV RMSE; the outer endpoint is predeclared,
not selected. `claim_label: confirmatory` and `honest_eval_usage:
diagnostic_only` are stamped in both artifacts.

### 1.4 Comparison statistics

Primary endpoint (predeclared in the attempt-3 report decision 1, spec 31,
and restated in-session before either artifact was read):
`mean_per_opponent_spearman` on the outer test panel — Spearman ρ computed
within each included test opponent, averaged across opponents. Decision
rule: ratify CatBoost as the default family iff Δ = CatBoost − RF > 0 on
seed 151. Interpretation note: a single-seed sign test carries little
standalone weight (p = 0.5 under the null); its evidentiary value is as an
out-of-sample confirmation of the attempt-3 pattern (10/10 canonical seeds,
CI excluding 0) on a never-before-used partition. Cluster bootstrap
(500 resamples, resampling unit = opponent) CIs are descriptive.

### 1.5 Diagnostics & thresholds

Noise floor 0.068559 (honest-eval replicates, n_groups = 2,916) for
per-opponent tie handling, as in the canonical matrix. Opponent inclusion:
14 of 54 test-panel opponents excluded as low-variance (degenerate at this
target's censoring), 1 excluded small-n; 39 included — the same exclusion
machinery as attempt 3.

## 2. Results

### 2.1 Primary endpoint

**Method (§1.4).** `mean_per_opponent_spearman` on the shared seed-151
build-split test panel; decision rule Δ > 0.

| Metric (n_test = 4,204; 39 included opponents) |   CatBoost |   RF tuned |       Δ |
|---|---:|---:|---:|
| `mean_per_opponent_spearman` (primary)         |     0.4645 |     0.3996 | **+0.0649** |
| bootstrap 95% CI (500 resamples, by opponent)  | [0.367, 0.582] | [0.304, 0.527] |       — |
| median per-opponent Spearman                   |     0.5123 |     0.4002 | +0.1121 |
| Δ vs best inline comparator (fixed-default RF, 0.3854) | +0.0791 | +0.0142 |       — |

**Reading.** CatBoost beats RF on the predeclared metric on the reserved
seed; the margin (+0.0649) sits at the attempt-3 per-seed median (≈ +0.07)
— seed 151 looks like a typical draw from the same effect distribution, not
a regression to zero. Combined with attempt 3 this is 11/11 seeds. The
decision rule is met: **ratified**. Tuned RF's advantage over its own
fixed-default comparator remains marginal (+0.0142), consistent with
attempt 3's finding that HPO adds little for RF on this data.

### 2.2 Secondary/descriptive statistics

**Method (§1.4, descriptive only).** Same partition; pooled and
build-aggregate statistics as defined in the attempt-3 report Methods.

| Statistic (n_test = 4,204; 475 builds)   |  CatBoost |  RF tuned |
|---|---:|---:|
| pooled Spearman ρ                        |    0.8165 |    0.8028 |
| build-aggregate Spearman                 |    0.8238 |    0.7736 |
| RMSE                                     |    0.3433 |    0.3545 |
| MAE                                      |    0.2077 |    0.2189 |
| normalized regret@1                      |    0.0074 |    0.0000 |
| normalized regret@3 / @5                 | 0.0 / 0.0 | 0.0 / 0.0 |
| mean top-fraction Kendall τ              |    0.0097 |    0.0253 |

**Reading.** Every secondary statistic except the top-end pair also favors
CatBoost. The two top-end statistics (regret@1, top-fraction τ) nominally
favor RF, but attempt 3 established both as uninformative-to-noisy at this
panel size (precision@1 bootstrap CIs span [0, 1]); both models' regret@1
is under 1% of the target range. The pooled-vs-per-opponent gap (0.82 vs
0.46) reproduces the pooled-metric illusion quantified in attempt 3 §2.3.

## 3. Synthesis & decisions

1. **Ratified: `catboost_regressor` is the default learned model family**
   for build-like splits. Implemented in this change: the learned script's
   `--model` default is now `DEFAULT_MODEL = "catboost_regressor"`; spec 31
   records the ratification and the claim boundary (build-like splits only
   — attempt 3's zero-opponent-transfer finding is untouched by this check).
2. **The reserved seed is spent.** Spec 31 now marks seed 151 as consumed;
   any future promotion-grade confirmatory claim requires appending a fresh
   reserved seed to `phase7_matchup_data.py` first. Seed 151 must still
   never appear in batch seed lists.
3. **Lineage-label fix:** both artifacts carry `seed_bank_label: "ad-hoc"`
   because the labeler only knew bank/non-bank; the script now stamps
   `reserved-confirmatory` for seed 151 (with test), and spec 31 documents
   the three-value semantics. The artifacts' identity is unambiguous via
   `split_seed: 151` + `claim_label: confirmatory`.
4. **Runtime note (local macOS workstation, hpo_jobs 4 × threads 4):**
   CatBoost cell 1,691 s, RF cell 3,915 s — no AWS spend; the check cost
   ~95 minutes of local compute.

## 4. Open questions / next steps

- Whether CatBoost's build-split advantage survives under the
  feature-profile ablations (roadmap item 2) and the ranking-objective
  family (item 4) — the top-end discrimination weakness is family-agnostic
  so far.
- The FM/bilinear family (item 3) and pairwise ranking (item 4) remain the
  designed responses to the opponent-transfer negative; nothing here
  changes their priority.

## Appendix — file map

- Producer script: `scripts/analysis/phase7_learned_surrogate_experiment.py`
  (invoked once per family: `--split build --split-seed 151 --model
  {catboost_regressor,random_forest_tuned} --claim-label confirmatory`,
  all other flags at canonical-matrix defaults).
- Raw artifacts (gitignored, local):
  `data/phase7/phase7_seed151_confirmatory_catboost_2026-07-12.json`,
  `data/phase7/phase7_seed151_confirmatory_rf_2026-07-12.json`.
- Source data: `data/phase7/wave1_matchups.sqlite` (gitignored, local).
- Charts: none.
- Dependent reports:
  [attempt-3 surrogate results](2026-07-12-phase7-attempt3-surrogate-results.md)
  (predeclaration + canonical-matrix evidence).
