"""Post-hoc ranking comparison on Wave 1 evaluation logs.

Runs four estimators (raw_mean, twfe, twfe_eb, bradley_terry) against the
post-migration Wave 1 logs in `data/logs/wave1-*/`, both per-cell and pooled.
Reports top-K rankings, top-K agreement, Spearman ρ, and a build-by-build
description for human review.

Usage:
    uv run python scripts/analysis/posthoc_rank_wave1.py --k 5
    uv run python scripts/analysis/posthoc_rank_wave1.py --k 5 --pool-all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from starsector_optimizer.posthoc_ranker import (
    RankedBuild,
    load_records,
    rank_bradley_terry,
    rank_raw_mean,
    rank_twfe,
    rank_twfe_eb,
    spearman_rho,
    topk_overlap,
)


METHODS = {
    "raw_mean":       rank_raw_mean,
    "twfe":           rank_twfe,
    "twfe_eb":        rank_twfe_eb,
    "bradley_terry":  rank_bradley_terry,
}


def _format_build(rb: RankedBuild) -> str:
    b = rb.raw_build
    weapons = b.get("weapon_assignments", {})
    weapons_compact = ", ".join(
        f"{slot}={w}" for slot, w in sorted(weapons.items()) if w is not None
    )
    return (
        f"  hull={b.get('hull_id')}\n"
        f"  vents={b.get('flux_vents')}, caps={b.get('flux_capacitors')}\n"
        f"  hullmods={sorted(b.get('hullmods', []))}\n"
        f"  weapons=[{weapons_compact}]"
    )


def _print_ranked(name: str, ranked: list[RankedBuild], k: int) -> None:
    print(f"\n[{name}] top-{k}:")
    for i, r in enumerate(ranked, 1):
        print(
            f"  #{i:>2}  build={r.build_id.short}  "
            f"score={r.score:+.3f}  σ={r.sigma:.3f}  "
            f"n={r.n_matches:>3}  studies={','.join(r.studies)}"
        )


def _agreement_table(rankings: dict[str, list[RankedBuild]], k: int) -> None:
    print(f"\n[top-{k} agreement matrix] (overlap / Spearman-ρ on overlap)")
    keys = list(rankings.keys())
    width = max(len(k) for k in keys) + 2
    header = " " * width + "".join(f"{k:>16}" for k in keys)
    print(header)
    for a in keys:
        row = f"{a:<{width}}"
        for b in keys:
            ov = topk_overlap(rankings[a], rankings[b])
            rho = spearman_rho(rankings[a], rankings[b])
            row += f"{ov:>3}/{rho:+.2f}".rjust(16)
        print(row)


def _all_logs() -> list[Path]:
    return sorted(REPO_ROOT.glob(
        "data/logs/wave1-*/hammerhead__early__tpe__seed*/evaluation_log.jsonl"
    ))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--k", type=int, default=5, help="top-K to report")
    p.add_argument("--pool-all", action="store_true",
                   help="pool across all 15 studies (default: per-cell)")
    p.add_argument("--cells", nargs="+", default=["c0a", "c0b", "c1", "c2", "c3"])
    args = p.parse_args()

    logs = _all_logs()
    if not logs:
        print("No Wave 1 logs found at data/logs/wave1-*/...")
        return 1

    if args.pool_all:
        print(f"Loading {len(logs)} JSONL files (pooled across all studies)...")
        records = load_records(logs)
        print(f"  -> {len(records)} completed trials, "
              f"{sum(len(r.matches) for r in records)} matchups")
        rankings = {}
        for name, fn in METHODS.items():
            rankings[name] = fn(records, k=max(args.k, 25))
            _print_ranked(name, rankings[name][:args.k], args.k)
        _agreement_table(
            {n: r[:args.k] for n, r in rankings.items()}, args.k,
        )
        # Detailed dump of top-K from each method for review.
        print(f"\n=== Build details for top-{args.k} of each method ===")
        for name, ranked in rankings.items():
            print(f"\n--- {name} ---")
            for i, r in enumerate(ranked[:args.k], 1):
                print(f"\n#{i} build={r.build_id.short} score={r.score:+.3f}")
                print(_format_build(r))
        return 0

    # Per-cell: 5 cells, each pooled across 3 seeds.
    by_cell: dict[str, list[Path]] = {}
    for fp in logs:
        cell = fp.parent.parent.name.removeprefix("wave1-")
        by_cell.setdefault(cell, []).append(fp)

    for cell in args.cells:
        if cell not in by_cell:
            continue
        print(f"\n{'='*70}")
        print(f"=== Cell {cell} ===")
        print('='*70)
        records = load_records(by_cell[cell])
        print(f"  {len(records)} trials, {sum(len(r.matches) for r in records)} matchups")
        rankings = {}
        for name, fn in METHODS.items():
            rankings[name] = fn(records, k=max(args.k, 25))
            _print_ranked(name, rankings[name][:args.k], args.k)
        _agreement_table(
            {n: r[:args.k] for n, r in rankings.items()}, args.k,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
