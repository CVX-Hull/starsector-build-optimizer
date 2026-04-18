#!/bin/bash
# Terminate all resources tagged Project=starsector-bench-20260418.
# Safe to run repeatedly; idempotent.
# Usage: ./teardown.sh [--keep-sg-and-key]
set -uo pipefail

PROJECT_TAG='starsector-bench-20260418'
REGION='us-east-1'
KEEP_SG_AND_KEY=false

for arg in "$@"; do
    case "$arg" in
        --keep-sg-and-key) KEEP_SG_AND_KEY=true ;;
    esac
done

echo "=== Teardown: Project=$PROJECT_TAG ==="

# 1. Terminate instances
echo "Finding tagged instances..."
INSTANCE_IDS=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Project,Values=$PROJECT_TAG" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' --output text)

if [ -n "$INSTANCE_IDS" ]; then
    echo "Terminating: $INSTANCE_IDS"
    aws ec2 terminate-instances --region "$REGION" --instance-ids $INSTANCE_IDS >/dev/null
    echo "Waiting for termination..."
    aws ec2 wait instance-terminated --region "$REGION" --instance-ids $INSTANCE_IDS
    echo "Instances terminated."
else
    echo "No running instances with tag."
fi

# 2. Cancel any open spot requests
echo "Canceling open spot requests..."
SPOT_IDS=$(aws ec2 describe-spot-instance-requests --region "$REGION" \
    --filters "Name=tag:Project,Values=$PROJECT_TAG" "Name=state,Values=open,active" \
    --query 'SpotInstanceRequests[].SpotInstanceRequestId' --output text 2>/dev/null)
if [ -n "$SPOT_IDS" ]; then
    aws ec2 cancel-spot-instance-requests --region "$REGION" --spot-instance-request-ids $SPOT_IDS >/dev/null
    echo "Canceled: $SPOT_IDS"
fi

# 3. Delete any dangling volumes (block-device-mappings with DeleteOnTermination should handle most)
VOL_IDS=$(aws ec2 describe-volumes --region "$REGION" \
    --filters "Name=tag:Project,Values=$PROJECT_TAG" "Name=status,Values=available" \
    --query 'Volumes[].VolumeId' --output text)
if [ -n "$VOL_IDS" ]; then
    echo "Deleting orphaned volumes: $VOL_IDS"
    for v in $VOL_IDS; do
        aws ec2 delete-volume --region "$REGION" --volume-id "$v" >/dev/null || true
    done
fi

if [ "$KEEP_SG_AND_KEY" = "true" ]; then
    echo "Keeping SG and key (--keep-sg-and-key)."
else
    # 4. Delete security group
    SG_IDS=$(aws ec2 describe-security-groups --region "$REGION" \
        --filters "Name=tag:Project,Values=$PROJECT_TAG" \
        --query 'SecurityGroups[].GroupId' --output text)
    for sg in $SG_IDS; do
        echo "Deleting SG $sg"
        aws ec2 delete-security-group --region "$REGION" --group-id "$sg" 2>&1 || echo "  (failed, may still have dependencies)"
    done

    # 5. Delete keypair
    KEY_NAMES=$(aws ec2 describe-key-pairs --region "$REGION" \
        --filters "Name=tag:Project,Values=$PROJECT_TAG" \
        --query 'KeyPairs[].KeyName' --output text 2>/dev/null)
    for k in $KEY_NAMES; do
        echo "Deleting keypair $k"
        aws ec2 delete-key-pair --region "$REGION" --key-name "$k" >/dev/null
    done
fi

# 6. Final audit
echo
echo "=== Final audit ==="
echo "Instances still alive with tag:"
aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Project,Values=$PROJECT_TAG" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
echo "Volumes with tag:"
aws ec2 describe-volumes --region "$REGION" \
    --filters "Name=tag:Project,Values=$PROJECT_TAG" \
    --query 'Volumes[].[VolumeId,State]' --output text
echo "Done."
