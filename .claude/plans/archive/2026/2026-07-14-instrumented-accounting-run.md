---
plan_type: implementation
status: implemented
created: 2026-07-14
approved: 2026-07-14
implemented: 2026-07-17
owner: agent
related_docs:
  - docs/roadmap.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/specs/24-optimizer.md
  - docs/reports/2026-07-14-phase7-prequential-replay.md
  - .claude/skills/cloud-worker-ops.md
  - docs/reports/2026-07-11-aws-cost-analysis.md
implementation_commit: 3bd03b0
post_impl_audit: passed
superseded_by: null
---

# Instrumented accounting run (roadmap item 3, final prerequisite)

## Goal

Run one **instrumented sim campaign** that (a) resolves the **matchups-per-trial
spread** (including the never-measured **wolf** non-meta hull), and (b) produces
a proposal stream that **doubles as a fresh prequential-replay substrate** for
the Phase-7 go/no-go gate. Its sizing and any oracle-coverage spend are ratified
by the user **at this plan gate** (the D4 pattern) before any launch; this plan
does the no-spend design + code + predeclaration, then stops at the spend gate.

Both no-spend code ports item 3 depends on shipped 2026-07-14 (cost-ledger
`3433b15`, drain `67010c0`); this run is also where those two features get their
first live activation (see §"AMI re-bake").

## Context and source docs

- Roadmap item 3 (`docs/roadmap.md:63-117`) is the normative scope: accounting +
  wolf; replay-double-duty; replay data prerequisites; replay-adequate sizing;
  minimum hammerhead-cell count; **stream-reuse discipline** (predeclared gate
  statistic + git-tracked analysis ledger); **stream oracle coverage** cost
  decision; deliverable ownership (run the replay + file its report); artifact
  retention through item 7.
- Replay data contract: spec 31 §"Prequential Replay Ablation"
  (`docs/specs/31-phase7-matchup-data.md:1162-1412`) + producer
  `scripts/analysis/phase7_prequential_replay.py`. Replay is **local, spends no
  sim budget**; it consumes the frozen matchup DB + source eval logs + study DBs
  under a **total join** (below).
- Shipped replay evidence: `docs/reports/2026-07-14-phase7-prequential-replay.md`
  — zero-regret q\* is lumpy and could not certify the surrogate's gating value
  on the 15-cell wave-1 stream; the report is one of two predeclared inputs to
  the Phase-7 gate. Continuous oracle-value regret is the roadmap's proposed
  discriminating statistic (needs oracle coverage).
- Launch/rebake/sizing SOP: `.claude/skills/cloud-worker-ops.md`; bake
  `scripts/cloud/bake_image.sh`; sweep `scripts/cloud/final_audit.sh`.

### Verified mechanics (2026-07-14 code map)

1. **Matchups-per-trial is largely already logged.** `evaluation_log.jsonl`
   carries per trial: `opponents_evaluated` (scored matchups),
   `opponents_total` (planned panel), `opponent_order`, `opponent_results`, and
   the `pruned`/`cache_hit`/`invalid_spec` kind flags
   (`optimizer.py:1582-1613`). Pruning depth = `opponents_total −
   opponents_evaluated`; cache-hit/invalid-spec trials consume **0** matchups.
   The **one genuine gap**: only *scored* matchups are recorded — matchups
   *dispatched* including `RetryableMatchupError` re-dispatches and
   `InstanceError` (whole-trial `failure_score`) are recorded **nowhere**
   (`optimizer.py:682-700`). No aggregate matchup counter exists per study or
   campaign; no `set_user_attr` is used anywhere in `src/`.
2. **Study DBs carry Optuna-native `datetime_start`/`datetime_complete`** per
   trial — the replay's in-flight-gap input; no custom schema.
3. **Replay join totality** (`phase7_prequential_replay.py:217-253`): the
   `training_matchups` ↔ eval-log join on `(source_path, trial_number)` must be
   **bijective** (either-side orphan → `ValueError`); log `pruned` must equal DB
   `row_kind=="pruned"`; every `source_path` log file and every cell's study DB
   must be present with usable timestamps. Any gap aborts — never a silent drop.
4. **AMI re-bake is mandatory.** The new `active_matchups` field in
   `worker_agent.py` is a `WorkerSourceSha` input (`bake_image.sh:28`), so the
   2026-05-10 AMIs now **fail** the `WorkerSourceSha` preflight
   (`campaign.py:1060-1067`) → launch is blocked until re-bake. Re-baking is
   therefore both required to launch *and* the activation trigger for
   cost-measurement + drain.
5. **Sizing precedent**: the shipped replay stream was 15 cells (5 campaigns × 3
   TPE seeds), 131–205 trials/cell, 1744 finalized + 630 pruned trials — and was
   still only *directional* on the zero-regret gate statistic.
6. **No live cloud cost model** (V1 deleted; V2 pending —
   `cloud-worker-ops.md:526`). Dollar/throughput magnitudes are all "pending V2
   re-validation"; the operative cost control is the mandatory `budget_usd` hard
   cap (`CostLedger` raises `BudgetExceeded` at 100%). This plan asserts **no**
   dollar magnitudes; the user sets `budget_usd` at the gate and the run
   self-limits to it.

## The spend fork (user-ratified at this gate — the D4 decision)

Gate adequacy on the replay is not free. The shipped 15-cell stream could not
discriminate the surrogate gate from the build-blind null under the zero-regret
q\* statistic (too lumpy). Two tiers:

- **Tier 1 — accounting + directional replay (cheaper).** Sim campaign only.
  Resolves the matchups-per-trial spread (hammerhead + wolf) and produces a
  replay stream read *directionally* (T1/T2 fidelity + zero-regret q\*, as the
  shipped run). No honest-eval oracle coverage. The replay reading is explicitly
  directional-only, not gate-adequate.
- **Tier 2 — gate-adequate replay (adds the expensive spend).** Everything in
  Tier 1 **plus** honest-eval oracle coverage of a rank-stratified stream-build
  subset, enabling the **continuous oracle-value-regret** statistic over the
  hammerhead cell subset — the roadmap's proposed discriminating gate statistic.
  This is "the expensive kind of spend" (honest-eval sim budget).

**This plan designs both tiers and their predeclared statistics; the user picks
the tier and the `budget_usd` caps at the gate.** No launch happens inside this
plan.

## Scope

### A. No-spend code + design (lands before any launch)

1. **Matchups-dispatched instrumentation** (`src/starsector_optimizer/optimizer.py`,
   spec 24). Close the one genuine accounting gap — only *scored* matchups are
   logged today (`opponents_evaluated`); matchups **dispatched** (incl.
   `RetryableMatchupError` re-dispatches and instance-error trials that scored
   nothing) are recorded nowhere, and `_dispatched` (`optimizer.py:611`) is a
   membership *set* discarded on completion, not a counter. Add a per-trial
   dispatch counter incremented at dispatch in `_fill_workers`. Record it with
   **exactly one writer per path** (no dual-write — the codebase's "say it once"
   rule):
   - **The four eval-log-bearing terminal kinds** (completed / pruned /
     cache-hit=0 / invalid-spec=0): write `matchups_dispatched` as a new
     **eval-log-row field only**. The extractor reads it from the JSONL.
   - **Instance-error** (`optimizer.py:689-700`): this path emits **no**
     eval-log row today, and adding one would orphan the replay's bijective
     `(source_path, trial_number)` join (`phase7_prequential_replay.py:203-242`
     skips only cache-hit/invalid-spec) → hard `ValueError`. So record its
     dispatched count via an Optuna **`trial.set_user_attr("matchups_dispatched",
     n)` only** — no eval-log row. This is the sole path where a study-DB-side
     record is defensible (no JSONL row exists, and the producer is out of
     scope). Spec-first: amend spec 24 with the eval-log field (four kinds) +
     the instance-error `user_attr`.
2. **Accounting extractor** (`scripts/analysis/`, new). Emits the
   matchups-per-trial spread: distribution of `opponents_evaluated` (scored)
   and `matchups_dispatched` per trial, partitioned by trial kind, per cell and
   pooled; plus the aggregate campaign matchup total (the missing counter, by
   summation). Sources: the eval-log JSONL for the four logged kinds; the study
   DB for **instance-error** trials — identified as `state=COMPLETE` trials with
   **no corresponding JSONL row** (their `failure_score` sentinel is NOT a safe
   discriminator — invalid-spec shares it), reading the `matchups_dispatched`
   `user_attr`. Pure offline analysis; no sim spend. Data input for the
   hand-authored accounting report.
3. **Ĝ in-flight-gap correctness fix** (`scripts/analysis/phase7_prequential_replay.py`,
   `measured_inflight_gap`). Adjacent issue this instrumentation makes fixable
   (principle #2 — address in touched code, don't paper over): instance-error
   trials are `state=COMPLETE` in the study DB, so the in-flight median Ĝ query
   (`where state='COMPLETE'`, ~line 294) **counts** them, yet they are absent
   from the arrival stream (no eval-log row) — a small pre-existing inflation of
   Ĝ. Now that instance-error trials are identifiable (COMPLETE-in-DB, absent
   from the JSONL trial set the replay already loads), exclude them from the Ĝ
   count. This is a narrow correctness fix to the gap *measurement*, not a change
   to the replay's ranking/gating algorithm or its data contract. Test pins that
   an errored-COMPLETE trial no longer contributes to Ĝ.
4. **Stream-reuse predeclaration + ledger** — a **dated pre-registration report
   companion** `docs/reports/2026-<MM-DD>-accounting-stream-preregistration.md`
   (a CONVENTIONS-compliant home + indexed in `docs/reports/INDEX.md`; NOT a
   loose `docs/` root file, NOT the gitignored frozen-DB dir), authored and
   committed **before** the stream is collected. It predeclares the **complete
   gate statistic** for each tier — statistic type, arm, cell scope, cutoffs,
   aggregation — plus the Tier-2 **oracle-coverage subset-selection rule**
   (rank-stratified under the predeclared arm), so neither coverage nor the
   statistic can be chosen after readings exist, and states that comparability
   with the shipped run requires the **same loader-skip / exclusion semantics**
   (no instance-error rows on either side). No model-selection re-run consults
   the stream before this predeclaration; later new-family re-runs append as
   ledger entries. Predeclared values (subject to plan review):
   - **Tier 1 statistic**: T1/T2 opponent-adjusted fidelity + zero-regret median
     q\* with the `opponent_mean` null alongside (spec 31 defaults: cutoffs from
     `min_train_trials`, stride `cutoff_stride`, `min_future_trials`;
     aggregation = median over hammerhead cells; the wolf cell is directional
     only). Mirrors the shipped run so the two streams are comparable.
   - **Tier 2 statistic** (only if the user funds coverage): continuous
     oracle-value regret@k under the CatBoost opponent-adjusted arm, aggregated
     as median over the hammerhead cell subset; **subset-selection rule**:
     rank-stratified sample of stream builds under that same arm, fixed here.
5. **Cell-design campaign YAMLs** (`examples/`, new) — **two files**, one hull
   each (the repo's one-hull-per-campaign precedent — `wave1-c0a.yaml`,
   `wave2-wolf-early.yaml` — and so each gets its **own `budget_usd` cap**, so
   the directional wolf block can't starve behind the gate-hammerhead block on a
   shared cap):
   - `examples/<accounting-hammerhead>.yaml` — a **hammerhead / early** block of
     `HAMMERHEAD_CELL_MIN` cells (proposed below, user-ratified) at **full-study
     trial counts** so each cell is replay-rankable.
   - `examples/<accounting-wolf>.yaml` — a **wolf / early** block for the
     accounting measurement (directional replay only; frigate → smaller opp
     pool, faster TTK).
   All studies `sampler: tpe`; `active_opponents` at the production default (not
   the smoke `1`) so the opponent panel matches wave-1 and the replay's
   panel-composition semantics hold. Seeds are **fresh** (151 is spent) — N
   campaigns × 3 fresh seeds, the specific reserved seeds enumerated in the
   predeclaration ledger, not reused from a prior run. `budget_usd` left as a
   placeholder the user sets per file at the gate.
6. **Replay-input retention wiring** (design + doc). Verify the run writes eval
   logs to `data/logs/<campaign>/…` and study DBs to
   `data/study_dbs/<campaign>/<study>.db` (the paths the replay producer
   expects), and that each eval-log row carries `opponent_order` (all trials
   incl. pruned) and `covariate_vector` (**finalized/completed trials only** —
   the prune path omits it and the replay tolerates `None`;
   `optimizer.py:1133-1151`, spec 31:1184-1186). Document the **artifact
   retention obligation**: the frozen matchup DB + eval logs + study DBs are
   retained, re-runnable, through item 7 (items 6/7 re-fit the instrument
   offline on them). Note the actual hazard is narrow: the AWS cleanup tooling
   (`final_audit.sh`, `cleanup_amis.sh`, `teardown.sh`) reaps **only AWS
   resources**, never `data/`; the residual risk is a manual `data/` cleanup or
   disk loss over the multi-item horizon (`data/` is gitignored). Add a
   lightweight **retained-paths manifest** (a committed list of the item-3
   artifact paths under `data/`) so the retention obligation is machine-checkable
   rather than prose-only.

### B. Launch runbook (spend-gated — executed only after user ratifies)

7. **AMI re-bake + pre-launch gates** (runbook in the plan, not auto-run):
   `bake_image.sh` (the drain commit `67010c0` is already committed, so the
   checkout is clean for the `WorkerSourceSha` gate — but re-bake after Scope A
   commits so the new instrumentation is baked in too) → paste new
   `ami_ids_by_region:` into the two new YAMLs → `audit_amis.sh` +
   `cleanup_amis.sh` for superseded AMIs → `campaign --dry-run` → `probe.sh` →
   smoke campaign → `final_audit.sh` clean (stale-resource sweep, mandatory) →
   spot-quota check. Post-impl audit of the code changes (Scope A) is the other
   standing gate.
8. **The run itself** (`launch_campaign.sh <hammerhead.yaml>` and
   `<wolf.yaml>`), and — Tier 2 only — the honest-eval oracle-coverage pass over
   the predeclared rank-stratified subset.
9. **Frozen matchup-DB materialization + join-totality verify** (before the
   replay): run `scripts/analysis/phase7_materialize_matchups.py` over the fresh
   stream's eval logs to build `training_matchups`/`recovered_builds`
   (+ `honest_eval_matchups` for the oracle'd subset under Tier 2), then verify
   the bijective `(source_path, trial_number)` join is total against the eval
   logs (the producer raises on any orphan) — the materialize→verify→replay
   chain the replay depends on.
10. **Deliverables** (owned by this item): the accounting report (matchups-per-
    trial spread + wolf); running the prequential replay on the fresh materialized
    stream + filing its report; the committed pre-registration ledger + its later
    consultation entries.

## Proposed sizing (user ratifies at the gate)

Grounded in the 15-cell shipped precedent; **these are proposals for
ratification, not decisions**:

- `HAMMERHEAD_CELL_MIN` = **9** hammerhead/early cells (3 campaigns × 3 fresh
  seeds), matching the shipped per-hull cell density, at full-study trial counts
  (~250 trials/cell, the wave-1 depth) so each cell is replay-rankable.
  **Reinterpretation flagged for ratification (finding F4):** roadmap:83-88
  literally frames the plan gate as setting "the minimum cell count for gate
  adequacy." The evidence (shipped 15-cell parity of q\* vs null;
  "larger k needs longer streams", report §4) says raw cell count does **not**
  buy discrimination under the zero-regret statistic — so this plan locates gate
  adequacy in **Tier-2 oracle coverage** (the continuous statistic), not cell
  count. `HAMMERHEAD_CELL_MIN` is set to be replay-*rankable* and comparable to
  the shipped run, not self-sufficient for gate adequacy. The reviewer/user
  ratifies this reading. If gate adequacy without Tier-2 spend is preferred, the
  only sim-only alternative is a materially larger cell count / longer streams —
  the more expensive path.
- **Wolf**: 1–3 wolf/early cells (accounting focus; directional replay only).
- Fleet: per-study workers ≤ 24 (TPE saturation → ~12 VMs/study),
  `matchup_slots_per_worker=2`; workers buy walltime, not total cost (cost is
  ~constant in matchup count and capped by `budget_usd`) — the honest-eval
  fleet-sizing principle, applied to the optimizer campaign.
- `budget_usd`: user sets the hard cap per campaign at the gate. No dollar
  figure is asserted here (V2 cost model pending).

## Out of scope

- The **designed data wave** (item 4) — this run is a *stream*, not the balanced
  panel; cross-hull and novel-build claims stay gated behind item 4.
- Any **cost-model rebuild** (V2 deliverable) — the run self-limits via
  `budget_usd`; this plan does not resurrect the deleted estimator.
- **Multi-hull gate claims** — gate adequacy is defined only over the hammerhead
  subset; wolf is accounting + directional.
- Changing the **replay producer's ranking/gating algorithm or data contract** —
  `phase7_prequential_replay.py` stays as-is except the **narrow Ĝ correctness
  fix** (Scope A.3), which changes only the in-flight-gap *measurement*, not the
  contract or algorithm; this plan otherwise produces inputs that satisfy its
  existing contract.
- **Executing** the launch / oracle coverage — deferred to the spend gate.

## Critical files

- `src/starsector_optimizer/optimizer.py` — new per-trial dispatch counter;
  `matchups_dispatched` eval-log field (four logged kinds) + instance-error
  `user_attr`.
- `docs/specs/24-optimizer.md` — spec the new field + user_attr.
- `scripts/analysis/phase7_prequential_replay.py` — Ĝ in-flight-gap fix
  (exclude instance-error COMPLETE trials).
- `scripts/analysis/<accounting_extractor>.py` — new offline extractor.
- `examples/<accounting-hammerhead>.yaml`, `examples/<accounting-wolf>.yaml` —
  two new one-hull campaigns (independent `budget_usd`).
- `docs/reports/2026-<MM-DD>-accounting-stream-preregistration.md` — git-tracked
  pre-registration ledger (indexed in `docs/reports/INDEX.md`).
- a committed **retained-paths manifest** for the item-3 `data/` artifacts.
- `tests/test_optimizer.py`, `tests/test_phase7_prequential_replay.py`,
  `tests/test_<extractor>.py` — tests.
- `docs/roadmap.md` — close item 3 on retirement.
- Scope-B runbook only (not edited here): `scripts/cloud/bake_image.sh`,
  `scripts/analysis/phase7_materialize_matchups.py`, `launch_campaign.sh`.

## Public concepts and canonical owners

- `matchups_dispatched` per-trial field — spec 24.
- Replay data contract / join totality — spec 31 (unchanged; this run obeys it).
- Predeclared gate statistic + oracle-coverage subset rule — the git-tracked
  stream-reuse ledger (named in the eventual report).
- All dollar/throughput magnitudes — dated reports only; none asserted here.

## Implementation sequence

1. Amend spec 24 (matchups-dispatched eval-log field + instance-error user_attr).
2. Write failing tests for the instrumentation + extractor + Ĝ fix; implement to
   green (one concern per change).
3. Author the two cell-design YAMLs + the pre-registration ledger + the
   retained-paths manifest.
4. Full gates + post-impl audit of Scope A.
5. **Stop at the spend gate**: present the tier fork + sizing + `budget_usd` +
   oracle-coverage decision to the user for ratification. Only after ratification
   does the Scope B runbook execute (re-bake → gates → launch → materialize →
   verify join totality → replay → report).

## Tests and mechanical gates

- Instrumentation: per-trial dispatch counter equals dispatched count incl.
  retries; `matchups_dispatched` written to the eval-log row on the four logged
  kinds (completed / pruned / cache-hit=0 / invalid-spec=0) and to the
  **`user_attr` only** on instance-error (assert NO new eval-log row is emitted
  there — the replay-join guard).
- Ĝ fix: an errored-COMPLETE trial does not contribute to `measured_inflight_gap`.
- Extractor: correct per-kind partition; pruning-depth = total − evaluated;
  aggregate total = Σ dispatched; instance-error identified as COMPLETE-in-DB
  minus JSONL (not via the ambiguous `failure_score` sentinel); wolf vs
  hammerhead cell separation.
- `uv run pytest tests/ -q`; `uv run ruff check . && uv run ruff format --check .
  && uv run mypy && uv run deptry .`; `uv run python scripts/validate_docs.py`.
- design-invariants: no magic numbers (cell counts are named plan constants /
  YAML config; no bare literals in code), manifest-as-oracle untouched.

## Review findings and dispositions

Consolidated across the three fresh-eye auditors (S=spec/replay, E=engineering,
P=pattern; dedup'd). All folded before approval.

1. **F1/E1-2/P1-3 — HIGH — instance-error instrumentation collided with the
   replay's bijective join, and the `set_user_attr` dual-write was scope creep.**
   The draft mirrored `matchups_dispatched` into an eval-log row on *every* path
   including instance-error — but instance-error emits no row today, and a new
   one orphans the replay join (`ValueError`); and for the four logged kinds a
   `user_attr` alongside the JSONL field is a reader-less dual-write.
   **Fixed:** JSONL field only on the four logged kinds; `user_attr` **only** on
   instance-error (no eval-log row); extractor reads instance-error from
   study-DB-minus-JSONL (Scope A.1-A.2, Tests).
2. **E4/P (framing) — `matchups_dispatched` needs real new per-trial state.**
   `_dispatched` is a set discarded on completion; *scored* matchups are logged,
   *dispatched* are not. **Fixed:** Scope A.1 adds an explicit per-trial dispatch
   counter in `_fill_workers`; the "largely already logged" framing corrected to
   apply to scored only.
3. **E3 — MEDIUM — pre-existing Ĝ inflation on the path this change touches.**
   Instance-error trials are `state=COMPLETE` so they inflate the replay's
   in-flight median yet are absent from the stream. **Fixed in scope** (not
   deferred): Scope A.3 excludes them from `measured_inflight_gap` — a narrow
   measurement correctness fix, not an algorithm/contract change; out-of-scope
   wording adjusted; test pinned.
4. **P4 — MEDIUM — one-vs-two YAML contradiction + shared-budget starvation.**
   **Fixed:** two one-hull campaigns with independent `budget_usd` (Scope A.5,
   Critical files).
5. **S-F2/P5 — MEDIUM — `covariate_vector` overstated as on all trials.**
   **Fixed:** finalized-only for covariate, all-trials for opponent_order; replay
   tolerates `None` (Scope A.6).
6. **S-F3 — MEDIUM — frozen-DB materialization step unnamed.** **Fixed:**
   Scope B.9 names `phase7_materialize_matchups.py` + a materialize→verify
   join-totality→replay chain.
7. **P6 — MEDIUM — ledger placement had no CONVENTIONS home.** **Fixed:** dated
   `docs/reports/` pre-registration companion, indexed (Scope A.4, Critical
   files).
8. **E6 — LOW — retention hazard overstated.** **Fixed:** the AWS cleanup tools
   never touch `data/`; language corrected; a committed retained-paths manifest
   added for the real residual (manual/disk loss) (Scope A.6).
9. **S-F4 — LOW — "minimum cell count for gate adequacy" reinterpreted.**
   Dispositioned: the reframe (gate adequacy = Tier-2 coverage, not cell count)
   is evidence-backed and surfaced explicitly for user/reviewer ratification
   (Proposed sizing).
10. **P7 — LOW — seed wording.** **Fixed:** "N campaigns × 3 fresh seeds", seeds
    enumerated in the ledger (Scope A.5).

Affirmed by auditors (no change): spend-gate integrity (Scope A no-spend / Scope
B gated / sequence stops at the gate, AMI re-bake behind the gate); predeclaration
anti-forking integrity (git-tracked ledger committed before collection; Tier-2
subset rule fixed pre-collection); no dollar magnitudes asserted; no-magic-numbers.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14 (self-review phases 1–4)
- Findings: phases 1–4 clean after verifying spec-first ordering, no dollar
  magnitudes, no-magic-numbers (cell counts are named constants / YAML config),
  and the spend-gate separation. The self-review flagged the `matchups_dispatched`
  proportionality question, which the fresh-eye lane then sharpened into the F1
  cluster (folded).
- Dispositions: see "Review findings and dispositions".
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14
- Agents:
  - Pattern Consistency: findings (7) — all resolved (items 1, 2, 4, 7, 10 above)
  - Spec/Replay Alignment: findings (4) — all resolved (items 1, 5, 6, 9 above; item-3 sub-requirement coverage confirmed)
  - Engineering & Design Invariants: findings (6) — all resolved (items 1, 2, 3, 8 above; spend-gate + predeclaration integrity affirmed PASS)
- Findings: see "Review findings and dispositions".
- Dispositions: all consolidated findings fixed in scope or explicitly
  dispositioned; the one HIGH (F1 cluster) resolved by the JSONL-only +
  instance-error-user_attr redesign; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- post-impl-audit over the Scope-A diff (instrumentation + extractor + YAMLs +
  ledger).
- Confirm `matchups_dispatched` is set on EVERY terminal trial path (no path
  leaves it unset) and equals dispatched incl. retries, by test.
- Confirm the run's eval-log + study-DB output paths satisfy the replay
  producer's join-totality contract (dry-run / fixture check), before launch.
- Spend gate: sizing + tier + `budget_usd` + oracle-coverage explicitly ratified
  by the user before any `launch --execute`; stale-AWS-resource sweep clean.

## Scope-A implementation record (2026-07-14)

Scope A (no-spend code + design) is **implemented, verified, and audited**;
Scope B (the launch + oracle coverage) remains **pending user spend
ratification** at the gate, so this plan stays active until the run lands.

Delivered: spec 24 amendment (`matchups_dispatched` on the four logged kinds +
the instance-error `user_attr`, "the fifth path"); the per-trial dispatch
counter in `optimizer.py` (`_InFlightBuild.matchups_dispatched`, incremented in
`_fill_workers`, written on completed/pruned, defaulted 0 on cache-hit/invalid-
spec, `set_user_attr` on instance-error with no eval-log row); the Ĝ
in-flight-gap fix in `phase7_prequential_replay.py`; the `accounting_extract.py`
offline extractor; `examples/accounting-hammerhead.yaml` + `accounting-wolf.yaml`
(re-bake-placeholder AMIs, independent `budget_usd`); the pre-registration ledger
+ retained-paths manifest (`docs/reports/2026-07-14-accounting-stream-preregistration.md`).

Three independent post-impl auditors (plan-vs-code, engineering/correctness,
spec/docs): **no correctness bugs, no invariant violations, no scope creep**;
all four required verifications confirmed (field set on every terminal path;
retries counted; instance-error emits no eval-log row; Ĝ fix correct, no
over-exclusion). Folded findings:

1. **LOW (plan-vs-code) — retry counter value not asserted end-to-end.** Added
   `test_retry_is_counted_in_matchups_dispatched` (asserts a retried trial's
   logged `matchups_dispatched` exceeds `opponents_evaluated` by the retry
   count).
2. **LOW (correctness) — instance-error `opponents_evaluated=0` under-documented.**
   Added a docstring clause: 0 is the corpus useful-work count (scored-then-lost
   matchups never enter the corpus); dispatched is the exact cost basis.

Dispositioned (no change): the hammerhead YAML realizes the 9 cells as 1
campaign × 9 fresh seeds (vs the sizing note's illustrative "3 × 3") — same 9
`(campaign, seed)` replay cells, noted in the YAML; the Ĝ query's pre-existing
exclusion of in-flight *pruned* trials is out of scope for this narrow fix
(confirmed intended — the gap models finalized-data availability).

Verification: full suite **1175 passed, 1 skipped**; ruff/format/mypy/deptry/
validate_docs all green.

## Scope-B spend-gate ratification (2026-07-15)

The user ratified **Package B — Tier 2** at the plan gate:

- **Tier**: 2 (accounting + directional replay **+** honest-eval oracle coverage
  enabling the continuous oracle-value-regret gate statistic over the hammerhead
  subset).
- **`budget_usd` caps** (written into the YAMLs; conservative, directional
  pending V2): `accounting-hammerhead.yaml` = **$72**, `accounting-wolf.yaml` =
  **$19**.
- **Oracle coverage K = 3 builds/cell** — realized as the entry-0 rank-stratified
  rule: 3 predicted-rank strata (CatBoost opponent-adjusted tertiles) × 1 build
  per stratum per cell → 27 oracle-covered builds (9 hammerhead cells). Recorded
  as ledger **entry 1** (`docs/reports/2026-07-14-accounting-stream-preregistration.md`),
  committed before any stream exists. Wolf: no oracle coverage.
- Estimate basis: 2026-07-15 tier/budget brief (Tier-1 realized ~$23-32; Tier-2
  K=3 add-on ~$23-32; totals ~$46-63 realized). All directional.

**Remaining before spend** (Scope B runbook — each an explicit AWS/billing action
requiring the user's `--execute` go-ahead): AMI re-bake → paste AMI ids into both
YAMLs → `audit_amis.sh`/`cleanup_amis.sh` superseded → `--dry-run` → `probe.sh`
→ smoke → `final_audit.sh` clean (mandatory stale-resource sweep) → spot-quota
check → `launch_campaign.sh` both YAMLs → honest-eval oracle pass (set its
`budget_usd` at launch) → materialize → verify join totality → replay → reports.

## Scope-B delivery record (2026-07-17)

Scope B (launch + Tier-2 oracle coverage + deliverables) is **complete**. The
9-cell hammerhead + 3-cell wolf stream was collected (task #73–74), the frozen
matchup DB materialized with bijective-join verify (task #77), the 27-build
rank-stratified oracle pass ran on maintain-mode fleet to full 1,620/1,620
coverage per build (task #76), the frozen DB was re-materialized with
`--honest-ledger` + `--honest-selector-json` (`honest_eval_matchups` 43,740,
0 unresolved; `training_matchups` byte-identical to the pre-oracle
materialization — checksum-verified), and the Tier-2 prequential replay was run
under the predeclared statistic.

**Deliverables filed** (2026-07-17): the accounting report
([2026-07-17-accounting-matchup-spread.md](../../../docs/reports/2026-07-17-accounting-matchup-spread.md)),
the oracle-value replay report
([2026-07-17-phase7-oracle-value-replay.md](../../../docs/reports/2026-07-17-phase7-oracle-value-replay.md)),
and pre-registration ledger entry 3
([2026-07-14-accounting-stream-preregistration.md](../../../docs/reports/2026-07-14-accounting-stream-preregistration.md)).

**Reading**: the Tier-2 oracle coverage did **not** certify the surrogate — it
confirmed the shipped "gating value not established" against an independent
oracle (CatBoost-vs-oracle Spearman ≈0 among deployable builds; gating q\* =
the build-blind null; T2 drift reproduces). The one positive oracle signal is
the TWFE α̂ **target** (+0.5, marginal), not the surrogate.

Two follow-ups surfaced (both filed, neither blocks retirement): (a) a spec-31
amendment naming an explicit gating-arm oracle-regret statistic so future
pre-registrations bind to a tool output verbatim (the entry-0 "oracle-value
regret@k under the CatBoost arm" phrase did not map verbatim; realized three
faithful, direction-agreeing ways instead); (b) hash-seed nondeterminism in
study-DB build reconstruction (unreferenced lookup builds only; no result
affected; frozen DB pinned to `PYTHONHASHSEED=0`).

## Retirement checklist

- [x] Scope A: implemented + audited (`3bd03b0`).
- [x] Scope B: launched, oracle'd, re-materialized, replayed, reports filed
      (this record, 2026-07-17).
- [x] status: implemented; implemented 2026-07-17; implementation_commit `3bd03b0`.
- [x] Roadmap: item-3 accounting bullet → delivered note; item 3 closed.
- [x] Archive to `.claude/plans/archive/2026/`.
