---
type: report
status: draft
last-validated: 2026-05-11
---

# Phase 7 Matchup Surrogate Preliminary

Preliminary sanity check for the Phase 7 featurized matchup-surrogate data
layer. This is not a tuned surrogate result and should not be cited as a final
model-performance claim.

## Data

Generated local SQLite DB:

```text
data/phase7/wave1_matchups.sqlite
```

Materialization command used:

```bash
uv run python scripts/analysis/phase7_materialize_matchups.py \
  --output data/phase7/wave1_matchups.sqlite \
  --game-dir game/starsector \
  --log-glob 'data/logs/wave1-*/hammerhead__early__tpe__seed*/evaluation_log.jsonl' \
  --honest-ledger data/honest_eval/starsector-honest-eval-wave1-c0a-20260510T170431Z/results.jsonl \
  --honest-candidate-log-glob 'data/logs/wave1-*/hammerhead__early__tpe__seed*/evaluation_log.jsonl' \
  --honest-hull-id hammerhead \
  --study-db ...15 Wave 1 Hammerhead study DB specs...
```

Materialized row counts:

| Table / source | Rows |
|---|---:|
| `recovered_builds` | 5,103 |
| `training_matchups` | 21,362 |
| `honest_eval_matchups` | 53,162 |
| honest-eval rows with unresolved `build_key` | 0 |
| exact logged builds | 2,374 |
| DB-reconstructed builds | 2,579 |
| honest-eval candidate builds | 150 |

The parser emitted the pre-existing corrupted-hullmod warning:

```text
Skipping corrupted hullmod id: 'all point-defense weapons deal %s more damage to all targets."'
```

That warning also appears in existing parser-driven workflows and was not
introduced by this materialization.

## Baseline

Command template:

```bash
uv run python scripts/analysis/phase7_baseline_surrogate.py \
  data/phase7/wave1_matchups.sqlite \
  --split <split> \
  --tree-count 80
```

Model: scikit-learn `RandomForestRegressor` over flat dict features with
`DictVectorizer`. This is a smoke baseline, not a tuned model.

| Split | Train rows | Test rows | MAE | RMSE |
|---|---:|---:|---:|---:|
| held-out build | 17,075 | 4,287 | 0.2612 | 0.4034 |
| held-out component combination | 17,078 | 4,284 | 0.2635 | 0.4072 |
| held-out seed/cell | 17,158 | 4,204 | 0.2630 | 0.4018 |
| forward-time | 17,090 | 4,272 | 0.2774 | 0.4228 |
| held-out opponent | 15,065 | 6,297 | 0.7035 | 0.9180 |
| honest-eval exact matchup repeat | 42,532 | 10,630 | 0.3038 | 0.5071 |

## Interpretation

The pipeline is coherent enough for the next experiment stage: the DB
materializes without unresolved honest-eval build IDs, and every grouped split
runs end to end.

The split pattern is more important than the absolute numbers. Held-out
opponent transfer is much harder than held-out build, component-combination,
seed/cell, or forward-time transfer. That supports the Phase 7 design choice to
model opponent context explicitly instead of treating builds as scalar global
strengths.

The held-out build, component-combination, and seed/cell metrics are close to
each other in this smoke baseline. That may mean the current flattened features
are mostly learning build-family and campaign-level structure; it does not yet
prove component-level counterfactual validity.

The honest-eval repeat split is not a transfer metric. It is a repeat/noise
sanity check over exact build/opponent groups, useful for calibration work but
not for claiming generalization to new opponents or builds.

## Next Checks

- Add a baseline that predicts the training-set mean and opponent mean so the
  RandomForest numbers have a trivial comparator.
- Add CatBoost and sparse interaction baselines.
- Report metrics by opponent family and score regime.
- Compare surrogate top-k recall against the honest-eval ranking without
  tuning on those same honest-eval rows.
- Profile feature extraction and cache opponent feature rows before larger
  experiment sweeps.
