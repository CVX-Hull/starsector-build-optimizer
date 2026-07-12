---
name: Design Invariants Check
description: Architectural invariant checklist for the Starsector ship build optimizer — derived from the root workflow file's design principles and invariants
disable-model-invocation: true
type: skill
status: shipped
last-validated: 2026-07-12
---

# Design Invariants Checklist

Verify these invariants against the current changes. Every applicable item must be checked during implementation review. Source: the root workflow file's engineering principles, design principles, and design invariants.

---

## Engineering Principles (global)

Source: the root workflow file's engineering principles. These apply to every change, not just architectural ones.

- [ ] **Principled over expedient**: every shortcut in this change has explicit user justification — otherwise the principled form was taken
- [ ] **Root-cause fixes only**: every observed problem in touched code has been root-cause-fixed in this change, OR explicitly raised to the user with a proposed fix and deferred with explicit consent
- [ ] **No new TODO/FIXME/XXX/HACK comments** introduced as deferral mechanisms (TODOs are acceptable only when listed in the plan's DEFERRED section with user approval)
- [ ] **No new `pytest.skip` / `@pytest.mark.skip` / `# type: ignore` / lint suppressions** without an explicit user-approved reason in code or PR description
- [ ] **No swallowed exceptions** (bare `except:` or `except Exception: pass`) added to silence problems
- [ ] **No tests weakened** to make a failure pass — root cause was investigated instead
- [ ] **Boy-scout fixes applied**: issues observed in touched files but unrelated to the immediate task were addressed in the same change, or surfaced to the user

## Domain Models
- [ ] All domain dataclasses are `@dataclass(frozen=True)` — `Build`, `EngineStats`, `ScorerResult`, `CombatFitnessConfig`, `TWFEConfig`, `ImportanceResult`, `OpponentPool`, `MatchupConfig`, `Heartbeat`, etc.
- [ ] `Build.hullmods` is `frozenset`, not `set` or `list`
- [ ] Repair always returns new instances — never mutates input
- [ ] Every `Build` returned by `repair_build()` passes `is_feasible()`

## No Magic Numbers
- [ ] Timeouts, coordinates, polling intervals, thresholds, and batch sizes live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`) — never as literals in function bodies
- [ ] Algorithm-inherent values (indices, normalization divisors like 1.0, sentinels like `float("inf")`) are exempt
- [ ] Formatting constants (column widths for display) use named local variables, not bare literals

## Single Source of Truth
- [ ] `game/starsector/manifest.json` is the only source for hullmod applicability, conditional exclusions, and damage multipliers
- [ ] Python loads game-rule facts through `GameManifest.load()` / `manifest.constants`, not hardcoded registries
- [ ] Java `EngineStats` emission owns hullmod-adjusted combat stats; Python does not reimplement hullmod stat effects
- [ ] Deleted registries stay deleted: no `hullmod_effects.py`, no `HULLMOD_EFFECTS`, no `compute_effective_stats()` resurrection

## Optimizer-Space vs Domain-Space Boundary
- [ ] Raw optimizer proposals go through `repair_build()` before any domain logic
- [ ] `define_distributions()` produces optimizer-space parameters
- [ ] `trial_params_to_build()` converts optimizer-space → domain-space
- [ ] Fixed params are merged in `trial_params_to_build()`, not in the sampler

## Config Dataclasses
- [ ] `OptimizerConfig` — all optimizer tuning parameters
- [ ] `InstanceConfig` — game instance management parameters
- [ ] `CombatFitnessConfig` — all fitness function coefficients
- [ ] New tunable parameters added to the appropriate config, not as function-body literals

## Game Data Verification (Principle 7)
- [ ] Hullmod IDs verified against `data/hullmods/hull_mods.csv` — not guessed
- [ ] Weapon types verified from `.wpn` files — `weapon_data.csv` `type` column is damage type, not weapon type
- [ ] Hull sizes from `.ship` JSON `hullSize` — `ship_data.csv` `designation` is a role string
- [ ] Opponent variant IDs verified to exist as `.variant` files under `data/variants/`

## Forward Compatibility (Principle 5)
- [ ] Unknown enum values: `from_str()` returns `None`, parser logs warning and skips
- [ ] Never crash on unknown game data — warn and continue

## Testing Patterns
- [ ] Tests use `_make_*` helpers for test data — not raw constructor calls scattered everywhere
- [ ] Module-scoped fixtures for expensive operations (`game_data` loading)
- [ ] Error paths tested — `ValueError` for invalid inputs, edge cases
- [ ] No bare `MagicMock()` on new code — use `spec=` or configure return values

## Documentation
- [ ] Spec docs updated in the same session as code changes — function signatures, parameters, defaults must match
- [ ] Root workflow file project layout updated if new modules added
- [ ] Reference docs updated if phase status or decisions changed
- [ ] After file renames: `grep -rn "old_filename" --include="*.md" --include="*.py"`

## Mechanical Checks (run these)

```bash
# Verify all tests pass
uv run pytest tests/ -v

# Quality gates (adopted 2026-07-12 — also enforced by .githooks/pre-commit;
# evidence: docs/reports/2026-07-12-quality-tooling-research.md)
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run deptry .
# Java gates run inside the Gradle build (-Werror + Error Prone + NullAway):
# cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew jar test

# Check for stale references after renames
grep -rn "OLD_NAME" --include="*.md" --include="*.py" src/ tests/ docs/

# Check no bare MagicMock() in new test code
grep -rn "MagicMock()" tests/ --include="*.py" | grep -v "spec=\|side_effect\|return_value\|# justified"

# Verify syntax on changed files
python -c "import ast; ast.parse(open('FILE').read())"

# Verify imports work
python -c "from starsector_optimizer.MODULE import SYMBOL"

# Check frozen dataclasses
grep -B1 "class NewDataclass" src/starsector_optimizer/models.py
# Should show @dataclass(frozen=True) above each

# Engineering principles — scan changed files for deferral patterns
git diff --name-only HEAD | xargs grep -nE "(TODO|FIXME|XXX|HACK)" 2>/dev/null
git diff --name-only HEAD | xargs grep -nE "(pytest\.skip|@pytest\.mark\.skip|# type: ignore|# noqa|# pragma)" 2>/dev/null
git diff --name-only HEAD | xargs grep -nE "except[^:]*:\s*pass" 2>/dev/null
# Each hit must have user-approved justification or be removed
```
