"""Tests for game data parser using real 0.98a game data."""

from pathlib import Path

import pytest

from starsector_optimizer.models import (
    DamageType,
    GameData,
    HullSize,
    ShieldType,
    SlotSize,
    SlotType,
    WeaponType,
)
from starsector_optimizer.parser import (
    extract_wpn_metadata,
    load_game_data,
    parse_hullmod_csv,
    parse_loose_json,
    parse_ship_csv,
    parse_ship_file,
    parse_weapon_csv,
    merge_ship_hull_data,
)


# --- parse_loose_json tests ---


class TestParseLooseJson:
    def test_trailing_comma_object(self):
        result = parse_loose_json('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_array(self):
        result = parse_loose_json('{"a": [1, 2, 3,]}')
        assert result["a"] == [1, 2, 3]

    def test_hash_comment(self):
        result = parse_loose_json('{\n"a": 1, # comment\n"b": 2\n}')
        assert result == {"a": 1, "b": 2}

    def test_slash_comment(self):
        result = parse_loose_json('{\n"a": 1, // comment\n"b": 2\n}')
        assert result == {"a": 1, "b": 2}

    def test_valid_json_unchanged(self):
        result = parse_loose_json('{"a": 1, "b": [2, 3]}')
        assert result == {"a": 1, "b": [2, 3]}


# --- Ship parsing tests (real game data) ---


class TestParseShipCsv:
    def test_eagle_exists(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        hull_ids = {h.id for h in hulls}
        assert "eagle" in hull_ids

    def test_eagle_stats(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        eagle = next(h for h in hulls if h.id == "eagle")
        assert eagle.name == "Eagle"
        assert eagle.hull_size == HullSize.CRUISER
        assert eagle.hitpoints == 8000.0
        assert eagle.armor_rating == 1000.0
        assert eagle.ordnance_points == 155
        assert eagle.flux_dissipation == 700.0
        assert eagle.max_flux == 11000.0
        assert eagle.shield_type == ShieldType.FRONT
        assert eagle.shield_efficiency == 0.8

    def test_wolf_is_frigate(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        wolf = next(h for h in hulls if h.id == "wolf")
        assert wolf.hull_size == HullSize.FRIGATE

    def test_parses_multiple_hulls(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        assert len(hulls) > 100  # 211 rows in 0.98a


class TestParseShipFile:
    def test_eagle_ship_file(self, game_dir):
        data = parse_ship_file(game_dir / "data" / "hulls" / "eagle.ship")
        assert data["hullId"] == "eagle"
        assert data["hullSize"] == "CRUISER"
        assert len(data["weaponSlots"]) == 13

    def test_weapon_slot_fields(self, game_dir):
        data = parse_ship_file(game_dir / "data" / "hulls" / "eagle.ship")
        slot = data["weaponSlots"][0]
        assert "id" in slot
        assert "type" in slot
        assert "size" in slot
        assert "mount" in slot


class TestMergeShipHullData:
    def test_eagle_gets_weapon_slots(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        hulls = merge_ship_hull_data(hulls, game_dir / "data" / "hulls")
        eagle = next(h for h in hulls if h.id == "eagle")
        assert len(eagle.weapon_slots) == 13

    def test_eagle_slot_types(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        hulls = merge_ship_hull_data(hulls, game_dir / "data" / "hulls")
        eagle = next(h for h in hulls if h.id == "eagle")
        slot_types = {s.slot_type for s in eagle.weapon_slots}
        assert SlotType.BALLISTIC in slot_types
        assert SlotType.ENERGY in slot_types
        assert SlotType.MISSILE in slot_types

    def test_afflictor_has_builtin_mods(self, game_dir):
        hulls = parse_ship_csv(game_dir / "data" / "hulls" / "ship_data.csv")
        hulls = merge_ship_hull_data(hulls, game_dir / "data" / "hulls")
        afflictor = next((h for h in hulls if h.id == "afflictor"), None)
        if afflictor:
            assert "phasefield" in afflictor.built_in_mods


# --- Weapon parsing tests ---


class TestParseWeaponCsv:
    def test_heavy_mauler_exists(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        weapon_ids = {w.id for w in weapons}
        assert "heavymauler" in weapon_ids

    def test_heavy_mauler_stats(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        mauler = next(w for w in weapons if w.id == "heavymauler")
        assert mauler.name == "Heavy Mauler"
        assert mauler.weapon_type == WeaponType.BALLISTIC
        assert mauler.size == SlotSize.MEDIUM
        assert mauler.damage_type == DamageType.HIGH_EXPLOSIVE
        assert mauler.damage_per_shot == 200.0
        assert mauler.op_cost == 12
        assert mauler.range == 1000.0

    def test_tactical_laser_is_beam(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        laser = next(w for w in weapons if w.id == "taclaser")
        assert laser.is_beam is True
        assert laser.damage_per_second > 0
        assert laser.weapon_type == WeaponType.ENERGY

    def test_parses_multiple_weapons(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        assert len(weapons) > 100

    def test_sustained_dps_positive(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        combat_weapons = [w for w in weapons if w.op_cost > 0]
        for w in combat_weapons:
            assert w.sustained_dps >= 0, f"{w.id} has negative DPS"

    def test_pd_weapon_detected(self, game_dir):
        weapons = parse_weapon_csv(
            game_dir / "data" / "weapons" / "weapon_data.csv",
            game_dir / "data" / "weapons",
        )
        pd_weapons = [w for w in weapons if w.is_pd]
        assert len(pd_weapons) > 5


# --- Hullmod parsing tests ---


class TestParseHullmodCsv:
    def test_heavy_armor_exists(self, game_dir):
        mods = parse_hullmod_csv(game_dir / "data" / "hullmods" / "hull_mods.csv")
        mod_ids = {m.id for m in mods}
        assert "heavyarmor" in mod_ids

    def test_heavy_armor_stats(self, game_dir):
        mods = parse_hullmod_csv(game_dir / "data" / "hullmods" / "hull_mods.csv")
        ha = next(m for m in mods if m.id == "heavyarmor")
        assert ha.name == "Heavy Armor"
        assert ha.cost_frigate == 8
        assert ha.cost_destroyer == 15
        assert ha.cost_cruiser == 20
        assert ha.is_logistics is False
        assert ha.is_hidden is False

    def test_logistics_mod_detected(self, game_dir):
        """Logistics mods identified by 'Logistics' in uiTags."""
        mods = parse_hullmod_csv(game_dir / "data" / "hullmods" / "hull_mods.csv")
        logistics = [m for m in mods if m.is_logistics]
        assert len(logistics) > 3
        # Augmented Drive Field should be logistics
        aug = next((m for m in mods if m.id == "augmentedengines"), None)
        assert aug is not None
        assert aug.is_logistics is True

    def test_hidden_mods_detected(self, game_dir):
        mods = parse_hullmod_csv(game_dir / "data" / "hullmods" / "hull_mods.csv")
        hidden = [m for m in mods if m.is_hidden]
        assert len(hidden) > 0

    def test_parses_multiple_mods(self, game_dir):
        mods = parse_hullmod_csv(game_dir / "data" / "hullmods" / "hull_mods.csv")
        assert len(mods) > 100


# --- load_game_data integration test ---


class TestLoadGameData:
    def test_returns_game_data(self, game_data):
        assert isinstance(game_data, GameData)

    def test_has_hulls(self, game_data):
        assert len(game_data.hulls) > 50

    def test_has_weapons(self, game_data):
        assert len(game_data.weapons) > 100

    def test_has_hullmods(self, game_data):
        assert len(game_data.hullmods) > 100

    def test_eagle_in_hulls(self, game_data):
        assert "eagle" in game_data.hulls
        assert game_data.hulls["eagle"].hull_size == HullSize.CRUISER

    def test_eagle_has_weapon_slots(self, game_data):
        assert len(game_data.hulls["eagle"].weapon_slots) == 13

    def test_heavymauler_in_weapons(self, game_data):
        assert "heavymauler" in game_data.weapons
        assert game_data.weapons["heavymauler"].weapon_type == WeaponType.BALLISTIC

    def test_heavyarmor_in_hullmods(self, game_data):
        assert "heavyarmor" in game_data.hullmods
