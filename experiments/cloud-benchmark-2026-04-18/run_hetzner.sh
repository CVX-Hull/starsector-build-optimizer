#!/bin/bash
# Run benchmark on Hetzner CCX33 for direct comparison to AWS c7i.2xlarge.
# Assumes `scripts/cloud/deploy.sh 1 ccx33 ash` already ran.
set -euo pipefail

NUM_INSTANCES="${1:-2}"
SIM_BUDGET="${2:-6}"
HULL="${3:-hammerhead}"

IP=$(hcloud server describe sim-worker-0 -o format='{{.PublicNet.IPv4.IP}}')
INSTANCE_TYPE="ccx33"
SSH_KEY="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY"

RESULTS_DIR="/home/sdai/ClaudeCode/experiments/cloud-benchmark-2026-04-18/results-${INSTANCE_TYPE}"
mkdir -p "$RESULTS_DIR"

echo "=== Hetzner CCX33 benchmark config ==="
echo "Server:        sim-worker-0 ($INSTANCE_TYPE, Ashburn VA)"
echo "IP:            $IP"
echo "Hull:          $HULL"
echo "Instances:     $NUM_INSTANCES"
echo "sim-budget:    $SIM_BUDGET"
echo "Results dir:   $RESULTS_DIR"
echo

# Install sysstat for sar
ssh $SSH_OPTS root@"$IP" 'dpkg -l sysstat >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y sysstat >/dev/null 2>&1 || true'

# Clean any prior run state
ssh $SSH_OPTS root@"$IP" 'pkill -9 -f run_optimizer 2>/dev/null; pkill -9 -f StarfarerLauncher 2>/dev/null; pkill -9 Xvfb 2>/dev/null; rm -rf /tmp/starsector-instances/ 2>/dev/null; rm -f /opt/optimizer/data/evaluation_log.jsonl 2>/dev/null; rm -f /opt/optimizer/bench.db 2>/dev/null; true'

START_EPOCH=$(date +%s)
echo "Start: $(date -Iseconds)"

# Start sar monitor
ssh $SSH_OPTS root@"$IP" "sar -u 10 540 > /tmp/sar.log 2>&1 &"

# Run optimizer
echo "Starting optimizer..."
set +e
ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && /root/.local/bin/uv run python scripts/run_optimizer.py \
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
ssh $SSH_OPTS root@"$IP" "pkill -f 'sar -u' 2>/dev/null || true"

echo "=== Collecting results ==="
scp $SSH_OPTS root@"$IP":/tmp/sar.log "$RESULTS_DIR/sar.log" 2>/dev/null || echo "  sar.log missing"
scp $SSH_OPTS root@"$IP":/opt/optimizer/bench.db "$RESULTS_DIR/bench.db" 2>/dev/null || echo "  bench.db missing"
scp -r $SSH_OPTS root@"$IP":/opt/optimizer/data/ "$RESULTS_DIR/data/" 2>/dev/null || echo "  data/ missing"

cat > "$RESULTS_DIR/metadata.json" <<EOF
{
  "instance_type": "$INSTANCE_TYPE",
  "provider": "hetzner",
  "location": "ash (Ashburn VA)",
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
echo "=== Hetzner benchmark done ==="
cat "$RESULTS_DIR/metadata.json"
