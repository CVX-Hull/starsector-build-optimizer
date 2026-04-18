#!/bin/bash
# Rsync local code changes to one or all deployed Hetzner workers.
# Use this when iterating on src/ or tests/ without redeploying the VM.
#
# Usage:
#   ./sync-code.sh              # sync to all sim-worker-* hosts
#   ./sync-code.sh 0            # sync to sim-worker-0 only
#   ./sync-code.sh all true     # sync all + force-run `uv sync` (when
#                                 pyproject.toml changed)
#
# Background: deploy.sh syncs the working tree at deploy time, so any code
# changes made AFTER deploy don't reach the workers. During Phase 5E
# validation (2026-04-18) the deploy ran before the Step 4 changes landed
# and I had to reach for rsync by hand. This script is the canonical path.
set -euo pipefail

TARGET=${1:-all}
FORCE_UV_SYNC=${2:-auto}
SSH_KEY_FILE="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY_FILE"
OPTIMIZER_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

# Resolve target to concrete server names.
if [ "$TARGET" = "all" ]; then
    mapfile -t SERVERS < <(hcloud server list -o noheader -o columns=name \
        | grep '^sim-worker-' || true)
    if [ ${#SERVERS[@]} -eq 0 ]; then
        echo "No sim-worker-* servers found. Run ./scripts/cloud/deploy.sh first."
        exit 1
    fi
else
    SERVERS=("sim-worker-${TARGET}")
fi

# Decide whether to run `uv sync` after pushing code. `auto` runs it when
# pyproject.toml or uv.lock changed in the working tree relative to HEAD —
# the common trigger for dependency drift.
need_uv_sync() {
    if [ "$FORCE_UV_SYNC" = "true" ]; then return 0; fi
    if [ "$FORCE_UV_SYNC" = "false" ]; then return 1; fi
    # auto: check git diff for dep-file changes
    (cd "$OPTIMIZER_DIR" && git diff --name-only HEAD -- pyproject.toml uv.lock 2>/dev/null | grep -q .)
}

DO_UV_SYNC=0
if need_uv_sync; then
    DO_UV_SYNC=1
    echo "Detected pyproject.toml or uv.lock drift — will run \`uv sync\` on each worker."
fi

sync_one() {
    local name="$1"
    local IP
    IP=$(hcloud server describe "$name" -o format='{{.PublicNet.IPv4.IP}}' 2>/dev/null) || {
        echo "[$name] not found"
        return 1
    }

    echo "[$name] Rsyncing src/ tests/ scripts/ to $IP..."
    rsync -az --delete -e "ssh $SSH_OPTS" \
        "$OPTIMIZER_DIR/src/" root@"$IP":/opt/optimizer/src/
    rsync -az --delete -e "ssh $SSH_OPTS" \
        "$OPTIMIZER_DIR/tests/" root@"$IP":/opt/optimizer/tests/
    rsync -az --delete -e "ssh $SSH_OPTS" \
        "$OPTIMIZER_DIR/scripts/" root@"$IP":/opt/optimizer/scripts/

    if [ $DO_UV_SYNC -eq 1 ]; then
        rsync -az -e "ssh $SSH_OPTS" \
            "$OPTIMIZER_DIR/pyproject.toml" \
            "$OPTIMIZER_DIR/uv.lock" \
            root@"$IP":/opt/optimizer/
        echo "[$name] Running uv sync..."
        ssh $SSH_OPTS root@"$IP" \
            "cd /opt/optimizer && /root/.local/bin/uv sync" </dev/null
    fi

    # Post-sync smoke: verify imports still work after the code push.
    # Catches broken syntax / missing imports before the user runs a
    # long experiment against the worker.
    if ! ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && /root/.local/bin/uv run python -c '
from starsector_optimizer.optimizer import _shape_fitness
from starsector_optimizer.deconfounding import eb_shrinkage
print(\"[$name] verify:OK\")
'" </dev/null; then
        echo "[$name] FATAL: post-sync import smoke failed"
        return 1
    fi
    echo "[$name] Ready at $IP"
}

FAILED=""
pids=()
for name in "${SERVERS[@]}"; do
    sync_one "$name" &
    pids+=($!)
done

idx=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        FAILED="$FAILED ${SERVERS[$idx]}"
    fi
    idx=$((idx + 1))
done

if [ -n "$FAILED" ]; then
    echo "SYNC FAILED for:$FAILED"
    exit 1
fi
echo "All ${#SERVERS[@]} workers synced."
