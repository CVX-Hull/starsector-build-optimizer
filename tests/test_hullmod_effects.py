"""Tests for hullmod effects registry and compute_effective_stats."""

import pytest

from starsector_optimizer.models import (
    Build,
    DamageType,
    EffectiveStats,
    GameData,
    HullMod,
    HullSize,
    ShieldType,
    ShipHull,
    SlotType,
    WeaponType,
    MAX_VENTS,
    FLUX_PER_CAPACITOR,
    DISSIPATION_PER_VENT,
    MAX_LOGISTICS_HULLMODS,
)
from starsector_optimizer.hullmod_effects import (
    HULLMOD_EFFECTS,
    INCOMPATIBLE_PAIRS,
    HULL_SIZE_RESTRICTIONS,
    SHIELD_DEPENDENT_MODS,
    SLOT_COMPATIBILITY,
    compute_effective_stats,
    get_effective_weapon_range,
    validate_registry,
)


# --- Helpers ---


def _hull(hull_size=HullSize.CRUISER, shield_type=ShieldType.FRONT, **kw):
    defaults = dict(
        id="test_hull", name="Test", hull_size=hull_size, designation="Cruiser",
        tech_manufacturer="", system_id="", fleet_pts=10, hitpoints=8000.0,
        armor_rating=1000.0, max_flux=11000.0, flux_dissipation=700.0,
        ordnance_points=155, fighter_bays=0, max_speed=60.0,
        shield_type=shield_type, shield_arc=270.0, shield_upkeep=0.4,
        shield_efficiency=0.8, phase_cost=0.0, phase_upkeep=0.0,
        peak_cr_sec=480.0, cr_loss_per_sec=0.25,
        weapon_slots=[], built_in_mods=[], built_in_weapons={},
        hints=[], tags=[],
    )
    defaults.update(kw)
    return ShipHull(**defaults)


def _build(hullmods=frozenset(), vents=0, caps=0):
    return Build(
        hull_id="test_hull",
        weapon_assignments={},
        hullmods=hullmods,
        flux_vents=vents,
        flux_capacitors=caps,
    )


def _game_data():
    return GameData(hulls={}, weapons={}, hullmods={})


# --- SLOT_COMPATIBILITY tests ---


class TestSlotCompatibility:
    def test_ballistic_only_ballistic(self):
        assert SLOT_COMPATIBILITY[SlotType.BALLISTIC] == {WeaponType.BALLISTIC}

    def test_energy_only_energy(self):
        assert SLOT_COMPATIBILITY[SlotType.ENERGY] == {WeaponType.ENERGY}

    def test_missile_only_missile(self):
        assert SLOT_COMPATIBILITY[SlotType.MISSILE] == {WeaponType.MISSILE}

    def test_hybrid_ballistic_and_energy(self):
        assert SLOT_COMPATIBILITY[SlotType.HYBRID] == {WeaponType.BALLISTIC, WeaponType.ENERGY}

    def test_composite_ballistic_and_missile(self):
        assert SLOT_COMPATIBILITY[SlotType.COMPOSITE] == {WeaponType.BALLISTIC, WeaponType.MISSILE}

    def test_synergy_energy_and_missile(self):
        assert SLOT_COMPATIBILITY[SlotType.SYNERGY] == {WeaponType.ENERGY, WeaponType.MISSILE}

    def test_universal_all_types(self):
        assert SLOT_COMPATIBILITY[SlotType.UNIVERSAL] == {
            WeaponType.BALLISTIC, WeaponType.ENERGY, WeaponType.MISSILE
        }

    def test_all_slot_types_covered(self):
        for st in SlotType:
            assert st in SLOT_COMPATIBILITY


# --- HULLMOD_EFFECTS registry tests ---


class TestHullModEffectsRegistry:
    def test_heavy_armor_exists(self):
        assert "heavyarmor" in HULLMOD_EFFECTS

    def test_hardened_shields_exists(self):
        assert "hardenedshieldemitter" in HULLMOD_EFFECTS

    def test_safety_overrides_exists(self):
        assert "safetyoverrides" in HULLMOD_EFFECTS

    def test_shield_shunt_exists(self):
        assert "shield_shunt" in HULLMOD_EFFECTS

    def test_reinforced_bulkheads_exists(self):
        assert "reinforcedhull" in HULLMOD_EFFECTS

    def test_stabilized_shields_exists(self):
        assert "stabilizedshieldemitter" in HULLMOD_EFFECTS

    def test_targeting_unit_exists(self):
        assert "targetingunit" in HULLMOD_EFFECTS

    def test_expanded_magazines_exists(self):
        assert "magazines" in HULLMOD_EFFECTS


# --- compute_effective_stats tests ---


class TestComputeEffectiveStatsNoMods:
    def test_base_dissipation(self):
        hull = _hull(flux_dissipation=700.0)
        stats = compute_effective_stats(hull, _build(vents=10), _game_data())
        assert stats.flux_dissipation == 700.0 + 10 * DISSIPATION_PER_VENT

    def test_base_capacity(self):
        hull = _hull(max_flux=11000.0)
        stats = compute_effective_stats(hull, _build(caps=5), _game_data())
        assert stats.flux_capacity == 11000.0 + 5 * FLUX_PER_CAPACITOR

    def test_base_armor(self):
        hull = _hull(armor_rating=1000.0)
        stats = compute_effective_stats(hull, _build(), _game_data())
        assert stats.armor_rating == 1000.0

    def test_base_shields(self):
        hull = _hull(shield_type=ShieldType.FRONT, shield_efficiency=0.8)
        stats = compute_effective_stats(hull, _build(), _game_data())
        assert stats.has_shields is True
        assert stats.shield_efficiency == 0.8

    def test_no_shields(self):
        hull = _hull(shield_type=ShieldType.NONE)
        stats = compute_effective_stats(hull, _build(), _game_data())
        assert stats.has_shields is False

    def test_base_speed(self):
        hull = _hull(max_speed=60.0)
        stats = compute_effective_stats(hull, _build(), _game_data())
        assert stats.max_speed == 60.0

    def test_no_range_bonus(self):
        stats = compute_effective_stats(_hull(), _build(), _game_data())
        assert stats.weapon_range_bonus == 0.0
        assert stats.weapon_range_cap is None


class TestComputeEffectiveStatsWithMods:
    def test_heavy_armor_cruiser(self):
        hull = _hull(hull_size=HullSize.CRUISER, armor_rating=1000.0)
        build = _build(hullmods=frozenset(["heavyarmor"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert stats.armor_rating == 1000.0 + 400  # +400 for cruiser

    def test_heavy_armor_frigate(self):
        hull = _hull(hull_size=HullSize.FRIGATE, armor_rating=200.0)
        build = _build(hullmods=frozenset(["heavyarmor"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert stats.armor_rating == 200.0 + 150

    def test_heavy_armor_capital(self):
        hull = _hull(hull_size=HullSize.CAPITAL_SHIP, armor_rating=1500.0)
        build = _build(hullmods=frozenset(["heavyarmor"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert stats.armor_rating == 1500.0 + 500

    def test_hardened_shields(self):
        hull = _hull(shield_efficiency=0.8)
        build = _build(hullmods=frozenset(["hardenedshieldemitter"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert pytest.approx(stats.shield_efficiency) == 0.8 * 0.80

    def test_safety_overrides_dissipation(self):
        hull = _hull(flux_dissipation=700.0)
        build = _build(hullmods=frozenset(["safetyoverrides"]), vents=10)
        stats = compute_effective_stats(hull, build, _game_data())
        expected = (700.0 + 10 * DISSIPATION_PER_VENT) * 2.0
        assert pytest.approx(stats.flux_dissipation) == expected

    def test_safety_overrides_range_cap(self):
        build = _build(hullmods=frozenset(["safetyoverrides"]))
        stats = compute_effective_stats(_hull(), build, _game_data())
        assert stats.weapon_range_cap is not None
        assert stats.weapon_range_cap == pytest.approx(450.0, abs=50)

    def test_safety_overrides_ppt(self):
        hull = _hull(peak_cr_sec=480.0)
        build = _build(hullmods=frozenset(["safetyoverrides"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert stats.peak_performance_time < 480.0
        assert stats.peak_performance_time == pytest.approx(480.0 / 3, rel=0.05)

    def test_shield_shunt_removes_shields(self):
        hull = _hull(shield_type=ShieldType.FRONT)
        build = _build(hullmods=frozenset(["shield_shunt"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert stats.has_shields is False

    def test_shield_shunt_armor_bonus(self):
        hull = _hull(armor_rating=1000.0)
        build = _build(hullmods=frozenset(["shield_shunt"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert pytest.approx(stats.armor_rating) == 1000.0 * 1.15

    def test_reinforced_bulkheads(self):
        hull = _hull(hitpoints=8000.0)
        build = _build(hullmods=frozenset(["reinforcedhull"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert pytest.approx(stats.hull_hitpoints) == 8000.0 * 1.40

    def test_stabilized_shields(self):
        hull = _hull(shield_upkeep=0.4)
        build = _build(hullmods=frozenset(["stabilizedshieldemitter"]))
        stats = compute_effective_stats(hull, build, _game_data())
        assert pytest.approx(stats.shield_upkeep) == 0.4 * 0.50

    def test_targeting_unit_range_bonus(self):
        build = _build(hullmods=frozenset(["targetingunit"]))
        stats = compute_effective_stats(_hull(), build, _game_data())
        assert stats.weapon_range_bonus == 200.0

    def test_multiple_mods_stack(self):
        """Heavy Armor + Shield Shunt = flat bonus + 15% mult."""
        hull = _hull(hull_size=HullSize.CRUISER, armor_rating=1000.0)
        build = _build(hullmods=frozenset(["heavyarmor", "shield_shunt"]))
        stats = compute_effective_stats(hull, build, _game_data())
        # Heavy Armor adds 400 flat, Shield Shunt multiplies by 1.15
        # Order: flat bonus first, then mult
        expected = (1000.0 + 400) * 1.15
        assert pytest.approx(stats.armor_rating, rel=0.01) == expected


# --- get_effective_weapon_range tests ---


class TestGetEffectiveWeaponRange:
    def test_no_bonus(self):
        from starsector_optimizer.models import Weapon, SlotSize, WeaponType, DamageType
        w = Weapon("test", "Test", SlotSize.MEDIUM, WeaponType.BALLISTIC,
                   200, 0, DamageType.KINETIC, 0, 200, 0, 700, 10,
                   0, 0.75, 1, 0, 0, 0, 500, 30, [], [])
        stats = EffectiveStats(700, 11000, 1000, 8000, 0.8, 0.4,
                               True, 60, 0.0, None, 480)
        assert get_effective_weapon_range(w, stats) == 700.0

    def test_with_itu(self):
        from starsector_optimizer.models import Weapon, SlotSize, WeaponType, DamageType
        w = Weapon("test", "Test", SlotSize.MEDIUM, WeaponType.BALLISTIC,
                   200, 0, DamageType.KINETIC, 0, 200, 0, 700, 10,
                   0, 0.75, 1, 0, 0, 0, 500, 30, [], [])
        stats = EffectiveStats(700, 11000, 1000, 8000, 0.8, 0.4,
                               True, 60, 200.0, None, 480)
        assert get_effective_weapon_range(w, stats) == 900.0

    def test_with_so_cap(self):
        from starsector_optimizer.models import Weapon, SlotSize, WeaponType, DamageType
        w = Weapon("test", "Test", SlotSize.MEDIUM, WeaponType.BALLISTIC,
                   200, 0, DamageType.KINETIC, 0, 200, 0, 700, 10,
                   0, 0.75, 1, 0, 0, 0, 500, 30, [], [])
        stats = EffectiveStats(700, 11000, 1000, 8000, 0.8, 0.4,
                               True, 60, 0.0, 450.0, 480)
        assert get_effective_weapon_range(w, stats) <= 450.0


# --- Constraint constants tests ---


class TestConstraintConstants:
    def test_incompatible_pairs_contains_shield_shunt_makeshift(self):
        pairs_flat = [(a, b) for a, b in INCOMPATIBLE_PAIRS]
        assert any(
            ("shield_shunt" in (a, b) and "frontshield" in (a, b))
            for a, b in pairs_flat
        )

    def test_incompatible_pairs_contains_shield_conversions(self):
        pairs_flat = [(a, b) for a, b in INCOMPATIBLE_PAIRS]
        assert any(
            ("frontemitter" in (a, b) and "adaptiveshields" in (a, b))
            for a, b in pairs_flat
        )

    def test_incompatible_pairs_contains_so_fluxshunt(self):
        pairs_flat = [(a, b) for a, b in INCOMPATIBLE_PAIRS]
        assert any(
            ("safetyoverrides" in (a, b) and "fluxshunt" in (a, b))
            for a, b in pairs_flat
        )

    def test_so_restricted_from_capital(self):
        allowed = HULL_SIZE_RESTRICTIONS["safetyoverrides"]
        assert HullSize.CAPITAL_SHIP not in allowed
        assert HullSize.FRIGATE in allowed
        assert HullSize.CRUISER in allowed

    def test_shield_dependent_mods(self):
        assert "hardenedshieldemitter" in SHIELD_DEPENDENT_MODS
        assert "stabilizedshieldemitter" in SHIELD_DEPENDENT_MODS


# --- validate_registry tests ---


class TestValidateRegistry:
    def test_valid_registry(self):
        """With all hullmod IDs present, no warnings."""
        hullmods = {}
        # Create minimal HullMod for every ID referenced in the registry
        all_ids = set(HULLMOD_EFFECTS.keys())
        for a, b in INCOMPATIBLE_PAIRS:
            all_ids.add(a)
            all_ids.add(b)
        for ids in HULL_SIZE_RESTRICTIONS.values():
            pass  # keys are hullmod IDs, already in HULLMOD_EFFECTS
        all_ids.update(HULL_SIZE_RESTRICTIONS.keys())
        all_ids.update(SHIELD_DEPENDENT_MODS)

        for hid in all_ids:
            hullmods[hid] = HullMod(hid, hid, 0, [], [], 0, 0, 0, 0, False, "")
        gd = GameData(hulls={}, weapons={}, hullmods=hullmods)
        warnings = validate_registry(gd)
        assert len(warnings) == 0

    def test_missing_id_produces_warning(self):
        """Empty game data should produce warnings for all registry IDs."""
        gd = GameData(hulls={}, weapons={}, hullmods={})
        warnings = validate_registry(gd)
        assert len(warnings) > 0


# --- Game constants tests ---


class TestGameConstants:
    def test_max_vents(self):
        assert MAX_VENTS["FRIGATE"] == 10
        assert MAX_VENTS["DESTROYER"] == 20
        assert MAX_VENTS["CRUISER"] == 30
        assert MAX_VENTS["CAPITAL_SHIP"] == 50

    def test_flux_per_capacitor(self):
        assert FLUX_PER_CAPACITOR == 200

    def test_dissipation_per_vent(self):
        assert DISSIPATION_PER_VENT == 10

    def test_max_logistics(self):
        assert MAX_LOGISTICS_HULLMODS == 2
