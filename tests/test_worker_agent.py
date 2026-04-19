"""Tests for worker_agent.py — runs on cloud VM; pulls from Redis, POSTs result."""

import ast
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


BEARER = "bearer-testing-abc"


@pytest.fixture
def worker_config():
    from starsector_optimizer.models import WorkerConfig
    return WorkerConfig(
        campaign_id="c1", study_id="wolf__early__seed0",
        redis_host="127.0.0.1", redis_port=6379,
        http_endpoint="http://127.0.0.1:9000/result",
        bearer_token=BEARER,
        max_lifetime_hours=0.0001,
        http_retry_count=2,
        http_retry_base_seconds=0.01,
        http_retry_max_seconds=0.05,
        worker_id="i-0000",
    )


class TestWorkerAgentQueue:
    def test_worker_pulls_from_source_queue(self, worker_config, fake_redis):
        from starsector_optimizer.worker_agent import claim_matchup
        source = f"queue:{worker_config.study_id}:source"
        processing = f"queue:{worker_config.study_id}:processing"
        fake_redis.lpush(source, json.dumps({"matchup_id": "m1"}))
        item = claim_matchup(fake_redis, source, processing, timeout=1)
        assert item["matchup_id"] == "m1"
        assert fake_redis.llen(processing) == 1

    def test_claim_returns_none_on_timeout(self, worker_config, fake_redis):
        from starsector_optimizer.worker_agent import claim_matchup
        source = f"queue:{worker_config.study_id}:source"
        processing = f"queue:{worker_config.study_id}:processing"
        item = claim_matchup(fake_redis, source, processing, timeout=1)
        assert item is None


class TestWorkerAgentPost:
    def test_post_includes_bearer_token(self, worker_config):
        from starsector_optimizer.worker_agent import post_result
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            post_result(worker_config, matchup_id="m1", result={"foo": "bar"})
        call = mock_post.call_args
        body = call.kwargs.get("json") or call.args[1]
        assert body["bearer_token"] == BEARER
        assert body["matchup_id"] == "m1"

    def test_post_retries_on_transient_failure(self, worker_config):
        from starsector_optimizer.worker_agent import post_result
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=500),
                MagicMock(status_code=500),
                MagicMock(status_code=200),
            ]
            with patch("time.sleep"):
                post_result(worker_config, matchup_id="m1", result={"foo": "bar"})
        assert mock_post.call_count <= worker_config.http_retry_count + 1

    def test_post_401_hard_fails(self, worker_config):
        from starsector_optimizer.worker_agent import post_result, AuthError
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 401
            with pytest.raises(AuthError):
                post_result(worker_config, matchup_id="m1", result={"foo": "bar"})


class TestJanitor:
    def test_janitor_requeues_stuck(self, fake_redis):
        from starsector_optimizer.campaign import run_janitor_pass
        source = "queue:s:source"
        processing = "queue:s:processing"
        stuck = json.dumps({"matchup_id": "m1", "enqueued_at": time.time() - 9999})
        fake_redis.lpush(processing, stuck)
        run_janitor_pass(fake_redis, source, processing, visibility_timeout_seconds=120.0)
        assert fake_redis.llen(source) == 1
        assert fake_redis.llen(processing) == 0

    def test_janitor_leaves_fresh_alone(self, fake_redis):
        from starsector_optimizer.campaign import run_janitor_pass
        source = "queue:s:source"
        processing = "queue:s:processing"
        fresh = json.dumps({"matchup_id": "m1", "enqueued_at": time.time()})
        fake_redis.lpush(processing, fresh)
        run_janitor_pass(fake_redis, source, processing, visibility_timeout_seconds=120.0)
        assert fake_redis.llen(source) == 0
        assert fake_redis.llen(processing) == 1


class TestWorkerLifetime:
    def test_self_terminates_on_lifetime_cap(self, worker_config, fake_redis):
        from starsector_optimizer.worker_agent import should_exit
        # max_lifetime_hours=0.0001 (~0.36 seconds)
        started_at = time.monotonic() - 1.0
        assert should_exit(worker_config, started_at) is True


class TestLoadWorkerConfigFromEnv:
    """load_worker_config_from_env iterates dataclasses.fields(WorkerConfig) and
    resolves types via typing.get_type_hints (handles PEP-563 string
    annotations). Every STARSECTOR_WORKER_<FIELD> is read; missing required
    field raises ValueError; unknown coercion target raises TypeError.
    """

    _REQUIRED_ENV = {
        "STARSECTOR_WORKER_CAMPAIGN_ID": "c-all",
        "STARSECTOR_WORKER_STUDY_ID": "wolf__early__seed0",
        "STARSECTOR_WORKER_REDIS_HOST": "100.64.0.2",
        "STARSECTOR_WORKER_REDIS_PORT": "6379",
        "STARSECTOR_WORKER_HTTP_ENDPOINT": "http://100.64.0.2:9000/result",
        "STARSECTOR_WORKER_BEARER_TOKEN": "tok-xyz",
    }

    def test_reads_every_dataclass_field_from_env(self, monkeypatch):
        """Set env for every field (required + optional) and check they all
        round-trip. This pins the contract: render_user_data writes every
        field; load_worker_config_from_env reads every field."""
        import dataclasses
        from starsector_optimizer.models import WorkerConfig
        from starsector_optimizer.worker_agent import load_worker_config_from_env

        # Build an env value per field keyed off its type.
        per_field_values = {
            "campaign_id": "c-FULL",
            "study_id": "wolf__mid__seed1",
            "redis_host": "100.64.7.42",
            "redis_port": "6380",
            "http_endpoint": "http://100.64.7.42:9050/result",
            "bearer_token": "tok-FULL",
            "max_lifetime_hours": "2.5",
            "http_retry_count": "7",
            "http_retry_base_seconds": "2.0",
            "http_retry_max_seconds": "60.0",
            "http_retry_backoff_multiplier": "3.0",
            "http_post_timeout_seconds": "45.0",
            "worker_poll_margin_seconds": "10.0",
            "num_instances_per_worker": "3",
            "worker_id": "i-0FULL",
        }
        # Sanity: every WorkerConfig field has a value in per_field_values.
        for f in dataclasses.fields(WorkerConfig):
            assert f.name in per_field_values, f"missing env setup for {f.name}"
            monkeypatch.setenv(
                f"STARSECTOR_WORKER_{f.name.upper()}",
                per_field_values[f.name],
            )
        cfg = load_worker_config_from_env()
        assert cfg.campaign_id == "c-FULL"
        assert cfg.study_id == "wolf__mid__seed1"
        assert cfg.redis_host == "100.64.7.42"
        assert cfg.redis_port == 6380
        assert cfg.http_endpoint == "http://100.64.7.42:9050/result"
        assert cfg.bearer_token == "tok-FULL"
        assert cfg.max_lifetime_hours == 2.5
        assert cfg.http_retry_count == 7
        assert cfg.http_retry_base_seconds == 2.0
        assert cfg.http_retry_max_seconds == 60.0
        assert cfg.http_retry_backoff_multiplier == 3.0
        assert cfg.http_post_timeout_seconds == 45.0
        assert cfg.worker_poll_margin_seconds == 10.0
        assert cfg.num_instances_per_worker == 3
        assert cfg.worker_id == "i-0FULL"

    def test_applies_defaults_when_optional_env_missing(self, monkeypatch):
        from starsector_optimizer.worker_agent import load_worker_config_from_env
        for k, v in self._REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        # Ensure optional vars are NOT set.
        for f in ("MAX_LIFETIME_HOURS", "HTTP_RETRY_COUNT",
                  "NUM_INSTANCES_PER_WORKER", "WORKER_ID"):
            monkeypatch.delenv(f"STARSECTOR_WORKER_{f}", raising=False)
        cfg = load_worker_config_from_env()
        assert cfg.max_lifetime_hours == 6.0          # WorkerConfig default
        assert cfg.http_retry_count == 3              # WorkerConfig default
        assert cfg.num_instances_per_worker == 2      # WorkerConfig default
        assert cfg.worker_id == ""                    # placeholder default

    def test_raises_value_error_on_missing_required(self, monkeypatch):
        from starsector_optimizer.worker_agent import load_worker_config_from_env
        for k, v in self._REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("STARSECTOR_WORKER_CAMPAIGN_ID", raising=False)
        with pytest.raises(ValueError, match="CAMPAIGN_ID"):
            load_worker_config_from_env()

    def test_coerces_int_and_float_types(self, monkeypatch):
        from starsector_optimizer.worker_agent import load_worker_config_from_env
        for k, v in self._REQUIRED_ENV.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("STARSECTOR_WORKER_NUM_INSTANCES_PER_WORKER", "5")
        monkeypatch.setenv("STARSECTOR_WORKER_MAX_LIFETIME_HOURS", "0.25")
        cfg = load_worker_config_from_env()
        assert cfg.num_instances_per_worker == 5
        assert isinstance(cfg.num_instances_per_worker, int)
        assert cfg.max_lifetime_hours == 0.25
        assert isinstance(cfg.max_lifetime_hours, float)


class TestRepairBoundary:
    def test_worker_agent_does_not_import_repair(self):
        """AST-scan: worker_agent.py must not import repair."""
        path = Path(__file__).parent.parent / "src" / "starsector_optimizer" / "worker_agent.py"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "repair" not in module, (
                    f"worker_agent.py imports from '{module}' — repair runs orchestrator-side only"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "repair" not in alias.name, (
                        f"worker_agent.py imports '{alias.name}' — repair runs orchestrator-side only"
                    )
