#!/usr/bin/env bash
# Deploy the Java LOADOUT_MISMATCH root-cause fix (task #89) via the
# `serve_mod_jar.sh` tailnet override path, without rebaking the AMI.
#
# Pre-flight:
#   1. Honest-eval has FINISHED (no in-flight workers booting).
#   2. `combat-harness/mod/jars/combat-harness.jar` is the FIXED version
#      (rebuilt locally via `./gradlew jar`).
#
# Procedure:
#   1. Stop the running serve_mod_jar.sh (it's serving stale env vars).
#   2. Restart serve_mod_jar.sh as a backgrounded process; capture its
#      printed STARSECTOR_MOD_JAR_OVERRIDE_URL + SHA256 to a file.
#   3. The wave 2 launcher will source the env vars from that file.
#
# Usage:
#   scripts/cloud/deploy_java_fix_for_wave2.sh
#
# After this completes:
#   set -a && source data/.mod_jar_env && set +a
#   scripts/cloud/launch_wave2.sh

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

ENV_OUT="data/.mod_jar_env"

# Step 1: kill any existing serve_mod_jar.sh + http.server
echo "[deploy-java-fix] killing existing serve_mod_jar.sh..."
pkill -f "serve_mod_jar.sh" || true
pkill -f "python.*http.server.*8081" || true
sleep 1

# Step 2: rebuild jar (idempotent — the operator may have run gradle already)
echo "[deploy-java-fix] rebuilding combat-harness.jar..."
JAVA_HOME="$STARSECTOR_JDK_HOME" \
    ./combat-harness/gradlew --project-dir combat-harness jar > /dev/null

# Step 3: capture the env vars by running serve_mod_jar.sh in --env mode first
echo "[deploy-java-fix] capturing new env vars to ${ENV_OUT}..."
mkdir -p data
scripts/cloud/serve_mod_jar.sh --env > "${ENV_OUT}"
echo "[deploy-java-fix] new env:"
sed 's/=.*$/=<...>/' "${ENV_OUT}"

# Step 4: launch serve_mod_jar.sh in the background (foreground HTTP server)
echo "[deploy-java-fix] launching serve_mod_jar.sh in background..."
nohup scripts/cloud/serve_mod_jar.sh > data/.serve_mod_jar.log 2>&1 &
SERVE_PID=$!
disown $SERVE_PID
echo "[deploy-java-fix] serve_mod_jar.sh pid=${SERVE_PID}"

# Wait briefly for HTTP server to come up
sleep 3
if ! pgrep -f "http.server.*8081" > /dev/null; then
    echo "[deploy-java-fix] ERROR: http.server failed to start. Check data/.serve_mod_jar.log"
    exit 1
fi

cat <<EOF

[deploy-java-fix] ✓ DONE.

Next steps:
  set -a && source ${ENV_OUT} && set +a
  scripts/cloud/launch_wave2.sh

After Wave 2: validate the Java fix worked by checking the LOADOUT_MISMATCH
rate in data/campaigns/wave2-{mid-warmstart,wolf-early}/orchestrator.log.
Expected: << 1 % (vs C2's 3.67 % / C3's 19 % under the buggy variant).

EOF
