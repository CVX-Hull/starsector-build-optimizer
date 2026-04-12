#!/bin/bash
# Start optimizer on a cloud machine.
# Usage: ./run.sh <hull_id> [worker_index] [extra_args...]
# Example: ./run.sh hammerhead 0 --sim-budget 200 --active-opponents 10
set -euo pipefail

HULL=${1:?Usage: ./run.sh <hull_id> [worker_index] [extra_args...]}
WORKER=${2:-0}
shift 2 || shift $#

SSH_KEY_FILE="$HOME/.ssh/starsector-opt"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i $SSH_KEY_FILE"

IP=$(hcloud server describe "sim-worker-${WORKER}" -o format='{{.PublicNet.IPv4.IP}}')
echo "Starting optimization on sim-worker-${WORKER} ($IP): hull=$HULL args=$*"

# Copy study.db if it exists locally
STUDY_DB="optuna_study.db"
if [ -f "$STUDY_DB" ]; then
    echo "Uploading existing study database..."
    scp $SSH_OPTS "$STUDY_DB" root@"$IP":/opt/optimizer/study.db
fi

# Start optimizer (backgrounded on remote)
ssh $SSH_OPTS root@"$IP" "cd /opt/optimizer && nohup /root/.local/bin/uv run python scripts/run_optimizer.py \
    --hull $HULL \
    --game-dir /opt/starsector \
    --num-instances 8 \
    --study-db study.db \
    $* \
    > run.log 2>&1 &"

echo "Optimizer started in background. Monitor with:"
echo "  ssh $SSH_OPTS root@$IP 'tail -f /opt/optimizer/run.log'"
