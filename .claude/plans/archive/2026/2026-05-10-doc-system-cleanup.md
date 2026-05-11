---
plan_type: implementation
status: implemented
created: 2026-05-10
approved: 2026-05-10
implemented: 2026-05-11
owner: agent
related_docs:
  - AGENTS.md
  - .claude/skills/ddd-tdd.md
  - .claude/skills/plan-lifecycle.md
  - .claude/skills/plan-review.md
  - docs/CONVENTIONS.md
  - docs/project-overview.md
  - docs/reference/README.md
  - docs/reports/INDEX.md
  - docs/specs/README.md
implementation_commit: b558786b237136a9206ec6ff62d9b767ed59b68d
post_impl_audit: passed
superseded_by: null
---

# Documentation System Cleanup

## Goal

Make the repository documentation system clear, navigable, and maintainable
while the Wave 1 honest-eval resume runs in the background. Consolidate stale
or overlapping docs where appropriate, improve report/spec/reference indexing,
and identify repo-local skills that should become portable packaged skills.

## Scope

- Documentation taxonomy and ownership: `docs/CONVENTIONS.md`,
  `docs/project-overview.md`, `docs/specs/README.md`,
  `docs/reports/INDEX.md`, `AGENTS.md`.
- Plan lifecycle integration: `.claude/plans/`, `plan-lifecycle`,
  planning/review skills, and `.gitignore` tracking rules.
- Report cleanup: especially dated Wave 1 and validation reports.
- Spec/reference consistency: specs, reference docs, and cross-links.
- Repo-local skill audit: `.claude/skills/*.md`.
- Lightweight mechanical gates: link/path checks, Mermaid validation if any
  diagrams are touched, pre-commit hook, and targeted grep checks.

## Out Of Scope

- Rewriting empirical conclusions before the honest-eval run completes.
- Moving global skills into `~/.codex/skills` without a final user-visible
  decision and a clean patch.
- Large code refactors unrelated to documentation or skill workflow.
- Treating active or archived plans as durable design authority.

## Current Status

As of 2026-05-10, most original documentation-system cleanup has landed in
prior commits. The remaining active work is the lifecycle cleanup discovered
while making plans trackable:

- Add and wire the repo-local plan-lifecycle SOP.
- Make `.claude/plans/active/` and `.claude/plans/archive/` tracked.
- Normalize this plan to the plan frontmatter schema.
- Update documentation conventions so plans have an explicit category,
  lifecycle, and retirement procedure.

This plan was implemented in commit
`b558786b237136a9206ec6ff62d9b767ed59b68d` and retired to
`.claude/plans/archive/2026/` after post-implementation audit.

## Audit Waves

Wave 1 launches independent read-only auditors for:

- Documentation taxonomy and navigation.
- Reports cleanup and empirical-claims hygiene.
- Specs/reference consistency and ownership.
- Repo-local skill promotion candidates.
- Mechanical link/index health.

Wave 2 launches after fixes and must include:

- Implementer-readiness pass over the documentation system.
- Verify-fixes pass for cross-doc drift.
- Skill quality pass for any skill updates or proposed global promotions.

## Mechanical Gates

- `git diff --check`
- `.githooks/pre-commit`
- Markdown path/link existence check for edited docs.
- Mermaid validation for edited docs containing Mermaid diagrams.

## Retirement Checklist

- [x] Plan-lifecycle changes committed.
- [x] `implementation_commit` set to the commit hash.
- [x] Post-implementation audit complete.
- [x] `post_impl_audit` set to `passed` or an audit record link.
- [x] `status` set to `implemented`.
- [x] `implemented` set to the completion date.
- [x] File moved to `.claude/plans/archive/2026/`.

## Honest-Eval Monitor

Keep the current resume session alive. If it fails or exits, run the final
audit command for `starsector-honest-eval-wave1-c0a-20260510T170431Z` and
report the result immediately.
