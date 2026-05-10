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

@dataclass(frozen=True)
class EvaluatedBuild:
    build: Build
    source_campaign: str            # e.g. "wave1-c0a"
    source_study_idx: int
    source_seed_idx: int
    source_rank: int                # 1 = best in that seed by Optuna's value
    source_value: float             # the within-cell shaped score (for diagnostic only)
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
def extract_top_builds(
    study_db_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    top_k: int,
) -> tuple[tuple[int, float, Build], ...]:
    """Open per-study SQLite, return (rank, value, Build) for top_k completed
    trials in descending value order.

    Implementation mirrors `optimizer._enqueue_warm_start_from_regime`'s
    extraction pattern (`optimizer.py:1233-1255`):

        completed = [t for t in study.trials
                     if t.state == TrialState.COMPLETE and t.value is not None]
        completed.sort(key=lambda t: t.value, reverse=True)
        for t in completed[:top_k]:
            build = repair_build(trial_params_to_build(t.params, ...), ...)

    Raises:
      ValueError if the study has fewer than top_k completed trials.
      RuntimeError if any selected trial's params fail repair_build —
        stale params are a data-corruption signal (search-space changed
        without migration, or repair operator regressed); they must NOT
        be silently skipped because doing so quietly alters what "top-k"
        means and breaks cross-cell comparison.
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
- `--game-dir game/starsector` — game data + manifest source
- `--out-root data` — JSON output root
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

Writes `data/campaigns/<name>/honest_eval.json` per input campaign plus
a single `data/campaigns/honest_eval_summary_YYYY-MM-DD.json` covering
all cells. Each JSON also embeds `pool_size` (= `len(pool_variant_ids)`)
and the resolved `config` dict for downstream tooling.

**Cloud-pool lifecycle.** `main()` provisions an isolated AWS fleet via
`cloud_runner.prepare_cloud_pool` (the shared context-manager helper
that also backs `run_cloud_study`). The honest-eval fleet uses a
distinct namespace `honest-eval-{first-campaign-name}-{utc-stamp}` for
all four name-bearing fields (`study_id`, `project_tag`, `fleet_name`,
plus the Flask port reserved at the top of the ACL range). This
isolation is mandatory: the source campaign's `terminate_all_tagged`
key must NOT match honest-eval's, or post-run teardown could sweep the
wrong fleet. `eval_tag` is capped at 60 chars so the doubled
`{project_tag}__{fleet_name}` form fits the AWS Launch Template name
limit (128 chars).

**Inherited fields from the source campaign YAML** (read via
`load_campaign_config(args.campaign_config)` and forwarded through
`prepare_cloud_pool`): `regions`, `instance_types`,
`spot_allocation_strategy`, `ami_ids_by_region`, `ssh_key_name`,
`max_lifetime_hours`, `matchup_slots_per_worker`, `redis_port`,
`base_flask_port`, `flask_ports_per_study`, `result_timeout_seconds`,
`visibility_timeout_seconds`, `janitor_interval_seconds`,
`max_requeues`. Honest-eval intentionally does NOT inherit
`studies` (it spins one fleet, not N) or `budget_usd` (no per-eval
budget cap; operator controls cost via `--workers` + `--replicates`).

**Required env vars** (subset of `run_cloud_study`; loaded via
`_require_env`): `STARSECTOR_WORKSTATION_TAILNET_IP`,
`STARSECTOR_BEARER_TOKEN`, `STARSECTOR_TAILSCALE_AUTHKEY`. Optional:
`STARSECTOR_DEBUG_SSH_PUBKEY`, `STARSECTOR_MOD_JAR_OVERRIDE_URL`,
`STARSECTOR_MOD_JAR_OVERRIDE_SHA256`. Notably, `STARSECTOR_PROJECT_TAG`
is NOT required — honest-eval derives its own tag.

**Preflight (`_preflight_for_honest_eval`)** runs after env-var loading
and before `prepare_cloud_pool`. All three gates delegate to public
helpers in `campaign` so honest-eval and `CampaignManager._preflight`
cannot drift on remediation messages or exception types — every
helper raises `PreflightFailure` (a `ValueError` subclass):

- `campaign.check_authkey_syntax(authkey)` — gate 5 (`startswith("tskey-auth-")`).
- `campaign.check_aws_credentials()` — gate 4 (`STS get_caller_identity`).
- `campaign.check_ami_tags_against_manifest(provider, ami_ids_by_region, manifest)`
  — "Manifest + AMI tag preflight (2026-04-19)" gate. Catches the
  silent-corruption case where an operator regenerated the manifest
  without re-baking the AMI: workers would run pre-G probe code
  against a v2 manifest, producing variant-id mismatches in the
  oracle pool.

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
honest_evaluate(campaign_names, config):
    # 1. Load each campaign's per-study DBs from data/study_dbs/<name>/
    builds_with_provenance = []
    for name in campaign_names:
        for db_path in sorted(glob("data/study_dbs/<name>/*.db")):
            study_idx, seed_idx = parse_filename(db_path)
            tops = extract_top_builds(db_path, hull, game_data, manifest, config.top_k_per_seed)
            for rank, value, build in tops:
                builds_with_provenance.append(BuildWithProvenance(
                    build=build, source_campaign=name,
                    source_study_idx=study_idx, source_seed_idx=seed_idx,
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
| Study DB has < `top_k_per_seed` completed trials | `extract_top_builds` raises ValueError. Operator must lower top_k or rerun with more budget. |
| Selected trial's params fail `repair_build` | `extract_top_builds` raises RuntimeError. Investigate: search-space drift, repair regression. Do NOT silently skip. |
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
