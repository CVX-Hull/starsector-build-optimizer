---
name: Plan Review
description: Validate implementation plan against DDD+TDD practices, design invariants, and writing quality before approval
disable-model-invocation: true
type: skill
status: shipped
last-validated: 2026-05-10
---

# Plan Review

Run this **before approving a plan or moving from planning into implementation**. Validates the plan for correctness, completeness, and clarity.

Plan review operates on a plan file path. Plans live under
`.claude/plans/active/` while current and are managed by the
`plan-lifecycle` skill. Do not review chat-only prose for non-trivial work.

The plan must include visible `## Plan Review Gate` and
`## Fresh-Eye Review Gate` sections. A plan is not approved until both sections
record `Status: passed`, the Plan Review Gate references this `plan-review`
skill, the Fresh-Eye Review Gate records the independent sub-agent lanes, and
frontmatter `status`/`approved` are updated afterward. If scope changes after
approval, reset both gates to `not_run` and rerun this review before
implementation continues.

## Phase 1: Writing Quality (self-review)

Review the plan text for:

- [ ] **Coherence**: Read end-to-end. Does each step flow logically from the previous? Are there contradictions between sections?
- [ ] **Consistency**: Do code snippets match prose descriptions? Do step numbers match the Critical Files table? Do config field names match between config section and implementation code?
- [ ] **Ambiguity**: Flag any sentence where a reader could reasonably interpret it two ways. Replace with precise language. Common offenders: "should", "might", "for now", "as needed", "if applicable".
- [ ] **Completeness**: Every file in the Files table has a corresponding step. Every step produces a verifiable artifact.

## Phase 2: DDD+TDD Practices (self-review)

### Specification Phase
- [ ] Does the plan write/update spec docs in `docs/specs/` BEFORE tests and implementation?
- [ ] Do specs define class signatures, function parameters with types and defaults, and return types?
- [ ] Are cross-cutting updates included (root workflow file, reference docs)?

### Test Phase
- [ ] Are tests derived from spec requirements?
- [ ] Does the plan include error path tests (ValueError, edge cases)?
- [ ] Do test names describe the behavior being tested?

### Implementation Phase
- [ ] Does the plan mix feature work with refactoring or cleanup? (It shouldn't)
- [ ] Audit every numeric literal in code snippets. Each must be a config field, named constant, or algorithm-inherent.
- [ ] Does the plan include test runs after each implementation step, not just at the end?
- [ ] Is every plan item clearly DONE or DEFERRED?

### Verification Phase
- [ ] Does the plan end with a full test suite run?
- [ ] Does the plan include grep-based checks for stale references?

## Phase 3: Engineering Principles (self-review)

Validate against the root workflow file's engineering principles:

- [ ] **Principled over expedient**: every shortcut in the plan has explicit justification — otherwise the principled form is taken. If the plan picks a small fix where a larger one is the principled fix, both are named and the user is asked which to take.
- [ ] **No deferred-issue residue**: every issue surfaced by the plan (existing TODOs the plan won't fix, suspect code in touched modules, known-flaky tests, dormant code paths) is either fixed in scope, or explicitly listed in the plan's DEFERRED section with user approval.
- [ ] **No new TODO/FIXME/XXX/HACK introduced** outside of explicitly user-approved deferred items.
- [ ] **No new test skips, type-ignores, lint suppressions, or swallowed exceptions** unless the plan calls them out with reason.
- [ ] **No tests weakened** to accommodate the plan — if tests need to change, the change is justified by the spec, not by implementation convenience.

## Phase 4: Design Invariants (self-review)

Validate against the root workflow file's design principles and invariants:

- [ ] **Frozen dataclasses**: Are all new dataclasses `@dataclass(frozen=True)`?
- [ ] **No magic numbers**: Are all thresholds in config dataclasses, not function bodies?
- [ ] **Single source of truth**: Does the plan create any duplicate game knowledge?
- [ ] **Config dataclasses**: Are new tunable parameters in the appropriate config class?
- [ ] **Forward compatibility**: Does error handling warn instead of crash for unknown data?
- [ ] **Game data verification**: Does the plan verify game facts against actual files?
- [ ] **Repair boundary**: Does optimizer-space → domain-space conversion go through `repair_build()`?

## Phase 5: Independent Sub-Agent Audits

Fresh-eye sub-agent review is part of plan approval for non-trivial work.
Follow the global `sub-agent-orchestration` skill shape: bounded task, minimal
context, no leaked expected findings, and one clear deliverable per auditor.
If the active runtime requires explicit current-turn authorization to launch
sub-agents, the plan cannot be approved until that authorization is obtained
and the sub-agents complete.

Launch **3 sub-agents in parallel**. Each is an independent auditor — provide only the plan path and reference material. Do not hint at expected findings.

### Sub-Agent A: Pattern Consistency

> "You are an independent code auditor. Read the implementation plan at `{plan_path}`. Read the root workflow file for project practices and design principles. For every code snippet and structural decision in the plan, find the analogous existing pattern in this codebase and compare. Report any divergence from established patterns, any practice violation, and any inconsistency you find. Use your own judgment about what to check — do not assume the plan is correct."

### Sub-Agent B: Spec Alignment

> "You are an independent spec auditor. Read the implementation plan at `{plan_path}`. Read every spec doc in `docs/specs/` referenced in the plan. Compare the plan against those specs requirement by requirement. Report anything the plan gets wrong, anything the plan omits that the spec requires, anything the plan adds that the spec doesn't call for, and any contradiction. Quote the relevant spec sections. Use your own judgment — do not assume the plan is correct."

### Sub-Agent C: Engineering & Design Invariants

> "You are an independent design auditor. Read the implementation plan at `{plan_path}`. Read the root workflow file for engineering principles, design principles, and design invariants. Evaluate the plan against every applicable invariant — including the global engineering invariants ('principled over expedient', 'address issues, don't paper over them'). Specifically flag: shortcuts taken without explicit justification, deferrals that aren't called out as explicit DEFERRED items with user-approval rationale, new TODO/FIXME/skip/type-ignore/lint-suppression introductions, and tests being weakened to fit the implementation. Report any violation or risk. Use your own judgment about which invariants apply — do not assume the plan is correct."

## Execution

1. Confirm the target plan has `## Plan Review Gate` and
   `## Fresh-Eye Review Gate`.
2. Run Phases 1-4 yourself (checklist self-review).
3. Launch all 3 sub-agents for Phase 5 in a **single message** (parallel execution).
4. Review all sub-agent findings. Fix every valid finding in the plan.
5. Update `## Plan Review Gate` and `## Fresh-Eye Review Gate` with status,
   findings, agents, and dispositions.
6. Only approve the plan / proceed to implementation after all findings are resolved.
