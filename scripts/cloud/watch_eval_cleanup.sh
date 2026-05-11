#!/usr/bin/env bash
# Watch evaluator PIDs and enforce a final AWS cleanup after they exit.
#
# Intended for unattended paid runs: the evaluator and wrapper already clean up
# on normal exit; this watchdog is an independent backstop that audits after the
# evaluator process is gone and runs teardown if tagged resources remain.

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <eval_tag> <pid> [<pid> ...]" >&2
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"

eval_tag="$1"
shift
teardown_arg="${eval_tag#starsector-}"
log_path="data/honest_eval/cleanup-watchdog-${eval_tag}.log"

mkdir -p "$(dirname "$log_path")"

{
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] watchdog armed for Project=$eval_tag pids=$*"
    while true; do
        alive=0
        for pid in "$@"; do
            if kill -0 "$pid" 2>/dev/null; then
                alive=1
            fi
        done
        if [[ "$alive" == "0" ]]; then
            break
        fi
        sleep "${WATCHDOG_POLL_SECONDS:-300}"
    done

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] evaluator pids stopped; giving wrapper ${WATCHDOG_GRACE_SECONDS:-120}s for built-in audit"
    sleep "${WATCHDOG_GRACE_SECONDS:-120}"

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] running watchdog final audit"
    if scripts/cloud/final_audit.sh "$teardown_arg"; then
        echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] watchdog final audit clean"
        exit 0
    fi

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] resources remain; running watchdog teardown"
    scripts/cloud/teardown.sh "$teardown_arg"

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] running watchdog final audit after teardown"
    scripts/cloud/final_audit.sh "$teardown_arg"
} >> "$log_path" 2>&1
