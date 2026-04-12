#!/bin/bash
# Collect results from cloud machines.
# Usage: ./collect.sh [num_machines]
set -euo pipefail

NUM_MACHINES=${1:-1}
SSH_KEY_FILE="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY_FILE"
RESULTS_DIR="results/$(date +%Y%m%d-%H%M%S)"

mkdir -p "$RESULTS_DIR"

for i in $(seq 0 $((NUM_MACHINES - 1))); do
    IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}')
    echo "Collecting from sim-worker-${i} ($IP)..."

    scp $SSH_OPTS root@"$IP":/opt/optimizer/study.db "$RESULTS_DIR/study_worker_${i}.db" 2>/dev/null || echo "  No study.db"
    scp $SSH_OPTS root@"$IP":/opt/optimizer/data/evaluation_log.jsonl "$RESULTS_DIR/eval_log_worker_${i}.jsonl" 2>/dev/null || echo "  No eval log"
    scp $SSH_OPTS root@"$IP":/opt/optimizer/run.log "$RESULTS_DIR/run_log_worker_${i}.log" 2>/dev/null || echo "  No run log"
done

echo "Results collected in $RESULTS_DIR/"
ls -la "$RESULTS_DIR/"
