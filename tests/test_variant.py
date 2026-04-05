"""Tests for variant generator."""

import json
import tempfile
from pathlib import Path

import pytest

from starsector_optimizer.models import (
    Build, HullSize, ShieldType, SlotSize, SlotType, MountType,
    WeaponSlot, ShipHull, Weapon, HullMod, DamageType, GameData, WeaponType,
)
from starsector_optimizer.variant import (
    generate_variant,
    write_variant_file,
    load_variant_file,
)


def _hull(**kw):
    defaults = dict(
        id="eagle", name="Eagle", hull_size=HullSize.CRUISER, designation="Cruiser",
        tech_manufacturer="", system_id="", fleet_pts=10, hitpoints=5000,
        armor_rating=500, max_flux=5000, flux_dissipation=300, ordnance_points=100,
        fighter_bays=0, max_speed=60, shield_type=ShieldType.FRONT, shield_arc=270,
        shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.25,
        weapon_slots=[
            WeaponSlot("WS1", SlotType.BALLISTIC, SlotSize.MEDIUM, MountType.TURRET, 0, 150, (0, 0)),
            WeaponSlot("WS2", SlotType.ENERGY, SlotSize.SMALL, MountType.TURRET, 0, 150, (0, 0)),
        ],
        built_in_mods=[], built_in_weapons={"WS3": "builtin_weapon"}, hints=[], tags=[],
    )
    defaults.update(kw)
    return ShipHull(**defaults)


def _game_data():
    weapons = {
        "heavymauler": Weapon("heavymauler", "Heavy Mauler", SlotSize.MEDIUM,
                              WeaponType.BALLISTIC, 200, 0, DamageType.KINETIC, 0,
                              200, 0, 700, 10, 0, 0.5, 1, 0, 0, 0, 500, 30, [], []),
        "pdlaser": Weapon("pdlaser", "PD Laser", SlotSize.SMALL,
                          WeaponType.ENERGY, 0, 100, DamageType.ENERGY, 0,
                          0, 80, 500, 3, 0, 0, 1, 0, 0, 0, 0, 30, ["PD"], []),
    }
    return GameData(hulls={}, weapons=weapons, hullmods={})


class TestGenerateVariant:
    def test_has_required_keys(self):
        build = Build("eagle", {"WS1": "heavymauler"}, frozenset(["heavyarmor"]), 15, 10)
        variant = generate_variant(build, _hull(), _game_data())
        for key in ["variantId", "hullId", "fluxVents", "fluxCapacitors",
                     "hullMods", "weaponGroups"]:
            assert key in variant

    def test_hull_id_matches(self):
        build = Build("eagle", {}, frozenset(), 0, 0)
        variant = generate_variant(build, _hull(), _game_data())
        assert variant["hullId"] == "eagle"

    def test_flux_values(self):
        build = Build("eagle", {}, frozenset(), 15, 10)
        variant = generate_variant(build, _hull(), _game_data())
        assert variant["fluxVents"] == 15
        assert variant["fluxCapacitors"] == 10

    def test_hullmods_list(self):
        build = Build("eagle", {}, frozenset(["heavyarmor", "targetingunit"]), 0, 0)
        variant = generate_variant(build, _hull(), _game_data())
        assert set(variant["hullMods"]) == {"heavyarmor", "targetingunit"}

    def test_weapon_groups_present(self):
        build = Build("eagle", {"WS1": "heavymauler", "WS2": "pdlaser"}, frozenset(), 0, 0)
        variant = generate_variant(build, _hull(), _game_data())
        assert len(variant["weaponGroups"]) > 0

    def test_empty_slots_excluded(self):
        build = Build("eagle", {"WS1": None, "WS2": "pdlaser"}, frozenset(), 0, 0)
        variant = generate_variant(build, _hull(), _game_data())
        all_weapons = {}
        for group in variant["weaponGroups"]:
            all_weapons.update(group["weapons"])
        assert "WS1" not in all_weapons

    def test_builtin_weapons_excluded(self):
        build = Build("eagle", {"WS3": "builtin_weapon", "WS1": "heavymauler"}, frozenset(), 0, 0)
        hull = _hull()
        variant = generate_variant(build, hull, _game_data())
        all_weapons = {}
        for group in variant["weaponGroups"]:
            all_weapons.update(group["weapons"])
        assert "WS3" not in all_weapons

    def test_valid_json(self):
        build = Build("eagle", {"WS1": "heavymauler"}, frozenset(["heavyarmor"]), 15, 10)
        variant = generate_variant(build, _hull(), _game_data())
        # Should be JSON-serializable
        text = json.dumps(variant)
        assert json.loads(text) == variant

    def test_custom_variant_id(self):
        build = Build("eagle", {}, frozenset(), 0, 0)
        variant = generate_variant(build, _hull(), _game_data(), variant_id="my_custom_id")
        assert variant["variantId"] == "my_custom_id"


class TestWriteAndLoadVariant:
    def test_round_trip(self):
        build = Build("eagle", {"WS1": "heavymauler"}, frozenset(["heavyarmor"]), 15, 10)
        variant = generate_variant(build, _hull(), _game_data())
        with tempfile.NamedTemporaryFile(suffix=".variant", mode="w", delete=False) as f:
            write_variant_file(variant, Path(f.name))
            loaded = load_variant_file(Path(f.name))
        assert loaded["hullId"] == variant["hullId"]
        assert loaded["fluxVents"] == variant["fluxVents"]
        assert set(loaded["hullMods"]) == set(variant["hullMods"])


class TestLoadExistingVariant:
    def test_load_game_variant(self, game_dir):
        """Load a real .variant file from game data."""
        variant_dir = game_dir / "data" / "variants"
        variant_files = list(variant_dir.glob("*.variant"))
        assert len(variant_files) > 0
        loaded = load_variant_file(variant_files[0])
        assert "hullId" in loaded
        assert "weaponGroups" in loaded
