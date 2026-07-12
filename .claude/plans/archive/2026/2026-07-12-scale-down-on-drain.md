---
plan_type: implementation
status: implemented
created: 2026-07-12
approved: 2026-07-12
implemented: 2026-07-12
owner: agent
related_docs:
  - docs/specs/22-cloud-deployment.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-07-12-phase7-tail-walltime.md
  - docs/roadmap.md
implementation_commit: f28339d (+ audit closure in the retirement commit)
post_impl_audit: passed
superseded_by: null
---

# Scale-down-on-drain + longest-expected-first dispatch (learned batch)

## Goal

Eliminate the learned-batch idle drain tail (measured as a material share of
attempt-3 spend — magnitudes in the
[tail-walltime report](../../../docs/reports/2026-07-12-phase7-tail-walltime.md))
by (a) terminating each worker at queue-empty, and (b) dispatching jobs
longest-expected-first (LPT scheduling) so the makespan shrinks. This is the
roadmap follow-up gated before the item-2 feature-profile ablation wave.

## Context and source docs

- Evidence: `docs/reports/2026-07-12-phase7-tail-walltime.md` — idle tail
  ≈14% of spend at 36 workers; tuned RF supplies the entire scheduling tail;
  LPT dispatch worth ≈11.5% walltime at the same fleet size.
- Contract owners: spec 22 §"One-shot AWS batch runners" (cloud lifecycle,
  control-plane auth, UserData security — item 8 of that section, teardown),
  spec 31 §"Learned AWS Batch Artifacts" (Phase 7 job matrix and artifact
  semantics).
- Fleet mechanics verified: `AWSProvider._create_fleet_in_region` uses
  `Type="instant"` (one-shot; AWS does NOT respawn self-terminated
  instances), and spot instances terminate on OS `shutdown -h now` — the
  same mechanism the worker script already trusts on every failure path.

## Named trade-off (accepted)

Post-drain spot-reclaim recovery latency changes class. Before: a late
reclaim is absorbed by a warm idle worker within one poll. After: it costs
`lease_grace_seconds` + a full replacement bootstrap (provision, tailscale,
bundle, `uv sync` — minutes). Accepted because the measured idle-tail spend
is per-run-certain while late reclaims are rare, and correctness is
unaffected (requeue → replacement machinery). This trade-off is stated in
the spec 22 amendment, not left implicit.

## Scope

1. **Longest-expected-first dispatch** (`src/starsector_optimizer/phase7_learned_batch.py`)
   - New module constants `DISPATCH_MODEL_RANK` / `DISPATCH_SPLIT_RANK`:
     designed ranking with provenance comment pointing at the tail-walltime
     report. Evidence scope stated honestly: model family dominates duration
     everywhere; the within-family split ranking (opponent-hierarchy splits
     first) is evidence-backed within `random_forest_tuned` only and acts as
     a harmless tie-break for the other families. Unknown model/split ranks
     first — labeled forward-compat defense (unreachable through a validated
     config today, since `validate_batch_config` rejects unknown values;
     protects future families before a measured ranking exists).
   - New `order_jobs_for_dispatch(jobs) -> tuple[LearnedBatchJob, ...]`:
     stable sort by `(model_rank, split_rank)`; ties keep canonical
     `generate_jobs` order. Pure permutation — job IDs, output paths, and
     `merge_job_artifacts` (which re-derives from `generate_jobs`) are
     untouched. Operator-visible ordering divergence: `status.json` job rows
     will appear in dispatch order; the CLI `--dry-run` job listing in
     `scripts/cloud/phase7_learned_batch.py` is switched to
     `order_jobs_for_dispatch` so the operator view matches the actual queue.
   - `run_live_batch` constructs `BatchState(order_jobs_for_dispatch(jobs), ...)`.
     `BatchState.lease` already hands out jobs in insertion order (and
     requeued jobs keep their position, preserving LPT priority), so no
     lease-path change is needed.

2. **Scale-down-on-drain**
   - Control plane `/lease`: when `state.lease()` returns `None` (no pending
     job now), respond `200 {"status": "drained"}` instead of `204`. The
     server never emits 204 for an empty queue after this change (spec 22
     amendment says so explicitly); policy is server-driven and the worker
     obeys mechanically.
   - Worker user-data template: in the 200 path, check the drained verdict
     BEFORE any job-field parse (a drained body reaching the `job_id` parse
     would KeyError into the ERR trap and mislabel the exit as
     `worker_failed`). On drained: `post_worker_event "worker_drained"`,
     then `shutdown -h now` and exit 0. Keep the 204/empty-body branch as a
     defensive sleep-retry for transport hiccups (curl `|| true` yields an
     empty body), so only an explicit drained verdict terminates the worker.
   - Boy-scout (same idle-waste class, same template): the
     `worker_deadline_too_close_for_new_job` branch currently `break`s and
     idles until the bootstrap-scheduled `shutdown -h +N`; add an immediate
     `shutdown -h now` after its event post.
   - Requeue-after-drain is covered by existing machinery: a spot-reclaimed
     leased job requeues after `lease_grace_seconds`, and replacement
     provisioning (`replacement_count` + `counts["pending"]` gate) boots a
     fresh worker for it. A replacement that boots after another worker
     already took the job gets drained and self-terminates — bounded waste,
     correct behavior.

3. **Monitor-loop drain-compatibility fixes** (`run_live_batch`)
   - Guard fix: `if not active and not pending_instance_ids and
     counts["completed"] < len(jobs): raise` becomes reachable-in-error once
     idle workers self-terminate (last busy workers spot-reclaimed while
     every drained worker is already gone → `active == []` during the
     lease-grace window). Fix: also require `not counts["leased"]` before
     raising — leased jobs waiting out their grace are a recoverable state.
     This is the monitor-side counterpart of spec 31's "a single stale
     active-instance snapshot is not sufficient to steal a lease".
   - Exception-provenance correction (plan-review finding): `provision_fleet`
     raises provider exceptions, not `BatchLaunchFailed`; and a replacement
     `provision_fleet` that returns an EMPTY list without raising is caught
     by the amended guard itself on the next iteration (no active, no
     pending IDs, `leased == 0`, pending jobs remain). The guard's message is
     extended to include the pending/leased counts so a zero-capacity
     provisioning outcome is diagnosable as capacity failure rather than
     worker loss.
   - Drained-worker reconciliation of `pending_instance_ids`: a replacement
     that boots, drains, and self-terminates entirely between two monitor
     polls never appears in `list_active` (the filter is pending|running),
     so its entry would hit `pending_instance_grace_seconds` and abort the
     batch spuriously. Fix: the `/worker-event` endpoint records
     `worker_drained` instance IDs in `BatchState` (new thread-safe
     `record_worker_drained(worker_id)` / `drained_worker_ids()`); the
     monitor pops drained IDs from `pending_instance_ids` each iteration.
   - Finish-line ordering: evaluate the `counts["completed"] == len(jobs)`
     merge-and-return before the expired-pending raise, so a stale pending
     entry cannot abort a batch whose jobs are all done.
   - Boy-scout comment fix: `validate_batch_config`'s worker-cap rationale
     ("a fleet larger than the job count would idle instantly") is updated —
     under drain such workers self-terminate instantly; the cap remains as a
     provisioning-waste guard.

4. **Spec amendments** (spec-first)
   - Spec 22 §"One-shot AWS batch runners": add invariant 9 —
     scale-down-on-drain: drained lease verdict (200 `{"status": "drained"}`;
     204 no longer emitted for an empty queue), worker self-shutdown via the
     trusted `shutdown -h now` path with a `worker_drained` event first,
     instant-fleet no-respawn precondition, drained-ID reconciliation against
     pending-instance accounting, leased-in-grace monitor guard semantics,
     and the named recovery-latency trade-off.
   - Spec 31 §"Learned AWS Batch Artifacts": dispatch order is
     longest-expected-first by the designed model×split ranking (family rank
     evidence-backed across the matrix; split rank evidence-backed within
     tuned RF, tie-break elsewhere; unknown values are forward-compat
     defense). Ranking provenance lives in the dated report
     (empirical-numbers rule).

## Out of scope

- Provider-side per-instance termination (`CloudProvider` ABC extension).
  Rejected: redundant with worker self-shutdown, which every failure path
  already uses and the attempt-1 incident validated; an ABC extension would
  touch all providers for a belt-and-suspenders no-op.
- Dynamic duration estimation (per-job predicted runtimes). The static
  family×split ranking captures the dominant effect (RF ≈ 3× other
  families); revisit only if a future wave shows the static ranking failing.
- Honest-eval path scale-down (different orchestrator; separate roadmap item).
- Config-level dispatch-rank overrides. The ranks are designed policy
  constants (precedent: `SEED_AGGREGATE_METRIC_PATHS`,
  `DEFAULT_RESULT_UPLOAD_ATTEMPTS`); a per-config override would invite
  silent divergence from the report-backed ranking. Add only when a wave
  actually needs a different ranking.

## Critical files

- `src/starsector_optimizer/phase7_learned_batch.py` — dispatch constants +
  `order_jobs_for_dispatch`, `/lease` drained response, `BatchState`
  drained-worker tracking, user-data template drained branch +
  deadline-branch shutdown, `run_live_batch` guard/ordering fixes,
  `validate_batch_config` comment fix.
- `scripts/cloud/phase7_learned_batch.py` — dry-run listing in dispatch order.
- `tests/test_phase7_learned_batch.py` — new/extended tests (below).
- `docs/specs/22-cloud-deployment.md`, `docs/specs/31-phase7-matchup-data.md`.
- `docs/roadmap.md` — close the follow-up item on retirement.

## Public concepts and canonical owners

- "Scale-down-on-drain" (lifecycle invariant) — spec 22.
- "Longest-expected-first dispatch" (job-matrix semantics) — spec 31.
- Measured magnitudes — the 2026-07-12 tail-walltime report only.

## Implementation sequence

1. Amend specs 22 and 31.
2. Write failing tests:
   - `test_order_jobs_for_dispatch_longest_expected_first` — RF-tuned before
     catboost before ridge; within RF, opponent-hierarchy splits first;
     unknown families/splits first (forward-compat defense, direct-call
     only); output is a permutation; ties stable.
   - `test_lease_returns_drained_verdict_when_queue_empty` — Flask client
     leases the whole matrix; next `/lease` → `200 {"status": "drained"}`
     (bearer auth still required).
   - `test_run_live_batch_dispatches_longest_expected_first` — `on_poll`
     leases two jobs from a small mixed-model matrix and asserts the RF job
     is handed out first.
   - `test_user_data_shuts_down_on_drained_lease` — rendered script posts
     `worker_drained` then shuts down; the drained-body check precedes the
     `job_id` field parse in the 200 path (assert on substring indices), and
     the deadline-too-close branch also shuts down immediately.
   - `test_run_live_batch_waits_for_leased_grace_when_no_active_workers` —
     active list goes empty while a job is leased and unexpired: no
     `BatchLaunchFailed`; after grace, requeue + replacement completes the
     batch.
   - `test_run_live_batch_raises_when_replacement_provisioning_yields_nothing`
     — positive guard fire: replacement `provision_fleet` returns `[]`,
     nothing active/pending/leased, pending jobs remain → `BatchLaunchFailed`
     whose message names the pending/leased counts.
   - `test_worker_drained_event_reconciles_pending_instances` — a
     provisioned-but-never-active instance that posts `worker_drained` is
     removed from pending accounting and does not trip the pending-grace
     abort; and a batch whose jobs all complete merges even with a stale
     pending entry (finish-line ordering).
3. Implement to green, one concern per change; run
   `uv run pytest tests/test_phase7_learned_batch.py` after each concern.
4. Full gates + post-impl audit + roadmap grooming + plan retirement.

## Tests and mechanical gates

- `uv run pytest tests/test_phase7_learned_batch.py -v`, then full
  `uv run pytest tests/ -q`.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run deptry .`
- `uv run python scripts/validate_docs.py`.
- design-invariants: no magic numbers in function bodies (ranking tables are
  module-level designed constants), manifest-as-oracle untouched.

## Review findings and dispositions

Consolidated from the three fresh-eye auditors (dedup'd; A=invariants,
B=pattern, C=spec):

1. **A1 — stale `pending_instance_ids` from a short-lived drained
   replacement can spuriously abort; abort outranks completion.** Fixed in
   scope: drained-ID reconciliation via `worker_drained` +
   `BatchState.record_worker_drained`, and the completed-merge return is
   evaluated before the expired-pending raise (Scope 3).
2. **A2 — drained verdict is HTTP 200; the load-bearing requirement is
   drained-check-before-job-field-parse.** Fixed: script + test assert the
   semantic directly (Scope 2, test list).
3. **A3/B2/C2 — guard-fix rationale misattributed the failure surface for an
   empty replacement-provisioning return; no positive guard test.** Fixed:
   rationale corrected (the amended guard itself catches it next iteration),
   guard message extended with counts, positive-fire test added (Scope 3,
   test list).
4. **A4 — post-drain reclaim recovery-latency trade-off unstated.** Fixed:
   named trade-off section added; carried into the spec 22 amendment.
5. **A5 — dispatch ranks: module constants vs config-dataclass invariant.**
   Dispositioned: designed policy constants with report provenance
   (precedent `SEED_AGGREGATE_METRIC_PATHS`); config override explicitly
   out of scope with reason.
6. **B1/C1 — spec 22 section mis-citation.** Fixed: all references now name
   §"One-shot AWS batch runners" (UserData security = item 8).
7. **B4 — `worker_deadline_too_close_for_new_job` idles until scheduled
   shutdown.** Fixed in scope (boy-scout): immediate shutdown on that branch.
8. **B5/C6 — status.json ordering changes; CLI dry-run shows canonical
   order.** Fixed: dry-run switched to dispatch order; divergence named in
   the plan.
9. **B6/C3 — unknown-rank behavior unreachable via validated config.**
   Fixed: labeled forward-compat defense in plan/spec/comment.
10. **B7 — stale worker-cap comment in `validate_batch_config`.** Fixed in
    scope (boy-scout comment update).
11. **C4 — spec must state 204 is no longer emitted for an empty queue.**
    Fixed: explicit in the spec 22 amendment text (Scope 4).
12. **C5 — split-rank evidence is RF-specific.** Fixed: evidence scope
    stated in Scope 1 and the spec 31 amendment.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 (self-review phases 1–4)
- Findings: phases 1–4 self-review clean after verifying: no test pins the
  204 response; `complete_all_jobs` is lease-order-independent; module-level
  designed constants satisfy the no-magic-numbers invariant.
- Dispositions: see "Review findings and dispositions" (items from the
  fresh-eye lanes were folded into scope before approval).
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12
- Agents:
  - Pattern Consistency: findings (7) — all resolved (items 3, 6–10 above)
  - Spec Alignment: findings (6) — all resolved (items 3, 6, 8, 9, 11, 12 above)
  - Engineering & Design Invariants: findings (5) — all resolved (items 1–5 above)
- Findings: see "Review findings and dispositions".
- Dispositions: all 12 consolidated findings fixed in scope or explicitly
  dispositioned; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- post-impl-audit skill sub-agents over the diff.
- Verify the rendered user-data still passes every security-invariant test
  (spec 22 §"One-shot AWS batch runners" item 8).
- Confirm `merge_job_artifacts` output is byte-identical for a fixed input
  set regardless of dispatch order (ordering is dispatch-only).

## Post-implementation audit results (2026-07-12)

Three independent sub-agents (plan-vs-code, engineering/design invariants,
spec alignment) plus mechanical checks. Plan-vs-code: no correctness
findings; all scope areas and all planned tests verified present (noting two
benign divergences: the finish-line ordering is implemented as a
completion-gated abort rather than a literal block move — behaviorally
equivalent and tested — and the planned reconciliation test was split into
two better-isolated tests). Merge order-independence confirmed structurally
(`merge_job_artifacts` re-derives from `generate_jobs`) and by the passing
merge suite. UserData security tests all pass unchanged.

Audit findings fixed in scope before commit:

1. **`worker_drained` was fire-and-forget but load-bearing** (invariants
   auditor): pending-instance reconciliation depends on the event, and the
   codebase's own upload-retry precedent says a single failed curl must not
   discard load-bearing state. Fixed: new `post_worker_event_reliably`
   template function retrying with the result-upload budget; drained branch
   uses it; spec 22 item 9 updated; test pins it.
2. **Deadline-too-close branch leased a job it would abandon** (both
   auditors; pre-existing wart in a branch this plan already touched):
   the check ran after `/lease`, burning an attempt and stranding the job
   for `lease_grace_seconds` — more expensive under drain with no warm
   fleet. Fixed: check moved before the lease request; `JOB_TIMEOUT`
   recomputed after the lease round-trip so it cannot overshoot the
   deadline; spec 22 item 9 updated; ordering pinned by test.
3. **Spec 31 pending-grace paragraph contradicted the new carve-outs**
   (spec auditor): qualified with a cross-reference to spec 22 item 9
   (drained-ID reconciliation; completed batches merge despite stale
   pending entries).
4. **Guard message always prints `leased=0`** (invariants auditor, minor):
   dispositioned as intentional — the guard fires only when leased is zero,
   and printing it makes that precondition visible in failure logs.

Verification after fixes: full suite 1024 passed + 1 skipped; ruff check,
ruff format, mypy, deptry, validate_docs all green.

## Retirement checklist

- [x] status: implemented, dates, commit hash (`f28339d`).
- [x] Roadmap: follow-up item replaced with a "shipped 2026-07-12" note;
      item-2 ablation wave un-gated.
- [x] Archive to `.claude/plans/archive/2026/`.
