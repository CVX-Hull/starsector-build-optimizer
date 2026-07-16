---
type: skill
status: shipped
last-validated: unvalidated
name: Cloud Worker Operations
description: SOP for launching, monitoring, and tearing down multi-worker cloud campaigns for Starsector optimization. Invoke when the user asks to run optimization in the cloud, spin up many workers, start a campaign, spend a budget on experiments, or debug cloud-worker issues.
disable-model-invocation: true
---

# Cloud Worker Operations SOP

Use this skill when the user asks you to run or debug a cloud campaign — anything involving multiple Starsector workers outside the local workstation. Built on the Phase 6 Cloud Worker Federation design (`docs/reference/phase6-cloud-worker-federation.md`). Per-VM throughput was re-validated under V2 (all cells in the design band; see [../../docs/reports/2026-05-10-wave1-validation.md](../../docs/reports/2026-05-10-wave1-validation.md)); campaign-level cost figures live in dated reports (learned-batch: [../../docs/reports/2026-07-11-aws-cost-analysis.md](../../docs/reports/2026-07-11-aws-cost-analysis.md)).

## The rules of money

1. **Every launch sets a budget ceiling.** If you don't have a `budget_usd` figure, STOP and ask the user for one. Default "launch and see what happens" is a $200/day runaway pattern.
2. **Every launch prints the teardown command as its first line of output.** Operator must be able to copy-paste to nuke resources in case the orchestrator dies.
3. **Final-audit runs at the end of every session.** After ANY cloud work, `scripts/cloud/final_audit.sh <campaign-name>` must exit 0. Don't skip this step.
4. **Unattended paid runs get a watchdog.** If a run is expected to finish while the operator is away, launch `scripts/cloud/watch_eval_cleanup.sh <eval_tag> <pid> [<pid> ...]` against the evaluator process ids. The watchdog waits for those PIDs, gives the wrapper time to run its built-in audit, then runs final audit and teardown automatically if tagged resources remain.

## Which provider

| Situation | Pick |
|---|---|
| **Phase 6 MVP + Phase 7 prep budget tier** | **AWS c7a.2xlarge spot us-east-1 + us-east-2**. Account quota: 640 spot vCPU per region = 80 VMs each with zero lead time. Pricing under `price-capacity-optimized` + `CapacityRebalancing`. Concrete $-figures (campaign budget, hourly rate, per-matchup cost, preemption rate) live in dated reports — V2 figures: [../../docs/reports/2026-07-11-aws-cost-analysis.md](../../docs/reports/2026-07-11-aws-cost-analysis.md). Note: 16-vCPU learned-batch workers cap at 40 per region under the same 640-vCPU quota. |
| Larger-budget campaign where Hetzner's per-matchup advantage justifies a quota-ticket lead time | **Hetzner CCX33** — `HetznerProvider` is stubbed until this threshold; implementing it means filing a quota ticket (1-2 business days) then writing the hcloud-python wrapper per `docs/specs/22-cloud-deployment.md`. |
| GPU cloud | **Never.** CPU per-instance throughput meets or exceeds local after the XRandR fix; GPU adds no throughput and costs more. The quantitative cloud-vs-local ratio was retired unmeasured (no Linux local baseline on this workstation; no decision consumes it — [../../docs/reports/2026-07-13-roadmap-regroom.md](../../docs/reports/2026-07-13-roadmap-regroom.md)). |
| ARM / Graviton | **Never.** LWJGL 2.9.3 is x86_64-only. |

**Why AWS primary at small budget**: at the Phase 6 MVP scale the dominant operator cost is *lead time*, not per-matchup price. AWS already has 1,792 spot vCPU across 4 US regions; Hetzner's default 10-VM project cap requires a multi-day quota ticket. The AWS premium at small budgets is small relative to a human-day of waiting. At larger spend, the absolute Hetzner-vs-AWS delta begins to exceed a human-day of engineering, and Hetzner becomes the better pick. The cost-tier crossover point and per-matchup deltas under V2 live in [../../docs/reports/2026-07-11-aws-cost-analysis.md](../../docs/reports/2026-07-11-aws-cost-analysis.md).

## Initial workstation setup (one-time)

Skip this section if `aws sts get-caller-identity` already returns `Arn=...:user/starsector-optimizer` AND `scripts/cloud/packer/prefs.xml` already exists. Otherwise these are the one-time bootstraps every fork has to do before the cloud workflow works.

### AWS profile

`boto3` (used by `CampaignManager`, `AWSProvider`, `cloud_runner`) needs static credentials it can find via the standard chain. Two AWS auth flavors that **don't** work here without extra ceremony: (1) Amazon Q's `login_session` — AWS CLI understands it but boto3 doesn't, and (2) ad-hoc `aws configure export-credentials --format env` snapshots — they expire mid-run because the underlying SSO session times out at 1 hour, blowing up long campaigns at the boundary.

Working setup: a dedicated IAM user with EC2-only permissions, persisted in `~/.aws/credentials` under a non-default profile, surfaced via `AWS_PROFILE` in the repo `.env`. boto3 auto-refreshes from the credentials file, so multi-hour campaigns survive without operator intervention.

```bash
# One-time, from a shell that already has working AWS auth (root keys, SSO, etc.)
aws iam create-user --user-name starsector-optimizer \
    --tags Key=Project,Value=starsector
aws iam attach-user-policy --user-name starsector-optimizer \
    --policy-arn arn:aws:iam::aws:policy/AmazonEC2FullAccess

# Generate access key into a temp file (never display the values)
aws iam create-access-key --user-name starsector-optimizer \
    > /tmp/starsector_key.json
chmod 600 /tmp/starsector_key.json

# Append a [starsector] profile to ~/.aws/credentials (preserves any [default])
test -f ~/.aws/credentials || touch ~/.aws/credentials
chmod 600 ~/.aws/credentials
jq -r '"\n[starsector]\naws_access_key_id = " + .AccessKey.AccessKeyId
       + "\naws_secret_access_key = " + .AccessKey.SecretAccessKey
       + "\nregion = us-east-1"' /tmp/starsector_key.json >> ~/.aws/credentials

# Wipe the file — keys are now persisted in the credentials file only
rm -P /tmp/starsector_key.json 2>/dev/null || rm -f /tmp/starsector_key.json

# Tell the project to use the profile (campaign launch sources .env)
echo "AWS_PROFILE=starsector" >> .env

# Verify
AWS_PROFILE=starsector uv run python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"
# → arn:aws:iam::<acct>:user/starsector-optimizer
```

**Why these specific choices**:
- **Dedicated IAM user (vs. root keys)**: standard AWS hygiene; the keys live only on operator laptops and have no path to billing / IAM / other accounts.
- **`AmazonEC2FullAccess`**: the campaign needs `ec2:CreateFleet/RunInstances/CreateLaunchTemplate/CreateSecurityGroup/...`, Packer needs `ec2:RegisterImage/CopyImage/CreateTags/...`, and the ledger tick needs `ec2:DescribeSpotPriceHistory`. Tighter scoping is possible but adds maintenance load when AWS adds APIs.
- **Non-default profile + `AWS_PROFILE` in `.env`**: lets the workstation's existing `[default]` profile (often Amazon Q login_session, IAM Identity Center, etc.) keep working for unrelated AWS work.

### EC2 Fleet service-linked role (maintain fleets only)

**Only needed when a campaign/eval sets `fleet_type: maintain`** (spec 22 §Config dataclasses; default `instant` does NOT need this). `Type="maintain"` fleets — which self-replenish spot-reclaimed capacity — are managed by AWS via the `AWSServiceRoleForEC2Fleet` service-linked role. It is created **once per AWS account** and then persists; there is nothing to store locally and nothing that references it from our side (AWS assumes it automatically).

The dedicated `starsector-optimizer` IAM user has `AmazonEC2FullAccess` but **no `iam:*` permissions**, so it cannot auto-create the role. If the role is missing, `create_fleet(Type="maintain")` fails at provision time with `AuthFailure.ServiceLinkedRoleCreationNotPermitted` (instant fleets are unaffected). Create it once from a shell with admin/root AWS auth — **NOT** the `starsector` profile:

```bash
aws iam create-service-linked-role --aws-service-name ec2fleet.amazonaws.com
# Already exists → "InvalidInput: ... has been taken", which is fine.
```

You can't verify it from the `starsector` profile (no `iam:GetRole`); the practical check is "a `fleet_type: maintain` launch gets past provisioning." (Created for this account 2026-07-16.)

**Iterating on maintain provisioning code:** the unit tests mock `create_fleet`, so they cannot catch AWS API-validation errors — the SLR requirement above and the "maintain `create_fleet` accepts ONLY a `ResourceType:"fleet"` tag, not `"instance"` (`InvalidTagKey.Malformed`)" rule both surfaced only at launch. Any `src/` change forces a ~30-min AMI re-bake (WorkerSourceSha covers all of `src/`, including orchestrator-only files like `cloud_provider.py`), so before baking, validate against real AWS with a throwaway 1-worker round-trip: `provider.provision_fleet(fleet_type="maintain", target_workers=1, ...)` → `provider.terminate_fleet(...)`. Sub-dollar, ~90s, and it catches the API-validation class the mocks can't.

### Game prefs.xml

The Packer `provisioner "file"` copies `scripts/cloud/packer/prefs.xml` (gitignored) into the AMI at `/home/ubuntu/.java/.userPrefs/com/fs/starfarer/prefs.xml`. Java reads that file via `FileSystemPreferences` at game-launch time, and Starsector reads `serial` to satisfy the activation check. Without it, the launcher's first-run / activation dialog blocks the game indefinitely; the worker's `LocalInstancePool.run_matchup` then hangs on `pool.run_matchup` until `result_timeout_seconds` and the campaign times out wholesale.

**Format on Linux** (the FileSystemPreferences disk format — bare leaf-node, `<map>` only, NOT the full `<preferences><root>...` export tree):

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE map SYSTEM "http://java.sun.com/dtd/preferences.dtd">
<map MAP_XML_VERSION="1.0">
  <entry key="serial" value="XXXXX-XXXXX-XXXXX-XXXXX"/>
  <entry key="firstGameRun" value="false"/>
  <entry key="resolution" value="1920x1080"/>
  <entry key="fullscreen" value="false"/>
  <entry key="sound" value="false"/>
</map>
```

The five entries are all required — `serial` alone is insufficient because Starsector's launcher gates startup on `firstGameRun=false` (the first-run setup dialog) and reads `resolution` / `fullscreen` to skip the display-config dialog. `sound=false` matches the headless-OpenAL workaround already baked into the AMI.

**Sourcing the file**:
- **Already activated on Linux** — copy directly: `cp ~/.java/.userPrefs/com/fs/starfarer/prefs.xml scripts/cloud/packer/prefs.xml`.
- **Activated on macOS** — Java on macOS uses NSUserDefaults, not FileSystemPreferences. The activated state lives at `~/Library/Preferences/com.fs.starfarer.plist`. Extract entries via `plutil -p ~/Library/Preferences/com.fs.starfarer.plist`, transcribe `serial` / `firstGameRun` / `resolution` / `fullscreen` / `sound` into the XML format above.
- **Activated on Windows** — registry path `HKEY_CURRENT_USER\Software\JavaSoft\Prefs\com\fs\starfarer`; transcribe the same five values.

The file is gitignored at `scripts/cloud/packer/prefs.xml` and bakes into the AMI on next `bake_image.sh`.

## Preflight checklist (before launching ANY cloud worker)

Run all of these. Failure on any one = STOP. `CampaignManager._preflight` re-runs checks 3 (Tailscale up), 4 (Redis on tailnet), 11 (AWS credentials), 6 (authkey syntax), and the AMI tag/provenance portion of checks 9/15/16/17 in-process before it spawns anything. Direct cloud-study, loadout-AB, and honest-eval launch paths also run the AMI tag gate before provisioning. This checklist is the operator-side verification — items 1/2/5/7/8/10/12/13/14 are operator-only.

1. **Budget is set**: user has given a `budget_usd` figure AND it's written into the campaign YAML's `budget_usd` field.
2. **Python modules import cleanly**:
   ```bash
   uv run python -c "from starsector_optimizer.campaign import CampaignManager, CostLedger"
   uv run python -c "from starsector_optimizer.cloud_provider import AWSProvider"
   uv run python -c "from starsector_optimizer.cloud_worker_pool import CloudWorkerPool"
   uv run python -c "from starsector_optimizer.cloud_runner import run_cloud_study"
   uv run python -c "from starsector_optimizer.worker_agent import load_worker_config_from_env"
   ```
3. **Tailscale is up on the workstation**:
   ```bash
   tailscale ip -4   # must return a 100.x.y.z address; empty = run `tailscale up`
   ```
   Rootless alternative (no sudo, no kernel TUN): `scripts/cloud/devenv-up.sh` brings up userspace-mode tailscaled on a per-user socket. See "Dev environment (rootless)" below. The preflight auto-detects the rootless daemon at `~/.local/state/starsector-cloud/tailscale/tailscaled.sock` (or whatever `STARSECTOR_TAILSCALE_SOCKET` points at).
4. **Redis is reachable by cloud workers over the tailnet**. Two supported configurations:
   - **kernel-mode**: Redis bound to the tailnet interface. `redis-cli -h "$(tailscale ip -4)" ping` returns `PONG`. If it fails: `sudo systemctl edit redis-server` → `[Service]` / `ExecStart=` override with `--bind 0.0.0.0`. Then `sudo systemctl restart redis-server`.
   - **userspace-mode (rootless)**: Redis bound to 127.0.0.1, exposed to the tailnet via `tailscale serve --bg --tcp=6379 tcp://127.0.0.1:6379`. `devenv-up.sh` sets this up for you. Preflight verifies via `tailscale serve status`.
5. **Tailscale policy grants `tag:starsector-worker` → workstation on `tcp:6379,9000-9099`**. Verify at the tailnet policy editor (`https://login.tailscale.com/admin/acls/file`). Tailscale made grants GA as the preferred policy language; the file now accepts both `acls` (legacy) and `grants` (current). Minimal stanza:
   ```json
   {
     "tagOwners": {
       "tag:starsector-worker": ["autogroup:admin"]
     },
     "grants": [
       {
         "src": ["tag:starsector-worker"],
         "dst": ["*"],
         "ip": ["tcp:6379", "tcp:9000-9099"]
       }
     ]
   }
   ```
   In grants, the port moves out of `dst` into a separate `ip` array (each port/range is its own entry); `"action": "accept"` is removed (grants are accept-only). The editor has a **"Convert to grants"** button that rewrites any legacy `acls` block.
6. **Ephemeral + pre-approved auth key exists** (from Tailscale admin panel → Keys), tagged `tag:starsector-worker`. The canonical source is the repo-local `.env` file (mode 0600, gitignored). **`.env` is auto-sourced** by `scripts/cloud/launch_campaign.sh` and `scripts/cloud/bake_image.sh` (each script does `set -a; source .env; set +a` if `AWS_PROFILE` is unset and `.env` exists), so operators no longer need to remember the manual sourcing step before every launch. Verify the file's contents directly:
   ```bash
   grep -q '^TAILSCALE_AUTHKEY=tskey-auth-' .env && grep -q '^AWS_PROFILE=starsector' .env && echo OK
   ```
   `.env` must contain at minimum `TAILSCALE_AUTHKEY=tskey-auth-...` and `AWS_PROFILE=starsector`. Campaign YAMLs using `${TAILSCALE_AUTHKEY}` substitution resolve from the shell env, which the auto-source populates. If `.env` is missing or empty, recreate it from the key at the Tailscale admin panel before proceeding. **Manual sourcing remains supported** for ad-hoc commands (e.g. `aws s3 ls` from outside the entry-point scripts) — `set -a; source .env; set +a`.
7. **AWS quota check** (for every `regions:` entry):
   ```bash
   for region in us-east-1 us-east-2; do
     aws service-quotas get-service-quota --service-code ec2 \
       --quota-code L-34B43A08 --region $region --query 'Quota.Value' --output text
   done
   ```
   At 8 vCPU/VM, confirm `quota ≥ 8 × planned_workers_per_region`.
   - **If the YAML sets `fleet_type: maintain`**: the `AWSServiceRoleForEC2Fleet` service-linked role must exist, or provisioning dies with `AuthFailure.ServiceLinkedRoleCreationNotPermitted` *after* SGs/LTs are created. One-time per account — see "Initial workstation setup → EC2 Fleet service-linked role". Not verifiable from the `starsector` profile (no `iam:GetRole`); the check is a maintain launch getting past provisioning.
8. **No orphaned resources** under your target tag:
   ```bash
   scripts/cloud/final_audit.sh <campaign-name>   # must exit 0 before launching
   ```
9. **AMI exists in every `regions:` entry** — inspect `ami_ids_by_region:` in the campaign YAML and verify each AMI is available:
   ```bash
   aws ec2 describe-images --owners self --region <region> --image-ids <ami-id>
   ```
10. **Validation probe passed within last 48 hours**:
    ```bash
    scripts/cloud/probe.sh <campaign.yaml>
    ```
11. **Provider credentials alive**: `AWS_PROFILE=starsector aws sts get-caller-identity` returns `Arn=...:user/starsector-optimizer`. If the user check fails, redo "Initial workstation setup → AWS profile" above. boto3 reads from `[starsector]` in `~/.aws/credentials`; `launch_campaign.sh` + `bake_image.sh` auto-source `.env` (idempotent — skipped when `AWS_PROFILE` is already set) which exports `AWS_PROFILE=starsector` into the orchestrator subprocess env. **The wrong-creds failure mode** that recurred 10+ times in 2026-04/05: invoking `bake_image.sh` or any boto3 caller from a fresh shell where `.env` was never sourced caused the AWS SDK to fall back to the default profile in `~/.aws/config`, which on this workstation is an Amazon-Q `login_session = ...:root` entry the SDK can't resolve. Symptom: Packer "No valid credential sources found" or boto3 NoCredentialsError. The auto-sourcing eliminates the failure mode at the entry-point layer.
12. **Tier-2 pipeline smoke passed within last 30 days** (first real paid campaign gate):
    ```bash
    export TAILSCALE_AUTHKEY=tskey-auth-...
    scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml
    scripts/cloud/final_audit.sh smoke   # must exit 0
    ```
    Expected gate: launch exits 0 + ledger.jsonl has ≥1 `worker_heartbeat` + Optuna study SQLite has 1 `TrialState.COMPLETE` (< 10 min wall-clock; smoke cost is sub-dollar by design — exact figure pending re-validation, see [../../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](../../docs/reports/2026-05-10-v1-loadout-bug-invalidation.md)).
13. **Game prefs file exists** at `scripts/cloud/packer/prefs.xml` (gitignored, baked into the AMI by Packer at the path `/home/ubuntu/.java/.userPrefs/com/fs/starfarer/prefs.xml`). Format + sourcing recipes in "Initial workstation setup → Game prefs.xml" above. The file is checked at AMI bake, not at campaign launch — so its absence surfaces only when `bake_image.sh` fails or, worse, succeeds with a stale file and the worker hangs at the launcher's first-run / activation dialog.
14. **SSH key** present; name must match `ssh_key_name:` in the YAML. The campaign-YAML key only registers a public key in AWS (its private side is unrecoverable from AWS). For interactive worker debugging, use the optional `STARSECTOR_DEBUG_SSH_PUBKEY` mechanism — generate `~/.ssh/starsector-debug` (ed25519, no passphrase), `export STARSECTOR_DEBUG_SSH_PUBKEY="$(cat ~/.ssh/starsector-debug.pub)"` before `launch_campaign.sh`, and SSH with `ssh -i ~/.ssh/starsector-debug ubuntu@<worker-tailnet-ip>`. **`tailscale up --ssh` is intentionally NOT enabled** in user-data (smoke #8, 2026-05-09) — it hijacks port 22 for tailscaled's identity-based SSH server, gates connections via the tailnet ACL, and a default-permissive personal tailnet still silent-denies SSH; enabling it would shadow the regular sshd. The `STARSECTOR_DEBUG_SSH_PUBKEY` injection is the only operator-SSH path until ACL-as-code lands (Phase 7.5 R2).
15. **LWJGL XRandR fix in code**: `grep 'xrandr --query' src/starsector_optimizer/instance_manager.py` returns a match in `_start_xvfb`. Without it, workers crash with `ArrayIndexOutOfBoundsException: Index 0`.
16. **`x11-xserver-utils` baked into the AMI**: check `scripts/cloud/packer/aws.pkr.hcl` contains `x11-xserver-utils` in the apt list.
17. **`legacyLauncher=true` in `game/starsector/data/config/settings.json`**: the worker advances the launcher via `xdotool windowmap <wid> && xdotool windowfocus <wid> && xdotool key Return`, which only works against the **Swing** launcher whose default focused button is "Play Starsector". `windowfocus` (XSetInputFocus) sidesteps `windowactivate`'s EWMH `_NET_ACTIVE_WINDOW` requirement that fails under Xvfb-no-WM, and `xdotool key` *without* `--window` dispatches via XTest (real keystroke) rather than XSendEvent (which Java AWT filters as `send_event=True` for security). With Starsector's default `legacyLauncher=false`, the LWJGL `GLLauncher` runs fullscreen and ignores xdotool synthetic key/click events entirely — the JVM hangs on the launcher screen forever. Verify with `grep '"legacyLauncher"' game/starsector/data/config/settings.json` returns `true`. Re-bake the AMI after flipping.

## Dev environment (rootless)

Tailscale and Redis on the workstation normally need root (systemd services binding kernel TUN / low-numbered ports). For an easy-to-launch, easy-to-tear-down setup that leaves zero system-wide state behind, use the rootless helper:

```bash
# Bring up per-user tailscaled + redis-server + tailscale serve proxies.
# Idempotent — safe to re-run.
export TAILSCALE_AUTHKEY=tskey-auth-...
scripts/cloud/devenv-up.sh

# Tear everything down (logs out of tailnet, stops both daemons).
scripts/cloud/devenv-down.sh
```

What `devenv-up.sh` does:
- Starts `redis-server` bound to `127.0.0.1:6379` (data in `~/.local/state/starsector-cloud/redis`).
- Starts `tailscaled --tun=userspace-networking` on a per-user socket (no kernel TUN, no sudo).
- Runs `tailscale up` with the exported authkey.
- Calls `tailscale serve --bg --tcp=<port> tcp://127.0.0.1:<port>` for Redis (6379) and the Flask result-port range (default 9000-9099). This is what exposes the workstation services to remote workers over the tailnet in userspace mode — without `tailscale serve`, the tailnet IP isn't bound to any local interface and workers can't reach you.

What `CampaignManager._preflight` does differently under this setup:
- Detects the userspace socket and passes `--socket <path>` on every `tailscale` CLI call.
- Pings Redis first on `127.0.0.1` to confirm redis-server is up.
- Then tries the tailnet IP (kernel mode works this way); on failure, falls back to checking `tailscale serve status` for the TCP proxy mapping (userspace mode).

Use kernel-mode tailscale when you already have it installed system-wide — the preflight accepts both. Use `devenv-up.sh` when you want zero-sudo setup/teardown cycles for experimentation.

## Launching a campaign

Smoke and prep share the same launch command. Only the YAML differs.

```bash
# 0. (optional, if not using system tailscale / redis) Rootless dev env
set -a && source .env && set +a   # loads TAILSCALE_AUTHKEY from .env
scripts/cloud/devenv-up.sh

# 1. (once per AMI rebuild) Bake and copy the AMI
scripts/cloud/bake_image.sh
# → prints AMI IDs for us-east-1 and us-east-2; paste into campaign.yaml

# 2. Dry-run validate the YAML + resolve config (free)
TAILSCALE_AUTHKEY=tskey-auth-placeholder \
  uv run python -m starsector_optimizer.campaign --dry-run <campaign.yaml>

# 3. Tier-1 validation probe (sub-dollar, design-target ~$0.05; pending re-validation)
scripts/cloud/probe.sh examples/probe-campaign.yaml

# 4. Tier-2 pipeline smoke (sub-dollar, design-target ~$0.30; pending re-validation) — SAME code path as prep, tiny study
scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml

# 5. Real launch (prints teardown command as first line)
scripts/cloud/launch_campaign.sh <campaign.yaml>

# 6. Monitor
scripts/cloud/status.sh <campaign-name>

# 7. For unattended paid runs, arm a cleanup watchdog before walking away.
# Use the evaluator/orchestrator PIDs from `pgrep -af`.
scripts/cloud/watch_eval_cleanup.sh <project-tag-or-eval-tag> <pid> [<pid> ...]

# 8. On completion OR error — explicit teardown
scripts/cloud/teardown.sh <campaign-name>

# 9. Final audit — MANDATORY (launch_campaign.sh EXIT trap also runs this)
scripts/cloud/final_audit.sh <campaign-name>

# 10. (optional, end of session) Stop the rootless dev env
scripts/cloud/devenv-down.sh
```

`launch_campaign.sh` wraps the Python invocation in a `trap EXIT` that
re-runs `teardown.sh` + `final_audit.sh` on normal shell exit, crashes
that still unwind the shell, and trappable signals. In-process,
`CampaignManager.run()` has a `try/finally: terminate_all_tagged` sweep +
`atexit.register(teardown)`. Each study subprocess also has its own
`try/finally: terminate_fleet` for its own fleet. **Four layers of teardown
belt-and-suspenders.**

`watch_eval_cleanup.sh` is a fifth layer for unattended sessions. It is not a
replacement for normal cleanup; it is an independent process that waits for
the supplied evaluator PIDs to disappear, then audits the project tag and runs
`teardown.sh` if audit reports survivors. Run it from the same trusted shell
context as the campaign so it has AWS credentials and process visibility.
For honest-eval tags, pass the full `starsector-honest-eval-...` tag or the
teardown argument without the leading `starsector-`; both cleanup scripts
normalize the prefix.

### Java JAR override (debug-only fast iteration)

For disposable Java-only smoke/debug loops, the worker's UserData can fetch a freshly built jar from the workstation over the tailnet at boot, sha256-verified, and overlay the AMI-baked copy. Do not use this path for publishable, resumable, or cross-session evaluation results: the AMI provenance gate validates the baked `ModCommitSha` and manifest, while an override changes the runtime jar after that gate. For production/resumable correctness, rebuild/deploy the jar, regenerate the manifest if needed, re-bake the AMI, and update every region's `ami_ids_by_region`.

```bash
# Workstation terminal A: build + serve (Ctrl-C when iteration is done)
scripts/cloud/serve_mod_jar.sh
# → builds combat-harness.jar, prints URL + SHA256, serves on port 8081

# Workstation terminal B: export the env vars + launch
eval "$(scripts/cloud/serve_mod_jar.sh --env)"   # rebuilds + exports
scripts/cloud/launch_campaign.sh examples/smoke-campaign.yaml
```

The `STARSECTOR_MOD_JAR_OVERRIDE_URL` + `STARSECTOR_MOD_JAR_OVERRIDE_SHA256` env vars are read by `cloud_runner.py` and rendered into UserData. Workers `curl` the JAR after `tailscale up`, sha256-verify, and `install` it before `systemctl start starsector-worker.service`. Any failure (404, sha mismatch, network) halts boot via `set -euo pipefail` — workers never run against the wrong jar.

Wave launch helpers ignore `data/.mod_jar_env` by default. Set
`STARSECTOR_ENABLE_JAR_OVERRIDE=1` only for a disposable debug launch when you
explicitly want those helpers to source the override environment.

When to AMI-rebake instead:
- Game files, manifest, Python code, `uv.lock`, systemd unit, or bake/Packer scripts changed → rebake
- Java changes that affect manifest output, production correctness, resumable evals, or publishable reports → rebuild/deploy, regenerate manifest if needed, then rebake
- Java-only smoke/debug iteration → the override path is acceptable, but results are diagnostic only
- Mixed (Python + Java) → rebake (overlay only handles the JAR)

`scripts/cloud/bake_image.sh` refuses dirty worker-source paths by default and
tags the AMI as `ManifestSha256=<sha256(game/starsector/manifest.json)>` and
`WorkerSourceSha=<worker-source-input digest>`, where the digest covers `src`,
`pyproject.toml`, `uv.lock`, and the cloud bake/Packer scripts. Dirty debug
bakes require `STARSECTOR_ALLOW_DIRTY_AMI_BAKE=1` and are tagged
`<digest>-dirty`. Campaign, direct cloud-study, loadout-AB, and honest-eval
preflight compare those tags against the current committed manifest and source
inputs, so commit source/manifest changes before baking and update every
region's `ami_ids_by_region` after the bake/copy finishes. Dirty launch override
(`STARSECTOR_ALLOW_DIRTY_AMI_LAUNCH=1`) is only for disposable debugging images;
those AMIs should not back a resumable evaluation.

Important failure mode: the JAR override does not update worker Python. If a
run shows orchestrator-side `result matchup_id mismatch: envelope=... parsed=...`
after a Python-side guard or queue/protocol fix was made locally, assume the AMI
is stale until proven otherwise. Rebuild/copy the AMI, paste the new IDs into
the campaign YAML, and then resume; otherwise workers can keep posting stale JVM
results under fresh Redis envelopes.

### Study-per-(hull,regime,seed) sizing cheatsheet

- **All current cloud studies**: TPE (`sampler: tpe`). It is the only accepted
  sampler in campaign YAMLs.
- **Future high-concurrency studies**: require a new sampler implementation and
  spec update before use. CatCMAwM was removed because this search space has no
  continuous dimensions.

Per-study budget sweet spot: **500-1500 trials**.

## Monitoring during runs

Every 15-30 min while a campaign is live:

1. **Cost ledger + cumulative cost**:
   ```bash
   scripts/cloud/status.sh <campaign-name>
   ```
   Cross-reference cumulative against `budget_usd`.
2. **Worker liveness**: `aws ec2 describe-instances --filters 'Name=tag:Project,Values=starsector-<campaign-name>' 'Name=instance-state-name,Values=pending,running'`. Dead workers should be auto-replaced; persistent gap = bug.
3. **Redis queue depth per study**: `redis-cli LLEN queue:starsector-<campaign-name>:<study_id>:source`. Keys are namespaced by `project_tag` (= `starsector-<campaign-name>`) so multiple campaigns can coexist. If growing unbounded, workers can't keep up — scale up or reduce per-worker lifespan.
4. **Stuck studies**: any study with no trial progress for >15 min = worker crash loop. Inspect worker logs; typically the XRandR or heartbeat issue.
5. **Worker CPU utilization**: `redis-cli HGETALL worker:starsector-<campaign-name>:<worker_id>:heartbeat`. The hash has `load_avg_1min` / `load_avg_5min` / `load_avg_15min` / `cpu_count`. Healthy range for `c7a.2xlarge` with `matchup_slots_per_worker=2` is `load_avg_1min` in `[3, 8]` (8 vCPU, 2 JVMs @ ~2.5 cores each). Persistent `load_avg_1min > cpu_count` → over-subscription, reduce `matchup_slots_per_worker`. Persistent `load_avg_1min < 3` → under-utilization, either the orchestrator isn't dispatching fast enough or `matchup_slots_per_worker` is smaller than the box supports. `scripts/cloud/status.sh <campaign-name>` prints per-worker load.

## Failure recovery recipes

### "Redis connection refused" on tailnet IP

Workers boot and fail `BRPOPLPUSH` with `ConnectionRefusedError: [Errno 111]`. Root cause: workstation Redis isn't reachable to workers over the tailnet. Pick the path matching your setup:

**Kernel-mode tailscale (system install, sudo available)** — bind Redis to all interfaces via systemd drop-in:
```bash
sudo systemctl edit redis-server
# In the editor, add:
#   [Service]
#   ExecStart=
#   ExecStart=/usr/bin/redis-server /etc/redis/redis.conf --bind 0.0.0.0
sudo systemctl restart redis-server
redis-cli -h "$(tailscale ip -4)" ping   # must now return PONG
```

**Userspace-mode tailscale (rootless `devenv-up.sh`)** — verify the TCP proxy is in place:
```bash
tailscale --socket ~/.local/state/starsector-cloud/tailscale/tailscaled.sock \
    serve status   # must list :6379 → tcp://127.0.0.1:6379
# If missing, re-run scripts/cloud/devenv-up.sh.
```

`CampaignManager._preflight` catches both cases at launch — if you see "Redis not reachable ..." or "Redis responds on 127.0.0.1:6379 but is not reachable over the tailnet", apply the matching fix and relaunch.

### "Tailscale policy denies tag:starsector-worker"

Workers boot, `tailscale up` succeeds, then their BRPOPLPUSH hangs and eventually times out. Root cause: the tailnet policy doesn't grant the worker → workstation reachability. Fix at the tailnet policy editor (`https://login.tailscale.com/admin/acls/file`); add a grant:

```json
{
  "grants": [
    {
      "src": ["tag:starsector-worker"],
      "dst": ["*"],
      "ip": ["tcp:6379", "tcp:9000-9099"]
    }
  ],
  "tagOwners": {
    "tag:starsector-worker": ["autogroup:admin"]
  }
}
```

The `dst: ["*"]` targets the whole tailnet including the untagged workstation; if you want a tighter destination, use the workstation hostname from `tailscale status --self`. Grants are the current policy language — the editor's **"Convert to grants"** button rewrites any legacy `acls` block automatically.

### `create_fleet` returns `InvalidGroup.NotFound` for a just-created SG

Symptoms: campaign subprocess logs `RuntimeError: create_fleet produced zero instances in <region>` with per-AZ errors like `"The security group 'sg-XXXX' does not exist in VPC 'vpc-YYYY'"`. Happens under concurrent provisioning (multiple studies racing their `provision_fleet` calls) and surfaced when an aborted sampler benchmark ran 6 studies in parallel.

Root cause: AWS EC2 Fleet service has a replication lag after `create_security_group` beyond what the `describe_security_groups` visibility waiter covers. Fleet's internal registry needs a few extra seconds to see the SG.

Fix already in `cloud_provider.py` (no operator action unless the fix doesn't hold): `_ensure_security_group` blocks on `client.get_waiter("security_group_exists").wait(...)` after create, and `_create_fleet_in_region` retries up to `_FLEET_PROVISION_MAX_RETRIES=4` times at `_FLEET_PROVISION_RETRY_DELAY_SECONDS=3.0` intervals when the response contains ANY `InvalidGroup.NotFound` / `InvalidSecurityGroupID.NotFound` error. The predicate is `any(transient)` not `all(transient)` — permanent per-AZ rejections like `us-east-1e` not stocking `c7a.2xlarge` routinely co-occur with transient SG errors on other AZs, and we want to retry through so the non-1e AZs succeed. Test coverage: `tests/test_cloud_provider.py::TestFleetProvisionSGPropagation`.

If this keeps firing even past the retry budget, the AWS region may be genuinely backed up — try a different region or wait a few minutes. Don't increase the retry cap without checking Fleet service health first.

### Other known concurrency hazards (not yet observed in prod)

See `docs/reports/2026-04-19-phase6-deferred-audit.md` for the deferred
list from the 2026-04-19 audit: TimeoutTuner dormancy, POST-before-register
race, janitor `enqueued_at` ping-pong, and Tier-3 concurrency-shakedown
proposal. Each entry has reproduction notes, a proposed fix, and a revisit
criterion.

### `_apply_eb_shrinkage` raises `ValueError: eb_shrinkage needs n >= 3 builds, got 1`

Symptom: orchestrator subprocess crashes on a small number of completed trials despite `eb_min_builds=8` supposedly guarding. Surfaced at 32 concurrent matchup slots (sampler-benchmark attempt) but NOT at smoke/Tier-2 scale.

Root cause (pre-2026-04-19 bug, now fixed): the guard read `score_matrix.n_builds` (counts trials with ≥1 matchup result) whereas `eb_shrinkage`'s OLS fit consumes `_completed_records` (fully-finalized trials). Under high concurrency `score_matrix.n_builds` crosses 8 while `len(_completed_records)` is still 1. Fix: guard now uses `len(_completed_records)`. No operator action — verify `optimizer.py:_apply_eb_shrinkage` reads `self._completed_records` not `self._score_matrix.n_builds` if you ever see this stack again.

### Workers crashing on startup with `ArrayIndexOutOfBoundsException: Index 0`

LWJGL XRandR bug. Check:
1. `instance_manager.py::_start_xvfb` has the `xrandr --query` warmup call.
2. `x11-xserver-utils` is baked into the AMI. `ssh worker 'which xrandr'` should return a path.
3. If the AMI predates the fix, rebuild via `scripts/cloud/bake_image.sh`.

### Campaign blew past budget

1. **IMMEDIATELY**: `scripts/cloud/teardown.sh <campaign-name>`. Blunt-force stop bleeding.
2. Check `~/starsector-campaigns/<name>/ledger.jsonl` for the crossing event — did it fire the warning threshold? If not, the ledger-write path is broken.
3. Root-cause before next launch. Candidate causes: (a) `CostLedger.record_heartbeat` not being called on a cadence consistent with `ledger_heartbeat_interval_seconds`, (b) `BudgetExceeded` caught and swallowed somewhere, (c) worker `max_lifetime_hours` not honored by the worker agent loop.

### Spot preemption cascade

If >30% of workers are being preempted in a short window:
1. Check AWS Spot placement score for the target regions: `aws ec2 describe-spot-placement-scores --region us-east-1 --single-availability-zone`.
2. If score <7, pause, then relaunch after expanding `instance_types:` (add `c7a.4xlarge`, `c7i.4xlarge`) or switching the `regions:` list.
3. At $500+ scale, ship `HetznerProvider` (no spot preemption) as a cost-stable alternative.

### Worker output never comes back

SSH directly (assumes Tailscale node for this worker is reachable):
```bash
ssh ubuntu@<worker-tailscale-ip> 'systemctl status starsector-worker; journalctl -u starsector-worker -n 200'
```

Common causes: Xvfb died, Starsector JVM hung, heartbeat file stale. `pkill -9 java; pkill -9 Xvfb` and let the `instance_manager` restart logic kick in; if 3 restarts fail, treat as broken worker and replace.

### Result envelope mismatch

Symptom in orchestrator log:
`result matchup_id mismatch: envelope=<current> parsed=<previous>`.

Root-cause pattern: a persistent worker JVM returned an old
`combat_harness_results.json.data` for a newer Redis assignment, or the worker
AMI is stale and lacks the Python-side stale-result guard. There are two
behaviors depending on worker vintage:

- Current workers detect the stale local result before POST, kill/restart the
  desynchronized JVM, and retry the same matchup immediately while restart
  budget remains.
- Stale workers can still POST the stale result. Current orchestrators reject
  that POST as HTTP 422, wake the waiting dispatcher with a retryable failure,
  and make the worker ack the corrupt Redis item instead of replaying it.

Operator action after seeing a cluster of these lines:

1. Stop the run through the normal wrapper/Ctrl-C path and verify final audit is
   clean.
2. If any Python worker/orchestrator code changed since the AMI was baked, run
   `scripts/cloud/bake_image.sh` and update the campaign AMI IDs before resume.
3. Honest-eval only: resume with `--resume-from <eval_tag>` after the AMI and
   Java jar path are consistent across regions. Normal optimization campaigns
   resume by relaunching the campaign YAML with the same study DBs; do not pass
   `--resume-from` to `scripts/cloud/launch_campaign.sh`.

### AMI-copy-image drift across regions

AWS AMIs are region-scoped. If `aws ec2 copy-image` hasn't run or silently failed, `us-east-2` workers launch from a stale AMI. Check:
```bash
aws ec2 describe-images --owners self --region us-east-1 --image-ids <us-east-1 ami>
aws ec2 describe-images --owners self --region us-east-2 --image-ids <us-east-2 ami>
```
Both must show `State: available`. If only one, re-run `scripts/cloud/bake_image.sh` — it bakes once in us-east-1 then copies to us-east-2 automatically.

## AMI / snapshot lifecycle (separate from per-campaign teardown)

`final_audit.sh` and `teardown.sh` are **campaign-scoped** — they look for `Project=starsector-<campaign>` tags on instances, SGs, and volumes. They do **not** touch AMIs and EBS snapshots, which carry the cross-campaign `Project=starsector` tag and persist across operator sessions. AMI storage isn't free (~$0.05/GB/month per snapshot, ~12 GB per worker AMI ≈ $0.60/AMI/month), so periodically reclaim space:

```bash
# Read-only inventory across all 4 US regions; flags AMIs not referenced
# by any examples/*.yaml as cleanup candidates.
scripts/cloud/audit_amis.sh

# Dry-run: shows what would be deleted, makes no changes.
scripts/cloud/cleanup_amis.sh ami-XXXX [ami-YYYY ...]

# Apply: deregister AMI + delete its underlying snapshot(s) per region.
scripts/cloud/cleanup_amis.sh --apply ami-XXXX [ami-YYYY ...]

# Override the YAML-reference safety guard (only if you've already
# updated the YAMLs and want to delete the previously-pointed-to AMI):
scripts/cloud/cleanup_amis.sh --apply --force ami-XXXX
```

`cleanup_amis.sh` enforces four safety guards: ownership (must be in caller's account), tag (`Project=starsector` only — refuses untagged or wrong-project AMIs), YAML-reference check (refuses to delete AMIs still referenced by `examples/*.yaml`, override with `--force`), and dry-run-by-default (must pass `--apply` to actually delete).

Every re-bake should be followed by an audit + cleanup of the previous bake's AMIs once the new ones are wired into YAMLs. Skipping leaves storage cost accruing on stale images.

## Teardown discipline

**After every cloud work session, run:**

```bash
scripts/cloud/final_audit.sh <campaign-name>
```

Checks all 4 US regions (us-east-1, us-east-2, us-west-1, us-west-2) for instances / SGs / volumes tagged `Project=starsector-<campaign-name>`. Exit 0 if clean, 1 if any resource leaked, 2 if AWS describe calls fail and the audit is inconclusive. Use as the last command of every session.

`launch_campaign.sh` wraps its Python invocation in `trap EXIT` that re-runs `final_audit.sh` on normal shell exit or trappable signals. SIGKILL cannot run traps; use `watch_eval_cleanup.sh` for unattended paid sessions. Belt-and-suspenders with `CampaignManager.run()`'s in-process `try/finally` and `atexit`.

**If you're ending a session with active campaigns running**: that's an explicit user decision. Confirm with the user before leaving resources alive. Default posture is "no active resources at session end."

## Things to push back on

- **"Launch both campaigns at once"** when they share `base_flask_port` (the default `9000`). Two campaigns on one workstation collide on Flask result-ports — `study_idx` 0/1/2 → ports `9000/9100/9200` in **both** — silently dropping the loser's overlapping studies (2026-07-15 accounting-run root cause). Either launch **sequentially**, or give each campaign a **distinct `base_flask_port`** range (e.g. campaign B at `base_flask_port: 9500`, non-overlapping with A's `9000..9000+max_study_idx*flask_ports_per_study`). The launch-time port preflight (`_check_flask_ports_free`) catches an already-bound collision but has a TOCTOU gap against a *concurrently starting* campaign — the distinct-port rule is the actual prevention. A non-zero study exit is now surfaced loudly (`_report_study_exits`), not silently counted done.
- **"Let's run it overnight and see"** without a budget cap. No — set `budget_usd` explicitly first. A misconfig can burn $500 overnight.
- **"Skip the baked image, just use cloud-init each time"**. Not supported. Packer bake is mandatory — cloud-init bulk apt/PyPI fails under 50+ concurrent cold starts.
- **"GPU cloud for speed"**. CPU per-instance throughput meets or exceeds local after the XRandR fix; GPU doesn't help this workload. The quantitative cloud-vs-local ratio was retired unmeasured ([../../docs/reports/2026-07-13-roadmap-regroom.md](../../docs/reports/2026-07-13-roadmap-regroom.md)).
- **"One giant study with 200 workers"**. TPE saturates above 24; a 200-worker mega-study wastes most of the budget on random sampling at the front of TPE's startup. Federate into ≤24-worker studies per `(hull, regime, seed)`.
- **"PostgreSQL for Optuna storage"**. Not needed — each study runs its own SQLite locally in a subprocess on the orchestrator.
- **"Let's try SkyPilot / Ray / Modal / Fargate"**. Already rejected in the design — see `docs/reference/phase6-cloud-worker-federation.md` §rejected alternatives.
- **"Add warm pools" at <$10k/mo spend**. EBS idle cost dominates; not worth it.

## References

- **Design doc**: `docs/reference/phase6-cloud-worker-federation.md`
- **Cloud deployment spec**: `docs/specs/22-cloud-deployment.md`
- **Empirical validation**: V2 throughput gate passed ([../../docs/reports/2026-05-10-wave1-validation.md](../../docs/reports/2026-05-10-wave1-validation.md)); remaining Phase 5/6 re-validation gates were retired/folded/parked by the [2026-07-13 re-groom](../../docs/reports/2026-07-13-roadmap-regroom.md). Dated reports are tracked in [../../docs/reports/INDEX.md](../../docs/reports/INDEX.md).
- **Cost model**: pre-V1-invalidation `experiments/phase6-planning/cost_model.py` was deleted alongside the rest of the V1 experiment artefacts; the next cost model lands as part of V2 re-validation.
- **Scripts**: `scripts/cloud/{devenv-up,devenv-down,launch_campaign,status,teardown,final_audit,watch_eval_cleanup,probe,bake_image}.sh` + `scripts/cloud/packer/aws.pkr.hcl`
- **LWJGL XRandR fix**: `src/starsector_optimizer/instance_manager.py::_start_xvfb`
