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
│     ThreadPoolExecutor(workers=N)    │             │   heartbeat + queue files      │
│     Flask POST /result listener      │             │                                │
│     janitor thread (requeue stuck)   │             │                                │
│   provider.terminate_fleet  (owns)   │             │                                │
└──────────────────────────────────────┘             └────────────────────────────────┘
```

**Fleet ownership**: the study subprocess owns its own fleet — provisioning AND teardown. `CampaignManager` is a pure supervisor (spawn + monitor + sweep-backstop via `terminate_all_tagged`). Per-study fleet ownership is load-bearing: each study's workers carry that study's bearer token + Redis queue keys + Flask endpoint in their UserData. A campaign-wide fleet would force every worker to carry every study's secrets.

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
| `name` | `str` | required | Campaign identifier; validated by `load_campaign_config` against `^[a-zA-Z0-9._-]{1,64}$` so `f"starsector-{name}__{fleet_name}"` always satisfies AWS LT name rules. Used as AWS resource tag `Project=starsector-<name>` |
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
| `tailscale_authkey_secret` | `str` | required | Injected into cloud-init; redacted from `__repr__`. Supports `${VAR}` env-substitution — if value starts with `${` and ends with `}`, `load_campaign_config` resolves via `os.environ`. Missing env var → `ValueError`. Substitution is SCOPED to this field only. |
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
| `base_flask_port` | `int` | `9000` | Study at `(study_idx, seed_idx)` listens on `base_flask_port + study_idx * len(seeds) + seed_idx` |
| `redis_port` | `int` | `6379` | Workstation Redis port; shared by preflight ping and by `WorkerConfig.redis_port` in spawned children |
| `redis_preflight_timeout_seconds` | `float` | `2.0` | `_preflight` Redis ping timeout — covers only the tailnet-binding-check, not campaign-wide connectivity |
| `num_instances_per_worker` | `int` | `2` | Per-VM JVM count; forwarded to `WorkerConfig.num_instances_per_worker`. c7a.2xlarge (8 vCPU) fits 2 JVMs at ~2.5 cores each |

### `WorkerConfig`

Injected by cloud-init as env vars at VM boot. Read once; worker treats as immutable. `load_worker_config_from_env` iterates `dataclasses.fields(WorkerConfig)` + `typing.get_type_hints(WorkerConfig)` to read + coerce every field from `STARSECTOR_WORKER_<FIELD_UPPER>`. Unknown coercion target → `TypeError` (loud-fail, forward-compat).

| Field | Type | Default | Description |
|---|---|---|---|
| `campaign_id` | `str` | required | Matches `CampaignConfig.name` |
| `study_id` | `str` | required | `f"{hull}__{regime}__seed{n}"` |
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
| `num_instances_per_worker` | `int` | `2` | Per-VM JVM count for the inner `LocalInstancePool` |
| `worker_id` | `str` | `""` | EC2 instance ID. **Placeholder at render time** — cloud-init overwrites via IMDSv2 before `systemctl start`. Moved to end-of-dataclass so earlier required fields stay positional-required. |

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

Four layers from innermost to outermost:

1. **Study subprocess `try/finally`** — `run_optimizer.py --worker-pool cloud` wraps its work in `try:` and `finally: provider.terminate_fleet(fleet_name=study_id, project_tag=project_tag)`. Pool `__exit__` runs first (Flask + janitor shutdown), then fleet teardown.
2. **CampaignManager in-process `try/finally`** — `CampaignManager.run()` body in `try:`; `finally:` calls `provider.terminate_all_tagged(project_tag)` as a sweep backstop for any study subprocess that crashed before its own teardown ran. Asserts `provider.list_active(project_tag) == []` with one retry after `config.teardown_retry_delay_seconds`.
3. **`atexit.register(self.teardown)`** — registered in `CampaignManager.__init__`, runs on crash paths that bypass `finally`.
4. **Shell-level `trap EXIT`** in `launch_campaign.sh` — re-runs `teardown.sh` + `final_audit.sh` unconditionally and exits non-zero if any resource leaked.

`final_audit.sh` checks all 4 US regions (not just `regions:`) for any instance tagged `Project=starsector-<campaign-name>` or security groups / volumes / launch templates tagged the same.

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
    ) -> list[str]: ...
        # returns instance IDs

    @abc.abstractmethod
    def terminate_fleet(self, *, fleet_name: str, project_tag: str) -> int: ...
        # targeted teardown: reaps resources tagged BOTH project_tag AND fleet_name

    @abc.abstractmethod
    def terminate_all_tagged(self, project_tag: str) -> int: ...
        # sweep: reaps everything tagged project_tag regardless of fleet. Crash-recovery backstop.

    @abc.abstractmethod
    def list_active(self, project_tag: str) -> list[dict]: ...
        # RUNNING+PENDING instances with tag Project=project_tag

    @abc.abstractmethod
    def get_spot_price(self, region: str, instance_type: str) -> float: ...
```

No `CampaignConfig` parameter — the provider is cloud-mechanical, not campaign-aware. Callers (study subprocess, `probe.py`) compose the call from explicit fields.

### Two-tag scheme

Every resource (instance, LT, SG) carries BOTH tags:
- `Project=<project_tag>` — e.g. `starsector-smoke`. Enables `terminate_all_tagged(project_tag)` sweep.
- `Fleet=<fleet_name>` — e.g. `hammerhead__early__seed0`. Enables `terminate_fleet(fleet_name, project_tag)` targeted reap.

LT/SG NAMES are `f"{project_tag}__{fleet_name}"`, e.g. `starsector-smoke__hammerhead__early__seed0`. Unique per fleet → multiple studies in the same region don't collide.

### `AWSProvider`

boto3-direct. Credentials loaded from the standard AWS credential chain — never stored in Python.

`provision_fleet(...)`:

1. **Per region**: ensure SG named `f"{project_tag}__{fleet_name}"` exists with all egress allowed, zero ingress (workers are outbound-only). Tag it with both `Project` and `Fleet`.
2. **Per region**: ensure LT named `f"{project_tag}__{fleet_name}"` exists with:
   - `ImageId` = `ami_ids_by_region[region]`
   - `KeyName` = `ssh_key_name`
   - `SecurityGroupIds` = `[<sg from step 1>]`
   - `InstanceMarketOptions={MarketType: spot}`
   - `UserData` = `base64(user_data)`
   - `BlockDeviceMappings` = `[{DeviceName: "/dev/sda1", Ebs: {DeleteOnTermination: true}}]` (prevents volume audit leak)
   - `TagSpecifications` on `instance` and `volume` include both `Project` and `Fleet`
   - If an LT with the same name already exists, create a new version and `modify_launch_template(DefaultVersion=...)` — LT versions are immutable once referenced.
3. Fire one `ec2.create_fleet(SpotOptions={AllocationStrategy: spot_allocation_strategy}, Type="instant")` per region, diversified across `instance_types`. `TagSpecifications` on the Fleet resource also include both tags.

`terminate_fleet(fleet_name, project_tag)`: per region, filter by BOTH tags → terminate instances → delete LT (by name) → delete SG (by name, with ENI-detach retry loop: `_SG_DELETE_DEADLINE_SECONDS=300.0`, `_SG_DELETE_POLL_INTERVAL_SECONDS=10.0`). Idempotent.

`terminate_all_tagged(project_tag)`: per region, filter by `Project` tag ONLY → terminate all tagged instances → delete every LT matching the tag (tag-filter `describe_launch_templates`) → delete every SG matching the tag. Idempotent.

`list_active(project_tag)`: per region, instances in `pending` or `running` state with tag `Project=project_tag`.

### Cloud-init UserData

`src/starsector_optimizer/cloud_userdata.py::render_user_data(worker_config, tailscale_authkey) -> str` emits a bash payload that:

1. `set -euo pipefail` + `umask 077` so every file created by the script is owner-read-only and any command failure halts the script before `systemctl start`.
2. `tailscale up --authkey-stdin --advertise-tags=tag:starsector-worker --accept-dns=false <<EOF`. The authkey is piped via stdin, **never** argv — `/proc/<pid>/cmdline` is world-readable on Linux by default, so any `--authkey=<value>` form would leak the secret to every local user during boot.
3. Writes `/etc/starsector-worker.env` via a quoted heredoc with every `WorkerConfig` field mapped to `STARSECTOR_WORKER_<FIELD>` (every field in `dataclasses.fields(WorkerConfig)`, including the placeholder `worker_id=""`). Owner is `root:root`; mode `0600` is inherited from `umask 077`.
4. `chown root:root /etc/starsector-worker.env`.
5. **IMDSv2 WORKER_ID override block** (inserted between `chown` and `systemctl daemon-reload`; see §Worker ID resolution below). Fetches the live EC2 instance ID, overwrites the placeholder line in the env file. If IMDS is unreachable, `curl --fail` + `set -euo pipefail` halts the script BEFORE `systemctl start` — the worker never boots with `worker_id=""`.
6. `systemctl daemon-reload && systemctl start starsector-worker.service` (the service unit is baked into the AMI; see `scripts/cloud/packer/starsector-worker.service`).

The renderer is a pure function — takes a frozen `WorkerConfig` + a string authkey, returns a string. No I/O. Lives in its own module (not `cloud_provider.py`) so providers other than AWS can reuse it.

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

For probe scenarios where no real worker is needed, `render_probe_user_data(campaign_id) -> str` returns a minimal script: `echo probe-boot-ok > /var/log/starsector-probe.log`. The probe tests fleet lifecycle, not worker connectivity.

## Per-study fleet lifecycle

Each study subprocess (`scripts/run_optimizer.py --worker-pool cloud`) owns its fleet end-to-end:

1. `_require_env` reads `STARSECTOR_WORKSTATION_TAILNET_IP`, `STARSECTOR_BEARER_TOKEN`, `STARSECTOR_TAILSCALE_AUTHKEY`, `STARSECTOR_PROJECT_TAG` — raises `ValueError` with remediation pointer if any missing.
2. Constructs `WorkerConfig` with per-study bearer token (already in env), tailnet-based `redis_host` + `http_endpoint`, `worker_id=""` placeholder.
3. Renders UserData via `render_user_data(worker_cfg, tailscale_authkey=authkey)`.
4. Calls `provider.provision_fleet(fleet_name=study_id, project_tag=project_tag, ...)`.
5. Enters `with CloudWorkerPool(...) as pool:` — Flask listener + janitor threads start.
6. Runs Optuna study (`optimize_hull` loop).
7. On any exit path (normal, KeyboardInterrupt, exception): `finally: provider.terminate_fleet(fleet_name=study_id, project_tag=project_tag)`. Pool `__exit__` runs first (via `with`), then fleet teardown.

`CampaignManager` is a pure supervisor: `_preflight` + `spawn_studies` + `monitor_loop` + `teardown` (which calls `terminate_all_tagged` as a campaign-wide sweep backstop for any fleet orphaned by a study crash). It NEVER calls `provision_fleet` or `terminate_fleet` directly.

## Preflight gates

`CampaignManager.run()` calls `_preflight()` immediately after installing signal handlers. Preflight executes BEFORE any subprocess is spawned and BEFORE any cloud resource is provisioned. Failure → non-zero exit + explicit remediation message.

1. **Tailnet IP**: `subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)`. Empty output → fail with "Tailscale must be up on the workstation. Run: `tailscale up`". Stored on `self._tailnet_ip` for subprocess env plumbing.
2. **Redis tailnet binding**: `redis.Redis(host=self._tailnet_ip, port=config.redis_port, socket_timeout=config.redis_preflight_timeout_seconds).ping()`. Failure → fail with "Redis not reachable at `<tailnet_ip>:<port>`. Edit `/etc/redis/redis.conf` or run `sudo systemctl edit redis-server` to set `bind 0.0.0.0` (or the tailnet IP explicitly), then `sudo systemctl restart redis-server`."
3. **AWS credentials**: `boto3.client("sts").get_caller_identity()`. Failure → fail with "AWS credentials unavailable. Run `aws sso login` or set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
4. **Authkey syntax**: `config.tailscale_authkey_secret.startswith("tskey-auth-")`. Violation → fail with "tailscale_authkey_secret must start with `tskey-auth-`. Generate a pre-approved ephemeral key from the Tailscale admin panel (tagged `tag:starsector-worker`)."

Preflight subprocess env plumbing (`_generate_study_env(study_idx, seed_idx, study_cfg, *, token_factory=secrets.token_urlsafe)`):

```
STARSECTOR_WORKSTATION_TAILNET_IP=<tailnet_ip>
STARSECTOR_BEARER_TOKEN=<fresh per-study token via token_factory(32)>
STARSECTOR_TAILSCALE_AUTHKEY=<config.tailscale_authkey_secret>
STARSECTOR_PROJECT_TAG=starsector-<config.name>
STARSECTOR_CAMPAIGN_YAML=<resolved yaml path>
```

None of these are ever logged (`grep -En "logger.*env\|print.*env" src/starsector_optimizer/campaign.py` must be empty).

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
