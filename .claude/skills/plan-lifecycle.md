---
name: Plan Lifecycle
description: Create, review, track, retire, and archive implementation plan files without turning them into durable design authority
disable-model-invocation: true
type: skill
status: shipped
last-validated: 2026-05-10
---

# Plan Lifecycle

Use this skill when work is large enough that chat state is not a reliable
source of truth. A plan file is an execution record: it describes the intended
work, review findings, implementation scope, verification, and retirement.
It is not durable design authority. Current truth lives in code, specs,
reference docs, reports, tests, and the root workflow file.

## Storage

Plan files live under the repo-local workflow directory:

```text
.claude/plans/
├── active/
└── archive/
```

Use `active/` for work that is still planned, approved, or being implemented.
Use `archive/YYYY/` for completed or superseded plans. Keep active plans few
enough that the directory can be scanned at the start of a work session.

## When To Create A Plan

Create a plan before implementation for non-trivial work:

- Multi-file code changes.
- Protocol, schema, persistence, telemetry, cloud, setup, or test-fixture
  boundary changes.
- Public concept renames, removals, or authority moves.
- Spec/reference/report systems work that affects documentation ownership.
- Any implementation that requires formal plan review.

Tiny single-file edits may stay in chat when they do not cross a boundary and
do not need formal review.

## Naming

Use stable, sortable names:

```text
.claude/plans/active/YYYY-MM-DD-short-slug.md
.claude/plans/archive/YYYY/YYYY-MM-DD-short-slug.md
```

The date is the date the plan is created. Do not rename a plan during
implementation except when moving it from `active/` to `archive/YYYY/`.

## Frontmatter

Every plan starts with:

```yaml
---
plan_type: implementation
status: draft
created: YYYY-MM-DD
approved: null
implemented: null
owner: agent
related_docs: []
implementation_commit: null
post_impl_audit: null
superseded_by: null
---
```

Allowed statuses:

- `draft` — being written or reviewed.
- `approved` — plan review passed, the plan's `Plan Review Gate` records
  `Status: passed`, and the plan's `Fresh-Eye Review Gate` records
  `Status: passed`; implementation may proceed.
- `active` — implementation is underway.
- `implemented` — implementation, verification, and post-implementation audit
  are complete.
- `superseded` — replaced before completion.

Use `owner: agent` unless a human owner is explicitly named. `related_docs`
lists owning specs, references, reports, skills, or root workflow files that
the plan expects to touch or obey.

## Required Sections

Non-trivial plans include:

- Goal.
- Context and source docs.
- Scope.
- Out of scope.
- Critical files.
- Public concepts and canonical owners.
- Step-by-step implementation sequence.
- Tests and mechanical gates.
- Review findings and dispositions.
- Plan Review Gate.
- Fresh-Eye Review Gate.
- Post-implementation audit requirements.
- Retirement checklist.

For small but still non-trivial work, keep the sections compact instead of
omitting the lifecycle fields.

## Review Procedure

1. Create or update the plan in `active/`.
2. Leave frontmatter `status: draft` and `approved: null` until the review has
   actually passed. Do not mark a newly created plan approved during creation.
3. Add a `## Plan Review Gate` section to the plan:

   ```text
   ## Plan Review Gate

   - Status: not_run | passed | failed
   - Review source: `.claude/skills/plan-review.md`
   - Reviewed at: YYYY-MM-DD HH:MM | null
   - Findings:
     - ...
   - Dispositions:
     - ...
   - Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.
   ```

4. Run the `plan-review` skill against the file path, not chat text.
5. Add a `## Fresh-Eye Review Gate` section to the plan:

   ```text
   ## Fresh-Eye Review Gate

   - Status: not_run | passed
   - Review source: sub-agents via `.claude/skills/plan-review.md`
   - Reviewed at: YYYY-MM-DD HH:MM | null
   - Agents:
     - Pattern Consistency: pending | passed | findings
     - Spec Alignment: pending | passed | findings
     - Engineering & Design Invariants: pending | passed | findings
   - Findings:
     - ...
   - Dispositions:
     - ...
   - Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.
   ```

6. Launch the plan-review sub-agents when the active runtime permits sub-agent
   use. If the runtime requires explicit current-turn authorization, stop and
   ask for that authorization rather than approving the plan without fresh-eye
   review.
7. Resolve every valid finding in the plan.
8. Set both Plan Review Gate and Fresh-Eye Review Gate to `Status: passed`.
9. Set frontmatter `status: approved` and `approved: YYYY-MM-DD` before
   implementation.

If implementation discovers that the approved plan is materially wrong, update
the plan, reset the Plan Review Gate and Fresh-Eye Review Gate to
`Status: not_run`, restore frontmatter `status: draft`, and rerun plan review
before continuing. Do not silently implement a different plan.

## Implementation Procedure

During implementation:

- Treat the active plan as the scope source of truth.
- Keep the plan current when scope, files, gates, or deferrals change.
- Mark discovered issues as fixed-in-scope or explicitly deferred with user
  approval. Do not leave TODO/FIXME/HACK comments as hidden deferrals.
- Record verification commands and outcomes in the plan or in the owning
  report when the work is report-driven.

## Retirement Procedure

After implementation and post-implementation audit:

1. Set `status: implemented`.
2. Set `implemented: YYYY-MM-DD`.
3. Set `implementation_commit` to the commit hash, or `not_committed`.
4. Set `post_impl_audit` to `passed`, `failed`, or a relative link to the
   audit record.
5. Move the file to `.claude/plans/archive/YYYY/`.
6. Groom `docs/roadmap.md`: delete the delivered items, absorb any follow-up
   work the plan surfaced (see [`doc-grooming`](doc-grooming.md) step 2).

If the plan is replaced before implementation:

1. Set `status: superseded`.
2. Set `superseded_by` to the replacement plan path.
3. Add a short supersession note in the body.
4. Move the file to `.claude/plans/archive/YYYY/`.

## Noise Control

- Do not index archived plans in durable doc indexes.
- Do not load archived plans by default from the root workflow file.
- Do not cite an archived plan as current design authority.
- Search archived plans only for historical execution context.
- A stale active plan should be implemented, updated, superseded, or archived.
