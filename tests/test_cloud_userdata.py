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
        study_id="hammerhead__early__seed0",
        redis_host="100.64.0.1",
        redis_port=6379,
        http_endpoint="http://100.64.0.1:9000/result",
        bearer_token=BEARER_TOKEN_SENTINEL,
        max_lifetime_hours=6.0,
        # worker_id left to default (""); cloud-init IMDSv2 overrides at boot.
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
        # Modern Tailscale: authkey passes via `--auth-key=file:<path>`; the
        # raw key is written to a tmpfile (0600 via umask) and shredded
        # after `tailscale up`. This keeps the key off /proc/<pid>/cmdline
        # without relying on the deprecated --authkey-stdin flag.
        assert "--auth-key=file:" in out
        assert "shred -u" in out
        # Sanity: the raw key must NEVER appear inline on a `tailscale up`
        # invocation (would leak through /proc). Accept it only inside a
        # heredoc body that targets the tmpfile.
        assert f"--auth-key={TAILSCALE_SECRET_SENTINEL}" not in out
        assert f"--auth-key {TAILSCALE_SECRET_SENTINEL}" not in out
        assert f"--authkey={TAILSCALE_SECRET_SENTINEL}" not in out
        assert f"--authkey {TAILSCALE_SECRET_SENTINEL}" not in out
        # And the deprecated flag must not regress in either form.
        assert "--authkey-stdin" not in out

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


class TestRenderUserDataImdsV2WorkerIdOverride:
    """The render-time heredoc emits worker_id as a placeholder; IMDSv2 must
    override it at VM boot BEFORE systemctl start. The override uses IMDSv2
    (PUT /api/token then GET /meta-data/instance-id with the token header)
    because IMDSv1 is SSRF-exploitable."""

    def test_appends_worker_id_via_imdsv2(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # IMDSv2 token fetch (PUT with TTL header).
        assert "X-aws-ec2-metadata-token-ttl-seconds" in out
        assert "http://169.254.169.254/latest/api/token" in out
        # IMDSv2 GET with token header.
        assert "X-aws-ec2-metadata-token:" in out
        assert "/latest/meta-data/instance-id" in out
        # Override writes WORKER_ID into the env file.
        assert "STARSECTOR_WORKER_WORKER_ID=" in out
        assert "/etc/starsector-worker.env" in out

    def test_imdsv2_override_runs_before_systemctl_start(self):
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        imds_pos = out.find("/latest/meta-data/instance-id")
        svc_pos = out.find("systemctl start")
        assert imds_pos != -1 and svc_pos != -1
        assert imds_pos < svc_pos, (
            "IMDSv2 WORKER_ID override must precede systemctl start — otherwise "
            "the worker can boot with worker_id='' if IMDS is unreachable."
        )

    def test_uses_curl_fail_so_pipefail_traps_imds_failure(self):
        """curl --fail returns non-zero on HTTP >=400; `set -euo pipefail` then
        halts the script so systemctl start never runs with empty WORKER_ID."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # Both IMDS curl calls must use --fail.
        # Crude but effective: count the occurrences we expect.
        assert out.count("--fail") >= 2

    def test_does_not_use_imdsv1(self):
        """Unauthenticated GET /latest/meta-data/instance-id (IMDSv1) is SSRF-
        exploitable. Only the token-header form is permitted."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        # Every occurrence of the instance-id path must be accompanied by the
        # v2 token header in the same curl invocation. Easiest check: the
        # instance-id line must come AFTER an X-aws-ec2-metadata-token: line
        # AND there must not be a curl to that path without a token.
        for line in out.splitlines():
            stripped = line.strip()
            if (stripped.startswith("curl") or "curl " in stripped) and (
                "/latest/meta-data/instance-id" in stripped
            ):
                assert "X-aws-ec2-metadata-token:" in stripped, (
                    f"IMDSv1 curl detected (no token header): {line!r}"
                )

    def test_sed_then_append_prevents_duplicate_env_lines(self):
        """sed -i deletes the render-time placeholder line before appending
        the IMDS value — guarantees a single STARSECTOR_WORKER_WORKER_ID
        line in the final env file, not two with systemd last-wins ambiguity."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config()
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        sed_pos = out.find("sed -i")
        append_pos = out.find(">> /etc/starsector-worker.env")
        assert sed_pos != -1, "sed -i should clear placeholder before IMDS append"
        assert append_pos != -1, "IMDS value should be appended with >>"
        assert sed_pos < append_pos, "sed must run BEFORE append"
        # Verify the sed pattern matches STARSECTOR_WORKER_WORKER_ID= lines.
        assert "STARSECTOR_WORKER_WORKER_ID" in out

    def test_accepts_empty_worker_id_placeholder(self):
        """WorkerConfig.worker_id='' is the render-time default; must not raise."""
        from starsector_optimizer.cloud_userdata import render_user_data
        cfg = _make_worker_config(worker_id="")
        out = render_user_data(cfg, tailscale_authkey=TAILSCALE_SECRET_SENTINEL)
        assert isinstance(out, str) and len(out) > 0


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
