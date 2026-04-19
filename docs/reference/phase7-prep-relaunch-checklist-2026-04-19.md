# Pre-Phase-7-prep relaunch checklist (2026-04-19)

Action items that must land before the next Phase 7 prep cloud campaign.

## Why this exists

The 2026-04-19 Phase 7 prep campaign launched across 8 hulls × 96 VMs × $70
budget and was aborted at T+48 min after winning-rate diagnostics revealed
the optimizer was not learning. Key postmortem findings (full analysis in
`experiments/phase7-prep-aborted-2026-04-19/`):

- Frigates (wolf, lasher) produced 0 wins across 1000+ matchups each
  because the observed between-build TWFE α̂ variance (τ̂²) was ~10⁵×
  smaller on frigates than on working hulls — no gradient for TPE.
- The hand-engineered `composite_score` heuristic was contributing
  11-22% of the final fitness signal via the EB regression prior
  (not <1% as previously believed). Its backing implementation
  (`compute_effective_stats` + `HULLMOD_EFFECTS`) is blind to ~90% of
  tier-1 hullmods — a structurally incomplete prior driving the signal.
- The `~/starsector-campaigns/<name>/ledger.jsonl` never got written
  because the `CampaignManager.monitor_loop` ledger-tick is a documented
  stub. The `budget_usd` hard cap was therefore never enforced.
- Janitor re-queues observed in practice on capital trials
  (onslaught_opt_000590 re-queued 3×, onslaught_opt_000579 2×) due to
  `enqueued_at` not being reset on re-dispatch.

Two deeper constraints pinned by the team:

1. **Fixed budget $85**. No local sim — all verification is cloud-only.
2. **No dropping popular hulls**. Frigates must work in Phase 7.

## Must-include items (safety + signal)

### 1. Wire up `CampaignManager.monitor_loop` ledger tick (budget safety)

**Location**: `src/starsector_optimizer/campaign.py:589-596`.

The supervisor's polling loop currently sleeps on
`ledger_heartbeat_interval_seconds` without calling
`self._ledger.record_heartbeat()`. Replace the stub with a loop that
reads per-worker heartbeat keys from Redis (`worker:<project_tag>:*:heartbeat`)
and writes one ledger row per worker per interval. Requires:

- `worker_agent.py` to publish `region` + `instance_type` in its Redis
  heartbeat payload (available via IMDSv2 at boot; not currently written).
- Spot-rate lookup per (region, instance_type) — use
  `AWSProvider.get_spot_price()` with per-region caching.
- `BudgetExceeded` raised inside `record_heartbeat` must propagate to
  `CampaignManager.run()`'s `finally:` teardown path.

**Verify**: new unit test mocks a Redis heartbeat stream + checks that
(a) ledger rows are written, (b) `BudgetExceeded` fires when
cumulative_usd ≥ budget_usd.

**Effort**: ~2 hours.

### 2. Janitor re-queue payload reset (M1)

**Location**: `src/starsector_optimizer/campaign.py:254-279`
(`run_janitor_pass`).

Before the `redis_client.lpush(source_list, raw)` on line 273:

```python
item["enqueued_at"] = now                    # reset the clock
item["requeue_count"] = item.get("requeue_count", 0) + 1
if item["requeue_count"] > MAX_REQUEUES:     # hard cap to surface broken matchups
    logger.error("matchup %s exceeded max requeues, dropping", item["matchup_id"])
    continue
redis_client.lpush(source_list, json.dumps(item))
```

Without this, a matchup that genuinely takes longer than
`visibility_timeout_seconds` gets re-queued every janitor interval
indefinitely, polluting the queue and inflating `requeue` log noise.

**Verify**: unit test simulating a slow matchup should see
`requeue_count` increment and the payload `enqueued_at` refresh each pass.

**Effort**: 15 minutes.

### 3. Drop `composite_score` from the EB covariate vector

**Location**: `src/starsector_optimizer/optimizer.py::_build_covariate_vector`.

Post-run analysis showed `composite_score` (the hand-crafted heuristic
scalar) contributes 11-22% of the regression prior mass via γ̂, but its
backing implementation (`compute_effective_stats`) ignores ~90% of
tier-1 hullmods and is hull-size-biased. Remove it from the 7-element
vector. Keep the function `heuristic_score` importable in
`scorer.py` — it remains useful for notebooks and debugging, just not
for optimization.

**Verify**: existing `test_optimizer.py` covariate-vector assertions
update to the new length (6 → see item 4 for added features).

**Effort**: 30 minutes.

### 4. Add 5 engine-truth covariates drawn from existing `CombatResult`

**Location**: `src/starsector_optimizer/optimizer.py::_build_covariate_vector`;
`src/starsector_optimizer/combat_fitness.py` if aggregation helpers are
needed.

All five signals are **already emitted** by the Java combat harness
today (`combat-harness/src/main/java/.../ResultWriter.java`). They're
in the `CombatResult` Python model but not fed into the covariate
vector:

| new covariate | source (already in `ShipCombatResult`) |
|--------------|----------------------------------------|
| `mean_damage_dealt_fraction` | `sum((1 - enemy_ship.hull_fraction)) / len(enemy_ships)` per matchup |
| `mean_seconds_survived` | per-ship `duration_seconds` weighted by `!destroyed` |
| `mean_cr_remaining` | `ShipCombatResult.cr_remaining` averaged |
| `mean_flameout_count` | `ShipCombatResult.flameouts` summed |
| `mean_overload_count` | `flux_stats.overload_count` summed |

These are simulation-derived (ground-truth from the combat engine),
vary per build within a hull, and contain no hand-engineered "what
makes a build good" prior. The lasso in `eb_shrinkage` will keep the
ones that actually predict fitness.

**Verify**: post-change unit test on `_build_covariate_vector` emits a
9-element vector (3 kept aggregates + 5 new + intercept slot); test
with a synthetic `CombatResult` populated with known field values.

**Effort**: 2 hours (primarily aggregation + tests; no new Java work).

### 5. Set `warm_start_n = 0` default in `OptimizerConfig`

**Location**: `src/starsector_optimizer/models.py::OptimizerConfig`,
field currently defaulted to `500`.

The heuristic warm-start pre-enqueues 500 Optuna trials with values
set to `heuristic_score × 0.1`. Under the current incomplete heuristic,
this biases TPE toward heuristic-preferred regions before any real
evaluation happens. Setting `warm_start_n = 0` lets TPE's
`n_startup_trials` random exploration (default 100) seed the search
from stock variants + random picks only. Stock-build warm-start
(from the game's own `.variant` files via `load_stock_builds`) stays
active — those are human-vetted anchors, not a synthesized prior.

**Verify**: campaign dry-run log shows
`"Warm-started study with N stock + 0 heuristic trials"`.

**Effort**: 15 minutes (1-line default change + test assertion update).

### 6. Tier-3 concurrency shakedown stage before prep

**Location**: new section in `docs/reference/phase6-cloud-worker-federation.md`;
new `examples/phase7-prep-shakedown.yaml`.

Specify a 4-study × 8-worker × 2-slot configuration (64 concurrent
matchup slots) on a cheap hull + regime that runs ~15 min and costs
~$1. Must run green immediately before any `examples/phase7-prep.yaml`
launch. Would have caught all four concurrent-dispatch bugs surfaced
2026-04-19 (SG-race, EB-guard-race, study_id-collision, eval-log
collision) at 1-2% of the prep budget.

Recommended stage ladder:

```
Tier-2.0 smoke  (1 study × 1 worker × 2 slots =  2 slots)
Tier-2.5 smoke  (1 study × 3 workers × 2 slots =  6 slots)
Tier-3  shakedown (4 studies × 8 workers × 2 slots = 64 slots) ← NEW
Prep            (8 studies × 12 workers × 2 slots = 192 slots)
```

Gate for prep launch: Tier-3 shakedown exits 0,
`final_audit.sh <name>` reports clean, all 4 studies have ≥1 COMPLETE
trial, ledger.jsonl has ≥1 heartbeat row.

**Effort**: 1 hour (YAML + doc section + CI wiring).

## Include if cheap

### 7. Delete `TimeoutTuner` module, spec, and tests

**Location**: `src/starsector_optimizer/timeout_tuner.py`,
`tests/test_timeout_tuner.py`, `docs/specs/21-timeout-tuner.md`, plus
the `__init__.py` export.

Dormant code (no production caller). The module is a hand-crafted
Weibull AFT survival model for tuning a single timeout parameter —
exactly the kind of baked-in domain knowledge we're removing from the
optimization loop (items 3-5). Matches the no-dead-code invariant.
Retain `matchup_timeout_seconds` as a fixed config; GP uncertainty
estimates absorb any future adaptive-timeout behaviour if needed.

**Verify**: `uv run pytest tests/` stays green; no imports of
`timeout_tuner` remain.

**Effort**: 30 minutes (delete + remove imports + re-run test suite).

### 8. Test-fixture `study_id` format cleanup (M2)

**Location**: `tests/test_cloud_provider.py:123,130`,
`tests/test_campaign.py:702-703`, `tests/test_cloud_userdata.py:24`,
`tests/test_cloud_worker_pool.py:58,141`,
`tests/test_worker_agent.py:22,123,142,167`.

The literal `"hammerhead__early__seed0"` in these test fixtures
doesn't match production `cloud_runner.py` output
(`"hammerhead__early__tpe__seed0"`). Purely cosmetic — tests pass
either way — but confusing for anyone grepping tests for a
representative study_id.

Fix: `sed -i 's/__seed/__tpe__seed/g'` across the listed files.

**Effort**: 5 minutes.

## Phase 7 kernel design additions (land with Phase 7, not here)

These are captured for the Phase 7 kernel design doc, not pre-relaunch
work:

- **Heteroscedastic GP noise structure** (RAHBO, Makarova et al. 2021
  [arXiv:2111.03637](https://arxiv.org/abs/2111.03637); hetGP, Binois
  & Gramacy). Adopt a class-indexed noise term in the composed kernel
  so the GP auto-downweights flat-landscape hulls (e.g. frigates at
  τ̂²≈2e-7) without dropping them from the training set. Validated
  empirically in `experiments/phase7-layer34-benchmark-2026-04-19/`
  Layer 3 results: recovers per-hull σ² to within 0.06 log-orders on
  a 4-hull synthetic; homoscedastic GP is 0.35–2.35 orders off and
  inflates flat-hull noise estimates 220× above truth.

- **TurBO-style trust-region restart** (Eriksson et al. 2019,
  [arXiv:1910.01739](https://arxiv.org/abs/1910.01739)). Documented as
  a future option, not load-bearing. Benchmark on a 6D
  flat-plateau-with-narrow-peak synthetic (analog of the frigate
  pathology) showed only a marginal 2× improvement over vanilla EI
  with both methods far from the true peak — heteroscedastic noise
  already gives us the "notice this hull is flat" signal without the
  trust-region bookkeeping. Revisit with real data once items 3-5
  land.

## Out of scope (explicitly considered and not included)

- **Expanding `HULLMOD_EFFECTS`** to cover the ~26 tier-1 hullmods not
  currently in the registry. Rejected under the bitter-lesson heuristic
  (Sutton 2019) — hand-encoding the combat effects of 20+ hullmods is
  domain knowledge that rots on every game update / modded hull.
  Replacement path is items 3-4: let the simulator carry the signal,
  drop the hand-built scorer from the optimization loop. Python
  `heuristic_score` stays importable for notebook-level analysis.

- **Excluding "non-combat" hullmods** (cargo / fuel / crew-recovery /
  survey) from the `early` regime. Rejected because the optimizer's
  output guides real player builds where OP is allocated across combat
  AND campaign logistics. A player can't actually fly a no-fuel-tanks
  wolf across the sector map. The simulator must reason about
  combat-effective builds that include logistics choices, not pretend
  logistics doesn't exist.

- **Dropping frigates or other flat-landscape hulls** from the
  Phase 7 prep set. Rejected by product constraint (popular hulls).
  Heteroscedastic GP is the Phase-7 mechanism for keeping them in the
  training set without polluting the posterior.

- **Expanding the total budget beyond $85**. Constraint pin.

- **Local sim runs before cloud launch**. Constraint pin.

## Validation gate before next prep launch

Before `scripts/cloud/launch_campaign.sh examples/phase7-prep.yaml`:

1. `uv run pytest tests/ -v` — full suite green including new tests
   from items 1-5.
2. `scripts/cloud/launch_campaign.sh examples/phase7-prep-shakedown.yaml`
   (item 6) — exit 0, clean final_audit, `ledger.jsonl` has rows,
   `BudgetExceeded` path tested via a low `budget_usd` in the shakedown YAML.
3. Dry-run `uv run python -m starsector_optimizer.campaign --dry-run
   examples/phase7-prep.yaml` — loads, validates regime filters,
   prints expected covariate vector length (9, not 7).
4. AWS + Redis preflight clean.

## Budget envelope

| stage | cost estimate | cumulative |
|-------|---------------|------------|
| Tier-2.5 smoke (already shipped) | ~$0.20 | $0.20 |
| Tier-3 shakedown (item 6) | ~$1 | $1.20 |
| Phase 7 prep (8 hulls × ~$8) | ~$64 | $65 |
| buffer for re-queues / retries | ~$15 | $80 |

Below the $85 hard budget envelope. Ledger-cap (item 1) enforces this
as a runtime guarantee, not a spec intent.
