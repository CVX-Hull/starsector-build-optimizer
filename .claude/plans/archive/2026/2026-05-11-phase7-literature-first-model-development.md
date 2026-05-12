---
plan_type: implementation
status: implemented
created: 2026-05-11
approved: 2026-05-11
implemented: 2026-05-11
owner: agent
related_docs:
  - AGENTS.md
  - docs/CONVENTIONS.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md
  - docs/reports/2026-05-11-validation-to-phase7-roadmap.md
  - docs/reference/phase7-learned-surrogate-research.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
  - docs/reference/phase7-search-space-compression.md
  - docs/reference/README.md
implementation_commit: not_committed
post_impl_audit: passed
superseded_by: null
---

# Phase 7 Literature-First Model Development

## Goal

Create a durable Phase 7 research synthesis that gates learned-surrogate model
development behind paper-grounded decisions. The output is a reference note and
cross-links; it does not implement model code, hyperparameter search, or new
experiments.

## Context And Source Docs

- Root workflow file: `AGENTS.md`.
- Documentation authority: `docs/CONVENTIONS.md`.
- Current Phase 7 substrate: `docs/specs/31-phase7-matchup-data.md`.
- Current comparator evidence:
  `docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md`.
- Current roadmap:
  `docs/reports/2026-05-11-validation-to-phase7-roadmap.md`.
- Existing Phase 7 references:
  `docs/reference/phase7-featurized-matchup-surrogate.md` and
  `docs/reference/phase7-search-space-compression.md`.

## Scope

- Add `docs/reference/phase7-learned-surrogate-research.md`.
- Record the research brief, source table, evidence matrix, and derived
  decision gates.
- Treat derived gates as design guidance only. Spec 31 remains the contract
  owner for Phase 7 data, comparator boundaries, feature-schema provenance,
  leakage controls, and honest-eval top-k protocol.
- Reconcile the new note with the existing `_research/phase7-featurized-matchup`
  corpus and the global literature-research workflow: source breadth,
  full-text access when available, source access path, read status, and
  separation of paper claims from project inference.
- Use research sub-agents split by discipline before writing final synthesis.
- Update reference/index links so the new note is discoverable.
- Update existing Phase 7 reference docs to point model-selection readers to
  the new research note.

## Out Of Scope

- No model implementation.
- No dependency installation.
- No experiment run.
- No new empirical numbers from project data.
- No optimizer integration.

## Critical Files

- `docs/reference/phase7-learned-surrogate-research.md`
- `docs/reference/phase7-featurized-matchup-surrogate.md`
- `docs/reference/phase7-search-space-compression.md`
- `docs/reference/README.md`
- `.claude/plans/archive/2026/2026-05-11-phase7-literature-first-model-development.md`

## Public Concepts And Canonical Owners

- Literature synthesis and design rationale live in `docs/reference/`.
- Empirical results remain owned by `docs/reports/`.
- The dated roadmap and promotion checklist remain owned by
  `docs/reports/2026-05-11-validation-to-phase7-roadmap.md`; the new reference
  note can refine literature-derived rationale but must not silently supersede
  report-owned roadmap authority.
- Exact Phase 7 data contracts remain owned by spec 31.
- Comparator-gate scope remains owned by spec 31: scikit-learn-only
  comparators named `global_mean`, `opponent_mean`, `build_mean`,
  `twfe_additive`, `ridge_hybrid`, and `random_forest`; no random-row
  headline default; CatBoost, sparse interaction models, model-assisted search,
  and BoTorch remain later-plan work after comparator outputs exist.
- Temporary execution state is owned by this plan until archived.

## Implementation Sequence

1. Add the Phase 7 learned-surrogate research note with:
   - research date and cutoff;
   - research questions;
   - inclusion and exclusion criteria;
   - relationship to the existing `_research/phase7-featurized-matchup`
     corpus, noting sources reused, newly added, and not yet full-text-read;
   - source table with URLs, DOI/arXiv/PDF access when available, and read
     status that distinguishes full-paper read, skimmed, and metadata-only;
   - explicit separation of paper claims from project-specific inferences;
   - field-by-field synthesis;
   - evidence matrix;
   - decision table;
   - derived experiment-plan requirements.
2. Launch research sub-agents for independent discipline lanes and integrate
   their paper-level findings before finalizing the note:
   - statistical learning, validation, tuning, and leakage;
   - tabular, sparse-interaction, and ranking/matchup models;
   - mixed-variable optimization, surrogate-assisted optimization, active
     learning, and simulation allocation.
3. In the research note, preserve spec 31 constraints:
   - no learned baseline may read training-log test targets or honest-eval
     targets while fitting or tuning;
   - any script reporting model metrics must emit `feature_schema_version` and
     source DB path;
   - honest-eval top-k remains post-fit diagnostic only;
   - comparator-gate names and scikit-learn-only scope are not redefined.
4. Update the Phase 7 featurized-surrogate reference so its modeling sequence
   is explicitly gated by the new research note rather than treated as a fixed
   prescription.
5. Update the Phase 7 search-space-compression reference so structured kernel
   and optimizer work is downstream of the research and validation gates.
6. Update `docs/reference/README.md` to include the new reference.
7. Run doc checks:
   - frontmatter/category sanity by inspection;
   - link check for changed Markdown files;
   - Mermaid validation if changed files contain Mermaid blocks;
   - `git diff --check`.
8. Run focused post-implementation audit and archive this plan.

## Tests And Mechanical Gates

- `python .claude/skills/scripts/validate_mermaid.py <changed-md-files>` if
  Mermaid blocks are present.
- Focused Markdown link check for changed docs:
  `uv run python -c "<pathlib-based check over changed Markdown links>"`.
- `git diff --check`.
- Full test suite deviation: this documentation-only change touches no Python
  or Java runtime path. Run `uv run python scripts/validate_active_plans.py`
  and focused doc checks instead of `uv run pytest tests/ -v`. Residual risk:
  a latent unrelated test failure would not be discovered by this docs-only
  change; acceptable because no runtime behavior changes.

## Review Findings And Dispositions

- 2026-05-11 plan-review agents found valid gaps:
  - literature workflow needed explicit source-grounding and corpus
    reconciliation;
  - the new reference could accidentally become a second owner for spec/report
    authority;
  - spec 31 comparator, leakage, honest-eval, and provenance constraints needed
    to be named;
  - mechanical gates needed concrete commands;
  - full-suite deviation needed explicit justification.
- Disposition: all valid findings incorporated in the plan before
  implementation.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11 19:42
- Findings:
  - Pattern consistency: literature workflow, authority, related-doc, and
    verification gaps.
  - Spec alignment: missing spec 31 comparator, leakage, honest-eval, and
    provenance constraints.
  - Engineering/design invariants: risk that reference note becomes second
    owner; traceability/read-status gaps.
- Dispositions:
  - All valid findings incorporated in this plan before implementation.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11 19:42
- Agents:
  - Pattern Consistency: passed with findings
  - Spec Alignment: passed with findings
  - Engineering & Design Invariants: passed with findings
- Findings:
  - See Plan Review Gate.
- Dispositions:
  - See Plan Review Gate.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Post-Implementation Audit Requirements

- Verify changed docs obey `docs/CONVENTIONS.md`.
- Verify no empirical internal-sim numbers were added to reference docs.
- Verify existing Phase 7 reports remain the owners of project measurements.
- Verify the research note points to spec 31 for contracts and reports for
  project measurements instead of becoming a second owner.
- Run focused link and whitespace checks.

## Post-Implementation Audit Findings

- Plan-vs-code audit found missing plan retirement, missing durable evidence
  of research sub-agents, and incomplete corpus/decision-table traceability.
  Disposition: added sub-agent execution record, local corpus access table, and
  decision table to the learned-surrogate research note; plan retired after
  verification.
- Design audit found `phase7-search-space-compression.md` still read as a
  second owner for concrete BoTorch implementation scope, ship gates, and
  unsupported projections; it also found unsupported paper-family names in the
  featurized-surrogate evidence list. Disposition: demoted the BoTorch outline
  and ship gates to historical candidate rationale, reframed projection language
  as hypotheses, and trimmed evidence families to sources represented in the
  research gate.
- Spec-alignment audit found a report-authority risk because reference docs
  refined the next model step beyond the dated roadmap report. Disposition:
  updated the roadmap report so it remains the owner of the refined next-step
  wording.
- Mechanical verification passed:
  - `uv run python scripts/validate_active_plans.py`
  - focused Markdown link check for changed docs
  - `git diff --check`
  - Mermaid check not applicable; changed files contain no Mermaid blocks.

## Retirement Checklist

- Set `status: implemented`.
- Set `implemented: 2026-05-11`.
- Set `implementation_commit` to commit hash or `not_committed`.
- Set `post_impl_audit: passed`.
- Move to `.claude/plans/archive/2026/`.
