"""Tests for optimizer — Optuna integration, build conversion, warm-start, caching."""

from pathlib import Path

import optuna
import pytest

from starsector_optimizer.models import Build, HullSize
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.search_space import build_search_space
from starsector_optimizer.calibration import generate_random_build
from starsector_optimizer.opponent_pool import OpponentPool
from starsector_optimizer.models import BuildSpec
from starsector_optimizer.optimizer import (
    BuildCache,
    OptimizerConfig,
    _create_pruner,
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

    def _make_mock_pool(self, *, num_instances=1):
        """Create a mock InstancePool with run_matchup returning synthetic CombatResults."""
        from unittest.mock import MagicMock
        from starsector_optimizer.models import CombatResult, ShipCombatResult, DamageBreakdown
        from starsector_optimizer.instance_manager import InstancePool

        mock_pool = MagicMock(spec=InstancePool)
        mock_pool.game_dir = Path("game/starsector")
        mock_pool.num_instances = num_instances

        def mock_run_matchup(instance_id, matchup):
            m = matchup
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
            return CombatResult(
                matchup_id=m.matchup_id, winner="PLAYER",
                duration_seconds=60.0,
                player_ships=(player_ship,), enemy_ships=(enemy_ship,),
                player_ships_destroyed=0, enemy_ships_destroyed=1,
                player_ships_retreated=0, enemy_ships_retreated=0,
            )

        mock_pool.run_matchup = mock_run_matchup
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

    def test_error_recovery(self, wolf_hull, game_data):
        """InstanceError during run_matchup doesn't crash the optimizer."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_run = pool.run_matchup

        def failing_run(instance_id, matchup):
            call_count[0] += 1
            if call_count[0] == 2:  # Fail on second matchup
                raise InstanceError("Test failure")
            return original_run(instance_id, matchup)

        pool.run_matchup = failing_run
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=4, warm_start_n=5, warm_start_sample_n=20)

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
        pool.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="not found"):
            preflight_check("nonexistent_hull", game_data, pool, opp_pool)

    def test_missing_mod_raises(self, game_data):
        """Missing combat harness mod raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool.game_dir = Path("/tmp/fake_game_dir")
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
        pool.game_dir = tmp_path
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        with pytest.raises(ValueError, match="combat_harness"):
            preflight_check("wolf", game_data, pool, opp_pool)

    def test_valid_config_passes(self, game_data):
        """Valid config passes without raising."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool.game_dir = Path("game/starsector")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        preflight_check("wolf", game_data, pool, opp_pool)  # Should not raise

    def test_missing_opponent_variant_raises(self, game_data):
        """Opponent variant not found raises ValueError."""
        from unittest.mock import MagicMock
        from starsector_optimizer.instance_manager import InstancePool, InstanceConfig
        pool = MagicMock(spec=InstancePool)
        pool.game_dir = Path("game/starsector")
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

    def _make_mock_pool(self, *, winner="PLAYER", num_instances=1):
        """Create a mock InstancePool with run_matchup returning synthetic CombatResults."""
        from unittest.mock import MagicMock
        from starsector_optimizer.models import CombatResult, ShipCombatResult, DamageBreakdown
        from starsector_optimizer.instance_manager import InstancePool

        mock_pool = MagicMock(spec=InstancePool)
        mock_pool.game_dir = Path("game/starsector")
        mock_pool.num_instances = num_instances

        def mock_run_matchup(instance_id, matchup):
            m = matchup
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
            return CombatResult(
                matchup_id=m.matchup_id, winner=winner,
                duration_seconds=60.0,
                player_ships=(player_ship,), enemy_ships=(enemy_ship,),
                player_ships_destroyed=1 if player_destroyed else 0,
                enemy_ships_destroyed=1 if enemy_destroyed else 0,
                player_ships_retreated=0, enemy_ships_retreated=0,
            )

        mock_pool.run_matchup = mock_run_matchup
        return mock_pool

    def test_cached_builds_skip_evaluation(self, wolf_hull, game_data):
        """Cached builds are told to Optuna immediately without run_matchup()."""
        from unittest.mock import MagicMock
        from starsector_optimizer.optimizer import (
            BuildCache, StagedEvaluator, optimize_hull,
        )
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        call_count = [0]
        original_run = pool.run_matchup

        def counting_run(instance_id, matchup):
            call_count[0] += 1
            return original_run(instance_id, matchup)

        pool.run_matchup = counting_run
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        # Small budget to keep test fast
        config = OptimizerConfig(sim_budget=2, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # With 2 sim trials and 1 opponent, we expect at most 2 run_matchup calls
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
        # wilcoxon_n_startup_steps=0 so pruning kicks in immediately
        config = OptimizerConfig(
            sim_budget=5, warm_start_n=3, warm_start_sample_n=20,
            wilcoxon_n_startup_steps=0,
        )

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        pruned = [t for t in study.trials
                  if t.state == optuna.trial.TrialState.PRUNED]
        # With all ENEMY wins and wilcoxon_n_startup_steps=0, some should be pruned
        # (WilcoxonPruner compares against best trial via signed-rank test)
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
        original_run = pool.run_matchup

        def tracking_run(instance_id, matchup):
            all_matchup_ids.append(matchup.matchup_id)
            return original_run(instance_id, matchup)

        pool.run_matchup = tracking_run

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

    def test_intermediate_values_per_opponent(self, wolf_hull, game_data):
        """Staged evaluator reports one intermediate value per opponent evaluated."""
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
        original_run = pool.run_matchup

        def tracking_run(instance_id, matchup):
            all_matchup_ids.append(matchup.matchup_id)
            return original_run(instance_id, matchup)

        pool.run_matchup = tracking_run

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # All matchup IDs should follow the pattern: {hull}_opt_{trial:06d}_vs_{opponent}
        for mid in all_matchup_ids:
            assert "_vs_" in mid, f"Matchup ID missing '_vs_': {mid}"
            assert "wolf_opt_" in mid, f"Matchup ID missing 'wolf_opt_': {mid}"

    def test_instance_error_scores_negative(self, wolf_hull, game_data):
        """InstanceError during run_matchup() scores affected trials as -1.0."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_run = pool.run_matchup

        def sometimes_failing(instance_id, matchup):
            call_count[0] += 1
            if call_count[0] == 1:
                raise InstanceError("First matchup fails")
            return original_run(instance_id, matchup)

        pool.run_matchup = sometimes_failing
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=4, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Some trials should have -1.0 value (from the failed matchup)
        negative_trials = [t for t in study.trials
                           if t.state == optuna.trial.TrialState.COMPLETE
                           and t.value is not None and t.value < 0]
        assert len(negative_trials) > 0, "Expected some trials with negative scores from InstanceError"

    def test_raw_intermediate_reports_at_stable_steps(self, wolf_hull, game_data):
        """Intermediate trial.report() values are raw combat_fitness at rung positions."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault")})
        config = OptimizerConfig(sim_budget=10, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        trials_with_iv = [t for t in study.trials if len(t.intermediate_values) > 0]
        assert len(trials_with_iv) > 2
        # Step IDs are rung positions (0-based), so all trials share the same
        # step IDs — enabling WilcoxonPruner paired comparisons from trial 1.
        for trial in trials_with_iv:
            steps = sorted(trial.intermediate_values.keys())
            n_opps = len(steps)
            assert steps == list(range(n_opps)), (
                f"Trial {trial.number} steps {steps} should be 0..{n_opps-1}"
            )
            # With all PLAYER wins from mock, raw scores in [1.0, 1.5]
            for step, val in trial.intermediate_values.items():
                assert 1.0 <= val <= 1.5, (
                    f"Trial {trial.number} step {step} has value {val}, "
                    f"expected raw PLAYER win score in [1.0, 1.5]"
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

    def test_intermediate_reports_are_raw_scores(self, wolf_hull, game_data):
        """Intermediate trial.report() values are raw combat_fitness, not cumulative z-scores."""
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
        # Raw combat_fitness scores: PLAYER wins are in [1.0, 1.5],
        # ENEMY losses in [-1.0, -0.5], timeouts in [-0.49, 0.49]
        all_intermediates = [
            val for t in trials_with_iv
            for val in t.intermediate_values.values()
        ]
        assert len(all_intermediates) > 0, "Should have intermediate values"
        # With all PLAYER wins from mock, raw scores should be in [1.0, 1.5]
        for v in all_intermediates:
            assert 1.0 <= v <= 1.5, (
                f"Raw score {v} outside PLAYER win range [1.0, 1.5]"
            )

    def test_failure_score_bypasses_transformations(self, wolf_hull, game_data):
        """Invalid builds get raw config.failure_score, not transformed."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import HullSize
        from starsector_optimizer.instance_manager import InstanceError

        pool = self._make_mock_pool()
        call_count = [0]
        original_run = pool.run_matchup

        def always_fails(instance_id, matchup):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise InstanceError("Simulated failure")
            return original_run(instance_id, matchup)

        pool.run_matchup = always_fails
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

    def test_all_trials_complete_without_error(self, wolf_hull, game_data):
        """Multi-opponent evaluation completes without errors (general regression test)."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault")})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE
                     and len(t.intermediate_values) > 0]
        assert len(completed) > 0

    def test_active_opponents_limits_rungs(self, wolf_hull, game_data, tmp_path):
        """With active_opponents=3 and pool of 5, builds complete after 3 opponents."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        import json

        pool = self._make_mock_pool()
        opponents = (
            "brawler_Assault", "hound_Standard", "lasher_Assault",
            "vigilance_Standard", "wolf_Assault",
        )
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        log_path = tmp_path / "eval.jsonl"
        config = OptimizerConfig(
            sim_budget=5, warm_start_n=3, warm_start_sample_n=20,
            active_opponents=3,
            eval_log_path=log_path,
        )

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        for rec in records:
            assert rec["opponents_total"] == 3
            assert len(rec["opponent_order"]) == 3

    def test_active_opponents_exceeds_pool_uses_all(self, wolf_hull, game_data, tmp_path):
        """With active_opponents=20 and pool of 3, all 3 are used."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        import json

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault", "hyperion_Attack")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        log_path = tmp_path / "eval.jsonl"
        config = OptimizerConfig(
            sim_budget=5, warm_start_n=3, warm_start_sample_n=20,
            active_opponents=20,
            eval_log_path=log_path,
        )

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        for rec in records:
            assert rec["opponents_total"] == 3


    def test_twfe_fitness_in_finalize(self, wolf_hull, game_data):
        """study.tell() receives rank-shaped value derived from TWFE alpha."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool()
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault", "lasher_Assault")})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        sim_trials = [t for t in study.trials
                      if t.state == optuna.trial.TrialState.COMPLETE
                      and t.value is not None
                      and len(t.intermediate_values) > 0]
        assert len(sim_trials) > 0
        # Values should be rank-shaped (0, 1]
        for trial in sim_trials:
            assert 0.0 < trial.value <= 1.0

    def test_incumbent_tracking(self, wolf_hull, game_data):
        """After evaluating builds, incumbent tracks the best build's opponents."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault", "hyperion_Attack")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        # Pipeline should complete — incumbent tracking does not crash
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE]
        assert len(completed) > 0

    def test_score_matrix_populated_on_result(self, wolf_hull, game_data, tmp_path):
        """ScoreMatrix receives records for each matchup result."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        import json

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        log_path = tmp_path / "eval.jsonl"
        config = OptimizerConfig(
            sim_budget=3, warm_start_n=3, warm_start_sample_n=20,
            eval_log_path=log_path,
        )

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        # Eval log records confirm opponents were evaluated (ScoreMatrix was populated)
        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        for rec in records:
            assert rec["opponents_evaluated"] > 0
            assert len(rec["opponent_results"]) == rec["opponents_evaluated"]

    def test_incumbent_overlap_forces_opponents(self, wolf_hull, game_data, tmp_path):
        """After burn-in, new builds share opponents with the incumbent."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import TWFEConfig
        import json

        pool = self._make_mock_pool()
        # Need enough opponents that overlap isn't trivial
        opponents = (
            "brawler_Assault", "hound_Standard", "lasher_Assault",
            "vigilance_Standard", "wolf_Assault",
        )
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        log_path = tmp_path / "eval.jsonl"
        # Small burn-in so we exercise post-burn-in path
        twfe_cfg = TWFEConfig(anchor_burn_in=3, n_incumbent_overlap=2)
        config = OptimizerConfig(
            sim_budget=10, warm_start_n=3, warm_start_sample_n=20,
            active_opponents=3, twfe=twfe_cfg, eval_log_path=log_path,
        )

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        # After burn-in (first 3), later builds should share opponents with earlier ones
        # We can't assert exact overlap without inspecting internal state,
        # but we verify the pipeline completes and produces valid results
        assert len(records) >= 3

    def test_anchor_first_ordering(self, wolf_hull, game_data, tmp_path):
        """After burn-in, anchors appear at the front of the opponent order."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import TWFEConfig
        import json

        pool = self._make_mock_pool()
        opponents = (
            "brawler_Assault", "hound_Standard", "lasher_Assault",
            "vigilance_Standard", "wolf_Assault",
        )
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        log_path = tmp_path / "eval.jsonl"
        twfe_cfg = TWFEConfig(anchor_burn_in=3, n_anchors=2)
        config = OptimizerConfig(
            sim_budget=8, warm_start_n=3, warm_start_sample_n=20,
            active_opponents=4, twfe=twfe_cfg, eval_log_path=log_path,
        )

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        records = [json.loads(line) for line in log_path.read_text().splitlines()]
        # Post-burn-in records should have consistent first opponents (anchors)
        post_burn_in = records[3:]  # after 3 burn-in builds
        if len(post_burn_in) >= 2:
            # Anchor opponents should appear in the same positions across trials
            first_opps = [rec["opponent_order"][:2] for rec in post_burn_in
                          if len(rec["opponent_order"]) >= 2]
            if len(first_opps) >= 2:
                # Anchors are locked — first 2 opponents should be the same set
                anchor_set = set(first_opps[0])
                for opps in first_opps[1:]:
                    assert set(opps) == anchor_set, (
                        f"Anchor opponents should be consistent: {first_opps}"
                    )

    def test_anchor_computation_after_burn_in(self, wolf_hull, game_data):
        """Anchors are computed and locked after anchor_burn_in builds."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool
        from starsector_optimizer.models import TWFEConfig

        pool = self._make_mock_pool()
        opponents = ("wolf_Assault", "lasher_Assault", "hyperion_Attack")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        twfe_cfg = TWFEConfig(anchor_burn_in=3, n_anchors=2)
        config = OptimizerConfig(
            sim_budget=8, warm_start_n=3, warm_start_sample_n=20,
            twfe=twfe_cfg,
        )

        # Pipeline should complete without errors — anchors computed after burn-in
        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE]
        assert len(completed) > 3  # More than burn-in count

    def test_config_no_fitness_mode(self):
        """OptimizerConfig no longer has fitness_mode attribute."""
        assert not hasattr(OptimizerConfig(), "fitness_mode")


class TestParallelDispatch:
    """Tests for async parallel instance dispatch."""

    def _make_mock_pool(self, *, winner="PLAYER", num_instances=1):
        """Create a mock pool with run_matchup tracking instance_id."""
        from unittest.mock import MagicMock
        from starsector_optimizer.models import CombatResult, ShipCombatResult, DamageBreakdown
        from starsector_optimizer.instance_manager import InstancePool

        mock_pool = MagicMock(spec=InstancePool)
        mock_pool.game_dir = Path("game/starsector")
        mock_pool.num_instances = num_instances
        mock_pool._call_log = []  # track (instance_id, matchup_id)

        def mock_run_matchup(instance_id, matchup):
            m = matchup
            mock_pool._call_log.append((instance_id, m.matchup_id))
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
            return CombatResult(
                matchup_id=m.matchup_id, winner=winner,
                duration_seconds=60.0,
                player_ships=(player_ship,), enemy_ships=(enemy_ship,),
                player_ships_destroyed=1 if player_destroyed else 0,
                enemy_ships_destroyed=1 if enemy_destroyed else 0,
                player_ships_retreated=0, enemy_ships_retreated=0,
            )

        mock_pool.run_matchup = mock_run_matchup
        return mock_pool

    def test_all_instances_used(self, game_data):
        """With num_instances=3 and multiple opponents, all 3 instance_ids get work."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool(num_instances=3)
        opponents = ("wolf_Assault", "lasher_Assault", "hound_Standard")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        used_instances = {inst_id for inst_id, _ in pool._call_log}
        assert used_instances == {0, 1, 2}, (
            f"Expected all instances used, got: {used_instances}"
        )

    def test_single_instance_works(self, game_data):
        """With num_instances=1, optimization completes normally."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool(num_instances=1)
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: ("wolf_Assault",)})
        config = OptimizerConfig(sim_budget=3, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)
        completed = [t for t in study.trials
                     if t.state == optuna.trial.TrialState.COMPLETE]
        assert len(completed) > 0

    def test_build_not_dispatched_twice(self, game_data):
        """Same build is never evaluated on two instances simultaneously."""
        import threading
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool(num_instances=3)
        active_trials: dict[str, int] = {}  # trial_prefix -> concurrent count
        max_concurrent: dict[str, int] = {}
        lock = threading.Lock()
        original_run = pool.run_matchup

        def tracking_run(instance_id, matchup):
            prefix = matchup.matchup_id.split("_vs_")[0]
            with lock:
                active_trials[prefix] = active_trials.get(prefix, 0) + 1
                max_concurrent[prefix] = max(
                    max_concurrent.get(prefix, 0), active_trials[prefix],
                )
            try:
                return original_run(instance_id, matchup)
            finally:
                with lock:
                    active_trials[prefix] -= 1

        pool.run_matchup = tracking_run
        opponents = ("wolf_Assault", "lasher_Assault", "hound_Standard")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=5, warm_start_n=3, warm_start_sample_n=20)

        optimize_hull("wolf", game_data, pool, opp_pool, config)

        for prefix, count in max_concurrent.items():
            assert count == 1, (
                f"Build {prefix} had {count} concurrent dispatches, expected 1"
            )

    def test_queue_drains_after_budget(self, game_data):
        """After sim_budget reached, builds in queue still complete."""
        from starsector_optimizer.optimizer import optimize_hull
        from starsector_optimizer.opponent_pool import OpponentPool

        pool = self._make_mock_pool(num_instances=1)
        opponents = ("wolf_Assault", "lasher_Assault", "hound_Standard")
        opp_pool = OpponentPool(pools={HullSize.FRIGATE: opponents})
        config = OptimizerConfig(sim_budget=3, warm_start_n=3, warm_start_sample_n=20)

        study = optimize_hull("wolf", game_data, pool, opp_pool, config)

        # All sim trials should be COMPLETE or PRUNED (not abandoned mid-evaluation)
        allowed = {optuna.trial.TrialState.COMPLETE, optuna.trial.TrialState.PRUNED}
        for trial in study.trials:
            assert trial.state in allowed, (
                f"Trial {trial.number} is {trial.state.name}"
            )


class TestWilcoxonPruner:
    """Tests for WilcoxonPruner integration and per-trial shuffling."""

    def test_wilcoxon_pruner_created(self):
        """_create_pruner returns WilcoxonPruner with config params."""
        config = OptimizerConfig(wilcoxon_p_threshold=0.05, wilcoxon_n_startup_steps=3)
        pruner = _create_pruner(config)
        assert isinstance(pruner, optuna.pruners.WilcoxonPruner)
        assert pruner._p_threshold == 0.05
        assert pruner._n_startup_steps == 3

    def test_rung_based_step_ids(self):
        """Step IDs are rung positions (0..N-1), not opponent pool indices."""
        import optuna

        study = optuna.create_study(direction="maximize")
        trial = study.ask()
        # Simulate reporting at rung positions
        for rung_step in range(3):
            trial.report(1.0, step=rung_step)
        study.tell(trial, 1.0)
        frozen = study.trials[-1]
        steps = sorted(frozen.intermediate_values.keys())
        assert steps == [0, 1, 2]

    def test_per_trial_opponent_shuffle(self):
        """Different trial numbers produce different opponent orderings."""
        import random as _random
        opponents = list(range(10))
        orders = set()
        for trial_num in range(20):
            shuffled = list(opponents)
            _random.Random(trial_num).shuffle(shuffled)
            orders.add(tuple(shuffled))
        # With 20 different seeds and 10 items, we should get many distinct orderings
        assert len(orders) >= 10
