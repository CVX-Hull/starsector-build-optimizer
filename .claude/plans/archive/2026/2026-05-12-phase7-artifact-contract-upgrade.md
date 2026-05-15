---
plan_type: implementation
status: implemented
created: 2026-05-12
approved: null
implemented: 2026-05-12
owner: codex
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-05-11-validation-to-phase7-roadmap.md
implementation_commit: d05ce8f
post_impl_audit: passed
---

# Phase 7 Artifact Contract Upgrade

## Context

The 2026-05-11 roadmap requires a spec-31 artifact-contract upgrade before
more learned-surrogate experiments. The current code emits partial ML context,
but does not propagate explicit claim-boundary fields through CLI, batch config,
job commands, result validation, or merged artifacts. The current component
split also groups only weapon IDs plus hullmods, while spec 31 requires the
full component fingerprint.

## Scope

DONE in this plan means:

- learned experiment artifacts declare the claim boundary and feature/model
  selection policy in every result;
- batch configs propagate and validate those declarations;
- component holdout uses the current spec-31 component key;
- focused tests cover script output, batch propagation, result validation, and
  the corrected component grouping.

Out of scope:

- running a new full learned-model experiment;
- adding new model families or a nested selector implementation;
- implementing opponent-hull or opponent-family group-key builders.

## Critical Files

- `docs/specs/31-phase7-matchup-data.md`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `src/starsector_optimizer/phase7_learned_batch.py`
- `scripts/cloud/phase7_learned_batch.py`
- `examples/phase7-learned-batch.yaml`
- `tests/test_phase7_matchup_data.py`
- `tests/test_phase7_learned_surrogate_experiment.py`
- `tests/test_phase7_learned_batch.py`

## Public Concept Ledger

- New spec-clarification objects, emitted top-level and per-result:
  `claim_boundary`, `model_family_policy`, `feature_selection_protocol`,
  `deployment_policy`, `hierarchy_scorecard`, `leakage_diagnostics`.
- `honest_eval_usage`: enum string, one of `diagnostic_only`,
  `exploratory_selection`, `final_claim`. Default is `diagnostic_only`; batch
  examples use `exploratory_selection` because the current ledger has already
  influenced roadmap decisions. `final_claim` requires an explicit
  `--fresh-honest-eval-ledger-id`; simple override is rejected.
- `claim_boundary`: `target_variable="training_matchups.target"`,
  `honest_eval_diagnostic_target="honest_eval_top_k"`, `primary_split`,
  `primary_top_k`, `promotion_metric`, `promotion_threshold`,
  `higher_is_better`, `claim_label`, and `honest_eval_usage`.
- `model_family_policy`: `policy_type="fixed_matrix"`,
  `candidate_model_families`, `selected_model_family`, and
  `selection_scope="predeclared_fixed_matrix"`.
- `feature_selection_protocol`: `policy_type="fixed_profile_no_selector"`,
  `feature_profile`, `selected_feature_families`, `selected_feature_count`,
  `selector_family="none"`, `selector_hyperparameters={}`,
  `stability="not_applicable"`, `heredity_policy="not_applicable"`, and
  `selection_scope="no_feature_selection"`.
- `feature_family_registry`: actual per-feature registry keyed by generated
  column, with `family`, `template`, `parents`, and `leakage_risk`, plus
  `feature_family_registry_sha256`. The digest is over the canonical sorted
  JSON registry, not a substitute for the registry.
- `hierarchy_scorecard`: `split_level`, `group_key_fields`,
  `group_key_function`, `claim_supported`, `forbidden_cross_split_keys`, and
  overlap counts for exact opponent, hull ID, component combination, campaign
  cell, and exact matchup group where available. Component split records
  `component_key_definition="canonical_full_component_fingerprint"` and
  component overlap diagnostics for `k=1`, `k=2`, and `k=3`.
- `leakage_diagnostics`: named entries for forbidden-key overlap,
  adversarial-validation AUC, rare-combination overlap,
  nearest-neighbor overlap, and sparse-ID ablation delta. Unimplemented
  diagnostics must be represented as `{status: "not_applicable", reason: ...}`;
  no placeholder may be a bare boolean.
- `deployment_policy`: `final_refit_policy`,
  `candidate_universe`, and `deployment_artifact`.

## Change Family Matrix

| Family | Spec first | Tests | Implementation |
|---|---|---|---|
| Artifact contract | Clarify required object fields and defaults | Assert result JSON fields and batch validation | Add config fields, helpers, provenance/result emission |
| Batch propagation | Clarify batch command/config propagation | Assert command flags and merged artifact fields | Add dataclass/YAML fields, CLI flags, validation, merge checks |
| Component split | Clarify fingerprint fields | Assert slot/hull/flux distinctions split apart | Update `held_out_component_combination_split` key |

## Canonical Path Statement

The first upgraded run uses the existing fixed matrix: five splits by three
model families. It is not a nested model selector and does not perform feature
selection. Standalone script defaults are diagnostic-only; the current full
batch config must explicitly stamp `honest_eval_usage=exploratory_selection`.
`final_claim` is rejected unless a fresh honest-eval ledger ID is provided.

## Implementation Steps

1. Update spec 31 only where needed to make field names, defaults, JSON paths,
   and batch propagation unambiguous.
2. Add failing tests for:
   - parser/config defaults and explicit CLI overrides;
   - result artifact claim-boundary objects, feature registry, hierarchy
     scorecard, leakage diagnostics, and disabled feature-selection semantics;
   - batch config loading, job command flags, validation, and merge propagation;
   - spec-required batch gates: job identity, no skipped/partial/stale jobs,
     matching schema/config/provenance, dependency extra, bundle SHA256, clean
     code provenance, passing leakage checklist, comparator context,
     `merged.json`, and canonical-output protection for subset matrices;
   - component split key distinguishing slot placement, hull ID, vents, and
     capacitors.
3. Implement learned experiment contract helpers and emit them for completed,
   insufficient, and skipped outputs. Skipped optional-model artifacts include
   the same top-level claim-boundary fields, but canonical batch validation
   continues to reject skipped jobs for publication.
4. Implement batch config fields, command propagation, payload validation, and
   merged artifact preservation. Keep existing matrix invariants:
   `target_workers == len(splits) * len(models)`,
   `min_workers_to_start == target_workers`, and `publish_canonical` only for
   the full 15-job matrix.
5. Update component split grouping to serialize full component fingerprints:
   hull ID, sorted slot-to-weapon assignments, sorted hullmods, vents, and
   capacitors. Reuse `_canonical_build_dict()` or a helper derived from it
   rather than creating a second serialization.
6. Run focused tests after implementation and fix all failures at root cause.

## Test Plan

- `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v`
- `uv run pytest tests/ -v`
- `uv run python scripts/validate_active_plans.py`
- `git diff --check`

If the focused tests expose unrelated failures in touched modules, fix those
failures in this plan. Do not skip or weaken tests.

## Plan Review Gate

Status: passed

Self-review checked DDD/TDD order, spec-first scope, tests before
implementation, no test weakening, no new deferrals, frozen dataclass/config
patterns, and no game-rule duplication. Valid findings from the fresh review
lanes were incorporated before approval.

## Fresh-Eye Review Gate

Status: passed

Agents:

- Pattern consistency: `019e1ef3-6431-7ab1-8b60-508e888da7a9`
- Spec alignment: `019e1ef3-644e-7420-972a-beaee862ff98`
- Engineering/design invariants: `019e1ef3-649e-7f61-96cf-6bf52888aa75`

Dispositions:

- Added full-suite verification.
- Expanded batch validation/config/publication gates.
- Made split-claim metadata, component overlap diagnostics, feature registry,
  leakage diagnostics, and no-selector semantics explicit.
- Rejected simple `final_claim` override without a fresh ledger ID.
- Added missing critical files and canonical component serialization reuse.

## Post-Implementation Audit

Status: passed

Sub-agents:

- Plan-vs-code verification: `019e1efc-ead7-7ce1-89e2-d290abb5a8d0`
- Engineering/design invariants: `019e1efc-eb39-7c72-bd7a-25283ef14b3a`
- Spec alignment: `019e1efc-eb1c-7f53-94f3-52c0156d527a`

Disposition:

- Fixed live UserData claim-boundary propagation.
- Preserved top-level contract objects in merged artifacts.
- Tightened batch validation for claim-boundary drift, target-variable drift,
  dependency extra, deployment policy, feature registry digest, and contract
  object shapes.
- Changed leakage diagnostics to derive forbidden-key overlap from hierarchy
  overlap counts.
- Replaced bare unimplemented component diagnostics with structured
  `not_applicable` objects.
- Aligned feature-registry leakage labels and spec signature/defaults.

Verification:

- `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v`
- `uv run pytest tests/ -v`
- `uv run python scripts/validate_active_plans.py`
- `git diff --check`
- `python -m py_compile scripts/analysis/phase7_learned_surrogate_experiment.py src/starsector_optimizer/phase7_learned_batch.py src/starsector_optimizer/phase7_matchup_data.py`

## Retirement

After implementation and post-implementation audit pass, set frontmatter
`status: implemented`, set `implemented`, `implementation_commit`, and
`post_impl_audit`, then move this plan to `.claude/plans/archive/2026/`.
