#!/usr/bin/env bash
# Launch a Phase 6 cloud campaign. Triggers CampaignManager, which provisions
# the EC2 Fleet, spawns one subprocess per (hull, regime, seed) study, and
# tears down all workers on any exit path. `final_audit.sh` runs via EXIT
# trap so leaked resources are caught even on SIGKILL of this script.
#
# Usage:
#   scripts/cloud/launch_campaign.sh <campaign.yaml>
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
YAML="${1:?Usage: $0 <campaign.yaml>}"
CAMPAIGN_NAME=$(uv run python -c "import yaml,sys; print(yaml.safe_load(open('$YAML'))['name'])")

echo "[launch_campaign] Teardown command if this script is interrupted:"
echo "[launch_campaign]   scripts/cloud/teardown.sh $CAMPAIGN_NAME"
echo

trap "scripts/cloud/final_audit.sh '$CAMPAIGN_NAME'" EXIT

uv run python -m starsector_optimizer.campaign "$YAML"
