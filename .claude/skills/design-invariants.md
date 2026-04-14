---
name: Design Invariants Check
description: Architectural invariant checklist for the Starsector ship build optimizer — derived from CLAUDE.md design principles and invariants
disable-model-invocation: true
---

# Design Invariants Checklist

Verify these invariants against the current changes. Every applicable item must be checked during implementation review. Source: `CLAUDE.md` § "Design Principles" and § "Design Invariants".

---

## Domain Models
- [ ] All domain dataclasses are `@dataclass(frozen=True)` — `Build`, `EffectiveStats`, `ScorerResult`, `CombatFitnessConfig`, `TWFEConfig`, `ImportanceResult`, `OpponentPool`, `MatchupConfig`, `Heartbeat`, etc.
- [ ] `Build.hullmods` is `frozenset`, not `set` or `list`
- [ ] Repair always returns new instances — never mutates input
- [ ] Every `Build` returned by `repair_build()` passes `is_feasible()`

## No Magic Numbers
- [ ] Timeouts, coordinates, polling intervals, thresholds, and batch sizes live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`) — never as literals in function bodies
- [ ] Algorithm-inherent values (indices, normalization divisors like 1.0, sentinels like `float("inf")`) are exempt
- [ ] Formatting constants (column widths for display) use named local variables, not bare literals

## Single Source of Truth
- [ ] All hullmod effects in `HULLMOD_EFFECTS` dict in `hullmod_effects.py` — never duplicated in scorer, repair, or search_space
- [ ] `INCOMPATIBLE_PAIRS` and `HULL_SIZE_RESTRICTIONS` are the only locations for hullmod constraint knowledge
- [ ] `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications
- [ ] All game constants (`MAX_VENTS`, damage multipliers, etc.) live in `hullmod_effects.py`

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
- [ ] `CLAUDE.md` project layout updated if new modules added
- [ ] Reference docs updated if phase status or decisions changed
- [ ] After file renames: `grep -rn "old_filename" --include="*.md" --include="*.py"`

## Mechanical Checks (run these)

```bash
# Verify all tests pass
uv run pytest tests/ -v

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
```
