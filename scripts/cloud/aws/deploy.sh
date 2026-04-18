#!/bin/bash
# Launch AWS spot instance for Starsector benchmark and provision it.
# Usage: ./deploy.sh [instance_type]
# Default: c7i.2xlarge (CPU-only variant)
#
# Trap-based safety: if this script dies at any point before completing,
# it invokes teardown.sh (keeping SG and keypair for retry).
set -euo pipefail

INSTANCE_TYPE="${1:-c7i.2xlarge}"
PROJECT_TAG='starsector-bench-20260418'
REGION='us-east-1'
AMI_ID='ami-009d9173b44d0482b'  # Ubuntu 24.04 us-east-1
SUBNET_ID='subnet-01745d2ce8253cc8b'
SG_ID='sg-06dcd686e374e510f'
KEY_NAME='starsector-bench-key'
SSH_KEY="$HOME/.ssh/starsector-opt"

OPTIMIZER_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
GAME_DIR="$OPTIMIZER_DIR/game/starsector"
PREFS_FILE="$HOME/.java/.userPrefs/com/fs/starfarer/prefs.xml"
CLOUD_INIT="$OPTIMIZER_DIR/scripts/cloud/cloud-init.yaml"
STATE_DIR="$OPTIMIZER_DIR/experiments/cloud-benchmark-2026-04-18"
STATE_FILE="$STATE_DIR/instance.txt"

# Validate prerequisites
[ -d "$GAME_DIR" ] || { echo "ERROR: Game dir missing: $GAME_DIR"; exit 1; }
[ -f "$SSH_KEY" ] || { echo "ERROR: SSH key missing: $SSH_KEY"; exit 1; }
[ -f "$PREFS_FILE" ] || { echo "ERROR: Prefs file missing: $PREFS_FILE"; exit 1; }
[ -f "$CLOUD_INIT" ] || { echo "ERROR: cloud-init missing: $CLOUD_INIT"; exit 1; }

mkdir -p "$STATE_DIR"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -i $SSH_KEY"

# Launch
echo "=== Launching $INSTANCE_TYPE spot instance in $REGION ==="
INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --subnet-id "$SUBNET_ID" \
    --associate-public-ip-address \
    --user-data "file://$CLOUD_INIT" \
    --instance-market-options 'MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Project,Value=${PROJECT_TAG}},{Key=Name,Value=bench-${INSTANCE_TYPE}}]" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3,DeleteOnTermination=true}' \
    --query 'Instances[0].InstanceId' --output text)

echo "Instance ID: $INSTANCE_ID"
echo "$INSTANCE_ID" > "$STATE_FILE"

# Safety trap: on any error from here on, terminate instance
cleanup_on_error() {
    echo
    echo "=== ERROR: invoking teardown to prevent stray costs ==="
    "$(dirname "$0")/teardown.sh" --keep-sg-and-key || true
    exit 1
}
trap cleanup_on_error ERR

# Tag root volume (block-device-mapping tags don't propagate)
sleep 5
VOLUME_ID=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' --output text)
if [ "$VOLUME_ID" != "None" ] && [ -n "$VOLUME_ID" ]; then
    aws ec2 create-tags --region "$REGION" --resources "$VOLUME_ID" \
        --tags "Key=Project,Value=${PROJECT_TAG}" >/dev/null
fi

echo "Waiting for instance running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "Public IP: $IP"
echo "$IP" > "$STATE_DIR/ip.txt"

echo "Waiting for SSH..."
for attempt in $(seq 1 30); do
    if ssh $SSH_OPTS ubuntu@"$IP" 'true' 2>/dev/null; then
        echo "  SSH ready after $((attempt * 10))s"
        break
    fi
    [ $attempt -eq 30 ] && { echo "SSH never came up"; exit 1; }
    sleep 10
done

echo "Waiting for cloud-init to finish (up to 10 min)..."
for attempt in $(seq 1 60); do
    if ssh $SSH_OPTS ubuntu@"$IP" "test -f /tmp/cloud-init-done" 2>/dev/null; then
        echo "  cloud-init done after $((attempt * 10))s"
        break
    fi
    [ $attempt -eq 60 ] && { echo "cloud-init never finished"; exit 1; }
    sleep 10
done

# /opt/starsector and /opt/optimizer writable by ubuntu user
ssh $SSH_OPTS ubuntu@"$IP" "sudo mkdir -p /opt/starsector /opt/optimizer && sudo chown -R ubuntu:ubuntu /opt/starsector /opt/optimizer"

# Ensure uv is available for ubuntu user
ssh $SSH_OPTS ubuntu@"$IP" "if [ ! -x \$HOME/.local/bin/uv ]; then curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; fi; \$HOME/.local/bin/uv --version"

echo "=== Syncing game directory (551M) ==="
rsync -az --info=progress2 -e "ssh $SSH_OPTS" "$GAME_DIR/" ubuntu@"$IP":/opt/starsector/

echo "=== Syncing optimizer code ==="
rsync -az --info=progress2 -e "ssh $SSH_OPTS" \
    --exclude='.git' \
    --exclude='game/' \
    --exclude='experiments/' \
    --exclude='notebooks/' \
    --exclude='data/' \
    --exclude='*.db' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    "$OPTIMIZER_DIR/" ubuntu@"$IP":/opt/optimizer/

echo "=== Copying game activation prefs ==="
ssh $SSH_OPTS ubuntu@"$IP" "mkdir -p /home/ubuntu/.java/.userPrefs/com/fs/starfarer/"
scp $SSH_OPTS "$PREFS_FILE" ubuntu@"$IP":/home/ubuntu/.java/.userPrefs/com/fs/starfarer/prefs.xml

echo "=== uv sync ==="
ssh $SSH_OPTS ubuntu@"$IP" "cd /opt/optimizer && \$HOME/.local/bin/uv sync 2>&1 | tail -10"

# Clear trap — deploy succeeded
trap - ERR

echo
echo "=== Deploy complete ==="
echo "Instance:    $INSTANCE_ID"
echo "IP:          $IP"
echo "SSH:         ssh $SSH_OPTS ubuntu@$IP"
echo "Teardown:    $(dirname "$0")/teardown.sh"
