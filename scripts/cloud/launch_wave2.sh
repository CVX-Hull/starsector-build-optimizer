#!/usr/bin/env bash
# Wave 2 launcher — runs the cross-regime warm-start study + the wolf
# frigate cross-cut sequentially. Per
# docs/reports/2026-05-10-validation-plan.md §3 Wave 2.
#
# Pre-flight (gated by Wave 1 report verdict):
#   1. Wave 1 c2 must have finished cleanly (data/study_dbs/wave1-c2/
#      hammerhead__early__tpe__seed0.db exists with ≥ 200 COMPLETE trials)
#   2. tailscale userspace daemon up (scripts/cloud/devenv-up.sh)
#   3. .env present (auto-sourced by launch_campaign.sh via _env.sh)
#   4. AMIs in each YAML still exist (audit_amis.sh)
#
# Cost estimate: ~$5 (warm-start) + ~$4 (wolf) ≈ $9 worst-case.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

# Pick up the Java LOADOUT_MISMATCH fix override if deploy_java_fix_for_wave2.sh
# was run. The env file exports STARSECTOR_MOD_JAR_OVERRIDE_URL +
# STARSECTOR_MOD_JAR_OVERRIDE_SHA256; cloud_runner.py threads them through
# to worker user-data. Without these env vars Wave 2 uses the AMI-baked jar
# (still has the Wave 1 cross-trial loadout bleed).
if [[ -f data/.mod_jar_env ]]; then
    echo "[wave2] sourcing mod-jar override env from data/.mod_jar_env"
    set -a
    # shellcheck disable=SC1091
    source data/.mod_jar_env
    set +a
fi

SOURCE_DB="data/study_dbs/wave1-c2/hammerhead__early__tpe__seed0.db"
DEST_DB_DIR="data/study_dbs/wave2-mid-warmstart"
DEST_DB="${DEST_DB_DIR}/hammerhead__mid__tpe__seed0.db"

if [[ ! -f "${SOURCE_DB}" ]]; then
    echo "[wave2] ERROR: source DB ${SOURCE_DB} missing — Wave 1 c2 has not"
    echo "[wave2]        completed. Re-run after Wave 1 c2 finishes."
    exit 1
fi

# Verify the source has enough completed trials for warm-start to be meaningful
SOURCE_COMPLETE=$(python3 -c "
import sqlite3
conn = sqlite3.connect('${SOURCE_DB}')
print(conn.execute(\"SELECT COUNT(*) FROM trials WHERE state='COMPLETE'\").fetchone()[0])
")
if (( SOURCE_COMPLETE < 50 )); then
    echo "[wave2] ERROR: source DB has only ${SOURCE_COMPLETE} COMPLETE trials"
    echo "[wave2]        warm_start_n=50 default needs >= 50; abort."
    exit 1
fi
echo "[wave2] source DB has ${SOURCE_COMPLETE} COMPLETE trials — sufficient for warm-start"

# Pre-seed the wave2 SQLite by copying the wave1 c2 seed-0 DB. The mid study
# (hammerhead__mid) is created via load_if_exists=True alongside the
# pre-existing hammerhead__early study; warm-start finds it via
# Optuna study_name namespacing.
mkdir -p "${DEST_DB_DIR}"
cp "${SOURCE_DB}" "${DEST_DB}"
echo "[wave2] copied source DB → ${DEST_DB}"

echo
echo "[wave2] === step 1: hammerhead × mid × warm-start from early ==="
scripts/cloud/launch_campaign.sh examples/wave2-mid-warmstart.yaml

echo
echo "[wave2] === step 2: wolf × early × frigate cross-cut ==="
scripts/cloud/launch_campaign.sh examples/wave2-wolf-early.yaml

echo
echo "[wave2] both steps complete. Run analyzer + author Wave 2 report."
echo "[wave2]   uv run python scripts/analyze_wave2.py"
