"""Tests for CloudWorkerPool — EvaluatorPool implementation using Redis + Flask."""

import ast
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from starsector_optimizer.evaluator_pool import EvaluatorPool
from starsector_optimizer.models import (
    BuildSpec, CombatResult, MatchupConfig, ShipCombatResult,
)


BEARER = "pool-bearer-zzz"


def _matchup(matchup_id: str = "m1") -> MatchupConfig:
    return MatchupConfig(
        matchup_id=matchup_id,
        player_builds=(BuildSpec(
            variant_id="v", hull_id="wolf",
            weapon_assignments={}, hullmods=(),
            flux_vents=0, flux_capacitors=0,
        ),),
        enemy_variants=("dominator_Assault",),
    )


def _combat_result_json(matchup_id: str) -> dict:
    return {
        "matchup_id": matchup_id,
        "winner": "player",
        "duration_seconds": 12.5,
        "player_ships": [],
        "enemy_ships": [],
        "aggregate": {
            "player_ships_destroyed": 0,
            "enemy_ships_destroyed": 1,
            "player_ships_retreated": 0,
            "enemy_ships_retreated": 0,
        },
    }


@pytest.fixture
def pool(fake_redis):
    from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
    p = CloudWorkerPool(
        study_id="wolf__early__seed0",
        redis_client=fake_redis,
        flask_port=0,
        bearer_token=BEARER,
        workers_per_study=4,
        result_timeout_seconds=2.0,
        visibility_timeout_seconds=120.0,
        janitor_interval_seconds=0.1,
    )
    p.setup()
    yield p
    p.teardown()


class TestPoolHappyPath:
    def test_enqueue_then_receive_result(self, pool, flask_test_client_factory):
        """Simulated worker POSTs result → run_matchup returns CombatResult."""
        matchup = _matchup("m1")
        client = flask_test_client_factory(pool.app)

        holder = {}

        def _run():
            holder["result"] = pool.run_matchup(matchup)

        t = threading.Thread(target=_run)
        t.start()
        time.sleep(0.05)
        resp = client.post("/result", json={
            "matchup_id": "m1",
            "result": _combat_result_json("m1"),
            "bearer_token": BEARER,
        })
        assert resp.status_code == 200
        t.join(timeout=3.0)
        assert holder["result"].matchup_id == "m1"


class TestDedup:
    def test_second_post_with_same_id_returns_409(self, pool, flask_test_client_factory):
        matchup = _matchup("m2")
        client = flask_test_client_factory(pool.app)

        def _run():
            pool.run_matchup(matchup)

        t = threading.Thread(target=_run)
        t.start()
        time.sleep(0.05)
        r1 = client.post("/result", json={
            "matchup_id": "m2",
            "result": _combat_result_json("m2"),
            "bearer_token": BEARER,
        })
        assert r1.status_code == 200
        t.join(timeout=3.0)

        r2 = client.post("/result", json={
            "matchup_id": "m2",
            "result": _combat_result_json("m2"),
            "bearer_token": BEARER,
        })
        assert r2.status_code == 409


class TestAuth:
    def test_bad_bearer_returns_401(self, pool, flask_test_client_factory):
        client = flask_test_client_factory(pool.app)
        resp = client.post("/result", json={
            "matchup_id": "m-whatever",
            "result": _combat_result_json("m-whatever"),
            "bearer_token": "wrong",
        })
        assert resp.status_code == 401


class TestTimeout:
    def test_timeout_on_no_result(self, fake_redis):
        from starsector_optimizer.cloud_worker_pool import (
            CloudWorkerPool, WorkerTimeout,
        )
        p = CloudWorkerPool(
            study_id="wolf__early__seed0",
            redis_client=fake_redis,
            flask_port=0, bearer_token=BEARER,
            workers_per_study=1,
            result_timeout_seconds=0.3,
            visibility_timeout_seconds=120.0,
            janitor_interval_seconds=60.0,
        )
        p.setup()
        try:
            with pytest.raises(WorkerTimeout):
                p.run_matchup(_matchup("m-slow"))
        finally:
            p.teardown()


class TestAttackSurface:
    def test_rejects_get_on_result(self, pool, flask_test_client_factory):
        client = flask_test_client_factory(pool.app)
        assert client.get("/result").status_code in (404, 405)

    def test_rejects_admin_routes(self, pool, flask_test_client_factory):
        client = flask_test_client_factory(pool.app)
        for route in ("/admin", "/config", "/patch", "/shutdown"):
            assert client.get(route).status_code == 404
            assert client.post(route).status_code == 404


class TestPoolContract:
    def test_implements_evaluator_pool(self):
        from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
        assert issubclass(CloudWorkerPool, EvaluatorPool)

    def test_source_file_has_no_repair_import(self):
        """cloud_worker_pool.py must not import starsector_optimizer.repair."""
        path = Path(__file__).parent.parent / "src" / "starsector_optimizer" / "cloud_worker_pool.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "repair" not in module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "repair" not in alias.name
