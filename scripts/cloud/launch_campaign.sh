#!/usr/bin/env bash
# Launch a Phase 6 cloud campaign. Triggers CampaignManager, which preflights
# the workstation (Tailscale + Redis + AWS creds + authkey syntax), spawns one
# subprocess per (study, seed) pair, and tears down all workers on any exit
# path. `teardown.sh` + `final_audit.sh` run via EXIT trap so leaked
# resources are caught even on SIGKILL of this script.
#
# Usage:
#   export TAILSCALE_AUTHKEY=tskey-auth-...     # if campaign YAML references ${TAILSCALE_AUTHKEY}
#   scripts/cloud/launch_campaign.sh <campaign.yaml>
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
YAML="${1:?Usage: $0 <campaign.yaml>}"
CAMPAIGN_NAME=$(uv run python -c "import yaml,sys; print(yaml.safe_load(open('$YAML'))['name'])")
CAMPAIGN_DIR="$HOME/starsector-campaigns/$CAMPAIGN_NAME"
mkdir -p "$CAMPAIGN_DIR"
ORCHESTRATOR_LOG="$CAMPAIGN_DIR/orchestrator.log"
EVENTS_LOG="$CAMPAIGN_DIR/events.log"

echo "[launch_campaign] Teardown command if this script is interrupted:"
echo "[launch_campaign]   scripts/cloud/teardown.sh $CAMPAIGN_NAME"
echo "[launch_campaign] Full log:    $ORCHESTRATOR_LOG  (gitignored, not archived)"
echo "[launch_campaign] Events log:  $EVENTS_LOG        (extracted on EXIT)"
echo

# Four-layer teardown belt-and-suspenders:
#   (1) study subprocess finally: provider.terminate_fleet
#   (2) CampaignManager.run() finally: provider.terminate_all_tagged (sweep)
#   (3) CampaignManager atexit.register(self.teardown)
#   (4) THIS trap EXIT: teardown.sh + final_audit.sh + events.log extract
# Events extraction runs last so it captures every line tee'd during teardown.
trap "scripts/cloud/teardown.sh '$CAMPAIGN_NAME' || true; \
      scripts/cloud/final_audit.sh '$CAMPAIGN_NAME' || true; \
      scripts/cloud/extract_events.sh '$ORCHESTRATOR_LOG' > '$EVENTS_LOG' 2>/dev/null || true" EXIT

# Tee stdout+stderr into the campaign-dir orchestrator.log so events.log
# has a source to extract from regardless of how the operator invoked us.
uv run python -m starsector_optimizer.campaign "$YAML" 2>&1 | tee "$ORCHESTRATOR_LOG"
