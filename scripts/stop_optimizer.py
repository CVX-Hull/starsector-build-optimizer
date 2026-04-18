#!/usr/bin/env python3
"""Panic-button: stop all Starsector optimizer processes and clean up.

Prefer Ctrl-C on the orchestrator — that triggers `InstancePool.__exit__` →
`teardown()` which writes shutdown signals and terminates processes cleanly.

Use this script when:
- The orchestrator crashed or was killed with SIGKILL, orphaning JVMs/Xvfb.
- A prior `pkill -f starsector` (wrong pattern!) left processes running.
- You need to force-stop a runaway optimizer.

Sequence:
1. Write shutdown signal file to every work dir under --instance-root.
2. Send SIGTERM to StarfarerLauncher JVMs and Xvfb processes.
3. Wait up to --grace seconds for graceful exit.
4. Send SIGKILL to any survivors.
5. Report remaining process counts (non-zero exit code if any remain).

Patterns matched:
    JVM:  cmdline contains 'StarfarerLauncher'
    Xvfb: cmdline matches 'Xvfb :1[0-9][0-9]' (display range 100-199)

Usage:
    uv run python scripts/stop_optimizer.py [--instance-root PATH] [--grace N]
"""
from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_INSTANCE_ROOT = Path("/tmp/starsector-instances")
DEFAULT_GRACE_SECONDS = 5

JVM_PATTERN = "StarfarerLauncher"
XVFB_REGEX = re.compile(r"^Xvfb :1\d{2}\b")


def _pids_matching(substring: str | None = None, regex: re.Pattern | None = None) -> list[int]:
    """Return PIDs whose full command line contains substring or matches regex."""
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    pids = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, args = line.partition(" ")
        if substring and substring in args:
            pids.append(int(pid_str))
        elif regex and regex.search(args.strip()):
            pids.append(int(pid_str))
    return pids


def _write_shutdown_signals(instance_root: Path) -> int:
    """Write shutdown signal to every discovered instance work dir. Returns count."""
    if not instance_root.exists():
        return 0
    signals_written = 0
    ts = str(int(time.time() * 1000))
    for work_dir in sorted(instance_root.iterdir()):
        saves_common = work_dir / "saves" / "common"
        if not saves_common.is_dir():
            continue
        signal_path = saves_common / "combat_harness_shutdown.data"
        try:
            signal_path.write_text(ts)
            signals_written += 1
        except OSError as e:
            print(f"  warn: could not write {signal_path}: {e}", file=sys.stderr)
    return signals_written


def _signal_pids(pids: list[int], sig: signal.Signals) -> None:
    import os
    for pid in pids:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            print(f"  warn: cannot signal pid={pid}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Stop all optimizer processes")
    ap.add_argument("--instance-root", type=Path, default=DEFAULT_INSTANCE_ROOT,
                    help=f"Work-dir root (default {DEFAULT_INSTANCE_ROOT})")
    ap.add_argument("--grace", type=int, default=DEFAULT_GRACE_SECONDS,
                    help=f"Seconds to wait for graceful exit (default {DEFAULT_GRACE_SECONDS})")
    args = ap.parse_args()

    print(f"[1/5] Writing shutdown signals under {args.instance_root}...")
    n = _write_shutdown_signals(args.instance_root)
    print(f"      wrote {n} signal file(s)")

    print(f"[2/5] Sending SIGTERM to JVMs ({JVM_PATTERN}) and Xvfb (:100-:199)...")
    jvm_pids = _pids_matching(substring=JVM_PATTERN)
    xvfb_pids = _pids_matching(regex=XVFB_REGEX)
    print(f"      JVMs: {len(jvm_pids)}, Xvfb: {len(xvfb_pids)}")
    _signal_pids(jvm_pids + xvfb_pids, signal.SIGTERM)

    print(f"[3/5] Waiting {args.grace}s for graceful exit...")
    time.sleep(args.grace)

    print("[4/5] Sending SIGKILL to survivors...")
    jvm_pids = _pids_matching(substring=JVM_PATTERN)
    xvfb_pids = _pids_matching(regex=XVFB_REGEX)
    print(f"      remaining JVMs: {len(jvm_pids)}, Xvfb: {len(xvfb_pids)}")
    _signal_pids(jvm_pids + xvfb_pids, signal.SIGKILL)
    time.sleep(1)

    print("[5/5] Verifying...")
    jvm_pids = _pids_matching(substring=JVM_PATTERN)
    xvfb_pids = _pids_matching(regex=XVFB_REGEX)
    if jvm_pids or xvfb_pids:
        print(f"  FAIL: still running — JVMs={jvm_pids}, Xvfb={xvfb_pids}",
              file=sys.stderr)
        return 1
    print("  OK: all optimizer processes stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
