#!/usr/bin/env bash
# Post-Wave-1 automation. Runs after launch_wave1_resume.sh prints
# "all 4 cells complete":
#   1. Final wave1_status.py snapshot.
#   2. analyze_wave1.py — re-run with full data.
#   3. Honest-evaluator dry-run (validates inputs without provisioning).
#   4. Print the honest-evaluator full-run command for the operator to confirm.
#
# This script does NOT launch Wave 2 directly — that's a separate
# `scripts/cloud/launch_wave2.sh` invocation gated on the report verdict.
#
# Pre-flight: Wave 1 must be fully done. Cells C0a/C0b/C1/C2/C3 each need
# `data/study_dbs/wave1-{cell}/hammerhead__early__tpe__seed{0,1,2}.db`.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CELLS=(c0a c0b c1 c2 c3)

echo "[post-wave1] === step 1: status snapshot ==="
uv run python scripts/wave1_status.py
echo

echo "[post-wave1] === step 2: gate analyzer ==="
uv run python scripts/analyze_wave1.py
echo

# Verify all 5 cells produced data
missing=0
for cell in "${CELLS[@]}"; do
    for seed in 0 1 2; do
        db="data/study_dbs/wave1-${cell}/hammerhead__early__tpe__seed${seed}.db"
        if [[ ! -f "$db" ]]; then
            echo "[post-wave1] MISSING: $db"
            missing=$((missing + 1))
        fi
    done
done
if (( missing > 0 )); then
    echo "[post-wave1] ERROR: ${missing} missing study DBs — Wave 1 incomplete."
    exit 1
fi
echo "[post-wave1] all 15 study DBs present"
echo

# Sanity-check the honest-evaluator wiring with --dry-run
echo "[post-wave1] === step 3: honest-evaluator dry-run ==="
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/cloud/_env.sh"
uv run python -m starsector_optimizer.honest_evaluator \
    --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
    --hull hammerhead \
    --top-k 10 \
    --campaign-config examples/wave1-c2.yaml \
    --dry-run
echo

cat <<'EOF'
[post-wave1] === step 4: ready to launch honest-evaluator full run ===

Full-run command (estimated cost ~$5-10, ~30-45 min wall-clock):

  source .env
  uv run python -m starsector_optimizer.honest_evaluator \
      --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
      --hull hammerhead \
      --top-k 10 \
      --campaign-config examples/wave1-c2.yaml \
      2>&1 | tee data/honest-eval-wave1-$(date -u +%Y%m%dT%H%M%SZ).log

After it completes, fill in the <<TBD>> slots in
docs/reports/2026-05-10-wave1-validation.md and decide:

  PROCEED → scripts/cloud/launch_wave2.sh
  ROLLBACK → re-evaluate per validation plan §7

EOF
