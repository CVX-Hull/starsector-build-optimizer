#!/usr/bin/env bash
# Wave 1 ablation matrix orchestrator. Per
# `docs/reports/2026-05-10-validation-plan.md` §3.1, runs 5 cells × 3 seeds
# = 15 studies sequentially in 5 cell-batches. Each batch's 3 studies run
# concurrently on a 24-VM fleet (3 studies × 8 workers); fleet is torn
# down between cells so the SG-replication-lag retry path doesn't fire.
#
# Per-cell ablation knobs are passed via STARSECTOR_* env vars consumed
# by `scripts/run_optimizer.py::_resolve_ablation_overrides`. Each invocation
# below scopes the env vars to a single `launch_campaign.sh` line; the next
# cell starts with a clean env.
#
# Per-cell budget (`budget_usd: 5.0` in each YAML) is a hard cap, not an
# estimate. Cells that hit the cap stop at partial trial completion;
# `CampaignManager.run()` returns 0 on `BudgetExceeded` (designed
# termination, see spec 22 §"Cost ledger") so `set -e` advances to the
# next cell. Total wave spend ≤ 5 × $5.00 = $25.00. Equal-budget-per-cell
# is the comparison contract: each ablation cell gets the same $-budget,
# revealing per-cell efficiency (pruner / curriculum / EB shrinkage all
# show up as "more useful trials per $5"). Wall-clock varies with the
# fleet's TIMEOUT-rate; observed C0a ran 3h21m to spend $5.
#
# Pre-flight (operator):
#   1. tailscale userspace daemon up (scripts/cloud/devenv-up.sh)
#   2. .env contains TAILSCALE_AUTHKEY + AWS_PROFILE
#   3. AMIs ami-098d4cd753a6576f2 (us-east-1) and ami-0ea7ac393de421fe5
#      (us-east-2) still exist and pass tag preflight
#
# Recovery: each cell tears down its own fleet on exit (launch_campaign.sh
# trap). If this wrapper itself is interrupted between cells, run
# `scripts/cloud/teardown.sh wave1-c<X>` for whichever cell was in flight.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

CELLS=(c0a c0b c1 c2 c3)

echo "[wave1] launching 5 cells × 3 seeds = 15 studies sequentially"
echo "[wave1] each cell's fleet: 24 VMs (3 studies × 8 workers)"
echo "[wave1] teardown safety net per-cell: scripts/cloud/teardown.sh wave1-<cell>"
echo

# C0a — plain TWFE A0 baseline (EB off, scalar CV off, Box-Cox off)
echo "[wave1] === cell C0a (plain TWFE A0 baseline) ==="
STARSECTOR_EB_MIN_BUILDS=251 \
STARSECTOR_SHAPE_MIN_SAMPLES=251 \
STARSECTOR_TWFE_TRIM_WORST=0 \
  scripts/cloud/launch_campaign.sh examples/wave1-c0a.yaml

# C0b — scalar CV "A" baseline (EB off, scalar CV on, Box-Cox off)
echo "[wave1] === cell C0b (scalar CV A baseline) ==="
STARSECTOR_EB_MIN_BUILDS=251 \
STARSECTOR_SHAPE_MIN_SAMPLES=251 \
  scripts/cloud/launch_campaign.sh examples/wave1-c0b.yaml

# C1 — EB-only (Box-Cox off)
echo "[wave1] === cell C1 (EB-only, Box-Cox off) ==="
STARSECTOR_SHAPE_MIN_SAMPLES=251 \
  scripts/cloud/launch_campaign.sh examples/wave1-c1.yaml

# C2 — production default (EB + Box-Cox + triple-goal + warm_start=0)
echo "[wave1] === cell C2 (production default) ==="
scripts/cloud/launch_campaign.sh examples/wave1-c2.yaml

# C3 — production default + heuristic warm-start
echo "[wave1] === cell C3 (production default + warm_start=50) ==="
STARSECTOR_WARM_START_N=50 \
  scripts/cloud/launch_campaign.sh examples/wave1-c3.yaml

echo
echo "[wave1] all 5 cells complete. Run analysis notebooks against:"
for c in "${CELLS[@]}"; do
  echo "  data/campaigns/wave1-$c/"
done
