#!/usr/bin/env bash
# Tailnet-served combat-harness.jar publisher for Java-only fast iteration.
#
# Builds the jar locally, computes its sha256, exposes it as a one-file HTTP
# server on the tailnet, and prints the env vars `cloud_runner.py` reads:
#
#   STARSECTOR_MOD_JAR_OVERRIDE_URL    — http://<tailnet-ip>:<port>/combat-harness.jar
#   STARSECTOR_MOD_JAR_OVERRIDE_SHA256 — 64 hex chars (sha256 of the jar)
#
# When both env vars are set at campaign launch, every worker fetches this
# jar at boot and overlays the AMI-baked copy. AMI rebakes are no longer
# required for Java-only edits — the inner loop becomes:
#
#   1. edit Java
#   2. ./scripts/cloud/serve_mod_jar.sh                  (rebuilds jar; ~3s)
#   3. eval "$(./scripts/cloud/serve_mod_jar.sh --env)"  (exports env vars)
#   4. ./scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml
#
# (1) and (3) can be a single invocation; the script keeps the HTTP server
# running in the foreground so the operator can Ctrl-C when done.
#
# Tailnet exposure: assumes `devenv-up.sh` has already run and added the
# JAR-server port to `tailscale serve`. The port is configurable via
# STARSECTOR_MOD_JAR_PORT (default 8081).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${STARSECTOR_MOD_JAR_PORT:-8081}"
STATE_DIR="${STARSECTOR_DEVENV_STATE_DIR:-$HOME/.local/state/starsector-cloud}"
TS_SOCKET="$STATE_DIR/tailscale/tailscaled.sock"

msg() { echo "[serve-mod-jar] $*" >&2; }

# --- Resolve tailnet IP from devenv-up's userspace tailscaled, or fall back
#     to a kernel-mode `tailscale` if the userspace socket isn't present. ---
resolve_tailnet_ip() {
    local ip
    if [[ -S "$TS_SOCKET" ]]; then
        ip="$(tailscale --socket="$TS_SOCKET" ip -4 2>/dev/null | head -1 || true)"
    else
        ip="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    fi
    if [[ -z "$ip" ]]; then
        echo "ERROR: could not resolve tailnet IP. Run scripts/cloud/devenv-up.sh first." >&2
        exit 1
    fi
    echo "$ip"
}

# --- Build the jar (skips re-build if Gradle's incremental cache is hot). ---
# combat-harness's `jar` task writes to `mod/jars/` (matching the deployed
# layout), not the gradle default `build/libs/`. See
# `combat-harness/build.gradle.kts: destinationDirectory`.
build_jar() {
    local jdk_home="${STARSECTOR_JDK_HOME:?STARSECTOR_JDK_HOME must be exported (path to JDK 17)}"
    msg "building combat-harness.jar (JDK $jdk_home)"
    (cd "$PROJECT_ROOT/combat-harness" && JAVA_HOME="$jdk_home" ./gradlew --quiet jar)
    echo "$PROJECT_ROOT/combat-harness/mod/jars/combat-harness.jar"
}

# --- Compute sha256 (sha256sum on Linux, shasum -a 256 on macOS). ---
compute_sha256() {
    local jar="$1"
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$jar" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$jar" | awk '{print $1}'
    else
        echo "ERROR: need sha256sum or shasum on PATH" >&2
        exit 1
    fi
}

# --- Expose the JAR port through tailscale serve so workers can curl it. ---
expose_via_tailscale_serve() {
    local port="$1"
    if [[ -S "$TS_SOCKET" ]]; then
        # Userspace mode (devenv-up.sh). `--bg` so it persists; idempotent.
        tailscale --socket="$TS_SOCKET" serve --bg \
            --tcp="$port" "tcp://127.0.0.1:$port" >/dev/null
    else
        # Kernel mode: bind directly to the tailnet IP via the HTTP server's
        # bind address, no proxy needed. Caller passes the tailnet IP to
        # `python -m http.server --bind <ip>`.
        :
    fi
}

# --- Mode 1: --env-only — print exports + exit (no server). For sourcing. ---
if [[ "${1:-}" == "--env" ]]; then
    JAR_PATH="$(build_jar)"
    SHA="$(compute_sha256 "$JAR_PATH")"
    IP="$(resolve_tailnet_ip)"
    echo "export STARSECTOR_MOD_JAR_OVERRIDE_URL=http://$IP:$PORT/combat-harness.jar"
    echo "export STARSECTOR_MOD_JAR_OVERRIDE_SHA256=$SHA"
    exit 0
fi

# --- Mode 2: build + serve in foreground until Ctrl-C. ---
JAR_PATH="$(build_jar)"
SHA="$(compute_sha256 "$JAR_PATH")"
IP="$(resolve_tailnet_ip)"
SERVE_DIR="$(mktemp -d)"
cp "$JAR_PATH" "$SERVE_DIR/combat-harness.jar"
trap 'rm -rf "$SERVE_DIR"' EXIT

expose_via_tailscale_serve "$PORT"

cat <<EOF
[serve-mod-jar] jar:    $JAR_PATH
[serve-mod-jar] sha256: $SHA
[serve-mod-jar] url:    http://$IP:$PORT/combat-harness.jar

Export these in the shell that runs launch_campaign.sh:

  export STARSECTOR_MOD_JAR_OVERRIDE_URL=http://$IP:$PORT/combat-harness.jar
  export STARSECTOR_MOD_JAR_OVERRIDE_SHA256=$SHA

Or eval the --env mode in one go:

  eval "\$(scripts/cloud/serve_mod_jar.sh --env)"

Serving on $IP:$PORT — Ctrl-C to stop.
EOF

# Bind to 127.0.0.1 — `tailscale serve` proxies the port for userspace
# tailscaled. For kernel-mode fallback, set STARSECTOR_MOD_JAR_BIND to the
# tailnet IP (or 0.0.0.0 if you really want the LAN to see it too).
BIND="${STARSECTOR_MOD_JAR_BIND:-127.0.0.1}"
cd "$SERVE_DIR"
exec python3 -m http.server --bind "$BIND" "$PORT"
