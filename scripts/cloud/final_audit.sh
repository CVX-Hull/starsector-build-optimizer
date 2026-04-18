#!/bin/bash
# End-of-session audit — confirm zero cloud resources are still accruing cost.
# Run after every campaign, every smoke test, every abort.
#
# Exit code 0: all clean.
# Exit code 1: leaked resource detected (listed in stdout).
#
# Covers both providers in one pass so we don't leave orphans on the side we
# forgot to check. The list of things to check mirrors the "Teardown
# discipline" section of .claude/skills/cloud-worker-ops.md.
set -uo pipefail

LEAKS=""
record_leak() { LEAKS="$LEAKS\n  - $1"; }

echo "=== Hetzner ==="
if command -v hcloud >/dev/null 2>&1; then
    SERVERS=$(hcloud server list -o noheader -o columns=name 2>/dev/null \
        | grep '^sim-worker-' || true)
    if [ -n "$SERVERS" ]; then
        echo "LEAK: sim-worker-* servers still running:"
        echo "$SERVERS" | sed 's/^/  /'
        record_leak "hetzner servers: $SERVERS"
    else
        echo "  no sim-worker-* servers"
    fi
else
    echo "  hcloud not installed on this host — skipping"
fi

echo ""
echo "=== AWS instances (us-east-1, us-west-2) ==="
if command -v aws >/dev/null 2>&1; then
    for region in us-east-1 us-west-2; do
        INSTANCES=$(aws ec2 describe-instances --region "$region" \
            --filters 'Name=tag:Project,Values=starsector-*' \
                      'Name=instance-state-name,Values=pending,running,stopping,stopped' \
            --query 'Reservations[].Instances[].[InstanceId,State.Name,Tags[?Key==`Project`].Value|[0]]' \
            --output text 2>/dev/null)
        if [ -n "$INSTANCES" ]; then
            echo "LEAK in $region: instances still alive:"
            echo "$INSTANCES" | sed 's/^/  /'
            record_leak "aws instances in $region"
        else
            echo "  $region: no starsector-* instances"
        fi
    done

    echo ""
    echo "=== AWS security groups ==="
    for region in us-east-1 us-west-2; do
        SGS=$(aws ec2 describe-security-groups --region "$region" \
            --filters 'Name=tag:Project,Values=starsector-*' \
            --query 'SecurityGroups[].GroupId' --output text 2>/dev/null)
        if [ -n "$SGS" ]; then
            echo "LEAK in $region: security groups:"
            echo "$SGS" | sed 's/^/  /'
            record_leak "aws SGs in $region: $SGS"
        else
            echo "  $region: no starsector-* SGs"
        fi
    done

    echo ""
    echo "=== AWS key pairs ==="
    for region in us-east-1 us-west-2; do
        KPS=$(aws ec2 describe-key-pairs --region "$region" \
            --filters 'Name=tag:Project,Values=starsector-*' \
            --query 'KeyPairs[].KeyName' --output text 2>/dev/null)
        if [ -n "$KPS" ]; then
            echo "LEAK in $region: key pairs:"
            echo "$KPS" | sed 's/^/  /'
            record_leak "aws keypairs in $region: $KPS"
        else
            echo "  $region: no starsector-* keypairs"
        fi
    done

    echo ""
    echo "=== AWS orphaned volumes (available, not attached) ==="
    for region in us-east-1 us-west-2; do
        VOLS=$(aws ec2 describe-volumes --region "$region" \
            --filters 'Name=tag:Project,Values=starsector-*' \
                      'Name=status,Values=available' \
            --query 'Volumes[].VolumeId' --output text 2>/dev/null)
        if [ -n "$VOLS" ]; then
            echo "LEAK in $region: orphaned volumes:"
            echo "$VOLS" | sed 's/^/  /'
            record_leak "aws orphan volumes in $region: $VOLS"
        else
            echo "  $region: no starsector-* volumes"
        fi
    done
else
    echo "  aws CLI not installed — skipping"
fi

echo ""
if [ -n "$LEAKS" ]; then
    echo "FINAL AUDIT: LEAKS DETECTED$(printf "$LEAKS")"
    echo ""
    echo "Clean up before ending session:"
    echo "  ./scripts/cloud/teardown.sh                   # Hetzner"
    echo "  ./scripts/cloud/aws/teardown.sh               # AWS"
    exit 1
fi
echo "FINAL AUDIT: clean — zero resources accruing cost."
