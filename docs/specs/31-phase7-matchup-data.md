---
type: spec
status: shipped
last-validated: 2026-05-12
---

# Spec 31 — Phase 7 Matchup Data

Module contracts for Phase 7's derived matchup data layer. The implementation
lives in:

- `src/starsector_optimizer/matchup_features.py`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `src/starsector_optimizer/phase7_learned_batch.py`
- `scripts/analysis/phase7_materialize_matchups.py`
- `scripts/analysis/phase7_baseline_surrogate.py`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `scripts/cloud/phase7_learned_batch.py`

This layer does not run simulations and does not replace honest evaluation. It
recovers prior-run build/matchup evidence into auditable local tables for
surrogate experiments.

## Source Authority

Use this precedence when reconstructing prior-run builds:

1. **JSONL optimizer logs** (`evaluation_log.jsonl`) are authoritative for
   rows they contain. They store the post-repair `Build` used for simulation,
   opponent results, trial number, score fields, and engine/covariate data.
2. **Optuna study DBs** (`data/study_dbs/.../*.db`) recover trial states,
   values, optimizer-space params, and unlogged history. DB builds must be
   decoded with `trial_params_to_build(params, hull_id)` and then passed
   through `repair_build(...)`.
3. **Honest-eval ledgers** store `(build_id, opponent_variant_id,
   replicate_idx) -> fitness`. Build specs are reconstructed from the
   candidate-selection logs via the same `extract_top_builds(...)` path used by
   the honest evaluator, or from completed `honest_eval.json` outputs for
   evaluator-generated builds such as random baselines.

Never treat a DB-reconstructed build as an exact logged build unless it is
cross-checked against a JSONL row.

## Provenance

```python
class BuildSourceKind(StrEnum):
    EXACT_LOGGED_BUILD = "exact_logged_build"
    DB_RECONSTRUCTED_BUILD = "db_reconstructed_build"
    HONEST_EVAL_CANDIDATE_BUILD = "honest_eval_candidate_build"
    HONEST_EVAL_OUTPUT_BUILD = "honest_eval_output_build"
    UNRESOLVED = "unresolved"
```

```python
@dataclass(frozen=True)
class RecoveredBuild:
    build_key: str
    build: Build
    source_kind: BuildSourceKind
    campaign: str | None
    study: str | None
    seed: int | None
    rank: int | None
    trial_number: int | None
    score: float | None
    source_path: str
```

`build_key` is a stable hash of the canonical build JSON:
`hull_id`, sorted `weapon_assignments`, sorted `hullmods`, `flux_vents`, and
`flux_capacitors`.

## Feature Rows

Feature rows are versioned by the module-level integer
`FEATURE_SCHEMA_VERSION`. Every row returned by this module includes
`feature_schema_version`. Any script that reports model metrics must also emit
that version and the source DB path in its JSON output.

`matchup_features.py` exposes:

```python
def build_feature_row(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
) -> dict[str, float | int | str]: ...

def opponent_feature_row(
    variant_id: str,
    game_dir: Path,
    game_data: GameData,
) -> dict[str, float | int | str]: ...

def matchup_feature_row(
    build: Build,
    opponent_variant_id: str,
    game_dir: Path,
    game_data: GameData,
    manifest: GameManifest,
) -> dict[str, float | int | str]: ...
```

Feature extraction uses existing parser/scorer/domain models. Unknown opponent
variants raise `FileNotFoundError`; variants whose `hullId` is unknown in
`GameData` raise `ValueError`; malformed direct variant files raise
`ValueError`.

Feature keys are deterministic. Slot features are emitted in sorted slot-ID
order with a stable `build_slot_{idx:02d}_...` prefix. Slot IDs are also
emitted as categorical values so the ordinal index is not the only identity.
Per-slot fields include:

- `slot_id`, `slot_type`, `slot_size`, `mount_type`,
- `angle_degrees`, `angle_sin`, `angle_cos`,
- `arc_degrees`, `arc_fraction`,
- `x`, `y`, `x_norm`, `y_norm`, `forward_projection`,
- `weapon_id`, `weapon_type`, `weapon_size`, `damage_type`,
- weapon OP, sustained DPS, sustained flux, range, ammo, projectile speed,
  turn rate, PD flag, and beam flag.

Empty slots use the categorical sentinel `EMPTY`; assigned weapon IDs missing
from `GameData.weapons` use `UNKNOWN`. Unknown weapons contribute to
`build_unknown_weapon_count` and zero-valued numeric weapon attributes.

Hullmod features are emitted from `GameData.hullmods` only. The feature row
uses multi-hot keys `build_hullmod__{hullmod_id}` and aggregate tag/UI-tag
counts. Unknown hullmods are counted in `build_unknown_hullmod_count` and do
not create hardcoded rule entries.

Opponent features are sourced from stock variant files and parsed `GameData`.
They include opponent hull and variant categorical residuals. No opponent
family registry is hardcoded in the feature extractor; any family diagnostics
use derived hull size/designation/manufacturer labels from `GameData`.

## Log And DB Recovery

`phase7_matchup_data.py` exposes:

```python
def build_key(build: Build) -> str: ...

def build_from_log_row(row: Mapping[str, Any]) -> Build: ...

def recover_logged_builds(paths: Sequence[Path]) -> tuple[RecoveredBuild, ...]: ...

def recover_study_db_builds(
    db_path: Path,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    *,
    campaign: str | None = None,
    study: str | None = None,
    seed: int | None = None,
) -> tuple[RecoveredBuild, ...]: ...

def iter_training_matchups(paths: Sequence[Path]) -> Iterator[TrainingMatchupRow]: ...

def recover_honest_eval_candidate_builds(
    eval_log_paths: Sequence[Path],
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
    *,
    top_k: int,
    method: str = "twfe_eb",
) -> tuple[RecoveredBuild, ...]: ...

def recover_honest_eval_output_builds(paths: Sequence[Path]) -> tuple[RecoveredBuild, ...]: ...

def honest_build_id_to_key(candidates: Sequence[RecoveredBuild]) -> dict[str, str]: ...

def iter_honest_eval_matchups(
    ledger_path: Path,
    build_id_to_key: Mapping[str, str] | None = None,
) -> Iterator[HonestEvalMatchupRow]: ...

def materialize_sqlite(
    db_path: Path,
    *,
    recovered_builds: Sequence[RecoveredBuild],
    training_matchups: Iterable[TrainingMatchupRow] = (),
    honest_eval_matchups: Iterable[HonestEvalMatchupRow] = (),
) -> None: ...

def load_recovered_builds(db_path: Path) -> tuple[RecoveredBuild, ...]: ...

def load_training_matchups(db_path: Path) -> tuple[TrainingMatchupRow, ...]: ...

def load_honest_eval_matchups(db_path: Path) -> tuple[HonestEvalMatchupRow, ...]: ...
```

DB recovery decodes Optuna categorical params by reading each
`distribution_json`; categorical `param_value` is an index into `choices`, while
integer/float distributions use the numeric value directly. Unsupported
distribution JSON raises `ValueError` instead of guessing.

## SQLite Schema

The derived DB is local generated data under `data/phase7/`.
Materialization replaces the contents of the Phase 7 tables for the selected
output DB; rerunning with fewer inputs must not leave stale rows from prior
materializations.

### `recovered_builds`

| Column | Type | Meaning |
|---|---|---|
| `row_key` | TEXT | Materialization conflict key |
| `build_key` | TEXT | Stable canonical build hash |
| `source_kind` | TEXT | `BuildSourceKind.value` |
| `campaign` | TEXT NULL | Source campaign/cell |
| `study` | TEXT NULL | Source study label |
| `seed` | INTEGER NULL | Source seed |
| `rank` | INTEGER NULL | Honest-eval source rank if applicable |
| `trial_number` | INTEGER NULL | Optimizer trial number |
| `score` | REAL NULL | Source score/value when present |
| `source_path` | TEXT | Source file path |
| `build_json` | TEXT | Canonical build JSON |

### `training_matchups`

| Column | Type | Meaning |
|---|---|---|
| `source_path` | TEXT | JSONL path |
| `campaign` | TEXT NULL | Source campaign/cell |
| `seed` | INTEGER NULL | Source seed |
| `trial_number` | INTEGER | Optimizer trial number |
| `build_key` | TEXT | Stable build key |
| `opponent_variant_id` | TEXT | Opponent variant |
| `opponent_index` | INTEGER | Index inside `opponent_results` |
| `target` | REAL | Per-opponent `hp_differential` from training logs |
| `row_kind` | TEXT | `finalized` or `pruned` |

Use `source_path`, `trial_number`, and `opponent_index` as the conflict key.

### `honest_eval_matchups`

| Column | Type | Meaning |
|---|---|---|
| `source_path` | TEXT | Honest-eval ledger path |
| `build_id` | TEXT | Honest-eval build identifier |
| `build_key` | TEXT NULL | Stable build key when candidate recovery resolved it |
| `opponent_variant_id` | TEXT | Opponent variant |
| `replicate_idx` | INTEGER | Honest-eval replicate index |
| `target` | REAL | Per-matchup honest-eval fitness |

Use `source_path`, `build_id`, `opponent_variant_id`, and `replicate_idx` as
the conflict key.

## Split Builders

```python
def held_out_build_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def held_out_opponent_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def held_out_replicate_split(
    rows: Sequence[HonestEvalMatchupRow],
    holdout_fraction: float,
    seed: int,
) -> SplitIds: ...
def held_out_component_combination_split(
    rows,
    build_lookup: Mapping[str, Build],
    holdout_fraction: float,
    seed: int,
) -> SplitIds: ...
def held_out_seed_cell_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def forward_time_split(rows, train_fraction: float) -> SplitIds: ...
```

`holdout_fraction` and `train_fraction` must be in `(0, 1)`, otherwise raise
`ValueError`. The group named by each split must not appear in both train and
test.

## Baseline Evaluation

The comparator-gate baseline script uses scikit-learn only. It loads the
derived SQLite DB, builds flat feature rows, and reports grouped split metrics.
Random row splits are allowed only as debugging output and must not be the
headline default.

The comparator-gate model names are:

```python
global_mean
opponent_mean
build_mean
twfe_additive
ridge_hybrid
random_forest
```

`random_forest` is retained as the carryover smoke baseline in full grid runs.
`catboost`, sparse-interaction models, model-assisted search, and BoTorch are
not part of the comparator gate. They require a later plan after comparator
outputs exist.

All models fit only on the split's training rows. No comparator or learned
baseline may read test targets or honest-eval targets while fitting. For unseen
groups at prediction time, `opponent_mean`, `build_mean`, and `twfe_additive`
fall back to the training global mean and report fallback counts. `ridge_hybrid`
and `random_forest` use feature rows only and must not include target-derived
test labels.

Reported metrics:

- `mae`
- `rmse`
- `spearman_rho`
- `n_train`
- `n_test`
- fallback counts when applicable

The script also reports stratified diagnostics where labels are available:

- opponent family using opponent hull size, designation, and manufacturer,
- score regime using fixed target bands (`loss`, `timeout_like`, `win`),
- campaign cell using `TrainingMatchupRow.campaign`.

Honest-eval top-k recall is required for this gate. The protocol is:

1. Fit the model on training-log matchups only.
2. Predict every resolved honest-eval matchup using build/opponent features.
3. Aggregate predicted and observed honest-eval targets by `build_key`.
4. Report `top_k_recall` for configured `k` values against the observed
   honest-eval build ranking.
5. Do not tune features, model choice, or hyperparameters on the same
   honest-eval rows cited by a report.

## Learned Baseline Experiment

The learned-baseline experiment is separate from the comparator gate. It may
use stronger model families and nested hyperparameter search, but it must keep
the comparator-gate script and model names stable.

### Library Usage Policy

Repository code owns the experiment contract: split construction, leakage
rules, provenance, comparator context, JSON/report schema, and honest-eval
diagnostic discipline. Library defaults must not replace those contracts.

Use libraries by layer:

- `scikit-learn` is the baseline ML framework for auditable pipelines,
  vectorization, metrics, random forests, ridge-style models, sparse
  interaction sketches, and simple feature-importance diagnostics.
- CatBoost is the first primary categorical gradient-boosted tree baseline. It
  uses native categorical handling through pandas columns and `cat_features`,
  not manual one-hot preprocessing.
- Optuna may be used for a later nested HPO implementation after the
  random-search baseline establishes runtime and search-space behavior. Any
  Optuna study must be created inside the inner validation loop and must never
  observe outer-test or honest-eval targets.
- LightGBM or XGBoost require a later plan and spec update. Add them only when
  CatBoost/RF results justify another boosted-tree implementation or runtime
  pressure requires it.
- BoTorch/GPyTorch/Torch are reserved for structured optimizer integration and
  calibrated allocation work, not this learned-baseline report.
- MLflow, Weights & Biases, or similar external experiment trackers are not
  part of this phase. Use JSON artifacts and the local report/SQLite
  provenance path first.

Parallel execution policy:

- Threaded HPO is acceptable for this phase because the expensive estimators
  are native-heavy (`scikit-learn`/OpenMP, CatBoost C++, NumPy/SciPy kernels)
  and generally release the Python GIL during fit/predict work.
- Avoid uncontrolled nested parallelism. Treat `hpo_jobs *
  model_thread_count` as the intended logical-core budget and record both
  fields in every artifact.
- Use conservative full-run defaults on a 16-logical-core workstation:
  `hpo_jobs = 4` and `model_thread_count = 4`.
- If scaling is poor, unstable, or memory-bound, move trial-level parallelism
  to a later `loky`/process-backed design with cached or memmapped feature
  matrices rather than increasing nested threads.

The learned-baseline script exposes:

```python
EXPERIMENT_SCHEMA_VERSION: int

@dataclass(frozen=True)
class LearnedExperimentConfig:
    db_path: Path
    game_dir: Path
    comparator_json_path: Path | None
    split: str
    model: str
    holdout_fraction: float
    train_fraction: float
    split_seed: int
    hpo_seed: int
    hpo_trials: int
    hpo_jobs: int
    model_thread_count: int
    max_rows: int | None
    top_k_values: tuple[int, ...]
    progress: bool
    allow_missing_optional_models: bool

def build_experiment_configs(
    config: LearnedExperimentConfig,
) -> list[LearnedExperimentConfig]: ...

def run_experiment(config: LearnedExperimentConfig) -> dict[str, object]: ...
```

All tunable thresholds, budgets, seeds, fractions, and search spaces live in
`LearnedExperimentConfig` or module-level named constants.

`hpo_jobs` controls concurrent HPO trial execution. `model_thread_count`
controls estimator-level parallelism for libraries that expose it, including
scikit-learn random forests and CatBoost. Reports must record both fields so
runtime comparisons are interpretable.

When `--output` is provided, the learned script writes partial checkpoint JSON
after each completed model/split result and overwrites it with the final
payload at completion. Checkpoints must include enough provenance to inspect an
interrupted run.

The initial learned model-family names are:

```python
random_forest_tuned
catboost_regressor
sparse_pairwise_ridge
```

The learned feature vectors may use game-data categorical descriptors such as
weapon IDs, hullmod IDs, slot IDs, and opponent variant IDs. They must not
include `build_key`, target-derived build means, target-derived opponent means,
TWFE residuals, honest-eval labels, or any field computed from outer-test
targets. Under held-out-build and held-out-opponent splits, unseen categories
must be handled by the fitted encoder or model without target fallback.

Each learned model/split run uses nested validation:

1. Build the outer split using one of the comparator-gate split names:
   `build`, `opponent`, `component`, `seed-cell`, or `forward-time`.
2. Build the inner validation split from outer training rows only, using the
   same grouping stressor as the outer split.
3. If the outer training rows cannot support that inner split, emit an
   `insufficient_inner_groups` result for the model/split instead of using a
   random row split.
4. Select hyperparameters by minimizing inner-validation RMSE. Tie breakers are
   higher Spearman rho and then lower fit/predict runtime.
5. Refit the selected model on the full outer training rows.
6. Evaluate once on the outer test rows.

The learned script accepts `--comparator-json`, defaulting to
`data/phase7/wave1_comparator_gate_2026-05-11.json`. Results include the
comparator artifact path, matching comparator context for the same split, and
default-vs-tuned deltas when tuning is used. If no matching comparator row
exists, the result records `comparator_missing` instead of failing.

The learned script accepts `--output <path>` and writes the JSON payload there
instead of requiring shell redirection. Parent directories are created when
missing.

CatBoost is part of the optional `surrogate` dependency set. If the caller
selects `catboost_regressor` and CatBoost is unavailable, the script raises an
actionable dependency error unless `--allow-missing-optional-models` is set. If
that flag is set, the JSON output records the skipped model and reason.

The learned experiment JSON output includes:

- `experiment_schema_version`
- `feature_schema_version`
- source DB path and game directory
- comparator JSON path
- code/version provenance, or `unknown`
- split seed and HPO seed
- train/holdout fractions
- model-family list and skipped-model list
- per-result target, feature-family summary, split family, HPO space, HPO
  trace summary, selected hyperparameters, metrics, comparator context,
  honest-eval diagnostic, timing, and leakage checklist

Honest-eval top-k recall remains a post-fit diagnostic only. No learned
baseline may train, tune, choose features, choose model families, or calibrate
on honest-eval targets.

## Learned AWS Batch Artifacts

The learned AWS batch runner is a distributed execution path for the learned
baseline experiment. Spec 22 owns the cloud lifecycle, preflight, budget,
teardown, UserData security, and authenticated control-plane requirements.
This spec owns the Phase 7 job matrix and artifact semantics.

The canonical batch job matrix is exactly:

- splits: `build`, `opponent`, `component`, `seed-cell`, `forward-time`;
- model families: `random_forest_tuned`, `catboost_regressor`,
  `sparse_pairwise_ridge`.

That produces 15 jobs. Each job runs
`scripts/analysis/phase7_learned_surrogate_experiment.py` with exactly one
split and exactly one model family, plus the configured source DB, game dir,
comparator JSON, HPO settings, split seeds, fractions, top-k values, progress
flag, and an explicit per-job `--output` path. The generated command must not
include honest-eval training inputs or unsafe feature/model-selection flags.

Per-job artifacts are normal learned-experiment JSON payloads with one
completed result. Batch provenance may add a top-level `batch_job` object with
`job_id`, `split`, `model`, `attempt`, `instance_id`, `region`,
`instance_type`, `bundle_sha256`, and timestamps. A skipped optional model is
a failed canonical job for full-run publication unless the batch config was
explicitly created for smoke/debug output.

The batch merge step must validate every per-job artifact before publishing:

- all 15 canonical job IDs are present exactly once;
- no job is failed, missing, duplicate, stale, or partial;
- every artifact has the same `experiment_schema_version`,
  `feature_schema_version`, source DB path, game dir, comparator JSON path,
  split/HPO seeds, train/holdout fractions, top-k values, dependency extra,
  source bundle SHA256, and code provenance;
- every result has a present and passing leakage checklist;
- every result's `split` and `model` match its job ID;
- comparator context is present for every result.

Valid merge writes a batch-internal `merged.json` and then atomically promotes
the canonical full-run artifact to
`data/phase7/learned_surrogate_full_2026-05-12.json`. Partial batches may
write `.partial` or batch-internal artifacts only; they must not overwrite the
canonical full-run path.
