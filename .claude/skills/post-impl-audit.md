---
name: Post-Implementation Audit
description: Mechanical checks and sub-agent verification after completing implementation phases for the Starsector optimizer
disable-model-invocation: true
type: skill
status: shipped
last-validated: 2026-05-10
---

# Post-Implementation Audit

Run this after completing implementation tasks. Combines the after-changes checklist, mechanical invariant checks, and sub-agent audit.

## After Changes Checklist

1. **Run ALL tests**: `uv run pytest tests/ -v`
2. **Investigate ALL failures**: Every failing test is a potential design issue — read the test, read the traceback, identify root cause. Never dismiss failures as "flaky" without reading the code.
3. **Verify syntax**: `python -c "import ast; ast.parse(open('FILE').read())"` for each changed file
4. **Verify imports**: `python -c "from starsector_optimizer.MODULE import SYMBOL"` for new exports
5. **Update the root workflow file**: Project layout if new modules added
6. **Update spec docs**: If function signatures, parameters, or defaults changed
7. **Update reference docs**: If decisions, phase status, or experiment findings changed
8. **After file renames**: `grep -rn "old_filename" --include="*.md" --include="*.py"` across the entire codebase

## Mechanical Checks

Run these for ALL changes. Check results and fix any issues:

```bash
# 1. Full test suite
uv run pytest tests/ -v

# 2. Stale references after renames or removals (substitute actual old name)
grep -rn "OLD_NAME" --include="*.md" --include="*.py" src/ tests/ docs/

# 3. Bare MagicMock without spec= in new test code
grep -rn "MagicMock()" tests/ --include="*.py" | grep -v "spec=\|side_effect\|return_value\|# justified"

# 4. Magic numbers in new implementation code (spot check changed files)
# Look for bare numeric literals in function bodies — should be config fields or named constants
grep -n "[^=] = [0-9]" src/starsector_optimizer/CHANGED_FILE.py | grep -v "def \|#\|self\.\|config\."

# 5. Frozen dataclass verification for new dataclasses
grep -B1 "class NewDataclass" src/starsector_optimizer/models.py
# Should show @dataclass(frozen=True) above each

# 6. Spec-code alignment — verify config field defaults match between spec and implementation
# Compare docs/specs/NN-module.md field tables against actual dataclass definitions
```

## Sub-Agent Audit

In this repository workflow, a user request to run a named workflow that
includes a sub-agent lane (for example, **"post-impl audit"**) is intended
as project-level authorization to launch that workflow's bounded audit
sub-agents. Launch **3 independent sub-agents in parallel** (single
message, 3 Agent tool calls). Each auditor receives only the plan path
and reference material — no hints about expected findings.

Higher-priority runtime or developer instructions still take
precedence. If the active session requires explicit authorization in the
current turn, do not spawn agents until the user says e.g. **"launch audit
sub-agents"**.

### Sub-Agent A: Plan-vs-Code Verification

> "You are an independent code auditor. Read the plan file at `{plan_path}`. Read every file listed in the plan's Files table. Verify each plan step was implemented correctly by reading the actual code. Report what matches, what diverges, what's missing, and what was added beyond the plan. Use your own judgment — do not assume the implementation is correct."

### Sub-Agent B: Engineering & Design Invariant Audit

> "You are an independent design auditor. Read the implementation files changed in this session. Read the root workflow file for engineering principles, design principles, and design invariants. Evaluate the implementation against every applicable invariant: (engineering) principled-over-expedient choices, no papered-over issues, no new TODO/FIXME/skip/type-ignore/lint-suppression deferrals without explicit user approval, no swallowed exceptions, no tests weakened to make failures pass; (design) frozen dataclasses, no magic numbers, config dataclasses, single source of truth, game data verification, forward compatibility, repair boundary. Check that `should_stop()` return values are handled correctly downstream (read `instance_manager.py`). For each issue found, judge whether the implementation chose the principled fix or an expedient shortcut. Report any violation or risk."

### Sub-Agent C: Spec Alignment Audit

> "You are an independent spec auditor. Read the implementation files changed in this session. Read every spec doc in `docs/specs/` relevant to the changes. Compare the implementation against spec requirements field-by-field: class signatures, function signatures, parameter types and defaults, return types, error conditions. Report any mismatch. Quote the relevant spec sections."

**Fix all audit findings before marking the phase complete.** Every implementation phase ends with: implement -> audit agents -> fix findings -> regression test.

## No Deferring During Implementation

Every item in an approved plan must be completed fully. The plan is the deferral mechanism — items not in the plan are deferred to a future phase. Items IN the plan are committed scope. This is the implementation-time corollary of the engineering principle to address issues at root cause.

- **Do not partially implement plan items.** No "easy part now, rest later", no TODO comments.
- **If an item is larger than expected**, stop and re-plan with the user.
- **Check completeness before marking tasks done.**
- **Issues discovered mid-implementation that aren't in the plan**: address them in scope if they're in code already being touched (boy-scout rule), or surface them to the user with a proposed fix and ask before deferring. Don't quietly add a TODO.
- **Test failures are signal, not noise.** Investigate root cause; never `pytest.skip` or weaken an assertion to make a failure pass without explicit user approval.
