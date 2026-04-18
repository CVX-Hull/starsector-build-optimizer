"""Per-hull search space builder."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .hullmod_effects import INCOMPATIBLE_PAIRS, SLOT_COMPATIBILITY
from .models import (
    GameData,
    HullMod,
    RegimeConfig,
    ShipHull,
    Weapon,
    WeaponSlot,
)

logger = logging.getLogger(__name__)


@dataclass
class SearchSpace:
    hull_id: str
    weapon_options: dict[str, list[str]]  # slot_id → ["empty", weapon_id, ...]
    eligible_hullmods: list[str]
    max_vents: int
    max_capacitors: int
    incompatible_pairs: list[tuple[str, str]] = field(default_factory=list)


def _is_assignable_weapon(w: Weapon) -> bool:
    """Filter out system weapons, fighter-only weapons, and restricted weapons."""
    if "SYSTEM" in w.hints:
        return False
    if "restricted" in w.tags:
        return False
    if w.op_cost <= 0 and not w.is_beam:
        # 0-OP non-beam weapons are typically system payloads (e.g., gorgon_payload)
        return False
    return True


def _regime_admits_hullmod(hm: HullMod, regime: RegimeConfig) -> bool:
    """Phase 5F mask: admit iff tier is within ceiling AND no excluded tag is present."""
    if hm.tier > regime.max_hullmod_tier:
        return False
    return regime.exclude_hullmod_tags.isdisjoint(hm.tags)


def _regime_admits_weapon(w: Weapon, regime: RegimeConfig) -> bool:
    """Phase 5F mask: admit iff no excluded blueprint/availability tag is present."""
    return regime.exclude_weapon_tags.isdisjoint(w.tags)


def get_compatible_weapons(
    slot: WeaponSlot,
    weapons: dict[str, Weapon],
) -> list[Weapon]:
    """Get weapons compatible with a slot (matching type, size, and assignability)."""
    allowed_types = SLOT_COMPATIBILITY.get(slot.slot_type, set())
    return sorted(
        [w for w in weapons.values()
         if w.weapon_type in allowed_types and w.size == slot.slot_size
         and _is_assignable_weapon(w)],
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


def build_search_space(
    hull: ShipHull,
    game_data: GameData,
    regime: RegimeConfig,
) -> SearchSpace:
    """Build the optimization search space for a given hull under the given loadout regime.

    `regime` is a mandatory positional argument — Phase 5F treats the mask as
    a load-bearing boundary between the global component catalogue and the
    per-run feasible set; a silent default would let the mask drift out of
    sync with the Optuna study's per-(hull, regime) identity. Pass
    `REGIME_ENDGAME` to preserve pre-5F unfiltered behaviour.
    """
    # Weapon options per slot (excluding built-in and non-assignable slots)
    weapon_options: dict[str, list[str]] = {}
    total_weapons_admitted = 0
    total_weapons_considered = 0
    for slot in hull.weapon_slots:
        if slot.id in hull.built_in_weapons:
            continue
        if slot.slot_type not in SLOT_COMPATIBILITY:
            continue  # SYSTEM, BUILT_IN, DECORATIVE, LAUNCH_BAY, etc.
        compatible = get_compatible_weapons(slot, game_data.weapons)
        total_weapons_considered += len(compatible)
        admitted = [w for w in compatible if _regime_admits_weapon(w, regime)]
        total_weapons_admitted += len(admitted)
        weapon_options[slot.id] = ["empty"] + [w.id for w in admitted]

    # Eligible hullmods, then regime mask
    eligible_pre = get_eligible_hullmods(hull, game_data.hullmods)
    eligible = [m for m in eligible_pre if _regime_admits_hullmod(m, regime)]

    # Filter incompatible pairs to only include eligible hullmod ids
    eligible_ids = {m.id for m in eligible}
    pairs = [
        (a, b) for a, b in INCOMPATIBLE_PAIRS
        if a in eligible_ids and b in eligible_ids
    ]

    logger.info(
        "Regime '%s' admits %d/%d hullmods, %d/%d weapons for hull=%s",
        regime.name,
        len(eligible), len(eligible_pre),
        total_weapons_admitted, total_weapons_considered,
        hull.id,
    )

    return SearchSpace(
        hull_id=hull.id,
        weapon_options=weapon_options,
        eligible_hullmods=[m.id for m in eligible],
        max_vents=hull.max_vents,
        max_capacitors=hull.max_capacitors,
        incompatible_pairs=pairs,
    )
