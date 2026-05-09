#!/usr/bin/env bash
# Read-only inventory of every AMI + EBS snapshot tagged Project=starsector
# across all four US regions. Prints, for each AMI, which examples/*.yaml
# campaign YAMLs reference it (the "live" set) — anything unreferenced is a
# candidate for `cleanup_amis.sh`.
#
# AMI/snapshot lifecycle is intentionally separate from per-campaign
# instance/SG teardown:
#   * Instances + SGs tagged Project=starsector-<campaign>  (per-campaign,
#     handled by teardown.sh + final_audit.sh)
#   * AMIs + snapshots tagged Project=starsector            (cross-campaign,
#     persistent, handled by audit_amis.sh + cleanup_amis.sh)
# Mixing them in one tool conflates two different time-horizons of cleanup.
#
# Usage:
#   scripts/cloud/audit_amis.sh
#
# Exit code is always 0 — this is a report, not a gate. (Storage from
# orphan AMIs is real but bounded; ~$0.05/GB/month per snapshot. The script
# flags candidates; the operator decides.)
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

REGIONS=(us-east-1 us-east-2 us-west-1 us-west-2)
TAG="Project"
TAG_VALUE="starsector"

# Collect every ami-XXXX referenced anywhere under examples/*.yaml.
# Comments and stray strings are tolerable — false positives just keep
# stale AMIs alive longer; false negatives could delete a referenced one,
# which is the worst failure mode, so we err on the inclusive side.
LIVE_AMIS="$(grep -rhE 'ami-[0-9a-f]+' examples/ 2>/dev/null \
    | grep -oE 'ami-[0-9a-f]+' | sort -u)"

echo "=== AMI / snapshot inventory for Project=$TAG_VALUE ==="
echo

for region in "${REGIONS[@]}"; do
    images_json=$(aws ec2 describe-images --owners self \
        --region "$region" \
        --filters "Name=tag:$TAG,Values=$TAG_VALUE" \
        --query 'Images[].{Id:ImageId,Name:Name,Created:CreationDate,Snap:BlockDeviceMappings[?Ebs].Ebs.SnapshotId | [0]}' \
        --output json 2>/dev/null)

    count=$(echo "$images_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
    if [[ "$count" == "0" ]]; then
        echo "  $region: no AMIs"
        echo
        continue
    fi

    echo "  $region: $count AMI(s)"
    echo "$images_json" | python3 -c "
import json, sys
live = set('''$LIVE_AMIS'''.split())
for img in json.load(sys.stdin):
    ref = '[REFERENCED]' if img['Id'] in live else '[unreferenced]'
    print(f\"    {ref}  {img['Id']}  snap={img.get('Snap','-')}  created={img['Created']}  name={img['Name']}\")
"
    echo
done

echo "Live AMIs (referenced in examples/*.yaml): $(echo "$LIVE_AMIS" | wc -w | tr -d ' ')"
echo
echo "To deregister and delete an unreferenced AMI + its snapshot:"
echo "  scripts/cloud/cleanup_amis.sh ami-XXXXXXX [ami-YYYYYYY ...]"
