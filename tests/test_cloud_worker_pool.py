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
        "player_loadout_diagnostics": [],
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
        max_requeues=5,
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


class TestLoadoutMismatchDiscard:
    """Wave 1 C1 surfaced a 0.59% cross-trial loadout-bleed bug: workers
    occasionally apply trial N+1's spec to trial N's matchup. The Java root
    cause is unsolved; the band-aid in cloud_worker_pool drops corrupt
    results so the janitor re-queues them, instead of feeding false data
    into fitness aggregation. See task #89 / docs/reports/2026-05-10-wave1-validation.md."""

    @staticmethod
    def _mismatch_diagnostic_json() -> dict:
        return {
            "fleet_member_id": "fm-mismatch",
            "spec_weapons": {"WS 001": "flak"},
            "live_weapons": {"WS 001": "arbalest"},  # different
            "spec_hullmods": ["heavyarmor"],
            "live_hullmods": ["heavyarmor"],
            "spec_flux_vents": 0,
            "live_flux_vents": 0,
            "spec_flux_capacitors": 0,
            "live_flux_capacitors": 0,
            "weapons_match": False,
            "hullmods_match": True,
            "flux_vents_match": True,
            "flux_capacitors_match": True,
        }

    def test_post_with_mismatch_returns_422_and_does_not_store(
        self, pool, flask_test_client_factory,
    ):
        client = flask_test_client_factory(pool.app)
        body = _combat_result_json("m-mismatch")
        body["player_loadout_diagnostics"] = [self._mismatch_diagnostic_json()]
        resp = client.post("/result", json={
            "matchup_id": "m-mismatch",
            "result": body,
            "bearer_token": BEARER,
        })
        assert resp.status_code == 422
        # Not stored -> a subsequent matching POST for the same id must
        # succeed (would 409 if the prior call had registered it as seen)
        assert "m-mismatch" not in pool._seen
        assert "m-mismatch" not in pool._results

    def test_post_with_mismatch_then_clean_resubmit_succeeds(
        self, pool, flask_test_client_factory,
    ):
        """Matchup re-queued by janitor → resubmitted by another worker
        with a clean (matching) loadout → must be stored normally."""
        client = flask_test_client_factory(pool.app)
        bad_body = _combat_result_json("m-retry")
        bad_body["player_loadout_diagnostics"] = [self._mismatch_diagnostic_json()]
        bad_resp = client.post("/result", json={
            "matchup_id": "m-retry",
            "result": bad_body,
            "bearer_token": BEARER,
        })
        assert bad_resp.status_code == 422
        good_resp = client.post("/result", json={
            "matchup_id": "m-retry",
            "result": _combat_result_json("m-retry"),
            "bearer_token": BEARER,
        })
        assert good_resp.status_code == 200
        assert "m-retry" in pool._seen

    def test_empty_diagnostics_passes(self, pool, flask_test_client_factory):
        """Test fixtures + pre-V2 paths emit empty diagnostic lists. Empty
        means 'no signal', not 'mismatch' — must not be rejected."""
        client = flask_test_client_factory(pool.app)
        body = _combat_result_json("m-empty-diag")
        body["player_loadout_diagnostics"] = []
        resp = client.post("/result", json={
            "matchup_id": "m-empty-diag",
            "result": body,
            "bearer_token": BEARER,
        })
        assert resp.status_code == 200

    def test_high_mismatch_rate_aborts_run_matchup(
        self, pool, flask_test_client_factory,
    ):
        """If MISMATCH_ABORT_RATE is exceeded after MIN_SAMPLES observations,
        run_matchup must raise LoadoutMismatchAbort instead of dispatching.
        Surfaces a regressed Java fix or a stale jar before the run drains
        cloud budget on per-matchup retries."""
        from starsector_optimizer.cloud_worker_pool import (
            LoadoutMismatchAbort, MISMATCH_ABORT_MIN_SAMPLES, MISMATCH_ABORT_RATE,
        )
        client = flask_test_client_factory(pool.app)
        # Drive the rate above MISMATCH_ABORT_RATE: pump in ≥
        # MIN_SAMPLES observations where >RATE are mismatches. We use 20%
        # mismatches over 50 samples (rate=0.20 > 0.05).
        n_total = MISMATCH_ABORT_MIN_SAMPLES
        n_bad = int(n_total * 0.20)
        for i in range(n_total):
            mid = f"m-rate-{i}"
            body = _combat_result_json(mid)
            if i < n_bad:
                body["player_loadout_diagnostics"] = [
                    self._mismatch_diagnostic_json()
                ]
            else:
                body["player_loadout_diagnostics"] = []
            client.post("/result", json={
                "matchup_id": mid,
                "result": body,
                "bearer_token": BEARER,
            })
        assert pool._mismatch_discard_count == n_bad
        assert (n_bad / n_total) > MISMATCH_ABORT_RATE
        # run_matchup must raise on the next call.
        from starsector_optimizer.models import MatchupConfig
        sentinel = MagicMock(spec=MatchupConfig)
        sentinel.matchup_id = "after-abort"
        with pytest.raises(LoadoutMismatchAbort, match="exceeded"):
            pool.run_matchup(sentinel)

    def test_low_mismatch_rate_does_not_abort(
        self, pool, flask_test_client_factory,
    ):
        """Below MISMATCH_ABORT_RATE the empirical noise level — must NOT
        abort. Otherwise normal Wave-2 ~0.6% rates would falsely trip."""
        from starsector_optimizer.cloud_worker_pool import (
            MISMATCH_ABORT_MIN_SAMPLES,
        )
        client = flask_test_client_factory(pool.app)
        # 1 mismatch in 100 = 1% < 5%.
        n_total = max(MISMATCH_ABORT_MIN_SAMPLES, 100)
        for i in range(n_total):
            mid = f"m-low-{i}"
            body = _combat_result_json(mid)
            if i == 0:
                body["player_loadout_diagnostics"] = [
                    self._mismatch_diagnostic_json()
                ]
            else:
                body["player_loadout_diagnostics"] = []
            client.post("/result", json={
                "matchup_id": mid,
                "result": body,
                "bearer_token": BEARER,
            })
        # run_matchup's pre-flight check must NOT raise.
        pool._check_mismatch_rate()  # asserts no raise


class TestStalledProgressDetector:
    """Diagnostic guardrail: when no /result POST has arrived for
    STALLED_PROGRESS_WARN_SECONDS AND queue work is pending, the
    janitor logs WARN with in-flight matchup IDs. Catches the
    failure mode that cost 6,596 results on 2026-05-10 (workers
    silent for 1h20m before operator noticed)."""

    def test_warn_when_idle_and_queue_nonempty(
        self, pool, caplog,
    ):
        from starsector_optimizer.cloud_worker_pool import (
            STALLED_PROGRESS_WARN_SECONDS,
        )
        # Queue something so source_queue > 0.
        pool._redis.lpush(pool._source, json.dumps({
            "matchup_id": "in-flight-A",
            "matchup": {},
        }))
        # Simulate idle: rewind _last_post_at past the threshold.
        pool._last_post_at = time.time() - STALLED_PROGRESS_WARN_SECONDS - 10
        with caplog.at_level("WARNING"):
            pool._check_stalled_progress()
        records = [r for r in caplog.records if "stalled progress" in r.message]
        assert len(records) == 1
        assert "in-flight-A" not in records[0].message  # source, not processing
        # Debounced: second call must NOT re-emit.
        caplog.clear()
        with caplog.at_level("WARNING"):
            pool._check_stalled_progress()
        assert not [r for r in caplog.records if "stalled progress" in r.message]

    def test_no_warn_when_queue_empty(self, pool, caplog):
        from starsector_optimizer.cloud_worker_pool import (
            STALLED_PROGRESS_WARN_SECONDS,
        )
        # No items queued.
        pool._last_post_at = time.time() - STALLED_PROGRESS_WARN_SECONDS - 10
        with caplog.at_level("WARNING"):
            pool._check_stalled_progress()
        # Empty queue means dispatcher is finished, not stalled.
        assert not [r for r in caplog.records if "stalled progress" in r.message]

    def test_no_warn_when_recently_posted(self, pool, caplog):
        # Just had a POST → not stalled even with pending work.
        pool._redis.lpush(pool._source, json.dumps({
            "matchup_id": "fresh", "matchup": {},
        }))
        pool._last_post_at = time.time()
        with caplog.at_level("WARNING"):
            pool._check_stalled_progress()
        assert not [r for r in caplog.records if "stalled progress" in r.message]

    def test_post_resets_idle_timer_and_debounce(
        self, pool, flask_test_client_factory,
    ):
        """Any /result POST (200 or 422) resets the stalled debounce —
        operator should see a fresh WARN if the workers stall again
        after recovery."""
        from starsector_optimizer.cloud_worker_pool import (
            STALLED_PROGRESS_WARN_SECONDS,
        )
        client = flask_test_client_factory(pool.app)
        # Enter the stalled state.
        pool._stalled_warn_emitted = True
        pool._last_post_at = time.time() - STALLED_PROGRESS_WARN_SECONDS - 1
        # A clean POST clears the debounce + updates last_post_at.
        body = _combat_result_json("recover-1")
        body["player_loadout_diagnostics"] = []
        resp = client.post("/result", json={
            "matchup_id": "recover-1",
            "result": body,
            "bearer_token": BEARER,
        })
        assert resp.status_code == 200
        assert pool._stalled_warn_emitted is False
        assert (time.time() - pool._last_post_at) < 5.0


class TestModJarConsistency:
    """Workers report mod-jar SHA in heartbeat; janitor logs WARN if the
    fleet is heterogeneous (some workers picked up a tailnet override
    and others didn't). Catches the silent-stale-jar failure mode that
    serve_mod_jar.sh introduces."""

    def _seed_heartbeat(self, fake_redis, project_tag, worker_id, sha):
        key = f"worker:{project_tag}:{worker_id}:heartbeat"
        fake_redis.hset(key, mapping={
            "timestamp": time.time(),
            "mod_jar_sha256": sha,
        })

    def test_homogeneous_fleet_no_warn(self, pool, caplog):
        sha = "a" * 64
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-001", sha)
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-002", sha)
        with caplog.at_level("WARNING"):
            pool._check_mod_jar_consistency()
        assert not [r for r in caplog.records if "heterogeneous mod-jar" in r.message]

    def test_heterogeneous_fleet_logs_warn(self, pool, caplog):
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-001", "a" * 64)
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-002", "b" * 64)
        with caplog.at_level("WARNING"):
            pool._check_mod_jar_consistency()
        records = [r for r in caplog.records if "heterogeneous mod-jar" in r.message]
        assert len(records) == 1
        assert "2 distinct" in records[0].message

    def test_warn_is_debounced(self, pool, caplog):
        from starsector_optimizer.cloud_worker_pool import (
            HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS,
        )
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-001", "a" * 64)
        self._seed_heartbeat(pool._redis, pool._project_tag, "w-002", "b" * 64)
        with caplog.at_level("WARNING"):
            pool._check_mod_jar_consistency()
            pool._check_mod_jar_consistency()  # within debounce window
        records = [r for r in caplog.records if "heterogeneous mod-jar" in r.message]
        assert len(records) == 1
        # Rewind past debounce: WARN re-emits.
        pool._last_jar_warn_at = time.time() - HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS - 1
        caplog.clear()
        with caplog.at_level("WARNING"):
            pool._check_mod_jar_consistency()
        assert [r for r in caplog.records if "heterogeneous mod-jar" in r.message]

    def test_no_workers_no_warn(self, pool, caplog):
        with caplog.at_level("WARNING"):
            pool._check_mod_jar_consistency()
        assert not [r for r in caplog.records if "heterogeneous mod-jar" in r.message]


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
            max_requeues=5,
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
        from tests.conftest import make_pass_diagnostic
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
            player_loadout_diagnostics=make_pass_diagnostic(1),
            engine_stats=EngineStats(8000.0, 600.0, 1500.0, 1.0, 0.0, 1.0),
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
            player_loadout_diagnostics=(),
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
