"""Cloud-init UserData renderer — pure functions, no I/O.

Emits the bash script that AWSProvider injects into each launch template.
Cloud-init runs it on first boot as root. The script:

  1. `tailscale up --authkey ...` so workstation-side Redis + Flask become
     reachable via the tailnet.
  2. Writes /etc/starsector-worker.env (0600 root:root) with every
     WorkerConfig field exposed as a STARSECTOR_WORKER_* env var — the
     service unit baked into the AMI reads this file via EnvironmentFile.
  3. `systemctl start starsector-worker.service` — the worker_agent loop
     begins.

For Tier-1 probes the worker does not run, so render_probe_user_data emits
a trivial boot marker instead.
"""

from __future__ import annotations

import dataclasses
import shlex

from .models import WorkerConfig


_ENV_FILE = "/etc/starsector-worker.env"
_SERVICE = "starsector-worker.service"


def render_user_data(
    worker_config: WorkerConfig,
    *,
    tailscale_authkey: str,
) -> str:
    """Produce the cloud-init bash script for a real worker boot.

    Every WorkerConfig field becomes a STARSECTOR_WORKER_<FIELD> env var.
    """
    env_lines: list[str] = []
    for f in dataclasses.fields(worker_config):
        value = getattr(worker_config, f.name)
        env_lines.append(
            f"STARSECTOR_WORKER_{f.name.upper()}={shlex.quote(str(value))}"
        )
    env_body = "\n".join(env_lines)

    # Secret handling:
    #   - umask 077 BEFORE the heredoc so the env file is created 0600, with
    #     no 0644 window between write + chmod. (Audit A finding 2026-04-18.)
    #   - Tailscale authkey fed via stdin, NOT argv. /proc/<pid>/cmdline is
    #     world-readable on Linux by default, so `tailscale up --authkey=<key>`
    #     would leak the secret to any local user during boot. Piping via
    #     stdin keeps it in-process.
    #   - The env-file heredoc is a bash single-quoted heredoc — no variable
    #     expansion — so values embedding `$` cannot be reinterpreted.
    script = f"""#!/bin/bash
set -euo pipefail
umask 077

# --- Tailscale join (authkey on stdin, not argv) ---
tailscale up --authkey-stdin \\
    --advertise-tags=tag:starsector-worker \\
    --accept-dns=false <<'TS_AUTHKEY_EOF'
{tailscale_authkey}
TS_AUTHKEY_EOF

# --- Environment file for the worker service (0600 via umask) ---
cat >{_ENV_FILE} <<'STARSECTOR_WORKER_ENV_EOF'
{env_body}
STARSECTOR_WORKER_ENV_EOF
chown root:root {_ENV_FILE}

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
