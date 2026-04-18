#!/usr/bin/env bash
# Tail the cost ledger + show per-study trial counts + best fitness for a
# running campaign. Safe to run concurrently with a campaign; read-only.
#
# Usage:
#   scripts/cloud/status.sh <campaign-name>
set -euo pipefail

CAMPAIGN="${1:?Usage: $0 <campaign-name>}"
LEDGER="$HOME/starsector-campaigns/$CAMPAIGN/ledger.jsonl"

if [[ ! -f "$LEDGER" ]]; then
  echo "[status] no ledger at $LEDGER — has the campaign started?" >&2
  exit 2
fi

echo "=== Cost ledger tail ==="
tail -5 "$LEDGER" | uv run python -c "
import json, sys
for line in sys.stdin:
    row = json.loads(line)
    print(f'  {row[\"timestamp\"]}  worker={row[\"worker_id\"]}  '
          f'delta=\${row[\"delta_usd\"]:.4f}  '
          f'cumulative=\${row[\"cumulative_usd\"]:.2f}')
"

echo
echo "=== Cumulative cost ==="
uv run python -c "
import json
total = 0.0
with open('$LEDGER') as f:
    for line in f:
        total = json.loads(line)['cumulative_usd']
print(f'\${total:.2f}')
"

echo
echo "=== Active AWS instances (Project=starsector-$CAMPAIGN) ==="
for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  count=$(aws ec2 describe-instances \
    --region "$region" \
    --filters "Name=tag:Project,Values=starsector-$CAMPAIGN" \
              "Name=instance-state-name,Values=pending,running" \
    --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo "?")
  echo "  $region: $count"
done
