# Cloud Deployment Specification

Phase 6 Cloud Worker Federation. Runs bulk combat simulation on AWS spot VMs while the workstation keeps every Optuna Study local. Defined in `src/starsector_optimizer/campaign.py`, `cloud_provider.py`, `cloud_worker_pool.py`, `worker_agent.py`, and `scripts/cloud/`.

## Topology

Workstation is the sole orchestrator. Every Optuna Study runs in a `run_optimizer.py --worker-pool cloud` subprocess on the workstation; `study.ask()` / `study.tell()` never cross the network. Workers on cloud VMs are pure evaluators: they pull `MatchupConfig` messages from a Redis queue, drive a local `LocalInstancePool(num_instances=2)` to produce `CombatResult`, and `POST /result` back to their study's Flask listener on the workstation. All orchestrator ↔ worker traffic rides a Tailscale mesh network; Redis and Flask are never exposed to the public internet.

```
workstation (single machine)                         cloud VM (N of them)
┌──────────────────────────────────────┐             ┌────────────────────────────────┐
│ CampaignManager (supervisor)         │             │ worker_agent.py main loop      │
│   spawn/kill study subprocesses      │             │   BRPOPLPUSH <queue> <proc>    │
│   provider.create_fleet              │◄───tailscale──┤   run LocalInstancePool (×2) │
│   CostLedger (JSONL, fsync'd)        │    redis    │   POST /result (bearer+dedup)  │
│   atexit teardown                    │             │   HSET worker:<id>:heartbeat   │
│                                      │             │                                │
│ study subprocess (×N per campaign):  │             │ LocalInstancePool              │
│   Optuna Study (SQLite, local)       │             │   Xvfb :100, :101              │
│   CloudWorkerPool                    │             │   Starsector JVM (×2)          │
│     ThreadPoolExecutor(workers=24)   │             │   combat harness mod           │
│     Flask POST /result listener      │             │   heartbeat + queue files      │
│     janitor thread (requeue stuck)   │             │                                │
└──────────────────────────────────────┘             └────────────────────────────────┘
```

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
| `sampler` | `str` | `tpe` or `catcma` |

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
| `name` | `str` | required | Campaign identifier; used as AWS resource tag `Project=starsector-<name>` |
| `budget_usd` | `float` | required | Hard cap; `CostLedger.record_heartbeat` raises `BudgetExceeded` at `cumulative_usd >= budget_usd` |
| `provider` | `str` | required | `"aws"`; `"hetzner"` raises `NotImplementedError` |
| `regions` | `tuple[str, ...]` | required | e.g. `("us-east-1", "us-east-2")` |
| `instance_types` | `tuple[str, ...]` | required | e.g. `("c7a.2xlarge", "c7i.2xlarge", "c7a.4xlarge", "c7i.4xlarge")` |
| `spot_allocation_strategy` | `str` | required | `"price-capacity-optimized"` |
| `capacity_rebalancing` | `bool` | required | EC2 Fleet CapacityRebalancing flag |
| `max_concurrent_workers` | `int` | required | Total VMs across all studies |
| `min_workers_to_start` | `int` | required | Partial-fleet floor; validator enforces `<= max_concurrent_workers` |
| `partial_fleet_policy` | `str` | required | `"proceed_half_speed"` or `"abort"` |
| `ami_ids_by_region` | `dict[str, str]` | required | Populated exactly once by `load_campaign_config`; grep invariant forbids post-load mutation |
| `ssh_key_name` | `str` | required | Pre-registered AWS key pair name |
| `tailscale_authkey_secret` | `str` | required | Injected into cloud-init; redacted from `__repr__` |
| `studies` | `tuple[StudyConfig, ...]` | required | |
| `global_auto_stop` | `GlobalAutoStopConfig` | `GlobalAutoStopConfig()` | |
| `max_lifetime_hours` | `float` | `6.0` | Worker self-terminates at this age; 6h covers prep's 4.1h run |
| `visibility_timeout_seconds` | `float` | `120.0` | Redis processing-list timeout |
| `janitor_interval_seconds` | `float` | `60.0` | How often the study subprocess sweeps stuck items |
| `worker_poll_margin_seconds` | `float` | `5.0` | Subtract from `visibility_timeout_seconds` for worker BRPOPLPUSH timeout |
| `fleet_provision_timeout_seconds` | `float` | `600.0` | EC2 Fleet retry window before partial-fleet decision |
| `result_timeout_seconds` | `float` | `900.0` | `CloudWorkerPool.run_matchup` blocks at most this long |
| `ledger_heartbeat_interval_seconds` | `float` | `60.0` | How often `CampaignManager.monitor_loop` appends ledger rows |
| `ledger_warn_thresholds` | `tuple[float, ...]` | `(0.5, 0.8, 0.95)` | Budget fractions at which a WARN log fires |
| `base_flask_port` | `int` | `9000` | Study `i` listens on `base_flask_port + i` |

### `WorkerConfig`

Injected by cloud-init as env vars at VM boot. Read once; worker treats as immutable.

| Field | Type | Default | Description |
|---|---|---|---|
| `campaign_id` | `str` | required | Matches `CampaignConfig.name` |
| `worker_id` | `str` | required | EC2 instance ID |
| `study_id` | `str` | required | `f"{hull}__{regime}__seed{n}"` |
| `redis_host` | `str` | required | Workstation's Tailscale address |
| `redis_port` | `int` | required | Default 6379 |
| `http_endpoint` | `str` | required | `f"http://{workstation}:{port}/result"` |
| `bearer_token` | `str` | required | Redacted from `__repr__` |
| `max_lifetime_hours` | `float` | `6.0` | |
| `http_retry_count` | `int` | `3` | POST retry attempts |
| `http_retry_base_seconds` | `float` | `1.0` | Exponential backoff start |
| `http_retry_max_seconds` | `float` | `30.0` | Backoff cap |
| `worker_poll_margin_seconds` | `float` | `5.0` | BRPOPLPUSH timeout = `visibility_timeout_seconds - worker_poll_margin_seconds` |

### `CostLedgerEntry`

One JSONL row in `~/starsector-campaigns/<name>/ledger.jsonl`. All fields primitive and secret-free.

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
- `queue:<study_id>:source` — matchups awaiting a worker
- `queue:<study_id>:processing` — matchups claimed by a worker but not yet ack'd via `POST /result`

Worker main loop:

```
item = BRPOPLPUSH source processing  (timeout = visibility_timeout_seconds - worker_poll_margin_seconds)
result = LocalInstancePool(...).run_matchup(item)
POST /result {matchup_id, result, bearer_token}   # retries from WorkerConfig
on 200: LREM processing 1 item
on 409: LREM processing 1 item  (already-received dedup; silently drop)
on 401: terminate worker (crypto invariant violation)
on repeated 5xx/network: let visibility_timeout expire → janitor re-queues
```

Study-subprocess janitor thread (runs every `janitor_interval_seconds`):

```
for item in LRANGE processing 0 -1:
    if item.enqueued_at older than visibility_timeout_seconds:
        LREM processing 1 item
        LPUSH source item
        logger.warning("requeued stuck matchup: study=%s matchup_id=%s", ...)
```

Idempotency key is `MatchupConfig.matchup_id`, which `CampaignManager` sets to `f"{study_id}__{trial_number}__{opponent_id}"` before enqueue. Globally unique across all studies.

## HTTP protocol

Per-study Flask listener on `config.base_flask_port + study_idx` exposes exactly one route:

```
POST /result
  body: {matchup_id: str, result: CombatResult-JSON, bearer_token: str}
  200: first observation; result registered, waiter notified via threading.Event
  409: matchup_id already observed; dedup silently drops
  401: bearer_token mismatch; no entry added
  404: any other path or method
```

No `GET`, no `PATCH`, no `PUT`, no admin route, no static files. Test `test_http_listener_rejects_non_result_routes` enforces this at every release.

## Cost ledger

Append-only JSONL at `~/starsector-campaigns/<name>/ledger.jsonl`. Every write is followed by `file.flush()` + `os.fsync(file.fileno())` to prevent torn lines on crash (~1ms overhead per row, negligible at 96 rows/min).

Warning logs fire at each `ledger_warn_thresholds` (default 50%/80%/95%) — once per threshold, never repeated. Hard cap at `budget_usd`: `record_heartbeat` raises `BudgetExceeded`, which `CampaignManager.run()` catches in a `try/finally` to trigger teardown.

## Teardown discipline

Three layers:

1. In-process `try/finally` — `CampaignManager.run()` body runs inside `try:`; `finally:` calls `provider.terminate_all_tagged(config.name)` and asserts `provider.list_active(config.name) == []` with one retry after `config.teardown_retry_delay_seconds` (default 10s).
2. `atexit.register(self.teardown)` — registered in `CampaignManager.__init__`, runs on crash paths that bypass `finally`.
3. Shell-level `trap EXIT` in `launch_campaign.sh` — re-runs `final_audit.sh` unconditionally and exits non-zero if any resource leaked.

`final_audit.sh` checks all 4 US regions (not just `regions:`) for any instance tagged `Project=starsector-<campaign-name>` or security groups / volumes / key pairs tagged the same.

## `CloudProvider` ABC

```python
class CloudProvider(abc.ABC):
    @abc.abstractmethod
    def create_fleet(self, config: CampaignConfig, *, user_data: str) -> list[str]: ...
        # returns instance IDs; user_data is a cloud-init script injected at boot
    @abc.abstractmethod
    def terminate_all_tagged(self, campaign_name: str) -> int: ...
        # terminates instances + deletes launch templates + deletes security groups
        # returns instances-terminated count (LT/SG deletion is best-effort)
    @abc.abstractmethod
    def list_active(self, campaign_name: str) -> list[dict]: ...        # RUNNING+PENDING instances
    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float: ...
```

### `AWSProvider`

boto3-direct. Credentials loaded from the standard AWS credential chain — never stored in Python.

`create_fleet(config, *, user_data: str)`:

1. **Per region**: ensure a security group `starsector-<campaign_name>` exists with all egress allowed, zero ingress (workers are outbound-only; Tailscale handles reachability; a blank-ingress SG closes the public-internet attack surface).
2. **Per region**: ensure a launch template `starsector-<campaign_name>` exists with:
   - `ImageId` = `ami_ids_by_region[region]`
   - `KeyName` = `config.ssh_key_name`
   - `SecurityGroupIds` = `[<sg created above>]`
   - `InstanceMarketOptions={MarketType: spot}`
   - `UserData` = `base64(user_data)` (the caller-supplied cloud-init script; see §Cloud-init UserData)
   - `TagSpecifications`: on both `instance` and `volume`, include `Project=starsector-<campaign_name>`
   - If a launch template with the same name already exists, create a new version and set it as default (never edit in place — LT versions are immutable once referenced by a fleet).
3. Fire one `ec2.create_fleet(SpotOptions={AllocationStrategy: "price-capacity-optimized", MaintenanceStrategies: CapacityRebalance}, Type="instant")` per region, diversified across `config.instance_types`.

Tag `Project=starsector-<campaign_name>` propagates to the security group, the launch template, and every instance. `terminate_all_tagged` uses this single tag to reap everything together.

`terminate_all_tagged(campaign_name)`: per region, terminate instances first (avoids EC2 deletion-ordering constraints), then delete the launch template, then delete the security group. Idempotent — missing resources are treated as already-cleaned.

`list_active(campaign_name)`: per region, instances in `pending` or `running` state with tag `Project=starsector-<campaign_name>`. Does NOT include launch templates or security groups (they are stateless scaffolding; absence of instances is the teardown signal).

### Cloud-init UserData

`src/starsector_optimizer/cloud_userdata.py::render_user_data(worker_config, tailscale_authkey) -> str` emits a bash payload that:

1. `umask 077` so every file created by the script is owner-read-only.
2. `tailscale up --authkey-stdin --advertise-tags=tag:starsector-worker --accept-dns=false <<EOF`. The authkey is piped via stdin, **never** argv — `/proc/<pid>/cmdline` is world-readable on Linux by default, so any `--authkey=<value>` form would leak the secret to every local user during boot.
3. Writes `/etc/starsector-worker.env` with every `WorkerConfig` field mapped to `STARSECTOR_WORKER_<FIELD>`. Owner is `root:root`; mode `0600` is inherited from `umask 077` at file-creation time (no 0644 window between creation and chmod).
4. `systemctl daemon-reload && systemctl start starsector-worker.service` (the service unit is baked into the AMI; see `scripts/cloud/packer/starsector-worker.service`).

The renderer is a pure function — takes a frozen `WorkerConfig` + a string authkey, returns a string. No I/O. Lives in its own module (not `cloud_provider.py`) so providers other than AWS can reuse it.

For probe scenarios where no real worker is needed, `render_probe_user_data(campaign_id) -> str` returns a minimal script: `echo probe-boot-ok > /var/log/starsector-probe.log`. The probe tests fleet lifecycle, not worker connectivity.

### `HetznerProvider`

Raises `NotImplementedError` with message `"HetznerProvider is stubbed; implement when campaign budget ≥ $500. Hetzner's ~13% per-matchup advantage amortizes only at larger scale. See docs/reference/phase6-cloud-worker-federation.md §3."` Every abstract method raises.

## `EvaluatorPool` subclasses

- `LocalInstancePool` (spec 18) — drives local JVM+Xvfb instances for `run_optimizer.py --worker-pool local`.
- `CloudWorkerPool` — implements `EvaluatorPool.run_matchup(matchup)` by enqueueing to Redis and blocking on the Flask listener's dedup dict. `ThreadPoolExecutor(max_workers=workers_per_study)` serializes concurrent `run_matchup` calls; `StagedEvaluator` sees exactly the same blocking per-call semantics as `LocalInstancePool`.

## Packer AMI

`scripts/cloud/packer/aws.pkr.hcl` builds a golden AMI in `us-east-1`. `bake_image.sh` wraps `packer build` and runs `aws ec2 copy-image --source-region us-east-1 --region us-east-2` to produce the us-east-2 copy (AWS AMIs are region-scoped; the 551 MB game files would otherwise transfer per-boot).

**Baked contents** (validated via post-build provisioner; AMI tag set only on zero exit code):
- Starsector game files (551 MB) at pinned version
- combat-harness mod (deployed, ready to run)
- `uv` + project venv at a pinned git commit SHA
- `x11-xserver-utils` (for `xrandr --query` warmup; see below)
- `xvfb`, `xdotool`, OpenJDK
- Tailscale client
- `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (game activation)

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

Phase 6 ships AWS only. Hetzner is stubbed. Rationale: AWS quota is verified at 1,792 spot vCPU across four US regions (no quota ticket needed); Hetzner default 10-VM project cap requires a 1–2 business-day ticket. At $85 total budget, the AWS premium (~13% per-matchup) is dominated by the Hetzner provisioning lead time. The stub is a one-line `NotImplementedError` so adding Hetzner post-Phase-7 is a greenfield effort, not a refactor.

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

**Benchmarks (2026-04-18, `experiments/cloud-benchmark-2026-04-18/`):**

| Provider | Instance | Spot $/hr | Matchups/hr/inst | vs local (27/hr/inst) |
|---|---|---|---|---|
| Local workstation | 12-core, RTX 4090 | $0 | 27 | 1× baseline |
| AWS c7i.2xlarge | 8 vCPU Intel SPR, us-east-1 | $0.158 | **64** | 2.4× |
| Hetzner CCX33 | 8 vCPU AMD Milan, Ashburn VA | $0.13 | **~63** | 2.3× |

Both CPU cloud paths match or exceed local per-instance throughput at negligible cost. GPU instances are not required.

## Lessons Learned (2026-04-12 Hetzner prototype)

1. **Software rendering is a dealbreaker.** Mesa/llvmpipe on CPU-only VMs makes Starsector unplayably slow. The game loop ties simulation speed to frame rendering — slow frames = slow simulation. The `xrandr --query` warmup plus the real Xvfb implementation sidesteps this by giving LWJGL a functioning display.
2. **Missing native libraries cause silent failures.** LWJGL needs `libxcursor1` and `libxxf86vm1` beyond the obvious X11 libs. Without them the game crashes with `UnsatisfiedLinkError` in `liblwjgl64.so` — no stack trace visible to the launcher.
3. **OpenAL error blocks the launcher.** Missing audio produces a modal dialog that prevents the "Play Starsector" click from working. Fix: install `libopenal1` + null ALSA config.
4. **rsync without `--delete` leaves stale files.** If a different game version was previously synced, leftover files (e.g., `jre_linux/lib/ext/`) cause JRE startup failures. The Packer AMI avoids this by baking one pinned game version.
5. **Game bundles its own JRE.** Installing system Java is unnecessary and a system `JAVA_HOME` can interfere with the bundled JRE's module path.
6. **Game activation via prefs.xml works.** Copying `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (contains `serial` key) transfers activation to new machines. The Packer AMI bakes this file — the activation travels with the image.

## Scripts

```
scripts/cloud/
├── packer/
│   └── aws.pkr.hcl               # AMI template (us-east-1 build)
├── bake_image.sh                 # packer build + aws ec2 copy-image us-east-2
├── probe.sh                      # $0.15 validation: 2 spot VMs, boot-test, teardown
├── launch_campaign.sh            # wraps `uv run python -m starsector_optimizer.campaign <yaml>`
├── status.sh                     # tail ledger, print per-study best-fitness + trial counts
├── teardown.sh                   # emergency tag-based terminate across all 4 US regions
└── final_audit.sh                # zero-leak verifier; exits 0 clean, 1 on any leaked resource
```

Every launch script prints its teardown command as its first line of output. `final_audit.sh` is the mandatory end-of-session check per `.claude/skills/cloud-worker-ops.md`.

## Deferred / out of scope

- **Tag-based sweeper cron** and **CloudWatch billing alarm** (listed in design doc §6 as orthogonal hard-stops) — deferred to post-MVP operational infrastructure. The three teardown layers above are the MVP hard-stop mechanism.
- **PlateauDetector** (design doc §4) — deferred to a follow-up commit. First campaign uses only the absolute `budget_per_study` trial cap.
- **Hetzner implementation** — stub-until-$500+-scale.
- **Libcloud abstraction** — not used; boto3 direct. A Libcloud wrapper can slot behind `CloudProvider` later without refactoring callers.
