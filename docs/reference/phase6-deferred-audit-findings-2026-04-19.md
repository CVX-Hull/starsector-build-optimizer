# Phase 6 — Deferred audit findings (2026-04-19)

Retrospective capture of audit items identified during the post-sampler-benchmark-abort sweep on 2026-04-19 but **not fixed in that session**. Each entry states the exact location, the theoretical failure mode, whether it has been observed in practice, the proposed fix, and the rationale for deferring.

A future Phase 6 hardening pass (or the Phase 7.5 reliability work) should triage these before any campaign that scales past the current production envelope (~96 workers × 2 matchup_slots_per_worker = 192 concurrent matchup slots). If a campaign starts hitting one of these, the entry below has enough context to pick up.

See the session-of-record findings (live-surfaced + fixed) inline in:
- `src/starsector_optimizer/cloud_provider.py` (SG + LT visibility waiters, transient-error retry)
- `src/starsector_optimizer/optimizer.py::_apply_eb_shrinkage` (EB guard on `_completed_records`)
- `src/starsector_optimizer/cloud_runner.py` (study_id includes sampler)
- `scripts/run_optimizer.py` (per-study eval-log path)
- `docs/specs/22-cloud-deployment.md` §`provision_fleet`
- `docs/specs/24-optimizer.md` §A2′
- `docs/reference/phase6-cloud-worker-federation.md` §10

---

## High-severity deferred items

### H1 — `TimeoutTuner` is dormant production code

- **Location:** `src/starsector_optimizer/timeout_tuner.py` (entire module); `src/starsector_optimizer/__init__.py` (export); `docs/specs/21-timeout-tuner.md` (spec).
- **Finding (audit E):** no production caller instantiates `TimeoutTuner`. Only `tests/test_timeout_tuner.py` exercises it (11 sites). `LocalInstancePool` and the combat harness use the fixed `InstanceConfig.matchup_timeout_seconds` default unchanged from Phase 3.
- **Status quo:** spec 21 was edited this session to flag DORMANT at the top of the file, pointing a future wire-in at the per-study eval-log path (`data/logs/<study_id>/evaluation_log.jsonl`) instead of the now-stale single-file assumption.
- **Related concurrency hazard if wired in as written:** `timeout_tuner.py:102` appends to `self._data_dir / "evaluation_log.jsonl"` with no `study_id` in the row schema. Mirrors the exact bug fixed this session in the optimizer's eval-log path — if N study subprocesses each own a `TimeoutTuner`, the shared file corrupts the survival-model training set (trials from study A train the model that study B then consults).
- **Proposed fix (pick one):**
  1. **Delete** the module + spec + tests if Phase 3.5 is not going to be wired in before Phase 7 ships. Matches the no-dead-code invariant. Smallest risk.
  2. **Wire up** `TimeoutTuner` behind `OptimizerConfig.timeout_tuner` opt-in, have it glob `data/logs/*/evaluation_log.jsonl` recursively at `_count_observations` / `refit`, and write to its OWN per-study path (not shared). Non-trivial.
- **Why deferred:** deleting a whole spec + module + tests is a scope decision that should include the user. Wiring it up is a new feature, not a bug fix.

### H2 — POST-before-register race in `CloudWorkerPool._dispatch_and_wait`

- **Location:** `src/starsector_optimizer/cloud_worker_pool.py:163-185` (`/result` handler), `:231-253` (`_dispatch_and_wait`).
- **Finding (audit D):** the dedup check at line 172 (`if matchup_id in self._seen: return duplicate`) is populated at line 181 inside the `/result` handler and is never cleared. If the orchestrator ever retries a matchup with the same `matchup_id` after the first dispatch hit `WorkerTimeout`, the late POST from the original worker (which the first dispatcher's `_results.pop(...)` already gave up on) will populate `_seen` with that id; the retry's fresh `_result_events` entry is registered but the late or new worker's POST then returns 409 duplicate, the event never fires, retry times out. The retry's stale `_results` entry (if populated) is popped at line 247 but the `if not got or result is None` check at line 248 still raises `WorkerTimeout` because `got=False`, so no silent-success-on-stale-data; the dispatcher just hangs for a full `result_timeout_seconds` before failing.
- **Observed?** **Not in practice** — StagedEvaluator does not currently retry the same `matchup_id` after `WorkerTimeout`; a timeout there propagates as `InstanceError` and gets scored with `failure_score` (see `optimizer.py:558-568`). The hazard becomes live only if a future retry path is added.
- **Proposed fix:** scope `_seen` and `_results` per-dispatch-attempt by keying on `(matchup_id, dispatch_nonce)` where `dispatch_nonce` is freshly generated per `_dispatch_and_wait` call and included in the matchup payload → worker echoes it back in the POST. Alternative: TTL-expire `_seen` entries after `visibility_timeout_seconds + result_timeout_seconds` + janitor interval so late POSTs from abandoned dispatches can't block retries.
- **Why deferred:** unreachable in the current call graph; any fix is premature until a retry path is introduced.

### H4 — `compute_effective_stats` ignores `hull.built_in_mods`

- **Location:** `src/starsector_optimizer/hullmod_effects.py:131-204` (`compute_effective_stats`). Iterates `build.hullmods` only (lines 155, 165); `hull.built_in_mods` is never read.
- **Finding (surfaced 2026-04-19 during Phase 7 prep hull-selection review):** the Python heuristic scorer treats every hull as if its factory-built-in hullmods were absent. Concrete impact on the Phase 7 prep hull set (`examples/phase7-prep.yaml`):
  - `lasher` has `builtInMods=["ballistic_rangefinder"]` — real-engine play extends all ballistic turret ranges to match hardpoint range. Scorer undercounts Lasher turret effective range.
  - `onslaught` has `builtInMods=["hbi"]` (High-Burst Ion / projectile-speed buff depending on mapping) — scorer misses the buff entirely if `hbi` is also absent from `HULLMOD_EFFECTS`.
  - Other 6 hulls in the prep set have no built-ins; no immediate impact.
- **Scope of miscalibration:** Python heuristic ONLY. The Java combat harness uses the real engine, so `CombatResult` fitness values are correct. The gap affects: (a) warm-start trial quality (heuristic ranks used to seed Optuna), (b) the EB regression prior `γ̂` (7-covariate heuristic regression; `composite_score` is one of the 7). Phase 5D EB shrinkage treats the heuristic as a noisy α covariate and shrinks per-trial weight `w_i = τ̂²/(τ̂² + σ̂²_i)` as evidence accumulates, bounding the harm asymptotically. But the bias is systematic (always-on factory buff), not random, so it doesn't fully cancel.
- **Observed?** **Not empirically benchmarked** — caught by code review, not a failing test or suspicious JSONL.
- **Proposed fix:** two parts, either independent or joint.
  1. `compute_effective_stats` reads `hull.built_in_mods + list(build.hullmods)` (or set-union; dedupe) in both the armor-flat and multiplier passes.
  2. `HULLMOD_EFFECTS` gains entries for `ballistic_rangefinder`, `hbi`, and any other built-ins that appear on hulls in the Phase 7+ roster (`apogee.sensor_array`, `afflictor.phasefield`, `legion.heavyflightdeck`, etc.). Run a grep to enumerate: `python -c "import json, re, pathlib; ...` over `game/starsector/data/hulls/*.ship` to list all `builtInMods` values, cross-reference against `HULLMOD_EFFECTS` keys.
- **Why deferred:** a pre-campaign inline fix would change the heuristic feature distribution for two of the eight prep-campaign hulls mid-run — the EB regression prior γ̂ is trained per-study, so the within-study signal would still be coherent, but cross-hull γ̂ reconciliation gets harder. Better to ship the prep campaign with the current heuristic, then fix + re-run the 7D overnight on Hammerhead to quantify Δρ before committing.
- **Gating criterion to revisit:** Phase 7 kernel implementation — when the GP needs `HullInputFeatures` columns that depend on effective-stat correctness, this fix lands as a co-requisite.

### H3 — `BoundedSemaphore` vs Redis source-queue depth invariant

- **Location:** `src/starsector_optimizer/cloud_worker_pool.py:228` (`self._dispatch_semaphore`), `:242` (unconditional `lpush`).
- **Finding (audit D):** the dispatch semaphore gates in-flight Python-side `run_matchup` calls to `total_matchup_slots`. But `run_janitor_pass` (`campaign.py:273`) re-LPUSHes stuck items to the source queue WITHOUT consulting the semaphore — so the queue depth can exceed `total_matchup_slots`. Under heavy spot preemption this creates a backlog invisible to the orchestrator semaphore.
- **Observed?** **No** — at 96 workers × 2 slots the queue sustains <5 items in normal operation. Would only become visible at 1000+ slot scales or under a preemption storm.
- **Proposed fix:** none needed. The semaphore gates dispatch-rate, not queue-depth; a backlog in Redis is not a correctness issue (workers drain it). Document-only: call out that the semaphore is a dispatch-rate gate, not a queue-depth invariant.
- **Why deferred:** not a bug, just a mental-model clarification.

---

## Medium-severity deferred items

### M1 — Janitor `enqueued_at` ping-pong under steady-state slow matchups

- **Location:** `src/starsector_optimizer/campaign.py:254-279` (`run_janitor_pass`). Specifically line 273 `redis_client.lpush(source_list, raw)` re-LPUSHes the original payload verbatim.
- **Finding (audit D):** `enqueued_at` stored in the payload at `cloud_worker_pool.py:239` is the FIRST-dispatch wall clock. When the janitor re-LPUSHes a stuck item, the payload carries the original `enqueued_at`. Next janitor pass computes `now - enqueued_at` against the SAME first-dispatch timestamp, so if the new worker is still slow the item appears stuck again → requeued again. Pathological case: a matchup that genuinely takes `2 × visibility_timeout_seconds` gets requeued every janitor interval indefinitely.
- **Observed?** **No** — `visibility_timeout_seconds=120` is 4× a typical matchup's 20-30 s runtime. Ping-pong requires matchups slower than the visibility timeout, which would already be failing the optimizer-side `result_timeout_seconds`.
- **Proposed fix:**
  ```python
  # In run_janitor_pass, right before lpush:
  item["enqueued_at"] = now                # reset the clock for the requeue
  item["requeue_count"] = item.get("requeue_count", 0) + 1
  redis_client.lpush(source_list, json.dumps(item))
  ```
  Optionally add a `max_requeues` cap so a genuinely-broken matchup surfaces as a hard failure instead of cycling forever.
- **Why deferred:** not observed in practice; a campaign that starts emitting "requeued stuck matchup" warnings more than once per matchup would surface this fast.

### M2 — Test fixtures use legacy `study_id` format

- **Location:** `tests/test_cloud_provider.py:123,130`, `tests/test_campaign.py:702-703`, `tests/test_cloud_userdata.py:24`, `tests/test_cloud_worker_pool.py:58,141`, `tests/test_worker_agent.py:22,123,142,167`.
- **Finding (audit A):** these tests use string literals like `"hammerhead__early__seed0"` and `"wolf__mid__seed1"` as arbitrary `study_id` values. The literals still work correctly — `study_id` is just a string to the code under test — but they no longer match what `cloud_runner.py` emits in production (`"hammerhead__early__tpe__seed0"`). Consistency risk for anyone grepping tests for a representative study_id.
- **Proposed fix:** s/`__seed/__tpe__seed/` across those test fixture literals.
- **Why deferred:** zero functional impact (tests pass); cosmetic only.

---

## Low-severity / housekeeping deferrals

### L1 — Notebooks reference the old shared eval-log path

- **Location:** `notebooks/build_analysis.ipynb:52`, `notebooks/trial_analysis.ipynb:52`.
- **Finding (audit E):** both notebooks find files via `p.name == "evaluation_log.jsonl"` which still matches the new per-study path (the filename inside each directory is unchanged). Notebook-level analysis aggregation would need awareness of the new directory layout to group by study — currently they would treat every per-study log as an independent run.
- **Proposed fix:** update notebooks to `rglob("**/evaluation_log.jsonl")` with study_id parsed from parent dir name.
- **Why deferred:** notebook edits are outside the primary workflow; whoever next uses the notebooks will notice immediately.

### L2 — Stale historical references in reference docs

Remaining un-updated CatCMAwM mentions in:
- `docs/reference/quality-diversity.md` — `CatCMAEmitter` design sketch for a Phase 8+ QD reboot. Historical aspiration; the spec 24 removal note suffices as a read-forward warning. Leave as-is until QD is actively revisited.
- `docs/reference/literature-review.md` — literature review citing CatCMAwM. Leave as-is (literature review = historical context).
- `docs/reference/phase4-research-findings.md` — Phase 4 research log. Historical by definition.

### L3 — `cloud_worker_pool.py` teardown race (theoretical)

- **Location:** `src/starsector_optimizer/cloud_worker_pool.py:215-222` (`teardown`).
- **Finding (audit D):** Flask's `make_server(..., threaded=True)` spawns handler threads. Under campaign teardown, `self._stop_event.set()` + `self._server.shutdown()` may race with an in-flight `/result` handler that has just read `_result_events`; `run_matchup`'s `_results_lock` block at line 245-247 could `pop` an entry the handler thread then tries to `get`. The `get(..., None)` at line 182 is defensively nil-safe, so the worst case is the handler silently drops the result — no exception, no data-integrity loss.
- **Proposed fix:** none needed. The defensive `.get(..., None)` already handles the race; any refinement is premature.

---

## Meta-finding — testing gap revealed

All four bugs fixed this session (SG race, EB guard race, study_id collision, eval-log collision) share the pattern **"invariants hold under sequential execution but break under concurrent dispatch."** Unit tests with `fakeredis` + `moto` + `ThreadPoolExecutor` exercise the sequential path; they do not exercise the specific race windows.

Before any campaign at the Phase 7 prep scale (96 workers × 2 slots = 192 concurrent matchup slots), add a **concurrency shakedown stage** between Tier-2.5 smoke (3 workers × 2 slots) and prep. A 4-study dry-run at 16 slots/study on a cheap hull/regime (~$1, 15 min wall-clock) would have caught all four of this session's bugs simultaneously. Recommended gating:

```
Tier-2.0 smoke  (1 study  × 1 worker × 2 slots =  2 slots)  ← shipped
Tier-2.5 smoke  (1 study  × 3 workers × 2 slots =  6 slots)  ← shipped
Tier-3 shakedown (4 studies × 8 workers × 2 slots = 64 slots) ← GAP
Prep            (8 studies × 12 workers × 2 slots = 192 slots)
```

Proposed as a new §12 in `docs/reference/phase6-cloud-worker-federation.md` at the time Phase 7 prep is actually scheduled.

---

## Triage decisions (at time of writing, 2026-04-19)

| Item | Observed? | Actionable now? | Gating criterion to revisit |
|---|---|---|---|
| H1 TimeoutTuner dormant | N/A (never wired) | No — scope decision | User decides delete vs wire-in |
| H2 POST-before-register race | No — no retry path | No — unreachable | A retry path is introduced |
| H3 semaphore vs queue depth | No | No — not a bug | Queue depth > slots in prod |
| H4 built-in hullmods absent from heuristic | Code review only | Defer to Phase 7 kernel work | GP needs effective-stat correctness |
| M1 janitor ping-pong | No | Defensible low-cost fix | "requeued stuck matchup" > 1× per id |
| M2 test study_id literals | Cosmetic | Yes — low priority | Sampler plumbing is revisited |
| L1 notebooks | Cosmetic | Yes — low priority | Someone opens the notebooks |
| L2 doc staleness (QD / lit-review) | N/A (historical) | Only if QD is revisited | Phase 8 QD kick-off |
| L3 teardown race | No — nil-safe | No — not a bug | — |
| Testing-gap (concurrency shakedown) | Yes — root cause | Yes — spec-only | Phase 7 prep scheduling |
