---
type: report
status: shipped
last-validated: 2026-07-11
---

# Phase 7 Surrogate Methodology Review

## Abstract

Multi-agent adversarial review (2026-07-11) of the Phase 7 learned-surrogate
methodology as recorded in
[2026-05-16-phase7-seven-split-evidence.md](2026-05-16-phase7-seven-split-evidence.md)
and implemented in `scripts/analysis/phase7_baseline_surrogate.py`,
`scripts/analysis/phase7_learned_surrogate_experiment.py`,
`src/starsector_optimizer/phase7_matchup_data.py`, and
`src/starsector_optimizer/matchup_features.py`. The review confirms the
leakage guardrails are sound (HPO nested inside outer-train, outer-test and
honest-eval targets never fit or tuned on, forbidden-key overlaps zero) but
finds the **evaluation design measures the wrong quantities**: pooled metrics
are dominated by opponent-difficulty prediction rather than build ranking, the
component split is bijectively identical to the build split, model-family
selection happens on outer-test with no uncertainty quantification, and the
seed-17 outer-test partitions have absorbed four adaptive evidence waves
untracked. This report does not supersede the 2026-05-16 evidence (the
measurements stand); it revises how those measurements should be read and
replaces the planned next evidence wave with a redesigned one. Companion
literature synthesis:
[phase7-surrogate-methodology-gaps.md](../reference/phase7-surrogate-methodology-gaps.md).
Companion cost analysis:
[2026-07-11-aws-cost-analysis.md](2026-07-11-aws-cost-analysis.md).

## 1. Review method

Four parallel review streams, run 2026-07-11:

1. **Methodology map** — end-to-end reconstruction of the pipeline from code
   (not prose), cross-checked against the reports for discrepancies.
2. **Adversarial statistical review** — direct SQL queries against
   `data/phase7/wave1_matchups.sqlite` and the 2026-05-15/16 JSON artifacts,
   plus code review of split construction, HPO nesting, metrics, and
   comparator logic.
3. **Literature survey** — five themes (opponent representation, sparse
   interactions, holdout reuse, ranking-aware evaluation, mixed-space BO);
   synthesized into the companion reference doc.
4. **AWS execution/cost inventory** — separate report.

Findings are labeled **CONFIRMED** (verified against code, artifacts, or the
DB — the two most load-bearing were independently re-verified during report
authoring) or **PLAUSIBLE** (mechanism identified, magnitude unmeasured).
Ranked by severity.

## 2. Critical findings

### C1 (CONFIRMED) — Pooled metrics measure opponent-difficulty prediction, not build ranking

`regression_metrics` (`phase7_baseline_surrogate.py:596-601`) computes
Spearman rho over pooled test rows. Direct variance decomposition of the
21,362 training targets: **69.8% of target variance is between-opponent**.
The `opponent_mean` lookup comparator alone achieves pooled rho 0.738 on the
build split; CatBoost's headline 0.821 is ~0.08 above a build-blind baseline.

The opponent-hull split is degenerate: the seed-17 holdout is
{scintilla, tarsus, buffalo, nebula} — 385 rows, 5 variants, of which 4 have
zero within-opponent target variance (constant ±1.0). 99.8% of that test
panel's variance is between-opponent. The reported rho 0.835 (§2.5 of the
seven-split report, read there as "high rank signal") means only "the model
can tell that buffalos die and scintillas win" and contains no evidence about
ranking builds — the surrogate's entire downstream job.

**Remedy** (already prescribed by
[phase7-featurized-matchup-surrogate.md](../reference/phase7-featurized-matchup-surrogate.md)
"rank correlation by held-out build" but never implemented): primary metric =
mean per-opponent Spearman/Kendall over held-out builds, excluding opponents
whose within-opponent target SD falls below a noise floor (median
within-opponent SD is 0.275; several opponents are constant); plus
build-aggregate rank metrics and top-k regret. `stratified_metrics`
(`phase7_baseline_surrogate.py:633-650`) needs an `opponent_variant_id`
stratum.

### C2 (CONFIRMED) — The component split is the build split; seven splits are ~3 independent stressors

`component_fingerprint_json` returns `_build_json(build)` verbatim and
`build_key` is the SHA-256 of that same string
(`phase7_matchup_data.py:98-109`; re-verified 2026-07-11). The two splits
group rows by the same equivalence classes and differ only in shuffle order
(test N 4,287 vs 4,303; CatBoost RMSE 0.339 vs 0.351). The component split
does **not** test transfer to novel component combinations — the §2.4
diagnostics of the seven-split report show 99.3% of k=1 and 98.7% of k=2
component combinations overlap train/test.

Empirically, seed-cell and forward-time also track the build split (exact-build
overlap across cells is zero, so cell holdout is a correlated build holdout).
The effective independent stressors are: within-distribution build holdout,
exact-opponent holdout, and opponent-group holdout. Any "consistent across N
splits" reading is inflated.

**Remedy**: redefine the component split as **component-vocabulary holdout**
(hold out all builds containing a chosen weapon/hullmod ID, or a chosen k=2
pair never seen in train) — the question BO acquisition actually needs
answered. Treat the current component split as "build split, seed B."

### C3 (CONFIRMED) — Family selection on outer test, single-draw splits, no uncertainty, inconsistent comparator baselines

HPO is properly nested, but the §3 decisions of the seven-split report ("keep
CatBoost…", "tuned RF for opponent splits…") were selected from the same
outer-test numbers reported as their evidence — best-of-3 learned vs best-of-6
comparator, both test-selected. Every split is a single draw (seed 17), with
no SE, bootstrap, or repeats; rows are clustered by build and opponent so
nominal test N (~4,300) wildly overstates effective N (on opponent-hull,
effectively 4 groups). Deltas of −0.005 to −0.018 RMSE drive the narrative.

The delta column also mixes baselines: `load_comparator_context`
(`phase7_learned_surrogate_experiment.py:645-648`) matches CatBoost/RF to the
comparator `random_forest` but sparse-ridge to `ridge_hybrid`. On
opponent-hull the reported learned deficit is +0.072 vs RF but +0.105 vs the
split's actual best comparator (ridge). The comparator RF also ran 80 trees
against a learned family tuned up to 800 trees with 24 trials — a
tuning-budget asymmetry presented as a model-family comparison.

**Remedy**: repeated grouped splits (≥10 seeds; LOGO is cheap for comparators
across 22 hulls / 54 variants but see the distributional-bias caveat in the
companion reference doc — prefer repeated grouped k-fold); cluster bootstrap
(resample builds and opponents, not rows) for CIs; family selection nested or
predeclared; headline deltas vs the best comparator per split.

### C4 (CONFIRMED) — Outer-test reuse across evidence waves is untracked

The claim-boundary machinery tracks honest-eval reuse only. The seed-17
outer-test partitions have driven four adaptive waves (05-11 comparator, 05-12
learned draft, 05-14 v3 refresh, 05-16 seven-split; the 05-14 and 05-16
reports show identical per-split Train/Test N). In between, the feature
schema went v1→v3, model families were added/dropped, and HPO spaces were set
— all informed by metrics on those same test rows. This is textbook adaptive
holdout reuse; the small deltas in C3 are exactly the effect sizes such reuse
manufactures.

**Remedy**: rotate the outer split seed every wave (grouping functions fixed,
draw changes) or run repeated splits so no partition accumulates history;
predeclare one reserved confirmatory seed for promotion-grade claims; add
outer-test reuse lineage to the spec 31 artifact contract, parallel to
`honest_eval_usage`.

## 3. High-severity findings

### H1 (CONFIRMED) — Censored target with 58.7% endpoint mass; Gaussian per-row loss misspecified

Of 21,362 targets, 9,393 (44.0%) are exactly −1.0 and 3,144 (14.7%) exactly
+1.0 (re-verified 2026-07-11 by direct query). `hp_differential` is a
saturated outcome; all models minimize unclipped squared error on it
(sparse-ridge posts RMSE 2.02 — only possible with predictions outside
[−1, 1]). RMSE mixes classification error (which endpoint) with
conditional-magnitude error. Only 388 (build, opponent) pairs repeat in
training rows, but the honest-eval panel (54×54×30 replicates) is an
unexploited noise-model source. **Remedy**: two-part model (score-regime
classifier + contested-regime magnitude — the regime thresholds already exist
at `phase7_baseline_surrogate.py:62-63`) or censored likelihood; fit a
matchup-level heteroscedastic noise model from honest-eval replicates;
evaluate and calibrate at the build-aggregate level where the BO consumer
lives.

### H2 (CONFIRMED) — Raw RMSE compared across panels with different target distributions

Per-split global-mean RMSE (in the 05-15 artifact, never surfaced): build
0.807, opponent 0.756, opponent-hull 1.145, opponent-family 1.382. The §2.5
hierarchy ordering partly reverses under skill scores
(1 − MSE/MSE_trainmean). **Remedy**: report skill scores and panel target
mean/SD alongside raw RMSE; never compare raw RMSE across panels.

### H3 (CONFIRMED) — Honest-eval top-1 recall over 54 builds is a Bernoulli coin flip, and not clean holdout

The declared promotion metric (`primary_top_k = 1` over 54 builds) flips
0↔1 between near-identical models in the 05-16 artifact; chance level
(k/54) and CIs are never reported. Additionally, the diagnostic predicts
honest-eval rows with the model fit on the split's outer-train — but
honest-eval candidate builds are drawn from the same Wave-1 logs and can
appear in outer-train, so it is not a held-out ranking check. **Remedy**:
rank correlation over the 54 build means + top-k oracle regret with
build-level bootstrap CIs; overlap curves across all k; exclude degenerate
always-±1 opponents from the predicted build aggregate.

### H4 (CONFIRMED) — `sparse_pairwise_ridge` is a representation strawman

The pipeline (`phase7_learned_surrogate_experiment.py:477-491`) sketches
degree-2 interactions over **unscaled** inputs (hitpoints in thousands next to
0/1 indicators; products up to ~1e7), over all feature pairs rather than the
build×opponent cross block, with an HPO alpha floor of 0.001. Its failure
shape matches the unscaled `ridge_hybrid` comparator. The "sparse interactions
are noncompetitive" verdict attaches to this artifact, not to interaction
modeling. **Remedy**: factorization-machine-style low-rank bilinear models
(see companion reference doc §2 and §Shortlist) — with standardized numerics
and cross-block-only interactions as the minimal salvage of the current
family.

### H5 (counts CONFIRMED, remedies PLAUSIBLE) — Opponent transfer is data-starved, not only representation-starved

The opponent axis rests on 54 variants / 22 hulls / ~15 cells, with 28–2,194
rows per variant (78× imbalance). Pruned trials (3,922 rows) truncate opponent
exposure conditional on doing badly, and the 5C curriculum shifted exposure
over time — per-opponent target distributions are MNAR-censored and unweighted.
No model family identifies opponent functional structure from ~40 groups.
**Remedy ladder**: (1) dedicated opponent-panel data wave — wide stock-variant
panel, randomized exposure, balanced replicates (now cheap on AWS; see cost
report); (2) hierarchical partial pooling variant⊂hull⊂family with the feature
regression as prior mean; (3) pressure-axis opponent compression tested via
the planned `opponent-parity` profile, scored with per-opponent metrics (C1);
(4) out-of-fold opponent-difficulty encoding for non-opponent splits.

## 4. Medium/low findings

- **M1 (CONFIRMED)** Single inner-validation draw for HPO (~9 variants on the
  opponent split → winner's curse); trial models seeded `hpo_seed + idx` but
  the final refit uses `hpo_seed`, so the selected config never ran under its
  shipping seed (`phase7_learned_surrogate_experiment.py:563` vs `:1069`).
  Remedy: grouped k-fold inner CV, aligned seeds.
- **M2 (CONFIRMED gap)** The four unimplemented leakage diagnostics
  (adversarial-validation AUC, nearest-neighbor overlap, rare-combination
  overlap, sparse-ID ablation) are precisely the ones that would qualify the
  strong build-split numbers: TPE concentrates proposals, so "build transfer
  rho 0.82" should be read as interpolation within a TPE-concentrated cloud
  until nearest-neighbor-distance-stratified metrics exist.
- **M3 (CONFIRMED)** No split measures the deployment task: all player builds
  are one hull (`build_hull_*` features constant — zero cross-hull evidence is
  possible from this DB), and the comparator gate (per-row RMSE parity)
  answers a different question than the integration decision (sims saved at
  fixed top-k regret vs the incumbent TPE/TWFE+EB path at equal budget).
  Remedy: prequential replay ablation on existing logs — train on rows &lt; t,
  score the next proposal batch, measure rank fidelity and budget savings if
  the bottom-q surrogate-ranked proposals were skipped. Cross-hull claims
  require a small multi-hull data wave before any BoTorch kernel work claims
  hull generality.
- **L1 (CONFIRMED)** Report/code mismatches: learned-RF default is
  `n_estimators=200` in code, reported as 80 (the comparator's CLI value);
  `final_refit_policy` provenance string describes a refit step that never
  runs (fit is outer-train only, no deployment artifact);
  `feature_schema_version=3` constant leaks into feature vectors (harmless);
  §2.1/§2.2 delta columns use different comparator baselines without a label.

## 5. What survives

The artifact plumbing is strong: claim boundaries, forbidden-key checks
(all `exact_matchup_group` overlaps zero), provenance stamping, exploratory
labeling, and the hard rejection of `final_claim` without a fresh ledger. The
per-opponent metrics, repeated seeds, skill-score normalization, redefined
component split, and replay ablation are all implementable inside the existing
producer scripts **without new simulation data**. Only the opponent-panel
widening and cross-hull evidence need new sim spend.

## 6. Redesigned next evidence wave

Replaces §4 of the 2026-05-16 report as the current next-step list, in order:

1. **Evaluation-harness fix (blocks everything else)**: per-opponent rank
   metrics with noise-floor tie handling, sparse Kendall τ, top-decile-stratified
   τ, precision@k / regret@k, build-aggregate metrics, skill scores, cluster
   bootstrap CIs; repeated grouped splits (≥10 seeds) with rotated seeds; C2
   component-vocabulary split; M1 inner-CV fix. Re-run the existing 21-cell
   matrix under the fixed harness before trusting any prior reading.
2. **Feature-profile ablations** (the previously planned wave) under the fixed
   harness, on the repeated opponent-family/opponent splits.
3. **FM/bilinear interaction features** (H4 remedy) as a new model family,
   evaluated on unseen-family sparse-τ and regret@k.
4. **Within-opponent pairwise-ranking CatBoost** (PairLogit/YetiRank, groups =
   opponent, pairs gapped beyond noise floor) — cancels opponent main effects
   by construction.
5. **Prequential replay ablation** (M3) — the decision-relevant integration
   gate, from existing logs.
6. **Reuse-discipline adoption** (C4): rotated/predeclared seeds, opponent-family
   lockbox, Ladder-margin acceptance, model-info-sheet per wave (spec 31
   amendment).
7. **Opponent-panel data wave** (H5) — new sim spend, costed in the AWS report.

Compute for items 1–5 is CPU-only and now runs on AWS learned-batch (see
[2026-07-11-aws-cost-analysis.md](2026-07-11-aws-cost-analysis.md) for the
scope-change rationale and costs).

## Appendix — Review provenance

Conducted 2026-07-11 by four parallel review agents (methodology map,
adversarial statistical review with direct DB/artifact queries, literature
survey, AWS cost inventory), synthesized and spot-re-verified (C2 code
identity; H1 endpoint counts) by the coordinating session. Endpoint-mass
figures in H1 use exact float equality; tolerance-band counts (±0.001) are
9,496 / 3,206.
