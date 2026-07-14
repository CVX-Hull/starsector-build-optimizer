"""Chart producer for the prequential replay report (spec 31 §"Prequential
Replay Ablation").

Reads the replay artifact JSON and writes report-companion outputs to
data/phase7-prequential-replay/: charts (git-tracked) and
headline_numbers.json. Deterministic: bootstrap RNG is seeded from the
artifact's own config echo.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

_REPLAY_PATH = Path(__file__).with_name("phase7_prequential_replay.py")
_REPLAY_SPEC = importlib.util.spec_from_file_location("_phase7_prequential_replay", _REPLAY_PATH)
assert _REPLAY_SPEC is not None and _REPLAY_SPEC.loader is not None
replay = importlib.util.module_from_spec(_REPLAY_SPEC)
sys.modules.setdefault("_phase7_prequential_replay", replay)
if sys.modules["_phase7_prequential_replay"] is replay:
    _REPLAY_SPEC.loader.exec_module(replay)
else:  # pragma: no cover - test harness loaded it first
    replay = sys.modules["_phase7_prequential_replay"]

plt.rcParams.update(
    {
        "savefig.dpi": 200,
        "figure.constrained_layout.use": True,
        "axes.prop_cycle": plt.cycler(
            color=plt.style.library["tableau-colorblind10"]["axes.prop_cycle"].by_key()["color"]
        ),
        "axes.axisbelow": True,
        "axes.grid": True,
        "grid.color": "0.9",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.8,
        "legend.frameon": False,
    }
)

FIDELITY_ARMS = (
    ("catboost_regressor", "CatBoost (default params)"),
    ("random_forest_tuned", "RF learned family (default params)"),
    ("random_forest", "RF comparator (200 trees)"),
    ("opponent_mean", "opponent-mean null (build-blind)"),
    ("twfe_additive", "TWFE-additive comparator"),
)
GAP_MODE = replay.HEADLINE_GAP_MODE
ARM_X_OFFSET = 0.06  # horizontal jitter between arms at one bucket tick
PAIR_X_OFFSET = 0.08  # horizontal jitter between the surrogate/null pair


def _bucket_labels(payload: Mapping[str, Any]) -> tuple[str, ...]:
    buckets = payload["config"]["horizon_buckets"]
    return (*(f"{lo}-{hi}" for lo, hi in buckets), replay.TAIL_BUCKET_LABEL)


def _pooled_bucket_stat(
    cells: Mapping[str, Any],
    arm: str,
    bucket: str,
    key: str,
    iterations: int,
    seed: int,
) -> dict[str, float | int | None]:
    """Cell means of a fidelity metric, bootstrapped by the replay module's
    campaign-stratified cell bootstrap (single owner of that statistic)."""
    per_cell: dict[str, float] = {}
    support = 0
    for cell, payload in cells.items():
        records = payload["fidelity"][GAP_MODE].get(arm, {}).get(bucket, [])
        values = [r[key] for r in records if r[key] is not None]
        support += len(values)
        if values:
            per_cell[cell] = float(np.mean(values))
    return {**replay.stratified_cell_bootstrap(per_cell, iterations, seed), "support": support}


def balanced_bucket_means(
    cells: Mapping[str, Any], arms: Sequence[str], buckets: Sequence[str]
) -> dict[str, dict[str, float | int | None]]:
    """T2 cell means restricted to cutoffs that populate every bucket.

    The unrestricted per-bucket pooled means confound temporal distance with
    cutoff position (deep buckets exist only at early cutoffs); this balanced
    panel is the support-controlled comparison the report's drift reading
    rests on.
    """
    out: dict[str, dict[str, float | int | None]] = {}
    for arm in arms:
        per_bucket_cell_means: dict[str, list[float]] = {b: [] for b in buckets}
        n_cells = 0
        for payload in cells.values():
            fidelity = payload["fidelity"][GAP_MODE].get(arm, {})
            by_cutoff: dict[int, dict[str, float]] = {}
            for bucket in buckets:
                for record in fidelity.get(bucket, []):
                    if record["t2_spearman"] is not None:
                        by_cutoff.setdefault(record["cutoff"], {})[bucket] = record["t2_spearman"]
            balanced = {c: v for c, v in by_cutoff.items() if len(v) == len(buckets)}
            if not balanced:
                continue
            n_cells += 1
            for bucket in buckets:
                per_bucket_cell_means[bucket].append(
                    float(np.mean([v[bucket] for v in balanced.values()]))
                )
        out[arm] = {
            **{
                bucket: (float(np.mean(values)) if values else None)
                for bucket, values in per_bucket_cell_means.items()
            },
            "n_cells": n_cells,
        }
    return out


def chart_t2_drift(payload: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    cells = payload["cells"]
    iterations = payload["config"]["bootstrap_iterations"]
    seed = payload["config"]["bootstrap_seed"]
    buckets = _bucket_labels(payload)
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    x = np.arange(len(buckets))
    stats_out: dict[str, Any] = {}
    for offset, (arm, label) in enumerate(FIDELITY_ARMS):
        stats = [
            _pooled_bucket_stat(cells, arm, b, "t2_spearman", iterations, seed) for b in buckets
        ]
        stats_out[arm] = dict(zip(buckets, stats, strict=True))
        xs = x + (offset - len(FIDELITY_ARMS) / 2) * ARM_X_OFFSET
        means = np.asarray([s["mean"] for s in stats], dtype=float)
        ci_low = np.asarray([s["ci_low"] for s in stats], dtype=float)
        ci_high = np.asarray([s["ci_high"] for s in stats], dtype=float)
        ax.errorbar(
            xs,
            means,
            yerr=[means - ci_low, ci_high - means],
            marker="o",
            capsize=3,
            label=label,
        )
    ax.axhline(0.0, color="0.4", linewidth=0.8, linestyle="--")
    tick_labels = [f"{b} ahead" if b != replay.TAIL_BUCKET_LABEL else "tail (40+)" for b in buckets]
    ax.set_xticks(x, tick_labels)
    ax.set_xlabel("horizon bucket (trials ahead of the training cutoff)")
    ax.set_ylabel(r"T2 opponent-adjusted Spearman $\rho$ (dimensionless)")
    ax.set_title(
        "Opponent-adjusted fidelity of future proposals by temporal distance\n"
        f"(measured in-flight gap; {len(cells)}-cell means, stratified cell bootstrap)"
    )
    ax.legend(ncols=1, fontsize=8)
    fig.savefig(out_dir / "01_t2_drift.png")
    plt.close(fig)
    return stats_out


def chart_qstar(payload: Mapping[str, Any], out_dir: Path) -> None:
    headline = payload["aggregates"]["headline"]
    cells = sorted(headline["per_cell_q_star"])
    surrogate = [headline["per_cell_q_star"][c] for c in cells]
    null = [headline["null_per_cell_q_star"][c] for c in cells]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    x = np.arange(len(cells))
    ax.scatter(x - PAIR_X_OFFSET, surrogate, marker="o", label="CatBoost gate")
    ax.scatter(x + PAIR_X_OFFSET, null, marker="s", label="opponent-mean null gate")
    ax.axhline(headline["median_q_star"], color="0.4", linewidth=0.8, linestyle="--")
    ax.set_xticks(x, [c.replace("wave1-", "") for c in cells], rotation=45, ha="right")
    ax.set_xlabel("replay cell (campaign:seed)")
    ax.set_ylabel(r"$q^{*}$: max skip fraction at zero realized top-3 regret")
    ax.set_title(
        "Per-cell zero-regret skip fraction, surrogate gate vs build-blind null\n"
        "(measured in-flight gap; dashed line = surrogate median)"
    )
    ax.legend()
    fig.savefig(out_dir / "02_gating_qstar.png")
    plt.close(fig)


def _headline_numbers(payload: Mapping[str, Any], t2_stats: Mapping[str, Any]) -> dict[str, Any]:
    agg = payload["aggregates"]
    cells = payload["cells"]
    saved_fracs = []
    for cell, q in agg["headline"]["per_cell_q_star"].items():
        if q > 0:
            row = cells[cell]["gating"][GAP_MODE]["catboost_regressor"][str(q)]
            saved_fracs.append(row["rows_saved"] / row["rows_total"])
    return {
        "headline_median_q_star": agg["headline"]["median_q_star"],
        "null_median_q_star": agg["headline"]["null_median_q_star"],
        "median_rows_saved_fraction_at_q_star": (
            float(np.median(saved_fracs)) if saved_fracs else None
        ),
        "t1_spearman_adjacent_catboost": agg["fidelity_t1_spearman"][GAP_MODE][
            "catboost_regressor"
        ]["0-10"],
        "t1_spearman_adjacent_opponent_mean": agg["fidelity_t1_spearman"][GAP_MODE][
            "opponent_mean"
        ]["0-10"],
        "t2_drift_catboost": t2_stats["catboost_regressor"],
        "oracle_pairwise_concordance": agg["oracle_recovery"]["pairwise_concordance"],
        "inflight_gap_trials": payload["inflight_gap_trials"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prequential replay report charts.")
    parser.add_argument("artifact", type=Path, help="Replay artifact JSON (run_replay output).")
    parser.add_argument("--out-dir", type=Path, default=Path("data/phase7-prequential-replay"))
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.artifact.read_text())
    charts_dir = args.out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    t2_stats = chart_t2_drift(payload, charts_dir)
    chart_qstar(payload, charts_dir)
    numbers = _headline_numbers(payload, t2_stats)
    numbers["t2_balanced_panel"] = balanced_bucket_means(
        payload["cells"],
        [arm for arm, _ in FIDELITY_ARMS],
        _bucket_labels(payload),
    )
    (args.out_dir / "headline_numbers.json").write_text(
        f"{json.dumps(numbers, indent=2, sort_keys=True)}\n"
    )
    print(f"wrote {charts_dir} and {args.out_dir / 'headline_numbers.json'}")


if __name__ == "__main__":
    main()
