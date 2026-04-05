"""Tests for calibration pipeline."""

import pytest

from starsector_optimizer.models import Build, GameData
from starsector_optimizer.repair import is_feasible
from starsector_optimizer.calibration import (
    generate_random_build,
    generate_diverse_builds,
    compute_build_features,
)


class TestGenerateRandomBuild:
    def test_returns_build(self, game_data):
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        assert isinstance(build, Build)

    def test_is_feasible(self, game_data):
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        ok, violations = is_feasible(build, eagle, game_data)
        assert ok, f"Generated build is infeasible: {violations}"


class TestGenerateDiverseBuilds:
    def test_returns_n_builds(self, game_data):
        eagle = game_data.hulls["eagle"]
        builds = generate_diverse_builds(eagle, game_data, 20)
        assert len(builds) == 20

    def test_all_feasible(self, game_data):
        eagle = game_data.hulls["eagle"]
        builds = generate_diverse_builds(eagle, game_data, 50)
        for b in builds:
            ok, violations = is_feasible(b, eagle, game_data)
            assert ok, f"Build infeasible: {violations}"

    def test_has_variety(self, game_data):
        """Builds should not all be identical."""
        eagle = game_data.hulls["eagle"]
        builds = generate_diverse_builds(eagle, game_data, 20)
        weapon_sets = set()
        for b in builds:
            equipped = frozenset(
                (k, v) for k, v in b.weapon_assignments.items() if v is not None
            )
            weapon_sets.add(equipped)
        assert len(weapon_sets) > 5, "Too little variety in generated builds"

    def test_deterministic_with_seed(self, game_data):
        eagle = game_data.hulls["eagle"]
        builds1 = generate_diverse_builds(eagle, game_data, 10, seed=42)
        builds2 = generate_diverse_builds(eagle, game_data, 10, seed=42)
        for b1, b2 in zip(builds1, builds2):
            assert b1 == b2


class TestComputeBuildFeatures:
    def test_returns_dict(self, game_data):
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        features = compute_build_features(build, eagle, game_data)
        assert isinstance(features, dict)

    def test_has_expected_keys(self, game_data):
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        features = compute_build_features(build, eagle, game_data)
        for key in ["total_dps", "flux_balance", "flux_efficiency",
                     "effective_hp", "range_coherence", "damage_mix",
                     "engagement_range", "n_weapons", "n_hullmods",
                     "vents", "caps"]:
            assert key in features, f"Missing feature: {key}"

    def test_values_non_negative(self, game_data):
        eagle = game_data.hulls["eagle"]
        build = generate_random_build(eagle, game_data)
        features = compute_build_features(build, eagle, game_data)
        for key, val in features.items():
            assert val >= 0, f"Feature {key} is negative: {val}"
