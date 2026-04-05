"""Hullmod effects registry and effective stats computation.

Single source of truth for all hardcoded game knowledge about hullmod stat
modifications and constraints. All other modules import from here — never
duplicate hullmod logic elsewhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    Build,
    DamageType,
    EffectiveStats,
    GameData,
    HullSize,
    ShipHull,
    SlotType,
    Weapon,
    WeaponType,
    DISSIPATION_PER_VENT,
    FLUX_PER_CAPACITOR,
)

logger = logging.getLogger(__name__)


# --- Slot compatibility ---

SLOT_COMPATIBILITY: dict[SlotType, set[WeaponType]] = {
    SlotType.BALLISTIC: {WeaponType.BALLISTIC},
    SlotType.ENERGY:    {WeaponType.ENERGY},
    SlotType.MISSILE:   {WeaponType.MISSILE},
    SlotType.HYBRID:    {WeaponType.BALLISTIC, WeaponType.ENERGY},
    SlotType.COMPOSITE: {WeaponType.BALLISTIC, WeaponType.MISSILE},
    SlotType.SYNERGY:   {WeaponType.ENERGY, WeaponType.MISSILE},
    SlotType.UNIVERSAL: {WeaponType.BALLISTIC, WeaponType.ENERGY, WeaponType.MISSILE},
}


# --- Hullmod effect registry ---

@dataclass(frozen=True)
class HullModEffect:
    """Declarative description of a hullmod's stat modifications."""
    armor_flat_bonus: dict[HullSize, float] = field(default_factory=dict)
    shield_efficiency_mult: float = 1.0
    dissipation_mult: float = 1.0
    speed_bonus: dict[HullSize, float] = field(default_factory=dict)
    range_bonus: float = 0.0
    range_threshold: float | None = None   # SO: ranges above this are compressed
    range_compression: float = 1.0         # SO: multiplier for range above threshold (0.25 = 75% reduction)
    hull_hp_mult: float = 1.0
    armor_mult: float = 1.0
    removes_shields: bool = False
    ppt_mult: float = 1.0
    missile_ammo_mult: float = 1.0
    shield_upkeep_mult: float = 1.0
    custom_effects: dict[str, Any] = field(default_factory=dict)


HULLMOD_EFFECTS: dict[str, HullModEffect] = {
    "heavyarmor": HullModEffect(
        armor_flat_bonus={
            HullSize.FRIGATE: 150,
            HullSize.DESTROYER: 300,
            HullSize.CRUISER: 400,
            HullSize.CAPITAL_SHIP: 500,
        },
    ),
    "hardenedshieldemitter": HullModEffect(
        shield_efficiency_mult=0.80,
    ),
    "safetyoverrides": HullModEffect(
        dissipation_mult=2.0,
        speed_bonus={
            HullSize.FRIGATE: 50,
            HullSize.DESTROYER: 30,
            HullSize.CRUISER: 20,
        },
        range_threshold=450.0,
        range_compression=0.25,  # (range - 450) * 0.25 + 450
        ppt_mult=1.0 / 3.0,
    ),
    "shield_shunt": HullModEffect(
        removes_shields=True,
        armor_mult=1.15,
    ),
    "reinforcedhull": HullModEffect(
        hull_hp_mult=1.40,
    ),
    "stabilizedshieldemitter": HullModEffect(
        shield_upkeep_mult=0.50,
    ),
    "targetingunit": HullModEffect(
        range_bonus=200.0,
    ),
    "magazines": HullModEffect(
        missile_ammo_mult=2.0,
    ),
}


# --- Constraint constants ---

INCOMPATIBLE_PAIRS: list[tuple[str, str]] = [
    ("shield_shunt", "frontshield"),
    ("frontemitter", "adaptiveshields"),
    ("safetyoverrides", "fluxshunt"),
]

HULL_SIZE_RESTRICTIONS: dict[str, set[HullSize]] = {
    "safetyoverrides": {HullSize.FRIGATE, HullSize.DESTROYER, HullSize.CRUISER},
}

SHIELD_DEPENDENT_MODS: set[str] = {
    "hardenedshieldemitter",
    "stabilizedshieldemitter",
    "adaptiveshields",
    "frontemitter",
    "extendedshieldemitter",
}


# --- Functions ---

def compute_effective_stats(
    hull: ShipHull,
    build: Build,
    game_data: GameData,
) -> EffectiveStats:
    """Compute effective ship stats after applying all hullmod modifications.

    This is the ONLY function that applies hullmod stat modifications.
    """
    # Start with base stats
    dissipation = hull.flux_dissipation + build.flux_vents * DISSIPATION_PER_VENT
    capacity = hull.max_flux + build.flux_capacitors * FLUX_PER_CAPACITOR
    armor = hull.armor_rating
    hp = hull.hitpoints
    shield_eff = hull.shield_efficiency
    shield_up = hull.shield_upkeep
    has_shields = hull.shield_type.value not in ("NONE", "PHASE")
    speed = hull.max_speed
    range_bonus = 0.0
    range_threshold: float | None = None
    range_compression: float = 1.0
    ppt = hull.peak_cr_sec

    # Collect effects from installed hullmods
    for mod_id in build.hullmods:
        effect = HULLMOD_EFFECTS.get(mod_id)
        if effect is None:
            continue

        # Flat armor bonus (additive, applied before multipliers)
        if hull.hull_size in effect.armor_flat_bonus:
            armor += effect.armor_flat_bonus[hull.hull_size]

    # Second pass for multipliers (applied after flat bonuses)
    for mod_id in build.hullmods:
        effect = HULLMOD_EFFECTS.get(mod_id)
        if effect is None:
            continue

        # Multiplicative effects
        if effect.armor_mult != 1.0:
            armor *= effect.armor_mult
        if effect.hull_hp_mult != 1.0:
            hp *= effect.hull_hp_mult
        if effect.shield_efficiency_mult != 1.0:
            shield_eff *= effect.shield_efficiency_mult
        if effect.shield_upkeep_mult != 1.0:
            shield_up *= effect.shield_upkeep_mult
        if effect.dissipation_mult != 1.0:
            dissipation *= effect.dissipation_mult
        if effect.ppt_mult != 1.0:
            ppt *= effect.ppt_mult
        if effect.removes_shields:
            has_shields = False
        if hull.hull_size in effect.speed_bonus:
            speed += effect.speed_bonus[hull.hull_size]
        if effect.range_bonus != 0.0:
            range_bonus += effect.range_bonus
        if effect.range_threshold is not None:
            range_threshold = effect.range_threshold
            range_compression = effect.range_compression

    return EffectiveStats(
        flux_dissipation=dissipation,
        flux_capacity=capacity,
        armor_rating=armor,
        hull_hitpoints=hp,
        shield_efficiency=shield_eff if has_shields else 0.0,
        shield_upkeep=shield_up if has_shields else 0.0,
        has_shields=has_shields,
        max_speed=speed,
        weapon_range_bonus=range_bonus,
        weapon_range_threshold=range_threshold,
        weapon_range_compression=range_compression,
        peak_performance_time=ppt,
    )


def get_effective_weapon_range(weapon: Weapon, effective_stats: EffectiveStats) -> float:
    """Get weapon's effective range after hullmod modifications.

    Safety Overrides compresses ranges above threshold:
    effective = (base - threshold) * compression + threshold
    E.g., 700 range with SO: (700 - 450) * 0.25 + 450 = 512.5
    """
    r = weapon.range + effective_stats.weapon_range_bonus
    if effective_stats.weapon_range_threshold is not None and r > effective_stats.weapon_range_threshold:
        excess = r - effective_stats.weapon_range_threshold
        r = effective_stats.weapon_range_threshold + excess * effective_stats.weapon_range_compression
    return r


def validate_registry(game_data: GameData) -> list[str]:
    """Check that all hullmod IDs in the registry exist in game data.

    Returns list of warning messages for mismatches.
    """
    warnings = []
    all_ids: set[str] = set()

    all_ids.update(HULLMOD_EFFECTS.keys())
    for a, b in INCOMPATIBLE_PAIRS:
        all_ids.add(a)
        all_ids.add(b)
    all_ids.update(HULL_SIZE_RESTRICTIONS.keys())
    all_ids.update(SHIELD_DEPENDENT_MODS)

    for hid in sorted(all_ids):
        if hid not in game_data.hullmods:
            warnings.append(f"Hullmod ID '{hid}' in registry not found in game data")

    return warnings
