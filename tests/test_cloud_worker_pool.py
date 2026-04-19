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
    """Matches `dataclasses.asdict(CombatResult(...))` — the schema the worker
    actually posts (not the raw Java harness JSON). Flat aggregates + flat
    overload_count per ship (no `aggregate` or `flux_stats` wrappers)."""
    return {
        "matchup_id": matchup_id,
        "winner": "player",
        "duration_seconds": 12.5,
        "player_ships": [],
        "enemy_ships": [],
        "player_ships_destroyed": 0,
        "enemy_ships_destroyed": 1,
        "player_ships_retreated": 0,
        "enemy_ships_retreated": 0,
        "engine_stats": None,
    }


PROJECT_TAG = "starsector-pool-test"


@pytest.fixture
def pool(fake_redis):
    from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
    p = CloudWorkerPool(
        study_id="wolf__early__seed0",
        project_tag=PROJECT_TAG,
        redis_client=fake_redis,
        flask_port=0,
        bearer_token=BEARER,
        total_matchup_slots=4,
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
            project_tag=PROJECT_TAG,
            redis_client=fake_redis,
            flask_port=0, bearer_token=BEARER,
            total_matchup_slots=1,
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


class TestDictToCombatResultRoundTrip:
    """Regression — `dataclasses.asdict(result)` on the worker must round-trip
    through `_dict_to_combat_result` on the orchestrator. The previous
    implementation reused `result_parser.parse_combat_result` which expects
    the raw Java harness JSON (nested `flux_stats` / `aggregate` blocks) and
    would KeyError on the flat asdict schema."""

    def test_roundtrip_full_combat_result(self):
        import dataclasses
        from starsector_optimizer.cloud_worker_pool import _dict_to_combat_result
        from starsector_optimizer.models import (
            CombatResult, DamageBreakdown, EngineStats, ShipCombatResult,
        )

        ship = ShipCombatResult(
            fleet_member_id="fm1", variant_id="v1", hull_id="hammerhead",
            destroyed=False, hull_fraction=0.83, armor_fraction=0.61,
            cr_remaining=0.7, peak_time_remaining=120.0,
            disabled_weapons=1, flameouts=0,
            damage_dealt=DamageBreakdown(shield=100.0, armor=50.0, hull=25.0, emp=0.0),
            damage_taken=DamageBreakdown(shield=40.0, armor=10.0, hull=5.0, emp=0.0),
            overload_count=2,
        )
        original = CombatResult(
            matchup_id="mid-123",
            winner="PLAYER",
            duration_seconds=45.6,
            player_ships=(ship,),
            enemy_ships=(),
            player_ships_destroyed=0,
            enemy_ships_destroyed=1,
            player_ships_retreated=0,
            enemy_ships_retreated=0,
            engine_stats=EngineStats(8000.0, 600.0, 1500.0),
        )

        as_dict = dataclasses.asdict(original)
        reconstructed = _dict_to_combat_result(as_dict)
        assert reconstructed == original

    def test_roundtrip_accepts_none_engine_stats(self):
        import dataclasses
        from starsector_optimizer.cloud_worker_pool import _dict_to_combat_result
        from starsector_optimizer.models import CombatResult
        original = CombatResult(
            matchup_id="m-no-engine", winner="TIMEOUT",
            duration_seconds=0.0,
            player_ships=(), enemy_ships=(),
            player_ships_destroyed=0, enemy_ships_destroyed=0,
            player_ships_retreated=0, enemy_ships_retreated=0,
            engine_stats=None,
        )
        assert _dict_to_combat_result(dataclasses.asdict(original)) == original


class TestPoolContract:
    def test_implements_evaluator_pool(self):
        from starsector_optimizer.cloud_worker_pool import CloudWorkerPool
        assert issubclass(CloudWorkerPool, EvaluatorPool)

    def test_num_workers_returns_total_matchup_slots(self, pool):
        """num_workers is what StagedEvaluator uses to size its
        ThreadPoolExecutor. It MUST be total slots, not VM count, otherwise
        half the JVMs sit idle."""
        assert pool.num_workers == 4

    def test_redis_keys_are_scoped_by_project_tag(self, pool, fake_redis):
        """Two campaigns with the same study_id must not collide in Redis."""
        from starsector_optimizer.cloud_worker_pool import (
            _source_key, _processing_key,
        )
        assert _source_key("starsector-A", "s") != _source_key("starsector-B", "s")
        assert _processing_key("starsector-A", "s") != _processing_key("starsector-B", "s")
        assert pool._source.startswith(f"queue:{PROJECT_TAG}:")

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
