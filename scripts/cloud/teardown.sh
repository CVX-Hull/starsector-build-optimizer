#!/usr/bin/env bash
# Terminate every EC2 instance + delete every security group + delete every
# available volume tagged Project=starsector-<campaign-name> across all 4 US
# regions. Idempotent — safe to run repeatedly. Mirrors what
# `AWSProvider.terminate_all_tagged` does in-process; this script is the
# fallback when the orchestrator can't run that path (SIGKILL, expired
# credentials mid-run, crash).
#
# Order matters: instances first (releases ENIs), then SGs (which require
# zero ENI attachments), then volumes (only Available state).
#
# Usage:
#   scripts/cloud/teardown.sh <campaign-name>
set -euo pipefail

CAMPAIGN="${1:?Usage: $0 <campaign-name>}"
TAG="starsector-$CAMPAIGN"

# AWS releases ENIs from a terminating instance asynchronously. Polling for
# every instance to leave 'shutting-down' takes too long for a teardown
# script — so we just retry SG deletion a few times with a sleep, matching
# the AWSProvider._SG_DELETE_DEADLINE_SECONDS pattern (60s budget).
SG_DELETE_RETRIES=12
SG_DELETE_RETRY_SLEEP_SECONDS=5

for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  ids=$(aws ec2 describe-instances \
    --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=instance-state-name,Values=pending,running" \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)
  if [[ -z "$ids" ]]; then
    echo "  $region: no instances tagged $TAG"
  else
    echo "  $region: terminating $(echo "$ids" | wc -w) instance(s): $ids"
    aws ec2 terminate-instances --region "$region" --instance-ids $ids >/dev/null
  fi
done

echo

# Pass 2: delete every SG tagged with this campaign. Wait for ENI detach by
# retrying on DependencyViolation (= still attached to a terminating ENI).
for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  sgs=$(aws ec2 describe-security-groups \
    --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
    --query 'SecurityGroups[].GroupId' --output text 2>/dev/null || true)
  if [[ -z "$sgs" ]]; then
    echo "  $region: no SGs tagged $TAG"
    continue
  fi
  for sg in $sgs; do
    deleted=0
    for attempt in $(seq 1 $SG_DELETE_RETRIES); do
      if aws ec2 delete-security-group --region "$region" --group-id "$sg" 2>/dev/null; then
        echo "  $region: deleted SG $sg"
        deleted=1
        break
      fi
      sleep $SG_DELETE_RETRY_SLEEP_SECONDS
    done
    if [[ $deleted -eq 0 ]]; then
      echo "  $region: WARN failed to delete SG $sg after $SG_DELETE_RETRIES attempts (still in use?). Retry teardown.sh later or delete manually."
    fi
  done
done

echo

# Pass 3: delete any leaked Available volumes (in-use volumes are still
# attached to a terminating instance and AWS will release them itself once
# the instance is fully terminated).
for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  vols=$(aws ec2 describe-volumes \
    --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" "Name=status,Values=available" \
    --query 'Volumes[].VolumeId' --output text 2>/dev/null || true)
  if [[ -z "$vols" ]]; then
    echo "  $region: no available volumes tagged $TAG"
    continue
  fi
  for vol in $vols; do
    if aws ec2 delete-volume --region "$region" --volume-id "$vol" 2>/dev/null; then
      echo "  $region: deleted volume $vol"
    else
      echo "  $region: WARN failed to delete volume $vol"
    fi
  done
done

echo
echo "Teardown complete. Run scripts/cloud/final_audit.sh $CAMPAIGN to verify."
