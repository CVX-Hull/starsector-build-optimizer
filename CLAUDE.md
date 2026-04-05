# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation. Phase 1 implements the data layer (game data parsing, search space definition, constraint repair, heuristic scoring, variant generation).

## Commands

- Run tests: `uv run pytest tests/ -v`
- Run single test file: `uv run pytest tests/test_parser.py -v`
- Run single test: `uv run pytest tests/test_models.py::test_weapon_sustained_dps -v`
- Game data location: `game/starsector/data/` (gitignored, not in repo)

## Workflow — DDD + TDD

For every module: write spec doc (`docs/specs/`) first, then tests, then implementation. Never implement without a spec and failing tests first.

## Design Principles

1. **Single source of truth for game knowledge.** All hardcoded hullmod effects, incompatibilities, and game constants live in `hullmod_effects.py`. Never duplicate hullmod logic in scorer, repair, or search_space.

2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult` are frozen dataclasses. Repair returns new instances. `Build.hullmods` is `frozenset`.

3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals (with `vent_fraction`, potentially infeasible) go through `repair_build()` to produce valid `Build` objects. Everything downstream of repair works with concrete, valid Builds.

4. **Data-driven over logic-driven.** Hullmod effects are a declarative `HULLMOD_EFFECTS` registry dict, not scattered if-else chains. Adding a hullmod effect = one dict entry.

5. **Forward compatibility — warn, don't crash.** Unknown enum values from future game versions: `from_str()` returns `None`, parser logs warning and skips the record. Never crash on unknown game data.

6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics. These become Phase 5 behavior descriptors and Phase 6 features without refactoring.

## Design Invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications
- `HULLMOD_EFFECTS`, `INCOMPATIBLE_PAIRS`, `HULL_SIZE_RESTRICTIONS` are the ONLY locations for hardcoded hullmod game knowledge
- All game constants (MAX_VENTS, damage multipliers, etc.) are in `hullmod_effects.py`, not scattered

## Project Layout

```
src/starsector_optimizer/
├── models.py           # Dataclasses + enums (ShipHull, Weapon, HullMod, Build, etc.)
├── hullmod_effects.py  # Game constants, hullmod effect registry, compute_effective_stats()
├── parser.py           # CSV + loose JSON → model objects
├── search_space.py     # Per-hull weapon/hullmod compatibility
├── repair.py           # Constraint enforcement (optimizer→domain boundary)
├── scorer.py           # Heuristic scoring → ScorerResult
├── variant.py          # Build → .variant JSON
└── calibration.py      # Random build generation + feature extraction

docs/
├── specs/              # DDD module specifications (drive implementation)
└── reference/          # Background research and game mechanics reference
```
