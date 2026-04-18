"""Tests for the EvaluatorPool ABC + subclass conformance."""

import abc

import pytest

from starsector_optimizer.evaluator_pool import EvaluatorPool
from starsector_optimizer.models import BuildSpec, CombatResult, MatchupConfig


def _dummy_matchup() -> MatchupConfig:
    return MatchupConfig(
        matchup_id="m1",
        player_builds=(BuildSpec(
            variant_id="v", hull_id="wolf",
            weapon_assignments={}, hullmods=(),
            flux_vents=0, flux_capacitors=0,
        ),),
        enemy_variants=("dominator_Assault",),
    )


class TestEvaluatorPoolABC:
    """EvaluatorPool is abc.ABC; concrete subclasses must implement abstract methods."""

    def test_evaluator_pool_is_abc(self):
        """Direct instantiation of the ABC raises TypeError."""
        with pytest.raises(TypeError):
            EvaluatorPool()

    def test_subclass_missing_run_matchup_fails(self):
        """Subclass that omits run_matchup cannot be instantiated."""
        class Incomplete(EvaluatorPool):
            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            @property
            def num_workers(self) -> int:
                return 0

        with pytest.raises(TypeError):
            Incomplete()

    def test_subclass_missing_num_workers_fails(self):
        """Subclass that omits num_workers cannot be instantiated."""
        class Incomplete(EvaluatorPool):
            def setup(self) -> None:
                pass

            def teardown(self) -> None:
                pass

            def run_matchup(self, matchup):
                return None

        with pytest.raises(TypeError):
            Incomplete()

    def test_complete_subclass_instantiates(self):
        """A subclass with every abstract method implemented works."""
        class Complete(EvaluatorPool):
            def setup(self) -> None:
                self.ready = True

            def teardown(self) -> None:
                self.ready = False

            def run_matchup(self, matchup):
                return matchup.matchup_id

            @property
            def num_workers(self) -> int:
                return 3

        pool = Complete()
        assert pool.num_workers == 3


class TestLocalPoolConformance:
    """LocalInstancePool implements EvaluatorPool."""

    def test_local_pool_is_subclass(self):
        from starsector_optimizer.instance_manager import LocalInstancePool
        assert issubclass(LocalInstancePool, EvaluatorPool)


class TestCloudPoolConformance:
    """CloudWorkerPool implements EvaluatorPool."""

    def test_cloud_pool_is_subclass(self):
        from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
        assert issubclass(CloudWorkerPool, EvaluatorPool)


class TestContextManager:
    """EvaluatorPool __enter__/__exit__ delegate to setup/teardown."""

    def test_context_manager_setup_teardown(self):
        class Recorder(EvaluatorPool):
            def __init__(self):
                self.events = []

            def setup(self) -> None:
                self.events.append("setup")

            def teardown(self) -> None:
                self.events.append("teardown")

            def run_matchup(self, matchup):
                return None

            @property
            def num_workers(self) -> int:
                return 0

        r = Recorder()
        with r as pool:
            assert pool is r
            assert r.events == ["setup"]
        assert r.events == ["setup", "teardown"]

    def test_context_manager_teardown_on_exception(self):
        """teardown runs even if the with-block raises."""
        class Recorder(EvaluatorPool):
            def __init__(self):
                self.events = []

            def setup(self) -> None:
                self.events.append("setup")

            def teardown(self) -> None:
                self.events.append("teardown")

            def run_matchup(self, matchup):
                return None

            @property
            def num_workers(self) -> int:
                return 0

        r = Recorder()
        with pytest.raises(RuntimeError):
            with r:
                raise RuntimeError("boom")
        assert r.events == ["setup", "teardown"]
