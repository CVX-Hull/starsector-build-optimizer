# Cloud Deployment Specification

Automates provisioning, deployment, and teardown of Hetzner Cloud machines for batch combat simulation. Defined in `scripts/cloud/`.

## Overview

Local-first development with cloud burst for expensive simulation. Optuna studies persist via SQLite file transfer.

```
Local machine (dev + heuristic + small sim)
  ��� scp study.db + game + optimizer to cloud
  → Cloud machines run parallel sim
  ��� scp study.db + results back
  → Local analysis + visualization
```

## Machine Sizing

| Machine | vCPUs | RAM | Game Instances | Cost/hr | Use |
|---------|-------|-----|----------------|---------|-----|
| CCX33 | 8 | 32GB | 8 | ~$0.11 | **Default.** 1 core + ~2GB per instance. |
| CCX43 | 16 | 64GB | 16 | ~$0.22 | If 8 instances bottleneck on CPU. |

Starsector is single-threaded per instance. Xvfb is near-zero CPU. 8 instances on 8 vCPUs is sufficient. The game directory is 361MB (not 2GB as earlier estimated).

## Cloud-Init Script

```yaml
#cloud-config
package_update: true
packages:
  - xvfb
  - xdotool
  - rsync
  - curl
  - libgl1
  - libasound2t64
  - libxi6
  - libxrender1
  - libxtst6
  - libxrandr2
  - openjdk-17-jre-headless

runcmd:
  - curl -LsSf https://astral.sh/uv/install.sh | sh
  - touch /tmp/cloud-init-done
```

## Deployment Script

```bash
#!/bin/bash
# scripts/cloud/deploy.sh
# Usage: ./deploy.sh [num_machines] [server_type]

NUM_MACHINES=${1:-1}
SERVER_TYPE=${2:-ccx33}
SSH_KEY_NAME="starsector-opt"

GAME_DIR="$HOME/bin/starsector"
OPTIMIZER_DIR="$HOME/ClaudeCode"
PREFS_FILE="$HOME/.java/.userPrefs/com/fs/starfarer/prefs.xml"
CLOUD_INIT="scripts/cloud/cloud-init.yaml"

# Create machines in parallel
for i in $(seq 0 $((NUM_MACHINES - 1))); do
  hcloud server create \
    --name "sim-worker-${i}" \
    --type "$SERVER_TYPE" \
    --image ubuntu-24.04 \
    --ssh-key "$SSH_KEY_NAME" \
    --location fsn1 \
    --user-data-from-file "$CLOUD_INIT" &
done
wait

# Deploy to all machines in parallel
for i in $(seq 0 $((NUM_MACHINES - 1))); do
  IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}')
  (
    echo "[$i] Waiting for cloud-init on $IP..."
    ssh -o StrictHostKeyChecking=no root@$IP "cloud-init status --wait" 2>/dev/null

    echo "[$i] Syncing game directory (361MB)..."
    rsync -az "$GAME_DIR/" root@$IP:/opt/starsector/

    echo "[$i] Syncing optimizer code..."
    rsync -az --exclude='.git' --exclude='game/' "$OPTIMIZER_DIR/" root@$IP:/opt/optimizer/

    echo "[$i] Copying game activation..."
    ssh root@$IP "mkdir -p /root/.java/.userPrefs/com/fs/starfarer/"
    scp "$PREFS_FILE" root@$IP:/root/.java/.userPrefs/com/fs/starfarer/prefs.xml

    echo "[$i] Installing Python dependencies..."
    ssh root@$IP "cd /opt/optimizer && /root/.local/bin/uv sync"

    echo "[$i] Ready."
  ) &
done
wait
echo "All $NUM_MACHINES machines deployed."
```

## Work Distribution

Each machine gets a list of hull IDs to optimize. The local orchestrator partitions hulls across machines.

```bash
#!/bin/bash
# scripts/cloud/run_optimization.sh
# Usage: ./run_optimization.sh <hull_list_file> <study_db>

HULL_LIST=$1
STUDY_DB=$2

# Copy study to machine
IP=$(hcloud server describe sim-worker-0 -o format='{{.PublicNet.IPv4.IP}}')
scp "$STUDY_DB" root@$IP:/opt/optimizer/study.db

# Start optimization (backgrounded)
ssh root@$IP "cd /opt/optimizer && nohup uv run python scripts/run_optimizer.py \
  --hulls $(cat $HULL_LIST | tr '\n' ',') \
  --study-db study.db \
  --game-dir /opt/starsector \
  --num-instances 8 \
  > /opt/optimizer/run.log 2>&1 &"
```

## Result Collection

```bash
#!/bin/bash
# scripts/cloud/collect.sh

for i in $(seq 0 $((NUM_MACHINES - 1))); do
  IP=$(hcloud server describe "sim-worker-${i}" -o format='{{.PublicNet.IPv4.IP}}')
  scp root@$IP:/opt/optimizer/study.db "./results/study_worker_${i}.db"
  scp root@$IP:/opt/optimizer/data/evaluation_log.jsonl "./results/eval_log_worker_${i}.jsonl"
done
```

## Teardown

```bash
#!/bin/bash
# scripts/cloud/teardown.sh

for i in $(seq 0 $((NUM_MACHINES - 1))); do
  hcloud server delete "sim-worker-${i}" --poll
done
```

## Optuna Study Persistence

**TPESampler is stateless** — it reconstructs its model from stored trials on every call. Transferring the SQLite file preserves all knowledge.

**Local → Cloud workflow:**
1. Local: create study in `study.db`, add heuristic warm-start trials, run small sim validation
2. `scp study.db` to cloud machine
3. Cloud: `optuna.load_study(storage="sqlite:///study.db")`, continue with `n_jobs=8`
4. `scp study.db` back to local for analysis

**Multi-machine merge:** Each machine runs an independent study on different hulls. No study merging needed — each hull gets its own study. Results are in the shared JSONL evaluation log.

**Zombie trial cleanup:** If a machine crashes, trials may be stuck in RUNNING state. Clean up before resuming:
```python
for trial in study.trials:
    if trial.state == optuna.trial.TrialState.RUNNING:
        study.tell(trial.number, state=optuna.trial.TrialState.FAIL)
```

## Setup Time Breakdown

| Step | Time |
|------|------|
| `hcloud server create` | ~10s |
| Server boot + cloud-init | ~60-90s |
| rsync game dir (361MB over 1Gbps) | ~5-10s |
| rsync optimizer code | ~3s |
| Copy prefs.xml | ~1s |
| `uv sync` | ~15s |
| **Total** | **~2 minutes** |

## Cost Estimates

| Scenario | Machines | Instances | Sims | Wall-clock | Cost |
|----------|----------|-----------|------|------------|------|
| 3 dev hulls (local validation) | 0 | 2 local | ~600 | ~5h | $0 |
| 10 priority hulls | 1 × CCX33 | 8 | ~10K | ~8h | ~$0.90 |
| 50 hulls (all combat-relevant) | 3 × CCX33 | 24 | ~50K | ~14h | ~$4.60 |
| 50 hulls + QD validation | 3 × CCX33 | 24 | ~65K | ~18h | ~$6.00 |

All well within $30 budget. Dominant cost is development time, not compute.
