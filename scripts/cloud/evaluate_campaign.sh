#!/usr/bin/env bash
# Honest evaluator CLI wrapper. Re-scores top builds from one or more
# completed campaigns against the closed opponent population with the
# transform-free oracle (mean fitness over balanced design). Spec 30,
# methodology in docs/reference/honest-evaluation-methodology.md, SOP in
# .claude/skills/honest-evaluation.md.
#
# Per cloud-worker-ops "three rules of money", this IS a paid cloud run:
# every invocation prints the teardown command on first line, sources
# .env via _env.sh, runs final-audit on EXIT.
#
# Usage:
#   scripts/cloud/evaluate_campaign.sh \
#       --hull <hull_id> \
#       --campaign-name <name> [<name>...] \
#       [--top-k 3] [--replicates 30] [--max-retries 3]
#
# Cost estimate: top_k × n_seeds × n_cells × pool_size × replicates matchups,
# at ~120s/matchup observed in Wave 1 C0a. Default fleet sizing inherits
# `max(workers_per_study)` from the source campaign — for Wave 1 that's
# 8 workers × 2 matchup_slots = 16 concurrent matchups (NOT the 48 the
# full Wave 1 launch used across 3 parallel studies). Wall-clock scales
# inversely with concurrency.
#
# For Wave 1 hammerhead full sweep at the default 16-slot fleet:
#   3 × 3 × 5 × ~54 × 30 = ~73k matchups
#   73k × 120s ÷ 16 slots ≈ 2.5h, ~$5-10
# Pass --workers <N> to scale concurrency up — e.g. --workers 24 → 48 slots
# brings wall-clock back to ~30 min at 1.5× the per-hour rate (~same total).

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

# Pass-through arg passing — let argparse in honest_evaluator.main do the
# parsing. We only need the campaign names for the teardown banner.
echo "[evaluate_campaign] Honest evaluator — see SOP at .claude/skills/honest-evaluation.md"
echo "[evaluate_campaign] Fleet namespace: honest-eval-<first-campaign-name>-<utc-stamp>"
echo "[evaluate_campaign] On interrupt: honest-eval namespaces by the first"
echo "[evaluate_campaign] --campaign-name argument plus a UTC timestamp; tear down with:"
echo "[evaluate_campaign]   scripts/cloud/teardown.sh honest-eval-<first-campaign-name>-<stamp>"
echo "[evaluate_campaign]   (the exact tag is logged on the first dispatch line)"
echo

# Tee stdout+stderr to data/honest_eval/orchestrator.log for diagnostics.
LOG_DIR="data/honest_eval"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ORCHESTRATOR_LOG="$LOG_DIR/orchestrator-$STAMP.log"
echo "[evaluate_campaign] Full log: $ORCHESTRATOR_LOG"
echo

uv run python -m starsector_optimizer.honest_evaluator "$@" 2>&1 | tee "$ORCHESTRATOR_LOG"
