---
plan_type: implementation
status: implemented
created: 2026-07-15
approved: 2026-07-15
implemented: 2026-07-15
owner: agent
related_docs:
  - docs/specs/22-cloud-deployment.md
  - docs/specs/24-optimizer.md
  - .claude/plans/archive/2026/2026-07-14-instrumented-accounting-run.md
  - .claude/skills/cloud-worker-ops.md
implementation_commit: cdabdf4
post_impl_audit: passed
superseded_by: null
---

# Cloud study robustness — flask-port preflight, study-exit detection, WorkerTimeout handling

## Goal

Fix the three robustness defects the 2026-07-15 accounting run exposed, so the
re-run of the deficient hammerhead cells (seeds 100,101,102,106) completes
cleanly and so these failure modes can never again cause **silent** partial data
loss. This is the fix cycle gating the accounting-run re-launch (owning plan:
[instrumented-accounting-run](2026-07-14-instrumented-accounting-run.md)).

## Incident (verified 2026-07-15, two context agents)

Launched `accounting-hammerhead` (9 studies) and `accounting-wolf` (3 studies)
**concurrently on the same workstation**. Both YAMLs set `base_flask_port: 9000`,
`flask_ports_per_study: 100`; study_idx 0/1/2 → flask ports 9000/9100/9200 **in
both campaigns**. Wolf bound them first; hammerhead seeds 100/101/102 (idx
0/1/2) then failed `make_server(...)` bind (`cloud_worker_pool.py:510`, OSError
EADDRINUSE) — **after** their fleets provisioned — so `prepare_cloud_pool`'s
`finally` (`cloud_runner.py:186`) tore the fleets down and the subprocesses
exited nonzero. `CampaignManager.monitor_loop` (`campaign.py:1290`) never
inspects return codes → the 3 studies were **silently dropped**, never
rescheduled, producing empty eval-log dirs and no study DB. Seeds 103-108 (ports
9300-9800) did not collide → ran fine. Separately, seed106 (port 9600, no
collision) died at trial ~169/250 on an **uncaught `WorkerTimeout`**
(`optimizer.py:684-712` catches `RetryableMatchupError` + `InstanceError` only;
`WorkerTimeout` subclasses bare `Exception`, `cloud_worker_pool.py:75`), leaving
16 trials stuck RUNNING.

Red herrings ruled out: `InsufficientInstanceCapacity` on c7a was backfilled by
c7i (every fleet got 8 instances); `min_workers_to_start`/`partial_fleet_policy`
were never the constraint — grep confirms they are **parsed but consumed nowhere**
in the cloud path (`cloud_runner.py`/`cloud_provider.py`/`cloud_worker_pool.py`/
`optimizer.py`); they are enforced only in the separate `phase7_learned_batch.py`
entry point.

## Scope

### A. WorkerTimeout bounded-retry + terminal-reason discriminator (spec 24) — `optimizer.py`

The run loop must not crash on `WorkerTimeout`. Add an `except WorkerTimeout`
branch between the `RetryableMatchupError` and `InstanceError` handlers
(`optimizer.py:686-693`) implementing **Option C** (bounded retry → failure):

- New per-trial counter `_InFlightBuild.worker_timeouts: int = 0` (lives on the
  mutable `_InFlightBuild`, persists in `_queue` across rungs).
- New `OptimizerConfig.max_worker_timeout_retries: int = 2` (no magic number —
  config dataclass, inline default like `failure_score`).
- On `WorkerTimeout`: `ifb.worker_timeouts += 1`; if `<= max`, **`logger.warning`**
  + `continue` (build stays in `_queue`, `_dispatched` already discarded at :682 →
  re-dispatch via `_next_matchup`; re-dispatch is data-safe — stable `matchup_id`
  drains any retained/janitor-requeued result, `cloud_worker_pool.py:613`; each
  re-dispatch re-counts in `matchups_dispatched` at :826). If `> max`,
  **`logger.error`** + mirror the InstanceError finalization (:704-710): remove
  from `_queue`, `study.tell(failure_score)`, bump `_trials_completed` +
  `_trials_errored`, `continue` — and call **neither `_finalize_build` nor
  `_append_eval_log`**, so **no eval-log row** (preserves the replay's bijective
  `(source_path, trial_number)` join — spec 24 "the fifth path"). Import
  `WorkerTimeout` from `.cloud_worker_pool` (non-circular; precedented by the
  existing `InstanceError` import from `instance_manager`).
- **Terminal-reason discriminator (review M — accounting honesty).** WorkerTimeout
  and instance-error are **distinct** failure modes; folding both into
  `kind="instance_error"` inflates that bucket and hides the timeout mode — the
  opposite of item-3's honest per-kind partitioning. So **both** terminal
  finalizers set a `terminal_reason` user_attr alongside `matchups_dispatched`:
  the InstanceError path sets `terminal_reason="instance_error"`, the
  WorkerTimeout-exhausted path sets `"worker_timeout"`. (Both still set
  `matchups_dispatched` as today.)
- Retry-cost note: with `max=2` and `result_timeout_seconds=900`, one pathological
  trial can burn ≤ ~3×900 s ≈ 45 min before finalizing. Bounded and acceptable;
  state it in the spec.
- Spec-first: amend spec 24 with the WorkerTimeout terminal path + the
  `terminal_reason` discriminator, and **rewrite the now-false "only this path"
  claims** (spec 24:431 "`set_user_attr` used only on this path" → two paths;
  see §F doc list).

### F. Extractor per-kind partitioning (spec 24) — `accounting_extract.py`

The extractor recovers a terminal-finalized trial as COMPLETE-in-DB-minus-JSONL
(`read_instance_error_records`, `accounting_extract.py:102-137`) — mechanically
correct for both kinds. But it hardcodes `kind="instance_error"` (:132). Read the
`terminal_reason` user_attr and label the record `"instance_error"` or
`"worker_timeout"` accordingly (default absent → `"instance_error"` for
backward-compat); add `"worker_timeout"` to `_KINDS` (:26). Update the module
docstring (:11-14) and the `:105` "only that path sets it" comment. This keeps
the accounting spread honest across the two no-eval-log-row failure modes.

### B. Flask-port preflight — best-effort defense-in-depth (spec 22) — `campaign.py`

Add a **module-level** `_check_flask_ports_free(ports) -> None` (mirroring
`_check_redis_reachable`, `campaign.py:735` — module-level, returns `None`, raises
`PreflightFailure`; NOT a method) called from `_preflight` so it is reusable by
`honest_evaluator._preflight_for_honest_eval`. Enumerate the **exact** bound port
for **every `(study_idx, seed_idx)` pair** the campaign spawns:
`base_flask_port + study_idx * flask_ports_per_study + seed_idx`
(`cloud_runner.py:273`) — NOT per-study, NOT the whole `flask_ports_per_study`
tailnet-ACL range. Probe each with a throwaway `socket.bind(("0.0.0.0", port))`
**without `SO_REUSEADDR`** (must match `make_server`'s `0.0.0.0` bind,
`cloud_worker_pool.py:510`). Occupied → `PreflightFailure`.

**Reframed (review HIGH — TOCTOU):** the actual bind is inside each study
subprocess minutes after this parent-side probe, so a *concurrent* campaign that
has not yet bound will pass this check and then race. Therefore B is **best-effort
defense-in-depth** (catches an already-bound concurrent campaign and stale
listeners), NOT a guarantee. The guarantee against silent loss is **§C**; the
prevention is **§E**. Additionally, add an **in-subprocess pre-bind probe** in
`prepare_cloud_pool` immediately before `make_server` so the raw `OSError
EADDRINUSE` becomes a diagnosable, logged failure (still surfaced via §C).

### C. Study-exit detection — the real safety net (spec 22) — `campaign.py`

`monitor_loop`/`run` must not silently ignore a study subprocess that exits
nonzero. On reap, surface **every** `proc.returncode != 0` (including negative
signal-kills) at ERROR and in the campaign run-summary as a **failed study** —
**not** gated on "no study DB" (that gate misses a mid-run crash that already
wrote a partial DB, e.g. the pre-fix seed106 WorkerTimeout crash). Use DB
existence / trial count as an **annotation**, not the gate. `proc.returncode` is
safe to read after the `monitor_loop` while-condition drains
(`campaign.py:1293`). Full auto-reschedule (context Fix 3) stays **out of scope**;
the safety-critical part is surfacing the loss loudly. Ratify at plan review:
loud-report-only vs. also non-zero campaign exit on any failed study.

### D. Dead-config resolution — enforce (principle #2) — `cloud_runner.py`/spec 22

`min_workers_to_start` + `partial_fleet_policy` are parsed/validated
(`campaign.py:120-129`, `models.py:728-729`) but consumed **nowhere** in the cloud
path — false operator confidence. **Enforce** (house-consistent — mirrors
`phase7_learned_batch.py:1542`): in `prepare_cloud_pool` right after the existing
`if not instance_ids: raise` (`cloud_runner.py:~156`), if
`len(instance_ids) < min_workers_to_start` then honor `partial_fleet_policy` —
`abort` → raise; `proceed_half_speed` → warn + continue. Do **not** copy phase7's
stricter `min == target` equality (campaign intent differs; validator only
requires `min <= max_concurrent_workers`, `campaign.py:125`).

### E. Re-run operational change (no code) — the owning accounting plan + SOP

The re-run of seeds 100,101,102,106 must avoid the port collision: run
**hammerhead-only** (no concurrent wolf), so idx 0/1/2 → 9000/9100/9200 are free.
General rule (document in cloud-worker-ops SOP): concurrent campaigns on one
workstation MUST use **distinct `base_flask_port`** ranges, or launch
**sequentially**. This §E is the actual *prevention*; §B/§C are the guardrails.

## Out of scope

- Full campaign-level auto-reschedule of dropped studies (context Fix 3) — larger
  change; C surfaces the loss loudly, which is the safety-critical part.
- Any change to the replay ranking/gating algorithm or the accounting extractor
  (verify B/A need no extractor change; the WorkerTimeout-finalized trial is
  recovered by the existing COMPLETE-in-DB-minus-JSONL path).
- The re-run launch itself (owning plan's spend gate; already user-ratified
  Package B — this fix + re-bake + hammerhead-only re-run of the 4 cells).

## Critical files

- `src/starsector_optimizer/optimizer.py` — WorkerTimeout branch + `_InFlightBuild`
  counter + `OptimizerConfig.max_worker_timeout_retries` + `terminal_reason`
  user_attr on both finalizers + import.
- `scripts/analysis/accounting_extract.py` — `terminal_reason` branch;
  `"worker_timeout"` in `_KINDS`; docstring/`:105`-comment update (F).
- `src/starsector_optimizer/campaign.py` — module-level `_check_flask_ports_free`;
  study-exit-code detection in `monitor_loop`/`run`.
- `src/starsector_optimizer/cloud_runner.py` — in-subprocess pre-bind probe (B);
  `min_workers_to_start`/`partial_fleet_policy` enforcement (D).
- `docs/specs/24-optimizer.md` — WorkerTimeout terminal path + `terminal_reason`
  discriminator; **rewrite the "only this path" `set_user_attr` claim (:431)**.
- `docs/specs/22-cloud-deployment.md` — flask-port preflight (best-effort);
  study-exit contract; min_workers/partial_fleet enforcement.
- `scripts/analysis/phase7_prequential_replay.py` — Ĝ `measured_inflight_gap`
  docstring (:280-309): name both no-stream-row kinds, not only instance-error.
- `.claude/skills/cloud-worker-ops.md` — concurrent-campaign distinct-port-base rule.
- `tests/test_optimizer.py`, `tests/test_campaign.py`, `tests/test_accounting_extract.py` — tests.

## Tests and mechanical gates

- WorkerTimeout retry-then-recover: raise WorkerTimeout on call 1, delegate after
  → COMPLETE trial exists, `run()` does not raise (mirror
  `test_retryable_matchup_failure_requeues_trial`).
- WorkerTimeout exhaust-to-failure: raise on first `max+1` calls for one trial →
  trial COMPLETE with `value == failure_score`, `_trials_errored` bumped,
  `terminal_reason=="worker_timeout"` user_attr set, **no eval-log row** (the
  regression test for the crash; mirror the every-matchup-fails InstanceError test).
- Extractor discriminator: a study DB with one `terminal_reason="worker_timeout"`
  and one `"instance_error"` COMPLETE-minus-JSONL trial → extractor partitions
  them into the two kinds; absent `terminal_reason` defaults to `instance_error`.
- Flask-port preflight: bind a `("0.0.0.0", port)` in the campaign's exact set →
  `_check_flask_ports_free` raises `PreflightFailure` naming it; and `run()` exits
  `SystemExit(2)` with a caplog match, **no study subprocess spawned** (assert
  `spawn_studies` not reached — NOT `provision_fleet`, which was removed from
  `CampaignManager`). Include a multi-seed study to pin the `+ seed_idx` term.
- Study-exit detection: a study proc exits nonzero (incl. a negative signal code)
  → campaign records it as failed (ERROR log / run-summary), even when a partial
  study DB exists.
- Dead-config enforce: provision below `min_workers_to_start` → `abort` raises,
  `proceed_half_speed` warns + continues.
- `uv run pytest tests/ -q`; ruff/format/mypy/deptry; `validate_docs.py`.
- design-invariants: no magic numbers (retry bound in `OptimizerConfig`);
  manifest-as-oracle untouched.

## Implementation sequence

1. Spec-first: amend spec 24 (WorkerTimeout terminal path) + spec 22 (flask-port
   preflight, study-exit contract, min_workers/partial_fleet resolution).
2. Failing tests for A/B/C/D; implement to green (one concern per change).
3. Full gates + **review iterations** (plan-review + fresh-eye sub-agents on the
   plan; post-impl audit sub-agents on the diff) — per user instruction, before
   any re-run.
4. Commit → re-bake (src/ changed → WorkerSourceSha flips; existing AMI fails
   preflight) → update accounting YAML AMIs → re-run hammerhead-only cells
   100/101/102/106 → verify 9 complete cells → resume the owning accounting plan
   (oracle pass → materialize → replay → reports).

## Plan Review Gate
- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-15
- Findings: spec-first ordering, no-magic-numbers (retry bound is an
  `OptimizerConfig` field), and the incident-diagnosis accuracy verified; the
  self-review's open questions (port-formula completeness, WorkerTimeout
  semantics, honest per-kind partitioning) were sharpened by the fresh-eye lane
  below and folded.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate
- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-15 (3 sub-agents: engineering/correctness, spec/replay
  alignment, pattern consistency)
- Verdict: all three REVISE; all findings folded before approval.

### Review findings and dispositions
1. **HIGH (eng+pattern) — port formula dropped `+ seed_idx`.** Real bind is
   `base + study_idx*flask_ports_per_study + seed_idx` per `(study_idx, seed_idx)`
   pair; the range is the tailnet-ACL reservation, not bound ports. **Fixed** §B.
2. **HIGH (eng) — preflight TOCTOU; doesn't guarantee prevention.** Bind is
   in-subprocess minutes later; concurrent not-yet-bound campaign races through.
   **Fixed:** §B reframed best-effort; §C is the guarantee; §E the prevention;
   added in-subprocess pre-bind probe.
3. **HIGH (pattern) — test asserts removed `provision_fleet` + wrong exception.**
   `_preflight` catches `PreflightFailure` → `sys.exit(2)`. **Fixed:** tests assert
   `SystemExit(2)` + caplog + no study spawned.
4. **MEDIUM (spec/replay) — folding WorkerTimeout into `instance_error` is
   dishonest.** Two distinct modes; conflation hides the timeout mode. **Fixed:**
   §A `terminal_reason` discriminator + §F extractor branch + `_KINDS` entry.
5. **MEDIUM (eng) — §C gate too narrow** (missed mid-run crashes with a partial
   DB). **Fixed:** surface ALL nonzero exit codes; DB/trial-count is annotation.
6. **MEDIUM (spec/replay) — stale "only this path" claims** (spec 24:431,
   extractor :11-14/:105, replay Ĝ docstring). **Fixed:** added to §A/§F doc list.
7. **MEDIUM (eng) — probe must bind `0.0.0.0` without `SO_REUSEADDR`.** **Fixed** §B.
8. **LOW (pattern+eng) — enforce (not remove) dead config; module-level preflight
   helper; log levels (warn retry / error terminal); socket.bind is net-new,
   watch TIME_WAIT.** **Fixed:** §D enforce (mirror phase7:1542); §B module-level;
   §A log levels; noted.
Affirmed PASS (no change): Scope-A finalization mirror preserves invariants +
join-safety; `max_worker_timeout_retries` genuinely needed; config-field
placement; import non-circular; test monkeypatch pattern; B's flask-only port
scope; Ĝ interaction correct.

## Post-implementation audit requirements
- post-impl-audit over the diff; confirm WorkerTimeout-exhausted trials emit no
  eval-log row (replay-join safety) AND carry `terminal_reason="worker_timeout"`,
  by test; confirm the port preflight enumerates every `(study_idx, seed_idx)`
  port; confirm study-exit loss is surfaced for ALL nonzero codes; confirm the
  extractor partitions the two terminal kinds.

## Implementation record (2026-07-15)

Implemented A–F via TDD; **1188 pass, 1 skip**; ruff/format/mypy/deptry/
validate_docs all green. Delivered: `_finalize_terminal_failure(ifb, reason)`
shared helper (both terminal paths) + bounded `except WorkerTimeout`
(`OptimizerConfig.max_worker_timeout_retries=2`) + `terminal_reason` discriminator
in `optimizer.py`; `_check_flask_ports_free` + `_campaign_flask_ports` preflight +
`_report_study_exits` in `campaign.py`; `_probe_flask_port_free` + min_workers/
partial_fleet enforcement in `cloud_runner.py`; `read_terminal_failure_records`
(renamed, partitions by `terminal_reason`) in `accounting_extract.py`; spec 22/24
+ replay Ĝ docstring + cloud-worker-ops SOP updates.

### Post-impl audit (2 sub-agents: correctness, replay-join+tests) — PASS
Both PASS on all 6 required properties (no eval-log row on terminal path; bounded
retry; exact per-`(study_idx,seed_idx)` port set; all-nonzero study-exit; extractor
partition; min_workers enforce). Folded findings:
1. **MEDIUM — stale Ĝ docstring** named only instance-error (I'd updated only the
   inline comment). **Fixed** — docstring now names both terminal-failure kinds.
2. **MEDIUM — missing preflight-wiring integration test** (isolated helper tested,
   not the `_preflight`→`sys.exit(2)` wiring). **Fixed** —
   `test_preflight_exits_on_occupied_flask_port`.
3. **LOW — extractor allow-list used full `_KINDS`** (a corrupt `terminal_reason`
   matching a non-terminal kind could leak). **Fixed** — `_TERMINAL_KINDS`.
4. **LOW — flaky fixed-port test** (56789). **Fixed** — ephemeral grab-release.
5. **LOW — `_probe_flask_port_free` untested.** **Fixed** — added a test.
Not folded (LOW, deliberate): `_trials_errored`-bump assertion omitted (internal
state not observable via the returned study; the observable failure_score +
terminal_reason are asserted).

## Next (spend-gated) — re-bake + re-run
src/ changed → WorkerSourceSha flips → existing AMI fails preflight. Re-bake →
update accounting-hammerhead AMIs → re-run **hammerhead-only** cells 100/101/102/106
(no concurrent wolf → no port collision) → verify 9 complete cells → resume the
owning accounting plan (oracle pass → materialize → replay → reports).

## Retirement (2026-07-17)

**Complete.** The re-bake + hammerhead-only re-run of the deficient cells
landed (tasks #71–74, 9 complete cells, final audit clean); the owning
accounting plan then ran to completion (oracle → materialize → replay →
reports, 2026-07-17). All three robustness defects are fixed and shipped in
`cdabdf4` (WorkerTimeout bounded-retry + terminal-reason discriminator;
flask-port-**free** preflight + study-exit detection; min_workers/partial_fleet
dead-config enforcement); post-impl audit PASS. Scope note: this plan fixed the
flask-port **collision/bind** class; the separate flask-port-**serving**
preflight gap (a live server that binds but fails to serve) is deliberately
out of scope here and tracked independently as a post-oracle task. Archived to
`.claude/plans/archive/2026/`.
