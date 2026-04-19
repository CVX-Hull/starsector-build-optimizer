"""Tests for the game manifest — authoritative game-rule oracle.

Per spec 29 + plan §safeguard 5: schema-version gate, content-baseline
lower bounds, enum parsing, symmetry of incompatibility probe. These tests
run in CI on every PR — a stale or malformed manifest fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from starsector_optimizer.game_manifest import (
    EXPECTED_SCHEMA_VERSION,
    GameManifest,
    HullmodSpec,
    MIN_VANILLA_HULL_COUNT,
    MIN_VANILLA_HULLMOD_COUNT,
    MIN_VANILLA_WEAPON_COUNT,
    SlotMountType,
    WeaponMountType,
    _parse_enum,
    _parse_hullmod,
)
from starsector_optimizer.models import (
    DamageType,
    HullSize,
    ShieldType,
    SlotSize,
    SlotType,
    WeaponType,
)

_MANIFEST_PATH = Path("game/starsector/manifest.json")
_PREP_HULL_IDS: tuple[tuple[str, HullSize], ...] = (
    ("wolf", HullSize.FRIGATE),
    ("lasher", HullSize.FRIGATE),
    ("hammerhead", HullSize.DESTROYER),
    ("sunder", HullSize.DESTROYER),
    ("eagle", HullSize.CRUISER),
    ("dominator", HullSize.CRUISER),
    ("gryphon", HullSize.CRUISER),
    ("onslaught", HullSize.CAPITAL_SHIP),
)


@pytest.fixture(scope="module")
def manifest() -> GameManifest:
    return GameManifest.load(_MANIFEST_PATH)


def test_manifest_file_exists_and_parses_as_json() -> None:
    assert _MANIFEST_PATH.is_file(), f"missing {_MANIFEST_PATH}"
    data = json.loads(_MANIFEST_PATH.read_text())
    for key in ("constants", "weapons", "hullmods", "hulls"):
        assert key in data, f"manifest missing top-level key {key!r}"


def test_schema_version_matches_python_constant(manifest: GameManifest) -> None:
    assert manifest.constants.manifest_schema_version == EXPECTED_SCHEMA_VERSION


def test_schema_mismatch_raises_value_error(tmp_path: Path) -> None:
    data = json.loads(_MANIFEST_PATH.read_text())
    data["constants"]["manifest_schema_version"] = EXPECTED_SCHEMA_VERSION + 999
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="schema mismatch"):
        GameManifest.load(bad)


def test_missing_manifest_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest not found"):
        GameManifest.load(tmp_path / "does-not-exist.json")


def test_weapon_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    assert len(manifest.weapons) >= MIN_VANILLA_WEAPON_COUNT


def test_hullmod_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    assert len(manifest.hullmods) >= MIN_VANILLA_HULLMOD_COUNT


def test_hull_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    assert len(manifest.hulls) >= MIN_VANILLA_HULL_COUNT


def test_phase7_prep_hull_ids_present_and_correctly_sized(manifest: GameManifest) -> None:
    for hid, expected_size in _PREP_HULL_IDS:
        assert hid in manifest.hulls, f"prep hull {hid!r} missing from manifest"
        assert manifest.hulls[hid].size == expected_size, (
            f"{hid}: size={manifest.hulls[hid].size} expected={expected_size}"
        )


def test_parsed_enum_types_are_enums(manifest: GameManifest) -> None:
    wolf = manifest.hulls["wolf"]
    assert isinstance(wolf.size, HullSize)
    assert isinstance(wolf.shield_type, ShieldType)
    first_slot = next((s for s in wolf.slots if not s.is_decorative), None)
    assert first_slot is not None
    assert isinstance(first_slot.type, SlotType)
    assert isinstance(first_slot.size, SlotSize)
    assert isinstance(first_slot.mount_type, SlotMountType)
    w = next(iter(manifest.weapons.values()))
    assert isinstance(w.type, WeaponType)
    assert isinstance(w.damage_type, DamageType)
    assert isinstance(w.mount_type, WeaponMountType)


def test_incompatible_with_is_symmetric(manifest: GameManifest) -> None:
    """If hullmod A forbids B, B must forbid A. Asymmetry implies a probe bug."""
    for hm_id, hm in manifest.hullmods.items():
        for other in hm.incompatible_with:
            other_spec = manifest.hullmods.get(other)
            if other_spec is None:
                # Forward-compat: a mod listing an unknown counterparty is a
                # warning-level condition, not a test failure.
                continue
            assert hm_id in other_spec.incompatible_with, (
                f"asymmetric incompat: {hm_id} forbids {other} but {other} "
                f"does not forbid {hm_id}"
            )


def test_built_in_mods_reference_real_hullmods(manifest: GameManifest) -> None:
    for hull in manifest.hulls.values():
        for mod_id in hull.built_in_mods:
            assert mod_id in manifest.hullmods, (
                f"hull {hull.id} built-in mod {mod_id!r} absent from manifest"
            )


def test_built_in_weapons_reference_real_weapons(manifest: GameManifest) -> None:
    """Built-in weapons on player-assignable slot types must resolve.

    Non-assignable slot types (DECORATIVE, SYSTEM, BUILT_IN, LAUNCH_BAY,
    STATION_MODULE) carry engine-pseudo "weapons" — decorative blinkers, the
    Onslaught's ship-system TPC, station modules, fighter bays — that the
    manifest deliberately omits from the combat-relevant weapon pool.
    """
    assignable_slot_types = {
        SlotType.BALLISTIC,
        SlotType.ENERGY,
        SlotType.MISSILE,
        SlotType.HYBRID,
        SlotType.COMPOSITE,
        SlotType.SYNERGY,
        SlotType.UNIVERSAL,
    }
    for hull in manifest.hulls.values():
        slot_by_id = {s.id: s for s in hull.slots}
        for slot_id, weapon_id in hull.built_in_weapons.items():
            slot = slot_by_id.get(slot_id)
            if slot is None or slot.type not in assignable_slot_types:
                continue
            assert weapon_id in manifest.weapons, (
                f"hull {hull.id} slot {slot_id} built-in weapon "
                f"{weapon_id!r} absent from manifest"
            )


def test_unknown_enum_value_skipped_with_warning(caplog) -> None:
    """Forward-compat: an unknown enum member logs WARN and skips the spec."""
    import logging
    caplog.set_level(logging.WARNING, logger="starsector_optimizer.game_manifest")
    result = _parse_hullmod({
        "id": "future_mod",
        "tier": 0,
        "hidden": False,
        "hidden_everywhere": False,
        "tags": [],
        "ui_tags": [],
        "op_cost_by_size": {
            "FRIGATE": 5,
            "FUTURE_SIZE": 99,  # unknown — future Starsector version
        },
        "applicable_hull_sizes": [],
        "incompatible_with": [],
    })
    assert result is not None
    assert HullSize.FRIGATE in result.op_cost_by_size
    assert any("FUTURE_SIZE" in r.message for r in caplog.records)


def test_enum_parse_helper_returns_none_for_unknown() -> None:
    out = _parse_enum(HullSize, "UNKNOWN_SIZE", field_name="size", spec_id="x")
    assert out is None


def test_hullmod_op_cost_lookup_by_size(manifest: GameManifest) -> None:
    # Pick any hullmod with an op cost and verify op_cost() resolves sizes.
    for hm in manifest.hullmods.values():
        if hm.op_cost_by_size:
            size, cost = next(iter(hm.op_cost_by_size.items()))
            assert hm.op_cost(size) == cost
            return
    pytest.fail("no hullmod with op_cost_by_size found")


def test_constants_block_has_required_fields(manifest: GameManifest) -> None:
    c = manifest.constants
    assert c.game_version
    assert c.max_vents_per_ship > 0
    assert c.max_capacitors_per_ship > 0
    assert c.default_cr > 0
    assert c.flux_per_capacitor > 0
    assert c.dissipation_per_vent > 0
    assert c.max_logistics_hullmods >= 0


def test_damage_multipliers_cover_all_damage_types(manifest: GameManifest) -> None:
    """Both shield and armor multipliers must include every DamageType.

    Missing entries would crash scorer arithmetic with KeyError — better to
    fail here at load time with a clear assertion than at per-weapon scoring.
    """
    for dt in DamageType:
        assert dt in manifest.constants.shield_damage_mult_by_type, (
            f"shield_damage_mult_by_type missing DamageType.{dt.name}"
        )
        assert dt in manifest.constants.armor_damage_mult_by_type, (
            f"armor_damage_mult_by_type missing DamageType.{dt.name}"
        )
