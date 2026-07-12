---
type: report
status: shipped
last-validated: 2026-07-12
---

# Phase 7 Canonical Surrogate Matrix (Attempt 3): CatBoost Leads Where Transfer Is Easy; No Model Transfers Across Opponents

## Abstract

The 183-job canonical surrogate matrix (3 model families × 6 seeded split
families × 10 canonical seeds + forward-time) completed cleanly on AWS
under the spec-31 v2 evaluation harness (attempt 3; $75.50; zero
insufficiency artifacts). Headline readings: **catboost_regressor beats
every inline comparator with across-seed CIs excluding zero on the three
build-like splits** (build, component-vocab, seed-cell: Δ mean
per-opponent Spearman vs best comparator ≈ +0.07, 10/10 seeds positive)
— contradicting the predeclared `selected_model_family:
random_forest_tuned` on the predeclared promotion metric. **No model
family shows robust skill over comparators on any opponent-side split**
(opponent, opponent-family, opponent-hull): the central
transfer-to-unseen-opponents question remains negative.
sparse_pairwise_ridge is worst everywhere and catastrophically explodes
off-vocabulary (skill scores to −429). The methodology review's C1
prediction is confirmed in-artifact: pooled ρ ≈ 0.81 halves to ≈ 0.42
once conditioned per-opponent, and a comparator with zero
within-opponent information reaches pooled ρ 0.733. The component-vocab
split behaves like the build split for tree models (k≤2 component
combinations recur in train at ≥ 90%), and its realized test fraction
(0.23–0.49 under the 0.35 overshoot cap) affects only ridge. All claims
are **exploratory** per the artifact's claim boundary; the reserved
confirmatory seed (151) is unspent. This report covers only the
attempt-3 scientific results; the run's infrastructure incidents
(attempts 1–2) are owned by
[2026-07-12-phase7-batch-v2-incidents.md](2026-07-12-phase7-batch-v2-incidents.md),
and no new honest-eval cloud run is implicated (this batch evaluates
surrogates over existing honest-eval-derived data; it is not an
optimization run producing builds to re-score).

## Methods

### §1.1 Data

- **Artifact:** `data/phase7/learned_surrogate_full_v2_2026-07.json`
  (119 MB merged; `result_count` 183; `experiment_schema_version` 2,
  `feature_schema_version` 4; single `code_version`
  `dda1d3b…`; provenance batch `phase7-learned-batch-v2-202607`,
  `component_vocab_max_overshoot: 0.35` uniform). Source data:
  `data/phase7/wave1_matchups.sqlite`; honest-eval lineage ledger
  `…wave1-c0a-20260510T170431Z/results.jsonl`.
- **Matrix:** model families `random_forest_tuned`, `catboost_regressor`,
  `sparse_pairwise_ridge` × split families `build`, `component-vocab`,
  `opponent`, `opponent-family`, `opponent-hull`, `seed-cell` (10
  canonical seeds each: 101…149; reserved seed 151 unspent) +
  `forward-time` (1 per model, `reused_partition: true`). `skipped_models`
  empty. HPO uniform: 24 trials × 3 grouped inner folds, hpo_seed 23.
- **Features:** fixed profile "all", 1,239 features across 8 families;
  no feature selection (`fixed_profile_no_selector`).
- Typical test sizes: build ≈ 4,269 rows / ~40 included opponents /
  ~475 aggregate builds; component-vocab 4,825–10,482 rows (fraction-
  dependent); opponent-side splits ≈ 10 included opponents.

### §1.2 Definitions

- **Promotion metric** (predeclared): `mean_per_opponent_spearman` —
  Spearman within each non-degenerate opponent row, averaged. Primary
  split `build`, primary k = 1, claim label `exploratory`.
- **Skill vs comparators:** `delta_vs_best_comparator` on the promotion
  metric, best-of-6 inline comparators (`build_mean`, `global_mean`,
  `opponent_mean`, `random_forest`, `ridge_hybrid`, `twfe_additive`)
  chosen per seed. Comparators are computed once per (split, seed) and
  are bit-identical across the three model jobs (verified 0/61
  discrepancies).
- **Uncertainty:** per-seed two-way cluster bootstrap (500 resamples,
  descriptive spread per spec 31); across-seed dispersion reported as
  mean ± SD with a normal-approximation 95% CI for paired deltas
  (n = 10 seeds unless a cell is degenerate).
- **Noise floor:** 0.068559 from honest-eval replicates (2,916 groups),
  uniform across all 183 jobs (never the fallback path).

### §1.3 Integrity gates

Merge published with zero structured-insufficiency artifacts; the
implemented leakage check (`forbidden_key_overlap`) passes 180/180
applicable results with overlap 0; the four-item leakage checklist
(build key excluded from features, selection inside inner folds,
honest-eval targets excluded from fit, outer-test targets excluded from
fit) is uniformly true. Four of the five named leakage diagnostics
(`adversarial_validation_auc`, `nearest_neighbor_overlap`,
`rare_combination_overlap`, `sparse_id_ablation_delta`) are
declared-but-unimplemented placeholders (`diagnostic_not_implemented`)
— the M2 deferral is visible in-artifact, not silently absent.

## Results

### §2.1 Model ranking on build-like splits

**Method (§1.1, §1.2).** Across-seed means ± SD, n = 10 seeds per cell.

Build split (primary):

| metric | random_forest_tuned | catboost_regressor | sparse_pairwise_ridge |
|---|---:|---:|---:|
| mean_per_opponent_spearman | 0.420 ± 0.021 | **0.468 ± 0.026** | 0.170 ± 0.043 |
| mean_sparse_kendall | 0.334 ± 0.018 | **0.372 ± 0.022** | 0.129 ± 0.034 |
| build_aggregate.spearman | 0.795 ± 0.037 | **0.831 ± 0.031** | 0.625 ± 0.059 |
| regret_at_1 (normalized) | 0.136 ± 0.129 | 0.108 ± 0.103 | 0.260 ± 0.250 |

Component-vocab and seed-cell reproduce the same ordering at nearly the
same levels (CatBoost mean_spearman 0.433 ± 0.032 and 0.463 ± 0.047
respectively). Paired CatBoost−RF deltas are positive in 10/10 seeds on
all three splits (build: +0.048 [+0.029, +0.066] promotion metric;
+0.036 [+0.024, +0.047] build-aggregate Spearman). Note the per-seed
bootstrap CIs overlap heavily everywhere — the separation is only
resolvable in the paired across-seed comparison, exactly the
methodology-review-prescribed reading.

### §2.2 Skill vs comparators — where the models genuinely add value

**Method (§1.2).** Artifact `delta_vs_best_comparator` on the promotion
metric; across-seed 95% CI; n as shown (best-comparator choice omits the
metric in a few opponent-side seeds).

| split | RF_tuned | CatBoost | ridge |
|---|---:|---:|---:|
| build (n=10) | +0.021 [+0.002, +0.039] | **+0.069 [+0.056, +0.081]** | −0.230 (worse) |
| component-vocab (n=10) | +0.011 [+0.003, +0.020] | **+0.072 [+0.056, +0.088]** | −0.213 (worse) |
| seed-cell (n=10) | +0.025 [−0.000, +0.050] | **+0.067 [+0.041, +0.092]** | −0.233 (worse) |
| opponent (n=10) | +0.042 [+0.020, +0.064]† | +0.039 [−0.001, +0.080] | −0.063 (worse) |
| opponent-family (n=8) | +0.009 [−0.023, +0.040] | −0.008 [−0.047, +0.031] | −0.112 (worse) |
| opponent-hull (n=7) | +0.050 [+0.009, +0.091]† | +0.044 [−0.011, +0.099] | −0.079 (worse) |
| forward-time (n=1) | +0.031 | +0.059 | −0.221 |

† These two starred cells do not survive a stricter recomputation: the
artifact's per-seed best comparator is selected on this same metric and
is undefined for some seeds; recomputing the delta against the
best-of-six on the metric itself gives opponent RF −0.003
[−0.025, +0.019] and opponent-hull RF +0.013 [−0.026, +0.052] — both
straddling zero. On `build_aggregate.spearman` the same strict
recomputation leaves CatBoost CI-positive on build/component-vocab/
seed-cell and **nothing** CI-positive on any opponent-side split.

**Reading.** Genuine, comparator-beating skill exists only where test
builds interpolate a seen-opponent panel. Against unseen opponents,
opponent families, or opponent hulls, no learned family robustly beats
the best trivial/legacy comparator. The program's central open question
— opponent transfer — remains unanswered in the negative.

### §2.3 The pooled-metric illusion, quantified in-artifact

**Method (§1.2).** Comparator rows on the build split, n = 10.

Pooled Spearman for RF on build is ≈ 0.81 while its per-opponent mean is
0.42; the `opponent_mean` comparator — constant within every opponent,
zero within-opponent information — attains pooled ρ 0.733 ± 0.007. Half
the pooled correlation is opponent identity. This is the concrete,
current-artifact confirmation of methodology-review C1, and the reason
the promotion metric is per-opponent.

### §2.4 Component-vocab behaves like build for tree models

**Method (§1.1).** Component-overlap diagnostics + per-seed realized
fractions, n = 10 seeds.

Exact build fingerprints never leak into train (overlap 0.0), but 96%
of held-out builds' single components and 91% of component pairs recur
in train — so tree models treat the split as interpolation, and their
metrics match the build split. Realized test fractions span 0.226–0.491
(cap 0.35 admits up to 0.55); metric correlation with fraction is
negligible for RF/CatBoost (|ρ| ≤ 0.16) and material only for ridge
(rmse ρ = +0.87, skill ρ = −0.68). Two bank seeds (107, 149) produced
byte-identical splits (both hold out `weapon:pdlaser`) — the panel is
effectively 9 distinct splits, not 10.

### §2.5 Forward-time

**Method (§1.1).** n = 1 per model; partition reused from earlier waves
(`reused_partition: true`), so this is a weak, caveated signal.

CatBoost 0.438 / RF 0.410 mean per-opponent Spearman — essentially
build-split levels, no visible forward degradation. Not evidence of
temporal robustness at n = 1; consistent with it.

### §2.6 Anomalies and degeneracies

**Method (§1.1).** Full-artifact sweep.

- sparse_pairwise_ridge explodes off-vocabulary: worst skill −429
  (opponent-hull seed 139, rmse 23.2); its predictions leave the target
  range entirely on extrapolation splits. The family is dead weight in
  the matrix going forward.
- Two degenerate build-aggregate cells across all models
  (opponent-family seed 127, opponent-hull seed 131): every test build
  fell below `min_opponents_per_build`, so aggregate metrics are None
  (handled, not crashed; n = 9 rows there).
- `precision_at_1` per-seed bootstrap CIs are [0, 1] nearly everywhere —
  uninformative at k = 1, as the methodology review predicted (H3);
  normalized regret is the usable top-end statistic.
- `mean_top_fraction_kendall` hovers at ≈ 0 with large spread in every
  cell: no model ranks the top decile better than chance — top-end
  discrimination is the weakest link even where mid-rank transfer works.
- 12 cells have negative pooled Spearman (mostly ridge; 3 tree-model
  cells on opponent-side splits).

## Synthesis & decisions

1. **Model-family contradiction, deliberately not resolved here:** the
   artifact predeclared `selected_model_family: random_forest_tuned`,
   but CatBoost wins the predeclared promotion metric on the predeclared
   primary split in 10/10 seeds with a comparator-beating CI. Because
   swapping the selected family *after seeing results* is precisely the
   test-selected-decision failure mode the methodology review flagged,
   the swap should be ratified on the reserved confirmatory seed (151)
   or the next data wave — a cheap, single-cell check — before any spec
   or config changes the default.
2. **Opponent transfer stays negative:** roadmap items 3–4 (FM/bilinear
   interaction features; within-opponent pairwise ranking) are the
   designed responses and are now the highest-value next experiments;
   item 7 (opponent-panel data wave) is what would make their evaluation
   well-powered (current opponent-side splits have ~10 test opponents
   and two degenerate aggregate cells).
3. **Retire sparse_pairwise_ridge from the canonical matrix** (replaced
   by the FM/low-rank family per the roadmap; its catastrophic
   off-vocabulary behavior adds noise and cost without evidence value).
4. **Seed-bank hygiene:** the component-vocab duplicate (107 ≡ 149)
   means future component-vocab aggregates should either dedupe by
   realized split or the bank should gain a uniqueness check at
   construction; spec-31-level fix, small.
5. **Leakage program:** the four unimplemented M2 diagnostics are now
   visibly stamped in every artifact; implementing adversarial-validation
   AUC first would directly qualify the §2.4 interpolation reading.
6. **Evidence-reuse note:** this wave consumed all 10 canonical bank
   seeds across 6 split families; seed 151 remains reserved and unspent,
   and the forward-time partition is on its second use (stamped).

## Open questions / next steps

- Confirmatory check of CatBoost-over-RF on seed 151 (decision 1).
- Feature-profile ablations (roadmap item 2) under this harness, now
  unblocked by the clean canonical baseline.
- Whether top-decile discrimination (≈ 0 everywhere) improves under the
  ranking-objective family (roadmap item 4) — the current regression
  objectives optimize the wrong end of the distribution for our use.
- Wave-2 opponent-panel design (roadmap item 7) to give opponent-side
  splits real statistical power.

## Appendix — file map

- **Canonical artifact:**
  `data/phase7/learned_surrogate_full_v2_2026-07.json` (gitignored,
  local; 119 MB; `bundle_sha256 0093d8d6…477f` in top-level provenance).
- **Per-job artifacts + ledger:**
  `data/phase7/learned_surrogate_batch_v2_2026-07/` (183 results,
  events, budget ledger; final spend $75.50 of $150).
- **Producer:** `scripts/analysis/phase7_learned_surrogate_experiment.py`
  via `scripts/cloud/phase7_learned_batch.py` (attempt 3, launched
  2026-07-12 12:31 UTC, completed 20:10 UTC — corrected 2026-07-12 by the
  [tail-walltime analysis](2026-07-12-phase7-tail-walltime.md), which
  reconstructed the full 7.65 h fleet window from the cost ledger; this
  line originally misstated completion as ≈ 16:30 UTC).
- **Charts:** none (tables only; the artifact carries full per-cell
  detail).
- **Dependent docs:**
  [2026-07-12-phase7-batch-v2-incidents.md](2026-07-12-phase7-batch-v2-incidents.md)
  (owns attempts 1–2);
  [2026-07-11-phase7-methodology-review.md](2026-07-11-phase7-methodology-review.md)
  (defines the readings applied here); docs/roadmap.md items 2–4/7
  (next experiments this report gates).
