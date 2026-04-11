# Instance Manager Specification

Manages N parallel Starsector game instances for batch combat evaluation on a single machine. Defined in `src/starsector_optimizer/instance_manager.py`.

## Classes

### `InstanceConfig`

Frozen dataclass configuring the instance pool.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `game_dir` | `Path` | required | Path to shared Starsector installation |
| `instance_root` | `Path` | `/tmp/starsector-instances` | Root for per-instance work directories |
| `num_instances` | `int` | 4 | Number of parallel game instances |
| `xvfb_base_display` | `int` | 100 | First Xvfb display number (instances use base+0, base+1, ...) |
| `xvfb_screen` | `str` | `"1920x1080x24"` | Xvfb screen geometry (WxHxD) |
| `xvfb_poll_interval_seconds` | `float` | 0.1 | Poll interval for Xvfb socket readiness |
| `batch_size` | `int` | 6 | Matchups per instance per game launch |
| `heartbeat_timeout_seconds` | `float` | 120.0 | Kill instance if no heartbeat for this long |
| `startup_timeout_seconds` | `float` | 90.0 | Kill instance if no heartbeat within this after launch |
| `poll_interval_seconds` | `float` | 1.0 | How often to check heartbeat/done files |
| `max_restarts` | `int` | 3 | Max restarts per instance before raising error |
| `process_kill_timeout_seconds` | `float` | 5.0 | Timeout for process termination and xdotool commands |
| `launcher_timeout_seconds` | `float` | 30.0 | Max wait for launcher window to appear |
| `launcher_poll_interval_seconds` | `float` | 0.5 | Poll interval for launcher window search |
| `launcher_click_settle_seconds` | `float` | 0.3 | Settle time before/after clicking launcher |
| `launcher_x` | `int` | 297 | "Play Starsector" button X coordinate (calibrated for 1920x1080) |
| `launcher_y` | `int` | 255 | "Play Starsector" button Y coordinate (calibrated for 1920x1080) |
| `clean_restart_matchups` | `int` | 200 | Force full game restart after N total matchups per instance (prevents memory accumulation) |

### `InstanceState`

StrEnum for instance lifecycle:

- `IDLE` — no work assigned
- `PREPARING` — writing queue + variant files
- `STARTING` — Xvfb + game launched, waiting for first heartbeat
- `RUNNING` — heartbeat received, processing matchups
- `DONE` — done signal detected, results ready
- `FAILED` — crashed or timed out
- `STOPPED` — gracefully shut down

### `GameInstance`

Mutable dataclass tracking a single game instance.

| Field | Type | Description |
|-------|------|-------------|
| `instance_id` | `int` | 0-based index |
| `work_dir` | `Path` | Per-instance work directory |
| `display_num` | `int` | Xvfb display number |
| `state` | `InstanceState` | Current lifecycle state |
| `xvfb_process` | `Popen \| None` | Xvfb process handle |
| `game_process` | `Popen \| None` | Game process handle |
| `assigned_matchups` | `list[MatchupConfig]` | Current batch |
| `results` | `list[CombatResult]` | Results from current batch |
| `last_heartbeat_time` | `float` | `time.monotonic()` of last heartbeat detection |
| `launch_time` | `float` | `time.monotonic()` when game was launched |
| `restart_count` | `int` | Number of restarts for current batch |
| `total_matchups_processed` | `int` | Cumulative matchups across batches for this process (for clean restart threshold) |

**Properties:**

- `saves_common` → `work_dir / "saves" / "common"`
- `variants_dir` → `work_dir / "data" / "variants"`
- `queue_path` → `saves_common / "combat_harness_queue.json.data"`
- `results_path` → `saves_common / "combat_harness_results.json.data"`
- `done_path` → `saves_common / "combat_harness_done.data"`
- `heartbeat_path` → `saves_common / "combat_harness_heartbeat.txt.data"`
- `new_queue_signal_path` → `saves_common / "combat_harness_new_queue.data"`
- `shutdown_signal_path` → `saves_common / "combat_harness_shutdown.data"`

### `InstancePool`

Main class managing N parallel game instances.

```python
class InstancePool:
    def __init__(self, config: InstanceConfig, curtailment: CurtailmentMonitor | None = None) -> None: ...
    def setup(self) -> None: ...
    def teardown(self) -> None: ...
    def evaluate(self, matchups: list[MatchupConfig]) -> list[CombatResult]: ...
    def __enter__(self) -> InstancePool: ...
    def __exit__(self, *args) -> None: ...
    # Persistent session methods (internal)
    def _is_instance_reusable(self, inst: GameInstance) -> bool: ...
    def _assign_next_batch(self, inst: GameInstance, chunk: list[MatchupConfig]) -> None: ...
    def _write_shutdown_signal(self, inst: GameInstance) -> None: ...
```

## Per-Instance Work Directory

Each instance gets a work directory that is mostly symlinks to the shared game installation, with real directories for everything the game writes at runtime.

```
{instance_root}/instance_{id:03d}/
├── *.jar, starsector.sh, compiler_directives.txt  → symlinks
├── jre_linux/                  → symlink
├── native/                     → symlink
├── graphics/                   → symlink
├── sounds/                     → symlink
├── data/                       → REAL dir (shallow)
│   ├── hulls/, weapons/, hullmods/, ...           → symlinks to game data/*
│   ├── config/                 → REAL dir (COPIED — game writes settings.json)
│   └── variants/               → REAL dir (stock .variant files symlinked, optimizer variants as real files)
├── mods/                       → REAL dir
│   ├── combat-harness/         → COPIED (68KB)
│   └── enabled_mods.json       → COPIED
├── saves/                      → REAL dir
│   └── common/                 → REAL dir
└── screenshots/                → REAL dir
```

**Why these are real (not symlinked):**
- `saves/` — per-instance combat protocol files (queue, results, done, heartbeat)
- `data/config/` — game writes `settings.json` back at runtime (100KB). Contains `resolutionOverride` and `screenScaleOverride`.
- `data/variants/` — optimizer-generated variants are per-instance (different instances evaluate different builds)
- `mods/` — `enabled_mods.json` per instance. Mod dir copied to avoid shared-state issues.
- `screenshots/` — game may write here

## Lifecycle

### `setup()`

1. Create `instance_root` if needed
2. For each instance 0..N-1:
   - Create work directory with symlink structure
   - Symlink all top-level files (*.jar, starsector.sh, etc.)
   - Symlink directories: `jre_linux/`, `native/`, `graphics/`, `sounds/`
   - Create `data/` as real dir, symlink all subdirs except `config/` and `variants/`
   - Copy `data/config/` (game writes settings.json at runtime)
   - Create `data/variants/`, symlink all stock `.variant` files
   - Copy `mods/combat-harness/` and `mods/enabled_mods.json`
   - Create `saves/common/`, `screenshots/`

### `evaluate(matchups)`

1. Split matchups into chunks of `batch_size`
2. Assign initial chunks to instances:
   - If `_is_instance_reusable(inst)` (game+Xvfb processes still alive from previous evaluate): use `_assign_next_batch()` (persistent session reuse — no process creation)
   - Otherwise: use `_assign_and_launch()` (full Xvfb + game launch)
3. Poll loop (every `poll_interval_seconds`):
   - For each active instance:
     - **Done check:** If done file exists → DONE. Parse results. Track `total_matchups_processed += len(assigned_matchups)`. If more chunks:
       - If `total_matchups_processed >= clean_restart_matchups` or process exited: kill instance, reset counter, `_assign_and_launch()` (clean restart)
       - Otherwise: `_assign_next_batch()` (persistent session reuse)
     - **Process check:** If game process exited without done signal → FAILED.
     - **Heartbeat check:** If STARTING and `now - launch_time > startup_timeout` → FAILED. If RUNNING and heartbeat file mtime stale > `heartbeat_timeout` → FAILED. If heartbeat fresh and STARTING → RUNNING.
   - For FAILED instances: kill, restart (up to `max_restarts`), re-queue same chunk, reset `total_matchups_processed`
   - Instances with no remaining work stay alive in IDLE (game in WAITING state for potential reuse in next `evaluate()` call)
4. Return all results ordered by `matchup_id`

### `_is_instance_reusable(inst)`

Returns `True` if both `game_process` and `xvfb_process` are alive (`poll() is None`). Used to detect instances from prior `evaluate()` calls that can accept new work without a full restart.

### `_assign_next_batch(inst, chunk)`

Sends a new batch to an already-running persistent game instance:
1. Reset instance state: `assigned_matchups`, `results`, `heartbeats`, `restart_count`
2. Clean protocol files (removes stale done/heartbeat/results/signals)
3. Write new queue file
4. Write `combat_harness_new_queue.data` signal (triggers Java WAITING → INIT transition)
5. Set state to RUNNING, reset `last_heartbeat_time`

### `_write_shutdown_signal(inst)`

Write `combat_harness_shutdown.data` to request clean game exit. Used by `teardown()` and clean restart logic.

### `teardown()`

1. For each instance with a running game process: write shutdown signal (gives Java a chance to exit cleanly)
2. Wait `poll_interval_seconds` for clean exits
3. For each instance: terminate game process (SIGTERM, wait 5s, SIGKILL if needed), terminate Xvfb (SIGTERM, wait 5s, SIGKILL if needed)
4. Set state to STOPPED

## Xvfb Display

- Instance `i` gets display `xvfb_base_display + i`
- Resolution: `1920x1080x24` (must match MenuNavigator's hardcoded Robot coordinates)
- Flags: `-nolisten tcp` (no network access needed)

### Xvfb Lifecycle

**`_kill_instance(inst)`** must terminate both game and Xvfb with wait:
1. Terminate game process, wait 5s, SIGKILL if needed
2. Terminate Xvfb process, wait 5s, SIGKILL if needed (prevents stale Xvfb blocking restarts)

**`_start_xvfb(inst)`** must handle stale processes and verify readiness:
1. If `inst.xvfb_process` still running (`poll() is None`), terminate and wait first
2. Clean both lock file (`/tmp/.X{N}-lock`) AND socket file (`/tmp/.X11-unix/X{N}`)
3. Start Xvfb process
4. Wait for **socket file** to exist (not just lock file — the socket is what clients connect to)
5. Timeout: 5 seconds (50 iterations × 0.1s)

**`_start_game(inst)`** captures stdout/stderr to `{work_dir}/game_stdout.log` (not `/dev/null`). Essential for debugging crashes.

## Error Handling

| Error | Detection | Response |
|-------|-----------|----------|
| Game crash | Process exited + no done signal | Restart, re-queue same chunk |
| Game hang | Heartbeat stale > timeout | Kill, restart, re-queue |
| Xvfb failure | Xvfb process exited | Kill game, restart both |
| Max restarts exceeded | `restart_count >= max_restarts` | Raise `InstanceError` |
| Invalid results JSON | JSONDecodeError | Log, mark batch failed |

## Game Activation

Stored in `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (user-global). All instances on the same machine share activation automatically. No per-instance action needed.

## Launcher Click

The game has a launcher (Java Swing) before the actual game loads. The instance manager clicks "Play Starsector" using `xdotool` (which works on Swing windows, unlike LWJGL). After the game loads, TitleScreenPlugin + MenuNavigator (java.awt.Robot) handle in-game navigation.

**Launcher polling:** Instead of a fixed sleep, poll for the launcher window:
```python
for _ in range(30):  # up to 15 seconds
    result = subprocess.run(["xdotool", "search", "--name", "Starsector"], ...)
    if result.stdout.strip():
        break
    time.sleep(0.5)
```

## Per-Instance Game Logging

Game stdout/stderr captured to `{work_dir}/game_stdout.log` instead of `/dev/null`. Essential for debugging crashes.

## Enriched Heartbeat Parsing

Parse 6-field heartbeat content (not just file mtime) for curtailment integration:
```
<timestamp_ms> <elapsed_seconds> <player_hp> <enemy_hp> <player_alive> <enemy_alive>
```

Requires all 6 fields. Invalid formats raise an error.

## Curtailment Integration

`InstancePool` accepts an optional `CurtailmentMonitor`:

```python
InstancePool(config: InstanceConfig, curtailment: CurtailmentMonitor | None = None)
```

When `curtailment` is provided, the poll loop reads heartbeat file **content** (not just mtime) for RUNNING instances, accumulates `list[Heartbeat]` per instance, and calls `should_stop()` each cycle.

**`GameInstance` additions:**
- `heartbeats: list[Heartbeat]` — accumulated heartbeats for current batch, cleared on new batch assignment

**`_read_and_check_curtailment(inst)` method:**
1. Read `inst.heartbeat_path` content
2. Parse with `parse_heartbeat(line)` → `Heartbeat`
3. **Deduplicate:** if `inst.heartbeats` is non-empty and the parsed heartbeat's `timestamp_ms` equals the last accumulated heartbeat's `timestamp_ms`, skip (the game overwrites the file each cycle but the Python poll loop may read it multiple times before it changes)
4. Append to `inst.heartbeats`
5. Call `self._curtailment.should_stop(inst.heartbeats)`
6. If `(True, winner)`: call `CurtailmentMonitor.write_stop_signal(inst.saves_common)`

Called in poll loop when `inst.state == RUNNING` and heartbeat is fresh.

## Variant File Handling

Stock `.variant` files (for enemy ships) are symlinked into each instance's `data/variants/` directory at setup time. Optimizer-generated builds are embedded as `BuildSpec` objects in the matchup queue JSON — no `.variant` file I/O is needed for optimizer builds.

## Launch Script Portability

The enhanced `starsector.sh` has CPU-specific JVM flags (AVX3, Shenandoah GC). For cloud machines with different CPUs, a portable launch script with G1GC may be needed. The `InstanceConfig` can be extended with a `launch_script` field if needed.
