---
type: index
status: shipped
last-validated: 2026-07-14
---

# Roadmap

Canonical owner of "what is planned next": forward workstreams, open action
items, paused debts, and deferred ideas. `AGENTS.md` owns the *shipped* phase
map; this file owns everything forward-looking. Reports and reference docs
must not accumulate their own live next-step lists — when a report's "next
steps" section is adopted, move the items here and leave the report as the
dated evidence for *why*. No internal-sim numbers here; follow the links.

Groomed: 2026-07-14 — items 1–2 delivered (defaults flip; prequential
replay); replay follow-ups wired into items 3–7 + the Phase-7 gate; item-3
cost-ledger + scale-down-on-drain prerequisites shipped (honest-eval cost
measurement + orchestrator-driven fleet drain, both dormant/measurement-only
until the next AMI re-bake activates the worker-side signals).
Full data-first re-groom 2026-07-13, user-ratified; decisions and
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

1. ~~Optimizer defaults flip~~ — **shipped 2026-07-13** (plain TWFE
   default; EB/Box-Cox opt-in per
   [re-groom D3](reports/2026-07-13-roadmap-regroom.md)).
2. ~~Prequential replay ablation~~ — **shipped 2026-07-14**
   ([evidence](reports/2026-07-14-phase7-prequential-replay.md)): the M3
   instrument exists and re-runs on any future proposal stream; first
   readings show surrogate gating not separable from the build-blind
   null on wave-1 data, opponent-adjusted signal flat within the
   measured horizon then gone, and the 5A arm fold discharged
   (target-scale + EB-arm deviations noted at discharge). The
   adjacent-block opponent-adjusted fidelity from that report is the
   first of the **pre-wave baseline readings** (historical context);
   the operational baseline for the item-4 comparison is the plain
   replay on the item-3 stream, contrasted augmented-vs-unaugmented on
   the same cells and cutoffs (item 4).
3. **Data-wave prerequisites** (AWS; the base run is dollar-cheap, but
   walltime and the optional stream oracle coverage below are the real
   budgets, decided at the plan gate):
   - ~~port a **cost ledger** onto the honest-eval path~~ — **shipped
     2026-07-14**: measurement-only `CostLedger` (`budget_usd=None`) driven
     by the extracted `CostHeartbeatTicker` from a background loop in
     `honest_evaluator.main`, writing `data/honest_eval/<tag>/cost_ledger.jsonl`
     (spec 30 §"Cost measurement"); closes AWS-cost-analysis Unknown #1
     (realized honest-eval spend now measured, not derived);
   - ~~port **scale-down-on-drain** to the honest-eval fleet~~ — **shipped
     2026-07-14**: orchestrator-driven `WorkerDrainTicker` in
     `honest_evaluator.main` terminates provably-idle surplus workers (idle
     signal = `active_matchups == 0` in the heartbeat; keep-floor
     `max(1, ceil(remaining/slots))` sized from Python-side remaining, not
     Redis depth) via a new `CloudProvider.terminate_instances` primitive
     (spec 22 §"Worker drain (honest-eval)", spec 30 §"Fleet drain"). Dormant
     until the worker AMI is re-baked to emit `active_matchups`; `--no-drain`
     escape hatch;
   - one **instrumented accounting run** to resolve the matchups-per-trial
     spread — includes the never-landed **wolf** (non-meta hull)
     measurement (absorbs the retired Wave-2 residue). The run's
     proposal stream additionally **doubles as the fresh
     prequential-replay input** for the Phase 7 gate — the designed wave
     (item 4) is a balanced panel, not a stream, so this run is the
     natural second replay substrate
     ([replay evidence](reports/2026-07-14-phase7-prequential-replay.md)).
     That role imposes requirements the item-3 plan gate must design
     in: the replay's full data prerequisites (standard eval logs with
     planned opponent order including pruned trials, study DBs with
     start/complete timestamps for the in-flight gap, frozen matchup-DB
     materialization with join totality — spec 31 §"Prequential Replay
     Ablation"); replay-adequate sizing (full-study trial counts per
     cell; the plan gate sets the minimum cell count for gate adequacy —
     single-cell streams are not gate-adequate, their readings are
     directional only); a **minimum count of wave-hull (hammerhead)
     cells** — gate adequacy is defined over that subset, both conditions of
     the item-4 contrast run on it, and wolf cells serve the accounting
     purpose only (directional replay readings at most; cross-hull
     claims stay gated behind the multi-hull wave); a **stream-reuse
     discipline** — the complete gate statistic (statistic type,
     arm, cell scope, cutoffs, aggregation) is predeclared at this plan
     gate **before the stream is collected**, superseding-or-reaffirming
     the replay's shipped gating headline for gate purposes, and no
     model-selection re-run consults the stream before that
     predeclaration; consultations are appended to a **git-tracked
     per-stream analysis ledger** (a report-companion file under a
     negated `data/<stream>/` path or `docs/`, created by this item and
     named in its report — not the gitignored frozen-DB directory, so
     the anti-forking record is version-controlled), later new-family
     re-runs entering as ledger entries; an
     explicit cost/scope decision on **stream oracle coverage** — if
     gating evaluation on this stream is to use continuous oracle-value
     regret (zero-regret counting proved too lumpy to discriminate
     gates, [replay evidence](reports/2026-07-14-phase7-prequential-replay.md)),
     a designed subset of stream builds needs honest-eval coverage,
     which is the expensive kind of spend, and the subset-selection
     rule (e.g. rank-stratified under the predeclared arm) is fixed in
     the same predeclaration so coverage cannot be chosen after
     readings exist; and ownership — **running
     the replay on this stream and filing its report is a deliverable
     of this item**. This item also **owns retaining the frozen stream
     matchup DB + eval logs, re-runnable, through item 7** — items 6/7
     re-fit the instrument on them offline to obtain their promotion
     readings, so the artifacts must not be reaped mid-program. Sizing
     and any oracle-coverage spend are ratified by the user at this
     item's plan gate (the D4 pattern), since they convert the ratified
     accounting errand into the Phase-7 gate's evidence substrate.
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
   C4). Added by the replay findings
   ([replay evidence](reports/2026-07-14-phase7-prequential-replay.md)):
   the wave's **player hull is hammerhead** (the program's single-hull
   anchor; multi-hull stays separately gated — the item-3 same-hull
   sizing depends on this); **non-adaptive batch sampling** — the wave
   must remain a panel (the item-3 stream framing and the Phase 7 gate
   rationale rest on this; any adaptive acquisition arm would need its
   own stream treatment); the **off-TPE build sampler must be
   independent of the item-3 stream's proposals** (enforceable via the
   per-row acquisition/propensity metadata above) — but process
   independence does not guarantee zero realized overlap in the
   repair-collapsed build space, so the leakage control is an
   **exclusion rule, not just disclosure**: any wave-panel build whose
   build key collides with a scored stream future-block trial is
   (keyed on the same repair-collapsed canonical `Build` identity the
   replay uses to join matchup rows) removed from the augmentation set
   before the paired re-run. The contrast report states the
   post-exclusion residual as a verified hard-zero check **and** the
   pre-exclusion realized-overlap magnitude (count and fraction of
   wave-panel builds excluded) — the latter is the panel↔stream
   similarity diagnostic that disambiguates a null result
   (high overlap ⇒ panel-unhelpful; low overlap ⇒ off-TPE mismatch to a
   TPE stream); the wave's
   oracle/holdout design should support **continuous oracle-value
   regret@k for selection evaluation** (expressible in spec 31's
   existing rank-metrics suite; the stream-side gating version is an
   item-3 oracle-coverage decision, above); and the wave's
   surrogate-improvement claim is measured by a **panel-augmented
   replay** on the item-3 stream — train on the wave panel plus the
   stream prefix, score stream future blocks — as a **paired same-code,
   same-config run of both conditions at item-4 time** on the wave-hull cells
   and cutoffs (item 3's filed unaugmented report is the predeclaration
   anchor and consistency check; the re-run is cheap and, with an empty
   augmentation set, must reproduce that report's computed fields). The
   contrast's **directional bar form** (which statistic's delta, its
   sign, whether a margin exists) is fixed at the item-3 plan gate
   alongside the gate statistic — before item 3's baseline value is
   published — so only the null-result *interpretation*
   (panel-unhelpful vs off-TPE-panel-mismatched-to-a-TPE-stream) is
   settled at item 4;
   **implementing the training-set extension to the replay instrument**
   (spec'd at the same gate; a strict no-op at empty augmentation,
   verified to reproduce item 3's computed fields) and **running the
   paired contrast and filing its report are deliverables of this
   item**. Spec-first through the normal plan gates.
5. **Re-baseline + feature-profile ablations on the new DB** — absorbs the
   staged-but-unlaunched b1/b2 wave
   ([re-groom D1](reports/2026-07-13-roadmap-regroom.md); configs
   `examples/phase7-learned-batch-ablation-b1/-b2.yaml` stay staged; the
   four predeclared primary contrasts carry over from the retired plan).
   Before launch: decide the **RF HPO-budget rebalance** (tail-walltime
   open question — RF is most of fleet compute and loses on build-like
   splits; the replay ran all arms at defaults, so it does not speak to
   HPO's marginal value, but it found no RF variant sustained
   opponent-adjusted signal on future proposals while only CatBoost
   stayed positive across buckets — a prior against spending fleet
   compute on RF tuning, with the tail-walltime report the primary
   driver of the decision;
   [replay evidence](reports/2026-07-14-phase7-prequential-replay.md)).
   Model-family promotion claims from this item onward must cite the
   most recent **stream-based prequential opponent-adjusted fidelity**
   reading for the family (the item-3 stream once it exists; wave-1 for
   families that were wave-1 arms; otherwise the nearest-available
   reading as labelled context, per the spec 31 rule) alongside static
   split metrics, stating the
   designed-panel limitation of the claim's own data (a spec 31 claim
   rule effective 2026-07-14; static build-like metrics alone overstate
   deployment-relevant signal per the replay evidence).
   Delivers the parked M2 **sparse-ID ablation** as predeclared
   contrast 1.
6. **FM / low-rank bilinear interaction features** as a new model family
   (replaces retired sparse-pairwise-ridge), judged on the new DB's
   powered opponent splits. Mandatory comparator: **archetype-cluster-mean
   opponent baseline** before any learned-opponent-representation claim
   ([methodology-gaps §1](reference/phase7-surrogate-methodology-gaps.md)).
   Because this family is promoted on fresh static build-like metrics —
   exactly the case the replay showed overstates deployment signal — its
   promotion deliverable includes **adding FM as a predeclared surrogate
   arm on the item-3 stream and filing its adjacent-bucket T2 reading**,
   satisfying the spec 31 disclosure rule with real evidence rather than
   a no-reading disclaimer.
7. **Within-opponent pairwise-ranking CatBoost** (groups = opponent, pairs
   gapped beyond noise floor) — targets the near-zero top-decile rank
   fidelity, the statistic the optimizer actually exploits
   ([attempt-3 results](reports/2026-07-12-phase7-attempt3-surrogate-results.md)).
   Scope now includes the **H1 two-part censored-target treatment**
   (top-end weakness and the large endpoint mass are two faces of the
   same target problem;
   [methodology review H1](reports/2026-07-11-phase7-methodology-review.md)). Promotion
   deliverable (spec 31 disclosure rule): **land the ranking-family
   planned-panel-score spec amendment first** (spec 31 §"Prequential
   Replay Ablation" requires a ranking arm define its planned-panel
   score before inclusion), then add the family as a predeclared
   surrogate arm on the item-3 stream and file its T2 reading.

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
  the prequential replay
  ([first readings shipped](reports/2026-07-14-phase7-prequential-replay.md);
  the designed wave is a panel, not a stream — this bullet predeclares
  the gate readings' **roles** only: the plain item-3-stream re-run is
  the baseline control and item 4's paired panel-augmented reading on
  the same wave-hull cells is the gate reading, deployment-faithful for
  *incumbent-generated* streams (the MCBO bake-off remains the evidence
  for the BoTorch proposal distribution). The complete gate statistic,
  including its arm, is fixed at the item-3 plan gate before the stream
  is collected; the pinned arm's reading stays primary, and families
  that only mature in items 5–7 enter as labelled sensitivity via the
  spec 31 new-family path, never a post-hoc arm swap. Until a successor
  stream is declared, wave-1 is the designated prior replay stream)
  plus an offline MCBO bake-off
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
