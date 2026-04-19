#!/usr/bin/env bash
# Rootless dev environment for Starsector Phase 6 Tier-2 smoke / prep.
#
# Brings up, as the current user and without sudo:
#   * userspace-mode tailscaled on a per-user socket
#   * redis-server bound to 127.0.0.1
#   * `tailscale serve` TCP proxies that expose Redis + the Flask result-port
#     range (default 9000-9099) over the tailnet to remote workers
#
# State lives under $STARSECTOR_DEVENV_STATE_DIR (default
# ~/.local/state/starsector-cloud), so the CampaignManager preflight can
# auto-detect the tailscaled socket without needing an exported env var.
#
# Usage:
#   export TAILSCALE_AUTHKEY=tskey-auth-...
#   scripts/cloud/devenv-up.sh
#   scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml
#   scripts/cloud/devenv-down.sh
#
# Idempotent: re-running while the daemons are up is a no-op (checks PIDs).

set -euo pipefail

STATE_DIR="${STARSECTOR_DEVENV_STATE_DIR:-$HOME/.local/state/starsector-cloud}"
mkdir -p "$STATE_DIR/tailscale" "$STATE_DIR/redis"

TS_SOCKET="$STATE_DIR/tailscale/tailscaled.sock"
TS_STATE_FILE="$STATE_DIR/tailscale/tailscaled.state"
TS_PID_FILE="$STATE_DIR/tailscale/tailscaled.pid"
TS_LOG="$STATE_DIR/tailscale/tailscaled.log"

REDIS_PORT="${STARSECTOR_REDIS_PORT:-6379}"
REDIS_PID_FILE="$STATE_DIR/redis/redis.pid"
REDIS_LOG="$STATE_DIR/redis/redis.log"

FLASK_PORT_MIN="${STARSECTOR_FLASK_PORT_MIN:-9000}"
FLASK_PORT_MAX="${STARSECTOR_FLASK_PORT_MAX:-9099}"

: "${TAILSCALE_AUTHKEY:?TAILSCALE_AUTHKEY must be exported before running devenv-up.sh}"

msg() { echo "[devenv-up] $*" >&2; }

# --- redis-server ------------------------------------------------------------

if [[ -f "$REDIS_PID_FILE" ]] && kill -0 "$(cat "$REDIS_PID_FILE")" 2>/dev/null; then
    msg "redis already running (pid=$(cat "$REDIS_PID_FILE"))"
else
    redis-server \
        --port "$REDIS_PORT" \
        --bind 127.0.0.1 \
        --dir "$STATE_DIR/redis" \
        --daemonize yes \
        --pidfile "$REDIS_PID_FILE" \
        --logfile "$REDIS_LOG"
    # Wait for readiness; redis-cli ping is the authoritative signal.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if redis-cli -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
            break
        fi
        sleep 0.2
    done
    msg "redis listening on 127.0.0.1:$REDIS_PORT (pid=$(cat "$REDIS_PID_FILE"))"
fi

# --- tailscaled (userspace-mode, no TUN, no root) ----------------------------

if [[ -f "$TS_PID_FILE" ]] && kill -0 "$(cat "$TS_PID_FILE")" 2>/dev/null; then
    msg "tailscaled already running (pid=$(cat "$TS_PID_FILE"))"
else
    # Userspace networking mode: no TUN device, no root needed. Inbound TCP
    # is handled via `tailscale serve` proxies (set up below).
    nohup tailscaled \
        --tun=userspace-networking \
        --socket="$TS_SOCKET" \
        --state="$TS_STATE_FILE" \
        >>"$TS_LOG" 2>&1 &
    echo $! > "$TS_PID_FILE"
    for _ in $(seq 1 30); do
        [[ -S "$TS_SOCKET" ]] && break
        sleep 0.2
    done
    if [[ ! -S "$TS_SOCKET" ]]; then
        msg "ERROR: tailscaled socket $TS_SOCKET did not appear (see $TS_LOG)"
        exit 1
    fi
    msg "tailscaled started (pid=$(cat "$TS_PID_FILE"), socket=$TS_SOCKET)"
fi

# Bring the node up on the tailnet. `tailscale up` is idempotent — a second
# invocation with a valid state file just re-verifies the current session.
tailscale --socket="$TS_SOCKET" up \
    --authkey="$TAILSCALE_AUTHKEY" \
    --hostname="starsector-workstation-$USER" \
    --accept-dns=false \
    --accept-routes=false

# --- tailscale serve: expose local ports to the tailnet ----------------------

# Clear any prior serve state (from a previous campaign) so we start clean.
tailscale --socket="$TS_SOCKET" serve reset 2>/dev/null || true

# Redis.
tailscale --socket="$TS_SOCKET" serve --bg \
    --tcp="$REDIS_PORT" "tcp://127.0.0.1:$REDIS_PORT" >/dev/null

# Flask result-port range. CampaignManager assigns per-(study,seed) ports
# from this range; see CampaignConfig.flask_ports_per_study.
for port in $(seq "$FLASK_PORT_MIN" "$FLASK_PORT_MAX"); do
    tailscale --socket="$TS_SOCKET" serve --bg \
        --tcp="$port" "tcp://127.0.0.1:$port" >/dev/null
done

TS_IP="$(tailscale --socket="$TS_SOCKET" ip -4 | head -1)"

msg "tailnet IP: $TS_IP"
msg "redis exposed on $TS_IP:$REDIS_PORT via tailscale serve"
msg "flask ports $FLASK_PORT_MIN-$FLASK_PORT_MAX exposed via tailscale serve"
msg "next: scripts/cloud/launch_campaign.sh <campaign.yaml>"
msg "tear down: scripts/cloud/devenv-down.sh"
