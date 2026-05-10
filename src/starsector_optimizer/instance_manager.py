"""Instance manager — launches and manages N parallel Starsector game instances.

Creates per-instance work directories (symlinks to shared game files, real dirs
for saves/config/variants), starts Xvfb + game processes, monitors health via
heartbeat files, restarts crashed instances, and collects combat results.

See spec 18 for full design.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from .evaluator_pool import EvaluatorPool
from .models import CombatResult, MatchupConfig
from .result_parser import parse_results_file, write_queue_file

logger = logging.getLogger(__name__)

# Files the game writes to saves/common/ (with .data appended by game)
PROTOCOL_FILES = [
    "combat_harness_queue.json.data",
    "combat_harness_results.json.data",
    "combat_harness_done.data",
    "combat_harness_heartbeat.txt.data",
    "combat_harness_shutdown.data",
]

# Top-level game directory entries that should be symlinked (not copied)
_SYMLINK_DIRS = {"jre_linux", "native", "graphics", "sounds"}

# data/ subdirectories that must be real (game writes to them)
_REAL_DATA_SUBDIRS = {"config", "variants"}

# Each Starsector JVM consumes ~2.5 cores under active combat (empirical, measured
# 2026-04-18 on 12-core host: `ps -eo pcpu` showed 232–254% per java process for 4
# instances). Add ~0.5 cores of orchestrator + Xvfb overhead per instance → 3 cores
# per instance. Safe ceiling = cpu_count // 3.
_CORES_PER_INSTANCE = 3


class InstanceError(Exception):
    """Raised when an instance fails unrecoverably."""


class InstanceState(StrEnum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class InstanceConfig:
    game_dir: Path
    instance_root: Path = field(default_factory=lambda: Path("/tmp/starsector-instances"))
    num_instances: int = 4
    xvfb_base_display: int = 100
    xvfb_screen: str = "1920x1080x24"
    xvfb_poll_interval_seconds: float = 0.1
    xvfb_ready_timeout_seconds: float = 5.0
    heartbeat_timeout_seconds: float = 120.0
    startup_timeout_seconds: float = 90.0
    poll_interval_seconds: float = 1.0
    max_restarts: int = 3
    process_kill_timeout_seconds: float = 5.0
    launcher_timeout_seconds: float = 30.0
    launcher_poll_interval_seconds: float = 0.5
    launcher_click_settle_seconds: float = 0.3
    # Play-button position WITHIN the launcher window, expressed as fractions
    # of the window's width and height. Smoke #12 confirmed (0.5, 0.7) hits
    # the Play button on Starsector 0.98a-RC8's 597×373 Swing launcher.
    # Future game versions may shift the layout — see launcher_dispatch.log
    # in the worker heartbeat to verify the click landed on the button.
    launcher_play_button_x_fraction: float = 0.5
    launcher_play_button_y_fraction: float = 0.7
    clean_restart_matchups: int = 200  # force full game restart after N total matchups


@dataclass
class GameInstance:
    instance_id: int
    work_dir: Path
    display_num: int
    state: InstanceState = InstanceState.IDLE
    xvfb_process: subprocess.Popen | None = None
    game_process: subprocess.Popen | None = None
    assigned_matchups: list[MatchupConfig] = field(default_factory=list)
    results: list[CombatResult] = field(default_factory=list)
    last_heartbeat_time: float = 0.0
    launch_time: float = 0.0
    restart_count: int = 0
    total_matchups_processed: int = 0
    _game_log_file: TextIO | None = field(default=None, repr=False)

    @property
    def saves_common(self) -> Path:
        return self.work_dir / "saves" / "common"

    @property
    def variants_dir(self) -> Path:
        return self.work_dir / "data" / "variants"

    @property
    def queue_path(self) -> Path:
        return self.saves_common / "combat_harness_queue.json.data"

    @property
    def results_path(self) -> Path:
        return self.saves_common / "combat_harness_results.json.data"

    @property
    def done_path(self) -> Path:
        return self.saves_common / "combat_harness_done.data"

    @property
    def heartbeat_path(self) -> Path:
        return self.saves_common / "combat_harness_heartbeat.txt.data"

    @property
    def shutdown_signal_path(self) -> Path:
        return self.saves_common / "combat_harness_shutdown.data"


class LocalInstancePool(EvaluatorPool):
    """Manages N parallel local game instances for combat evaluation.

    Implements the EvaluatorPool ABC. Pool owns worker-selection; callers
    invoke run_matchup(matchup) concurrently from up to num_workers threads
    and the pool internally claims a free GameInstance for each call.

    Usage::

        config = InstanceConfig(game_dir=Path("game/starsector"), num_instances=4)
        with LocalInstancePool(config) as pool:
            result = pool.run_matchup(matchup)  # blocks until complete
    """

    def __init__(self, config: InstanceConfig) -> None:
        self._config = config
        self._instances: list[GameInstance] = []
        self._free_instances: queue.Queue[GameInstance] = queue.Queue()

    @property
    def game_dir(self) -> Path:
        """Public accessor for the game directory path."""
        return self._config.game_dir

    @property
    def num_workers(self) -> int:
        """Number of concurrent matchups this pool can dispatch."""
        return len(self._instances)

    def setup(self) -> None:
        """Create per-instance work directories with symlink structure.

        Preflight: refuses if num_instances > os.cpu_count() // 3. A Starsector
        JVM runs at ~2.5 cores under active combat; the per-3-core rule absorbs
        the JVM footprint plus Xvfb + Python orchestrator overhead.
        """
        cpu = os.cpu_count() or 1
        safe_max = cpu // _CORES_PER_INSTANCE
        if self._config.num_instances > safe_max:
            raise InstanceError(
                f"num_instances={self._config.num_instances} exceeds host capacity "
                f"(cpu_count={cpu}, max {safe_max}). Each Starsector JVM ≈2.5 cores "
                f"under active combat; reduce --num-instances or run on a larger host."
            )
        self._config.instance_root.mkdir(parents=True, exist_ok=True)
        self._instances = []
        self._free_instances = queue.Queue()
        for i in range(self._config.num_instances):
            work_dir = self._config.instance_root / f"instance_{i:03d}"
            display_num = self._config.xvfb_base_display + i
            inst = GameInstance(instance_id=i, work_dir=work_dir, display_num=display_num)
            self._create_work_dir(inst)
            self._instances.append(inst)
            self._free_instances.put(inst)

    def teardown(self) -> None:
        """Signal all instances to shut down, then kill processes."""
        for inst in self._instances:
            if inst.game_process and inst.game_process.poll() is None:
                self._write_shutdown_signal(inst)
        time.sleep(self._config.poll_interval_seconds)
        for inst in self._instances:
            self._kill_instance(inst)
            inst.state = InstanceState.STOPPED

    def run_matchup(self, matchup: MatchupConfig) -> CombatResult:
        """Run a single matchup on a pool-chosen instance. Blocks until complete.

        Thread-safe: up to num_workers concurrent calls are serialized through
        the internal free-instance queue. Raises InstanceError if the chosen
        instance fails unrecoverably.
        """
        inst = self._free_instances.get()
        try:
            return self._run_matchup_on(inst, matchup)
        finally:
            self._free_instances.put(inst)

    def _run_matchup_on(self, inst: GameInstance, matchup: MatchupConfig) -> CombatResult:
        # Clean restart if memory threshold exceeded
        if inst.total_matchups_processed >= self._config.clean_restart_matchups:
            self._kill_instance(inst)
            inst.total_matchups_processed = 0

        # Dispatch: reuse persistent session or full launch
        if self._is_instance_reusable(inst):
            self._assign_next_batch(inst, [matchup])
        else:
            self._assign_and_launch(inst, [matchup])

        # Poll for completion
        _ACTIVE_STATES = (InstanceState.PREPARING, InstanceState.STARTING,
                          InstanceState.RUNNING)
        while True:
            time.sleep(self._config.poll_interval_seconds)

            if inst.state not in _ACTIVE_STATES:
                break

            if self._is_done(inst):
                try:
                    results = parse_results_file(inst.results_path)
                except Exception as e:
                    logger.error("Instance %d: failed to parse results: %s",
                                 inst.instance_id, e)
                    results = []
                inst.total_matchups_processed += 1
                inst.state = InstanceState.IDLE
                if not results:
                    raise InstanceError(
                        f"Instance {inst.instance_id}: no results parsed")
                result = results[0]
                if result.matchup_id != matchup.matchup_id:
                    raise InstanceError(
                        f"Instance {inst.instance_id}: result matchup_id "
                        f"mismatch: expected {matchup.matchup_id}, "
                        f"got {result.matchup_id}"
                    )
                return result

            if self._is_process_exited(inst):
                logger.warning("Instance %d crashed", inst.instance_id)
                inst.state = InstanceState.FAILED
                self._restart_or_raise(inst, matchup)
                continue

            if inst.state == InstanceState.STARTING:
                if self._is_heartbeat_fresh(inst):
                    inst.state = InstanceState.RUNNING
                    inst.last_heartbeat_time = time.monotonic()
                elif self._is_startup_timed_out(inst):
                    logger.warning("Instance %d startup timed out",
                                   inst.instance_id)
                    inst.state = InstanceState.FAILED
                    self._restart_or_raise(inst, matchup)
                    continue
            elif inst.state == InstanceState.RUNNING:
                if self._is_heartbeat_fresh(inst):
                    inst.last_heartbeat_time = time.monotonic()
                elif (time.monotonic() - inst.last_heartbeat_time
                      > self._config.heartbeat_timeout_seconds):
                    logger.warning("Instance %d heartbeat timed out",
                                   inst.instance_id)
                    inst.state = InstanceState.FAILED
                    self._restart_or_raise(inst, matchup)
                    continue

        raise InstanceError(
            f"Instance {inst.instance_id} in unexpected state: {inst.state}")

    # --- Work directory creation ---

    def _create_work_dir(self, inst: GameInstance) -> None:
        """Create per-instance work directory with symlink structure."""
        wd = inst.work_dir
        if wd.exists():
            shutil.rmtree(wd)
        wd.mkdir(parents=True)

        game = self._config.game_dir.resolve()

        # Symlink top-level files (use absolute paths so symlinks work from any CWD)
        for item in game.iterdir():
            if item.is_file():
                (wd / item.name).symlink_to(item.resolve())

        # Symlink read-only directories
        for dirname in _SYMLINK_DIRS:
            src = game / dirname
            if src.exists():
                (wd / dirname).symlink_to(src.resolve())

        # data/ — real dir with symlinked subdirs except config/ and variants/
        data_dir = wd / "data"
        data_dir.mkdir()
        for item in (game / "data").iterdir():
            if item.name in _REAL_DATA_SUBDIRS:
                if item.name == "config":
                    shutil.copytree(item, data_dir / "config")
                elif item.name == "variants":
                    variants_dir = data_dir / "variants"
                    variants_dir.mkdir()
                    for vf in item.iterdir():
                        if vf.is_file():
                            (variants_dir / vf.name).symlink_to(vf.resolve())
                        elif vf.is_dir():
                            # Symlink variant subdirectories (e.g., dominator/, eagle/)
                            (variants_dir / vf.name).symlink_to(vf.resolve())
            else:
                (data_dir / item.name).symlink_to(item.resolve())

        # mods/ — copied
        mods_dir = wd / "mods"
        mods_dir.mkdir()
        mod_src = game / "mods" / "combat-harness"
        if mod_src.exists():
            shutil.copytree(mod_src, mods_dir / "combat-harness")
        enabled_src = game / "mods" / "enabled_mods.json"
        if enabled_src.exists():
            shutil.copy2(enabled_src, mods_dir / "enabled_mods.json")

        # saves/common/, screenshots/
        (wd / "saves" / "common").mkdir(parents=True)
        (wd / "screenshots").mkdir()

        # Null ALSA config — disables audio per-instance to avoid OpenAL
        # errors on headless displays without affecting the host system
        (wd / "asound_null.conf").write_text(
            "pcm.!default { type null }\nctl.!default { type null }\n"
        )

    # --- File management ---

    def _clean_protocol_files(self, inst: GameInstance) -> None:
        """Remove old queue/results/done/heartbeat files before a new batch."""
        for name in PROTOCOL_FILES:
            path = inst.saves_common / name
            path.unlink(missing_ok=True)

    def _write_queue(self, inst: GameInstance, matchups: list[MatchupConfig]) -> None:
        """Write matchup queue to instance's saves/common/."""
        write_queue_file(matchups, inst.queue_path)

    def _is_instance_reusable(self, inst: GameInstance) -> bool:
        """Check if instance has running game+Xvfb processes for persistent reuse."""
        return (inst.game_process is not None
                and inst.game_process.poll() is None
                and inst.xvfb_process is not None
                and inst.xvfb_process.poll() is None)

    def _assign_next_batch(self, inst: GameInstance, chunk: list[MatchupConfig]) -> None:
        """Send a new batch to an already-running persistent instance."""
        inst.assigned_matchups = chunk
        inst.results = []
        inst.restart_count = 0

        # Clean queue/results/done but touch (not delete) heartbeat so health
        # checks stay happy during the ~15-20s mission restart transition.
        # Touching resets mtime to now, giving a clean heartbeat_timeout window.
        # Deleting would make _is_heartbeat_fresh return False immediately.
        for name in PROTOCOL_FILES:
            path = inst.saves_common / name
            if "heartbeat" in name:
                path.touch(exist_ok=True)
            else:
                path.unlink(missing_ok=True)
        self._write_queue(inst, chunk)

        inst.state = InstanceState.RUNNING
        inst.last_heartbeat_time = time.monotonic()
        logger.info("Instance %d: queued %d matchup(s) for mission restart",
                    inst.instance_id, len(chunk))

    def _write_shutdown_signal(self, inst: GameInstance) -> None:
        """Write shutdown signal to request clean game exit."""
        inst.shutdown_signal_path.write_text(str(int(time.time() * 1000)))

    # --- Process management ---

    def _assign_and_launch(self, inst: GameInstance, chunk: list[MatchupConfig]) -> None:
        """Assign a chunk and launch the game instance."""
        inst.state = InstanceState.PREPARING
        inst.assigned_matchups = chunk
        inst.results = []
        inst.restart_count = 0

        self._clean_protocol_files(inst)
        self._write_queue(inst, chunk)
        self._start_xvfb(inst)
        self._start_game(inst)

        inst.state = InstanceState.STARTING
        inst.launch_time = time.monotonic()
        inst.last_heartbeat_time = time.monotonic()
        logger.info("Instance %d: launched with %d matchups", inst.instance_id, len(chunk))

    def _start_xvfb(self, inst: GameInstance) -> None:
        """Start Xvfb with instance's display number and wait until ready."""
        kill_timeout = self._config.process_kill_timeout_seconds
        # Kill any existing Xvfb for this instance
        if inst.xvfb_process and inst.xvfb_process.poll() is None:
            inst.xvfb_process.terminate()
            try:
                inst.xvfb_process.wait(timeout=kill_timeout)
            except subprocess.TimeoutExpired:
                inst.xvfb_process.kill()

        # Clean up stale lock and socket files
        lock_file = Path(f"/tmp/.X{inst.display_num}-lock")
        socket_file = Path(f"/tmp/.X11-unix/X{inst.display_num}")
        lock_file.unlink(missing_ok=True)
        socket_file.unlink(missing_ok=True)

        inst.xvfb_process = subprocess.Popen(
            ["Xvfb", f":{inst.display_num}", "-screen", "0",
             self._config.xvfb_screen, "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Wait for socket file (what clients actually connect to)
        poll_interval = self._config.xvfb_poll_interval_seconds
        ready_timeout = self._config.xvfb_ready_timeout_seconds
        max_polls = int(ready_timeout / poll_interval)
        for _ in range(max_polls):
            if socket_file.exists() and inst.xvfb_process.poll() is None:
                break
            time.sleep(poll_interval)
        else:
            logger.warning("Xvfb :%d may not be ready (socket not found)", inst.display_num)

        # Warm XRandR: LWJGL 2.x crashes on some Xvfb builds (Ubuntu 24.04)
        # if the first XRandR query hits an uninitialized mode list. Running
        # `xrandr --query` as a client forces the extension to populate state.
        try:
            subprocess.run(
                ["xrandr", "--query"],
                env={**os.environ, "DISPLAY": f":{inst.display_num}"},
                check=False, timeout=5,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("xrandr warmup skipped on :%d (%s)", inst.display_num, e)

    def _start_game(self, inst: GameInstance) -> None:
        """Launch game process with DISPLAY set to instance's Xvfb."""
        env = os.environ.copy()
        env["DISPLAY"] = f":{inst.display_num}"
        # Disable audio — null ALSA + null OpenAL, without affecting host
        env["ALSA_CONFIG_PATH"] = str(inst.work_dir / "asound_null.conf")
        env["ALSOFT_DRIVERS"] = "null"
        log_path = inst.work_dir / "game_stdout.log"
        inst._game_log_file = open(log_path, "w")
        inst.game_process = subprocess.Popen(
            ["./starsector.sh"],
            cwd=str(inst.work_dir),
            env=env,
            stdout=inst._game_log_file,
            stderr=subprocess.STDOUT,
        )
        self._click_launcher(inst)

    def _click_launcher(self, inst: GameInstance) -> None:
        """Coordinate-click the Play button to advance the launcher.

        Three X-server / Java AWT quirks have to be defeated under bare Xvfb;
        all are documented from launcher_dispatch.log evidence:

        1. **Xvfb runs without a window manager.** `xdotool windowactivate`
           requires the EWMH `_NET_ACTIVE_WINDOW` atom which only a WM sets,
           so it errors out (`Your windowmanager claims not to support
           _NET_ACTIVE_WINDOW`). `windowfocus` (XSetInputFocus) is the WM-
           free equivalent. (Smoke #10, 2026-05-09.)

        2. **Java AWT filters XSendEvent.** `xdotool key --window <wid>`
           dispatches via XSendEvent which sets `send_event=True`; Java AWT
           silently drops such events as synthetic-event-injection defense.
           `xdotool key Return` (no `--window`) falls back to the XTest
           extension which produces real-looking keystrokes Java accepts.
           (Smoke #10, 2026-05-09.)

        3. **The Play button isn't AWT default-focused.** Even after
           windowfocus + XTest Return, the launcher didn't advance — Java's
           FocusManager hadn't placed focus on the Play JButton, so KeyEvents
           hit the JFrame and went nowhere. `getwindowfocus` showed an
           AWT-internal proxy window (id 2097182) with X focus before our
           intervention, distinct from the JFrame (id 2097156).
           (Smoke #11, 2026-05-09.)

        The working dispatch is a coordinate-based mouse click computed from
        the launcher's geometry — sidesteps focus entirely. The Play button
        is centered horizontally and ~70% down the launcher's 597x373 frame.
        Geometry is parsed from `xdotool getwindowgeometry --shell <wid>`
        which outputs `X=…\\nY=…\\nWIDTH=…\\nHEIGHT=…` regardless of WM.

        Every xdotool invocation (including pre/post-state probes) is logged
        to `<work_dir>/launcher_dispatch.log` so the worker heartbeat glob
        ships the trace back to the orchestrator.
        """
        display = f":{inst.display_num}"
        env = {**os.environ, "DISPLAY": display}
        launcher_timeout = self._config.launcher_timeout_seconds
        poll_interval = self._config.launcher_poll_interval_seconds
        max_polls = int(launcher_timeout / poll_interval)
        kill_timeout = self._config.process_kill_timeout_seconds
        dispatch_log = inst.work_dir / "launcher_dispatch.log"

        # Fast-fail on missing xdotool. The polling loop's `except Exception:
        # pass` would otherwise swallow FileNotFoundError 60× before the
        # watchdog catches a launch_timeout — caller would see "launcher did
        # not appear" instead of the actual root cause. Linux-only worker by
        # design (LocalInstancePool spec); we just want the right error.
        if shutil.which("xdotool") is None:
            logger.error(
                "Instance %d: xdotool not found on PATH; cannot dispatch "
                "launcher click. Re-bake the AMI with the xdotool apt package.",
                inst.instance_id,
            )
            return

        def _trace(line: str) -> None:
            ts = time.strftime("%H:%M:%S", time.localtime())
            with open(dispatch_log, "a", encoding="utf-8") as fh:
                fh.write(f"{ts} {line}\n")

        def _xdotool(*args: str, label: str) -> subprocess.CompletedProcess:
            try:
                cp = subprocess.run(
                    ["xdotool", *args],
                    env=env, timeout=kill_timeout,
                    capture_output=True, text=True,
                )
            except Exception as exc:
                _trace(f"{label}: xdotool {' '.join(args)!r} raised: {exc!r}")
                raise
            stdout = cp.stdout.strip()
            stderr = cp.stderr.strip()
            _trace(
                f"{label}: xdotool {' '.join(args)} → exit={cp.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
            return cp

        _trace(
            f"_click_launcher start display={display} "
            f"timeout={launcher_timeout}s poll={poll_interval}s"
        )

        wid: str | None = None
        for poll_idx in range(max_polls):
            try:
                cp = _xdotool(
                    "search", "--name", "Starsector",
                    label=f"poll[{poll_idx}] search",
                )
                first_line = cp.stdout.strip().splitlines()[:1]
                if first_line:
                    wid = first_line[0].strip()
                    break
            except Exception:
                pass
            time.sleep(poll_interval)

        if wid is None:
            _trace(f"FAILED: launcher window did not appear within {launcher_timeout}s")
            logger.warning(
                "Instance %d: launcher window did not appear within %.1fs",
                inst.instance_id, launcher_timeout,
            )
            return

        # Probe geometry first — needed to compute the click coordinate.
        # `getwindowclass` is not a real xdotool subcommand (was a typo);
        # dropped. Window class info is recoverable via `xprop -id <wid>` if
        # ever needed and isn't load-bearing for the dispatch.
        for label, args in (
            ("name", ("getwindowname", wid)),
            ("geom", ("getwindowgeometry", "--shell", wid)),
            ("focus_before", ("getwindowfocus",)),
        ):
            try:
                _xdotool(*args, label=f"probe[{label}]")
            except Exception:
                continue

        # Parse the geometry probe so we can target the Play button by
        # absolute screen coordinate. Re-run `getwindowgeometry --shell`
        # cleanly so we don't rely on stale captured output.
        try:
            geom_cp = _xdotool(
                "getwindowgeometry", "--shell", wid, label="geom_for_click",
            )
        except Exception as e:
            _trace(f"FAILED: geom probe raised: {e!r}")
            return
        coords: dict[str, int] = {}
        for line in geom_cp.stdout.splitlines():
            line = line.strip()
            if "=" in line:
                k, _, v = line.partition("=")
                try:
                    coords[k.strip()] = int(v.strip())
                except ValueError:
                    continue
        if not all(k in coords for k in ("X", "Y", "WIDTH", "HEIGHT")):
            _trace(f"FAILED: incomplete geometry parse: {coords}")
            return
        x_frac = self._config.launcher_play_button_x_fraction
        y_frac = self._config.launcher_play_button_y_fraction
        click_x = coords["X"] + int(coords["WIDTH"] * x_frac)
        click_y = coords["Y"] + int(coords["HEIGHT"] * y_frac)
        _trace(
            f"computed click coord (W*{x_frac:.2f}, H*{y_frac:.2f}) → "
            f"abs=({click_x}, {click_y}) from geom={coords}"
        )

        time.sleep(self._config.launcher_click_settle_seconds)
        try:
            _xdotool("windowmap", wid, label="windowmap")
            _xdotool("windowfocus", wid, label="windowfocus")
            time.sleep(self._config.launcher_click_settle_seconds)
            _xdotool(
                "mousemove", str(click_x), str(click_y),
                label="mousemove_play",
            )
            _xdotool("click", "1", label="click_play")
            time.sleep(self._config.launcher_click_settle_seconds)
            # Belt-and-suspenders: if the click missed (geometry off, button
            # moved between versions), Return at least catches default-button
            # configurations. Cheap, never harmful.
            _xdotool("key", "Return", label="key_Return_fallback")
            try:
                _xdotool("getwindowfocus", label="focus_after_dispatch")
            except Exception:
                pass
            logger.info(
                "Instance %d: clicked launcher window %s at (%d, %d)",
                inst.instance_id, wid, click_x, click_y,
            )
        except Exception as e:
            _trace(f"FAILED: dispatch raised: {e!r}")
            logger.warning(
                "Instance %d: failed to dispatch launcher click: %s",
                inst.instance_id, e,
            )

    def _kill_instance(self, inst: GameInstance) -> None:
        """Kill game process and Xvfb for an instance."""
        timeout = self._config.process_kill_timeout_seconds
        if inst.game_process and inst.game_process.poll() is None:
            inst.game_process.terminate()
            try:
                inst.game_process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                inst.game_process.kill()
        if inst.xvfb_process and inst.xvfb_process.poll() is None:
            inst.xvfb_process.terminate()
            try:
                inst.xvfb_process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                inst.xvfb_process.kill()
        if inst._game_log_file and not inst._game_log_file.closed:
            inst._game_log_file.close()

    # --- Health checks ---

    def _is_heartbeat_fresh(self, inst: GameInstance) -> bool:
        """Return True if heartbeat file exists and was modified recently."""
        if not inst.heartbeat_path.exists():
            return False
        try:
            mtime = inst.heartbeat_path.stat().st_mtime
            return (time.time() - mtime) < self._config.heartbeat_timeout_seconds
        except OSError:
            return False

    def _is_startup_timed_out(self, inst: GameInstance) -> bool:
        """Return True if instance has been in STARTING state too long."""
        return (time.monotonic() - inst.launch_time) > self._config.startup_timeout_seconds

    def _is_done(self, inst: GameInstance) -> bool:
        """Return True if done signal file exists."""
        return inst.done_path.exists()

    def _is_process_exited(self, inst: GameInstance) -> bool:
        """Return True if game process has exited."""
        if inst.game_process is None:
            return True
        return inst.game_process.poll() is not None

    def _can_restart(self, inst: GameInstance) -> bool:
        """Return True if instance can be restarted."""
        return inst.restart_count < self._config.max_restarts

    # --- Failure handling ---

    def _restart_or_raise(
        self, inst: GameInstance, matchup: MatchupConfig,
    ) -> None:
        """Restart instance or raise InstanceError if max restarts exceeded."""
        self._kill_instance(inst)
        if not self._can_restart(inst):
            raise InstanceError(
                f"Instance {inst.instance_id} failed after "
                f"{inst.restart_count} restarts"
            )
        inst.restart_count += 1
        logger.info("Instance %d: restarting (attempt %d/%d)",
                    inst.instance_id, inst.restart_count,
                    self._config.max_restarts)
        self._assign_and_launch(inst, [matchup])
