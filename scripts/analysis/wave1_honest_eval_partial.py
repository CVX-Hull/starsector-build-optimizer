"""Read-only partial analysis for an in-flight Wave 1 honest-eval ledger.

The final honest-eval report is written from completed per-cell
``honest_eval.json`` files. During a live/resumed run, those files do not exist
yet, but the append-only ledger is already useful for progress, health checks,
and clearly-labelled provisional rankings.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BUILD_ID_RE = re.compile(
    r"^honest__(?P<cell>.+)__s(?P<study>\d+)__seed(?P<seed>-?\d+)__rank(?P<rank>\d+)$"
)

STALL_PATTERNS = (
    "LOADOUT_MISMATCH",
    "matchup_id mismatch",
    "ResultEnvelopeMismatch",
    "corrupt result",
    "ERROR",
    "Traceback",
    "WorkerTimeout",
    "BudgetExceeded",
    "preflight failed",
)


@dataclass(frozen=True)
class BuildKey:
    build_id: str
    cell: str
    seed: int
    rank: int


@dataclass(frozen=True)
class BuildStats:
    key: BuildKey
    n: int
    mean: float
    sem: float
    min_score: float
    max_score: float


def parse_build_id(build_id: str) -> BuildKey:
    match = BUILD_ID_RE.match(build_id)
    if not match:
        return BuildKey(build_id=build_id, cell="<unparsed>", seed=-1, rank=-1)
    return BuildKey(
        build_id=build_id,
        cell=match.group("cell"),
        seed=int(match.group("seed")),
        rank=int(match.group("rank")),
    )


def mean_sem(values: list[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return mean, math.sqrt(var / len(values))


def load_ledger(path: Path) -> tuple[int, dict[tuple[str, str, int], float]]:
    rows = 0
    completed: dict[tuple[str, str, int], float] = {}
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            rows += 1
            data = json.loads(line)
            key = (
                str(data["build_id"]),
                str(data["opponent_variant_id"]),
                int(data["replicate_idx"]),
            )
            completed[key] = float(data["fitness"])
    return rows, completed


def build_stats(completed: dict[tuple[str, str, int], float]) -> list[BuildStats]:
    by_build: dict[str, list[float]] = defaultdict(list)
    for (build_id, _opp, _rep), fitness in completed.items():
        by_build[build_id].append(fitness)
    out: list[BuildStats] = []
    for build_id, values in by_build.items():
        mean, sem = mean_sem(values)
        out.append(
            BuildStats(
                key=parse_build_id(build_id),
                n=len(values),
                mean=mean,
                sem=sem,
                min_score=min(values),
                max_score=max(values),
            )
        )
    return sorted(out, key=lambda item: (item.key.cell, item.key.seed, item.key.rank))


def log_health(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {}
    counts: Counter[str] = Counter()
    bins: Counter[str] = Counter()
    first_time: datetime | None = None
    last_time: datetime | None = None
    with path.open(errors="replace") as f:
        for line in f:
            if '"POST /result HTTP/1.1" 200' in line:
                counts["http_200"] += 1
            if '"POST /result HTTP/1.1" 409' in line:
                counts["http_409"] += 1
            if "LOADOUT_OK" in line:
                counts["loadout_ok"] += 1
            if "requeue" in line.lower():
                counts["requeue"] += 1
            for pattern in STALL_PATTERNS:
                if pattern in line:
                    counts[f"pattern:{pattern}"] += 1
            match = re.match(r"(?P<h>\d\d):(?P<m>\d\d):(?P<s>\d\d) ", line)
            if match and '"POST /result HTTP/1.1" 200' in line:
                stamp = datetime(
                    2026, 5, 10,
                    int(match.group("h")),
                    int(match.group("m")),
                    int(match.group("s")),
                )
                first_time = stamp if first_time is None else min(first_time, stamp)
                last_time = stamp if last_time is None else max(last_time, stamp)
                minute = stamp.hour * 60 + stamp.minute
                bucket_minute = minute - (minute % 15)
                bucket = f"{bucket_minute // 60:02d}:{bucket_minute % 60:02d}"
                bins[bucket] += 1
    elapsed_minutes = None
    rate_per_minute = None
    if first_time is not None and last_time is not None and last_time > first_time:
        elapsed_minutes = (last_time - first_time).total_seconds() / 60.0
        rate_per_minute = counts["http_200"] / elapsed_minutes
    return {
        "counts": counts,
        "bins": bins,
        "first_time": first_time,
        "last_time": last_time,
        "elapsed_minutes": elapsed_minutes,
        "rate_per_minute": rate_per_minute,
    }


def fmt(value: float) -> str:
    return f"{value:+.4f}"


def pct(num: float, den: float) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "n/a"


def render(
    ledger_path: Path,
    log_path: Path | None,
    expected_builds: int,
    expected_opponents: int,
    expected_replicates: int,
    resumed_from_count: int,
) -> str:
    physical_rows, completed = load_ledger(ledger_path)
    stats = build_stats(completed)
    expected_per_build = expected_opponents * expected_replicates
    expected_total = expected_builds * expected_per_build
    unique_rows = len(completed)
    new_rows = max(0, unique_rows - resumed_from_count)
    remaining = max(0, expected_total - unique_rows)

    complete = [item for item in stats if item.n == expected_per_build]
    observed = [item for item in stats if item.n > 0]
    partial = [item for item in stats if 0 < item.n < expected_per_build]

    health = log_health(log_path)
    rate = health.get("rate_per_minute")
    eta = "n/a"
    if isinstance(rate, float) and rate > 0:
        eta_hours = remaining / (rate * 60.0)
        eta = f"{eta_hours:.1f} h"

    out: list[str] = []
    out.append("# Wave 1 Honest-Eval Partial Snapshot")
    out.append("")
    out.append("Read-only preliminary analysis. Rankings below are provisional because the ledger is prefix-ordered by dispatch, not a randomized complete panel.")
    out.append("")
    out.append("## Progress")
    out.append("")
    out.append("| metric | value |")
    out.append("|---|---:|")
    out.append(f"| physical ledger rows | {physical_rows} |")
    out.append(f"| unique resume keys | {unique_rows} |")
    out.append(f"| duplicate physical rows | {physical_rows - unique_rows} |")
    out.append(f"| total expected matchups | {expected_total} |")
    out.append(f"| total progress | {pct(unique_rows, expected_total)} |")
    out.append(f"| new rows since resume baseline | {new_rows} |")
    out.append(f"| remaining matchups | {remaining} |")
    out.append(f"| observed builds | {len(observed)} / {expected_builds} |")
    out.append(f"| complete builds | {len(complete)} / {expected_builds} |")
    out.append(f"| partial builds | {len(partial)} |")
    out.append(f"| current-run ETA at observed HTTP 200 rate | {eta} |")
    out.append("")

    out.append("## Cell Coverage")
    out.append("")
    out.append("| cell | builds observed | complete builds | results | coverage | complete-build mean oracle | observed-build mean oracle |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    cells = sorted({item.key.cell for item in stats})
    for cell in cells:
        cell_stats = [item for item in stats if item.key.cell == cell]
        cell_complete = [item for item in cell_stats if item.n == expected_per_build]
        results = sum(item.n for item in cell_stats)
        complete_mean = (
            sum(item.mean for item in cell_complete) / len(cell_complete)
            if cell_complete else float("nan")
        )
        observed_mean = sum(item.mean for item in cell_stats) / len(cell_stats)
        complete_s = fmt(complete_mean) if cell_complete else "n/a"
        out.append(
            f"| {cell} | {len(cell_stats)} | {len(cell_complete)} | {results} | "
            f"{pct(results, 9 * expected_per_build)} | {complete_s} | {fmt(observed_mean)} |"
        )
    out.append("")

    out.append("## Complete-Build Ranking")
    out.append("")
    if complete:
        out.append("| rank | cell | seed | source rank | n | oracle mean | SE | min | max |")
        out.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
        for i, item in enumerate(sorted(complete, key=lambda x: x.mean, reverse=True)[:15], 1):
            out.append(
                f"| {i} | {item.key.cell} | {item.key.seed} | {item.key.rank} | {item.n} | "
                f"{fmt(item.mean)} | {item.sem:.4f} | {fmt(item.min_score)} | {fmt(item.max_score)} |"
            )
    else:
        out.append("No build has a complete 1,620-matchup panel yet.")
    out.append("")

    out.append("## Active/Partial Builds")
    out.append("")
    if partial:
        out.append("| cell | seed | source rank | n | coverage | observed mean | SE |")
        out.append("|---|---:|---:|---:|---:|---:|---:|")
        for item in sorted(partial, key=lambda x: x.n, reverse=True)[:20]:
            out.append(
                f"| {item.key.cell} | {item.key.seed} | {item.key.rank} | {item.n} | "
                f"{pct(item.n, expected_per_build)} | {fmt(item.mean)} | {item.sem:.4f} |"
            )
    else:
        out.append("No partial build panels are present.")
    out.append("")

    out.append("## Honest-Eval Checks From Existing Reports")
    out.append("")
    by_cell_complete = defaultdict(list)
    for item in complete:
        by_cell_complete[item.key.cell].append(item)
    cell_complete_mean = {
        cell: sum(item.mean for item in items) / len(items)
        for cell, items in by_cell_complete.items()
    }
    if {"wave1-c0a", "wave1-c0b", "wave1-c2"} <= set(cell_complete_mean):
        c2 = cell_complete_mean["wave1-c2"]
        out.append(f"- F1c C2 vs C0a complete-panel delta: {fmt(c2 - cell_complete_mean['wave1-c0a'])}")
        out.append(f"- F1c C2 vs C0b complete-panel delta: {fmt(c2 - cell_complete_mean['wave1-c0b'])}")
    else:
        out.append("- F1c C2-vs-baseline gate: not estimable yet; C2 complete panels are not all present.")
    if "random-baseline" in cell_complete_mean:
        beats = sum(
            1
            for cell in ("wave1-c0a", "wave1-c0b", "wave1-c1", "wave1-c2", "wave1-c3")
            if cell in cell_complete_mean
            and cell_complete_mean[cell] > cell_complete_mean["random-baseline"]
        )
        out.append(f"- Random-baseline existence check: {beats}/5 cells beat the complete-panel random-baseline mean.")
    else:
        out.append("- Random-baseline existence check: not estimable yet; random-baseline panels have not completed.")
    out.append("")

    if health:
        counts: Counter[str] = health["counts"]  # type: ignore[assignment]
        bins: Counter[str] = health["bins"]  # type: ignore[assignment]
        out.append("## Current Orchestrator Health")
        out.append("")
        out.append("| metric | value |")
        out.append("|---|---:|")
        out.append(f"| HTTP 200 result posts | {counts['http_200']} |")
        out.append(f"| LOADOUT_OK lines | {counts['loadout_ok']} |")
        out.append(f"| HTTP 409 duplicate posts | {counts['http_409']} |")
        out.append(f"| requeue lines | {counts['requeue']} |")
        for pattern in STALL_PATTERNS:
            out.append(f"| {pattern} lines | {counts[f'pattern:{pattern}']} |")
        if isinstance(rate, float):
            out.append(f"| observed HTTP 200 rate | {rate:.1f} / min |")
        out.append("")
        if bins:
            out.append("### 15-Minute Result Bins")
            out.append("")
            out.append("| local time bucket | results | rate/min |")
            out.append("|---|---:|---:|")
            for bucket in sorted(bins):
                out.append(f"| {bucket} | {bins[bucket]} | {bins[bucket] / 15.0:.1f} |")
            out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--expected-builds", type=int, default=54)
    parser.add_argument("--expected-opponents", type=int, default=54)
    parser.add_argument("--expected-replicates", type=int, default=30)
    parser.add_argument("--resumed-from-count", type=int, default=29_523)
    args = parser.parse_args()
    print(
        render(
            ledger_path=args.ledger,
            log_path=args.log,
            expected_builds=args.expected_builds,
            expected_opponents=args.expected_opponents,
            expected_replicates=args.expected_replicates,
            resumed_from_count=args.resumed_from_count,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
