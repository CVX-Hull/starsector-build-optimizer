"""Tests for search space builder."""

import pytest

from starsector_optimizer.models import (
    HullSize, SlotType, SlotSize, MountType, ShieldType, WeaponType,
    WeaponSlot, ShipHull, Weapon, HullMod, DamageType, GameData,
    REGIME_EARLY, REGIME_MID, REGIME_LATE, REGIME_ENDGAME, RegimeConfig,
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
    def test_excludes_hidden(self, manifest):
        """Schema v2: hidden filter runs on top of manifest applicable set."""
        from tests.conftest import attach_synthetic_hull
        mods = {"m1": _hullmod("m1", is_hidden=False), "m2": _hullmod("m2", is_hidden=True)}
        hull = _hull()
        # Synthetic hull claims both mods are applicable; the is_hidden
        # filter in get_eligible_hullmods excludes m2.
        m = attach_synthetic_hull(manifest, hull.id, ["m1", "m2"])
        result = get_eligible_hullmods(hull, mods, m)
        assert {mod.id for mod in result} == {"m1"}

    def test_only_manifest_applicable_returned(self, manifest):
        """Schema v2: built-in exclusion is an engine-probe concern. The
        manifest's applicable_hullmods set is authoritative; what isn't
        in that set doesn't come out of get_eligible_hullmods."""
        from tests.conftest import attach_synthetic_hull
        mods = {"m1": _hullmod("m1"), "m2": _hullmod("m2")}
        hull = _hull(built_in_mods=["m2"])
        # Probe output: m1 applicable, m2 NOT (because engine found it
        # already installed via built-in → isApplicableToShip returns false).
        m = attach_synthetic_hull(manifest, hull.id, ["m1"])
        result = get_eligible_hullmods(hull, mods, m)
        assert {mod.id for mod in result} == {"m1"}


# --- build_search_space tests ---


class TestBuildSearchSpace:
    def test_returns_search_space(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        assert isinstance(space, SearchSpace)
        assert space.hull_id == "eagle"

    def test_eagle_has_weapon_options(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        assert len(space.weapon_options) > 0

    def test_each_slot_starts_with_empty(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        for slot_id, options in space.weapon_options.items():
            assert options[0] == "empty", f"Slot {slot_id} doesn't start with 'empty'"

    def test_slot_options_are_compatible(self, game_data, manifest):
        """Every weapon in a slot's options must be compatible with that slot."""
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        slot_map = {s.id: s for s in eagle.weapon_slots}
        from starsector_optimizer.game_manifest import (
            SLOT_WEAPON_COMPATIBILITY as SLOT_COMPATIBILITY,
        )
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

    def test_builtin_weapon_slots_excluded(self, game_data, manifest):
        """Slots with built-in weapons should not appear in weapon_options."""
        for hull in game_data.hulls.values():
            if not hull.built_in_weapons:
                continue
            space = build_search_space(hull, game_data, REGIME_ENDGAME, manifest)
            for slot_id in hull.built_in_weapons:
                assert slot_id not in space.weapon_options
            break  # just test one hull with built-in weapons

    def test_has_eligible_hullmods(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        assert len(space.eligible_hullmods) > 10

    def test_has_max_vents_caps(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_ENDGAME, manifest)
        assert space.max_vents == 30  # CRUISER
        assert space.max_capacitors == 30


# --- Phase 5F: regime mask tests ---


def _fixture_game_data_with_tagged_components():
    """Minimal GameData with a tiered, tagged hullmod set and tagged weapons.

    Hullmods: mt0 (tier 0, clean), mt1 (tier 1, clean), mt3 (tier 3, clean),
              mt_nds (tier 2, tagged `no_drop_salvage`), mt_rare (tier 3, tagged `codex_unlockable`).
    Weapons: w_clean (no tags), w_rare (`rare_bp`), w_codex (`codex_unlockable`).
    """
    hull = _hull(
        id="testhull",
        weapon_slots=[
            WeaponSlot("WS001", SlotType.BALLISTIC, SlotSize.MEDIUM, MountType.TURRET, 0, 150, (0, 0)),
        ],
    )

    def _h(mid, tier, tags):
        return HullMod(mid, mid, tier, tags, [], 5, 10, 15, 20, False, "")

    hullmods = {
        "mt0": _h("mt0", 0, []),
        "mt1": _h("mt1", 1, []),
        "mt3": _h("mt3", 3, []),
        "mt_nds": _h("mt_nds", 2, ["no_drop_salvage"]),
        "mt_rare": _h("mt_rare", 3, ["codex_unlockable"]),
    }

    def _w(wid, tags):
        return Weapon(
            wid, wid, SlotSize.MEDIUM, WeaponType.BALLISTIC,
            100, 0, DamageType.KINETIC, 0, 100, 0, 700, 10, 0, 0.5, 1, 0, 0, 0,
            500, 30, [], tags,
        )

    weapons = {
        "w_clean": _w("w_clean", []),
        "w_rare": _w("w_rare", ["rare_bp"]),
        "w_codex": _w("w_codex", ["codex_unlockable"]),
    }

    return hull, GameData(hulls={"testhull": hull}, weapons=weapons, hullmods=hullmods)


def _fixture_with_manifest(manifest):
    """Returns (hull, game_data, manifest-with-testhull-entry). Schema v2:
    the synthetic `testhull` must have an applicable_hullmods entry in
    the manifest, or get_eligible_hullmods KeyErrors."""
    from tests.conftest import attach_synthetic_hull
    hull, gd = _fixture_game_data_with_tagged_components()
    # All 5 synthetic mods applicable to testhull — regime filters apply on
    # top via tags/tier, not via applicability.
    m = attach_synthetic_hull(manifest, "testhull",
                              ["mt0", "mt1", "mt3", "mt_nds", "mt_rare"])
    return hull, gd, m


class TestRegimeMask:
    def test_endgame_regime_admits_everything(self, manifest):
        hull, gd, m = _fixture_with_manifest(manifest)
        space = build_search_space(hull, gd, REGIME_ENDGAME, m)
        assert set(space.eligible_hullmods) == {"mt0", "mt1", "mt3", "mt_nds", "mt_rare"}
        # Weapon options include "empty" + all three weapons
        assert set(space.weapon_options["WS001"]) == {"empty", "w_clean", "w_rare", "w_codex"}

    def test_mid_regime_excludes_no_drop_tagged_hullmods(self, manifest):
        hull, gd, m = _fixture_with_manifest(manifest)
        mid = build_search_space(hull, gd, REGIME_MID, m)
        endgame = build_search_space(hull, gd, REGIME_ENDGAME, m)
        assert "mt_nds" in endgame.eligible_hullmods
        assert "mt_nds" not in mid.eligible_hullmods

    def test_early_regime_enforces_tier_ceiling(self, manifest):
        hull, gd, m = _fixture_with_manifest(manifest)
        early = build_search_space(hull, gd, REGIME_EARLY, m)
        late = build_search_space(hull, gd, REGIME_LATE, m)
        endgame = build_search_space(hull, gd, REGIME_ENDGAME, m)
        assert "mt3" not in early.eligible_hullmods
        assert "mt3" in late.eligible_hullmods
        assert "mt3" in endgame.eligible_hullmods
        assert "mt0" in early.eligible_hullmods
        assert "mt1" in early.eligible_hullmods

    def test_regime_excludes_rare_bp_weapons(self, manifest):
        hull, gd, m = _fixture_with_manifest(manifest)
        early = build_search_space(hull, gd, REGIME_EARLY, m)
        mid = build_search_space(hull, gd, REGIME_MID, m)
        late = build_search_space(hull, gd, REGIME_LATE, m)
        endgame = build_search_space(hull, gd, REGIME_ENDGAME, m)
        assert "w_rare" not in early.weapon_options["WS001"]
        assert "w_rare" not in mid.weapon_options["WS001"]
        assert "w_rare" in late.weapon_options["WS001"]
        assert "w_rare" in endgame.weapon_options["WS001"]

    def test_regime_mask_preserves_ordering(self, manifest):
        """Surviving items keep their original relative order (determinism)."""
        hull, gd, m = _fixture_with_manifest(manifest)
        endgame = build_search_space(hull, gd, REGIME_ENDGAME, m)
        mid = build_search_space(hull, gd, REGIME_MID, m)
        # Build the expected sub-sequence: the endgame order filtered to mid's admitted set.
        admitted = set(mid.eligible_hullmods)
        expected_sub = [h for h in endgame.eligible_hullmods if h in admitted]
        assert mid.eligible_hullmods == expected_sub

    def test_build_search_space_signature_has_regime(self, game_data, manifest):
        """Calling without regime must raise TypeError — no silent default."""
        eagle = game_data.hulls["eagle"]
        with pytest.raises(TypeError):
            build_search_space(eagle, game_data)  # type: ignore[call-arg]
        # explicit regime works
        space = build_search_space(eagle, game_data, REGIME_MID, manifest)
        assert isinstance(space, SearchSpace)

    def test_regime_does_not_filter_hulls(self, game_data, manifest):
        """Regime never vetoes the hull itself — hull is user-picked."""
        eagle = game_data.hulls["eagle"]
        space = build_search_space(eagle, game_data, REGIME_EARLY, manifest)
        assert space.hull_id == "eagle"
