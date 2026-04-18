#!/usr/bin/env bash
# Validation probe: launch 2 spot VMs per target region from a campaign's
# AMI IDs, assert they boot + Xvfb comes up + worker_agent imports, then
# tear them down. ~$0.15 per run; run 24h before any major campaign.
#
# Usage:
#   scripts/cloud/probe.sh <campaign.yaml>
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
YAML="${1:?Usage: $0 <campaign.yaml>}"
TRAP_PROBE_NAME="probe-$(date -u +%Y%m%d%H%M%S)"

echo "[probe] Teardown command if this script is interrupted:"
echo "[probe]   scripts/cloud/teardown.sh $TRAP_PROBE_NAME"
echo

trap "scripts/cloud/teardown.sh $TRAP_PROBE_NAME || true" EXIT

# Swap the campaign name in-memory to a probe marker; every resource created
# below carries Project=starsector-$TRAP_PROBE_NAME, so teardown.sh cleans up.
uv run python -c "
import os, sys, yaml
with open('$YAML') as f: c = yaml.safe_load(f)
c['name'] = '$TRAP_PROBE_NAME'
c['max_concurrent_workers'] = 2 * len(c['regions'])
c['min_workers_to_start'] = 1
c['budget_usd'] = 0.50
with open('/tmp/probe.yaml', 'w') as f: yaml.safe_dump(c, f)
print('probe config ready: /tmp/probe.yaml')
"

uv run python -m starsector_optimizer.campaign --dry-run /tmp/probe.yaml
echo "[probe] Dry-run passed; live probe launch is a user action (see docs)."
echo "[probe] When ready, remove --dry-run and re-run."
