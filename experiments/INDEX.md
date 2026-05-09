---
type: index
status: shipped
last-validated: 2026-05-10
---

# Experiments — Index

Forward-looking registry of dated experiment directories. See [../docs/CONVENTIONS.md](../docs/CONVENTIONS.md) for the documentation category contract and [../docs/reports/INDEX.md](../docs/reports/INDEX.md) for reports.

## Current state (post-V1-invalidation cleanup)

**Post-V2 evidence currently lives only at `data/campaigns/smoke*` (gitignored campaign artifacts).**

The pre-V2 experiment directories listed below were deleted during the 2026-05-10 V1 loadout-bug invalidation cleanup. Their numbers were derived from a confounded sim that mounted stock-variant weapons on the deployed player ship instead of the optimizer's chosen loadout. Keeping the directories on disk while the numbers are invalid is worse than deleting them — see [../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md) for the rationale.

This INDEX is forward-looking. New experiments land here when they're set up; pre-V2 ones are listed below for historical pointer-resolution only. If a reference doc cites `experiments/foo-2026-04-XX/`, that path resolves through this file to the deletion record.

## Pre-V2 experiment directories (deleted 2026-05-10)

All of the following were removed from the working tree as part of the V1 invalidation cleanup commit. Use `git log --diff-filter=D --name-only -- experiments/` to recover the deletion commit hash; the historical content is reachable via that commit's parent tree.

| Directory | Date | What it claimed |
|---|---|---|
| `hammerhead-overnight-2026-04-13` | 2026-04-13 | First Hammerhead overnight cloud run; baseline for Phase 5A TWFE deconfounding gate. |
| `hammerhead-twfe-2026-04-13` | 2026-04-13 | TWFE A0/A1/A2/A3 ablation on the overnight Hammerhead corpus. |
| `phase5b-curriculum-simulation` | 2026-04-1X | Synthetic Wilcoxon-pruner + ASHA simulation feeding Phase 5B parameter choices. |
| `signal-quality-2026-04-17` | 2026-04-17 | First-pass signal-quality LOOO grid that motivated EB shrinkage adoption. |
| `phase5d-covariate-2026-04-17` | 2026-04-17 | EB covariate-count sweep + FEATURE_COUNT_REPORT.md; sized p=7. |
| `phase5d-ttk-signal-2026-04-18` | 2026-04-18 | TTK-as-8th-EB-covariate benchmark. |
| `signal-quality-5d-2026-04-18` | 2026-04-18 | 4-regime calibration sweep. |
| `phase5e-cloud-validation-2026-04-18` | 2026-04-18 | Cloud-side Box-Cox validation. |
| `cloud-benchmark-2026-04-18` | 2026-04-18 | First full c7a.2xlarge × workers throughput benchmark. |
| `phase6-planning` | 2026-04-1X | Phase 6 cost / sizing plans. |
| `phase7-layer34-benchmark-2026-04-19` | 2026-04-19 | Phase 7 prep layer-3/4 sampler comparison. Aborted at T+48m. |
| `phase7-prep-2026-04-19` | 2026-04-19 | First Phase 7 prep relaunch attempt. |
| `phase7-prep-aborted-2026-04-19` | 2026-04-19 | Same campaign retained for post-mortem. |
| `shakedown-2026-04-19-gate` | 2026-04-19 | Concurrent-dispatch shakedown that surfaced three correctness fixes (these fixes are valid; the timing/throughput numbers are confounded). |
| `smoke-2026-04-19-gate` | 2026-04-19 | Tier-2 smoke gate run; passed under V1 sim. |
| `stale-pre-seed0-fix-2026-04-19` | 2026-04-19 | Pre-fix corpus retained for the seed-disambiguation diff. |

See [../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md) for full context.

## How to file a new experiment

1. Create `experiments/<slug>-YYYY-MM-DD/` (date suffix; the directory itself is the unit of work).
2. Add a row to "Current experiments" (create the section above the deletion record if this is the first post-V2 experiment).
3. When the experiment is summarized into a report, link that report from the INDEX row.
4. Experiments that turn out to be confounded (like the V1 set) get deleted, not archived — the cost of accidental future citation outweighs the storage savings of keeping them.
