# Repair Operator Specification

Constraint enforcement — the boundary between optimizer-space and domain-space. Defined in `src/starsector_optimizer/repair.py`.

All signatures take a `manifest: GameManifest` positional argument.
It is the authoritative source for OP costs, hullmod
incompatibilities, hull-size applicability, and engine caps
(`max_vents_per_ship`, `max_capacitors_per_ship`,
`max_logistics_hullmods`). See spec 29 for the invariant.

## Functions

### compute_op_cost(build, hull, manifest) → int
Sum of: weapon OP costs (`manifest.weapons[w].op_cost`) +
hullmod OP costs by hull size
(`manifest.hullmods[h].op_cost_by_size[hull.hull_size]` — 0 for
built-in mods, reflecting the engine's free-discount contract) +
flux_vents + flux_capacitors.

### repair_build(build, hull, manifest, vent_fraction=0.5) → Build
Full pipeline: incompatibilities → hull-size applicability →
shield-dependent mods → logistics limit → OP budget → flux
allocation. Returns a new frozen Build.

### is_feasible(build, hull, manifest) → tuple[bool, list[str]]
Returns `(True, [])` or `(False, [violation messages])`.

Constraints checked:
- C1: OP budget (total cost ≤ `hull.ordnance_points`).
- C2: Slot compatibility (every assigned weapon fits its slot per
  `SLOT_WEAPON_COMPATIBILITY`).
- C3: Hullmod incompatibilities — no pair `(a, b)` where
  `b ∈ manifest.hullmods[a].incompatible_with`.
- C4: Hull-size applicability —
  `hull.hull_size ∈ manifest.hullmods[h].applicable_hull_sizes`
  for every installed `h`.
- C5: Logistics limit — at most
  `manifest.constants.max_logistics_hullmods` hullmods carrying the
  `"logistics"` tag in `manifest.hullmods[h].tags`.
- C6: Vent / cap limits — `flux_vents ≤ hull.max_vents`,
  `flux_capacitors ≤ hull.max_capacitors` (both populated from
  manifest constants at hull-construction time).
