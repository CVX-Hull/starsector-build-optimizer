#!/bin/bash
# Check optimizer status on cloud machines.
# Usage: ./status.sh [num_machines]
set -euo pipefail

NUM_MACHINES=${1:-1}
SSH_KEY_FILE="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY_FILE"

for i in $(seq 0 $((NUM_MACHINES - 1))); do
    IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}' 2>/dev/null) || { echo "sim-worker-${i}: not found"; continue; }
    echo "=== sim-worker-${i} ($IP) ==="

    # Check if optimizer is running
    RUNNING=$(ssh $SSH_OPTS root@"$IP" "pgrep -f run_optimizer.py >/dev/null && echo 'RUNNING' || echo 'STOPPED'" 2>/dev/null) || RUNNING="UNREACHABLE"
    echo "  Status: $RUNNING"

    # Show last 5 lines of log
    ssh $SSH_OPTS root@"$IP" "tail -5 /opt/optimizer/run.log 2>/dev/null" | sed 's/^/  /' || true
    echo ""
done
