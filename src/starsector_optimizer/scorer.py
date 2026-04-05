"""Heuristic scorer — static build quality metrics."""

from __future__ import annotations

import math

from .hullmod_effects import compute_effective_stats, get_effective_weapon_range
from .models import (
    Build,
    DamageType,
    EffectiveStats,
    GameData,
    ShipHull,
    ScorerResult,
    Weapon,
)

DEFAULT_WEIGHTS = {
    "total_dps": 0.25,
    "flux_efficiency": 0.20,
    "flux_balance": 0.20,
    "effective_hp": 0.15,
    "range_coherence": 0.10,
    "damage_mix": 0.10,
}


def _get_equipped_weapons(build: Build, game_data: GameData) -> list[Weapon]:
    weapons = []
    for wid in build.weapon_assignments.values():
        if wid and wid in game_data.weapons:
            weapons.append(game_data.weapons[wid])
    return weapons


def _flux_balance_score(ratio: float) -> float:
    """Score flux balance ratio. Ideal 0.4-0.8, penalty above 1.0."""
    if ratio <= 0:
        return 1.0
    if ratio <= 0.8:
        return 1.0
    if ratio <= 1.0:
        return 1.0 - (ratio - 0.8) * 2.5  # linear drop 1.0 → 0.5
    return max(0.0, 0.5 - (ratio - 1.0) * 1.0)  # steep penalty above 1.0


def heuristic_score(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> ScorerResult:
    """Compute heuristic quality score for a build."""
    eff = compute_effective_stats(hull, build, game_data)
    weapons = _get_equipped_weapons(build, game_data)

    # --- DPS by damage type ---
    kinetic_dps = sum(w.sustained_dps for w in weapons if w.damage_type == DamageType.KINETIC)
    he_dps = sum(w.sustained_dps for w in weapons if w.damage_type == DamageType.HIGH_EXPLOSIVE)
    energy_dps = sum(w.sustained_dps for w in weapons if w.damage_type == DamageType.ENERGY)
    frag_dps = sum(w.sustained_dps for w in weapons if w.damage_type == DamageType.FRAGMENTATION)
    total_dps = kinetic_dps + he_dps + energy_dps + frag_dps

    # --- Flux balance ---
    total_weapon_flux = sum(w.sustained_flux for w in weapons)
    flux_balance = total_weapon_flux / eff.flux_dissipation if eff.flux_dissipation > 0 else 0.0

    # --- Flux efficiency ---
    flux_efficiency = total_dps / total_weapon_flux if total_weapon_flux > 0 else 0.0

    # --- Effective HP ---
    armor_ehp = eff.armor_rating * 5.0  # rough: armor × coverage factor
    shield_ehp = 0.0
    if eff.has_shields and eff.shield_efficiency > 0:
        shield_ehp = eff.flux_capacity / eff.shield_efficiency
    effective_hp = eff.hull_hitpoints + armor_ehp + shield_ehp

    # --- Range coherence (non-PD weapons only) ---
    non_pd_weapons = [w for w in weapons if not w.is_pd]
    if len(non_pd_weapons) >= 2:
        ranges = [get_effective_weapon_range(w, eff) for w in non_pd_weapons]
        mean_r = sum(ranges) / len(ranges)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in ranges) / len(ranges))
        range_coherence = max(0.0, 1.0 - std_r / mean_r) if mean_r > 0 else 0.0
    elif len(non_pd_weapons) == 1:
        range_coherence = 1.0
    else:
        range_coherence = 0.0

    # --- Engagement range (DPS-weighted mean) ---
    if total_dps > 0:
        engagement_range = sum(
            w.sustained_dps * get_effective_weapon_range(w, eff) for w in weapons
        ) / total_dps
    else:
        engagement_range = 0.0

    # --- Damage mix score ---
    if total_dps > 0:
        kin_frac = kinetic_dps / total_dps
        he_frac = he_dps / total_dps
        # Reward having both kinetic (shield pressure) and HE (armor cracking)
        damage_mix = 2.0 * min(kin_frac + energy_dps / total_dps, he_frac + frag_dps / total_dps)
        damage_mix = min(1.0, damage_mix)
    else:
        damage_mix = 0.0

    # --- OP efficiency ---
    from .repair import compute_op_cost
    op_used = compute_op_cost(build, hull, game_data)
    op_efficiency = (total_dps + effective_hp * 0.01) / max(op_used, 1)

    # --- Composite score ---
    # Normalize each metric to ~[0, 1] range
    norm_dps = min(1.0, total_dps / 1000.0) if total_dps > 0 else 0.0
    norm_flux_eff = min(1.0, flux_efficiency / 2.0) if flux_efficiency > 0 else 0.0
    norm_flux_bal = _flux_balance_score(flux_balance)
    norm_ehp = min(1.0, effective_hp / 50000.0)
    norm_range = range_coherence
    norm_mix = damage_mix

    composite = (
        DEFAULT_WEIGHTS["total_dps"] * norm_dps
        + DEFAULT_WEIGHTS["flux_efficiency"] * norm_flux_eff
        + DEFAULT_WEIGHTS["flux_balance"] * norm_flux_bal
        + DEFAULT_WEIGHTS["effective_hp"] * norm_ehp
        + DEFAULT_WEIGHTS["range_coherence"] * norm_range
        + DEFAULT_WEIGHTS["damage_mix"] * norm_mix
    )

    return ScorerResult(
        composite_score=composite,
        total_dps=total_dps,
        kinetic_dps=kinetic_dps,
        he_dps=he_dps,
        energy_dps=energy_dps,
        flux_balance=flux_balance,
        flux_efficiency=flux_efficiency,
        effective_hp=effective_hp,
        armor_ehp=armor_ehp,
        shield_ehp=shield_ehp,
        range_coherence=range_coherence,
        damage_mix=damage_mix,
        engagement_range=engagement_range,
        op_efficiency=op_efficiency,
        effective_stats=eff,
    )
