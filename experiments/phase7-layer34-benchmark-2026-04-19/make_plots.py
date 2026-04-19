"""Render headline plots for Layer 3 + Layer 4 after both benchmarks land."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent


def plot_layer3():
    df = pd.read_csv(ROOT / "layer3_agg.csv")
    df = df.sort_values("true_sigma2")
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    ax = axes[0]
    x = np.arange(len(df)); w = 0.35
    ax.bar(x - w/2, df["homo_sigma2_fit"], w, label="homoscedastic", color="tab:red", alpha=0.7)
    ax.bar(x + w/2, df["het_sigma2_fit"], w, label="heteroscedastic", color="tab:blue", alpha=0.7)
    ax.plot(x, df["true_sigma2"], "k*", markersize=14, label="ground truth σ²")
    ax.set_yscale("log"); ax.set_xticks(x); ax.set_xticklabels(df["hull"], rotation=20, ha="right")
    ax.set_ylabel("σ² estimate (log)"); ax.set_title("Recovered observation noise per hull"); ax.legend()

    ax = axes[1]
    ax.bar(x - w/2, df["homo_rmse"], w, label="homo", color="tab:red", alpha=0.7)
    ax.bar(x + w/2, df["het_rmse"], w, label="het", color="tab:blue", alpha=0.7)
    ax.set_xticks(x); ax.set_xticklabels(df["hull"], rotation=20, ha="right")
    ax.set_ylabel("predictive RMSE"); ax.set_title("Held-out RMSE (lower=better)"); ax.legend()

    ax = axes[2]
    ax.bar(x - w/2, df["homo_ll"], w, label="homo", color="tab:red", alpha=0.7)
    ax.bar(x + w/2, df["het_ll"], w, label="het", color="tab:blue", alpha=0.7)
    ax.set_xticks(x); ax.set_xticklabels(df["hull"], rotation=20, ha="right")
    ax.set_ylabel("log-likelihood (higher=better)"); ax.set_title("Held-out log-likelihood"); ax.legend()

    plt.tight_layout(); plt.savefig(ROOT / "layer3_plot.png", dpi=120); plt.close()
    print(f"wrote {ROOT}/layer3_plot.png")


def plot_layer4():
    traces = pd.read_csv(ROOT / "layer4_traces.csv")
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    for seed in sorted(traces["seed"].unique()):
        sub = traces[traces["seed"] == seed]
        ax.plot(sub["iter"], sub["vanilla"], color="tab:red", alpha=0.2, lw=0.7)
        ax.plot(sub["iter"], sub["turbo"],   color="tab:blue", alpha=0.2, lw=0.7)
    V = traces.groupby("iter")["vanilla"].median().values
    T = traces.groupby("iter")["turbo"].median().values
    xs = np.arange(len(V))
    ax.plot(xs, V, color="tab:red", lw=2, label="vanilla BO (median)")
    ax.plot(xs, T, color="tab:blue", lw=2, label="TurBO (median)")
    ax.set_xlabel("iteration"); ax.set_ylabel("best fitness found so far")
    ax.set_title("Plateau-peak landscape: best-so-far trace"); ax.legend()

    ax = axes[1]
    # Gap (TurBO - vanilla) per iter, median across seeds
    gap = traces.groupby("iter").apply(lambda g: g["turbo"].median() - g["vanilla"].median())
    ax.plot(gap.index, gap.values, color="tab:purple", lw=2)
    ax.axhline(0, color="k", lw=0.5, ls=":")
    ax.set_xlabel("iteration"); ax.set_ylabel("median(turbo best) − median(vanilla best)")
    ax.set_title("TurBO advantage over iterations")

    plt.tight_layout(); plt.savefig(ROOT / "layer4_plot.png", dpi=120); plt.close()
    print(f"wrote {ROOT}/layer4_plot.png")


if __name__ == "__main__":
    if (ROOT / "layer3_agg.csv").exists():
        plot_layer3()
    if (ROOT / "layer4_traces.csv").exists():
        plot_layer4()
