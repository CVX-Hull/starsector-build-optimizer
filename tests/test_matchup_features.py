"""Tests for Phase 7 flat matchup feature extraction."""

import json

import pytest

from starsector_optimizer.models import Build
from starsector_optimizer.matchup_features import (
    EMPTY_SENTINEL,
    FEATURE_PROFILES,
    build_feature_row,
    filter_feature_profile,
    matchup_feature_row,
    opponent_feature_row,
)


def _as_number(value: "float | int | str") -> float:
    """Narrow a feature-row value to a number for comparisons."""
    assert isinstance(value, (int, float))
    return value


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
        # Schema version is provenance, not data (spec 31 / review L1): it must
        # never appear as a feature column.
        assert "feature_schema_version" not in row
        assert row["build_hull_size"] == hull.hull_size.value
        assert row["build_weapon_count"] == 6
        assert row["build_empty_slot_count"] == 2
        assert row["build_hullmod_count"] == 2
        assert row["build_flux_vents"] == 5
        assert row["build_flux_capacitors"] == 3
        assert _as_number(row["build_total_dps"]) > 0
        assert _as_number(row["build_scorer_total_dps"]) > 0
        assert "build_damage_kinetic_dps" in row
        assert "build_slot_small_count" in row
        assert row["build_slot_00_slot_id"]
        assert row["build_slot_00_slot_type"]
        assert "build_slot_00_angle_sin" in row
        assert "build_slot_00_arc_fraction" in row
        assert "build_slot_00_arc_bucket" in row
        assert "build_geometry_collision_radius" in row
        assert "build_arc_front_weapon_dps" in row
        assert "build_slot_00_weapon_id" in row
        assert "build_hullmod__armoredweapons" in row
        assert "build_small_pd_count" in row
        assert "build_hull_system_id" in row
        assert "build_weapon_hint__pd_count" in row

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

        assert EMPTY_SENTINEL in {value for key, value in row.items() if key.endswith("_weapon_id")}

    def test_builtin_weapons_are_included_in_aggregates(self, game_data, manifest):
        hull = game_data.hulls["hammerhead"]
        builtin_slot = hull.weapon_slots[0].id
        original_builtins = dict(hull.built_in_weapons)
        try:
            hull.built_in_weapons = {builtin_slot: "heavyac"}
            build = Build(
                "hammerhead", {slot.id: None for slot in hull.weapon_slots}, frozenset(), 0, 0
            )
            row = build_feature_row(build, hull, game_data, manifest)
        finally:
            hull.built_in_weapons = original_builtins

        assert row["build_weapon_count"] == 1
        assert row["build_total_dps"] == game_data.weapons["heavyac"].sustained_dps


class TestOpponentFeatureRow:
    def test_loads_stock_variant_features(self, game_dir, game_data):
        row = opponent_feature_row("enforcer_Balanced", game_dir, game_data)

        assert row["opponent_variant_id"] == "enforcer_Balanced"
        # Schema version is provenance, not data (spec 31 / review L1): it must
        # never appear as a feature column.
        assert "feature_schema_version" not in row
        assert row["opponent_hull_id"] == "enforcer"
        assert _as_number(row["opponent_weapon_count"]) > 0
        assert row["opponent_hull_size"] == game_data.hulls["enforcer"].hull_size.value
        assert "opponent_flux_vents" in row
        assert "opponent_hullmod_op" in row
        assert "opponent_scorer_total_dps" in row
        assert "opponent_hull_system_id" in row
        assert "opponent_geometry_shield_radius" in row

    def test_stock_variant_wings_are_opponent_only_features(self, game_dir, game_data):
        row = opponent_feature_row("mora_Support", game_dir, game_data)

        assert _as_number(row["opponent_wing_count"]) > 0
        assert _as_number(row["opponent_wing_size"]) > 0
        assert "opponent_wing_role__fighter_count" in row

    def test_missing_variant_raises(self, game_dir, game_data):
        with pytest.raises(FileNotFoundError):
            opponent_feature_row("not_a_real_variant", game_dir, game_data)

    def test_unknown_variant_hull_raises(self, tmp_path, game_data):
        variants = tmp_path / "data" / "variants"
        variants.mkdir(parents=True)
        (variants / "bad.variant").write_text(
            json.dumps(
                {
                    "variantId": "bad",
                    "hullId": "not_a_real_hull",
                    "weaponGroups": [],
                }
            )
        )

        with pytest.raises(ValueError, match="unknown hull"):
            opponent_feature_row("bad", tmp_path, game_data)

    def test_malformed_variant_raises(self, tmp_path, game_data):
        variants = tmp_path / "data" / "variants"
        variants.mkdir(parents=True)
        (variants / "bad.variant").write_text("{not json")

        with pytest.raises(ValueError, match="malformed"):
            opponent_feature_row("bad", tmp_path, game_data)

    def test_malformed_wings_raise(self, tmp_path, game_data):
        variants = tmp_path / "data" / "variants"
        variants.mkdir(parents=True)
        (variants / "bad_wings.variant").write_text(
            json.dumps(
                {
                    "variantId": "bad_wings",
                    "hullId": "enforcer",
                    "weaponGroups": [],
                    "wings": "not-a-list",
                }
            )
        )

        with pytest.raises(ValueError, match="malformed wings"):
            opponent_feature_row("bad_wings", tmp_path, game_data)


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
        assert _as_number(row["interaction_kinetic_vs_shield"]) >= 0
        assert "interaction_he_vs_armor" in row
        assert "interaction_small_pd_vs_missile" in row
        assert "interaction_front_dps_delta" in row
        assert "interaction_wing_pressure_vs_pd" in row


class TestFeatureProfiles:
    def test_profile_filter_rejects_unknown_profile(self, game_dir, game_data, manifest):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        with pytest.raises(ValueError, match="unknown feature profile"):
            filter_feature_profile(row, "not-a-profile")
        assert "v2-compatible" not in FEATURE_PROFILES

    def test_geometry_profile_keeps_geometry_and_drops_sparse_hullmods(
        self, game_dir, game_data, manifest
    ):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        filtered = filter_feature_profile(row, "geometry")

        assert "build_geometry_collision_radius" in filtered
        assert "build_arc_front_weapon_dps" in filtered
        assert "build_hullmod__armoredweapons" not in filtered

    def test_geometry_profile_drops_interaction_fields(self, game_dir, game_data, manifest):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        filtered = filter_feature_profile(row, "geometry")

        assert not any(key.startswith("interaction_") for key in filtered)

    def test_sparse_component_profile_keeps_ids(self, game_dir, game_data, manifest):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        filtered = filter_feature_profile(row, "sparse-component")

        assert "build_slot_00_weapon_id" in filtered
        assert "build_hullmod__armoredweapons" in filtered
        assert "build_hull_id" in filtered
        assert "interaction_range_delta" not in filtered

    def test_opponent_parity_profile_drops_sparse_ids_and_interactions(
        self, game_dir, game_data, manifest
    ):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        filtered = filter_feature_profile(row, "opponent-parity")

        assert "opponent_flux_vents" in filtered
        assert "opponent_hullmod_op" in filtered
        assert "opponent_variant_id" not in filtered
        assert "opponent_slot_00_weapon_id" not in filtered
        assert "build_hullmod__armoredweapons" not in filtered
        assert "interaction_range_delta" not in filtered

    def test_sparse_cross_profile_is_removed(self, game_dir, game_data, manifest):
        # sparse-cross was byte-identical to `all` by definition
        # (sparse-component already keeps every non-interaction feature);
        # spec 31 removed it as a redundant arm.
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        assert "sparse-cross" not in FEATURE_PROFILES
        with pytest.raises(ValueError, match="unknown feature profile"):
            filter_feature_profile(row, "sparse-cross")

    def test_all_profile_is_identity(self, game_dir, game_data, manifest):
        row = matchup_feature_row(
            _hammerhead_build(), "enforcer_Balanced", game_dir, game_data, manifest
        )
        assert filter_feature_profile(row, "all") == dict(row)
