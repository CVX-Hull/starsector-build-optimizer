---
plan_type: implementation
status: implemented
created: 2026-05-14
approved: null
implemented: 2026-05-14
owner: codex
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-05-14-phase7-v3-evidence-refresh.md
implementation_commit: null
post_impl_audit: passed
---

# Phase 7 V3 Evidence Refresh

## Context

The Phase 7 artifact-contract upgrade is committed as `d05ce8f`, but the
archived implementation plan still records `implementation_commit:
not_committed`. Current code also uses feature schema v3 and the corrected
component fingerprint, while the preliminary comparator and learned-surrogate
reports still describe v2 and the legacy component split. The next evidence
pass must rerun local comparator and learned-surrogate artifacts under the
current contract before any optimizer-integration or custom-BO work.

## Scope

DONE in this plan means:

- the archived artifact-contract plan records commit `d05ce8f`;
- a current-schema comparator artifact is produced locally with the fixed
  spec-31 comparator ladder;
- a current-schema learned-surrogate artifact is produced locally using the
  refreshed comparator context and `honest_eval_usage=exploratory_selection`;
- a new dated Phase 7 report records the 2026-05-14 v3 evidence and
  explicitly supersedes the v2 / legacy-component evidence for current-contract
  claims;
- focused tests, full tests, active-plan validation, and post-implementation
  audit pass.

Out of scope:

- spending AWS budget or re-enabling disabled AWS learned-batch configs;
- adding new model families;
- adding opponent-hull or opponent-family split builders;
- claiming final learned-surrogate promotion from the reused honest-eval
  ledger.

## Critical Files

- `.claude/plans/archive/2026/2026-05-12-phase7-artifact-contract-upgrade.md`
- `docs/reports/2026-05-14-phase7-v3-evidence-refresh.md`
- `docs/reports/INDEX.md`
- `data/phase7/wave1_comparator_gate_2026-05-14.json`
- `data/phase7/learned_surrogate_v3_local_2026-05-14.json`

## Public Concept Ledger

- `feature_schema_version=3`: the current feature schema, including static
  geometry, arc-pressure summaries, opponent parity, and deterministic feature
  profiles.
- `component` split: the current spec-31 component fingerprint, including hull
  ID, slot assignments, hullmods, vents, and capacitors.
- `honest_eval_usage=exploratory_selection`: reused honest-eval rows may guide
  learned-surrogate development but cannot support final promotion claims.
- `comparator artifact`: fixed scikit-learn comparator ladder with no HPO.
- `learned artifact`: current learned matrix over the predeclared learned
  model families, using nested grouped HPO inside each outer split.

## Change Family Matrix

| Family | Change | Verification |
|---|---|---|
| Plan metadata | Replace stale `implementation_commit: not_committed` with `d05ce8f`. | `git diff`; plan archive inspection. |
| Comparator evidence | Run `phase7_baseline_surrogate.py` against `wave1_matchups.sqlite` with split/model `all`, top-k `1,3,5`, tree count `80`, current default feature profile. | JSON artifact has schema v3, feature profile, source DB path, 5 spec splits, 6 spec comparator models, 30 total results, top-k recall at `1,3,5`, and component overlap diagnostics at `k=1,2,3`. |
| Learned evidence | Run `phase7_learned_surrogate_experiment.py` against the refreshed comparator artifact with split/model `all`, HPO defaults, top-k `1,3,5`, and `honest_eval_usage=exploratory_selection`. | JSON artifact completes 5 spec splits × 3 spec learned model families, records exact expansion, and validates all spec-31 contract objects. |
| Reports | Create a new `2026-05-14` report and index entry; mark older v2 reports as prior evidence only if edited at all. | Report standard self-check, stale-reference grep, and tests. |

## Canonical Path Statement

Local evidence is authoritative for this phase. AWS learned-batch artifacts
remain infrastructure diagnostics and are not rerun. If a full local learned
run is too slow to complete in this session, the plan must be updated before
substituting a smaller run.

## Fixture Parity Statement

No synthetic fixtures replace the real Phase 7 DB for empirical artifacts.
Tests continue to use existing lightweight fixtures; reports cite only current
local artifacts generated from `data/phase7/wave1_matchups.sqlite`.

## Implementation Steps

1. Update archived plan commit metadata.
2. Preflight optional learned-model dependencies by importing CatBoost through
   `uv run`; the full learned run must include all three spec model families,
   so missing CatBoost is a blocker rather than a skipped model.
3. Run the v3 comparator command:
   `uv run python scripts/analysis/phase7_baseline_surrogate.py data/phase7/wave1_matchups.sqlite --split all --model all --tree-count 80 --top-k 1,3,5 > data/phase7/wave1_comparator_gate_2026-05-14.json`.
4. Inspect the comparator JSON for:
   `feature_schema_version=3`, `feature_profile`, source DB path, the five spec
   splits (`build`, `opponent`, `component`, `seed-cell`, `forward-time`), the
   six spec comparator models (`global_mean`, `opponent_mean`, `build_mean`,
   `twfe_additive`, `ridge_hybrid`, `random_forest`), `result_count=30`,
   split claim metadata, group-key fields/functions, top-k recall at `1,3,5`,
   and component train/test overlap diagnostics at `k=1,2,3`.
5. Run the v3 learned command:
   `uv run python scripts/analysis/phase7_learned_surrogate_experiment.py data/phase7/wave1_matchups.sqlite --split all --model all --top-k 1,3,5 --comparator-json data/phase7/wave1_comparator_gate_2026-05-14.json --honest-eval-usage exploratory_selection --output data/phase7/learned_surrogate_v3_local_2026-05-14.json`.
6. Inspect the learned JSON for all 15 split/model results and exact expansion
   to the five spec splits and three spec learned model families
   (`random_forest_tuned`, `catboost_regressor`, `sparse_pairwise_ridge`).
   Validate top-level and per-result `claim_boundary`, `model_family_policy`,
   `feature_selection_protocol`, `feature_family_registry`,
   `feature_family_registry_sha256`, `hierarchy_scorecard`,
   `leakage_diagnostics`, `deployment_policy`, comparator context,
   `honest_eval_usage`, runtime parallelism metadata (`hpo_jobs`,
   `model_thread_count`), source DB path, and feature schema/profile.
7. Create `docs/reports/2026-05-14-phase7-v3-evidence-refresh.md` with
   methods before results, the supervised-learning checklist, primary split,
   primary metric, primary top-k value, model-promotion rule, exploratory vs
   confirmatory label for every table, runtime parallelism, stale-v2
   comparison, and next-step interpretation. Add it to
   `docs/reports/INDEX.md`.
8. Run focused tests, full tests, mechanical checks, stale-reference greps,
   empirical-report-standard self-check, active-plan validation, and
   post-implementation audit.
9. Mark this plan implemented, record `implementation_commit: not_committed`,
   move it to archive, and leave commit for the user-requested commit step.

## Test Plan

- `uv run python -c "import catboost; print(catboost.__version__)"`
- `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v`
- `uv run pytest tests/ -v`
- `uv run python scripts/validate_active_plans.py`
- `git diff --check`
- `python -m py_compile scripts/analysis/phase7_baseline_surrogate.py scripts/analysis/phase7_learned_surrogate_experiment.py`
- `rg -n "feature_schema_version\\s*=\\s*2|FEATURE_SCHEMA_VERSION = 2|legacy component|weapon multiset|weapon-multiset|v2" docs/reports/2026-05-14-phase7-v3-evidence-refresh.md`
- Manual report-standard check against `docs/CONVENTIONS.md`: Methods precede
  Results; every Results section has Method/Statistic/Threshold; appendix file
  map exists; supervised-learning checklist is present.

## Post-Implementation Audit And Retirement

Status: passed.

Audit waves:

- Initial implementation audit:
  `019e2add-6ca8-7001-b905-ccfeb521e2fd`,
  `019e2add-6cc7-71a2-9596-e4ac92ddf1f1`,
  `019e2add-6ce4-7d52-a965-59c6022dc7c7`.
- Final fresh-eye audit:
  `019e2aea-57b5-7c92-9baa-2d5aed8d2380`,
  `019e2aea-57ed-7c40-95ec-03b50c05671c`,
  `019e2aea-57cf-7d20-8ccc-5f166fa99f25`.

Resolved findings:

- Recomputed top-level learned feature-family registry and comparator context
  from per-result artifacts.
- Made leakage diagnostics split-aware and changed top-level split-all
  forbidden-overlap status to `not_applicable`.
- Added exact and k=1/2/3 component-overlap diagnostics to comparator and
  learned artifact contracts.
- Added comparator overlap counts for all splits.
- Improved semantic feature-family registry labels and templates.
- Completed report-standard gaps: sample sizes, metric formulas, HPO spaces,
  preprocessing, headline numbers, component-overlap table, and appendix map.
- Updated learned default comparator artifact from the superseded 2026-05-11
  artifact to the 2026-05-14 v3 artifact.
- Tightened learned-batch validation so failed leakage diagnostics reject a
  job payload.
- Removed duplicate keys from the learned per-result payload.

Verification:

- `uv run python -c "import catboost; print(catboost.__version__)"` passed.
- `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v` passed.
- `uv run pytest tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v` passed after final audit fixes.
- `uv run pytest tests/ -v` passed: 922 passed, 1 skipped.
- `uv run python scripts/validate_active_plans.py` passed.
- `git diff --check` passed.
- `uv run python -m py_compile scripts/analysis/phase7_baseline_surrogate.py scripts/analysis/phase7_learned_surrogate_experiment.py src/starsector_optimizer/phase7_learned_batch.py` passed.

Retirement requires:

- frontmatter `status: implemented`;
- `implemented: 2026-05-14`;
- `implementation_commit: null` until the user requests commit;
- `post_impl_audit: passed`;
- move to `.claude/plans/archive/2026/`.

## Deferred Items

- AWS rerun: deferred because the local full run is the modeling authority and
  AWS would duplicate evidence at additional cost.
- Opponent-hull/opponent-family split builders: deferred because spec-31
  requires explicit group-key builders before those claims.
- Final promotion claim: deferred because the reused honest-eval ledger is
  exploratory for learned-surrogate decisions.

## Plan Review Gate

Status: passed

Self-review checklist:

- Writing quality: passed
- DDD/TDD sequencing: passed
- Engineering principles: passed
- Design invariants: passed

Plan-review skill: `.claude/skills/plan-review.md`.

Disposition:

- Corrected dated-report handling to create a new `2026-05-14` report and
  update `docs/reports/INDEX.md`.
- Corrected comparator command to stdout redirection.
- Added explicit comparator, learned-artifact, report, dependency, and
  stale-reference checks required by spec 31 and report conventions.
- Added focused baseline tests, post-implementation audit section, and
  retirement checklist.

## Fresh-Eye Review Gate

Status: passed

Auditors:

- Pattern consistency: `019e24d1-9947-7ed3-b5b8-d75e9611e33a`, passed after
  dispositions.
- Spec alignment: `019e24d1-9963-72a3-9afe-4832180cbe4e`, passed after
  dispositions.
- Engineering/design invariants: `019e24d1-99c2-70e1-8728-f40a6eb7991e`,
  passed after dispositions.

Findings and dispositions:

- New May 14 evidence in old dated reports: fixed by creating a new dated
  report and indexing it.
- Invalid comparator command: fixed by using shell stdout redirection.
- Comparator validation underspecified: fixed by requiring 30-result matrix,
  metadata, top-k recall, and component overlap diagnostics.
- Learned artifact validation underspecified: fixed by requiring all stable
  contract objects and runtime parallelism metadata.
- Missing CatBoost preflight: fixed by making dependency import a blocker.
- Missing stale-reference checks and report-standard checks: fixed in the test
  plan.
- Missing lifecycle audit/retirement section: fixed.
