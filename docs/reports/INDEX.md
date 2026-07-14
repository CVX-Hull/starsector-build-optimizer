---
type: index
status: shipped
last-validated: 2026-07-12
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
| 2026-05-11 | shipped | [Validation-to-Phase-7 roadmap](2026-05-11-validation-to-phase7-roadmap.md) | Consolidates final honest-eval results, corrected Wave 1 analyses, Phase 7 feature-substrate findings, and the staged roadmap; 2026-05-12 text revision tightens learned-surrogate gates without changing 2026-05-11 evidence. |
| 2026-05-16 | shipped | [Phase 7 seven-split evidence](2026-05-16-phase7-seven-split-evidence.md) | Current seven-split feature-schema-v3 comparator and learned-surrogate matrix. CatBoost leads non-opponent splits; tuned random forest leads opponent hierarchy splits by learned RMSE; all claims exploratory. Readings revised by the 2026-07-11 methodology review. |
| 2026-07-11 | shipped | [Phase 7 methodology review](2026-07-11-phase7-methodology-review.md) | Adversarial review of the seven-split methodology. Evidence stands but readings revised: pooled metrics dominated by opponent difficulty, component split ≡ build split, test-selected family decisions, untracked outer-test reuse. Defines the redesigned next evidence wave. |
| 2026-07-11 | shipped | [AWS execution shift and cost analysis](2026-07-11-aws-cost-analysis.md) | Records the decision to shift Phase 7 compute to AWS (local box occupied) with per-experiment-class cost model, live-verified prices, and unknowns needing measurement. Flags the stale-AMI leak. |
| 2026-07-12 | shipped | [Phase 7 batch v2 re-run incidents](2026-07-12-phase7-batch-v2-incidents.md) | Attempts 1–2 of the 183-job canonical re-run: ERR-trap worker loss ($24, 0/183 results) and component-vocab overshoot infeasibility ($92, 24 insufficiency cells). Root causes, feasibility-probe evidence for the 0.35 cap, and the preflight/upload-retry hardening. No surrogate results. |
| 2026-07-12 | shipped | [Phase 7 attempt-3 surrogate results](2026-07-12-phase7-attempt3-surrogate-results.md) | Canonical 183-job matrix under the v2 harness. CatBoost beats all comparators on build-like splits (10/10 seeds, exploratory); no model transfers across opponents; pooled-metric illusion quantified; ridge retired; CatBoost-vs-RF promotion deferred to confirmatory seed 151. |
| 2026-07-12 | shipped | [Seed-151 confirmatory check](2026-07-12-phase7-seed151-confirmatory.md) | Reserved-seed ratification of CatBoost over tuned RF on the build split (predeclared endpoint met, 11/11 seeds combined with attempt 3). CatBoost becomes the learned-script default; claim boundary stays build-like splits only. |
| 2026-07-12 | shipped | [Learned-batch tail-walltime analysis](2026-07-12-phase7-tail-walltime.md) | Attempt-3 fleet drain reconstruction: 85.3% utilization, idle tail ≈14% of spend (recoverable via scale-down-on-drain), tuned RF supplies the entire scheduling tail, longest-first dispatch worth −11.5% walltime. Corrects the attempt-3 completion time; closes the roadmap tail-walltime measurement item. |
| 2026-07-12 | shipped | [Adversarial-AUC evidence sweep](2026-07-12-phase7-adversarial-auc-evidence.md) | First M2 diagnostic over all 60 canonical cells: build split indistinguishable on 10/10 seeds (attempt-3 build-transfer numbers are interpolation within the TPE cloud), component-vocab and forward-time impose genuine shift, opponent-hierarchy AUCs unstable at their group counts. |
| 2026-07-12 | shipped | [Quality-tooling research](2026-07-12-quality-tooling-research.md) | Empirical tool trials behind the quality-gate adoption: per-tool finding counts and judgments, real defects surfaced, the Error Prone 2.42.0/JDK-17 pin, the shellcheck ERR-trap false negative, and measured rejection evidence for bandit/SpotBugs/PMD/codespell/markdownlint/etc. |
| 2026-07-13 | shipped | [Roadmap re-groom](2026-07-13-roadmap-regroom.md) | Decision record: data-first reordering of the Phase 7 program (designed data wave = opponent panel + off-TPE build arm), staged ablation wave folded into the post-wave re-baseline, six of seven Phase 5/6 re-validation debts retired/folded (5F parked), optimizer defaults flipped to plain TWFE. |
| 2026-07-14 | shipped | [Prequential replay ablation](2026-07-14-phase7-prequential-replay.md) | The M3 optimizer-integration instrument + first readings: CatBoost gate median q\*=0.2 (≈12% rows saved at zero top-3 regret) but the build-blind null matches the median; opponent-adjusted fidelity is positive only for CatBoost (ρ≈0.12 adjacent) and decays to ≈0 by 40+ trials (drift confirmed); folded 5A arms A0–A3+EB indiscriminable on the oracle direction check. All claims exploratory. |
| 2026-05-10 | shipped | [Wave 1 validation](2026-05-10-wave1-validation.md) | Wave 1 training-gate readings under V2 (incl. the per-VM throughput gate, all cells in band); promoted from draft 2026-07-13 — the deferred cross-cell verdict landed in the honest-eval final report. |
| 2026-05-09 | shipped | [Wave 0 validation](2026-05-09-wave0-validation.md) | V2 re-validation Wave 0 preflight gate. All gates passed post-fix; multi-worker LOADOUT_MISMATCH root-caused and verified clean. |

## Draft / In-Flight Reports

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-05-10 | draft | [Validation campaign plan](2026-05-10-validation-plan.md) | Re-validation campaign plan: hull selection, per-mechanism gates, 4-wave architecture, budget, and decision tree. |
| 2026-05-10 | draft | [Post-hoc ranker — research and Wave 1 empirics](2026-05-10-posthoc-ranker-research.md) | Training-log candidate-selection study. `36538033d63b` is the strongest domain-vetted candidate in this draft, not the honest-eval winner. |
| 2026-05-10 | draft | [Wave 2 validation](2026-05-10-wave2-validation.md) | Wave 2 cross-regime warm-start + wolf frigate scaffold. Never launched; the scaffold was retired by the [2026-07-13 re-groom](2026-07-13-roadmap-regroom.md) (wolf measurement folded into the instrumented accounting run). |

## Superseded

Kept for provenance; do not cite for current claims. Each file's frontmatter names its successor.

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-05-10 | superseded | [Wave 1 honest-eval live preliminary](2026-05-10-wave1-honest-eval-live-preliminary.md) | Read-only in-flight snapshot of resumed honest eval after the late-result retry fix and AMI rebake. Superseded by the final 2026-05-11 honest-eval report. |
| 2026-05-11 | superseded | [Phase 7 matchup surrogate preliminary](2026-05-11-phase7-matchup-surrogate-preliminary.md) | V2 / legacy-component comparator evidence. Superseded for current-contract claims by the 2026-05-14 v3 evidence refresh. |
| 2026-05-12 | superseded | [Phase 7 learned surrogate experiment](2026-05-12-phase7-learned-surrogate-experiment.md) | V2 learned-surrogate draft. Superseded for current-contract claims by the 2026-05-14 v3 evidence refresh. |
| 2026-05-14 | superseded | [Phase 7 v3 evidence refresh](2026-05-14-phase7-v3-evidence-refresh.md) | Five-split feature-schema-v3 comparator and learned-surrogate matrix. Superseded for current seven-split claims by the 2026-05-16 seven-split evidence report. |

## Historical / Pre-V2 Reports

| Date | Status | Report | Topic |
|---|---|---|---|
| 2026-04-19 | shipped | [Phase 6 deferred audit findings](2026-04-19-phase6-deferred-audit.md) | Audit items identified during the 2026-04-19 sweep but not fixed in that session. Concurrent-dispatch correctness items remain valid code-path findings. |
| 2026-04-19 | shipped | [Pre-Phase-7-prep relaunch checklist](2026-04-19-phase7-prep-relaunch.md) | Action items that must land before the next Phase 7 prep cloud campaign. Pre-V2; action items are design-grade, not current empirical evidence. |

## Pending re-validation

The Phase 5/6 re-validation debt table moved to the canonical roadmap
([docs/roadmap.md](../roadmap.md) §"Paused") on 2026-07-11. When a report
lands that closes one of those debts, update the roadmap row and list the
report above.

## How to file a new report

1. Filename: `YYYY-MM-DD-<slug>.md`. Date is when the evidence was gathered.
2. Frontmatter: `type: report`, `status: draft` while incomplete or
   `status: shipped` once reviewed, `last-validated: <same date>` when
   shipped or `unvalidated` while draft.
3. Add a row to the appropriate table above.
4. If the report supersedes another, set `supersedes:` in the new file's frontmatter and `superseded-by:` + `status: superseded` in the older file's frontmatter.
5. Before marking `status: shipped`, verify the report against the [`empirical-report`](../../.claude/skills/empirical-report.md) skill, including the supervised-learning checklist when applicable.
6. If the report fills a paused re-validation debt, update the corresponding row in [docs/roadmap.md](../roadmap.md).
