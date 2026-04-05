"""Integration tests for the combat harness batch protocol.

Tests queue generation, batch result parsing, and done signal detection.
Game-launch testing is manual (see combat-harness/CLAUDE.md).
"""

import json
from pathlib import Path

import pytest

from starsector_optimizer.models import (
    CombatResult,
    DamageBreakdown,
    MatchupConfig,
    ShipCombatResult,
)
from starsector_optimizer.calibration import generate_random_build
from starsector_optimizer.variant import generate_variant, write_variant_file


SAVES_COMMON = Path(__file__).parent.parent / "game" / "starsector" / "saves" / "common"
VARIANT_DIR = Path(__file__).parent.parent / "game" / "starsector" / "data" / "variants"


class TestQueueGeneration:
    """Test that Python can write valid queue files for the Java mod."""

    def test_generate_queue_json(self, game_data):
        """Generate a queue with multiple matchup configs."""
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        variant = generate_variant(build, eagle, game_data)
        vid = variant["variantId"]

        queue = [
            {
                "matchup_id": "test_001",
                "player_variants": [vid],
                "enemy_variants": ["dominator_Assault"],
                "time_limit_seconds": 180,
                "time_mult": 3.0,
            },
            {
                "matchup_id": "test_002",
                "player_variants": [vid],
                "enemy_variants": ["enforcer_Assault"],
                "time_limit_seconds": 180,
                "time_mult": 3.0,
            },
        ]

        json_str = json.dumps(queue, indent=2)
        parsed = json.loads(json_str)
        assert len(parsed) == 2
        assert parsed[0]["matchup_id"] == "test_001"
        assert parsed[1]["matchup_id"] == "test_002"

    def test_matchup_config_construction(self):
        mc = MatchupConfig(
            matchup_id="eval_001",
            player_variants=("eagle_test",),
            enemy_variants=("dominator_Assault",),
        )
        assert mc.time_limit_seconds == 300.0
        assert mc.time_mult == 3.0

    def test_deploy_queue_file(self, game_data):
        """Write queue + variant files to saves/common/ with .data extension."""
        if not SAVES_COMMON.exists():
            pytest.skip("saves/common/ not found")

        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        variant = generate_variant(build, eagle, game_data)
        vid = variant["variantId"]

        if VARIANT_DIR.exists():
            write_variant_file(variant, VARIANT_DIR / f"{vid}.variant")

        queue = [
            {
                "matchup_id": "integration_test",
                "player_variants": [vid],
                "enemy_variants": ["dominator_Assault"],
                "time_limit_seconds": 120,
                "time_mult": 3.0,
            },
        ]

        queue_path = SAVES_COMMON / "combat_harness_queue.json.data"
        queue_path.write_text(json.dumps(queue, indent=2))
        assert queue_path.exists()

        loaded = json.loads(queue_path.read_text())
        assert len(loaded) == 1
        assert loaded[0]["matchup_id"] == "integration_test"


class TestBatchResultParsing:
    """Test that Python can parse batch results written by the Java mod."""

    SAMPLE_RESULTS = [
        {
            "matchup_id": "eval_001",
            "winner": "ENEMY",
            "duration_seconds": 56.8,
            "player_ships": [
                {
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
                    "flux_stats": {"curr_flux": 0.0, "hard_flux": 0.0, "max_flux": 12900.0, "overload_count": 0},
                }
            ],
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
        },
    ]

    def _parse_result(self, data: dict) -> CombatResult:
        """Parse a single result dict into CombatResult."""
        player_ships = tuple(
            ShipCombatResult(
                fleet_member_id=s["fleet_member_id"],
                variant_id=s["variant_id"],
                hull_id=s["hull_id"],
                destroyed=s["destroyed"],
                hull_fraction=s["hull_fraction"],
                armor_fraction=s["armor_fraction"],
                cr_remaining=s["cr_remaining"],
                peak_time_remaining=s["peak_time_remaining"],
                disabled_weapons=s["disabled_weapons"],
                flameouts=s["flameouts"],
                damage_dealt=DamageBreakdown(**s["damage_dealt"]),
                damage_taken=DamageBreakdown(**s["damage_taken"]),
                overload_count=s["flux_stats"]["overload_count"],
            )
            for s in data["player_ships"]
        )
        enemy_ships = tuple(
            ShipCombatResult(
                fleet_member_id=s["fleet_member_id"],
                variant_id=s["variant_id"],
                hull_id=s["hull_id"],
                destroyed=s["destroyed"],
                hull_fraction=s["hull_fraction"],
                armor_fraction=s["armor_fraction"],
                cr_remaining=s["cr_remaining"],
                peak_time_remaining=s["peak_time_remaining"],
                disabled_weapons=s["disabled_weapons"],
                flameouts=s["flameouts"],
                damage_dealt=DamageBreakdown(**s["damage_dealt"]),
                damage_taken=DamageBreakdown(**s["damage_taken"]),
                overload_count=s["flux_stats"]["overload_count"],
            )
            for s in data["enemy_ships"]
        )
        return CombatResult(
            matchup_id=data["matchup_id"],
            winner=data["winner"],
            duration_seconds=data["duration_seconds"],
            player_ships=player_ships,
            enemy_ships=enemy_ships,
            player_ships_destroyed=data["aggregate"]["player_ships_destroyed"],
            enemy_ships_destroyed=data["aggregate"]["enemy_ships_destroyed"],
            player_ships_retreated=data["aggregate"]["player_ships_retreated"],
            enemy_ships_retreated=data["aggregate"]["enemy_ships_retreated"],
        )

    def test_parse_batch_results(self):
        results = [self._parse_result(r) for r in self.SAMPLE_RESULTS]
        assert len(results) == 1
        assert results[0].winner == "ENEMY"
        assert results[0].player_ships[0].destroyed is True
        assert results[0].enemy_ships[0].hull_fraction == 1.0

    def test_results_round_trip(self, tmp_path):
        results_path = tmp_path / "combat_harness_results.json.data"
        results_path.write_text(json.dumps(self.SAMPLE_RESULTS, indent=2))

        loaded = json.loads(results_path.read_text())
        assert len(loaded) == 1
        assert loaded[0]["matchup_id"] == "eval_001"

    def test_done_signal_detection(self, tmp_path):
        done_path = tmp_path / "combat_harness_done.data"
        assert not done_path.exists()

        done_path.write_text("1712345678000")
        assert done_path.exists()

    def test_validate_result_schema(self):
        for data in self.SAMPLE_RESULTS:
            assert data["winner"] in ("PLAYER", "ENEMY", "TIMEOUT")
            assert data["duration_seconds"] > 0
            for ship in data["player_ships"] + data["enemy_ships"]:
                assert 0.0 <= ship["hull_fraction"] <= 1.0
                assert 0.0 <= ship["armor_fraction"] <= 1.0
                assert ship["disabled_weapons"] >= 0
