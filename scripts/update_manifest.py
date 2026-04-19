"""Regenerate the authoritative Starsector game manifest.

Writes `combat_harness_manifest_request.data` into a throwaway Starsector
work directory, launches the game headlessly via Xvfb, polls for the
manifest done sentinel, copies the emitted JSON to
`game/starsector/manifest.json`, and tears down.

Driven by the Java `ManifestDumper` class (combat-harness mod). The mod
runs the dump on title-screen stabilize when the sentinel is present;
see `combat-harness/src/main/java/starsector/combatharness/ManifestDumper.java`.

Prerequisites:
- `game/starsector/` is a working Starsector install.
- `combat-harness/` has been deployed (`./gradlew deploy`) so the updated
  mod is at `game/starsector/mods/combat-harness/`.
- `Xvfb`, `xrandr`, and the JVM shipped with the game are installed.

Usage:
    uv run python scripts/update_manifest.py [--game-dir PATH] [--timeout SECS]
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("update_manifest")

# Sentinel / output filenames inside `<work_dir>/saves/common/`.
# Keep in sync with ManifestDumper.MANIFEST_* constants.
#
# The manifest is split into 4 part files because the game's
# writeTextFileToCommon caps each write at 1 MiB (confirmed on 0.98a)
# and the compact manifest is ~1.3 MiB total. Python loader merges.
REQUEST_FILE = "combat_harness_manifest_request.data"
DONE_FILE = "combat_harness_manifest_done.data"
MANIFEST_PARTS = {
    "constants": "combat_harness_manifest_constants.json.data",
    "weapons":   "combat_harness_manifest_weapons.json.data",
    "hullmods":  "combat_harness_manifest_hullmods.json.data",
    "hulls":     "combat_harness_manifest_hulls.json.data",
}

# Defaults borrowed from instance_manager.InstanceConfig — mirror the
# constants used by LocalInstancePool so behavior is consistent.
DEFAULT_DISPLAY = 150  # out-of-range of LocalInstancePool's :100–:103
DEFAULT_SCREEN = "1920x1080x24"
DEFAULT_TIMEOUT_SECONDS = 180
POLL_INTERVAL_SECONDS = 1.0
PROCESS_KILL_TIMEOUT_SECONDS = 5.0


def _create_work_dir(game_dir: Path, work_dir: Path) -> None:
    """Materialize a throwaway Starsector work dir with symlinks.

    Mirrors the structure that `LocalInstancePool._create_work_dir` creates,
    minus the matchup queue plumbing. We need:
    - Symlinks to top-level game files + read-only dirs
    - Real `data/config/` (game writes to it)
    - Real `saves/common/` (game writes to it)
    - Copy of the deployed `mods/combat-harness/`
    """
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    game = game_dir.resolve()

    # Top-level files → symlinks
    for item in game.iterdir():
        if item.is_file():
            (work_dir / item.name).symlink_to(item.resolve())

    # Read-only game dirs → symlinks
    for dirname in ("jre_linux", "native", "graphics", "sounds"):
        src = game / dirname
        if src.exists():
            (work_dir / dirname).symlink_to(src.resolve())

    # data/ — real dir; config/ copied; variants/ symlinks per-entry;
    # everything else symlinked wholesale
    data_dir = work_dir / "data"
    data_dir.mkdir()
    for item in (game / "data").iterdir():
        if item.name == "config":
            shutil.copytree(item, data_dir / "config")
        elif item.name == "variants":
            variants_dir = data_dir / "variants"
            variants_dir.mkdir()
            for vf in item.iterdir():
                (variants_dir / vf.name).symlink_to(vf.resolve())
        else:
            (data_dir / item.name).symlink_to(item.resolve())

    # mods/ — copy combat-harness to pick up the freshly deployed jar
    mods_dir = work_dir / "mods"
    mods_dir.mkdir()
    mod_src = game / "mods" / "combat-harness"
    if not mod_src.exists():
        raise FileNotFoundError(
            f"combat-harness mod not found at {mod_src}. Run "
            f"`cd combat-harness && ./gradlew deploy` first."
        )
    shutil.copytree(mod_src, mods_dir / "combat-harness")
    enabled_src = game / "mods" / "enabled_mods.json"
    if enabled_src.exists():
        shutil.copy2(enabled_src, mods_dir / "enabled_mods.json")

    # saves/common/ + screenshots/ — real dirs the game writes to
    (work_dir / "saves" / "common").mkdir(parents=True)
    (work_dir / "screenshots").mkdir()

    # Null ALSA config — same convention as instance_manager
    (work_dir / "asound_null.conf").write_text(
        "pcm.!default { type null }\nctl.!default { type null }\n"
    )


def _start_xvfb(display_num: int) -> subprocess.Popen:
    """Start Xvfb on the requested display and wait for its socket."""
    lock_file = Path(f"/tmp/.X{display_num}-lock")
    socket_file = Path(f"/tmp/.X11-unix/X{display_num}")
    lock_file.unlink(missing_ok=True)
    socket_file.unlink(missing_ok=True)

    proc = subprocess.Popen(
        ["Xvfb", f":{display_num}", "-screen", "0", DEFAULT_SCREEN, "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):  # 5-second timeout
        if socket_file.exists() and proc.poll() is None:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError(f"Xvfb :{display_num} did not start")

    # XRandR warmup — required by LWJGL 2.x on some Xvfb builds.
    subprocess.run(
        ["xrandr", "--query"],
        env={**os.environ, "DISPLAY": f":{display_num}"},
        check=False, timeout=5,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def _start_game(work_dir: Path, display_num: int) -> tuple[subprocess.Popen, Path]:
    """Launch Starsector with the Xvfb display. Returns (proc, log_path)."""
    env = os.environ.copy()
    env["DISPLAY"] = f":{display_num}"
    env["ALSA_CONFIG_PATH"] = str(work_dir / "asound_null.conf")
    env["ALSOFT_DRIVERS"] = "null"
    log_path = work_dir / "game_stdout.log"
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        ["./starsector.sh"],
        cwd=str(work_dir),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return proc, log_path


_LAUNCHER_PLAY_X = 297  # "Play Starsector" button, calibrated for 1920x1080
_LAUNCHER_PLAY_Y = 255
_LAUNCHER_CLICK_SETTLE_SECONDS = 0.3


def _click_launcher(display_num: int, launcher_timeout: float = 30.0) -> None:
    """Click the Starsector launcher's "Play Starsector" button via xdotool.

    Mirrors `instance_manager._click_launcher`: poll for any window named
    "Starsector", then move mouse to the hardcoded button coordinates
    and click. Coordinates calibrated for 1920x1080 Xvfb.
    """
    env = {**os.environ, "DISPLAY": f":{display_num}"}
    deadline = time.monotonic() + launcher_timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Starsector"],
            env=env, capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Launcher window did not appear within {launcher_timeout}s")

    time.sleep(_LAUNCHER_CLICK_SETTLE_SECONDS)
    subprocess.run(
        ["xdotool", "mousemove", str(_LAUNCHER_PLAY_X), str(_LAUNCHER_PLAY_Y)],
        env=env, check=False, timeout=5,
    )
    time.sleep(_LAUNCHER_CLICK_SETTLE_SECONDS)
    subprocess.run(
        ["xdotool", "click", "1"],
        env=env, check=False, timeout=5,
    )
    logger.info("Clicked launcher Play button")


def _kill_process(proc: subprocess.Popen | None, label: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=PROCESS_KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("%s required SIGKILL", label)


def run_manifest_dump(game_dir: Path, repo_manifest_path: Path,
                      work_dir: Path, display_num: int,
                      timeout_seconds: float) -> None:
    logger.info("Using work_dir=%s display=:%d", work_dir, display_num)
    _create_work_dir(game_dir, work_dir)

    saves_common = work_dir / "saves" / "common"
    request_path = saves_common / REQUEST_FILE
    done_path = saves_common / DONE_FILE
    part_paths = {k: saves_common / v for k, v in MANIFEST_PARTS.items()}

    # Place the sentinel — the mod's TitleScreenPlugin will detect this
    # at title-screen stabilize and run ManifestDumper.dump then exit.
    request_path.write_text(str(int(time.time())))
    logger.info("Wrote sentinel %s", request_path)

    xvfb = game = None
    try:
        xvfb = _start_xvfb(display_num)
        game, log_path = _start_game(work_dir, display_num)
        logger.info("Game launched, log=%s", log_path)

        # The launcher window appears first; click through it.
        _click_launcher(display_num)

        def _all_parts_present() -> bool:
            return (done_path.exists() and
                    all(p.exists() for p in part_paths.values()))

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if _all_parts_present():
                logger.info("Manifest done sentinel + all parts detected")
                break
            if game.poll() is not None:
                if _all_parts_present():
                    break
                raise RuntimeError(
                    f"Game process exited before producing manifest "
                    f"(code={game.returncode}). Check {log_path}."
                )
            time.sleep(POLL_INTERVAL_SECONDS)
        else:
            raise TimeoutError(
                f"Manifest not produced within {timeout_seconds}s. "
                f"Check {log_path}."
            )

        # Merge the 4 part files into a single repo artifact.
        import json as _json
        merged = {
            part_name: _json.loads(path.read_text())
            for part_name, path in part_paths.items()
        }
        repo_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # Indented on disk — the repo copy is for human review + tooling,
        # not size-constrained; indent=2 makes git diff readable.
        repo_manifest_path.write_text(
            _json.dumps(merged, indent=2, sort_keys=True) + "\n"
        )
        logger.info("Manifest merged to %s (%.1f KB; %d weapons, %d hullmods, %d hulls)",
                    repo_manifest_path,
                    repo_manifest_path.stat().st_size / 1024.0,
                    len(merged.get("weapons", {})),
                    len(merged.get("hullmods", {})),
                    len(merged.get("hulls", {})))
    finally:
        _kill_process(game, "game")
        _kill_process(xvfb, "xvfb")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path,
                        default=Path("game/starsector"),
                        help="Path to the Starsector install (default: game/starsector)")
    parser.add_argument("--manifest-out", type=Path,
                        default=Path("game/starsector/manifest.json"),
                        help="Where to write the regenerated manifest in the repo")
    parser.add_argument("--work-dir", type=Path,
                        default=Path("/tmp/starsector-manifest-dump"),
                        help="Throwaway per-run work directory")
    parser.add_argument("--display", type=int, default=DEFAULT_DISPLAY,
                        help=f"Xvfb display number (default: :{DEFAULT_DISPLAY})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help=f"Overall timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})")
    args = parser.parse_args()

    game_dir = args.game_dir.resolve()
    if not (game_dir / "starsector.sh").exists():
        logger.error("Invalid game_dir %s — missing starsector.sh", game_dir)
        return 2

    try:
        run_manifest_dump(
            game_dir=game_dir,
            repo_manifest_path=args.manifest_out,
            work_dir=args.work_dir,
            display_num=args.display,
            timeout_seconds=args.timeout,
        )
    except Exception as e:
        logger.error("Manifest dump failed: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
