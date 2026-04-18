#!/usr/bin/env bash
# End-of-session audit — confirm zero AWS resources accrue cost for this
# campaign across all 4 US regions (even the ones the campaign YAML didn't
# target). Mandatory final step per .claude/skills/cloud-worker-ops.md.
#
# Usage:
#   scripts/cloud/final_audit.sh <campaign-name>
#
# Exit 0: clean. Exit 1: leaked resource (listed in stdout).
set -uo pipefail

CAMPAIGN="${1:?Usage: $0 <campaign-name>}"
TAG="starsector-$CAMPAIGN"
LEAKED=0

echo "=== AWS audit for Project=$TAG ==="

for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  instances=$(aws ec2 describe-instances --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name]' \
    --output text 2>/dev/null)
  if [[ -n "$instances" ]]; then
    echo "LEAK in $region: instances:"
    echo "$instances" | sed 's/^/    /'
    LEAKED=1
  else
    echo "  $region: no instances"
  fi

  sgs=$(aws ec2 describe-security-groups --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
    --query 'SecurityGroups[].GroupId' --output text 2>/dev/null)
  if [[ -n "$sgs" ]]; then
    echo "LEAK in $region: SGs: $sgs"
    LEAKED=1
  fi

  volumes=$(aws ec2 describe-volumes --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=status,Values=available" \
    --query 'Volumes[].VolumeId' --output text 2>/dev/null)
  if [[ -n "$volumes" ]]; then
    echo "LEAK in $region: volumes: $volumes"
    LEAKED=1
  fi
done

if [[ $LEAKED -ne 0 ]]; then
  echo
  echo "FINAL AUDIT: LEAKS DETECTED. Run:"
  echo "  scripts/cloud/teardown.sh $CAMPAIGN"
  exit 1
fi
echo
echo "FINAL AUDIT: clean — zero resources accruing cost for $CAMPAIGN."
