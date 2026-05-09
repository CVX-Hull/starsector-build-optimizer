#!/usr/bin/env bash
# Launch a Phase 6 cloud campaign. Triggers CampaignManager, which preflights
# the workstation (Tailscale + Redis + AWS creds + authkey syntax), spawns one
# subprocess per (study, seed) pair, and tears down all workers on any exit
# path. `teardown.sh` + `final_audit.sh` run via EXIT trap so leaked
# resources are caught even on SIGKILL of this script.
#
# Usage:
#   export TAILSCALE_AUTHKEY=tskey-auth-...     # if campaign YAML references ${TAILSCALE_AUTHKEY}
#   export STARSECTOR_DEBUG_SSH_PUBKEY="$(cat ~/.ssh/starsector-debug.pub)"  # optional
#   scripts/cloud/launch_campaign.sh <campaign.yaml>
#
# Worker debug access (when launcher / mid-game hangs need diagnosis):
#   If STARSECTOR_DEBUG_SSH_PUBKEY is exported, cloud_userdata appends the
#   pubkey to /home/ubuntu/.ssh/authorized_keys at boot. SSH with the
#   matching private key:
#       ssh -i ~/.ssh/starsector-debug ubuntu@<worker-tailnet-ip>
#   (Tailscale SSH was tried smoke #8 2026-05-09 — `tailscale up --ssh`
#   hijacks port 22 and gates via the tailnet ACL which silent-denies on
#   default personal tailnets. We use plain sshd + key-injection instead.)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Auto-source .env so AWS_PROFILE + TAILSCALE_AUTHKEY are set without operators
# having to remember `set -a; source .env; set +a`. Per
# `.claude/skills/cloud-worker-ops.md` § AWS profile, the principled auth flow
# is the dedicated `starsector` IAM user surfaced via AWS_PROFILE — without
# it, boto3 falls back to whatever default-profile session the CLI happens
# to have (e.g. an Amazon-Q `login_session` against root, which boto3's SDK
# can't resolve). Skipped if AWS_PROFILE is already set so an explicit
# operator override is honored.
if [[ -z "${AWS_PROFILE:-}" && -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
YAML="${1:?Usage: $0 <campaign.yaml>}"
CAMPAIGN_NAME=$(uv run python -c "import yaml,sys; print(yaml.safe_load(open('$YAML'))['name'])")
# Project-relative artifacts (data/ is gitignored). Mirrors data/logs/<study>/
# layout so a forked engineer doesn't need to know about a magic ~ path.
CAMPAIGN_DIR="data/campaigns/$CAMPAIGN_NAME"
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
