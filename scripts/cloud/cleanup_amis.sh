#!/usr/bin/env bash
# Deregister one or more AMIs (any region) and delete their underlying EBS
# snapshots. Use after a re-bake supersedes an old AMI so storage doesn't
# accumulate (~$0.05/GB/month per snapshot; ~12 GB per Starsector worker
# AMI).
#
# Safety guards (each AMI must pass ALL):
#   1. AMI must be owned by the caller's account.
#   2. AMI must carry tag Project=starsector — refuses anything else,
#      including untagged AMIs from outside this project.
#   3. AMI must NOT be referenced by any examples/*.yaml — refuses to
#      delete the AMI a campaign YAML still depends on. Override with
#      --force if you've already updated the YAMLs.
#   4. Default mode is dry-run; --apply required to actually delete.
#
# Usage:
#   scripts/cloud/cleanup_amis.sh ami-XXXX [ami-YYYY ...]            # dry-run
#   scripts/cloud/cleanup_amis.sh --apply ami-XXXX [ami-YYYY ...]    # delete
#   scripts/cloud/cleanup_amis.sh --apply --force ami-XXXX           # bypass YAML-ref guard
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

APPLY=0
FORCE=0
AMIS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=1; shift ;;
        --force) FORCE=1; shift ;;
        ami-*)   AMIS+=("$1"); shift ;;
        -h|--help)
            sed -n '2,28p' "$0"
            exit 0
            ;;
        *) echo "[cleanup_amis] ERROR: unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ ${#AMIS[@]} -eq 0 ]]; then
    echo "[cleanup_amis] ERROR: at least one ami-XXXX required" >&2
    sed -n '20,24p' "$0" >&2
    exit 2
fi

LIVE_AMIS="$(grep -rhE 'ami-[0-9a-f]+' examples/ 2>/dev/null \
    | grep -oE 'ami-[0-9a-f]+' | sort -u)"
REGIONS=(us-east-1 us-east-2 us-west-1 us-west-2)

if [[ $APPLY -eq 0 ]]; then
    echo "[cleanup_amis] DRY-RUN — no changes will be made. Re-run with --apply to commit."
    echo
fi

EXIT_CODE=0
for ami in "${AMIS[@]}"; do
    echo "[cleanup_amis] $ami:"

    # Find the AMI's region by trying each US region.
    region=""
    img_json=""
    for r in "${REGIONS[@]}"; do
        result=$(aws ec2 describe-images --region "$r" --owners self \
            --image-ids "$ami" --output json 2>/dev/null || echo '{"Images":[]}')
        n=$(echo "$result" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('Images',[])))")
        if [[ "$n" == "1" ]]; then
            region="$r"
            img_json="$result"
            break
        fi
    done

    if [[ -z "$region" ]]; then
        echo "  ERROR: not found in any of: ${REGIONS[*]}"
        EXIT_CODE=1
        continue
    fi

    # Guards: ownership + project tag.
    project_tag=$(echo "$img_json" | python3 -c "
import json, sys
img = json.load(sys.stdin)['Images'][0]
tags = {t['Key']: t['Value'] for t in img.get('Tags', [])}
print(tags.get('Project', ''))
")
    if [[ "$project_tag" != "starsector" ]]; then
        echo "  ERROR: tag Project='$project_tag' (expected 'starsector') — refusing"
        EXIT_CODE=1
        continue
    fi

    # Guard: not referenced by any campaign YAML.
    if echo "$LIVE_AMIS" | grep -qx "$ami"; then
        if [[ $FORCE -eq 0 ]]; then
            echo "  ERROR: $ami is referenced in examples/*.yaml — pass --force to override"
            EXIT_CODE=1
            continue
        fi
        echo "  WARN: $ami is referenced in examples/*.yaml — proceeding (--force given)"
    fi

    # Resolve associated snapshots.
    snapshots=$(echo "$img_json" | python3 -c "
import json, sys
img = json.load(sys.stdin)['Images'][0]
print(' '.join(b['Ebs']['SnapshotId']
               for b in img.get('BlockDeviceMappings', [])
               if 'Ebs' in b and b['Ebs'].get('SnapshotId')))
")

    echo "  region=$region snapshots=${snapshots:-<none>}"

    if [[ $APPLY -eq 0 ]]; then
        echo "  would deregister AMI + delete snapshots ($snapshots)"
        continue
    fi

    aws ec2 deregister-image --region "$region" --image-id "$ami"
    echo "  deregistered $ami"

    for snap in $snapshots; do
        # AWS sometimes refuses to delete the snapshot until deregister
        # finishes propagating; retry briefly.
        for _attempt in 1 2 3 4 5; do
            if aws ec2 delete-snapshot --region "$region" --snapshot-id "$snap" 2>/dev/null; then
                echo "  deleted snapshot $snap"
                break
            fi
            sleep 2
        done
    done
done

if [[ $APPLY -eq 0 ]]; then
    echo
    echo "[cleanup_amis] DRY-RUN complete. Re-run with --apply to commit."
fi
exit $EXIT_CODE
