"""Migrate Wave 1 interleaved JSONL eval logs to per-cell paths.

Pre-task-#90, all 5 Wave 1 cells (c0a, c0b, c1, c2, c3) wrote their
per-trial JSONL rows to a path that didn't include the campaign name,
so 5 cells × 3 seeds = 15 studies all interleaved into 3 shared
files at `data/logs/hammerhead__early__tpe__seed{0,1,2}/evaluation_log.jsonl`.
Task #90 fixed this for Wave 2+ but the Wave 1 data is stuck.

This script disambiguates by:

1. Reading each cell's per-cell SQLite DB (`data/study_dbs/wave1-{cell}/...`)
   for trial timestamps and the trial-number set per (cell, seed).
2. Slicing the interleaved JSONL by timestamp range (cells were run
   sequentially per `launch_wave1.sh`; gaps verified ≥ 240s) and
   trial_number membership.
3. Writing each line to the post-fix path
   `data/logs/wave1-{cell}/hammerhead__early__tpe__seed{N}/evaluation_log.jsonl`.
4. Moving the legacy interleaved file aside with a `.legacy-interleaved`
   suffix so the audit trail is preserved.

After this runs, the layout matches what task #90 produces for Wave 2+,
and downstream code (honest-eval candidate ranking, analyzers) can use
a single file-path convention without timestamp slicing.

Usage:
    uv run python scripts/migrate_wave1_eval_logs.py
    uv run python scripts/migrate_wave1_eval_logs.py --dry-run
    uv run python scripts/migrate_wave1_eval_logs.py --force  # re-migrate
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import TypedDict
import itertools


class CellMeta(TypedDict):
    cell: str
    ts_lo: datetime
    ts_hi: datetime
    trial_numbers: set[int]

REPO_ROOT = Path(__file__).resolve().parent.parent
WAVE1_CELLS = ("c0a", "c0b", "c1", "c2", "c3")
WAVE1_SEEDS = (0, 1, 2)
LEGACY_SUFFIX = ".legacy-interleaved"
# Optuna stores datetime in naive local time. The Wave 1 run system was
# in EDT = UTC-4. We convert SQLite naive → UTC to match JSONL's
# tz-aware UTC timestamps.
EDT_OFFSET_HOURS = 4


def _parse_sqlite_ts(ts: str | None) -> datetime | None:
    if ts is None:
        return None
    naive = datetime.fromisoformat(ts)
    return (naive + timedelta(hours=EDT_OFFSET_HOURS)).replace(tzinfo=UTC)


def _parse_jsonl_ts(ts: str | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _shared_log_path(seed: int) -> Path:
    return (
        REPO_ROOT
        / "data" / "logs"
        / f"hammerhead__early__tpe__seed{seed}"
        / "evaluation_log.jsonl"
    )


def _migrated_log_path(cell: str, seed: int) -> Path:
    return (
        REPO_ROOT
        / "data" / "logs" / f"wave1-{cell}"
        / f"hammerhead__early__tpe__seed{seed}"
        / "evaluation_log.jsonl"
    )


def _cell_db_path(cell: str, seed: int) -> Path:
    return (
        REPO_ROOT
        / "data" / "study_dbs" / f"wave1-{cell}"
        / f"hammerhead__early__tpe__seed{seed}.db"
    )


def _load_cell_metadata(seed: int) -> list[CellMeta]:
    """For each cell at this seed, read SQLite and return:
        - cell name
        - overall (ts_start, ts_complete) range
        - set of trial numbers in this study (any state)
    """
    out: list[CellMeta] = []
    for cell in WAVE1_CELLS:
        db_path = _cell_db_path(cell, seed)
        if not db_path.exists():
            raise FileNotFoundError(f"missing per-cell DB: {db_path}")
        conn = sqlite3.connect(str(db_path))
        try:
            rng = conn.execute(
                "SELECT MIN(datetime_start), MAX(datetime_complete) FROM trials"
            ).fetchone()
            trial_numbers = {
                n for (n,) in conn.execute("SELECT number FROM trials")
            }
        finally:
            conn.close()
        ts_lo = _parse_sqlite_ts(rng[0])
        ts_hi = _parse_sqlite_ts(rng[1])
        if ts_lo is None or ts_hi is None:
            raise RuntimeError(f"{db_path}: missing trial timestamps")
        out.append({
            "cell": cell,
            "ts_lo": ts_lo,
            "ts_hi": ts_hi,
            "trial_numbers": trial_numbers,
        })
    out.sort(key=lambda r: r["ts_lo"])
    # Sanity: cells must be disjoint with positive gaps. Overlap means
    # the timestamp-slicing approach is invalid for this seed.
    for prev, curr in itertools.pairwise(out):
        gap = (curr["ts_lo"] - prev["ts_hi"]).total_seconds()
        if gap <= 0:
            raise RuntimeError(
                f"seed {seed}: cells {prev['cell']} → {curr['cell']} "
                f"overlap (gap = {gap:.1f}s). Refusing to migrate by "
                f"timestamp slicing — investigate ledgers/orchestrator "
                f"logs to find a finer disambiguator."
            )
    return out


def _classify_line(
    data: dict, cell_meta: list[CellMeta],
) -> str | None:
    """Return the cell name this JSONL row belongs to, or None if it
    cannot be confidently classified.

    Two-factor disambiguation (timestamp + trial_number membership):
      - Timestamp must fall inside exactly one cell's [ts_lo, ts_hi]
      - That cell's SQLite study must contain a trial with this number
    Boundary lines (timestamp slightly outside any range) are checked
    against each cell's trial-number set; if exactly one matches, we
    pick that. If no cell matches by either criterion, return None
    (caller decides drop vs raise).
    """
    ts = _parse_jsonl_ts(data.get("timestamp"))
    trial_n = data.get("trial_number")

    candidates: list[str] = []
    for meta in cell_meta:
        # Allow a small grace window outside the strict trial range to
        # absorb rows written just after datetime_complete (the JSONL
        # write happens AFTER Optuna's datetime_complete; spec 22 logs
        # have shown this within ~2s).
        grace = timedelta(seconds=5)
        if ts is not None and (meta["ts_lo"] - grace) <= ts <= (meta["ts_hi"] + grace):
            if trial_n is None or trial_n in meta["trial_numbers"]:
                candidates.append(meta["cell"])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # Tie-break by trial_number ∈ that cell's set (eliminates the
        # cross-boundary case where ts falls in cell A's grace window
        # but trial_number only exists in cell B).
        if trial_n is not None:
            narrowed = [
                c for c in candidates
                if trial_n in next(m["trial_numbers"] for m in cell_meta if m["cell"] == c)
            ]
            if len(narrowed) == 1:
                return narrowed[0]
    return None


def migrate_seed(seed: int, dry_run: bool = False, force: bool = False) -> dict:
    src = _shared_log_path(seed)
    if not src.exists():
        raise FileNotFoundError(f"no shared log to migrate for seed {seed}: {src}")

    # Refuse to overwrite without --force
    targets = {cell: _migrated_log_path(cell, seed) for cell in WAVE1_CELLS}
    existing = [p for p in targets.values() if p.exists()]
    if existing and not force:
        raise RuntimeError(
            f"seed {seed}: target paths already exist (e.g. {existing[0]}). "
            f"Re-run with --force to overwrite."
        )

    cell_meta = _load_cell_metadata(seed)
    # Group lines per cell.
    classified: dict[str, list[str]] = defaultdict(list)
    unclassified: list[tuple[int, str]] = []
    with src.open() as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                unclassified.append((lineno, "JSON-decode error"))
                continue
            cell = _classify_line(data, cell_meta)
            if cell is None:
                unclassified.append((
                    lineno,
                    f"timestamp={data.get('timestamp')!r} "
                    f"trial_number={data.get('trial_number')!r}",
                ))
                continue
            classified[cell].append(stripped + "\n")

    summary = {
        "seed": seed,
        "src": str(src),
        "src_total_lines": sum(len(v) for v in classified.values()) + len(unclassified),
        "per_cell_lines": {c: len(classified.get(c, [])) for c in WAVE1_CELLS},
        "unclassified": len(unclassified),
        "unclassified_sample": unclassified[:5],
    }

    if dry_run:
        return summary

    # Write per-cell files.
    for cell in WAVE1_CELLS:
        target = targets[cell]
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w") as f:
            f.writelines(classified.get(cell, []))
    # Move legacy file aside.
    legacy = src.with_suffix(src.suffix + LEGACY_SUFFIX)
    shutil.move(str(src), str(legacy))
    summary["legacy_path"] = str(legacy)
    return summary


def verify_migration(seed: int) -> dict:
    """Cross-check post-migration counts against SQLite COMPLETE-trial counts.

    Each migrated JSONL should contain at least one row per
    completed trial (the optimizer emits one per `_log_evaluation`
    call, which fires on completion). Pruned trials may also appear,
    so we expect lines >= COMPLETE count.
    """
    per_cell: dict[str, dict[str, object]] = {}
    for cell in WAVE1_CELLS:
        db_path = _cell_db_path(cell, seed)
        log_path = _migrated_log_path(cell, seed)
        conn = sqlite3.connect(str(db_path))
        try:
            n_complete = conn.execute(
                "SELECT COUNT(*) FROM trials WHERE state='COMPLETE'"
            ).fetchone()[0]
            n_pruned = conn.execute(
                "SELECT COUNT(*) FROM trials WHERE state='PRUNED'"
            ).fetchone()[0]
        finally:
            conn.close()
        n_log = sum(1 for _ in log_path.open())
        per_cell[cell] = {
            "complete": n_complete,
            "pruned": n_pruned,
            "log_lines": n_log,
            "ok": n_log >= n_complete,
        }
    return {"seed": seed, "per_cell": per_cell}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Compute classification but don't write files.")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing migrated paths.")
    p.add_argument("--seeds", type=int, nargs="+", default=list(WAVE1_SEEDS),
                   help="Subset of seeds to migrate (default: all).")
    args = p.parse_args()

    print("=" * 70)
    print(f"Wave 1 JSONL migration — seeds {args.seeds}, "
          f"dry_run={args.dry_run}, force={args.force}")
    print("=" * 70)

    all_ok = True
    for seed in args.seeds:
        try:
            summary = migrate_seed(seed, dry_run=args.dry_run, force=args.force)
        except Exception as exc:
            print(f"\n[seed {seed}] FAILED: {exc}")
            all_ok = False
            continue
        print(f"\n[seed {seed}] {summary['src_total_lines']} lines from {summary['src']}")
        for cell, n in summary["per_cell_lines"].items():
            print(f"  {cell}: {n} lines")
        if summary["unclassified"]:
            print(f"  UNCLASSIFIED: {summary['unclassified']} (first 5: "
                  f"{summary['unclassified_sample']})")
        if not args.dry_run:
            print(f"  legacy file moved to: {summary['legacy_path']}")
            v = verify_migration(seed)
            print(f"\n[seed {seed}] verification:")
            for cell, info in v["per_cell"].items():
                marker = "OK" if info["ok"] else "WARN"
                print(f"  [{marker}] {cell}: complete={info['complete']} "
                      f"pruned={info['pruned']} log_lines={info['log_lines']}")
                if not info["ok"]:
                    all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
