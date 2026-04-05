"""Constraint repair operator — boundary between optimizer-space and domain-space."""

from __future__ import annotations

from .hullmod_effects import (
    HULL_SIZE_RESTRICTIONS,
    INCOMPATIBLE_PAIRS,
    SHIELD_DEPENDENT_MODS,
    SLOT_COMPATIBILITY,
)
from .models import (
    Build,
    GameData,
    HullSize,
    ShipHull,
    MAX_LOGISTICS_HULLMODS,
)


def compute_op_cost(build: Build, hull: ShipHull, game_data: GameData) -> int:
    """Compute total OP cost of a build."""
    cost = 0
    for slot_id, weapon_id in build.weapon_assignments.items():
        if weapon_id and weapon_id in game_data.weapons:
            cost += game_data.weapons[weapon_id].op_cost
    for mod_id in build.hullmods:
        if mod_id in game_data.hullmods:
            cost += game_data.hullmods[mod_id].op_cost(hull.hull_size)
    cost += build.flux_vents + build.flux_capacitors
    return cost


def _repair_incompatibilities(hullmods: set[str], hull: ShipHull, game_data: GameData) -> set[str]:
    """Remove hullmods that violate incompatibility or size restriction rules."""
    # Hull size restrictions
    for mod_id in list(hullmods):
        if mod_id in HULL_SIZE_RESTRICTIONS:
            if hull.hull_size not in HULL_SIZE_RESTRICTIONS[mod_id]:
                hullmods.discard(mod_id)

    # Incompatible pairs
    for a, b in INCOMPATIBLE_PAIRS:
        if a in hullmods and b in hullmods:
            # Remove the one with lower OP cost
            cost_a = game_data.hullmods[a].op_cost(hull.hull_size) if a in game_data.hullmods else 0
            cost_b = game_data.hullmods[b].op_cost(hull.hull_size) if b in game_data.hullmods else 0
            hullmods.discard(b if cost_b <= cost_a else a)

    # Shield-dependent mods: remove if hull has no shields or Shield Shunt installed
    has_shields = hull.shield_type.value not in ("NONE", "PHASE") and "shield_shunt" not in hullmods
    if not has_shields:
        for mod_id in list(hullmods):
            if mod_id in SHIELD_DEPENDENT_MODS:
                hullmods.discard(mod_id)

    return hullmods


def _repair_logistics(hullmods: set[str], hull: ShipHull, game_data: GameData) -> set[str]:
    """Enforce max logistics hullmod limit."""
    logistics = [(m, game_data.hullmods[m].op_cost(hull.hull_size))
                 for m in hullmods
                 if m in game_data.hullmods and game_data.hullmods[m].is_logistics]
    if len(logistics) <= MAX_LOGISTICS_HULLMODS:
        return hullmods
    # Keep the highest-cost logistics mods
    logistics.sort(key=lambda x: x[1], reverse=True)
    to_remove = {m for m, _ in logistics[MAX_LOGISTICS_HULLMODS:]}
    return hullmods - to_remove


def _repair_op_budget(
    weapons: dict[str, str | None],
    hullmods: set[str],
    hull: ShipHull,
    game_data: GameData,
) -> tuple[dict[str, str | None], set[str]]:
    """Greedily drop lowest value-per-OP items until budget is met."""
    def _total_cost():
        c = 0
        for wid in weapons.values():
            if wid and wid in game_data.weapons:
                c += game_data.weapons[wid].op_cost
        for mid in hullmods:
            if mid in game_data.hullmods:
                c += game_data.hullmods[mid].op_cost(hull.hull_size)
        return c

    while _total_cost() > hull.ordnance_points:
        # Build list of removable items with value/OP
        candidates = []
        for slot_id, wid in weapons.items():
            if wid and wid in game_data.weapons:
                w = game_data.weapons[wid]
                value = w.sustained_dps / max(w.op_cost, 1)
                candidates.append(("weapon", slot_id, value))
        for mid in hullmods:
            if mid in game_data.hullmods:
                cost = game_data.hullmods[mid].op_cost(hull.hull_size)
                candidates.append(("hullmod", mid, 1.0 / max(cost, 1)))

        if not candidates:
            break

        # Remove lowest value item
        candidates.sort(key=lambda x: x[2])
        kind, key, _ = candidates[0]
        if kind == "weapon":
            weapons[key] = None
        else:
            hullmods.discard(key)

    return weapons, hullmods


def repair_build(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
    vent_fraction: float = 0.5,
) -> Build:
    """Repair a build to satisfy all constraints.

    This is the boundary between optimizer-space and domain-space.
    """
    # Work with mutable copies
    weapons = dict(build.weapon_assignments)
    hullmods = set(build.hullmods)

    # Step 1: Fix hullmod incompatibilities and restrictions
    hullmods = _repair_incompatibilities(hullmods, hull, game_data)

    # Step 2: Fix logistics limit
    hullmods = _repair_logistics(hullmods, hull, game_data)

    # Step 3: Fix OP budget (drop items, vents/caps reset to 0 for budget calc)
    weapons, hullmods = _repair_op_budget(weapons, hullmods, hull, game_data)

    # Step 4: Allocate remaining OP to vents/caps
    item_cost = 0
    for wid in weapons.values():
        if wid and wid in game_data.weapons:
            item_cost += game_data.weapons[wid].op_cost
    for mid in hullmods:
        if mid in game_data.hullmods:
            item_cost += game_data.hullmods[mid].op_cost(hull.hull_size)

    remaining = max(0, hull.ordnance_points - item_cost)
    vents = min(round(vent_fraction * remaining), hull.max_vents)
    caps = min(remaining - vents, hull.max_capacitors)
    # If caps went negative (vents took too much), reduce vents
    if caps < 0:
        vents = min(remaining, hull.max_vents)
        caps = 0

    return Build(
        hull_id=build.hull_id,
        weapon_assignments=weapons,
        hullmods=frozenset(hullmods),
        flux_vents=vents,
        flux_capacitors=caps,
    )


def is_feasible(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> tuple[bool, list[str]]:
    """Check if a build satisfies all constraints."""
    violations = []

    # C1: OP budget
    cost = compute_op_cost(build, hull, game_data)
    if cost > hull.ordnance_points:
        violations.append(f"OP budget exceeded: {cost} > {hull.ordnance_points}")

    # C2: Slot compatibility
    slot_map = {s.id: s for s in hull.weapon_slots}
    for slot_id, weapon_id in build.weapon_assignments.items():
        if not weapon_id:
            continue
        if slot_id not in slot_map:
            violations.append(f"Unknown slot {slot_id}")
            continue
        slot = slot_map[slot_id]
        if weapon_id in game_data.weapons:
            weapon = game_data.weapons[weapon_id]
            allowed = SLOT_COMPATIBILITY.get(slot.slot_type, set())
            if weapon.weapon_type not in allowed:
                violations.append(
                    f"Weapon {weapon_id} type {weapon.weapon_type.value} "
                    f"incompatible with slot {slot_id} type {slot.slot_type.value}"
                )
            if weapon.size != slot.slot_size:
                violations.append(
                    f"Weapon {weapon_id} size {weapon.size.value} "
                    f"doesn't match slot {slot_id} size {slot.slot_size.value}"
                )

    # C3: Incompatible pairs
    for a, b in INCOMPATIBLE_PAIRS:
        if a in build.hullmods and b in build.hullmods:
            violations.append(f"Incompatible hullmods: {a} + {b}")

    # C4: Hull size restrictions
    for mod_id in build.hullmods:
        if mod_id in HULL_SIZE_RESTRICTIONS:
            if hull.hull_size not in HULL_SIZE_RESTRICTIONS[mod_id]:
                violations.append(f"Hullmod {mod_id} not allowed on {hull.hull_size.value}")

    # C5: Logistics limit
    logistics_count = sum(
        1 for m in build.hullmods
        if m in game_data.hullmods and game_data.hullmods[m].is_logistics
    )
    if logistics_count > MAX_LOGISTICS_HULLMODS:
        violations.append(f"Too many logistics mods: {logistics_count} > {MAX_LOGISTICS_HULLMODS}")

    # C6: Vent/cap limits
    if build.flux_vents > hull.max_vents:
        violations.append(f"Vents exceed max: {build.flux_vents} > {hull.max_vents}")
    if build.flux_capacitors > hull.max_capacitors:
        violations.append(f"Caps exceed max: {build.flux_capacitors} > {hull.max_capacitors}")

    return (len(violations) == 0, violations)
