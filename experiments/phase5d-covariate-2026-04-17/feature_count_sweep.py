"""Phase 5D: sweep feature count p × dataset size N.

Question: given realistic dataset budgets (N ≈ 200 / 368 / 900 builds from
overnight / Hammerhead / multi-day runs at ~27 trials/hour with 4 instances),
how does the HN empirical-Bayes estimator's rank correlation respond as we
vary the number of covariates in X_build?

Three regimes expected:
  (a) Too few useful features → prior is weak → w̄ → 1 → HN → A0 (plain TWFE).
  (b) Right-sized prior → Δρ(HN, A0) peaks.
  (c) Too many features (p/N ↑) → OLS γ̂ overfits residual noise → τ̂² collapses
      → prior becomes too tight → HN over-shrinks to a noisy regression
      surface → Δρ falls, sometimes below A0.

Sweep (p_useful, p_noise, N) and report mean Δρ(HN, A0) + w̄ per cell. Pure
noise columns are the adversarial test: does HN degrade gracefully as we
dilute the prior with irrelevant features?

Key optimization: for each (seed, N) the score matrix depends only on the
quality vector, not on the covariate design — so we generate it once per
(seed, N) and reuse across all (p_useful, p_noise) configs.

Usage:
    uv run python feature_count_sweep.py            # full sweep, ~15 min
    uv run python feature_count_sweep.py --quick    # 3 seeds × small grid
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import phase5d_validation as p5d  # noqa: E402
import phase5d_fusion_validation as fusion  # noqa: E402
import curriculum_simulation as cs  # noqa: E402

OUT_DIR = HERE

plt.style.use("seaborn-v0_8-darkgrid")
plt.rcParams["figure.figsize"] = (14, 9)


@dataclass
class QualityBuild:
    quality: float
    archetype: np.ndarray
    has_exploit: bool
    heuristic: float


def generate_quality_builds(n: int, rng: np.random.Generator) -> list[QualityBuild]:
    """Just quality + archetype + exploit flag + heuristic (no X yet)."""
    builds = []
    for _ in range(n):
        has_exploit = rng.random() < p5d.EXPLOIT_FRACTION
        if has_exploit:
            q = p5d.EXPLOIT_UPLIFT + rng.normal(0, p5d.EXPLOIT_SUBVAR)
        else:
            q = rng.normal(0, 1)
        h = q + rng.normal(0, p5d.HEURISTIC_NOISE_STD)
        builds.append(QualityBuild(
            quality=q, archetype=rng.dirichlet([2, 2, 2]),
            has_exploit=has_exploit, heuristic=h,
        ))
    return builds


def to_xbuild(b: QualityBuild, x_useful: np.ndarray,
              x_noise: np.ndarray) -> p5d.XBuild:
    return p5d.XBuild(
        quality=b.quality, archetype=b.archetype, has_exploit=b.has_exploit,
        x_useful=x_useful, x_noise=x_noise, heuristic=b.heuristic,
    )


# Fixed pool of feature-noise σs — represents the realistic spread of scorer-
# component fidelity. σ ≈ 0.5 → ρ(feature, q) ≈ 0.9 (best-case component, e.g.
# total_dps for mostly-DPS builds). σ ≈ 1.2 → ρ ≈ 0.4 (composite_score level).
# Drawn once from U(0.5, 1.3) with fixed seed so the sweep is reproducible and
# "adding the k-th feature" always means the same feature across all cells.
_POOL_RNG = np.random.default_rng(42)
SIGMA_POOL = np.sort(_POOL_RNG.uniform(0.5, 1.3, 25))  # best (0.5) → noisiest (1.3)


def generate_x_columns(qualities: np.ndarray, p_useful: int, p_noise: int,
                       rng: np.random.Generator) -> np.ndarray:
    """p_useful nested-quality q-proxies + p_noise independent N(0,1) columns.

    Nested: the first p_useful features of the fixed SIGMA_POOL (σ ∈ [0.5, 1.3],
    sorted best→worst). This answers "what does the k-th added feature do?"
    with monotone feature quality.
    """
    n = len(qualities)
    cols = []
    if p_useful > 0:
        sigmas = SIGMA_POOL[:p_useful]
        for s in sigmas:
            cols.append(qualities + rng.normal(0, s, size=n))
    if p_noise > 0:
        for _ in range(p_noise):
            cols.append(rng.normal(0, 1, size=n))
    if not cols:
        return np.zeros((n, 0))
    X = np.column_stack(cols)
    # Standardize (matches production X_build preprocessing).
    return (X - X.mean(axis=0)) / X.std(axis=0).clip(min=1e-10)


def run_cell(
    qb: list[QualityBuild], score_mat: np.ndarray,
    p_useful: int, p_noise: int, rng: np.random.Generator,
) -> dict:
    """One (seed, N, p_useful, p_noise) cell. Returns rhos + diagnostics."""
    qualities = np.array([b.quality for b in qb])
    heuristic = np.array([b.heuristic for b in qb])
    truth = qualities

    X_build = generate_x_columns(qualities, p_useful, p_noise, rng)
    p_total = p_useful + p_noise

    # A0 plain TWFE
    a_A0, _ = p5d.twfe_plain(score_mat)
    # A shipped scalar CV
    a_A = p5d.estimator_baseline_A(score_mat, heuristic)
    # HN: EB shrinkage w/ full X prior
    if X_build.shape[1] == 0:
        # Fallback: degenerate "0 features" → HN equals A0.
        a_HN = a_A0.copy()
        tau2 = float("nan"); w_mean = 1.0
    else:
        a_HN, diag = fusion.estimator_H_eb_shrinkage(score_mat, X_build)
        tau2 = diag["tau2"]; w_mean = diag["w_mean"]
    # EBT: triple-goal on HN
    a_EBT = fusion.triple_goal(a_HN, a_A0)

    def rho(x): return float(stats.spearmanr(x, truth).statistic)
    return {
        "p_useful": p_useful, "p_noise": p_noise, "p_total": p_total,
        "rho_A0": rho(a_A0), "rho_A": rho(a_A),
        "rho_HN": rho(a_HN), "rho_EBT": rho(a_EBT),
        "tau2": tau2, "w_mean": w_mean,
        "var_HN": float(a_HN.var()), "var_A0": float(a_A0.var()),
    }


def run_one_seed(seed: int, n_builds: int, grid: list[tuple[int, int]]) -> pd.DataFrame:
    """Generate builds + score matrix once per (seed, N), iterate across grid."""
    rng = np.random.default_rng(seed)
    opponents = cs.generate_opponents(p5d.N_OPPONENTS_DEFAULT, rng)
    qb = generate_quality_builds(n_builds, rng)
    schedule = p5d.build_schedule(
        n_builds, opponents, p5d.N_ACTIVE_DEFAULT,
        p5d.N_ANCHORS_DEFAULT, p5d.N_INCUMBENT_OVERLAP, rng,
    )
    # Use the XBuild-based collect_run path (it only touches .quality, .archetype,
    # .has_exploit). Wrap QualityBuilds in XBuild with empty X columns.
    wrapped = [to_xbuild(b, np.zeros(0), np.zeros(0)) for b in qb]
    score_mat, *_ = p5d.collect_run(wrapped, opponents, schedule, rng)

    rows = []
    for p_u, p_n in grid:
        # Per-cell RNG: reproducible, independent of other cells.
        cell_rng = np.random.default_rng((seed + 1) * 100003 + p_u * 1009 + p_n * 17)
        r = run_cell(qb, score_mat, p_u, p_n, cell_rng)
        r["seed"] = seed
        r["n_builds"] = n_builds
        rows.append(r)
    return pd.DataFrame(rows)


def sweep(n_seeds: int, n_builds_list: list[int],
          p_useful_list: list[int], p_noise_list: list[int]) -> pd.DataFrame:
    grid = [(pu, pn) for pu in p_useful_list for pn in p_noise_list]
    all_rows = []
    total_seeds = n_seeds * len(n_builds_list)
    done = 0
    for N in n_builds_list:
        for seed in range(n_seeds):
            t0 = time.time()
            df = run_one_seed(seed, N, grid)
            all_rows.append(df)
            done += 1
            dt = time.time() - t0
            print(f"  [{done:3d}/{total_seeds}] N={N:4d} seed={seed:2d}  "
                  f"{len(grid)} cells in {dt:5.1f}s  "
                  f"mean Δρ(HN−A0)={(df['rho_HN'] - df['rho_A0']).mean():+.3f}")
    return pd.concat(all_rows, ignore_index=True)


def plot_heatmaps(df: pd.DataFrame, out_path: Path) -> None:
    agg = df.groupby(["n_builds", "p_useful", "p_noise"]).agg(
        delta_HN=("rho_HN", lambda x: (x - df.loc[x.index, "rho_A0"]).mean()),
        delta_EBT=("rho_EBT", lambda x: (x - df.loc[x.index, "rho_A0"]).mean()),
        w_mean=("w_mean", "mean"),
        rho_HN=("rho_HN", "mean"),
        rho_A0=("rho_A0", "mean"),
    ).reset_index()

    n_builds_vals = sorted(df["n_builds"].unique())
    fig, axes = plt.subplots(3, len(n_builds_vals),
                              figsize=(4.5 * len(n_builds_vals), 11))
    if len(n_builds_vals) == 1:
        axes = axes.reshape(-1, 1)

    for col_idx, N in enumerate(n_builds_vals):
        sub = agg[agg["n_builds"] == N]
        piv_dHN = sub.pivot(index="p_noise", columns="p_useful", values="delta_HN")
        piv_w = sub.pivot(index="p_noise", columns="p_useful", values="w_mean")
        piv_rho = sub.pivot(index="p_noise", columns="p_useful", values="rho_HN")

        for row_idx, (piv, title_fmt, cmap, vmin, vmax, fmt) in enumerate([
            (piv_dHN, "Δρ(HN − A0), N={}", "RdBu_r", -0.1, 0.1, ".3f"),
            (piv_w, "mean shrinkage weight w̄, N={}", "viridis", 0.0, 1.0, ".2f"),
            (piv_rho, "ρ(HN, truth), N={}", "viridis", 0.3, 0.9, ".3f"),
        ]):
            ax = axes[row_idx, col_idx]
            im = ax.imshow(piv.values, cmap=cmap, aspect="auto",
                            vmin=vmin, vmax=vmax, origin="lower")
            ax.set_xticks(range(len(piv.columns)))
            ax.set_xticklabels(piv.columns)
            ax.set_yticks(range(len(piv.index)))
            ax.set_yticklabels(piv.index)
            ax.set_xlabel("p_useful")
            ax.set_ylabel("p_noise")
            ax.set_title(title_fmt.format(N))
            for i in range(len(piv.index)):
                for j in range(len(piv.columns)):
                    val = piv.values[i, j]
                    if np.isfinite(val):
                        ax.text(j, i, format(val, fmt), ha="center", va="center",
                                color="white" if cmap == "viridis" else "black",
                                fontsize=8)
            plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close()


def plot_curves(df: pd.DataFrame, out_path: Path) -> None:
    """Line plots: Δρ(HN − A0) vs p_useful, one line per (N, p_noise)."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    df = df.copy()
    df["delta_HN"] = df["rho_HN"] - df["rho_A0"]
    df["delta_EBT"] = df["rho_EBT"] - df["rho_A0"]

    for ax, col, title in [(axes[0], "delta_HN", "Δρ(HN − A0)"),
                            (axes[1], "delta_EBT", "Δρ(EBT − A0)")]:
        for N in sorted(df["n_builds"].unique()):
            for p_n in sorted(df["p_noise"].unique()):
                sub = df[(df["n_builds"] == N) & (df["p_noise"] == p_n)]
                agg = sub.groupby("p_useful")[col].agg(["mean", "std"])
                label = f"N={N}, p_noise={p_n}"
                ax.errorbar(agg.index, agg["mean"], yerr=agg["std"],
                             marker="o", capsize=2, label=label, alpha=0.85)
        ax.axhline(0, color="black", lw=0.5)
        ax.axhline(0.02, color="green", lw=0.5, ls="--", label="ship gate (+0.02)")
        ax.set_xlabel("p_useful (number of q-correlated covariates)")
        ax.set_ylabel(title)
        ax.set_title(title)
        ax.legend(fontsize=8, ncol=2)
        ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    plt.close()


def print_summary(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("SUMMARY: mean Δρ(HN − A0) across (N × p_useful × p_noise)")
    print("=" * 72)
    df = df.copy()
    df["delta_HN"] = df["rho_HN"] - df["rho_A0"]

    for N in sorted(df["n_builds"].unique()):
        print(f"\n--- N = {N} builds ---")
        sub = df[df["n_builds"] == N]
        table = sub.groupby(["p_useful", "p_noise"])["delta_HN"].agg(
            ["mean", "std", "count"]).round(4)
        print(table.to_string())

    # Where does HN degrade below A0?
    print("\n" + "=" * 72)
    print("Cells where HN UNDERPERFORMS A0 (mean Δρ < 0):")
    print("=" * 72)
    agg = df.groupby(["n_builds", "p_useful", "p_noise"])["delta_HN"].mean()
    bad = agg[agg < 0]
    if len(bad):
        print(bad.round(4).to_string())
    else:
        print("  None — HN dominates A0 in every cell.")

    # Ship-gate: Δρ ≥ +0.02
    print("\n" + "=" * 72)
    print("Cells where HN PASSES ship-gate (mean Δρ ≥ +0.02):")
    print("=" * 72)
    pass_cells = agg[agg >= 0.02]
    print(f"  {len(pass_cells)}/{len(agg)} cells pass, "
          f"mean Δρ in passing cells = {pass_cells.mean():+.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--n-seeds", type=int, default=6)
    args = ap.parse_args()

    if args.quick:
        n_seeds = 3
        n_builds_list = [200, 368]
        p_useful_list = [0, 1, 3, 8, 16]
        p_noise_list = [0, 4]
    else:
        n_seeds = args.n_seeds
        # Matches realistic dataset budgets: 200 (8h overnight),
        # 368 (Hammerhead), 900 (multi-day).
        n_builds_list = [200, 368, 900]
        p_useful_list = [0, 1, 2, 4, 8, 13, 20]
        p_noise_list = [0, 2, 6, 12]

    total_cells = n_seeds * len(n_builds_list) * len(p_useful_list) * len(p_noise_list)
    print(f"Feature-count sweep: {n_seeds} seeds × {len(n_builds_list)} Ns × "
          f"{len(p_useful_list)} p_useful × {len(p_noise_list)} p_noise "
          f"= {total_cells} cells total")
    print(f"N values: {n_builds_list}")
    print(f"p_useful: {p_useful_list}")
    print(f"p_noise:  {p_noise_list}")
    print()

    t0 = time.time()
    df = sweep(n_seeds, n_builds_list, p_useful_list, p_noise_list)
    elapsed = time.time() - t0
    print(f"\nSweep complete: {elapsed:.1f}s = {elapsed / 60:.1f} min")

    df.to_csv(OUT_DIR / "feature_count_results.csv", index=False)
    print_summary(df)

    plot_heatmaps(df, OUT_DIR / "feature_count_heatmap.png")
    plot_curves(df, OUT_DIR / "feature_count_curves.png")
    print(f"\nSaved:")
    print(f"  {OUT_DIR / 'feature_count_results.csv'}")
    print(f"  {OUT_DIR / 'feature_count_heatmap.png'}")
    print(f"  {OUT_DIR / 'feature_count_curves.png'}")


if __name__ == "__main__":
    main()
