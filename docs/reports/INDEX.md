---
type: index
status: shipped
last-validated: 2026-05-10
---

# Reports — Index

Dated empirical evidence: campaign results, validation outcomes, ablation tables, audit findings, retrospectives. See [docs/CONVENTIONS.md](../CONVENTIONS.md) for the category contract.

Reports are append-only; supersession is via frontmatter (`status: superseded` + `superseded-by`), not deletion.

## Active reports

| Date | Report | Topic |
|---|---|---|
| 2026-05-09 | [Wave 0 validation](2026-05-09-wave0-validation.md) | **Draft.** V2 re-validation Wave 0 results: 4-step preflight; steps 1-3 GREEN, step 4 surfaces multi-worker LOADOUT_MISMATCH (Wave-1 blocker). |
| 2026-05-10 | [V1 loadout-bug invalidation](2026-05-10-v1-loadout-bug-invalidation.md) | Master invalidation report. All Phase 5A–5F empirical claims pending re-validation. |
| 2026-05-10 | [Validation campaign plan](2026-05-10-validation-plan.md) | **Draft.** Re-validation campaign plan: hull selection, per-mechanism gates, 4-wave architecture, budget + decision tree. |
| 2026-05-10 | [Doc reorg summary](2026-05-10-doc-reorg-summary.md) | Documentation reorganization work-product: convention, files moved, files edited, decisions. |
| 2026-04-19 | [Phase 6 deferred audit findings](2026-04-19-phase6-deferred-audit.md) | Audit items identified during the 2026-04-19 sweep but not fixed in that session. Pre-V2; concurrent-dispatch correctness items remain valid (they're code-path bugs, not measurement claims). |
| 2026-04-19 | [Pre-Phase-7-prep relaunch checklist](2026-04-19-phase7-prep-relaunch.md) | Action items that must land before the next Phase 7 prep cloud campaign. Pre-V2; the action-item list is design-grade and unaffected by V1 invalidation. |

## Pending re-validation

Re-validation reports for the following Phase 5/6/7 claims are expected once V2 sim runs land. Each entry below names the design threshold (where one exists) and the reference doc the report will be linked from.

| Claim | Design threshold | Reference doc to link from |
|---|---|---|
| Phase 5A TWFE A0/A1/A2/A3 ablation | A2/A3 outperform A0/A1 on LOOO ρ | [phase5-signal-quality.md](../reference/phase5-signal-quality.md), [phase5a-deconfounding-theory.md](../reference/phase5a-deconfounding-theory.md) |
| Phase 5D EB shrinkage vs A0 | Δρ ≥ +0.03 | [phase5d-covariate-adjustment.md](../reference/phase5d-covariate-adjustment.md), [28-deconfounding.md](../specs/28-deconfounding.md) |
| Phase 5E Box-Cox A3 ceiling/overlap | ceiling saturation < 5%; top-5 overlap > 0.3 | [phase5e-shape-revision.md](../reference/phase5e-shape-revision.md) |
| Phase 5F regime segmentation effect | distinguishable optimum across regimes | [phase5f-regime-segmented-optimization.md](../reference/phase5f-regime-segmented-optimization.md) |
| Phase 6 cloud throughput per VM | enables ≤ $85 budget at 8-hull × 600-trial scope | [phase6-cloud-worker-federation.md](../reference/phase6-cloud-worker-federation.md), [throughput-optimization.md](../reference/throughput-optimization.md), [17-throughput-estimator.md](../specs/17-throughput-estimator.md), [22-cloud-deployment.md](../specs/22-cloud-deployment.md) |
| Phase 6 cloud-vs-local speedup | ≥ 2× | [phase6-cloud-worker-federation.md](../reference/phase6-cloud-worker-federation.md) |

## How to file a new report

1. Filename: `YYYY-MM-DD-<slug>.md`. Date is when the evidence was gathered.
2. Frontmatter: `type: report`, `status: shipped`, `last-validated: <same date>`.
3. Add a row to the "Active reports" table above.
4. If the report supersedes another, set `supersedes:` in the new file's frontmatter and `superseded-by:` + `status: superseded` in the older file's frontmatter.
5. If the report fills a "Pending re-validation" row, remove that row from the table.
