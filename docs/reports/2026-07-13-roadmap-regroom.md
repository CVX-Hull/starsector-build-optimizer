---
type: report
status: shipped
last-validated: 2026-07-13
---

# Roadmap re-groom — data-first reordering and Phase 5/6 debt dispositions

**Date**: 2026-07-13. **Kind**: decision record (grooming), not new
empirical evidence — every empirical claim below cites the dated report
that owns it.

## Abstract

A full backlog-grooming pass (four parallel audit agents: roadmap-vs-repo
audit, scattered-commitments sweep, Phase 5/6 debt assessment, evidence
synthesis over the 2026-07 report series) concluded that the binding
constraint on the Phase 7 surrogate program is now **data, not models or
harness**, and the user ratified four decisions: (1) the staged
feature-profile ablation wave (b1/b2, preflight-passed) is **folded into a
post-data-wave re-baseline** instead of launching on the current DB;
(2) six of the seven paused Phase 5/6 re-validation debts are **retired or
folded**, with only 5F kept parked; (3) the optimizer's shipping defaults
**flip to plain TWFE** (EB shrinkage and Box-Cox off by default), resolving
a May-to-July contradiction between defaults and the honest-eval verdict;
(4) the new program centerpiece is a **designed data wave: opponent panel +
off-TPE build-diversity arm**. `docs/roadmap.md` was restructured
accordingly; the 2026-07-11 methodology review's §6 *sequencing* is
superseded by this record (its findings remain authoritative).

## 1. Why data-first

The evidence chain, each link owned by its dated report:

- **Opponent transfer is data-starved.** No learned family is CI-positive
  on any opponent-side split under best-comparator recomputation
  ([attempt-3 §2.2](2026-07-12-phase7-attempt3-surrogate-results.md));
  the axis rests on 54 variants / 22 hulls with 78× row imbalance and
  MNAR pruner/curriculum censoring
  ([methodology review H5](2026-07-11-phase7-methodology-review.md));
  opponent-hierarchy adversarial AUCs swing 0.122–0.960 because ≤ 4
  held-out groups cannot support the estimate
  ([adversarial-AUC evidence](2026-07-12-phase7-adversarial-auc-evidence.md)).
  Opponent-side modeling items are unreadable until panel data exists.
- **The build axis supports only interpolation claims.** Grouped
  adversarial-validation AUC 0.469–0.518 on all 10 bank seeds — held-out
  builds are statistically indistinguishable from training builds
  ([adversarial-AUC evidence](2026-07-12-phase7-adversarial-auc-evidence.md)).
  Novel-build generality is unmeasurable from the wave-1 DB in principle;
  only off-TPE build sampling fixes that. No existing planning doc carried
  such an arm — it is the largest single gap this groom closes.
- **Cost is not the constraint.** Sim matchups ≈ $0.001 each; the H5
  opponent-panel example (~40k matchups) ≈ $45–60 spot; cost is
  ~constant in fleet size
  ([AWS cost analysis §3](2026-07-11-aws-cost-analysis.md)). The binding
  costs are walltime, the holdout-reuse budget (seed 151 is spent —
  confirmatory capacity is zero until a fresh reserved seed is appended,
  [seed-151 confirmatory §3.2](2026-07-12-phase7-seed151-confirmatory.md)),
  and collection-time design quality.

## 2. Decisions (user-ratified 2026-07-13)

### D1 — Fold the staged ablation wave into the new-data re-baseline

The b1/b2 batch configs (240 jobs each, preflight-passed against
`ami-0dfbd09e1d9420a3a`) stay staged but unlaunched. Rationale: three of
the four predeclared contrasts re-run with better power and a fresh reuse
ledger on the designed-wave DB; the opponent-parity contrast (H5 remedy 3)
is precisely the one known to be underpowered today. The enabling code
(feature-profile matrix axis, schema v5) shipped in `96d33cc` and does not
expire. The plan
`.claude/plans/archive/2026/2026-07-12-feature-profile-ablation-wave.md`
is retired with sections A–C implemented and §D/E folded forward.

### D2 — Phase 5/6 debt dispositions

| Debt | Disposition | Reason (evidence owner) |
|---|---|---|
| 5A TWFE A0–A3 ablation | **FOLD** into prequential replay as estimator arms | Same wave-1 logs, same incumbent definition; zero sim cost ([honest-eval final](2026-05-11-wave1-honest-eval-final.md); replay = methodology review M3 remedy) |
| 5D EB shrinkage LOOO gate | **RETIRE** | End-to-end honest eval ranked c0a (plain TWFE) > c0b > c1 (EB): the mechanism gate's proxy question is superseded ([honest-eval final](2026-05-11-wave1-honest-eval-final.md)) |
| 5E Box-Cox shape gate | **RETIRE** | TPE-only mechanism with no Phase 7 consumer; c2 < c1 end-to-end (same report) |
| 5F regime segmentation | **KEEP PARKED** | Real product claim, no current trigger; free side effect of the first multi-hull/regime wave |
| Phase 6 throughput/VM | **RETIRE (passed)** | All 5 cells 138.3–149.4 m/hr/VM, in the design band, under V2 ([wave-1 validation](2026-05-10-wave1-validation.md)) |
| Phase 6 cloud-vs-local ≥ 2× | **RETIRE** | Unmeasurable on the macOS workstation (LocalInstancePool is Linux-only) and no live decision consumes the ratio |
| Wave 2 warm-start + wolf scaffold | **RETIRE scaffold; FOLD wolf measurement** into the instrumented accounting run | Java `uniqueVariantId` fix operationally proven since; wolf m/trial already an open AWS item; warm-start path dormant with no consumer |

Wave-2/3 scaffold scripts (`launch_wave2.sh`, `launch_wave3.sh`,
`deploy_java_fix_for_wave2.sh`, `analyze_wave2.py`,
`post_wave2_automation.sh`, and `post_wave1_automation.sh` — which existed
solely to gate the Wave-2 launch) are deleted; git history is the archive.

### D3 — Optimizer defaults flip to plain TWFE

`OptimizerConfig` shipped EB shrinkage + Box-Cox unconditionally in the
scoring path while the honest-eval verdict ranked plain TWFE above both
([honest-eval final](2026-05-11-wave1-honest-eval-final.md): mean top-K
monotonically worsens c0a → c0b → c1 → c2). The defaults now match the
evidence: EB and shaping become opt-in flags, default off. The folded 5A
estimator arms in the prequential replay can re-open the question with
fresh evidence if warranted.

### D4 — Designed data wave scope: opponent panel + off-TPE build arm

Scope per methodology-review H5 remedy 1 plus the AUC-motivated build
arm; multi-hull remains a separately-triggered follow-up (it gates
BoTorch cross-hull claims, not this wave). Design constraints already
fixed by existing reports (randomized exposure, no pruner censoring,
balanced cells, ~10 replicates/cell for noise floors, hundreds of
variants for double-digit held-out hull/family groups, endpoint-mass
handling, predeclared seeds + fresh reserved confirmatory seed +
opponent-family lockbox + per-wave model-info sheet, per-row
acquisition/propensity metadata for the off-TPE arm) are enumerated with
citations in the roadmap's data-wave item.

## 3. Grooming actions taken with this record

- `docs/roadmap.md` restructured (data-first Active workstream; shipped
  items deleted; item 6 discipline absorbed into the data-wave design;
  stale schema/matrix references fixed; quota constraint recorded).
- Methodology review §6 sequencing bannered as superseded by this record.
- [Wave-1 validation report](2026-05-10-wave1-validation.md) promoted from
  draft: its training-gate readings (incl. the throughput gate) are the
  citation basis for the Phase-6 throughput retirement, and the missing
  honest-eval oracle it awaited has since landed
  ([honest-eval final](2026-05-11-wave1-honest-eval-final.md)).
- `cloud-worker-ops` skill: stale "pending re-validation" pointers for
  throughput/speedup updated to cite the V2 readings / retirement.
- Root workflow file: Phase 5A–5F row updated (mechanism gates resolved;
  magnitude re-validation debts closed per this record — the V1-invalidated
  magnitudes stay invalid, they simply no longer have open re-measurement
  gates).
- Memory: AWS quota note gains the 16-vCPU learned-batch worker cap
  (640 vCPU ⇒ 40 workers); honest-eval fleet-sizing note points at the
  roadmap item for the drain scale-down port.

## Appendix — file map

- `docs/roadmap.md` — restructured in the same commit.
- `.claude/plans/archive/2026/2026-07-12-feature-profile-ablation-wave.md`
  — retired plan (A–C implemented; D/E folded per D1).
- `examples/phase7-learned-batch-ablation-b1.yaml`, `-b2.yaml` — staged,
  unlaunched wave configs retained for the re-baseline.
- Audit-agent transcripts are session artifacts (not retained); their
  load-bearing findings are inlined above with report citations.
