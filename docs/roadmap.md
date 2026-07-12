---
type: index
status: shipped
last-validated: 2026-07-12
---

# Roadmap

Canonical owner of "what is planned next": forward workstreams, open action
items, paused debts, and deferred ideas. `AGENTS.md` owns the *shipped* phase
map; this file owns everything forward-looking. Reports and reference docs
must not accumulate their own live next-step lists — when a report's "next
steps" section is adopted, move the items here and leave the report as the
dated evidence for *why*. No internal-sim numbers here; follow the links.

Groomed: 2026-07-12 (tail-walltime measurement closed; scale-down-on-drain
follow-up added). Re-groom whenever a wave completes or a decision changes
scope; update `last-validated`.

## Active workstream — Phase 7 surrogate evidence program

Owner of rationale: [2026-07-11 methodology review](reports/2026-07-11-phase7-methodology-review.md)
(§6 order is authoritative); literature grounding:
[phase7-surrogate-methodology-gaps](reference/phase7-surrogate-methodology-gaps.md).
Compute runs on AWS learned-batch — decision + costs:
[2026-07-11 AWS cost analysis](reports/2026-07-11-aws-cost-analysis.md).

1. **Evaluation-harness fix** (gates all later items): **implemented
   2026-07-11** (plan `2026-07-11-phase7-eval-harness-fix`; spec 31 schema v2)
   — per-opponent rank metrics with noise-floor ties, sparse/top-decile
   Kendall τ, precision@k / regret@k, build-aggregate metrics, skill scores,
   cluster-bootstrap CIs, rotated 10-seed bank (+ reserved confirmatory seed
   151), component-vocabulary split, grouped k-fold inner CV with aligned
   seeds, inline comparators, outer-split lineage. The 183-job canonical
   re-run completed 2026-07-12 (attempt 3); results:
   [2026-07-12 attempt-3 surrogate results](reports/2026-07-12-phase7-attempt3-surrogate-results.md).
   The seed-151 confirmatory check ratified CatBoost over tuned RF on the
   build split (2026-07-12; the reserved seed is now spent and the learned
   script's default family is `catboost_regressor`):
   [2026-07-12 seed-151 confirmatory](reports/2026-07-12-phase7-seed151-confirmatory.md).
   **Item closed — no open sub-items.**
2. **Feature-profile ablations** on repeated opponent-family/opponent splits,
   under the fixed harness.
3. **FM / low-rank bilinear interaction features** as a new model family
   (replaces the retired sparse-pairwise-ridge path), judged on unseen-family
   rank metrics.
4. **Within-opponent pairwise-ranking CatBoost** (groups = opponent, pairs
   gapped beyond noise floor).
5. **Prequential replay ablation** — the decision-relevant optimizer-integration
   gate, from existing logs.
6. **Evidence-reuse discipline**: rotated/predeclared split seeds,
   opponent-family lockbox, Ladder-margin acceptance, per-wave model-info
   sheet; amend spec 31 artifact contract with outer-test reuse lineage.
7. **Opponent-panel data wave** (new sim spend): wide stock-variant panel,
   randomized exposure, balanced replicates.

## AWS / infrastructure action items

From [2026-07-11 AWS cost analysis](reports/2026-07-11-aws-cost-analysis.md)
§4–5:

- Port a cost ledger onto the honest-eval path before the next sweep.
- One instrumented run to resolve the matchups-per-trial accounting spread
  (blocks phase7-prep budgeting); includes the never-landed wolf (non-meta
  hull) measurement.
- Tail-job walltime at scale: **measured 2026-07-12** from the attempt-3
  ledger ([tail-walltime analysis](reports/2026-07-12-phase7-tail-walltime.md))
  — idle drain tail is a material spend share and tuned RF supplies the
  entire scheduling tail. Follow-up (do before the item-2 ablation wave):
  **implement scale-down-on-drain** in the learned-batch control plane
  (terminate each worker at queue-empty + last upload), plus
  longest-expected-first dispatch ordering (static family × split duration
  ranking). Event timestamps (`received_at_utc`) already landed with the
  analysis.
- Stale-AMI hygiene: run `audit_amis.sh` + `cleanup_amis.sh` after every
  re-bake (done 2026-07-11; keep as post-bake SOP step).
- Seed-bank split-uniqueness check (spec 31, small): component-vocab seeds
  107 and 149 produced byte-identical splits in the 2026-07 wave —
  dedupe-or-reject at split construction; evidence:
  [attempt-3 results §2.4](reports/2026-07-12-phase7-attempt3-surrogate-results.md).

## Planned phases (unchanged in scope, gated)

- **Phase 7 — BoTorch structured-search GP sampler**: implement only after
  the evidence program above shows learned-surrogate value; start from
  D-scaled vanilla mixed-GP baseline per the updated kernel plan in
  [phase7-search-space-compression](reference/phase7-search-space-compression.md);
  an offline surrogate bake-off (MCBO harness, LFBO/RF baselines) precedes
  any sim-budget commitment. Cross-hull claims additionally require a small
  multi-hull data wave.
- **Phase 7.5 — Infra & reproducibility**:
  [phase7.5-infrastructure-reproducibility](reference/phase7.5-infrastructure-reproducibility.md).

## Paused — Phase 5/6 re-validation debts

Mechanism-specific V2 re-validation gates below were defined before the
Phase 7 pivot and remain unscheduled; they are debts, not active work. Decide
per-item whether to run or retire when the surrogate program stabilizes.
Evidence context: [Wave 1 honest-eval final](reports/2026-05-11-wave1-honest-eval-final.md).

| Claim | State | Design threshold | Reference doc |
|---|---|---|---|
| Phase 5A TWFE A0/A1/A2/A3 ablation | honest-eval final complete; mechanism gate unscheduled | A2/A3 outperform A0/A1 on LOOO ρ | [phase5-signal-quality](reference/phase5-signal-quality.md), [phase5a-deconfounding-theory](reference/phase5a-deconfounding-theory.md) |
| Phase 5D EB shrinkage vs A0/A | honest-eval disfavors c2 as default; LOOO gate unscheduled | Δρ ≥ +0.02 vs A0 and vs legacy A | [phase5d-covariate-adjustment](reference/phase5d-covariate-adjustment.md), [spec 28](specs/28-deconfounding.md) |
| Phase 5E Box-Cox A3 ceiling/overlap | honest-eval disfavors EB+Box-Cox as tested; shape gate unscheduled | ceiling saturation ≤ 1%; top-5 overlap ≥ 0.40 | [phase5e-shape-revision](reference/phase5e-shape-revision.md) |
| Phase 5F regime segmentation effect | pending Wave 2+ | distinguishable optimum across regimes | [phase5f-regime-segmented-optimization](reference/phase5f-regime-segmented-optimization.md) |
| Phase 6 cloud throughput per VM | partial V2 draft | per-VM gate passed; Wave 3 feasibility pending Wave 2 sizing | [phase6-cloud-worker-federation](reference/phase6-cloud-worker-federation.md), [throughput-optimization](reference/throughput-optimization.md), specs [17](specs/17-throughput-estimator.md)/[22](specs/22-cloud-deployment.md) |
| Phase 6 cloud-vs-local speedup | pending | ≥ 2× | [phase6-cloud-worker-federation](reference/phase6-cloud-worker-federation.md) |
| Wave 2 (warm-start + wolf scaffold) | pre-launch draft, never launched | see draft | [2026-05-10 Wave 2 validation](reports/2026-05-10-wave2-validation.md) |

## Deferred

- **H1 two-part censored model** (score-regime classifier + contested-regime
  magnitude, heteroscedastic noise from honest-eval replicates) — methodology
  review H1 remedy; no current owner. Closest kin is item 4's noise-floor
  machinery.
- **M2 leakage diagnostics** (adversarial-validation AUC, nearest-neighbor
  overlap, rare-combination overlap, sparse-ID ablation) — parked under
  item 2's ablation wave (our assignment; the review left them unowned).
  Until they exist, build-split rank numbers read as interpolation within a
  TPE-concentrated cloud. The v2 artifacts now stamp all four as
  `diagnostic_not_implemented`; adversarial-validation AUC first — it
  directly qualifies the attempt-3 interpolation reading (§2.4).
- **Comparator tuning-budget parity** (C3 residue): comparators run at fixed
  defaults vs tuned learned families; a tuned-comparator arm only if a
  model-family claim ever needs it.
- **Phase 5G — adversarial PSRO opponent curriculum**: researched, revisit
  after the opponent-representation work (items 3/7 above) lands.
- **Weapon-group decisions in the search space** (fidelity limitation recorded
  in the roadmap report; a future prior/residual may learn grouping
  sensitivity).
- **Multi-hull surrogate** (natural Phase 7+ extension once single-hull kernel
  validated).

## Superseded next-step lists (do not work from these)

- [2026-05-16 seven-split evidence §4](reports/2026-05-16-phase7-seven-split-evidence.md)
  — replaced by the Active workstream above.
- [2026-05-11 validation-to-Phase-7 roadmap](reports/2026-05-11-validation-to-phase7-roadmap.md)
  staged sequence — readings revised by the 2026-07-11 methodology review.
- [2026-04-19 phase7-prep relaunch checklist](reports/2026-04-19-phase7-prep-relaunch.md)
  — pre-V2; its must-do items shipped with Phase-7-prep.
