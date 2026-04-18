"""Synthetic multi-hull stress test for the TTK-signal benchmark conclusions.

The real-data benchmark (`ttk_signal_benchmark.py`) runs on Hammerhead only
because no other hull has a full calibration log. To check whether "keep EB7"
generalizes across the hull distribution Phase 5D actually ships to, we
generate synthetic data spanning:

  • n_builds ∈ {100, 250, 500}
  • n_opponents ∈ {10, 30}
  • signal-to-noise σ_α²/σ_ε² ∈ {0.3, 1.0, 3.0}
  • covariate informativeness (R²(X7 → α)) ∈ {0.2, 0.5, 0.8}
  • duration regime ∈ {clean, informative-mediator, collider}
        - clean:           duration ⊥ α conditional on X7 (null)
        - mediator:        duration = f(α) + noise (Case 17 bad control)
        - collider:        duration = g(α, Y) + noise (joint descendant)

For each configuration we fit A0 / EB7 / EB8_dur / EB8_ttk and measure
correlation of α̂ with the true α (the latent quality we *actually* want to
recover, not a held-out probe proxy). This probes whether adding a Case-17
covariate *in the shrinkage prior* degrades ranking fidelity under conditions
that may arise in other hulls — e.g., smaller-n frigates, sparser opponent
pools, or weaker pre-battle predictors.

This is NOT a replacement for multi-hull live data; it's a stress test that
characterizes WHERE the real-data conclusion holds vs where it fails.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from starsector_optimizer.deconfounding import eb_shrinkage, twfe_decompose
from starsector_optimizer.models import EBShrinkageConfig

HERE = Path(__file__).parent


@dataclass
class Scenario:
    n_builds: int
    n_opponents: int
    snr: float
    r2_x7: float
    duration_regime: str  # "clean" | "mediator" | "collider"


def generate_data(scenario: Scenario, rng: np.random.Generator) -> dict:
    n, k = scenario.n_builds, scenario.n_opponents
    p_pre = 7

    sigma_alpha = np.sqrt(scenario.snr)
    sigma_eps = 1.0

    loading_var = scenario.r2_x7 / max(1 - scenario.r2_x7, 1e-6)
    gamma_true = rng.normal(scale=np.sqrt(loading_var / p_pre), size=p_pre)
    X_pre = rng.normal(size=(n, p_pre))
    alpha = X_pre @ gamma_true + sigma_alpha * rng.normal(size=n)
    alpha = alpha - alpha.mean()
    beta = 0.3 * rng.normal(size=k)
    Y = alpha[:, None] + beta[None, :] + sigma_eps * rng.normal(size=(n, k))

    # Missingness: each build observes ~60% of opponents to mimic real sparse
    # evaluation logs (Hammerhead avg 6/55).
    obs = rng.random((n, k)) < 0.6
    Y_observed = np.where(obs, Y, np.nan)

    build_durations_observed = np.where(
        obs,
        200.0 - 50.0 * alpha[:, None] + 20.0 * rng.normal(size=(n, k)),
        np.nan,
    )
    if scenario.duration_regime == "clean":
        build_dur_mean = 200.0 + 20.0 * rng.normal(size=n)
    elif scenario.duration_regime == "mediator":
        build_dur_mean = np.nanmean(build_durations_observed, axis=1)
    elif scenario.duration_regime == "collider":
        Y_mean = np.nanmean(np.where(obs, Y, np.nan), axis=1)
        build_dur_mean = (
            200.0 - 50.0 * alpha - 30.0 * Y_mean + 20.0 * rng.normal(size=n)
        )
    else:
        raise ValueError(f"unknown regime {scenario.duration_regime}")

    proj_ttk = X_pre @ rng.normal(size=p_pre) + 0.1 * rng.normal(size=n)

    return {
        "Y": Y_observed,
        "alpha_true": alpha,
        "X7": X_pre,
        "dur": build_dur_mean,
        "proj_ttk": proj_ttk,
    }


def twfe_sigma_sq(score_mat, alpha, beta):
    obs = ~np.isnan(score_mat)
    pred = alpha[:, None] + beta[None, :]
    diff = np.where(obs, score_mat - pred, 0.0)
    resid_sq = float(np.sum(diff * diff))
    n_obs = int(obs.sum())
    n_b, n_o = score_mat.shape
    denom = max(n_obs - (n_b + n_o - 1), 1)
    sigma_eps_sq = resid_sq / denom
    n_i = obs.sum(axis=1).clip(min=1)
    return sigma_eps_sq / n_i


def fit_variants(data: dict) -> dict[str, np.ndarray]:
    alpha_hat, beta_hat = twfe_decompose(data["Y"], n_iters=20, ridge=0.01)
    sigma_sq = twfe_sigma_sq(data["Y"], alpha_hat, beta_hat)
    cfg = EBShrinkageConfig()
    X7 = data["X7"]
    X8_dur = np.hstack([X7, data["dur"].reshape(-1, 1)])
    X8_ttk = np.hstack([X7, data["proj_ttk"].reshape(-1, 1)])
    X9 = np.hstack([X8_ttk, data["dur"].reshape(-1, 1)])
    out = {"A0": alpha_hat}
    out["EB7"], *_ = eb_shrinkage(alpha_hat, sigma_sq, X7, cfg)
    out["EB8_dur"], *_ = eb_shrinkage(alpha_hat, sigma_sq, X8_dur, cfg)
    out["EB8_ttk"], *_ = eb_shrinkage(alpha_hat, sigma_sq, X8_ttk, cfg)
    out["EB9_ttk_dur"], *_ = eb_shrinkage(alpha_hat, sigma_sq, X9, cfg)
    return out


def run() -> None:
    scenarios = []
    for n in (100, 250, 500):
        for k in (10, 30):
            for snr in (0.3, 1.0, 3.0):
                for r2 in (0.2, 0.5, 0.8):
                    for regime in ("clean", "mediator", "collider"):
                        scenarios.append(Scenario(n, k, snr, r2, regime))

    rows = []
    for sc in scenarios:
        deltas_by_rep = {k: [] for k in ("EB7", "EB8_dur", "EB8_ttk", "EB9_ttk_dur")}
        for rep in range(10):
            rng = np.random.default_rng(1000 * rep + hash(repr(sc)) % 10000)
            data = generate_data(sc, rng)
            ests = fit_variants(data)
            alpha_true = data["alpha_true"]
            for name, est in ests.items():
                if name == "A0":
                    continue
                rho_est = stats.spearmanr(est, alpha_true).statistic
                rho_a0 = stats.spearmanr(ests["A0"], alpha_true).statistic
                deltas_by_rep[name].append(float(rho_est - rho_a0))

        row = {
            "n_builds": sc.n_builds,
            "n_opp": sc.n_opponents,
            "snr": sc.snr,
            "r2_x7": sc.r2_x7,
            "regime": sc.duration_regime,
        }
        for name, deltas in deltas_by_rep.items():
            arr = np.asarray(deltas)
            row[f"{name}_dmean"] = float(arr.mean())
            row[f"{name}_dstd"] = float(arr.std())
        rows.append(row)

    df = pd.DataFrame(rows)
    out_csv = HERE / "synthetic_multihull_results.csv"
    df.to_csv(out_csv, index=False)
    print(f"Wrote {len(df)} scenarios → {out_csv}")

    print("\n=== AGGREGATE by duration regime (mean Δρ vs A0, averaged over all configs) ===")
    pivot = df.groupby("regime")[
        [c for c in df.columns if c.endswith("_dmean")]
    ].mean().round(4)
    print(pivot)

    print("\n=== AGGREGATE by n_builds × regime (Δρ EB8_dur − EB7) ===")
    df["delta_dur_vs_eb7"] = df["EB8_dur_dmean"] - df["EB7_dmean"]
    pivot2 = df.pivot_table(
        index="n_builds", columns="regime", values="delta_dur_vs_eb7"
    ).round(4)
    print(pivot2)

    print("\n=== AGGREGATE by SNR × regime (Δρ EB8_dur − EB7) ===")
    pivot3 = df.pivot_table(
        index="snr", columns="regime", values="delta_dur_vs_eb7"
    ).round(4)
    print(pivot3)

    # Scenario-level worst-case: where does EB8_dur hurt most?
    worst = df.nsmallest(10, "delta_dur_vs_eb7")[
        ["n_builds", "n_opp", "snr", "r2_x7", "regime",
         "EB7_dmean", "EB8_dur_dmean", "delta_dur_vs_eb7"]
    ]
    print("\n=== 10 worst scenarios for EB8_dur vs EB7 ===")
    print(worst.to_string(index=False))

    summary = {
        "regime_means": pivot.to_dict(),
        "n_x_regime": pivot2.to_dict(),
        "snr_x_regime": pivot3.to_dict(),
    }
    (HERE / "synthetic_multihull_summary.json").write_text(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    run()
