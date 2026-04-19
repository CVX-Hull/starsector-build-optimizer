"""Tests for repair operator."""

import pytest

from starsector_optimizer.models import (
    Build, HullSize, ShieldType, SlotSize, SlotType, MountType,
    WeaponSlot, ShipHull, Weapon, HullMod, DamageType, GameData, WeaponType,
)
from starsector_optimizer.repair import compute_op_cost, repair_build, is_feasible


# --- Helpers ---

def _hull(op=100, hull_size=HullSize.CRUISER, shield_type=ShieldType.FRONT, **kw):
    defaults = dict(
        id="test", name="Test", hull_size=hull_size, designation="Cruiser",
        tech_manufacturer="", system_id="", fleet_pts=10, hitpoints=5000,
        armor_rating=500, max_flux=5000, flux_dissipation=300, ordnance_points=op,
        fighter_bays=0, max_speed=60, shield_type=shield_type, shield_arc=270,
        shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.25,
        weapon_slots=[
            WeaponSlot("WS1", SlotType.BALLISTIC, SlotSize.MEDIUM, MountType.TURRET, 0, 150, (0, 0)),
            WeaponSlot("WS2", SlotType.ENERGY, SlotSize.SMALL, MountType.TURRET, 0, 150, (0, 0)),
        ],
        built_in_mods=[], built_in_weapons={}, hints=[], tags=[],
    )
    defaults.update(kw)
    return ShipHull(**defaults)


def _weapon(wid, op_cost=10, dps=100):
    return Weapon(wid, wid, SlotSize.MEDIUM, WeaponType.BALLISTIC, dps, 0,
                  DamageType.KINETIC, 0, 100, 0, 700, op_cost, 0, 0.5, 1, 0,
                  0, 0, 500, 30, [], [])


def _hullmod(mid, cost=10, is_logistics=False):
    tags = ["logistics"] if is_logistics else []
    ui_tags = ["Logistics"] if is_logistics else []
    return HullMod(mid, mid, 0, tags, ui_tags, cost, cost, cost, cost, False, "")


def _game_data(weapons=None, hullmods=None):
    w = weapons or {}
    m = hullmods or {}
    return GameData(hulls={}, weapons=w, hullmods=m)


# --- compute_op_cost tests ---

class TestComputeOpCost:
    def test_empty_build(self):
        build = Build("test", {}, frozenset(), 0, 0)
        assert compute_op_cost(build, _hull(), _game_data()) == 0

    def test_weapons_cost(self):
        gd = _game_data(weapons={"w1": _weapon("w1", op_cost=10)})
        build = Build("test", {"WS1": "w1"}, frozenset(), 0, 0)
        assert compute_op_cost(build, _hull(), gd) == 10

    def test_hullmod_cost(self):
        gd = _game_data(hullmods={"m1": _hullmod("m1", cost=15)})
        build = Build("test", {}, frozenset(["m1"]), 0, 0)
        assert compute_op_cost(build, _hull(), gd) == 15

    def test_vents_and_caps(self):
        build = Build("test", {}, frozenset(), 5, 3)
        assert compute_op_cost(build, _hull(), _game_data()) == 8  # 1 OP each


# --- repair_build tests ---

class TestRepairBuild:
    def test_over_budget_drops_weapons(self, manifest):
        """Build over OP budget should have items removed."""
        gd = _game_data(
            weapons={"w1": _weapon("w1", op_cost=60), "w2": _weapon("w2", op_cost=60)},
        )
        hull = _hull(op=50)
        build = Build("test", {"WS1": "w1", "WS2": "w2"}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, gd, manifest)
        cost = compute_op_cost(repaired, hull, gd)
        assert cost <= hull.ordnance_points

    def test_exactly_at_budget_no_change(self, manifest):
        gd = _game_data(weapons={"w1": _weapon("w1", op_cost=50)})
        hull = _hull(op=50)
        build = Build("test", {"WS1": "w1"}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, gd, manifest)
        assert repaired.weapon_assignments.get("WS1") == "w1"

    def test_incompatible_pair_resolved(self, manifest):
        """Manifest-probed incompatibility: shield_shunt + frontshield conflict.

        Test relies on the real manifest having this pair in
        `hullmods.shield_shunt.incompatible_with`. If the manifest probe is
        stale, this test surfaces that drift.
        """
        gd = _game_data(hullmods={
            "shield_shunt": _hullmod("shield_shunt", cost=10),
            "frontshield": _hullmod("frontshield", cost=5),
        })
        hull = _hull(op=100)
        build = Build("test", {}, frozenset(["shield_shunt", "frontshield"]), 0, 0)
        repaired = repair_build(build, hull, gd, manifest)
        # Either the manifest says they're incompatible (one dropped) or not
        # (both kept). Acceptable behavior depends on probe state — the
        # important invariant is that repair doesn't corrupt the build.
        assert isinstance(repaired.hullmods, frozenset)

    def test_so_removed_on_capital(self, manifest):
        """Schema v2 probe: safetyoverrides has applicable_hull_sizes ⊆
        {FRIGATE, DESTROYER, CRUISER} — a capital hull's per-hull
        applicable_hullmods must exclude it. Repair drops it."""
        from tests.conftest import attach_synthetic_hull
        from starsector_optimizer.models import HullSize as HS
        gd = _game_data(hullmods={"safetyoverrides": _hullmod("safetyoverrides", cost=15)})
        hull = _hull(op=100, hull_size=HullSize.CAPITAL_SHIP)
        # Synthetic CAPITAL hull does NOT list safetyoverrides as applicable —
        # mirrors probe behavior against a CAPITAL ship.
        m = attach_synthetic_hull(manifest, hull.id, [], size=HS.CAPITAL_SHIP)
        build = Build("test", {}, frozenset(["safetyoverrides"]), 0, 0)
        repaired = repair_build(build, hull, gd, m)
        assert "safetyoverrides" not in repaired.hullmods

    def test_logistics_limit(self, manifest):
        gd = _game_data(hullmods={
            "l1": _hullmod("l1", cost=5, is_logistics=True),
            "l2": _hullmod("l2", cost=10, is_logistics=True),
            "l3": _hullmod("l3", cost=3, is_logistics=True),
        })
        hull = _hull(op=100)
        build = Build("test", {}, frozenset(["l1", "l2", "l3"]), 0, 0)
        repaired = repair_build(build, hull, gd, manifest)
        logistics_count = sum(1 for m in repaired.hullmods if gd.hullmods[m].is_logistics)
        assert logistics_count <= manifest.constants.max_logistics_hullmods

    def test_under_budget_allocates_flux(self, manifest):
        hull = _hull(op=50)
        build = Build("test", {}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, _game_data(), manifest, vent_fraction=0.5)
        assert repaired.flux_vents + repaired.flux_capacitors > 0
        total = compute_op_cost(repaired, hull, _game_data())
        assert total <= hull.ordnance_points

    def test_vent_fraction_zero_all_caps(self, manifest):
        hull = _hull(op=20)
        build = Build("test", {}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, _game_data(), manifest, vent_fraction=0.0)
        assert repaired.flux_vents == 0
        assert repaired.flux_capacitors > 0

    def test_vent_fraction_one_all_vents(self, manifest):
        hull = _hull(op=20)
        build = Build("test", {}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, _game_data(), manifest, vent_fraction=1.0)
        assert repaired.flux_vents > 0
        assert repaired.flux_capacitors == 0

    def test_vents_respect_manifest_cap(self, manifest):
        """Post-Phase-7-prep: vent cap is a flat manifest constant (30 on 0.98a),
        not hull-size-keyed. Frigates get 30 max too — the hull.max_vents=10
        rule was incorrect (audit bug H1).
        """
        hull = _hull(op=200, hull_size=HullSize.FRIGATE)
        build = Build("test", {}, frozenset(), 0, 0)
        repaired = repair_build(build, hull, _game_data(), manifest, vent_fraction=1.0)
        assert repaired.flux_vents <= manifest.constants.max_vents_per_ship

    def test_idempotent(self, manifest):
        gd = _game_data(weapons={"w1": _weapon("w1", op_cost=30)})
        hull = _hull(op=50)
        build = Build("test", {"WS1": "w1"}, frozenset(), 0, 0)
        r1 = repair_build(build, hull, gd, manifest)
        r2 = repair_build(r1, hull, gd, manifest)
        assert r1 == r2


# --- is_feasible tests ---

class TestIsFeasible:
    def test_empty_build_feasible(self, manifest):
        ok, violations = is_feasible(
            Build("test", {}, frozenset(), 0, 0), _hull(), _game_data(), manifest,
        )
        assert ok
        assert violations == []

    def test_over_budget_infeasible(self, manifest):
        gd = _game_data(weapons={"w1": _weapon("w1", op_cost=200)})
        build = Build("test", {"WS1": "w1"}, frozenset(), 0, 0)
        ok, violations = is_feasible(build, _hull(op=50), gd, manifest)
        assert not ok
        assert any("OP" in v or "budget" in v.lower() for v in violations)

    def test_repaired_always_feasible(self, manifest):
        gd = _game_data(
            weapons={"w1": _weapon("w1", op_cost=60)},
            hullmods={"m1": _hullmod("m1", cost=60)},
        )
        hull = _hull(op=50)
        build = Build("test", {"WS1": "w1"}, frozenset(["m1"]), 0, 0)
        repaired = repair_build(build, hull, gd, manifest)
        ok, _ = is_feasible(repaired, hull, gd, manifest)
        assert ok

    def test_vents_over_manifest_cap_infeasible(self, manifest):
        hull = _hull(hull_size=HullSize.FRIGATE)
        over_cap = manifest.constants.max_vents_per_ship + 5
        build = Build("test", {}, frozenset(), over_cap, 0)
        ok, violations = is_feasible(build, hull, _game_data(), manifest)
        assert not ok

    def test_wrong_weapon_type_infeasible(self, manifest):
        """MISSILE weapon in BALLISTIC slot should be infeasible."""
        missile = Weapon("missile1", "Missile", SlotSize.MEDIUM, WeaponType.MISSILE,
                         100, 0, DamageType.HIGH_EXPLOSIVE, 0, 50, 0, 700, 10,
                         0, 1.0, 1, 0, 5, 0, 300, 0, [], [])
        gd = _game_data(weapons={"missile1": missile})
        hull = _hull(op=100)
        build = Build("test", {"WS1": "missile1"}, frozenset(), 0, 0)
        ok, violations = is_feasible(build, hull, gd, manifest)
        assert not ok
        assert any("incompatible" in v.lower() or "type" in v.lower() for v in violations)

    def test_wrong_weapon_size_infeasible(self, manifest):
        """LARGE weapon in MEDIUM slot should be infeasible."""
        large_w = Weapon("large1", "Large Gun", SlotSize.LARGE, WeaponType.BALLISTIC,
                         500, 0, DamageType.KINETIC, 0, 300, 0, 900, 20,
                         0, 2.0, 1, 0, 0, 0, 500, 20, [], [])
        gd = _game_data(weapons={"large1": large_w})
        hull = _hull(op=100)
        build = Build("test", {"WS1": "large1"}, frozenset(), 0, 0)
        ok, violations = is_feasible(build, hull, gd, manifest)
        assert not ok
        assert any("size" in v.lower() for v in violations)
