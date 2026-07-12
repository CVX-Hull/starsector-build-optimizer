---
type: spec
status: shipped
last-validated: 2026-07-12
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

This spec also owns the Phase 7 learned-surrogate experiment and AWS batch
artifact contracts. The filename remains matchup-oriented to avoid repo-wide
link churn.

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
`FEATURE_SCHEMA_VERSION`. The version is provenance, not data: feature rows
must NOT contain a `feature_schema_version` column (a constant column in the
model input is a schema leak — methodology review L1). Any script that
reports model metrics must emit the module-level version, the selected
`feature_profile`, and the source DB path in its JSON output. Feature
schema v4 is the structured static-game-data feature surface with no
schema-version column (v3 minus that constant column — the same version
number must never name two different column sets, so removing the column
bumped the version). Historical v2/v3 artifacts remain valid only as
evidence at their own versions.

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

def filter_feature_profile(
    row: Mapping[str, float | int | str],
    feature_profile: str = "all",
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
- `arc_degrees`, `arc_fraction`, `arc_bucket`,
- `x`, `y`, `x_norm`, `y_norm`, `forward_projection`,
  `lateral_offset`, `longitudinal_offset`,
- `weapon_id`, `weapon_type`, `weapon_size`, `damage_type`,
- weapon OP, sustained DPS, sustained flux, range, ammo, projectile speed,
  turn rate, PD flag, and beam flag.

Empty slots use the categorical sentinel `EMPTY`; assigned weapon IDs missing
from `GameData.weapons` use `UNKNOWN`. Unknown weapons contribute to
`build_unknown_weapon_count` and zero-valued numeric weapon attributes.
Built-in weapons are resolved consistently for per-slot, small-slot, and
aggregate weapon features.

Schema v3 adds static geometry features parsed from `.ship` files:
`geometry_width`, `geometry_height`, `geometry_collision_radius`,
`geometry_center_*`, `geometry_shield_center_*`, `geometry_shield_radius`,
`geometry_style`, and engine-slot count/width/length summaries. These fields
are static structured game data; sprite pixels, video, and audio are not part
of this feature schema. Tactical arc summaries use module-level named
constants for front/aft/port/starboard bucket thresholds and emit
`arc_{bucket}_slot_count`, `arc_{bucket}_weapon_dps`,
`arc_{bucket}_weapon_range_weighted_dps`, `arc_{bucket}_pd_count`,
`arc_broadside_weapon_dps`, `arc_frontal_weapon_dps`, and
`arc_aft_weapon_dps`.

Player/build feature rows do not model fighter wings under v3 because the
current optimizer and combat-harness build contracts do not support non-empty
player wings. Opponent rows may emit descriptive stock-variant wing pressure
from `hulls/wing_data.csv` and variant `wings` lists, including wing count,
OP, fleet points, total wingcraft count, mean range, mean attack-run range,
mean refit time, role counts, tag counts, and unknown-wing count.

Hullmod features are emitted from `GameData.hullmods` only. The feature row
uses multi-hot keys `build_hullmod__{hullmod_id}` and aggregate tag/UI-tag
counts. Unknown hullmods are counted in `build_unknown_hullmod_count` and do
not create hardcoded rule entries.

Opponent features are sourced from stock variant files and parsed `GameData`.
They include opponent hull and variant categorical residuals. No opponent
family registry is hardcoded in the feature extractor; any family diagnostics
use derived hull size/designation/manufacturer labels from `GameData`.
Opponent rows also emit variant vents/capacitors, hullmod OP, built-in overlap,
unknown weapon count, scorer-like summaries, hull system ID, phase stats, and
the same static geometry/arc summaries as build rows.

Feature profiles are deterministic ablation subsets:

- `all` — every feature in the current schema.
- `aggregate` — aggregate/scorer/context features without per-slot sparse
  component columns or interaction columns.
- `geometry` — aggregate features plus geometry, slot placement, and arc
  pressure fields.
- `opponent-parity` — build/opponent aggregate context without sparse ID
  residuals or explicit interaction fields.
- `sparse-component` — aggregate features plus hull/slot/weapon/hullmod/
  opponent categorical components.
- `sparse-cross` — sparse-component features plus explicit interaction fields.

Unknown feature profiles raise `ValueError`.

Future feature-selection experiments must not treat the materialized feature
columns as an anonymous flat mask. Any learned-selection artifact that claims
feature-selection evidence must include a feature-family registry for the
feature schema/profile used in that run. For every generated feature column,
the registry records:

- `family`: semantic subsystem such as hull, mobility, flux, defense, weapon
  pressure, slot geometry, hullmod, opponent aggregate, sparse component,
  explicit interaction, or provenance/context;
- `template`: generator pattern such as raw descriptor, aggregate, normalized
  ratio, categorical residual, sparse indicator, interaction, binned value, or
  learned/derived embedding;
- `parents`: empty for main-effect features and the parent feature families
  for interaction features;
- `leakage_risk`: `low`, `medium`, or `high`, with sparse IDs, rare component
  fingerprints, and target-derived aggregates classified conservatively.

Feature selection, family screening, clustering, dimensionality reduction,
encoding, scaling, target transforms, calibration, and HPO are all fitted
operations. They must be fit inside the training portion of the relevant
outer split. No fitted preprocessing or feature-selection decision may use
outer-test or honest-eval targets.

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
BURNED_SPLIT_SEEDS: frozenset[int]  # {17} — retired outer-split seeds

@dataclass(frozen=True)
class ComponentVocabularySplit:
    split: SplitIds
    held_out_components: tuple[str, ...]
    realized_test_fraction: float

def component_vocabulary(build: Build) -> tuple[str, ...]: ...
def held_out_build_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def held_out_opponent_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def held_out_opponent_hull_split(
    rows,
    opponent_hull_by_variant: Mapping[str, str],
    holdout_fraction: float,
    seed: int,
) -> SplitIds: ...
def held_out_opponent_family_split(
    rows,
    opponent_family_by_variant: Mapping[str, str],
    holdout_fraction: float,
    seed: int,
) -> SplitIds: ...
def held_out_replicate_split(
    rows: Sequence[HonestEvalMatchupRow],
    holdout_fraction: float,
    seed: int,
) -> SplitIds: ...
def held_out_component_vocabulary_split(
    rows,
    build_lookup: Mapping[str, Build],
    holdout_fraction: float,
    max_overshoot_fraction: float,
    seed: int,
) -> ComponentVocabularySplit: ...
def held_out_seed_cell_split(rows, holdout_fraction: float, seed: int) -> SplitIds: ...
def forward_time_split(rows, train_fraction: float) -> SplitIds: ...
def grouped_kfold(rows, groups, n_folds: int, seed: int) -> tuple[SplitIds, ...]: ...
```

`holdout_fraction` and `train_fraction` must be in `(0, 1)`, otherwise raise
`ValueError`. The group named by each split must not appear in both train and
test.

### Seed policy

`BURNED_SPLIT_SEEDS` is the single owner of outer-split seed retirement.
Seed 17 drove four adaptive evidence waves (05-11 through 05-16; methodology
review C4) and is burned: the baseline script, the learned experiment
script, and batch-config validation must all reject burned outer-split seeds
with an error naming C4. Script seed defaults are the first canonical bank
seed (101), not 17. The canonical rotated seed bank and the reserved
confirmatory seed live beside `BURNED_SPLIT_SEEDS` in
`phase7_matchup_data.py` (`CANONICAL_SPLIT_SEED_BANK = (101, 103, 107, 109,
113, 127, 131, 137, 139, 149)`, `RESERVED_CONFIRMATORY_SEED = 151`); batch
validation references them. `phase7_matchup_data.py` is likewise the single
owner of the shared experiment-contract constants —
`EXPERIMENT_SCHEMA_VERSION` (2), `DEFAULT_PROMOTION_METRIC`,
`DEFAULT_FINAL_REFIT_POLICY`, `DEFAULT_DEPENDENCY_EXTRA`,
`DEFAULT_INNER_CV_FOLDS`, `DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT`,
`SEEDLESS_SPLITS`, and `INSUFFICIENCY_STATUSES`
(`degenerate_component_vocab_split`, `empty_outer_split`,
`insufficient_inner_groups`) — so the experiment script and the batch
validator cannot drift. The reserved seed must not
appear in any batch seed list; it is spent only on a promotion-grade
confirmatory claim with a predeclared model family and endpoint. It was
spent once, on 2026-07-12, ratifying CatBoost over tuned random forest on
the build split
([evidence](../reports/2026-07-12-phase7-seed151-confirmatory.md)); it
stays excluded from batch seed lists, and any future confirmatory claim
needs a fresh reserved seed appended here first. The learned script's
`--model` default is `catboost_regressor` per that ratification; the claim
boundary is build-like splits only.

The supported split levels and their claim boundaries are:

| Split level | Group key | Supported claim |
|---|---|---|
| `replicate` | exact `(build_key, opponent_variant_id)` with held-out replicate indices | Simulator-noise estimation only; not transfer evidence. |
| `build` | `build_key` | Transfer to unseen repaired player builds drawn from the same broader build distribution. |
| `opponent` | `opponent_variant_id` | Transfer to unseen exact opponent variants/builds. |
| `opponent-hull` | opponent `hull_id` derived from the stock variant | Transfer to unseen opponent hulls using outcome-free stock variant descriptors. |
| `opponent-family` | opponent hull size, designation, and tech/manufacturer derived from parsed game data | Transfer to unseen coarse opponent families/archetypes. Learned or target-derived clusters require a separate train-fold-fitted implementation before reportable use. |
| `component-vocab` | component-vocabulary membership (slot-agnostic weapon/hullmod IDs) | Transfer to builds containing weapon/hullmod IDs never seen in training. |
| `seed-cell` | `(campaign, seed)` or campaign-cell key when seed is absent | Transfer across campaign cells/proposal contexts. |
| `forward-time` | source order key, normally `(source_path, trial_number, opponent_index)` | Forward deployment over later optimizer proposals. Its deterministic partition predates the seed bank and absorbed the burned waves; artifacts stamp `reused_partition: true` and reports must caveat it. |

Each result must record `split_level`, the exact group-key function or field
set, the supported claim, and overlap counts for stricter hierarchy levels
when available. Random-row splits may be implemented only as smoke/debug
diagnostics and must set `claim_supported` to `debug_only`.

### Component keys

Two component-key definitions exist, with different jobs:

- `canonical_full_component_fingerprint` — `component_fingerprint_json`:
  hull ID, slot-qualified weapon assignments, sorted hullmods, and flux
  allocation. Used only for overlap diagnostics (exact-fingerprint and
  k-combination counts at `k = 1, 2, 3`). The former component *split* built
  on this key was retired (methodology review C2): the fingerprint is the
  canonical build JSON verbatim, so that split was the build split under a
  different shuffle.
- `slot_agnostic_weapon_and_hullmod_vocabulary` — `component_vocabulary`:
  tokens `weapon:<id>` and `hullmod:<id>`, no slot qualification, no hull or
  flux tokens (single-hull DB; flux is numeric). This is the
  `component-vocab` split's group definition and the question BO acquisition
  needs answered: transfer to component IDs never seen in training.

`held_out_component_vocabulary_split` builds its candidate vocabulary from
the union over builds that appear in the given rows (never from lookup-only
builds — held-out component lists are stamped, comparable artifact fields),
shuffles it with `random.Random(seed)`, and moves components into the
held-out set one at a time; after each addition the test-row set is
recomputed (rows whose build contains ≥ 1 held-out component); it stops when
test rows ≥ `holdout_fraction` of all rows. Invariant: no train build
contains any held-out component. Degenerate draws — vocabulary exhaustion,
an empty partition, or `realized_test_fraction > holdout_fraction +
max_overshoot_fraction` (unbounded overshoot makes rotated-seed panels
incomparable) — raise `ComponentVocabularyError` (a `ValueError` subclass);
config errors such as invalid fractions raise plain `ValueError`. Callers
running inside batch jobs catch exactly `ComponentVocabularyError` and emit
structured insufficiency artifacts (`degenerate_component_vocab_split`)
rather than crashing, so a deterministic bad draw cannot burn lease retries;
config errors propagate. Artifacts must name the component key definition
and record `held_out_components` and `realized_test_fraction`.

The designed overshoot cap is `DEFAULT_COMPONENT_VOCAB_MAX_OVERSHOOT =
0.35` (amended 2026-07-12 from 0.15). The component vocabulary is coarse —
each held-out item can swing the realized test fraction by a large,
quantized step — so the cap must admit the discrete fractions actually
achievable; at 0.15 most canonical bank seeds were structurally infeasible
(outer or inner draws overshoot) and the batch surfaced this as designed
via structured insufficiency artifacts. Feasibility evidence:
[2026-07-12-phase7-batch-v2-incidents.md](../reports/2026-07-12-phase7-batch-v2-incidents.md).
Consumers of component-vocab metrics must read `realized_test_fraction`
per cell rather than assuming the nominal `holdout_fraction`.

### Inner validation

Inner validation must use a split compatible with the outer claim, built
from outer-training rows only, with `inner_cv_folds` (default 3) inner
train/validation pairs:

- Grouped splits (`build`, `opponent`, `opponent-hull`, `opponent-family`,
  `seed-cell`): `grouped_kfold` on the outer split's group key, seeded with
  the HPO seed. `grouped_kfold` shuffles unique groups with
  `random.Random(seed)` and deals them round-robin into `n_folds` folds
  (fold i = validation, rest = train). It raises `ValueError` for
  `n_folds < 2` and returns `()` when unique groups < `n_folds`; the caller
  emits `insufficient_inner_groups` instead of falling back to row-random
  validation.
- `component-vocab` is not a row partition, so inner validation uses
  `inner_cv_folds` independent vocabulary-holdout draws within outer-train,
  draw `i` seeded `hpo_seed + i`, with the outer `holdout_fraction` and
  overshoot bound.
- `forward-time` uses rolling-origin semantics: `inner_cv_folds` ordered
  prefix/suffix origins within the outer-training prefix, declaring the
  ordering key.

Hyperparameter selection minimizes the **mean** inner-validation RMSE across
folds (tie-breakers on fold means). Every trial fit and the final refit use
the same model seed (the HPO seed): the selected configuration's inner
scores must have been produced under its shipping seed.

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
- the evaluation-metric suite below (`rank_metrics`, `skill_scores`,
  `panel_target_stats`, `noise_floor`)

Pooled row-level metrics are retained but demoted: 69.8% of target variance
is between-opponent (methodology review C1), so pooled Spearman measures
opponent-difficulty prediction, not build ranking. Rank metrics are the
primary evidence.

### Evaluation-metric suite

Owned by `src/starsector_optimizer/phase7_eval.py`, configured by the frozen
dataclass `EvalMetricsConfig` (all thresholds are config fields):

```python
@dataclass(frozen=True)
class EvalMetricsConfig:
    min_builds_per_opponent: int = 5
    min_opponents_per_build: int = 3
    top_fraction: float = 0.1
    min_top_fraction_rows: int = 3
    bootstrap_resamples: int = 500
    bootstrap_seed: int = 7717
    bootstrap_ci_level: float = 0.95
    noise_floor_override: float | None = None
    noise_floor_fallback: float = 0.05
    degenerate_denominator_epsilon: float = 1e-12
```

The module never imports from `scripts/`; caller-specific values (top-k
lists and the primary k) arrive as parameters. Every metric function
collapses replicate rows to (build, opponent) cell means first: replicated
panels (honest-eval: 30 replicates per cell) must not weight aggregates by
replicate multiplicity, satisfy panel-size gates via replicates, or feed
duplicate rows into rank statistics. All SD comparisons against the noise
floor use the sample SD (ddof=1, `sample_sd`) matching the floor's
derivation; fewer than two observations is degenerate by definition.
Correlation degeneracy uses the same `degenerate_denominator_epsilon`
threshold on input ranges.

**Degenerate-value rule (applies to every function):** any statistic whose
denominator (variance, MSE, range) falls below
`degenerate_denominator_epsilon`, or whose inputs are constant, is emitted
as `None` with a named exclusion/degeneracy counter — never `inf`/`NaN`.
All outputs must survive `json.dumps` unchanged. Rotated seeds will produce
degenerate panels (the seed-17 opponent-hull holdout had 4/5 variants with
zero within-opponent variance), so this is a load-bearing rule, not an edge
case.

**Noise floor.** `noise_floor_from_replicates(honest_eval_rows)` returns the
median within-`(build_key, opponent_variant_id)` target SD over groups with
≥ 2 replicates, with `n_groups` and source. Resolution order:
`noise_floor_override` → replicate-derived → `noise_floor_fallback`; the
resolved value and source are recorded in every artifact. Carve-out to the
honest-eval leakage rule: deriving the evaluation noise floor from
honest-eval replicate targets is permitted usage — the targets define
evaluation resolution (tie handling, opponent exclusion), are never fitted
on, and the derivation is stamped in the artifact.

**Per-opponent rank metrics** (`per_opponent_rank_metrics(builds,
opponents, y_true, y_pred, noise_floor, config)`): for each test opponent
with ≥ `min_builds_per_opponent` distinct builds AND within-opponent target
sample SD ≥ noise floor — Spearman ρ, Kendall τ-b, sparse Kendall τ (targets
quantized to noise-floor bins before τ-b), top-fraction Kendall τ (τ-b over
rows in the observed top `top_fraction` of that opponent's targets when
≥ `min_top_fraction_rows` such rows exist, else `None`). Opponents with a
constant prediction vector yield `None` correlations, counted separately
(comparators like `opponent_mean` predict constants within every opponent).
Output: per-opponent table, aggregate mean/median over non-`None` values,
and counters `included_opponents`, `excluded_low_variance`,
`excluded_small_n`, `null_prediction_degenerate`.

**Build-aggregate rank metrics** (`build_aggregate_rank_metrics`): the
degenerate-opponent set (within-opponent SD < noise floor) is computed once
per (split, seed) from the full test panel and held fixed. Per-build
aggregate = mean over the build's non-degenerate test opponents; builds with
< `min_opponents_per_build` contributing opponents are excluded and counted
(outer-test panels are TPE-log-unbalanced; honest-eval panels are balanced).
Output: Spearman, Kendall τ-b, `precision_at_k` (|top-k pred ∩ top-k true|
/ k), `regret_at_k` (best true aggregate − best true aggregate among top-k
predicted; raw always, normalized-by-range `None` when the range is
degenerate) per configured k; per-build panel sizes (min/median/max).
Top-k tie-break is deterministic: descending value, then ascending build
key.

**Skill scores** (`skill_scores`): `1 − MSE(pred)/MSE(train-mean predictor)`
plus both MSEs; `None` when the denominator is degenerate. Raw RMSE must
never be compared across panels without the accompanying
`panel_target_stats` (n, mean, SD, endpoint mass at ±1.0).

**Two-way cluster bootstrap** (`two_way_cluster_bootstrap`):
pigeonhole-style resampling that never feeds duplicated rows into a rank
statistic (duplication manufactures ties and biases ρ/τ downward). Each
resample draws a multiset of builds and a multiset of opponents with
replacement (seeded `bootstrap_seed` + resample index). Mean per-opponent
Spearman: each drawn opponent copy contributes its ρ over the rows
restricted to the distinct drawn-build set (multiplicity acts as a weight in
the outer mean only). Build-aggregate statistics: distinct drawn builds,
aggregates weighted by opponent-draw multiplicity, rank statistics over
distinct builds with the deterministic tie-break. CI = percentile interval
at `bootstrap_ci_level` over resamples with a finite statistic, reporting
the finite-resample count. Known property: rank statistics on distinct
clusters are mildly anti-conservative on the duplicated axis — bootstrap
CIs are **descriptive spread, not calibrated standard errors**, and reports
must present them as such.

Stratified diagnostics where labels are available:

- exact opponent variant (`opponent_variant_id`),
- opponent family using opponent hull size, designation, and manufacturer,
- score regime using fixed target bands (`loss`, `timeout_like`, `win`),
- campaign cell using `TrainingMatchupRow.campaign`.

The honest-eval diagnostic is required for this gate. The protocol is:

1. Fit the model on training-log matchups only.
2. Predict every resolved honest-eval matchup using build/opponent features.
3. Aggregate predicted and observed honest-eval targets by `build_key`,
   excluding degenerate opponents from the aggregates.
4. Report `honest_eval_build_metrics` (bootstrap at the caller-passed
   primary k — the learned script passes `primary_top_k`, the comparator
   gate passes its smallest configured k): build-mean rank correlations
   (Spearman/Kendall), `precision_at_k` with the chance level `k/n`
   alongside, `regret_at_k`, the overlap curve over all `k = 1..n`,
   build-level bootstrap CIs, and `outer_train_build_overlap` — the count of
   honest-eval builds whose `build_key` appears in outer-train, recording
   that this diagnostic is NOT clean holdout (honest-eval candidates were
   drawn from the same Wave-1 logs). `top_k_recall` is retained as a
   secondary continuity metric; top-1 recall over ~54 builds is a Bernoulli
   draw and must never be a promotion metric (methodology review H3).
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
EXPERIMENT_SCHEMA_VERSION: int  # 2

@dataclass(frozen=True)
class LearnedExperimentConfig:
    db_path: Path
    game_dir: Path
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
    feature_profile: str = "all"
    honest_eval_usage: str = "diagnostic_only"
    fresh_honest_eval_ledger_id: str | None = None
    primary_top_k: int = 1
    promotion_metric: str = "mean_per_opponent_spearman"
    promotion_threshold: float = 0.0
    claim_label: str = "exploratory"
    final_refit_policy: str = "fit_outer_train_only_no_deployment_artifact"
    candidate_universe: str = "source_db_builds"
    deployment_artifact: str = "none"
    inner_cv_folds: int = 3
    noise_floor_override: float | None = None
    bootstrap_resamples: int = 500
    component_vocab_max_overshoot: float = 0.35
    batch_job_id: str | None = None
    batch_name: str | None = None
    batch_fleet_name: str | None = None

def build_experiment_configs(
    config: LearnedExperimentConfig,
) -> list[LearnedExperimentConfig]: ...

def run_experiment(
    config: LearnedExperimentConfig,
    *,
    checkpoint_path: Path | None = None,
) -> dict[str, object]: ...
```

All tunable thresholds, budgets, seeds, fractions, feature-profile names, and search spaces live in
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
targets. Source path, trial number, row kind, source kind, rank, campaign,
seed, and batch/job identifiers are diagnostic provenance by default, not model
features. A later plan may allow campaign/seed context for a split-specific
question only if the plan names the supported claim and forbids incompatible
claims such as forward-time or seed-cell transfer. Under held-out-build and
held-out-opponent splits, unseen categories must be handled by the fitted
encoder or model without target fallback.

Each learned model/split run uses nested validation:

1. Build the outer split using one of the comparator-gate split names:
   `build`, `opponent`, `opponent-hull`, `opponent-family`,
   `component-vocab`, `seed-cell`, or `forward-time`. Outer split seeds in
   `BURNED_SPLIT_SEEDS` are rejected. A degenerate component-vocab draw
   (vocabulary exhaustion, empty partition, overshoot bound) is emitted as a
   `degenerate_component_vocab_split` insufficiency result, not a crash.
2. Build `inner_cv_folds` inner train/validation pairs from outer training
   rows only, per the §"Inner validation" rules (grouped k-fold; repeated
   vocabulary draws for component-vocab; rolling-origin for forward-time).
3. If the outer training rows cannot support that inner split, emit an
   `insufficient_inner_groups` result for the model/split instead of using a
   random row split.
4. Select hyperparameters by minimizing the mean inner-validation RMSE
   across folds. Tie breakers are higher mean Spearman rho and then lower
   fit/predict runtime. Every trial fit uses the same model seed as the
   final refit.
5. Refit the selected model on the full outer training rows.
6. Evaluate once on the outer test rows, emitting the full
   evaluation-metric suite (rank metrics with bootstrap CIs, skill scores,
   panel stats) alongside pooled metrics.

The canonical job matrix reports fixed model families across fixed splits
and rotated seeds (21 (split, model) cells; 183 jobs); it is a comparison
matrix, not an automatically nested model-family selector.
Any claim that names a single "best" learned model must either predeclare the
model family and primary endpoint before the run, or implement model-family
choice inside the inner validation procedure and evaluate that full selection
procedure on the outer fold. Reports must declare the primary split, primary
metric, primary top-k value when applicable, model-promotion rule, and whether
each table is exploratory or confirmatory.

Feature selection is part of that nested validation procedure. If a learned
run enables feature selection, the JSON artifact must record the selector
family, selected feature families, selected feature count, selected-family
stability when repeated resampling is used, heredity policy for interactions,
and every selector hyperparameter. Bayesian optimization, TPE, SMBO, random
search, successive halving, embedded regularization, and filter/wrapper
selectors are all allowed only inside the inner validation loop. Flat
per-column masks over the full feature table are diagnostic only unless the
run justifies the mask size, grouping, and leakage controls in the artifact.
If multi-fidelity screening or successive halving is used as a promotion gate,
the artifact must include the cheap-fidelity definition, full-fidelity
definition, promotion rule, and a rank-preservation diagnostic or mark the run
as exploratory-only.

Hierarchy-aware split metadata is required for feature-selection claims. Each
result must record the split unit, claim supported by that split, forbidden
cross-split keys, and overlap counts for exact opponent, hull ID, component
combination, component vocabulary (held-out component IDs appearing in any
train build — the component-vocab split's forbidden-key count, which must be
zero), campaign cell, and exact matchup group where those labels are
available. Random-row splits may be emitted as smoke/debug diagnostics, but
they are not evidence for held-out-opponent transfer.

Canonical learned runs must include named leakage diagnostics, or an explicit
`not_applicable` reason for each unavailable diagnostic: forbidden-key overlap,
adversarial-validation AUC by hierarchy level, rare-combination overlap,
nearest-neighbor overlap, and sparse-ID ablation delta. Diagnostics may fail
open only for exploratory artifacts; confirmatory artifacts must define pass,
warning, or fail semantics before the run.

Comparator context is computed **inline**: each learned run fits the six
comparator-gate models on its exact outer split (same rows, same seed) and
records per-comparator metrics (pooled + rank), `best_comparator` (minimum
finite RMSE), `delta_vs_best_comparator`, and `delta_vs_matched_family`.
Matched families are defined only where a natural analog exists:
`random_forest_tuned` → `random_forest`, `sparse_pairwise_ridge` →
`ridge_hybrid`, `catboost_regressor` → `null` (its headline is
`delta_vs_best_comparator`). There is no external comparator JSON: matching
a stale artifact from a different seed was the C3 comparability defect.
Known residual caveat, which reports must state: comparators run at fixed
defaults while learned families are tuned, so `delta_vs_best_comparator` is
a floor comparison, not a tuned-family comparison.

The learned script accepts `--output <path>` and writes the JSON payload there
instead of requiring shell redirection. Parent directories are created when
missing.

CatBoost is part of the optional `surrogate` dependency set. If the caller
selects `catboost_regressor` and CatBoost is unavailable, the script raises an
actionable dependency error unless `--allow-missing-optional-models` is set. If
that flag is set, the JSON output records the skipped model and reason.

The learned experiment JSON output includes:

- `experiment_schema_version` (2)
- `feature_schema_version`
- `feature_profile`
- source DB path and game directory
- code/version provenance, or `unknown`
- split seed and HPO seed
- train/holdout fractions
- model-family list and skipped-model list
- per-result target, feature-family summary, split family, HPO space, HPO
  trace summary (per-trial fold means for mae/rmse/Spearman plus the
  per-trial RMSE fold SD), selected
  hyperparameters, pooled metrics, `rank_metrics` (per-opponent +
  build-aggregate + bootstrap CIs), `skill_scores`, `panel_target_stats`,
  `noise_floor` (resolved value + source), `comparator_inline`, `inner_cv`
  (fold count, per-fold sizes), `outer_split_lineage`, honest-eval
  diagnostic, timing, feature profile, and leakage checklist
- feature-family registry digest, feature-selection protocol, selected-family
  summary, hierarchy scorecard, and model-specific regularization settings
  when those are applicable to the run
- `honest_eval_usage` with one of `diagnostic_only`, `exploratory_selection`,
  or `final_claim`, plus the honest-eval ledger identifier and run-lineage
  pointer when honest-eval diagnostics are emitted

`outer_split_lineage` is the C4 reuse ledger, parallel to
`honest_eval_usage`: `{split_seed, seed_bank_label,
confirmatory_reserved_seed, reused_partition}`. `seed_bank_label` is the
bank label for bank seeds, `reserved-confirmatory` for the reserved
confirmatory seed, and `ad-hoc` otherwise. `reused_partition` is `true`
for `forward-time` (its deterministic partition predates the seed bank);
seed history across waves is tracked by `seed_bank_label` plus the
burned-seed registry, not per-seed ledgers. The promotion
metric is `mean_per_opponent_spearman` on the outer test panel; the
honest-eval diagnostic's primary readout is build-aggregate Spearman with
CI. Any claim naming a single best model family must be predeclared and, for
promotion-grade confirmation, spend the reserved confirmatory seed.

The artifact contract uses these stable JSON object names at both the top level
and per-result where the object is result-specific:

- `claim_boundary`: `target_variable`, `honest_eval_diagnostic_target`,
  `primary_split`, `primary_top_k`, `promotion_metric`,
  `promotion_threshold`, `higher_is_better`, `claim_label`,
  `honest_eval_usage`, and `fresh_honest_eval_ledger_id`.
- `model_family_policy`: `policy_type`, `candidate_model_families`,
  `selected_model_family`, and `selection_scope`.
- `feature_selection_protocol`: `policy_type`, `feature_profile`,
  `feature_family_registry`, `feature_family_registry_sha256`,
  `selected_feature_families`, `selected_feature_count`, `selector_family`,
  `selector_hyperparameters`, `stability`, `heredity_policy`, and
  `selection_scope`. For the fixed-matrix baseline with no feature selector,
  `policy_type` is `fixed_profile_no_selector`.
- `feature_family_registry`: per-generated-feature entries with `family`,
  `template`, `parents`, and `leakage_risk`. The SHA-256 digest is computed over
  canonical sorted JSON and does not replace the registry itself.
- `hierarchy_scorecard`: `split_level`, `group_key_function`,
  `group_key_fields`, `claim_supported`, `forbidden_cross_split_keys`,
  `overlap_counts`, `component_key_definition`, and
  `component_overlap_diagnostics`.
- `leakage_diagnostics`: named entries for forbidden-key overlap,
  adversarial-validation AUC, rare-combination overlap,
  nearest-neighbor overlap, and sparse-ID ablation delta. Unavailable
  diagnostics are represented as `not_applicable` objects with reasons.
- `deployment_policy`: `final_refit_policy`, `candidate_universe`, and
  `deployment_artifact`.
- `rank_metrics`: `per_opponent` (table + aggregates + exclusion counters),
  `build_aggregate` (rank correlations, precision@k, regret@k, panel sizes),
  and `bootstrap` (percentile CIs + finite-resample counts) per the
  evaluation-metric suite.
- `skill_scores`, `panel_target_stats`, `noise_floor`: as defined in the
  evaluation-metric suite.
- `comparator_inline`: per-comparator pooled and rank metrics.
- `comparator_delta`: `best_comparator`, `delta_vs_best_comparator`,
  `matched_family`, `delta_vs_matched_family`.
- `inner_cv`: `fold_count`, per-fold train/validation sizes, and fold
  construction (`grouped_kfold` | `vocabulary_draws` | `rolling_origin`).
- `outer_split_lineage`: `split_seed`, `seed_bank_label`,
  `confirmatory_reserved_seed`, `reused_partition`.

The standalone learned script defaults `honest_eval_usage` to
`diagnostic_only`. Current-ledger batch configs that informed roadmap decisions
must stamp `exploratory_selection`. `final_claim` requires an explicit fresh
honest-eval ledger identifier and must be rejected without one.

Honest-eval top-k recall remains a post-fit diagnostic only. No learned
baseline may train, tune, choose features, choose model families, or calibrate
on honest-eval targets. If an experiment plan changes model families, feature
families, promotion thresholds, or optimizer-integration decisions after
inspecting an honest-eval diagnostic, subsequent claims on the same ledger are
exploratory unless a fresh honest-eval ledger is used for the final claim.

## Learned AWS Batch Artifacts

The learned AWS batch runner is a distributed execution path for the learned
baseline experiment. Spec 22 owns the cloud lifecycle, preflight, budget,
teardown, UserData security, and authenticated control-plane requirements.
This spec owns the Phase 7 job matrix and artifact semantics.

Launch preflight must dry-run split construction (outer split plus inner
folds, via the same `construct_splits` path the workers execute) for every
unique `(split, split_seed)` cell in the job matrix against the local source
DB, and refuse to provision when any cell is structurally infeasible —
insufficiency is a preflight failure, not a discovery to make with a running
fleet (added 2026-07-12 after the overshoot-cap incident). The preflight
must derive each probe config by parsing the rendered job command through
the experiment script's own parser and config builder — not by mirroring a
hand-written field list that can drift from what the workers run.

The canonical full-run batch job matrix is exactly:

- splits: `build`, `opponent`, `opponent-hull`, `opponent-family`,
  `component-vocab`, `seed-cell`, `forward-time`;
- model families: `random_forest_tuned`, `catboost_regressor`,
  `sparse_pairwise_ridge`;
- split seeds: `CANONICAL_SPLIT_SEED_BANK` (10 rotated seeds) for every
  split except `forward-time`, which is deterministic and runs one instance
  per model.

That produces 6 × 3 × 10 + 1 × 3 = 183 jobs. Seeded job IDs are
`{split}__{model}__s{seed}`; forward-time job IDs are `{split}__{model}`.
Each job runs `scripts/analysis/phase7_learned_surrogate_experiment.py` with
exactly one split, one model family, and one split seed (carried in the
lease payload), plus the configured source DB, game dir, HPO settings,
fractions, top-k values, `--feature-profile`,
`--inner-cv-folds`, noise-floor/bootstrap/overshoot options, claim-boundary
options, and an explicit per-job `--output` path. The generated command must
not include honest-eval training inputs or unsafe feature/model-selection
flags.

Batch configs define `split_seeds` explicitly. Config validation rejects
seed lists that are empty, intersect `BURNED_SPLIT_SEEDS`, or contain
`RESERVED_CONFIRMATORY_SEED`. When `publish_canonical: true`, `split_seeds`
must equal the canonical bank.

Batch configs may define explicit `splits`, `models`, and `split_seeds`
subsets for smoke or debug runs. Workers drain a lease queue, so
`target_workers` must satisfy `1 ≤ target_workers ≤ job_count`;
`min_workers_to_start` must equal `target_workers`, and `target_workers`
must be divisible by the region count (provisioning is split evenly across
regions; the implementation currently restricts configs to exactly one
region until replacement provisioning supports per-region allocation). Subset batches are diagnostic only. `publish_canonical` must be
false for any subset matrix, and the implementation must reject configs that
combine `publish_canonical: true` with anything other than the full
canonical matrix.
`max_job_attempts` controls the lease retry budget and must be positive. A job
lease is not a wall-clock runtime cap: workers must renew the lease while the
model process is still alive. A single stale AWS active-instance snapshot is
not sufficient to steal a lease. The controller may requeue only after the
lease has exceeded `lease_grace_seconds` without renewal; missing-worker
classification is diagnostic status, not an immediate ownership transfer. Spot
worker loss and renewal loss consume a lease attempt; configs intended for
Spot execution therefore need a retry budget larger than one transient
interruption cycle.

Provisioned instance IDs are counted as pending only for
`pending_instance_grace_seconds`. They must become visible in the provider's
active-instance listing before that grace expires; otherwise the batch fails
rather than suppressing recovery indefinitely behind never-active instance IDs.

The batch bundle must include every runtime script imported by the worker
command, including both `phase7_learned_surrogate_experiment.py` and its
baseline helper `phase7_baseline_surrogate.py`. Worker failures during the
experiment command must post a control-plane event with the command exit code
and a bounded stdout/stderr tail before the shell exits. The result upload
must retry transient failures (`result_upload_attempts` ×
`result_upload_retry_seconds`, designed defaults 5 × 10 s; config validation
requires the retry window to fit inside `lease_grace_seconds`) and post a
failure event with the final exit code before giving the job back — a
completed experiment must never be discarded by a single failed upload.

Per-job artifacts are normal learned-experiment JSON payloads with one
completed result. Each per-job artifact must include a top-level `batch_job`
object with `job_id`, `batch_name`, `fleet_name`, `split`, `model`, and
`split_seed`, and matching `batch_job_id`, `batch_name`,
`batch_fleet_name`, and `dependency_extra` in provenance (the experiment
script stamps its required dependency set; result validation rejects
artifacts without it).
Worker telemetry may additionally record attempt, instance ID, region,
instance type, bundle SHA256, and timestamps in event logs. A skipped
optional model is rejected at result acceptance for every control-plane
batch; smoke/debug output that tolerates missing optional dependencies runs
through `local-smoke`, which bypasses the control plane.

The batch merge step must validate every per-job artifact before publishing:

- all expected job IDs (the configured splits × models × seeds matrix) are
  present exactly once and every artifact's stamped `batch_job.job_id`
  matches the expected file/job identity;
- no job is failed, missing, duplicate, stale, or partial; structured
  insufficiency artifacts (statuses in `INSUFFICIENCY_STATUSES`) are
  ACCEPTED at result time — identity- and provenance-validated with the
  completed-result field checks skipped — so a deterministic bad draw
  terminates its job without burning lease retries, but any insufficiency
  artifact in the batch makes the merge refuse with an error naming the
  affected jobs (no `merged.json`, no publication);
- every artifact has the same `experiment_schema_version` (2),
  `feature_schema_version`, `feature_profile`, source DB path, game dir,
  HPO seed, train/holdout fractions, top-k values, dependency extra, source
  bundle SHA256, and code provenance — the split seed is per-job identity,
  validated against the job spec and required to be in the config's
  `split_seeds`;
- every result has a present and passing leakage checklist;
- every result's `split`, `model`, and `split_seed` match its job ID;
- `comparator_inline`, `rank_metrics`, `skill_scores`,
  `panel_target_stats`, `inner_cv`, and `outer_split_lineage` are present
  for every completed result.

Merged output carries a top-level `seed_aggregates` object keyed
`"split:model"`, one block per group: mean, SD, min/max, and `n_seeds` over the
headline metrics (pooled Spearman/RMSE, mean per-opponent Spearman,
build-aggregate Spearman, precision@k, regret@k, skill score); SD is `null`
when `n_seeds == 1` (forward-time). `seed_aggregate` is descriptive spread
over overlapping resplits of one dataset — not a calibrated standard error
(Nadeau–Bengio); reports must not present SD/√n as inference.

Valid merge always writes a batch-internal `merged.json`. It atomically
promotes the canonical full-run artifact to the config's
`canonical_output_path` — a dated, per-wave path (the schema-v2 wave uses
`data/phase7/learned_surrogate_full_v2_2026-07.json`) — only when
`publish_canonical: true` and the validated matrix is the full canonical
matrix. Publishing refuses to overwrite an existing canonical file whose
`batch_name` differs: prior waves' canonical artifacts (including the
schema-v1 `learned_surrogate_full_2026-05-12.json`) are dated evidence and
must remain on disk. Partial batches may write batch-internal artifacts
only; they must not write any canonical path.
