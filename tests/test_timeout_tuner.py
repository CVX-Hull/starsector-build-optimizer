"""Tests for timeout tuner — data-driven priors, survival analysis, persistence."""

import json
from pathlib import Path

import pytest

from starsector_optimizer.models import (
    CombatResult,
    DamageBreakdown,
    GameData,
    HullMod,
    HullSize,
    MatchupConfig,
    MountType,
    ShieldType,
    ShipCombatResult,
    ShipHull,
    SlotSize,
    SlotType,
    Weapon,
    WeaponSlot,
    WeaponType,
    DamageType,
)
from starsector_optimizer.timeout_tuner import TimeoutTuner


# --- Fixtures ---


def _make_hull(id: str, hull_size: HullSize, speed: float, hp: float, armor: float,
               n_slots: int = 5) -> ShipHull:
    slots = [
        WeaponSlot(f"WS{i:02d}", SlotType.BALLISTIC, SlotSize.MEDIUM,
                   MountType.TURRET, 0, 360, (0, 0))
        for i in range(n_slots)
    ]
    return ShipHull(
        id=id, name=id, hull_size=hull_size, designation="Test",
        tech_manufacturer="test", system_id="", fleet_pts=10,
        hitpoints=hp, armor_rating=armor, max_flux=5000, flux_dissipation=300,
        ordnance_points=100, fighter_bays=0, max_speed=speed,
        shield_type=ShieldType.OMNI, shield_arc=360, shield_upkeep=0.2,
        shield_efficiency=1.0, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.01,
        weapon_slots=slots,
    )


def _make_weapon(dps: float = 50) -> Weapon:
    return Weapon(
        id="test_wpn", name="test", size=SlotSize.MEDIUM, weapon_type=WeaponType.BALLISTIC,
        damage_per_shot=100, damage_per_second=dps, damage_type=DamageType.KINETIC,
        emp=0, flux_per_shot=50, flux_per_second=25, range=600,
        op_cost=5, chargeup=0, chargedown=0, burst_size=1, burst_delay=0,
        ammo=-1, ammo_per_sec=0, proj_speed=800, turn_rate=0,
        hints=[], tags=[],
    )


def _make_game_data(hulls: dict[str, ShipHull]) -> GameData:
    return GameData(
        hulls=hulls,
        weapons={"test_wpn": _make_weapon(50)},
        hullmods={},
    )


def _make_combat_result(matchup_id: str, winner: str, duration: float) -> CombatResult:
    return CombatResult(
        matchup_id=matchup_id, winner=winner, duration_seconds=duration,
        player_ships=(ShipCombatResult(
            fleet_member_id="p1", variant_id="test_v", hull_id="test_hull",
            destroyed=winner == "ENEMY", hull_fraction=0.5, armor_fraction=0.5,
            cr_remaining=0.7, peak_time_remaining=100, disabled_weapons=0,
            flameouts=0, damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
            overload_count=0,
        ),),
        enemy_ships=(ShipCombatResult(
            fleet_member_id="e1", variant_id="enemy_v", hull_id="enemy_hull",
            destroyed=winner == "PLAYER", hull_fraction=0.3, armor_fraction=0.3,
            cr_remaining=0.6, peak_time_remaining=80, disabled_weapons=0,
            flameouts=0, damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
            overload_count=0,
        ),),
        player_ships_destroyed=1 if winner == "ENEMY" else 0,
        enemy_ships_destroyed=1 if winner == "PLAYER" else 0,
        player_ships_retreated=0, enemy_ships_retreated=0,
    )


@pytest.fixture
def game_data():
    hulls = {
        "frigate_fast": _make_hull("frigate_fast", HullSize.FRIGATE, 150, 3000, 300, 3),
        "destroyer_mid": _make_hull("destroyer_mid", HullSize.DESTROYER, 90, 6000, 600, 6),
        "cruiser_slow": _make_hull("cruiser_slow", HullSize.CRUISER, 60, 15000, 1200, 10),
        "capital_heavy": _make_hull("capital_heavy", HullSize.CAPITAL_SHIP, 30, 30000, 2000, 14),
    }
    return _make_game_data(hulls)


# --- Cold start tests ---


class TestColdStartPriors:

    def test_derived_from_game_data(self, game_data, tmp_path):
        """Uses speeds, EHP, DPS from GameData — not magic numbers."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(
            matchup_id="test", player_variants=("cruiser_slow",),
            enemy_variants=("cruiser_slow",),
        )
        timeout = tuner.predict_timeout(mc, game_data)
        assert timeout > 0
        assert timeout < 600  # capped at 10 minutes

    def test_scales_with_hull_size(self, game_data, tmp_path):
        """Frigate pair should have shorter timeout than capital pair."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc_frg = MatchupConfig(matchup_id="frg", player_variants=("frigate_fast",),
                               enemy_variants=("frigate_fast",))
        mc_cap = MatchupConfig(matchup_id="cap", player_variants=("capital_heavy",),
                               enemy_variants=("capital_heavy",))
        t_frg = tuner.predict_timeout(mc_frg, game_data)
        t_cap = tuner.predict_timeout(mc_cap, game_data)
        assert t_frg < t_cap

    def test_no_magic_numbers(self, tmp_path):
        """Changing game data changes priors."""
        fast_hulls = {
            "fast": _make_hull("fast", HullSize.CRUISER, 120, 10000, 800, 8),
        }
        slow_hulls = {
            "slow": _make_hull("slow", HullSize.CRUISER, 30, 10000, 800, 8),
        }
        gd_fast = _make_game_data(fast_hulls)
        gd_slow = _make_game_data(slow_hulls)
        tuner = TimeoutTuner(data_dir=tmp_path)

        mc = MatchupConfig(matchup_id="t", player_variants=("fast",), enemy_variants=("fast",))
        t_fast = tuner.predict_timeout(mc, gd_fast)

        mc2 = MatchupConfig(matchup_id="t", player_variants=("slow",), enemy_variants=("slow",))
        t_slow = tuner.predict_timeout(mc2, gd_slow)

        # Slower ships → longer timeout (more approach time)
        assert t_slow > t_fast


# --- Recording tests ---


class TestRecording:

    def test_record_completed(self, game_data, tmp_path):
        """PLAYER/ENEMY → completed=True."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="rec1", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",))
        result = _make_combat_result("rec1", "PLAYER", 72.5)
        tuner.record_result(mc, result, game_data)

        log_path = tmp_path / "evaluation_log.jsonl"
        assert log_path.exists()
        line = json.loads(log_path.read_text().strip())
        assert line["completed"] is True
        assert line["duration"] == 72.5

    def test_record_censored(self, game_data, tmp_path):
        """TIMEOUT/STOPPED → completed=False."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="rec2", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",), time_limit_seconds=180)
        result = _make_combat_result("rec2", "TIMEOUT", 180.0)
        tuner.record_result(mc, result, game_data)

        line = json.loads((tmp_path / "evaluation_log.jsonl").read_text().strip())
        assert line["completed"] is False

    def test_record_features(self, game_data, tmp_path):
        """Hull sizes and ship counts extracted."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="rec3",
                           player_variants=("frigate_fast", "frigate_fast"),
                           enemy_variants=("capital_heavy",))
        result = _make_combat_result("rec3", "ENEMY", 100.0)
        tuner.record_result(mc, result, game_data)

        line = json.loads((tmp_path / "evaluation_log.jsonl").read_text().strip())
        assert line["ship_counts"] == [2, 1]


# --- Blended transition tests ---


class TestBlendedTransition:

    def test_blend_weight_zero(self, game_data, tmp_path):
        """0 observations → pure prior."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="t", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",))
        timeout = tuner.predict_timeout(mc, game_data)
        prior = TimeoutTuner.compute_default_timeout(
            game_data.hulls["cruiser_slow"], game_data.hulls["cruiser_slow"], game_data)
        assert timeout == pytest.approx(prior, rel=0.01)

    def test_blend_weight_increases(self, game_data, tmp_path):
        """With 50 observations, prediction should differ from pure prior."""
        tuner = TimeoutTuner(data_dir=tmp_path, blend_scale=100)
        mc = MatchupConfig(matchup_id="t", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",))

        # Record 60 results (enough to trigger refit at threshold=50)
        for i in range(60):
            result = _make_combat_result(f"train_{i}", "PLAYER", 40.0 + i * 0.5)
            tuner.record_result(mc, result, game_data)

        # After recording, prediction should blend prior with model
        # (exact value depends on lifelines fit, just verify it changed)
        timeout_after = tuner.predict_timeout(mc, game_data)
        prior = TimeoutTuner.compute_default_timeout(
            game_data.hulls["cruiser_slow"], game_data.hulls["cruiser_slow"], game_data)
        # With 60 obs and blend_scale=100, weight = 0.6
        # The model should pull the prediction toward the observed data (~40-70s)
        # Prior is likely higher. So timeout_after < prior (model tightens it)
        assert timeout_after != pytest.approx(prior, rel=0.01)


# --- Persistence tests ---


class TestPersistence:

    def test_round_trip(self, game_data, tmp_path):
        """Save data, recreate tuner, predictions should be consistent."""
        tuner1 = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="t", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",))
        for i in range(10):
            result = _make_combat_result(f"p_{i}", "PLAYER", 50.0 + i)
            tuner1.record_result(mc, result, game_data)

        # Recreate tuner from same directory
        tuner2 = TimeoutTuner(data_dir=tmp_path)
        t1 = tuner1.predict_timeout(mc, game_data)
        t2 = tuner2.predict_timeout(mc, game_data)
        assert t1 == pytest.approx(t2, rel=0.01)

    def test_append_only_log(self, game_data, tmp_path):
        """New records appended, old preserved."""
        tuner = TimeoutTuner(data_dir=tmp_path)
        mc = MatchupConfig(matchup_id="t", player_variants=("cruiser_slow",),
                           enemy_variants=("cruiser_slow",))

        tuner.record_result(mc, _make_combat_result("a", "PLAYER", 50), game_data)
        tuner.record_result(mc, _make_combat_result("b", "ENEMY", 60), game_data)

        lines = (tmp_path / "evaluation_log.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["matchup_id"] == "a"
        assert json.loads(lines[1])["matchup_id"] == "b"
