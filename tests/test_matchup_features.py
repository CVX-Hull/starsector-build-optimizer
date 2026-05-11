"""Tests for Phase 7 flat matchup feature extraction."""

import json
from pathlib import Path

import pytest

from starsector_optimizer.models import Build
from starsector_optimizer.matchup_features import (
    EMPTY_SENTINEL,
    FEATURE_SCHEMA_VERSION,
    build_feature_row,
    matchup_feature_row,
    opponent_feature_row,
)


def _hammerhead_build() -> Build:
    return Build(
        hull_id="hammerhead",
        weapon_assignments={
            "WS 001": "heavyac",
            "WS 002": "heavymortar",
            "WS 003": "harpoon",
            "WS 004": None,
            "WS 005": "vulcan",
            "WS 006": "lrpdlaser",
            "WS 007": None,
            "WS 008": "lightag",
        },
        hullmods=frozenset({"armoredweapons", "fluxcoil"}),
        flux_vents=5,
        flux_capacitors=3,
    )


class TestBuildFeatureRow:
    def test_contains_core_hull_weapon_and_scorer_features(self, game_data, manifest):
        hull = game_data.hulls["hammerhead"]
        row = build_feature_row(_hammerhead_build(), hull, game_data, manifest)

        assert row["build_hull_id"] == "hammerhead"
        assert row["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert row["build_hull_size"] == hull.hull_size.value
        assert row["build_weapon_count"] == 6
        assert row["build_empty_slot_count"] == 2
        assert row["build_hullmod_count"] == 2
        assert row["build_flux_vents"] == 5
        assert row["build_flux_capacitors"] == 3
        assert row["build_total_dps"] > 0
        assert row["build_scorer_total_dps"] > 0
        assert "build_damage_kinetic_dps" in row
        assert "build_slot_small_count" in row
        assert row["build_slot_00_slot_id"]
        assert row["build_slot_00_slot_type"]
        assert "build_slot_00_angle_sin" in row
        assert "build_slot_00_arc_fraction" in row
        assert "build_slot_00_weapon_id" in row
        assert "build_hullmod__armoredweapons" in row
        assert "build_small_pd_count" in row

    def test_unknown_weapon_is_ignored_not_crashed(self, game_data, manifest):
        hull = game_data.hulls["hammerhead"]
        build = Build(
            hull_id="hammerhead",
            weapon_assignments={"WS 001": "missing_weapon"},
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )
        row = build_feature_row(build, hull, game_data, manifest)
        assert row["build_weapon_count"] == 0
        assert row["build_unknown_weapon_count"] == 1
        assert row["build_slot_00_weapon_id"] == "UNKNOWN"

    def test_empty_slots_use_sentinel(self, game_data, manifest):
        hull = game_data.hulls["hammerhead"]
        build = Build(
            hull_id="hammerhead",
            weapon_assignments={slot.id: None for slot in hull.weapon_slots},
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )

        row = build_feature_row(build, hull, game_data, manifest)

        assert EMPTY_SENTINEL in {
            value for key, value in row.items() if key.endswith("_weapon_id")
        }


class TestOpponentFeatureRow:
    def test_loads_stock_variant_features(self, game_dir, game_data):
        row = opponent_feature_row("enforcer_Balanced", game_dir, game_data)

        assert row["opponent_variant_id"] == "enforcer_Balanced"
        assert row["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert row["opponent_hull_id"] == "enforcer"
        assert row["opponent_weapon_count"] > 0
        assert row["opponent_hull_size"] == game_data.hulls["enforcer"].hull_size.value

    def test_missing_variant_raises(self, game_dir, game_data):
        with pytest.raises(FileNotFoundError):
            opponent_feature_row("not_a_real_variant", game_dir, game_data)

    def test_unknown_variant_hull_raises(self, tmp_path, game_data):
        variants = tmp_path / "data" / "variants"
        variants.mkdir(parents=True)
        (variants / "bad.variant").write_text(json.dumps({
            "variantId": "bad",
            "hullId": "not_a_real_hull",
            "weaponGroups": [],
        }))

        with pytest.raises(ValueError, match="unknown hull"):
            opponent_feature_row("bad", tmp_path, game_data)

    def test_malformed_variant_raises(self, tmp_path, game_data):
        variants = tmp_path / "data" / "variants"
        variants.mkdir(parents=True)
        (variants / "bad.variant").write_text("{not json")

        with pytest.raises(ValueError, match="malformed"):
            opponent_feature_row("bad", tmp_path, game_data)


class TestMatchupFeatureRow:
    def test_includes_interactions(self, game_dir, game_data, manifest):
        row = matchup_feature_row(
            _hammerhead_build(),
            "enforcer_Balanced",
            game_dir,
            game_data,
            manifest,
        )

        assert row["build_hull_id"] == "hammerhead"
        assert row["opponent_variant_id"] == "enforcer_Balanced"
        assert "interaction_range_delta" in row
        assert "interaction_speed_delta" in row
        assert row["interaction_kinetic_vs_shield"] >= 0
        assert "interaction_he_vs_armor" in row
        assert "interaction_small_pd_vs_missile" in row
