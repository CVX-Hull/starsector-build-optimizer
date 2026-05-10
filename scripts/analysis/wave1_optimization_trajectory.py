"""Wave 1 optimization-trajectory analysis — chart + headline producer.

Where the post-hoc-ranker analysis (`wave1_comprehensive_analysis.py`) asks
*"which build is best?"*, this script asks *"how well did the optimizer
search?"*. It reads per-trial JSONL ledgers from
`data/logs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}/
evaluation_log.jsonl` and computes:

  - best-so-far convergence per (cell, seed)
  - sample efficiency at matched trial budgets T ∈ {50, 100, 150, 200}
  - time-to-90 %-of-final per seed
  - cross-seed convergence variance (CV)
  - pruner-rate trajectory by trial-number bucket
  - cache-hit / invalid-spec trajectory
  - unique-builds-per-window (exploration breadth)
  - mean Jaccard distance to prior proposal (exploration locality)

Outputs:

  data/wave1-trajectory/charts/01_best_so_far.png
  data/wave1-trajectory/charts/02_sample_efficiency.png
  data/wave1-trajectory/charts/03_time_to_90.png
  data/wave1-trajectory/charts/04_cross_seed_cv.png
  data/wave1-trajectory/charts/05_pruner_trajectory.png
  data/wave1-trajectory/charts/06_invalid_cache_trajectory.png
  data/wave1-trajectory/charts/07_unique_builds_window.png
  data/wave1-trajectory/charts/08_proposal_distance.png

… and `data/wave1-trajectory/headline_numbers.json` containing the exact
numeric values cited by `docs/reports/2026-05-10-wave1-optimization-
trajectory.md`.

Run: `uv run python scripts/analysis/wave1_optimization_trajectory.py`
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# --- publication-quality matplotlib defaults (aligned with sibling producer) ---
plt.rcParams.update({
    "figure.dpi": 110,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.15,
    "figure.constrained_layout.use": True,
    "axes.prop_cycle": plt.cycler(color=[
        "#006BA4", "#FF800E", "#ABABAB", "#595959", "#5F9ED1",
        "#C85200", "#898989", "#A2C8EC", "#FFBC79", "#CFCFCF",
    ]),
    "axes.grid": True,
    "axes.grid.axis": "y",
    "axes.axisbelow": True,
    "grid.color": "#cccccc",
    "grid.linewidth": 0.6,
    "grid.alpha": 0.7,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.8,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.size": 3.5,
    "ytick.major.size": 3.5,
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "legend.frameon": False,
    "figure.titlesize": 12,
    "figure.titleweight": "bold",
    "image.cmap": "viridis",
})

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

CELLS = ["c0a", "c0b", "c1", "c2", "c3"]
SEEDS = ["0", "1", "2"]
CHECKPOINTS = (50, 100, 150, 200)
WINDOW_W = 50          # exploration-window width (trials) for §7
BUCKET_W = 25          # bucket width for §5/§6 trajectory rates
TIME_TO_FRAC = 0.90    # §4 — fraction of eventual best to detect

CHARTS_DIR = REPO_ROOT / "data" / "wave1-trajectory" / "charts"
HEADLINES_PATH = REPO_ROOT / "data" / "wave1-trajectory" / "headline_numbers.json"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("wave1-trajectory")


# ---------------------------------------------------------------- data load ---


@dataclass(frozen=True)
class TrialRow:
    """One row from an evaluation_log.jsonl, with kind tag."""
    cell: str
    seed: str
    trial_number: int
    kind: str          # "finalized" | "pruned" | "cache_hit" | "invalid_spec"
    raw_fitness: float | None
    fitness: float | None
    build_id: tuple | None
    timestamp: str | None


def _build_id(b: dict) -> tuple:
    """Hash-stable identity matching `posthoc_ranker._BuildId`."""
    return (
        b["hull_id"],
        tuple(sorted(b["weapon_assignments"].items())),
        tuple(sorted(b["hullmods"])),
        int(b["flux_vents"]),
        int(b["flux_capacitors"]),
    )


def _load_cell_seed(cell: str, seed: str) -> list[TrialRow]:
    p = (REPO_ROOT / "data" / "logs" / f"wave1-{cell}" /
         f"hammerhead__early__tpe__seed{seed}" / "evaluation_log.jsonl")
    if not p.exists():
        return []
    out: list[TrialRow] = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("invalid_spec"):
                kind = "invalid_spec"
            elif d.get("cache_hit"):
                kind = "cache_hit"
            elif d.get("pruned"):
                kind = "pruned"
            else:
                kind = "finalized"
            try:
                trial_number = int(d["trial_number"])
            except (KeyError, TypeError):
                continue
            b = d.get("build")
            bid = _build_id(b) if b else None
            out.append(TrialRow(
                cell=cell, seed=seed,
                trial_number=trial_number,
                kind=kind,
                raw_fitness=d.get("raw_fitness"),
                fitness=d.get("fitness"),
                build_id=bid,
                timestamp=d.get("timestamp"),
            ))
    out.sort(key=lambda r: r.trial_number)
    return out


def _load_all() -> dict[tuple[str, str], list[TrialRow]]:
    out: dict[tuple[str, str], list[TrialRow]] = {}
    for cell in CELLS:
        for seed in SEEDS:
            rows = _load_cell_seed(cell, seed)
            if rows:
                out[(cell, seed)] = rows
                log.info(
                    "  loaded %s seed=%s: %d rows (%d finalized, %d pruned, "
                    "%d cache, %d invalid)",
                    cell, seed, len(rows),
                    sum(1 for r in rows if r.kind == "finalized"),
                    sum(1 for r in rows if r.kind == "pruned"),
                    sum(1 for r in rows if r.kind == "cache_hit"),
                    sum(1 for r in rows if r.kind == "invalid_spec"),
                )
    return out


# ----------------------------------------------------------- trajectory ops ---


def _best_so_far(rows: Sequence[TrialRow], field: str = "raw_fitness") -> tuple[np.ndarray, np.ndarray]:
    """Return (trial_numbers, best-so-far). Steps forward at finalized rows;
    pruned / cache / invalid rows do not update the running max but still
    occupy a position on the trial-number axis."""
    if not rows:
        return np.array([]), np.array([])
    xs = []
    ys = []
    cur = float("-inf")
    for r in rows:
        v = getattr(r, field)
        if r.kind == "finalized" and v is not None and not np.isnan(v):
            cur = max(cur, float(v))
        xs.append(r.trial_number)
        ys.append(cur)
    return np.array(xs), np.array(ys)


def _best_at(rows: Sequence[TrialRow], T: int, field: str = "raw_fitness") -> float | None:
    """Best finalized fitness among trials with trial_number ≤ T."""
    best = None
    for r in rows:
        if r.trial_number > T:
            break
        if r.kind == "finalized":
            v = getattr(r, field)
            if v is None or np.isnan(v):
                continue
            best = float(v) if best is None else max(best, float(v))
    return best


def _time_to_target(rows: Sequence[TrialRow], target: float,
                    field: str = "raw_fitness") -> int | None:
    """First trial_number whose finalized best-so-far ≥ target. None if never."""
    cur = float("-inf")
    for r in rows:
        if r.kind == "finalized":
            v = getattr(r, field)
            if v is None or np.isnan(v):
                continue
            cur = max(cur, float(v))
            if cur >= target:
                return r.trial_number
    return None


def _interp_best_at(xs: np.ndarray, ys: np.ndarray, T: int) -> float | None:
    """Last best-so-far value at trial_number ≤ T. None if not reached."""
    if len(xs) == 0:
        return None
    mask = xs <= T
    if not mask.any():
        return None
    val = ys[mask][-1]
    if np.isfinite(val):
        return float(val)
    return None


# ---------------------------------------------------------- chart sections ---


def section_01_best_so_far(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Best-so-far convergence: 5 panels (one per cell), 3 lines per panel."""
    log.info("[01] Best-so-far convergence per (cell, seed)")
    fig, axes = plt.subplots(1, 5, figsize=(20, 4.6), sharey=True)
    out: dict[str, dict] = {}
    for ax_i, (ax, cell) in enumerate(zip(axes, CELLS)):
        ax.grid(True, linewidth=0.6, alpha=0.7)
        seed_finals = {}
        for seed in SEEDS:
            rows = data.get((cell, seed), [])
            xs, ys = _best_so_far(rows, "raw_fitness")
            if len(xs) == 0:
                continue
            ax.plot(xs, ys, label=f"seed {seed}", linewidth=1.4)
            seed_finals[seed] = float(ys[-1]) if len(ys) else None
        ax.set_xlabel("trial number")
        if ax_i == 0:
            ax.set_ylabel(r"best-so-far raw fitness, $\hat{r}^{\max}_{T}$")
        ax.set_title(f"({chr(97 + ax_i)}) {cell}\n"
                     f"final best = "
                     f"{', '.join(f'{seed_finals[s]:.3f}' if s in seed_finals else '—' for s in SEEDS)}")
        ax.legend(loc="lower right", ncol=1)
        out[cell] = {"final_best_per_seed": seed_finals}
    fig.suptitle("Wave 1 — best-so-far raw fitness by trial, per cell × seed")
    fig.savefig(CHARTS_DIR / "01_best_so_far.png")
    plt.close(fig)
    return out


def section_02_sample_efficiency(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Best raw-fitness reached at fixed trial budgets T ∈ CHECKPOINTS."""
    log.info("[02] Sample efficiency at T ∈ %s", CHECKPOINTS)
    out: dict[str, dict] = {}
    table: list[dict] = []
    for cell in CELLS:
        per_T: dict[int, list[float]] = {T: [] for T in CHECKPOINTS}
        for seed in SEEDS:
            rows = data.get((cell, seed), [])
            for T in CHECKPOINTS:
                v = _best_at(rows, T, "raw_fitness")
                if v is not None:
                    per_T[T].append(v)
        out[cell] = {}
        for T in CHECKPOINTS:
            vals = per_T[T]
            if not vals:
                continue
            med = float(np.median(vals))
            lo = float(np.min(vals))
            hi = float(np.max(vals))
            out[cell][f"T={T}"] = {
                "n_seeds": len(vals),
                "median": med, "min": lo, "max": hi,
                "values": [float(v) for v in vals],
            }
            table.append({"cell": cell, "T": T, "median": med, "min": lo, "max": hi, "n": len(vals)})

    # Bar chart with seed-spread error bars
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(CHECKPOINTS))
    bar_w = 0.16
    for ci, cell in enumerate(CELLS):
        meds = []
        errs_lo = []
        errs_hi = []
        for T in CHECKPOINTS:
            entry = out[cell].get(f"T={T}")
            if entry is None:
                meds.append(np.nan); errs_lo.append(0); errs_hi.append(0)
            else:
                meds.append(entry["median"])
                errs_lo.append(entry["median"] - entry["min"])
                errs_hi.append(entry["max"] - entry["median"])
        offsets = (ci - 2) * bar_w
        ax.bar(x + offsets, meds, bar_w, yerr=[errs_lo, errs_hi],
               capsize=2.5, label=cell)
    ax.set_xticks(x)
    ax.set_xticklabels([f"T = {T}" for T in CHECKPOINTS])
    ax.set_xlabel("trial-budget checkpoint")
    ax.set_ylabel(r"best raw fitness reached, $\hat{r}^{\max}_{T}$  (median across seeds)")
    ax.set_title("Sample efficiency at matched trial budgets — per-cell median, "
                 "error bars = seed min/max")
    ax.legend(ncol=5, loc="lower right")
    fig.savefig(CHARTS_DIR / "02_sample_efficiency.png")
    plt.close(fig)
    return {"per_cell": out, "table": table}


def section_03_time_to_target(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Trial number at which best-so-far first reaches TIME_TO_FRAC × eventual best."""
    log.info("[03] Time-to-%.0f%%-of-final per (cell, seed)", TIME_TO_FRAC * 100)
    out: dict[str, dict] = {}
    box_data: list[list[int]] = []
    box_labels: list[str] = []
    for cell in CELLS:
        per_seed = {}
        cell_vals: list[int] = []
        for seed in SEEDS:
            rows = data.get((cell, seed), [])
            xs, ys = _best_so_far(rows, "raw_fitness")
            if len(xs) == 0 or not np.isfinite(ys[-1]):
                continue
            final_best = float(ys[-1])
            target = TIME_TO_FRAC * final_best if final_best > 0 else None
            tt = _time_to_target(rows, target) if target is not None else None
            n_finalized = sum(1 for r in rows if r.kind == "finalized")
            per_seed[seed] = {
                "final_best": final_best,
                "target": target,
                "trial_number": tt,
                "n_finalized": n_finalized,
                "frac_used": (tt / n_finalized) if (tt is not None and n_finalized > 0) else None,
            }
            if tt is not None:
                cell_vals.append(tt)
        out[cell] = per_seed
        if cell_vals:
            box_data.append(cell_vals)
            box_labels.append(cell)

    fig, ax = plt.subplots(figsize=(8.5, 4.6))
    bp = ax.boxplot(box_data, tick_labels=box_labels, widths=0.5,
                    patch_artist=True, medianprops=dict(color="#C85200"))
    for patch in bp["boxes"]:
        patch.set_facecolor("#A2C8EC"); patch.set_edgecolor("#006BA4")
    for ci, vals in enumerate(box_data):
        ax.scatter([ci + 1] * len(vals), vals, color="#595959",
                   s=22, zorder=3, alpha=0.8)
    ax.set_xlabel("cell")
    ax.set_ylabel(r"trials until best-so-far $\geq 0.9 \cdot \hat{r}^{\max}_{\mathrm{final}}$")
    ax.set_title(f"Time to {TIME_TO_FRAC:.0%}-of-final best — per-cell distribution "
                 f"over the {len(SEEDS)} seeds")
    fig.savefig(CHARTS_DIR / "03_time_to_90.png")
    plt.close(fig)
    return out


def section_04_cross_seed_cv(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Coefficient of variation of best-so-far across the 3 seeds, vs trial T."""
    log.info("[04] Cross-seed CV of best-so-far")
    fig, ax = plt.subplots(figsize=(10, 4.8))
    out: dict[str, dict] = {}
    Ts_eval = np.arange(20, 220, 5)
    for cell in CELLS:
        cvs = []
        for T in Ts_eval:
            vals = []
            for seed in SEEDS:
                rows = data.get((cell, seed), [])
                v = _best_at(rows, int(T), "raw_fitness")
                if v is not None and v > 0:
                    vals.append(v)
            if len(vals) >= 2:
                mean = np.mean(vals)
                std = np.std(vals, ddof=1)
                cvs.append(std / mean if mean > 0 else np.nan)
            else:
                cvs.append(np.nan)
        ax.plot(Ts_eval, cvs, label=cell, linewidth=1.6)
        # Headline: CV at T=200 (or last available)
        last_finite = next((c for c in reversed(cvs) if np.isfinite(c)), None)
        out[cell] = {
            "cv_at_T200": cvs[-1] if Ts_eval[-1] == 200 and np.isfinite(cvs[-1]) else None,
            "cv_at_last_finite": last_finite,
        }
    ax.set_xlabel("trial number, T")
    ax.set_ylabel(r"$\mathrm{CV}_T = \sigma(\hat{r}^{\max}_T) / \mu(\hat{r}^{\max}_T)$")
    ax.set_title("Cross-seed convergence variance — lower is more reproducible")
    ax.legend(ncol=5, loc="upper right")
    fig.savefig(CHARTS_DIR / "04_cross_seed_cv.png")
    plt.close(fig)
    return out


def section_05_pruner_trajectory(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Pruner-fire rate by trial-number bucket per cell (pooled over seeds)."""
    log.info("[05] Pruner-rate trajectory per cell")
    fig, ax = plt.subplots(figsize=(10, 4.8))
    out: dict[str, dict] = {}
    for cell in CELLS:
        all_rows: list[TrialRow] = []
        for seed in SEEDS:
            all_rows.extend(data.get((cell, seed), []))
        if not all_rows:
            continue
        max_T = max(r.trial_number for r in all_rows)
        buckets: list[tuple[int, int]] = []
        for lo in range(0, max_T + 1, BUCKET_W):
            hi = lo + BUCKET_W
            in_bucket = [r for r in all_rows if lo <= r.trial_number < hi]
            if not in_bucket:
                continue
            n_total = len(in_bucket)
            n_pruned = sum(1 for r in in_bucket if r.kind == "pruned")
            buckets.append((lo + BUCKET_W // 2, n_pruned, n_total))
        xs = np.array([b[0] for b in buckets])
        rates = np.array([b[1] / b[2] for b in buckets])
        ax.plot(xs, rates, marker="o", markersize=3, label=cell, linewidth=1.4)
        out[cell] = {
            "n_trials": len(all_rows),
            "n_pruned_total": sum(1 for r in all_rows if r.kind == "pruned"),
            "overall_pruner_rate": (
                sum(1 for r in all_rows if r.kind == "pruned") / len(all_rows)),
            "bucket_rates": [(int(b[0]), int(b[1]), int(b[2])) for b in buckets],
        }
    ax.set_xlabel(f"trial-number bucket centre  (width = {BUCKET_W} trials)")
    ax.set_ylabel(r"$N_{\mathrm{pruned}} / N_{\mathrm{total}}$")
    ax.set_title("Pruner-fire rate over time — testing whether TPE learns "
                 "to avoid prunable regions")
    ax.set_ylim(0, max(0.05, ax.get_ylim()[1]))
    ax.legend(ncol=5, loc="upper right")
    fig.savefig(CHARTS_DIR / "05_pruner_trajectory.png")
    plt.close(fig)
    return out


def section_06_invalid_cache_trajectory(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Cache-hit + invalid-spec rate per bucket per cell (pooled over seeds)."""
    log.info("[06] Cache-hit + invalid-spec trajectory")
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.8), sharey=True)
    ax_cache, ax_inv = axes
    out: dict[str, dict] = {}
    for cell in CELLS:
        all_rows: list[TrialRow] = []
        for seed in SEEDS:
            all_rows.extend(data.get((cell, seed), []))
        if not all_rows:
            continue
        max_T = max(r.trial_number for r in all_rows)
        cache_xs, cache_rates = [], []
        inv_xs, inv_rates = [], []
        for lo in range(0, max_T + 1, BUCKET_W):
            hi = lo + BUCKET_W
            in_bucket = [r for r in all_rows if lo <= r.trial_number < hi]
            if not in_bucket:
                continue
            n_total = len(in_bucket)
            n_cache = sum(1 for r in in_bucket if r.kind == "cache_hit")
            n_inv = sum(1 for r in in_bucket if r.kind == "invalid_spec")
            cache_xs.append(lo + BUCKET_W // 2)
            cache_rates.append(n_cache / n_total)
            inv_xs.append(lo + BUCKET_W // 2)
            inv_rates.append(n_inv / n_total)
        ax_cache.plot(cache_xs, cache_rates, marker="o", markersize=3,
                      label=cell, linewidth=1.4)
        ax_inv.plot(inv_xs, inv_rates, marker="o", markersize=3,
                    label=cell, linewidth=1.4)
        out[cell] = {
            "cache_hit_rate": sum(1 for r in all_rows if r.kind == "cache_hit") / len(all_rows),
            "invalid_spec_rate": sum(1 for r in all_rows if r.kind == "invalid_spec") / len(all_rows),
            "n_total": len(all_rows),
        }
    ax_cache.set_xlabel("trial-number bucket centre")
    ax_cache.set_ylabel(r"$N_{\mathrm{cache\_hit}} / N_{\mathrm{total}}$")
    ax_cache.set_title("(a) cache-hit rate by bucket")
    ax_cache.legend(ncol=5, loc="upper left")
    ax_inv.set_xlabel("trial-number bucket centre")
    ax_inv.set_title("(b) invalid-spec rate by bucket")
    fig.suptitle("Optimizer drift diagnostics — TPE re-proposing already-evaluated "
                 "or repair-failing builds")
    fig.savefig(CHARTS_DIR / "06_invalid_cache_trajectory.png")
    plt.close(fig)
    return out


def section_07_unique_builds_window(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Distinct build_ids in a sliding window of W=50 trials, per (cell, seed)."""
    log.info("[07] Unique-builds-per-%d-trial window", WINDOW_W)
    fig, ax = plt.subplots(figsize=(10, 4.8))
    out: dict[str, dict] = {}
    for cell in CELLS:
        ys_per_seed: list[np.ndarray] = []
        xs_per_seed: list[np.ndarray] = []
        n_uniq_per_seed: list[int] = []
        for seed in SEEDS:
            rows = data.get((cell, seed), [])
            if not rows:
                continue
            n_uniq_per_seed.append(len({r.build_id for r in rows
                                        if r.build_id is not None}))
            max_T = max(r.trial_number for r in rows)
            xs = []
            ys = []
            for T in range(WINDOW_W, max_T + 1, 5):
                window = [r for r in rows
                          if T - WINDOW_W < r.trial_number <= T
                          and r.build_id is not None]
                if not window:
                    continue
                xs.append(T)
                ys.append(len({r.build_id for r in window}))
            xs_per_seed.append(np.array(xs))
            ys_per_seed.append(np.array(ys))
        # Plot mean across seeds (require all 3)
        if len(xs_per_seed) == 3:
            T_min = max(xs[0] for xs in xs_per_seed)
            T_max = min(xs[-1] for xs in xs_per_seed)
            grid = np.arange(T_min, T_max + 1, 5)
            stacked = []
            for xs, ys in zip(xs_per_seed, ys_per_seed):
                stacked.append(np.interp(grid, xs, ys))
            mean_curve = np.mean(np.array(stacked), axis=0)
            ax.plot(grid, mean_curve, label=cell, linewidth=1.6)
        out[cell] = {
            "unique_builds_per_seed": n_uniq_per_seed,
            "mean_unique_builds_per_seed": (
                float(np.mean(n_uniq_per_seed)) if n_uniq_per_seed else None),
        }
    ax.set_xlabel("trial number, T")
    ax.set_ylabel(f"distinct build_ids in window (T − {WINDOW_W}, T]")
    ax.set_title(f"Local search diversity — distinct builds per {WINDOW_W}-trial "
                 f"window (mean over seeds)")
    ax.legend(ncol=5, loc="lower right")
    fig.savefig(CHARTS_DIR / "07_unique_builds_window.png")
    plt.close(fig)
    return out


def _jaccard_dist(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return 1.0 - len(a & b) / max(len(a | b), 1)


def section_08_proposal_distance(data: dict[tuple[str, str], list[TrialRow]]) -> dict:
    """Mean Jaccard distance between consecutive proposals' hullmod-sets,
    rolling over a 25-trial window per (cell, seed)."""
    log.info("[08] Proposal-to-prior Jaccard distance")
    fig, ax = plt.subplots(figsize=(10, 4.8))
    out: dict[str, dict] = {}
    rolling = 25
    for cell in CELLS:
        all_curves_xs: list[np.ndarray] = []
        all_curves_ys: list[np.ndarray] = []
        per_seed_means: list[float] = []
        for seed in SEEDS:
            rows = [r for r in data.get((cell, seed), []) if r.build_id is not None]
            if len(rows) < 2:
                continue
            dists: list[tuple[int, float]] = []
            prev_hm: set | None = None
            for r in rows:
                hm = set(r.build_id[2])  # hullmods tuple at index 2
                if prev_hm is not None:
                    dists.append((r.trial_number, _jaccard_dist(prev_hm, hm)))
                prev_hm = hm
            if not dists:
                continue
            xs = np.array([d[0] for d in dists])
            ys = np.array([d[1] for d in dists])
            # Rolling mean over 25 consecutive points (uniform window)
            kernel = np.ones(rolling) / rolling
            if len(ys) >= rolling:
                roll = np.convolve(ys, kernel, mode="valid")
                roll_xs = xs[rolling - 1:]
                all_curves_xs.append(roll_xs)
                all_curves_ys.append(roll)
            per_seed_means.append(float(np.mean(ys)))
        if all_curves_xs:
            T_min = max(xs[0] for xs in all_curves_xs)
            T_max = min(xs[-1] for xs in all_curves_xs)
            if T_max > T_min:
                grid = np.arange(T_min, T_max + 1, 5)
                stacked = [np.interp(grid, xs, ys)
                           for xs, ys in zip(all_curves_xs, all_curves_ys)]
                ax.plot(grid, np.mean(stacked, axis=0), label=cell, linewidth=1.6)
        out[cell] = {
            "mean_proposal_distance_per_seed": per_seed_means,
            "mean_proposal_distance": (
                float(np.mean(per_seed_means)) if per_seed_means else None),
        }
    ax.set_xlabel("trial number, T")
    ax.set_ylabel(r"$J_{\mathrm{dist}}(\mathrm{HM}_t, \mathrm{HM}_{t-1})$  "
                  r"(rolling mean, 25 trials)")
    ax.set_title("Proposal locality — Jaccard distance between consecutive "
                 "proposals' hullmod sets")
    ax.legend(ncol=5, loc="lower left")
    ax.set_ylim(0, 1)
    fig.savefig(CHARTS_DIR / "08_proposal_distance.png")
    plt.close(fig)
    return out


# -------------------------------------------------------------- top-level ---


def main() -> None:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Loading per-trial JSONL ledgers …")
    data = _load_all()
    n_studies = len(data)
    n_rows = sum(len(rs) for rs in data.values())
    log.info("Loaded %d studies, %d total trial rows", n_studies, n_rows)

    out: dict[str, object] = {
        "n_studies": n_studies,
        "n_total_trial_rows": n_rows,
        "per_study_row_counts": {f"{c}_seed{s}": len(rs)
                                 for (c, s), rs in data.items()},
        "checkpoints_T": list(CHECKPOINTS),
        "window_W": WINDOW_W,
        "bucket_W": BUCKET_W,
        "time_to_frac": TIME_TO_FRAC,
    }
    out["s01_best_so_far"] = section_01_best_so_far(data)
    out["s02_sample_efficiency"] = section_02_sample_efficiency(data)
    out["s03_time_to_target"] = section_03_time_to_target(data)
    out["s04_cross_seed_cv"] = section_04_cross_seed_cv(data)
    out["s05_pruner_trajectory"] = section_05_pruner_trajectory(data)
    out["s06_invalid_cache_trajectory"] = section_06_invalid_cache_trajectory(data)
    out["s07_unique_builds_window"] = section_07_unique_builds_window(data)
    out["s08_proposal_distance"] = section_08_proposal_distance(data)

    HEADLINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEADLINES_PATH.write_text(json.dumps(out, indent=2, default=str))
    log.info("Wrote %s and %d charts to %s",
             HEADLINES_PATH.relative_to(REPO_ROOT),
             len(list(CHARTS_DIR.glob("*.png"))),
             CHARTS_DIR.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
