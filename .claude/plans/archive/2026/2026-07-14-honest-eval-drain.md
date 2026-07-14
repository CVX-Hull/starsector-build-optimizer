---
plan_type: implementation
status: implemented
created: 2026-07-14
approved: 2026-07-14
implemented: 2026-07-14
owner: agent
related_docs:
  - docs/specs/22-cloud-deployment.md
  - docs/specs/30-honest-evaluator.md
  - docs/roadmap.md
  - .claude/plans/archive/2026/2026-07-12-scale-down-on-drain.md
implementation_commit: 71f5ec4
post_impl_audit: passed
superseded_by: null
---

# Scale-down-on-drain for the static honest-eval fleet (orchestrator-driven)

## Goal

Eliminate the honest-eval idle-drain tail: near end-of-run, once the number of
outstanding matchups drops below the fleet's slot capacity, some workers are
provably idle yet keep billing until the whole fleet is torn down at
`evaluate_builds` return. Terminate those idle workers as the work drains,
**from the orchestrator**, keeping enough workers alive to finish the
remainder. This is roadmap item-3's second no-spend code port (the cost-ledger
port shipped 2026-07-14, commit `3433b15`).

The learned-batch drain (archived plan `2026-07-12-scale-down-on-drain.md`)
does **not** transfer: it is a *worker*-self-terminate model coupled to the
HTTP `/lease` protocol with a bounded job queue and a terminal "drained"
verdict, plus replacement provisioning. Honest-eval uses the Redis
BRPOPLPUSH reliable queue on a **static fleet with no replacement
provisioning** — a worker whose BRPOPLPUSH times out just loops until
`max_lifetime_hours`. So the roadmap explicitly calls for an
**orchestrator-driven** design, which this plan delivers.

## Context and source docs

- Contract owners: spec 22 §"Cost ledger" / §"Ledger tick" (the
  orchestrator-owns-a-background-tick precedent this reuses), spec 30
  §"Cloud-pool lifecycle" / §"Cost measurement" (honest-eval fleet
  lifecycle + the just-landed background-thread pattern).
- Fleet mechanics verified (2026-07-14 code map):
  - `evaluate_builds` (`honest_evaluator.py:674-680`) submits at most
    `num_workers` (= `total_matchup_slots`) matchups to Redis at once
    (`while queue and len(pending) < num_workers`). **The full backlog
    lives in the Python `queue` list, invisible to Redis.** Therefore
    `llen(source)+llen(processing)` ≈ concurrency throughout the run and is
    NOT a usable "remaining work" signal — remaining work is Python-side
    (`total - completed`). *(This is the load-bearing fact; a Redis-depth
    drain would fire at t=0 and kill the fleet.)*
  - `worker_id` == the live EC2 instance-id: userdata overrides
    `STARSECTOR_WORKER_WORKER_ID` via IMDSv2 at boot
    (`cloud_userdata.py:143-154`). The heartbeat key
    `worker:<project_tag>:<worker_id>:heartbeat`
    (`worker_agent.py:376-377`) therefore carries the instance-id — the
    orchestrator can map an idle worker directly to an instance to
    terminate.
  - Existing terminate paths are **tag-only** (whole fleet
    `terminate_fleet`, whole project `terminate_all_tagged`;
    `cloud_provider.py:452-477`). No primitive terminates an explicit
    subset of instance-ids. `list_active(project_tag)`
    (`cloud_provider.py:590-615`) returns `{id, region, state,
    instance_type}` per live instance — the authoritative live set.
  - The heartbeat hash (`worker_agent.py:376-388`) does NOT currently
    carry per-worker occupancy, so idle-vs-busy is not observable today.
    The write cadence is `_HEARTBEAT_INTERVAL_SECONDS = 30.0`
    (`worker_agent.py:499`) — a module constant, promoted to a public
    `WORKER_HEARTBEAT_INTERVAL_SECONDS` by this plan so the drain ticker can
    source its liveness-freshness cutoff from the single true cadence.
  - **No-respawn invariant** (the property that makes external termination
    safe): `AWSProvider._create_fleet_in_region` provisions with
    `Type="instant"` — AWS does NOT respawn a terminated instance
    (spec 22 §"Per-study fleet lifecycle", the `Type="instant"` invariant,
    which spec 22 itself flags "must be revisited if a maintain-type fleet is
    ever introduced"). This is a **spec 22** invariant the drain *depends on*;
    it is NOT stated in spec 30. The "static fleet, no replacement
    provisioning" property is a code fact (`prepare_cloud_pool` calls
    `provision_fleet` exactly once), reinforced by this spec 22 invariant.

## Design decisions (named)

1. **Orchestrator-driven termination, not worker self-terminate.** The
   orchestrator (a background thread in `honest_evaluator.main`, the
   campaign-analog owner — same role it plays for the cost tick) reads
   global progress + per-worker occupancy and calls a provider primitive to
   terminate idle surplus. Rejected alternative — a Redis "drain" marker
   that workers self-terminate on — because on a static fleet with **no
   replacement provisioning**, distributed self-termination makes the
   last-worker-standing liveness guarantee hard to prove (a momentarily
   empty queue could let every idle worker exit while queued work remains,
   deadlocking `evaluate_builds`). The orchestrator's global keep-floor
   makes liveness trivially provable, and it matches the roadmap's explicit
   "orchestrator-driven, not a copy of the worker-self-terminate model"
   steer.

2. **Remaining work is read Python-side, never from Redis depth.**
   `evaluate_builds` publishes `len(queue)+len(pending)` (= outstanding
   matchups) into a thread-safe `MatchupProgress` sink each dispatch-loop
   iteration; the drain ticker reads `progress.remaining()`. Keeps
   `evaluate_builds` pool-agnostic (it still works with `LocalInstancePool`;
   `progress=None` is a no-op).

3. **New provider primitive `terminate_instances(ids, *, region)`.** The
   batch plan rejected a provider-side per-instance terminate as "redundant
   with worker self-shutdown" — that reason **does not apply here**: there is
   no self-shutdown path, so the primitive is *necessary*, not redundant.
   Minimal ABC extension; `AWSProvider` reuses the existing
   `client.terminate_instances(InstanceIds=...)` SDK call (as
   `_terminate_by_tags` does, minus the tag-describe since we have explicit
   ids); `HetznerProvider` stubs `NotImplementedError` like its siblings.
   Idempotent (terminating an already-terminating id is an AWS no-op).

4. **Per-worker occupancy via a heartbeat field, backward-compatible.**
   Workers maintain a thread-safe `active_matchups` counter (incremented on
   BRPOPLPUSH claim, decremented in a `finally` after the run attempt —
   success-then-ack or failure-then-leave-for-janitor) and publish it in the
   heartbeat hash. The orchestrator treats **absent** `active_matchups` (an
   un-re-baked AMI) as *busy/unknown* → never terminates that worker. So the
   drain is **dormant until the worker AMI is re-baked**: landing this code
   changes nothing on current live runs. Activation is gated behind a
   separate AMI re-bake + live run (both already spend-gated), which keeps
   this a **no-spend code port** — same posture as the cost-ledger port.

5. **Keep-floor liveness invariant.** `keep = max(1, ceil(remaining /
   matchup_slots_per_worker))` while `remaining > 0`; the drain terminates
   only workers in the *idle* set, and at most `surplus = max(0, live −
   keep)` of them. It never terminates a busy worker and never drops the
   live count below `keep`, so surviving capacity always covers the
   outstanding + any janitor-requeued matchups. At `remaining == 0` the
   ticker returns `[]` and lets the normal `prepare_cloud_pool` teardown own
   the final shutdown (no fighting at the finish line).

6. **Reuse, don't copy, the background-thread lifecycle.** Generalize the
   cost port's `_CostHeartbeatThread` (already `tick: Callable`-driven) into
   a mechanism-agnostic `_PeriodicBackgroundThread(tick, *, interval,
   join_timeout, on_close, name)`; drive both the cost tick and the drain
   tick with it. Same bounded-join-on-every-exit-path, stop-event-not-sleep,
   per-tick `except Exception` guarantees.

## Named trade-off (accepted)

**Claim-race → bounded requeue latency.** `active_matchups` is written by the
worker's heartbeat loop at its cadence (`WORKER_HEARTBEAT_INTERVAL_SECONDS`,
~30 s), which is *independent* of the drain tick. So a heartbeat that passes
the liveness-freshness filter (fresh *timestamp*) can still carry an
`active_matchups == 0` *occupancy snapshot* up to one heartbeat interval old.
The vulnerable window is therefore `(last_heartbeat_write, terminate_call)` ≈
one worker-heartbeat interval — **not** the tiny read→terminate gap — and in
that window a worker can claim up to `matchup_slots_per_worker` matchups (one
per consumer thread). The honest per-tick strand bound is thus
`surplus × matchup_slots_per_worker`, not `surplus`.

A stranded matchup sits in the processing list until the janitor requeues it
(after `visibility_timeout_seconds`, which honest-eval deliberately raises
above the full retry window), after which a surviving keep-worker runs it.
**Correctness is preserved with high probability, not unconditionally**:
repeated stranding of the *same* matchup is bounded by two independent retry
ceilings — the janitor drops an item once `requeue_count > max_requeues`
(`campaign.py:457-464`), after which `run_matchup` raises `WorkerTimeout` and
consumes one of `evaluate_builds`' separate `max_retries_per_matchup`
attempts; exhausting *those* aborts the eval. The keep-floor makes this
pathological path very unlikely (it stops draining while `surplus > 0`, i.e.
while many keep-workers remain to absorb requeues), but the plan states the
bound honestly rather than claiming absolute safety.

**Primary mitigation — the source-empty gate.** The freshness filter controls
*timestamp* age, not occupancy-snapshot age, so it does not shrink the race
window on its own. The load-bearing mitigation is instead a Redis-side gate:
**the drain ticker terminates only when `llen(source) == 0`** — i.e. no
matchup is currently queued-but-unclaimed. A worker can only claim from a
non-empty source, so with an empty source the sole residual race is a dispatch
`LPUSH` landing inside the tick, for which the keep-floor survivors are also
consumers. This is cheap (one `LLEN`), only ever makes the drain *more*
conservative (liveness unaffected), and is exactly aligned with the tail
regime where the source is usually empty (that's *why* the worker is idle).
Stated in the spec 22 amendment, not left implicit (mirrors the batch plan's
named recovery-latency trade-off).

## Scope

1. **Provider primitive** (`cloud_provider.py`)
   - ABC `CloudProvider.terminate_instances(self, instance_ids:
     Sequence[str], *, region: str) -> int` (count acted on). `AWSProvider`
     implements via the per-region client `terminate_instances(InstanceIds=…)`
     (empty ids → return 0, no API call). `HetznerProvider` raises
     `NotImplementedError`.

2. **Worker occupancy** (`worker_agent.py`)
   - A thread-safe counter (a tiny `_ActiveMatchups` holder with a `Lock`,
     or `threading` primitive) shared by the consumer threads; `+1` right
     after a successful `brpoplpush` claim, `−1` in a `finally` wrapping the
     run+POST (covers success→ack and failure→leave-for-janitor).
   - `heartbeat(...)` writes `active_matchups` into the hash. Consumer loop
     (`_consume_loop`) and heartbeat loop share the counter via the worker
     runtime.

3. **Progress sink** (`honest_evaluator.py`)
   - `MatchupProgress` — thread-safe (`Lock`) `set_remaining(int)` /
     `remaining() -> int`. Constructed empty by `main()` with
     `remaining() == 0`; `evaluate_builds` seeds it to `len(jobs)` on entry
     and updates it thereafter. A pre-seed `remaining() == 0` makes the drain
     ticker a no-op (§Design-decision-5), which is exactly right — there is
     nothing to drain before dispatch begins.
   - **Circular-import avoidance** (fresh-eye finding): `MatchupProgress`
     lives in `honest_evaluator.py`, but `WorkerDrainTicker` lives in
     `campaign.py` (§4) and `honest_evaluator.py` already imports from
     `campaign`. So the ticker must NOT import `MatchupProgress`. It consumes
     a minimal `typing.Protocol` — `RemainingWork` with `remaining() -> int`
     — defined in `campaign.py`; `MatchupProgress` satisfies it structurally.
     No new import edge.
   - `evaluate_builds` gains `progress: RemainingWork | None = None`
     (spec 30 signature amendment; typed by the Protocol so `evaluate_builds`
     also has no dependency on the concrete class). When non-None it calls
     `progress.set_remaining(len(jobs))` before the loop and
     `progress.set_remaining(len(queue) + len(pending))` at the top of each
     dispatch-loop iteration. **`len(jobs)` is the post-replay count** — the
     job list is built *after* the `--resume-from` skip-filter
     (`honest_evaluator.py:608-617`), so on resume `remaining` seeds to the
     matchups that actually still need dispatch, not the full matrix.
     (`set_remaining` is write-only; the `progress`-absent path is byte-for-byte
     unchanged, so the documented "one EvaluatedBuild per input build, order
     matches input" and the resume-ledger contract are untouched.)

4. **Drain decision + ticker** (`campaign.py`, beside `CostHeartbeatTicker`)
   - Pure `plan_worker_drain(*, live_instance_ids, idle_instance_ids,
     remaining_matchups, matchup_slots_per_worker) -> list[str]`: returns the
     ≤ `surplus` idle ids to terminate per §Design-decision-5. Fully
     unit-testable, no I/O.
   - `WorkerDrainTicker` (`__init__(*, redis_client, provider, project_tag,
     source_list, progress, matchup_slots_per_worker,
     heartbeat_interval_seconds, heartbeat_stale_multiplier)`). `tick(now=None)`
     runs Redis-only work first and only touches the EC2 API when there is
     actually something to terminate (fresh-eye finding — dormant mode must
     make zero EC2 calls):
     1. `if self._redis is None: return`.
     2. `remaining = progress.remaining()`; if `<= 0` return (finish line is
        teardown's job).
     3. **Source-empty gate:** `if self._redis.llen(source_list) > 0: return`
        — defer while any matchup is queued-but-unclaimed (§Named-trade-off
        primary mitigation).
     4. Scan `worker:<tag>:*:heartbeat`: skip heartbeats older than
        `heartbeat_interval_seconds × heartbeat_stale_multiplier` (liveness);
        skip any without `active_matchups` (absent ⇒ busy ⇒ dormant-safe) or
        with `active_matchups != 0` → collect idle ids. **If `not idle_ids:
        return`** — before any provider call, so an un-re-baked fleet issues
        no `DescribeInstances`.
     5. `live = provider.list_active(project_tag)` → live ids + id→region;
        `to_terminate = plan_worker_drain(...)` restricted to
        `idle_ids ∩ live_ids`; group by region; call
        `provider.terminate_instances(ids, region=…)` per region.

     `redis_client` MUST be `decode_responses=True` (same trap the cost ticker
     documents). `heartbeat_interval_seconds` is fed
     `WORKER_HEARTBEAT_INTERVAL_SECONDS` (the worker's true write cadence, not
     the ledger-tick interval) by `_make_worker_drain_thread` (§5) — the
     liveness cutoff must be sized against the cadence that actually writes
     the hash.

5. **Thread lifecycle generalization + wiring** (`honest_evaluator.py`,
   `worker_agent.py`)
   - Promote `worker_agent._HEARTBEAT_INTERVAL_SECONDS` →
     module-public `WORKER_HEARTBEAT_INTERVAL_SECONDS` (it is now referenced
     outside the module); `_heartbeat_loop` reads the same name — one source
     of truth for the cadence.
   - Rename `_CostHeartbeatThread` → `_PeriodicBackgroundThread`, add a
     `name: str` param; docstring made mechanism-agnostic. **Keyword args keep
     the `_seconds` suffix** (`interval_seconds=`, `join_timeout_seconds=`) —
     the rename changes only the class name + the added `name` param, not the
     existing signature. Edit the existing call site
     `_make_cost_heartbeat_thread` (`honest_evaluator.py:268-273`) to pass
     `name="honest-eval-cost-tick"`.
   - `_make_worker_drain_thread(campaign, project_tag, provider, progress)`:
     own `decode_responses=True` Redis client + `WorkerDrainTicker`
     (fed `heartbeat_interval_seconds=WORKER_HEARTBEAT_INTERVAL_SECONDS`,
     `source_list=f"queue:{project_tag}:{project_tag}:source"` — honest-eval's
     `study_id == project_tag == eval_tag`) +
     `_PeriodicBackgroundThread(ticker.tick,
     interval_seconds=campaign.drain_poll_interval_seconds,
     join_timeout_seconds=campaign.teardown_thread_join_seconds,
     on_close=client.close, name="honest-eval-drain")`.
   - `main()`: build an empty `progress = MatchupProgress()` (seeded inside
     `evaluate_builds`). When drain is enabled, nest the drain thread inside
     the pool context alongside the cost thread; pass `progress` to
     `evaluate_builds`. `--no-drain` CLI flag (default: drain on) routes to
     *not* entering the drain thread — an operator escape hatch for a
     feature that terminates instances. Because the feature is dormant until
     the worker AMI is re-baked, the first post-rebake live run is the natural
     point to validate drain behavior (operator may start it `--no-drain` and
     flip on once satisfied).

6. **Config** (`models.py`, `campaign.py`)
   - `CampaignConfig.drain_poll_interval_seconds: float = 60.0` (new; no
     magic numbers). Inherited by honest-eval as pass-through (spec 30
     inherited-fields list).
   - **Add `"drain_poll_interval_seconds"` to the `load_campaign_config`
     pass-through opt tuple** (`campaign.py:189-210`) — that tuple carries a
     standing warning (`campaign.py:186-188`) that a new dataclass field not
     listed there silently drops operator YAML overrides (audit finding V1).
     honest-eval loads via `load_campaign_config` (`honest_evaluator.py:1315`),
     so this is required, not optional.

7. **Spec amendments** (spec-first). Each new contract element must land at
   its **canonical** location, not only in prose (spec auditor: the specs have
   authoritative enumerations that would otherwise become competing sources of
   truth):
   - Spec 22:
     - New §"Worker drain (honest-eval)" — the keep-floor invariant, the
       Python-side-remaining requirement, the source-empty gate, the named
       claim-race trade-off (with the honest `surplus × slots` bound and the
       two-retry-ceiling caveat), and the no-respawn (`Type="instant"`)
       dependency (cross-ref the existing §"Per-study fleet lifecycle"
       invariant).
     - Add `terminate_instances(instance_ids, *, region) -> int` to the
       **canonical `CloudProvider` ABC block** (spec 22:543-575), not only in
       the new section.
     - Add `active_matchups` to the **canonical heartbeat-hash field
       enumeration** (spec 22:150) with its backward-compat (absent ⇒ busy)
       semantics.
     - Add `drain_poll_interval_seconds` to the **`CampaignConfig` field
       table** (spec 22:70-105), beside its cadence siblings.
     - Note in §"Fleet ownership" (spec 22:36) that honest-eval's `main()`
       gains an in-context partial-termination path (the drain thread), so the
       single-owner model reads coherently; the later `terminate_fleet` +
       project sweep stays idempotent over already-terminated ids.
   - Spec 30:
     - New §"Fleet drain" companion to §"Cost measurement" — background-thread
       analog, `--no-drain`, dormant-until-rebake, source-empty gate.
     - `evaluate_builds` signature gains `progress: RemainingWork | None =
       None` (observation-only; return/order/resume contract unchanged);
       state that `progress` seeds to the **post-replay** job count.
     - Add `--no-drain` to the **CLI flag enumeration** (spec 30:234-266).
     - Inherited-fields list gains `drain_poll_interval_seconds` (pass-through).

## Out of scope

- **Campaign-path drain** — campaign studies are separate subprocesses each
  owning their own fleet; a different orchestrator. Roadmap scopes this item
  to honest-eval.
- **Replacement provisioning** — honest-eval is static; the drain only
  shrinks, never grows. (This is *why* the batch model doesn't port.)
- **Worker self-terminate / Redis drain marker** — rejected in §Design-1.
- **LPT / duration-ordered dispatch** — the batch plan's companion feature.
  honest-eval matchups are homogeneous (one build × one opp × one rep, same
  `matchup_time_limit_seconds`); no strong duration skew to exploit. Add only
  if a future run shows a heavy tail.
- **Deleting stale heartbeat keys on terminate** — unnecessary; the
  freshness filter already excludes a dead worker's lingering heartbeat, and
  the existing pre-launch `_flush_stale_campaign_keys` clears them between
  runs.

## Critical files

- `src/starsector_optimizer/cloud_provider.py` — `terminate_instances`.
- `src/starsector_optimizer/worker_agent.py` — `active_matchups` counter +
  heartbeat field.
- `src/starsector_optimizer/campaign.py` — `plan_worker_drain`,
  `WorkerDrainTicker`.
- `src/starsector_optimizer/honest_evaluator.py` — `MatchupProgress`,
  `evaluate_builds` progress param, thread rename + `_make_worker_drain_thread`,
  `main()` wiring + `--no-drain`.
- `src/starsector_optimizer/models.py` — `drain_poll_interval_seconds`.
- `docs/specs/22-cloud-deployment.md`, `docs/specs/30-honest-evaluator.md`.
- `docs/roadmap.md` — close the follow-up on retirement.
- Tests: `tests/test_cloud_provider.py`, `tests/test_worker_agent.py`,
  `tests/test_campaign.py`, `tests/test_honest_evaluator.py`.

## Public concepts and canonical owners

- "Worker drain (honest-eval)" / keep-floor invariant / provider
  `terminate_instances` — spec 22.
- `active_matchups` heartbeat field + backward-compat — spec 22
  §"Worker drain".
- honest-eval drain thread + `--no-drain` + `evaluate_builds(progress=…)` —
  spec 30.
- Any measured tail-savings magnitude — a future dated report only
  (empirical-numbers rule; none asserted here).

## Implementation sequence

1. Amend specs 22 and 30.
2. Write failing tests (see §Tests).
3. Implement, one concern per change, `uv run pytest tests/test_<module>.py`
   after each: provider primitive → worker counter → `MatchupProgress` +
   `evaluate_builds` param → `plan_worker_drain` → `WorkerDrainTicker` →
   thread rename + drain thread + `main()` wiring + config field.
4. **Reconciliation verification** (do during impl, before audit):
   fresh-eye auditors already confirmed `CloudWorkerPool._check_stalled_progress`
   and `_check_mod_jar_consistency` (`cloud_worker_pool.py:721,662`) are
   diagnostic-only (log WARN, explicitly "does NOT abort dispatch") and
   `_check_mismatch_rate` aborts only on corrupt-result rate (unrelated to
   termination) — so this is expected-green. Confirm in code during impl and
   add a test that a drained worker (stale/absent heartbeat) does not trip a
   pool abort. If any check *does* abort, reconcile (the batch plan's
   `pending_instance_ids` precedent).
5. Full gates + post-impl audit + roadmap grooming + plan retirement.

## Tests and mechanical gates

- `plan_worker_drain` (pure): keep-floor at `remaining>0`; `remaining==0 → []`;
  terminates only idle ids; caps at `surplus`; spread-busy worst case (busy
  ids never returned even when `keep < live−idle`); `idle_count > surplus`
  returns exactly `surplus`; single-worker floor (never empties the fleet
  while work remains).
- `WorkerDrainTicker.tick`: `None` redis → no-op; `remaining==0` → no-op;
  **source-empty gate** — `llen(source) > 0` ⇒ no terminate, no `list_active`;
  absent `active_matchups` field ⇒ worker treated as busy ⇒ not terminated
  (dormant-until-rebake) **and `list_active` is never called** (zero EC2 API
  in dormant mode); stale idle heartbeat ⇒ skipped; fresh idle surplus with
  empty source ⇒ `provider.terminate_instances` called with the right ids
  grouped by region; only ids in `list_active` are terminated (intersection).
- `terminate_instances`: AWS path calls the SDK with the id list; empty ids →
  0, no call; Hetzner raises `NotImplementedError`; the abstract method addition
  keeps the `Partial(CloudProvider)` non-instantiability expectation green
  (`tests/test_cloud_provider.py:73`) — update it alongside the two impls.
- worker `active_matchups`: increments on claim, decrements on ack AND on
  failure-leave; heartbeat hash carries the field.
- `evaluate_builds(progress=…)`: seeds total, decrements to 0 across a run
  (fake pool); `progress=None` unchanged.
- `_PeriodicBackgroundThread`: rename keeps the cost-thread tests green;
  ticks, joins bounded, closes on exit; swallows tick errors.
- `main()` wiring: `--no-drain` → no drain thread entered; default enters it;
  dry-run provisions nothing (no drain thread, no terminate calls).
- Gates: `uv run pytest tests/ -q`; `uv run ruff check . && uv run ruff format
  --check . && uv run mypy && uv run deptry .`; `uv run python
  scripts/validate_docs.py`. design-invariants: no magic numbers
  (`drain_poll_interval_seconds` is a config field), manifest-as-oracle
  untouched.

## Review findings and dispositions

Consolidated across the three fresh-eye auditors (P=pattern, S=spec,
C=correctness; dedup'd). All folded into the plan before approval.

1. **P-HIGH — new config field silently dropped from YAML.**
   `drain_poll_interval_seconds` must be added to the `load_campaign_config`
   pass-through opt tuple (`campaign.py:189-210`; standing warning at
   :186-188), or operator overrides are dropped. **Fixed:** Scope 6.
2. **P-MED-HIGH — circular import.** `MatchupProgress` (honest_evaluator) vs
   `WorkerDrainTicker` (campaign), and honest_evaluator already imports
   campaign. **Fixed:** the ticker consumes a `RemainingWork` Protocol defined
   in campaign.py; `MatchupProgress` satisfies it structurally; no new import
   edge (Scope 3).
3. **C-MED-HIGH / P-MED / C-MED — claim-race understated.** Occupancy is
   sampled at the worker heartbeat cadence (~30 s), not the tick; the true
   window is `(last_heartbeat_write, terminate)` ≈ one heartbeat interval and
   the strand bound is `surplus × slots`, not `surplus`; the freshness filter
   controls timestamp age, not occupancy-snapshot age; the worker cadence was
   a private non-config constant and the plan didn't say what the ticker uses.
   **Fixed:** §Named-trade-off rewritten with the honest bound + window; added
   the **source-empty (`llen(source)==0`) gate** as the primary mitigation;
   promoted `WORKER_HEARTBEAT_INTERVAL_SECONDS` and fed it to the ticker as the
   liveness-cutoff basis (Scope 4-5).
4. **S-MED / C-MED — "correctness never affected" overclaim.** Two retry
   ceilings (janitor `max_requeues` drop + `evaluate_builds`
   `max_retries_per_matchup`) can interact to abort under pathological repeated
   stranding. **Fixed:** softened to "preserved with high probability; bounded
   by the two ceilings; keep-floor makes it very unlikely" (§Named-trade-off).
5. **C-LOW — dormant mode still issued `DescribeInstances` per tick.**
   **Fixed:** tick reordered to `if not idle_ids: return` before `list_active`
   (Scope 4) — zero EC2 API on an un-re-baked fleet.
6. **S-MED ×3 — spec amendments must land at canonical locations.**
   `terminate_instances` → the ABC block (22:543-575); `active_matchups` → the
   heartbeat-field enumeration (22:150); `drain_poll_interval_seconds` → the
   `CampaignConfig` table (22:70-105). **Fixed:** Scope 7.
7. **S-MED — misattribution.** "static fleet / no replacement" is a code fact
   + the spec 22 `Type="instant"` no-respawn invariant, NOT spec 30 text.
   **Fixed:** citation corrected + dependency named (§Context, Scope 7).
8. **P-LOW-MED — thread keyword-arg convention + call-site edit.** Keep the
   `_seconds` suffix; the rename adds only class-name + `name` param; edit
   `_make_cost_heartbeat_thread`'s call site. **Fixed:** Scope 5.
9. **P-LOW — ABC-conformance test.** Update the `Partial(CloudProvider)`
   non-instantiability expectation (`tests/test_cloud_provider.py:73`).
   **Fixed:** §Tests.
10. **S-LOW ×3 — fleet-ownership note, resume×progress seeding, `--no-drain`
    flag list.** **Fixed:** Scope 3 (post-replay seed), Scope 7 (ownership note
    + CLI flag enumeration).
11. **C-LOW — drain defaults on for first activation.** Dispositioned:
    dormant-until-rebake means the first live exercise is a deliberate,
    spend-gated step; `--no-drain` is the operator escape hatch and the plan
    notes the first post-rebake run is the validation point (Scope 5). Kept
    default-on.

Affirmed by auditors (no change): keep-floor liveness is sound with no
deadlock; "remaining is Python-side, not Redis depth" is correct; dormancy is
genuine; mutable `MatchupProgress`/counter are legitimate concurrency
primitives, not domain models; the pool-abort concern is a real-but-green
verification (all three pool checks are diagnostic-only); no
TODO/skip/type-ignore/suppression/test-weakening introduced; empirical-numbers
rule honored (no magnitudes asserted).

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14 (self-review phases 1–4)
- Findings: phase-1 self-review caught one ambiguity (`MatchupProgress`
  seeding) — fixed. Phases 2–4 clean after verifying: spec-first ordering,
  no magic numbers (`drain_poll_interval_seconds` is a config field, `keep`'s
  `max(1, …)` is algorithm-inherent), the `_CostHeartbeatThread` generalization
  is a feature-enabling extract (not gratuitous refactor), and the mutable
  progress/counter are concurrency primitives (not domain models).
- Dispositions: see "Review findings and dispositions".
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14
- Agents:
  - Pattern Consistency: findings (6) — all resolved (items 1, 2, 3, 8, 9 above; INFO reconciliation-already-satisfied noted)
  - Spec Alignment: findings (8) — all resolved (items 4, 6, 7, 10 above; confirmed non-issues on progress/return contract, lifecycle ordering, empirical-numbers rule)
  - Engineering & Design Invariants: findings (5) — all resolved (items 3, 4, 5, 11 above; core design affirmed — keep-floor sound, no deadlock, Python-side-remaining correct, dormancy genuine)
- Findings: see "Review findings and dispositions".
- Dispositions: all consolidated findings fixed in scope or explicitly
  dispositioned; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- post-impl-audit skill sub-agents over the diff.
- Confirm the drain is a **no-op** on a heartbeat without `active_matchups`
  (dormant-until-rebake), by test.
- Confirm `evaluate_builds` output is identical with `progress` present vs
  absent (progress is observation-only).
- Confirm no drain path can drop the live fleet below the keep-floor while
  `remaining > 0` (keep-floor invariant), by test.
- Confirm an intentionally-terminated worker does not trip a `CloudWorkerPool`
  abort (§Implementation-sequence-4).

## Post-implementation audit results (2026-07-14)

Three independent sub-agents (plan-vs-code, engineering/design invariants,
spec alignment) plus mechanical checks (ruff/mypy/deptry/validate_docs/vulture).

- **Plan-vs-code**: no defects. Every scope item (1–7) and all four
  correctness-critical claims (keep-floor math, tick ordering, post-replay
  progress seeding, decrement-on-failure) confirmed against current code.
  Flagged the added `progress.set_remaining(0)` on clean completion as a
  *correct* addition (prevents the drain acting on a stale positive count in
  the teardown window) and the increment-after-JSON-parse placement as
  marginally more correct than the plan's literal wording.
- **Spec alignment**: no mismatches — spec and code in lockstep field-by-field;
  empirical-numbers rule honored (only designed constants/defaults in the new
  spec text).
- **Engineering & design invariants**: no active bug; every flagged correctness
  hazard (counter balance on all paths, freshness-cutoff sourcing, source-empty
  gate, TOCTOU, mutable-primitive legitimacy) checks out. One actionable
  LOW–MED finding folded (below).

Audit findings fixed in scope before commit:

1. **`heartbeat(active_matchups=0)` defaulted to the *terminable* value**
   (invariants auditor): the drain treats an absent field as busy/safe but an
   explicit `0` as terminable, so a `0` default sat on the dangerous side of
   the design's own safety asymmetry (inert today — production always passes
   the real count — but a latent footgun). Fixed: default is now
   `active_matchups: int | None = None` and the field is written only when
   supplied, so a caller that does not report occupancy omits it and the drain
   reads it as busy. Existing direct-`heartbeat()` tests stay green (field
   omitted); the production `_heartbeat_loop` still always emits the real count.

Dispositioned (no change): `plan_worker_drain`'s `max(1, …)` keep-floor `1` is
an algorithm-inherent semantic minimum ("always keep one worker while work
remains"), not a tunable — consistent with the no-magic-numbers carve-out for
algorithm-inherent literals (precedent: `max(1, total // buckets)`). The
reconciliation check (Impl-seq-4) is verified-by-inspection: `cloud_worker_pool`
`_check_stalled_progress` / `_check_mod_jar_consistency` are diagnostic-only
(no `raise`/abort), and the drain never interacts with the pool's logic (it
terminates instances externally), so a terminated worker's vanished heartbeat
cannot trip a pool abort — a synthetic heavy-fixture test would only re-assert
already-proven log-only behavior, so it was not added.

Verification after fixes: full suite **1164 passed, 1 skipped**; ruff check,
ruff format, mypy, deptry, validate_docs all green.

## Retirement checklist

- [x] status: implemented, dates, commit hash.
- [x] Roadmap: item-3 drain bullet → "shipped 2026-07-14" note; "Groomed:"
      header updated.
- [x] Archive to `.claude/plans/archive/2026/`.
