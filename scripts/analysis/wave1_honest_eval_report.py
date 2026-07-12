"""Render §3 of the Wave 1 validation report from honest-eval JSON outputs.

Reads each `data/campaigns/<cell>/honest_eval.json` (written by
honest_evaluator.write_outputs) and prints a markdown chunk covering:
  - Per-cell top-1 row + mean-top-K oracle
  - Cell ranking by mean honest fitness
  - F1c gate: do C0a/C0b dominate C2/C3?

Usage:
    uv run python scripts/analysis/wave1_honest_eval_report.py \
        [--root data/campaigns] \
        [--cells wave1-c0a wave1-c0b wave1-c1 wave1-c2 wave1-c3 random-baseline]

Outputs to stdout. The Wave 1 validation report owner copy-pastes the
relevant chunks into docs/reports/2026-05-10-wave1-validation.md §3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path


WAVE1_CELLS_DEFAULT = (
    "wave1-c0a",
    "wave1-c0b",
    "wave1-c1",
    "wave1-c2",
    "wave1-c3",
    "random-baseline",
)


@dataclass
class CellRow:
    cell: str
    n_builds: int
    mean_top_k_oracle: float
    best_oracle: float
    best_oracle_se: float
    best_source_value: float
    best_build_hash: str
    best_source_rank: int
    best_source_seed_idx: int


def build_hash(build_dict: dict) -> str:
    """12-char hex hash of the canonicalized Build — matches build_id format
    used in evaluation_log.jsonl + posthoc-ranker debug output."""
    canonical = json.dumps(
        {
            "hull_id": build_dict["hull_id"],
            "weapon_assignments": dict(sorted(build_dict["weapon_assignments"].items())),
            "hullmods": sorted(build_dict["hullmods"]),
            "flux_vents": build_dict["flux_vents"],
            "flux_capacitors": build_dict["flux_capacitors"],
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def load_cell(root: Path, cell: str) -> CellRow | None:
    path = root / cell / "honest_eval.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    builds = data["evaluated_builds"]
    if not builds:
        return None
    sorted_by_oracle = sorted(builds, key=lambda b: b["oracle_score"], reverse=True)
    best = sorted_by_oracle[0]
    mean_top_k = sum(b["oracle_score"] for b in builds) / len(builds)
    return CellRow(
        cell=cell,
        n_builds=len(builds),
        mean_top_k_oracle=mean_top_k,
        best_oracle=best["oracle_score"],
        best_oracle_se=best["oracle_se"],
        best_source_value=best["source_value"],
        best_build_hash=build_hash(best["build"]),
        best_source_rank=best["source_rank"],
        best_source_seed_idx=best["source_seed_idx"],
    )


def render_md(rows: list[CellRow]) -> str:
    out: list[str] = []
    out.append("## 3. Honest-evaluator headline (build-quality oracle)\n")
    out.append(
        "Top-1 = the EvaluatedBuild with the highest oracle_score within "
        "each cell (out of top-3 × 3 seeds = 9 candidates per cell + 9 "
        "random-baseline draws).\n"
    )
    out.append(
        "| Cell | Mean top-K oracle | Top-1 oracle (±SE) | Top-1 source α | "
        "Top-1 build hash | Top-1 src(rank,seed) |\n"
        "|---|---|---|---|---|---|"
    )
    sorted_rows = sorted(rows, key=lambda r: r.mean_top_k_oracle, reverse=True)
    for r in sorted_rows:
        out.append(
            f"| {r.cell} | {r.mean_top_k_oracle:+.4f} "
            f"| {r.best_oracle:+.4f} (±{r.best_oracle_se:.4f}) "
            f"| {r.best_source_value:+.4f} "
            f"| `{r.best_build_hash}` "
            f"| (#{r.best_source_rank}, seed{r.best_source_seed_idx}) |"
        )
    out.append("")
    out.append(
        "Cell ranking by mean honest fitness "
        "(production-relevant headline): " + " > ".join(r.cell for r in sorted_rows) + "."
    )
    out.append("")
    out.append("### F1c gate — does the prod config (C2) beat A0/A baselines?")
    out.append("")
    by_cell = {r.cell: r for r in rows}
    c2 = by_cell.get("wave1-c2")
    c0a = by_cell.get("wave1-c0a")
    c0b = by_cell.get("wave1-c0b")
    if c2 and c0a and c0b:
        d_a0 = c2.mean_top_k_oracle - c0a.mean_top_k_oracle
        d_a = c2.mean_top_k_oracle - c0b.mean_top_k_oracle
        out.append(f"- C2 vs C0a (EB+BoxCox vs A0 plain TWFE): Δ = {d_a0:+.4f}")
        out.append(f"- C2 vs C0b (EB+BoxCox vs A scalar-CV):    Δ = {d_a:+.4f}")
        verdict_a0 = "WIN" if d_a0 > 0 else "LOSS"
        verdict_a = "WIN" if d_a > 0 else "LOSS"
        out.append("")
        out.append(
            f"- F1c verdict at point estimate: C2 vs C0a = {verdict_a0}, "
            f"C2 vs C0b = {verdict_a}. (Bootstrap CI bands not computed in "
            f"this scaffold; add via downstream analyzer if Δ is small.)"
        )
    else:
        out.append(
            "- One or more required cells (wave1-c0a, wave1-c0b, wave1-c2) "
            "missing — F1c gate not evaluated."
        )
    out.append("")
    out.append("### Random-baseline existence check")
    out.append("")
    base = by_cell.get("random-baseline")
    if base is not None:
        beats = sum(
            1
            for c in ("wave1-c0a", "wave1-c0b", "wave1-c1", "wave1-c2", "wave1-c3")
            if c in by_cell and by_cell[c].mean_top_k_oracle > base.mean_top_k_oracle
        )
        out.append(f"- Random-baseline mean top-K oracle: {base.mean_top_k_oracle:+.4f}")
        out.append(
            f"- {beats}/5 optimization cells beat the random-feasible baseline. "
            f"If 0/5, the optimization machinery is not extracting signal "
            f"beyond random sampling — flag as an incident."
        )
    else:
        out.append("- random-baseline cell missing — existence check skipped.")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("data/campaigns"))
    p.add_argument("--cells", nargs="+", default=list(WAVE1_CELLS_DEFAULT))
    args = p.parse_args()

    rows: list[CellRow] = []
    missing: list[str] = []
    for cell in args.cells:
        row = load_cell(args.root, cell)
        if row is None:
            missing.append(cell)
        else:
            rows.append(row)
    if not rows:
        print(
            f"ERROR: no honest_eval.json found under {args.root} for any of {args.cells}",
            file=sys.stderr,
        )
        return 1
    if missing:
        print(f"# Note: {len(missing)} cell(s) missing honest_eval.json: {', '.join(missing)}\n")
    print(render_md(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
