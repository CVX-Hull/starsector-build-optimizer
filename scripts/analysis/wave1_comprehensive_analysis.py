"""Wave 1 comprehensive post-hoc analysis — chart + numerical-headline producer.

Reads `data/logs/wave1-{c0a,c0b,c1,c2,c3}/hammerhead__early__tpe__seed{0,1,2}/
evaluation_log.jsonl`, runs the four ranker estimators in
`src.starsector_optimizer.posthoc_ranker`, plus a bundle of confounding /
saturation / coverage diagnostics, and writes:

  data/wave1-comprehensive/charts/01_top_k_agreement_per_cell.png
  data/wave1-comprehensive/charts/02_top_k_agreement_pooled.png
  data/wave1-comprehensive/charts/03_alpha_distribution_per_cell.png
  data/wave1-comprehensive/charts/04_alpha_eb_shrinkage_scatter.png
  data/wave1-comprehensive/charts/05_boxcox_saturation_per_cell.png
  data/wave1-comprehensive/charts/06_pruner_rate_per_cell.png
  data/wave1-comprehensive/charts/07_confounding_heatmap_seed0.png
  data/wave1-comprehensive/charts/08_pooling_rank_stability.png
  data/wave1-comprehensive/charts/09_bt_vs_twfe_eb_alpha.png
  data/wave1-comprehensive/charts/10_search_coverage.png

… and dumps a `data/wave1-comprehensive/headline_numbers.json` with the
exact numeric values the report (`docs/reports/2026-05-10-wave1-
comprehensive-analysis.md`) cites.

Sections (each self-contained, returns a dict, writes a chart):

  section_01_topk_per_cell       — per-cell 4×4 method-agreement Jaccard heatmaps
  section_02_topk_pooled         — pooled-across-cells agreement matrix + ρ
  section_03_alpha_distribution  — TWFE α̂ violin+box per cell
  section_04_eb_shrinkage_scatter — α̂ vs α̂_EB scatter coloured by n_matches
  section_05_boxcox_saturation   — % trials with eb_fitness ≥ 0.99 per cell
  section_06_pruner_rate         — pruner rate per (cell, seed)
  section_07_confounding_heatmap — build × opponent matchup-count heatmap
  section_08_pooling_stability   — per-seed top-10 vs within-cell-pooled top-10
  section_09_bt_vs_twfe_alpha    — Bradley-Terry α vs TWFE+EB α scatter
  section_10_search_coverage     — distinct builds per cell + cumulative curve
  section_11_f1c_gate            — F1c bootstrap CI on Δ(top-3 mean α̂_EB)
  section_12_eb_shrinkage_diag   — (α̂ − α̂_EB)/σ(α̂) histograms (Stein QC)
  section_13_pruner_boxcox       — pruner × Box-Cox saturation cross-tab

Reuses module APIs — does not re-implement TWFE/EB/BT.

Run: `uv run python scripts/analysis/wave1_comprehensive_analysis.py`
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from collections.abc import Sequence

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np

# --- publication-quality matplotlib defaults ----------------------------------
# Applied once at import. All sections inherit these. The principles:
#   - 200 dpi PNG is the project-wide standard for empirical-report charts.
#   - Tableau-Colorblind-10 is the default cycle (8/10 colorblind-safe + grey).
#   - Constrained layout > bbox_inches="tight" — handles colorbars, suptitles,
#     and cross-axis legends correctly without manual margin tweaking.
#   - Grids on by default; light grey, behind data, never on top.
#   - Spines minimised: top/right off, left/bottom thin.
#   - Titles never include trailing punctuation. Axes always have units.
plt.rcParams.update({
    "figure.dpi": 110,            # screen preview
    "savefig.dpi": 200,           # production output
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

from starsector_optimizer.posthoc_ranker import (
    RankedBuild,
    TrialRecord,
    _build_score_matrix,
    load_records,
    rank_bradley_terry,
    rank_raw_mean,
    rank_twfe,
    rank_twfe_eb,
    spearman_rho,
    topk_overlap,
)

# ----------------------------------------------------------- configuration ---

CELLS = ["c0a", "c0b", "c1", "c2", "c3"]
SEEDS = ["0", "1", "2"]
METHOD_FNS = {
    "raw_mean": rank_raw_mean,
    "twfe": rank_twfe,
    "twfe_eb": rank_twfe_eb,
    "bradley_terry": rank_bradley_terry,
}
METHOD_ORDER = ["raw_mean", "twfe", "twfe_eb", "bradley_terry"]
SAT_THRESHOLD = 0.99            # eb_fitness saturation cutoff
SAT_FAIL_FRAC = 0.01            # F2a doc-gate trigger (1 %)
F1C_TARGET_DELTA = 0.02         # phase5d Δρ doc-gate
BOOTSTRAP_ITERS = 5000
BOOTSTRAP_SEED = 0xC0DE

CHARTS_DIR = REPO_ROOT / "data" / "wave1-comprehensive" / "charts"
HEADLINES_PATH = REPO_ROOT / "data" / "wave1-comprehensive" / "headline_numbers.json"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("wave1-analysis")


# ---------------------------------------------------------------- helpers ---


def _cell_log_paths(cell: str) -> list[Path]:
    return sorted((REPO_ROOT / "data" / "logs" / f"wave1-{cell}").glob(
        "hammerhead__early__tpe__seed*/evaluation_log.jsonl"
    ))


def _seed_log_paths(cell: str, seed: str) -> list[Path]:
    p = (REPO_ROOT / "data" / "logs" / f"wave1-{cell}" /
         f"hammerhead__early__tpe__seed{seed}" / "evaluation_log.jsonl")
    return [p] if p.exists() else []


def _all_logs() -> list[Path]:
    return sorted((REPO_ROOT / "data" / "logs").glob(
        "wave1-*/hammerhead__early__tpe__seed*/evaluation_log.jsonl"
    ))


def _jaccard(a: Sequence[RankedBuild], b: Sequence[RankedBuild]) -> float:
    """Jaccard similarity of build_id sets (intersection/union)."""
    sa = {r.build_id for r in a}
    sb = {r.build_id for r in b}
    if not sa and not sb:
        return float("nan")
    return len(sa & sb) / max(len(sa | sb), 1)


def _iter_log_rows(paths: Sequence[Path]):
    """Yield (cell, seed, dict) for every JSONL row including pruned/cache_hit."""
    for fp in paths:
        cell = fp.parent.parent.name.removeprefix("wave1-")
        seed = fp.parent.name.rsplit("__seed", 1)[-1]
        with fp.open() as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                yield cell, seed, json.loads(stripped)


# -------------------------------------------------------------- section 1 ---


def section_01_topk_per_cell(k: int = 5) -> dict:
    """5-panel grid of per-cell 4×4 Jaccard agreement matrices (top-K)."""
    log.info("[01] Per-cell top-%d agreement (Jaccard)", k)
    fig, axes = plt.subplots(1, 5, figsize=(18, 4.6),
                             sharey=True)
    out: dict[str, dict] = {}
    im = None
    for ax_i, (ax, cell) in enumerate(zip(axes, CELLS)):
        ax.grid(False)
        records = load_records(_cell_log_paths(cell))
        rankings = {n: METHOD_FNS[n](records, k=k) for n in METHOD_ORDER}
        mat = np.zeros((4, 4))
        for i, a in enumerate(METHOD_ORDER):
            for j, b in enumerate(METHOD_ORDER):
                mat[i, j] = _jaccard(rankings[a], rankings[b])
        im = ax.imshow(mat, vmin=0.0, vmax=1.0, cmap="viridis")
        ax.set_xticks(range(4))
        ax.set_yticks(range(4))
        ax.set_xticklabels(METHOD_ORDER, rotation=40, ha="right")
        if ax_i == 0:
            ax.set_yticklabels(METHOD_ORDER)
        ax.set_title(f"({chr(97 + ax_i)}) {cell}\nn_trials = {len(records)}")
        for i in range(4):
            for j in range(4):
                ax.text(j, i, f"{mat[i, j]:.2f}", ha="center", va="center",
                        color="white" if mat[i, j] < 0.5 else "black",
                        fontsize=9)
        out[cell] = {"matrix": mat.tolist(), "n_trials": len(records)}
    fig.suptitle(f"Top-{k} Jaccard agreement between rankers, per cell")
    fig.colorbar(im, ax=axes.tolist(), shrink=0.85,
                 label=f"Jaccard similarity J(top-{k}_a, top-{k}_b)")
    fig.savefig(CHARTS_DIR / "01_top_k_agreement_per_cell.png")
    plt.close(fig)
    return {"k": k, "per_cell": out}


# -------------------------------------------------------------- section 2 ---


def section_02_topk_pooled(ks: tuple[int, ...] = (3, 5, 10)) -> dict:
    """Pooled-across-all-15-studies 4×4 agreement matrix at K ∈ {3,5,10}.

    Saves the K=5 panel as the headline chart; full {3,5,10} numbers live
    in the returned dict.
    """
    log.info("[02] Pooled top-K agreement (all 15 studies)")
    records = load_records(_all_logs())
    log.info("    pooled records=%d, total matchups=%d",
             len(records), sum(len(r.matches) for r in records))
    out: dict[str, dict] = {}
    for k in ks:
        rankings = {n: METHOD_FNS[n](records, k=k) for n in METHOD_ORDER}
        mat_jac = np.zeros((4, 4))
        mat_ov = np.zeros((4, 4), dtype=int)
        mat_rho = np.zeros((4, 4))
        for i, a in enumerate(METHOD_ORDER):
            for j, b in enumerate(METHOD_ORDER):
                mat_jac[i, j] = _jaccard(rankings[a], rankings[b])
                mat_ov[i, j] = topk_overlap(rankings[a], rankings[b])
                mat_rho[i, j] = spearman_rho(rankings[a], rankings[b])
        out[f"k={k}"] = {
            "jaccard": mat_jac.tolist(),
            "overlap": mat_ov.tolist(),
            "spearman": [[None if np.isnan(v) else v for v in row]
                         for row in mat_rho.tolist()],
            "top_builds": {n: [r.build_id.short for r in rankings[n]]
                           for n in METHOD_ORDER},
        }

    # Spearman ρ across ALL builds (not just top-K) — more stable diagnostic.
    rankings_full = {n: METHOD_FNS[n](records, k=10**6) for n in METHOD_ORDER}
    mat_rho_full = np.full((4, 4), np.nan)
    for i, a in enumerate(METHOD_ORDER):
        for j, b in enumerate(METHOD_ORDER):
            mat_rho_full[i, j] = spearman_rho(
                rankings_full[a], rankings_full[b]
            )
    out["full_spearman"] = [[None if np.isnan(v) else v for v in row]
                            for row in mat_rho_full.tolist()]

    # Headline chart: K=5 Jaccard.
    k_main = 5
    mat = np.array(out[f"k={k_main}"]["jaccard"])
    mat_ov_main = np.array(out[f"k={k_main}"]["overlap"])
    fig, ax = plt.subplots(1, 1, figsize=(7.5, 6.4))
    ax.grid(False)
    im = ax.imshow(mat, vmin=0.0, vmax=1.0, cmap="viridis")
    ax.set_xticks(range(4))
    ax.set_yticks(range(4))
    ax.set_xticklabels(METHOD_ORDER, rotation=30, ha="right")
    ax.set_yticklabels(METHOD_ORDER)
    for i in range(4):
        for j in range(4):
            ax.text(j, i,
                    f"J = {mat[i, j]:.2f}\novl = {mat_ov_main[i, j]}",
                    ha="center", va="center",
                    color="white" if mat[i, j] < 0.5 else "black",
                    fontsize=10)
    ax.set_title(
        f"Pooled top-{k_main} ranker agreement\n"
        f"15 studies · {len(records):,} builds · "
        f"{sum(len(r.matches) for r in records):,} matchups"
    )
    fig.colorbar(im, ax=ax,
                 label=f"Jaccard similarity J(top-{k_main}_a, top-{k_main}_b)")
    fig.savefig(CHARTS_DIR / "02_top_k_agreement_pooled.png")
    plt.close(fig)
    return {
        "n_trials_pooled": len(records),
        "n_matchups_pooled": sum(len(r.matches) for r in records),
        **out,
    }


# -------------------------------------------------------------- section 3 ---


def section_03_alpha_distribution() -> dict:
    """TWFE α̂ violin/box per cell."""
    log.info("[03] α̂ distribution per cell")
    fig, ax = plt.subplots(1, 1, figsize=(10, 5.2))
    out: dict[str, dict] = {}
    data = []
    labels = []
    for cell in CELLS:
        records = load_records(_cell_log_paths(cell))
        ranked = rank_twfe(records, k=10**6)
        alphas = np.asarray([r.score for r in ranked])
        data.append(alphas)
        labels.append(f"{cell}\nn = {len(alphas)}")
        out[cell] = {
            "n_builds": len(alphas),
            "mean": float(alphas.mean()),
            "std": float(alphas.std(ddof=1)) if len(alphas) > 1 else float("nan"),
            "min": float(alphas.min()) if len(alphas) else float("nan"),
            "max": float(alphas.max()) if len(alphas) else float("nan"),
            "p95": float(np.percentile(alphas, 95)) if len(alphas) else float("nan"),
            "p05": float(np.percentile(alphas, 5)) if len(alphas) else float("nan"),
        }
    parts = ax.violinplot(data, showmeans=False, showmedians=False,
                          showextrema=False)
    palette = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for pc, color in zip(parts["bodies"], palette):
        pc.set_alpha(0.45)
        pc.set_facecolor(color)
        pc.set_edgecolor("black")
        pc.set_linewidth(0.5)
    ax.boxplot(data, widths=0.22, patch_artist=False, showfliers=False,
               medianprops={"color": "black", "linewidth": 1.4})
    means = [float(d.mean()) for d in data]
    ax.scatter(range(1, len(CELLS) + 1), means, marker="D",
               color="white", edgecolor="black", zorder=5, s=24,
               label="mean")
    ax.set_xticks(range(1, len(CELLS) + 1))
    ax.set_xticklabels(labels)
    ax.set_ylabel(r"TWFE estimate $\hat{\alpha}_i$  "
                  r"(residual hp-differential, dimensionless)")
    ax.set_xlabel("cell (configuration)")
    ax.axhline(0.0, color="grey", lw=0.7, ls="--", label=r"$\hat{\alpha} = 0$")
    ax.set_title(
        r"Distribution of build-quality residual $\hat{\alpha}_i$ per cell  "
        r"(violin + box; 3 seeds pooled within cell)"
    )
    ax.legend(loc="upper left")
    fig.savefig(CHARTS_DIR / "03_alpha_distribution_per_cell.png")
    plt.close(fig)
    return out


# -------------------------------------------------------------- section 4 ---


def section_04_eb_shrinkage_scatter() -> dict:
    """α̂ vs α̂_EB per cell, coloured by n_matches per build."""
    log.info("[04] α̂ vs α̂_EB scatter per cell")
    fig, axes = plt.subplots(1, 5, figsize=(18, 4.6), sharey=True)
    out: dict[str, dict] = {}
    sc = None
    for ax_i, (ax, cell) in enumerate(zip(axes, CELLS)):
        records = load_records(_cell_log_paths(cell))
        ranked_twfe = {r.build_id: r for r in rank_twfe(records, k=10**6)}
        ranked_eb = rank_twfe_eb(records, k=10**6)
        x, y, n = [], [], []
        for r in ranked_eb:
            tw = ranked_twfe.get(r.build_id)
            if tw is None:
                continue
            x.append(tw.score)
            y.append(r.score)
            n.append(r.n_matches)
        x = np.asarray(x)
        y = np.asarray(y)
        n = np.asarray(n)
        sc = ax.scatter(x, y, c=n, cmap="plasma", s=18, alpha=0.7,
                        edgecolors="none")
        lo, hi = float(min(x.min(), y.min())), float(max(x.max(), y.max()))
        ax.plot([lo, hi], [lo, hi], color="black", ls="--", lw=0.8,
                label="y = x  (no shrinkage)")
        ax.axvline(0, color="grey", lw=0.5, ls=":")
        ax.axhline(0, color="grey", lw=0.5, ls=":")
        if x.std() > 0:
            slope = float(np.polyfit(x, y, 1)[0])
        else:
            slope = float("nan")
        ax.set_title(f"({chr(97 + ax_i)}) {cell}\n"
                     fr"shrinkage slope $b$ = {slope:.3f}")
        ax.set_xlabel(r"$\hat{\alpha}_i$  (TWFE)")
        if ax_i == 0:
            ax.legend(loc="lower right")
        out[cell] = {
            "shrinkage_slope": slope,
            "n_builds": len(x),
            "mean_alpha_twfe": float(x.mean()) if len(x) else float("nan"),
            "mean_alpha_eb": float(y.mean()) if len(y) else float("nan"),
        }
    axes[0].set_ylabel(r"$\hat{\alpha}^{\mathrm{EB}}_i$  (TWFE + Empirical Bayes)")
    fig.colorbar(sc, ax=axes.tolist(), shrink=0.85,
                 label=r"$n_i$  (matchups per build)")
    fig.suptitle(
        r"Empirical-Bayes shrinkage of $\hat{\alpha}_i$ — "
        r"slope $b = \sigma^2_\alpha / (\sigma^2_\alpha + \sigma^2_{e,i})$"
    )
    fig.savefig(CHARTS_DIR / "04_alpha_eb_shrinkage_scatter.png")
    plt.close(fig)
    return out


# -------------------------------------------------------------- section 5 ---


def section_05_boxcox_saturation() -> dict:
    """% non-pruned trials with eb_fitness ≥ 0.99 per cell."""
    log.info("[05] Box-Cox saturation rate per cell")
    out: dict[str, dict] = {}
    counts_total = Counter()
    counts_sat = Counter()
    for cell, _seed, d in _iter_log_rows(_all_logs()):
        if d.get("pruned"):
            continue
        if d.get("eb_fitness") is None:
            continue
        counts_total[cell] += 1
        if float(d["eb_fitness"]) >= SAT_THRESHOLD:
            counts_sat[cell] += 1
    fig, ax = plt.subplots(1, 1, figsize=(8.5, 4.8))
    bars = []
    n_obs = []
    for cell in CELLS:
        tot = counts_total[cell]
        sat = counts_sat[cell]
        frac = (sat / tot) if tot > 0 else 0.0
        bars.append(frac * 100.0)
        n_obs.append(tot)
        out[cell] = {
            "n_trials_non_pruned": int(tot),
            "n_saturated": int(sat),
            "saturation_pct": float(frac * 100.0),
        }
    colors = ["#C85200" if b > SAT_FAIL_FRAC * 100.0 else "#006BA4"
              for b in bars]
    ax.bar(CELLS, bars, color=colors, edgecolor="black", linewidth=0.6)
    ax.axhline(SAT_FAIL_FRAC * 100, color="black", ls="--", lw=0.9,
               label=f"F2a threshold = {SAT_FAIL_FRAC*100:.0f} %")
    ax.set_ylabel(r"saturation rate  $\frac{|\{i: \mathrm{eb\_fitness}_i \geq 0.99\}|}{N_{\mathrm{nonpruned}}}$  (%)")
    ax.set_xlabel("cell (configuration)")
    ax.set_title(
        "Box-Cox ceiling saturation per cell  "
        "(F2a gate: orange = exceeds 1 %)"
    )
    for i, (b, n) in enumerate(zip(bars, n_obs)):
        ax.text(i, b + max(0.05, 0.02 * max([*bars, SAT_FAIL_FRAC * 100])),
                f"{b:.2f} %\n(n = {n})",
                ha="center", va="bottom", fontsize=9)
    ax.legend(loc="upper left")
    ax.set_ylim(0, max([*bars, SAT_FAIL_FRAC * 100]) * 1.4)
    fig.savefig(CHARTS_DIR / "05_boxcox_saturation_per_cell.png")
    plt.close(fig)
    return out


# -------------------------------------------------------------- section 6 ---


def section_06_pruner_rate() -> dict:
    """Pruner rate per (cell, seed)."""
    log.info("[06] Pruner rate per (cell, seed)")
    counts: dict[str, Counter] = defaultdict(Counter)  # cell -> {seed: total}
    pruned: dict[str, Counter] = defaultdict(Counter)  # cell -> {seed: pruned}
    for cell, seed, d in _iter_log_rows(_all_logs()):
        counts[cell][seed] += 1
        if d.get("pruned"):
            pruned[cell][seed] += 1
    fig, ax = plt.subplots(1, 1, figsize=(10, 4.8))
    n_cells = len(CELLS)
    width = 0.26
    out: dict[str, dict] = {}
    for j, seed in enumerate(SEEDS):
        ys = []
        for cell in CELLS:
            tot = counts[cell][seed]
            pr = pruned[cell][seed]
            rate = pr / tot if tot > 0 else 0.0
            ys.append(rate)
            out.setdefault(cell, {})[f"seed{seed}"] = {
                "n_total": int(tot), "n_pruned": int(pr), "rate": float(rate),
            }
        xs = np.arange(n_cells) + (j - 1) * width
        ax.bar(xs, ys, width, label=f"seed {seed}",
               edgecolor="black", linewidth=0.4)
        for x_, y_ in zip(xs, ys):
            ax.text(x_, y_ + 0.012, f"{y_*100:.0f}%",
                    ha="center", va="bottom", fontsize=8)
    ax.axhspan(0.10, 0.60, alpha=0.12, color="#C85200",
               label="design band  [0.10, 0.60]", zorder=0)
    ax.set_xticks(np.arange(n_cells))
    ax.set_xticklabels(CELLS)
    ax.set_ylabel(r"MedianPruner rate  $N_{\mathrm{pruned}} / N_{\mathrm{total}}$")
    ax.set_xlabel("cell (configuration)")
    ax.set_title("Pruner rate per (cell, seed)")
    ax.set_ylim(0, max(0.6, ax.get_ylim()[1]) + 0.05)
    ax.legend(loc="upper right", ncol=2)
    fig.savefig(CHARTS_DIR / "06_pruner_rate_per_cell.png")
    plt.close(fig)
    return out


# -------------------------------------------------------------- section 7 ---


def section_07_confounding_heatmap(repr_cell: str = "c2",
                                   repr_seed: str = "0") -> dict:
    """Per-study build × opponent matchup-count heatmap + per-cell imbalance."""
    log.info("[07] Confounding heatmap (%s/seed%s) + per-cell imbalance",
             repr_cell, repr_seed)

    # Headline panel: c2/seed0 build × opponent count matrix.
    paths = _seed_log_paths(repr_cell, repr_seed)
    records = load_records(paths)
    matrix, _builds, opps = _build_score_matrix(records)
    # Counts (not means) for visualization: re-build raw counts directly.
    counts = np.zeros_like(matrix, dtype=int)
    bidx_map = {bid: i for i, bid in enumerate({r.build_id for r in records})}
    bidx_keys = list(bidx_map.keys())
    bidx_map = {bid: i for i, bid in enumerate(bidx_keys)}
    opp_map = {o: j for j, o in enumerate(opps)}
    for r in records:
        i = bidx_map[r.build_id]
        for opp, _hp, _w in r.matches:
            counts[i, opp_map[opp]] += 1

    # Imbalance index per study: Var(count) / Mean(count) over present cells.
    def _imbalance(c: np.ndarray) -> float:
        flat = c.flatten()
        mean = float(flat.mean())
        if mean <= 0:
            return float("nan")
        return float(flat.var() / mean)

    repr_imbalance = _imbalance(counts)

    # Per-cell mean imbalance (averaged across 3 seeds within each cell).
    per_cell_imb: dict[str, float] = {}
    per_seed_imb: dict[str, dict[str, float]] = {}
    for cell in CELLS:
        ratios = []
        per_seed_imb[cell] = {}
        for seed in SEEDS:
            recs = load_records(_seed_log_paths(cell, seed))
            if not recs:
                continue
            mat, builds, opps = _build_score_matrix(recs)
            c = np.zeros_like(mat, dtype=int)
            bm = {b: i for i, b in enumerate(builds)}
            om = {o: j for j, o in enumerate(opps)}
            for r in recs:
                i = bm[r.build_id]
                for opp, _hp, _w in r.matches:
                    c[i, om[opp]] += 1
            imb = _imbalance(c)
            ratios.append(imb)
            per_seed_imb[cell][f"seed{seed}"] = imb
        per_cell_imb[cell] = float(np.mean(ratios)) if ratios else float("nan")

    # 2-panel figure: heatmap + per-cell bar.
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 5.4),
                             gridspec_kw={"width_ratios": [2.5, 1.2]})

    ax = axes[0]
    ax.grid(False)
    row_order = np.argsort(-counts.sum(axis=1))
    col_order = np.argsort(-counts.sum(axis=0))
    M = counts[row_order][:, col_order]
    im = ax.imshow(M, aspect="auto", cmap="magma")
    ax.set_xlabel("opponent index  (sorted by total matchups, descending)")
    ax.set_ylabel("build index  (sorted by total matchups, descending)")
    ax.set_title(
        f"(a) {repr_cell} / seed {repr_seed}  build × opponent matchup count\n"
        f"$n_{{\\mathrm{{builds}}}}$ = {M.shape[0]}, "
        f"$n_{{\\mathrm{{opps}}}}$ = {M.shape[1]}, "
        f"density = {(M > 0).mean():.1%}, "
        f"$I$ = {repr_imbalance:.2f}"
    )
    fig.colorbar(im, ax=ax, label="number of matchups")

    ax = axes[1]
    cells = list(per_cell_imb.keys())
    vals = [per_cell_imb[c] for c in cells]
    ax.bar(cells, vals, color="#006BA4", edgecolor="black", linewidth=0.5)
    ax.set_ylabel(r"imbalance index  $I = \mathrm{Var}(c) / \mathrm{Mean}(c)$")
    ax.set_xlabel("cell (configuration)")
    ax.set_title("(b) Per-cell mean imbalance  (higher = more confounded)")
    ymax = max(vals) * 1.15
    ax.set_ylim(0, ymax)
    for i, v in enumerate(vals):
        ax.text(i, v + ymax * 0.015, f"{v:.2f}", ha="center", va="bottom",
                fontsize=9)
    fig.savefig(CHARTS_DIR / "07_confounding_heatmap_seed0.png")
    plt.close(fig)
    return {
        "repr_cell": repr_cell,
        "repr_seed": repr_seed,
        "repr_imbalance": repr_imbalance,
        "repr_n_builds": int(M.shape[0]),
        "repr_n_opps": int(M.shape[1]),
        "repr_density_pct": float((M > 0).mean() * 100),
        "per_cell_mean_imbalance": per_cell_imb,
        "per_seed_imbalance": per_seed_imb,
    }


# -------------------------------------------------------------- section 8 ---


def section_08_pooling_stability(k: int = 10) -> dict:
    """Per-seed top-K vs within-cell-pooled top-K — Jaccard + Spearman ρ."""
    log.info("[08] Pooling rank stability — per-seed vs within-cell-pooled top-%d", k)
    out: dict[str, dict] = {}
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    pooled_keys, jaccards, rhos = [], [], []
    for cell in CELLS:
        cell_records = load_records(_cell_log_paths(cell))
        cell_ranked = rank_twfe_eb(cell_records, k=10**6)
        cell_top = cell_ranked[:k]
        cell_rank_map = {r.build_id: i for i, r in enumerate(cell_ranked)}

        per_seed_jaccard, per_seed_rho = [], []
        for seed in SEEDS:
            recs = load_records(_seed_log_paths(cell, seed))
            if not recs:
                continue
            seed_ranked = rank_twfe_eb(recs, k=10**6)
            seed_top = seed_ranked[:k]
            jc = _jaccard(seed_top, cell_top)
            # Spearman ρ over UNION of (seed-top-K ∪ pooled-top-K) ranks.
            seed_rank_map = {r.build_id: i for i, r in enumerate(seed_ranked)}
            common = ({r.build_id for r in seed_top} |
                      {r.build_id for r in cell_top})
            common &= set(seed_rank_map.keys())
            common &= set(cell_rank_map.keys())
            if len(common) >= 2:
                xs = np.asarray([seed_rank_map[b] for b in common], dtype=float)
                ys = np.asarray([cell_rank_map[b] for b in common], dtype=float)
                if xs.std() > 0 and ys.std() > 0:
                    rho = float(np.corrcoef(xs, ys)[0, 1])
                else:
                    rho = float("nan")
            else:
                rho = float("nan")
            per_seed_jaccard.append(jc)
            per_seed_rho.append(rho)
            pooled_keys.append(f"{cell}/seed{seed}")
            jaccards.append(jc)
            rhos.append(rho)
        out[cell] = {
            "per_seed_jaccard": per_seed_jaccard,
            "per_seed_rho": per_seed_rho,
            "mean_jaccard": float(np.mean(per_seed_jaccard))
                if per_seed_jaccard else float("nan"),
            "mean_rho": float(np.nanmean(per_seed_rho))
                if per_seed_rho else float("nan"),
        }

    ax = axes[0]
    xs_pos = np.arange(len(pooled_keys))
    cell_color = {c: plt.rcParams["axes.prop_cycle"].by_key()["color"][i]
                  for i, c in enumerate(CELLS)}
    bar_colors = [cell_color[k.split("/")[0]] for k in pooled_keys]
    ax.bar(xs_pos, jaccards, color=bar_colors, edgecolor="black", linewidth=0.4)
    ax.set_xticks(xs_pos)
    ax.set_xticklabels(pooled_keys, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(rf"top-{k} Jaccard  $J(\mathrm{{seed}}, \mathrm{{pooled}})$")
    ax.set_xlabel("(cell / seed)")
    ax.set_ylim(0, 1)
    ax.set_title(f"(a) Top-{k} Jaccard: per-seed TWFE+EB vs within-cell-pooled")
    mean_j = float(np.mean(jaccards))
    ax.axhline(mean_j, color="black", ls="--", lw=0.9,
               label=f"overall mean = {mean_j:.2f}")
    ax.legend(loc="upper right")

    ax = axes[1]
    rhos_arr = np.asarray(rhos, dtype=float)
    ax.bar(xs_pos, rhos_arr, color=bar_colors, edgecolor="black", linewidth=0.4)
    ax.set_xticks(xs_pos)
    ax.set_xticklabels(pooled_keys, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(r"Spearman $\rho$ over union of top-$K$ ids")
    ax.set_xlabel("(cell / seed)")
    ax.set_ylim(-1.0, 1.0)
    ax.axhline(0, color="grey", lw=0.5)
    mean_rho = float(np.nanmean(rhos_arr)) if len(rhos_arr) else float("nan")
    ax.axhline(mean_rho, color="black", ls="--", lw=0.9,
               label=f"overall mean = {mean_rho:.2f}")
    ax.legend(loc="lower right")
    ax.set_title(r"(b) Rank correlation: per-seed top-$K$ positions vs pooled positions")

    fig.savefig(CHARTS_DIR / "08_pooling_rank_stability.png")
    plt.close(fig)
    out["overall"] = {
        "mean_jaccard": float(np.mean(jaccards)) if jaccards else float("nan"),
        "mean_rho": float(np.nanmean(rhos)) if rhos else float("nan"),
    }
    return out


# -------------------------------------------------------------- section 9 ---


def section_09_bt_vs_twfe_alpha() -> dict:
    """Bradley-Terry α vs TWFE+EB α scatter (pooled across all cells)."""
    log.info("[09] BT skill vs TWFE+EB α scatter")
    records = load_records(_all_logs())
    bt = {r.build_id: r for r in rank_bradley_terry(records, k=10**6)}
    eb = {r.build_id: r for r in rank_twfe_eb(records, k=10**6)}
    common = set(bt.keys()) & set(eb.keys())
    xs = np.asarray([eb[b].score for b in common])
    ys = np.asarray([bt[b].score for b in common])
    if xs.std() > 0 and ys.std() > 0:
        pearson_r = float(np.corrcoef(xs, ys)[0, 1])
    else:
        pearson_r = float("nan")
    # Disagreement candidates: where BT and α̂_EB disagree by > 1 σ in
    # standardised units (z-score on each metric).
    z_eb = (xs - xs.mean()) / xs.std()
    z_bt = (ys - ys.mean()) / ys.std()
    disagree_mask = np.abs(z_eb - z_bt) > 1.0
    n_disagree = int(disagree_mask.sum())

    fig, ax = plt.subplots(1, 1, figsize=(8, 7.2))
    ax.scatter(xs, ys, c="#006BA4", alpha=0.5, s=20, edgecolors="none",
               label=f"builds  (n = {len(common):,})")
    ax.scatter(xs[disagree_mask], ys[disagree_mask], c="#C85200", alpha=0.9,
               s=24, edgecolors="black", linewidths=0.4,
               label=fr"$|\Delta z| > 1$  (n = {n_disagree})")
    if xs.std() > 0 and ys.std() > 0:
        slope, intercept = np.polyfit(xs, ys, 1)
        x_line = np.linspace(xs.min(), xs.max(), 50)
        ax.plot(x_line, slope * x_line + intercept, color="black",
                ls="--", lw=0.9, label=fr"OLS fit  $b$ = {slope:.2f}")
    ax.axhline(0, color="grey", lw=0.5)
    ax.axvline(0, color="grey", lw=0.5)
    ax.set_xlabel(r"TWFE+EB  $\hat{\alpha}^{\mathrm{EB}}_i$  "
                  r"(residual hp-differential, dimensionless)")
    ax.set_ylabel(r"Bradley–Terry skill  $\alpha^{\mathrm{BT}}_i$  (logit units)")
    ax.set_title(
        r"Bradley–Terry skill vs TWFE+EB build-quality  "
        f"(pooled, Pearson $r$ = {pearson_r:.3f})"
    )
    ax.legend(loc="upper left")
    fig.savefig(CHARTS_DIR / "09_bt_vs_twfe_eb_alpha.png")
    plt.close(fig)
    return {
        "n_builds": len(common),
        "pearson_r": pearson_r,
        "n_disagree_gt_1sigma": n_disagree,
        "disagree_frac": float(n_disagree / max(len(common), 1)),
    }


# ------------------------------------------------------------- section 10 ---


def section_10_search_coverage() -> dict:
    """Distinct builds per cell + cumulative-distinct-vs-trial curve."""
    log.info("[10] Search coverage (distinct builds, dup rate, cumulative)")
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    out: dict[str, dict] = {}
    bar_y_distinct, bar_y_total = [], []
    for cell in CELLS:
        records = load_records(_cell_log_paths(cell))
        distinct = {r.build_id for r in records}
        out[cell] = {
            "n_finalized": len(records),
            "n_distinct_builds": len(distinct),
            "duplicate_rate": float(1.0 - len(distinct) / max(len(records), 1)),
        }
        bar_y_distinct.append(len(distinct))
        bar_y_total.append(len(records))
    ax = axes[0]
    width = 0.36
    x = np.arange(len(CELLS))
    ax.bar(x - width / 2, bar_y_total, width, color="#ABABAB",
           edgecolor="black", linewidth=0.5, label="finalized trials")
    ax.bar(x + width / 2, bar_y_distinct, width, color="#006BA4",
           edgecolor="black", linewidth=0.5, label="distinct Build hashes")
    ax.set_xticks(x)
    ax.set_xticklabels(CELLS)
    ax.set_xlabel("cell (configuration)")
    ax.set_ylabel("count")
    ax.set_title("(a) Finalized trials vs distinct builds per cell")
    ax.legend(loc="lower right")
    ymax = max(*bar_y_total, *bar_y_distinct)
    ax.set_ylim(0, ymax * 1.13)
    for i, cell in enumerate(CELLS):
        dup = out[cell]["duplicate_rate"] * 100
        ax.text(i, max(bar_y_total[i], bar_y_distinct[i]) + ymax * 0.025,
                f"dup = {dup:.1f}%", ha="center", fontsize=9)

    # Cumulative distinct-vs-trial — pool seeds within cell, sort by trial #.
    ax = axes[1]
    cell_color = {c: plt.rcParams["axes.prop_cycle"].by_key()["color"][i]
                  for i, c in enumerate(CELLS)}
    for cell in CELLS:
        for _seed_i, seed in enumerate(SEEDS):
            recs = load_records(_seed_log_paths(cell, seed))
            recs = sorted(recs, key=lambda r: r.trial_number)
            seen = set()
            ys = []
            xs = []
            for r in recs:
                seen.add(r.build_id)
                xs.append(r.trial_number)
                ys.append(len(seen))
            ax.plot(xs, ys, alpha=0.7, lw=1.3,
                    color=cell_color[cell],
                    label=cell if seed == SEEDS[0] else None)
    # Reference: y = x (every trial new)
    xref = np.arange(0, max(bar_y_total) * 2)
    ax.plot(xref, xref, color="black", ls=":", lw=0.8,
            label=r"$y = x$  (every trial new)")
    ax.set_xlabel("trial number within seed-study")
    ax.set_ylabel("cumulative distinct Build hashes")
    ax.set_title("(b) Search-space coverage  (3 curves per cell — one per seed)")
    ax.legend(loc="lower right", ncol=2)
    ax.set_xlim(0, max(bar_y_total) * 1.1)

    fig.savefig(CHARTS_DIR / "10_search_coverage.png")
    plt.close(fig)
    return out


# ------------------------------------------------------------- section 11 ---


def _bootstrap_top3_alpha_eb(records: Sequence[TrialRecord],
                             rng: np.random.Generator,
                             n_iters: int) -> np.ndarray:
    """Bootstrap distribution of mean(top-3 α̂_EB) by resampling builds.

    For each iteration: sample n_builds *records* with replacement, refit
    TWFE+EB, take the top-3 α̂_EB scores, average. Returns (n_iters,) array.
    """
    out = np.empty(n_iters, dtype=float)
    n_recs = len(records)
    for i in range(n_iters):
        idx = rng.integers(0, n_recs, size=n_recs)
        boot = [records[j] for j in idx]
        ranked = rank_twfe_eb(boot, k=3)
        if not ranked:
            out[i] = float("nan")
            continue
        out[i] = float(np.mean([r.score for r in ranked[:3]]))
    return out


def section_11_f1c_gate(n_boot: int = BOOTSTRAP_ITERS) -> dict:
    """F1c training-time gate — bootstrap CI on Δ(top-3 mean α̂_EB)."""
    log.info("[11] F1c gate — %d-iter bootstrap on top-3 α̂_EB Δ", n_boot)
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    point_estimates = {}
    bt_top3 = {}
    boot_dists: dict[str, np.ndarray] = {}
    for cell in CELLS:
        recs = load_records(_cell_log_paths(cell))
        # Point estimate — top-3 mean α̂_EB and top-3 mean BT-skill.
        ranked_eb = rank_twfe_eb(recs, k=3)
        eb_mean = float(np.mean([r.score for r in ranked_eb[:3]]))
        ranked_bt = rank_bradley_terry(recs, k=3)
        bt_mean = float(np.mean([r.score for r in ranked_bt[:3]]))
        point_estimates[cell] = eb_mean
        bt_top3[cell] = bt_mean
        log.info("    %s top-3 α̂_EB mean = %+.4f  (BT %+.4f)  (n_recs=%d)",
                 cell, eb_mean, bt_mean, len(recs))
        boot_dists[cell] = _bootstrap_top3_alpha_eb(recs, rng, n_boot)

    # Decision-tree branch lookup helper.
    def _branch(delta: float, ci_low: float, ci_high: float) -> str:
        if delta < 0:
            return "F1c"  # paradigm-flip negative
        if (ci_low <= 0 <= ci_high) or delta < F1C_TARGET_DELTA:
            return "F1e"  # CI crosses 0 OR Δ below +0.02 doc-gate
        return "PASS"

    comparisons = {}
    for ctrl in ("c0a", "c0b"):
        # Δ_b = boot_C2[b] - boot_ctrl[b]  (matched bootstrap iterations)
        delta_dist = boot_dists["c2"] - boot_dists[ctrl]
        delta_dist = delta_dist[~np.isnan(delta_dist)]
        delta_point = point_estimates["c2"] - point_estimates[ctrl]
        ci_low, ci_high = (
            float(np.percentile(delta_dist, 2.5)),
            float(np.percentile(delta_dist, 97.5)),
        )
        comparisons[f"c2_vs_{ctrl}"] = {
            "delta_point": delta_point,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "branch": _branch(delta_point, ci_low, ci_high),
        }
        log.info("    Δ(c2 − %s) point=%+.4f  95%% CI [%+.4f, %+.4f]  branch=%s",
                 ctrl, delta_point, ci_low, ci_high,
                 comparisons[f"c2_vs_{ctrl}"]["branch"])

    # Best cell by honest mean α̂_EB
    best_cell = max(point_estimates, key=point_estimates.get)
    return {
        "point_top3_alpha_eb": point_estimates,
        "point_top3_bt_skill": bt_top3,
        "comparisons": comparisons,
        "best_cell_by_alpha_eb": best_cell,
        "best_cell_alpha_eb": point_estimates[best_cell],
    }


# ------------------------------------------------------------- section 12 ---


def section_12_eb_shrinkage_diagnostics() -> dict:
    """EB-shrinkage diagnostic: distribution of (α̂ − α̂_EB)/σ(α̂) per cell.

    Heavy negative tail at low n_matches confirms heteroscedastic shrinkage
    is squeezing under-sampled builds toward the prior. (No chart of its
    own — the slope chart in §4 already visualises shrinkage; here we just
    return the summary stats so the report can cite them.)
    """
    log.info("[12] EB shrinkage standardised-delta summary per cell")
    out: dict[str, dict] = {}
    for cell in CELLS:
        recs = load_records(_cell_log_paths(cell))
        twfe = {r.build_id: r for r in rank_twfe(recs, k=10**6)}
        eb = rank_twfe_eb(recs, k=10**6)
        zs = []
        n_arr = []
        for r in eb:
            tw = twfe[r.build_id]
            if tw.sigma == 0 or np.isnan(tw.sigma):
                continue
            zs.append((tw.score - r.score) / tw.sigma)
            n_arr.append(tw.n_matches)
        if not zs:
            out[cell] = {"n_builds": 0}
            continue
        zs = np.asarray(zs)
        out[cell] = {
            "n_builds": len(zs),
            "mean_z": float(zs.mean()),
            "std_z": float(zs.std(ddof=1)) if len(zs) > 1 else float("nan"),
            "median_abs_z": float(np.median(np.abs(zs))),
            "max_abs_z": float(np.max(np.abs(zs))),
            "n_match_min": int(np.min(n_arr)),
            "n_match_max": int(np.max(n_arr)),
            "n_match_mean": float(np.mean(n_arr)),
        }
    return out


# ------------------------------------------------------------- section 13 ---


def section_13_pruner_boxcox() -> dict:
    """Pruner × Box-Cox saturation cross-tab — and per-cell pruner / saturation
    co-incidence.

    The fundamental question is: when Box-Cox saturates a trial's eb_fitness
    to ≥0.99, is that trial more likely to be a finalized (unpruned) trial?
    Pruner-pruned trials emit JSONL rows but with `pruned=true` and
    `eb_fitness=null`, so saturation is only defined on the un-pruned slice.
    We instead measure: per-cell, what fraction of (un-pruned) trials are
    saturated, vs the per-cell pruner rate. If saturation is mostly hitting
    the post-pruner survivors, the lift suggests Box-Cox is amplifying
    already-strong signal rather than distorting weak signal. If pruner
    rates correlate inversely with saturation, the pruner is masking the
    saturation problem.
    """
    log.info("[13] Pruner × Box-Cox saturation: per-cell co-incidence")
    out: dict[str, dict] = {}
    for cell in CELLS:
        n_total = 0
        n_pruned = 0
        n_finalized = 0
        n_finalized_saturated = 0
        for _c, _s, d in _iter_log_rows(_cell_log_paths(cell)):
            n_total += 1
            if d.get("pruned"):
                n_pruned += 1
                continue
            eb = d.get("eb_fitness")
            if eb is None:
                continue
            n_finalized += 1
            if float(eb) >= SAT_THRESHOLD:
                n_finalized_saturated += 1
        out[cell] = {
            "n_total": int(n_total),
            "n_pruned": int(n_pruned),
            "n_finalized": int(n_finalized),
            "n_finalized_saturated": int(n_finalized_saturated),
            "pruner_rate": (n_pruned / max(n_total, 1)),
            "saturation_rate_finalized": (
                n_finalized_saturated / max(n_finalized, 1)
            ),
        }
    # Spearman rank correlation between per-cell pruner_rate and saturation_rate.
    cells = list(out.keys())
    pr = np.asarray([out[c]["pruner_rate"] for c in cells])
    sr = np.asarray([out[c]["saturation_rate_finalized"] for c in cells])
    if pr.std() > 0 and sr.std() > 0:
        corr = float(np.corrcoef(pr, sr)[0, 1])
    else:
        corr = float("nan")
    out["overall_pearson_pr_vs_sat"] = corr
    return out


# ----------------------------------------------------------------- driver ---


def main() -> int:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    HEADLINES_PATH.parent.mkdir(parents=True, exist_ok=True)

    headline = {}
    headline["section_01_topk_per_cell_k5"] = section_01_topk_per_cell(k=5)
    headline["section_02_topk_pooled"] = section_02_topk_pooled(ks=(3, 5, 10))
    headline["section_03_alpha_distribution"] = section_03_alpha_distribution()
    headline["section_04_eb_shrinkage_scatter"] = section_04_eb_shrinkage_scatter()
    headline["section_05_boxcox_saturation"] = section_05_boxcox_saturation()
    headline["section_06_pruner_rate"] = section_06_pruner_rate()
    headline["section_07_confounding"] = section_07_confounding_heatmap()
    headline["section_08_pooling_stability"] = section_08_pooling_stability(k=10)
    headline["section_09_bt_vs_twfe"] = section_09_bt_vs_twfe_alpha()
    headline["section_10_search_coverage"] = section_10_search_coverage()
    headline["section_11_f1c"] = section_11_f1c_gate()
    headline["section_12_eb_diag"] = section_12_eb_shrinkage_diagnostics()
    headline["section_13_pruner_boxcox"] = section_13_pruner_boxcox()

    HEADLINES_PATH.write_text(json.dumps(headline, indent=2, default=float))
    log.info("Wrote %s", HEADLINES_PATH)
    log.info("Wrote %d charts to %s", len(list(CHARTS_DIR.glob("*.png"))),
             CHARTS_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
