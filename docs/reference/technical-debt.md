# Technical Debt

Pre-existing gaps and inconsistencies found during Phase A (signal quality) implementation audits. None are bugs — all are minor spec staleness or fragility concerns.

## 1. Spec 24: `optimize_hull` signature omits `eval_log_path`

**Location:** `docs/specs/24-optimizer.md` line ~244, `optimizer.py` line ~715

The spec defines `optimize_hull(hull_id, game_data, instance_pool, opponent_pool, config) -> Study` with 5 parameters. The implementation adds a 6th: `eval_log_path: Path | None = None`. The spec should document this parameter.

## 2. Spec 24: `optimize_hull` step list omits `game_dir` pass to `warm_start`

**Location:** `docs/specs/24-optimizer.md` step 8, `optimizer.py` line ~746

The spec's `optimize_hull` algorithm shows `warm_start(study, hull, game_data, config)` without `game_dir`. The implementation passes `game_dir=instance_pool._config.game_dir`. Stock build seeding requires this — the spec step list is incomplete.

## 3. Spec 24: `optuna.logging.set_verbosity` not documented

**Location:** `optimizer.py` line ~729

`optimize_hull` calls `optuna.logging.set_verbosity(optuna.logging.WARNING)` to suppress verbose Optuna output. Not mentioned in the spec.

## 4. `BuildCache.hash_build` omits `hull_id`

**Location:** `optimizer.py` lines ~111-117

The hash includes weapon_assignments, hullmods, vents, and caps but not `hull_id`. Safe today because `BuildCache` is scoped to a single `StagedEvaluator` (single hull). Fragile if the cache were ever shared across hulls — two different-hull builds with identical loadouts would collide.

## 5. `instance_pool._config` private attribute access

**Location:** `optimizer.py` lines ~142, ~746

`preflight_check` and `optimize_hull` access `instance_pool._config.game_dir` directly. A public accessor on `InstancePool` would be more robust against internal refactoring.
