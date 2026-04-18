"""EvaluatorPool — cross-backend contract for matchup dispatch.

Two concrete subclasses ship: LocalInstancePool (instance_manager.py,
--worker-pool local) and CloudWorkerPool (cloud_worker_pool.py,
--worker-pool cloud). StagedEvaluator depends only on this ABC —
isinstance checks against the concrete classes are a lint failure.
"""

from __future__ import annotations

import abc

from .models import CombatResult, MatchupConfig


class EvaluatorPool(abc.ABC):
    """Abstract pool of matchup evaluators.

    Pool owns concurrency internally. StagedEvaluator calls run_matchup
    from up to num_workers threads concurrently; the pool serializes
    access to a free worker.
    """

    @abc.abstractmethod
    def setup(self) -> None:
        """Initialize pool resources (work dirs, Redis listener, etc.)."""

    @abc.abstractmethod
    def teardown(self) -> None:
        """Release pool resources."""

    @abc.abstractmethod
    def run_matchup(self, matchup: MatchupConfig) -> CombatResult:
        """Evaluate one matchup and return its CombatResult. Blocks until done."""

    @property
    @abc.abstractmethod
    def num_workers(self) -> int:
        """Number of concurrent matchups this pool can dispatch. StagedEvaluator
        uses this to size its ThreadPoolExecutor.
        """

    def __enter__(self) -> "EvaluatorPool":
        self.setup()
        return self

    def __exit__(self, *args) -> None:
        self.teardown()
