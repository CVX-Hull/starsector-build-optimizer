"""Phase 5D ship-gate replay — LOOO Δρ on Hammerhead 2026-04-13.

Exercises the SHIPPED production code (`src/starsector_optimizer/deconfounding.py`
`eb_shrinkage` and `triple_goal_rank`, plus `optimizer.py::_build_covariate_vector`)
against the 2026-04-13 Hammerhead evaluation log. The log predates Phase 5D so
it has no `setup_stats` block — the Python fallback path in
`_build_covariate_vector` fills the 3 engine-stat columns from
`ScorerResult.effective_stats`, matching the replay-time semantics documented
in `phase5d-covariate-adjustment.md` §5 (implementation notes).

Ship gate per plan Step 10b:
    mean Δρ(EB − A0) ≥ +0.02   AND   mean Δρ(EB − A) ≥ +0.02
on LOOO over the top-5 most-sampled anchor opponents.

A0 = plain TWFE
A  = plain TWFE + shipped scalar control variate (pre-5D A2)
EB = plain TWFE + eb_shrinkage (fused with γ̂ᵀX_i on 7 covariates)
EBT = EB + triple_goal_rank

Usage:
    uv run python experiments/phase5d-covariate-2026-04-17/ship_gate_replay.py
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
HAMMERHEAD_LOG = (
    HERE.parent / "hammerhead-twfe-2026-04-13" / "evaluation_log.jsonl"
)
GAME_DIR = Path("/home/sdai/ClaudeCode/game/starsector")

# Ship-gate parameters (plan Step 10b)
N_BOOT = 200
N_PROBES = 5
GATE_MARGIN = 0.02


def load_records() -> list[dict]:
    """Load Hammerhead log and attach re-scored ScorerResult to each record."""
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
    records: list[dict],
) -> tuple[np.ndarray, list[str]]:
    """Construct (n_builds, n_opps) score matrix with NaN for unobserved.

    Uses hp_differential as the scalar Y_ij (scaled 0.5× for TIMEOUT, same
    as `phase5d_fusion_validation.py`). Production uses combat_fitness; for
    LOOO gate comparability with prior validation we stay on hp_differential.
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
            score[bi, opp_idx[o["opponent"]]] = y
    return score, opps


def twfe_sigma_sq(score_mat: np.ndarray, alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """σ̂_i² = σ̂_ε² / n_i (mirrors ScoreMatrix.build_sigma_sq on ndarrays)."""
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
    """Shipped A2 scalar control variate: α̂ − β̂·(h − h̄), β̂ = Cov(α̂,h)/Var(h)."""
    h_mean = heuristic.mean()
    h_var = heuristic.var(ddof=0)
    if h_var < 1e-12:
        return alpha.copy()
    a_mean = alpha.mean()
    cov = np.mean((heuristic - h_mean) * (alpha - a_mean))
    beta = cov / h_var
    return alpha - beta * (heuristic - h_mean)


def build_X(records: list[dict]) -> np.ndarray:
    """Assemble (n, 7) covariate matrix using the production _build_covariate_vector.

    No setup_stats in the 2026-04-13 log → production fallback to Python
    effective_stats (emits UserWarning; suppressed here for noise control).
    """
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for r in records:
            rec = _EBRecord(
                trial_number=r["trial_number"],
                scorer_result=r["_scorer_result"],
                engine_stats=None,  # pre-5D log
            )
            rows.append(_build_covariate_vector(rec))
    return np.vstack(rows)


def fit_all(score_mat: np.ndarray, X: np.ndarray, heuristic: np.ndarray) -> dict:
    """Fit A0, A, EB, EBT on the given score matrix."""
    alpha, beta = twfe_decompose(score_mat, n_iters=20, ridge=0.01)
    sigma_sq = twfe_sigma_sq(score_mat, alpha, beta)
    a_A0 = alpha
    a_A = scalar_cv(alpha, heuristic)
    a_EB, _, _, _ = eb_shrinkage(alpha, sigma_sq, X, EBShrinkageConfig())
    a_EBT = triple_goal_rank(a_EB, alpha)
    return {"A0": a_A0, "A": a_A, "EB": a_EB, "EBT": a_EBT}


def main() -> None:
    print(f"Loading {HAMMERHEAD_LOG}")
    records = load_records()
    score_mat, opp_names = build_score_matrix(records)
    X = build_X(records)
    heuristic = np.array([r["_scorer_result"].composite_score for r in records])
    n_b, n_o = score_mat.shape
    print(f"  {n_b} non-pruned builds × {n_o} opponents; X shape = {X.shape}")

    # Ship gate: LOOO on top-N anchors, bootstrap CIs.
    opp_counts = np.sum(np.isfinite(score_mat), axis=0)
    probe_opps = np.argsort(-opp_counts)[:N_PROBES]
    rng_boot = np.random.default_rng(0)

    gate_rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", stats.ConstantInputWarning)
        for probe in probe_opps:
            probe_name = opp_names[probe]
            probe_y = score_mat[:, probe]
            score_red = score_mat.copy()
            score_red[:, probe] = np.nan
            ests = fit_all(score_red, X, heuristic)
            valid = np.isfinite(probe_y)
            # Guard: probe with <2 valid points or constant probe_y has undefined rank correlation.
            if valid.sum() < 3 or np.std(probe_y[valid]) < 1e-12:
                print(f"  Skipping probe {opp_names[probe]}: "
                      f"n_valid={int(valid.sum())}, std={np.std(probe_y[valid]):.3e}")
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
                ci_lo = float(np.quantile(boot, 0.025)) if boot else float("nan")
                ci_hi = float(np.quantile(boot, 0.975)) if boot else float("nan")
                gate_rows.append({
                    "probe_opp": probe_name, "estimator": name,
                    "rho": float(rho) if np.isfinite(rho) else float("nan"),
                    "ci_lo": ci_lo, "ci_hi": ci_hi,
                    "n_valid": n_v,
                })

    df = pd.DataFrame(gate_rows)
    out_csv = HERE / "ship_gate_replay_results.csv"
    df.to_csv(out_csv, index=False)

    print("\nPer-probe Spearman ρ vs LOOO probe (raw hp_differential):")
    print(df.pivot_table(index="probe_opp", columns="estimator", values="rho").round(3))

    means = df.groupby("estimator")["rho"].mean()
    print("\nMean ρ across probes:")
    for name, val in means.items():
        print(f"  {name}: {val:+.3f}")

    delta_eb_a0 = means["EB"] - means["A0"]
    delta_eb_a = means["EB"] - means["A"]
    delta_ebt_a0 = means["EBT"] - means["A0"]
    delta_ebt_a = means["EBT"] - means["A"]

    print("\nShip-gate deltas:")
    print(f"  Δρ(EB  − A0) = {delta_eb_a0:+.3f}  (gate: +{GATE_MARGIN:.2f})")
    print(f"  Δρ(EB  − A)  = {delta_eb_a:+.3f}  (gate: +{GATE_MARGIN:.2f})")
    print(f"  Δρ(EBT − A0) = {delta_ebt_a0:+.3f}")
    print(f"  Δρ(EBT − A)  = {delta_ebt_a:+.3f}")

    passes_eb = delta_eb_a0 >= GATE_MARGIN and delta_eb_a >= GATE_MARGIN
    passes_ebt = delta_ebt_a0 >= GATE_MARGIN and delta_ebt_a >= GATE_MARGIN
    print(f"\nShip gate EB  : {'PASS' if passes_eb else 'FAIL'}")
    print(f"Ship gate EBT : {'PASS' if passes_ebt else 'FAIL'}")
    print(f"\nResults → {out_csv}")


if __name__ == "__main__":
    main()
