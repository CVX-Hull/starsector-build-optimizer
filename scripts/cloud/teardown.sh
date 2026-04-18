#!/usr/bin/env bash
# Terminate every EC2 instance tagged Project=starsector-<campaign-name>
# across all 4 US regions. Idempotent — safe to run repeatedly.
#
# Usage:
#   scripts/cloud/teardown.sh <campaign-name>
set -euo pipefail

CAMPAIGN="${1:?Usage: $0 <campaign-name>}"
TAG="starsector-$CAMPAIGN"

for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  ids=$(aws ec2 describe-instances \
    --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=instance-state-name,Values=pending,running" \
    --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null || true)
  if [[ -z "$ids" ]]; then
    echo "  $region: no instances tagged $TAG"
    continue
  fi
  echo "  $region: terminating $(echo "$ids" | wc -w) instances: $ids"
  aws ec2 terminate-instances --region "$region" --instance-ids $ids >/dev/null
done

echo
echo "Teardown complete. Run scripts/cloud/final_audit.sh $CAMPAIGN to verify."
