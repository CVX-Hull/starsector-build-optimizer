---
type: spec
status: shipped
last-validated: 2026-07-12
---

# Cloud Deployment Specification

Phase 6 Cloud Worker Federation. Runs bulk combat simulation on AWS spot VMs while the workstation keeps every Optuna Study local. Defined in `src/starsector_optimizer/campaign.py`, `cloud_provider.py`, `cloud_worker_pool.py`, `worker_agent.py`, and `scripts/cloud/`.

## Topology

Workstation is the sole orchestrator. Every Optuna Study runs in a `run_optimizer.py --worker-pool cloud` subprocess on the workstation; `study.ask()` / `study.tell()` never cross the network. Workers on cloud VMs are pure evaluators: they pull `MatchupConfig` messages from a Redis queue, drive a local `LocalInstancePool(num_instances=2)` to produce `CombatResult`, and `POST /result` back to their study's Flask listener on the workstation. All orchestrator ↔ worker traffic rides a Tailscale mesh network; Redis and Flask are never exposed to the public internet.

```
workstation (single machine)                         cloud VM (N of them)
┌──────────────────────────────────────┐             ┌────────────────────────────────┐
│ CampaignManager (supervisor)         │             │ worker_agent.py main loop      │
│   _preflight (tailnet/redis/STS)     │             │   BRPOPLPUSH <queue> <proc>    │
│   spawn/kill study subprocesses      │             │   run LocalInstancePool (×2)   │
│   CostLedger (JSONL, fsync'd)        │             │   POST /result (bearer+dedup)  │
│   terminate_all_tagged backstop      │             │   HSET worker:<id>:heartbeat   │
│   atexit teardown                    │             │                                │
│                                      │◄──tailscale─┤                                │
│ study subprocess (×N per campaign):  │    redis    │ LocalInstancePool              │
│   Optuna Study (SQLite, local)       │    flask    │   Xvfb :100, :101              │
│   provider.provision_fleet  (owns)   │             │   Starsector JVM (×2)          │
│   CloudWorkerPool                    │             │   combat harness mod           │
│     BoundedSemaphore(N_total_slots)  │             │   N Redis consumer threads     │
│     Flask POST /result listener      │             │                                │
│     janitor thread (requeue stuck)   │             │                                │
│   provider.terminate_fleet  (owns)   │             │                                │
└──────────────────────────────────────┘             └────────────────────────────────┘
```

**Fleet ownership**: the study subprocess owns its own fleet — provisioning AND teardown. `CampaignManager` is a pure supervisor (spawn + monitor + sweep-backstop via `terminate_all_tagged`). Per-study fleet ownership is load-bearing: each study's workers carry that study's bearer token + Redis queue keys + Flask endpoint in their UserData. A campaign-wide fleet would force every worker to carry every study's secrets. (Honest-eval's `main()` additionally gains an in-context *partial*-termination path — the drain thread, §"Worker drain (honest-eval)" — but ownership stays singular: it is the same process that owns the fleet, and the final teardown is idempotent over already-terminated ids.)

## Config dataclasses

All frozen. Defined in `src/starsector_optimizer/models.py`.

### `StudyConfig`

One row in the campaign YAML's `studies:` list. `seeds=(0, 1, 2)` fans out into three Optuna studies sharing the other settings.

| Field | Type | Description |
|---|---|---|
| `hull` | `str` | Hull ID (e.g. `hammerhead`) |
| `regime` | `str` | Phase 5F regime name (`early` / `mid` / `late` / `endgame`) |
| `seeds` | `tuple[int, ...]` | One Optuna study per seed |
| `budget_per_study` | `int` | Absolute trial cap per study |
| `workers_per_study` | `int` | TPE saturates above 24 |
| `sampler` | `str` | `tpe` (only accepted value; see spec 24 for the CatCMAwM removal rationale) |
| `active_opponents` | `int \| None` | Optional per-study override of `OptimizerConfig.active_opponents` (default 10). Smaller values shrink ASHA rungs per trial — `active_opponents: 1` makes each trial complete after one returned matchup (used by `examples/smoke-campaign.yaml` to guarantee ≥1 `TrialState.COMPLETE` within the smoke's 10-minute gate). `None` uses the optimizer default. Plumbed into the study subprocess via `--active-opponents` when non-None. |
| `warm_start_from_regime` | `str \| None` | Optional cross-regime carry (Phase 5F mechanism 13b). When set to a regime name different from `regime`, the spawn dispatches `--warm-start-from-regime <name>` to the study subprocess. The source Optuna study (`{hull}__{warm_start_from_regime}`) must already exist in the per-study SQLite file derived by `spawn_studies` — operator's responsibility to seed that DB before launch (typically `cp` from a prior campaign's study DB, see "Per-study SQLite layout" below). `_parse_studies` rejects `warm_start_from_regime == regime`. `None` (default) skips the carry. |

### `GlobalAutoStopConfig`

Mirrors the YAML `global_auto_stop:` nested block.

| Field | Type | Default | Description |
|---|---|---|---|
| `on_budget` | `str` | `"hard"` | `"hard"` = SIGTERM children at cap; `"soft"` = warn only |
| `on_plateau` | `bool` | `True` | Enable plateau termination (plateau detector deferred; first MVP ignores) |

### `CampaignConfig`

Top-level campaign descriptor, loaded from YAML. Immutable after `load_campaign_config` returns. `__repr__` redacts `tailscale_authkey_secret` to `"***REDACTED***"`. Never pickled across subprocesses; child processes re-parse the YAML.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Campaign identifier; validated by `load_campaign_config` against `^[a-zA-Z0-9._-]{1,64}$` so `f"starsector-{name}__{fleet_name}"` always satisfies AWS LT name rules. Used as AWS resource tag `Project=starsector-<name>` |
| `budget_usd` | `float` | required | Hard cap; `CostLedger.record_heartbeat` raises `BudgetExceeded` at `cumulative_usd >= budget_usd` |
| `provider` | `str` | required | `"aws"`; `"hetzner"` raises `NotImplementedError` |
| `regions` | `tuple[str, ...]` | required | e.g. `("us-east-1", "us-east-2")` |
| `instance_types` | `tuple[str, ...]` | required | e.g. `("c7a.2xlarge", "c7i.2xlarge", "c7a.4xlarge", "c7i.4xlarge")` |
| `spot_allocation_strategy` | `str` | required | `"price-capacity-optimized"` |
| `fleet_type` | `str` | `"instant"` | EC2 Fleet type. `"instant"` (default) = one-shot fleet, no persistent resource, reclaimed spot is **never** replaced (correctness preserved by the Redis janitor + `matchup_id` dedup, throughput is not). `"maintain"` = persistent fleet that relaunches reclaimed spot to hold `TargetCapacity` — opt-in for long runs (honest-eval, campaign studies). Validated against `_ALLOWED_FLEET_TYPES = frozenset({"instant","maintain"})`; other values raise `ValueError`. learned-batch + probe are always `"instant"` (their self-terminate/boot-test models are respawn-incompatible). |
| `capacity_rebalancing` | `bool` | required | EC2 Fleet CapacityRebalance flag. **Honored only under `fleet_type="maintain"`**, where it adds `SpotOptions.MaintenanceStrategies.CapacityRebalance` (`ReplacementStrategy="launch"`) so the fleet proactively replaces instances AWS flags as at elevated interruption risk. A **no-op under `fleet_type="instant"`** (instant fleets have no maintenance strategy). |
| `max_concurrent_workers` | `int` | required | Total VMs across all studies |
| `min_workers_to_start` | `int` | required | Partial-fleet floor; validator enforces `<= max_concurrent_workers`. **Enforced** in `cloud_runner.prepare_cloud_pool` after `provision_fleet`: `len(instance_ids) < min_workers_to_start` triggers `partial_fleet_policy` |
| `partial_fleet_policy` | `str` | required | `"proceed_half_speed"` (warn + continue on a partial fleet) or `"abort"` (raise). Enforced in `prepare_cloud_pool` (see `min_workers_to_start`) |
| `ami_ids_by_region` | `dict[str, str]` | required | Populated exactly once by `load_campaign_config`; grep invariant forbids post-load mutation |
| `ssh_key_name` | `str` | required | Pre-registered AWS key pair name |
| `tailscale_authkey_secret` | `str` | required | Injected into cloud-init; redacted from `__repr__`. Supports `${VAR}` env-substitution — if value starts with `${` and ends with `}`, `load_campaign_config` resolves via `os.environ`. Missing env var → `ValueError`. Substitution is SCOPED to this field only. |
| `studies` | `tuple[StudyConfig, ...]` | required | |
| `global_auto_stop` | `GlobalAutoStopConfig` | `GlobalAutoStopConfig()` | |
| `max_lifetime_hours` | `float` | `6.0` | Worker self-terminates at this age; sized as an operational safety cap rather than a target runtime |
| `visibility_timeout_seconds` | `float` | `120.0` | Redis processing-list timeout |
| `janitor_interval_seconds` | `float` | `60.0` | How often the study subprocess sweeps stuck items |
| `worker_poll_margin_seconds` | `float` | `5.0` | Subtract from `visibility_timeout_seconds` for worker BRPOPLPUSH timeout |
| `fleet_provision_timeout_seconds` | `float` | `600.0` | EC2 Fleet provision window. Under `fleet_type="maintain"` it bounds the `describe_fleet_instances` poll that waits for the async fleet to launch toward its per-region target before `provision_fleet` returns; the returned instance-ID count then feeds the `min_workers_to_start`/`partial_fleet_policy` decision. (Under `instant` the response is synchronous, so this is the transient-error retry window.) Threaded into `provision_fleet` as `provision_timeout_seconds` (the `CloudProvider` ABC takes no `CampaignConfig`). |
| `result_timeout_seconds` | `float` | `900.0` | `CloudWorkerPool.run_matchup` blocks at most this long |
| `ledger_heartbeat_interval_seconds` | `float` | `60.0` | How often `CampaignManager.monitor_loop` appends ledger rows |
| `ledger_warn_thresholds` | `tuple[float, ...]` | `(0.5, 0.8, 0.95)` | Budget fractions at which a WARN log fires |
| `base_flask_port` | `int` | `9000` | Base for per-study result listener ports; study at `(study_idx, seed_idx)` listens on `base_flask_port + study_idx * flask_ports_per_study + seed_idx` |
| `redis_port` | `int` | `6379` | Workstation Redis port; shared by preflight ping and by `WorkerConfig.redis_port` in spawned children |
| `redis_preflight_timeout_seconds` | `float` | `2.0` | `_preflight` Redis ping timeout — covers only the tailnet-binding-check, not campaign-wide connectivity |
| `matchup_slots_per_worker` | `int` | `2` | Concurrent matchup slots per VM. The worker spawns this many Redis consumer threads sharing one `LocalInstancePool` so every JVM stays busy. Total pool concurrency = `workers_per_study × matchup_slots_per_worker`. c7a.2xlarge (8 vCPU) fits 2 JVMs (per-JVM core consumption pending V2 re-validation) |
| `flask_ports_per_study` | `int` | `100` | Ceiling on Flask result-listener ports allocated per `study_idx`. Matches the `tcp:9000-9099` range documented in `.claude/skills/cloud-worker-ops.md` preflight gate 2. Per-seed ports are `base_flask_port + study_idx * flask_ports_per_study + seed_idx` |
| `game_dir` | `str` | `"game/starsector"` | Orchestrator-side path to the Starsector install. Study subprocesses load game data here for constraint-aware sampling + opponent-pool construction; workers run the JVM from their AMI-baked `/opt/starsector` |
| `teardown_retry_delay_seconds` | `float` | `10.0` | Wait before retrying `terminate_all_tagged` in `CampaignManager.teardown` when `list_active` still shows workers |
| `teardown_thread_join_seconds` | `float` | `5.0` | Bound on `CloudWorkerPool.teardown` waits on the Flask server thread + janitor thread |
| `spot_price_cache_ttl_seconds` | `float` | `300.0` | In-process `(region, instance_type) → rate` cache lifetime in `CostHeartbeatTicker` (§"Ledger tick"). Prevents one `DescribeSpotPriceHistory` call per tick per worker at steady-state. Cache miss or TTL expiry re-fetches. |
| `max_requeues` | `int` | `5` | Janitor hard cap. A matchup whose next visibility-timeout breach would push `requeue_count` above this value is dropped (LREM from processing, NOT re-LPUSH to source) with an ERROR log. Catches permanently stuck matchups without pathological ping-pong. |
| `heartbeat_stale_multiplier` | `int` | `3` | Heartbeats whose `timestamp > heartbeat_stale_multiplier × ledger_heartbeat_interval_seconds` old are treated as dead and skipped by `CostHeartbeatTicker.tick` — no cost accrues. Dead-key clean-up happens at teardown via `_flush_stale_campaign_keys`. The honest-eval drain (§"Worker drain (honest-eval)") reuses this multiplier for its liveness cutoff, but against `WORKER_HEARTBEAT_INTERVAL_SECONDS` (the true write cadence), not the ledger-tick interval. |
| `drain_poll_interval_seconds` | `float` | `60.0` | How often the honest-eval `WorkerDrainTicker` (§"Worker drain (honest-eval)") scans for idle-surplus workers to terminate. Honest-eval-only; the campaign path has no fleet drain. Must be listed in the `load_campaign_config` pass-through opt tuple so operator YAML overrides are honored. |

### `WorkerConfig`

Injected by cloud-init as env vars at VM boot. Read once; worker treats as immutable. `load_worker_config_from_env` iterates `dataclasses.fields(WorkerConfig)` + `typing.get_type_hints(WorkerConfig)` to read + coerce every field from `STARSECTOR_WORKER_<FIELD_UPPER>`. Unknown coercion target → `TypeError` (loud-fail, forward-compat).

| Field | Type | Default | Description |
|---|---|---|---|
| `campaign_id` | `str` | required | Matches `CampaignConfig.name` |
| `study_id` | `str` | required | `f"{hull}__{regime}__{sampler}__seed{n}"` — composed in `cloud_runner.run_cloud_study`. Sampler segment present defensively so two studies differing only in sampler never collide on fleet/LT/SG/Redis-key names (forward-compat; with TPE the sole allowed sampler per spec 24, collisions can't arise in practice). |
| `project_tag` | `str` | required | `f"starsector-{campaign_name}"`. Scopes Redis queue keys (`queue:{project_tag}:{study_id}:source`) + worker heartbeat keys (`worker:{project_tag}:{worker_id}:heartbeat`). Stops stale state from one campaign leaking to the next even when study_ids repeat. |
| `redis_host` | `str` | required | Workstation's Tailscale address |
| `redis_port` | `int` | required | Matches `CampaignConfig.redis_port` (default 6379) |
| `http_endpoint` | `str` | required | `f"http://{workstation}:{port}/result"` |
| `bearer_token` | `str` | required | Redacted from `__repr__`; per-study, generated by `CampaignManager._generate_study_env` |
| `max_lifetime_hours` | `float` | `6.0` | |
| `http_retry_count` | `int` | `3` | POST retry attempts |
| `http_retry_base_seconds` | `float` | `1.0` | Exponential backoff start |
| `http_retry_max_seconds` | `float` | `30.0` | Backoff cap |
| `http_retry_backoff_multiplier` | `float` | `2.0` | Backoff multiplier |
| `http_post_timeout_seconds` | `float` | `30.0` | `requests.post` timeout |
| `worker_poll_margin_seconds` | `float` | `5.0` | BRPOPLPUSH timeout = `visibility_timeout_seconds - worker_poll_margin_seconds` |
| `matchup_slots_per_worker` | `int` | `2` | Number of concurrent Redis consumer threads spawned on the VM. Each shares one `LocalInstancePool` with `num_instances=matchup_slots_per_worker`, so each thread holds exactly one JVM. Without threading the VM would use only 1 JVM regardless of `num_instances`. |
| `worker_id` | `str` | `""` | EC2 instance ID. **Placeholder at render time** — cloud-init overwrites via IMDSv2 before `systemctl start`. Moved to end-of-dataclass so earlier required fields stay positional-required. |

### `CostLedgerEntry`

One JSONL row in `data/campaigns/<name>/ledger.jsonl`. All fields primitive and secret-free.

| Field | Type | Description |
|---|---|---|
| `timestamp` | `str` | ISO-8601 UTC (`datetime.now(timezone.utc).isoformat()`) |
| `event_type` | `str` | `"worker_heartbeat"` \| `"worker_terminated"` \| `"campaign_end"` |
| `worker_id` | `str` | |
| `region` | `str` | |
| `instance_type` | `str` | |
| `hours_elapsed` | `float` | Minutes-to-hours for this interval |
| `delta_usd` | `float` | |
| `cumulative_usd` | `float` | Monotone |

## Reliable-queue protocol

Each study subprocess owns two Redis lists:
- `queue:<project_tag>:<study_id>:source` — matchups awaiting a worker
- `queue:<project_tag>:<study_id>:processing` — matchups claimed by a worker but not yet ack'd via `POST /result`
- `worker:<project_tag>:<worker_id>:heartbeat` — Redis hash written every `WORKER_HEARTBEAT_INTERVAL_SECONDS` (30s) by the worker with fields: `timestamp`, `load_avg_1min`, `load_avg_5min`, `load_avg_15min`, `cpu_count`, `region`, `instance_type`, `game_log_tail`, `mod_jar_sha256`, `active_matchups`. `active_matchups` is the worker's count of currently-running matchups (incremented on BRPOPLPUSH claim, decremented after the run attempt completes — success-then-ack or failure-then-leave-for-janitor); it is the honest-eval drain's idle signal (`active_matchups == 0` ⇒ fully idle). **Backward-compat:** a heartbeat WITHOUT `active_matchups` (a worker on an AMI baked before this field existed) is treated by the drain as *busy/unknown* and never terminated — so the drain is a safe no-op until the worker AMI is re-baked. The load averages let the orchestrator verify `matchup_slots_per_worker` fits the VM shape — on c7a.2xlarge (8 vCPU), the healthy `load_avg_1min` target band is design-set at [3, 8]. Persistent `load_avg_1min > cpu_count` indicates over-subscription; `< 3` indicates under-utilization. `region` and `instance_type` are fetched from IMDSv2 at worker startup (cached in `_WORKER_VM_METADATA`; fallback `"unknown"` on IMDS failure — the resulting zero-rate ledger row is self-identifying). `game_log_tail` is the concatenated last ~32 KiB of every per-instance `game_stdout.log` on this VM — the only orchestrator-side window into a hung JVM, since worker SGs grant zero ingress and `tailscale ssh` is unreliable from userspace-mode workstations. `mod_jar_sha256` is the SHA-256 of the loaded `combat-harness.jar`; the orchestrator's janitor scans it for fleet-wide consistency (see §"Diagnostic checks").

Keys are namespaced by `project_tag` (= `starsector-<campaign_name>`) so a re-run of a campaign whose study_ids happen to match a prior run's never inherits stale processing-list items. `CampaignManager._preflight` additionally SCANs and DELs `queue:<project_tag>:*` + `worker:<project_tag>:*` at startup to defend against same-campaign re-launch.

Worker main loop:

```
item = BRPOPLPUSH source processing  (timeout = visibility_timeout_seconds - worker_poll_margin_seconds)
result = LocalInstancePool(...).run_matchup(item)
POST /result {matchup_id, result, bearer_token}   # retries from WorkerConfig
on 200: LREM processing 1 item
on 409: LREM processing 1 item  (already-received dedup; silently drop)
on 422: LREM processing 1 item  (corrupt result rejected; orchestrator wakes dispatcher to retry fresh combat)
on 401: terminate worker (crypto invariant violation)
on repeated 5xx/network: let visibility_timeout expire → janitor re-queues
```

Study-subprocess janitor thread (runs every `janitor_interval_seconds`):

```
for item in LRANGE processing 0 -1:
    if (now - item.enqueued_at) > visibility_timeout_seconds:
        LREM processing 1 item
        item.requeue_count = item.get("requeue_count", 0) + 1
        if item.requeue_count > config.max_requeues:
            logger.error("matchup %s exceeded max_requeues=%d; dropping",
                         item.matchup_id, config.max_requeues)
            # NOT re-LPUSHed — permanently broken matchups surface as
            # an ERROR log and a missing trial, not a ping-pong loop.
            continue
        item.enqueued_at = now  # reset timer so the next visibility
                                # breach is measured from re-queue, not
                                # the original enqueue (guards against
                                # ping-pong under steady-state slow
                                # matchups — audit finding M1, 2026-04-19)
        LPUSH source item
        logger.warning("requeued stuck matchup: study=%s matchup_id=%s requeue_count=%d",
                       ..., item.requeue_count)
```

`max_requeues` and the `enqueued_at` reset landed 2026-04-19 as
part of the Phase-7-prep refactor. Before the reset, a matchup that
consistently took longer than `visibility_timeout_seconds` to evaluate
would re-queue on every janitor pass forever; without `max_requeues`,
a deterministically-broken matchup would block a trial's completion
indefinitely.

Idempotency key is `MatchupConfig.matchup_id`, which `CampaignManager` sets to `f"{study_id}__{trial_number}__{opponent_id}"` before enqueue. Globally unique across all studies.

## HTTP protocol

Per-study Flask listener on
`config.base_flask_port + study_idx * config.flask_ports_per_study + seed_idx`
exposes exactly one route:

```
POST /result
  body: {matchup_id: str, result: CombatResult-JSON, bearer_token: str}
  200: first observation; result registered, waiter notified via threading.Event
  409: duplicate POST; either matchup_id already observed OR duplicate
       corrupt-result body already rejected. Dedup silently drops on the
       worker side; if a dispatcher is currently waiting on the duplicate
       corrupt-result class, it is still woken with the matching retryable
       failure before 409 is returned.
  401: bearer_token mismatch; no entry added
  400: malformed result; no entry added
  422: corrupt result rejected; orchestrator does NOT add to `_seen` and
       does NOT store the result. Two corrupt-result classes use this
       terminal status:
       - `LOADOUT_MISMATCH`: diagnostic field shows weapon/hullmod/flux
         corruption. A waiting dispatcher is woken with
         `LoadoutMismatchRejected`.
       - `matchup_id_mismatch`: POST envelope `matchup_id` and parsed
         `CombatResult.matchup_id` differ, which means the worker is
         replaying a stale result for a new Redis assignment. A waiting
         dispatcher is woken with `ResultEnvelopeMismatchRejected`.
       Workers treat 422 and duplicate 409 as terminal and ack the Redis
       item so the same corrupt POST is not replayed. CloudWorkerPool tracks the running
       discard rate and raises `LoadoutMismatchAbort` from `run_matchup`
       when the rate exceeds `MISMATCH_ABORT_RATE` (5%) after
       `MISMATCH_ABORT_MIN_SAMPLES` (100) observations — surfaces a
       regressed Java fix, stale worker image, or stale jar instead of
       silently retrying every matchup.
  404: any other path or method
```

No `GET`, no `PATCH`, no `PUT`, no admin route, no static files. Test `test_http_listener_rejects_non_result_routes` enforces this at every release.

## Cost ledger

Append-only JSONL at `data/campaigns/<name>/ledger.jsonl`. Every write is followed by `file.flush()` + `os.fsync(file.fileno())` to prevent torn lines on crash. The durability requirement is part of the contract; throughput and cost measurements belong in dated reports.

Warning logs fire at each `ledger_warn_thresholds` (default 50%/80%/95%) — once per threshold, never repeated. Hard cap at `budget_usd`: `record_heartbeat` raises `BudgetExceeded`, which `CampaignManager.run()` catches in a `try/finally` to trigger teardown and **returns exit code 0** (designed termination, not failure — wrapper scripts running multiple budget-capped cells back-to-back depend on this to advance to the next cell). Other failure modes keep distinct non-zero exit codes: preflight failure → 2, KeyboardInterrupt / SIGTERM / SIGHUP → 130, unexpected exception → propagates.

**Optional budget (`budget_usd: float | None`).** `budget_usd=None` puts the
ledger in **measurement-only** mode: `record_heartbeat` appends rows and
advances `cumulative_usd` but never warns and never raises `BudgetExceeded`.
This exists for consumers that must *measure* spend without a hard cap — the
honest-eval path ([spec 30 §"Cost measurement"](30-honest-evaluator.md)), which
deliberately has no per-eval budget. The `CampaignManager` campaign path always
passes a concrete `float` (`config.budget_usd`), so its warn-and-cap behavior is
unchanged. `CostLedger.__init__` also accepts `initial_cumulative: float = 0.0`
to seed `cumulative_usd` — used by measurement-only consumers to keep the column
monotone across an appended-to ledger on resume (default `0.0` preserves the
campaign path). `cumulative_usd` is monotone within a single ledger lifetime; a
consumer reading total realized spend across resumes should use `sum(delta_usd)`
(always correct) or read the last row only when the ledger was seeded on resume.

**`budget_usd` is a hard ceiling, not a target.** Wave 1 surfaced an operator footgun: a flat per-cell budget can truncate cells before their trial-count design floor, making downstream gate thresholds mis-interpretable (decision recorded 2026-05-10). The principled operator contract: **size `budget_usd` as `expected_cost × 1.5` headroom**, where expected_cost = trials × matchups_per_trial × per_matchup_cost. The 1.5× cushion absorbs spot-price spikes and worker-restart overhead without hitting the cap. Studies designed against trial counts MUST budget for trials, not flat dollar amounts. Future config option `min_trials_before_budget_cap` was considered and deferred — keeping budget purely-as-ceiling avoids a "two ways to do it" config surface; the trial-floor concern lives in operator math, not the framework.

After every major optimization run that uses this infrastructure, the operator runs the **honest evaluator** ([spec 30](30-honest-evaluator.md), [`scripts/cloud/evaluate_campaign.sh`](../../scripts/cloud/evaluate_campaign.sh)) before publishing report findings — see [`honest-evaluation`](../../.claude/skills/honest-evaluation.md) skill for the SOP. The evaluator dispatches via the same `EvaluatorPool` ABC defined in this spec and reuses `cloud_runner.prepare_cloud_pool` for the per-eval fleet (separate `starsector-honest-eval-{name}-{utc}` namespace from the source campaign).

### Ledger tick (2026-04-19)

The per-tick attribution logic is owned by **`CostHeartbeatTicker`**
(`campaign.py`, beside `CostLedger`) — a reusable unit that holds the
per-worker `_last_tick_ts` and the `(region, instance_type) → (rate,
fetched_at)` spot-price cache, and exposes `tick(now: float | None = None)`.
`CampaignManager` constructs one after its Redis client + `CostLedger` exist
(in `_preflight`) and delegates: `monitor_loop` calls `self._cost_ticker.tick()`
every `ledger_heartbeat_interval_seconds` (guarded so a `monitor_loop` reached
without a successful preflight is a no-op, matching the prior
`if self._redis is None: return`). The honest-eval orchestrator
([spec 30 §"Cost measurement"](30-honest-evaluator.md)) reuses the same ticker
from its own background loop. The mechanics below are unchanged; they describe
one `tick()`:

1. SCAN `worker:<project_tag>:*:heartbeat` via the ticker's Redis client.
   That client **must** be built with `decode_responses=True` (as
   `CampaignManager._preflight` does) — the hash reads below assume `str`
   keys/values, and a bytes-returning client would read `timestamp` as missing,
   treat every worker as stale, and record zero rows.
2. For each key, HGETALL and read `timestamp`, `region`, `instance_type`.
   Skip heartbeats older than
   `heartbeat_stale_multiplier × ledger_heartbeat_interval_seconds`
   (dead worker).
3. Compute `hours_elapsed = min(interval_seconds, now - last_tick_ts[worker_id]) / _SECONDS_PER_HOUR`
   and update `self._last_tick_ts[worker_id] = now`. Per-worker last-
   tick state is orchestrator-local (`self._last_tick_ts: dict[str, float]`);
   on orchestrator restart we resume from `now`, losing at most one
   interval of attribution — the warn-threshold set and hard cap
   absorb that.
4. Look up `rate_usd_per_hr` via
   `self._get_spot_price_cached(region, instance_type)`. The cache
   holds `(rate, fetched_at)` tuples; entries expire after
   `spot_price_cache_ttl_seconds`, on which the next lookup calls
   `self._provider.get_spot_price(region, instance_type)` and
   refreshes.
5. Call `ledger.record_heartbeat(...)`. In the campaign path (concrete
   `budget_usd`), `BudgetExceeded` propagates out of `tick()` → out of
   `monitor_loop` → caught by `run()`'s top-level `try/finally`, which triggers
   teardown. In a measurement-only consumer (`budget_usd=None`),
   `record_heartbeat` never raises.

Module-level constant `_SECONDS_PER_HOUR = 3600.0` guards against
bare `3600.0` literals in function bodies (project invariant).

### Diagnostic checks (2026-05-10)

`CloudWorkerPool._janitor_loop` runs two diagnostic checks alongside
the reliable-queue janitor pass. Both are diagnostic-only — neither
aborts dispatch — but both surface failure modes that previously cost
significant operator time to diagnose.

**Stalled-progress detector.** If no `/result` POST has arrived for
`STALLED_PROGRESS_WARN_SECONDS` (default 600s = 10 min) AND the source
queue or processing list is non-empty, the janitor logs WARN with the
source queue length, processing list length, and a sample of
in-flight matchup IDs (bounded to `STALLED_PROGRESS_INFLIGHT_SAMPLE_COUNT`
= 10 via `LRANGE 0,9`). `_last_post_at` is updated on every accepted
(200) and discarded (422) `/result`, so any incoming POST counts as
progress regardless of outcome. Debounced via `_stalled_warn_emitted`
so a sustained stall logs once, not once per janitor tick. The
detector was added after the 2026-05-10 stall incident so future runs
surface the condition in the orchestrator log.

**Mod-jar fleet-consistency check.** Workers report `mod_jar_sha256`
(SHA-256 of their loaded `combat-harness.jar`) in heartbeat. The
janitor scans heartbeats and logs WARN if more than one distinct SHA
appears in the fleet — including a `"missing"` bucket for heartbeats
that don't carry the field at all (pre-2026-05-10 worker code).
Heterogeneous fleets produce results from different code paths and
must not be silently combined; the WARN tells the operator to
investigate before publishing findings. Debounced via
`HETEROGENEOUS_JAR_WARN_INTERVAL_SECONDS` (default 300s = 5 min).
Catches the failure mode introduced by `serve_mod_jar.sh` tailnet
overrides — without the consistency check, a partial override that
some workers picked up and others didn't would silently corrupt the
fleet's results.

### Flask-port preflight + study-exit detection (2026-07-15)

Two guards against a Flask result-port collision silently dropping studies (root
cause of the 2026-07-15 accounting-run partial loss — two campaigns launched
concurrently sharing `base_flask_port`, so `study_idx` 0/1/2 mapped to the same
ports 9000/9100/9200 in both):

1. **Preflight port check** (`_preflight` → `_check_flask_ports_free`). Before
   `spawn_studies`, probe (`socket.bind(("0.0.0.0", p))`, no `SO_REUSEADDR`, to
   match `make_server`) every port `_campaign_flask_ports(config)` will bind — the
   exact `base_flask_port + study_idx * flask_ports_per_study + seed_idx` per
   `(study_idx, seed_idx)` pair, NOT the whole `flask_ports_per_study` ACL range.
   An occupied port → `PreflightFailure` → exit 2, before any fleet is
   provisioned. **Best-effort defense-in-depth, not a guarantee:** the real bind
   is in-subprocess minutes later, so a concurrent campaign that has not yet bound
   passes this probe and then races. `cloud_runner.prepare_cloud_pool` adds an
   in-subprocess `_probe_flask_port_free(flask_port)` immediately before
   provisioning, converting the raw `EADDRINUSE` from `make_server` into a
   diagnosable failure without wasting a fleet.
2. **Study-exit detection** (`monitor_loop` → `_report_study_exits`). The
   guarantee against silent loss: after all study subprocesses exit,
   **every** `returncode != 0` (including negative signal-kills) is logged at
   ERROR and recorded in `self._failed_studies` — gated on the return code, NOT
   on study-DB existence (a mid-run crash leaves a partial DB, which is an
   annotation only). Does not auto-reschedule.

**Prevention** (operational, no code): concurrent campaigns on one workstation
must use **distinct `base_flask_port`** ranges, or launch **sequentially** — see
`.claude/skills/cloud-worker-ops.md`.

### Manifest + AMI tag preflight (2026-04-19, expanded 2026-05-10)

`CampaignManager._check_manifest_and_ami_tags` runs alongside the
other preflight checks. It:
1. Loads `GameManifest.load()` from `game/starsector/manifest.json`.
   Schema-version mismatch raises `ValueError` with remediation
   pointing at `scripts/update_manifest.py`.
2. Delegates the actual cross-check to the module-level helper
   `check_ami_tags_against_manifest(provider, ami_ids_by_region,
   manifest)`. For every `(region, ami_id)` it calls
   `provider.describe_ami_tag(ami_id=..., region=..., tag_key="GameVersion")`
   plus `tag_key="ManifestSha256"` and `tag_key="ModCommitSha"` and asserts
   they equal `manifest.constants.game_version`, the local manifest file hash,
   and `manifest.constants.mod_commit_sha`. It also reads
   `tag_key="WorkerSourceSha"` and asserts it equals the expected worker-source
   tag. Mismatch
   raises `PreflightFailure` (a `ValueError` subclass) with
   remediation: re-bake AMI after the relevant data/source update. The
   source tag is required because Java JAR overlay can update the
   combat-harness jar without updating Python worker code baked under
   `/opt/starsector-optimizer/src`.
3. Caches the loaded manifest on `self._manifest` for subprocess
   env plumbing (study subprocesses re-load from disk; the cache
   is orchestrator-internal).

The module-level helper is reused by `honest_evaluator._preflight_for_honest_eval`
(spec 30 §Preflight) so honest-eval has the same protection against
silent oracle corruption when an operator regenerates the manifest
without re-baking the AMI. Providers that don't implement
`describe_ami_tag` (Hetzner stub, test fakes) cause the helper to
log a warning and return — preserves backward compatibility for
non-AWS preflight paths.

Helper signature:

```python
def check_ami_tags_against_manifest(
    provider: CloudProvider,
    ami_ids_by_region: dict[str, str],
    manifest: GameManifest,
    *,
    required_regions: tuple[str, ...] | list[str] | None = None,
) -> None:
    """For every (region, ami_id) in `ami_ids_by_region`:
      1. Read `provider.describe_ami_tag(ami_id, region, "GameVersion")`
         and assert it equals `manifest.constants.game_version`.
      2. Read `provider.describe_ami_tag(ami_id, region, "ManifestSha256")`
         and assert it equals sha256 of `game/starsector/manifest.json`.
      3. Read `provider.describe_ami_tag(ami_id, region, "ModCommitSha")`
         and assert it equals `manifest.constants.mod_commit_sha`.
      4. Reject empty/`"unknown"` `mod_commit_sha` (Commit G R6 dual-check
         — Gradle's `generateBuildInfo` task stamps the git SHA into the
         jar, ManifestDumper embeds it into the manifest, Packer reads it
         back for the AMI tag; a missing value means the chain broke
         upstream and the AMI cannot be trusted).
      5. Read `provider.describe_ami_tag(ami_id, region, "WorkerSourceSha")`
         and assert it equals the SHA-256 digest of the worker-source input
         set copied into the AMI (`src`, `pyproject.toml`, `uv.lock`, and
         cloud bake/Packer scripts). Dirty debug launches with
         `STARSECTOR_ALLOW_DIRTY_AMI_LAUNCH=1` expect `<digest>-dirty`.
         Documentation-only commits do not change the digest. This prevents
         Python-only fixes from being skipped by the Java-only JAR override
         path without forcing rebakes for docs/config-only commits.
      6. If `required_regions` is supplied, assert every configured
         region has an explicit `ami_ids_by_region` entry before launch.
    Raises `PreflightFailure` (a `ValueError` subclass) on any mismatch
    or missing tag. Returns silently with WARN log if `provider` raises
    `AttributeError` from `describe_ami_tag` (Hetzner stub / test fakes).
    """
```

Companion public helpers (same module, same exception type) — both
reused by `honest_evaluator._preflight_for_honest_eval`:

```python
def check_aws_credentials() -> None:
    """STS get_caller_identity probe; raises PreflightFailure on auth fail."""

def check_authkey_syntax(authkey: str) -> None:
    """Validate `authkey.startswith('tskey-auth-')`; raises PreflightFailure."""
```

The AWS AMI tags are set by the Packer template:
`GameVersion`, `ManifestSha256`, and `ModCommitSha` are read from the committed
manifest; `WorkerSourceSha` is passed by `scripts/cloud/bake_image.sh` from the
current worker-source input digest. The bake script refuses dirty AMI inputs
(`src`, `pyproject.toml`, `uv.lock`, `game/starsector/manifest.json`, and cloud
Packer/bake scripts) unless `STARSECTOR_ALLOW_DIRTY_AMI_BAKE=1` is set. Dirty
debug bakes are tagged
`<digest>-dirty`, not the clean digest, so they cannot masquerade as production
AMIs. Launch/honest-eval preflight runs the same dirty-source check and rejects
dirty launches unless `STARSECTOR_ALLOW_DIRTY_AMI_LAUNCH=1` is set. See spec 29
for the manifest-as-oracle contract.

## Worker IMDSv2 metadata fetch

`worker_agent._fetch_vm_metadata()` runs once at worker startup:
1. IMDSv2 PUT `/latest/api/token` with
   `X-aws-ec2-metadata-token-ttl-seconds: 300`.
2. GET `/latest/meta-data/placement/region`.
3. GET `/latest/meta-data/instance-type`.
4. Cache result in `_WORKER_VM_METADATA` module dict.

On any failure (IMDS unreachable, HTTP error, timeout) the helper
logs ERROR (not WARN — this is a cloud-worker misconfiguration)
and populates `{"region": "unknown", "instance_type": "unknown"}`.
`AWSProvider.get_spot_price("unknown", "unknown")` returns 0.0 from
its miss-path, yielding a self-identifying zero-rate ledger row
instead of silently under-attributing cost.

## Teardown discipline

Four layers from innermost to outermost:

1. **Study subprocess `try/finally`** — `run_optimizer.py --worker-pool cloud` wraps its work in `try:` and `finally: provider.terminate_fleet(fleet_name=study_id, project_tag=project_tag)`. Pool `__exit__` runs first (Flask + janitor shutdown), then fleet teardown.
2. **CampaignManager in-process `try/finally`** — `CampaignManager.run()` body in `try:`; `finally:` calls `provider.terminate_all_tagged(project_tag)` as a sweep backstop for any study subprocess that crashed before its own teardown ran. Asserts `provider.list_active(project_tag) == []` with one retry after `config.teardown_retry_delay_seconds`.
3. **`atexit.register(self.teardown)`** — registered in `CampaignManager.__init__`, runs on crash paths that bypass `finally`.
4. **Shell-level `trap EXIT`** in `launch_campaign.sh` — re-runs `teardown.sh` + `final_audit.sh` unconditionally and exits non-zero if any resource leaked.

`final_audit.sh` checks all 4 US regions (not just `regions:`) for any instance tagged `Project=starsector-<campaign-name>` or security groups / volumes / launch templates tagged the same, **plus any `submitted|active|modifying` EC2 Fleet whose `Tags` match** (the highest-stakes leak: a live maintain fleet keeps launching billable spot forever and, unlike an orphaned instance, re-creates instances the audit terminated). Fleet discovery is `describe-fleets` filtered by `fleet-state` (no server-side tag filter exists) with a client-side `Project` tag match, `NextToken`-paginated; a failed `describe-fleets` sets the inconclusive/exit-2 path, a match sets the leak/exit-1 path.

### One-shot AWS batch runners

One-shot AWS batch runners that reuse `AWSProvider` but do not use the
combat-worker Redis queue still inherit the cloud safety contract:

1. **Preflight before provisioning.** Launch paths call
   `check_aws_credentials()`, `check_authkey_syntax(authkey)`, and
   `check_ami_tags_against_manifest(provider, ami_ids_by_region,
   GameManifest.load(), required_regions=regions)` before
   `provider.provision_fleet(...)`.
2. **Unique ownership tags.** Every batch uses a unique
   `project_tag` and one explicit `fleet_name`. Batch-owned cleanup may use
   `terminate_all_tagged(project_tag)` because no other workload shares that
   project tag.
3. **Teardown command first.** The launch CLI prints the exact targeted
   teardown command before creating any AWS resource.
4. **Budget ledger.** The batch config contains `budget_usd`,
   `max_lifetime_hours`, `ledger_heartbeat_interval_seconds`, and warn
   thresholds. The orchestrator appends durable secret-free JSONL rows under
   the batch output directory and terminates the batch-owned fleet when the
   hard ceiling is reached.
5. **Layered teardown.** Launch wraps orchestration in `try/finally`,
   registers `atexit` cleanup, handles SIGINT/SIGTERM, calls
   `terminate_fleet(fleet_name, project_tag)` on normal exit, and calls
   `terminate_all_tagged(project_tag)` as the crash-recovery sweep.
6. **Final audit.** The operator-facing completion path includes
   `scripts/cloud/final_audit.sh <campaign-or-batch-name>` or an equivalent
   provider audit over all project-supported AWS regions. The current scripts
   audit all four US regions.
7. **Authenticated control plane.** Any local HTTP control plane used by the
   batch requires a per-batch bearer token on every route that serves bundles,
   leases work, accepts events, accepts results, or reports status. Missing or
   wrong tokens return 401 and do not mutate state.
8. **UserData security.** Custom batch UserData keeps the same fail-closed
   invariants as combat-worker UserData: `set -euo pipefail`, `umask 077`,
   Tailscale authkey through a 0600 file followed by `shred -u`, no
   `--ssh`, no secret logging, IMDSv2 instance ID capture, baked worker
   service disabled before custom work, bounded lifetime, and explicit result
   upload failure handling.
9. **Scale-down-on-drain.** Idle batch workers are cost with no throughput
   (evidence: [2026-07-12 tail-walltime analysis](../reports/2026-07-12-phase7-tail-walltime.md)),
   so the batch drains its fleet instead of idling it:
   - The control plane's `/lease` route answers an empty queue with a
     drained verdict — `200 {"status": "drained"}`. It never emits `204`
     for an empty queue; workers keep a defensive sleep-retry for
     `204`/empty-body responses because transport failures still produce
     them client-side.
   - On the drained verdict a worker posts a `worker_drained` worker-event
     and immediately runs `shutdown -h now` — the same trusted terminator
     every worker failure path uses. The `worker_drained` post is
     load-bearing (pending-instance reconciliation depends on it), so it
     retries transient failures (`result_upload_attempts` ×
     `result_upload_retry_seconds`, the same budget as the result upload)
     instead of being a single fire-and-forget curl. In the worker's lease
     loop, the drained-verdict check precedes any job-field parse of the
     response body. The deadline-too-close check is evaluated BEFORE
     requesting a lease — a doomed worker must not burn a lease attempt and
     strand a job for `lease_grace_seconds` — and its branch likewise shuts
     down immediately after posting its event rather than idling to the
     bootstrap-scheduled shutdown.
   - Precondition: fleets are provisioned `Type="instant"`, so AWS does not
     respawn self-terminated instances. **learned-batch is therefore always
     `instant` and MUST NOT opt into `fleet_type="maintain"`**: its workers
     self-terminate on the drained `/lease` verdict, which a maintain fleet
     would relaunch — an unbounded respawn loop that never converges. The
     opt-in maintain fleet (§Config `fleet_type`) is reserved for the
     Redis-queue paths (honest-eval, campaign studies) whose drain/teardown
     were rewired to be respawn-safe.
   - Monitor accounting: the control plane records `worker_drained` instance
     IDs, and the monitor reconciles them out of pending-instance
     accounting, so a replacement that boots, drains, and self-terminates
     between polls cannot trip the pending-grace abort. The
     all-jobs-completed merge is evaluated before the pending-grace abort.
     The no-active-workers abort requires zero leased jobs in addition to
     zero active and zero pending instances — leased jobs waiting out
     `lease_grace_seconds` are a recoverable state (the monitor-side
     counterpart of the spec 31 rule that a stale active-instance snapshot
     cannot steal a lease), and its failure message carries the pending and
     leased counts so zero-capacity provisioning is diagnosable as such.
   - Named trade-off (accepted): a spot reclaim that lands after the fleet
     has drained is no longer absorbed by a warm idle worker; recovery costs
     `lease_grace_seconds` plus a full replacement bootstrap. Accepted
     because the idle-tail spend is per-run-certain while late reclaims are
     rare, and correctness is unaffected.

## Worker drain (honest-eval)

The honest-eval fleet (spec 30) dispatches over the Redis BRPOPLPUSH reliable
queue, so the learned-batch drain above (an HTTP-`/lease` worker-self-terminate
model with replacement) does **not** transfer. Honest-eval instead drains
**orchestrator-driven**: a background `WorkerDrainTicker` — **defined in
`campaign.py` and constructed by `honest_evaluator._make_worker_drain_thread`**
(the campaign-analog owner, the same role it plays for the cost tick) —
terminates provably-idle surplus workers as the outstanding matchup count falls
below fleet capacity near end-of-run. Under `fleet_type="instant"` the fleet is
**static** (one `provision_fleet` call, reclaimed spot never replaced); under
`fleet_type="maintain"` the fleet self-replenishes reclaimed spot during the
bulk phase and the drain lowers `TargetCapacity` to shed the idle tail (see
"Respawn-safety under `fleet_type`" below).

**Respawn-safety under `fleet_type` (§Config).** The drain's mechanism depends
on the fleet type, resolved from `campaign.fleet_type` threaded into
`WorkerDrainTicker`:

- **`instant`:** AWS does not respawn a terminated instance, so external
  `terminate_instances` on the idle surplus permanently shrinks the fleet — the
  original behavior, unchanged.
- **`maintain`:** a bare `terminate_instances` would be *relaunched* by the
  fleet. So the maintain branch, per region with idle surplus, first
  `list_fleets_by_tag(project_tag, fleet_name, region)` (cached) →
  `modify_fleet_target(fleet_id, new_target, excess_policy="no-termination")`
  where **`new_target = max(0, len(live_in_region) − k_region)` is computed from
  the ticker's already-observed live count, NOT from the fleet's reported
  `TargetCapacity`** (a spot reclaim leaves `TargetCapacity` stale-high, so
  targeting off it would sit above the true live count and respawn — the exact
  failure this drain exists to avoid). `no-termination` means the fleet neither
  relaunches toward the lowered target nor self-selects victims; the drain then
  `terminate_instances` on the same precisely-chosen idle ids as the instant
  path. Net: precise idle-only selection AND no respawn.

The global keep-floor (`keep = max(1, ceil(remaining / matchup_slots_per_worker))`)
and never-terminate-a-busy-worker guarantees below are identical for both fleet
types; maintain only *adds* the target-lower step before the terminate.

**Interaction with `max_lifetime_hours` (named limitation).** A maintain fleet
replaces *reclaimed/interrupted* instances, not instances whose worker *process*
self-exited at `max_lifetime_hours` — the process ends but the instance keeps
running (idle), so the fleet sees capacity unchanged and does not refresh it.
For honest-eval this does not arise (spec 30 raises `max_lifetime_hours` to
cover the whole eval); for a campaign study opted into maintain it matches
today's instant behavior (aged idle instance lingers until teardown).
Process-liveness-driven replacement is out of scope.

Contract:

1. **Remaining work is Python-side, never Redis depth.** `evaluate_builds`
   caps in-flight dispatch at `num_workers` (= `total_matchup_slots`), so the
   Redis source+processing depth ≈ concurrency throughout and is NOT the
   backlog. The drain reads outstanding matchups (`total − completed`) from a
   `MatchupProgress` sink that `evaluate_builds` updates; a Redis-depth drain
   would misfire at t=0 and kill the fleet.

2. **Keep-floor liveness invariant.** With `remaining` outstanding matchups
   and `matchup_slots_per_worker` slots per worker,
   `keep = max(1, ceil(remaining / matchup_slots_per_worker))` while
   `remaining > 0`. The drain terminates only workers in the *idle* set
   (`active_matchups == 0` in a live-fresh heartbeat) and at most
   `surplus = max(0, live − keep)` of them. It never terminates a busy worker
   and never drops the live count below `keep`, so surviving capacity always
   covers the outstanding + any janitor-requeued matchups. At `remaining == 0`
   it terminates nothing — normal `prepare_cloud_pool` teardown owns the final
   shutdown.

3. **Idle identification.** `worker_id == the EC2 instance-id` (IMDSv2 override
   at worker boot), so the heartbeat key carries the instance to terminate.
   The `active_matchups` heartbeat field (§"Redis key schema") is the idle
   signal; an absent field (pre-field AMI) reads as busy, making the drain a
   no-op until the worker AMI is re-baked.

4. **Source-empty gate + tick order.** A tick runs Redis-only work first and
   touches the EC2 API only when something is actually terminable: (a)
   `remaining <= 0` → return; (b) `llen(source) > 0` → return (defer while any
   matchup is queued-but-unclaimed); (c) scan heartbeats for live-fresh
   `active_matchups == 0` idle ids, and if none → return **before**
   `list_active` (an un-re-baked fleet issues zero `DescribeInstances`); (d)
   `list_active(project_tag)` for the authoritative live set, terminate the
   idle∩live surplus grouped by region. Freshness cutoff =
   `WORKER_HEARTBEAT_INTERVAL_SECONDS × heartbeat_stale_multiplier` (sized
   against the true write cadence, not the ledger-tick interval).

5. **Named trade-off (accepted): claim-race → bounded requeue latency.**
   `active_matchups` is sampled at the worker heartbeat cadence (~30s),
   independent of the tick, so a live-fresh heartbeat can carry an idle
   snapshot up to one cadence old; a worker that claims in that window and is
   then terminated strands up to `matchup_slots_per_worker` matchups (per
   terminated worker) in the processing list until the janitor requeues them,
   after which a keep-worker runs them. **Correctness is preserved with high
   probability, not unconditionally**: repeated stranding of the same matchup
   is bounded by two independent ceilings — the janitor drops past
   `max_requeues`, after which `run_matchup` raises `WorkerTimeout` and
   consumes one of `evaluate_builds`' `max_retries_per_matchup` attempts;
   exhausting those aborts the eval. The keep-floor (which leaves many
   keep-workers while `surplus > 0`) plus the source-empty gate make this
   pathological path very unlikely; the primary mitigation is the source-empty
   gate, not the freshness filter (which controls timestamp age, not
   occupancy-snapshot age).

6. **Fleet ownership** (§"Fleet ownership") stays singular: the drain runs
   inside honest-eval's own `main()` process, and the later
   `terminate_fleet` + project sweep is idempotent over already-terminated
   ids. Operator escape hatch: `--no-drain` (spec 30).

## `CloudProvider` ABC

```python
class CloudProvider(abc.ABC):
    @abc.abstractmethod
    def provision_fleet(
        self, *,
        fleet_name: str,                       # per-fleet unique; e.g. "probe" or "<study_id>"
        project_tag: str,                      # e.g. "starsector-<campaign>"; used for campaign-wide sweep
        regions: Sequence[str],
        ami_ids_by_region: dict[str, str],
        instance_types: Sequence[str],
        ssh_key_name: str,
        spot_allocation_strategy: str,         # "price-capacity-optimized"
        target_workers: int,
        user_data: str,                        # cloud-init script (caller-rendered)
        root_volume_size_gb: int | None = None,
        fleet_type: str = "instant",           # "instant" | "maintain"
        capacity_rebalance: bool = False,      # honored only when fleet_type="maintain"
        provision_timeout_seconds: float = 600.0,  # bounds the maintain async instance-poll
    ) -> list[str]: ...
        # returns instance IDs. Under fleet_type="maintain", create_fleet is async:
        # captures response["FleetId"], tags the fleet resource (both Project + Fleet),
        # polls describe_fleet_instances toward the per-region target (bounded by
        # provision_timeout_seconds), and returns the full discovered set.

    @abc.abstractmethod
    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int: ...
        # targeted teardown: reaps resources tagged BOTH project_tag AND fleet_name.
        # Under maintain, deletes the matching fleet resource(s) FIRST (delete_fleets,
        # TerminateInstances=True) before the instance/LT/SG backstop passes, so the
        # fleet cannot relaunch what the backstop terminates.

    @abc.abstractmethod
    def terminate_all_tagged(self, project_tag: str) -> int: ...
        # sweep: reaps everything tagged project_tag regardless of fleet. Crash-recovery backstop.
        # Under maintain, deletes matching fleet resource(s) FIRST (Project-tag match).

    @abc.abstractmethod
    def list_fleets_by_tag(
        self, project_tag: str, fleet_name: str | None = None, *, region: str
    ) -> list[str]: ...
        # FleetIds of fleets in fleet-state ∈ {submitted, active, modifying} whose Tags
        # match Project=project_tag (AND Fleet=fleet_name when given). describe_fleets has
        # no server-side tag filter, so the match is client-side over Fleets[].Tags.
        # The discovery primitive for maintain teardown + leaked-fleet audit + drain.

    @abc.abstractmethod
    def delete_fleets(
        self, fleet_ids: Sequence[str], *, region: str, terminate_instances: bool = True
    ) -> int: ...
        # delete-fleets on an explicit FleetId list. terminate_instances=True kills the
        # fleet's instances atomically. Empty ids → 0, no API call. Idempotent.

    @abc.abstractmethod
    def modify_fleet_target(
        self, fleet_id: str, target: int, *, region: str, excess_policy: str
    ) -> None: ...
        # modify-fleet TotalTargetCapacity=target. Used by the maintain drain to lower a
        # regional fleet's target (excess_policy="no-termination" so the fleet neither
        # relaunches nor self-terminates; the caller then terminates the chosen idle ids).

    @abc.abstractmethod
    def terminate_instances(self, instance_ids: Sequence[str], *, region: str) -> int: ...
        # terminate an explicit subset of instance IDs in one region; empty ids → 0, no API call.
        # The only subset-termination primitive (all other terminate paths are tag-scoped whole-fleet).
        # Used by the honest-eval WorkerDrainTicker (§"Worker drain (honest-eval)"). Idempotent:
        # terminating an already-terminating id is an AWS no-op.

    @abc.abstractmethod
    def list_active(self, project_tag: str) -> list[dict]: ...
        # RUNNING+PENDING instances with tag Project=project_tag

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float: ...
```

No `CampaignConfig` parameter — the provider is cloud-mechanical, not campaign-aware. Callers (study subprocess, `probe.py`) compose the call from explicit fields.

### Two-tag scheme

Every resource (instance, LT, SG — plus the **fleet resource itself under `fleet_type="maintain"`**) carries BOTH tags:
- `Project=<project_tag>` — e.g. `starsector-smoke`. Enables `terminate_all_tagged(project_tag)` sweep.
- `Fleet=<fleet_name>` — e.g. `hammerhead__early__seed0`. Enables `terminate_fleet(fleet_name, project_tag)` targeted reap.

Under `instant` there is **no persistent fleet resource** to tag (the fleet evaporates after returning instances), so only instance/LT/SG carry the tags. Under `maintain` the persistent fleet is tagged at `create_fleet` time (`TagSpecifications` `ResourceType:"fleet"`, both keys) so `list_fleets_by_tag` can rediscover it for teardown and the leaked-fleet audit purely from AWS state — no on-disk FleetId manifest.

LT/SG NAMES are `f"{project_tag}__{fleet_name}"`, e.g. `starsector-smoke__hammerhead__early__seed0`. Unique per fleet → multiple studies in the same region don't collide.

### `AWSProvider`

boto3-direct. Credentials loaded from the standard AWS credential chain — never stored in Python.

`provision_fleet(...)`:

1. **Per region**: ensure SG named `f"{project_tag}__{fleet_name}"` exists with all egress allowed, zero ingress (workers are outbound-only). Tag it with both `Project` and `Fleet`. After `create_security_group` returns, **block on the `security_group_exists` boto3 waiter** (`Delay=2s`, `MaxAttempts=10`) so the SG is present in `describe_security_groups` before any dependent call references it. Under concurrent provisioning (N studies racing to `provision_fleet` simultaneously) the waiter alone is insufficient — Fleet's internal replication lags describe-visibility — so `_create_fleet_in_region` also retries on transient errors; see step 3.
2. **Per region**: ensure LT named `f"{project_tag}__{fleet_name}"` exists with:
   - `ImageId` = `ami_ids_by_region[region]`
   - `KeyName` = `ssh_key_name`
   - `SecurityGroupIds` = `[<sg from step 1>]`
   - `InstanceMarketOptions={MarketType: spot}`
   - `UserData` = `base64(user_data)`
   - `BlockDeviceMappings` = `[{DeviceName: "/dev/sda1", Ebs: {DeleteOnTermination: true}}]` (prevents volume audit leak)
   - `TagSpecifications` on `instance` and `volume` include both `Project` and `Fleet`
   - If an LT with the same name already exists, create a new version and `modify_launch_template(DefaultVersion=...)` — LT versions are immutable once referenced.
3. Fire one `ec2.create_fleet(SpotOptions={AllocationStrategy: spot_allocation_strategy, ...}, Type=fleet_type)` per region, diversified across `instance_types`.
   - **`fleet_type="instant"` (default):** the response is synchronous — `response["Instances"]` carries the launched IDs. `TagSpecifications` tag `ResourceType:"instance"` (both tags); there is no persistent fleet resource. **Retry up to `_FLEET_PROVISION_MAX_RETRIES=4` times, separated by `_FLEET_PROVISION_RETRY_DELAY_SECONDS=3.0`, when the response contains ANY `InvalidGroup.NotFound` / `InvalidSecurityGroupID.NotFound` error** (the `any(...)` predicate — not `all(...)` — because permanent per-AZ errors like `InvalidFleetConfiguration` when `c7a.2xlarge` is unsupported in `us-east-1e` commonly co-occur with transient SG-visibility errors on the other AZs, and we want to retry so the non-1e AZs succeed). Emit zero-instances failure only if the retry budget is exhausted or no error is transient.
   - **`fleet_type="maintain"`:** `create_fleet` is **asynchronous** — `response["Instances"]` is empty/partial; capture `response["FleetId"]`. `TagSpecifications` additionally tag `ResourceType:"fleet"` (both tags) so the persistent fleet is rediscoverable by `list_fleets_by_tag`. When `capacity_rebalance`, `SpotOptions` also carries `MaintenanceStrategies={CapacityRebalance: {ReplacementStrategy: _CAPACITY_REBALANCE_REPLACEMENT_STRATEGY="launch"}}`. After the create call, poll `describe_fleet_instances(FleetId)` every `_FLEET_INSTANCE_POLL_INTERVAL_SECONDS` until `len(active_instance_ids) >= per_region_target` OR `provision_timeout_seconds` elapses, then return **all** discovered active IDs. Returning the full set (not an early ≥1) keeps the caller's `min_workers_to_start`/`total_matchup_slots` sizing correct; a genuine capacity shortfall surfaces to `partial_fleet_policy` exactly as with instant. The transient SG-visibility retry still guards the create call, and a `FleetId`-with-partial-`Errors` response logs a WARN (matching the instant partial-error log). Because `provision_fleet` iterates regions sequentially, worst-case maintain provision wall time is `len(regions) × provision_timeout_seconds` (each region may poll the full timeout) — materially longer than instant's transient-retry budget; size `fleet_provision_timeout_seconds` accordingly.

`terminate_fleet(fleet_name, project_tag)`: per region — **under maintain, FIRST `list_fleets_by_tag(project_tag, fleet_name, region=…)` (BOTH tags) → `delete_fleets(ids, terminate_instances=True)` so the persistent fleet cannot relaunch drained/terminated instances** — then filter by BOTH tags → terminate any straggler instances → delete LT (by name) → delete SG (by name, with ENI-detach retry loop: `_SG_DELETE_DEADLINE_SECONDS=300.0`, `_SG_DELETE_POLL_INTERVAL_SECONDS=10.0`). Under instant, `list_fleets_by_tag` returns empty → the fleet step is a no-op and the path is unchanged. Idempotent. (The pre-maintain documented order — terminate instances *before* deleting the fleet — is respawn-unsafe and is superseded by this fleet-first ordering.)

`terminate_all_tagged(project_tag)`: per region — **under maintain, FIRST `list_fleets_by_tag(project_tag, region=…)` (`Project` ONLY) → `delete_fleets(ids, terminate_instances=True)`** — then filter by `Project` tag ONLY → terminate all tagged straggler instances → delete every LT matching the tag (tag-filter `describe_launch_templates`) → delete every SG matching the tag. Idempotent.

`list_active(project_tag)`: per region, instances in `pending` or `running` state with tag `Project=project_tag`.

### Cloud-init UserData

`src/starsector_optimizer/cloud_userdata.py::render_user_data(worker_config, *, tailscale_authkey, debug_ssh_pubkey="", mod_jar_override_url="", mod_jar_override_sha256="") -> str` emits a bash payload that:

1. `set -euo pipefail` + `umask 077` so every file created by the script is owner-read-only and any command failure halts the script before `systemctl start`.
2. `tailscale up --auth-key=file:"$TS_AUTHKEY_FILE" --advertise-tags=tag:starsector-worker --accept-dns=false`. The authkey is written to a 0600 tmpfile (owner-only via `umask 077`), passed by path, then `shred -u`'d. Modern Tailscale CLI no longer supports `--authkey-stdin`; `--auth-key=file:` is the equivalent argv-free mechanism — only the path appears on `/proc/<pid>/cmdline`. **`--ssh` is intentionally not passed** (smoke #8 2026-05-09): it hijacks port 22 on the worker for tailscaled's identity-based SSH server, gates connections via the tailnet ACL, and a default-permissive personal tailnet still silent-denies SSH — so enabling it would shadow the regular sshd and prevent any operator access. Operator SSH instead goes through the optional debug-pubkey injection path described below.
3. **(Optional) Debug SSH pubkey injection.** If `debug_ssh_pubkey` is non-empty (whitespace-stripped), appends the pubkey to `/home/ubuntu/.ssh/authorized_keys` (mode 0600, owner `ubuntu:ubuntu`) so the operator can `ssh -i <matching-private-key> ubuntu@<worker-tailnet-ip>` into a hung worker. The pubkey is the only operator-SSH path in the absence of Tailscale ACL configuration. Empty string skips the block entirely. Caller side: `cloud_runner.run_cloud_study` reads `STARSECTOR_DEBUG_SSH_PUBKEY` from `os.environ` and threads it through; production runs leave the env var unset.
4. Writes `/etc/starsector-worker.env` via a quoted heredoc with every `WorkerConfig` field mapped to `STARSECTOR_WORKER_<FIELD>` (every field in `dataclasses.fields(WorkerConfig)`, including the placeholder `worker_id=""`). Owner is `root:root`; mode `0600` is inherited from `umask 077`.
5. `chown root:root /etc/starsector-worker.env`.
6. **IMDSv2 WORKER_ID override block** (inserted between `chown` and `systemctl daemon-reload`; see §Worker ID resolution below). Fetches the live EC2 instance ID, overwrites the placeholder line in the env file. If IMDS is unreachable, `curl --fail` + `set -euo pipefail` halts the script BEFORE `systemctl start` — the worker never boots with `worker_id=""`.
7. **(Optional) JAR-overlay block.** When *both* `mod_jar_override_url` and `mod_jar_override_sha256` are non-empty, emits a `curl --fail | sha256sum --check | install -m 0644` block that downloads the JAR over the tailnet, verifies the digest, and overlays the AMI-baked `/opt/starsector/mods/combat-harness/jars/combat-harness.jar` before `systemctl start`. Fail-closed by design — any download error, digest mismatch, or chown failure halts boot via `set -euo pipefail` so workers never run against the wrong JAR. `_validate_jar_override` (called from `render_user_data`) raises `ValueError` if exactly one of the two values is set (no silent verification skip). Both empty → block is omitted entirely; the AMI-baked JAR runs unchanged. Caller side: `cloud_runner.run_cloud_study` reads `STARSECTOR_MOD_JAR_OVERRIDE_URL` + `STARSECTOR_MOD_JAR_OVERRIDE_SHA256` from `os.environ` and threads them through. Production runs leave both env vars unset; the Java-only fast-iteration loop populates them via `scripts/cloud/serve_mod_jar.sh --env`.
8. `systemctl daemon-reload && systemctl start starsector-worker.service` (the service unit is baked into the AMI; see `scripts/cloud/packer/starsector-worker.service`).

The renderer is a pure function — takes a frozen `WorkerConfig` + a string authkey + an optional debug pubkey + an optional URL/SHA pair, returns a string. No I/O. Lives in its own module (not `cloud_provider.py`) so providers other than AWS can reuse it.

### Worker ID resolution (IMDSv2)

`WorkerConfig.worker_id` defaults to `""` at render time because the EC2 instance ID is unknown until after `provision_fleet` returns. The UserData script overrides the placeholder at boot:

```bash
IMDS_TOKEN=$(curl --silent --fail -X PUT \
    -H "X-aws-ec2-metadata-token-ttl-seconds: ${_IMDSV2_TOKEN_TTL_SECONDS}" \
    http://169.254.169.254/latest/api/token)
INSTANCE_ID=$(curl --silent --fail \
    -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
    http://169.254.169.254/latest/meta-data/instance-id)
sed -i '/^STARSECTOR_WORKER_WORKER_ID=/d' /etc/starsector-worker.env
echo "STARSECTOR_WORKER_WORKER_ID=$INSTANCE_ID" >> /etc/starsector-worker.env
```

`_IMDSV2_TOKEN_TTL_SECONDS = 300` is a module-level constant. IMDSv1 is NEVER used (SSRF risk). `sed -i` + append guarantees exactly one `STARSECTOR_WORKER_WORKER_ID=` line — no last-write-wins ambiguity. IMDS unreachable (dev VM, broken networking) → script halts at `curl --fail` → `systemctl start` never runs → worker never boots with empty ID.

For probe scenarios where no real worker is needed, `render_probe_user_data(campaign_id) -> str` returns a minimal script that writes a single boot marker line — `probe-boot-ok campaign_id=<id> <UTC timestamp>` — to `/var/log/starsector-probe.log`. The probe tests fleet lifecycle, not worker connectivity.

## Per-study SQLite layout

Every spawned study subprocess writes its Optuna trial state to its own SQLite file at `data/study_dbs/<campaign.name>/<study_id>.db` (created on demand by `spawn_studies`; `study_id` follows `cloud_runner.resolve_study_id` — `{hull}__{regime}__{sampler}__seed{seed_value}`). One DB per (campaign, study_idx, seed_idx) tuple is load-bearing:

- The Optuna study-name namespace is `{hull}__{regime}` (no sampler, no seed). Sharing one DB across ablation cells (C0a/C0b/C1/C2/C3 of the same hull+regime) would collapse them into one Optuna study via `load_if_exists=True` and destroy per-cell trial isolation.
- Sharing one DB across seeds within a single cell would do the same, eliminating the variance estimate seeds are designed to provide.
- Per-(study, seed) DBs hold ≤16 concurrent writers (the matchup-slot ceiling per study), well below the SQLite-Optuna contention cliff at ~32 (R8 in `docs/reports/2026-05-10-validation-plan.md`).

**Cross-regime warm-start carry** (mechanism 13b): the `--warm-start-from-regime <name>` flag — set by `spawn_studies` when `StudyConfig.warm_start_from_regime` is non-None — requires the source study `{hull}__{name}` to already exist in the same SQLite file as the target. Across campaigns, the operator carries it explicitly: `cp data/study_dbs/<source_campaign>/<source_study_id>.db data/study_dbs/<target_campaign>/<target_study_id>.db` before launch. `campaign.py` does not bake cross-campaign relations into the YAML — the carry is shell-visible, not config-buried.

## Per-study fleet lifecycle

Each study subprocess (`scripts/run_optimizer.py --worker-pool cloud`) owns its fleet end-to-end:

1. `_require_env` reads `STARSECTOR_WORKSTATION_TAILNET_IP`, `STARSECTOR_BEARER_TOKEN`, `STARSECTOR_TAILSCALE_AUTHKEY`, `STARSECTOR_PROJECT_TAG` — raises `ValueError` with remediation pointer if any missing.
2. Constructs `WorkerConfig` with per-study bearer token (already in env), tailnet-based `redis_host` + `http_endpoint`, `worker_id=""` placeholder.
3. Renders UserData via `render_user_data(worker_cfg, tailscale_authkey=authkey, debug_ssh_pubkey=os.environ.get("STARSECTOR_DEBUG_SSH_PUBKEY", "").strip(), mod_jar_override_url=os.environ.get("STARSECTOR_MOD_JAR_OVERRIDE_URL", "").strip(), mod_jar_override_sha256=os.environ.get("STARSECTOR_MOD_JAR_OVERRIDE_SHA256", "").strip())`.
4. Calls `provider.provision_fleet(fleet_name=study_id, project_tag=project_tag, ...)`.
5. Enters `with CloudWorkerPool(...) as pool:` — Flask listener + janitor threads start.
6. Runs Optuna study (`optimize_hull` loop).
7. On any exit path (normal, KeyboardInterrupt, exception): `finally: provider.terminate_fleet(fleet_name=study_id, project_tag=project_tag)`. Pool `__exit__` runs first (via `with`), then fleet teardown.

`CampaignManager` is a pure supervisor: `_preflight` + `spawn_studies` + `monitor_loop` + `teardown` (which calls `terminate_all_tagged` as a campaign-wide sweep backstop for any fleet orphaned by a study crash). It NEVER calls `provision_fleet` or `terminate_fleet` directly.

Steps 2–7 are factored into `cloud_runner.prepare_cloud_pool` (a `@contextmanager`) so the same lifecycle (provision → pool.__enter__ → caller body → pool.__exit__ → terminate_fleet) is reused by `honest_evaluator.main`. Full keyword-only signature:

```python
@contextmanager
def prepare_cloud_pool(
    *, campaign: CampaignConfig,
    study_id: str, project_tag: str, fleet_name: str,
    flask_port: int, target_workers: int, total_matchup_slots: int,
    tailnet_ip: str, bearer_token: str, tailscale_authkey: str,
    debug_ssh_pubkey: str = "",
    mod_jar_override_url: str = "",
    mod_jar_override_sha256: str = "",
    sweep_project_on_exit: bool = False,
) -> Iterator[CloudWorkerPool]:
```

The four name-bearing params (`study_id`, `project_tag`, `fleet_name`, `flask_port`) are caller-supplied so distinct callers (study runs vs. honest-eval) get distinct namespaces and cannot collide on Redis keys, AWS tags, or Flask ports. The orchestrator-side Redis client is hardcoded to `host="localhost"` (the tailnet-exposed Redis lives on the workstation; only workers connect via `tailnet_ip`). See spec 30 §CLI entry point.

## Preflight gates

`CampaignManager.run()` calls `_preflight()` immediately after installing signal handlers. Preflight executes BEFORE any subprocess is spawned and BEFORE any cloud resource is provisioned. Failure → non-zero exit + explicit remediation message.

1. **Tailnet IP**: `subprocess.run(["tailscale", *_tailscale_socket_args(), "ip", "-4"], capture_output=True, text=True, timeout=5)`. Empty output → fail with remediation pointing at both kernel-mode (`tailscale up`) and rootless (`scripts/cloud/devenv-up.sh`) options. Stored on `self._tailnet_ip` for subprocess env plumbing. `_tailscale_socket_args()` appends `["--socket", <path>]` when `STARSECTOR_TAILSCALE_SOCKET` is set or when `~/.local/state/starsector-cloud/tailscale/tailscaled.sock` (the rootless daemon socket written by `devenv-up.sh`) exists — that lets the preflight target a per-user userspace tailscaled without an explicit env var.
2. **Redis reachable** (two-step check, supporting both kernel-mode and userspace-mode tailscale):
   - Step 2a — Redis alive: `redis.Redis(host="127.0.0.1", port=config.redis_port, socket_timeout=config.redis_preflight_timeout_seconds).ping()`. Failure → "Redis not reachable on 127.0.0.1:<port>. Start redis-server; see `scripts/cloud/devenv-up.sh` for a rootless recipe."
   - Step 2b — Tailnet exposure: attempt `redis.Redis(host=self._tailnet_ip, port=config.redis_port, …).ping()`. On success → pass (kernel-mode tailscale binds the tailnet IP to a local interface). On failure, fall back to `_tailscale_serve_exposes_port(port)`: if `tailscale serve status` lists `127.0.0.1:<port>` in its output, pass (userspace-mode tailscale proxies via `tailscale serve`). If neither succeeds → "Redis responds on 127.0.0.1 but is not reachable over the tailnet. Either (kernel-mode) bind redis-server to the tailnet IP or (userspace-mode) run `tailscale serve --bg --tcp=<port> tcp://127.0.0.1:<port>`."
3. **Flush stale Redis keys**: `_flush_stale_campaign_keys(project_tag, port, timeout)` SCANs `queue:<project_tag>:*` and `worker:<project_tag>:*` and DELs everything. Prevents a re-launched campaign with the same `name` from inheriting processing-list entries from the prior run (which the janitor would otherwise re-dispatch as phantom matchups) or stale worker-heartbeat hashes.
4. **AWS credentials**: `boto3.client("sts").get_caller_identity()`. Failure → fail with remediation pointing at the project `.env` profile (`AWS_PROFILE=starsector`) or dedicated EC2 IAM-user access keys. Long campaigns must not rely on transient SSO/login-session credentials.
5. **Authkey syntax**: `config.tailscale_authkey_secret.startswith("tskey-auth-")`. Violation → fail with "tailscale_authkey_secret must start with `tskey-auth-`. Generate a pre-approved ephemeral key from the Tailscale admin panel (tagged `tag:starsector-worker`)."

Preflight subprocess env plumbing (`_generate_study_env(study_idx, seed_idx, study_cfg, *, token_factory=secrets.token_urlsafe)`):

```
STARSECTOR_WORKSTATION_TAILNET_IP=<tailnet_ip>
STARSECTOR_BEARER_TOKEN=<fresh per-study token via token_factory(32)>
STARSECTOR_TAILSCALE_AUTHKEY=<config.tailscale_authkey_secret>
STARSECTOR_PROJECT_TAG=starsector-<config.name>
STARSECTOR_CAMPAIGN_YAML=<resolved yaml path>
# Optional pass-through to render_user_data — operator-set when populated:
STARSECTOR_DEBUG_SSH_PUBKEY=<operator pubkey>          # operator SSH access
STARSECTOR_MOD_JAR_OVERRIDE_URL=<tailnet jar URL>      # Java-only fast iteration
STARSECTOR_MOD_JAR_OVERRIDE_SHA256=<sha256 hex>        # required when URL set; ValueError otherwise
```

None of these are ever logged (`grep -En "logger.*env\|print.*env" src/starsector_optimizer/campaign.py` must be empty).

### `HetznerProvider`

Raises `NotImplementedError` with message `"HetznerProvider is stubbed; implement when campaign budget ≥ $500. Hetzner's per-matchup advantage amortizes only at larger scale (precise gap pending V2 re-validation; see ../reports/INDEX.md). See docs/reference/phase6-cloud-worker-federation.md §3."` Every abstract method raises.

## `EvaluatorPool` subclasses

- `LocalInstancePool` (spec 18) — drives local JVM+Xvfb instances for `run_optimizer.py --worker-pool local`.
- `CloudWorkerPool` — implements `EvaluatorPool.run_matchup(matchup)` by enqueueing to Redis and blocking on the Flask listener's dedup dict. Constructor takes `total_matchup_slots: int`; `prepare_cloud_pool` sizes it from the actual `len(instance_ids)` returned by `provider.provision_fleet(...) × campaign.matchup_slots_per_worker`, not merely the requested fleet size. The internal `threading.BoundedSemaphore(total_matchup_slots)` caps in-flight dispatches to what the fleet can actually consume. `num_workers` returns `total_matchup_slots`, which is what `StagedEvaluator` reads to size its `ThreadPoolExecutor`. StagedEvaluator sees exactly the same blocking per-call semantics as `LocalInstancePool`.

## Packer AMI

`scripts/cloud/packer/aws.pkr.hcl` builds a golden AMI in `us-east-1`. `bake_image.sh` wraps `packer build` and runs `aws ec2 copy-image --source-region us-east-1 --region us-east-2` to produce the us-east-2 copy (AWS AMIs are region-scoped; the 551 MB game files would otherwise transfer per-boot).

**Baked contents** (validated via post-build provisioner; AMI tag set only on zero exit code):
- Starsector game files (551 MB) at pinned version
- combat-harness mod (deployed, ready to run)
- `uv` + project venv built from the worker-source input digest
- `x11-xserver-utils` (for `xrandr --query` warmup; see below)
- `xvfb`, `xdotool`, OpenJDK
- Tailscale client
- `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (game activation; sourced from operator's `scripts/cloud/packer/prefs.xml`, gitignored — must contain at minimum `serial`, `firstGameRun=false`, `resolution=1920x1080`, `fullscreen=false`, `sound=false`)

`bake_image.sh` tags the source AMI and copied regional AMIs with
`ManifestSha256=<sha256(game/starsector/manifest.json)>` and
`WorkerSourceSha=<worker-source-input digest>` (or `<digest>-dirty` for dirty
debug bakes). `CampaignManager`, direct cloud-study, loadout-AB, and
honest-eval preflight reject AMIs whose tags differ from the current committed
worker inputs and manifest; after manifest, Python, `uv.lock`, or bake-script
changes, re-bake and update every relevant `ami_ids_by_region` entry before
launch/resume.

**Cloud-init injects** (never baked — these are per-campaign secrets and identifiers):
- Tailscale auth key (from `CampaignConfig.tailscale_authkey_secret`)
- `WorkerConfig` env vars (campaign_id, worker_id, study_id, redis_host, http_endpoint, bearer_token, max_lifetime_hours)
- Systemd unit wrapping `worker_agent.py main()`

**Post-build validation** (gates AMI tagging):
```
xvfb-run xrandr --query                                        # verifies X + XRandR warmup works
uv run python -c 'from starsector_optimizer.worker_agent import main; print("OK")'
```

## AWS-only MVP

Phase 6 ships AWS only. Hetzner is stubbed. Rationale: AWS quota is verified at 1,792 spot vCPU across four US regions (no quota ticket needed); Hetzner default 10-VM project cap requires a 1–2 business-day ticket. At MVP-scale budget, the AWS per-matchup premium is dominated by the Hetzner provisioning lead time (precise gap pending V2 re-validation; see [../reports/INDEX.md](../reports/INDEX.md)). The stub is a one-line `NotImplementedError` so adding Hetzner post-Phase-7 is a greenfield effort, not a refactor.

## Packages discovered during testing (2026-04-12 Hetzner prototype, validated 2026-04-18)

The Packer AMI bakes the full list below. Omitting any one of them reproduces a specific failure mode; this list is operationally load-bearing.

- `libxcursor1`, `libxxf86vm1` — required by LWJGL native libraries (`liblwjgl64.so`). Without them the game crashes with `UnsatisfiedLinkError`.
- `libopenal1` — OpenAL audio. Without it the launcher shows a modal dialog that prevents "Play Starsector" click-through.
- `libasound2t64` + null ALSA config at `/etc/asound.conf` (`pcm.!default {type null}; ctl.!default {type null}`) — prevents sound card errors on headless VMs.
- `x11-xserver-utils` — provides the `xrandr` binary that `instance_manager.py::_start_xvfb` invokes to warm the XRandR extension. **Without it LWJGL 2.x crashes on first Starsector launch** with `ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0` from `LinuxDisplay.getAvailableDisplayModes`.
- `xvfb` — headless X display.
- `xdotool` — Swing launcher "Play Starsector" click-through.
- `rsync`, `curl` — provisioning.
- **No system `openjdk`** — the game bundles its own JRE at `jre_linux/`. A system JRE with an interfering `JAVA_HOME` can break the bundled JRE's module path.

## LWJGL / XRandR root cause (2026-04-18 investigation)

The original "GPU required" conclusion from 2026-04-12 was a misdiagnosis: Starsector was crashing at startup, not rendering slowly. Root cause: LWJGL 2.x's `LinuxDisplay.getAvailableDisplayModes` throws `ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0` when Xvfb's XRandR extension has not populated its mode list. Xvfb does not finalize XRandR state until a client queries it — so the first call from LWJGL returns an empty array and crashes.

**The fix** (in `instance_manager.py::_start_xvfb`): after waiting for the Xvfb socket, run `xrandr --query` once as a client to warm the XRandR extension. This makes LWJGL's enumeration succeed. Requires `x11-xserver-utils` baked into the AMI.

**Per-instance cloud-vs-local throughput**: pending re-validation under V2 loadout fix; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). Design-target threshold is ≥ 2× local per-instance to justify the AWS premium at small budgets — re-validation must clear that floor before the next paid Phase 7 prep launch. GPU instances are not required.

## Lessons Learned (2026-04-12 Hetzner prototype)

1. **Software rendering is a dealbreaker.** Mesa/llvmpipe on CPU-only VMs makes Starsector unplayably slow. The game loop ties simulation speed to frame rendering — slow frames = slow simulation. The `xrandr --query` warmup plus the real Xvfb implementation sidesteps this by giving LWJGL a functioning display.
2. **Missing native libraries cause silent failures.** LWJGL needs `libxcursor1` and `libxxf86vm1` beyond the obvious X11 libs. Without them the game crashes with `UnsatisfiedLinkError` in `liblwjgl64.so` — no stack trace visible to the launcher.
3. **OpenAL error blocks the launcher.** Missing audio produces a modal dialog that prevents the "Play Starsector" click from working. Fix: install `libopenal1` + null ALSA config.
4. **rsync without `--delete` leaves stale files.** If a different game version was previously synced, leftover files (e.g., `jre_linux/lib/ext/`) cause JRE startup failures. The Packer AMI avoids this by baking one pinned game version.
5. **Game bundles its own JRE.** Installing system Java is unnecessary and a system `JAVA_HOME` can interfere with the bundled JRE's module path.
6. **Game activation via prefs.xml works** — but `serial` alone is insufficient. The Linux disk format is the bare leaf-node `<map>` (Java FileSystemPreferences format, NOT the `<preferences><root>` export tree), and the launcher gates startup on `firstGameRun=false` (skips the first-run setup dialog) and reads `resolution`/`fullscreen` to skip the display-config dialog. With only `serial` set, the worker's JVM hangs at the launcher's first-run dialog — `LocalInstancePool.run_matchup` then blocks until `result_timeout_seconds` and the entire campaign times out wholesale. The five entries (`serial`, `firstGameRun=false`, `resolution=1920x1080`, `fullscreen=false`, `sound=false`) are baked at `/home/ubuntu/.java/.userPrefs/com/fs/starfarer/prefs.xml`. macOS operators (where Java uses NSUserDefaults) extract these values from `~/Library/Preferences/com.fs.starfarer.plist` via `plutil -p`. Sourcing recipes per OS in `.claude/skills/cloud-worker-ops.md` § "Initial workstation setup → Game prefs.xml".
7. **AWS auth via dedicated IAM user, not Q login_session / SSO snapshot.** boto3 (used by `CampaignManager`, `AWSProvider`, `cloud_runner`) doesn't understand Amazon Q's `login_session` config and ad-hoc `aws configure export-credentials --format env` snapshots expire at the SSO session boundary (typically 1 hour) — long campaigns blow up mid-run with `RuntimeError: Credentials were refreshed, but the refreshed credentials are still expired`. Working setup: a dedicated IAM user (`starsector-optimizer`) with managed policy `arn:aws:iam::aws:policy/AmazonEC2FullAccess`, persisted in `~/.aws/credentials` under the `[starsector]` profile, surfaced via `AWS_PROFILE=starsector` in the repo `.env`. boto3 auto-refreshes from the credentials file and multi-hour campaigns survive without operator intervention. `EC2FullAccess` is the right scope: campaign needs `ec2:CreateFleet/RunInstances/CreateLaunchTemplate/CreateSecurityGroup/...`, Packer needs `ec2:RegisterImage/CopyImage/CreateTags/...`, ledger tick needs `ec2:DescribeSpotPriceHistory`. Setup recipe in `.claude/skills/cloud-worker-ops.md` § "Initial workstation setup → AWS profile".
8. **`legacyLauncher=true` is load-bearing for cloud workers, and the Swing launcher is advanced via X-core `windowfocus` + a coordinate-based mouse click computed from `xdotool getwindowgeometry`.** Starsector ships with `data/config/settings.json` setting `legacyLauncher=false`, which selects the LWJGL `com.fs.starfarer.launcher.opengl.GLLauncher` (fullscreen, sized to the Xvfb display). LWJGL ignores xdotool synthetic events entirely, so the only launcher path that works under Xvfb is the Swing launcher (`legacyLauncher=true`). `instance_manager._click_launcher` polls for the Swing launcher window via `xdotool search --name Starsector`, captures the WID + geometry, then dispatches `windowmap` → `windowfocus` → `mousemove (X + W*0.5, Y + H*0.7)` → `click 1` → `key Return` (belt-and-suspenders). **Three non-obvious traps under bare Xvfb,** each verified by `launcher_dispatch.log` on 2026-05-09: (a) `xdotool windowactivate` requires the EWMH `_NET_ACTIVE_WINDOW` atom which only a window manager sets, so it fails with `Your windowmanager claims not to support _NET_ACTIVE_WINDOW`; `windowfocus` (XSetInputFocus) is the WM-free equivalent (smoke #10). (b) `xdotool key --window <wid>` dispatches via XSendEvent which sets `send_event=True`; Java AWT filters such events as a synthetic-event-injection hardening, so the launcher never sees the key. Dropping `--window` falls back to XTest which produces real-looking keystrokes Java accepts (smoke #10). (c) The Play JButton is not AWT default-focused, so Return alone hits the JFrame and goes nowhere; a coordinate click bypasses the focus chain entirely (smoke #11). The click coordinate is parsed dynamically from `getwindowgeometry --shell <wid>` rather than hardcoded — handles the launcher's centered-on-Xvfb placement and is robust to Xvfb-size changes. The fractions `(0.5, 0.7)` live in `InstanceConfig.launcher_play_button_{x,y}_fraction` so future game-version layout shifts can be retuned without code edits.

## Scripts

```
scripts/cloud/
├── packer/
│   └── aws.pkr.hcl               # AMI template (us-east-1 build)
├── bake_image.sh                 # packer build + aws ec2 copy-image us-east-2
├── probe.sh                      # Tier-1 validation: 2 spot VMs, boot-test, teardown (sub-dollar; see reports/INDEX.md)
├── launch_campaign.sh            # wraps `uv run python -m starsector_optimizer.campaign <yaml>`
├── status.sh                     # tail ledger, print per-study best-fitness + trial counts
├── teardown.sh                   # emergency tag-based cleanup (fleets/instances/SGs/LTs/volumes) — campaign-scoped (Project=starsector-<campaign>); deletes maintain fleets FIRST (delete-fleets --terminate-instances) before instances so they cannot respawn
├── final_audit.sh                # zero-leak verifier (fleets/instances/SGs/LTs/volumes) — campaign-scoped, exits 0 clean / 1 on any leak / 2 inconclusive
├── audit_amis.sh                 # cross-campaign AMI/snapshot inventory (Project=starsector); flags YAML-unreferenced as cleanup candidates
├── cleanup_amis.sh               # deregister AMIs + delete snapshots; dry-run by default, --apply to commit, --force to override YAML-reference guard
├── devenv-up.sh                  # rootless workstation: userspace tailscaled + redis-server + tailscale serve TCP proxies
└── devenv-down.sh                # tear down rootless workstation
```

Every launch script prints its teardown command as its first line of output. `final_audit.sh` is the mandatory end-of-session check per `.claude/skills/cloud-worker-ops.md`.

## Deferred / out of scope

- **Tag-based sweeper cron** and **CloudWatch billing alarm** (listed in design doc §6 as orthogonal hard-stops) — deferred to post-MVP operational infrastructure. The three teardown layers above are the MVP hard-stop mechanism.
- **PlateauDetector** (design doc §4) — deferred to a follow-up commit. First campaign uses only the absolute `budget_per_study` trial cap.
- **Hetzner implementation** — stub-until-$500+-scale.
- **Libcloud abstraction** — not used; boto3 direct. A Libcloud wrapper can slot behind `CloudProvider` later without refactoring callers.
