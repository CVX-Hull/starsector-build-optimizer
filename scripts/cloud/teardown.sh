#!/bin/bash
# Delete all sim-worker machines.
# Usage: ./teardown.sh [num_machines]
set -euo pipefail

NUM_MACHINES=${1:-1}

echo "Deleting $NUM_MACHINES sim-worker machines..."
for i in $(seq 0 $((NUM_MACHINES - 1))); do
    echo "  Deleting sim-worker-${i}..."
    hcloud server delete "sim-worker-${i}" 2>/dev/null || echo "  sim-worker-${i} not found"
done
echo "Done."
