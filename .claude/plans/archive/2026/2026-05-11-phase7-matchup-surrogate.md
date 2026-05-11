---
plan_type: implementation
status: implemented
created: 2026-05-11
approved: 2026-05-11
implemented: 2026-05-11
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
  - docs/specs/24-optimizer.md
  - docs/specs/30-honest-evaluator.md
  - docs/CONVENTIONS.md
implementation_commit: not_committed
post_impl_audit: passed
superseded_by: null
---

# Phase 7 Matchup Surrogate Data Layer

## Goal

Implement the first Phase 7 matchup-surrogate substrate: recover exact builds
from prior-run JSONL logs and Optuna study DBs, materialize matchup rows into a
derived SQLite database, extract auditable non-atomic build/opponent/matchup
features, and provide baseline validation utilities.

## Context And Source Docs

- `docs/reference/phase7-featurized-matchup-surrogate.md` owns the design
  rationale for contextual matchup modeling.
- `docs/specs/24-optimizer.md` owns JSONL row semantics and
  `trial_params_to_build`.
- `docs/specs/30-honest-evaluator.md` owns honest-eval ledger semantics and
  the candidate reconstruction path.
- `docs/CONVENTIONS.md` owns the doc category and empirical-numbers rules.

## Scope

- Reconcile the draft spec for the Phase 7 matchup data layer with the approved
  plan, and promote it to `shipped` only after implementation and tests pass.
- Add code to recover builds from logs and Optuna DBs with explicit provenance.
- Add honest-eval candidate and ledger recovery using the same
  `extract_top_builds(...)` path as the honest evaluator.
- Add feature extraction for player builds, opponent variants, and matchup
  interactions using existing game-data models and scorer outputs.
- Add SQLite materialization for derived Phase 7 rows.
- Add split builders and a small baseline evaluator for grouped validation,
  covering held-out replicate, build, opponent, component-combination,
  seed/cell, and forward-time splits.
- Add tests for recovery, features, SQLite persistence, split behavior, and all
  spec-defined error paths.
- Update the Phase 7 reference and indexes for the corrected provenance model.

## Out Of Scope

- Paid simulations or changing the running honest-eval campaign.
- Replacing honest evaluation as the oracle.
- Neural set/graph models.
- Committing generated `data/phase7/*.sqlite` artifacts.

## Critical Files

- `docs/specs/31-phase7-matchup-data.md`
- `docs/specs/README.md`
- `docs/project-overview.md`
- `docs/reference/phase7-featurized-matchup-surrogate.md`
- `src/starsector_optimizer/matchup_features.py`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `scripts/analysis/phase7_materialize_matchups.py`
- `scripts/analysis/phase7_baseline_surrogate.py`
- `tests/test_matchup_features.py`
- `tests/test_phase7_matchup_data.py`

## Public Concepts And Owners

- `RecoveredBuild`: `phase7_matchup_data.py`, provenance-tagged build recovery.
- Flat matchup feature rows: `matchup_features.py`, dict-backed rows for
  baseline surrogate training.
- Phase 7 derived SQLite schema: spec 31.
- Generated DB location: `data/phase7/`, local derived data only.

## Implementation Sequence

1. Reconcile existing draft spec 31 and index/project-overview updates with
   this plan. Keep spec 31 `status: draft` until the implementation passes.
2. Write tests for build recovery, feature extraction, DB materialization, and
   grouped splits:
   - Hermetic fixture DB test for Optuna categorical/int param decoding.
   - DB reconstruction assertions for `trial_params_to_build -> repair_build`.
   - Feasibility assertions for DB-reconstructed builds via `is_feasible`.
   - Honest-eval ledger/candidate reconstruction tests using
     `extract_top_builds`.
   - Error-path tests for missing opponent variants, unknown opponent hulls,
     unsupported Optuna distributions, malformed variant files, and invalid
     split fractions.
   - Split tests for held-out replicate, build, opponent,
     component-combination, seed/cell, and forward-time partitions.
3. Implement feature extraction; immediately run
   `uv run pytest tests/test_matchup_features.py -v`.
4. Implement build/log/DB/honest-eval recovery and SQLite materialization;
   immediately run `uv run pytest tests/test_phase7_matchup_data.py -v`.
5. Add CLI scripts for materialization and baseline validation; immediately run
   both `--help` smoke tests.
6. Update the Phase 7 reference with the corrected DB/log/honest-eval
   provenance model.
7. Promote spec 31 from `draft` to `shipped` only after code and tests pass.
8. Run targeted tests, grep/stale-reference checks, mechanical checks, and
   post-implementation audit.

## Tests And Mechanical Gates

- `uv run pytest tests/test_matchup_features.py tests/test_phase7_matchup_data.py -v`
- `uv run python scripts/analysis/phase7_materialize_matchups.py --help`
- `uv run python scripts/analysis/phase7_baseline_surrogate.py --help`
- `uv run pytest tests/ -v`
- `uv run python -c "from starsector_optimizer.phase7_matchup_data import RecoveredBuild; from starsector_optimizer.matchup_features import build_feature_row"`
- `rg -n "MatchupFeatureRow|31-phase7-matchup-data|phase7_materialize_matchups|phase7_baseline_surrogate" docs src tests scripts`
- `git diff --check`
- Post-implementation audit per `.claude/skills/post-impl-audit.md`.

## Review Findings And Dispositions

- Finding: `related_docs` omitted the new spec that owns the planned API.
  Disposition: added `docs/specs/31-phase7-matchup-data.md`.
- Finding: `MatchupFeatureRow` in Public Concepts implied a dataclass, while
  spec 31 defines dict-backed flat feature rows. Disposition: renamed the
  concept to flat matchup feature rows.
- Finding: CLI scripts were in scope but not explicitly smoke-tested.
  Disposition: added `--help` smoke gates for both scripts.
- Finding: sub-agent plan-review lanes are part of the repo-local plan-review
  skill when authorized, but the active runtime requires explicit current-turn
  authorization to spawn sub-agents. Disposition: no sub-agents launched in this
  review; require a later explicit request before using those lanes.
- Finding: implementation had already been partially started before the review
  gate was made explicit. Disposition: implementation must be reconciled against
  this approved plan before resuming; no plan item is considered complete until
  the tests and post-implementation audit pass.
- Fresh-eye finding: active plan approval was invalid without a Fresh-Eye Review
  Gate. Disposition: lifecycle and validator now require the gate; this plan
  records the three completed sub-agent lanes below.
- Fresh-eye finding: spec 31 was marked `shipped` before implementation exists.
  Disposition: spec 31 reset to `draft`; implementation sequence promotes it to
  `shipped` only after tests pass.
- Fresh-eye finding: honest-eval provenance was in the contract but not planned.
  Disposition: scope, tests, and implementation sequence now include
  honest-eval candidate/ledger recovery.
- Fresh-eye finding: DB recovery tests could skip the core repair-boundary
  behavior. Disposition: plan now requires a hermetic fixture DB test,
  repair-boundary assertions, and feasibility checks.
- Fresh-eye finding: required error-path tests were too broad. Disposition:
  plan now names missing-variant, unknown-hull, unsupported-distribution,
  malformed-variant, and invalid-split-fraction tests.
- Fresh-eye finding: validation split coverage was narrower than the reference
  protocol. Disposition: plan now includes replicate, component-combination,
  and seed/cell splits in addition to build, opponent, and forward-time.
- Fresh-eye finding: current feature extraction code swallowed malformed
  variant parse errors. Disposition: plan now requires a malformed-variant
  error-path test and implementation fix.
- Fresh-eye finding: verification cadence lacked per-step tests and stale
  reference greps. Disposition: implementation sequence and gates now include
  per-step pytest runs and `rg` stale-reference check.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11
- Findings:
  - `related_docs` omitted spec 31.
  - Public Concepts named `MatchupFeatureRow` although spec 31 defines dict-backed feature rows.
  - CLI script smoke gates were missing.
  - Sub-agent review lanes require explicit current-turn authorization before spawning.
  - Partial implementation existed before the review gate was enforced.
- Dispositions:
  - Added spec 31 to `related_docs`.
  - Renamed public concept to flat matchup feature rows.
  - Added CLI `--help` smoke gates.
  - Did not launch sub-agents; record the authorization constraint.
  - Require reconciliation against this approved plan before implementation resumes.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11
- Agents:
  - Pattern Consistency: passed with findings
  - Spec Alignment: passed with findings
  - Engineering & Design Invariants: passed with findings
- Findings:
  - Approval lacked a Fresh-Eye Review Gate.
  - Plan recorded no sub-agents launched while approval required them.
  - Plan status did not match the partial implementation state.
  - Verification cadence needed per-step test runs and stale-reference checks.
  - Spec/index wording still described creating spec 31 after it already existed.
  - Spec 31 was marked `shipped` before implementation existed.
  - Honest-eval provenance was specified but not planned.
  - Error-path tests were not named explicitly.
  - Split coverage was narrower than the Phase 7 reference protocol.
  - DB recovery tests could skip the core repair-boundary path.
  - DB-reconstructed build feasibility was not explicit.
  - Feature extraction currently swallowed malformed variant parse errors.
  - Plan-specific post-implementation audit requirements were too narrow.
- Dispositions:
  - Added this Fresh-Eye Review Gate and set it to passed after all lanes returned.
  - Replaced the prior no-sub-agent disposition with the completed agent record.
  - Set plan `status: active` because implementation artifacts already exist and
    gates are now recorded.
  - Reworded spec 31 work as reconciliation/update rather than first creation.
  - Reset spec 31 to `draft`; promotion to `shipped` is a planned final step.
  - Added honest-eval recovery to scope, tests, and implementation sequence.
  - Added named error-path tests and full split-family coverage.
  - Added hermetic DB, repair-boundary, and feasibility checks.
  - Added malformed-variant handling to planned tests/fixes.
  - Added per-step tests, stale-reference grep, and broader post-impl audit items.
- Approval rule: frontmatter `status: approved` or `active` is invalid unless this gate is `passed`.

## Post-Implementation Audit Requirements

- Verified no generated SQLite artifacts are tracked.
- Verified references do not put Wave 1 empirical numbers in reference docs.
- Verified DB-reconstructed builds are provenance-tagged and not treated as
  exact logged builds unless a log row cross-check exists.
- Verified no new `pytest.skip`, swallowed exception, TODO/FIXME/HACK, or test
  weakening was introduced.
- Verified spec-code signature alignment for every new public API in spec 31.
- Verified split/baseline numeric thresholds are named constants or config fields.
- Verified DB recovery always passes optimizer-space proposals through
  `repair_build` and checks feasibility where the plan requires it.

## Post-Implementation Audit Findings

- Finding: materialization reused existing SQLite tables and could leave stale
  generated rows when rerun with fewer inputs. Disposition: `materialize_sqlite`
  now clears all Phase 7 tables before inserting the current materialization.
- Finding: `--game-dir` loaded parser data from one tree while manifest data
  could come from the default tree. Disposition: both CLIs now load
  `manifest.json` from the selected `--game-dir`.
- Finding: campaign provenance only recognized `wave*` path components.
  Disposition: log paths under `data/logs/<campaign>/...` and
  `<campaign>/<study__seedN>/evaluation_log.jsonl` now recover arbitrary
  campaign names.
- Finding: invalid-spec rows could be recovered as exact logged builds.
  Disposition: `recover_logged_builds` skips `invalid_spec` rows.
- Finding: replicate split was incorrectly implemented over training-log
  opponent indexes. Disposition: it now operates on honest-eval rows and groups
  exact build/opponent repeats together.
- Finding: invalid `train_fraction` lacked direct test coverage. Disposition:
  added direct `forward_time_split(..., train_fraction=0.0)` coverage.
- Finding: model/data tuning literals were embedded in code. Disposition:
  extracted build-key length, default honest top-k, RandomForest leaf size, and
  core-count sentinel into named constants.
- Finding: spec 31 wording still said draft after promotion. Disposition:
  updated spec wording and documented fresh materialization semantics and
  loader APIs.

## Retirement Checklist

- [x] Implementation complete.
- [x] Tests and mechanical gates pass.
- [x] Post-implementation audit complete.
- [x] Plan frontmatter updated.
- [x] Plan archived under `.claude/plans/archive/2026/`.
