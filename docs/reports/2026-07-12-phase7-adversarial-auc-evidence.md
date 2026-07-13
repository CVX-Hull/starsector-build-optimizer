---
type: report
status: shipped
last-validated: 2026-07-12
---

# Phase 7 — Adversarial-validation AUC evidence sweep (wave-1 DB)

## Abstract

The newly shipped adversarial-validation AUC diagnostic (spec 31
§"Adversarial-validation AUC"), run over all 60 canonical (split, seed)
cells of the frozen wave-1 DB, directly confirms the methodology review's
M2 interpolation reading for the build split: **held-out builds are
statistically indistinguishable from training builds in the model's
feature space on every seed** (pooled out-of-fold AUC 0.469–0.518 across
all 10 bank seeds; all in the `indistinguishable` band). The build-split
rank metrics in the attempt-3 report therefore measure interpolation
inside the TPE-concentrated proposal cloud, not extrapolation to novel
builds — which simultaneously bounds any novel-build generality claim and
supports deployment relevance, since the surrogate's deployment task is
scoring proposals drawn from that same process. Component-vocab
(AUC > 0.99 on all 9 seeds) and forward-time (0.820) impose genuine
distribution shift; seed-cell sits at chance; the opponent-hierarchy
AUCs are unstable across seeds (0.122–0.960) because those splits have
too few groups for a reliable per-cell estimate. This report does not
re-evaluate any surrogate model — it characterizes the splits themselves;
model rank metrics remain owned by the
[attempt-3 report](2026-07-12-phase7-attempt3-surrogate-results.md).

## Methods

### Data

Unit of analysis: one canonical (split family, split seed) cell of the
frozen wave-1 matchup DB (`data/phase7/wave1_matchups.sqlite`; 21,362
training-matchup rows). Cells: the 5 seeded non-component families × the
10-seed canonical bank, component-vocab × its 9-seed effective panel
(seed 149 excluded per `SPLIT_SEED_EXCLUSIONS`), and the seedless
forward-time split — 60 cells, matching the effective canonical batch
matrix at one model (the diagnostic is model-independent). Split
construction, fractions (holdout 0.2 / train 0.8), and feature profile
(`all`, feature schema v4) are identical to the canonical batch config
(`data/phase7/phase7-learned-batch-v2-launch.yaml`); rows are the outer
feature bundles' kept rows, i.e. exactly what the surrogate models see.
Per-cell class sizes range from n_train 10,296–20,917 versus
n_test 445–11,066 across all 60 cells; grouped-split test sizes swing
widely with the seed's group draw (opponent-family n_test 445–11,066
across its 10 seeds, opponent-hull 495–6,180, opponent 1,120–7,975),
which is itself part of the few-groups instability the Results flag.

### Estimator

**Adversarial-validation AUC** (implemented by
`adversarial_validation_entry` in
`scripts/analysis/phase7_learned_surrogate_experiment.py`; contract in
spec 31 §"Adversarial-validation AUC"): label outer-train rows 0 and
outer-test rows 1; vectorize the combined feature records with a
diagnostic-only sparse `DictVectorizer`; obtain out-of-fold predicted
probabilities from a scikit-learn `RandomForestClassifier`
(`n_estimators=100`, `min_samples_leaf=5`) under **grouped** stratified
CV (`StratifiedGroupKFold`); report the pooled out-of-fold ROC AUC
(every row predicted exactly once by a classifier that never saw its
group). AUC ≈ 0.5 means train and test rows are indistinguishable in
feature space (the split's test set sits inside the training
distribution — interpolation); AUC → 1.0 means the split imposes real
distribution shift (extrapolation).

Grouped CV is load-bearing, not a refinement: feature records are a pure
function of (build, opponent, profile) and every cluster on the split's
assignment side shares one class label, so row-level CV lets the
classifier fingerprint clusters seen during fit. On the build split
(seed 101), row-level CV returns AUC 1.0000 (pure group memorization)
where grouped CV returns 0.5025 — the row-level number carries no
information about shift. The row-level figure comes from the
design-validation prototype and the mutation check (it is not in the
sweep JSON, which holds grouped entries only); it is reproducible by
substituting `StratifiedKFold` for the grouped splitter. CV group units per family are the outer split's assignment unit,
coarsened to `build_key` where that unit is finer than the build-level
feature clustering: `build_key` (build, component-vocab, forward-time),
`opponent_variant_id` (opponent), hull / family group maps
(opponent-hull / opponent-family), `campaign:seed` (seed-cell).

### Statistical-learning setup

Deterministic by contract: classifier and fold shuffle seeded from the
cell's split seed; `predict_proba` single-threaded (spec 31 determinism
note); fit threads 8. Fold count is 5 reduced to the smaller class's
distinct group count (realized: 5 folds on build/component-vocab/
opponent/forward-time, 4 on opponent-hull/opponent-family, 3 on
seed-cell). No tuning, no model selection — fixed designed parameters
(`ADVERSARIAL_VALIDATION_PARAMS`). The diagnostic classifier's outputs
never feed any surrogate; leakage controls of the surrogate pipeline are
unaffected.

### Thresholds

Interpretation bands are the spec-predeclared `ADVERSARIAL_AUC_BANDS`
(spec 31): `indistinguishable` (< 0.55), `weak_separation`
(0.55–0.70), `strong_separation` (≥ 0.70). Bands are descriptive
labels, not pass/fail — high separation on grouped splits is those
splits working as designed.

### Known caveats (predeclared in the implementation plan)

1. **Opponent-identity features**: feature records exclude build-side
   identity but include opponent identity features
   (`opponent_variant_id`, `opponent_hull_id`, …). Grouping prevents
   these one-hots from trivially saturating the opponent-split AUCs (a
   scored variant's ID is zero-variance during its fit), but
   opponent-split AUCs still reflect the full opponent feature block.
2. **Per-fold instability on coarse-grouped splits**: with ≤ 4 test-side
   groups, a fold scores one or two groups and its per-fold AUC is
   nearly meaningless (observed per-fold values of 0.0 and 1.0 around
   moderate pooled values). Only the pooled out-of-fold `value` is
   interpreted; per-fold AUCs are recorded as detail.

## Results

**Method (§Estimator). Statistic: pooled out-of-fold AUC per cell;
per-family mean/min/max over the seed panel. Thresholds (§Thresholds).**

| Split family | n seeds | mean AUC | min | max | band counts | CV group unit | test groups | n_train | n_test |
|---|---:|---:|---:|---:|---|---|---:|---:|---:|
| build | 10 | 0.492 | 0.469 | 0.518 | indistinguishable 10/10 | `build_key` | 475 | 17,027 | 4,335 |
| seed-cell | 10 | 0.515 | 0.480 | 0.557 | indist. 9, weak 1 | `campaign_seed_cell` | 3 | 16,931 | 4,431 |
| opponent | 10 | 0.483 | 0.330 | 0.638 | indist. 5, weak 5 | `opponent_variant_id` | 11 | 16,816 | 4,546 |
| opponent-hull | 10 | 0.596 | 0.271 | 0.941 | indist. 3, weak 4, strong 3 | `opponent_hull_id` | 4 | 18,727 | 2,635 |
| opponent-family | 10 | 0.498 | **0.122** | **0.960** | indist. 5, weak 1, strong 4 | `opponent_family` | 4 | 19,300 | 2,062 |
| component-vocab | 9 | 0.998 | 0.993 | 1.000 | strong 9/9 | `build_key` | 783 | 14,402 | 6,960 |
| forward-time | 1 | 0.820 | 0.820 | 0.820 | strong 1/1 | `build_key` | 486 | 17,090 | 4,272 |

Group counts and n_train/n_test shown for the first seed of each family.
Build/seed-cell/forward-time sizes are stable across seeds; grouped
opponent-side splits and component-vocab vary widely with the seed's
group draw (ranges in §Data). Raw per-cell entries
(including per-fold AUCs and group counts): `data/phase7/adversarial_auc_sweep_2026-07-12.json`.

**Reading — build (the M2 target).** Every seed lands in the
`indistinguishable` band, tightly clustered around chance (0.469–0.518).
With ~1.9k train builds vs ~475 held-out builds and a classifier free to
exploit the full feature space, held-out builds cannot be told apart
from training builds: the build split's test set sits **inside** the
TPE-concentrated training cloud. The methodology review's M2 statement —
'"build transfer rho 0.82" should be read as interpolation within a
TPE-concentrated cloud until nearest-neighbor-distance-stratified
metrics exist'
([methodology review](2026-07-11-phase7-methodology-review.md) §4) — is
now directly supported by measurement rather than by inference from TPE
behavior. Two implications, pulling in opposite directions:
the attempt-3 build-split rank metrics (and the seed-151 confirmatory
CatBoost promotion, whose claim boundary is already build-like splits
only) are **interpolation** results and must not be quoted as evidence
the surrogate generalizes to novel build designs; and for the
optimizer-integration use case this is the *relevant* regime — a
deployed surrogate scores TPE proposals drawn from the same concentrated
process, so interpolation fidelity is what the prequential replay
(roadmap item 5) will exercise.

**Reading — seed-cell.** At chance on 9/10 seeds (max 0.557): campaign
cells do not shift the feature distribution, consistent with attempt-3
treating seed-cell as a build-like split.

**Reading — component-vocab.** AUC > 0.99 on all 9 effective seeds:
held-out component one-hots are zero-variance in train, so the split
imposes complete, genuine vocabulary shift by construction. Its rank
metrics measure real extrapolation — the attempt-3 §2.4 component-vocab
discussion (whose recommendation 5 requested this diagnostic) now has
its premise confirmed: component-vocab is the one build-side split that
is *not* interpolation.

**Reading — forward-time.** 0.820 (strong): TPE proposals drift over
time, so the forward-time split measures genuine temporal extrapolation.
This is encouraging for the decision-relevance of the prequential replay
ablation: train-on-past / score-future is a real distribution-shift
task on this DB, not a re-shuffle.

**Reading — opponent hierarchy.** The per-cell estimates are unreliable
at these group counts (11 test variants / 4 test hulls / 4 test
families) and swing across seeds from 0.122 to 0.960 — including values
far below chance, meaning the direction of "difference" the classifier
learns from its CV-train groups anti-generalizes to other held-out
groups. The honest conclusion is the spread itself: with ≤ 18 train
groups, adversarial AUC cannot reliably characterize opponent-level
shift per cell, and no single opponent-split cell's AUC should be
quoted alone. Opponent-representation questions stay owned by the
opponent-panel data wave (roadmap item 7).

## Synthesis & decisions

1. The attempt-3 / seed-151 build-split promotion evidence is
   interpolation-qualified: quote it with the "within the TPE proposal
   distribution" scope. No existing decision changes — the claim
   boundary was already build-like splits only — but the item-2
   feature-profile ablation wave inherits the same reading, and its
   artifacts will now stamp this diagnostic per cell automatically
   (schema v4).
2. Component-vocab and forward-time are the two splits whose rank
   metrics measure genuine extrapolation on this DB; they are the right
   headline splits for any generality claim short of new data waves.
3. Opponent-level distinguishability needs more opponents, not more
   diagnostics — deferring to the opponent-panel wave is confirmed as
   the correct remedy path.
4. M2 is partially discharged: adversarial-validation AUC is
   implemented and stamped; nearest-neighbor overlap, rare-combination
   overlap, and sparse-ID ablation remain
   `diagnostic_not_implemented`, and the review's fuller remedy
   (nearest-neighbor-distance-stratified rank metrics) remains open.

## Open questions / next steps

- Distance-stratified rank metrics (M2's fuller remedy): stratify
  build-split rank fidelity by nearest-neighbor distance to train, so
  the interpolation region and its edge are scored separately.
- Whether the item-2 ablation profiles change the build-split AUC
  (a profile that drops the concentrated feature block could move the
  split toward genuine shift); the per-cell stamps in the item-2 wave
  will answer this for free.
- The forward-time drift (0.820) invites a drift-aware replay design:
  the prequential ablation should report performance as a function of
  temporal distance, not only pooled.

## Appendix — file map

- Producer: session scratchpad driver (`adversarial_auc_sweep.py`,
  not checked in — reproduction is any loop over the canonical cells
  calling `construct_splits` + `adversarial_cv_groups` +
  `adversarial_validation_entry` from
  `scripts/analysis/phase7_learned_surrogate_experiment.py` with the
  canonical config values above).
- Raw data: `data/phase7/adversarial_auc_sweep_2026-07-12.json`
  (gitignored; reproducible deterministically from the frozen DB).
- Charts: none.
- Dependent reports:
  [attempt-3 surrogate results](2026-07-12-phase7-attempt3-surrogate-results.md)
  (build-split reading qualified),
  [seed-151 confirmatory](2026-07-12-phase7-seed151-confirmatory.md)
  (same qualification),
  [methodology review](2026-07-11-phase7-methodology-review.md) (M2).
- Contract: spec 31 §"Adversarial-validation AUC" (schema v4);
  implementation plan
  `.claude/plans/archive/2026/2026-07-12-adversarial-validation-auc.md`.
