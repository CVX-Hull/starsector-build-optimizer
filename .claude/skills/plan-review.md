---
name: Plan Review
description: Validate implementation plan against DDD+TDD practices, design invariants, and writing quality before approval
disable-model-invocation: true
---

# Plan Review

Run this **before calling ExitPlanMode**. Validates the plan for correctness, completeness, and clarity.

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
- [ ] Are cross-cutting updates included (CLAUDE.md layout, reference docs)?

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

## Phase 3: Design Invariants (self-review)

Validate against `CLAUDE.md` § "Design Principles" and § "Design Invariants":

- [ ] **Frozen dataclasses**: Are all new dataclasses `@dataclass(frozen=True)`?
- [ ] **No magic numbers**: Are all thresholds in config dataclasses, not function bodies?
- [ ] **Single source of truth**: Does the plan create any duplicate game knowledge?
- [ ] **Config dataclasses**: Are new tunable parameters in the appropriate config class?
- [ ] **Forward compatibility**: Does error handling warn instead of crash for unknown data?
- [ ] **Game data verification**: Does the plan verify game facts against actual files?
- [ ] **Repair boundary**: Does optimizer-space → domain-space conversion go through `repair_build()`?

## Phase 4: Independent Sub-Agent Audits

Launch **3 sub-agents in parallel**. Each is an independent auditor — provide only the plan path and reference material. Do not hint at expected findings.

### Sub-Agent A: Pattern Consistency

> "You are an independent code auditor. Read the implementation plan at `{plan_path}`. Read `CLAUDE.md` for project practices and design principles. For every code snippet and structural decision in the plan, find the analogous existing pattern in this codebase and compare. Report any divergence from established patterns, any practice violation, and any inconsistency you find. Use your own judgment about what to check — do not assume the plan is correct."

### Sub-Agent B: Spec Alignment

> "You are an independent spec auditor. Read the implementation plan at `{plan_path}`. Read every spec doc in `docs/specs/` referenced in the plan. Compare the plan against those specs requirement by requirement. Report anything the plan gets wrong, anything the plan omits that the spec requires, anything the plan adds that the spec doesn't call for, and any contradiction. Quote the relevant spec sections. Use your own judgment — do not assume the plan is correct."

### Sub-Agent C: Design Invariants

> "You are an independent design auditor. Read the implementation plan at `{plan_path}`. Read `CLAUDE.md` for design principles and invariants. Evaluate the plan against every applicable invariant. Report any violation or risk. Use your own judgment about which invariants apply — do not assume the plan is correct."

## Execution

1. Run Phases 1-3 yourself (checklist self-review).
2. Launch all 3 sub-agents for Phase 4 in a **single message** (parallel execution).
3. Review all sub-agent findings. Fix every valid finding in the plan.
4. Only call ExitPlanMode after all findings are resolved.
