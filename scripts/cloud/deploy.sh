#!/bin/bash
# Deploy Hetzner Cloud machine(s) for Starsector optimizer simulation.
# Usage: ./deploy.sh [num_machines] [server_type] [locations]
#
# `locations` is a comma-separated fallback list. Default: "ash,hil,fsn1,nbg1".
# If creation in the first location fails with `resource_unavailable`, the
# next location is tried. Ashburn tends to be tight on CCX33 capacity
# (observed 2026-04-18 during Phase 5E validation); Hillsboro is usually
# the reliable US fallback.
set -euo pipefail

NUM_MACHINES=${1:-1}
SERVER_TYPE=${2:-ccx33}
LOCATIONS=${3:-ash,hil,fsn1,nbg1}
SSH_KEY_NAME="starsector-opt"
SSH_KEY_FILE="$HOME/.ssh/starsector-opt"

OPTIMIZER_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
GAME_DIR="$OPTIMIZER_DIR/game/starsector"
PREFS_FILE="$HOME/.java/.userPrefs/com/fs/starfarer/prefs.xml"
CLOUD_INIT="$OPTIMIZER_DIR/scripts/cloud/cloud-init.yaml"

# Validate prerequisites
if [ ! -d "$GAME_DIR" ]; then
    echo "Error: Game directory not found at $GAME_DIR"
    echo "Set GAME_DIR or symlink your Starsector installation there."
    exit 1
fi
if [ ! -f "$SSH_KEY_FILE" ]; then
    echo "Error: SSH key not found at $SSH_KEY_FILE"
    exit 1
fi
if [ ! -f "$PREFS_FILE" ]; then
    echo "Error: Game activation prefs not found at $PREFS_FILE"
    exit 1
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY_FILE"

# Create one server, trying each location in order until one succeeds.
# Returns 0 on success, prints the chosen location to stdout.
create_one() {
    local name="$1"
    local chosen=""
    IFS=',' read -r -a loc_arr <<< "$LOCATIONS"
    for loc in "${loc_arr[@]}"; do
        if hcloud server create \
            --name "$name" \
            --type "$SERVER_TYPE" \
            --image ubuntu-24.04 \
            --ssh-key "$SSH_KEY_NAME" \
            --location "$loc" \
            --user-data-from-file "$CLOUD_INIT" >/dev/null 2>&1; then
            chosen="$loc"
            break
        fi
        echo "[$name] location $loc unavailable, trying next..." >&2
    done
    if [ -z "$chosen" ]; then
        echo "[$name] FATAL: no location in ($LOCATIONS) had capacity" >&2
        return 1
    fi
    echo "$chosen"
}

echo "Creating $NUM_MACHINES x $SERVER_TYPE machines (locations: $LOCATIONS)..."

# Create sequentially so a later worker can reuse an earlier worker's
# successful location. Parallel creation with fallback is racy against
# per-location quotas.
declare -A SERVER_LOCATION
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    name="sim-worker-${i}"
    loc=$(create_one "$name") || exit 1
    SERVER_LOCATION[$name]="$loc"
    echo "[$name] created in $loc"
done

echo "Machines created. Waiting for cloud-init and deploying..."

# Track per-worker success for a single post-deploy pass/fail summary.
FAILED_WORKERS=""

deploy_one() {
    local i="$1"
    local name="sim-worker-${i}"
    local IP
    IP=$(hcloud server describe "$name" -o format='{{.PublicNet.IPv4.IP}}')

    echo "[$i] Waiting for cloud-init on $IP..."
    # Poll for the /tmp/cloud-init-done marker (cloud-init now gates this on
    # a verified uv install — see cloud-init.yaml). `cloud-init status --wait`
    # can hang on Hetzner Ubuntu 24.04 images.
    local ready=0
    for attempt in $(seq 1 72); do
        if ssh $SSH_OPTS root@"$IP" "test -f /tmp/cloud-init-done" </dev/null 2>/dev/null; then
            ready=1
            break
        fi
        sleep 5
    done
    if [ $ready -eq 0 ]; then
        echo "[$i] FATAL: cloud-init did not complete within 6 minutes on $IP"
        return 1
    fi

    echo "[$i] Syncing game directory..."
    rsync -az -e "ssh $SSH_OPTS" "$GAME_DIR/" root@"$IP":/opt/starsector/

    echo "[$i] Syncing optimizer code..."
    rsync -az -e "ssh $SSH_OPTS" \
        --exclude='.git' \
        --exclude='game/' \
        --exclude='experiments/' \
        --exclude='notebooks/' \
        --exclude='data/' \
        --exclude='*.db' \
        "$OPTIMIZER_DIR/" root@"$IP":/opt/optimizer/

    echo "[$i] Copying game activation..."
    ssh $SSH_OPTS root@"$IP" "mkdir -p /root/.java/.userPrefs/com/fs/starfarer/" </dev/null
    scp $SSH_OPTS "$PREFS_FILE" root@"$IP":/root/.java/.userPrefs/com/fs/starfarer/prefs.xml

    echo "[$i] Installing Python dependencies..."
    if ! ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && /root/.local/bin/uv sync" </dev/null; then
        echo "[$i] FATAL: uv sync failed on $IP"
        return 1
    fi

    echo "[$i] Verifying deployment..."
    # Post-deploy smoke: uv version + deep import that exercises scipy/optuna/
    # the Phase 5D+5E pipeline. Hard-fails the deploy if any of these are
    # missing — catches the silent failure mode observed 2026-04-18 where
    # deploy.sh reported success despite uv being absent.
    if ! ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && /root/.local/bin/uv run python -c '
from starsector_optimizer.optimizer import _shape_fitness, _ShapeDiag
from starsector_optimizer.deconfounding import eb_shrinkage, triple_goal_rank
from starsector_optimizer.models import ShapeConfig, EBShrinkageConfig
print(\"verify:OK shape/eb/triple_goal importable\")
'" </dev/null; then
        echo "[$i] FATAL: post-deploy import smoke failed on $IP"
        return 1
    fi

    echo "[$i] Ready at $IP (location=${SERVER_LOCATION[$name]})"
    return 0
}

# Deploy workers in parallel, but capture per-worker exit codes. The parent
# waits for all and emits a final PASS/FAIL summary.
pids=()
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    deploy_one "$i" &
    pids+=($!)
done

for idx in "${!pids[@]}"; do
    if ! wait "${pids[$idx]}"; then
        FAILED_WORKERS="$FAILED_WORKERS sim-worker-${idx}"
    fi
done

echo ""
if [ -n "$FAILED_WORKERS" ]; then
    echo "DEPLOY FAILED for:$FAILED_WORKERS"
    echo "Run ./scripts/cloud/teardown.sh $NUM_MACHINES to clean up, then retry."
    exit 1
fi

echo "All $NUM_MACHINES machines deployed AND verified."
echo "IPs:"
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    name="sim-worker-${i}"
    IP=$(hcloud server describe "$name" -o format='{{.PublicNet.IPv4.IP}}')
    echo "  $name: $IP  (location=${SERVER_LOCATION[$name]})"
done
