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
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

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

WORKER_SOURCE_INPUTS=(
  src
  pyproject.toml
  uv.lock
  scripts/cloud/bake_image.sh
  scripts/cloud/packer
)
AMI_DIRTY_INPUTS=(
  "${WORKER_SOURCE_INPUTS[@]}"
  game/starsector/manifest.json
)
WORKER_SOURCE_SHA=$(
  for path in "${WORKER_SOURCE_INPUTS[@]}"; do
    printf '%s\0%s\0' "$path" "$(git rev-parse "HEAD:$path")"
  done | shasum -a 256 | awk '{print $1}'
)
DIRTY_STATUS=$(git status --porcelain -- "${AMI_DIRTY_INPUTS[@]}")
if [[ "${STARSECTOR_ALLOW_DIRTY_AMI_BAKE:-}" != "1" ]]; then
  if [[ -n "$DIRTY_STATUS" ]]; then
    echo "[bake_image] ERROR: worktree has uncommitted changes." >&2
    echo "[bake_image] Commit or stash before baking so WorkerSourceSha=$WORKER_SOURCE_SHA matches the AMI contents." >&2
    echo "[bake_image] Set STARSECTOR_ALLOW_DIRTY_AMI_BAKE=1 only for throwaway debugging AMIs." >&2
    exit 1
  fi
elif [[ -n "$DIRTY_STATUS" ]]; then
  WORKER_SOURCE_SHA="${WORKER_SOURCE_SHA}-dirty"
  echo "[bake_image] WARNING: baking dirty worker source; WorkerSourceSha=$WORKER_SOURCE_SHA" >&2
fi

echo "[bake_image] Teardown command if this script is interrupted:"
echo "[bake_image]   scripts/cloud/teardown.sh <campaign-name-here>"
echo

echo "[bake_image] Building AMI in $SOURCE_REGION..."
packer build -machine-readable \
  -var "worker_source_sha=$WORKER_SOURCE_SHA" \
  "$PACKER_TEMPLATE" | tee packer.log
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
# shellcheck disable=SC2016  # backticks are JMESPath literals, not shell
src_tags_json=$(aws ec2 describe-images \
  --owners self --region "$SOURCE_REGION" --image-ids "$AMI_ID" \
  --query 'Images[0].Tags[?Key==`Project` || Key==`Role` || Key==`GameVersion` || Key==`ManifestSha256` || Key==`ModCommitSha` || Key==`WorkerSourceSha`]' \
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
  echo "[bake_image] Waiting for $copied_ami in $target to become available..."
  aws ec2 wait image-available --region "$target" --image-ids "$copied_ami"
done

rm -f packer.log

cat <<EOF

Next step: paste the ami_ids_by_region: block into your campaign YAML.
EOF
