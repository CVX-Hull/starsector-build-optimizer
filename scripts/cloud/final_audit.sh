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

# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

CAMPAIGN="${1:?Usage: $0 <campaign-name|starsector-project-tag>}"
if [[ "$CAMPAIGN" == starsector-* ]]; then
  TAG="$CAMPAIGN"
else
  TAG="starsector-$CAMPAIGN"
fi
LEAKED=0
AUDIT_FAILED=0

echo "=== AWS audit for Project=$TAG ==="

for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  if ! instances=$(aws ec2 describe-instances --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name]' \
    --output text); then
    echo "AUDIT ERROR in $region: failed to describe instances" >&2
    AUDIT_FAILED=1
    continue
  fi
  if [[ -n "$instances" ]]; then
    echo "LEAK in $region: instances:"
    echo "$instances" | sed 's/^/    /'
    LEAKED=1
  else
    echo "  $region: no instances"
  fi

  if ! sgs=$(aws ec2 describe-security-groups --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
    --query 'SecurityGroups[].GroupId' --output text); then
    echo "AUDIT ERROR in $region: failed to describe security groups" >&2
    AUDIT_FAILED=1
    continue
  fi
  if [[ -n "$sgs" ]]; then
    echo "LEAK in $region: SGs: $sgs"
    LEAKED=1
  fi

  if ! lts=$(aws ec2 describe-launch-templates --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
    --query 'LaunchTemplates[].LaunchTemplateName' --output text); then
    echo "AUDIT ERROR in $region: failed to describe launch templates" >&2
    AUDIT_FAILED=1
    continue
  fi
  if [[ -n "$lts" ]]; then
    echo "LEAK in $region: launch templates: $lts"
    LEAKED=1
  fi

  if ! volumes=$(aws ec2 describe-volumes --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=status,Values=available" \
    --query 'Volumes[].VolumeId' --output text); then
    echo "AUDIT ERROR in $region: failed to describe volumes" >&2
    AUDIT_FAILED=1
    continue
  fi
  if [[ -n "$volumes" ]]; then
    echo "LEAK in $region: volumes: $volumes"
    LEAKED=1
  fi
done

if [[ $AUDIT_FAILED -ne 0 ]]; then
  echo
  echo "FINAL AUDIT: INCONCLUSIVE. One or more AWS describe calls failed."
  echo "Do not treat this as clean; rerun after fixing AWS credentials/network:"
  echo "  scripts/cloud/final_audit.sh $CAMPAIGN"
  exit 2
fi

if [[ $LEAKED -ne 0 ]]; then
  echo
  echo "FINAL AUDIT: LEAKS DETECTED. Run:"
  echo "  scripts/cloud/teardown.sh $CAMPAIGN"
  exit 1
fi
echo
echo "FINAL AUDIT: clean — zero resources accruing cost for $TAG."
