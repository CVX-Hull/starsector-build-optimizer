"""Instance manager — launches and manages N parallel Starsector game instances.

Creates per-instance work directories (symlinks to shared game files, real dirs
for saves/config/variants), starts Xvfb + game processes, monitors health via
heartbeat files, restarts crashed instances, and collects combat results.

See spec 18 for full design.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from .models import CombatResult, Heartbeat, MatchupConfig
from .curtailment import CurtailmentMonitor, parse_heartbeat
from .result_parser import parse_results_file, write_queue_file

logger = logging.getLogger(__name__)

# Files the game writes to saves/common/ (with .data appended by game)
PROTOCOL_FILES = [
    "combat_harness_queue.json.data",
    "combat_harness_results.json.data",
    "combat_harness_done.data",
    "combat_harness_heartbeat.txt.data",
    "combat_harness_new_queue.data",
    "combat_harness_shutdown.data",
]

# Top-level game directory entries that should be symlinked (not copied)
_SYMLINK_DIRS = {"jre_linux", "native", "graphics", "sounds"}

# data/ subdirectories that must be real (game writes to them)
_REAL_DATA_SUBDIRS = {"config", "variants"}


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
    batch_size: int = 6  # matchups per instance; should equal opponent count
    heartbeat_timeout_seconds: float = 120.0
    startup_timeout_seconds: float = 90.0
    poll_interval_seconds: float = 1.0
    max_restarts: int = 3
    process_kill_timeout_seconds: float = 5.0
    launcher_timeout_seconds: float = 30.0
    launcher_poll_interval_seconds: float = 0.5
    launcher_click_settle_seconds: float = 0.3
    launcher_x: int = 297  # "Play Starsector" button, calibrated for 1920x1080
    launcher_y: int = 255
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
    heartbeats: list[Heartbeat] = field(default_factory=list)
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
    def new_queue_signal_path(self) -> Path:
        return self.saves_common / "combat_harness_new_queue.data"

    @property
    def shutdown_signal_path(self) -> Path:
        return self.saves_common / "combat_harness_shutdown.data"


class InstancePool:
    """Manages N parallel game instances for batch combat evaluation.

    Usage::

        config = InstanceConfig(game_dir=Path("game/starsector"), num_instances=4)
        with InstancePool(config) as pool:
            results = pool.evaluate(matchups)
    """

    def __init__(self, config: InstanceConfig, curtailment: CurtailmentMonitor | None = None) -> None:
        self._config = config
        self._curtailment = curtailment
        self._instances: list[GameInstance] = []

    @property
    def game_dir(self) -> Path:
        """Public accessor for the game directory path."""
        return self._config.game_dir

    def setup(self) -> None:
        """Create per-instance work directories with symlink structure."""
        self._config.instance_root.mkdir(parents=True, exist_ok=True)
        self._instances = []
        for i in range(self._config.num_instances):
            work_dir = self._config.instance_root / f"instance_{i:03d}"
            display_num = self._config.xvfb_base_display + i
            inst = GameInstance(instance_id=i, work_dir=work_dir, display_num=display_num)
            self._create_work_dir(inst)
            self._instances.append(inst)

    def teardown(self) -> None:
        """Signal all instances to shut down, then kill processes."""
        for inst in self._instances:
            if inst.game_process and inst.game_process.poll() is None:
                self._write_shutdown_signal(inst)
        time.sleep(self._config.poll_interval_seconds)
        for inst in self._instances:
            self._kill_instance(inst)
            inst.state = InstanceState.STOPPED

    def evaluate(self, matchups: list[MatchupConfig]) -> list[CombatResult]:
        """Submit matchups for evaluation, block until all complete, return results."""
        chunks = self._split_into_chunks(matchups)
        chunk_queue = list(chunks)  # remaining chunks to assign
        all_results: list[CombatResult] = []

        # Assign initial chunks to instances (reuse persistent sessions when possible)
        for inst in self._instances:
            if chunk_queue:
                if self._is_instance_reusable(inst):
                    self._assign_next_batch(inst, chunk_queue.pop(0))
                else:
                    self._assign_and_launch(inst, chunk_queue.pop(0))

        # Poll loop
        while any(inst.state in (InstanceState.STARTING, InstanceState.RUNNING,
                                  InstanceState.PREPARING)
                  for inst in self._instances):
            time.sleep(self._config.poll_interval_seconds)

            for inst in self._instances:
                if inst.state not in (InstanceState.STARTING, InstanceState.RUNNING):
                    continue

                # Check for done signal first
                if self._is_done(inst):
                    inst.state = InstanceState.DONE
                    try:
                        inst.results = parse_results_file(inst.results_path)
                    except Exception as e:
                        logger.error("Failed to parse results for instance %d: %s",
                                     inst.instance_id, e)
                        inst.results = []
                    all_results.extend(inst.results)
                    inst.total_matchups_processed += len(inst.assigned_matchups)
                    inst.state = InstanceState.IDLE

                    # Assign next chunk: reuse persistent session or full restart
                    if chunk_queue:
                        needs_restart = (
                            inst.total_matchups_processed >= self._config.clean_restart_matchups
                            or not self._is_instance_reusable(inst)
                        )
                        if needs_restart:
                            self._kill_instance(inst)
                            inst.total_matchups_processed = 0
                            self._assign_and_launch(inst, chunk_queue.pop(0))
                        else:
                            self._assign_next_batch(inst, chunk_queue.pop(0))
                    # else: leave instance alive in IDLE for reuse in next evaluate()
                    continue

                # Check for process exit without done signal (crash)
                if self._is_process_exited(inst):
                    logger.warning("Instance %d crashed (process exited without done signal)",
                                   inst.instance_id)
                    inst.state = InstanceState.FAILED
                    self._handle_failure(inst, chunk_queue, all_results)
                    continue

                # Check heartbeat
                if inst.state == InstanceState.STARTING:
                    if self._is_heartbeat_fresh(inst):
                        inst.state = InstanceState.RUNNING
                        inst.last_heartbeat_time = time.monotonic()
                    elif self._is_startup_timed_out(inst):
                        logger.warning("Instance %d startup timed out", inst.instance_id)
                        inst.state = InstanceState.FAILED
                        self._handle_failure(inst, chunk_queue, all_results)

                elif inst.state == InstanceState.RUNNING:
                    if self._is_heartbeat_fresh(inst):
                        inst.last_heartbeat_time = time.monotonic()
                        self._read_and_check_curtailment(inst)
                    elif time.monotonic() - inst.last_heartbeat_time > self._config.heartbeat_timeout_seconds:
                        logger.warning("Instance %d heartbeat timed out", inst.instance_id)
                        inst.state = InstanceState.FAILED
                        self._handle_failure(inst, chunk_queue, all_results)

        return sorted(all_results, key=lambda r: r.matchup_id)

    def __enter__(self) -> InstancePool:
        return self

    def __exit__(self, *args) -> None:
        self.teardown()

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
        inst.heartbeats = []
        inst.restart_count = 0

        self._clean_protocol_files(inst)
        self._write_queue(inst, chunk)
        inst.new_queue_signal_path.write_text(str(int(time.time() * 1000)))

        inst.state = InstanceState.RUNNING
        inst.last_heartbeat_time = time.monotonic()
        logger.info("Instance %d: sent batch (%d matchups) to persistent session",
                    inst.instance_id, len(chunk))

    def _write_shutdown_signal(self, inst: GameInstance) -> None:
        """Write shutdown signal to request clean game exit."""
        inst.shutdown_signal_path.write_text(str(int(time.time() * 1000)))

    # --- Matchup distribution ---

    def _split_into_chunks(self, matchups: list[MatchupConfig]) -> list[list[MatchupConfig]]:
        """Split matchups into chunks of batch_size."""
        bs = self._config.batch_size
        return [matchups[i:i + bs] for i in range(0, len(matchups), bs)]

    # --- Process management ---

    def _assign_and_launch(self, inst: GameInstance, chunk: list[MatchupConfig]) -> None:
        """Assign a chunk and launch the game instance."""
        inst.state = InstanceState.PREPARING
        inst.assigned_matchups = chunk
        inst.results = []
        inst.restart_count = 0
        inst.heartbeats = []

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
        timeout = self._config.process_kill_timeout_seconds
        # Kill any existing Xvfb for this instance
        if inst.xvfb_process and inst.xvfb_process.poll() is None:
            inst.xvfb_process.terminate()
            try:
                inst.xvfb_process.wait(timeout=timeout)
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
        max_polls = int(timeout / poll_interval)
        for _ in range(max_polls):
            if socket_file.exists() and inst.xvfb_process.poll() is None:
                break
            time.sleep(poll_interval)
        else:
            logger.warning("Xvfb :%d may not be ready (socket not found)", inst.display_num)

    def _start_game(self, inst: GameInstance) -> None:
        """Launch game process with DISPLAY set to instance's Xvfb."""
        env = os.environ.copy()
        env["DISPLAY"] = f":{inst.display_num}"
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
        """Click 'Play Starsector' on the launcher using xdotool search polling."""
        display = f":{inst.display_num}"
        env = {**os.environ, "DISPLAY": display}
        launcher_timeout = self._config.launcher_timeout_seconds
        poll_interval = self._config.launcher_poll_interval_seconds
        max_polls = int(launcher_timeout / poll_interval)

        # Poll for launcher window
        for _ in range(max_polls):
            try:
                result = subprocess.run(
                    ["xdotool", "search", "--name", "Starsector"],
                    env=env, timeout=self._config.process_kill_timeout_seconds,
                    capture_output=True, text=True,
                )
                if result.stdout.strip():
                    break
            except Exception:
                pass
            time.sleep(poll_interval)

        time.sleep(self._config.launcher_click_settle_seconds)
        try:
            lx, ly = self._config.launcher_x, self._config.launcher_y
            subprocess.run(
                ["xdotool", "mousemove", str(lx), str(ly)],
                env=env, timeout=self._config.process_kill_timeout_seconds,
                capture_output=True,
            )
            time.sleep(self._config.launcher_click_settle_seconds)
            subprocess.run(
                ["xdotool", "click", "1"],
                env=env, timeout=self._config.process_kill_timeout_seconds,
                capture_output=True,
            )
            logger.info("Instance %d: clicked launcher Play button", inst.instance_id)
        except Exception as e:
            logger.warning("Instance %d: failed to click launcher: %s", inst.instance_id, e)

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

    def _handle_failure(
        self,
        inst: GameInstance,
        chunk_queue: list[list[MatchupConfig]],
        all_results: list[CombatResult],
    ) -> None:
        """Handle a failed instance: restart or give up."""
        self._kill_instance(inst)
        if self._can_restart(inst):
            inst.restart_count += 1
            logger.info("Instance %d: restarting (attempt %d/%d)",
                        inst.instance_id, inst.restart_count, self._config.max_restarts)
            self._clean_protocol_files(inst)
            self._write_queue(inst, inst.assigned_matchups)
            self._start_xvfb(inst)
            self._start_game(inst)
            inst.total_matchups_processed = 0
            inst.state = InstanceState.STARTING
            inst.launch_time = time.monotonic()
            inst.last_heartbeat_time = time.monotonic()
        else:
            logger.error("Instance %d: max restarts exceeded, giving up on %d matchups",
                         inst.instance_id, len(inst.assigned_matchups))
            inst.state = InstanceState.IDLE
            raise InstanceError(
                f"Instance {inst.instance_id} failed after {inst.restart_count} restarts"
            )

    # --- Curtailment integration ---

    def _read_and_check_curtailment(self, inst: GameInstance) -> None:
        """Read heartbeat content and check if curtailment should trigger."""
        if self._curtailment is None:
            return
        try:
            content = inst.heartbeat_path.read_text().strip()
            if not content:
                return
            hb = parse_heartbeat(content)
            # Deduplicate: skip if same timestamp as last heartbeat
            if inst.heartbeats and inst.heartbeats[-1].timestamp_ms == hb.timestamp_ms:
                return
            inst.heartbeats.append(hb)
            should_stop, _ = self._curtailment.should_stop(inst.heartbeats)
            if should_stop:
                CurtailmentMonitor.write_stop_signal(inst.saves_common)
        except (ValueError, OSError):
            pass  # Malformed heartbeat or file not readable

