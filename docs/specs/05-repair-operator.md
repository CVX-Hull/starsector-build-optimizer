# Repair Operator Specification

Constraint enforcement — the boundary between optimizer-space and domain-space. Defined in `src/starsector_optimizer/repair.py`.

## Functions

### compute_op_cost(build, hull, game_data) → int
Sum of: weapon OP costs + hullmod OP costs (by hull size) + flux_vents + flux_capacitors.

### repair_build(build, hull, game_data, vent_fraction=0.5) → Build
Full pipeline: incompatibilities → hull size restrictions → shield-dependent mods → logistics limit → OP budget → flux allocation. Returns new frozen Build.

### is_feasible(build, hull, game_data) → tuple[bool, list[str]]
Checks all constraints. Returns (True, []) or (False, [violation messages]).

Constraints checked:
- C1: OP budget (total cost ≤ ordnance_points)
- C2: Slot compatibility (all assigned weapons fit their slots)
- C3: Hullmod incompatibilities (no pair from INCOMPATIBLE_PAIRS)
- C4: Hull size restrictions (HULL_SIZE_RESTRICTIONS)
- C5: Logistics limit (≤ MAX_LOGISTICS_HULLMODS logistics mods)
- C6: Vent/cap limits (≤ hull.max_vents, ≤ hull.max_capacitors)
