"""Cloud-init UserData renderer — pure functions, no I/O.

Emits the bash script that AWSProvider injects into each launch template.
Cloud-init runs it on first boot as root. The script:

  1. `set -euo pipefail` + `umask 077` so any failure halts the script
     before `systemctl start` and every file is owner-read-only.
  2. `tailscale up --auth-key=file:<tmpfile>` so workstation-side Redis +
     Flask become reachable via the tailnet. The authkey is written to a
     0600 tmpfs file and passed by path — it NEVER enters argv (Linux
     `/proc/<pid>/cmdline` is world-readable by default). The tmpfile is
     `shred -u`ed immediately after. (Modern Tailscale CLI no longer
     supports `--authkey-stdin`; `--auth-key=file:` is the equivalent
     argv-free mechanism.)
  3. Writes /etc/starsector-worker.env (0600 root:root) with every
     WorkerConfig field exposed as a STARSECTOR_WORKER_* env var.
  4. **Overrides WORKER_ID via IMDSv2** — the live EC2 instance ID is
     unknown at render time, so `render_user_data` emits worker_id="" in
     the heredoc and the script fetches the real value at boot. `sed -i`
     removes the placeholder line before appending so exactly one
     `STARSECTOR_WORKER_WORKER_ID=` entry remains.
  5. `systemctl start starsector-worker.service` — the service unit is
     baked into the AMI and begins the worker_agent loop.

For Tier-1 probes the worker does not run, so render_probe_user_data emits
a trivial boot marker instead.
"""

from __future__ import annotations

import dataclasses
import shlex

from .models import WorkerConfig


_ENV_FILE = "/etc/starsector-worker.env"
_SERVICE = "starsector-worker.service"
_IMDSV2_TOKEN_TTL_SECONDS = 300


def render_user_data(
    worker_config: WorkerConfig,
    *,
    tailscale_authkey: str,
) -> str:
    """Produce the cloud-init bash script for a real worker boot.

    Every WorkerConfig field becomes a STARSECTOR_WORKER_<FIELD> env var.
    `worker_id` at render time is a placeholder; IMDSv2 overwrites it
    before `systemctl start` so the worker never boots with an empty ID.
    """
    env_lines: list[str] = []
    for f in dataclasses.fields(worker_config):
        value = getattr(worker_config, f.name)
        env_lines.append(
            f"STARSECTOR_WORKER_{f.name.upper()}={shlex.quote(str(value))}"
        )
    env_body = "\n".join(env_lines)

    script = f"""#!/bin/bash
set -euo pipefail
umask 077

# --- Tailscale join (authkey via file:, NOT argv) ---
# Write authkey to a 0600 tmpfile (via the umask 077 above), pass by path,
# then shred. `--auth-key=file:<path>` keeps the raw key off argv — the
# only thing in /proc/<pid>/cmdline is the path.
TS_AUTHKEY_FILE=$(mktemp)
cat >"$TS_AUTHKEY_FILE" <<'TS_AUTHKEY_EOF'
{tailscale_authkey}
TS_AUTHKEY_EOF
tailscale up --auth-key=file:"$TS_AUTHKEY_FILE" \\
    --advertise-tags=tag:starsector-worker \\
    --accept-dns=false
shred -u "$TS_AUTHKEY_FILE"

# --- Environment file for the worker service (0600 via umask) ---
cat >{_ENV_FILE} <<'STARSECTOR_WORKER_ENV_EOF'
{env_body}
STARSECTOR_WORKER_ENV_EOF
chown root:root {_ENV_FILE}

# --- Override WORKER_ID with the live EC2 instance-id (IMDSv2) ---
# IMDSv2 is the token-based variant; IMDSv1 is SSRF-exploitable.
# curl --fail so set -euo pipefail halts the script if IMDS is unreachable
# — the worker never boots with an empty worker_id.
IMDS_TOKEN=$(curl --silent --fail -X PUT \\
    -H "X-aws-ec2-metadata-token-ttl-seconds: {_IMDSV2_TOKEN_TTL_SECONDS}" \\
    http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl --silent --fail \\
    -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \\
    http://169.254.169.254/latest/meta-data/instance-id)
sed -i '/^STARSECTOR_WORKER_WORKER_ID=/d' {_ENV_FILE}
echo "STARSECTOR_WORKER_WORKER_ID=$INSTANCE_ID" >> {_ENV_FILE}

# --- Start the worker ---
systemctl daemon-reload
systemctl start {_SERVICE}
"""
    return script


def render_probe_user_data(campaign_id: str) -> str:
    """Tier-1 probe: minimal boot marker; no worker, no tailscale, no secrets."""
    return f"""#!/bin/bash
set -euo pipefail
mkdir -p /var/log
echo probe-boot-ok campaign_id={shlex.quote(campaign_id)} $(date -u +%Y-%m-%dT%H:%M:%SZ) \\
    > /var/log/starsector-probe.log
"""
