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
| `batch_size` | `int` | 50 | Matchups per game launch |
| `heartbeat_timeout_seconds` | `float` | 120.0 | Kill instance if no heartbeat for this long |
| `startup_timeout_seconds` | `float` | 90.0 | Kill instance if no heartbeat within this after launch |
| `poll_interval_seconds` | `float` | 1.0 | How often to check heartbeat/done files |
| `max_restarts` | `int` | 3 | Max restarts per instance before raising error |

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

**Properties:**

- `saves_common` → `work_dir / "saves" / "common"`
- `variants_dir` → `work_dir / "data" / "variants"`
- `queue_path` → `saves_common / "combat_harness_queue.json.data"`
- `results_path` → `saves_common / "combat_harness_results.json.data"`
- `done_path` → `saves_common / "combat_harness_done.data"`
- `heartbeat_path` → `saves_common / "combat_harness_heartbeat.txt.data"`

### `InstancePool`

Main class managing N parallel game instances.

```python
class InstancePool:
    def __init__(self, config: InstanceConfig) -> None: ...
    def setup(self) -> None: ...
    def teardown(self) -> None: ...
    def evaluate(self, matchups: list[MatchupConfig]) -> list[CombatResult]: ...
    def __enter__(self) -> InstancePool: ...
    def __exit__(self, *args) -> None: ...
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
2. Assign initial chunks to idle instances
3. Per assigned instance:
   - Clean old protocol files (queue, results, done, heartbeat)
   - Write optimizer `.variant` files to `data/variants/`
   - Write queue file via `write_queue_file()`
   - Start Xvfb: `Xvfb :{display_num} -screen 0 1920x1080x24 -nolisten tcp`
   - Start game: `./starsector.sh` with `DISPLAY=:{display_num}`, cwd=work_dir
   - Set state to STARTING, record launch_time
4. Poll loop (every `poll_interval_seconds`):
   - For each active instance:
     - **Process check:** If game process exited and done signal exists → DONE. If exited without done → FAILED.
     - **Heartbeat check:** If STARTING and `now - launch_time > startup_timeout` → FAILED. If RUNNING and heartbeat file mtime stale > `heartbeat_timeout` → FAILED. If heartbeat fresh and STARTING → RUNNING.
     - **Done check:** If done file exists → DONE.
   - For DONE instances: read results, assign next chunk if available
   - For FAILED instances: restart (up to `max_restarts`), re-queue same chunk
5. Return all results ordered by `matchup_id`

### `teardown()`

1. For each instance: terminate game process (SIGTERM, wait 5s, SIGKILL if needed), terminate Xvfb
2. Set state to STOPPED

## Xvfb Display

- Instance `i` gets display `xvfb_base_display + i`
- Resolution: `1920x1080x24` (must match MenuNavigator's hardcoded Robot coordinates)
- Flags: `-nolisten tcp` (no network access needed)

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
3. Append to `inst.heartbeats`
4. Call `self._curtailment.should_stop(inst.heartbeats)`
5. If `(True, winner)`: call `CurtailmentMonitor.write_stop_signal(inst.saves_common)`

Called in poll loop when `inst.state == RUNNING` and heartbeat is fresh.

## Variant File Placement

`InstancePool` provides a method to write optimizer-generated variant files to all instances:

```python
def write_variant_to_all(self, variant: dict, filename: str) -> None
```

Writes a variant JSON file to every instance's `data/variants/` directory. Required because `_assign_and_launch` only writes the queue JSON — variant files must already exist in the work directory. Stock variants are symlinked at setup time; optimizer-generated variants need explicit placement before `evaluate()`.

## Launch Script Portability

The enhanced `starsector.sh` has CPU-specific JVM flags (AVX3, Shenandoah GC). For cloud machines with different CPUs, a portable launch script with G1GC may be needed. The `InstanceConfig` can be extended with a `launch_script` field if needed.
