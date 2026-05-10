#!/usr/bin/env bash
# Wave 1 honest-evaluation pass — Plan C.
#
# Re-scores the top builds from each Wave 1 cell against the closed
# destroyer-class opponent population using the transform-free oracle.
# Spec 30 + .claude/skills/honest-evaluation.md.
#
# Plan-C parameters (post-2026-05-10 audit):
#   - Full scope: all 5 cells (c0a, c0b, c1, c2, c3)
#   - top-k=3, replicates=30 (defaults; do NOT reduce eval power)
#   - 64-worker fleet (32 per region, 128 matchup slots concurrent)
#   - $100 budget guard (raw cost ~$70 with 1.5× headroom)
#   - matchup_time_limit_seconds defaults to 300s (HonestEvaluationConfig)
#   - Auto-sources .env via _env.sh; auto-resolves tailnet IP / authkey
#
# Plan-C-specific safety nets shipped 2026-05-10:
#   - Append-only ledger at data/honest_eval/<eval_tag>/results.jsonl
#     with --resume-from to recover from any interrupt
#   - LOADOUT_MISMATCH 422 discard + abort guardrail (5% over 100 samples)
#   - Stalled-progress detector (10-min idle WARN with in-flight sample)
#   - Mod-jar fleet-consistency check
#   - Resume preflight refuses if prior fleet still up
#
# AWS quota check (2026-05-10): both regions have 640 vCPU available;
# 64 workers × 8 vCPU = 512 vCPU total = 256 vCPU per region — 40% utilization.
#
# Recovery on interrupt:
#   1. Note the eval_tag printed on the first dispatch line
#      (format: starsector-honest-eval-wave1-c0a-<utc-stamp>)
#   2. Run: scripts/cloud/teardown.sh <eval_tag>
#   3. Verify teardown via aws ec2 describe-instances + the
#      Project tag filter (see audit_amis.sh pattern)
#   4. Resume: scripts/cloud/evaluate_campaign.sh \
#                --hull hammerhead \
#                --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
#                --workers 64 --resume-from <eval_tag>

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
# shellcheck source=scripts/cloud/_env.sh
source "$(dirname "$0")/_env.sh"

# Java fix override — picks up SHA from data/.mod_jar_env if
# serve_mod_jar.sh is currently serving the post-V2 jar (see
# deploy_java_fix_for_wave2.sh). Without this, workers boot with the
# AMI's stale jar and the fleet-consistency WARN will fire.
if [ -f data/.mod_jar_env ]; then
  echo "[wave1-honest-eval] sourcing data/.mod_jar_env — Java fix override active"
  # shellcheck disable=SC1091
  source data/.mod_jar_env
fi

echo "[wave1-honest-eval] Plan C: 64 workers, full scope, ~6h walltime, ~\$70 raw"
echo "[wave1-honest-eval] Cells: c0a c0b c1 c2 c3 + random-baseline (9 builds)"
echo "[wave1-honest-eval] top-k=3 reps=30 (full eval power)"
echo "[wave1-honest-eval] Ranking: TWFE+EB on per-study evaluation_log.jsonl"
echo "[wave1-honest-eval]   (default; phase5a + phase5d-without-X). Replaces"
echo "[wave1-honest-eval]   the prior raw-mean default which had 0/5 top-5"
echo "[wave1-honest-eval]   overlap with principled methods on Wave 1. See"
echo "[wave1-honest-eval]   docs/reports/2026-05-10-posthoc-ranker-research.md"
echo "[wave1-honest-eval] Bradley-Terry runs in parallel; main() WARNs when"
echo "[wave1-honest-eval]   methods disagree on top-K (heavy confounding /"
echo "[wave1-honest-eval]   near-tied top region — operator should inspect)"
echo "[wave1-honest-eval] Ledger: data/honest_eval/<eval_tag>/results.jsonl"
echo

exec scripts/cloud/evaluate_campaign.sh \
  --hull hammerhead \
  --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
  --top-k 3 \
  --replicates 30 \
  --workers 64 \
  --random-baseline-n 9 \
  --random-baseline-seed 0 \
  --ranking-method twfe_eb \
  "$@"
