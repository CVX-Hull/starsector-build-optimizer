---
type: index
status: shipped
last-validated: 2026-05-11
---

# Reports — Index

Dated empirical evidence: campaign results, validation outcomes, ablation tables, audit findings, retrospectives. See [docs/CONVENTIONS.md](../CONVENTIONS.md) for the category contract.

Reports are append-only; supersession is via frontmatter (`status: superseded` + `superseded-by`), not deletion.

## Current Reports / V2 Evidence

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-05-10 | shipped | [V1 loadout-bug invalidation](2026-05-10-v1-loadout-bug-invalidation.md) | Master invalidation report. All Phase 5A–5F empirical magnitudes require V2 re-validation before use as current evidence. |
| 2026-05-10 | shipped | [Doc reorg summary](2026-05-10-doc-reorg-summary.md) | Documentation reorganization work-product: convention, files moved, files edited, decisions. |
| 2026-05-10 | shipped | [Wave 1 comprehensive analysis](2026-05-10-wave1-comprehensive-analysis.md) | Training-log ranker and diagnostic analysis, not the honest-eval verdict. c1 leads c2 by training-log TWFE+EB point estimate; c3 trips objective-fidelity and rank-stability warnings. |
| 2026-05-10 | shipped | [Wave 1 optimization-trajectory analysis](2026-05-10-wave1-optimization-trajectory.md) | Training-log trajectory diagnostics. Warm-start is not justified as a default from this report alone; honest eval remains the oracle for cross-cell build quality. |
| 2026-05-10 | shipped | [Wave 1 honest-eval stall checkpoint](2026-05-10-wave1-honest-eval-stall-checkpoint.md) | Snapshot of interrupted eval `…20260510T170431Z`; root causes, cleanup fixes, and resume path. The run is recoverable via `--resume-from` after teardown. |
| 2026-05-11 | shipped | [Wave 1 honest-eval final](2026-05-11-wave1-honest-eval-final.md) | Final transform-free oracle verdict. c0a wins by mean top-K, c1 has the best individual build, c2 loses to both baselines, c3 warm-start remains quarantined, and all optimizer cells beat random-feasible. |
| 2026-05-11 | shipped | [Validation-to-Phase-7 roadmap](2026-05-11-validation-to-phase7-roadmap.md) | Consolidates final honest-eval results, corrected Wave 1 analyses, Phase 7 feature-substrate findings, and the staged roadmap from validation completion through structured optimizer work. |
| 2026-05-11 | shipped | [Phase 7 matchup surrogate preliminary](2026-05-11-phase7-matchup-surrogate-preliminary.md) | Generated SQLite materialization plus comparator-gate grouped baselines for the featurized matchup surrogate. |
| 2026-05-09 | shipped | [Wave 0 validation](2026-05-09-wave0-validation.md) | V2 re-validation Wave 0 preflight gate. All gates passed post-fix; multi-worker LOADOUT_MISMATCH root-caused and verified clean. |

## Draft / In-Flight Reports

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-05-10 | draft | [Validation campaign plan](2026-05-10-validation-plan.md) | Re-validation campaign plan: hull selection, per-mechanism gates, 4-wave architecture, budget, and decision tree. |
| 2026-05-10 | draft | [Wave 1 validation](2026-05-10-wave1-validation.md) | Partial Wave 1 training/gate draft. No final cross-cell build-quality verdict until the honest-eval run completes. |
| 2026-05-10 | superseded | [Wave 1 honest-eval live preliminary](2026-05-10-wave1-honest-eval-live-preliminary.md) | Read-only in-flight snapshot of resumed honest eval after the late-result retry fix and AMI rebake. Superseded by the final 2026-05-11 honest-eval report. |
| 2026-05-10 | draft | [Post-hoc ranker — research and Wave 1 empirics](2026-05-10-posthoc-ranker-research.md) | Training-log candidate-selection study. `36538033d63b` is the strongest domain-vetted candidate in this draft, not the honest-eval winner. |
| 2026-05-10 | draft | [Wave 2 validation](2026-05-10-wave2-validation.md) | Wave 2 cross-regime warm-start + wolf frigate scaffold. Pre-launch; fills in after `launch_wave2.sh` completes. |
| 2026-05-12 | draft | [Phase 7 learned surrogate experiment](2026-05-12-phase7-learned-surrogate-experiment.md) | Local full learned-surrogate run completed; AWS renewable-lease smoke is infra validation only; next work is feature schema v3 ablation without changing the v2 result claims. |

## Historical / Pre-V2 Reports

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-04-19 | shipped | [Phase 6 deferred audit findings](2026-04-19-phase6-deferred-audit.md) | Audit items identified during the 2026-04-19 sweep but not fixed in that session. Concurrent-dispatch correctness items remain valid code-path findings. |
| 2026-04-19 | shipped | [Pre-Phase-7-prep relaunch checklist](2026-04-19-phase7-prep-relaunch.md) | Action items that must land before the next Phase 7 prep cloud campaign. Pre-V2; action items are design-grade, not current empirical evidence. |

## Pending re-validation

Re-validation reports for the following Phase 5/6/7 claims are expected as
V2 sim runs land. Each entry names the current evidence state and the docs
that should link to the eventual shipped report.

| Claim | Status | Design threshold | Reference doc to link from |
|---|---|---|---|
| Phase 5A TWFE A0/A1/A2/A3 ablation | Wave 1 honest-eval final complete; follow-up mechanism-specific gates still pending | A2/A3 outperform A0/A1 on LOOO ρ | [phase5-signal-quality.md](../reference/phase5-signal-quality.md), [phase5a-deconfounding-theory.md](../reference/phase5a-deconfounding-theory.md) |
| Phase 5D EB shrinkage vs A0/A | Wave 1 honest-eval final disfavors c2 as default; mechanism-specific LOOO gate still pending | Δρ ≥ +0.02 vs A0 and vs legacy A | [phase5d-covariate-adjustment.md](../reference/phase5d-covariate-adjustment.md), [28-deconfounding.md](../specs/28-deconfounding.md) |
| Phase 5E Box-Cox A3 ceiling/overlap | Wave 1 honest-eval final disfavors EB+Box-Cox as tested; mechanism-specific shape gate still pending | ceiling saturation ≤ 1%; top-5 overlap ≥ 0.40 | [phase5e-shape-revision.md](../reference/phase5e-shape-revision.md) |
| Phase 5F regime segmentation effect | pending Wave 2+ | distinguishable optimum across regimes | [phase5f-regime-segmented-optimization.md](../reference/phase5f-regime-segmented-optimization.md) |
| Phase 6 cloud throughput per VM | partial V2 draft | per-VM throughput gate passed; Wave 3 budget feasibility pending Wave 2 sizing | [phase6-cloud-worker-federation.md](../reference/phase6-cloud-worker-federation.md), [throughput-optimization.md](../reference/throughput-optimization.md), [17-throughput-estimator.md](../specs/17-throughput-estimator.md), [22-cloud-deployment.md](../specs/22-cloud-deployment.md) |
| Phase 6 cloud-vs-local speedup | pending | ≥ 2× | [phase6-cloud-worker-federation.md](../reference/phase6-cloud-worker-federation.md) |

## How to file a new report

1. Filename: `YYYY-MM-DD-<slug>.md`. Date is when the evidence was gathered.
2. Frontmatter: `type: report`, `status: draft` while incomplete or
   `status: shipped` once reviewed, `last-validated: <same date>` when
   shipped or `unvalidated` while draft.
3. Add a row to the appropriate table above.
4. If the report supersedes another, set `supersedes:` in the new file's frontmatter and `superseded-by:` + `status: superseded` in the older file's frontmatter.
5. Before marking `status: shipped`, verify the report against [docs/CONVENTIONS.md](../CONVENTIONS.md) §"Empirical-report writing standard", including the supervised-learning checklist when applicable.
6. If the report fills a "Pending re-validation" row, remove that row from the table.
