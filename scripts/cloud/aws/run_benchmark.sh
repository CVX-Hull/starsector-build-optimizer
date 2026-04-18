#!/bin/bash
# Run a time-bounded Starsector optimizer benchmark on a deployed instance.
# Usage: ./run_benchmark.sh <num_instances> <sim_budget> [hull]
# Example: ./run_benchmark.sh 3 30 hammerhead
#
# Collects:
#   - run.log (stdout of optimizer)
#   - eval log JSONL
#   - sar/mpstat CPU utilization sample (10-min window)
#   - trials/hr computed from eval log
set -euo pipefail

NUM_INSTANCES="${1:?Usage: $0 <num_instances> <sim_budget> [hull]}"
SIM_BUDGET="${2:?Usage: $0 <num_instances> <sim_budget> [hull]}"
HULL="${3:-hammerhead}"

OPTIMIZER_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
STATE_DIR="$OPTIMIZER_DIR/experiments/cloud-benchmark-2026-04-18"
IP=$(cat "$STATE_DIR/ip.txt")
INSTANCE_ID=$(cat "$STATE_DIR/instance.txt")
SSH_KEY="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY"

# Get the instance type for naming results
INSTANCE_TYPE=$(aws ec2 describe-instances --region us-east-1 --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].InstanceType' --output text)
RESULTS_DIR="$STATE_DIR/results-${INSTANCE_TYPE}"
mkdir -p "$RESULTS_DIR"

echo "=== Benchmark config ==="
echo "Instance:      $INSTANCE_ID ($INSTANCE_TYPE)"
echo "IP:            $IP"
echo "Hull:          $HULL"
echo "Instances:     $NUM_INSTANCES"
echo "sim-budget:    $SIM_BUDGET"
echo "Results dir:   $RESULTS_DIR"
echo

# Install sysstat for sar (idempotent)
ssh $SSH_OPTS ubuntu@"$IP" "dpkg -l sysstat >/dev/null 2>&1 || sudo apt-get install -y sysstat >/dev/null 2>&1 || true"

# Record start time
START_EPOCH=$(date +%s)
echo "Start: $(date -Iseconds)"

# Start CPU monitor in background (10s samples for up to 90 min)
ssh $SSH_OPTS ubuntu@"$IP" "sar -u 10 540 > /tmp/sar.log 2>&1 &"

# Run the optimizer
echo "Starting optimizer..."
set +e
ssh $SSH_OPTS ubuntu@"$IP" "cd /opt/optimizer && \
    DISPLAY_START=100 \
    \$HOME/.local/bin/uv run python scripts/run_optimizer.py \
    --hull $HULL \
    --game-dir /opt/starsector \
    --num-instances $NUM_INSTANCES \
    --sim-budget $SIM_BUDGET \
    --study-db bench.db \
    2>&1" | tee "$RESULTS_DIR/run.log"
EXIT_CODE=$?
set -e

END_EPOCH=$(date +%s)
ELAPSED=$((END_EPOCH - START_EPOCH))
echo "End: $(date -Iseconds)  Elapsed: ${ELAPSED}s  ExitCode: $EXIT_CODE"

# Stop sar
ssh $SSH_OPTS ubuntu@"$IP" "pkill -f 'sar -u' 2>/dev/null || true"

echo "=== Collecting results ==="
scp $SSH_OPTS ubuntu@"$IP":/tmp/sar.log "$RESULTS_DIR/sar.log" 2>/dev/null || echo "  sar.log missing"
scp $SSH_OPTS ubuntu@"$IP":/opt/optimizer/bench.db "$RESULTS_DIR/bench.db" 2>/dev/null || echo "  bench.db missing"
scp -r $SSH_OPTS ubuntu@"$IP":/opt/optimizer/data/ "$RESULTS_DIR/data/" 2>/dev/null || echo "  data/ missing"

# Metadata
cat > "$RESULTS_DIR/metadata.json" <<EOF
{
  "instance_type": "$INSTANCE_TYPE",
  "instance_id": "$INSTANCE_ID",
  "hull": "$HULL",
  "num_instances": $NUM_INSTANCES,
  "sim_budget": $SIM_BUDGET,
  "start_epoch": $START_EPOCH,
  "end_epoch": $END_EPOCH,
  "elapsed_seconds": $ELAPSED,
  "exit_code": $EXIT_CODE
}
EOF

echo
echo "=== Benchmark done ==="
echo "Results: $RESULTS_DIR"
echo "Metadata:"
cat "$RESULTS_DIR/metadata.json"
