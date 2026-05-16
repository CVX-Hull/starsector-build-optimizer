---
plan_type: implementation
status: implemented
created: 2026-05-15
approved: 2026-05-15
implemented: 2026-05-16
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
  - docs/reference/README.md
  - docs/reports/2026-05-14-phase7-v3-evidence-refresh.md
implementation_commit: not_committed
post_impl_audit: passed
---

# Phase 7 Seven-Split Evidence Wave

## Context

The current shipped Phase 7 evidence report covers schema-v3 comparator and
learned matrices over five splits. The code now supports the next opponent
transfer hierarchy levels, `opponent-hull` and `opponent-family`, but no dated
full evidence artifact or report covers the seven-split matrix.

## Scope

DONE means the local seven-split comparator and learned matrices have run over
the canonical transfer splits `build`, `opponent`, `opponent-hull`,
`opponent-family`, `component`, `seed-cell`, and `forward-time`; a new dated
report records the results and interpretation; and Phase 7 navigation points
to the new evidence without changing empirical claims outside reports.

Out of scope: AWS execution, optimizer integration, new model families, and
new feature-selection implementation. Those require a later plan after the
seven-split diagnostics are known.

## Critical Files

- `data/phase7/wave1_comparator_gate_2026-05-15.json`
- `data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json`
- `docs/reports/2026-05-16-phase7-seven-split-evidence.md`
- `docs/reports/INDEX.md`
- `docs/reference/README.md`
- `docs/reference/phase7-featurized-matchup-surrogate.md`

## Implementation Steps

1. Run the seven-split comparator gate locally with schema-v3 features, the
   spec-31 comparator model set (`global_mean`, `opponent_mean`,
   `build_mean`, `twfe_additive`, `ridge_hybrid`, `random_forest`), and top-k
   diagnostics. The report-local dated output artifact is
   `data/phase7/wave1_comparator_gate_2026-05-15.json`, produced by:

   ```bash
   uv run python scripts/analysis/phase7_baseline_surrogate.py \
     data/phase7/wave1_matchups.sqlite --split all --model all \
     --tree-count 80 --top-k 1,3,5 \
     > data/phase7/wave1_comparator_gate_2026-05-15.json
   ```

   Validate the artifact has `result_count = 42`, exactly the seven canonical
   splits, exactly the six comparator models, `feature_schema_version = 3`,
   `feature_profile = all`, and per-result `mae`, `rmse`, `spearman_rho`,
   `n_train`, `n_test`, fallback counts where applicable, split metadata,
   overlap counts, and stratified diagnostics.
2. Run the seven-split learned matrix locally with the current canonical model
   families, nested grouped HPO, the 2026-05-15 comparator artifact, and
   `honest_eval_usage=exploratory_selection`. The report-local dated output
   artifact is
   `data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json`, produced
   by:

   ```bash
   uv run python scripts/analysis/phase7_learned_surrogate_experiment.py \
     data/phase7/wave1_matchups.sqlite \
     --split all --model all --top-k 1,3,5 \
     --comparator-json data/phase7/wave1_comparator_gate_2026-05-15.json \
     --honest-eval-usage exploratory_selection \
     --output data/phase7/learned_surrogate_v3_seven_split_2026-05-16.json
   ```

   Validate the artifact has `result_count = 21`, exactly the seven canonical
   splits, exactly the three canonical learned models, `feature_schema_version
   = 3`, `feature_profile = all`, and stable spec-31 objects:
   `claim_boundary`, `model_family_policy`, `feature_selection_protocol`,
   `hierarchy_scorecard`, `leakage_diagnostics`, and `deployment_policy`.
   Inner validation must use the same grouping stressor inside outer training
   rows only, with blocked/rolling semantics for `forward-time`; if any split
   lacks enough inner groups, the result must be `insufficient_inner_groups`
   rather than a random-row fallback. Canonical leakage diagnostics must be
   present or carry explicit `not_applicable` reasons.
3. Write a new empirical report for the seven-split evidence. The report must
   include methods before results, supervised-learning checklist, file map,
   comparator-vs-learned tables, component-holdout overlap diagnostics for
   exact full fingerprints and k=1/2/3 component combinations, opponent
   hierarchy interpretation, leakage-diagnostic summary, and explicit
   exploratory claim boundaries for reused honest-eval diagnostics.
   Frontmatter must be `type: report`, `status: shipped`, and
   `last-validated: 2026-05-16`; it must supersede the 2026-05-14 report for
   current seven-split Phase 7 evidence.
4. Update `docs/reports/INDEX.md`, `docs/reference/README.md`, and the Phase 7
   reference note so current readers can find the new report. Do not copy
   empirical magnitudes into timeless reference prose except as pointers to the
   dated report.
5. Run verification: CatBoost import preflight, focused Phase 7 tests, full
   test suite, relevant `py_compile`, active-plan validation,
   stale-reference greps for supersession/report paths, explicit
   report-convention inspection against `docs/CONVENTIONS.md` including
   Methods before Results, supervised-learning setup, comparison statistics,
   diagnostics/thresholds, synthesis, open questions, and file map, and
   `git diff --check`.
6. Run post-implementation audit with independent fresh-eye review, fix any
   valid findings, and archive this plan with `status: implemented`,
   `implemented: 2026-05-16`, `implementation_commit`, and `post_impl_audit`.

## Plan Review Gate

- Status: passed
- Skill: plan-review

Self-review and three independent sub-agent plan audits were run before
implementation. Valid findings were incorporated into this plan:

- exact run commands and comparator artifact override;
- explicit seven-split names and 42/21 result-count checks;
- spec-31 comparator metrics, split metadata, component overlap diagnostics,
  hierarchy scorecard, leakage diagnostics, and learned stable-object checks;
- CatBoost preflight, full-suite verification, stale-reference greps, and
  explicit report-convention inspection;
- reference index inclusion and plan-retirement metadata.

## Fresh-Eye Review Gate

- Status: passed
- Review type: sub-agents fresh-eye audit
- Sub-agent lanes:
  - Pattern consistency: Peirce
  - Spec alignment: Raman
  - Engineering and invariants: James
- Dispositions: all valid findings were resolved in the plan before
  implementation.

## Verification

- `uv run python -c "import catboost; print(catboost.__version__)"` — passed; CatBoost `1.2.10`.
- `uv run python -m py_compile scripts/analysis/phase7_baseline_surrogate.py scripts/analysis/phase7_learned_surrogate_experiment.py src/starsector_optimizer/phase7_learned_batch.py` — passed.
- `uv run pytest tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v` — `83 passed`.
- `uv run pytest tests/ -v` — `933 passed, 1 skipped, 194 warnings`.
- Learned artifact shape check — `status=completed`, `result_count=21`, seven canonical splits, three learned model families, honest-eval lineage populated, `inner_validation_metadata` populated, sparse indicators classified as medium leakage risk.
- `git diff --check` — passed.

## Post-Implementation Audit

- Status: passed after fixes.
- Audit lanes: plan/code alignment, report/spec alignment, and design/documentation consistency.
- Valid findings resolved:
  - added honest-eval ledger lineage to learned artifact provenance and claim boundaries;
  - made sparse indicator and identifier-like features at least medium leakage risk;
  - documented missing leakage diagnostics as limitations rather than evidence of absence;
  - expanded the seven-split report with ML/statistical-learning methods, target variable, feature protocol, hyperparameters, HPO search spaces, metric formulas, comparison statistics, diagnostics, and numbered result references;
  - added explicit `inner_validation_metadata` to learned results and tests;
  - removed timeless empirical coverage prose from the Phase 7 reference and marked the current reference as shipped;
  - superseded the 2026-05-14 report in indexes and pointed current navigation at the 2026-05-16 report.

## Retirement

Archived to `.claude/plans/archive/2026/2026-05-15-phase7-seven-split-evidence-wave.md`
after verification and post-implementation audit.
