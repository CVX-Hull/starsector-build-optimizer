"""Tests for result parser — JSON ↔ Python dataclass conversion."""

import json

import pytest

from starsector_optimizer.models import (
    BuildSpec,
    CombatResult,
    DamageBreakdown,
    EngineStats,
    MatchupConfig,
    ShipCombatResult,
)
from starsector_optimizer.result_parser import (
    parse_combat_result,
    parse_results_file,
    write_queue_file,
)


SAMPLE_SHIP_RESULT = {
    "fleet_member_id": "uuid-1",
    "variant_id": "eagle_opt_test",
    "hull_id": "eagle",
    "destroyed": True,
    "hull_fraction": 0.0,
    "armor_fraction": 0.36,
    "cr_remaining": 0.7,
    "peak_time_remaining": 472.0,
    "disabled_weapons": 8,
    "flameouts": 0,
    "damage_dealt": {"shield": 4300.0, "armor": 0.0, "hull": 0.0, "emp": 0.0},
    "damage_taken": {"shield": 0.0, "armor": 5098.0, "hull": 24588.0, "emp": 0.0},
    "flux_stats": {"curr_flux": 0.0, "hard_flux": 0.0, "max_flux": 12900.0, "overload_count": 2},
}

SAMPLE_RESULT = {
    "matchup_id": "eval_001",
    "winner": "ENEMY",
    "duration_seconds": 56.8,
    "player_ships": [SAMPLE_SHIP_RESULT],
    "enemy_ships": [
        {
            "fleet_member_id": "uuid-2",
            "variant_id": "dominator_Assault",
            "hull_id": "dominator",
            "destroyed": False,
            "hull_fraction": 1.0,
            "armor_fraction": 1.0,
            "cr_remaining": 0.7,
            "peak_time_remaining": 532.0,
            "disabled_weapons": 0,
            "flameouts": 0,
            "damage_dealt": {"shield": 0.0, "armor": 5098.0, "hull": 24588.0, "emp": 0.0},
            "damage_taken": {"shield": 4300.0, "armor": 0.0, "hull": 0.0, "emp": 0.0},
            "flux_stats": {"curr_flux": 0.0, "hard_flux": 0.0, "max_flux": 13000.0, "overload_count": 0},
        }
    ],
    "aggregate": {
        "player_total_damage_dealt": 4300.0,
        "enemy_total_damage_dealt": 29686.0,
        "player_ships_destroyed": 1,
        "enemy_ships_destroyed": 0,
        "player_ships_retreated": 0,
        "enemy_ships_retreated": 0,
    },
}


class TestParseCombatResult:

    def test_basic_fields(self):
        result = parse_combat_result(SAMPLE_RESULT)
        assert isinstance(result, CombatResult)
        assert result.matchup_id == "eval_001"
        assert result.winner == "ENEMY"
        assert result.duration_seconds == 56.8

    def test_player_ships(self):
        result = parse_combat_result(SAMPLE_RESULT)
        assert len(result.player_ships) == 1
        ship = result.player_ships[0]
        assert isinstance(ship, ShipCombatResult)
        assert ship.fleet_member_id == "uuid-1"
        assert ship.variant_id == "eagle_opt_test"
        assert ship.hull_id == "eagle"
        assert ship.destroyed is True
        assert ship.hull_fraction == 0.0
        assert ship.armor_fraction == 0.36
        assert ship.cr_remaining == 0.7
        assert ship.disabled_weapons == 8

    def test_damage_breakdown(self):
        result = parse_combat_result(SAMPLE_RESULT)
        ship = result.player_ships[0]
        assert isinstance(ship.damage_dealt, DamageBreakdown)
        assert ship.damage_dealt.shield == 4300.0
        assert ship.damage_dealt.armor == 0.0
        assert isinstance(ship.damage_taken, DamageBreakdown)
        assert ship.damage_taken.hull == 24588.0

    def test_overload_count_from_flux_stats(self):
        """overload_count is nested under flux_stats in JSON but top-level in dataclass."""
        result = parse_combat_result(SAMPLE_RESULT)
        assert result.player_ships[0].overload_count == 2
        assert result.enemy_ships[0].overload_count == 0

    def test_aggregate_fields(self):
        result = parse_combat_result(SAMPLE_RESULT)
        assert result.player_ships_destroyed == 1
        assert result.enemy_ships_destroyed == 0
        assert result.player_ships_retreated == 0
        assert result.enemy_ships_retreated == 0

    def test_enemy_ships(self):
        result = parse_combat_result(SAMPLE_RESULT)
        assert len(result.enemy_ships) == 1
        assert result.enemy_ships[0].destroyed is False
        assert result.enemy_ships[0].hull_fraction == 1.0

    def test_multiple_ships_per_side(self):
        """Handle matchups with multiple ships per side."""
        data = dict(SAMPLE_RESULT)
        data["player_ships"] = [SAMPLE_SHIP_RESULT, SAMPLE_SHIP_RESULT]
        result = parse_combat_result(data)
        assert len(result.player_ships) == 2


class TestParseResultsFile:

    def test_single_result(self, tmp_path):
        path = tmp_path / "combat_harness_results.json.data"
        path.write_text(json.dumps([SAMPLE_RESULT], indent=2))
        results = parse_results_file(path)
        assert len(results) == 1
        assert results[0].matchup_id == "eval_001"

    def test_multiple_results(self, tmp_path):
        second = dict(SAMPLE_RESULT)
        second["matchup_id"] = "eval_002"
        second["winner"] = "PLAYER"
        path = tmp_path / "results.json.data"
        path.write_text(json.dumps([SAMPLE_RESULT, second], indent=2))
        results = parse_results_file(path)
        assert len(results) == 2
        assert results[0].matchup_id == "eval_001"
        assert results[1].matchup_id == "eval_002"
        assert results[1].winner == "PLAYER"

    def test_empty_array(self, tmp_path):
        path = tmp_path / "empty.json.data"
        path.write_text("[]")
        results = parse_results_file(path)
        assert results == []


class TestWriteQueueFile:

    @staticmethod
    def _build_spec(variant_id="eagle_test", hull_id="eagle"):
        return BuildSpec(
            variant_id=variant_id,
            hull_id=hull_id,
            weapon_assignments={"WS1": "heavymauler"},
            hullmods=("heavyarmor",),
            flux_vents=15,
            flux_capacitors=10,
        )

    def test_single_matchup(self, tmp_path):
        mc = MatchupConfig(
            matchup_id="test_001",
            player_builds=(self._build_spec(),),
            enemy_variants=("dominator_Assault",),
        )
        path = tmp_path / "combat_harness_queue.json.data"
        write_queue_file([mc], path)

        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["matchup_id"] == "test_001"
        assert len(data[0]["player_builds"]) == 1
        assert data[0]["player_builds"][0]["variant_id"] == "eagle_test"
        assert data[0]["player_builds"][0]["hull_id"] == "eagle"
        assert data[0]["player_builds"][0]["weapon_assignments"] == {"WS1": "heavymauler"}
        assert data[0]["player_builds"][0]["hullmods"] == ["heavyarmor"]
        assert data[0]["player_builds"][0]["flux_vents"] == 15
        assert data[0]["player_builds"][0]["flux_capacitors"] == 10
        assert data[0]["enemy_variants"] == ["dominator_Assault"]
        assert data[0]["time_limit_seconds"] == 300.0
        assert data[0]["time_mult"] == 3.0

    def test_multiple_matchups(self, tmp_path):
        matchups = [
            MatchupConfig(
                matchup_id=f"test_{i:03d}",
                player_builds=(self._build_spec(),),
                enemy_variants=("dominator_Assault",),
                time_mult=5.0,
                time_limit_seconds=60,
            )
            for i in range(3)
        ]
        path = tmp_path / "queue.json.data"
        write_queue_file(matchups, path)

        data = json.loads(path.read_text())
        assert len(data) == 3
        assert data[2]["matchup_id"] == "test_002"
        assert data[0]["time_mult"] == 5.0
        assert data[0]["time_limit_seconds"] == 60

    def test_custom_map_dimensions(self, tmp_path):
        mc = MatchupConfig(
            matchup_id="test_map",
            player_builds=(self._build_spec("wolf_test", "wolf"),),
            enemy_variants=("lasher_CS",),
            map_width=16000,
            map_height=12000,
        )
        path = tmp_path / "queue.json.data"
        write_queue_file([mc], path)

        data = json.loads(path.read_text())
        assert data[0]["map_width"] == 16000
        assert data[0]["map_height"] == 12000

    def test_round_trip_with_parser(self, tmp_path):
        """Write queue → simulate Java output → parse results should be consistent IDs."""
        mc = MatchupConfig(
            matchup_id="roundtrip_001",
            player_builds=(self._build_spec("eagle_test"), self._build_spec("wolf_test", "wolf")),
            enemy_variants=("dominator_Assault",),
        )
        queue_path = tmp_path / "queue.json.data"
        write_queue_file([mc], queue_path)

        queue_data = json.loads(queue_path.read_text())
        assert queue_data[0]["matchup_id"] == "roundtrip_001"
        assert len(queue_data[0]["player_builds"]) == 2
        assert queue_data[0]["player_builds"][0]["variant_id"] == "eagle_test"
        assert queue_data[0]["player_builds"][1]["variant_id"] == "wolf_test"


class TestParseSetupStats:
    """Phase 5D — tolerant parsing of the new Java setup_stats block."""

    def test_populates_engine_stats(self):
        """Post-Phase-7-prep: 6-field setup_stats block (added 3 fields:
        eff_hull_hp_pct / ballistic_range_bonus / shield_damage_taken_mult).
        """
        data = dict(SAMPLE_RESULT)
        data["setup_stats"] = {
            "player": {
                "eff_max_flux": 12000.0,
                "eff_flux_dissipation": 800.0,
                "eff_armor_rating": 1050.0,
                "eff_hull_hp_pct": 1.4,
                "ballistic_range_bonus": 300.0,
                "shield_damage_taken_mult": 0.75,
            }
        }
        result = parse_combat_result(data)
        assert isinstance(result.engine_stats, EngineStats)
        assert result.engine_stats.eff_max_flux == pytest.approx(12000.0)
        assert result.engine_stats.eff_flux_dissipation == pytest.approx(800.0)
        assert result.engine_stats.eff_armor_rating == pytest.approx(1050.0)
        assert result.engine_stats.eff_hull_hp_pct == pytest.approx(1.4)
        assert result.engine_stats.ballistic_range_bonus == pytest.approx(300.0)
        assert result.engine_stats.shield_damage_taken_mult == pytest.approx(0.75)

    def test_missing_block_is_none_no_warn(self):
        """Absent setup_stats → engine_stats=None, NO warning (legitimate pre-5D)."""
        import warnings as _warnings

        # SAMPLE_RESULT has no setup_stats
        with _warnings.catch_warnings():
            _warnings.simplefilter("error")  # fail if any warning raised
            result = parse_combat_result(SAMPLE_RESULT)
        assert result.engine_stats is None

    def test_missing_player_key_warns(self):
        data = dict(SAMPLE_RESULT)
        data["setup_stats"] = {"enemy": {"eff_max_flux": 1000.0}}
        with pytest.warns(UserWarning, match="player"):
            result = parse_combat_result(data)
        assert result.engine_stats is None

    def test_missing_subkey_warns(self):
        data = dict(SAMPLE_RESULT)
        data["setup_stats"] = {
            "player": {
                "eff_max_flux": 12000.0,
                # eff_flux_dissipation missing
                "eff_armor_rating": 1050.0,
            }
        }
        with pytest.warns(UserWarning):
            result = parse_combat_result(data)
        assert result.engine_stats is None

    def test_nan_value_warns(self):
        data = dict(SAMPLE_RESULT)
        data["setup_stats"] = {
            "player": {
                "eff_max_flux": float("nan"),
                "eff_flux_dissipation": 800.0,
                "eff_armor_rating": 1050.0,
                "eff_hull_hp_pct": 1.0,
                "ballistic_range_bonus": 0.0,
                "shield_damage_taken_mult": 1.0,
            }
        }
        with pytest.warns(UserWarning, match="NaN"):
            result = parse_combat_result(data)
        assert result.engine_stats is None
