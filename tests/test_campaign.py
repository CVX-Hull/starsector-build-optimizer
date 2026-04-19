"""Tests for CampaignConfig, CostLedger, CampaignManager (Phase 6)."""

import atexit
import dataclasses
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


BEARER_TOKEN_SENTINEL = "bearer-xxxxxx-secret"
TAILSCALE_SECRET_SENTINEL = "tskey-auth-yyyyyy-secret"


def _minimal_campaign_yaml(tmp_path: Path, **overrides) -> Path:
    """Write a minimal campaign YAML file and return its path."""
    defaults = {
        "name": "test-campaign",
        "budget_usd": 10.0,
        "provider": "aws",
        "regions": ["us-east-1", "us-east-2"],
        "instance_types": ["c7a.2xlarge", "c7i.2xlarge"],
        "spot_allocation_strategy": "price-capacity-optimized",
        "capacity_rebalancing": True,
        "max_concurrent_workers": 96,
        "min_workers_to_start": 48,
        "partial_fleet_policy": "proceed_half_speed",
        "ami_ids_by_region": {"us-east-1": "ami-aaaa", "us-east-2": "ami-bbbb"},
        "ssh_key_name": "starsector-opt",
        "tailscale_authkey_secret": TAILSCALE_SECRET_SENTINEL,
        "studies": [
            {
                "hull": "wolf", "regime": "early",
                "seeds": [0], "budget_per_study": 200,
                "workers_per_study": 12, "sampler": "tpe",
            },
        ],
    }
    defaults.update(overrides)
    path = tmp_path / "campaign.yaml"
    path.write_text(yaml.safe_dump(defaults))
    return path


class TestCampaignConfigLoading:
    """CampaignConfig loads from YAML and validates."""

    def test_loads_yaml(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path)
        config = load_campaign_config(path)
        assert config.name == "test-campaign"
        assert config.budget_usd == 10.0
        assert config.provider == "aws"
        assert config.regions == ("us-east-1", "us-east-2")
        assert len(config.studies) == 1
        assert config.studies[0].hull == "wolf"

    def test_round_trip_preserves_fields(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, max_lifetime_hours=8.0)
        config = load_campaign_config(path)
        assert config.max_lifetime_hours == 8.0

    def test_min_workers_not_more_than_max(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, min_workers_to_start=120)
        with pytest.raises(ValueError, match="min_workers_to_start"):
            load_campaign_config(path)

    def test_unknown_provider_rejected(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, provider="gcp")
        with pytest.raises(ValueError, match="provider"):
            load_campaign_config(path)

    def test_unknown_sampler_rejected(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, studies=[
            {"hull": "wolf", "regime": "early", "seeds": [0],
             "budget_per_study": 200, "workers_per_study": 12,
             "sampler": "bogus"},
        ])
        with pytest.raises(ValueError, match="sampler"):
            load_campaign_config(path)


class TestFrozenDataclasses:
    """Every Phase 6 dataclass is frozen."""

    def test_campaign_config_frozen(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        config = load_campaign_config(_minimal_campaign_yaml(tmp_path))
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.budget_usd = 999.0

    def test_study_config_frozen(self):
        from starsector_optimizer.models import StudyConfig
        s = StudyConfig(
            hull="wolf", regime="early", seeds=(0,),
            budget_per_study=200, workers_per_study=12, sampler="tpe",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.hull = "eagle"

    def test_worker_config_frozen(self):
        from starsector_optimizer.models import WorkerConfig
        w = WorkerConfig(
            campaign_id="c", study_id="s", project_tag="starsector-c",
            redis_host="h", redis_port=6379,
            http_endpoint="http://h/result", bearer_token="t",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            w.bearer_token = "different"

    def test_cost_ledger_entry_frozen(self):
        from starsector_optimizer.models import CostLedgerEntry
        e = CostLedgerEntry(
            timestamp="t", event_type="worker_heartbeat",
            worker_id="w", region="us-east-1", instance_type="c7a.2xlarge",
            hours_elapsed=1.0, delta_usd=0.15, cumulative_usd=0.15,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            e.cumulative_usd = 999.0

    def test_global_auto_stop_config_frozen(self):
        from starsector_optimizer.models import GlobalAutoStopConfig
        g = GlobalAutoStopConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            g.on_budget = "soft"


class TestSecretRedaction:
    """__repr__ must redact secrets."""

    def test_campaign_config_repr_redacts_tailscale_secret(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        config = load_campaign_config(_minimal_campaign_yaml(tmp_path))
        text = repr(config)
        assert TAILSCALE_SECRET_SENTINEL not in text
        assert "REDACTED" in text

    def test_worker_config_repr_redacts_bearer_token(self):
        from starsector_optimizer.models import WorkerConfig
        w = WorkerConfig(
            campaign_id="c", study_id="s", project_tag="starsector-c",
            redis_host="h", redis_port=6379,
            http_endpoint="http://h/result",
            bearer_token=BEARER_TOKEN_SENTINEL,
        )
        text = repr(w)
        assert BEARER_TOKEN_SENTINEL not in text
        assert "REDACTED" in text


class TestCostLedger:
    """CostLedger: JSONL appending, fsync, warnings, budget cap, secret hygiene."""

    def _ledger(self, tmp_path, budget_usd=10.0, warn_thresholds=(0.5, 0.8, 0.95)):
        from starsector_optimizer.campaign import CostLedger
        return CostLedger(
            path=tmp_path / "ledger.jsonl",
            budget_usd=budget_usd,
            warn_thresholds=warn_thresholds,
        )

    def test_appends_heartbeat(self, tmp_path):
        ledger = self._ledger(tmp_path)
        ledger.record_heartbeat(
            worker_id="w1", region="us-east-1",
            instance_type="c7a.2xlarge",
            hours_elapsed=1 / 60, rate_usd_per_hr=0.15,
        )
        lines = (tmp_path / "ledger.jsonl").read_text().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["worker_id"] == "w1"
        assert row["cumulative_usd"] > 0

    def test_cumulative_monotone(self, tmp_path):
        ledger = self._ledger(tmp_path)
        for _ in range(5):
            ledger.record_heartbeat(
                worker_id="w1", region="us-east-1",
                instance_type="c7a.2xlarge",
                hours_elapsed=1 / 60, rate_usd_per_hr=0.15,
            )
        values = [
            json.loads(line)["cumulative_usd"]
            for line in (tmp_path / "ledger.jsonl").read_text().splitlines()
        ]
        assert values == sorted(values)

    def test_fsync_called_on_every_write(self, tmp_path):
        ledger = self._ledger(tmp_path)
        with patch("os.fsync") as mock_fsync:
            ledger.record_heartbeat(
                worker_id="w1", region="us-east-1",
                instance_type="c7a.2xlarge",
                hours_elapsed=1 / 60, rate_usd_per_hr=0.15,
            )
        mock_fsync.assert_called()

    def test_warns_at_configurable_thresholds(self, tmp_path, caplog):
        ledger = self._ledger(tmp_path, budget_usd=1.0, warn_thresholds=(0.5,))
        with caplog.at_level(logging.WARNING):
            ledger.record_heartbeat(
                worker_id="w1", region="us-east-1",
                instance_type="c7a.2xlarge",
                hours_elapsed=4.0, rate_usd_per_hr=0.15,
            )
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("50" in r.getMessage() or "0.5" in r.getMessage() for r in warnings)

    def test_warning_fires_only_once_per_threshold(self, tmp_path, caplog):
        ledger = self._ledger(tmp_path, budget_usd=10.0, warn_thresholds=(0.5,))
        with caplog.at_level(logging.WARNING):
            for _ in range(10):
                ledger.record_heartbeat(
                    worker_id="w1", region="us-east-1",
                    instance_type="c7a.2xlarge",
                    hours_elapsed=4.0, rate_usd_per_hr=0.15,
                )
        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        threshold_warnings = [m for m in warning_msgs if "0.5" in m or "50" in m]
        assert len(threshold_warnings) <= 2

    def test_hard_caps_at_budget(self, tmp_path):
        from starsector_optimizer.campaign import BudgetExceeded
        ledger = self._ledger(tmp_path, budget_usd=0.01)
        with pytest.raises(BudgetExceeded):
            ledger.record_heartbeat(
                worker_id="w1", region="us-east-1",
                instance_type="c7a.2xlarge",
                hours_elapsed=10.0, rate_usd_per_hr=0.15,
            )

    def test_no_secrets_in_rows(self, tmp_path):
        ledger = self._ledger(tmp_path)
        for _ in range(10):
            ledger.record_heartbeat(
                worker_id="w1", region="us-east-1",
                instance_type="c7a.2xlarge",
                hours_elapsed=1 / 60, rate_usd_per_hr=0.15,
            )
        content = (tmp_path / "ledger.jsonl").read_text()
        assert BEARER_TOKEN_SENTINEL not in content
        assert TAILSCALE_SECRET_SENTINEL not in content


class TestCampaignManager:
    """CampaignManager: pure supervisor — preflight, spawn subprocess per (study, seed),
    campaign-wide teardown sweep. No longer owns fleet provisioning (that moves to
    run_optimizer.py --worker-pool cloud subprocess)."""

    def _config(self, tmp_path, **overrides):
        from starsector_optimizer.campaign import load_campaign_config
        return load_campaign_config(_minimal_campaign_yaml(tmp_path, **overrides))

    def test_provision_fleet_method_removed(self, tmp_path):
        """Clean rewrite — provision_fleet no longer exists on CampaignManager."""
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        assert not hasattr(manager, "provision_fleet"), (
            "provision_fleet must be gone — fleet ownership moved to the study subprocess."
        )

    def test_partial_fleet_methods_removed(self, tmp_path):
        """partial_fleet_decide / log_partial_fleet_abort both removed with
        provision_fleet. Per-study subprocess handles its own provisioning."""
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        assert not hasattr(manager, "partial_fleet_decide")
        assert not hasattr(manager, "log_partial_fleet_abort")

    def test_partial_fleet_abort_exception_removed(self):
        """PartialFleetAbort exception class also removed (no remaining callers)."""
        import starsector_optimizer.campaign as mod
        assert not hasattr(mod, "PartialFleetAbort")

    def test_spawns_one_subprocess_per_study_seed(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        studies = [
            {"hull": "wolf", "regime": "early", "seeds": [0, 1, 2],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
        ]
        config = self._config(tmp_path, studies=studies)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        # _preflight must have run (workstation-side side effects stored); bypass
        # by calling spawn_studies directly with a prepared tailnet IP.
        manager._tailnet_ip = "100.64.1.2"

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies()
        assert mock_popen.call_count == 3  # one per seed

    def test_subprocess_gets_study_idx_and_seed_idx(self, tmp_path):
        """Flat-idx bug fix: subprocess must receive BOTH indexes so
        campaign.studies[study_idx].seeds[seed_idx] picks the correct seed."""
        from starsector_optimizer.campaign import CampaignManager
        studies = [
            {"hull": "wolf", "regime": "early", "seeds": [0, 1],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
            {"hull": "eagle", "regime": "early", "seeds": [0],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
        ]
        config = self._config(tmp_path, studies=studies)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager._tailnet_ip = "100.64.1.2"

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies()
        cmds = [call.args[0] for call in mock_popen.call_args_list]
        # 3 total subprocesses: (wolf, seed0), (wolf, seed1), (eagle, seed0).
        pairs = set()
        for cmd in cmds:
            study_idx = cmd[cmd.index("--study-idx") + 1]
            seed_idx = cmd[cmd.index("--seed-idx") + 1]
            pairs.add((int(study_idx), int(seed_idx)))
        assert pairs == {(0, 0), (0, 1), (1, 0)}

    def test_subprocess_gets_yaml_path_not_pickle(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager._tailnet_ip = "100.64.1.2"

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies()
        cmd = mock_popen.call_args_list[0].args[0]
        assert "--campaign-config" in cmd
        assert not any(b"pickle" in str(arg).encode() for arg in cmd)

    def test_subprocess_env_contains_required_secrets_and_ip(self, tmp_path):
        """Every subprocess gets STARSECTOR_WORKSTATION_TAILNET_IP,
        STARSECTOR_BEARER_TOKEN, STARSECTOR_TAILSCALE_AUTHKEY,
        STARSECTOR_PROJECT_TAG — the env plumbing contract."""
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager._tailnet_ip = "100.64.9.9"

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies()
        env = mock_popen.call_args_list[0].kwargs.get("env", {})
        assert env.get("STARSECTOR_WORKSTATION_TAILNET_IP") == "100.64.9.9"
        assert env.get("STARSECTOR_TAILSCALE_AUTHKEY") == TAILSCALE_SECRET_SENTINEL
        assert env.get("STARSECTOR_PROJECT_TAG") == "starsector-test-campaign"
        # Per-study bearer token: present, non-empty, not the tailscale secret.
        bearer = env.get("STARSECTOR_BEARER_TOKEN", "")
        assert bearer and bearer != TAILSCALE_SECRET_SENTINEL

    def test_each_study_gets_distinct_bearer_token(self, tmp_path):
        """Per-study secret isolation: N subprocesses → N distinct bearer tokens."""
        from starsector_optimizer.campaign import CampaignManager
        studies = [
            {"hull": "wolf", "regime": "early", "seeds": [0, 1, 2],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
            {"hull": "eagle", "regime": "early", "seeds": [0],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
        ]
        config = self._config(tmp_path, studies=studies)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager._tailnet_ip = "100.64.1.2"

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies()
        tokens = {
            call.kwargs["env"]["STARSECTOR_BEARER_TOKEN"]
            for call in mock_popen.call_args_list
        }
        assert len(tokens) == 4  # 3 + 1

    def test_teardown_calls_provider_terminate_with_project_tag(self, tmp_path):
        """Campaign-wide sweep backstop reaps by Project tag only."""
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager.teardown()
        provider.terminate_all_tagged.assert_called_with("starsector-test-campaign")

    def test_teardown_retries_then_succeeds(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.side_effect = [[{"id": "i-1"}], []]
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager.teardown()
        assert provider.terminate_all_tagged.call_count >= 2

    def test_teardown_raises_if_leaks_persist(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager, TeardownError
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = [{"id": "i-1"}]
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        with pytest.raises(TeardownError):
            manager.teardown()

    def test_atexit_registered(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        with patch("atexit.register") as mock_register:
            CampaignManager(config, provider, ledger)
        mock_register.assert_called()

    def test_signal_handlers_installed(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        with patch("signal.signal") as mock_signal:
            manager = CampaignManager(config, provider, ledger)
            manager.install_signal_handlers()
        installed = {call.args[0] for call in mock_signal.call_args_list}
        assert signal.SIGTERM in installed
        assert signal.SIGHUP in installed


class TestCampaignManagerPreflight:
    """_preflight checks: tailnet IP, Redis reachable on tailnet, AWS creds alive,
    authkey syntax. Failure → non-zero exit with clear remediation message."""

    def _config(self, tmp_path, **overrides):
        from starsector_optimizer.campaign import load_campaign_config
        return load_campaign_config(_minimal_campaign_yaml(tmp_path, **overrides))

    def _manager_with_mocks(self, tmp_path, **overrides):
        from starsector_optimizer.campaign import CampaignManager
        from starsector_optimizer.game_manifest import GameManifest
        config = self._config(tmp_path, **overrides)
        provider = MagicMock()
        provider.list_active.return_value = []
        # Preflight (Commit G R6) now dual-checks GameVersion AND
        # ModCommitSha. Mock to dispatch on tag_key so both succeed; tests
        # that want to verify mismatch paths override side_effect explicitly.
        _m = GameManifest.load()
        def _describe_ami_tag(*, ami_id, region, tag_key):
            if tag_key == "GameVersion":
                return _m.constants.game_version
            if tag_key == "ModCommitSha":
                return _m.constants.mod_commit_sha
            raise KeyError(tag_key)
        provider.describe_ami_tag.side_effect = _describe_ami_tag
        ledger = MagicMock()
        return CampaignManager(config, provider, ledger)

    def test_requires_nonempty_tailnet_ip(self, tmp_path, caplog):
        manager = self._manager_with_mocks(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        assert any("tailscale" in r.getMessage().lower() for r in caplog.records)

    def test_requires_redis_reachable_on_tailnet_ip(self, tmp_path, caplog):
        manager = self._manager_with_mocks(tmp_path)
        # tailscale ip -4 returns a value; redis.ping raises.
        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis") as mock_redis_ctor, \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            instance = MagicMock()
            instance.ping.side_effect = Exception("connection refused")
            mock_redis_ctor.return_value = instance
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        assert any("redis" in r.getMessage().lower() for r in caplog.records)

    def test_requires_aws_credentials(self, tmp_path, caplog):
        manager = self._manager_with_mocks(tmp_path)
        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis") as mock_redis_ctor, \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            mock_redis_ctor.return_value.ping.return_value = True
            mock_boto.return_value.get_caller_identity.side_effect = Exception("creds expired")
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        assert any("aws" in r.getMessage().lower() or "creden" in r.getMessage().lower()
                   for r in caplog.records)

    def test_requires_authkey_syntax(self, tmp_path, caplog):
        manager = self._manager_with_mocks(
            tmp_path, tailscale_authkey_secret="not-a-valid-key",
        )
        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis") as mock_redis_ctor, \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            mock_redis_ctor.return_value.ping.return_value = True
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        assert any("tskey" in r.getMessage().lower() or "authkey" in r.getMessage().lower()
                   for r in caplog.records)

    def test_preflight_stores_tailnet_ip(self, tmp_path):
        manager = self._manager_with_mocks(tmp_path)
        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis") as mock_redis_ctor, \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.7.7\n", stderr="")
            mock_redis_ctor.return_value.ping.return_value = True
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            manager._preflight()
        assert manager._tailnet_ip == "100.64.7.7"

    def test_redis_reachable_via_tailscale_serve_when_tailnet_ip_ping_fails(
        self, tmp_path,
    ):
        """Rootless/userspace-mode path: Redis binds to 127.0.0.1 only, and
        `tailscale serve` TCP-proxies it to the tailnet. The preflight must
        accept this configuration (self-loopback via tailnet IP FAILS but
        `tailscale serve status` shows the proxy mapping).
        """
        manager = self._manager_with_mocks(tmp_path)

        loopback_client = MagicMock()
        loopback_client.ping.return_value = True
        tailnet_client = MagicMock()
        tailnet_client.ping.side_effect = Exception(
            "userspace tailscale: tailnet IP not bound to a local interface"
        )

        def redis_ctor(*, host, port, socket_timeout, **kwargs):
            return loopback_client if host == "127.0.0.1" else tailnet_client

        def subprocess_stub(cmd, *args, **kwargs):
            # strip optional ["--socket", <path>] between "tailscale" and verb
            bare = [a for a in cmd if a != "tailscale" and not a.startswith("--socket")]
            # skip explicit socket-path positional (follows --socket)
            cleaned: list[str] = []
            skip_next = False
            for a in cmd[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if a == "--socket":
                    skip_next = True
                    continue
                cleaned.append(a)
            if cleaned[:2] == ["ip", "-4"]:
                return MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            if cleaned[:2] == ["serve", "status"]:
                return MagicMock(
                    returncode=0,
                    stdout=(
                        "TCP State:\n"
                        "  127.0.0.1:6379  tcp://127.0.0.1:6379  (bg)\n"
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=subprocess_stub), \
             patch("redis.Redis", side_effect=redis_ctor), \
             patch("boto3.client") as mock_boto:
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            manager._preflight()  # must not raise

        assert manager._tailnet_ip == "100.64.1.2"

    def test_redis_down_on_loopback_fails_fast(self, tmp_path, caplog):
        """If redis-server isn't running at all, fail at step 1 with a
        message that points at devenv-up.sh."""
        manager = self._manager_with_mocks(tmp_path)
        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis") as mock_redis_ctor, \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            instance = MagicMock()
            instance.ping.side_effect = Exception("connection refused")
            mock_redis_ctor.return_value = instance
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "127.0.0.1" in combined
        assert "devenv-up" in combined

    def test_redis_reachable_fails_when_no_tailnet_exposure(self, tmp_path, caplog):
        """Redis responds on loopback but nothing exposes it to the tailnet:
        preflight must refuse (workers would be unable to reach Redis)."""
        manager = self._manager_with_mocks(tmp_path)

        loopback_client = MagicMock()
        loopback_client.ping.return_value = True
        tailnet_client = MagicMock()
        tailnet_client.ping.side_effect = Exception("no route")

        def redis_ctor(*, host, port, socket_timeout, **kwargs):
            return loopback_client if host == "127.0.0.1" else tailnet_client

        def subprocess_stub(cmd, *args, **kwargs):
            if "ip" in cmd and "-4" in cmd:
                return MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            # serve status returns empty → no mapping
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=subprocess_stub), \
             patch("redis.Redis", side_effect=redis_ctor), \
             patch("boto3.client") as mock_boto:
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            with caplog.at_level(logging.ERROR):
                with pytest.raises(SystemExit):
                    manager._preflight()
        combined = " ".join(r.getMessage() for r in caplog.records)
        assert "tailnet" in combined.lower()
        assert "tailscale serve" in combined

    def test_tailscale_socket_env_var_passed_to_cli(self, tmp_path, monkeypatch):
        """When STARSECTOR_TAILSCALE_SOCKET is set, every `tailscale` CLI call
        must include `--socket <path>` so it targets the userspace daemon."""
        manager = self._manager_with_mocks(tmp_path)
        custom_sock = str(tmp_path / "custom-tailscaled.sock")
        monkeypatch.setenv("STARSECTOR_TAILSCALE_SOCKET", custom_sock)

        captured_cmds: list[list[str]] = []

        def subprocess_stub(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            if "ip" in cmd and "-4" in cmd:
                return MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            # serve status (exposes 6379)
            return MagicMock(
                returncode=0,
                stdout="TCP 127.0.0.1:6379 → tcp://127.0.0.1:6379\n",
                stderr="",
            )

        loopback_client = MagicMock()
        loopback_client.ping.return_value = True
        tailnet_client = MagicMock()
        tailnet_client.ping.side_effect = Exception("userspace mode")

        def redis_ctor(*, host, port, socket_timeout, **kwargs):
            return loopback_client if host == "127.0.0.1" else tailnet_client

        with patch("subprocess.run", side_effect=subprocess_stub), \
             patch("redis.Redis", side_effect=redis_ctor), \
             patch("boto3.client") as mock_boto:
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            manager._preflight()

        tailscale_cmds = [c for c in captured_cmds if c and c[0] == "tailscale"]
        assert tailscale_cmds, "expected at least one tailscale CLI call"
        for c in tailscale_cmds:
            assert "--socket" in c
            assert custom_sock in c

    def test_preflight_flushes_stale_redis_keys_for_project_tag(self, tmp_path):
        """Re-launched campaigns with the same name would otherwise inherit
        processing-list entries from the prior run; the janitor would then
        re-dispatch stale matchups."""
        manager = self._manager_with_mocks(tmp_path)

        loopback_client = MagicMock()
        loopback_client.ping.return_value = True
        # Seed the loopback client with stale keys matching both patterns
        # so scan_iter + delete can be verified.
        project_tag = manager._project_tag
        stale_keys = [
            f"queue:{project_tag}:hammerhead__early__seed0:processing",
            f"queue:{project_tag}:hammerhead__early__seed0:source",
            f"worker:{project_tag}:i-abc:heartbeat",
        ]

        def scan_iter_stub(match, count):
            return iter([k for k in stale_keys if k.startswith(match.rstrip("*"))])

        loopback_client.scan_iter = MagicMock(side_effect=scan_iter_stub)
        tailnet_client = MagicMock()
        tailnet_client.ping.return_value = True

        def redis_ctor(*, host, port, socket_timeout, **kwargs):
            return loopback_client if host == "127.0.0.1" else tailnet_client

        with patch("subprocess.run") as mock_run, \
             patch("redis.Redis", side_effect=redis_ctor), \
             patch("boto3.client") as mock_boto:
            mock_run.return_value = MagicMock(returncode=0, stdout="100.64.1.2\n", stderr="")
            mock_boto.return_value.get_caller_identity.return_value = {"UserId": "u"}
            manager._preflight()

        # Every stale key should have been deleted.
        assert loopback_client.delete.call_count == len(stale_keys)
        deleted_args = {c.args[0] for c in loopback_client.delete.call_args_list}
        assert deleted_args == set(stale_keys)


class TestLedgerTick:
    """Phase-7-prep: CampaignManager._tick_ledger SCANs worker heartbeats,
    attributes cost via spot-price cache, records one ledger row per tick
    per live worker. Budget hit → BudgetExceeded propagates."""

    def _manager(self, tmp_path, **overrides):
        from starsector_optimizer.campaign import CampaignManager, CostLedger
        from starsector_optimizer.game_manifest import GameManifest
        config_path = _minimal_campaign_yaml(tmp_path, **overrides)
        from starsector_optimizer.campaign import load_campaign_config
        config = load_campaign_config(config_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        _m = GameManifest.load()
        def _describe_ami_tag(*, ami_id, region, tag_key):
            if tag_key == "GameVersion":
                return _m.constants.game_version
            if tag_key == "ModCommitSha":
                return _m.constants.mod_commit_sha
            raise KeyError(tag_key)
        provider.describe_ami_tag.side_effect = _describe_ami_tag
        provider.get_spot_price.return_value = 0.30
        ledger = CostLedger(
            path=tmp_path / "ledger.jsonl",
            budget_usd=config.budget_usd,
        )
        mgr = CampaignManager(config, provider, ledger)
        return mgr, provider, ledger

    def _seed_heartbeat(self, redis_client, project_tag, worker_id,
                         region="us-east-1", instance_type="c7a.2xlarge",
                         ts_offset=0.0):
        redis_client.hset(
            f"worker:{project_tag}:{worker_id}:heartbeat",
            mapping={
                "timestamp": time.time() - ts_offset,
                "region": region,
                "instance_type": instance_type,
                "load_avg_1min": 4.0,
                "load_avg_5min": 3.8,
                "load_avg_15min": 3.5,
                "cpu_count": 8,
            },
        )

    def test_tick_ledger_writes_row_per_live_worker(self, tmp_path, fake_redis):
        mgr, provider, ledger = self._manager(tmp_path)
        mgr._redis = fake_redis
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-b")
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-c")
        mgr._tick_ledger()
        rows = [json.loads(l) for l in
                (tmp_path / "ledger.jsonl").read_text().splitlines()]
        assert len(rows) == 3
        assert all(r["delta_usd"] > 0 for r in rows)
        assert {r["worker_id"] for r in rows} == {"worker-a", "worker-b", "worker-c"}

    def test_tick_ledger_raises_budget_exceeded(self, tmp_path, fake_redis):
        mgr, provider, ledger = self._manager(tmp_path, budget_usd=0.001)
        mgr._redis = fake_redis
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        provider.get_spot_price.return_value = 5.0  # exceeds 0.001 instantly
        from starsector_optimizer.campaign import BudgetExceeded
        with pytest.raises(BudgetExceeded):
            mgr._tick_ledger()

    def test_tick_ledger_caches_spot_price_across_ticks(self, tmp_path, fake_redis):
        mgr, provider, ledger = self._manager(tmp_path)
        mgr._redis = fake_redis
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        for _ in range(4):
            mgr._tick_ledger()
            # re-seed timestamp so heartbeat stays live
            self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        # Cache: one call per (region, instance_type) across 4 ticks.
        assert provider.get_spot_price.call_count == 1

    def test_tick_ledger_skips_stale_heartbeat(self, tmp_path, fake_redis):
        """Heartbeat older than heartbeat_stale_multiplier × interval →
        worker treated as dead, no ledger row written."""
        mgr, provider, ledger = self._manager(tmp_path)
        mgr._redis = fake_redis
        stale_offset = (
            mgr._config.ledger_heartbeat_interval_seconds
            * mgr._config.heartbeat_stale_multiplier + 10
        )
        self._seed_heartbeat(
            fake_redis, "starsector-test-campaign", "worker-a",
            ts_offset=stale_offset,
        )
        mgr._tick_ledger()
        assert not (tmp_path / "ledger.jsonl").exists() or (
            (tmp_path / "ledger.jsonl").read_text() == ""
        )

    def test_tick_ledger_hours_elapsed_capped_at_interval(self, tmp_path, fake_redis):
        """Consecutive ticks at interval_seconds apart → each tick charges
        at most `interval_seconds/3600` hours per worker."""
        mgr, provider, ledger = self._manager(tmp_path)
        mgr._redis = fake_redis
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        mgr._tick_ledger()
        # Re-seed so live, then tick again.
        self._seed_heartbeat(fake_redis, "starsector-test-campaign", "worker-a")
        mgr._tick_ledger()
        rows = [json.loads(l) for l in
                (tmp_path / "ledger.jsonl").read_text().splitlines()]
        interval_hours = mgr._config.ledger_heartbeat_interval_seconds / 3600.0
        for r in rows:
            # Allow tiny float slack.
            assert r["hours_elapsed"] <= interval_hours + 0.001


class TestRunJanitorPass:
    """Phase-7-prep: janitor resets enqueued_at on re-queue, tracks
    requeue_count per item, drops + ERROR on max_requeues exceeded."""

    def _seed_stale_item(self, fake_redis, source, processing,
                         matchup_id, age_seconds=300.0,
                         requeue_count=0):
        payload = {
            "matchup_id": matchup_id,
            "enqueued_at": time.time() - age_seconds,
            "requeue_count": requeue_count,
        }
        fake_redis.lpush(processing, json.dumps(payload))

    def test_janitor_resets_enqueued_at_on_requeue(self, fake_redis):
        from starsector_optimizer.campaign import run_janitor_pass
        source = "q:test:src"
        processing = "q:test:proc"
        self._seed_stale_item(fake_redis, source, processing, "m1", age_seconds=120.0)
        requeued = run_janitor_pass(
            fake_redis, source, processing,
            visibility_timeout_seconds=60.0, max_requeues=5,
        )
        assert requeued == 1
        raw = fake_redis.lindex(source, 0)
        item = json.loads(raw)
        # Fresh enqueued_at is within the last few seconds of now, much newer
        # than the 120s-stale original.
        assert time.time() - item["enqueued_at"] < 10.0

    def test_janitor_increments_requeue_count(self, fake_redis):
        from starsector_optimizer.campaign import run_janitor_pass
        source = "q:test:src"
        processing = "q:test:proc"
        self._seed_stale_item(
            fake_redis, source, processing, "m1", age_seconds=120.0,
            requeue_count=2,
        )
        run_janitor_pass(
            fake_redis, source, processing,
            visibility_timeout_seconds=60.0, max_requeues=5,
        )
        item = json.loads(fake_redis.lindex(source, 0))
        assert item["requeue_count"] == 3

    def test_janitor_drops_on_max_requeues_exceeded(self, fake_redis, caplog):
        from starsector_optimizer.campaign import run_janitor_pass
        source = "q:test:src"
        processing = "q:test:proc"
        self._seed_stale_item(
            fake_redis, source, processing, "m1", age_seconds=120.0,
            requeue_count=5,  # equal to max — next bump hits 6 which exceeds
        )
        with caplog.at_level(logging.ERROR, logger="starsector_optimizer.campaign"):
            requeued = run_janitor_pass(
                fake_redis, source, processing,
                visibility_timeout_seconds=60.0, max_requeues=5,
            )
        assert requeued == 0
        # Item dropped from processing, NOT pushed back to source.
        assert fake_redis.llen(processing) == 0
        assert fake_redis.llen(source) == 0
        assert any("max_requeues" in r.getMessage() for r in caplog.records)


class TestCampaignNameValidation:
    def test_name_regex_accepts_common_names(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        for i, name in enumerate(
            ("smoke", "phase7-prep-2026-04", "test.campaign.1", "a_b_c")
        ):
            subdir = tmp_path / f"case_{i}"
            subdir.mkdir()
            path = _minimal_campaign_yaml(subdir, name=name)
            load_campaign_config(path)  # must not raise

    def test_name_regex_rejects_whitespace(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, name="bad name")
        with pytest.raises(ValueError, match=r"name"):
            load_campaign_config(path)

    def test_name_regex_rejects_shell_metachars(self, tmp_path):
        from starsector_optimizer.campaign import load_campaign_config
        path = _minimal_campaign_yaml(tmp_path, name="bad/name")
        with pytest.raises(ValueError, match=r"name"):
            load_campaign_config(path)


class TestYamlEnvSubstitution:
    """load_campaign_config expands ${VAR} in tailscale_authkey_secret ONLY."""

    def test_expands_env_var_in_authkey_field(self, tmp_path, monkeypatch):
        from starsector_optimizer.campaign import load_campaign_config
        monkeypatch.setenv("SMOKE_TEST_TAILSCALE", "tskey-auth-REAL-VALUE-z9z9")
        path = _minimal_campaign_yaml(
            tmp_path, tailscale_authkey_secret="${SMOKE_TEST_TAILSCALE}",
        )
        config = load_campaign_config(path)
        assert config.tailscale_authkey_secret == "tskey-auth-REAL-VALUE-z9z9"

    def test_missing_env_var_raises_clear_error(self, tmp_path, monkeypatch):
        from starsector_optimizer.campaign import load_campaign_config
        monkeypatch.delenv("SMOKE_TEST_TAILSCALE_MISSING", raising=False)
        path = _minimal_campaign_yaml(
            tmp_path, tailscale_authkey_secret="${SMOKE_TEST_TAILSCALE_MISSING}",
        )
        with pytest.raises(ValueError, match="SMOKE_TEST_TAILSCALE_MISSING"):
            load_campaign_config(path)

    def test_other_fields_are_not_expanded(self, tmp_path, monkeypatch):
        """Only tailscale_authkey_secret gets substitution; other string
        fields pass through untouched (no global os.path.expandvars)."""
        from starsector_optimizer.campaign import load_campaign_config
        monkeypatch.setenv("UNRELATED_VAR", "SHOULD_NOT_APPEAR")
        path = _minimal_campaign_yaml(tmp_path, ssh_key_name="key_${UNRELATED_VAR}")
        config = load_campaign_config(path)
        # The literal must survive unchanged — no global expansion.
        assert config.ssh_key_name == "key_${UNRELATED_VAR}"
        assert "SHOULD_NOT_APPEAR" not in config.ssh_key_name


class TestSmokeYamlLoadsAndValidates:
    """The shipped examples/smoke-campaign.yaml must load without error and
    reflect the spec-pinned smoke parameters."""

    def test_loads_from_repo(self, monkeypatch):
        from starsector_optimizer.campaign import load_campaign_config
        monkeypatch.setenv("TAILSCALE_AUTHKEY", "tskey-auth-SMOKE-44e7f9b3")
        repo_root = Path(__file__).parent.parent
        smoke_path = repo_root / "examples" / "smoke-campaign.yaml"
        if not smoke_path.exists():
            pytest.skip(f"{smoke_path} not yet present")
        config = load_campaign_config(smoke_path)
        assert config.name == "smoke"
        assert len(config.studies) == 1
        assert config.studies[0].hull == "hammerhead"
        assert config.studies[0].regime == "early"
        assert config.studies[0].seeds == (0,)
        assert config.studies[0].budget_per_study == 2
        assert config.studies[0].workers_per_study == 1
        assert config.tailscale_authkey_secret == "tskey-auth-SMOKE-44e7f9b3"
        assert config.capacity_rebalancing is True  # spec-required


class TestEnvDictNotLogged:
    """Secrets in subprocess env must never hit logs."""

    def test_spawn_studies_does_not_log_env_dict(self, tmp_path, caplog):
        from starsector_optimizer.campaign import CampaignManager
        import starsector_optimizer.campaign as mod
        config = load_campaign_config = __import__(
            "starsector_optimizer.campaign", fromlist=["load_campaign_config"],
        ).load_campaign_config(_minimal_campaign_yaml(tmp_path))
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager._tailnet_ip = "100.64.1.2"
        with caplog.at_level(logging.DEBUG):
            with patch.object(subprocess, "Popen") as mock_popen:
                mock_popen.return_value.poll.return_value = 0
                manager.spawn_studies()
        for record in caplog.records:
            assert TAILSCALE_SECRET_SENTINEL not in record.getMessage()
            # Bearer tokens are generated on spawn; we can't name them, but
            # they should not appear in any log record.
            env = mock_popen.call_args_list[0].kwargs.get("env", {})
            bearer = env.get("STARSECTOR_BEARER_TOKEN", "")
            if bearer:
                assert bearer not in record.getMessage()
