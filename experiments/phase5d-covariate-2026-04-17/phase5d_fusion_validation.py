"""Phase 5D Fusion-Paradigm Validation.

Revalidates Phase 5D after re-reading the literature. The prior validation
(`phase5d_validation.py`) showed that CUPED / FWL / PDS / ICP all hurt rank
correlation because they apply the WRONG paradigm: they condition on `h` (a
noisy proxy of α) and partial it out of Y. Four independent literature
surveys converged on the FUSION paradigm: h is a second noisy estimator of
the same latent α, combine by inverse variance / Bayes rule / EB-shrinkage-
toward-a-regression-prior.

This file tests the fusion-family estimators. Ship gate: Δρ ≥ +0.02 vs
shipped A (scalar CV) on the Hammerhead LOOO replay, AND Δρ ≥ +0.02 vs A0
(plain TWFE) — both are needed, because A itself may be slightly harmful.

Estimators:

  A0.    plain TWFE (reference)
  A.     shipped TWFE + scalar CV
  H1.    EB shrinkage w/ scalar prior mean γ̂·h + γ̂₀        (Efron-Morris 1975
                                                              + Ignatiadis-Wager 2022)
  HN.    EB shrinkage w/ full multi-covariate regression prior γ̂ᵀX
  IV.    Inverse-variance combine α̂ with γ̂ᵀh              (Graybill & Deal 1959)
  FA.    One-factor ML factor analysis on (α̂, X)            (Bollen 1989;
                                                              Jöreskog 1967)
  EBT.   HN + triple-goal rank correction                     (Lin, Louis & Shen 1999)

Usage:
    uv run python phase5d_fusion_validation.py
    uv run python phase5d_fusion_validation.py --quick          # n_seeds=5
    uv run python phase5d_fusion_validation.py --real-only
"""
from __future__ import annotations

import argparse
import json
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
from sklearn.decomposition import FactorAnalysis
from sklearn.exceptions import ConvergenceWarning

warnings.filterwarnings("ignore", category=ConvergenceWarning)

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import phase5d_validation as p5d  # noqa: E402

HAMMERHEAD_LOG = p5d.HAMMERHEAD_LOG
OUT_DIR = HERE

plt.style.use("seaborn-v0_8-darkgrid")
plt.rcParams["figure.figsize"] = (14, 8)


# ═══════════════════════════════════════════════════════════════════════════
# Standard-error estimation for TWFE α̂_i
# ═══════════════════════════════════════════════════════════════════════════

def twfe_standard_errors(score_mat: np.ndarray, alpha: np.ndarray,
                         beta: np.ndarray) -> np.ndarray:
    """Return σ̂_i ≈ σ̂_ε / √n_i for each build's α̂_i.

    σ̂_ε² estimated from pooled observed residuals. n_i = number of observed
    opponents for build i. This is the classical "standard error of the
    mean" argument for the two-way fixed-effects within estimator when the
    matchup graph is approximately balanced — exact when every build faces
    the same number of opponents and the residual variance is homoscedastic.
    """
    n_b, n_o = score_mat.shape
    observed = ~np.isnan(score_mat)
    residuals = np.where(observed, score_mat - alpha[:, None] - beta[None, :],
                         np.nan)
    n_total = int(observed.sum())
    # Degrees of freedom: n_obs − n_b − n_o + 1 (one intercept collinearity).
    dof = max(n_total - n_b - n_o + 1, 1)
    sigma2_eps = float(np.nansum(residuals ** 2) / dof)
    n_i = observed.sum(axis=1).clip(min=1)
    return np.sqrt(sigma2_eps / n_i)


# ═══════════════════════════════════════════════════════════════════════════
# Fusion-paradigm estimators
# ═══════════════════════════════════════════════════════════════════════════

def estimator_H_eb_shrinkage(
    score_mat: np.ndarray, X_build: np.ndarray,
    floor_tau2_frac: float = 0.05,
) -> tuple[np.ndarray, dict]:
    """Empirical-Bayes shrinkage toward a regression prior.

    Prior: α_i | X_i ~ N(γᵀ[1, X_i], τ²)
    Likelihood: α̂_i | α_i ~ N(α_i, σ̂_i²)
    Posterior mean:
        α̂_EB_i = w_i · α̂_i + (1 − w_i) · (γ̂ᵀ[1, X_i])
        w_i    = τ̂² / (τ̂² + σ̂_i²)

    γ̂ via OLS (attenuation is minor when α̂ is close to α). τ̂² by MoM:
        τ̂² = max(Var(α̂ − γ̂ᵀX) − E[σ̂_i²],  floor_tau2_frac · Var(α̂))

    floor_tau2_frac prevents over-shrinkage when the MoM estimate collapses
    (noise in α̂ explained entirely by OLS residual, which can happen with
    small N and high-fidelity X).
    """
    alpha_hat, beta_hat = p5d.twfe_plain(score_mat)
    sigma_i = twfe_standard_errors(score_mat, alpha_hat, beta_hat)

    # Prior regression α̂ ~ [1, X] (one-pass OLS; attenuation ignored at first order)
    X_full = np.column_stack([np.ones(len(alpha_hat)), X_build])
    gamma, *_ = np.linalg.lstsq(X_full, alpha_hat, rcond=None)
    alpha_prior = X_full @ gamma

    residual = alpha_hat - alpha_prior
    total_var = float(residual.var())
    mean_sigma2 = float((sigma_i ** 2).mean())
    floor = floor_tau2_frac * float(alpha_hat.var())
    tau2 = max(total_var - mean_sigma2, floor)

    w = tau2 / (tau2 + sigma_i ** 2)
    alpha_eb = w * alpha_hat + (1 - w) * alpha_prior
    diag = {"tau2": tau2, "mean_sigma2": mean_sigma2,
            "w_mean": float(w.mean()), "w_std": float(w.std()),
            "gamma_norm": float(np.linalg.norm(gamma))}
    return alpha_eb, diag


def estimator_IV_inverse_variance(
    score_mat: np.ndarray, X_build: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """Inverse-variance combination of α̂ with a regression-scaled prior (Graybill-Deal 1959).

    α̃_i = (α̂_i/σ̂_i² + ĥ_i/σ̂_h²) / (1/σ̂_i² + 1/σ̂_h²)

    ĥ_i = γ̂ᵀ[1, X_i] is the scaled prior. σ̂_h² estimated from the OLS
    residual variance of the α̂ ~ X regression (empirical fit quality of h).
    Structurally differs from EB only in that no MoM separation of
    between/within variance is performed.
    """
    alpha_hat, beta_hat = p5d.twfe_plain(score_mat)
    sigma_i = twfe_standard_errors(score_mat, alpha_hat, beta_hat)

    X_full = np.column_stack([np.ones(len(alpha_hat)), X_build])
    gamma, *_ = np.linalg.lstsq(X_full, alpha_hat, rcond=None)
    h_scaled = X_full @ gamma
    sigma_h2 = float((alpha_hat - h_scaled).var())

    inv_var_data = 1.0 / (sigma_i ** 2 + 1e-12)
    inv_var_h = 1.0 / max(sigma_h2, 1e-12)
    w_data = inv_var_data / (inv_var_data + inv_var_h)
    alpha_iv = w_data * alpha_hat + (1 - w_data) * h_scaled
    diag = {"sigma_h2": sigma_h2, "w_data_mean": float(w_data.mean())}
    return alpha_iv, diag


def estimator_FA_one_factor(
    score_mat: np.ndarray, X_build: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """Maximum-likelihood one-factor analysis over (α̂, X_build).

    Model: Y_ik = λ_k · α_i + ε_ik, ε_ik ~ N(0, ψ_k), k = 1..K indicators.
    Factor scores α̂_i estimated by regression method (Thurstone 1935):
        α̂_i = λᵀ Σ⁻¹ Y_i
    where Σ = λλᵀ + diag(ψ) is the implied covariance matrix.

    Uses sklearn.decomposition.FactorAnalysis with n_components=1.
    """
    alpha_hat, _ = p5d.twfe_plain(score_mat)
    Y = np.column_stack([alpha_hat, X_build])
    mu = Y.mean(axis=0)
    std = Y.std(axis=0).clip(min=1e-10)
    Y_std = (Y - mu) / std
    fa = FactorAnalysis(n_components=1, random_state=0, max_iter=200)
    scores = fa.fit_transform(Y_std).ravel()
    # Align sign: positive correlation with α̂_TWFE
    if np.corrcoef(scores, alpha_hat)[0, 1] < 0:
        scores = -scores
    diag = {"loadings": fa.components_.ravel().tolist(),
            "noise_variance": fa.noise_variance_.tolist()}
    return scores, diag


def estimator_H_pds_prior(
    score_mat: np.ndarray, X_build: np.ndarray, seed: int = 0,
    floor_tau2_frac: float = 0.05,
) -> tuple[np.ndarray, dict]:
    """EB shrinkage where the prior regression is PDS-selected.

    Combines the fusion paradigm (shrinkage toward prior) with Stage-2
    selection (PDS lasso) inside the prior itself. The selection happens at
    the between-build (cross-sectional) level where it is well-posed — unlike
    the within-TWFE Phase 5D regression where build-only covariates are
    collinear with α and PDS selects every column.
    """
    alpha_hat, beta_hat = p5d.twfe_plain(score_mat)
    sigma_i = twfe_standard_errors(score_mat, alpha_hat, beta_hat)
    sel = p5d.pds_between(alpha_hat, X_build, seed=seed)
    if sel.sum() == 0:
        return alpha_hat, {"n_selected": 0, "tau2": float("nan"),
                            "w_mean": 1.0}
    X_sub = X_build[:, sel]
    X_full = np.column_stack([np.ones(len(alpha_hat)), X_sub])
    gamma, *_ = np.linalg.lstsq(X_full, alpha_hat, rcond=None)
    alpha_prior = X_full @ gamma
    residual = alpha_hat - alpha_prior
    total_var = float(residual.var())
    mean_sigma2 = float((sigma_i ** 2).mean())
    floor = floor_tau2_frac * float(alpha_hat.var())
    tau2 = max(total_var - mean_sigma2, floor)
    w = tau2 / (tau2 + sigma_i ** 2)
    alpha_eb = w * alpha_hat + (1 - w) * alpha_prior
    return alpha_eb, {"n_selected": int(sel.sum()), "tau2": tau2,
                       "w_mean": float(w.mean())}


def triple_goal(posterior: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """Lin-Louis-Shen 1999 triple-goal ranking.

    Preserves the ORDER of the posterior means but substitutes their values
    with the sorted raw α̂ values — so the histogram of α̂ is preserved while
    using the posterior's (shrinkage-improved) rank ordering.
    """
    n = len(posterior)
    ranks = np.argsort(np.argsort(posterior))
    raw_sorted = np.sort(raw)
    return raw_sorted[ranks]


# ═══════════════════════════════════════════════════════════════════════════
# Run-one-seed
# ═══════════════════════════════════════════════════════════════════════════

def run_one_seed(seed: int, n_builds: int, n_opp: int) -> dict:
    rng = np.random.default_rng(seed)
    import curriculum_simulation as cs
    opponents = cs.generate_opponents(n_opp, rng)
    builds = p5d.generate_xbuilds(n_builds, rng)
    schedule = p5d.build_schedule(n_builds, opponents, p5d.N_ACTIVE_DEFAULT,
                                   p5d.N_ANCHORS_DEFAULT, p5d.N_INCUMBENT_OVERLAP, rng)
    score_mat, bi, oi, _ = p5d.collect_run(builds, opponents, schedule, rng)

    truth = np.array([b.quality for b in builds])
    heuristic_i = np.array([b.heuristic for b in builds])
    # 8-col X_build: 4 useful + 4 noise (matching the Phase 5D validation)
    X_full = np.vstack([np.concatenate([b.x_useful, b.x_noise]) for b in builds])
    X_full = (X_full - X_full.mean(axis=0)) / X_full.std(axis=0).clip(min=1e-10)
    X_h_only = heuristic_i.reshape(-1, 1)
    X_h_only = (X_h_only - X_h_only.mean(axis=0)) \
                / X_h_only.std(axis=0).clip(min=1e-10)

    out = {"seed": seed, "n_obs": int(np.isfinite(score_mat).sum())}

    # A0 plain TWFE
    t0 = time.time()
    a_A0, _ = p5d.twfe_plain(score_mat)
    out["A0_wall"] = time.time() - t0

    # A shipped scalar CV
    t0 = time.time()
    a_A = p5d.estimator_baseline_A(score_mat, heuristic_i)
    out["A_wall"] = time.time() - t0

    # H1: EB with scalar h prior
    t0 = time.time()
    a_H1, diag_H1 = estimator_H_eb_shrinkage(score_mat, X_h_only)
    out["H1_wall"] = time.time() - t0
    out["H1_tau2"] = diag_H1["tau2"]
    out["H1_w_mean"] = diag_H1["w_mean"]

    # HN: EB with full (h implicit via scorer-like proxies + noise) prior
    t0 = time.time()
    a_HN, diag_HN = estimator_H_eb_shrinkage(score_mat, X_full)
    out["HN_wall"] = time.time() - t0
    out["HN_tau2"] = diag_HN["tau2"]
    out["HN_w_mean"] = diag_HN["w_mean"]

    # HP: EB with PDS-selected prior regression
    t0 = time.time()
    a_HP, diag_HP = estimator_H_pds_prior(score_mat, X_full, seed=seed)
    out["HP_wall"] = time.time() - t0
    out["HP_n_selected"] = diag_HP["n_selected"]

    # IV: inverse-variance w/ full regression
    t0 = time.time()
    a_IV, _ = estimator_IV_inverse_variance(score_mat, X_full)
    out["IV_wall"] = time.time() - t0

    # FA: one-factor ML
    t0 = time.time()
    a_FA, diag_FA = estimator_FA_one_factor(score_mat, X_full)
    out["FA_wall"] = time.time() - t0

    # EBT: HN + triple-goal
    t0 = time.time()
    a_EBT = triple_goal(a_HN, a_A0)
    out["EBT_wall"] = time.time() - t0

    all_est = [("A0", a_A0), ("A", a_A), ("H1", a_H1), ("HN", a_HN),
               ("HP", a_HP), ("IV", a_IV), ("FA", a_FA), ("EBT", a_EBT)]
    is_exploit = np.array([b.has_exploit for b in builds])
    for name, a_est in all_est:
        out[f"rho_{name}"] = float(stats.spearmanr(a_est, truth).statistic)
        if is_exploit.sum() >= 3:
            out[f"exploit_rho_{name}"] = float(
                stats.spearmanr(a_est[is_exploit], truth[is_exploit]).statistic)

    for name, a_est in all_est:
        out[f"var_{name}"] = float(a_est.var())

    # Top-k recall: what fraction of true top-10 builds are in est top-10?
    true_top = set(np.argsort(-truth)[:10])
    for name, a_est in all_est:
        est_top = set(np.argsort(-a_est)[:10])
        out[f"top10_{name}"] = len(true_top & est_top) / 10

    return out


# ═══════════════════════════════════════════════════════════════════════════
# Hammerhead replay
# ═══════════════════════════════════════════════════════════════════════════

def hammerhead_replay() -> dict:
    print("  Loading + re-scoring 368 Hammerhead builds...")
    records, scorer_keys = p5d.load_hammerhead()
    records = [r for r in records if not r["pruned"]]
    n_b = len(records)
    opp_names = sorted({o["opponent"]
                        for r in records for o in r["opponent_results"]})
    opp_idx = {n: i for i, n in enumerate(opp_names)}
    n_o = len(opp_names)
    score_mat = np.full((n_b, n_o), np.nan)
    bi_list, oi_list = [], []
    for bi_, r in enumerate(records):
        for o in r["opponent_results"]:
            y = float(o["hp_differential"])
            if o["winner"] == "TIMEOUT":
                y *= 0.5
            score_mat[bi_, opp_idx[o["opponent"]]] = y
            bi_list.append(bi_); oi_list.append(opp_idx[o["opponent"]])
    bi = np.asarray(bi_list); oi = np.asarray(oi_list)

    scorer_mat = np.array([[r["scorer"][k] for k in scorer_keys] for r in records])
    extras = np.array([[r["n_hullmods"], r["build"]["flux_vents"],
                        r["build"]["flux_capacitors"]] for r in records],
                       dtype=float)
    X_build_raw = np.hstack([scorer_mat, extras])
    X_build = (X_build_raw - X_build_raw.mean(axis=0)) \
              / X_build_raw.std(axis=0).clip(min=1e-10)
    heuristic_i = np.array([r["scorer"]["composite"] for r in records])
    X_h = heuristic_i.reshape(-1, 1)
    X_h = (X_h - X_h.mean()) / X_h.std(ddof=0).clip(min=1e-10)

    def fit_all(score: np.ndarray) -> dict[str, np.ndarray]:
        a_A0, _ = p5d.twfe_plain(score)
        a_A = p5d.estimator_baseline_A(score, heuristic_i)
        a_H1, _ = estimator_H_eb_shrinkage(score, X_h)
        a_HN, _ = estimator_H_eb_shrinkage(score, X_build)
        a_HP, _ = estimator_H_pds_prior(score, X_build, seed=0)
        a_IV, _ = estimator_IV_inverse_variance(score, X_build)
        a_FA, _ = estimator_FA_one_factor(score, X_build)
        a_EBT = triple_goal(a_HN, a_A0)
        return {"A0": a_A0, "A": a_A, "H1": a_H1, "HN": a_HN, "HP": a_HP,
                 "IV": a_IV, "FA": a_FA, "EBT": a_EBT}

    all_est = fit_all(score_mat)

    # Ship gate: LOOO on top-5 most-sampled anchors, bootstrap CIs on
    # build axis for each (probe × estimator).
    opp_counts = np.sum(np.isfinite(score_mat), axis=0)
    probe_opps = np.argsort(-opp_counts)[:5]
    gate_rows = []
    rng_boot = np.random.default_rng(0)
    n_boot = 200
    for probe in probe_opps:
        probe_name = opp_names[probe]
        probe_y = score_mat[:, probe]
        score_red = score_mat.copy(); score_red[:, probe] = np.nan
        ests = fit_all(score_red)
        valid = np.isfinite(probe_y)
        n_v = valid.sum()
        for name, est in ests.items():
            est_v = est[valid]; probe_v = probe_y[valid]
            rho = stats.spearmanr(est_v, probe_v).statistic
            # Bootstrap CI
            boot_rhos = []
            n_v_int = int(n_v)
            idx_valid = np.where(valid)[0]
            for _ in range(n_boot):
                idx = rng_boot.choice(idx_valid, size=n_v_int, replace=True)
                br = stats.spearmanr(est[idx], probe_y[idx]).statistic
                if np.isfinite(br):
                    boot_rhos.append(br)
            ci_lo = float(np.quantile(boot_rhos, 0.025)) if boot_rhos else np.nan
            ci_hi = float(np.quantile(boot_rhos, 0.975)) if boot_rhos else np.nan
            gate_rows.append({"probe_opp": probe_name, "estimator": name,
                              "rho_vs_probe": float(rho),
                              "ci_lo": ci_lo, "ci_hi": ci_hi,
                              "n_valid": int(n_v)})
    gate_df = pd.DataFrame(gate_rows)
    mean_rhos = gate_df.groupby("estimator")["rho_vs_probe"].mean().to_dict()
    return {"gate_df": gate_df, "mean_rhos": mean_rhos,
            "n_builds_fit": n_b, "n_obs": int(np.isfinite(score_mat).sum()),
            "n_opponents": n_o,
            "var_alpha": {k: float(v.var()) for k, v in all_est.items()},
            "alpha": {k: v.tolist() for k, v in all_est.items()}}


# ═══════════════════════════════════════════════════════════════════════════
# Plotting
# ═══════════════════════════════════════════════════════════════════════════

def plot_synthetic(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    keys = ["A0", "A", "H1", "HN", "HP", "IV", "FA", "EBT"]

    ax = axes[0, 0]
    data = [df[f"rho_{k}"].values for k in keys]
    ax.boxplot(data, tick_labels=keys, showmeans=True)
    ax.axhline(df["rho_A0"].mean(), color="gray", ls=":", label="A0 mean")
    ax.axhline(df["rho_A"].mean(), color="C1", ls=":", label="A mean (shipped)")
    ax.set_title("rho(alpha-hat, truth) — fusion paradigm")
    ax.set_ylabel("Spearman rho"); ax.legend(fontsize=9)

    ax = axes[0, 1]
    data = [df[f"top10_{k}"].values for k in keys]
    ax.boxplot(data, tick_labels=keys, showmeans=True)
    ax.axhline(df["top10_A0"].mean(), color="gray", ls=":")
    ax.axhline(df["top10_A"].mean(), color="C1", ls=":")
    ax.set_title("Top-10 recall of true top-10 (higher = better exploitation signal)")
    ax.set_ylabel("fraction")

    ax = axes[1, 0]
    data = [df[f"exploit_rho_{k}"].values for k in keys]
    ax.boxplot(data, tick_labels=keys, showmeans=True)
    ax.set_title("Exploit-cluster rho (within-cluster ranking of exploit builds)")
    ax.set_ylabel("Spearman rho")

    ax = axes[1, 1]
    ax.bar(keys, [df[f"var_{k}"].mean() for k in keys])
    ax.set_title("var(alpha-hat) — lower = stronger shrinkage")
    ax.set_ylabel("variance of alpha-hat")
    ax.tick_params(axis="x", rotation=0)

    plt.tight_layout()
    plt.savefig(out, dpi=130)
    plt.close()


def plot_hammerhead(ham: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    df = ham["gate_df"]
    piv = df.pivot(index="probe_opp", columns="estimator", values="rho_vs_probe")
    # reorder cols for comparison
    col_order = [c for c in ["A0", "A", "H1", "HN", "HP", "IV", "FA", "EBT"] if c in piv.columns]
    piv = piv[col_order]
    piv.plot.bar(ax=axes[0])
    axes[0].set_title("Hammerhead LOOO: rho(alpha-hat without probe, raw Y on probe)")
    axes[0].set_ylabel("Spearman rho"); axes[0].tick_params(axis="x", rotation=20)

    ax = axes[1]
    means = ham["mean_rhos"]
    ordered = [(k, means[k]) for k in col_order if k in means]
    ax.bar([k for k, _ in ordered], [v for _, v in ordered], color="C0")
    for i, (k, v) in enumerate(ordered):
        ax.text(i, v + 0.005, f"{v:.3f}", ha="center")
    ax.set_title("Ship-gate mean rho across 3 anchor probes")
    ax.set_ylim(0, max(v for _, v in ordered) * 1.2)

    plt.tight_layout()
    plt.savefig(out, dpi=130)
    plt.close()


# ═══════════════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════════════

def sweep(n_seeds: int) -> pd.DataFrame:
    rows = []
    for seed in range(n_seeds):
        t0 = time.time()
        r = run_one_seed(seed, p5d.N_BUILDS_DEFAULT, p5d.N_OPPONENTS_DEFAULT)
        r["total_wall"] = time.time() - t0
        rows.append(r)
        print(f"  seed {seed:2d} {r['total_wall']:4.1f}s  "
              f"A0={r['rho_A0']:+.3f} A={r['rho_A']:+.3f} "
              f"H1={r['rho_H1']:+.3f} HN={r['rho_HN']:+.3f} HP={r['rho_HP']:+.3f} "
              f"IV={r['rho_IV']:+.3f} FA={r['rho_FA']:+.3f} EBT={r['rho_EBT']:+.3f}")
    return pd.DataFrame(rows)


def paired_wilcoxon(df: pd.DataFrame, a: str, b: str) -> tuple[float, float]:
    diff = df[a] - df[b]
    try:
        return float(diff.mean()), float(stats.wilcoxon(diff).pvalue)
    except ValueError:
        return float(diff.mean()), float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--synthetic-only", action="store_true")
    ap.add_argument("--real-only", action="store_true")
    ap.add_argument("--n-seeds", type=int, default=20)
    args = ap.parse_args()
    n_seeds = 5 if args.quick else args.n_seeds

    if not args.real_only:
        print(f"[1/2] Synthetic fusion sweep — n_seeds={n_seeds}")
        df = sweep(n_seeds)
        df.to_csv(OUT_DIR / "fusion_results.csv", index=False)
        plot_synthetic(df, OUT_DIR / "fusion_synthetic.png")

        keys = ["A0", "A", "H1", "HN", "HP", "IV", "FA", "EBT"]
        print("\n--- rho(alpha-hat, truth): mean ± std across seeds ---")
        sumd = df[[f"rho_{k}" for k in keys]].describe().loc[["mean", "std"]].round(3)
        print(sumd.to_string())

        print("\n--- Top-10 recall of true top-10 (mean) ---")
        for k in keys:
            print(f"  {k}: {df[f'top10_{k}'].mean():.2f} ± {df[f'top10_{k}'].std():.2f}")

        print("\n--- Paired Wilcoxon vs A0 (plain TWFE) ---")
        for k in ["A", "H1", "HN", "HP", "IV", "FA", "EBT"]:
            m, p = paired_wilcoxon(df, f"rho_{k}", "rho_A0")
            print(f"  {k} − A0:  Δρ = {m:+.3f}   p = {p:.4f}")

        print("\n--- Paired Wilcoxon vs A (shipped scalar CV) ---")
        for k in ["A0", "H1", "HN", "HP", "IV", "FA", "EBT"]:
            m, p = paired_wilcoxon(df, f"rho_{k}", "rho_A")
            print(f"  {k} − A:   Δρ = {m:+.3f}   p = {p:.4f}")

        print("\n--- var(alpha-hat) ---")
        for k in keys:
            print(f"  {k}: {df[f'var_{k}'].mean():.4f}")

        print("\n--- EB diagnostics ---")
        print(f"  H1 mean τ² = {df['H1_tau2'].mean():.4f},  w̄ = {df['H1_w_mean'].mean():.2f}")
        print(f"  HN mean τ² = {df['HN_tau2'].mean():.4f},  w̄ = {df['HN_w_mean'].mean():.2f}")

    if not args.synthetic_only:
        print("\n[2/2] Hammerhead replay — ship-gate LOOO on anchor probes")
        ham = hammerhead_replay()
        plot_hammerhead(ham, OUT_DIR / "fusion_hammerhead.png")
        ham["gate_df"].to_csv(OUT_DIR / "fusion_hammerhead_gate.csv", index=False)
        print(f"  n_builds_fit={ham['n_builds_fit']}, n_obs={ham['n_obs']}, n_opp={ham['n_opponents']}")
        print(f"\n  Mean rho across probes:")
        for k, v in sorted(ham['mean_rhos'].items(), key=lambda x: -x[1]):
            print(f"    {k}: {v:+.3f}")
        print(f"\n  var(alpha-hat):  {ham['var_alpha']}")

        # Ship gate evaluation, including paired Wilcoxon across probes.
        print("\n  --- SHIP-GATE EVALUATION ---")
        baseline_A0 = ham['mean_rhos']['A0']
        baseline_A = ham['mean_rhos']['A']
        gdf = ham["gate_df"]
        for k in ["H1", "HN", "HP", "IV", "FA", "EBT"]:
            d_A0 = ham['mean_rhos'][k] - baseline_A0
            d_A = ham['mean_rhos'][k] - baseline_A
            # Paired over probes
            rho_k = gdf[gdf["estimator"] == k]["rho_vs_probe"].values
            rho_A0 = gdf[gdf["estimator"] == "A0"]["rho_vs_probe"].values
            try:
                p_A0 = float(stats.wilcoxon(rho_k - rho_A0).pvalue)
            except ValueError:
                p_A0 = float("nan")
            gate = "PASS" if (d_A0 >= 0.02 and d_A >= 0.02) else "FAIL"
            print(f"  {k}: Δρ vs A0 = {d_A0:+.3f} (p={p_A0:.3f}), "
                  f"vs A = {d_A:+.3f}  [{gate}]")


if __name__ == "__main__":
    main()
