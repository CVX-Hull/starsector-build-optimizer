"""Property-based integration tests — end-to-end pipeline verification."""

import pytest

from starsector_optimizer.calibration import generate_diverse_builds, compute_build_features
from starsector_optimizer.repair import is_feasible, compute_op_cost
from starsector_optimizer.scorer import heuristic_score
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.variant import generate_variant
from starsector_optimizer.game_manifest import (
    SLOT_WEAPON_COMPATIBILITY as SLOT_COMPATIBILITY,
)


class TestEndToEndPipeline:
    """Generate random builds → repair → verify feasibility → score → variant."""

    @pytest.fixture(scope="class")
    def eagle_builds(self, game_data, manifest):
        eagle = game_data.hulls["eagle"]
        return generate_diverse_builds(eagle, game_data, manifest, 200, seed=123)

    def test_all_repaired_builds_feasible(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        for i, build in enumerate(eagle_builds):
            ok, violations = is_feasible(build, eagle, game_data, manifest)
            assert ok, f"Build {i} infeasible: {violations}"

    def test_all_builds_within_op_budget(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        for build in eagle_builds:
            cost = compute_op_cost(build, eagle, game_data)
            assert cost <= eagle.ordnance_points

    def test_all_builds_slot_compatible(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        slot_map = {s.id: s for s in eagle.weapon_slots}
        for build in eagle_builds:
            for slot_id, weapon_id in build.weapon_assignments.items():
                if not weapon_id:
                    continue
                slot = slot_map[slot_id]
                weapon = game_data.weapons[weapon_id]
                allowed = SLOT_COMPATIBILITY[slot.slot_type]
                assert weapon.weapon_type in allowed, (
                    f"{weapon_id} ({weapon.weapon_type}) in {slot_id} ({slot.slot_type})"
                )
                assert weapon.size == slot.slot_size

    def test_all_builds_scoreable(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        for build in eagle_builds:
            result = heuristic_score(build, eagle, game_data)
            assert result.composite_score >= 0
            assert result.effective_hp > 0

    def test_all_builds_generate_valid_variants(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        for build in eagle_builds:
            variant = generate_variant(build, eagle, game_data)
            assert variant["hullId"] == "eagle"
            assert variant["fluxVents"] == build.flux_vents
            assert variant["fluxCapacitors"] == build.flux_capacitors
            assert set(variant["hullMods"]) == set(build.hullmods)

    def test_all_builds_extract_features(self, game_data, manifest, eagle_builds):
        eagle = game_data.hulls["eagle"]
        for build in eagle_builds:
            features = compute_build_features(build, eagle, game_data)
            for key, val in features.items():
                assert val >= 0, f"Feature {key} negative: {val}"

    def test_diversity_across_builds(self, eagle_builds):
        weapon_sets = {
            frozenset((k, v) for k, v in b.weapon_assignments.items() if v)
            for b in eagle_builds
        }
        hullmod_sets = {b.hullmods for b in eagle_builds}
        assert len(weapon_sets) > 50, f"Only {len(weapon_sets)} unique weapon configs"
        assert len(hullmod_sets) > 30, f"Only {len(hullmod_sets)} unique hullmod configs"


class TestMultiHullPipeline:
    """Verify pipeline works across different hull sizes and types."""

    @pytest.mark.parametrize("hull_id", ["wolf", "lasher", "eagle", "onslaught"])
    def test_build_score_variant_for_hull(self, game_data, manifest, hull_id):
        if hull_id not in game_data.hulls:
            pytest.skip(f"{hull_id} not in game data")
        hull = game_data.hulls[hull_id]
        builds = generate_diverse_builds(hull, game_data, manifest, 20, seed=42)
        for build in builds:
            ok, violations = is_feasible(build, hull, game_data, manifest)
            assert ok, f"{hull_id} build infeasible: {violations}"
            result = heuristic_score(build, hull, game_data)
            assert result.composite_score >= 0
            variant = generate_variant(build, hull, game_data)
            assert variant["hullId"] == hull_id
