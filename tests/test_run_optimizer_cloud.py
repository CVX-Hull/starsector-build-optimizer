"""Tests for the cloud-path entry point in `starsector_optimizer.cloud_runner`.

The cloud branch of `scripts/run_optimizer.py` delegates to
`cloud_runner.run_cloud_study(...)`, which:

  1. Reads required env vars via `_require_env` (ValueError on missing).
  2. Constructs a `WorkerConfig` for the worker side.
  3. Renders UserData via `render_user_data`.
  4. Provisions an `AWSProvider` fleet.
  5. Runs the Optuna study inside `with CloudWorkerPool(...)`.
  6. `finally`: calls `provider.terminate_fleet(fleet_name, project_tag)`.

Tests mock AWSProvider, CloudWorkerPool, and optimize_hull — no network I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"


def _make_smoke_config(tmp_path, **overrides):
    """Emit a minimal smoke YAML and return the loaded CampaignConfig."""
    import yaml
    from starsector_optimizer.campaign import load_campaign_config

    defaults = {
        "name": "ut-campaign",
        "budget_usd": 2.0,
        "provider": "aws",
        "regions": ["us-east-1"],
        "instance_types": ["c7a.2xlarge"],
        "spot_allocation_strategy": "price-capacity-optimized",
        "capacity_rebalancing": True,
        "max_concurrent_workers": 1,
        "min_workers_to_start": 1,
        "partial_fleet_policy": "abort",
        "ami_ids_by_region": {"us-east-1": "ami-abc"},
        "ssh_key_name": "starsector-probe",
        "tailscale_authkey_secret": "tskey-auth-SMOKE-TEST-44e7f9b3",
        "studies": [{
            "hull": "hammerhead", "regime": "early",
            "seeds": [0], "budget_per_study": 2,
            "workers_per_study": 1, "sampler": "tpe",
        }],
        "max_lifetime_hours": 0.5,
    }
    defaults.update(overrides)
    path = tmp_path / "campaign.yaml"
    path.write_text(yaml.safe_dump(defaults))
    return load_campaign_config(path), path


class TestRequireEnv:
    def test_raises_valueerror_on_missing(self, monkeypatch):
        from starsector_optimizer.cloud_runner import _require_env
        monkeypatch.delenv("UT_MISSING_VAR_xyz", raising=False)
        with pytest.raises(ValueError, match="UT_MISSING_VAR_xyz"):
            _require_env("UT_MISSING_VAR_xyz")

    def test_not_keyerror(self, monkeypatch):
        """Specifically ValueError, not KeyError — so error handling in the
        cloud path is explicit and remediation message reaches the user."""
        from starsector_optimizer.cloud_runner import _require_env
        monkeypatch.delenv("UT_MISSING_xyz", raising=False)
        try:
            _require_env("UT_MISSING_xyz")
        except KeyError:
            pytest.fail("_require_env raised KeyError; must raise ValueError")
        except ValueError:
            pass

    def test_returns_value_when_present(self, monkeypatch):
        from starsector_optimizer.cloud_runner import _require_env
        monkeypatch.setenv("UT_PRESENT_VAR", "hello")
        assert _require_env("UT_PRESENT_VAR") == "hello"


class TestRunCloudStudyOrdering:
    """Provisioning and teardown follow the contract:

        provision_fleet  →  pool.setup  →  (optimize_hull)  →  pool.teardown  →  terminate_fleet
    """

    def _prep_mocks(self, monkeypatch, tmp_path, smoke_env):
        """Patch AWSProvider, CloudWorkerPool, optimize_hull, game_data — record order."""
        import starsector_optimizer.cloud_runner as cloud_runner

        config, path = _make_smoke_config(tmp_path)

        call_log: list[str] = []

        class FakeProvider:
            def __init__(self, *, regions):
                self.regions = regions

            def provision_fleet(self, **kwargs):
                call_log.append("provision_fleet")
                return ["i-00000000abcd"]

            def terminate_fleet(self, *, fleet_name, project_tag):
                call_log.append("terminate_fleet")
                return 1

        class FakePool:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                call_log.append("pool.__enter__")
                return self

            def __exit__(self, *a):
                call_log.append("pool.__exit__")

        def fake_optimize_hull(*args, **kwargs):
            call_log.append("optimize_hull")
            return MagicMock()

        monkeypatch.setattr(cloud_runner, "AWSProvider", FakeProvider)
        monkeypatch.setattr(cloud_runner, "CloudWorkerPool", FakePool)
        monkeypatch.setattr(cloud_runner, "optimize_hull", fake_optimize_hull)
        # redis.Redis is used inside cloud_runner; mock it.
        import redis as redis_mod
        monkeypatch.setattr(redis_mod, "Redis", MagicMock())
        return config, path, call_log, cloud_runner

    def test_provision_before_pool_enter(self, monkeypatch, tmp_path, smoke_env):
        config, path, call_log, cloud_runner = self._prep_mocks(
            monkeypatch, tmp_path, smoke_env,
        )
        game_data = MagicMock()
        hull = MagicMock()
        hull.hull_size = MagicMock()
        opponent_pool = MagicMock()
        optimizer_config = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=game_data, manifest=MagicMock(),
            opponent_pool=opponent_pool, optimizer_config=optimizer_config,
        )
        assert call_log.index("provision_fleet") < call_log.index("pool.__enter__")

    def test_teardown_fleet_runs_in_finally_even_after_exception(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        config, path, call_log, cloud_runner = self._prep_mocks(
            monkeypatch, tmp_path, smoke_env,
        )
        monkeypatch.setattr(cloud_runner, "optimize_hull",
                            MagicMock(side_effect=RuntimeError("boom")))
        hull = MagicMock()
        hull.hull_size = MagicMock()
        with pytest.raises(RuntimeError, match="boom"):
            cloud_runner.run_cloud_study(
                campaign_yaml_path=path, study_idx=0, seed_idx=0,
                hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
                opponent_pool=MagicMock(), optimizer_config=MagicMock(),
            )
        assert "terminate_fleet" in call_log

    def test_teardown_order_pool_exit_before_terminate_fleet(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        """Pool __exit__ (shuts Flask + janitor) must run BEFORE terminate_fleet —
        otherwise a live worker could POST to a torn-down listener."""
        config, path, call_log, cloud_runner = self._prep_mocks(
            monkeypatch, tmp_path, smoke_env,
        )
        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        assert call_log.index("pool.__exit__") < call_log.index("terminate_fleet")

    def test_fleet_name_equals_study_id(self, monkeypatch, tmp_path, smoke_env):
        config, path, call_log, cloud_runner = self._prep_mocks(
            monkeypatch, tmp_path, smoke_env,
        )

        captured = {}

        class Recorder:
            def __init__(self, *, regions):
                pass

            def provision_fleet(self, **kwargs):
                captured["provision"] = kwargs
                return ["i-0"]

            def terminate_fleet(self, *, fleet_name, project_tag):
                captured["terminate"] = (fleet_name, project_tag)
                return 1

        monkeypatch.setattr(cloud_runner, "AWSProvider", Recorder)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        expected_study_id = "hammerhead__early__tpe__seed0"
        assert captured["provision"]["fleet_name"] == expected_study_id
        # project_tag comes from STARSECTOR_PROJECT_TAG env (smoke_env fixture
        # sets it to "starsector-smoke"), not from the YAML — that's the contract:
        # CampaignManager computes the tag once, passes it via env, and the
        # subprocess trusts it.
        assert captured["terminate"] == (expected_study_id, smoke_env["STARSECTOR_PROJECT_TAG"])

    def test_user_data_rendered_with_authkey_from_env(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        config, path, call_log, cloud_runner = self._prep_mocks(
            monkeypatch, tmp_path, smoke_env,
        )

        rendered = {}

        def fake_render(worker_cfg, *, tailscale_authkey):
            rendered["worker_cfg"] = worker_cfg
            rendered["authkey"] = tailscale_authkey
            return "#!/bin/bash\necho stub\n"

        monkeypatch.setattr(cloud_runner, "render_user_data", fake_render)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        # authkey comes from env (set by smoke_env fixture), not YAML
        assert rendered["authkey"] == smoke_env["STARSECTOR_TAILSCALE_AUTHKEY"]
        # WorkerConfig carries the bearer token from env, not the YAML secret
        assert rendered["worker_cfg"].bearer_token == smoke_env["STARSECTOR_BEARER_TOKEN"]
        # redis_host is the tailnet IP from env
        assert rendered["worker_cfg"].redis_host == smoke_env["STARSECTOR_WORKSTATION_TAILNET_IP"]


class TestRunCloudStudyEnvPreflight:
    """Missing required env vars raise ValueError (not KeyError)."""

    def _load_smoke(self, tmp_path):
        return _make_smoke_config(tmp_path)

    def test_missing_tailnet_ip_raises_valueerror(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        from starsector_optimizer.cloud_runner import run_cloud_study
        monkeypatch.delenv("STARSECTOR_WORKSTATION_TAILNET_IP", raising=False)
        _, path = self._load_smoke(tmp_path)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        with pytest.raises(ValueError, match="STARSECTOR_WORKSTATION_TAILNET_IP"):
            run_cloud_study(
                campaign_yaml_path=path, study_idx=0, seed_idx=0,
                hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
                opponent_pool=MagicMock(), optimizer_config=MagicMock(),
            )

    def test_missing_bearer_token_raises_valueerror(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        from starsector_optimizer.cloud_runner import run_cloud_study
        monkeypatch.delenv("STARSECTOR_BEARER_TOKEN", raising=False)
        _, path = self._load_smoke(tmp_path)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        with pytest.raises(ValueError, match="STARSECTOR_BEARER_TOKEN"):
            run_cloud_study(
                campaign_yaml_path=path, study_idx=0, seed_idx=0,
                hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
                opponent_pool=MagicMock(), optimizer_config=MagicMock(),
            )

    def test_missing_tailscale_authkey_raises_valueerror(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        from starsector_optimizer.cloud_runner import run_cloud_study
        monkeypatch.delenv("STARSECTOR_TAILSCALE_AUTHKEY", raising=False)
        _, path = self._load_smoke(tmp_path)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        with pytest.raises(ValueError, match="STARSECTOR_TAILSCALE_AUTHKEY"):
            run_cloud_study(
                campaign_yaml_path=path, study_idx=0, seed_idx=0,
                hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
                opponent_pool=MagicMock(), optimizer_config=MagicMock(),
            )

    def test_missing_project_tag_raises_valueerror(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        from starsector_optimizer.cloud_runner import run_cloud_study
        monkeypatch.delenv("STARSECTOR_PROJECT_TAG", raising=False)
        _, path = self._load_smoke(tmp_path)
        hull = MagicMock()
        hull.hull_size = MagicMock()
        with pytest.raises(ValueError, match="STARSECTOR_PROJECT_TAG"):
            run_cloud_study(
                campaign_yaml_path=path, study_idx=0, seed_idx=0,
                hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
                opponent_pool=MagicMock(), optimizer_config=MagicMock(),
            )


class TestPoolTotalMatchupSlots:
    """Pool concurrency cap == workers_per_study × matchup_slots_per_worker.
    Anything less under-utilizes the fleet (half the JVMs idle);
    anything more over-dispatches past Redis consumer capacity."""

    def test_pool_receives_total_matchup_slots_equal_to_product(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        import starsector_optimizer.cloud_runner as cloud_runner
        config, path = _make_smoke_config(
            tmp_path,
            matchup_slots_per_worker=2,
            studies=[{
                "hull": "hammerhead", "regime": "early",
                "seeds": [0], "budget_per_study": 20,
                "workers_per_study": 3, "sampler": "tpe",
            }],
        )

        pool_kwargs = {}

        class Recorder:
            def __init__(self, *, regions): pass
            def provision_fleet(self, **kwargs): return ["i-0", "i-1", "i-2"]
            def terminate_fleet(self, **kwargs): return 3

        class RecordingPool:
            def __init__(self, **kwargs):
                pool_kwargs.update(kwargs)
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(cloud_runner, "AWSProvider", Recorder)
        monkeypatch.setattr(cloud_runner, "CloudWorkerPool", RecordingPool)
        monkeypatch.setattr(cloud_runner, "optimize_hull", MagicMock())
        monkeypatch.setattr(cloud_runner, "render_user_data",
                            lambda *a, **kw: "#!/bin/bash\n")
        import redis as redis_mod
        monkeypatch.setattr(redis_mod, "Redis", MagicMock())

        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        assert pool_kwargs["total_matchup_slots"] == 3 * 2
        assert pool_kwargs["project_tag"] == smoke_env["STARSECTOR_PROJECT_TAG"]


class TestManifestIsThreadedIntoOptimizeHull:
    """Regression: a 2026-04-19 Tier-2 smoke crashed with `optimize_hull()
    missing 1 required positional argument: 'manifest'` because
    run_cloud_study did not forward the manifest. schema-v2 optimize_hull
    (Commit G) requires a GameManifest. This test fails if cloud_runner
    ever drops the plumbing again."""

    def test_manifest_forwarded_to_optimize_hull(
        self, monkeypatch, tmp_path, smoke_env,
    ):
        import starsector_optimizer.cloud_runner as cloud_runner
        config, path = _make_smoke_config(tmp_path)

        class Recorder:
            def __init__(self, *, regions): pass
            def provision_fleet(self, **kwargs): return ["i-0"]
            def terminate_fleet(self, **kwargs): return 1

        class FakePool:
            def __init__(self, *a, **kw): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

        optimize_hull_mock = MagicMock()
        monkeypatch.setattr(cloud_runner, "AWSProvider", Recorder)
        monkeypatch.setattr(cloud_runner, "CloudWorkerPool", FakePool)
        monkeypatch.setattr(cloud_runner, "optimize_hull", optimize_hull_mock)
        monkeypatch.setattr(cloud_runner, "render_user_data",
                            lambda *a, **kw: "#!/bin/bash\n")
        import redis as redis_mod
        monkeypatch.setattr(redis_mod, "Redis", MagicMock())

        sentinel_manifest = MagicMock(name="sentinel_manifest")
        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=0,
            hull_id="hammerhead", hull=hull, game_data=MagicMock(),
            manifest=sentinel_manifest,
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        optimize_hull_mock.assert_called_once()
        # Signature is positional: (hull_id, game_data, pool, opponent_pool,
        # config, manifest). Assert manifest is the 6th positional arg.
        call_args = optimize_hull_mock.call_args
        assert call_args.args[5] is sentinel_manifest, (
            f"manifest not forwarded as positional arg[5]; got {call_args.args}"
        )


class TestSeedIndexResolvesCorrectSeed:
    """Flat-idx bug fix: run_cloud_study picks `study_cfg.seeds[seed_idx]`,
    not the first seed unconditionally."""

    def test_seed_idx_picks_the_named_seed(self, monkeypatch, tmp_path, smoke_env):
        import starsector_optimizer.cloud_runner as cloud_runner
        # Smoke config with a multi-seed study.
        config, path = _make_smoke_config(
            tmp_path,
            studies=[{
                "hull": "hammerhead", "regime": "early",
                "seeds": [0, 1, 7],
                "budget_per_study": 2, "workers_per_study": 1, "sampler": "tpe",
            }],
        )

        captured = {}

        class Recorder:
            def __init__(self, *, regions):
                pass

            def provision_fleet(self, **kwargs):
                captured["fleet_name"] = kwargs["fleet_name"]
                return ["i-0"]

            def terminate_fleet(self, **kwargs):
                return 1

        class FakePool:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr(cloud_runner, "AWSProvider", Recorder)
        monkeypatch.setattr(cloud_runner, "CloudWorkerPool", FakePool)
        monkeypatch.setattr(cloud_runner, "optimize_hull", MagicMock())
        monkeypatch.setattr(cloud_runner, "render_user_data",
                            lambda *a, **kw: "#!/bin/bash\n")
        import redis as redis_mod
        monkeypatch.setattr(redis_mod, "Redis", MagicMock())

        hull = MagicMock()
        hull.hull_size = MagicMock()
        cloud_runner.run_cloud_study(
            campaign_yaml_path=path, study_idx=0, seed_idx=2,   # seeds[2] = 7
            hull_id="hammerhead", hull=hull, game_data=MagicMock(), manifest=MagicMock(),
            opponent_pool=MagicMock(), optimizer_config=MagicMock(),
        )
        assert captured["fleet_name"] == "hammerhead__early__tpe__seed7"


