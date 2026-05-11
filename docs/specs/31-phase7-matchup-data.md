---
type: spec
status: shipped
last-validated: 2026-05-11
---

# Spec 31 — Phase 7 Matchup Data

Module contracts for Phase 7's derived matchup data layer. The implementation
lives in:

- `src/starsector_optimizer/matchup_features.py`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `scripts/analysis/phase7_materialize_matchups.py`
- `scripts/analysis/phase7_baseline_surrogate.py`

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
