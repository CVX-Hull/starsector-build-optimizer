"""Tests for optimizer — Optuna integration, build conversion, warm-start, caching."""

from pathlib import Path

import optuna
import pytest

from starsector_optimizer.models import Build, HullSize
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.calibration import generate_random_build
from starsector_optimizer.opponent_pool import DEFAULT_OPPONENT_POOL, OpponentPool
from starsector_optimizer.optimizer import (
    BuildCache,
    OptimizerConfig,
    build_to_trial_params,
    define_distributions,
    preflight_check,
    trial_params_to_build,
    validate_variant,
    warm_start,
)


# --- Fixtures ---


@pytest.fixture(scope="module")
def game_data():
    return load_game_data(Path("game/starsector"))


@pytest.fixture(scope="module")
def wolf_hull(game_data):
    return game_data.hulls["wolf"]


@pytest.fixture(scope="module")
def wolf_space(wolf_hull, game_data):
    return build_search_space(wolf_hull, game_data)


# --- Build Conversion Tests ---


class TestBuildConversion:

    def test_build_to_params_round_trip(self, wolf_hull, wolf_space, game_data):
        """Build -> params -> Build preserves all fields."""
        build = generate_random_build(wolf_hull, game_data)
        params = build_to_trial_params(build, wolf_space)
        reconstructed = trial_params_to_build(params, "wolf")
        assert reconstructed.weapon_assignments == build.weapon_assignments
        assert reconstructed.hullmods == build.hullmods
        assert reconstructed.flux_vents == build.flux_vents
        assert reconstructed.flux_capacitors == build.flux_capacitors

    def test_params_include_all_weapon_slots(self, wolf_hull, wolf_space, game_data):
        """Every weapon slot in SearchSpace has a corresponding param."""
        build = generate_random_build(wolf_hull, game_data)
        params = build_to_trial_params(build, wolf_space)
        for slot_id in wolf_space.weapon_options:
            assert f"weapon_{slot_id}" in params

    def test_params_include_all_hullmods(self, wolf_hull, wolf_space, game_data):
        """Every eligible hullmod has a boolean param."""
        build = generate_random_build(wolf_hull, game_data)
        params = build_to_trial_params(build, wolf_space)
        for mod_id in wolf_space.eligible_hullmods:
            assert f"hullmod_{mod_id}" in params

    def test_empty_weapon_maps_to_empty_string(self, wolf_space):
        """Weapon slot with None maps to 'empty' and back."""
        weapons = {sid: None for sid in wolf_space.weapon_options}
        build = Build(
            hull_id="wolf",
            weapon_assignments=weapons,
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )
        params = build_to_trial_params(build, wolf_space)
        for sid in wolf_space.weapon_options:
            assert params[f"weapon_{sid}"] == "empty"

        reconstructed = trial_params_to_build(params, "wolf")
        for sid in wolf_space.weapon_options:
            assert reconstructed.weapon_assignments[sid] is None

    def test_hullmods_round_trip(self, wolf_hull, wolf_space, game_data):
        """Hullmod flags survive round-trip."""
        build = generate_random_build(wolf_hull, game_data)
        params = build_to_trial_params(build, wolf_space)
        reconstructed = trial_params_to_build(params, "wolf")
        assert reconstructed.hullmods == build.hullmods


# --- Build Cache Tests ---


class TestBuildCache:

    def test_cache_miss_returns_none(self):
        cache = BuildCache()
        build = Build(
            hull_id="wolf",
            weapon_assignments={},
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )
        assert cache.get(build) is None

    def test_cache_hit_returns_score(self):
        cache = BuildCache()
        build = Build(
            hull_id="wolf",
            weapon_assignments={},
            hullmods=frozenset(),
            flux_vents=5,
            flux_capacitors=3,
        )
        cache.put(build, 0.42)
        assert cache.get(build) == 0.42

    def test_different_builds_different_hash(self):
        cache = BuildCache()
        b1 = Build(
            hull_id="wolf",
            weapon_assignments={"WS0001": "heavymauler"},
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )
        b2 = Build(
            hull_id="wolf",
            weapon_assignments={"WS0001": "lightag"},
            hullmods=frozenset(),
            flux_vents=0,
            flux_capacitors=0,
        )
        assert cache.hash_build(b1) != cache.hash_build(b2)

    def test_same_build_same_hash(self):
        cache = BuildCache()
        build = Build(
            hull_id="wolf",
            weapon_assignments={"WS0001": "heavymauler"},
            hullmods=frozenset(["heavyarmor"]),
            flux_vents=10,
            flux_capacitors=5,
        )
        assert cache.hash_build(build) == cache.hash_build(build)

    def test_hullmod_order_irrelevant(self):
        """frozenset ensures order doesn't matter."""
        cache = BuildCache()
        b1 = Build(
            hull_id="wolf",
            weapon_assignments={},
            hullmods=frozenset(["heavyarmor", "hardenedshieldemitter"]),
            flux_vents=0,
            flux_capacitors=0,
        )
        b2 = Build(
            hull_id="wolf",
            weapon_assignments={},
            hullmods=frozenset(["hardenedshieldemitter", "heavyarmor"]),
            flux_vents=0,
            flux_capacitors=0,
        )
        assert cache.hash_build(b1) == cache.hash_build(b2)


# --- Define Distributions Tests ---


class TestDefineDistributions:

    def test_weapon_slots_categorical(self, wolf_space):
        dists = define_distributions(wolf_space)
        for slot_id in wolf_space.weapon_options:
            key = f"weapon_{slot_id}"
            assert key in dists
            assert isinstance(dists[key], optuna.distributions.CategoricalDistribution)

    def test_hullmod_flags_categorical(self, wolf_space):
        dists = define_distributions(wolf_space)
        for mod_id in wolf_space.eligible_hullmods:
            key = f"hullmod_{mod_id}"
            assert key in dists
            dist = dists[key]
            assert set(dist.choices) == {True, False}

    def test_vents_integer(self, wolf_space):
        dists = define_distributions(wolf_space)
        assert "flux_vents" in dists
        assert isinstance(dists["flux_vents"], optuna.distributions.IntDistribution)
        assert dists["flux_vents"].high == wolf_space.max_vents

    def test_caps_integer(self, wolf_space):
        dists = define_distributions(wolf_space)
        assert "flux_capacitors" in dists
        assert isinstance(dists["flux_capacitors"], optuna.distributions.IntDistribution)
        assert dists["flux_capacitors"].high == wolf_space.max_capacitors

    def test_total_dimension_count(self, wolf_space):
        """Total params = weapon slots + hullmods + 2 (vents, caps)."""
        dists = define_distributions(wolf_space)
        expected = len(wolf_space.weapon_options) + len(wolf_space.eligible_hullmods) + 2
        assert len(dists) == expected


# --- Warm Start Tests ---


class TestWarmStart:

    def test_adds_trials_to_study(self, wolf_hull, game_data):
        """Study gains warm_start_n completed trials."""
        study = optuna.create_study(direction="maximize")
        config = OptimizerConfig(warm_start_n=10, warm_start_sample_n=100)
        warm_start(study, wolf_hull, game_data, config)
        assert len(study.trials) == 10

    def test_trials_have_positive_scores(self, wolf_hull, game_data):
        """All warm-start trial values are positive (scaled heuristic scores)."""
        study = optuna.create_study(direction="maximize")
        config = OptimizerConfig(warm_start_n=5, warm_start_sample_n=50)
        warm_start(study, wolf_hull, game_data, config)
        for trial in study.trials:
            assert trial.value is not None
            assert trial.value > 0

    def test_trials_are_complete(self, wolf_hull, game_data):
        """All warm-start trials have COMPLETE state."""
        study = optuna.create_study(direction="maximize")
        config = OptimizerConfig(warm_start_n=5, warm_start_sample_n=50)
        warm_start(study, wolf_hull, game_data, config)
        for trial in study.trials:
            assert trial.state == optuna.trial.TrialState.COMPLETE

    def test_warm_start_scale_applied(self, wolf_hull, game_data):
        """Scores are scaled by warm_start_scale."""
        study = optuna.create_study(direction="maximize")
        config = OptimizerConfig(warm_start_n=3, warm_start_sample_n=30, warm_start_scale=0.1)
        warm_start(study, wolf_hull, game_data, config)
        # Heuristic composite scores are typically 0.2-0.8
        # Scaled by 0.1, should be 0.02-0.08
        for trial in study.trials:
            assert trial.value < 0.5  # Way below unscaled heuristic range


# --- Optimize Hull Integration Tests ---


class TestOptimizeHullIntegration:
    """Tests using mocked InstancePool to verify ask-tell loop correctness."""

    def _make_mock_pool(self):
        """Create a mock InstancePool that returns synthetic CombatResults."""
        from unittest.mock import MagicMock
        from starsector_optimizer.models import CombatResult, ShipCombatResult, DamageBreakdown

        mock_pool = MagicMock()
        mock_pool._config = MagicMock()
        mock_pool._config.game_dir = Path("game/starsector")
        mock_pool.write_variant_to_all = MagicMock()

        def mock_evaluate(matchups):
            results = []
            for m in matchups:
                player_ship = ShipCombatResult(
                    fleet_member_id="p0", variant_id=m.player_variants[0],
                    hull_id="wolf", destroyed=False, hull_fraction=0.7,
                    armor_fraction=0.8, cr_remaining=0.5, peak_time_remaining=100.0,
                    disabled_weapons=0, flameouts=0,
                    damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
                    overload_count=0,
                )
                enemy_ship = ShipCombatResult(
                    fleet_member_id="e0", variant_id=m.enemy_variants[0],
                    hull_id="enemy", destroyed=True, hull_fraction=0.0,
                    armor_fraction=0.0, cr_remaining=0.0, peak_time_remaining=0.0,
                    disabled_weapons=0, flameouts=0,
                    damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
                    overload_count=0,
                )
                results.append(CombatResult(
                    matchup_id=m.matchup_id, winner="PLAYER",
                    duration_seconds=60.0,
                    player_ships=(player_ship,), enemy_ships=(enemy_ship,),
                    player_ships_destroyed=0, enemy_ships_destroyed=1,
                    player_ships_retreated=0, enemy_ships_retreated=0,
                ))
            return results

        mock_pool.evaluate = mock_evaluate
        return mock_pool

    def test_no_orphaned_trials(self, wolf_hull, game_data):
        """After optimize_hull, no trials are in RUNNING or WAITING state."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=3, warm_start_n=5, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        for trial in study.trials:
            assert trial.state == optuna.trial.TrialState.COMPLETE, (
                f"Trial {trial.number} is {trial.state.name}, expected COMPLETE"
            )

    def test_trial_count_matches_budget(self, wolf_hull, game_data):
        """Study has exactly warm_start_n + sim_budget completed trials."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=3, warm_start_n=5, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        assert len(study.trials) == config.warm_start_n + config.sim_budget

    def test_batched_evaluation(self, wolf_hull, game_data):
        """optimize_hull sends multiple matchups per evaluate() call."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # eval_batch_size=2 with 1 opponent → 2 matchups per evaluate() call
        config = OptimizerConfig(sim_budget=4, warm_start_n=5, warm_start_sample_n=20,
                                 eval_batch_size=2)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Should have completed all trials
        assert len(study.trials) == config.warm_start_n + config.sim_budget
        for trial in study.trials:
            assert trial.state == optuna.trial.TrialState.COMPLETE

    def test_error_recovery(self, wolf_hull, game_data):
        """InstanceError during evaluation doesn't crash the optimizer."""
        from unittest.mock import MagicMock
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_evaluate = pool.evaluate

        def failing_evaluate(matchups):
            call_count[0] += 1
            if call_count[0] == 2:  # Fail on second batch
                raise InstanceError("Test failure")
            return original_evaluate(matchups)

        pool.evaluate = failing_evaluate
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=4, warm_start_n=5, warm_start_sample_n=20,
                                 eval_batch_size=2)

        # Should not raise — error is caught internally
        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        assert len(study.trials) == config.warm_start_n + config.sim_budget


# --- Preflight Check Tests ---


class TestPreflightCheck:

    def test_invalid_hull_id_raises(self, game_data):
        """Unknown hull_id raises ValueError."""
        from unittest.mock import MagicMock
        pool = MagicMock()
        pool._config = MagicMock()
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="not found"):
            preflight_check("nonexistent_hull", game_data, pool, opp_pool)

    def test_missing_mod_raises(self, game_data):
        """Missing combat harness mod raises ValueError."""
        from unittest.mock import MagicMock
        pool = MagicMock()
        pool._config = MagicMock()
        pool._config.game_dir = Path("/tmp/fake_game_dir")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="combat-harness"):
            preflight_check("wolf", game_data, pool, opp_pool)

    def test_valid_config_passes(self, game_data):
        """Valid config passes without raising."""
        from unittest.mock import MagicMock
        pool = MagicMock()
        pool._config = MagicMock()
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        preflight_check("wolf", game_data, pool, opp_pool)  # Should not raise

    def test_missing_opponent_variant_raises(self, game_data):
        """Opponent variant not found raises ValueError."""
        from unittest.mock import MagicMock
        pool = MagicMock()
        pool._config = MagicMock()
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("nonexistent_variant",)})
        with pytest.raises(ValueError, match="nonexistent_variant"):
            preflight_check("wolf", game_data, pool, opp_pool)


# --- Variant Validation Tests ---


class TestValidateVariant:

    def test_valid_variant_no_errors(self, game_data):
        """Valid variant returns empty error list."""
        variant = {
            "hullId": "wolf",
            "hullMods": ["heavyarmor", "hardenedshieldemitter"],
            "weaponGroups": [{"weapons": {"WS0001": "heavymauler"}}],
        }
        errors = validate_variant(variant, game_data)
        assert errors == []

    def test_invalid_hullmod_detected(self, game_data):
        """Unknown hullmod ID produces an error."""
        variant = {
            "hullId": "wolf",
            "hullMods": ["heavyarmor", "fake_hullmod_xyz"],
            "weaponGroups": [],
        }
        errors = validate_variant(variant, game_data)
        assert any("fake_hullmod_xyz" in e for e in errors)

    def test_invalid_weapon_detected(self, game_data):
        """Unknown weapon ID produces an error."""
        variant = {
            "hullId": "wolf",
            "hullMods": [],
            "weaponGroups": [{"weapons": {"WS0001": "nonexistent_gun"}}],
        }
        errors = validate_variant(variant, game_data)
        assert any("nonexistent_gun" in e for e in errors)

    def test_corrupted_hullmod_id_detected(self, game_data):
        """Hullmod ID with spaces/special chars produces an error."""
        variant = {
            "hullId": "wolf",
            "hullMods": ['all point-defense weapons deal %s more damage to all targets."'],
            "weaponGroups": [],
        }
        errors = validate_variant(variant, game_data)
        assert len(errors) > 0
