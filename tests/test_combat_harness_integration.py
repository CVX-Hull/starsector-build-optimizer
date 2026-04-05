"""Integration tests for the combat harness mod protocol.

Tests matchup.json generation and result.json parsing.
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


WORKDIR = Path(__file__).parent.parent / "game" / "starsector" / "mods" / "combat-harness" / "workdir"
VARIANT_DIR = Path(__file__).parent.parent / "game" / "starsector" / "data" / "variants"


class TestMatchupJsonGeneration:
    """Test that Python can write valid matchup.json for the Java mod."""

    def test_generate_matchup_json(self, game_data):
        """Generate a matchup.json with an optimizer variant vs vanilla opponent."""
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        variant = generate_variant(build, eagle, game_data)
        variant_id = variant["variantId"]

        matchup = {
            "matchup_id": "test_001",
            "player_variants": [variant_id],
            "enemy_variants": ["dominator_Standard"],
            "player_flagship": variant_id,
            "time_limit_seconds": 300,
            "time_mult": 3.0,
            "map_width": 24000,
            "map_height": 18000,
        }

        # Verify it's valid JSON
        json_str = json.dumps(matchup, indent=2)
        parsed = json.loads(json_str)
        assert parsed["matchup_id"] == "test_001"
        assert len(parsed["player_variants"]) == 1
        assert len(parsed["enemy_variants"]) == 1

    def test_matchup_config_dataclass_from_dict(self):
        """Verify MatchupConfig can be constructed from the same schema."""
        mc = MatchupConfig(
            matchup_id="eval_001",
            player_variants=("eagle_opt_test",),
            enemy_variants=("dominator_Standard",),
            player_flagship="eagle_opt_test",
        )
        assert mc.time_limit_seconds == 300.0
        assert mc.time_mult == 3.0

    def test_deploy_variant_and_matchup(self, game_data):
        """Generate variant file + matchup.json, write to mod workdir if it exists."""
        if not WORKDIR.exists():
            pytest.skip("Mod workdir not found (mod not deployed)")

        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        variant = generate_variant(build, eagle, game_data)
        variant_id = variant["variantId"]

        # Write variant file to game's variants directory
        if VARIANT_DIR.exists():
            variant_path = VARIANT_DIR / f"{variant_id}.variant"
            write_variant_file(variant, variant_path)
            assert variant_path.exists()

        # Write matchup.json
        matchup = {
            "matchup_id": "integration_test",
            "player_variants": [variant_id],
            "enemy_variants": ["dominator_Standard"],
            "player_flagship": variant_id,
            "time_limit_seconds": 120,
            "time_mult": 3.0,
        }
        matchup_path = WORKDIR / "matchup.json"
        matchup_path.write_text(json.dumps(matchup, indent=2))
        assert matchup_path.exists()

        # Verify it's parseable
        loaded = json.loads(matchup_path.read_text())
        assert loaded["matchup_id"] == "integration_test"


class TestResultJsonParsing:
    """Test that Python can parse result.json written by the Java mod."""

    SAMPLE_RESULT = {
        "matchup_id": "eval_001",
        "winner": "PLAYER",
        "duration_seconds": 87.3,
        "player_ships": [
            {
                "fleet_member_id": "0",
                "variant_id": "eagle_opt_test",
                "hull_id": "eagle",
                "destroyed": False,
                "hull_fraction": 0.82,
                "armor_fraction": 0.45,
                "cr_remaining": 0.61,
                "peak_time_remaining": 142.0,
                "disabled_weapons": 0,
                "flameouts": 0,
                "damage_dealt": {"shield": 12450.0, "armor": 8230.0, "hull": 3100.0, "emp": 500.0},
                "damage_taken": {"shield": 6200.0, "armor": 2100.0, "hull": 1800.0, "emp": 0.0},
                "flux_stats": {"curr_flux": 2340.0, "hard_flux": 1200.0, "max_flux": 12000.0, "overload_count": 0},
            }
        ],
        "enemy_ships": [],
        "aggregate": {
            "player_total_damage_dealt": 23780.0,
            "enemy_total_damage_dealt": 10100.0,
            "player_ships_destroyed": 0,
            "enemy_ships_destroyed": 2,
            "player_ships_retreated": 0,
            "enemy_ships_retreated": 0,
        },
    }

    def test_parse_sample_result(self):
        """Parse a sample result.json into CombatResult dataclass."""
        data = self.SAMPLE_RESULT

        player_ships = []
        for s in data["player_ships"]:
            player_ships.append(ShipCombatResult(
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
            ))

        result = CombatResult(
            matchup_id=data["matchup_id"],
            winner=data["winner"],
            duration_seconds=data["duration_seconds"],
            player_ships=tuple(player_ships),
            enemy_ships=(),
            player_ships_destroyed=data["aggregate"]["player_ships_destroyed"],
            enemy_ships_destroyed=data["aggregate"]["enemy_ships_destroyed"],
        )

        assert result.winner == "PLAYER"
        assert result.duration_seconds == 87.3
        assert len(result.player_ships) == 1
        assert result.player_ships[0].hull_fraction == 0.82
        assert result.player_ships[0].damage_dealt.shield == 12450.0
        assert result.enemy_ships_destroyed == 2

    def test_result_json_round_trip(self, tmp_path):
        """Write sample result to file, read back, verify."""
        result_path = tmp_path / "result.json"
        result_path.write_text(json.dumps(self.SAMPLE_RESULT, indent=2))

        loaded = json.loads(result_path.read_text())
        assert loaded["matchup_id"] == "eval_001"
        assert loaded["winner"] == "PLAYER"
        assert len(loaded["player_ships"]) == 1
        assert loaded["player_ships"][0]["hull_fraction"] == 0.82

    def test_validate_result_schema(self):
        """Verify all required fields are present in the sample."""
        data = self.SAMPLE_RESULT
        assert "matchup_id" in data
        assert "winner" in data
        assert data["winner"] in ("PLAYER", "ENEMY", "TIMEOUT")
        assert "duration_seconds" in data
        assert data["duration_seconds"] > 0
        assert "player_ships" in data
        assert "enemy_ships" in data
        assert "aggregate" in data

        for ship in data["player_ships"]:
            assert 0.0 <= ship["hull_fraction"] <= 1.0
            assert 0.0 <= ship["armor_fraction"] <= 1.0
            assert ship["disabled_weapons"] >= 0
            assert ship["flameouts"] >= 0
            assert "damage_dealt" in ship
            assert "damage_taken" in ship
            assert "flux_stats" in ship
