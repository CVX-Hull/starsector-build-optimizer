---
type: spec
status: shipped
last-validated: 2026-05-10
---

# Spec 30 — Honest Evaluator

Module contract for `src/starsector_optimizer/honest_evaluator.py`. Re-scores
the top builds from a campaign (or a set of campaigns) against the closed
opponent population with high replication under a transform-free oracle.

Design rationale: [../reference/honest-evaluation-methodology.md](../reference/honest-evaluation-methodology.md).
Operational SOP: [../../.claude/skills/honest-evaluation.md](../../.claude/skills/honest-evaluation.md).

## Public API

### Frozen dataclasses

`HonestEvaluationConfig` lives in `models.py` (sibling to other `*Config`
dataclasses). The result types live in `honest_evaluator.py` (module-internal
domain types coupled to its API surface, like `_InFlightBuild` is private to
`optimizer.py`).

```python
@dataclass(frozen=True)
class HonestEvaluationConfig:
    """Tunables for the honest evaluation pass.

    All defaults derived in docs/reference/honest-evaluation-methodology.md
    §"Replication count"; do not tune without re-deriving.
    """
    top_k_per_seed: int = 3
    replicates_per_matchup: int = 30
    max_retries_per_matchup: int = 3
    fitness_config: CombatFitnessConfig = field(default_factory=CombatFitnessConfig)
    matchup_time_limit_seconds: float = 300.0  # passed to MatchupConfig
    progress_log_buckets: int = 20
    cloud_lifetime_headroom: float = 1.5
    cloud_min_lifetime_hours: float = 6.0

@dataclass(frozen=True)
class EvaluatedBuild:
    build: Build
    source_campaign: str            # e.g. "wave1-c0a"
    source_study_idx: int
    source_seed_idx: int
    source_rank: int                # 1 = best in that seed by `--ranking-method` (default `twfe_eb`)
    source_value: float             # the chosen ranker's score for this trial (α̂_EB by default; NaN for synthetic baselines)
    oracle_score: float             # mean fitness across pool × replicates
    oracle_se: float                # standard error of the mean
    n_matchups_succeeded: int       # = pool_size × replicates_per_matchup (always; failures retried)

@dataclass(frozen=True)
class CellSummary:
    cell_name: str                  # = source_campaign for one-cell-per-campaign layout
    n_builds_evaluated: int
    mean_top_k_oracle: float
    best_build_oracle: float
    best_build_se: float

@dataclass(frozen=True)
class HonestEvaluationResult:
    schema_version: int             # 1 (bumped on any output schema change)
    evaluated_builds: tuple[EvaluatedBuild, ...]
    cell_summaries: tuple[CellSummary, ...]   # ordered desc by mean_top_k_oracle
    pool_variant_ids: tuple[str, ...]         # the population evaluated against
    pool_size: int
    config: HonestEvaluationConfig
    started_at: str                 # ISO-8601 UTC
    finished_at: str
```

### Functions

```python
RANKING_METHODS = ("twfe_eb", "twfe", "raw_mean", "bradley_terry")

def extract_top_builds(
    eval_log_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    top_k: int,
    *,
    method: str = "twfe_eb",
) -> tuple[tuple[int, float, Build], ...]:
    """Read per-study `evaluation_log.jsonl`, return (rank, score, Build) for
    top_k completed trials under the chosen ranking estimator.

    **Default `twfe_eb` (2026-05-10 revision)**. Trials are ranked by
    α̂_EB from the post-hoc TWFE + EB pipeline (`posthoc_ranker.rank_twfe_eb`,
    which reuses `deconfounding.twfe_decompose` and `eb_shrinkage`).
    Phase5a + phase5d-without-X — see
    `docs/reports/2026-05-10-posthoc-ranker-research.md` for the
    empirical comparison and rationale.

    **Why JSONL, not SQLite.** TWFE / EB / Bradley-Terry all need the
    (build × opponent) score matrix. SQLite's `intermediate_values`
    keys per-opponent fitnesses by opaque step-index, which loses
    opponent identity. The JSONL row carries `opponent_results`
    (opponent id + winner + hp_differential per match) — the only data
    source that supports principled deconfounded ranking.

    **Why not raw mean (the prior default).** Wave 1 training-log analysis
    showed raw-mean confounding; see
    [2026-05-10-posthoc-ranker-research.md](../reports/2026-05-10-posthoc-ranker-research.md).
    The bias comes from opponent confounding: TPE+pruner schedules
    different builds against different opponent subsets, so per-trial
    means are contaminated by which opponents a build happened to face.
    `raw_mean` is kept as a `method=` choice for ablation only.

    **`method` choices** (one of `RANKING_METHODS`):
      - `twfe_eb`        — default; α̂ + EB shrinkage on residuals.
      - `twfe`           — α̂ without shrinkage (EB-off ablation).
      - `raw_mean`       — pre-2026-05-10 default; biased; ablation only.
      - `bradley_terry`  — logistic skill model; secondary signal.
        Disagreement vs `twfe_eb` is informative
        (TWFE = magnitude-of-victory; BT = did you win) and surfaces
        as a WARN in `main()` pre-dispatch via
        `report_method_disagreement`.

    Raises:
      FileNotFoundError if the log path does not exist (Wave 1 logs
        must be migrated via `scripts/migrate_wave1_eval_logs.py`;
        Wave 2+ writes natively per task #90).
      ValueError if the study has fewer than top_k completed (non-
        pruned, non-cache-hit, non-invalid-spec) trials, or the method
        is unrecognized.
      RuntimeError if any selected build's spec fails `repair_build`
        — stale spec is a data-corruption signal (search-space drift,
        manifest change), must NOT be silently skipped because doing so
        quietly alters "top-k" and breaks cross-cell comparison.
    """


def report_method_disagreement(
    eval_log_path: Path,
    top_k: int,
    methods: tuple[str, ...] = RANKING_METHODS,
) -> dict[str, list[str]]:
    """Diagnostic helper. Returns each estimator's top-K build hashes so
    `main()` can WARN when methods disagree before paying for the
    oracle pass."""


def synthesize_random_baseline_builds(
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    n: int,
    seed: int = 0,
    regime: str = "early",
) -> tuple[_BuildWithProvenance, ...]:
    """Generate `n` random feasible builds tagged with
    `source_campaign='random-baseline'` so they ride alongside campaign-
    derived cells through evaluate_builds + summarize_by_cell.

    Without this baseline cell, even successful Wave 1 honest-eval cell
    rankings cannot answer the existence question: "does ANY of the
    optimization machinery beat random feasible sampling?". With it,
    the cross-cell summary includes a `random-baseline` row whose
    `mean_top_k_oracle` provides the floor that any cell must clear
    to claim it added signal.

    Deterministic in `seed` so `--resume-from` re-generates the same
    baseline builds and the ledger keys still match.
    """

def discover_evaluation_pool(
    game_dir: Path,
    game_data: GameData,
    hull: ShipHull,
) -> tuple[str, ...]:
    """Return all stock variant ids of compatible hull-size for `hull`.

    Pure composition of existing primitives:

        pool = discover_opponent_pool(game_dir, game_data)   # spec 23
        return get_opponents(pool, hull.hull_size)           # spec 23

    "Compatible" = same `HullSize` as the player hull, identical to what
    the optimizer trains against (modulo the `active_opponents` curriculum
    subsetting which this function does NOT apply). Cross-size matchups
    are deliberately excluded — out of scope per honest-evaluation
    methodology §"Closed-system framing".
    """

def evaluate_builds(
    builds_with_provenance: Sequence[_BuildWithProvenance],
    eval_pool: tuple[str, ...],
    pool: EvaluatorPool,
    config: HonestEvaluationConfig,
    hull: ShipHull,
    ledger_path: Path | None = None,
) -> tuple[EvaluatedBuild, ...]:
    """Dispatch every (build × opp × replicate) matchup, retry failures up
    to `config.max_retries_per_matchup`, aggregate per-build mean.

    Concurrency:
      Uses `pool.num_workers` (the EvaluatorPool ABC property at
      `evaluator_pool.py:36-41`, NOT a dispatch_concurrency method). Mirrors
      `optimizer.py:610-613`'s ThreadPoolExecutor pattern.

    Matchup-id uniqueness:
      Each replicate gets `matchup_id = f"{build_id}_vs_{opp}_rep{N}"`. The
      `_rep{N}` suffix is mandatory — without it CloudWorkerPool's `_seen`
      dedupe (cloud_worker_pool.py:242-243) would silently collapse
      replicates as 409 duplicates and oracle_score would be computed
      from 1 result per (build, opp) pair instead of N.

    Failure handling:
      A failed matchup (CombatResult.error set) is retried up to
      max_retries_per_matchup. If still failing after retries, raises
      RuntimeError — `evaluate_builds` does NOT silently exclude failures,
      because that would break the balanced-design guarantee that justifies
      mean-fitness as the oracle. Operators investigating persistent
      failures should look at the worker logs for the specific failed
      matchup_id.

    Returns:
      One EvaluatedBuild per input build. Order matches input order.
    """

def summarize_by_cell(
    evaluated: Sequence[EvaluatedBuild],
) -> tuple[CellSummary, ...]:
    """Group EvaluatedBuild by source_campaign, compute per-cell summary.
    Returned tuple is ordered descending by mean_top_k_oracle (best cell first).
    """
```

### CLI entry point

`scripts/cloud/evaluate_campaign.sh` — bash wrapper, `set -euo pipefail`,
auto-sources `.env` per cloud-worker-ops convention. Calls
`uv run python -m starsector_optimizer.honest_evaluator <args>`. The Python
module exposes a `main(argv)` that takes:

- `--campaign-name <name>...` — one or more (multi-cell ablation eval)
- `--hull <id>` — required; the hull to evaluate
- `--top-k 3` / `--replicates 30` / `--max-retries 3` — see methodology
- `--ranking-method twfe_eb` — score column used by `extract_top_builds`
- `--game-dir game/starsector` — game data + manifest source
- `--out-root data` — JSON output root
- `--random-baseline-n 0` / `--random-baseline-seed 0` — append
  deterministic synthetic feasible baseline builds when `n > 0`
- `--campaign-config <path>` — source-campaign YAML for fleet config;
  default `examples/{first-campaign-name}.yaml`
- `--workers <N>` — fleet size; default `max(s.workers_per_study)` from
  the source campaign
- `--flask-port <P>` — listener port; default `campaign.base_flask_port +
  flask_ports_per_study - 1` (top of the tailnet ACL range, e.g. 9099 for
  the default `9000-9099`). Operator-supplied values MUST be within
  `[base_flask_port, base + flask_ports_per_study)` or `main()` raises
  pre-provision — workers cannot POST through the tailnet ACL otherwise.
- `--dry-run` — extract top builds + load campaign config + enumerate
  pool + run lightweight preflight (authkey + STS), then exit without
  provisioning AWS resources
- `--resume-from <eval_tag>` — reuse a prior eval_tag and replay its
  ledger. Matchups already present in `{out_root}/honest_eval/{eval_tag}/results.jsonl`
  skip dispatch; the stored fitness folds straight into the in-memory
  aggregation. Use this to recover from a SIGTERM / OOM / network
  partition mid-run. Reuses the eval_tag for AWS resource naming, so
  any prior fleet must be torn down first
  (`scripts/cloud/teardown.sh <eval_tag>`).

**Resume ledger.** During every run, `evaluate_builds` appends one JSON
line per successful matchup to `{out_root}/honest_eval/{eval_tag}/results.jsonl`
with `flush()` + `os.fsync()` (mirrors the spec 22 cost-ledger pattern).
Each line contains `schema_version`, `matchup_id`, `build_id`,
`opponent_variant_id`, `replicate_idx`, `fitness`, `completed_at`. The
triple `(build_id, opponent_variant_id, replicate_idx)` is the resume
key — `--resume-from` reads the ledger, populates the skip-set, and
pre-loads `scores_per_build` so aggregation matches a fresh full run.
A ledger entry referencing an unknown `build_id` (because operator
changed `--top-k` or campaign DBs between runs) raises rather than
silently mixing scores. Unknown `schema_version` lines are skipped with
a warning. Corrupt JSON lines raise. The ledger is only the resume
substrate — final outputs (`honest_eval.json`, the cross-cell summary)
remain in `{out_root}/campaigns/`.

**Cost measurement.** Honest-eval writes a **measurement-only cost ledger**
alongside the results ledger at
`{out_root}/honest_eval/{eval_tag}/cost_ledger.jsonl` (the
[spec 22 §"Cost ledger"](22-cloud-deployment.md) format, `budget_usd=None`).
This exists to record *realized* dollar spend for the honest-eval path, which
otherwise had no cost accounting (only the source-campaign path did). It does
**not** contradict the "does NOT inherit `budget_usd`" decision above: the
ledger is pure measurement — it records rows and warns nothing, and never caps
or aborts the run. Honest-eval keeps its operator-control cost model
(`--workers` + `--replicates`); a hard cap is deliberately absent because it
would terminate a run mid-oracle and leave the (build × opp × rep) design
unbalanced.

A background thread — the honest-eval analog of `CampaignManager.monitor_loop`
— drives a `CostHeartbeatTicker` (spec 22 §"Ledger tick") every
`ledger_heartbeat_interval_seconds` for the duration of the provisioned fleet.
The thread is entered inside the cloud-pool context and joined (bounded by
`teardown_thread_join_seconds`) on every exit path — normal, exception, and
`KeyboardInterrupt` — so it never outlives the fleet; a stop-event drives the
loop so the join returns promptly rather than blocking a full interval, and a
per-tick `except Exception` keeps a transient Redis/AWS blip from aborting a
paid eval. The ticker's Redis client is built with `decode_responses=True`
(spec 22 §"Ledger tick", step 1). Its heartbeat scan matches the honest-eval
fleet's `project_tag` (= `eval_tag`); workers post the same
`worker:<project_tag>:*:heartbeat` hashes as campaign workers (same AMI), so
cost attribution is identical to the campaign path.

Realized spend = `sum(delta_usd)` over the ledger (resume-safe). On
`--resume-from`, the ledger **appends** across fleet lifetimes and
`cumulative_usd` is **seeded** from the prior file's last row (via
`initial_cumulative`) so it stays monotone across the appended file and its
last value equals total realized spend.

Writes `data/campaigns/<name>/honest_eval.json` per input campaign plus
a single `data/campaigns/honest_eval_summary_YYYY-MM-DD.json` covering
all cells. Each JSON also embeds `pool_size` (= `len(pool_variant_ids)`)
and the resolved `config` dict for downstream tooling.

**Cloud-pool lifecycle.** `main()` provisions an isolated AWS fleet via
`cloud_runner.prepare_cloud_pool` (the shared context-manager helper
that also backs `run_cloud_study`). The honest-eval fleet uses a
distinct namespace `starsector-honest-eval-{first-campaign-name}-{utc-stamp}`
for all four name-bearing fields (`study_id`, `project_tag`, `fleet_name`,
plus the Flask port reserved at the top of the ACL range). The
`starsector-` prefix matches `CampaignManager.project_tag` and
`scripts/cloud/teardown.sh` (which prepends `starsector-`), so a single
teardown convention covers every fleet we provision; the
`honest-eval-{campaign}-{stamp}` segment ensures the source campaign's
`terminate_all_tagged` key does NOT match honest-eval's. `eval_tag` is
capped at 63 chars so the doubled `{project_tag}__{fleet_name}` form
fits the AWS Launch Template 128-char name limit.

`honest_evaluator.main()` installs SIGTERM and SIGHUP handlers that
raise `KeyboardInterrupt`, matching `scripts/run_optimizer.py` and
`CampaignManager.run()`. A Ctrl-C, `kill <pid>`, or shell SIGHUP must
therefore unwind the Python context managers instead of relying on the
process-default signal action. Interrupted honest-eval runs return exit
code 130 after cleanup.

**Shutdown sequence.** The intended interrupt path is:

1. The wrapper receives SIGINT/SIGTERM/SIGHUP and forwards it to the
   evaluator process group.
2. The evaluator's first signal raises `KeyboardInterrupt`; repeated
   signals while shutdown is already in progress are logged and ignored so
   they cannot interrupt fleet cleanup midway.
3. `evaluate_builds()` stops submitting work, cancels not-yet-started
   futures, and returns control to the surrounding cloud-pool context.
4. `CloudWorkerPool.teardown()` sets its stop event, wakes every
   `run_matchup()` caller blocked on a result event with
   `PoolShuttingDown`, shuts down the Flask listener, and joins the
   janitor/listener threads within the configured bound.
5. `prepare_cloud_pool.__exit__()` terminates the tagged fleet, deletes
   launch templates/security groups, and, for honest-eval only, runs the
   project-wide sweep/retry described below.
6. The wrapper runs final audit for the concrete Project tag. A clean audit
   must report zero live instances/resources before a resume starts.

This ordering matters because `ThreadPoolExecutor` worker threads are
non-daemon. If they remain blocked in `run_matchup()` until the full result
timeout, Python interpreter finalization waits for them even after AWS
resources have already been torn down; locally the run looks stuck while no
paid work is still active.

Because an honest-eval run has a unique `Project=<eval_tag>` tag, it
passes `sweep_project_on_exit=True` to `prepare_cloud_pool`. The helper
first runs the normal `terminate_fleet(fleet_name, project_tag)` path
and then runs a project-wide `terminate_all_tagged(project_tag)` sweep
with one `list_active(project_tag)` retry. This sweep option is disabled
for normal campaign studies because their subprocesses share a campaign
project tag and a per-study sweep would kill sibling studies.

Before provisioning, honest-eval derives an adjusted cloud campaign
configuration from the source campaign YAML:

- `max_lifetime_hours` is raised when needed to cover the honest-eval
  matchup budget. The lower-bound estimate is
  `N_matchups × (matchup_time_limit_seconds / MatchupConfig.time_mult) /
  total_matchup_slots`, multiplied by
  `HonestEvaluationConfig.cloud_lifetime_headroom` and floored at
  `HonestEvaluationConfig.cloud_min_lifetime_hours`. This prevents
  source-campaign training lifetimes (e.g. short budget-capped Wave cells)
  from aging out the honest-eval worker processes mid-oracle.
- `visibility_timeout_seconds` is raised above the full local
  retry window: `result_timeout_seconds × (max_retries_per_matchup + 1) +
  janitor_interval_seconds`. Honest-eval already retries
  `WorkerTimeout` at the caller level; the Redis janitor must not
  re-dispatch a slow-but-live matchup before that caller-level timeout
  has resolved.
- `CloudWorkerPool` retains accepted late results by `matchup_id` even
  when the original dispatcher thread has already timed out. A retry for
  the same `matchup_id` must consume the retained result before enqueueing
  duplicate work, and timeout cleanup must return a result if it arrived
  during the wait/cleanup race. This keeps clean late results on the
  normal `pool.run_matchup()` return path so `evaluate_builds` can append
  them to the honest-eval ledger.
- Stale Redis keys under `queue:<eval_tag>:*` and `worker:<eval_tag>:*`
  are flushed before launch/resume. The append-only ledger is the resume
  substrate; Redis queues are ephemeral in-flight state and must not
  survive across interrupted fleets.

**Inherited-then-adjusted fields from the source campaign YAML** (read via
`load_campaign_config(args.campaign_config)`, adjusted for honest-eval
timing when needed, then forwarded through `prepare_cloud_pool`):
`regions`, `instance_types`,
`spot_allocation_strategy`, `ami_ids_by_region`, `ssh_key_name`,
`max_lifetime_hours`, `matchup_slots_per_worker`, `redis_port`,
`base_flask_port`, `flask_ports_per_study`, `result_timeout_seconds`,
`visibility_timeout_seconds`, `janitor_interval_seconds`,
`max_requeues`, and — inherited **as pass-through, not adjusted** — the
cost-measurement cadence knobs `ledger_heartbeat_interval_seconds`,
`heartbeat_stale_multiplier`, `spot_price_cache_ttl_seconds`
(§"Cost measurement"). Honest-eval also reads `teardown_thread_join_seconds`
for the cost-thread join bound and `redis_preflight_timeout_seconds` as the
`socket_timeout` for the cost-measurement Redis client (same use as
`CampaignManager._preflight`). Honest-eval intentionally does NOT inherit
`studies` (it spins one fleet, not N) or `budget_usd` (no per-eval
budget cap; operator controls cost via `--workers` + `--replicates`).

**Env vars (auto-resolved)**: `honest_evaluator.main` resolves the same
inputs `CampaignManager` generates per study, but from primitive sources
so the standalone CLI doesn't require manual exports.

| Var | Source (in order of precedence) |
|---|---|
| `STARSECTOR_WORKSTATION_TAILNET_IP` | env var, else shell out to `tailscale ip -4` (`_resolve_tailnet_ip`) |
| `STARSECTOR_BEARER_TOKEN` | env var, else fresh `uuid.uuid4().hex` per run |
| `STARSECTOR_TAILSCALE_AUTHKEY` | env var, else `TAILSCALE_AUTHKEY` (the name `.env` exports) |
| `STARSECTOR_DEBUG_SSH_PUBKEY` | optional; env var only |
| `STARSECTOR_MOD_JAR_OVERRIDE_URL` / `..._SHA256` | optional; env var only (set by `serve_mod_jar.sh`) |

`STARSECTOR_PROJECT_TAG` is NOT consumed — honest-eval derives its own
namespace `starsector-honest-eval-{first-campaign}-{utc_stamp}`
(separate from the source campaign's project_tag to prevent
fleet-resource collision). Operator-supplied env vars still win over
auto-resolution.

**Preflight (`_preflight_for_honest_eval`)** runs after env-var loading
and before `prepare_cloud_pool`. All three gates delegate to public
helpers in `campaign` so honest-eval and `CampaignManager._preflight`
cannot drift on remediation messages or exception types — every
helper raises `PreflightFailure` (a `ValueError` subclass):

- `campaign.check_authkey_syntax(authkey)` — gate 5 (`startswith("tskey-auth-")`).
- `campaign.check_aws_credentials()` — gate 4 (`STS get_caller_identity`).
- `campaign.check_ami_tags_against_manifest(provider, ami_ids_by_region,
  manifest, required_regions=campaign.regions)` — "Manifest + AMI tag
  preflight" gate. Catches silent-corruption cases where an operator
  regenerated the manifest, changed Java, or changed Python worker code
  without re-baking every regional AMI. Workers must run the same game
  data, mod commit, and committed worker source as the orchestrator.

NOT included (deferred): tailnet IP probe (trusts env var), Redis
tailnet exposure (assumes a working devenv).

`honest_eval.json` schema (per-campaign):

```json
{
  "schema_version": 1,
  "campaign": "wave1-c0a",
  "config": { ... HonestEvaluationConfig as dict ... },
  "pool_variant_ids": [...],
  "evaluated_builds": [{ ... EvaluatedBuild as dict ... }],
  "started_at": "2026-05-11T03:42:00+00:00",
  "finished_at": "2026-05-11T04:08:14+00:00"
}
```

The CLI does NOT auto-write to `docs/reports/`. Honest-eval writeups are
hand-authored by the operator with the JSON as data input — convention
per `docs/CONVENTIONS.md` (reports are dated empirical evidence requiring
human-authored frontmatter + narrative + cross-references).

## Algorithm

```
honest_evaluate(campaign_names, config, ranking_method="twfe_eb"):
    # 1. Load each campaign's per-study eval logs from data/logs/<name>/
    #    (post-task-#90 layout; Wave 1 logs were migrated via #101).
    builds_with_provenance = []
    for name in campaign_names:
        for jsonl_path in sorted(glob("data/logs/<name>/*/evaluation_log.jsonl")):
            seed_idx = parse_seed_from_dirname(jsonl_path.parent.name)
            # Diagnostic — WARN if methods disagree on top-K (heavy
            # opponent confounding or near-tied top region).
            disagreement = report_method_disagreement(jsonl_path, config.top_k_per_seed)
            tops = extract_top_builds(
                jsonl_path, hull, game_data, manifest,
                config.top_k_per_seed, method=ranking_method,
            )
            for rank, value, build in tops:
                builds_with_provenance.append(BuildWithProvenance(
                    build=build, source_campaign=name,
                    source_study_idx=0, source_seed_idx=seed_idx,
                    source_rank=rank, source_value=value,
                ))

    # 2. Enumerate evaluation pool (same for all builds — same hull)
    eval_pool = discover_evaluation_pool(game_dir, game_data, hull)

    # 3. Dispatch matchups (build × opp × rep), retry failures
    evaluated = evaluate_builds(builds_with_provenance, eval_pool, pool, config)

    # 4. Aggregate per-cell
    summaries = summarize_by_cell(evaluated)

    # 5. Build HonestEvaluationResult, write JSON per campaign + summary
    write_outputs(evaluated, summaries, config)
```

## Error conditions

| Condition | Behavior |
|---|---|
| Eval log path missing | `extract_top_builds` raises FileNotFoundError. Wave 1 needs `scripts/migrate_wave1_eval_logs.py`; Wave 2+ writes natively per task #90. |
| Eval log has < `top_k_per_seed` completed (non-pruned, non-cache-hit, non-invalid-spec) trials | `extract_top_builds` raises ValueError. Operator must lower top_k or rerun with more budget. |
| Unknown `--ranking-method` | `extract_top_builds` raises ValueError listing valid choices. |
| Selected trial's build fails `repair_build` | `extract_top_builds` raises RuntimeError. Investigate: search-space drift, manifest change, repair regression. Do NOT silently skip. |
| Methods disagree on top-K (zero overlap) | `main()` logs WARN via `report_method_disagreement` but proceeds. Operator inspects the JSONL before paying. |
| `replicates_per_matchup < 1` | `HonestEvaluationConfig.__post_init__` raises ValueError. |
| `top_k_per_seed < 1` | Same. |
| `max_retries_per_matchup < 0` | Same. |
| Matchup fails after all retries | `evaluate_builds` raises RuntimeError citing the offending matchup_id. Operator investigates worker logs. |
| Pool is empty (no compatible opponents) | `evaluate_builds` raises ValueError. Investigate: hull-size lookup, opponent-pool discovery. |

All errors propagate to the CLI which exits non-zero.

## Cross-references

- `combat_fitness.combat_fitness` (spec 25): per-matchup scorer used as the oracle
- `discover_opponent_pool` + `get_opponents` (spec 23): opponent enumeration
- `repair_build` (spec 05): trial-params → Build conversion
- `EvaluatorPool` (spec 22): polymorphic matchup dispatch (Cloud or Local)
- `MatchupConfig` (spec 09): the matchup payload sent to workers
- `cloud_runner.prepare_cloud_pool` (spec 22 §Per-study fleet lifecycle):
  shared fleet provision + pool entry + teardown context manager. Used
  by both `run_cloud_study` and `honest_evaluator.main` to guarantee
  the same lifecycle ordering (provision → pool.__enter__ → caller
  work → pool.__exit__ → terminate_fleet).
- `campaign.check_authkey_syntax`, `campaign.check_aws_credentials`,
  `campaign.check_ami_tags_against_manifest` (spec 22 §"Manifest + AMI
  tag preflight"): the three preflight gates honest-eval shares with
  `CampaignManager._preflight`. Every change to remediation messages,
  exception types, or skip behavior MUST land in one place and flow
  through to both call sites.

## What this spec does NOT define

- Faction-frequency-weighted oracle (deferred 2026-05-10 per user direction)
- Cross-size matchups in the evaluation pool (out of scope; smuggles a new
  game-rule decision into evaluation)
- Modded variant support (manifest-as-oracle invariant)
- Statistical hypothesis testing between cells (means + SEMs are the
  output; formal testing can be added later if cell deltas are too tight
  to call from SEM overlap)
- Auto-writing to `docs/reports/` (convention requires hand-authored
  reports; CLI writes only structured JSON)
