# Technical Debt

Pre-existing issues found by post-implementation audit agents. Address when touching the relevant code.

---

## Spec 18 (instance_manager) mismatches

### 1. Xvfb poll timeout reuses `process_kill_timeout_seconds`

`_start_xvfb()` uses `process_kill_timeout_seconds` (default 5.0) for the Xvfb socket readiness poll. Spec says "Timeout: 5 seconds (50 iterations x 0.1s)" as a dedicated Xvfb timeout. If `process_kill_timeout_seconds` is changed for process termination purposes, Xvfb poll timeout changes unintentionally.

### 2. `game_dir` property not in spec

`InstancePool.game_dir` property exists in implementation and is referenced by optimizer spec 24, but not listed in spec 18's `InstancePool` class signature.

### 3. `_game_log_file` field not in spec

`GameInstance._game_log_file: TextIO | None` is an implementation detail for stdout capture. Spec mentions the feature ("captures stdout/stderr to `{work_dir}/game_stdout.log`") but the field is not in the spec's field table.

---

## Spec 24 (optimizer) mismatches

### 4. `BuildCache.hash_build` includes `hull_id`

Spec says "SHA-256 of sorted weapon_assignments + sorted hullmods + vents + caps" (4 components). Implementation includes `hull_id` as first component (5 components). Arguably an improvement (prevents cross-hull collisions). Spec should be updated to match.

### 5. `_InFlightBuild` field ordering differs from spec

`heuristic_val` appears before `completed_results`/`raw_scores` in code (fields with defaults grouped together). Functionally equivalent since all construction uses keyword arguments.

---

## Spec 09 (combat_protocol) mismatches

### 6. Stale `evaluate()` reference

Spec 09 line ~149 references `InstancePool.evaluate(matchups)` which was replaced by `run_matchup(instance_id, matchup)` in the async dispatch refactor. Spec should say `InstancePool.run_matchup()`.

---

## Config coupling (cross-cutting)

### 7. `engagement_threshold` duplicated across `OptimizerConfig` and `CombatFitnessConfig`

Both config dataclasses define `engagement_threshold: float = 500.0`. `StagedEvaluator.__init__` copies the value from `OptimizerConfig` to `CombatFitnessConfig`. Defaults match today but nothing enforces it. Consider embedding `CombatFitnessConfig` as a field of `OptimizerConfig` instead.

---

## Combat harness

### 10. `useDefaultAI` flag confusion in MissionDefinition

`MissionDefinitionAPI.initFleet(side, prefix, goal, useDefaultAI)`: `true` = AI-controlled fleet, `false` = player-controlled fleet. The name is misleading — "default AI" sounds like "use basic AI" but actually means "use the game's fleet commander AI." Must be `true` for both sides in automated combat. Comment in code documents this.

---

## Code hygiene

### 8. Bare `MagicMock()` without `spec=` in tests

`tests/test_instance_manager.py` line 522 uses `MagicMock()` as placeholder value to verify list clearing. Should use `spec=CombatResult` to catch silent attribute access bugs.

### 9. Hardcoded eval log path

`scripts/run_optimizer.py` line 112 has `eval_log_path=Path("data/evaluation_log.jsonl")`. Should be a CLI argument or `OptimizerConfig` field.
