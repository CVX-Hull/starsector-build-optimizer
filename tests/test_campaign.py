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
            campaign_id="c", worker_id="w", study_id="s",
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
            campaign_id="c", worker_id="w", study_id="s",
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
    """CampaignManager: subprocess per study, signals, teardown, partial fleet."""

    def _config(self, tmp_path, **overrides):
        from starsector_optimizer.campaign import load_campaign_config
        return load_campaign_config(_minimal_campaign_yaml(tmp_path, **overrides))

    def test_spawns_one_subprocess_per_study_seed(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        studies = [
            {"hull": "wolf", "regime": "early", "seeds": [0, 1, 2],
             "budget_per_study": 200, "workers_per_study": 12, "sampler": "tpe"},
        ]
        config = self._config(tmp_path, studies=studies)
        provider = MagicMock()
        provider.create_fleet.return_value = [f"i-{i:04d}" for i in range(48)]
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            procs = manager.spawn_studies([])
        assert mock_popen.call_count == 3

    def test_subprocess_gets_yaml_path_not_pickle(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)

        with patch.object(subprocess, "Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = 0
            manager.spawn_studies([])
        args_list = mock_popen.call_args_list[0]
        cmd = args_list[0][0]
        assert "--campaign-config" in cmd
        assert not any(b"pickle" in str(arg).encode() for arg in cmd)

    def test_partial_fleet_proceed_at_floor(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        decision = manager.partial_fleet_decide(launched=48)
        assert decision == "proceed"

    def test_partial_fleet_abort_below_floor(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        decision = manager.partial_fleet_decide(launched=30)
        assert decision == "abort"

    def test_teardown_calls_provider_terminate(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        manager.teardown()
        provider.terminate_all_tagged.assert_called_with("test-campaign")

    def test_teardown_asserts_list_active_empty(self, tmp_path):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        # first call: one left, retry call: empty
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

    def test_budget_exceeded_triggers_teardown(self, tmp_path):
        from starsector_optimizer.campaign import (
            CampaignManager, BudgetExceeded,
        )
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        ledger.cumulative_usd.return_value = 0.0
        manager = CampaignManager(config, provider, ledger)
        with patch.object(manager, "teardown") as mock_td:
            with patch.object(manager, "monitor_loop", side_effect=BudgetExceeded("cap")):
                with patch.object(manager, "provision_fleet", return_value=["i-1"]):
                    with patch.object(manager, "spawn_studies", return_value=[]):
                        exit_code = manager.run()
        assert mock_td.called
        assert exit_code != 0

    def test_structured_error_log_on_abort(self, tmp_path, caplog):
        from starsector_optimizer.campaign import CampaignManager
        config = self._config(tmp_path)
        provider = MagicMock()
        provider.list_active.return_value = []
        ledger = MagicMock()
        manager = CampaignManager(config, provider, ledger)
        with caplog.at_level(logging.ERROR):
            manager.log_partial_fleet_abort(launched=30, elapsed_seconds=600.0)
        messages = [r.getMessage() for r in caplog.records]
        assert any("launched" in m.lower() for m in messages)
        assert any("30" in m for m in messages)
