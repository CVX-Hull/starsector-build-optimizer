#!/usr/bin/env bash
# Wave 3 launcher — production prep run (8 hulls × early × 1 seed × 600 trials).
# Per docs/reports/2026-05-10-validation-plan.md §3 Wave 3.
#
# Pre-flight (gated by Wave 2 report verdict):
#   1. Wave 2 must have finished cleanly (data/study_dbs/wave2-{mid-warmstart,
#      wolf-early}/ present with expected COMPLETE counts).
#   2. tailscale userspace daemon up (scripts/cloud/devenv-up.sh).
#   3. .env present (auto-sourced via _env.sh).
#   4. AMIs in examples/phase7-prep.yaml exist (audit_amis.sh).
#   5. (Optional but recommended) Java LOADOUT_MISMATCH fix DEPLOYED via
#      `scripts/cloud/deploy_java_fix_for_wave2.sh`. Without it Wave 3
#      runs with the AMI-baked jar (cross-matchup variant cache bug);
#      fitness is protected by the cloud_worker_pool band-aid but
#      AWS spend +7-19 % from retry overhead.
#
# Cost guidance: Wave 1 measured 27.3 m/trial (production-default
# config on hammerhead). For Wave 3:
#   - 8 hulls × 600 × 27.3 = 131k matchups → ~$150 AT THE FULL HULL SET.
#   - This BREACHES the $85 ceiling per phase7-prep.yaml budget_usd=70.
#   - Mitigation: drop to 4 hulls × 600 OR 8 hulls × 300 OR rely on
#     budget_usd cap to terminate cleanly mid-run.
#
# This launcher does NOT auto-edit phase7-prep.yaml; the operator
# decides the hull-set based on Wave 2 wolf m/trial measurement.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

# Source Java-fix mod-jar override if present
if [[ -f data/.mod_jar_env ]]; then
    echo "[wave3] sourcing mod-jar override env from data/.mod_jar_env"
    set -a
    # shellcheck disable=SC1091
    source data/.mod_jar_env
    set +a
fi

# Verify Wave 2 finished
WAVE2_DBS=(
    "data/study_dbs/wave2-mid-warmstart/hammerhead__mid__tpe__seed0.db"
    "data/study_dbs/wave2-wolf-early/wolf__early__tpe__seed0.db"
)
missing=0
for db in "${WAVE2_DBS[@]}"; do
    if [[ ! -f "$db" ]]; then
        echo "[wave3] MISSING Wave 2 DB: $db"
        missing=$((missing + 1))
    fi
done
if (( missing > 0 )); then
    echo "[wave3] ERROR: Wave 2 not complete; ${missing} DBs missing."
    echo "[wave3] If you intend to skip Wave 2, override with:"
    echo "[wave3]   STARSECTOR_SKIP_WAVE2_GATE=1 $0"
    if [[ -z "${STARSECTOR_SKIP_WAVE2_GATE:-}" ]]; then
        exit 1
    fi
    echo "[wave3] STARSECTOR_SKIP_WAVE2_GATE=1 set; proceeding anyway"
fi

# Verify env-var preconditions for the override path
if [[ -z "${STARSECTOR_MOD_JAR_OVERRIDE_URL:-}" ]]; then
    cat <<'EOF'
[wave3] WARNING: STARSECTOR_MOD_JAR_OVERRIDE_URL not set.
[wave3]   Wave 3 will use the AMI-baked jar (V2 loadout but WITH the
[wave3]   cross-matchup variant cache bug — band-aid will retry but
[wave3]   adds 7-19 % wall-clock overhead).
[wave3]
[wave3]   To deploy the Java fix:
[wave3]     scripts/cloud/deploy_java_fix_for_wave2.sh
[wave3]     set -a && source data/.mod_jar_env && set +a && $0
[wave3]
[wave3]   Continuing in 5s — Ctrl-C to abort.
EOF
    sleep 5
fi

echo
echo "[wave3] === launching Wave 3 production prep run ==="
echo "[wave3]   YAML: examples/phase7-prep.yaml"
echo "[wave3]   Budget: \$70 (per YAML budget_usd)"
echo "[wave3]   Estimated wall-clock: 4-7 hr (depends on m/trial under V2)"
echo
scripts/cloud/launch_campaign.sh examples/phase7-prep.yaml

echo
echo "[wave3] complete. Run analyzer + author Wave 3 report."
