"""Per-hull search space builder."""

from __future__ import annotations

from dataclasses import dataclass, field

from .hullmod_effects import INCOMPATIBLE_PAIRS, SLOT_COMPATIBILITY
from .models import (
    GameData,
    HullMod,
    ShipHull,
    Weapon,
    WeaponSlot,
)


@dataclass
class SearchSpace:
    hull_id: str
    weapon_options: dict[str, list[str]]  # slot_id → ["empty", weapon_id, ...]
    eligible_hullmods: list[str]
    max_vents: int
    max_capacitors: int
    incompatible_pairs: list[tuple[str, str]] = field(default_factory=list)


def get_compatible_weapons(
    slot: WeaponSlot,
    weapons: dict[str, Weapon],
) -> list[Weapon]:
    """Get weapons compatible with a slot (matching type and size)."""
    allowed_types = SLOT_COMPATIBILITY.get(slot.slot_type, set())
    return sorted(
        [w for w in weapons.values()
         if w.weapon_type in allowed_types and w.size == slot.slot_size],
        key=lambda w: w.id,
    )


def get_eligible_hullmods(
    hull: ShipHull,
    hullmods: dict[str, HullMod],
) -> list[HullMod]:
    """Get hullmods eligible for installation on a hull."""
    builtin = set(hull.built_in_mods)
    return sorted(
        [m for m in hullmods.values()
         if not m.is_hidden and m.id not in builtin],
        key=lambda m: m.id,
    )


def build_search_space(hull: ShipHull, game_data: GameData) -> SearchSpace:
    """Build the optimization search space for a given hull."""
    # Weapon options per slot (excluding built-in and non-assignable slots)
    weapon_options: dict[str, list[str]] = {}
    for slot in hull.weapon_slots:
        if slot.id in hull.built_in_weapons:
            continue
        if slot.slot_type not in SLOT_COMPATIBILITY:
            continue  # SYSTEM, BUILT_IN, DECORATIVE, LAUNCH_BAY, etc.
        compatible = get_compatible_weapons(slot, game_data.weapons)
        weapon_options[slot.id] = ["empty"] + [w.id for w in compatible]

    # Eligible hullmods
    eligible = get_eligible_hullmods(hull, game_data.hullmods)

    # Filter incompatible pairs to only include eligible hullmod ids
    eligible_ids = {m.id for m in eligible}
    pairs = [
        (a, b) for a, b in INCOMPATIBLE_PAIRS
        if a in eligible_ids and b in eligible_ids
    ]

    return SearchSpace(
        hull_id=hull.id,
        weapon_options=weapon_options,
        eligible_hullmods=[m.id for m in eligible],
        max_vents=hull.max_vents,
        max_capacitors=hull.max_capacitors,
        incompatible_pairs=pairs,
    )
