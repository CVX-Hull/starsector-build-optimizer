---
type: index
status: shipped
last-validated: 2026-07-13
---

# Roadmap

Canonical owner of "what is planned next": forward workstreams, open action
items, paused debts, and deferred ideas. `AGENTS.md` owns the *shipped* phase
map; this file owns everything forward-looking. Reports and reference docs
must not accumulate their own live next-step lists — when a report's "next
steps" section is adopted, move the items here and leave the report as the
dated evidence for *why*. No internal-sim numbers here; follow the links.

Groomed: 2026-07-13 — full data-first re-groom, user-ratified; decisions and
rationale: [2026-07-13 re-groom record](reports/2026-07-13-roadmap-regroom.md).
Re-groom whenever a wave completes or a decision changes scope; update
`last-validated`.

## Active workstream — Phase 7 surrogate evidence program (data-first order)

Findings owner: [2026-07-11 methodology review](reports/2026-07-11-phase7-methodology-review.md)
(its §6 *sequencing* is superseded by the
[2026-07-13 re-groom](reports/2026-07-13-roadmap-regroom.md); the findings
remain authoritative). Literature grounding:
[phase7-surrogate-methodology-gaps](reference/phase7-surrogate-methodology-gaps.md).
Compute runs on AWS learned-batch; costs:
[2026-07-11 AWS cost analysis](reports/2026-07-11-aws-cost-analysis.md).

1. **Optimizer defaults flip to plain TWFE** (small code change, gates
   apply): EB shrinkage and Box-Cox shaping become opt-in flags, default
   off, per the honest-eval end-to-end ranking
   ([re-groom D3](reports/2026-07-13-roadmap-regroom.md)).
2. **Prequential replay ablation** — the decision-relevant
   optimizer-integration gate (methodology review M3), from existing wave-1
   logs; local, no sim spend. Requirements added by later evidence:
   **drift-aware** reporting (rank fidelity vs temporal distance — the
   forward-time partition is genuinely shifted,
   [adversarial-AUC evidence](reports/2026-07-12-phase7-adversarial-auc-evidence.md))
   and **estimator arms** A0/A1/A2/A3 folded in from the retired Phase 5A
   debt (same logs, same incumbent definition;
   [re-groom D2](reports/2026-07-13-roadmap-regroom.md)).
3. **Data-wave prerequisites** (AWS, cheap):
   - port a **cost ledger** onto the honest-eval path (still absent;
     [AWS cost analysis §4](reports/2026-07-11-aws-cost-analysis.md));
   - port **scale-down-on-drain** to the honest-eval fleet (shipped for
     the learned batch only);
   - one **instrumented accounting run** to resolve the matchups-per-trial
     spread — includes the never-landed **wolf** (non-meta hull)
     measurement (absorbs the retired Wave-2 residue).
4. **Designed data wave — opponent panel + off-TPE build-diversity arm**
   (the centerpiece; new sim spend, scope user-ratified in
   [re-groom D4](reports/2026-07-13-roadmap-regroom.md)). Design
   constraints already fixed by evidence: randomized opponent exposure, no
   pruner censoring, balanced (build × opponent) cells with replicates for
   noise floors (methodology review H5, H1); enough variants for
   double-digit held-out hull/family groups (adversarial-AUC instability
   at current group counts); endpoint-mass handling per H1; **off-TPE
   build sampling** with per-row acquisition/propensity metadata so
   novel-build claims become measurable (build-split AUC ≈ 0.5 on all
   seeds); reuse discipline designed in at collection time — predeclared
   rotated seeds, a **fresh reserved confirmatory seed** (151 is spent),
   an **opponent-family lockbox** opened once per phase gate,
   **Ladder-margin acceptance**, and a **per-wave model-info sheet**
   (absorbs the former evidence-reuse-discipline item; methodology review
   C4). Spec-first through the normal plan gates.
5. **Re-baseline + feature-profile ablations on the new DB** — absorbs the
   staged-but-unlaunched b1/b2 wave
   ([re-groom D1](reports/2026-07-13-roadmap-regroom.md); configs
   `examples/phase7-learned-batch-ablation-b1/-b2.yaml` stay staged; the
   four predeclared primary contrasts carry over from the retired plan).
   Before launch: decide the **RF HPO-budget rebalance** (tail-walltime
   open question — RF is most of fleet compute and loses on build-like
   splits). Delivers the parked M2 **sparse-ID ablation** as predeclared
   contrast 1.
6. **FM / low-rank bilinear interaction features** as a new model family
   (replaces retired sparse-pairwise-ridge), judged on the new DB's
   powered opponent splits. Mandatory comparator: **archetype-cluster-mean
   opponent baseline** before any learned-opponent-representation claim
   ([methodology-gaps §1](reference/phase7-surrogate-methodology-gaps.md)).
7. **Within-opponent pairwise-ranking CatBoost** (groups = opponent, pairs
   gapped beyond noise floor) — targets the top-decile ≈ 0 weakness, the
   statistic the optimizer actually exploits. Scope now includes the **H1
   two-part censored-target treatment** (top-end weakness and 58.7%
   endpoint mass are two faces of the same target problem).

## AWS / infrastructure notes

- **Spot-quota constraint (measured 2026-07-13)**: L-34B43A08
  (Standard-family spot, us-east-1) = 640 vCPU ⇒ max 40 concurrent
  16-vCPU learned-batch workers (80 × 8-vCPU sim workers). Fleets must be
  sized to it or a quota increase requested per-launch.
- Stale-AMI hygiene and the pre-launch gate live as SOPs in
  [`cloud-worker-ops`](../.claude/skills/cloud-worker-ops.md) — not open
  work.

## Planned phases (gated)

- **Phase 7 — BoTorch structured-search GP sampler**: go/no-go gate =
  the prequential replay (item 2) plus an offline MCBO bake-off
  (D-scaled vanilla mixed-GP baseline first) per
  [phase7-search-space-compression](reference/phase7-search-space-compression.md);
  cross-hull claims additionally require a **multi-hull data wave**
  (also the trigger for parked 5F below).
- **Phase 7.5 — Infra & reproducibility**:
  [phase7.5-infrastructure-reproducibility](reference/phase7.5-infrastructure-reproducibility.md).

## Paused

- **Phase 5F regime segmentation** (distinguishable optimum across
  regimes) — kept parked by the
  [2026-07-13 re-groom](reports/2026-07-13-roadmap-regroom.md); explicit
  trigger: the first multi-hull/multi-regime data wave produces this
  evidence as a near-free side effect. Reference:
  [phase5f-regime-segmented-optimization](reference/phase5f-regime-segmented-optimization.md).

All other Phase 5/6 re-validation debts were retired or folded 2026-07-13
— dispositions and evidence:
[re-groom D2](reports/2026-07-13-roadmap-regroom.md).

## Deferred

- **M2 leakage-diagnostic residue**: nearest-neighbor overlap,
  rare-combination overlap, and nearest-neighbor-distance-stratified rank
  metrics (the review's fuller remedy) — natural home is the item-5
  re-baseline on the new DB. Adversarial-validation AUC shipped
  2026-07-12; the sparse-ID ablation is predeclared as item-5 contrast 1.
- **Feature-family registry / learned-selection enforcement** (spec 31
  forward-looking contract): becomes live the moment any model family does
  learned feature selection — item 6's FM path is the likely trigger.
- **Comparator tuning-budget parity** (C3 residue): comparators run at
  fixed defaults vs tuned learned families; a tuned-comparator arm only if
  a model-family claim ever needs it.
- **H5 remedy rungs 2 & 4** (hierarchical partial pooling variant⊂hull⊂
  family; out-of-fold opponent-difficulty encoding) — candidate additions
  to item 6's evaluation if the FM family under-delivers on panel data.
- **Phase 5G — adversarial PSRO opponent curriculum**: revisit after the
  opponent-representation work (items 4/6) lands.
- **Weapon-group decisions in the search space** (fidelity limitation; a
  future prior/residual may learn grouping sensitivity).
- **Multi-hull surrogate** (Phase 7+ extension once the single-hull kernel
  is validated; the multi-hull *data wave* itself is gated under Planned
  phases).

## Superseded next-step lists (do not work from these)

- [2026-07-11 methodology review §6](reports/2026-07-11-phase7-methodology-review.md)
  sequencing — replaced by the Active workstream above (findings remain
  authoritative); see the
  [2026-07-13 re-groom record](reports/2026-07-13-roadmap-regroom.md).
- [2026-05-16 seven-split evidence §4](reports/2026-05-16-phase7-seven-split-evidence.md)
  — replaced by the Active workstream above.
- [2026-05-11 validation-to-Phase-7 roadmap](reports/2026-05-11-validation-to-phase7-roadmap.md)
  staged sequence — readings revised by the 2026-07-11 methodology review.
- [2026-04-19 phase7-prep relaunch checklist](reports/2026-04-19-phase7-prep-relaunch.md)
  — pre-V2; its must-do items shipped with Phase-7-prep.
