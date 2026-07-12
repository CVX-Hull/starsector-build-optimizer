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
# Path the AMI bakes the combat-harness jar at — must match the Packer
# `file` block in `scripts/cloud/packer/aws.pkr.hcl` and the gradle deploy
# task in `combat-harness/build.gradle.kts`. The mod_info.json's
# `jars` entry resolves relative to the mod root, so the absolute path is
# `<game_dir>/mods/combat-harness/jars/combat-harness.jar`.
_BAKED_MOD_JAR_PATH = "/opt/starsector/mods/combat-harness/jars/combat-harness.jar"

# `_SHA256_HEX_LEN` would be a magic number if inlined; the cloud-init
# script validates the operator-supplied digest length against this.
_SHA256_HEX_LEN = 64


def render_user_data(
    worker_config: WorkerConfig,
    *,
    tailscale_authkey: str,
    debug_ssh_pubkey: str = "",
    mod_jar_override_url: str = "",
    mod_jar_override_sha256: str = "",
) -> str:
    """Produce the cloud-init bash script for a real worker boot.

    Every WorkerConfig field becomes a STARSECTOR_WORKER_<FIELD> env var.
    `worker_id` at render time is a placeholder; IMDSv2 overwrites it
    before `systemctl start` so the worker never boots with an empty ID.

    `debug_ssh_pubkey`: optional ssh-ed25519/rsa pubkey line. If non-empty,
    the script appends it to /home/ubuntu/.ssh/authorized_keys so the
    operator can `ssh -i <matching-private-key> ubuntu@<worker-tailnet-ip>`
    when a worker hangs. Pass empty string for production runs to keep the
    blast radius minimal. **This is the only operator-SSH path** — Tailscale
    SSH (`--ssh` on `tailscale up`) was tried smoke #8 2026-05-09 and removed
    because it hijacks port 22 and gates via the tailnet ACL (default
    personal tailnets silent-deny for SSH).

    `mod_jar_override_url` + `mod_jar_override_sha256`: optional pair. When
    BOTH are non-empty, after `tailscale up` the script curls the URL,
    verifies sha256, and overlays the result onto the AMI-baked combat-
    harness jar before starting the worker. Lets Java-only iterations skip
    AMI rebakes — operator runs `scripts/cloud/serve_mod_jar.sh` to publish
    a freshly built JAR over the tailnet, exports the two env vars,
    relaunches the campaign, and the workers fetch the new jar at boot.
    Both vars must be set together: setting URL alone or SHA256 alone is a
    boot-time fatal (we never silently skip verification). The override is
    fail-closed — any download error, sha mismatch, or chown failure halts
    the script via `set -euo pipefail` before `systemctl start`.
    """
    _validate_jar_override(mod_jar_override_url, mod_jar_override_sha256)
    env_lines: list[str] = []
    for f in dataclasses.fields(worker_config):
        value = getattr(worker_config, f.name)
        env_lines.append(
            f"STARSECTOR_WORKER_{f.name.upper()}={shlex.quote(str(value))}"
        )
    env_body = "\n".join(env_lines)

    if debug_ssh_pubkey.strip():
        # Heredoc body so newlines/special chars in the pubkey survive
        # cloud-init's bash without shell interpretation. The pubkey is
        # public material — no umask/shred dance required, but we still
        # 0600 the authorized_keys to match Ubuntu's ssh expectations.
        debug_pubkey_block = f"""
# --- Debug SSH pubkey injection (operator-controlled) ---
install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
cat >>/home/ubuntu/.ssh/authorized_keys <<'STARSECTOR_DEBUG_PUBKEY_EOF'
{debug_ssh_pubkey.strip()}
STARSECTOR_DEBUG_PUBKEY_EOF
chown ubuntu:ubuntu /home/ubuntu/.ssh/authorized_keys
chmod 0600 /home/ubuntu/.ssh/authorized_keys
"""
    else:
        debug_pubkey_block = ""

    script = f"""#!/bin/bash
set -euo pipefail
umask 077

# --- Tailscale join (authkey via file:, NOT argv) ---
# Write authkey to a 0600 tmpfile (via the umask 077 above), pass by path,
# then shred. `--auth-key=file:<path>` keeps the raw key off argv — the
# only thing in /proc/<pid>/cmdline is the path.
# `--ssh` is intentionally NOT passed: Tailscale SSH hijacks port 22 and
# gates connections via the tailnet ACL; on a default-permissive personal
# tailnet the ACL still has no `ssh` clause, so connection attempts are
# silently dropped (cloud-init prints `Tailscale SSH enabled, but access
# controls don't allow anyone to access this device`). Operator SSH is
# the `debug_ssh_pubkey` injection block below — keys land in
# /home/ubuntu/.ssh/authorized_keys and the regular sshd answers port 22.
TS_AUTHKEY_FILE=$(mktemp)
cat >"$TS_AUTHKEY_FILE" <<'TS_AUTHKEY_EOF'
{tailscale_authkey}
TS_AUTHKEY_EOF
tailscale up --auth-key=file:"$TS_AUTHKEY_FILE" \\
    --advertise-tags=tag:starsector-worker \\
    --accept-dns=false
shred -u "$TS_AUTHKEY_FILE"
{debug_pubkey_block}
{_render_jar_override_block(mod_jar_override_url, mod_jar_override_sha256)}

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


def _validate_jar_override(url: str, sha256: str) -> None:
    """Operator-facing validation: surface contract breaks at render time.

    Catches the easy mistakes (one var set without the other, malformed
    digest length) while the orchestrator is still in Python where the
    error is visible. The cloud-init script also re-validates at boot —
    that's the authoritative line of defense — but failing here lets the
    operator notice before paying for a fleet provision.
    """
    if bool(url) != bool(sha256):
        raise ValueError(
            "mod_jar_override_url and mod_jar_override_sha256 must be set "
            "together; setting one without the other would silently skip "
            "verification."
        )
    if sha256 and len(sha256) != _SHA256_HEX_LEN:
        raise ValueError(
            f"mod_jar_override_sha256 must be {_SHA256_HEX_LEN} hex chars "
            f"(got {len(sha256)}); use `sha256sum combat-harness.jar`."
        )


def _render_jar_override_block(url: str, sha256: str) -> str:
    """Bash block that curls a fresh combat-harness.jar from the operator's
    workstation over the tailnet, verifies sha256, and overlays the AMI's
    baked jar. Empty when no override is configured.

    Fail-closed: every failure mode (curl error, sha mismatch, install
    failure) propagates out via `set -euo pipefail` and halts boot before
    `systemctl start`. The worker never runs against a wrong-jar AMI.
    """
    if not url:
        return ""
    return f"""
# --- Optional combat-harness.jar overlay (Java-only fast iteration) ---
# When set, fetches a freshly built jar from the operator's workstation
# over the tailnet and replaces the AMI-baked jar before starting the
# worker. Verified against the operator-supplied sha256 — any mismatch
# halts boot. Lets Java-only iterations skip AMI rebakes (~15min →
# ~30s per loop). See scripts/cloud/serve_mod_jar.sh.
JAR_OVERRIDE_URL={shlex.quote(url)}
JAR_OVERRIDE_SHA256={shlex.quote(sha256)}
JAR_TMP=$(mktemp --suffix=.jar)
echo "[mod-jar-overlay] fetching $JAR_OVERRIDE_URL"
curl --silent --fail --show-error --location \\
    --max-time 60 --retry 3 --retry-delay 2 \\
    -o "$JAR_TMP" "$JAR_OVERRIDE_URL"
JAR_ACTUAL_SHA=$(sha256sum "$JAR_TMP" | awk '{{print $1}}')
if [ "$JAR_ACTUAL_SHA" != "$JAR_OVERRIDE_SHA256" ]; then
    echo "[mod-jar-overlay] sha256 mismatch: expected=$JAR_OVERRIDE_SHA256 actual=$JAR_ACTUAL_SHA" >&2
    rm -f "$JAR_TMP"
    exit 1
fi
install -m 0644 -o ubuntu -g ubuntu "$JAR_TMP" {_BAKED_MOD_JAR_PATH}
rm -f "$JAR_TMP"
echo "[mod-jar-overlay] installed jar (sha256=$JAR_OVERRIDE_SHA256)"
"""


def render_probe_user_data(campaign_id: str) -> str:
    """Tier-1 probe: minimal boot marker; no worker, no tailscale, no secrets."""
    return f"""#!/bin/bash
set -euo pipefail
mkdir -p /var/log
echo probe-boot-ok campaign_id={shlex.quote(campaign_id)} "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \\
    > /var/log/starsector-probe.log
"""
