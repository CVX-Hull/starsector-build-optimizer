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
  - docs/reports/2026-07-11-aws-cost-analysis.md
  - docs/roadmap.md
implementation_commit: c4f79bb
post_impl_audit: passed
superseded_by: null
---

# Cost-ledger port onto the honest-eval path

## Goal

Give the honest-eval sim path a **cost ledger** so realized dollar spend is
*measured*, not derived. This closes AWS-cost-analysis Unknown #1
([2026-07-11 AWS cost analysis §4](../../../docs/reports/2026-07-11-aws-cost-analysis.md):
"Realized honest-eval dollar spend (no cost ledger on that path)") and is a
no-spend prerequisite of roadmap item 3 (the instrumented accounting run
runs on this path and must report dollars in the same frame as matchups).

This is the first of the two item-3 code ports; **scale-down-on-drain to the
honest-eval fleet is a separate follow-up plan** (different concern, higher
correctness risk — static fleet with no replacement provisioning).

## Context and source docs

- Cost-ledger contract owner: [spec 22 §"Cost ledger"](../../../docs/specs/22-cloud-deployment.md)
  (`CostLedger`, `CostLedgerEntry`, the 2026-04-19 "Ledger tick" subsection).
- Honest-eval contract owner: [spec 30](../../../docs/specs/30-honest-evaluator.md).
  Spec 30 states honest-eval **deliberately does not inherit `budget_usd`**
  ("no per-eval budget cap; operator controls cost via `--workers` +
  `--replicates`"). A hard budget cap mid-eval would raise `BudgetExceeded`
  and abandon an in-flight matchup, **breaking the balanced (build × opp ×
  rep) design that justifies mean-fitness as the oracle** (spec 30
  `evaluate_builds`: "does NOT silently exclude failures"). So the honest-eval
  ledger is **measurement-only**.
- Existing tick machinery (the reuse source): `CampaignManager._tick_ledger`
  ([campaign.py:992](../../../src/starsector_optimizer/campaign.py)) +
  `_get_spot_price_cached` ([campaign.py:1039](../../../src/starsector_optimizer/campaign.py))
  + per-worker `_last_tick_ts` + `_spot_price_cache`, driven by
  `monitor_loop` ([campaign.py:985](../../../src/starsector_optimizer/campaign.py)).
- Verified facts that make the port a parity change, not a reimplementation:
  - honest-eval uses the **same worker AMI** as campaigns, so workers post the
    identical `worker:<project_tag>:*:heartbeat` Redis hash with `timestamp`,
    `region`, `instance_type` (spec 22 §"Reliable-queue protocol").
  - honest-eval's fleet `project_tag` = its `eval_tag`
    ([honest_evaluator.py:1339](../../../src/starsector_optimizer/honest_evaluator.py)),
    so the heartbeat scan pattern is `worker:<eval_tag>:*:heartbeat`.
  - The ledger cadence knobs (`ledger_heartbeat_interval_seconds`,
    `heartbeat_stale_multiplier`, `spot_price_cache_ttl_seconds`) live on
    `CampaignConfig`, which honest-eval already loads.
  - honest-eval already connects to Redis (`_flush_stale_campaign_keys`,
    [honest_evaluator.py:1316](../../../src/starsector_optimizer/honest_evaluator.py))
    and already constructs `AWSProvider(regions=campaign.regions)`
    ([honest_evaluator.py:1238](../../../src/starsector_optimizer/honest_evaluator.py)).

## Named design decisions

1. **Extract, don't copy.** Engineering principle #1 forbids duplicating the
   tick logic. Extract a `CostHeartbeatTicker` that owns the three pieces of
   per-tick state (`ledger`, `_last_tick_ts`, `_spot_price_cache`) and exposes
   `tick(now=None)`. `CampaignManager` delegates to it; honest-eval reuses it.
2. **Home = `campaign.py`, no new module, no public-concept move.**
   `campaign.py`'s module docstring already declares it "campaign manager +
   cost ledger"; `CostLedger` already lives there; `honest_evaluator` already
   imports from `campaign` (`_resolve_tailnet_ip`). A new `cost_ledger.py`
   module + moving `CostLedger` + re-exports is more churn (spec-ownership
   move, import updates) for no gain here — rejected as over-engineering.
   `CostHeartbeatTicker` sits beside `CostLedger`.
3. **The orchestrator owns cost ticking; the pool owns dispatch.**
   `CampaignManager` owns cost attribution via its own `monitor_loop`, NOT via
   `CloudWorkerPool`. Honest-eval — the campaign-analog orchestrator — owns
   its cost loop the same way: a background tick thread in
   `honest_evaluator.main`, not a hook inside the shared `CloudWorkerPool`.
   This keeps the pool a pure dispatch mechanism and avoids a double-tick for
   campaigns (which already tick via the manager). Rejected alternative:
   inject the ticker into `CloudWorkerPool._janitor_loop` (reuses its existing
   heartbeat scan) — cheaper by one thread + one Redis client, but breaks the
   orchestrator-owns-cost symmetry and couples the shared pool to cost
   machinery.
4. **Measurement-only via `budget_usd: float | None`.** `CostLedger` gains an
   optional budget: `None` ⇒ never warns, never raises (pure measurement);
   any `float` ⇒ current behavior (warn thresholds + `BudgetExceeded` cap).
   Making no-cap an explicit `None` is more honest than smuggling it through
   an `inf` sentinel, and it encodes honest-eval's no-cap design in the type.
   The campaign path always passes a `float` (`config.budget_usd`,
   [campaign.py:1212](../../../src/starsector_optimizer/campaign.py)), so its
   behavior is byte-identical.
   - **Rationale attribution (spec-review correction).** Spec 30's *stated*
     reason for not inheriting `budget_usd` is operator-control ("no per-eval
     budget cap; operator controls cost via `--workers` + `--replicates`",
     [spec 30:379-381](../../../docs/specs/30-honest-evaluator.md)). The
     stronger observation that a mid-eval hard cap would abandon an in-flight
     matchup and dent the balanced (build × opp × rep) design is *this plan's*
     synthesis, not a spec statement — the spec's balanced-design language
     ([spec 30:214-219](../../../docs/specs/30-honest-evaluator.md)) is about
     `evaluate_builds` not silently excluding failures, a different mechanism.
     Either way the conclusion (measurement-only, `budget_usd=None`) is the
     right and spec-consistent call; the spec 30 amendment cites
     operator-control as the reason and does not codify the balanced-design
     link as spec-derived.

## Scope

1. **`CostLedger` optional budget + resume seed** (`campaign.py`):
   - `__init__(path, budget_usd: float | None, warn_thresholds=...,
     initial_cumulative: float = 0.0)`.
   - `record_heartbeat`: when `_budget_usd is None`, skip `_maybe_warn` and the
     `BudgetExceeded` check (the `self._cumulative >= self._budget_usd`
     comparison at [campaign.py:269](../../../src/starsector_optimizer/campaign.py)
     is a `TypeError` against `None`, so the `None` branch **must** short-circuit
     before it); still append the row and advance `_cumulative`.
   - `_maybe_warn`: unchanged for the float path; not called when budget is None.
   - `initial_cumulative` seeds `self._cumulative` (default `0.0` → campaign
     path byte-identical). Honest-eval resume passes the prior ledger's last
     `cumulative_usd` so the column stays monotone across resume boundaries
     (spec-review finding, see Scope 4). No change to `CostLedgerEntry` (budget
     is not a row field).

2. **`CostHeartbeatTicker`** (new class, `campaign.py`, beside `CostLedger`):
   - Constructor: `redis_client`, `provider`, `project_tag`, `ledger:
     CostLedger`, `interval_seconds: float`, `heartbeat_stale_multiplier:
     float`, `spot_price_cache_ttl_seconds: float`.
   - `tick(now: float | None = None) -> None`: the exact body of the current
     `_tick_ledger` — SCAN `worker:<project_tag>:*:heartbeat`, HGETALL, skip
     stale, compute `hours_elapsed = min(interval, now - last_tick)/
     _SECONDS_PER_HOUR`, look up cached spot price, `ledger.record_heartbeat`.
     `now` defaults to `time.time()` (injectable for deterministic tests). If
     `redis_client is None`, return (parity with the current guard).
   - `_get_spot_price_cached(region, instance_type, now)`: moved verbatim.
   - `BudgetExceeded` still propagates out of `tick()` (via
     `ledger.record_heartbeat`) for the campaign path.

3. **`CampaignManager` delegates** (`campaign.py`):
   - Replace `self._last_tick_ts` / `self._spot_price_cache` attributes and the
     `_tick_ledger` / `_get_spot_price_cached` methods with a
     `self._cost_ticker: CostHeartbeatTicker | None`, constructed once the
     Redis client + `CostLedger` exist (in `_preflight`, where
     `self._redis` is set — [campaign.py:860](../../../src/starsector_optimizer/campaign.py)).
     `monitor_loop` calls `self._cost_ticker.tick()`.
   - **None-guard parity (pattern-review finding).** Today `_tick_ledger`
     opens with `if self._redis is None: return`
     ([campaign.py:1002](../../../src/starsector_optimizer/campaign.py)) and
     `monitor_loop` calls it unconditionally. After the refactor a
     `monitor_loop` reached without a successful `_preflight` would leave
     `_cost_ticker` unset. Preserve the silent-return semantics: initialize
     `self._cost_ticker = None` in `__init__` and guard the call
     (`if self._cost_ticker is not None: self._cost_ticker.tick()`). The
     ticker itself also no-ops on `redis_client is None` (Scope 2), so both
     layers are safe.
   - Behavior must be byte-identical: same rows, same `_cumulative`, same
     `BudgetExceeded` propagation into `run()`'s `try/finally`, same warn
     thresholds.
   - **Test re-pointing (pattern + invariants finding).** The six granular
     `TestTickLedger` cases
     ([tests/test_campaign.py:1267-1329](../../../tests/test_campaign.py)) call
     `mgr._tick_ledger()` directly. Re-point each to exercise
     `CostHeartbeatTicker.tick()` **preserving its specific assertion** (stale
     skip, budget-cap raise, spot-price caching, per-worker interval cap) — do
     NOT collapse them into the single new delegation test. No behavioral
     assertion weakens.

4. **Honest-eval cost tick loop** (`honest_evaluator.py`):
   - New `_cost_ledger_path(out_root, eval_tag) -> Path` →
     `_ledger_dir(out_root, eval_tag) / "cost_ledger.jsonl"` (reuse the
     existing `_ledger_dir` helper,
     [honest_evaluator.py:80](../../../src/starsector_optimizer/honest_evaluator.py),
     so it is a genuine sibling of `results.jsonl` and cannot drift from a
     re-derived `"honest_eval"/eval_tag` join — pattern-review finding).
   - **Redis client MUST decode (HIGH finding — pattern + invariants).** Build
     the ticker's client with `decode_responses=True`, modelled on the
     ledger-tick client in `CampaignManager._preflight`
     ([campaign.py:858-865](../../../src/starsector_optimizer/campaign.py)) —
     **NOT** on `_flush_stale_campaign_keys`
     ([campaign.py:505-509](../../../src/starsector_optimizer/campaign.py)),
     which omits `decode_responses` because it only `delete`s keys. The
     extracted `tick()` body reads `hash_data.get("timestamp")` and
     `key.split(":")` assuming `str`; against a bytes-returning client every
     worker is skipped as stale and the ledger records **zero rows** — a silent
     failure of the port's entire goal. The plan pins this in code review; the
     `fake_redis` fixture already sets `decode_responses=True`
     ([tests/conftest.py:150](../../../tests/conftest.py)), so tests exercise
     the decoded contract.
   - Build a `CostLedger(cost_ledger_path, budget_usd=None,
     initial_cumulative=_resume_cumulative)` (measurement-only) and a
     `CostHeartbeatTicker` with that decoded client, an
     `AWSProvider(regions=campaign.regions)`, `project_tag=eval_tag`, and the
     `campaign.*` cadence knobs (`ledger_heartbeat_interval_seconds`,
     `heartbeat_stale_multiplier`, `spot_price_cache_ttl_seconds`).
   - **Resume-monotone seed (spec-review finding).** New
     `_read_cost_ledger_cumulative(path) -> float` returns the last row's
     `cumulative_usd` (`0.0` if the file is absent/empty). On `--resume-from`,
     `_resume_cumulative` = that value so `cumulative_usd` stays monotone across
     the appended file and the last row equals total realized spend; on a fresh
     run it is `0.0`. Regardless of resume, realized spend also equals
     `sum(delta_usd)` (the resume-safe aggregation spec 30 documents). This
     mirrors the results-ledger resume-read (`read_ledger`,
     [honest_evaluator.py:92](../../../src/starsector_optimizer/honest_evaluator.py)).
   - **Thread lifecycle = repo idiom (MEDIUM finding — pattern + invariants +
     spec).** A `_CostHeartbeatThread` context-manager helper wrapping a
     `threading.Thread` + `threading.Event` stop flag. Loop body:
     `while not stop_event.is_set(): _tick_swallowing_errors();
     stop_event.wait(timeout=interval)` — `Event.wait`, never `time.sleep`, so
     a stop is prompt (matches `CloudWorkerPool._janitor_loop`,
     [cloud_worker_pool.py:646-660](../../../src/starsector_optimizer/cloud_worker_pool.py)).
     `__exit__` sets the stop event and `join(timeout=campaign.
     teardown_thread_join_seconds)` — a **bounded** join
     ([models.py:745](../../../src/starsector_optimizer/models.py)) so a tick
     blocked in a slow Redis/AWS socket cannot hang teardown or the
     `KeyboardInterrupt` path. Entered **inside** the
     `with prepare_cloud_pool(...) as pool:` block and nested so `__exit__`
     runs on every exit (normal return, exception, `KeyboardInterrupt`) before
     the fleet teardown — the thread never outlives the fleet and its Redis
     client is closed in `__exit__`.
   - **Swallow = `except Exception` (LOW finding).** The per-tick body catches
     `except Exception` (not bare `except`, so `KeyboardInterrupt`/`SystemExit`
     propagate), logs, and continues — measurement is best-effort and a
     transient Redis/AWS error must never abort a paid eval (mirrors
     `_get_spot_price_cached`'s swallow,
     [campaign.py:1051-1060](../../../src/starsector_optimizer/campaign.py)).
     Safe here because `budget_usd=None` means `record_heartbeat` raises no
     `BudgetExceeded` to eat; this safety is coupled to the ledger staying
     `None`-budget.
   - Skipped in `--dry-run` (`main()` returns before `prepare_cloud_pool`, so
     the block is never entered).

## Out of scope

- **Scale-down-on-drain on the honest-eval fleet** — separate follow-up plan
  (different orchestrator seam, static-fleet/no-replacement correctness risk).
  Named here so the item-3 prerequisite pair is not conflated.
- **A honest-eval hard budget cap / `--budget-usd` flag.** Rejected: a cap
  breaks the balanced-design oracle guarantee (Named design decision 4). A
  future *warn-only* soft threshold could be added if operators want a live
  hot-spend signal, but it is not required to close Unknown #1 and is not
  built now.
- **Consolidating the two distinct `BudgetExceeded` classes** (`campaign.py:57`
  vs `phase7_learned_batch.py:125`). The learned-batch one is untouched by
  this port; unifying them touches the whole learned-batch budget path.
  Surfaced, not silently deferred; belongs in a learned-batch grooming pass.
- **Deduplicating the third fsync-per-line ledger writer**
  (`_LedgerWriter.append`): it writes a *results* ledger with different
  semantics (per-matchup fitness, resume key), not a cost ledger. Out of this
  port's concern.

## Critical files

- `src/starsector_optimizer/campaign.py` — `CostLedger.__init__`/
  `record_heartbeat` optional budget; new `CostHeartbeatTicker`;
  `CampaignManager` delegation.
- `src/starsector_optimizer/honest_evaluator.py` — `_cost_ledger_path`, the
  background tick thread around `prepare_cloud_pool`.
- `tests/test_campaign.py` — optional-budget cases; ticker extraction parity;
  manager-delegation behavior unchanged.
- `tests/test_honest_evaluator.py` — cost-ledger tick loop writes
  `cost_ledger.jsonl`; measurement-only (no raise); clean stop/join; dry-run
  skips; resume appends.
- `docs/specs/22-cloud-deployment.md`, `docs/specs/30-honest-evaluator.md`.
- `docs/roadmap.md` — mark the cost-ledger port shipped on retirement.

## Public concepts and canonical owners

- "Cost ledger" (durability + budget contract) — spec 22 §"Cost ledger".
- "Cost heartbeat ticker" (shared per-tick attribution unit) — spec 22
  §"Ledger tick" (updated to name the shared owner).
- Honest-eval cost measurement (measurement-only ledger, path, resume-append)
  — spec 30.
- Measured dollar magnitudes — dated reports only (empirical-numbers rule);
  this port ships **no** numbers.

## Implementation sequence

1. Amend specs:
   - **Spec 22 §"Cost ledger"**: `budget_usd: float | None` (None ⇒
     measurement-only, no warn/no cap); `initial_cumulative` resume seed. Note
     `cumulative_usd` is monotone within a fleet-lifetime and, across a seeded
     resume, monotone over the whole file.
   - **Spec 22 §"Ledger tick (2026-04-19)"**: name `CostHeartbeatTicker` as the
     shared per-tick unit that owns `_last_tick_ts` + spot-price cache, which
     `CampaignManager` now delegates to (mechanics unchanged).
   - **Spec 30 — new "Cost measurement" subsection** placed adjacent to
     "Cloud-pool lifecycle" / the Resume-ledger section and cross-referencing
     the "does NOT inherit `budget_usd`" line
     ([spec 30:379-381](../../../docs/specs/30-honest-evaluator.md)) so
     "no budget cap" and "a cost ledger" don't read as a contradiction
     (spec-review placement finding): measurement-only cost ledger at
     `data/honest_eval/<eval_tag>/cost_ledger.jsonl`, ticked by a bounded
     background thread; **operator-control** cited as the no-cap reason (not
     balanced-design — see Named decision 4); realized spend = `sum(delta_usd)`
     (resume-safe) with `cumulative_usd` seeded on resume to stay monotone.
   - **Spec 30 "Inherited-then-adjusted fields" list**
     ([spec 30:371-379](../../../docs/specs/30-honest-evaluator.md)): add
     `ledger_heartbeat_interval_seconds`, `heartbeat_stale_multiplier`,
     `spot_price_cache_ttl_seconds` as inherited-not-adjusted pass-through
     fields (spec-review closed-list finding).
2. Write failing tests:
   - `test_cost_ledger_none_budget_never_warns_or_raises` — `budget_usd=None`:
     many `record_heartbeat` calls append rows, advance `_cumulative`, never
     warn, never raise.
   - `test_cost_ledger_float_budget_unchanged` — float budget still warns at
     thresholds and raises `BudgetExceeded` at the cap (guards the campaign
     path).
   - `test_cost_heartbeat_ticker_records_row_per_live_worker` — decoded
     `fake_redis` with two live + one stale heartbeat hash → two rows, stale
     skipped, spot price read via injected provider; `now` injected for
     determinism.
   - `test_cost_heartbeat_ticker_none_redis_is_noop`.
   - The five `TestLedgerTick` cases (writes-row-per-worker, budget-cap raise,
     spot-price caching, stale skip, per-worker interval cap) re-pointed to
     `CostHeartbeatTicker.tick()`, each keeping its specific assertion (Scope 3).
   - `test_campaign_manager_delegates_to_cost_ticker` — a `monitor_loop`/tick
     over `fake_redis` produces the same ledger rows the pre-refactor manager
     produced (parity), and `BudgetExceeded` still tears down.
   - `test_campaign_monitor_loop_without_preflight_is_noop` — `_cost_ticker`
     unset ⇒ `monitor_loop` does not raise (None-guard parity, Scope 3).
   - `test_cost_ledger_resume_seed_keeps_cumulative_monotone` —
     `_read_cost_ledger_cumulative` on an existing file + `initial_cumulative`
     ⇒ appended rows continue monotone; absent file ⇒ `0.0`.
   - `test_honest_eval_cost_ledger_path` — path helper reuses `_ledger_dir`.
   - `test_honest_eval_ticks_cost_ledger_during_run` — with `prepare_cloud_pool`
     and a decoded fake Redis, the background thread writes ≥1
     `cost_ledger.jsonl` row and is not `alive()` after the block exits (normal
     path).
   - `test_honest_eval_cost_thread_joined_on_exception` — an exception raised
     inside the `with` block still leaves the tick thread joined
     (not `alive()`) — the failure path the daemon flag would otherwise mask
     (MEDIUM join finding).
   - `test_honest_eval_cost_tick_error_does_not_abort` — a tick that raises
     (Redis error) is swallowed via `except Exception`; `evaluate_builds` still
     completes.
   - `test_honest_eval_dry_run_writes_no_cost_ledger`.
3. Implement to green, one concern per change (optional budget → ticker
   extraction → manager delegation → honest-eval loop). Run
   `uv run pytest tests/test_campaign.py tests/test_honest_evaluator.py -q`
   after each concern.
4. Full gates + post-impl audit + roadmap grooming + plan retirement.

## Tests and mechanical gates

- `uv run pytest tests/test_campaign.py tests/test_honest_evaluator.py -v`,
  then full `uv run pytest tests/ -q`.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run deptry .`
- `uv run python scripts/validate_docs.py`.
- design-invariants: no magic numbers in function bodies (`_SECONDS_PER_HOUR`
  stays the module constant; cadence knobs come from `CampaignConfig`); no new
  hardcoded game rules; `CostLedgerEntry` remains a frozen dataclass.

## Review findings and dispositions

Consolidated from the three fresh-eye auditors (deduped; A=pattern,
B=spec, C=engineering/invariants) + self-review.

1. **HIGH — Redis client `decode_responses` trap (A1, C1 converged).** The
   ticker's client must decode; modelled on `_preflight`
   ([campaign.py:858-865](../../../src/starsector_optimizer/campaign.py)), NOT
   `_flush_stale_campaign_keys`. Without it the extracted `tick()` treats every
   worker as stale → **zero rows**, silently defeating the port. Fixed in
   Scope 4 (explicit `decode_responses=True`, decoded `fake_redis` in tests).
2. **MEDIUM — thread lifecycle idiom (A2, C2, B3 converged).** Use
   `threading.Event` + `wait(timeout=interval)` (not `time.sleep`) + a
   **bounded** join via `teardown_thread_join_seconds`, in a context-manager
   whose `__exit__` runs on every exit path. Fixed in Scope 4; failure-path
   join test added (`test_honest_eval_cost_thread_joined_on_exception`).
3. **MEDIUM — `monitor_loop` None-guard parity (A3).** A `None` `_cost_ticker`
   would `AttributeError` where `_tick_ledger` silently returned. Fixed in
   Scope 3 (init `None` + call guard + ticker None-redis no-op);
   `test_campaign_monitor_loop_without_preflight_is_noop` added.
4. **MEDIUM — resume breaks `cumulative_usd` monotonicity (B1).** Fresh
   `CostLedger` per resume resets `_cumulative` → column drops at resume
   boundaries → under-counts. Fixed beyond the doc-only remedy: seed
   `_cumulative` from the prior ledger's last row (`initial_cumulative` +
   `_read_cost_ledger_cumulative`, Scope 1 + 4); spec 30 also documents
   `sum(delta_usd)` as the resume-safe aggregation.
5. **MEDIUM — spec 30 closed inherited-fields list (B2).** Add the three
   cadence knobs as inherited pass-through. Fixed in sequence step 1.
6. **LOW — narrow swallow to `except Exception` (C3).** Fixed in Scope 4.
7. **LOW — `_cost_ledger_path` reuse `_ledger_dir` (A4).** Fixed in Scope 4.
8. **LOW — budget-cap rationale over-attributed to spec (B4).** The
   balanced-design link is the plan's synthesis; spec 30's reason is
   operator-control. Fixed in Named decision 4 + sequence step 1 (amendment
   cites operator-control).
9. **LOW — spec 30 amendment placement (B5).** Named: new "Cost measurement"
   subsection adjacent to Cloud-pool lifecycle / Resume, cross-referencing the
   no-inherit-`budget_usd` line. Fixed in sequence step 1.

Clean areas confirmed by ≥1 auditor: module placement of `CostHeartbeatTicker`
in `campaign.py`; `budget_usd: float | None` campaign-path byte-identity;
orchestrator-owns-cost topology; empirical-numbers-rule compliance (ships no
dollar magnitudes; cadence knobs are pre-existing config defaults); the two
deferrals (dual `BudgetExceeded`, drain port) are properly surfaced, not
papered over. Extra note folded from C: the two `BudgetExceeded` classes have
**different base classes** (`Exception` vs `RuntimeError`), a latent
`except RuntimeError` trap — reinforces the surfaced consolidation note, still
out of scope here.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14 (self-review phases 1–4)
- Findings: phases 1–4 clean after verifying against code: `CampaignConfig`
  cadence knobs exist ([models.py:741,771,778](../../../src/starsector_optimizer/models.py));
  `CostLedger` is constructor-injected into `CampaignManager`
  ([campaign.py:797](../../../src/starsector_optimizer/campaign.py));
  refactor(ticker extraction)-in-service-of-feature(honest-eval loop) is
  sequenced as isolated one-concern-per-change steps, not intermixed.
- Dispositions: see "Review findings and dispositions"; all fresh-eye findings
  folded into scope before approval.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-14
- Agents:
  - Pattern Consistency: findings (4) — all resolved (items 1, 2, 3, 7 above)
  - Spec Alignment: findings (5) — all resolved (items 4, 5, 8, 9 + LOW-3 join)
  - Engineering & Design Invariants: findings (3) — all resolved (items 1, 2, 6)
- Findings: see "Review findings and dispositions".
- Dispositions: all 9 consolidated findings fixed in scope; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- post-impl-audit sub-agents over the diff.
- Verify the campaign cost path is behaviorally unchanged: `TestCostLedger`
  and manager monitor-loop tests pass; a fixed fake-Redis input produces the
  identical ledger rows before/after the refactor; the six granular
  `TestTickLedger` assertions survive re-pointed, not collapsed.
- Verify the honest-eval ticker's Redis client is built with
  `decode_responses=True` (HIGH finding) — the port's core failure mode.
- Verify the honest-eval tick thread cannot outlive the fleet (bounded join on
  every exit path incl. `KeyboardInterrupt` and exception) and cannot abort an
  eval (tick errors swallowed via `except Exception`).
- Verify resume seeds `_cumulative` so `cumulative_usd` is monotone across the
  appended `cost_ledger.jsonl`, and `sum(delta_usd)` equals the last row.
- Confirm no dollar magnitudes leaked into specs/roadmap (empirical-numbers
  rule).

## Post-implementation audit results (2026-07-14)

Three independent sub-agents (plan-vs-code, engineering/design invariants,
spec alignment) + mechanical checks (ruff, mypy, deptry, vulture, stale-ref
grep). Full suite 1140 passed + 1 skipped; all quality gates green.

- **Plan-vs-code**: faithful; HIGH (`decode_responses=True`) + all MEDIUM
  (bounded join on every exit path; None-guard parity; resume-monotone seed)
  correctly implemented; no behavioral defects. Flagged two plan-named tests
  covered only transitively — both **added directly** in scope
  (`test_cost_heartbeat_ticker_none_redis_is_noop`,
  `test_dry_run_writes_no_cost_ledger`); and a plan miscount (five
  `TestLedgerTick` cases, not six — plan text corrected).
- **Engineering/invariants**: clean, principled, no fixes required. Confirmed
  the extraction is behavior-preserving, the swallow is correctly scoped
  (`except Exception`, not `BaseException`), the bounded join cannot hang
  teardown, and no dead code / new suppressions.
- **Spec alignment**: conforms field-by-field; empirical-numbers rule clean.
  One completeness gap fixed: `redis_preflight_timeout_seconds` added to spec
  30's inherited-fields list.

Verification after fixes: `ruff check`/`ruff format --check`/`mypy`/`deptry`/
`validate_docs` all green; full suite 1140 passed, 1 skipped.

## Retirement checklist

- [x] status: implemented, dates, commit hash (set post-commit).
- [x] Roadmap item 3: cost-ledger port marked shipped (drain port remains,
      annotated with its static-fleet design caveat).
- [x] Archive to `.claude/plans/archive/2026/`.
