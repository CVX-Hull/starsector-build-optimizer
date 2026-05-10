---
type: report
status: shipped
last-validated: 2026-05-10
---

# Wave 1 honest-eval — stall checkpoint and cleanup fix (eval_tag `…20260510T170431Z`)

Snapshot of the in-flight Wave 1 honest-evaluation run captured immediately before recommended kill. Records what is on disk, what guardrails tripped, and how to resume.

**2026-05-10 follow-up.** The cleanup-side issue is fixed in code:
`honest_evaluator.main()` installs SIGTERM/SIGHUP handlers, returns 130
after interrupted cleanup, and invokes the cloud-pool helper with an
honest-eval-only project-wide sweep. A second pass over the local
orchestrator log identified two direct stall causes: honest eval inherited
`max_lifetime_hours=2.0` from `examples/wave1-c0a.yaml`, so workers aged
out just before the collapse while EC2 instances stayed alive; and it
inherited `visibility_timeout_seconds=120.0`, so slow-but-live matchups
were requeued from minute two onward. Both timing inherits are now
overridden for honest eval before provisioning.

## Run identity

| Field | Value |
|---|---|
| eval_tag | `starsector-honest-eval-wave1-c0a-20260510T170431Z` |
| Launch script | `scripts/cloud/launch_wave1_honest_eval.sh` (Plan C, 64 workers, 128 slots) |
| Cells | `wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3` + random-baseline (n=9, seed=0) |
| top-k / replicates | 3 / 30 |
| Ranking method | `twfe_eb` |
| Started | 2026-05-10T17:04:31Z |
| Last record | 2026-05-10T19:53:50Z |
| Elapsed | 2 h 49 min |
| Local PID at snapshot | 17167 (`python -m starsector_optimizer.honest_evaluator …`) |

## Progress at snapshot

| Cell | seed0 r1 | seed0 r2 | seed0 r3 | seed1 r1 | seed1 r2 | seed1 r3 | seed2 r1 | seed2 r2 | seed2 r3 |
|---|---|---|---|---|---|---|---|---|---|
| c0a | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 | ✅ 1620 |
| c0b | ✅ 1620 | ✅ 1620 | ✅ 1620 | 🟡 1107 | — | — | — | — | — |
| c1 | — | — | — | — | — | — | — | — | — |
| c2 | — | — | — | — | — | — | — | — | — |
| c3 | — | — | — | — | — | — | — | — | — |
| random-baseline | not yet started — synthesized via `synthesize_random_baseline_builds(seed=0)` at run-time, deterministic |

12 of 54 build panels complete + 1 partial. The 54 panels are 45
cell × seed × rank builds plus 9 random-baseline builds. Each completed
panel holds 1620 records (54 opponents × 30 reps); expected total at
finish is 87,480 ledger entries. Currently 20,547 unique resume keys
(`read_ledger()` count; also 20,547 physical JSONL lines). **Wall-clock progress
≈ 23.5 %.** The c0a cell (the eval_tag's lead cell) is fully done;
partial work is in c0b/seed1/rank1.

## Ledger location

```
data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/
└── results.jsonl   # 20,547 lines / 20,547 unique keys, ~5.7 MB, last-mtime 19:53Z
```

Append-only JSONL with `flush()` + `os.fsync()` per line (`_LedgerWriter` at `src/starsector_optimizer/honest_evaluator.py:125`). Schema-version-tagged. Resume-safe.

Orchestrator logs (gitignored):
```
data/honest_eval/orchestrator-20260510T170429Z.log   # 26 MB, this run
```

## Guardrail trip

The /loop sentry (set up at launch) runs four checks:

| # | Target | Observed | Verdict |
|---|---|---|---|
| (a) Throughput steady-state ≥ 250/min | ≥ 250/min | ~125/min for the first 2 h, then **collapse**: 44 → 11 → 2.4/min in the final three 15-min bins | **FAIL — never met target, now dying** |
| (b) Stuck-matchup requeue rate < 5 % | < 5 % | 1,074 requeues / 20,547 results = **5.2 %** in the final local log, climbing as workers drain | **FAIL (trending wrong)** |
| (c) Fitness diversity not stuck | distribution must not collapse | 1.5 mode 23.9 %, broad negative tail, 13 distinct builds — on-spec for a 1620-rep panel | PASS |
| (d) No LOADOUT_MISMATCH bursts | 0 LOADOUT_MISMATCH | 0 across 20,547 records | PASS |

Per-bin throughput (UTC):

```
17:05–17:20  133.7/min  ##########################
17:20–17:35  133.7/min  ##########################
17:35–17:50  169.1/min  #################################
17:50–18:05  180.0/min  ####################################
18:05–18:20  122.8/min  ########################
18:20–18:35  120.7/min  ########################
18:35–18:50  124.4/min  ########################
18:50–19:05  183.9/min  ####################################
19:05–19:20  143.7/min  ############################
19:20–19:35   44.3/min  ########            ← collapse begins
19:35–19:50   11.1/min  ##
19:50–20:05    2.4/min                       ← terminal
```

### Collapse signals visible in orchestrator log

- 568 HTTP 409s on `POST /result` — duplicates from requeued workers being rejected (wasted work, indicates the requeue path is firing on still-live matchups).
- New WARN class appearing post-19:30Z: `attempt 1/3 failed: matchup … did not receive result within 900.0s — retrying`. The 900 s deadline is the orchestrator's own kill-line for a matchup; tripping it means workers are no longer reporting at all on those slots.
- Stuck-matchup requeues escalate: most c0b/seed1/rank1 matchups now show `requeue_count=4–5` (= the same matchup has been re-dispatched 4–5 times).

## Diagnosis (preliminary)

Root cause is now confirmed from local evidence:

1. **Worker lifetime inherited from the source training YAML.**
   `examples/wave1-c0a.yaml` sets `max_lifetime_hours=2.0`. Honest eval
   reused that value. The run started at 17:04Z and throughput collapsed
   after 19:15Z, just after the worker-agent lifetime elapsed. The
   `starsector-worker.service` unit uses `Restart=on-failure`, while
   `worker_agent.main()` returns 0 on lifetime expiry, so the worker
   process exits cleanly and is not restarted. The EC2 instances remain
   running, explaining why teardown later found many live instances that
   were no longer producing results.
2. **Redis visibility timeout was too short for honest eval.**
   Honest eval also inherited the default `visibility_timeout_seconds=120.0`
   while its caller-level `result_timeout_seconds` was 900 s. The janitor
   began requeueing slow-but-live work at 13:06 local, exactly 120 s after
   first dispatch. Over the run it logged 1,074 requeues and the Flask
   listener rejected hundreds of duplicate result POSTs with HTTP 409.
   Requeue concentration later moved to `wave1-c0b/seed1/rank1` because
   that was the active panel when the worker lifetime limit was reached,
   not because that build or a single opponent was uniquely pathological.
3. **Long-tailed game-side TIMEOUTs are structural, not the cliff cause.**
   The ledger contains many legitimate 300 s in-engine TIMEOUT outcomes.
   Those increase average matchup walltime and make a 120 s visibility
   window unsafe, but they do not by themselves explain the abrupt collapse.

## Can the run be resumed? Yes.

Resume is built into `honest_evaluator` (spec 30; see task #95). The contract:

- **Ledger is append-only and dedup-keyed on `(build_id, opponent_variant_id, replicate_idx)`** (`read_ledger()` at `src/starsector_optimizer/honest_evaluator.py:85`).
- **Random-baseline regen is deterministic in `--random-baseline-seed`** (`synthesize_random_baseline_builds()` at `:164`), so the baseline cell's build IDs match a re-run, and its ledger entries (when they exist) carry over.
- A resume re-uses the *same eval_tag* — and therefore the same AWS resource tag. Any prior fleet must be torn down first or the next launch will refuse.

### Resume procedure

```bash
# 1. Capture the eval_tag (already known)
TAG=starsector-honest-eval-wave1-c0a-20260510T170431Z

# 2. Stop the local orchestrator if still running
kill 17167

# 3. Tear down the AWS fleet (idempotent across us-east-1/2, us-west-1/2).
#    teardown.sh accepts either the full Project tag or the tag with one
#    leading `starsector-` stripped.
scripts/cloud/teardown.sh honest-eval-wave1-c0a-20260510T170431Z

# 4. Verify zero running instances under that Project tag
for region in us-east-1 us-east-2 us-west-1 us-west-2; do
  aws ec2 describe-instances \
    --region "$region" \
    --filters "Name=tag:Project,Values=$TAG" \
              "Name=instance-state-name,Values=pending,running" \
    --query 'Reservations[].Instances[].InstanceId' --output text
done

# 5. Resume — only the missing 66,933 matchups dispatch
scripts/cloud/evaluate_campaign.sh \
  --hull hammerhead \
  --campaign-name wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 \
  --top-k 3 --replicates 30 --workers 64 \
  --random-baseline-n 9 --random-baseline-seed 0 \
  --ranking-method twfe_eb \
  --resume-from "$TAG"
```

### Resume cost forecast

- Remaining work: 87,480 − 20,547 unique ledger keys = **66,933 matchups**.
- At Plan-C steady-state (~125–180/min observed during the healthy phase), pure-walltime estimate is ~6.2–8.9 h.
- Conditional on launching with the timing fixes below. Reusing the old 2 h worker lifetime or 120 s visibility timeout will reproduce the stall.
- Cost: roughly **$65–90 incremental** at Plan-C rates, depending on spot mix and realized throughput; ledger checkpointing means a second stall costs only the slots burnt before the next kill.

### Resume hazards

- The 568 HTTP 409s in the prior log indicate the orchestrator already saw duplicate result POSTs against in-flight matchups (rejected at the HTTP layer, not the ledger layer — so the ledger remains de-duped). After resume, the same race can recur.
- The `--resume-from` preflight (spec 30) refuses if any prior fleet survives the teardown. **Do not skip step 4.** A leftover instance posting under the same eval_tag would queue-jump the resume.
- The data dir for this tag must remain untouched between kill and resume. In particular, do not rotate the orchestrator log (`data/honest_eval/orchestrator-…log`) — keeping it gives the post-mortem an evidence trail for whatever broke at 19:20Z.

## What happened on shutdown (post-action)

1. **`kill 17167` (SIGTERM) at 2026-05-10T19:54Z** — orchestrator unwound and exited within ~10 s.
2. **`scripts/cloud/teardown.sh honest-eval-wave1-c0a-20260510T170431Z`** reaped:
   - **21 instances in us-east-1**
   - **32 instances in us-east-2**
   - **2 security groups** (one per region)
   - 0 volumes, 0 resources in us-west-1 / us-west-2
3. `final_audit.sh` confirmed **zero remaining resources** under the eval_tag's Project tag.

### Root-cause signal: 53 of 64 workers survived the SIGTERM-unwind

The orchestrator's clean SIGTERM-unwind path is documented in CLAUDE.md as calling `AWSProvider.terminate_all_tagged()` (mirrored by `teardown.sh`). It did not. 53 instances had to be reaped manually.

This is consistent with hypothesis 1 (worker-pool degradation) but sharpens it:

- **The orchestrator was not aware that 53 workers had stopped working.** Workers were AWS-instance-alive (so `describe-instances` saw them) but were not delivering results back through the heartbeat / matchup-result channel — otherwise the throughput collapse would not have happened.
- **`terminate_all_tagged()` was apparently not invoked on shutdown.** If it had been, the 53 stragglers would have been reaped before SIGTERM-exit. Either the SIGTERM handler in `honest_evaluator` does not call it, or the call hit an exception that was swallowed.

Both findings are investigation leads that belong in the post-mortem (and possibly a new task) — they would not have surfaced from the ledger alone.

### Second shutdown rehearsal: shell orphan + local thread hang

The resumed run
`starsector-honest-eval-wave1-c0a-20260510T170431Z` was interrupted again
on 2026-05-10 while investigating the mismatch rate. This surfaced two
additional shutdown defects:

- `scripts/cloud/evaluate_campaign.sh` ran the evaluator as
  `uv ... 2>&1 | tee ...`. SIGTERM sent to the wrapper killed the shell
  side but did not forward the signal to the `uv`/Python child process
  group. The evaluator orphaned under PID 1 and continued until a direct
  SIGTERM was sent to the Python side.
- After direct SIGTERM, Python did complete cloud cleanup:
  31 instances in us-east-1, 32 instances in us-east-2, both launch
  templates, and both security groups were deleted by 17:48:48. A final
  audit confirmed zero live resources for the Project tag. The local
  process still remained because interpreter finalization was waiting on
  non-daemon worker threads blocked in `run_matchup()` result waits.

This means the paid-resource cleanup path worked after the signal reached
Python, but the local shutdown contract was incomplete: wrapper signals
must reach the evaluator process group, and pool teardown must wake blocked
dispatch threads so interpreter exit is not held hostage by
`result_timeout_seconds`.

## Recommended next steps

1. ✅ Kill orchestrator (PID 17167) — done.
2. ✅ Tear down AWS fleet — done. 53 stragglers reaped; final_audit clean.
3. Cleanup gap from the snapshot is fixed as described below; validate the final audit after the next honest-eval exit.
4. **Resume** per the procedure above after relaunching with the
   timing fixes described below.

If the resumed run still stalls with the adjusted timing, treat that as a
new failure mode and inspect live worker logs before another resume.

## Follow-up implementation

The checkpoint exposed two distinct problems: a fleet-health stall and an
orchestrator cleanup gap. The cleanup gap has been addressed:

- `src/starsector_optimizer/honest_evaluator.py` now installs SIGTERM and
  SIGHUP handlers that raise `KeyboardInterrupt`, matching the optimizer
  and campaign manager cleanup pattern.
- Honest eval passes `sweep_project_on_exit=True` to
  `prepare_cloud_pool()`. Because the honest-eval Project tag is unique,
  the helper can safely run `terminate_all_tagged(Project=<eval_tag>)`
  after the normal `terminate_fleet()` path. This option remains disabled
  for normal campaign study subprocesses because they share one campaign
  Project tag.
- Honest eval now derives cloud timing from the oracle workload instead
  of blindly inheriting the source training YAML: it raises worker
  lifetime above the estimated full-sweep walltime and raises Redis
  visibility above the full caller retry window.
- Honest eval flushes stale Redis queue/worker keys for the eval tag
  before launch/resume; the JSONL ledger remains the only resume substrate.
- `scripts/cloud/evaluate_campaign.sh` now runs a final audit on shell
  exit after it can parse the concrete eval tag from the orchestrator log.
- `scripts/cloud/evaluate_campaign.sh` no longer pipes the evaluator
  through `tee`. It redirects through process substitution, owns a direct
  child process, forwards INT/TERM/HUP to the evaluator process group, and
  waits for the child to finish cleanup before exiting.
- `CloudWorkerPool.teardown()` now wakes pending `run_matchup()` callers
  with `PoolShuttingDown` before shutting down the listener/janitor. This
  prevents non-daemon dispatch threads from blocking Python finalization
  after an interrupted run.
- `honest_evaluator.evaluate_builds()` cancels pending futures and avoids
  a blocking executor shutdown on interrupt, letting the surrounding
  cloud-pool context proceed to teardown immediately.
- Repeated SIGTERM/SIGHUP during shutdown is logged and ignored after the
  first signal so it cannot interrupt AWS cleanup halfway through.
- `scripts/cloud/teardown.sh` and `scripts/cloud/final_audit.sh` now accept
  either `honest-eval-...` or the full `starsector-honest-eval-...` tag,
  removing the operator foot-gun documented in the snapshot.
- Regression tests cover the opt-in project sweep, retry behavior, signal
  handler installation, interrupted-main exit path, and pool teardown
  waking a blocked `run_matchup()`.

Validated with:

```bash
uv run pytest tests/test_honest_evaluator.py tests/test_run_optimizer_cloud.py -q
```
