"""Tests for the game manifest — authoritative game-rule oracle.

Schema v2 (Commit G): per-hull applicability + conditional exclusions
replace the prior hullmod.applicable_hull_sizes + incompatible_with
fields. Canaries target specific built-in-aware behaviors that used to
fail silently on the 4-representative probe.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from starsector_optimizer.game_manifest import (
    EXPECTED_SCHEMA_VERSION,
    GameManifest,
    MIN_APPLICABLE_HULLMODS_PER_HULL,
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


# --- Structural / schema ----------------------------------------------------


def test_manifest_file_exists_and_parses_as_json() -> None:
    assert _MANIFEST_PATH.is_file(), f"missing {_MANIFEST_PATH}"
    data = json.loads(_MANIFEST_PATH.read_text())
    for key in ("constants", "weapons", "hullmods", "hulls"):
        assert key in data, f"manifest missing top-level key {key!r}"


def test_schema_version_is_v2(manifest: GameManifest) -> None:
    assert manifest.constants.manifest_schema_version == 2
    assert EXPECTED_SCHEMA_VERSION == 2


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


# --- Vanilla content baselines ---------------------------------------------


def test_weapon_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    assert len(manifest.weapons) >= MIN_VANILLA_WEAPON_COUNT


def test_hullmod_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    assert len(manifest.hullmods) >= MIN_VANILLA_HULLMOD_COUNT


def test_hull_count_meets_vanilla_lower_bound(manifest: GameManifest) -> None:
    # v2 emits only probed hulls (skip filter excludes fighters, stations,
    # modules, hidden). Baseline dropped from 532 → 200 accordingly.
    assert len(manifest.hulls) >= MIN_VANILLA_HULL_COUNT


def test_phase7_prep_hull_ids_present_and_correctly_sized(manifest: GameManifest) -> None:
    for hid, expected_size in _PREP_HULL_IDS:
        assert hid in manifest.hulls, f"prep hull {hid!r} missing from manifest"
        assert manifest.hulls[hid].size == expected_size, (
            f"{hid}: size={manifest.hulls[hid].size} expected={expected_size}"
        )


# --- Enum parsing / forward-compat ------------------------------------------


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


def test_unknown_enum_value_skipped_with_warning(caplog) -> None:
    """Forward-compat: an unknown enum member logs WARN and skips the field."""
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
    })
    assert result is not None
    assert HullSize.FRIGATE in result.op_cost_by_size
    assert any("FUTURE_SIZE" in r.message for r in caplog.records)


def test_enum_parse_helper_returns_none_for_unknown() -> None:
    out = _parse_enum(HullSize, "UNKNOWN_SIZE", field_name="size", spec_id="x")
    assert out is None


# --- Constants block ---------------------------------------------------------


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
    """Shield, armor, AND hull multipliers must include every DamageType."""
    for dt in DamageType:
        assert dt in manifest.constants.shield_damage_mult_by_type, (
            f"shield_damage_mult_by_type missing DamageType.{dt.name}"
        )
        assert dt in manifest.constants.armor_damage_mult_by_type, (
            f"armor_damage_mult_by_type missing DamageType.{dt.name}"
        )
        assert dt in manifest.constants.hull_damage_mult_by_type, (
            f"hull_damage_mult_by_type missing DamageType.{dt.name}"
        )


def test_hull_damage_mult_all_one_in_vanilla(manifest: GameManifest) -> None:
    """Vanilla 0.98a-RC8: all damage types deal 100% to hull. Future patches
    might change this; canary fires on that balance change."""
    for dt, mult in manifest.constants.hull_damage_mult_by_type.items():
        assert mult == 1.0, (
            f"hull_damage_mult_by_type[{dt.name}] = {mult}; vanilla is 1.0. "
            f"Probe balance patch regression."
        )


# --- Schema v2: per-hull applicability + conditional exclusions -------------


def test_no_stateful_hullmods(manifest: GameManifest) -> None:
    """Determinism invariant (audit R10): zero hullmods should have a
    divergent applicability answer on two back-to-back probes."""
    assert not manifest.constants.stateful_hullmods, (
        f"non-deterministic isApplicableToShip: "
        f"{sorted(manifest.constants.stateful_hullmods)}. "
        f"These mods cannot be probed reliably; their applicable set "
        f"is nondeterministic and upstream inference is untrustworthy."
    )


def test_hullmod_has_no_schema_v1_fields(manifest: GameManifest) -> None:
    """Schema v2 removes applicable_hull_sizes + incompatible_with from
    the per-hullmod record; both now live per-hull. If a future refactor
    reintroduces them, this test catches the parallel-path regression."""
    hm = next(iter(manifest.hullmods.values()))
    assert not hasattr(hm, "applicable_hull_sizes"), (
        "schema v1 field `applicable_hull_sizes` must stay deleted"
    )
    assert not hasattr(hm, "incompatible_with"), (
        "schema v1 field `incompatible_with` must stay deleted"
    )


def test_applicable_hullmods_floor(manifest: GameManifest) -> None:
    """Every hull in the manifest was probed and has ≥ MIN_APPLICABLE_HULLMODS_PER_HULL
    applicable mods. Empty would signal a probe crash."""
    for hid, hull in manifest.hulls.items():
        assert len(hull.applicable_hullmods) >= MIN_APPLICABLE_HULLMODS_PER_HULL, (
            f"hull {hid} has {len(hull.applicable_hullmods)} applicable_hullmods"
        )


def test_applicable_hullmods_reference_real_mods(manifest: GameManifest) -> None:
    """Every mod ID in any hull's applicable_hullmods / conditional_exclusions
    must resolve in manifest.hullmods."""
    known = set(manifest.hullmods.keys())
    for hid, hull in manifest.hulls.items():
        dangling_app = hull.applicable_hullmods - known
        assert not dangling_app, (
            f"hull {hid} applicable_hullmods refs unknown mods: {dangling_app}"
        )
        for a, blocked in hull.conditional_exclusions.items():
            assert a in known, f"hull {hid} cond_excl key {a!r} unknown"
            dangling_b = blocked - known
            assert not dangling_b, (
                f"hull {hid} cond_excl[{a}] refs unknown mods: {dangling_b}"
            )


def test_built_in_mods_reference_real_hullmods(manifest: GameManifest) -> None:
    for hull in manifest.hulls.values():
        for mod_id in hull.built_in_mods:
            assert mod_id in manifest.hullmods, (
                f"hull {hull.id} built-in mod {mod_id!r} absent from manifest"
            )


def test_built_in_weapons_reference_real_weapons(manifest: GameManifest) -> None:
    assignable_slot_types = {
        SlotType.BALLISTIC, SlotType.ENERGY, SlotType.MISSILE,
        SlotType.HYBRID, SlotType.COMPOSITE, SlotType.SYNERGY,
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


def test_hullmod_op_cost_lookup_by_size(manifest: GameManifest) -> None:
    for hm in manifest.hullmods.values():
        if hm.op_cost_by_size:
            size, cost = next(iter(hm.op_cost_by_size.items()))
            assert hm.op_cost(size) == cost
            return
    pytest.fail("no hullmod with op_cost_by_size found")


# --- Canaries: specific built-in / shield / carrier / civilian behaviors ---


def test_canary_paragon_advancedcore_blocks_targeting(manifest: GameManifest) -> None:
    """Paragon has built-in `advancedcore` which blocks `targetingunit` AND
    `dedicated_targeting_core`. Per-hull applicability must reflect this.
    Schema v1 (4-rep probe) did not capture built-in-induced conflicts; the
    4-rep capital was Onslaught (no advancedcore built-in) so both targeting
    mods appeared applicable to every CAPITAL_SHIP — wrong for Paragon."""
    paragon = manifest.hulls["paragon"]
    assert "advancedcore" in paragon.built_in_mods
    assert "targetingunit" not in paragon.applicable_hullmods, (
        "Paragon's built-in advancedcore blocks targetingunit; v2 probe must see this"
    )
    assert "dedicated_targeting_core" not in paragon.applicable_hullmods


def test_canary_afflictor_phase_excludes_shield_mods(manifest: GameManifest) -> None:
    """Afflictor is a phase ship (shield_type == PHASE). Shield-modification
    hullmods must not apply. Schema v1 was blind to shield type beyond the
    4 FRONT-shield rep probes; v2 captures this per-hull."""
    afflictor = manifest.hulls["afflictor"]
    assert afflictor.shield_type == ShieldType.PHASE
    for mod in ("hardenedshieldemitter", "stabilizedshieldemitter",
                "extendedshieldemitter"):
        assert mod not in afflictor.applicable_hullmods, (
            f"phase ship afflictor should reject shield mod {mod}"
        )


def test_canary_afflictor_phase_accepts_phase_mods(manifest: GameManifest) -> None:
    """Afflictor is a phase ship. Phase-specific mods must apply."""
    afflictor = manifest.hulls["afflictor"]
    assert "phase_anchor" in afflictor.applicable_hullmods, (
        "phase_anchor is a phase-only mod; must be applicable to afflictor"
    )


def test_canary_medusa_omni_accepts_frontemitter(manifest: GameManifest) -> None:
    """Medusa has an OMNI shield; `frontemitter` converts OMNI to FRONT.
    Schema v1's 4-rep probe used FRONT-shield hulls only, so frontemitter
    was marked `applicable=[]` (applicable-nowhere), and Python's
    empty-fallback made it applicable EVERYWHERE — the exact bug this
    refactor eliminates."""
    medusa = manifest.hulls["medusa"]
    assert medusa.shield_type == ShieldType.OMNI
    assert "frontemitter" in medusa.applicable_hullmods


def test_canary_heron_carrier_accepts_expanded_deck_crew(manifest: GameManifest) -> None:
    """Heron is a dedicated carrier. Carrier-specific mods must apply.
    Schema v1 marked expanded_deck_crew as applicable-nowhere (no carrier
    in the 4-rep probe set)."""
    heron = manifest.hulls["heron"]
    assert heron.is_carrier
    assert "expanded_deck_crew" in heron.applicable_hullmods


def test_canary_buffalo_civilian_accepts_militarized_subsystems(manifest: GameManifest) -> None:
    """Buffalo is a civilian hull (has civgrade built-in). Militarized
    Subsystems is civilian-only; v1 marked it applicable-nowhere."""
    buffalo = manifest.hulls["buffalo"]
    assert "civgrade" in buffalo.built_in_mods
    assert "militarized_subsystems" in buffalo.applicable_hullmods


def test_canary_14_previously_broken_mods_have_applicable_hulls(manifest: GameManifest) -> None:
    """Every mod previously marked applicable-nowhere in schema v1 must
    now reach at least one hull. If this fails, the probe is still
    mis-classifying some subset of mods."""
    previously_broken = {
        "frontemitter",           # OMNI-shield only
        "frontshield",            # no-shield only
        "militarized_subsystems", # civilian only
        "converted_hangar",       # non-carrier-to-have-bay
        "converted_bay",          # wide
        "converted_fighterbay",   # carrier
        "expanded_deck_crew",     # carrier
        "phase_anchor",           # phase only
        "neural_integrator",      # AI core path
    }
    for mod in previously_broken:
        hulls = [hid for hid, h in manifest.hulls.items()
                 if mod in h.applicable_hullmods]
        assert hulls, (
            f"mod {mod!r} applicable to zero hulls — probe still mis-classifies it"
        )
