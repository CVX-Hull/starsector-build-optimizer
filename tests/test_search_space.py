"""Tests for search space builder."""

import pytest

from starsector_optimizer.models import (
    HullSize, SlotType, SlotSize, MountType, ShieldType, WeaponType,
    WeaponSlot, ShipHull, Weapon, HullMod, DamageType, GameData,
)
from starsector_optimizer.search_space import (
    get_compatible_weapons,
    get_eligible_hullmods,
    build_search_space,
    SearchSpace,
)


# --- Helpers ---

def _slot(slot_type=SlotType.BALLISTIC, slot_size=SlotSize.MEDIUM):
    return WeaponSlot("WS 001", slot_type, slot_size, MountType.TURRET, 0, 150, (0, 0))


def _weapon(wid="w1", weapon_type=WeaponType.BALLISTIC, size=SlotSize.MEDIUM):
    return Weapon(wid, wid, size, weapon_type, 100, 0, DamageType.KINETIC, 0,
                  100, 0, 700, 10, 0, 0.5, 1, 0, 0, 0, 500, 30, [], [])


def _hullmod(mid="mod1", is_hidden=False):
    return HullMod(mid, mid, 0, [], [], 5, 10, 15, 20, is_hidden, "")


def _hull(**kw):
    defaults = dict(
        id="test", name="Test", hull_size=HullSize.CRUISER, designation="Cruiser",
        tech_manufacturer="", system_id="", fleet_pts=10, hitpoints=5000,
        armor_rating=500, max_flux=5000, flux_dissipation=300, ordnance_points=100,
        fighter_bays=0, max_speed=60, shield_type=ShieldType.FRONT, shield_arc=270,
        shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.25, weapon_slots=[], built_in_mods=[],
        built_in_weapons={}, hints=[], tags=[],
    )
    defaults.update(kw)
    return ShipHull(**defaults)


# --- get_compatible_weapons tests ---


class TestGetCompatibleWeapons:
    def test_ballistic_slot_only_ballistic(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.MEDIUM),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.MEDIUM),
            "m1": _weapon("m1", WeaponType.MISSILE, SlotSize.MEDIUM),
        }
        result = get_compatible_weapons(_slot(SlotType.BALLISTIC, SlotSize.MEDIUM), weapons)
        assert {w.id for w in result} == {"b1"}

    def test_energy_slot_only_energy(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.SMALL),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.SMALL),
        }
        result = get_compatible_weapons(_slot(SlotType.ENERGY, SlotSize.SMALL), weapons)
        assert {w.id for w in result} == {"e1"}

    def test_hybrid_ballistic_and_energy(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.MEDIUM),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.MEDIUM),
            "m1": _weapon("m1", WeaponType.MISSILE, SlotSize.MEDIUM),
        }
        result = get_compatible_weapons(_slot(SlotType.HYBRID, SlotSize.MEDIUM), weapons)
        assert {w.id for w in result} == {"b1", "e1"}

    def test_universal_all_types(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.LARGE),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.LARGE),
            "m1": _weapon("m1", WeaponType.MISSILE, SlotSize.LARGE),
        }
        result = get_compatible_weapons(_slot(SlotType.UNIVERSAL, SlotSize.LARGE), weapons)
        assert {w.id for w in result} == {"b1", "e1", "m1"}

    def test_size_must_match(self):
        weapons = {
            "s": _weapon("s", WeaponType.BALLISTIC, SlotSize.SMALL),
            "m": _weapon("m", WeaponType.BALLISTIC, SlotSize.MEDIUM),
            "l": _weapon("l", WeaponType.BALLISTIC, SlotSize.LARGE),
        }
        result = get_compatible_weapons(_slot(SlotType.BALLISTIC, SlotSize.SMALL), weapons)
        assert {w.id for w in result} == {"s"}

    def test_composite_ballistic_and_missile(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.MEDIUM),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.MEDIUM),
            "m1": _weapon("m1", WeaponType.MISSILE, SlotSize.MEDIUM),
        }
        result = get_compatible_weapons(_slot(SlotType.COMPOSITE, SlotSize.MEDIUM), weapons)
        assert {w.id for w in result} == {"b1", "m1"}

    def test_synergy_energy_and_missile(self):
        weapons = {
            "b1": _weapon("b1", WeaponType.BALLISTIC, SlotSize.MEDIUM),
            "e1": _weapon("e1", WeaponType.ENERGY, SlotSize.MEDIUM),
            "m1": _weapon("m1", WeaponType.MISSILE, SlotSize.MEDIUM),
        }
        result = get_compatible_weapons(_slot(SlotType.SYNERGY, SlotSize.MEDIUM), weapons)
        assert {w.id for w in result} == {"e1", "m1"}


# --- get_eligible_hullmods tests ---


class TestGetEligibleHullmods:
    def test_excludes_hidden(self):
        mods = {"m1": _hullmod("m1", is_hidden=False), "m2": _hullmod("m2", is_hidden=True)}
        hull = _hull()
        result = get_eligible_hullmods(hull, mods)
        assert {m.id for m in result} == {"m1"}

    def test_excludes_builtin(self):
        mods = {"m1": _hullmod("m1"), "m2": _hullmod("m2")}
        hull = _hull(built_in_mods=["m2"])
        result = get_eligible_hullmods(hull, mods)
        assert {m.id for m in result} == {"m1"}


# --- build_search_space tests ---


class TestBuildSearchSpace:
    def test_returns_search_space(self, game_data):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        assert isinstance(space, SearchSpace)
        assert space.hull_id == "eagle"

    def test_eagle_has_weapon_options(self, game_data):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        assert len(space.weapon_options) > 0

    def test_each_slot_starts_with_empty(self, game_data):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        for slot_id, options in space.weapon_options.items():
            assert options[0] == "empty", f"Slot {slot_id} doesn't start with 'empty'"

    def test_slot_options_are_compatible(self, game_data):
        """Every weapon in a slot's options must be compatible with that slot."""
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        slot_map = {s.id: s for s in eagle.weapon_slots}
        from starsector_optimizer.hullmod_effects import SLOT_COMPATIBILITY
        for slot_id, options in space.weapon_options.items():
            slot = slot_map[slot_id]
            allowed = SLOT_COMPATIBILITY[slot.slot_type]
            for wid in options:
                if wid == "empty":
                    continue
                weapon = game_data.weapons[wid]
                assert weapon.weapon_type in allowed, (
                    f"Weapon {wid} type {weapon.weapon_type} not in {allowed} for slot {slot_id}"
                )
                assert weapon.size == slot.slot_size

    def test_builtin_weapon_slots_excluded(self, game_data):
        """Slots with built-in weapons should not appear in weapon_options."""
        for hull in game_data.hulls.values():
            if not hull.built_in_weapons:
                continue
            space = build_search_space(hull, game_data)
            for slot_id in hull.built_in_weapons:
                assert slot_id not in space.weapon_options
            break  # just test one hull with built-in weapons

    def test_has_eligible_hullmods(self, game_data):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        assert len(space.eligible_hullmods) > 10

    def test_has_max_vents_caps(self, game_data):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data)
        assert space.max_vents == 30  # CRUISER
        assert space.max_capacitors == 30
