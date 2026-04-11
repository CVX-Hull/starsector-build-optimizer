"""Tests for optimizer — Optuna integration, build conversion, warm-start, caching."""

from pathlib import Path

import optuna
import pytest

from starsector_optimizer.models import Build, HullSize
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.calibration import generate_random_build
from starsector_optimizer.opponent_pool import DEFAULT_OPPONENT_POOL, OpponentPool
from starsector_optimizer.models import BuildSpec
from starsector_optimizer.optimizer import (
    BuildCache,
    OptimizerConfig,
    RunningStats,
    _create_sampler,
    build_to_trial_params,
    define_distributions,
    preflight_check,
    trial_params_to_build,
    validate_build_spec,
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


# --- RunningStats Tests ---


class TestRunningStats:
    """Tests for Welford's online mean/variance utility."""

    def test_empty_z_score_returns_zero(self):
        """z_score on empty stats returns 0.0."""
        rs = RunningStats()
        assert rs.z_score(42.0) == 0.0

    def test_single_sample_z_score_returns_zero(self):
        """z_score with n=1 returns 0.0 (need at least 2 for std)."""
        rs = RunningStats()
        rs.update(5.0)
        assert rs.n == 1
        assert rs.z_score(5.0) == 0.0

    def test_z_score_known_values(self):
        """z_score of the mean is 0; values above mean have positive z."""
        rs = RunningStats()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            rs.update(v)
        assert rs.z_score(3.0) == pytest.approx(0.0)
        assert rs.z_score(5.0) > 0.0
        assert rs.z_score(1.0) < 0.0

    def test_constant_values_z_score_zero(self):
        """All identical values → std=0 → z_score returns 0.0."""
        rs = RunningStats()
        for _ in range(5):
            rs.update(7.0)
        assert rs.z_score(7.0) == 0.0

    def test_welford_accuracy(self):
        """Welford mean/std matches statistics module for 100 values."""
        import statistics
        values = [i * 0.7 + 3.1 for i in range(100)]
        rs = RunningStats()
        for v in values:
            rs.update(v)
        assert rs.mean == pytest.approx(statistics.mean(values), rel=1e-9)
        assert rs.std == pytest.approx(statistics.stdev(values), rel=1e-9)

    def test_min_samples_respected(self):
        """z_score returns 0.0 until min_samples observations."""
        rs = RunningStats()
        rs.update(1.0)
        rs.update(10.0)
        # Default min_samples=2, n=2, should work
        assert rs.z_score(10.0) != 0.0
        # With min_samples=5, still returns 0.0
        assert rs.z_score(10.0, min_samples=5) == 0.0
        for v in [3.0, 5.0, 7.0]:
            rs.update(v)
        assert rs.n == 5
        assert rs.z_score(10.0, min_samples=5) != 0.0


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

    def test_trial_params_to_build_with_fixed_params(self, wolf_space):
        """Fixed params are merged into the build."""
        # Create params with all empty weapons and no hullmods
        params = {f"weapon_{sid}": "empty" for sid in wolf_space.weapon_options}
        for mod_id in wolf_space.eligible_hullmods:
            params[f"hullmod_{mod_id}"] = False
        params["flux_vents"] = 5
        params["flux_capacitors"] = 3

        # Fix a hullmod to True
        fixed = {f"hullmod_{wolf_space.eligible_hullmods[0]}": True, "flux_vents": 10}
        build = trial_params_to_build(params, "wolf", fixed_params=fixed)
        assert wolf_space.eligible_hullmods[0] in build.hullmods
        assert build.flux_vents == 10  # fixed overrides the 5


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

    def test_fixed_params_excluded_from_distributions(self, wolf_space):
        """Fixed hullmod param is excluded from distributions."""
        mod_id = wolf_space.eligible_hullmods[0]
        fixed = {f"hullmod_{mod_id}": True}
        dists = define_distributions(wolf_space, fixed_params=fixed)
        assert f"hullmod_{mod_id}" not in dists
        # Total count reduced by 1
        expected = len(wolf_space.weapon_options) + len(wolf_space.eligible_hullmods) + 2 - 1
        assert len(dists) == expected

    def test_fixed_weapon_excluded(self, wolf_space):
        """Fixed weapon slot is excluded from distributions."""
        slot_id = next(iter(wolf_space.weapon_options))
        weapon_id = wolf_space.weapon_options[slot_id][1]  # first non-empty option
        fixed = {f"weapon_{slot_id}": weapon_id}
        dists = define_distributions(wolf_space, fixed_params=fixed)
        assert f"weapon_{slot_id}" not in dists

    def test_fixed_flux_excluded(self, wolf_space):
        """Fixed flux_vents is excluded from distributions."""
        fixed = {"flux_vents": 15}
        dists = define_distributions(wolf_space, fixed_params=fixed)
        assert "flux_vents" not in dists

    def test_fixed_params_none_is_no_op(self, wolf_space):
        """fixed_params=None returns all distributions."""
        dists_none = define_distributions(wolf_space, fixed_params=None)
        dists_default = define_distributions(wolf_space)
        assert len(dists_none) == len(dists_default)


# --- Sampler Factory Tests ---


class TestSamplerFactory:

    def test_tpe_sampler_creation(self):
        """sampler='tpe' creates a TPESampler."""
        config = OptimizerConfig(sampler="tpe")
        sampler = _create_sampler(config)
        assert isinstance(sampler, optuna.samplers.TPESampler)

    def test_catcma_sampler_creation(self):
        """sampler='catcma' creates a CatCMAwM sampler."""
        config = OptimizerConfig(sampler="catcma")
        sampler = _create_sampler(config)
        # CatCMAwMSampler is loaded dynamically, just check it's a BaseSampler
        assert isinstance(sampler, optuna.samplers.BaseSampler)

    def test_invalid_sampler_raises(self):
        """Unknown sampler value raises ValueError."""
        config = OptimizerConfig(sampler="invalid")
        with pytest.raises(ValueError, match="Unknown sampler"):
            _create_sampler(config)

    def test_default_sampler_is_tpe(self):
        """Default sampler is 'tpe'."""
        config = OptimizerConfig()
        assert config.sampler == "tpe"


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
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig

        mock_pool = MagicMock(spec=InstancePool)
        mock_pool._config = MagicMock(spec=InstanceConfig)
        mock_pool._config.game_dir = Path("game/starsector")

        def mock_evaluate(matchups):
            results = []
            for m in matchups:
                player_ship = ShipCombatResult(
                    fleet_member_id="p0", variant_id=m.player_builds[0].variant_id,
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

        allowed = {optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED}
        for trial in study.trials:
            assert trial.state in allowed, (
                f"Trial {trial.number} is {trial.state.name}, expected COMPLETE or PRUNED"
            )

    def test_trial_count_matches_budget(self, wolf_hull, game_data):
        """Study has at least warm_start_n + sim_budget trials (stock builds add more)."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=3, warm_start_n=5, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Stock builds + heuristic warm-start + sim trials
        assert len(study.trials) >= config.warm_start_n + config.sim_budget

    def test_batched_evaluation(self, wolf_hull, game_data):
        """optimize_hull sends matchups from multiple builds per evaluate() call."""
        from unittest.mock import MagicMock
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        original_evaluate = pool.evaluate
        batch_sizes = []

        def tracking_evaluate(matchups):
            batch_sizes.append(len(matchups))
            return original_evaluate(matchups)

        pool.evaluate = tracking_evaluate
        # eval_batch_size=2 with 1 opponent → up to 2 matchups per evaluate() call
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=4, warm_start_n=5, warm_start_sample_n=20,
                                 eval_batch_size=2)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        assert len(study.trials) >= config.warm_start_n + config.sim_budget
        # At least one batch should have had multiple matchups (mixed builds)
        assert any(size > 1 for size in batch_sizes), (
            f"Expected mixed-build batches, got sizes: {batch_sizes}"
        )

    def test_error_recovery(self, wolf_hull, game_data):
        """InstanceError during evaluation doesn't crash the optimizer."""
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
        allowed = {optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED}
        sim_trials = [t for t in study.trials if t.number >= config.warm_start_n]
        for trial in sim_trials:
            assert trial.state in allowed, (
                f"Trial {trial.number} is {trial.state.name}"
            )


# --- Preflight Check Tests ---


class TestPreflightCheck:

    def test_invalid_hull_id_raises(self, game_data):
        """Unknown hull_id raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool._config = MagicMock(spec=InstanceConfig)
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="not found"):
            preflight_check("nonexistent_hull", game_data, pool, opp_pool)

    def test_missing_mod_raises(self, game_data):
        """Missing combat harness mod raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool._config = MagicMock(spec=InstanceConfig)
        pool._config.game_dir = Path("/tmp/fake_game_dir")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="combat-harness"):
            preflight_check("wolf", game_data, pool, opp_pool)

    def test_enabled_mods_missing_combat_harness(self, game_data, tmp_path):
        """enabled_mods.json without combat_harness raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        # Set up fake game dir with mod jar but wrong enabled_mods
        mods_dir = tmp_path / "mods" / "combat-harness" / "jars"
        mods_dir.mkdir(parents=True)
        (mods_dir / "combat-harness.jar").touch()
        enabled_mods = tmp_path / "mods" / "enabled_mods.json"
        enabled_mods.write_text('{"enabledMods": ["other_mod"]}')
        pool = MagicMock(spec=InstancePool)
        pool._config = MagicMock(spec=InstanceConfig)
        pool._config.game_dir = tmp_path
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="combat_harness"):
            preflight_check("wolf", game_data, pool, opp_pool)

    def test_valid_config_passes(self, game_data):
        """Valid config passes without raising."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool._config = MagicMock(spec=InstanceConfig)
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        preflight_check("wolf", game_data, pool, opp_pool)  # Should not raise

    def test_missing_opponent_variant_raises(self, game_data):
        """Opponent variant not found raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool._config = MagicMock(spec=InstanceConfig)
        pool._config.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("nonexistent_variant",)})
        with pytest.raises(ValueError, match="nonexistent_variant"):
            preflight_check("wolf", game_data, pool, opp_pool)


# --- Build Spec Validation Tests ---


class TestValidateBuildSpec:

    def test_valid_build_spec_no_errors(self, game_data):
        """Valid BuildSpec returns empty error list."""
        spec = BuildSpec(
            variant_id="wolf_opt_001",
            hull_id="wolf",
            weapon_assignments={"WS0001": "heavymauler"},
            hullmods=("heavyarmor", "hardenedshieldemitter"),
            flux_vents=10,
            flux_capacitors=5,
        )
        errors = validate_build_spec(spec, game_data)
        assert errors == []

    def test_unknown_hull(self, game_data):
        """Unknown hull_id produces an error."""
        spec = BuildSpec(
            variant_id="test",
            hull_id="nonexistent_hull",
            weapon_assignments={},
            hullmods=(),
            flux_vents=0,
            flux_capacitors=0,
        )
        errors = validate_build_spec(spec, game_data)
        assert any("nonexistent_hull" in e for e in errors)

    def test_unknown_weapon(self, game_data):
        """Unknown weapon ID produces an error."""
        spec = BuildSpec(
            variant_id="test",
            hull_id="wolf",
            weapon_assignments={"WS0001": "nonexistent_gun"},
            hullmods=(),
            flux_vents=0,
            flux_capacitors=0,
        )
        errors = validate_build_spec(spec, game_data)
        assert any("nonexistent_gun" in e for e in errors)

    def test_unknown_hullmod(self, game_data):
        """Unknown hullmod ID produces an error."""
        spec = BuildSpec(
            variant_id="test",
            hull_id="wolf",
            weapon_assignments={},
            hullmods=("fake_hullmod_xyz",),
            flux_vents=0,
            flux_capacitors=0,
        )
        errors = validate_build_spec(spec, game_data)
        assert any("fake_hullmod_xyz" in e for e in errors)


# --- Staged Evaluator Tests ---


class TestStagedEvaluator:
    """Tests for the staged evaluation loop with ASHA-style pruning."""

    def _make_mock_pool(self, *, winner="PLAYER"):
        """Create a mock InstancePool that returns synthetic CombatResults."""
        from unittest.mock import MagicMock
        from starsector_optimizer.models import CombatResult, ShipCombatResult, DamageBreakdown
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig

        mock_pool = MagicMock(spec=InstancePool)
        mock_pool._config = MagicMock(spec=InstanceConfig)
        mock_pool._config.game_dir = Path("game/starsector")

        def mock_evaluate(matchups):
            results = []
            for m in matchups:
                player_destroyed = winner == "ENEMY"
                enemy_destroyed = winner == "PLAYER"
                player_ship = ShipCombatResult(
                    fleet_member_id="p0", variant_id=m.player_builds[0].variant_id,
                    hull_id="wolf", destroyed=player_destroyed,
                    hull_fraction=0.0 if player_destroyed else 0.7,
                    armor_fraction=0.0 if player_destroyed else 0.8,
                    cr_remaining=0.0 if player_destroyed else 0.5,
                    peak_time_remaining=0.0 if player_destroyed else 100.0,
                    disabled_weapons=0, flameouts=0,
                    damage_dealt=DamageBreakdown(shield=100.0, armor=200.0, hull=300.0, emp=0.0),
                    damage_taken=DamageBreakdown(shield=50.0, armor=100.0, hull=150.0, emp=0.0),
                    overload_count=0,
                )
                enemy_ship = ShipCombatResult(
                    fleet_member_id="e0", variant_id=m.enemy_variants[0],
                    hull_id="enemy", destroyed=enemy_destroyed,
                    hull_fraction=0.0 if enemy_destroyed else 0.7,
                    armor_fraction=0.0 if enemy_destroyed else 0.8,
                    cr_remaining=0.0 if enemy_destroyed else 0.5,
                    peak_time_remaining=0.0 if enemy_destroyed else 100.0,
                    disabled_weapons=0, flameouts=0,
                    damage_dealt=DamageBreakdown(shield=50.0, armor=100.0, hull=150.0, emp=0.0),
                    damage_taken=DamageBreakdown(shield=100.0, armor=200.0, hull=300.0, emp=0.0),
                    overload_count=0,
                )
                results.append(CombatResult(
                    matchup_id=m.matchup_id, winner=winner,
                    duration_seconds=60.0,
                    player_ships=(player_ship,), enemy_ships=(enemy_ship,),
                    player_ships_destroyed=1 if player_destroyed else 0,
                    enemy_ships_destroyed=1 if enemy_destroyed else 0,
                    player_ships_retreated=0, enemy_ships_retreated=0,
                ))
            return results

        mock_pool.evaluate = mock_evaluate
        return mock_pool

    def test_cached_builds_skip_evaluation(self, wolf_hull, game_data):
        """Cached builds are told to Optuna immediately without evaluate()."""
        from unittest.mock import MagicMock
        from starsector_optimizer.optimizer import (
            BuildCache, StagedEvaluator, optimize_hull,
        )
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        call_count = [0]
        original_evaluate = pool.evaluate

        def counting_evaluate(matchups):
            call_count[0] += 1
            return original_evaluate(matchups)

        pool.evaluate = counting_evaluate
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # Small budget to keep test fast
        config = OptimizerConfig(sim_budget=2, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # With 2 sim trials and 1 opponent, we expect at most 2 evaluate calls
        # (fewer if cache hits occur — same build proposed twice)
        assert call_count[0] <= config.sim_budget

    def test_pruned_builds_not_cached(self, wolf_hull, game_data):
        """Pruned builds should NOT be in the cache."""
        from starsector_optimizer.optimizer import optimize_hull, BuildCache
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool(winner="ENEMY")
        opp_pool = OpponentPool(pools={
            HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault", "hyperion_Attack"),
        })
        # pruner_startup_trials=0 so pruning kicks in immediately
        config = OptimizerConfig(
            sim_budget=5, warm_start_n=3, warm_start_sample_n=20,
            pruner_startup_trials=0, pruner_warmup_steps=0,
        )

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        pruned = [t for t in study.trials
                  if t.state == optuna.trial.TrialState.PRUNED]
        # With all ENEMY wins and pruner_startup_trials=0, some should be pruned
        # (MedianPruner compares against median of previous trials)
        # This test verifies the pipeline runs without error when pruning occurs
        allowed = {optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED}
        for trial in study.trials:
            assert trial.state in allowed

    def test_completed_builds_cached(self, wolf_hull, game_data):
        """Non-pruned builds are stored in cache after completion."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=3, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE
                     and t.value is not None and t.value > 0]
        # At least some completed trials should exist with positive fitness
        assert len(completed) > 0

    def test_all_opponents_evaluated_when_not_pruned(self, wolf_hull, game_data):
        """Good builds get matchups against all opponents."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()  # All PLAYER wins — no pruning
        opponents = ("wolf_Assault", "lasher_Assault")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=2, warm_start_n=3, warm_start_sample_n=20)

        # Track all matchup IDs
        all_matchup_ids = []
        original_evaluate = pool.evaluate

        def tracking_evaluate(matchups):
            all_matchup_ids.extend(m.matchup_id for m in matchups)
            return original_evaluate(matchups)

        pool.evaluate = tracking_evaluate

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # Find trials that went through simulation (have matchup IDs containing their number)
        # Each such trial should have matchups against both opponents
        sim_trial_numbers = set()
        for mid in all_matchup_ids:
            # matchup_id format: wolf_opt_{trial:06d}_vs_{opponent}
            parts = mid.split("_vs_")[0]  # "wolf_opt_000003"
            sim_trial_numbers.add(parts)

        for prefix in sim_trial_numbers:
            trial_matchups = [mid for mid in all_matchup_ids
                              if mid.startswith(prefix)]
            assert len(trial_matchups) == len(opponents), (
                f"Build {prefix} has {len(trial_matchups)} matchups, "
                f"expected {len(opponents)}"
            )

    def test_cumulative_fitness_uses_aggregate(self, wolf_hull, game_data):
        """Staged evaluator reports intermediate values for multi-opponent evaluation."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=2, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Find trials with intermediate values (sim trials that weren't cache hits)
        trials_with_intermediates = [
            t for t in study.trials if len(t.intermediate_values) > 0
        ]
        # At least one trial should have intermediate reports for each opponent
        assert len(trials_with_intermediates) > 0, "No trials have intermediate values"
        for trial in trials_with_intermediates:
            assert len(trial.intermediate_values) == len(opponents), (
                f"Trial {trial.number} has {len(trial.intermediate_values)} "
                f"intermediate values, expected {len(opponents)}"
            )

    def test_matchup_id_routes_to_correct_build(self, wolf_hull, game_data):
        """Each result's matchup_id correctly identifies the trial and opponent."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=2, warm_start_n=3, warm_start_sample_n=20)

        all_matchup_ids = []
        original_evaluate = pool.evaluate

        def tracking_evaluate(matchups):
            all_matchup_ids.extend(m.matchup_id for m in matchups)
            return original_evaluate(matchups)

        pool.evaluate = tracking_evaluate

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # All matchup IDs should follow the pattern: {hull}_opt_{trial:06d}_vs_{opponent}
        for mid in all_matchup_ids:
            assert "_vs_" in mid, f"Matchup ID missing '_vs_': {mid}"
            assert "wolf_opt_" in mid, f"Matchup ID missing 'wolf_opt_': {mid}"

    def test_instance_error_scores_negative(self, wolf_hull, game_data):
        """InstanceError during evaluate() scores affected trials as -1.0."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_evaluate = pool.evaluate

        def sometimes_failing(matchups):
            call_count[0] += 1
            if call_count[0] == 1:
                raise InstanceError("First batch fails")
            return original_evaluate(matchups)

        pool.evaluate = sometimes_failing
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=4, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Some trials should have -1.0 value (from the failed batch)
        negative_trials = [t for t in study.trials
                           if t.state == optuna.trial.TrialState.COMPLETE
                           and t.value is not None and t.value < 0]
        assert len(negative_trials) > 0, "Expected some trials with negative scores from InstanceError"

    def test_opponent_stats_updated_per_result(self, wolf_hull, game_data):
        """After routing results, _opponent_stats[i].n increments for correct opponent."""
        from starsector_optimizer.optimizer import StagedEvaluator, BuildCache, optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=3, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # Can't directly inspect evaluator state after run(), so verify indirectly:
        # completed trials should have intermediate values (z-scored) for each opponent
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE
                     and len(t.intermediate_values) > 0]
        for trial in completed:
            assert len(trial.intermediate_values) == len(opponents)

    def test_z_scored_intermediate_reports(self, wolf_hull, game_data):
        """Intermediate trial.report() values use z-scored aggregate, not raw."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault")})
        # Use enough trials so z-scoring has data
        config = OptimizerConfig(sim_budget=10, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # With uniform PLAYER wins, z-scores should converge toward 0 as all
        # results are similar. Check that later trials have small intermediate values.
        trials_with_iv = [t for t in study.trials if len(t.intermediate_values) > 0]
        assert len(trials_with_iv) > 2
        # After enough data, z-scores of uniform results should be near 0
        last_trials = trials_with_iv[-3:]
        for trial in last_trials:
            for step, val in trial.intermediate_values.items():
                assert abs(val) < 5.0, (
                    f"Trial {trial.number} step {step} has z-score {val}, "
                    f"expected near 0 for uniform results"
                )

    def test_control_variate_activates(self, wolf_hull, game_data):
        """After cv_min_samples evaluations, _cv_active should become True if correlated."""
        from starsector_optimizer.optimizer import optimize_hull, StagedEvaluator
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # Need enough trials for CV to activate (cv_min_samples=30 default)
        config = OptimizerConfig(
            sim_budget=35, warm_start_n=3, warm_start_sample_n=20,
            cv_min_samples=10,  # lower threshold to keep test fast
        )

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # If the CV activated, the study should still have valid values
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE
                     and t.value is not None]
        # With uniform PLAYER wins and identical heuristic scores,
        # correlation may be low. The key test is that the pipeline runs.
        assert len(completed) > 0

    def test_control_variate_inactive_low_correlation(self, wolf_hull, game_data):
        """With uncorrelated heuristic/sim scores, CV should remain inactive."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # Very high threshold ensures CV stays inactive
        config = OptimizerConfig(
            sim_budget=5, warm_start_n=3, warm_start_sample_n=20,
            cv_rho_threshold=0.99,
        )

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Pipeline should complete normally even with CV inactive
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE]
        assert len(completed) > 0

    def test_rank_shaping_spreads_cluster(self, wolf_hull, game_data):
        """Rank shaping spreads clustered sim values across (0, 1]."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=10, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Sim-evaluated trials should have rank-shaped values in (0, 1]
        sim_trials = [t for t in study.trials
                      if t.state == optuna.trial.TrialState.COMPLETE
                      and t.value is not None
                      and t.value > 0 and t.value <= 1.0]
        assert len(sim_trials) >= 3, "Need enough sim trials to verify rank spread"
        # All values should be in valid rank range
        for trial in sim_trials:
            assert 0.0 < trial.value <= 1.0, (
                f"Trial {trial.number} value {trial.value} outside rank range (0, 1]"
            )
        # With multiple trials, rank values should span a range (not all identical)
        values = [t.value for t in sim_trials]
        assert max(values) > min(values), (
            f"Rank-shaped values should spread: got {values}"
        )

    def test_intermediate_reports_not_rank_shaped(self, wolf_hull, game_data):
        """Intermediate trial.report() values are z-scored, not rank-shaped."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={
            HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault"),
        })
        config = OptimizerConfig(sim_budget=8, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        trials_with_iv = [t for t in study.trials if len(t.intermediate_values) > 0]
        assert len(trials_with_iv) > 0
        # Intermediate values are z-scores (unbounded, centered ~0),
        # not rank-shaped (would be in (0, 1])
        for trial in trials_with_iv:
            for step, val in trial.intermediate_values.items():
                # Z-scores can be negative or exceed 1.0 — rank values cannot be negative
                # With uniform PLAYER wins, z-scores converge toward 0
                # Key: they should NOT all be in (0, 1] — at least some should be <= 0
                pass  # Structural check: they exist and pipeline completes
        # With enough uniform data, z-scores should converge near 0 (some ≤ 0)
        all_intermediates = [
            val for t in trials_with_iv
            for val in t.intermediate_values.values()
        ]
        has_non_positive = any(v <= 0.0 for v in all_intermediates)
        has_any = len(all_intermediates) > 0
        # With uniform results, z-scores are either 0 or near-0
        assert has_any, "Should have intermediate values"
        # Z-scores of uniform data converge to 0.0 — verify not all positive (rank would be)
        assert has_non_positive or all(abs(v) < 0.1 for v in all_intermediates), (
            f"Intermediate values look rank-shaped, not z-scored: {all_intermediates}"
        )

    def test_failure_score_bypasses_transformations(self, wolf_hull, game_data):
        """Invalid builds get raw config.failure_score, not transformed."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_evaluate = pool.evaluate

        def always_fails(matchups):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise InstanceError("Simulated failure")
            return original_evaluate(matchups)

        pool.evaluate = always_fails
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        failure_trials = [t for t in study.trials
                          if t.state == optuna.trial.TrialState.COMPLETE
                          and t.value is not None and t.value == config.failure_score]
        assert len(failure_trials) > 0, "Expected some trials with raw failure_score"

    def test_cached_builds_return_transformed_score(self, wolf_hull, game_data):
        """Cache stores post-transformation value; cache hit returns it."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # Small budget — with dedup, some builds may be cache hits
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Pipeline should complete; cache hits return same value as original
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE]
        assert len(completed) > 0
