---
plan_type: implementation
status: implemented
created: 2026-05-15
approved: 2026-05-15
implemented: 2026-05-15
owner: codex
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
implementation_commit: not_committed
post_impl_audit: passed
---

# Phase 7 Opponent Transfer Diagnostics

## Context

The 2026-05-14 Phase 7 v3 evidence refresh shows useful learned-model signal on
build, component, seed-cell, and forward-time splits, while held-out-opponent
transfer remains weak. Spec 31 already names opponent-hull and opponent-family
holdouts as the next hierarchy levels, but they are not implemented.

## Scope

DONE means the comparator and learned experiment scripts can run opponent-hull
and opponent-family split levels with artifact metadata and leakage checks.
Feature-family ablation execution remains a separate experiment run using the
existing `--feature-profile` switch.

## Critical Files

- `docs/specs/31-phase7-matchup-data.md`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `scripts/analysis/phase7_baseline_surrogate.py`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `src/starsector_optimizer/phase7_learned_batch.py`
- `examples/phase7-learned-batch.yaml`
- `tests/test_phase7_matchup_data.py`
- `tests/test_phase7_baseline_surrogate.py`
- `tests/test_phase7_learned_surrogate_experiment.py`
- `tests/test_phase7_learned_batch.py`

## Implementation Steps

1. DONE: Updated spec 31 so `opponent-hull` and `opponent-family` are
   implemented split levels, with outcome-free group keys and artifact leakage
   metadata.
2. DONE: Added mapping-driven data-layer split builders:
   `held_out_opponent_hull_split(rows, opponent_hull_by_variant, ...)` and
   `held_out_opponent_family_split(rows, opponent_family_by_variant, ...)`.
3. DONE: In the comparator script, derived opponent hull/family maps from
   stock variant descriptors and parsed game data, added both split choices,
   and emitted stricter overlap counts for exact opponent, opponent hull, and
   opponent family.
4. DONE: In the learned experiment script, reused the comparator split support
   for outer splits and added compatible inner validation, hierarchy
   scorecards, and forbidden-key leakage diagnostics for the new split levels.
5. DONE: Updated the learned-batch canonical matrix to 7 splits x 3 models so
   local `--split all` and batch publication semantics agree.
6. DONE: Added tests for disjoint split behavior, metadata, leakage
   diagnostics, and config expansion.

## Plan Review Gate

- Status: passed
- Skill: plan-review

Self-review checked coherence, DDD/TDD order, spec-first scope, test coverage,
no magic-number changes, and no new game-rule registries. This plan promotes
already-specified split levels and derives labels from parsed game data rather
than target outcomes.

## Fresh-Eye Review Gate

- Status: passed
- Review type: sub-agents fresh-eye audit
- Sub-agent lanes:
  - Pattern consistency: Descartes
  - Spec alignment: Noether
  - Engineering and invariants: Planck
- Dispositions:
  - Fixed learned scorecards so opponent hull/family overlap counts are
    populated for all split levels.
  - Updated spec 31 from the stale 15-job/five-split learned matrix to the
    21-job/seven-split matrix and the 2026-05-14 comparator default.
  - Made opponent family map construction fail loudly when descriptor fields
    are missing.
  - Stopped converting opponent descriptor errors into
    `insufficient_inner_groups`.
  - Reverted the dated empirical report status edit; implementation status now
    lives in spec/reference/plan artifacts.

## Verification

- PASS: `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py tests/test_phase7_learned_batch.py -v`
- PASS: `uv run pytest tests/ -v`
- PASS: `uv run python -m py_compile src/starsector_optimizer/phase7_matchup_data.py src/starsector_optimizer/phase7_learned_batch.py scripts/analysis/phase7_baseline_surrogate.py scripts/analysis/phase7_learned_surrogate_experiment.py`
- PASS: `uv run python scripts/validate_active_plans.py`
- PASS: `git diff --check`
