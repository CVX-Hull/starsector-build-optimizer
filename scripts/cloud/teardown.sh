#!/bin/bash
# Delete all sim-worker-* machines on Hetzner.
#
# Usage:
#   ./teardown.sh                # delete every sim-worker-* that exists
#   ./teardown.sh 4              # legacy: delete sim-worker-0..3 by count
#
# Discovering live servers via `hcloud server list` is safer than trusting a
# caller-supplied count — if a previous deploy created 5 workers but the
# caller passes 3, two leaked servers keep accruing cost until the next
# audit. The no-arg form is the preferred invocation.
set -euo pipefail

if [ $# -ge 1 ]; then
    NUM_MACHINES=$1
    echo "Legacy mode — deleting sim-worker-0..$((NUM_MACHINES - 1)) by index..."
    TARGETS=()
    for i in $(seq 0 $((NUM_MACHINES - 1))); do
        TARGETS+=("sim-worker-${i}")
    done
else
    echo "Discovering sim-worker-* servers on Hetzner..."
    mapfile -t TARGETS < <(hcloud server list -o noheader -o columns=name \
        | grep '^sim-worker-' || true)
    if [ ${#TARGETS[@]} -eq 0 ]; then
        echo "No sim-worker-* servers found. Nothing to delete."
        exit 0
    fi
fi

echo "Targets: ${TARGETS[*]}"
for name in "${TARGETS[@]}"; do
    echo "  Deleting ${name}..."
    hcloud server delete "$name" 2>/dev/null || echo "  ${name} not found (already gone?)"
done

echo ""
echo "Post-teardown audit:"
REMAINING=$(hcloud server list -o noheader -o columns=name 2>/dev/null \
    | grep '^sim-worker-' || true)
if [ -n "$REMAINING" ]; then
    echo "  WARNING: these sim-worker-* servers still exist:"
    echo "$REMAINING" | sed 's/^/    /'
    exit 1
fi
echo "  Clean — zero sim-worker-* servers remaining."
