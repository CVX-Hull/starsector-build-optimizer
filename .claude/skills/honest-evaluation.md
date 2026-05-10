---
type: skill
status: shipped
last-validated: 2026-05-10
name: Honest evaluation after major runs
description: SOP for re-scoring a campaign's top builds against the closed opponent population with a transform-free oracle, before publishing report findings. Invoke when an optimization run (Wave / production / large ablation) finishes and the user is about to write up the result.
disable-model-invocation: false
---

# Honest evaluation SOP

Use this skill the moment a major optimization run finishes and **before**
writing any report that ranks cells / runs / builds against each other.
Within-cell scores are not comparable across cells (each cell uses a
different transform stack); honest evaluation produces the cross-cell
ranking using a single transform-free oracle.

Design rationale: [../../docs/reference/honest-evaluation-methodology.md](../../docs/reference/honest-evaluation-methodology.md).
Tool contract: [../../docs/specs/30-honest-evaluator.md](../../docs/specs/30-honest-evaluator.md).

## When to invoke

**Mandatory**:
- After every Wave (1 / 2 / 3) completes
- After every production run completes
- After any ablation campaign whose result will inform a design decision

**Not needed**:
- Smoke tests (`smoke-*.yaml`)
- Heuristic-only runs (`--heuristic-only`)
- Single-study local runs without a comparative report

## The four rules

1. **Honest evaluation runs BEFORE the writeup.** The cell ranking in the
   report comes from the oracle, not from each cell's `best_value`. If you
   write the report first and then realize the oracle disagrees, the
   report is wrong.
2. **The honest oracle is the source of truth for "which cell wins."**
   When the within-cell shaped scores and the oracle disagree, **trust the
   oracle**. Within-cell scores are subject to the cell's own transform
   stack; the oracle is the same metric across all cells.
3. **Failed matchups halt the eval.** The tool raises on any matchup that
   can't be retried to success. Investigate the failure (worker log for the
   offending matchup_id); do not bypass with `|| true`.
4. **Stale-trial errors halt the eval.** If `extract_top_builds` raises
   RuntimeError on a trial whose params no longer round-trip through
   `repair_build`, investigate (search-space changed without a migration?
   repair regression?); do not skip and continue.

## Procedure

### Pre-flight

1. Verify the run is actually complete (`pgrep -f launch_*` returns nothing
   for the relevant wrapper, AND `data/campaigns/<name>/orchestrator.log`
   ends with `teardown complete`).
2. Verify per-study DBs exist: `ls data/study_dbs/<campaign>/*.db` returns
   N files (one per study × seed).
3. Verify cloud preconditions per [`cloud-worker-ops`](cloud-worker-ops.md)
   "The three rules of money" — honest evaluation IS a cloud campaign and
   must respect them: budget ceiling, teardown command, final-audit at end.

### Execute

Always dry-run first to validate inputs without paying:

```
scripts/cloud/evaluate_campaign.sh \
    --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
    --hull hammerhead \
    --top-k 3 --replicates 30 \
    --dry-run
```

Then launch the real run (omit `--dry-run`):

```
scripts/cloud/evaluate_campaign.sh \
    --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
    --hull hammerhead \
    --top-k 3 --replicates 30
```

The wrapper auto-sources `.env`, derives a `starsector-honest-eval-{first-campaign}-{utc}`
fleet namespace via `cloud_runner.prepare_cloud_pool` (separate from any
source-campaign tag — so a stuck source-campaign teardown can't be swept
by accident; the `starsector-` prefix matches `teardown.sh`'s
expectation so cleanup is uniform across campaign and honest-eval
fleets), dispatches via `CloudWorkerPool`, and tears down on exit.
Default fleet size = max `workers_per_study` from the source campaign;
override with `--workers N`. Source campaign config is read from
`examples/{first-campaign-name}.yaml` by default; override with
`--campaign-config <path>`.

#### Fleet sizing — cost is ~constant per matchup; speed scales with concurrency

Total matchup-work is fixed by `top_k × n_cells × n_seeds × pool_size × replicates`.
Cost is fleet_hours × spot_rate, which equals `total_matchups ÷ slots × matchup_duration × $/hr-per-worker / slots-per-worker` — the slot-count cancels, leaving cost roughly invariant under fleet size. **Workers buy walltime, not cost**, until you saturate your spot-quota or fragment the spot pool.

**Per-matchup empirical (post-V2, 2026-05-10)**:
- Wall-clock per matchup: **~75s** at `time_mult=5.0` (default), in-engine cap 300s, healthy combats end in ~40-60s in-engine
- c7a.2xlarge spot: **$0.14-0.18/hr** (us-east-2c cheapest, us-east-1f priciest at this snapshot)
- → **~$0.001 per matchup**

**For Wave 1 hammerhead full sweep** (5 cells × 3 seeds × 3 builds × ~28 destroyer opponents × 30 reps ≈ 38k matchups):

| Workers | Slots | Walltime | Raw cost | Recommended budget |
|---|---|---|---|---|
| 16 (Wave 1 baseline) | 32 | ~24h | $70 | $100 |
| 32 | 64 | ~12h | $70 | $100 |
| 64 (Plan C) | 128 | ~6h | $70 | $100 |

The recommended default is **64 workers** (`--workers 64`): well below quota (40% of one region's `L-34B43A08`), unlikely to fragment spot pool, and 4× faster than the 16-worker baseline. Plan launch via `scripts/cloud/launch_wave1_honest_eval.sh`.

Above 64 workers, spot capacity becomes the constraint (not money or quota); provisioning takes 5-10 min and partial fulfillment becomes likely. Don't go higher unless walltime is critical.

**Quota check** (re-probe before any large run — cf. `reference_aws_quotas.md`):
```sh
aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 \
  --region us-east-1 --query 'Quota.Value'
```
2026-05-10 snapshot: 640 vCPU per region in us-east-1 + us-east-2 (we use both). 64 c7a.2xlarge = 512 vCPU total = 256 per region = 40% of quota.

**Resume on interrupt**: ledger at `data/honest_eval/<eval_tag>/results.jsonl` survives SIGTERM/OOM/network partition. Tear down the fleet (`scripts/cloud/teardown.sh <eval_tag>`) then re-run with `--resume-from <eval_tag>`. Already-completed matchups skip dispatch; aggregation is identical to a clean run.

### Outputs

- `data/campaigns/<name>/honest_eval.json` — per campaign, schema_version=1
- `data/campaigns/honest_eval_summary_YYYY-MM-DD.json` — cross-campaign summary

### Writeup

Report goes to `docs/reports/YYYY-MM-DD-<campaign-set>-honest-eval.md`,
**hand-authored**, citing the JSON files as data inputs. Required sections:

1. **What ran** — campaigns evaluated, top_k, replicates_per_matchup, pool size
2. **Cell ranking** — table from `cell_summaries`, descending by `mean_top_k_oracle`
3. **Within-cell-vs-oracle agreement** — for each cell, did the within-cell
   top-1 match the oracle top-1? If not, by how much do they differ?
4. **Per-build details** — top-3 from each cell with oracle_score ± SEM
5. **Pool composition** — list of variants evaluated against (for reproducibility)
6. **Decisions made from this evaluation** — what design changes (if any) follow

Per `docs/CONVENTIONS.md`, the report carries empirical numbers; the spec /
reference do not.

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| `RuntimeError: trial X failed repair_build` from `extract_top_builds` | Search space evolved without a migration; or `repair_build` regressed | Inspect trial params vs current search space; fix migration or repair, then re-run honest eval |
| `RuntimeError: matchup X failed after N retries` from `evaluate_builds` | Persistent worker failure (corrupted variant? VM preempted at unlucky moment?) | Check worker logs for matchup_id, investigate root cause; do NOT bypass |
| Cell ranking under oracle differs significantly from within-cell `best_value` rankings | The cell's transform stack reshaped the loss landscape — exactly what honest evaluation is for; this is signal, not bug | Trust the oracle. Report both rankings in the writeup with the difference noted. |
| Pool has 0 variants for the player hull | Hull-size lookup or `discover_opponent_pool` mismatch | Check `manifest.json` for the hull's `HullSize`; verify `data/world/factions/` content; this is unusual |
| Cost estimate way off | Throughput differs from training (eg less TIMEOUT-saturated, or smaller fleet) | Recompute, adjust `--replicates` or split the eval into per-cell calls |

## Why this is a skill, not an Engineering Principle

Honest evaluation is an **operational rule** scoped to optimization-run
lifecycle. It does not apply globally to every code change (parser
edits, doc updates, harness work don't trigger it). Engineering
principles in CLAUDE.md are global stances ("principled over expedient",
"address issues, don't paper over them") that apply to all engineering.
Honest evaluation belongs alongside `cloud-worker-ops` and
`post-impl-audit` — operational SOPs invoked at specific lifecycle points.
