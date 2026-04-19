#!/usr/bin/env bash
# Rootless teardown for the dev environment brought up by devenv-up.sh.
# Clears `tailscale serve` mappings, logs out of the tailnet, stops tailscaled
# and redis-server. Idempotent: safe to re-run.
#
# Usage:
#   scripts/cloud/devenv-down.sh

set -uo pipefail

STATE_DIR="${STARSECTOR_DEVENV_STATE_DIR:-$HOME/.local/state/starsector-cloud}"
TS_SOCKET="$STATE_DIR/tailscale/tailscaled.sock"
TS_PID_FILE="$STATE_DIR/tailscale/tailscaled.pid"
REDIS_PID_FILE="$STATE_DIR/redis/redis.pid"
REDIS_PORT="${STARSECTOR_REDIS_PORT:-6379}"

msg() { echo "[devenv-down] $*" >&2; }

# --- tailscale serve mappings + tailnet logout (best-effort) -----------------
if [[ -S "$TS_SOCKET" ]]; then
    tailscale --socket="$TS_SOCKET" serve reset 2>/dev/null || true
    tailscale --socket="$TS_SOCKET" logout 2>/dev/null || true
fi

# --- tailscaled --------------------------------------------------------------
if [[ -f "$TS_PID_FILE" ]]; then
    pid="$(cat "$TS_PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.3
        done
        kill -9 "$pid" 2>/dev/null || true
        msg "tailscaled stopped (pid=$pid)"
    fi
    rm -f "$TS_PID_FILE"
fi
rm -f "$TS_SOCKET"

# --- redis-server ------------------------------------------------------------
if [[ -f "$REDIS_PID_FILE" ]]; then
    pid="$(cat "$REDIS_PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
        # Prefer the graceful path; fall back to SIGTERM.
        redis-cli -p "$REDIS_PORT" shutdown nosave 2>/dev/null \
            || kill "$pid" 2>/dev/null \
            || true
        for _ in 1 2 3 4 5; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.3
        done
        msg "redis stopped (pid=$pid)"
    fi
    rm -f "$REDIS_PID_FILE"
fi

msg "teardown complete"
