#!/usr/bin/env bash
# Launch the Phase 7 learned-surrogate AWS batch with an outer teardown/audit
# trap. The Python CLI owns normal orchestration; this wrapper is the
# operator-facing backstop for unattended runs.
set -euo pipefail

# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

CONFIG="examples/phase7-learned-batch.yaml"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="${2:?--config requires a path}"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--config examples/phase7-learned-batch.yaml]" >&2
      exit 2
      ;;
  esac
done

BATCH_NAME=$(uv run python -c 'from pathlib import Path; import sys, yaml; print(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["name"])' "$CONFIG")
CONTROL_PLANE_PORT=$(uv run python -c 'from pathlib import Path; import sys, yaml; print(yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))["control_plane_port"])' "$CONFIG")

TS_SOCKET="${STARSECTOR_TAILSCALE_SOCKET:-$HOME/.local/state/starsector-cloud/tailscale/tailscaled.sock}"
if [[ -S "$TS_SOCKET" ]]; then
  tailscale --socket="$TS_SOCKET" serve --bg \
    --tcp="$CONTROL_PLANE_PORT" "tcp://127.0.0.1:$CONTROL_PLANE_PORT" >/dev/null
else
  tailscale serve --bg \
    --tcp="$CONTROL_PLANE_PORT" "tcp://127.0.0.1:$CONTROL_PLANE_PORT" >/dev/null
fi

cleanup() {
  local status=$?
  local cleanup_status=0
  scripts/cloud/teardown.sh "$BATCH_NAME" || cleanup_status=$?
  scripts/cloud/final_audit.sh "$BATCH_NAME" || cleanup_status=$?
  if [[ "$status" -ne 0 ]]; then
    return "$status"
  fi
  return "$cleanup_status"
}
trap cleanup EXIT

echo "Cleanup command: scripts/cloud/teardown.sh $BATCH_NAME"
uv run python scripts/cloud/phase7_learned_batch.py launch --config "$CONFIG" --execute
