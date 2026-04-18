#!/usr/bin/env bash
# Validation probe wrapper: drives scripts/cloud/probe.py with a trap-EXIT
# safety net that always runs final_audit.sh — catches every teardown path,
# including SIGKILL of the Python process.
#
# Usage:
#   scripts/cloud/probe.sh <campaign.yaml>
#   scripts/cloud/probe.sh --dry-run <campaign.yaml>
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [[ "${1:-}" == "--dry-run" ]]; then
  shift
  exec uv run python scripts/cloud/probe.py --dry-run "$@"
fi

YAML="${1:?Usage: $0 <campaign.yaml>}"

# Extract the campaign name once so the trap can reach it without re-parsing.
NAME=$(uv run python -c "
import sys, yaml
with open('$YAML') as f: print(yaml.safe_load(f)['name'])
")

echo "[probe] campaign: $NAME"
echo "[probe] teardown safety net if this script is interrupted:"
echo "[probe]   scripts/cloud/teardown.sh $NAME && scripts/cloud/final_audit.sh $NAME"
echo

# The trap is the belt to probe.py's suspenders: even if Python is SIGKILLed
# or the host reboots, the final_audit.sh run here will surface any leak.
trap "scripts/cloud/teardown.sh '$NAME' || true; scripts/cloud/final_audit.sh '$NAME' || true" EXIT

uv run python scripts/cloud/probe.py "$YAML"
