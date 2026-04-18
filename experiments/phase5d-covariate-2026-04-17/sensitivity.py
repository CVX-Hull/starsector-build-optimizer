"""Sensitivity sweep — when does covariate adjustment help vs hurt?

Varies two regimes that change whether CUPED-style adjustment provides net
benefit:

  active_size ∈ {3, 5, 10, 20}  — how many opponents each build faces.
                                  Fewer ⇒ α̂ more noisy ⇒ CUPED can reduce
                                  variance.
  heuristic_noise ∈ {0.3, 0.6, 1.2, 2.5} — σ of q-proxy noise in heuristic_i.
                                  Lower ⇒ heuristic has more signal ⇒ CUPED
                                  risks subtracting signal (rank hurt).

For each (active_size, heuristic_noise) we evaluate A0/A/C at n_seeds=8 and
report mean Δρ(C − A0) and Δρ(A − A0).

Usage:
    uv run python sensitivity.py
"""
from __future__ import annotations

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

# Reuse machinery from the main validation module.
import sys
sys.path.insert(0, str(Path(__file__).parent))
import phase5d_validation as p5d  # noqa: E402

HERE = Path(__file__).parent


def run_cell(active_size: int, heuristic_noise: float, seed: int) -> dict:
    import curriculum_simulation as cs
    rng = np.random.default_rng(seed)
    # Temporarily patch global for this seed.
    orig = p5d.HEURISTIC_NOISE_STD
    p5d.HEURISTIC_NOISE_STD = heuristic_noise
    try:
        opponents = cs.generate_opponents(54, rng)
        builds = p5d.generate_xbuilds(368, rng)
        schedule = p5d.build_schedule(
            368, opponents, active_size,
            p5d.N_ANCHORS_DEFAULT, p5d.N_INCUMBENT_OVERLAP, rng)
        score_mat, bi, oi, _ = p5d.collect_run(builds, opponents, schedule, rng)

        truth = np.array([b.quality for b in builds])
        heuristic_i = np.array([b.heuristic for b in builds])
        X_build = np.vstack([np.concatenate([b.x_useful, b.x_noise]) for b in builds])
        X_build = (X_build - X_build.mean(axis=0)) \
                  / X_build.std(axis=0).clip(min=1e-10)

        a_A0, _ = p5d.twfe_plain(score_mat)
        a_A = p5d.estimator_baseline_A(score_mat, heuristic_i)
        a_C, _, _ = p5d.estimator_multi_pds_C(score_mat, X_build, seed=seed)
    finally:
        p5d.HEURISTIC_NOISE_STD = orig
    return {
        "active": active_size, "h_noise": heuristic_noise, "seed": seed,
        "rho_A0": float(stats.spearmanr(a_A0, truth).statistic),
        "rho_A":  float(stats.spearmanr(a_A,  truth).statistic),
        "rho_C":  float(stats.spearmanr(a_C,  truth).statistic),
    }


def main() -> None:
    actives = [3, 5, 10, 20]
    noises = [0.3, 0.6, 1.2, 2.5]
    n_seeds = 8

    rows = []
    t0 = time.time()
    for a in actives:
        for h in noises:
            for s in range(n_seeds):
                rows.append(run_cell(a, h, s))
    print(f"Total: {time.time() - t0:.1f}s")

    df = pd.DataFrame(rows)
    df.to_csv(HERE / "sensitivity_results.csv", index=False)

    summary = df.groupby(["active", "h_noise"]).agg(
        rho_A0=("rho_A0", "mean"), rho_A=("rho_A", "mean"),
        rho_C=("rho_C", "mean"),
    ).reset_index()
    summary["d_A_A0"] = summary["rho_A"] - summary["rho_A0"]
    summary["d_C_A0"] = summary["rho_C"] - summary["rho_A0"]
    print(summary.round(3).to_string(index=False))

    # Heatmap
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, metric, title in [
        (axes[0], "d_A_A0", "Shipped A − A0 (plain TWFE): scalar CV helps if > 0"),
        (axes[1], "d_C_A0", "C (multi-PDS) − A0: proposed 5D helps if > 0"),
    ]:
        piv = summary.pivot(index="active", columns="h_noise", values=metric)
        im = ax.imshow(piv.values, cmap="RdBu_r", aspect="auto",
                       vmin=-abs(piv.values).max(), vmax=abs(piv.values).max())
        for i in range(piv.shape[0]):
            for j in range(piv.shape[1]):
                ax.text(j, i, f"{piv.values[i, j]:+.2f}", ha="center",
                        va="center", color="k", fontsize=10)
        ax.set_xticks(range(piv.shape[1])); ax.set_xticklabels(piv.columns)
        ax.set_yticks(range(piv.shape[0])); ax.set_yticklabels(piv.index)
        ax.set_xlabel("heuristic noise (σ of q-proxy)")
        ax.set_ylabel("active (obs per build)")
        ax.set_title(title)
        plt.colorbar(im, ax=ax)
    plt.tight_layout()
    plt.savefig(HERE / "sensitivity_heatmap.png", dpi=130)
    plt.close()

    print(f"\nSaved: sensitivity_results.csv, sensitivity_heatmap.png")


if __name__ == "__main__":
    main()
