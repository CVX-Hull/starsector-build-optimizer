"""Tests for cloud_userdata.py — cloud-init UserData renderer.

Pure-function module — no boto3, no filesystem, no network. Verifies the
produced shell script:
  - contains every WorkerConfig field as an env var
  - runs `tailscale up` with the provided authkey before starting the worker
  - starts the systemd unit baked into the AMI
  - never leaks the bearer token or tailscale authkey into logs / stdout
"""

import base64

import pytest


BEARER_TOKEN_SENTINEL = "SENTINEL_BEARER_44e7f9b3"
TAILSCALE_SECRET_SENTINEL = "SENTINEL_TAILSCALE_e1a2f800"


def _make_worker_config(**overrides):
    from starsector_optimizer.models import WorkerConfig
    defaults = dict(
        campaign_id="unit-test-campaign",
        worker_id="i-0deadbeef",
        study_id="hammerhead__early__seed0",
        redis_host="100.64.0.1",
        redis_port=6379,
        http_endpoint="http://100.64.0.1:9000/result",
        bearer_token=BEARER_TOKEN_SENTINEL,
        max_lifetime_hours=6.0,
    )
    defaults.update(overrides)
    return WorkerConfig(**defaults)


class TestRenderUserData:
    def test_returns_str(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert isinstance(out, str)
        assert len(out) > 0

    def test_starts_with_shebang(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert out.startswith("#!/"), "cloud-init expects a shebang-prefixed bash script"

    def test_runs_tailscale_up_with_authkey(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert "tailscale up" in out
        assert TAILSCALE_SECRET_SENTINEL in out
        # Must feed authkey via stdin (--authkey-stdin), NOT --authkey <arg>.
        # /proc/<pid>/cmdline is world-readable; argv leaks the secret.
        assert "--authkey-stdin" in out
        # Sanity: the literal `--authkey=<value>` or `--authkey <value>` arg
        # form must NOT appear (would leak through /proc).
        assert f"--authkey {TAILSCALE_SECRET_SENTINEL}" not in out
        assert f"--authkey={TAILSCALE_SECRET_SENTINEL}" not in out

    def test_writes_env_file(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert "/etc/starsector-worker.env" in out

    def test_env_file_contains_every_worker_config_field(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # Every STARSECTOR_WORKER_* env var the worker_agent reads must be set.
        for required in (
            "STARSECTOR_WORKER_CAMPAIGN_ID=",
            "STARSECTOR_WORKER_WORKER_ID=",
            "STARSECTOR_WORKER_STUDY_ID=",
            "STARSECTOR_WORKER_REDIS_HOST=",
            "STARSECTOR_WORKER_REDIS_PORT=",
            "STARSECTOR_WORKER_HTTP_ENDPOINT=",
            "STARSECTOR_WORKER_BEARER_TOKEN=",
            "STARSECTOR_WORKER_MAX_LIFETIME_HOURS=",
        ):
            assert required in out, f"missing env var: {required}"

    def test_env_file_has_restrictive_mode(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # Bearer token in plaintext → owner-read-only. Must be set at creation
        # (via `umask 077`) OR via explicit chmod. Either closes the 0644
        # window a bare `cat >file` followed by `chmod 0600` would open.
        assert "umask 077" in out or "chmod 0600" in out or "chmod 600" in out
        assert "/etc/starsector-worker.env" in out

    def test_starts_systemd_service(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert "systemctl" in out
        assert "starsector-worker" in out

    def test_tailscale_up_runs_before_systemctl_start(self):
        """Worker agent Redis reachability requires the tailnet up first."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        ts_pos = out.find("tailscale up")
        svc_pos = out.find("systemctl start")
        assert ts_pos != -1 and svc_pos != -1
        assert ts_pos < svc_pos, "tailscale up must precede systemctl start"

    def test_bearer_token_plaintext_in_env_file_only(self):
        """Bearer token must appear only in the env-file context, never echoed."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # At least one occurrence for the env file line.
        assert BEARER_TOKEN_SENTINEL in out
        # There must be no `echo ... BEARER_TOKEN ...` style leak to stdout.
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("echo ") and BEARER_TOKEN_SENTINEL in stripped:
                pytest.fail(f"bearer token leaked via echo: {line!r}")
            if stripped.startswith("logger ") and BEARER_TOKEN_SENTINEL in stripped:
                pytest.fail(f"bearer token leaked via logger: {line!r}")

    def test_script_has_set_errexit(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # A failure anywhere in provisioning must not silently continue to
        # systemctl start (which would spin a broken worker).
        assert "set -e" in out or "set -euo" in out or "set -euxo" in out


class TestRenderProbeUserData:
    def test_returns_str(self):
        from starsector_optimizer.cloud_userdata import render_probe_user_data
        out = render_probe_user_data(campaign_id="probe-20260418")
        assert isinstance(out, str)
        assert len(out) > 0

    def test_starts_with_shebang(self):
        from starsector_optimizer.cloud_userdata import render_probe_user_data
        out = render_probe_user_data(campaign_id="probe-20260418")
        assert out.startswith("#!/")

    def test_writes_probe_log_marker(self):
        """Probe userdata is cheap: just drop a breadcrumb + exit clean."""
        from starsector_optimizer.cloud_userdata import render_probe_user_data
        out = render_probe_user_data(campaign_id="probe-20260418")
        assert "probe-boot-ok" in out or "probe_boot_ok" in out
        assert "/var/log/starsector-probe" in out or "/tmp/starsector-probe" in out

    def test_does_not_start_worker_service(self):
        """Probe must NOT attempt to start starsector-worker.service — it
        lacks a real WorkerConfig and would crashloop if launched.
        """
        from starsector_optimizer.cloud_userdata import render_probe_user_data
        out = render_probe_user_data(campaign_id="probe-20260418")
        assert "systemctl start starsector-worker" not in out

    def test_does_not_require_tailscale(self):
        """Tier-1 probe doesn't depend on Tailscale — no authkey to leak."""
        from starsector_optimizer.cloud_userdata import render_probe_user_data
        out = render_probe_user_data(campaign_id="probe-20260418")
        # No authkey flag = no secret exposure surface.
        assert "--authkey" not in out
