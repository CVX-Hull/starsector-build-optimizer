"""Sensitivity sweep for fusion-paradigm estimators.

Varies (active_size × heuristic_noise × noise_covariate_count) to verify that
EB / inverse-variance / factor-model estimators remain superior to plain TWFE
across regimes, and to probe failure modes (e.g., what happens with all-noise
covariates?).

Key regimes tested:
  active_size ∈ {3, 5, 10, 20}
  heuristic_noise ∈ {0.3, 0.6, 1.2, 2.5}       (lower = higher h fidelity)
  n_noise_cols ∈ {0, 4, 12}                    (sprinkle noise columns into X)

For the n_noise_cols ∈ {4, 12} cells we test robustness: if EB includes
genuine-noise columns in its prior regression, does it over-fit and collapse?

Usage:
    uv run python fusion_sensitivity.py
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.exceptions import ConvergenceWarning

warnings.filterwarnings("ignore", category=ConvergenceWarning)

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import phase5d_validation as p5d  # noqa: E402
import phase5d_fusion_validation as fus  # noqa: E402


def run_cell(active: int, h_noise: float, n_noise_cols: int,
             seed: int) -> dict:
    import curriculum_simulation as cs
    rng = np.random.default_rng(seed)
    orig_h = p5d.HEURISTIC_NOISE_STD
    orig_nnoise = p5d.N_NOISE_XB
    p5d.HEURISTIC_NOISE_STD = h_noise
    # We can't change N_NOISE_XB at runtime (it's used in default_factory);
    # instead, we stack extra noise columns onto the X_build matrix.
    try:
        opponents = cs.generate_opponents(54, rng)
        builds = p5d.generate_xbuilds(368, rng)
        schedule = p5d.build_schedule(368, opponents, active,
                                       p5d.N_ANCHORS_DEFAULT,
                                       p5d.N_INCUMBENT_OVERLAP, rng)
        score_mat, _, _, _ = p5d.collect_run(builds, opponents, schedule, rng)

        truth = np.array([b.quality for b in builds])
        heuristic_i = np.array([b.heuristic for b in builds])
        # Start with the 8-col default (4 useful + 4 noise)
        X_base = np.vstack([np.concatenate([b.x_useful, b.x_noise])
                             for b in builds])
        # Sprinkle additional pure-noise columns
        if n_noise_cols > 0:
            extra_noise = rng.normal(0, 1, size=(len(builds), n_noise_cols))
            X = np.hstack([X_base, extra_noise])
        else:
            X = X_base
        X = (X - X.mean(axis=0)) / X.std(axis=0).clip(min=1e-10)
        X_h = heuristic_i.reshape(-1, 1)
        X_h = (X_h - X_h.mean()) / X_h.std(ddof=0).clip(min=1e-10)

        a_A0, _ = p5d.twfe_plain(score_mat)
        a_A = p5d.estimator_baseline_A(score_mat, heuristic_i)
        a_H1, _ = fus.estimator_H_eb_shrinkage(score_mat, X_h)
        a_HN, _ = fus.estimator_H_eb_shrinkage(score_mat, X)
        a_IV, _ = fus.estimator_IV_inverse_variance(score_mat, X)
        a_FA, _ = fus.estimator_FA_one_factor(score_mat, X)
        a_EBT = fus.triple_goal(a_HN, a_A0)
    finally:
        p5d.HEURISTIC_NOISE_STD = orig_h

    return {
        "active": active, "h_noise": h_noise, "n_noise_cols": n_noise_cols,
        "seed": seed,
        "rho_A0": float(stats.spearmanr(a_A0, truth).statistic),
        "rho_A":  float(stats.spearmanr(a_A,  truth).statistic),
        "rho_H1": float(stats.spearmanr(a_H1, truth).statistic),
        "rho_HN": float(stats.spearmanr(a_HN, truth).statistic),
        "rho_IV": float(stats.spearmanr(a_IV, truth).statistic),
        "rho_FA": float(stats.spearmanr(a_FA, truth).statistic),
        "rho_EBT": float(stats.spearmanr(a_EBT, truth).statistic),
    }


def main() -> None:
    actives = [3, 5, 10, 20]
    noises = [0.3, 0.6, 1.2, 2.5]
    noise_cols = [0, 4, 12]
    n_seeds = 6

    rows = []
    t0 = time.time()
    for a in actives:
        for h in noises:
            for nc in noise_cols:
                for s in range(n_seeds):
                    rows.append(run_cell(a, h, nc, s))
    print(f"Total: {time.time() - t0:.1f}s")

    df = pd.DataFrame(rows)
    df.to_csv(HERE / "fusion_sensitivity_results.csv", index=False)

    # Aggregate
    agg = df.groupby(["active", "h_noise", "n_noise_cols"]).agg({
        k: "mean" for k in ["rho_A0", "rho_A", "rho_H1", "rho_HN",
                             "rho_IV", "rho_FA", "rho_EBT"]
    }).reset_index()

    # Key deltas
    for k in ["A", "H1", "HN", "IV", "FA", "EBT"]:
        agg[f"d_{k}"] = agg[f"rho_{k}"] - agg["rho_A0"]

    # Headline: wherever A0 was best, now what wins?
    print("\n--- Best estimator per cell (by rank ρ) ---")
    keys = ["A0", "A", "H1", "HN", "IV", "FA", "EBT"]
    for _, row in agg.iterrows():
        rhos = {k: row[f"rho_{k}"] for k in keys}
        winner = max(rhos, key=rhos.get)
        print(f"  active={row['active']:2.0f} h_noise={row['h_noise']:.1f} "
              f"noise_cols={row['n_noise_cols']:2.0f}  winner={winner:3s} "
              f"(ρ={rhos[winner]:+.3f})")

    # Plot: Δρ(HN − A0) and Δρ(FA − A0) across regimes, faceted by noise_cols
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))
    for row_i, metric in enumerate(["d_HN", "d_FA"]):
        for col_i, nc in enumerate(noise_cols):
            ax = axes[row_i, col_i]
            sub = agg[agg["n_noise_cols"] == nc]
            piv = sub.pivot(index="active", columns="h_noise", values=metric)
            im = ax.imshow(piv.values, cmap="RdBu_r", aspect="auto",
                           vmin=-0.5, vmax=0.5)
            for i in range(piv.shape[0]):
                for j in range(piv.shape[1]):
                    ax.text(j, i, f"{piv.values[i, j]:+.2f}", ha="center",
                            va="center", color="k", fontsize=9)
            ax.set_xticks(range(piv.shape[1])); ax.set_xticklabels(piv.columns)
            ax.set_yticks(range(piv.shape[0])); ax.set_yticklabels(piv.index)
            ax.set_xlabel("heuristic noise σ")
            ax.set_ylabel("active obs per build")
            est = "HN (EB multi)" if metric == "d_HN" else "FA (one-factor)"
            ax.set_title(f"Δρ({est} − A0)  |  extra-noise cols = {nc}")
            plt.colorbar(im, ax=ax)
    plt.tight_layout()
    plt.savefig(HERE / "fusion_sensitivity_heatmap.png", dpi=130)
    plt.close()

    # Summary across noise_cols
    print("\n--- Mean Δρ(winner − A0) by noise_cols ---")
    for nc in noise_cols:
        sub = agg[agg["n_noise_cols"] == nc]
        print(f"  noise_cols={nc}:  "
              f"Δ_H1={sub['d_H1'].mean():+.3f}  "
              f"Δ_HN={sub['d_HN'].mean():+.3f}  "
              f"Δ_IV={sub['d_IV'].mean():+.3f}  "
              f"Δ_FA={sub['d_FA'].mean():+.3f}  "
              f"Δ_EBT={sub['d_EBT'].mean():+.3f}")


if __name__ == "__main__":
    main()
