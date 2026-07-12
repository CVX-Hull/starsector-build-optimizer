#!/usr/bin/env bash
# Honest evaluator CLI wrapper. Re-scores top builds from one or more
# completed campaigns against the closed opponent population with the
# transform-free oracle (mean fitness over balanced design). Spec 30,
# methodology in docs/reference/honest-evaluation-methodology.md, SOP in
# .claude/skills/honest-evaluation.md.
#
# Per cloud-worker-ops "rules of money", this IS a paid cloud run:
# every invocation prints the teardown command on first line, sources
# .env via _env.sh, and runs final-audit on EXIT once the concrete
# honest-eval tag has appeared in the orchestrator log.
#
# Usage:
#   scripts/cloud/evaluate_campaign.sh \
#       --hull <hull_id> \
#       --campaign-name <name> [<name>...] \
#       [--top-k 3] [--replicates 30] [--max-retries 3]
#
# Cost estimate: top_k × n_seeds × n_cells × pool_size × replicates matchups,
# at ~75s/matchup wall-clock (time_mult=5, in-engine 300s cap). Default
# fleet sizing inherits `max(workers_per_study)` from the source campaign
# — for Wave 1 that's 8 workers × 2 matchup_slots = 16 concurrent
# matchups. Wall-clock scales inversely with concurrency; total cost
# stays roughly constant since matchup-work is fixed.
#
# For Wave 1 hammerhead full sweep (5 cells × 3 seeds × 3 builds × ~28
# destroyer opponents × 30 reps ≈ 38k matchups):
#   16 workers (32 slots): ~24h, ~$70 raw  ($100 with 1.5× headroom)
#   32 workers (64 slots): ~12h, ~$70 raw
#   64 workers (128 slots): ~6h,  ~$70 raw
# Pass --workers <N> to scale concurrency up. The honest-eval ledger
# at data/honest_eval/<eval_tag>/results.jsonl makes any interrupt
# survivable — re-run with --resume-from <eval_tag> to continue.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

# Pass-through arg passing — let argparse in honest_evaluator.main do the
# parsing. We only need the campaign names for the teardown banner.
echo "[evaluate_campaign] Honest evaluator — see SOP at .claude/skills/honest-evaluation.md"
echo "[evaluate_campaign] Fleet namespace: starsector-honest-eval-<first-campaign-name>-<utc-stamp>"
echo "[evaluate_campaign] On interrupt: tear down with:"
echo "[evaluate_campaign]   scripts/cloud/teardown.sh honest-eval-<first-campaign-name>-<stamp>"
echo "[evaluate_campaign]   (the exact tag is logged on the first dispatch line)"
echo "[evaluate_campaign] To resume after interrupt: re-run with --resume-from <eval_tag>"
echo

# Tee stdout+stderr to data/honest_eval/orchestrator.log for diagnostics.
LOG_DIR="data/honest_eval"
mkdir -p "$LOG_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
ORCHESTRATOR_LOG="$LOG_DIR/orchestrator-$STAMP.log"
echo "[evaluate_campaign] Full log: $ORCHESTRATOR_LOG"
echo

child_pid=""
shutdown_requested=0

# shellcheck disable=SC2329  # invoked indirectly: `trap audit_on_exit EXIT`
audit_on_exit() {
    if [[ ! -f "$ORCHESTRATOR_LOG" ]]; then
        return 0
    fi
    eval_tag=$(awk '/honest_eval cloud-pool: tag=/ {
        sub(/^.*tag=/, "", $0)
        sub(/ workers=.*$/, "", $0)
        tag=$0
    } END { print tag }' "$ORCHESTRATOR_LOG")
    if [[ -z "${eval_tag:-}" ]]; then
        return 0
    fi
    campaign_arg="${eval_tag#starsector-}"
    echo
    echo "[evaluate_campaign] Final audit for Project=$eval_tag"
    if ! scripts/cloud/final_audit.sh "$campaign_arg"; then
        echo "[evaluate_campaign] Final audit found resources or was inconclusive; running teardown:"
        echo "[evaluate_campaign]   scripts/cloud/teardown.sh $campaign_arg"
        if scripts/cloud/teardown.sh "$campaign_arg"; then
            echo "[evaluate_campaign] Re-running final audit after teardown"
            scripts/cloud/final_audit.sh "$campaign_arg" || true
        fi
    fi
}

# shellcheck disable=SC2329  # invoked indirectly: `trap 'forward_signal …'`
forward_signal() {
    local sig="$1"
    shutdown_requested=1
    if [[ -z "${child_pid:-}" ]]; then
        return 0
    fi
    echo "[evaluate_campaign] Forwarding SIG$sig to evaluator pid=$child_pid"
    # With job control enabled for this non-interactive shell, the
    # backgrounded evaluator is the process-group leader. Signal the group
    # first so both `uv` and its Python child see the interrupt; fall back to
    # the direct child if process-group signalling is unavailable.
    kill "-$sig" -- "-$child_pid" 2>/dev/null || kill "-$sig" "$child_pid" 2>/dev/null || true
}

trap audit_on_exit EXIT
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT
trap 'forward_signal HUP' HUP

# Avoid `uv ... | tee`: killing the shell side of a pipeline can orphan the
# evaluator under launchd/init. Process substitution keeps logging while the
# wrapper owns a direct child it can signal and wait on.
exec > >(tee "$ORCHESTRATOR_LOG") 2>&1
set -m

uv run python -m starsector_optimizer.honest_evaluator "$@" &
child_pid=$!

set +e
while true; do
    wait "$child_pid"
    status=$?
    if [[ "$shutdown_requested" == "1" ]] && kill -0 "$child_pid" 2>/dev/null; then
        continue
    fi
    break
done
set -e
child_pid=""
exit "$status"
