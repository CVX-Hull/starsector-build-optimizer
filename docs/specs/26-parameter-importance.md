# Parameter Importance Specification

Identifies which search space parameters most influence fitness, enabling dimensionality reduction via fixed parameters. Defined in `src/starsector_optimizer/importance.py`.

## Motivation

The optimizer's search space is 50-70 dimensions (13+ weapon slots, 62 hullmod flags, 2 flux params). Many parameters have negligible impact on fitness — e.g., small PD weapon slots or rarely-relevant hullmods. Identifying and fixing low-impact parameters reduces effective dimensionality, improving sample efficiency for all samplers (TPE, CatCMAwM).

## Classes

### `ImportanceResult`

Frozen dataclass in `models.py`.

| Field | Type | Description |
|-------|------|-------------|
| `importances` | `dict[str, float]` | Parameter name → importance score (0.0–1.0, sums to ~1.0) |

## Functions

### `analyze_importance(study, min_trials=20) -> ImportanceResult`

Wraps `optuna.importance.get_param_importances(study)` using Optuna's default fANOVA evaluator (random forest variance decomposition).

- Validates that the study has at least `min_trials` completed trials — raises `ValueError` if too few
- Returns `ImportanceResult` with per-parameter importance scores

### `print_importance_report(result, top_n=20) -> str`

Formats importance results as a readable table string showing the top-N most important parameters by importance score.

## Fixed Parameters

Fixed parameters are configured via `OptimizerConfig.fixed_params` (see spec 24). When fixed params are provided:

1. `define_distributions()` excludes them from the Optuna distribution dict — the sampler never suggests values for these parameters
2. `trial_params_to_build()` merges fixed values into the trial params before building — fixed values always override sampler-suggested values
3. The effective search dimensionality is reduced by the number of fixed parameters

### Workflow

1. Run an initial optimization (e.g., 200 trials with TPE)
2. `--analyze-importance` prints per-parameter importance from the study
3. Identify low-impact parameters (e.g., bottom 50% by importance)
4. Create a JSON file mapping those parameters to their best-known values
5. Re-run with `--fix-params fixed.json` to optimize the remaining subspace
