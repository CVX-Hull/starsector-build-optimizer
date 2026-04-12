# Technical Debt

Pre-existing issues found by post-implementation audit agents. Address when touching the relevant code.

---

## Spec 18 (instance_manager) mismatches

### 1. Heartbeat freshness uses file mtime, not parsed content

`_is_heartbeat_fresh()` checks `st_mtime` of the heartbeat file. Spec 18 says "Parse 6-field heartbeat content (not just file mtime)." The 6-field parsing only happens in `_read_and_check_curtailment()` (curtailment path). When curtailment is None, heartbeat freshness is mtime-only.

### 2. Xvfb poll timeout reuses `process_kill_timeout_seconds`

`_start_xvfb()` uses `process_kill_timeout_seconds` (default 5.0) for the Xvfb socket readiness poll. Spec says "Timeout: 5 seconds (50 iterations x 0.1s)" as a dedicated Xvfb timeout. If `process_kill_timeout_seconds` is changed for process termination purposes, Xvfb poll timeout changes unintentionally.

### 3. `game_dir` property not in spec

`InstancePool.game_dir` property exists in implementation and is referenced by optimizer spec 24, but not listed in spec 18's `InstancePool` class signature.

### 4. `_game_log_file` field not in spec

`GameInstance._game_log_file: TextIO | None` is an implementation detail for stdout capture. Spec mentions the feature ("captures stdout/stderr to `{work_dir}/game_stdout.log`") but the field is not in the spec's field table.

---

## Spec 24 (optimizer) mismatches

### 5. `BuildCache.hash_build` includes `hull_id`

Spec says "SHA-256 of sorted weapon_assignments + sorted hullmods + vents + caps" (4 components). Implementation includes `hull_id` as first component (5 components). Arguably an improvement (prevents cross-hull collisions). Spec should be updated to match.

### 6. `_InFlightBuild` field ordering differs from spec

`heuristic_val` appears before `completed_results`/`raw_scores` in code (fields with defaults grouped together). Functionally equivalent since all construction uses keyword arguments.

### 7. `_compute_opponent_ordering` fallback returns wrong tuple

Returns `self._opponents` (original unshuffled tuple) instead of `self._ordered_opponents` (current shuffled/reordered tuple). Dead code path in practice (guard in `_finalize_build` prevents calling with insufficient data), but should return `self._ordered_opponents` for correctness.

---

## Code hygiene

### 8. Bare `MagicMock()` without `spec=` in tests

`tests/test_instance_manager.py` lines 618-619 use `MagicMock()` as placeholder values to verify list clearing. Should use `spec=CombatResult` / `spec=Heartbeat` to catch silent attribute access bugs.

### 9. Hardcoded eval log path

`scripts/run_optimizer.py` line 115 has `eval_log_path=Path("data/evaluation_log.jsonl")`. Should be a CLI argument or `OptimizerConfig` field.
