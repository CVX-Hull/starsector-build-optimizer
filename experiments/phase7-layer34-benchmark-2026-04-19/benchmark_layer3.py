"""Layer 3 benchmark — heteroscedastic GP on mixed-hull training set.

Claim: a class-blocked heteroscedastic GP correctly learns per-hull noise
structure when trained on mixed data, whereas a homoscedastic GP with a
single noise parameter distorts its posterior to fit the highest-noise
hull's variance everywhere.

Setup:
    - 4 "hulls", each with different (τ², σ²) drawn from the aborted-run
      observations.
    - 100 samples per hull, 6-dim input.
    - Fit both models jointly across all 400 observations.
    - Evaluate the posterior on held-out test points per hull.

Success criteria:
    - hetGP's learned noise_per_class matches ground-truth σ² within 2×.
    - hetGP's posterior log-likelihood on held-out points is >= homoscedastic's,
      especially on the high-noise hulls.
    - hetGP's per-hull predictive RMSE is lower on the low-noise hull (the
      "learnable" one) because it doesn't inflate its noise estimate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Ensure local src/ is importable regardless of invocation CWD
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.synthetic import PRESETS
from src.gp import HomoscedasticGP, HeteroscedasticGP


def run_once(seed, n_per_hull=100, n_test=40):
    hulls = ["frigate_flat", "frigate_weak", "destroyer_signal", "cruiser_signal"]
    rng = np.random.default_rng(seed)
    X_all, y_all, cls_all = [], [], []
    true_sigma2 = []
    X_test_per, y_true_per, y_noisy_per = [], [], []
    for cls_idx, name in enumerate(hulls):
        L = PRESETS[name]
        Xh = rng.uniform(0, 1, size=(n_per_hull, L.dim))
        yh = np.array([L.sample(x, rng=rng) for x in Xh])
        X_all.append(Xh); y_all.append(yh); cls_all.append(np.full(n_per_hull, cls_idx))
        true_sigma2.append(L.sigma2)
        Xt = rng.uniform(0, 1, size=(n_test, L.dim))
        y_true = L.f_batch(Xt)
        y_noisy = np.array([L.sample(x, rng=rng) for x in Xt])
        X_test_per.append(Xt); y_true_per.append(y_true); y_noisy_per.append(y_noisy)

    X = np.vstack(X_all); y = np.concatenate(y_all); cls = np.concatenate(cls_all)

    # Homoscedastic baseline — fits one σ² across all data
    homo = HomoscedasticGP().fit(X, y)

    # Heteroscedastic — per-hull noise class
    het_init = np.array([1e-2] * 4)  # agnostic starting point
    het = HeteroscedasticGP(n_classes=4, noise_init_per_class=het_init).fit(X, y, cls)

    # Per-hull evaluation on held-out test points
    rows = []
    for cls_idx, name in enumerate(hulls):
        Xt = X_test_per[cls_idx]; y_true = y_true_per[cls_idx]; y_noisy = y_noisy_per[cls_idx]
        mu_homo, var_homo = homo.predict(Xt)
        mu_het, var_het = het.predict(Xt, np.full(len(Xt), cls_idx))
        rmse_homo = float(np.sqrt(np.mean((mu_homo - y_true) ** 2)))
        rmse_het = float(np.sqrt(np.mean((mu_het - y_true) ** 2)))
        # Log-likelihood of observed noisy test values under each posterior
        def ll(mu, var, y):
            return float(np.mean(-0.5 * np.log(2 * np.pi * var) - 0.5 * (y - mu) ** 2 / var))
        ll_homo = ll(mu_homo, var_homo, y_noisy)
        ll_het = ll(mu_het, var_het, y_noisy)
        rows.append({
            "hull": name,
            "true_sigma2": true_sigma2[cls_idx],
            "homo_sigma2_fit": float(homo.noise),
            "het_sigma2_fit": float(het.noise_per_class[cls_idx]),
            "homo_rmse": rmse_homo,
            "het_rmse": rmse_het,
            "homo_ll": ll_homo,
            "het_ll": ll_het,
            "homo_mean_pred_var": float(var_homo.mean()),
            "het_mean_pred_var": float(var_het.mean()),
        })
    return rows


def main():
    all_rows = []
    for seed in range(5):
        all_rows.extend(run_once(seed))
    # Aggregate per hull
    import pandas as pd
    df = pd.DataFrame(all_rows)
    agg = df.groupby("hull").agg({
        "true_sigma2": "first",
        "homo_sigma2_fit": "mean",
        "het_sigma2_fit": "mean",
        "homo_rmse": "mean",
        "het_rmse": "mean",
        "homo_ll": "mean",
        "het_ll": "mean",
        "homo_mean_pred_var": "mean",
        "het_mean_pred_var": "mean",
    })
    print("=" * 100)
    print("Layer 3 benchmark — heteroscedastic GP on mixed-hull data (5 seeds, 100 pts/hull)")
    print("=" * 100)
    print()
    print("Per-hull noise recovery (lower 'fit_error' is better):")
    for hull, row in agg.iterrows():
        true_s = row["true_sigma2"]
        homo_s = row["homo_sigma2_fit"]
        het_s = row["het_sigma2_fit"]
        homo_err = abs(np.log10(homo_s / true_s))
        het_err = abs(np.log10(het_s / true_s))
        print(f"  {hull:22s} true σ²={true_s:10.2e}  "
              f"homo fit={homo_s:10.2e} (err={homo_err:5.2f} orders)  "
              f"het fit={het_s:10.2e} (err={het_err:5.2f} orders)")
    print()
    print("Per-hull predictive RMSE (lower is better):")
    for hull, row in agg.iterrows():
        print(f"  {hull:22s} homo RMSE={row['homo_rmse']:.4f}   het RMSE={row['het_rmse']:.4f}   "
              f"Δ={row['het_rmse']-row['homo_rmse']:+.4f}")
    print()
    print("Per-hull held-out log-likelihood (higher is better):")
    for hull, row in agg.iterrows():
        print(f"  {hull:22s} homo LL={row['homo_ll']:+7.3f}   het LL={row['het_ll']:+7.3f}   "
              f"Δ={row['het_ll']-row['homo_ll']:+7.3f}")

    df.to_csv(ROOT / "layer3_raw.csv", index=False)
    agg.to_csv(ROOT / "layer3_agg.csv")
    print(f"\nSaved layer3_raw.csv and layer3_agg.csv to {ROOT}")


if __name__ == "__main__":
    main()
