---
name: DDD+TDD Workflow
description: Document-driven + test-driven development lifecycle for implementation phases in the Starsector optimizer project
disable-model-invocation: true
---

# DDD+TDD Implementation Workflow

Follow this lifecycle for all non-trivial implementation work. See `CLAUDE.md` § "Workflow — DDD + TDD" for the project rule.

## Step 1: Specification

Before writing any code:

1. **Update or create spec docs** in `docs/specs/`. Each module has a numbered spec (e.g., `24-optimizer.md`, `18-instance-manager.md`).
   - Specs define: classes (frozen dataclasses), function signatures with types and defaults, algorithms, and design rationale
   - Specs are normative — the implementation must match field-by-field

2. **Include in the spec:**
   - All constructor/function parameters with types and defaults
   - Return types
   - Error conditions (what raises, what warns)
   - Algorithm pseudocode for non-trivial logic
   - Design rationale (why this approach over alternatives)

3. **Update reference docs** in `docs/reference/` if the change affects:
   - `implementation-roadmap.md` — phase descriptions, deliverables, dependencies
   - `optimization-methods.md` — sampler/algorithm selection guidance
   - `phase4-research-findings.md` — experiment results and decisions

## Step 2: Tests

Write failing tests BEFORE implementation:

1. **One test per behavioral requirement** from the spec
2. **Test structure:** Use existing patterns from the test suite:
   - `_make_*` helper functions for test data construction
   - `@pytest.fixture(scope="module")` for expensive fixtures (game data loading)
   - Class-per-concern grouping (e.g., `TestStalemateDetection`, `TestSamplerFactory`)
3. **Test error paths** — `ValueError` for invalid inputs, edge cases
4. **Never weaken tests** — if a test fails during implementation, investigate the root cause

## Step 3: Implementation

Make the tests pass:

1. **One concern per change** — don't mix features with refactoring
2. **Verify after each module** — `uv run pytest tests/test_<module>.py -v` after each implementation step, not just at the end
3. **No partial implementations** — every plan item is DONE or DEFERRED, never "partially done"
4. **If scope grows**, stop and re-plan with the user

## Step 4: Verification

After all implementation tasks:

1. **Run full test suite:** `uv run pytest tests/ -v`
2. **Run mechanical invariant checks** (use the design-invariants skill)
3. **Launch independent audit sub-agents** (use the post-impl-audit skill)
4. **Fix all audit findings** before marking the phase complete

## Step 5: Classification

Every item in the plan's scope must be classified:

- **DONE**: Implemented, tested, and verified
- **DEFERRED**: Explicitly listed with reason for deferral

No item may be unlisted.

## Documentation Updates

In the same session as code changes:
- `CLAUDE.md` project layout if new modules added
- Spec docs if function signatures changed
- Reference docs if decisions or phase status changed
- After file renames: `grep -rn "old_filename" --include="*.md" --include="*.py"`
