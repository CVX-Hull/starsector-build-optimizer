---
title: Tier-2 oracle-coverage selection + honest-eval explicit-build input
status: implemented
approved: 2026-07-16
implemented: 2026-07-16
---

# Tier-2 oracle-coverage selection + honest-eval explicit-build input

Enables the **Tier-2 oracle pass** of roadmap item 3 (owning plan:
[2026-07-14-instrumented-accounting-run.md](2026-07-14-instrumented-accounting-run.md)).
The 9-cell hammerhead sim stream is collected and the frozen matchup DB is
materialized (`data/phase7/accounting_matchups.sqlite`: recovered_builds 5751,
training_matchups 21969; bijective join verified total). Three pieces are missing
before the oracle pass can honestly run over the **pre-registered 27** and have
its results **join back** to the stream:

1. **No selector** produces the rank-stratified subset (pre-registration
   [2026-07-14-accounting-stream-preregistration.md:96-105](../../docs/reports/2026-07-14-accounting-stream-preregistration.md):
   K=3 = 3 tertiles × 1 build/stratum × 9 cells = 27, ranked under the CatBoost
   opponent-adjusted arm). The tertile-cut + intra-tertile pick rule is
   unspecified there and must be **predeclared (ledger-appended) + committed
   before any selection runs** (anti-forking).
2. **`honest_evaluator` cannot take an explicit build list** — its only path
   globs eval logs for top-K-per-seed by TWFE-EB (`honest_evaluator.py:1281-1361`).
   Running as-is oracles a *different* 27 → pre-registration violation.
3. **The oracle results would not join back to the stream.** The replay's oracle
   statistic joins **by `build_key`** (`phase7_prequential_replay.py:726,753-761`),
   and the existing resolver `honest_build_id_to_key`
   (`phase7_matchup_data.py:514-528`) reconstructs ids from `recovered_builds`'
   **native** `rank`, **skipping `rank is None`** (`:517`) — which is every
   ordinary stream trial. A stratum-ordinal `source_rank` resolves to nothing →
   Tier-2 coverage silently zeroed. The selector JSON is the only artifact holding
   both the honest `build_id` components and the `build_key`, so a JSON-based
   resolver is required.

Consequences shaping sequencing:
- Piece 2 (and 3) touch `src/` → flip `WorkerSourceSha` → **a re-bake is required**
  before the oracle launch; the current 2026-07-16 AMIs go stale.
- Honest-eval has **no hard `budget_usd` cap** (spec 30 §Cost); the ~$48–60 is
  operator sizing via `--replicates` × matchup count + a measurement-only ledger
  that must be watched live. `--workers` buys **walltime, not cost**
  ([2026-07-11-aws-cost-analysis.md:99](../../docs/reports/2026-07-11-aws-cost-analysis.md)).

## Named constants (no magic numbers)
Declared at selector module scope and referenced in bodies:
`TERTILE_COUNT = 3`; `SELECTOR_HPO_SEED = learned.DEFAULT_HPO_SEED` (=23);
`SELECTOR_THREAD_COUNT = 1` (fit determinism); `SELECTOR_STUDY_IDX = 0`;
`ORACLE_SELECTION_SCHEMA_VERSION = 1`. Strata ordinals `bottom=1, middle=2,
top=3` as a named mapping.

## Scope

### S1 — Predeclare the selection rule (ledger append; docs, no code)
Append a **ledger entry** to
[2026-07-14-accounting-stream-preregistration.md](../../docs/reports/2026-07-14-accounting-stream-preregistration.md)
fixing the exact deterministic rule **before** the selector runs, and
**git-committed together with the selector code before first execution**:
- **Ranking arm**: `catboost_regressor` (opponent-adjusted),
  `learned.DEFAULT_HYPERPARAMETERS["catboost_regressor"]`,
  `learned.DEFAULT_HPO_SEED = 23`, `thread_count = 1`; per-cell **in-sample** fit
  on **all** of the cell's `training_matchups` rows (no row-kind filter — matches
  the replay arm `_fit_predict_scores`, `phase7_prequential_replay.py:806`, which
  fits on finalized + pruned rows; `TrainingMatchupRow` carries no `pruned`
  field).
- **Selection population**: the **distinct `build_key`** values within each
  hammerhead cell. Per-build predicted score = **mean predicted target over that
  build's matchup rows** in the cell (group CatBoost per-row predictions by
  `build_key`). (This is the "stream builds" unit of the pre-registration; a
  build re-proposed across trials is one selection unit. Diverges intentionally
  from `_fit_predict_scores`' per-`trial_number` grouping, whose purpose is
  per-trial regret, not build selection.)
- **Tertile rule (sole rule)**: sort distinct builds by `(predicted_score,
  build_key)` ascending → predicted rank; `numpy.array_split` the rank-ordered
  list into `TERTILE_COUNT` near-equal **contiguous** groups
  (bottom/middle/top). This *is* "thirds by predicted rank," is deterministic,
  and is always non-empty for ≥3 builds. **No score-value quantiles** (they are
  not the declared rank tertiles and fork on clustered predictions).
- **Intra-tertile pick**: the build at the **median predicted score of its
  stratum** — index `len(stratum)//2 - (1 if len even else 0)` (lower-middle on
  even counts) into the stratum's `(predicted_score, build_key)`-sorted list.
  Deterministic, no RNG.
- **`source_rank`** = stratum ordinal (bottom=1/middle=2/top=3) → the
  `(source_campaign, SELECTOR_STUDY_IDX, seed, source_rank)` tuple is unique
  across the 27 and survives the honest-eval `_build_id` guard
  (`honest_evaluator.py:648-678`).
- **<3-build cells**: fail loud (`ValueError`) — every hammerhead cell has ~250
  trials / hundreds of distinct builds, so <3 signals a data defect, not a case
  to silently degrade.
- **Anti-forking attestation**: the ledger entry states the rule was fixed on
  methodological grounds **without consulting the fitted predicted-score
  distribution or any oracle reading**, and the selector records the
  pre-registration doc's **git commit hash** (`prereg_commit`) in its output so
  "fixed before selection" is verifiable, not asserted.

### S2 — Selector script (new; TDD)
`scripts/analysis/phase7_select_oracle_builds.py` — offline, no sim spend.
- Compose (via `importlib` as `phase7_prequential_replay.py:52-71` does):
  `phase7_matchup_data.load_recovered_builds` / `load_training_matchups`
  (→ `RecoveredBuild.build` is a `Build`, both carry `build_key`);
  `baseline._feature_bundle(cell_rows, build_lookup, cfg)`;
  `learned.make_model("catboost_regressor", learned.DEFAULT_HYPERPARAMETERS["catboost_regressor"], SELECTOR_HPO_SEED, model_thread_count=SELECTOR_THREAD_COUNT)`;
  `.fit(bundle.rows, bundle.records, bundle.targets)`; `.predict(...).predictions`;
  `force_deterministic_predict(model)` (harmless no-op for CatBoost — the real
  determinism guarantors are `random_seed=23` + `thread_count=1`; the
  byte-identical guarantee is scoped to same-host / same CatBoost version).
- **`BaselineConfig` construction** (`phase7_baseline_surrogate.py:115-130` has
  ~11 required fields; `_feature_bundle` consumes only `game_dir` +
  `feature_profile`): construct with `game_dir=<arg>`,
  `feature_profile=baseline.DEFAULT_FEATURE_PROFILE`, and the remaining required
  fields set to explicit inert values with a comment
  `# inert here — _feature_bundle reads only game_dir + feature_profile`
  (`db_path=<frozen db>`, `split="train"`, `model="catboost_regressor"`,
  `holdout_fraction=0.0`, `train_fraction=1.0`, `seed=SELECTOR_HPO_SEED`,
  `tree_count=0`, `ridge_alpha=0.0`, `max_rows=None`, `top_k_values=()`,
  `progress=False`) — exact values pinned in the S2 spec block so they are not
  invented at implementation time. (`max_rows=None` is the no-limit sentinel —
  the safe inert value; `0` would slice to empty were the field ever read.)
- **Output JSON** (`schema_version = ORACLE_SELECTION_SCHEMA_VERSION`): top-level
  `selector`, `hpo_seed`, `thread_count`, `tertile_rule` ("array_split rank
  tertiles"), `source_frozen_db`, `prereg_commit`; **per-cell** `tertile_sizes`
  (the 3 group sizes — cuts are per-cell, not global); per build:
  `source_campaign` (const `accounting-hammerhead`), `source_study_idx`
  (`SELECTOR_STUDY_IDX`=0), `source_seed_idx` (seed), `source_rank` (stratum
  ordinal), `stratum`, `predicted_score`, `predicted_rank_in_cell`, `build_key`,
  `trial_number` (min trial_number for that build_key in the cell — provenance),
  `build` (canonical dict via a **public** `canonical_build_dict` — promote the
  current private `phase7_matchup_data._canonical_build_dict` at `:197`, same as
  `_build_from_canonical` in S3, since both are now cross-module imports).
- CLI: `--frozen-db`, `--game-dir`, `--campaign` (default `accounting-hammerhead`),
  `--seeds` (default 100-108), `--prereg-commit` (required — the committed
  pre-registration hash), `--output`. **No `--selection-seed`** (the pick is
  RNG-free; a seed knob would be dead config).
- Tests (`tests/test_phase7_select_oracle_builds.py`), one per behavior:
  array_split rank tertile on a synthetic ranked set; **all-equal predicted
  scores** (degenerate → array_split still yields 3 non-empty groups + picks 3);
  even- vs odd-sized stratum median index rule; **<3-build cell → ValueError**;
  distinct-`build_key` population (a build duplicated across trials counts once;
  provenance `trial_number` = min); per-cell fit isolation (no cross-cell row
  leakage); 27-row shape + `source_rank` uniqueness; JSON round-trips to a valid
  `Build`; determinism (two runs byte-identical, same host).

### S3 — `honest_evaluator --builds-file` (src; spec 30 amend; TDD)
- **Spec 30** ([docs/specs/30-honest-evaluator.md](../../docs/specs/30-honest-evaluator.md)):
  document the `--builds-file` path — schema consumed, provenance mapping, that
  `--campaign-name` + `--hull` remain **required** (naming/eval_tag/YAML-default
  only under this path; the glob is bypassed), mutual exclusion with
  `--random-baseline-n > 0`, and fail-loud on repair.
- **Code** (`honest_evaluator.py`): add `--builds-file` (default `None`) near
  `:1161`. When set:
  - **bypass** the campaign glob loop (`:1281-1361`) **and** the random-baseline
    block (`:1366-1380`); build `builds_with_provenance` from the JSON —
    `_build_from_canonical` → `repair_build(build, hull, game_data, manifest)`
    (revalidate; **raise** on failure, mirroring `extract_top_builds`'s fail-loud
    at `:550-559`, not silent skip) →
    `_BuildWithProvenance(build, source_campaign, source_study_idx,
    source_seed_idx, source_rank, source_value=predicted_score)`.
  - **mutual exclusion**: a **post-parse `parser.error()` guard** (not
    `add_mutually_exclusive_group`, which fires on mere co-presence and cannot
    express `> 0`) — hard-error if `--builds-file` is combined with
    `--random-baseline-n > 0` (prevents contaminating the 27 / overspend).
  - `--campaign-name` / `--hull` stay `required=True`; `campaign_name[0]` still
    feeds the campaign-yaml default (`:1405`) and `eval_tag`
    (`:1475`, keeps the `starsector-` prefix `teardown.sh` keys on). Validate the
    JSON `schema_version == ORACLE_SELECTION_SCHEMA_VERSION` (fail loud on
    mismatch).
  - `_build_from_canonical` is a leading-underscore helper in
    `phase7_matchup_data.py:207`; **promote it to a public name** (or add a public
    wrapper) rather than cross-importing a private symbol into `honest_evaluator`.
- Tests (`tests/test_honest_evaluator.py`): `--builds-file` yields the identical
  `_BuildWithProvenance` structure as the top-K path for an equivalent input;
  bypasses glob + random-baseline; raises on unrepairable build; raises on
  `schema_version` mismatch; **resolved `eval_tag` keeps the `starsector-`
  prefix**; `--builds-file` + `--random-baseline-n 1` → argparse error;
  `_build_id` uniqueness across the 27.

### S3b — Selector-JSON `build_id → build_key` resolver in the materializer (src; TDD)
Without this, the oracle ledger does not join back (problem 3 above).
- **Code** (`scripts/analysis/phase7_materialize_matchups.py` +
  `phase7_matchup_data.py`): add `--honest-selector-json <path>`. When supplied,
  resolve honest-ledger `build_id → build_key` from the selector JSON
  (`build_id = honest__{source_campaign}__s{source_study_idx}__seed{source_seed_idx}__rank{source_rank}`
  ↔ its `build_key`), taking precedence over / augmenting
  `honest_build_id_to_key` for those ids; unresolved honest ids still counted in
  `unresolved_honest_build_ids` (fail-loud visibility).
- Tests: a selector-JSON id resolves to the right `build_key`; an id absent from
  the JSON falls through to the existing resolver; end-to-end a materialize with
  `--honest-ledger` + `--honest-selector-json` yields `honest_eval_matchups` rows
  whose `build_key` matches the stream's `recovered_builds`.

### S4 — Verification gate + launch runbook (spend-gated; NOT auto-run)
**S4.0 — Verification (gates the re-bake; no spend):**
`uv run pytest tests/ -v && uv run ruff check . && uv run ruff format --check .
&& uv run mypy && uv run deptry .`; mechanical design-invariant checks; **post-impl
audit** (independent sub-agents); `grep -rn` stale-ref sweep. All green before
S4.1.
**S4.1 — Re-bake + pre-launch gates:** `bake_image.sh` → paste AMIs into the
honest-eval fleet-config path → `audit_amis.sh`/`cleanup_amis.sh` superseded →
stale-AWS sweep (`final_audit.sh` clean).
**S4.2 — Select:** run the selector → `data/phase7/accounting_oracle_builds.json`
(gitignored `data/` → recorded in the **retained-paths manifest**, not
git-tracked). Sanity: 27 builds, 3/seed, valid Builds, destroyer pool ≈ 28.
**S4.3 — Launch (stop for user spend confirm):**
`evaluate_campaign.sh` / `honest_evaluator --builds-file
data/phase7/accounting_oracle_builds.json --campaign-name accounting-hammerhead
--hull hammerhead --campaign-config examples/accounting-hammerhead.yaml
--replicates 30 --workers 64`. Matchups = 27 × ~28 × 30 ≈ 22.7k. Cost ≈ 22.7k ×
~$0.0010–0.0014/matchup (spot, `aws-cost-analysis.md:90`) ≈ **$23–32 spot; ~$48
with the documented 1.5× headroom** (`:130`). `--workers 64` ≈ ~3 h walltime
(buys walltime, not cost). **No hard cap — watch
`data/honest_eval/<tag>/cost_ledger.jsonl` live.** Drain on.
**S4.4 — Verify + teardown:** oracle scores for all 27; `final_audit.sh` clean.

Post-oracle (owning plan tasks): re-materialize with `--honest-ledger`
+ `--honest-selector-json`; run `phase7_prequential_replay.py` for the continuous
oracle-value-regret statistic; file accounting + replay reports; retire plans.

## Non-goals
- The replay statistic + reports (owning plan's tail).
- Any change to the CatBoost arm, featurizer, `evaluate_builds`, or `eval_pool`.
- The flask-port-serving preflight fix (tracked separately, post-oracle).

## Risks
- **In-sample fit**: pre-registration says "predicted rank," never out-of-sample;
  per-cell in-sample CatBoost is the faithful simplest reading, used **only to
  stratify** — oracle values are measured independently, so overfit cannot leak
  into the oracle-regret statistic (confirmed by review).
- **No hard budget cap**: mitigated by S4.3 sizing + live ledger watch + the
  user spend-confirm gate at S4.3.
- **Re-bake churn**: S3/S3b flip WorkerSourceSha; the re-bake is folded into S4.1
  behind the S4.0 verification gate.

## Implementation record (2026-07-16)

- **S1 DONE** — pre-registration entry 2 (deterministic selection rule) appended
  + committed before selection.
- **S2 DONE** — `scripts/analysis/phase7_select_oracle_builds.py` +
  `tests/test_phase7_select_oracle_builds.py` (22 tests: pure core + mocked
  `predicted_scores_for_cell` dedup/min-trial + `select_oracle_builds` per-cell
  isolation/shape). Validated on the real frozen DB → clean **27** (3 distinct
  builds/cell × 9, 9/stratum, 27 unique build_ids, monotonic bottom≤middle≤top).
- **S3 DONE** — `honest_evaluator.load_builds_from_file` + `--builds-file` +
  `parser.error` mutual exclusion; spec 30 documented; 7 tests.
- **S3b DONE** — `phase7_matchup_data.selector_json_build_id_to_key` +
  materializer `--honest-selector-json` (with a fail-loud guard that every
  resolved `build_key` exists in `recovered_builds`); 1 resolver test.
- **Helper promotion DONE** — `canonical_build_dict` / `build_from_canonical`
  public.
- **S4.0 verification DONE** — full suite **1215 passed, 1 skipped**;
  ruff/format/mypy/deptry/validate_docs/validate_active_plans all green; stale-ref
  grep clean.
- **S4.1–S4.3 (re-bake → select → launch)** — spend-gated, NOT in this commit.

Bug caught during validation: `np.int64` from `np.array_split` leaked into the
JSON payload → not serializable; fixed (`_stratify_indices` casts to native
`int`) + regression test.

## Post-impl audit (2026-07-16)

One independent read-only audit sub-agent (correctness + the $60-wasting join +
design-invariants + test adequacy). **Verdict: PASS** — the `build_id→build_key`
join verified byte-for-byte; selection rule faithful to entry 2; no invariant
violation. Findings folded: MEDIUM (missing `predicted_scores_for_cell` /
`select_oracle_builds` tests) → added; LOW (materializer fail-loud on absent
build_key) → added; LOW (`max_rows=None` vs pinned `0`) → plan reconciled (`None`
is the safe sentinel).

## Plan Review Gate
- Status: passed
- Skill: plan-review (Phases 1-4 self-review + the fresh-eye lanes below)
- Notes: two independent fresh-eye lanes (correctness + rigor) each returned
  REVISE; a consolidated confirmation pass returned PASS after all findings were
  folded. See Fresh-Eye Review Gate.

## Fresh-Eye Review Gate
- Status: passed
- Sub-agents: three read-only sub-agents — (1) correctness/pre-registration/
  honest-eval-integration lane, (2) DDD-TDD/design-invariants/determinism/cost
  lane, (3) confirmation pass on the revised plan.
- Folded findings: tertile rule → equal-count rank `array_split` (sole rule);
  **added S3b** selector-JSON `build_id→build_key` resolver (oracle results would
  otherwise not join back → Tier-2 silently zeroed); kept `--campaign-name`/
  `--hull` required + `starsector-` eval_tag prefix (16-instance-leak class);
  `parser.error()` mutual exclusion vs `--random-baseline-n>0`; single
  all-rows training policy (dropped `trial.pruned` miscitation); distinct
  `build_key` selection unit; verification gate (S4.0) before the paid re-bake;
  pinned `BaselineConfig` inert fields; corrected cost to ~$32 spot ×1.5≈$48;
  removed dead `--selection-seed`; named constants; per-cell `tertile_sizes`;
  promote `_canonical_build_dict`/`_build_from_canonical` to public.
