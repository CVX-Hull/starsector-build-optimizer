#!/usr/bin/env bash
# Post-Wave-2 automation. Runs after launch_wave2.sh completes.
#   1. analyze_wave2.py
#   2. Author Wave 2 report (template — fill TBDs manually)
#   3. Print Wave 3 launch command (gated on Wave 2 verdict).
#
# Pre-flight: Wave 2 must be fully done. Need:
#   data/study_dbs/wave2-mid-warmstart/hammerhead__mid__tpe__seed0.db
#   data/study_dbs/wave2-wolf-early/wolf__early__tpe__seed0.db

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

REQUIRED_DBS=(
    "data/study_dbs/wave2-mid-warmstart/hammerhead__mid__tpe__seed0.db"
    "data/study_dbs/wave2-wolf-early/wolf__early__tpe__seed0.db"
)
missing=0
for db in "${REQUIRED_DBS[@]}"; do
    if [[ ! -f "$db" ]]; then
        echo "[post-wave2] MISSING: $db"
        missing=$((missing + 1))
    fi
done
if (( missing > 0 )); then
    echo "[post-wave2] ERROR: ${missing} missing study DBs — Wave 2 incomplete."
    exit 1
fi

echo "[post-wave2] === step 1: gate analyzer ==="
uv run python scripts/analyze_wave2.py
echo

cat <<'EOF'
[post-wave2] === step 2: report verdict ===

Author docs/reports/2026-05-10-wave2-validation.md filling in:
  - cross-regime warm-start overlap %
  - regime tier conformance (early ≤1, mid expanded)
  - wolf finalized count + τ̂² + win-rate
  - F4a/F4b decision-tree branch (if wolf gates fail)

If PROCEED:

  source .env
  scripts/cloud/launch_campaign.sh examples/phase7-prep.yaml

This is Wave 3: 8 hulls × early × 1 seed × 600 trials. Estimated cost
$33-90 (validation plan §4 sensitivity); validation plan §3 Wave 3
gate applies per-hull. First check the budget cap with:

  grep budget_usd examples/phase7-prep.yaml

If Wave 1 measured matchups/trial > 10, drop hulls per validation plan
§4 Sensitivity to stay under $85 cumulative.

EOF
