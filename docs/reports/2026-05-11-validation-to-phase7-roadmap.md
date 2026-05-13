---
type: report
status: shipped
last-validated: 2026-05-11
---

# Validation-To-Phase-7 Roadmap

## Abstract

This report consolidates the final Wave 1 honest-eval validation state,
the corrected Wave 1 optimization analyses, and the Phase 7 representation
materials into a single working roadmap. The honest-eval run completed on
2026-05-11 with clean resource audits. It does not support promoting c2 as the
production default. It supports c0a as the best mean top-K cell, c1 as the
best high-ceiling candidate-generation branch, and Phase 7 feature-substrate
work as the next cost-effective direction.

**Roadmap revision note (2026-05-12).** The empirical tables below are still
the 2026-05-11 validation evidence. The Phase 7 execution roadmap was tightened
after the learned-surrogate research and documentation audits: feature
selection, model-family selection, hierarchy splits, honest-eval reuse,
multi-fidelity screening, and optimizer integration now have explicit
claim-boundary gates before any new promotion claim. Spec 31 owns the durable
artifact contracts; this report records dated decisions and sequencing.

## 1. Methods

### 1.1 Data

Primary local artifacts:

- Honest-eval ledger:
  `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Fixed-resume orchestrator log:
  `data/honest_eval/orchestrator-20260511T032626Z.log`
- Live preliminary report:
  [2026-05-10-wave1-honest-eval-live-preliminary.md](2026-05-10-wave1-honest-eval-live-preliminary.md)
- Final honest-eval report:
  [2026-05-11-wave1-honest-eval-final.md](2026-05-11-wave1-honest-eval-final.md)
- Wave 1 training-log analysis:
  [2026-05-10-wave1-comprehensive-analysis.md](2026-05-10-wave1-comprehensive-analysis.md)
- Wave 1 trajectory analysis:
  [2026-05-10-wave1-optimization-trajectory.md](2026-05-10-wave1-optimization-trajectory.md)
- Phase 7 matchup-surrogate preliminary:
  [2026-05-11-phase7-matchup-surrogate-preliminary.md](2026-05-11-phase7-matchup-surrogate-preliminary.md)
- Phase 7 reference designs:
  [phase7-featurized-matchup-surrogate.md](../reference/phase7-featurized-matchup-surrogate.md),
  [phase7-learned-surrogate-research.md](../reference/phase7-learned-surrogate-research.md),
  [phase7-search-space-compression.md](../reference/phase7-search-space-compression.md)

The final honest-eval report was generated with:

```bash
uv run python scripts/analysis/wave1_honest_eval_report.py --root data/campaigns
uv run python scripts/honest_eval_digest.py
```

Additional read-only checks inspected process state, the generated Phase 7
SQLite DB, and smoke surrogate baselines. Three independent read-only audits
were also run: Wave 1 comparison, Phase 7 roadmap implications, and current
honest-eval operational status.

### 1.2 Estimators

The honest-eval final outputs compute build-panel means over balanced
`(build, opponent, replicate)` rows. Mean top-K oracle is the average over the
9 evaluated build panels in a cell.

The Phase 7 comparator gate uses global mean, opponent mean, build mean,
TWFE-additive, ridge-hybrid, and random-forest baselines over flat feature rows
from `scripts/analysis/phase7_baseline_surrogate.py`. They are diagnostic
checks for feature-substrate coherence, not tuned model-performance claims.

The statistics used in this report are unique ledger rows, complete panel
counts, complete-cell mean oracle fitness, completed-panel oracle mean and
standard error, generated DB row counts, and grouped-split RMSE.

### 1.3 Decision Criteria

This roadmap applies these decision rules:

- Final cross-cell build quality is decided from complete honest-eval panels.
- Mean top-K oracle is the production-relevant cell summary.
- Top-1 oracle is a high-ceiling signal that requires repeatability testing.
- Default selection requires at least one optimization cell to beat the
  random-feasible baseline.
- Phase 7 optimizer work must be gated by feature-substrate validation before
  custom BoTorch kernel implementation.
- Learned-surrogate promotion requires a predeclared primary split, metric,
  top-k value if applicable, final refit/deployment policy, candidate universe,
  model-promotion rule, and exploratory-vs-confirmatory label.
- The completed 2026-05-11 honest-eval ledger remains final evidence for the
  Wave 1 cell comparison in this report. For future learned-surrogate feature,
  model, or promotion decisions, reuse of this same ledger is
  `exploratory_selection`; final learned-surrogate claims require a fresh
  ledger or an explicit exploratory label.
- References remain design owners; empirical magnitudes remain in reports.

## 2. Final Validation State

**Method (§1.1).** Final per-cell `honest_eval.json` outputs and summary JSON.
**Statistic (§1.2).** Mean top-K oracle, top-1 oracle, and final audit status.
**Threshold (§1.3).** Complete cells should beat random baseline for optimizer
existence; c2 must beat c0a and c0b to justify promotion.

| Metric | Value |
|---|---:|
| evaluated builds | 54 |
| cells | 6 |
| matchups per build | 1,620 |
| expected complete matchups | 87,480 |
| final outputs written | 6 / 6 cells + summary |
| wrapper final AWS audit | clean |
| watchdog final AWS audit | clean |

**Reading.** The resumed run finished successfully. The late-result retry fix
and rebaked worker image closed the previous c1/c2 holes. The wrapper and
watchdog both reported zero tagged AWS instances after shutdown.

## 3. Completed-Cell Comparison

**Method (§1.2).** Complete-cell means use only build panels with all 1,620
expected matchup results.
**Statistic (§1.2).** Complete-cell mean oracle fitness and completed-panel
mean oracle fitness with standard error.
**Threshold (§1.3).** Incomplete cells are not decision evidence.

| Cell | Builds | Mean top-K oracle | Top-1 oracle |
|---|---:|---:|---:|
| c0a | 9 / 9 | -0.0906 | +0.1104 |
| c0b | 9 / 9 | -0.1042 | +0.0610 |
| c1 | 9 / 9 | -0.1131 | +0.2433 |
| c2 | 9 / 9 | -0.1413 | +0.0302 |
| c3 | 9 / 9 | -0.1417 | -0.0370 |
| random-baseline | 9 / 9 | -0.2571 | +0.1151 |

The top completed panel remains:

| Rank | Cell | Seed | Source rank | Oracle mean | SE |
|---:|---|---:|---:|---:|---:|
| 1 | c1 | 1 | 1 | +0.2433 | 0.0245 |
| 2 | c0a | 2 | 1 | +0.1104 | 0.0250 |
| 3 | c0b | 2 | 3 | +0.0610 | 0.0248 |
| 4 | c0b | 2 | 2 | +0.0401 | 0.0243 |
| 5 | c2 | 2 | 1 | +0.0302 | 0.0282 |

**Reading.** The completed panels reinforce the corrected Wave 1 analyses:
c2 does not beat c0a or c0b on mean top-K oracle, while c1 has the best
single completed candidate panel. c3 does not rescue warm-start. The correct
interpretation is c0a as the best mean cell, high-ceiling c1 as a follow-up
branch, and no c2 production promotion.

## 4. Comparison To Wave 1 Optimization Analyses

**Method (§1.3).** Compare completed-panel honest-eval evidence against the
training-log and trajectory reports.
**Statistic (§1.2).** Directional agreement between completed honest-eval
summaries and prior training-log/trajectory findings.
**Threshold (§1.3).** Treat training-log signals as priors and honest eval as
the oracle.

| Prior finding | Current validation read | Roadmap implication |
|---|---|---|
| c2 did not beat c0a/c0b in the training-log top-3 TWFE+EB diagnostic. | Direction confirmed by completed honest-eval cell means. | Do not promote c2 as production default from Wave 1. |
| c1 had the strongest training-log point estimate and high variance. | c1 owns the strongest completed panel but not the best completed-cell mean. | Treat c1 as the main candidate-generation branch to investigate with more seeds and budget. |
| c3's apparent trajectory win was axis-dependent and warm-start was not justified. | Final c3 mean top-K is below c0a, c0b, c1, and c2. | Keep warm-start quarantined; only revisit via focused warm-start/A3/pruner ablation. |
| Raw means were unsafe under opponent imbalance. | Honest-eval candidate extraction already uses the TWFE+EB path; current top completed panel aligns with the c1 high-ceiling signal. | Keep deconfounded candidate selection; do not revert to raw means. |

The fixed honest-eval run completed cleanly, but Wave 1 training logs include the
documented c1 pre-band-aid contamination and c2/c3 retry history. Training-log
signals therefore remain priors; honest eval remains the build-quality oracle.

## 5. Phase 7 Feature-Substrate Read

**Method (§1.2).** Generated DB and comparator-gate baselines after
rematerializing the completed honest-eval ledger and per-cell outputs.
**Statistic (§1.2).** Generated DB row counts and best grouped-split RMSE.
**Threshold (§1.3).** Comparator-gate baselines are diagnostics only;
optimizer integration waits for the spec-owned gates summarized in §6.4.

Current generated DB:

| Artifact | Value |
|---|---:|
| `training_matchups` | 21,362 |
| `honest_eval_matchups` | 87,480 |
| recovered exact logged builds | 2,374 |
| DB-reconstructed builds | 2,579 |
| honest-eval candidate builds | 150 |
| honest-eval output builds | 54 |
| unresolved honest-eval build IDs | 0 |

Comparator-gate best RMSE by split:

| Split | Best model | RMSE |
|---|---|---:|
| held-out build | random forest | 0.354 |
| legacy held-out component combination | random forest | 0.360 |
| held-out seed/cell | random forest | 0.360 |
| path-ordered forward split | random forest | 0.381 |
| held-out opponent | random forest | 0.623 |

**Reading.** The versioned feature substrate is coherent enough to run grouped
splits, trivial/statistical comparators, and the random-forest carryover
baseline. Opponent mean and TWFE-additive explain much of the row-level target
signal, while random forest adds build-side signal on held-out build,
legacy component, seed/cell, and path-ordered forward splits. The component
split in the 2026-05-11 comparator report used the then-current weaker
weapon-multiset plus hullmod key. Spec 31 now defines a stricter default
component fingerprint including slot IDs, hull ID, and flux allocation plus
k-combination overlap diagnostics; component-transfer evidence must be rerun
under that current contract before it supports a new claim. Held-out opponent
transfer is still much weaker, which supports the Phase 7 design requirement
to model opponent context explicitly and to preserve opponent-conditioned
small-slot decisions.

## 6. Finalized Roadmap

### 6.1 Immediate: Close Out Validation Artifacts

The honest-eval run is complete and the stale Phase 7 DB has been regenerated.
The remaining closeout work is to keep reports aligned with the final summary.

### 6.2 Default Read: Do Not Promote c2

Do not promote c2 as a production default from Wave 1. The completed evidence
argues against EB+Box-Cox as tested. All optimizer cells beat the evaluated
9-build random-feasible panel by mean top-K oracle, so this run supports the
directional read that optimizer cells are extracting signal. It is not a full
random-feasible population claim; promotion-grade random-baseline comparisons
should use repeated random panels or a predeclared uncertainty procedure. c2
is not the winning configuration.

Use c1 as the primary next investigation branch because it generated the best
completed panel. The next c1 work should test repeatability, not assume
dominance:

- more seeds,
- larger budget,
- top-K retention,
- honest-eval confirmation on balanced panels.

### 6.3 Warm-Start: Keep c3 Quarantined

Do not use c3 warm-start as a default. If it is revisited, scope it as a
focused ablation:

- warm-start on/off,
- A3/Box-Cox on/off,
- pruner interactions,
- equal logged-combat-budget comparisons,
- direct honest-eval oracle comparison.

### 6.4 Phase 7: Feature Table And Selection Protocol Before Better Optimizer

The next implementation wave should not jump directly to the custom BoTorch
kernel. Build the representation, leakage, selection, and validation substrate
first:

1. Keep the Phase 7 SQLite dataset reproducibly materialized from complete
   Wave 1 and honest-eval artifacts.
2. Preserve per-slot token/assignment features instead of only aggregate slot
   summaries, including stable slot `type`, `size`, `mount`, `angle`, `arc`,
   and `x/y` position.
3. Add or verify weapon attributes, hull stats, hullmod indicators, OP/flux
   economy, opponent features, build/opponent interactions, and
   small-slot-by-opponent composition features.
4. Run grouped baselines with held-out build, opponent, component combination,
   seed/cell, and path-ordered forward splits.
5. Add comparator-gate models: global mean, opponent mean, build mean,
   TWFE-additive, ridge-hybrid, and random forest.
   **Completed 2026-05-11** in the Phase 7 comparator-gate report.
   The component-combination split in that completed report is legacy evidence
   under the older key and must be corrected and rerun before current-spec
   component-transfer claims.
6. Run the learned-surrogate research gate and derive the next experiment plan:
   candidate model families, hyperparameter search spaces, nested grouped
   validation, leakage controls, calibration policy, and provenance.
   **Completed as design rationale on 2026-05-12**; spec 31 now owns the
   artifact contract.
7. Before new learned experiments, create an active implementation plan and
   update spec 31 with implementable artifact contracts for the feature-family
   registry, hierarchy scorecard, leakage diagnostics, `honest_eval_usage`,
   promotion declarations, config/CLI fields, JSON schema, and batch
   propagation. The plan must pass the repository plan-review and fresh-review
   gates before code changes.
8. Add a feature-family registry for every generated feature: family,
   generator template, parent families for interactions, and leakage-risk
   class. Feature selection is part of the estimator and must be nested inside
   the outer split.
9. Add hierarchy-aware split metadata and diagnostics. The roadmap ladder is:
   random-row smoke only, exact-opponent holdout, opponent-hull holdout,
   opponent-family/archetype holdout, seed/cell or campaign-regime holdout,
   and forward-time holdout. Only implemented split builders can support
   reportable claims.
10. Predeclare the primary endpoint before any learned-surrogate promotion:
   primary split, primary metric, primary top-k value if top-k is used,
   model-family policy (fixed or selected inside inner validation), feature-
   selection policy, final refit/deployment policy, candidate universe, and
   model-promotion threshold.
11. Promote learned tree and sparse interaction baselines only through that
    reviewed experiment plan, after the comparator gate has run. The canonical
    spec-31 model/split matrix is a fixed comparison matrix; it is exploratory
    for single-winner claims unless it uses a predeclared fixed winner policy
    or is replaced by a fully nested model-family selector.
12. Report top-k recall against honest-eval rankings without training, tuning,
    feature selection, model-family selection, or calibration on the cited
    honest-eval rows. If prior honest-eval diagnostics affected the model or
    promotion rule, the result is exploratory unless a fresh honest-eval ledger
    confirms it.

Spec 31 owns the durable materialization and learned-artifact contracts. The
next implementation plan should satisfy the current spec and close these
roadmap-level prerequisites:

- JSONL optimizer logs remain authoritative for exact logged rows.
- DB-reconstructed builds are labeled as reconstructed and not treated as exact
  logged builds unless cross-checked.
- Honest-eval candidate builds resolve through the same extraction path used by
  the evaluator.
- The generated tables are replaced on rerun; no stale rows survive a smaller
  materialization.
- The rematerialized DB reports full ledger coverage for the completed run and
  zero unresolved honest-eval build keys.
- Row-kind breakdown remains inspectable; any additional skipped-row/cache-hit/
  invalid-spec reporting must be defined in spec 31 before it becomes a
  required artifact field.
- Feature/provenance schema versioning is sufficient to regenerate rows later.
- Feature-family registry coverage is complete for the schema/profile used by
  any selection run.
- Provenance/context fields such as source path, trial number, row kind,
  source kind, rank, campaign, seed, and batch/job IDs are diagnostic-only by
  default unless a reviewed plan explicitly scopes a split-specific claim that
  allows them.

Grouped-validation gate summary:

- Held-out build, opponent, component-combination, seed/cell, and forward-time
  splits are all reported.
- Any stronger split claim, such as opponent-hull or opponent-family transfer,
  has an implemented group-key function and records the exact supported claim.
- Trivial comparators are included.
- Honest-eval target rows are excluded from comparator fitting.
- Top-k recall is reported against honest-eval rankings without fitting or
  tuning on the cited honest-eval rows.
- `honest_eval_usage` is recorded as `diagnostic_only`,
  `exploratory_selection`, or `final_claim`; adaptive reuse of a ledger is
  labeled exploratory unless confirmed on a fresh ledger.
- Canonical learned runs emit forbidden-key overlap, adversarial-validation
  AUC by hierarchy level, rare-combination overlap, nearest-neighbor overlap,
  and sparse-ID ablation diagnostics, or explicit `not_applicable` reasons.
- Multi-fidelity screening or successive halving is exploratory until the run
  records a cheap-vs-full fidelity rank-preservation diagnostic.
- Active-learning repeat allocation, uncertainty-based candidate selection,
  or optimizer-allocation claims require grouped calibration/proper-score
  diagnostics by split, opponent group, and score regime.
- Failures by opponent family, score regime, and campaign cell are inspected
  before learned tree baselines, sparse interaction models, or optimizer
  integration.

### 6.5 Phase 7 Optimizer: Residual Online Search

Only after the feature substrate passes the grouped-validation checklist should
the optimizer upgrade proceed:

```text
observed fitness
  = supervised_matchup_surrogate(build, opponent)
  + online_BO_residual(build, opponent)
```

The first integration should be model-assisted search: candidate prefiltering
or prior mean can come before calibrated uncertainty. Active-learning repeat
allocation requires calibrated uncertainty or ranking-confidence evidence
under grouped diagnostics. The full custom BoTorch sampler should follow only
after cheaper gates show value under the same claim-boundary rules.
Optimizer-facing kernels, archetype priors, and feature blocks should be
restricted or regularized according to nested grouped feature-selection
evidence, not by historical design preference alone.

### 6.6 Explicit Deferrals

Fighter-wing decisions and weapon-group assignment remain outside the current
optimizer decision space. Carrier/fighter optimization should be scoped as a
separate Phase 7.1-style expansion rather than folded silently into the Phase
7 kernel work.

## 7. Audit Findings And Dispositions

Three independent read-only audits were run against the current artifacts.
Their shared findings are incorporated above:

- c2 argues against promotion under completed honest eval.
- c1 has the best high-ceiling signal but needs repeatability testing.
- c3 is not justified as a default warm-start path.
- all optimizer cells beat random-feasible by mean top-K oracle.
- Phase 7 must stage feature-substrate validation before custom optimizer work.
- Slot geometry and opponent-conditioned small-slot behavior are not optional.
- Fighter bays and weapon grouping are explicit deferrals.

A second documentation/literature audit wave on 2026-05-12 tightened the
Phase 7 learned-surrogate roadmap:

- feature selection, model-family choice, preprocessing, HPO, and calibration
  are one estimator for validation purposes;
- model-family winners require a predeclared fixed policy or nested selection;
- repeated use of the same honest-eval ledger is exploratory unless confirmed
  on a fresh ledger;
- hierarchy ladders need concrete group-key functions before reportable use;
- multi-fidelity screening needs cheap-vs-full rank-preservation evidence;
- older custom-kernel references were downgraded from asserted mechanisms to
  design hypotheses where the cited literature did not directly support them.

The 2026-05-12 documentation/literature audit findings required documentation
changes only; no code changes were made in that audit pass.

## 8. Next Checks

- Create an active implementation plan for the spec-31 artifact-contract
  upgrade, including plan-review and independent review gates before code.
- Define concrete JSON/config/CLI contracts for feature-family registry,
  hierarchy scorecard, `honest_eval_usage`, leakage diagnostics, promotion
  declaration, and batch propagation.
- Correct the component-combination split to the current spec or explicitly
  relabel old comparator evidence as legacy until rerun.
- Run the existing spec-31 fixed model-family comparison matrix only after the
  artifact-contract upgrade, or write a separate plan for a fully nested
  model-family selector; do not mix those claims.
- Predeclare the primary endpoint, primary split, primary top-k value, final
  refit/deployment policy, candidate universe, and promotion rule before
  running CatBoost, sparse interaction, or feature-selection experiments.
- Treat held-out-opponent transfer as the primary failure surface, then add
  opponent-hull and opponent-family/archetype stress tests only after their
  group-key builders exist.
- Treat the current honest-eval ledger as `exploratory_selection` for future
  learned-surrogate model development; reserve final learned-surrogate claims
  for a fresh ledger or label them exploratory.

## Appendix A. File Map

- Live ledger:
  `data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl`
- Fixed-resume log:
  `data/honest_eval/orchestrator-20260511T032626Z.log`
- Partial analyzer:
  `scripts/analysis/wave1_honest_eval_partial.py`
- Honest-eval final report script:
  `scripts/analysis/wave1_honest_eval_report.py`
- Phase 7 materializer:
  `scripts/analysis/phase7_materialize_matchups.py`
- Phase 7 baseline script:
  `scripts/analysis/phase7_baseline_surrogate.py`
- Phase 7 generated DB:
  `data/phase7/wave1_matchups.sqlite`
- Owning data spec:
  [../specs/31-phase7-matchup-data.md](../specs/31-phase7-matchup-data.md)
- Phase 7 reference docs:
  [../reference/phase7-featurized-matchup-surrogate.md](../reference/phase7-featurized-matchup-surrogate.md),
  [../reference/phase7-learned-surrogate-research.md](../reference/phase7-learned-surrogate-research.md),
  [../reference/phase7-search-space-compression.md](../reference/phase7-search-space-compression.md)
