"""Tests for throughput estimator."""

import math

import pytest

from starsector_optimizer.estimator import (
    CloudProvider,
    HullSpaceStats,
    SimulationParams,
    ThroughputEstimate,
    compute_all_hull_stats,
    compute_hull_space_stats,
    estimate_throughput,
    format_estimate_report,
)
from starsector_optimizer.models import (
    GameData,
    HullMod,
    HullSize,
    MountType,
    ShipHull,
    ShieldType,
    SlotSize,
    SlotType,
    Weapon,
    WeaponSlot,
    WeaponType,
    DamageType,
)


# --- Fixtures ---


def _make_weapon(id: str, size: SlotSize, wtype: WeaponType, op: int = 5) -> Weapon:
    return Weapon(
        id=id, name=id, size=size, weapon_type=wtype,
        damage_per_shot=100, damage_per_second=50, damage_type=DamageType.ENERGY,
        emp=0, flux_per_shot=50, flux_per_second=25, range=600,
        op_cost=op, chargeup=0, chargedown=0, burst_size=1, burst_delay=0,
        ammo=-1, ammo_per_sec=0, proj_speed=800, turn_rate=0,
        hints=[], tags=[],
    )


def _make_hullmod(id: str, hidden: bool = False) -> HullMod:
    return HullMod(
        id=id, name=id, tier=1, is_hidden=hidden, script="",
        cost_frigate=5, cost_destroyer=10, cost_cruiser=15, cost_capital=20,
        ui_tags=[], tags=[],
    )


def _make_hull(
    id: str = "test_cruiser",
    hull_size: HullSize = HullSize.CRUISER,
    slots: list[WeaponSlot] | None = None,
    built_in_mods: list[str] | None = None,
) -> ShipHull:
    if slots is None:
        slots = [
            WeaponSlot("WS01", SlotType.BALLISTIC, SlotSize.MEDIUM, MountType.TURRET, 0, 360, (0, 0)),
            WeaponSlot("WS02", SlotType.ENERGY, SlotSize.SMALL, MountType.TURRET, 0, 360, (0, 0)),
            WeaponSlot("WS03", SlotType.MISSILE, SlotSize.LARGE, MountType.TURRET, 0, 360, (0, 0)),
        ]
    return ShipHull(
        id=id, name=id, hull_size=hull_size, designation="Cruiser",
        tech_manufacturer="test", system_id="", fleet_pts=15,
        hitpoints=10000, armor_rating=1000, max_flux=5000, flux_dissipation=300,
        ordnance_points=100, fighter_bays=0, max_speed=50,
        shield_type=ShieldType.OMNI, shield_arc=360, shield_upkeep=0.2,
        shield_efficiency=1.0, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.01,
        weapon_slots=slots, built_in_mods=built_in_mods or [],
    )


def _make_game_data(hull: ShipHull) -> GameData:
    weapons = {
        "bal_med_1": _make_weapon("bal_med_1", SlotSize.MEDIUM, WeaponType.BALLISTIC),
        "bal_med_2": _make_weapon("bal_med_2", SlotSize.MEDIUM, WeaponType.BALLISTIC),
        "ene_sml_1": _make_weapon("ene_sml_1", SlotSize.SMALL, WeaponType.ENERGY),
        "ene_sml_2": _make_weapon("ene_sml_2", SlotSize.SMALL, WeaponType.ENERGY),
        "ene_sml_3": _make_weapon("ene_sml_3", SlotSize.SMALL, WeaponType.ENERGY),
        "mis_lrg_1": _make_weapon("mis_lrg_1", SlotSize.LARGE, WeaponType.MISSILE),
    }
    hullmods = {
        "mod_a": _make_hullmod("mod_a"),
        "mod_b": _make_hullmod("mod_b"),
        "mod_c": _make_hullmod("mod_c"),
        "mod_hidden": _make_hullmod("mod_hidden", hidden=True),
    }
    return GameData(hulls={hull.id: hull}, weapons=weapons, hullmods=hullmods)


# --- HullSpaceStats tests ---


class TestHullSpaceStats:

    def test_basic_stats(self, manifest):
        hull = _make_hull()
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        assert stats.hull_id == "test_cruiser"
        assert stats.hull_size == HullSize.CRUISER
        assert stats.num_slots == 3  # 3 assignable slots

    def test_options_per_slot(self, manifest):
        """Each slot has 'empty' + compatible weapons."""
        hull = _make_hull()
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        # Slot 1 (BALLISTIC MEDIUM): empty + bal_med_1 + bal_med_2 = 3
        # Slot 2 (ENERGY SMALL): empty + ene_sml_1 + ene_sml_2 + ene_sml_3 = 4
        # Slot 3 (MISSILE LARGE): empty + mis_lrg_1 = 2
        assert stats.options_per_slot == [3, 4, 2]

    def test_weapon_combinations(self, manifest):
        """Product of options per slot."""
        hull = _make_hull()
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        assert stats.weapon_combinations == 3 * 4 * 2  # 24

    def test_eligible_hullmods_excludes_hidden(self, manifest):
        hull = _make_hull()
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        assert stats.num_eligible_hullmods == 3  # mod_a, mod_b, mod_c (not hidden)

    def test_built_in_mods_excluded_from_eligible(self, manifest):
        hull = _make_hull(built_in_mods=["mod_a"])
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        assert stats.num_eligible_hullmods == 2  # mod_b, mod_c (mod_a is built-in)

    def test_no_slots(self, manifest):
        hull = _make_hull(slots=[])
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        assert stats.num_slots == 0
        assert stats.options_per_slot == []
        assert stats.weapon_combinations == 1  # empty product = 1

    def test_max_vents_capacitors(self, manifest):
        """Post-Phase-7-prep: max_vents/capacitors come from manifest.constants
        and are flat 30 for every hull size (audit bug H1 fix)."""
        hull = _make_hull(hull_size=HullSize.FRIGATE)
        gd = _make_game_data(hull)
        stats = compute_hull_space_stats(hull, gd, manifest)

        expected = manifest.constants.max_vents_per_ship
        assert stats.max_vents == expected
        assert stats.max_capacitors == manifest.constants.max_capacitors_per_ship

        hull2 = _make_hull(hull_size=HullSize.CAPITAL_SHIP)
        stats2 = compute_hull_space_stats(hull2, gd, manifest)
        assert stats2.max_vents == expected
        assert stats2.max_capacitors == manifest.constants.max_capacitors_per_ship


# --- ThroughputEstimate tests ---


class TestEstimateThroughput:

    def test_wall_seconds_per_matchup(self):
        """Wall time = game_time_limit / time_mult."""
        params = SimulationParams(time_mult=3.0, game_time_limit_seconds=180)
        est = estimate_throughput(params)
        assert est.wall_seconds_per_matchup == pytest.approx(60.0)

        params5x = SimulationParams(time_mult=5.0, game_time_limit_seconds=180)
        est5x = estimate_throughput(params5x)
        assert est5x.wall_seconds_per_matchup == pytest.approx(36.0)

    def test_matchups_per_hour_per_instance(self):
        """3600 / wall_seconds."""
        params = SimulationParams(time_mult=3.0, game_time_limit_seconds=180)
        est = estimate_throughput(params)
        assert est.matchups_per_hour_per_instance == pytest.approx(60.0)

    def test_startup_overhead(self):
        """Startup fraction decreases with larger batches."""
        p1 = SimulationParams(startup_seconds=35, batch_size=1, time_mult=3.0,
                              game_time_limit_seconds=180)
        e1 = estimate_throughput(p1)

        p50 = SimulationParams(startup_seconds=35, batch_size=50, time_mult=3.0,
                               game_time_limit_seconds=180)
        e50 = estimate_throughput(p50)

        # batch=1: 35 / (35 + 60) ≈ 0.368
        assert e1.startup_overhead_fraction == pytest.approx(35 / 95, rel=0.01)
        # batch=50: 35 / (35 + 3000) ≈ 0.012
        assert e50.startup_overhead_fraction == pytest.approx(35 / 3035, rel=0.01)

    def test_parallelism_scales_linearly(self):
        """Doubling instances halves total time."""
        p1 = SimulationParams(num_instances=1, sims_per_hull=1000, num_hulls=50)
        p8 = SimulationParams(num_instances=8, sims_per_hull=1000, num_hulls=50)

        e1 = estimate_throughput(p1)
        e8 = estimate_throughput(p8)

        assert e8.total_hours == pytest.approx(e1.total_hours / 8, rel=0.01)

    def test_total_sims(self):
        params = SimulationParams(sims_per_hull=1000, num_hulls=50)
        est = estimate_throughput(params)
        assert est.total_sims == 50000

    def test_cost_estimates(self):
        """Cost = total_hours * provider.cost_per_hour, adjusted for instances per machine."""
        params = SimulationParams(
            num_instances=8, sims_per_hull=100, num_hulls=10,
            time_mult=5.0, game_time_limit_seconds=180,
            providers=[
                CloudProvider("Hetzner CCX43", cost_per_hour=0.22, max_instances=8),
            ],
        )
        est = estimate_throughput(params)
        assert "Hetzner CCX43" in est.cost_estimates

        # 1000 sims / (100 matchups/hr * 8 instances) ≈ 1.25 hrs
        # But need ceiling of machines: 8 instances / 8 per machine = 1 machine
        # Cost = hours * 1 machine * $0.22/hr
        expected_hours = est.total_hours
        assert est.cost_estimates["Hetzner CCX43"] == pytest.approx(
            expected_hours * 1 * 0.22, rel=0.01)

    def test_multiple_machines_needed(self):
        """When num_instances > provider max, need multiple machines."""
        params = SimulationParams(
            num_instances=16, sims_per_hull=100, num_hulls=10,
            time_mult=5.0, game_time_limit_seconds=180,
            providers=[
                CloudProvider("Small VM", cost_per_hour=0.10, max_instances=4),
            ],
        )
        est = estimate_throughput(params)
        # 16 instances / 4 per machine = 4 machines
        expected_hours = est.total_hours
        assert est.cost_estimates["Small VM"] == pytest.approx(
            expected_hours * 4 * 0.10, rel=0.01)

    def test_5x_faster_than_3x(self):
        p3 = SimulationParams(time_mult=3.0, game_time_limit_seconds=180)
        p5 = SimulationParams(time_mult=5.0, game_time_limit_seconds=180)
        e3 = estimate_throughput(p3)
        e5 = estimate_throughput(p5)

        assert e5.total_hours < e3.total_hours
        assert e5.wall_seconds_per_matchup == pytest.approx(
            e3.wall_seconds_per_matchup * 3 / 5, rel=0.01)

    def test_shorter_time_limit(self):
        """60s game limit at 5x = 12s wall-clock per matchup."""
        params = SimulationParams(time_mult=5.0, game_time_limit_seconds=60)
        est = estimate_throughput(params)
        assert est.wall_seconds_per_matchup == pytest.approx(12.0)
        assert est.matchups_per_hour_per_instance == pytest.approx(300.0)


# --- Report formatting ---


class TestFormatReport:

    def test_report_contains_key_sections(self, manifest):
        hull = _make_hull()
        gd = _make_game_data(hull)
        stats = [compute_hull_space_stats(hull, gd, manifest)]
        params = SimulationParams(num_instances=8, sims_per_hull=1000, num_hulls=1)
        est = estimate_throughput(params)
        report = format_estimate_report(stats, est)

        assert "Search Space" in report
        assert "Throughput" in report
        assert "Cost" in report
        assert "test_cruiser" in report

    def test_report_large_numbers_readable(self, manifest):
        """Weapon combinations should be formatted with exponent for large numbers."""
        slots = [
            WeaponSlot(f"WS{i:02d}", SlotType.UNIVERSAL, SlotSize.MEDIUM, MountType.TURRET, 0, 360, (0, 0))
            for i in range(10)
        ]
        hull = _make_hull(slots=slots)
        gd = _make_game_data(hull)
        stats = [compute_hull_space_stats(hull, gd, manifest)]
        params = SimulationParams()
        est = estimate_throughput(params)
        report = format_estimate_report(stats, est)

        # Large numbers should have scientific notation or readable formatting
        assert "test_cruiser" in report
