---
type: spec
status: shipped
last-validated: unvalidated
---

# Instance Manager Specification

Manages N parallel Starsector game instances for batch combat evaluation on a single machine. Defined in `src/starsector_optimizer/instance_manager.py`. Implements the `EvaluatorPool` ABC from `src/starsector_optimizer/evaluator_pool.py`.

## `EvaluatorPool` (ABC, defined in `evaluator_pool.py`)

Cross-backend contract for matchup dispatch. Two concrete subclasses ship: `LocalInstancePool` (this module, `--worker-pool local`) and `CloudWorkerPool` (spec 22, `--worker-pool cloud`). `StagedEvaluator` (spec 24) depends only on this ABC — `isinstance(pool, LocalInstancePool)` or `isinstance(pool, CloudWorkerPool)` anywhere outside `scripts/run_optimizer.py` is a lint failure.

```python
class EvaluatorPool(abc.ABC):
    @abc.abstractmethod
    def setup(self) -> None: ...
    @abc.abstractmethod
    def teardown(self) -> None: ...
    @abc.abstractmethod
    def run_matchup(self, matchup: MatchupConfig) -> CombatResult: ...
    def __enter__(self) -> "EvaluatorPool":
        self.setup(); return self
    def __exit__(self, *args) -> None:
        self.teardown()
    @property
    @abc.abstractmethod
    def num_workers(self) -> int: ...
```

`run_matchup` takes no `worker_id` — the pool owns concurrency internally and serializes concurrent `run_matchup` calls up to `num_workers`.

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
| `xvfb_ready_timeout_seconds` | `float` | 5.0 | Timeout for Xvfb socket readiness poll (dedicated, not shared with process_kill_timeout) |
| `heartbeat_timeout_seconds` | `float` | 120.0 | Kill instance if no heartbeat for this long |
| `startup_timeout_seconds` | `float` | 90.0 | Kill instance if no heartbeat within this after launch |
| `poll_interval_seconds` | `float` | 1.0 | How often to check heartbeat/done files |
| `max_restarts` | `int` | 3 | Max restarts per instance before raising error |
| `process_kill_timeout_seconds` | `float` | 5.0 | Timeout for process termination and xdotool commands |
| `launcher_timeout_seconds` | `float` | 30.0 | Max wait for launcher window to appear |
| `launcher_poll_interval_seconds` | `float` | 0.5 | Poll interval for launcher window search |
| `launcher_click_settle_seconds` | `float` | 0.3 | Settle time between windowfocus, mouse click, and Return-fallback dispatches |
| `launcher_play_button_x_fraction` | `float` | 0.5 | Play button x position as fraction of launcher width (0.5 = center) |
| `launcher_play_button_y_fraction` | `float` | 0.7 | Play button y position as fraction of launcher height (0.7 = lower-third) |
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
| `_game_log_file` | `TextIO \| None` | File handle for stdout capture to `{work_dir}/game_stdout.log` (implementation detail) |

**Properties:**

- `saves_common` → `work_dir / "saves" / "common"`
- `variants_dir` → `work_dir / "data" / "variants"`
- `queue_path` → `saves_common / "combat_harness_queue.json.data"`
- `results_path` → `saves_common / "combat_harness_results.json.data"`
- `done_path` → `saves_common / "combat_harness_done.data"`
- `heartbeat_path` → `saves_common / "combat_harness_heartbeat.txt.data"`
- `shutdown_signal_path` → `saves_common / "combat_harness_shutdown.data"`

### `LocalInstancePool`

`EvaluatorPool` implementation for the local workstation. Manages N parallel game instances and provides blocking per-matchup execution. Pool owns concurrency — `StagedEvaluator` calls `run_matchup(matchup)` from up to `num_workers` threads concurrently and the pool routes each call to a free instance internally.

```python
class LocalInstancePool(EvaluatorPool):
    def __init__(self, config: InstanceConfig) -> None: ...
    def setup(self) -> None: ...
    def teardown(self) -> None: ...
    def run_matchup(self, matchup: MatchupConfig) -> CombatResult: ...   # no worker_id
    @property
    def num_workers(self) -> int: ...                                    # == config.num_instances
    @property
    def game_dir(self) -> Path: ...
    def __enter__(self) -> "LocalInstancePool": ...
    def __exit__(self, *args) -> None: ...
    # Internal methods
    def _is_instance_reusable(self, inst: GameInstance) -> bool: ...
    def _assign_next_batch(self, inst: GameInstance, chunk: list[MatchupConfig]) -> None: ...
    def _assign_and_launch(self, inst: GameInstance, chunk: list[MatchupConfig], *, reset_restart_count: bool = True) -> None: ...
    def _restart_or_raise(self, inst: GameInstance, matchup: MatchupConfig) -> None: ...
    def _write_shutdown_signal(self, inst: GameInstance) -> None: ...
```

Free-instance bookkeeping uses an internal `queue.Queue[GameInstance]` primed
with all instances after `setup()`. `run_matchup()` calls
`self._free_instances.get()` and returns the instance with
`self._free_instances.put(inst)` in a `finally:` block. The old
`StagedEvaluator.free_instances: deque[int]` pattern is retired — callers no
longer track worker IDs.

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

### `run_matchup(matchup)`

Run a single matchup on a pool-chosen instance. Blocks until complete. Thread-safe; concurrent calls from up to `num_workers` threads are serialized through the internal free-instance queue. Raises `InstanceError` if the chosen instance fails unrecoverably.

1. `inst = self._free_instances.get()` — blocks on an internal `queue.Queue` until an instance is free.
2. If `inst.total_matchups_processed >= clean_restart_matchups`, kill instance, reset counter.
3. If `_is_instance_reusable(inst)`: `_assign_next_batch(inst, [matchup])` (game still running, reuse via mission restart). Otherwise: `_assign_and_launch(inst, [matchup])` (full Xvfb + game launch).
4. Poll loop (every `poll_interval_seconds`):
   - **Done check:** If done file exists → parse results. Increment `total_matchups_processed`. If no results parse, mark the instance FAILED, kill game/Xvfb via `_restart_or_raise()`, and retry the same matchup on a fresh JVM while restart budget remains. If the first result's `matchup_id` differs from the requested `MatchupConfig.matchup_id`, treat the persistent JVM as protocol-desynchronized: mark the instance FAILED, restart it via `_restart_or_raise()`, and retry the same matchup immediately while restart budget remains. Only after a non-empty matching result is validated may the state become IDLE and `results[0]` be returned via the `finally:` that returns `inst` to `_free_instances`. If restart budget is exhausted, `_restart_or_raise()` raises `InstanceError`.
   - **Process check:** If game process exited without done signal → FAILED. Call `_restart_or_raise()`. Continue polling.
   - **Heartbeat check:** If STARTING and heartbeat fresh → RUNNING. If STARTING and startup timed out → FAILED, `_restart_or_raise()`. If RUNNING and heartbeat fresh → update timestamp. If RUNNING and heartbeat stale > timeout → FAILED, `_restart_or_raise()`.
5. Instance stays alive in IDLE after returning. Game is on title screen (or transitioning via Robot dismiss). TitleScreenPlugin will detect the next queue written by a subsequent `run_matchup()` call.

The result-ID guard is mandatory for persistent-session reuse. A stale
`combat_harness_done.data` / `combat_harness_results.json.data` pair can
otherwise be observed after `_assign_next_batch()` writes the next queue, causing
the same old result to be returned for new matchups. A mismatch is therefore an
instance-fatal condition, not a normal matchup failure; the instance is restarted
before the same matchup is retried.

### `num_workers` (property)

Returns `len(self._instances)`. Required by the `EvaluatorPool` ABC; `StagedEvaluator` uses it to size its `ThreadPoolExecutor`.

### `_restart_or_raise(inst, matchup)`

Kill instance, check restart count. If `restart_count < max_restarts`: increment count, `_assign_and_launch(inst, [matchup])`. Otherwise: raise `InstanceError`.
Restart relaunches pass `reset_restart_count=False` so the counter survives the
new process launch and the restart budget cannot reset itself.

### `_is_instance_reusable(inst)`

Returns `True` if both `game_process` and `xvfb_process` are alive (`poll() is None`). Used to detect instances from prior `run_matchup()` calls that can accept new work without a full restart.

### `_assign_next_batch(inst, chunk)`

Writes new queue to an already-running game instance. TitleScreenPlugin detects the queue on the title screen and auto-navigates to the mission.
1. Reset instance state: `assigned_matchups`, `results`, `restart_count`
2. Clean protocol files: remove stale done/results/queue/shutdown, but **touch** (not delete) heartbeat file — resets mtime to now, preventing false timeouts during the ~15-20s mission restart transition
3. Write new queue file
4. Set state to RUNNING, reset `last_heartbeat_time`

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

The game has a launcher (Java Swing, when `legacyLauncher=true` in `data/config/settings.json`) before the actual game loads. The instance manager polls for the Swing launcher window via `xdotool search --name Starsector`, captures its window ID and geometry via `xdotool getwindowgeometry --shell <wid>`, then dispatches: `xdotool windowmap <wid>` → `xdotool windowfocus <wid>` → `xdotool mousemove <abs_x> <abs_y>` → `xdotool click 1` → `xdotool key Return` (belt-and-suspenders). The click coordinate is computed from the parsed geometry as `(X + W * launcher_play_button_x_fraction, Y + H * launcher_play_button_y_fraction)`. After the game loads, TitleScreenPlugin + MenuNavigator (java.awt.Robot) handle in-game navigation.

**Three non-obvious traps under bare Xvfb (no window manager), each surfaced by `<work_dir>/launcher_dispatch.log` evidence on 2026-05-09:**

1. `xdotool windowactivate --sync` requires the EWMH `_NET_ACTIVE_WINDOW` atom which only a window manager sets. Under bare Xvfb it returns `Your windowmanager claims not to support _NET_ACTIVE_WINDOW`. `windowfocus` (XSetInputFocus) is the WM-free equivalent. (Smoke #10.)
2. `xdotool key --window <wid>` dispatches via XSendEvent, which sets `send_event=True` on the X event. Java AWT filters such events as a synthetic-event-injection hardening, so the key never reaches the focused widget. Dropping `--window` makes xdotool fall back to XTest, which produces real-looking keystrokes Java accepts. (Smoke #10.)
3. The Play button is **not** AWT default-focused. Even after `windowfocus + XTest Return`, the launcher didn't advance — Java's FocusManager hadn't placed focus on the Play JButton, so KeyEvents hit the JFrame and went nowhere. The fix is a coordinate-based mouse click (computed from the launcher's dynamic geometry) which sidesteps the focus question entirely. (Smoke #11.)

Coordinate-based clicks are robust to launcher position changes (the geometry is parsed every dispatch — no Xvfb-size assumption) but assume the Play button stays roughly at the center-horizontal, lower-third of the window. Future game version updates that move the Play button substantially may need the `launcher_play_button_*_fraction` defaults retuned, or a follow-up image-processing approach (template match / OCR) to discover the button location dynamically. `launcher_dispatch.log` and `getwindowgeometry` records make either retune cheap to drive from real evidence.

`_click_launcher` writes every xdotool call's exit code, stdout, and stderr to `<work_dir>/launcher_dispatch.log`. The worker heartbeat ships the contents back to the orchestrator via the existing `instance_*/*.log` glob — the only diagnostic window when SSH ingress is locked down.

**Launcher polling:**
```python
for _ in range(max_polls):
    result = subprocess.run(["xdotool", "search", "--name", "Starsector"], ...)
    first_line = result.stdout.strip().splitlines()[:1]
    if first_line:
        wid = first_line[0].strip()
        break
    time.sleep(launcher_poll_interval_seconds)
```

## Per-Instance Game Logging

Game stdout/stderr captured to `{work_dir}/game_stdout.log` instead of `/dev/null`. Essential for debugging crashes.

## Variant File Handling

Stock `.variant` files (for enemy ships) are symlinked into each instance's `data/variants/` directory at setup time. Optimizer-generated builds are embedded as `BuildSpec` objects in the matchup queue JSON — no `.variant` file I/O is needed for optimizer builds.

## Launch Script Portability

The enhanced `starsector.sh` has CPU-specific JVM flags (AVX3, Shenandoah GC). For cloud machines with different CPUs, a portable launch script with G1GC may be needed. The `InstanceConfig` can be extended with a `launch_script` field if needed.
