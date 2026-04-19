"""Constraint repair operator — boundary between optimizer-space and domain-space.

All game-rule constraints (incompat pairs, hull-size restrictions, vent/cap
caps, logistics-mod cap, slot compat) come from the GameManifest. The old
hand-coded `hullmod_effects.py` registry is gone; every callsite here
takes the manifest as a positional arg.
"""

from __future__ import annotations

from .game_manifest import GameManifest, SLOT_WEAPON_COMPATIBILITY
from .models import (
    Build,
    GameData,
    HullSize,
    ShipHull,
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


def _repair_incompatibilities(
    hullmods: set[str], hull: ShipHull,
    game_data: GameData, manifest: GameManifest,
) -> set[str]:
    """Remove hullmods that violate size or pair-incompatibility rules."""
    # Hull size restrictions — from manifest-probed applicable_hull_sizes.
    # Empty set means probe-unknown: treat as applicable (consistent with
    # search_space.get_eligible_hullmods, which only rejects non-empty
    # disjoint sets).
    for mod_id in list(hullmods):
        spec = manifest.hullmods.get(mod_id)
        if spec is None:
            continue
        if spec.applicable_hull_sizes and hull.hull_size not in spec.applicable_hull_sizes:
            hullmods.discard(mod_id)

    # Pair incompatibilities — drop the cheaper side of each conflict
    # (greedy but deterministic under the id ordering). Iterate a snapshot
    # so concurrent mutation during the drop loop is safe.
    for a in list(hullmods):
        if a not in hullmods:
            continue
        a_spec = manifest.hullmods.get(a)
        if a_spec is None:
            continue
        for b in list(a_spec.incompatible_with):
            if b not in hullmods or b == a:
                continue
            cost_a = (game_data.hullmods[a].op_cost(hull.hull_size)
                      if a in game_data.hullmods else 0)
            cost_b = (game_data.hullmods[b].op_cost(hull.hull_size)
                      if b in game_data.hullmods else 0)
            hullmods.discard(b if cost_b <= cost_a else a)

    return hullmods


def _repair_logistics(
    hullmods: set[str], hull: ShipHull,
    game_data: GameData, manifest: GameManifest,
) -> set[str]:
    """Enforce max_logistics_hullmods cap from the manifest."""
    cap = manifest.constants.max_logistics_hullmods
    logistics = [(m, game_data.hullmods[m].op_cost(hull.hull_size))
                 for m in hullmods
                 if m in game_data.hullmods and game_data.hullmods[m].is_logistics]
    if len(logistics) <= cap:
        return hullmods
    logistics.sort(key=lambda x: x[1], reverse=True)
    to_remove = {m for m, _ in logistics[cap:]}
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
    manifest: GameManifest,
    vent_fraction: float = 0.5,
) -> Build:
    """Repair a build to satisfy all constraints.

    Boundary between optimizer-space and domain-space. `manifest` supplies
    game-rule source-of-truth — callers pass a manifest loaded once at
    orchestrator startup.
    """
    weapons = dict(build.weapon_assignments)
    hullmods = set(build.hullmods)

    hullmods = _repair_incompatibilities(hullmods, hull, game_data, manifest)
    hullmods = _repair_logistics(hullmods, hull, game_data, manifest)
    weapons, hullmods = _repair_op_budget(weapons, hullmods, hull, game_data)

    item_cost = 0
    for wid in weapons.values():
        if wid and wid in game_data.weapons:
            item_cost += game_data.weapons[wid].op_cost
    for mid in hullmods:
        if mid in game_data.hullmods:
            item_cost += game_data.hullmods[mid].op_cost(hull.hull_size)

    remaining = max(0, hull.ordnance_points - item_cost)
    vent_cap = manifest.constants.max_vents_per_ship
    cap_cap = manifest.constants.max_capacitors_per_ship
    vents = min(round(vent_fraction * remaining), vent_cap)
    caps = min(remaining - vents, cap_cap)
    if caps < 0:
        vents = min(remaining, vent_cap)
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
    manifest: GameManifest,
) -> tuple[bool, list[str]]:
    """Check if a build satisfies all constraints."""
    violations: list[str] = []

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
            allowed = SLOT_WEAPON_COMPATIBILITY.get(slot.slot_type, frozenset())
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

    # C3: Pair incompatibilities (manifest-probed)
    installed = build.hullmods
    for mod_id in installed:
        spec = manifest.hullmods.get(mod_id)
        if spec is None:
            continue
        for other in spec.incompatible_with:
            if other in installed and mod_id < other:
                violations.append(f"Incompatible hullmods: {mod_id} + {other}")

    # C4: Hull-size restrictions (manifest-probed applicable_hull_sizes)
    for mod_id in build.hullmods:
        spec = manifest.hullmods.get(mod_id)
        if spec is None:
            continue
        if spec.applicable_hull_sizes and hull.hull_size not in spec.applicable_hull_sizes:
            violations.append(
                f"Hullmod {mod_id} not allowed on {hull.hull_size.value}"
            )

    # C5: Logistics limit
    cap = manifest.constants.max_logistics_hullmods
    logistics_count = sum(
        1 for m in build.hullmods
        if m in game_data.hullmods and game_data.hullmods[m].is_logistics
    )
    if logistics_count > cap:
        violations.append(f"Too many logistics mods: {logistics_count} > {cap}")

    # C6: Vent/cap limits
    vent_cap = manifest.constants.max_vents_per_ship
    cap_cap = manifest.constants.max_capacitors_per_ship
    if build.flux_vents > vent_cap:
        violations.append(f"Vents exceed max: {build.flux_vents} > {vent_cap}")
    if build.flux_capacitors > cap_cap:
        violations.append(f"Caps exceed max: {build.flux_capacitors} > {cap_cap}")

    return (len(violations) == 0, violations)
