"""Honest-eval digest. Reads `data/campaigns/{cell}/honest_eval.json`
files and emits a per-cell summary table that drops directly into the
Wave 1 report § 3.

Usage:
    uv run python scripts/honest_eval_digest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WAVE1_CELLS = ("wave1-c0a", "wave1-c0b", "wave1-c1", "wave1-c2", "wave1-c3")


def _build_short(b: dict) -> str:
    """Compact build identifier (caps/vents + first 3 hullmods)."""
    hm = b.get("hullmods", [])
    hm_short = ",".join(sorted(hm)[:3]) + ("..." if len(hm) > 3 else "")
    return f"caps={b.get('flux_capacitors')}/vents={b.get('flux_vents')} hm=[{hm_short}]"


def cell_summary(cell: str) -> dict:
    path = REPO_ROOT / "data" / "campaigns" / cell / "honest_eval.json"
    if not path.exists():
        return {"missing": True, "path": str(path)}
    data = json.loads(path.read_text())
    builds = data.get("evaluated_builds", [])
    if not builds:
        return {"missing": False, "n_builds": 0}
    # Build a per-build summary
    rows = []
    for eb in builds:
        rows.append({
            "build_id": f"{eb['source_campaign']}__s{eb['source_study_idx']}__seed{eb['source_seed_idx']}__rank{eb['source_rank']}",
            "training_value": eb.get("source_value"),
            "honest_mean": eb.get("oracle_score"),
            "honest_sem": eb.get("oracle_se"),
            "n_matchups_succeeded": eb.get("n_matchups_succeeded"),
            "build_short": _build_short(eb["build"]),
        })
    rows.sort(key=lambda r: r["honest_mean"] or float("-inf"), reverse=True)
    top1 = rows[0]
    top1_diff_in_sigma = (
        ((top1["honest_mean"] or 0) - (top1["training_value"] or 0))
        / max(top1["honest_sem"] or 1e-9, 1e-9)
    )
    return {
        "missing": False,
        "n_builds": len(rows),
        "top1": top1,
        "top1_honest_minus_training_in_sigma": top1_diff_in_sigma,
        "all_rows": rows,
        "pool_size": data.get("pool_size"),
        "config": data.get("config"),
    }


def main() -> int:
    print("=" * 78)
    print("Honest-eval digest")
    print("=" * 78)
    cell_data = {}
    for cell in WAVE1_CELLS:
        cell_data[cell] = cell_summary(cell)

    print("\n## Top-1 honest fitness across cells (the headline)\n")
    print(f"{'Cell':<14} {'top-1 honest':>12} {'top-1 training':>15} {'Δ(honest-train)/σ':>18} {'build':<60}")
    print("-" * 122)
    cells_with_data = [(c, d) for c, d in cell_data.items() if not d.get("missing") and d.get("n_builds", 0) > 0]
    for cell, d in cells_with_data:
        top1 = d["top1"]
        ht = top1["honest_mean"] or 0.0
        tt = top1["training_value"] or 0.0
        sd = d["top1_honest_minus_training_in_sigma"]
        build = top1["build_short"][:58]
        print(f"{cell:<14} {ht:>12.4f} {tt:>15.4f} {sd:>18.2f} {build:<60}")

    # Cell ranking by best honest fitness
    print("\n## Cell ranking by top-1 honest fitness (which cell's optimizer wins?)\n")
    ranked = sorted(cells_with_data, key=lambda x: x[1]["top1"]["honest_mean"] or float("-inf"), reverse=True)
    for rank, (cell, d) in enumerate(ranked, start=1):
        print(f"  {rank}. {cell}  honest={d['top1']['honest_mean']:.4f}  n_builds={d['n_builds']}")

    # Verdict heuristics
    print("\n## Decision-tree input")
    if len(ranked) >= 2:
        winner_cell, winner_d = ranked[0]
        runner_cell, runner_d = ranked[1]
        margin = (winner_d["top1"]["honest_mean"] or 0) - (runner_d["top1"]["honest_mean"] or 0)
        winner_sem = winner_d["top1"]["honest_sem"] or 0
        runner_sem = runner_d["top1"]["honest_sem"] or 0
        margin_in_sigma = margin / max((winner_sem ** 2 + runner_sem ** 2) ** 0.5, 1e-9)
        print(f"  Winner: {winner_cell}, Runner-up: {runner_cell}")
        print(f"  Margin: {margin:.4f} ({margin_in_sigma:.2f}σ)")

        # F1c paradigm-flip check: c2/c3 should win or tie if EB+Box-Cox is helpful
        if "c2" in winner_cell or "c3" in winner_cell:
            print("  → C2/C3 wins or near-ties: EB+Box-Cox config validates. PROCEED.")
        elif "c0a" in winner_cell or "c0b" in winner_cell:
            print("  → C0a/C0b wins: F1c PARADIGM FLIP signal. Consider EB rollback before Wave 3.")
        else:
            print("  → C1 wins: Box-Cox is harmful, EB alone is better. Consider F2 rollback.")

    out_path = REPO_ROOT / "data" / "honest_eval_digest.json"
    out_path.write_text(json.dumps(cell_data, indent=2, default=str))
    print(f"\nDetail dump: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
