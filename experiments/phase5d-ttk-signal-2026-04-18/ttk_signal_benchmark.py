"""Phase 5D TTK-signal benchmark — LOOO Δρ + placebo test.

Variants:
  A0           TWFE baseline on hp_differential
  A            TWFE + shipped scalar control variate
  EB7          shipped Phase 5D (7 pre-battle covariates)
  EB7_LEX      EB7 fit on lexicographically-perturbed Y (tiebreaker within hp-tier)
  EB8_dur      EB7 + raw build-mean duration_seconds   (NEGATIVE CONTROL)
  EB8_ttk      EB7 + pre-battle projected TTK = eff_hp / total_dps
  EB8_aft      EB7 + Weibull-AFT log-duration residual (build-mean)

Gate: Δρ vs A0 and A on LOOO over top-5 anchor probes, 200 bootstraps.

Placebo test (Eggers-Tuñón 2024, AJPS doi 10.1111/ajps.12818):
  Hold out duration from the covariate set. Regress build-mean duration on
  α̂_TWFE after residualizing both on the 7 pre-battle covariates.
  A nonzero partial correlation confirms duration carries outcome information
  beyond what X contains → duration is post-treatment-contaminated, and its
  inclusion as a covariate would leak realized-battle noise into α̂_EB.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from starsector_optimizer.deconfounding import (
    eb_shrinkage,
    triple_goal_rank,
    twfe_decompose,
)
from starsector_optimizer.models import Build, EBShrinkageConfig
from starsector_optimizer.parser import load_game_data
from starsector_optimizer.scorer import heuristic_score
from starsector_optimizer.optimizer import _EBRecord, _build_covariate_vector


HERE = Path(__file__).parent
DEFAULT_LOG = (
    HERE.parent / "hammerhead-twfe-2026-04-13" / "evaluation_log.jsonl"
)
GAME_DIR = Path("/home/sdai/ClaudeCode/game/starsector")


def _log_path() -> Path:
    import os
    override = os.environ.get("TTK_BENCHMARK_LOG")
    return Path(override) if override else DEFAULT_LOG


HAMMERHEAD_LOG = _log_path()

N_BOOT = 200
N_PROBES = 10
GATE_MARGIN = 0.02
TIMEOUT_CEIL = 300.0
LEX_EPSILONS = (0.001, 0.01, 0.1)


def load_records() -> list[dict]:
    records = [json.loads(line) for line in HAMMERHEAD_LOG.read_text().splitlines()]
    gd = load_game_data(GAME_DIR)
    hull = gd.hulls["hammerhead"]
    keep: list[dict] = []
    for r in records:
        if r.get("pruned"):
            continue
        b = r["build"]
        build = Build(
            hull_id=b["hull_id"],
            weapon_assignments={
                k: v for k, v in b["weapon_assignments"].items() if v is not None
            },
            hullmods=frozenset(b["hullmods"]),
            flux_vents=b["flux_vents"],
            flux_capacitors=b["flux_capacitors"],
        )
        r["_scorer_result"] = heuristic_score(build, hull, gd)
        keep.append(r)
    return keep


def build_score_matrix(
    records: list[dict], lex_epsilon: float = 0.0
) -> tuple[np.ndarray, list[str]]:
    """(n_builds, n_opps) Y matrix from hp_differential.

    lex_epsilon > 0 applies a lexicographic tiebreaker: subtract
    epsilon * (duration/TIMEOUT_CEIL) so faster wins rank above slower wins
    with the same hp_differential.
    """
    opps = sorted({o["opponent"] for r in records for o in r["opponent_results"]})
    opp_idx = {o: i for i, o in enumerate(opps)}
    n_b, n_o = len(records), len(opps)
    score = np.full((n_b, n_o), np.nan)
    for bi, r in enumerate(records):
        for o in r["opponent_results"]:
            y = float(o["hp_differential"])
            if o["winner"] == "TIMEOUT":
                y *= 0.5
            if lex_epsilon > 0.0:
                y -= lex_epsilon * (float(o["duration_seconds"]) / TIMEOUT_CEIL)
            score[bi, opp_idx[o["opponent"]]] = y
    return score, opps


def build_duration_matrix(records: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(n_builds, n_opps) duration and censoring matrices in opp-sorted order."""
    opps = sorted({o["opponent"] for r in records for o in r["opponent_results"]})
    opp_idx = {o: i for i, o in enumerate(opps)}
    n_b, n_o = len(records), len(opps)
    dur = np.full((n_b, n_o), np.nan)
    censored = np.zeros((n_b, n_o), dtype=bool)
    for bi, r in enumerate(records):
        for o in r["opponent_results"]:
            dur[bi, opp_idx[o["opponent"]]] = float(o["duration_seconds"])
            censored[bi, opp_idx[o["opponent"]]] = o["winner"] == "TIMEOUT"
    return dur, censored


def twfe_sigma_sq(score_mat: np.ndarray, alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
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


def scalar_cv(alpha: np.ndarray, heuristic: np.ndarray) -> np.ndarray:
    h_mean = heuristic.mean()
    h_var = heuristic.var(ddof=0)
    if h_var < 1e-12:
        return alpha.copy()
    a_mean = alpha.mean()
    cov = np.mean((heuristic - h_mean) * (alpha - a_mean))
    beta = cov / h_var
    return alpha - beta * (heuristic - h_mean)


def build_X7(records: list[dict]) -> np.ndarray:
    """Production 7-dim pre-battle covariate matrix (Python fallback for pre-5D log)."""
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for r in records:
            rec = _EBRecord(
                trial_number=r["trial_number"],
                scorer_result=r["_scorer_result"],
                engine_stats=None,
            )
            rows.append(_build_covariate_vector(rec))
    return np.vstack(rows)


def build_projected_ttk(records: list[dict]) -> np.ndarray:
    """Pre-battle projected TTK = effective_hp / total_dps. Log-transformed for scale.

    This is a build-intrinsic time scale: 'how long would it take to burn through
    my own effective HP at my own DPS rate'. Pre-battle, no outcome leakage.
    """
    ttk = []
    for r in records:
        sr = r["_scorer_result"]
        dps = max(sr.total_dps, 1.0)  # guard zero-weapon builds
        ehp = max(sr.effective_hp, 1.0)
        ttk.append(np.log(ehp / dps))
    return np.asarray(ttk)


def build_mean_duration(records: list[dict]) -> np.ndarray:
    """Build-level mean realized duration (POST-BATTLE — negative control only)."""
    out = []
    for r in records:
        durs = [float(o["duration_seconds"]) for o in r["opponent_results"]]
        out.append(float(np.mean(durs)) if durs else np.nan)
    return np.asarray(out)


def fit_aft_residuals(records: list[dict], X7: np.ndarray) -> np.ndarray:
    """Fit Weibull AFT on matchup durations ~ pre-battle X7 and return
    per-build mean log-duration residual.

    The residual is still a function of realized durations (post-battle), but
    its shape is calibrated under the AFT: unit-Gumbel under correct spec.
    """
    from lifelines import WeibullAFTFitter

    rows = []
    for bi, r in enumerate(records):
        for o in r["opponent_results"]:
            row = {f"x{i}": X7[bi, i] for i in range(X7.shape[1])}
            row["duration"] = float(o["duration_seconds"])
            row["observed"] = 0 if o["winner"] == "TIMEOUT" else 1
            row["build_idx"] = bi
            rows.append(row)
    df = pd.DataFrame(rows)

    fitter = WeibullAFTFitter(penalizer=1e-3)
    feat_cols = [f"x{i}" for i in range(X7.shape[1])]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fitter.fit(
            df[feat_cols + ["duration", "observed"]],
            duration_col="duration",
            event_col="observed",
        )
        mu = fitter.predict_expectation(df[feat_cols]).values
    resid = np.log(df["duration"].values.clip(min=1.0)) - np.log(mu.clip(min=1.0))
    df_out = pd.DataFrame({"build_idx": df["build_idx"], "resid": resid})
    per_build = df_out.groupby("build_idx")["resid"].mean().reindex(
        range(len(records)), fill_value=0.0
    )
    return per_build.values


def augment(X: np.ndarray, extra: np.ndarray) -> np.ndarray:
    return np.hstack([X, extra.reshape(-1, 1)])


def fit_estimators(
    score_mat: np.ndarray,
    X7: np.ndarray,
    X_variants: dict[str, np.ndarray],
    heuristic: np.ndarray,
) -> dict[str, np.ndarray]:
    alpha, beta = twfe_decompose(score_mat, n_iters=20, ridge=0.01)
    sigma_sq = twfe_sigma_sq(score_mat, alpha, beta)
    cfg = EBShrinkageConfig()
    out: dict[str, np.ndarray] = {}
    out["A0"] = alpha.copy()
    out["A"] = scalar_cv(alpha, heuristic)
    out["EB7"], _, _, _ = eb_shrinkage(alpha, sigma_sq, X7, cfg)
    for name, X in X_variants.items():
        out[name], _, _, _ = eb_shrinkage(alpha, sigma_sq, X, cfg)
    return out


def run_gate(
    score_mat: np.ndarray,
    probe_opps: np.ndarray,
    opp_names: list[str],
    X7: np.ndarray,
    X_variants: dict[str, np.ndarray],
    heuristic: np.ndarray,
    rng_boot: np.random.Generator,
    label_suffix: str = "",
) -> list[dict]:
    """LOOO over probe opponents; fit estimators on reduced matrix; record ρ."""
    rows: list[dict] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", stats.ConstantInputWarning)
        for probe in probe_opps:
            probe_name = opp_names[probe]
            probe_y = score_mat[:, probe]
            score_red = score_mat.copy()
            score_red[:, probe] = np.nan
            ests = fit_estimators(score_red, X7, X_variants, heuristic)
            valid = np.isfinite(probe_y)
            if valid.sum() < 3 or np.std(probe_y[valid]) < 1e-12:
                continue
            n_v = int(valid.sum())
            idx_valid = np.where(valid)[0]
            for name, est in ests.items():
                rho = stats.spearmanr(est[valid], probe_y[valid]).statistic
                boot = []
                for _ in range(N_BOOT):
                    idx = rng_boot.choice(idx_valid, size=n_v, replace=True)
                    br = stats.spearmanr(est[idx], probe_y[idx]).statistic
                    if np.isfinite(br):
                        boot.append(br)
                rows.append({
                    "probe_opp": probe_name,
                    "estimator": f"{name}{label_suffix}",
                    "rho": float(rho) if np.isfinite(rho) else float("nan"),
                    "ci_lo": float(np.quantile(boot, 0.025)) if boot else float("nan"),
                    "ci_hi": float(np.quantile(boot, 0.975)) if boot else float("nan"),
                    "n_valid": n_v,
                })
    return rows


def paired_bootstrap_delta(
    df: pd.DataFrame, estimator_a: str, estimator_b: str, n_boot: int = 2000
) -> dict:
    """Paired bootstrap over probes: sample probe-rows with replacement and
    compute the mean-ρ difference between two estimators. Returns percentile CI.

    A nonzero CI means the ranking is robust to probe-choice variation.
    """
    probes = df["probe_opp"].unique()
    rho_by_probe = (
        df.pivot_table(index="probe_opp", columns="estimator", values="rho")
    )
    if estimator_a not in rho_by_probe.columns or estimator_b not in rho_by_probe.columns:
        return {"delta": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan")}
    rng = np.random.default_rng(42)
    deltas = []
    for _ in range(n_boot):
        idx = rng.choice(len(probes), size=len(probes), replace=True)
        sample = rho_by_probe.iloc[idx]
        deltas.append(float(sample[estimator_a].mean() - sample[estimator_b].mean()))
    deltas_arr = np.asarray(deltas)
    return {
        "delta": float(rho_by_probe[estimator_a].mean() - rho_by_probe[estimator_b].mean()),
        "ci_lo": float(np.quantile(deltas_arr, 0.025)),
        "ci_hi": float(np.quantile(deltas_arr, 0.975)),
    }


def subsample_robustness(
    records: list[dict],
    score_mat: np.ndarray,
    opp_names: list[str],
    probe_opps: np.ndarray,
    X7: np.ndarray,
    X_variants: dict[str, np.ndarray],
    heuristic: np.ndarray,
    subsample_sizes: list[int],
    n_reps: int = 5,
) -> pd.DataFrame:
    """Repeat LOOO gate at smaller n (random build subsamples) to probe how the
    ranking holds up as the calibration set shrinks (tests whether wins are
    large-n artifacts of τ̂² auto-regularization)."""
    rng = np.random.default_rng(7)
    rows: list[dict] = []
    for n_sub in subsample_sizes:
        if n_sub >= score_mat.shape[0]:
            continue
        for rep in range(n_reps):
            idx = rng.choice(score_mat.shape[0], size=n_sub, replace=False)
            sub_score = score_mat[idx, :]
            sub_X7 = X7[idx, :]
            sub_X_variants = {k: v[idx, :] for k, v in X_variants.items()}
            sub_h = heuristic[idx]
            rng_boot_sub = np.random.default_rng(1000 + rep)
            sub_rows = run_gate(
                sub_score, probe_opps, opp_names, sub_X7, sub_X_variants,
                sub_h, rng_boot_sub, label_suffix="",
            )
            for r in sub_rows:
                r["n_sub"] = n_sub
                r["rep"] = rep
            rows.extend(sub_rows)
    return pd.DataFrame(rows)


def placebo_test(
    X7: np.ndarray, mean_duration: np.ndarray, alpha_hat: np.ndarray
) -> dict:
    """Eggers-Tuñón: partial correlation of duration with α̂_TWFE given X7.

    Residualize duration on X7 (OLS with intercept), residualize α̂ on X7,
    Spearman+Pearson correlation of the residuals. Nonzero ρ → duration
    carries outcome info not in X7 → post-treatment contamination.
    """
    def residualize(y: np.ndarray) -> np.ndarray:
        Xaug = np.hstack([np.ones((X7.shape[0], 1)), X7])
        gamma, *_ = np.linalg.lstsq(Xaug, y, rcond=None)
        return y - Xaug @ gamma

    dur_res = residualize(mean_duration)
    alpha_res = residualize(alpha_hat)
    pearson = stats.pearsonr(dur_res, alpha_res)
    spearman = stats.spearmanr(dur_res, alpha_res)
    return {
        "pearson_r": float(pearson.statistic),
        "pearson_p": float(pearson.pvalue),
        "spearman_rho": float(spearman.statistic),
        "spearman_p": float(spearman.pvalue),
        "n": int(X7.shape[0]),
    }


def main() -> None:
    print(f"Loading {HAMMERHEAD_LOG}")
    records = load_records()
    score_mat, opp_names = build_score_matrix(records, lex_epsilon=0.0)
    X7 = build_X7(records)
    heuristic = np.array([r["_scorer_result"].composite_score for r in records])
    mean_dur = build_mean_duration(records)
    proj_ttk = build_projected_ttk(records)
    print(f"  {score_mat.shape[0]} builds × {score_mat.shape[1]} opponents, X7 {X7.shape}")

    print("Fitting AFT for log-duration residual ...")
    aft_resid = fit_aft_residuals(records, X7)

    X_variants = {
        "EB8_dur": augment(X7, mean_dur),
        "EB8_ttk": augment(X7, proj_ttk),
        "EB8_aft": augment(X7, aft_resid),
        "EB9_ttk_dur": augment(augment(X7, proj_ttk), mean_dur),
        "EB9_ttk_aft": augment(augment(X7, proj_ttk), aft_resid),
    }

    opp_counts = np.sum(np.isfinite(score_mat), axis=0)
    probe_opps = np.argsort(-opp_counts)[:N_PROBES]
    rng_boot = np.random.default_rng(0)

    print("Running LOOO gate on canonical Y ...")
    rows = run_gate(
        score_mat, probe_opps, opp_names, X7, X_variants,
        heuristic, rng_boot, label_suffix="",
    )

    for eps in LEX_EPSILONS:
        print(f"Running LOOO gate on lexicographic Y (ε={eps}) ...")
        score_lex, _ = build_score_matrix(records, lex_epsilon=eps)
        rows += run_gate(
            score_lex, probe_opps, opp_names, X7, X_variants,
            heuristic, rng_boot, label_suffix=f"_LEX{eps}",
        )

    df = pd.DataFrame(rows)
    out_csv = HERE / "ttk_benchmark_results.csv"
    df.to_csv(out_csv, index=False)

    pivot = df.pivot_table(index="probe_opp", columns="estimator", values="rho").round(3)
    print("\nPer-probe Spearman ρ (LOOO probe):")
    print(pivot)

    means = df.groupby("estimator")["rho"].mean().sort_index()
    print("\nMean ρ across probes:")
    for name, val in means.items():
        print(f"  {name:15s}: {val:+.4f}")

    baseline_a0 = means["A0"]
    baseline_a = means["A"]
    baseline_eb7 = means["EB7"]
    deltas = {}
    for name in means.index:
        d_a0 = means[name] - baseline_a0
        d_a = means[name] - baseline_a
        d_eb7 = means[name] - baseline_eb7
        deltas[name] = {"vs_A0": d_a0, "vs_A": d_a, "vs_EB7": d_eb7}

    print("\nΔρ vs baselines (ship gate: ≥ +0.02 vs A0 and A):")
    for name, d in deltas.items():
        gate = "PASS" if d["vs_A0"] >= GATE_MARGIN and d["vs_A"] >= GATE_MARGIN else "fail"
        mark_eb7 = "+" if d["vs_EB7"] > 0 else ""
        print(
            f"  {name:15s}  vs_A0={d['vs_A0']:+.4f}  vs_A={d['vs_A']:+.4f}  "
            f"vs_EB7={mark_eb7}{d['vs_EB7']:+.4f}  [{gate}]"
        )

    print("\nPlacebo test (Eggers-Tuñón 2024 partial correlation):")
    alpha_full, _ = twfe_decompose(score_mat, n_iters=20, ridge=0.01)
    placebo = placebo_test(X7, mean_dur, alpha_full)
    print(
        f"  partial corr(duration, α̂_TWFE | X7):  "
        f"Pearson r={placebo['pearson_r']:+.3f} (p={placebo['pearson_p']:.2e}), "
        f"Spearman ρ={placebo['spearman_rho']:+.3f} (p={placebo['spearman_p']:.2e}), "
        f"n={placebo['n']}"
    )
    if placebo["pearson_p"] < 0.05:
        print("  → REJECT admissibility: duration carries outcome info beyond pre-battle X.")
    else:
        print("  → Cannot reject null: duration appears conditionally independent of α̂ given X.")

    placebo_ttk = placebo_test(X7, proj_ttk, alpha_full)
    print(
        f"  partial corr(proj_TTK, α̂_TWFE | X7): "
        f"Pearson r={placebo_ttk['pearson_r']:+.3f} (p={placebo_ttk['pearson_p']:.2e})"
    )

    placebo_aft = placebo_test(X7, aft_resid, alpha_full)
    print(
        f"  partial corr(AFT_resid, α̂_TWFE | X7): "
        f"Pearson r={placebo_aft['pearson_r']:+.3f} (p={placebo_aft['pearson_p']:.2e})"
    )

    print("\nPaired-bootstrap 95% CI on Δρ vs EB7 (tests significance of ranking):")
    df_canonical = df[~df["estimator"].str.contains("_LEX")]
    sig_rows = []
    for name in sorted(df_canonical["estimator"].unique()):
        if name == "EB7":
            continue
        result = paired_bootstrap_delta(df_canonical, name, "EB7", n_boot=2000)
        sig = "sig" if (result["ci_lo"] > 0 or result["ci_hi"] < 0) else "NS"
        print(
            f"  Δρ({name:14s} − EB7) = {result['delta']:+.4f}  "
            f"[{result['ci_lo']:+.4f}, {result['ci_hi']:+.4f}]  [{sig}]"
        )
        sig_rows.append({"pair": f"{name}-EB7", **result})
    pd.DataFrame(sig_rows).to_csv(HERE / "ttk_benchmark_sig.csv", index=False)

    sub_sizes = [s for s in (50, 100, 200) if s < score_mat.shape[0]]
    if sub_sizes:
        print(f"\nSubsample robustness ({sub_sizes}) ...")
        sub_df = subsample_robustness(
            records, score_mat, opp_names, probe_opps, X7, X_variants, heuristic,
            subsample_sizes=sub_sizes, n_reps=5,
        )
        sub_df.to_csv(HERE / "ttk_benchmark_subsample.csv", index=False)
        if not sub_df.empty:
            sub_means = (
                sub_df.groupby(["n_sub", "estimator"])["rho"].mean()
                .unstack().round(3)
            )
            print(sub_means)
    else:
        print("\nSkipping subsample robustness (n_builds too small)")

    summary_path = HERE / "ttk_benchmark_summary.json"
    summary_path.write_text(json.dumps({
        "mean_rho_by_estimator": {k: float(v) for k, v in means.items()},
        "deltas": {k: {k2: float(v2) for k2, v2 in d.items()} for k, d in deltas.items()},
        "placebo_duration": placebo,
        "placebo_proj_ttk": placebo_ttk,
        "placebo_aft_resid": placebo_aft,
        "n_probes": N_PROBES,
        "n_boot": N_BOOT,
        "gate_margin": GATE_MARGIN,
        "lex_epsilons": list(LEX_EPSILONS),
    }, indent=2))
    print(f"\nResults → {out_csv}")
    print(f"Summary → {summary_path}")


if __name__ == "__main__":
    main()
