# Technical Debt

Pre-existing issues discovered during audit phases. Each entry includes the violation, location, and suggested fix.

## 1. Magic number: `-1.0` failure score in optimizer

**Location:** `src/starsector_optimizer/optimizer.py` — lines 385, 482

**Violation:** The sentinel value `-1.0` is used as a failure score when `InstanceError` occurs or `validate_build_spec` finds errors. This bare literal appears in `StagedEvaluator._ask_new_trial()` and the `InstanceError` handler in `StagedEvaluator.run()`.

**Fix:** Add `failure_score: float = -1.0` to `OptimizerConfig`. Reference `self._config.failure_score` in both locations.

## 2. Magic number: `2.0` stock build scale multiplier in warm_start

**Location:** `src/starsector_optimizer/optimizer.py` — line 270

**Violation:** `config.warm_start_scale * 2.0` — the `2.0` multiplier for stock build warm-start values is a tunable constant embedded in the `warm_start()` function body.

**Fix:** Add `stock_build_scale_mult: float = 2.0` to `OptimizerConfig`. Use `config.warm_start_scale * config.stock_build_scale_mult`.

## 3. Spec 24 `preflight_check` — `enabled_mods.json` content not verified

**Location:** `src/starsector_optimizer/optimizer.py` — lines 112-115

**Violation:** Spec 24 says preflight check should verify `enabled_mods.json` exists **and contains `combat_harness`**. Implementation only checks the file exists, not its contents.

**Fix:** Read the JSON file and verify `"combat_harness"` is in the `enabledMods` array.

## 4. Spec 24 `CatCMAwMSampler` class name casing mismatch

**Location:** `docs/specs/24-optimizer.md` line 165 vs `src/starsector_optimizer/optimizer.py` line 168

**Violation:** Spec says `CatCMAwMSampler`, implementation uses `CatCmawmSampler`. The implementation reflects the actual optunahub API.

**Fix:** Update spec to match actual API: `CatCmawmSampler`.
