#!/usr/bin/env bash
# Wave 1 resume — runs cells C0b through C3 only. Used when the canonical
# `launch_wave1.sh` was aborted mid-wave (e.g. C0a budget-exhausted under
# the OLD CampaignManager exit-code convention where BudgetExceeded → 3
# tripped `set -e` in the wrapper). After commit X (BudgetExceeded → 0,
# spec 22 §"Cost ledger"), the canonical wrapper advances on its own and
# this resume script is unnecessary for fresh launches.
#
# This script is a one-off recovery tool, not a permanent ablation runner.
# Delete it after this resume completes — the canonical wrapper now
# handles cell-to-cell advancement under per-cell budget caps.
#
# Pre-flight (same as launch_wave1.sh):
#   1. tailscale userspace daemon up (scripts/cloud/devenv-up.sh)
#   2. .env present (auto-sourced by launch_campaign.sh via _env.sh)
#   3. AMI in each cell's YAML still exists (audit_amis.sh)
#   4. data/study_dbs/wave1-c0a/ exists with 3 partial DBs from the
#      original C0a run — those are NOT touched by this resume.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

CELLS=(c0b c1 c2 c3)

echo "[wave1-resume] launching 4 cells × 3 seeds = 12 studies sequentially"
echo "[wave1-resume] each cell capped at \$5.0 USD (per-cell YAML budget_usd)"
echo
echo "[wave1-resume] === cell C0b (scalar CV A baseline) ==="
STARSECTOR_EB_MIN_BUILDS=251 \
STARSECTOR_SHAPE_MIN_SAMPLES=251 \
  scripts/cloud/launch_campaign.sh examples/wave1-c0b.yaml

echo "[wave1-resume] === cell C1 (EB-only, Box-Cox off) ==="
STARSECTOR_SHAPE_MIN_SAMPLES=251 \
  scripts/cloud/launch_campaign.sh examples/wave1-c1.yaml

echo "[wave1-resume] === cell C2 (production default) ==="
scripts/cloud/launch_campaign.sh examples/wave1-c2.yaml

echo "[wave1-resume] === cell C3 (production default + warm_start=50) ==="
STARSECTOR_WARM_START_N=50 \
  scripts/cloud/launch_campaign.sh examples/wave1-c3.yaml

echo
echo "[wave1-resume] all 4 cells complete. C0a results are at:"
echo "  data/campaigns/wave1-c0a/  (partial — 366/750 trials)"
for c in "${CELLS[@]}"; do
  echo "  data/campaigns/wave1-$c/"
done
