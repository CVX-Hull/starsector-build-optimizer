---
plan_type: implementation
status: implemented
created: 2026-05-12
approved: 2026-05-12
implemented: 2026-05-12
owner: agent
related_docs:
  - AGENTS.md
  - docs/CONVENTIONS.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
  - docs/reference/phase7-search-space-compression.md
  - docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md
  - docs/reports/INDEX.md
implementation_commit: not_committed
post_impl_audit: passed
superseded_by: null
---

# Phase 7 Structured Features And Representation

## Goal

Implement the next Phase 7 feature-substrate wave after the completed local
learned-surrogate run. The change upgrades static game-data representation
from feature schema v2 to v3, fixes built-in weapon aggregate parity, and adds
geometry, slot-arc, opponent, wing, hull-system, phase, weapon-tag, and
ablation-profile support needed to test whether richer structured features
improve held-out-opponent transfer.

## Context

- The local full artifact
  `data/phase7/learned_surrogate_full_local_2026-05-12.json` completed all
  15 `(split, model)` jobs.
- CatBoost and tuned random forest are useful incumbents, but held-out-opponent
  rank signal remains weak.
- `.ship` files already expose static hull geometry, shield geometry, engine
  slots, and weapon-slot placement. Video/audio support and sprite-pixel
  analysis are out of scope.
- AWS learned-batch execution remains disabled and is not part of this plan.

## Scope

- Update spec 31 and reference/report docs for feature schema v3.
- Extend parser/domain models with static `.ship` geometry and wing metadata
  from `hulls/wing_data.csv`.
- Bump `FEATURE_SCHEMA_VERSION` to `3`.
- Add v3 feature columns for:
  - hull geometry, shield geometry, style, phase stats, hull system, and engine
    slot summaries;
  - normalized slot placement based on hull dimensions, tactical arc buckets,
    broadside/frontal/aft pressure, arc-weighted weapon pressure, and geometry
    interactions;
  - consistent built-in weapon handling in aggregate weapon features;
  - opponent vents/caps, hullmod OP, built-in overlap, unknown counts,
    scorer-like summaries, hull system, phase stats, and stock-variant carrier
    wing pressure;
  - weapon tags/hints and hullmod tag/UI-tag aggregates already available from
    parsed static game data;
  - feature profile filtering for aggregate, geometry,
    opponent-parity, sparse component, and sparse-cross ablation runs.
- Add focused tests for parser, feature extraction, profile filtering, and
  schema/version expectations.
- Retire completed Phase 7 learned-baseline and AWS batch plans after this
  implementation and audit.

## Out Of Scope

- No AWS launch, AMI bake, or cloud spend.
- No video/audio pipeline and no sprite-pixel feature extraction.
- No BoTorch/custom-kernel implementation.
- No Deep Sets, Set Transformer, PointNet, graph MPNN, LightGBM/XGBoost, or
  full factorization-machine experiment in this wave.
- No new empirical full run is required to complete the code change. Any v3
  empirical result requires a separate local experiment run and report update.
  This is an explicit scope boundary: the current user request is to implement
  the representation substrate, not to spend a full local/AWS run in the same
  change. The v3 code must not claim model-quality improvement until that
  later run exists.

## Critical Files

- `src/starsector_optimizer/models.py`
- `src/starsector_optimizer/parser.py`
- `src/starsector_optimizer/matchup_features.py`
- `scripts/analysis/phase7_baseline_surrogate.py`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `src/starsector_optimizer/phase7_learned_batch.py`
- `scripts/cloud/phase7_learned_batch.py`
- `tests/test_parser.py`
- `tests/test_matchup_features.py`
- `tests/test_phase7_matchup_data.py`
- `tests/test_phase7_baseline_surrogate.py`
- `tests/test_phase7_learned_surrogate_experiment.py`
- `docs/specs/31-phase7-matchup-data.md`
- `docs/reference/phase7-featurized-matchup-surrogate.md`
- `docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md`
- `.claude/plans/active/2026-05-12-phase7-structured-features.md`

## Public Concepts And Owners

- Spec 31 owns feature schema v3, feature-profile names, profile provenance
  fields, and leakage boundaries.
- `matchup_features.py` owns feature construction and feature-profile
  filtering. Feature-profile names and bucket definitions live as module-level
  named constants.
- Parser/domain models own static game-data parsing only; they do not derive
  combat rules.
- Reports own dated empirical results. This plan may update report limitations
  and next steps, but it must not claim v3 performance without a v3 run.

## Implementation Sequence

1. Update spec 31 with feature schema v3, new parser-owned static geometry and
   wing data, built-in aggregate parity, feature-profile names, and the
   no-video/audio boundary. The spec must explicitly state that wing features
   are opponent-stock-variant descriptive features only; player/build wing
   optimization remains out of scope until the combat-harness and optimizer
   contracts support non-empty player wings.
2. Add tests first:
   - `.ship` geometry, shield geometry, engine slot parsing, and wing-data
     parsing;
   - built-in weapon aggregate parity;
   - opponent vents/caps, hullmod OP, built-in overlap, scorer-like summaries,
     hull system, phase stats, and wing pressure;
   - weapon tag/hint features and hullmod aggregate tag/UI-tag count behavior;
   - arc bucket and arc-weighted pressure features;
   - feature-profile filtering, provenance symmetry, and leakage exclusions;
   - existing opponent error contracts: missing variant raises
     `FileNotFoundError`, unknown hull raises `ValueError`, and malformed
     direct variant raises `ValueError`;
   - forward-compatible malformed/unknown parser inputs warn/skip or default
     instead of crashing.
3. Implement frozen dataclasses for static hull geometry and wing metadata, and
   extend `GameData` with parsed wing specs.
4. Implement feature schema v3 in `matchup_features.py`, including helper
   functions for resolved weapons, geometry, arc pressure, tag/hint aggregates,
   opponent-stock wing aggregates, and profile filtering. Arc buckets,
   directional labels, geometry normalization floors, and profile membership
   are module-level named constants, not inline literals.
5. Add CLI/config support for feature profiles in comparator and learned
   experiment scripts. Default profile remains `all`; v2 historical artifacts
   remain valid only as schema v2 evidence. `feature_profile` is included in
   config dataclasses, provenance, result payloads, checkpoints,
   comparator context, learned `feature_families`, and AWS batch job/merge
   validation. AWS execution remains disabled, but its artifact contract must
   reject inconsistent feature profiles, stale schema versions, and stale
   artifacts whose stamped batch job identity does not match the expected job.
6. Update reference/report docs without adding new empirical v3 claims.
7. Run focused tests, full tests, active-plan validation, and post-implementation
   audit with fresh-eye sub-agents.
8. Archive completed predecessor Phase 7 plans and this plan after audit.

## Test And Verification Gates

- `uv run pytest tests/test_parser.py -q`
- `uv run pytest tests/test_matchup_features.py -q`
- `uv run pytest tests/test_phase7_matchup_data.py -q`
- `uv run pytest tests/test_phase7_learned_surrogate_experiment.py -q`
- `uv run pytest tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py -q`
- `uv run pytest tests/ -v`
- `uv run python scripts/validate_active_plans.py`
- `git diff --check`
- `rg -n "TODO|FIXME|XXX|HACK|pytest\\.skip|type: ignore|# noqa" src tests docs`

## Deferred Items

- Full v3 comparator and learned-surrogate empirical run.
  Deferred with user-approved scope boundary: this implementation prepares the
  substrate and records that any v3 model-quality claim requires a later local
  run.
- Factorization-machine model family and PyTorch training loop.
- Neural set/token/graph encoders.
- BoTorch/custom-kernel optimizer integration.
- Static sprite-pixel extraction. This is separate from video/audio and may be
  reconsidered only if `.ship` geometry and structured data fail to capture
  relevant hull geometry.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-12
- Findings:
  - Feature profiles needed an explicit spec/API/provenance contract.
  - AWS batch artifacts needed profile/schema validation despite execution
    remaining out of scope.
  - Wing features needed to be bounded to opponent-stock variants because
    player/build wings are outside current optimizer and combat-harness scope.
  - Bucket/profile constants, forward-compatible parser behavior, game-data
    verification, direct feature tests, and invariant grep checks needed to be
    explicit.
- Dispositions:
  - Added `feature_profile` to the spec-owned public concept surface and
    required propagation through configs, provenance, checkpoints, comparator
    context, learned feature families, and batch merge validation.
  - Added batch implementation files to scope for artifact-contract updates
    while keeping AWS execution disabled.
  - Restricted wing features to descriptive opponent-stock-variant pressure.
  - Required module-level named constants for arc buckets/profile definitions,
    parser error-path tests, real game-data verification, direct
    `tests/test_matchup_features.py` coverage, and invariant grep checks.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via repo workflow.
- Reviewed at: 2026-05-12
- Agents:
  - Pattern Consistency: passed with findings.
  - Spec Alignment: passed with findings.
  - Engineering & Design Invariants: passed with findings.
- Findings:
  - See Plan Review Gate.
- Dispositions:
  - See Plan Review Gate.

## Post-Implementation Audit Requirements

- Verify feature schema v3 and `feature_profile` are emitted consistently by
  comparator, learned, and batch artifacts.
- Verify built-in weapons are included consistently across per-slot and
  aggregate features.
- Verify no target-derived feature or honest-eval target enters profile
  filtering.
- Verify no empirical v3 claims are added without a v3 run.
- Run fresh-eye audit sub-agents before marking implemented.

## Retirement Checklist

- Frontmatter `status` is changed to `implemented`.
- Frontmatter `implemented` is set to the completion date.
- Frontmatter `implementation_commit` is set to the final commit hash or
  `not_committed`.
- Frontmatter `post_impl_audit` is set to `passed` or linked to the audit
  record.
- Plan is moved to `.claude/plans/archive/2026/`.
