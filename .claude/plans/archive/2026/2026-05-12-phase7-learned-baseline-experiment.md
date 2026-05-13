---
plan_type: implementation
status: implemented
created: 2026-05-12
approved: 2026-05-12
implemented: 2026-05-12
owner: agent
related_docs:
  - AGENTS.md
  - docs/CONVENTIONS.md
  - docs/specs/31-phase7-matchup-data.md
  - docs/reference/phase7-learned-surrogate-research.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
  - docs/reference/phase7-search-space-compression.md
  - docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md
  - docs/reports/2026-05-11-validation-to-phase7-roadmap.md
  - .claude/plans/active/2026-05-12-phase7-aws-learned-batch.md
implementation_commit: 55a77e96f7840adcfcbe6980fb26328953476c80
post_impl_audit: passed
superseded_by: null
---

# Phase 7 Learned Baseline Experiment

## Goal

Implement the first research-gated learned-surrogate experiment for Phase 7
matchup prediction. The experiment must compare serious learned baselines
against the existing comparator-gate outputs without changing the comparator
gate itself, and it must report enough model-development context for a
statistical-learning review: target, features, splits, hyperparameter search,
selection objective, refit policy, leakage controls, runtime, and provenance.

## Context And Source Docs

- Root workflow and engineering rules: `AGENTS.md`.
- Documentation categories and empirical-claims rules: `docs/CONVENTIONS.md`.
- Data and comparator contracts: `docs/specs/31-phase7-matchup-data.md`.
- Research gate: `docs/reference/phase7-learned-surrogate-research.md`.
- Current comparator report:
  `docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md`.
- Current validation-to-roadmap bridge:
  `docs/reports/2026-05-11-validation-to-phase7-roadmap.md`.

## Scope

- Add a learned-baseline experiment path separate from
  `scripts/analysis/phase7_baseline_surrogate.py`.
- Preserve the existing comparator-gate script and model names as stable
  baselines.
- Update spec 31 with a learned-baseline experiment contract, not empirical
  results.
- Implement nested grouped validation for model and hyperparameter selection.
- Add progress and ETA reporting for expensive model-development runs.
- Compare learned outputs against the locked comparator-gate JSON artifact.
- Emit machine-readable JSON results with model-development provenance.
- Add or update a report for the learned-baseline experiment using the evidence
  date in the report filename.
- Add focused unit tests for split discipline, search provenance, schema shape,
  optional dependency handling, and leakage guards.
- Run a smoke experiment before any full experiment.

## Out Of Scope

- No optimizer integration.
- No BoTorch composed-kernel implementation.
- No neural, graph, sequence, or set model implementation.
- No use of honest-eval targets for training, tuning, feature selection, or
  model-family selection.
- No replacement of the existing comparator-gate report.
- No new internal-sim empirical numbers in reference docs or specs.
- No calibration or uncertainty claims. Calibration diagnostics and calibrated
  allocation are explicitly deferred until point/ranking signal is established
  under this protocol.

## Critical Files

- `docs/specs/31-phase7-matchup-data.md`
- `scripts/analysis/phase7_learned_surrogate_experiment.py`
- `tests/test_phase7_learned_surrogate_experiment.py`
- `docs/reports/2026-05-12-phase7-learned-surrogate-experiment.md`
- `docs/reports/INDEX.md`
- `.claude/plans/active/2026-05-12-phase7-learned-baseline-experiment.md`

## Public Concepts And Canonical Owners

- Spec 31 owns Phase 7 data contracts, feature-schema provenance, split
  contracts, leakage constraints, and the boundary between comparator-gate
  baselines and learned-baseline experiments.
- The learned-baseline script owns execution mechanics for nested grouped
  validation, HPO, model fitting, prediction, metrics, and JSON output.
- Reports own dated empirical results and interpretation.
- Reference docs own theory, source-grounded rationale, and design guidance
  only.
- This plan owns temporary sequencing and review status until archived.

## Experiment Contract

### Target Variable

The supervised target is per-matchup `training_matchups.target`, currently the
per-opponent `hp_differential` recovered from prior optimizer training logs.
Honest-eval targets are post-fit diagnostics only.

### Features

The input feature set is the existing Phase 7 matchup feature row:

- hull, hullmod, flux, weapon, slot, and mount geometry features;
- weapon numeric attributes and categorical weapon descriptors;
- opponent hull, variant, designation, manufacturer, and parsed variant
  features;
- matchup interaction features already emitted by
  `matchup_feature_row(...)`.

The learned model feature vectors must not include `build_key`, target-derived
build means, target-derived opponent means, TWFE residuals, honest-eval labels,
or any field computed from outer-test targets. Weapon IDs, hullmod IDs, slot
IDs, and opponent variant IDs are allowed as categorical descriptors because
they are design/game-data facts, not target-derived residuals. Under held-out
build and held-out opponent splits, unseen categorical values must be handled
by the fitted encoder/model without target fallback.

The output JSON and report must name the feature schema version and summarize
the actual feature columns used by each model family. Feature filtering is in
scope only when implemented as part of a fitted pipeline selected inside inner
training folds.

### Splits

Use the same canonical outer split names as the comparator gate:

- `build`;
- `opponent`;
- `component`;
- `seed-cell`;
- `forward-time`.

Each outer split has an inner validation split over the outer training rows
only. The inner split must match the outer stressor:

- build outer split uses held-out builds inside outer train;
- opponent outer split uses held-out opponents inside outer train;
- component outer split uses held-out component combinations inside outer
  train;
- `seed-cell` outer split uses held-out seed/campaign cells inside outer train;
- `forward-time` outer split uses an earlier/later split inside outer train.

If a dataset slice cannot support the matching inner split, the script must
return an explicit `insufficient_inner_groups` result for that model/split
instead of silently falling back to a random row split.

### Model Families

Implement these first learned-baseline families:

- `random_forest_tuned`: tuned scikit-learn random forest that upgrades the
  comparator-gate carryover baseline into a proper model-development baseline.
- `catboost_regressor`: primary categorical tree-ensemble baseline using the
  optional `surrogate` dependency set.
- `sparse_pairwise_ridge`: sparse interaction baseline using a
  `DictVectorizer`, `PolynomialCountSketch(degree=2)`, and ridge regression.

If CatBoost is unavailable, the script must fail with an actionable dependency
message unless the caller passes an explicit `--allow-missing-optional-models`
flag. Skipping the primary learned tree baseline is not the default.

### Library Usage Policy

The repo owns the experiment contract. Splits, leakage rules, provenance,
comparator context, JSON/report schema, plan gates, and honest-eval diagnostic
discipline are implemented locally and tested locally; library defaults must
not define those behaviors.

Use libraries by layer:

- `scikit-learn`: baseline experiment framework, `Pipeline`,
  `DictVectorizer`, metrics, random forests, ridge-style models, sparse
  interaction sketches, and simple feature-importance diagnostics.
- CatBoost: primary categorical boosted-tree baseline using native categorical
  handling through pandas columns and `cat_features`.
- Optuna: later nested-HPO upgrade only after the random-search baseline gives
  runtime and search-space evidence. Any Optuna study must be scoped inside the
  inner validation split and must not observe outer-test or honest-eval
  targets.
- LightGBM/XGBoost: later-plan candidates only. Add one only if CatBoost/RF
  results justify another boosted-tree implementation or runtime pressure makes
  it worthwhile.
- BoTorch/GPyTorch/Torch: reserved for structured optimizer integration,
  calibrated allocation, and custom kernels after learned-baseline validation.
- External experiment trackers: out of scope for this phase. Use JSON
  artifacts, reports, and the future local SQLite experiment registry first.

Parallel execution policy:

- Threaded HPO is acceptable for this phase because the expensive estimators
  are native-heavy (`scikit-learn`/OpenMP, CatBoost C++, NumPy/SciPy kernels)
  and generally release the Python GIL during fit/predict work.
- Avoid uncontrolled nested parallelism. Treat `hpo_jobs *
  model_thread_count` as the intended logical-core budget and record both
  fields in every artifact.
- Use `--hpo-jobs 4 --model-thread-count 4` for the full run on this
  16-logical-core workstation.
- If scaling is poor, unstable, or memory-bound, defer process-backed HPO via
  `loky` and cached/memmapped feature matrices to a later plan instead of
  increasing nested threads.
- Visibility is part of the experiment contract: progress must show per-HPO
  trial completion, best-so-far metrics, elapsed time, ETA, and partial
  checkpoint writes after each completed model/split.

### Model Pipelines And HPO Spaces

All HPO spaces are module-level named constants. Random search samples from
these spaces with `hpo_seed`; log-uniform parameters are sampled uniformly in
log space.

`random_forest_tuned`:

- Pipeline: `DictVectorizer(sparse=True)` followed by
  `RandomForestRegressor(random_state=hpo_seed, n_jobs=-1,
  criterion="squared_error")`.
- Default comparison: comparator-gate `random_forest` result from
  `--comparator-json`.
- Search space:
  - `n_estimators`: categorical `[200, 400, 800]`;
  - `max_depth`: categorical `[None, 16, 32, 64]`;
  - `min_samples_leaf`: categorical `[1, 2, 4, 8]`;
  - `max_features`: categorical `["sqrt", 0.35, 0.6, 1.0]`;
  - `bootstrap`: fixed `True`;
  - `max_samples`: categorical `[None, 0.65, 0.85]`.

`catboost_regressor`:

- Pipeline: pandas `DataFrame` from feature records with categorical columns
  left as strings and numeric columns coerced to floats. CatBoost receives the
  categorical column names via `cat_features`.
- Fixed parameters: `loss_function="RMSE"`, `eval_metric="RMSE"`,
  `allow_writing_files=False`, `verbose=False`, `random_seed=hpo_seed`.
- Default comparison: CatBoost default with `iterations=600`,
  `learning_rate=0.05`, `depth=6`, and `l2_leaf_reg=3.0`.
- Search space:
  - `iterations`: categorical `[300, 600, 1000]`;
  - `learning_rate`: log-uniform `[0.02, 0.2]`;
  - `depth`: categorical `[4, 6, 8, 10]`;
  - `l2_leaf_reg`: log-uniform `[1.0, 30.0]`;
  - `random_strength`: log-uniform `[0.1, 10.0]`;
  - `bagging_temperature`: categorical `[0.0, 0.5, 1.0, 2.0]`.

`sparse_pairwise_ridge`:

- Pipeline: `DictVectorizer(sparse=True)`, a sparse feature union containing
  the original vectorized features and `PolynomialCountSketch(degree=2)`, then
  `Ridge(random_state=hpo_seed)`.
- Default comparison: same pipeline with `n_components=1024` and `alpha=10.0`.
- Search space:
  - `n_components`: categorical `[512, 1024, 2048, 4096]`;
  - `alpha`: log-uniform `[0.001, 1000.0]`;
  - `degree`: fixed `2`;
  - `include_original_features`: fixed `True`.

### Hyperparameter Search

Use random or quasi-random search first. TPE or Bayesian tuning is deferred
until a later plan shows that the search space and runtime merit it.

For every model/split, HPO must record:

- search method and seed;
- full search space;
- trial budget;
- inner split family and group counts;
- primary selection objective;
- tie breakers;
- all attempted hyperparameters;
- selected hyperparameters;
- inner validation metrics;
- final refit runtime.

Primary selection objective: minimize inner-validation RMSE. Tie breakers:
maximize Spearman rho, then minimize fit/predict runtime.

The final model is refit on the full outer training rows using the selected
hyperparameters, then evaluated once on the outer test rows.

### Metrics And Diagnostics

Report at minimum:

- MAE;
- RMSE;
- Spearman rho;
- `n_train`, `n_inner_train`, `n_inner_validation`, and `n_test`;
- fallback or insufficient-group diagnostics;
- stratified diagnostics already used by the comparator gate where labels are
  available;
- honest-eval top-k recall as post-fit diagnostic only;
- runtime and progress/ETA fields.

### Planned Script API

The implementation must expose these importable pieces for tests and future
reuse:

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

def build_experiment_configs(config: LearnedExperimentConfig) -> list[LearnedExperimentConfig]: ...
def run_experiment(config: LearnedExperimentConfig) -> dict[str, object]: ...
```

All tunable thresholds, budgets, seeds, and fractions must be fields on
`LearnedExperimentConfig` or module-level named constants.

### Output Schema

The JSON output must include:

- `experiment_schema_version`;
- `feature_schema_version`;
- source DB path;
- game directory;
- code/version provenance; use `unknown` when no version identifier can be
  discovered;
- comparator context loaded from `--comparator-json`, including comparator
  artifact path, matching split/model rows, and deltas against the locked
  comparator-gate model for the same split;
- split seed and HPO seed;
- train/holdout fractions;
- model-family list and skipped-model list;
- per-result model card with target, feature families, split family,
  hyperparameters, HPO trace summary, metrics, honest-eval diagnostic, and
  timing.

## Comparator Context

The learned experiment CLI must accept `--comparator-json`. The default path is
`data/phase7/wave1_comparator_gate_2026-05-11.json`. Each learned result must
report:

- the comparator artifact path and schema fields observed;
- the best matching comparator result for the same split;
- the comparator `random_forest` result for `random_forest_tuned`;
- default-vs-tuned deltas for tuned learned families;
- a `comparator_missing` diagnostic instead of failing when the artifact lacks
  a matching row.

The report must show learned results beside comparator-gate results; prose
comparison alone is not enough.

## Execution Amendment: Local Full Artifact

The initial local full run was restarted and completed successfully as
`data/phase7/learned_surrogate_full_local_2026-05-12.json`. That local full
artifact satisfies this plan's full-run requirement for the current evidence
pass.

The AWS batch path is no longer required for this plan. It remains available
only as future infrastructure validation, and checked-in AWS configs are
disabled for execution.

## Implementation Sequence

1. Update spec 31 with the learned-baseline experiment boundary, nested grouped
   validation requirements, model-family names, HPO provenance, and honest-eval
   diagnostic constraint.
2. Add focused tests for:
   - CLI/config parsing and explicit model-family names;
   - inner split builders using only outer training rows;
   - insufficient inner groups producing explicit diagnostics;
   - HPO selection using inner-validation RMSE and tie breakers;
   - JSON schema/provenance shape;
   - CatBoost optional dependency behavior;
   - sparse pairwise pipeline construction;
   - no honest-eval target access during fitting or HPO.
3. Implement `scripts/analysis/phase7_learned_surrogate_experiment.py`.
4. Run a smoke experiment against
   `data/phase7/wave1_matchups.sqlite` with `--max-rows 200`,
   `--hpo-trials 2`, all implemented model-family code paths, and progress
   enabled. Write JSON with `--output
   data/phase7/learned_surrogate_smoke_2026-05-12.json`. Install the optional
   `surrogate` dependency set before the smoke run if CatBoost is missing.
5. Run the full learned-baseline experiment with all outer splits, all model
   families, `--hpo-trials 24`, `--hpo-jobs 4`,
   `--model-thread-count 4`, `--top-k 1,3,5`, progress enabled, and JSON
   output via `--output
   data/phase7/learned_surrogate_full_local_2026-05-12.json`. This local full
   run is complete and is the evidence source for this report. The AWS batch
   path is now infrastructure validation only.
6. Create or update the learned-surrogate experiment report with method
   context before result interpretation:
   - target variable;
   - feature list/families;
   - outer and inner splits;
   - hyperparameter spaces;
   - model selection objective;
   - comparator context;
   - smoke/full-run status;
   - limitations and leakage controls.
7. Update `docs/reports/INDEX.md`.
8. Do not update `docs/reference/phase7-learned-surrogate-research.md` except
   to add a link to the owning spec or owning report. It must not gain
   empirical results, HPO spaces, or contract language.
9. Run focused verification, the full Python suite, and post-implementation
   audit.
10. Archive this plan after implementation and audit.

## Tests And Mechanical Gates

- `uv run pytest tests/test_phase7_learned_surrogate_experiment.py -q`
- `uv run pytest tests/test_phase7_baseline_surrogate.py tests/test_phase7_learned_surrogate_experiment.py -q`
- `uv run pytest tests/ -v`
- `uv run python scripts/validate_active_plans.py`
- Smoke run:
  `uv run python scripts/analysis/phase7_learned_surrogate_experiment.py data/phase7/wave1_matchups.sqlite --split build --model all --max-rows 200 --hpo-trials 2 --top-k 1 --progress --comparator-json data/phase7/wave1_comparator_gate_2026-05-11.json --output data/phase7/learned_surrogate_smoke_2026-05-12.json`
- Full run artifact:
  `data/phase7/learned_surrogate_full_local_2026-05-12.json`
- AWS full-run execution:
  disabled for this plan. `examples/phase7-learned-batch.yaml` has
  `execution_enabled: false` and must not be launched without a new explicit
  reproducibility or infrastructure-validation goal.
- Focused Markdown link check over changed docs.
- `git diff --check`

The smoke artifact is required before committing the runner. A schema-valid
full artifact is required before marking this plan implemented and before
marking the report `shipped`; mere path existence is not enough. The local
full artifact satisfies the empirical execution gate for this plan. The stale
interrupted file previously at
`data/phase7/learned_surrogate_full_2026-05-12.json` was quarantined under
`data/phase7/interrupted/` because it had `status = running` and only partial
results. A validated AWS batch merge is a future infrastructure-validation
option only, not required for this plan closeout.

## Deferred Items

- TPE/Bayesian HPO: deferred because the research gate selected random or
  quasi-random HPO as the first auditable baseline and required optimizer-like
  tuning only after this experiment establishes runtime and search-space
  behavior.
- LightGBM/XGBoost: deferred until CatBoost/RF results show that another
  boosted-tree implementation would answer a concrete robustness or runtime
  question.
- External experiment tracking services: deferred because this phase needs
  local, reviewable artifacts and does not need hosted tracking semantics.
- Calibration and uncertainty claims: deferred because the roadmap requires
  calibration before allocation claims, while this experiment is scoped to point
  prediction and top-k diagnostics. No calibrated uncertainty, coverage, or
  allocation claim may appear in the report.
- Ranking objectives: deferred until regression predictions are compared
  against top-k diagnostics under the locked comparator context.

## Retirement Checklist

- Frontmatter `status` is changed to `implemented`.
- Frontmatter `implemented` is set to the completion date.
- Frontmatter `implementation_commit` is set to the final commit hash or
  `not_committed`.
- Frontmatter `post_impl_audit` is set to `passed` or linked to the audit
  record.
- The full experiment artifact validates as a complete 15-result full run from
  either the local full run or the AWS batch plan's validated canonical
  promotion, or the user has explicitly approved a follow-up plan for full
  empirical execution.
- The plan is moved to `.claude/plans/archive/2026/`.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-12
- Findings:
  - Comparator comparison was not operationalized.
  - Model pipelines and HPO spaces were underdefined.
  - Calibration policy was omitted.
  - Canonical split names drifted from spec/CLI names.
  - Report filename used the plan date instead of the evidence date.
  - Smoke command did not preserve a JSON artifact.
  - Full-suite verification was not explicit.
  - TPE/Bayesian HPO deferral was not recorded.
  - Smoke-only report boundary was ambiguous.
  - Reference-doc ownership and build-identity leakage were underspecified.
  - Retirement checklist was missing.
- Dispositions:
  - Added comparator JSON input, comparator output context, and comparison
    requirements.
  - Added exact model pipelines, default comparisons, HPO spaces, and
    sampling rules.
  - Added calibration and ranking deferrals with no-claims policy.
  - Normalized split names to `build`, `opponent`, `component`, `seed-cell`,
    and `forward-time`.
  - Moved the plan/report evidence date to 2026-05-12.
  - Added smoke and full JSON artifact paths.
  - Added `uv run pytest tests/ -v` and full-run gate before plan retirement.
  - Removed build identity residual features from allowed learned-model inputs
    and required spec coverage for dependency behavior.
  - Added reference update limits and a retirement checklist.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-12
- Agents:
  - Pattern Consistency: passed with findings
  - Spec Alignment: passed with findings
  - Engineering & Design Invariants: passed with findings
- Findings:
  - See Plan Review Gate.
- Dispositions:
  - See Plan Review Gate.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is
  `passed`.

## Post-Implementation Audit Requirements

- Verify implementation follows the approved plan or the plan is updated and
  re-reviewed before deviation.
- Verify spec 31 remains the contract owner and reports remain empirical-result
  owners.
- Verify honest-eval data is never used in fitting, HPO, or model-family
  selection.
- Verify optional dependency behavior is explicit and does not silently weaken
  the experiment.
- Verify smoke output contains enough ML/statistical-learning context to audit
  the run.
- Run fresh-eye audit sub-agents after implementation.
