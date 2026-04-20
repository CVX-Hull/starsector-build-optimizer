#!/usr/bin/env bash
# Extract load-bearing events from a campaign orchestrator log. The full
# orchestrator.log is ~70 MB/run of mostly werkzeug POST lines + per-rung
# progress chatter — information-redundant with the per-study JSONL eval
# logs and the ledger. This distills to ~10 KB of signals that actually
# matter for post-hoc forensics: Run summaries, budget thresholds, fleet
# lifecycle, janitor activity, any errors.
#
# Usage: scripts/cloud/extract_events.sh <orchestrator.log> > events.log
set -euo pipefail

SRC="${1:?Usage: $0 <orchestrator.log>}"
[[ -f "$SRC" ]] || { echo "ERROR: $SRC not found" >&2; exit 1; }

# Pattern covers: study Run summaries, budget warn thresholds,
# BudgetExceeded / PreflightFailure fatals, per-fleet terminate calls +
# instance counts, final teardown confirmation, Python/shell errors, and
# janitor requeue / drop events.
grep -E \
    "Run summary|budget threshold|BudgetExceeded|PreflightFailure|terminate_fleet|terminated [0-9]+ instances|teardown complete|ERROR|CRITICAL|Traceback|max_requeues|requeued stuck|dropping" \
    "$SRC"
