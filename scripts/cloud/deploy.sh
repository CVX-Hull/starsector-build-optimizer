#!/bin/bash
# Deploy Hetzner Cloud machine(s) for Starsector optimizer simulation.
# Usage: ./deploy.sh [num_machines] [server_type] [location]
set -euo pipefail

NUM_MACHINES=${1:-1}
SERVER_TYPE=${2:-ccx33}
LOCATION=${3:-nbg1}
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

echo "Creating $NUM_MACHINES x $SERVER_TYPE machines..."

# Create machines in parallel
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    hcloud server create \
        --name "sim-worker-${i}" \
        --type "$SERVER_TYPE" \
        --image ubuntu-24.04 \
        --ssh-key "$SSH_KEY_NAME" \
        --location "$LOCATION" \
        --user-data-from-file "$CLOUD_INIT" &
done
wait

echo "Machines created. Waiting for cloud-init and deploying..."

# Deploy to all machines in parallel
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}')
    (
        echo "[$i] Waiting for cloud-init on $IP..."
        # Poll for cloud-init completion (cloud-init status --wait can hang)
        for attempt in $(seq 1 60); do
            if ssh $SSH_OPTS root@"$IP" "test -f /tmp/cloud-init-done" 2>/dev/null; then
                break
            fi
            sleep 5
        done

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
        ssh $SSH_OPTS root@"$IP" "mkdir -p /root/.java/.userPrefs/com/fs/starfarer/"
        scp $SSH_OPTS "$PREFS_FILE" root@"$IP":/root/.java/.userPrefs/com/fs/starfarer/prefs.xml

        echo "[$i] Installing Python dependencies..."
        ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && /root/.local/bin/uv sync 2>&1"

        echo "[$i] Ready at $IP"
    ) &
done
wait

echo ""
echo "All $NUM_MACHINES machines deployed."
echo "IPs:"
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}')
    echo "  sim-worker-${i}: $IP"
done
