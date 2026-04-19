#!/usr/bin/env bash
# Build the Starsector worker AMI in us-east-1, then copy to us-east-2.
# Prints both AMI IDs at the end for campaign YAML's ami_ids_by_region:.
#
# Usage:
#   scripts/cloud/bake_image.sh [--dry-run]
#
# Teardown on failure is automatic — Packer cleans up builder instances.
# See docs/specs/22-cloud-deployment.md for the baked-contents list.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DRY_RUN=${1:-}
PACKER_TEMPLATE="scripts/cloud/packer/aws.pkr.hcl"
SOURCE_REGION="us-east-1"
TARGET_REGIONS=("us-east-2")

if [[ "$DRY_RUN" == "--dry-run" ]]; then
  echo "[bake_image] dry-run: validating Packer template"
  packer validate "$PACKER_TEMPLATE"
  echo "[bake_image] template OK"
  exit 0
fi

echo "[bake_image] Teardown command if this script is interrupted:"
echo "[bake_image]   scripts/cloud/teardown.sh <campaign-name-here>"
echo

echo "[bake_image] Building AMI in $SOURCE_REGION..."
packer build -machine-readable "$PACKER_TEMPLATE" | tee packer.log
AMI_ID=$(grep 'artifact,0,id' packer.log | cut -d, -f6 | cut -d: -f2)

if [[ -z "$AMI_ID" ]]; then
  echo "[bake_image] ERROR: could not parse AMI id from packer output" >&2
  exit 1
fi
echo "[bake_image] $SOURCE_REGION AMI: $AMI_ID"

# Pull the authoritative tags from the source AMI — the Packer template
# sets GameVersion + ModCommitSha from the committed manifest, and preflight
# (_check_manifest_and_ami_tags) dual-checks both tags. aws ec2 copy-image
# does NOT propagate tags, so we mirror them onto each copy explicitly. A
# missing ModCommitSha tag on a copied AMI would wedge every cross-region
# campaign launch.
src_tags_json=$(aws ec2 describe-images \
  --owners self --region "$SOURCE_REGION" --image-ids "$AMI_ID" \
  --query 'Images[0].Tags[?Key==`Project` || Key==`Role` || Key==`GameVersion` || Key==`ModCommitSha`]' \
  --output json)

for target in "${TARGET_REGIONS[@]}"; do
  echo "[bake_image] Copying to $target..."
  copied_ami=$(aws ec2 copy-image \
    --source-region "$SOURCE_REGION" \
    --region "$target" \
    --source-image-id "$AMI_ID" \
    --name "starsector-worker-$(date -u +%Y%m%d%H%M%S)" \
    --query ImageId --output text)
  echo "[bake_image] $target AMI: $copied_ami"
  echo "[bake_image] Propagating tags to $copied_ami..."
  aws ec2 create-tags --region "$target" \
    --resources "$copied_ami" \
    --tags "$src_tags_json"
done

rm -f packer.log

cat <<EOF

Next step: paste the ami_ids_by_region: block into your campaign YAML.
EOF
