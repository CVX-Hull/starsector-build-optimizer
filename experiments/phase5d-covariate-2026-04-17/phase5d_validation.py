"""Phase 5D — Covariate-Adjusted TWFE Validation.

Validates the proposed Phase 5D design against ground-truth build quality,
mirroring the 2026-04-17 Hammerhead run (368 builds × 54 opponents with
anchor-first + incumbent-overlap scheduling, ~16% timeout, exploit cluster
at the top).

Phase 5D in practice is **multivariate CUPED at the build level with auto-
selection**:

  Y_ij = α_i + β_j + ε_ij                  (A1 shipped)
  α̂_i^adj = α̂_i − γ̂ᵀ (X_i − X̄)           (A2 multivariate)
  γ̂     = argmin Σ (α̂_i − γᵀ X_i)²        (OLS / lasso on build-level X_i)

The within-TWFE regression `Y_ij = α_i + β_j + γᵀ X_ij + ε_ij` is reserved for
*matchup-level* covariates X_ij that vary with both i and j. Our pre-matchup
candidate set is almost entirely build-level (scorer components, flux, hullmod
counts), so Stage 2 selection happens at the build level. The within-TWFE path
is exercised only as a *cost* check: what if we violated Stage 1 and admitted
post-matchup bad-controls (duration, damage-efficiency, overload)? The test
shows how much α̂ is corrupted when bad-controls enter — that's the motivation
for the Stage 1 timing filter.

Estimators (final-stage α̂ prior to any A3 shape):

  A. Baseline          — TWFE α + scalar CV on heuristic_i (shipped)
  B. Multi-CUPED full  — TWFE α + OLS on full X^build_pre (no selection)
  C. Multi-CUPED PDS   — TWFE α + OLS on post-double-selected S ⊆ X^build_pre
  D. PDS + ICP         — PDS shortlist further filtered by opponent-invariance

Stage-1 violation stress tests (cost of admitting bad-controls):

  E. Within-TWFE + bad — fit γ on X^matchup that INCLUDES 3 post-matchup colliders
  F. E + PDS           — does PDS drop the colliders?
  G. F + ICP           — does ICP filter survivors?

Usage:
    uv run python phase5d_validation.py                 # full (n_seeds=20)
    uv run python phase5d_validation.py --quick         # n_seeds=5
    uv run python phase5d_validation.py --real-only     # Hammerhead replay only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LassoCV

warnings.filterwarnings("ignore", category=ConvergenceWarning)

CURRICULUM_DIR = Path("/home/sdai/ClaudeCode/experiments/phase5b-curriculum-simulation")
sys.path.insert(0, str(CURRICULUM_DIR))
import curriculum_simulation as cs  # noqa: E402

plt.style.use("seaborn-v0_8-darkgrid")
plt.rcParams["figure.figsize"] = (14, 8)

HERE = Path(__file__).parent
OUT_DIR = HERE
HAMMERHEAD_LOG = Path(
    "/home/sdai/ClaudeCode/experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl"
)

# ═══════════════════════════════════════════════════════════════════════════
# Generative model parameters — tuned to mirror Hammerhead characteristics
# ═══════════════════════════════════════════════════════════════════════════

EXPLOIT_FRACTION = 0.90
EXPLOIT_UPLIFT = 0.80
EXPLOIT_SUBVAR = 0.30
CEILING = 1.2
EXPLOIT_LOGIT_BOOST = 1.5
TRIVIAL_THRESHOLD = -1.0

N_BUILDS_DEFAULT = 368       # matches Hammerhead run
N_OPPONENTS_DEFAULT = 54     # matches Hammerhead run
N_ACTIVE_DEFAULT = 10        # matches production
N_ANCHORS_DEFAULT = 3
N_INCUMBENT_OVERLAP = 5

# Pre-matchup build-level covariate design
N_USEFUL_XB = 4              # useful proxies of q
N_NOISE_XB = 4               # pure noise columns
# Production heuristic proxy: moderate ρ with q (realistic for scorer composite).
HEURISTIC_NOISE_STD = 1.2    # produces ρ(heuristic, q) ≈ 0.45


@dataclass
class XBuild:
    """Ground-truth build with multi-column pre-matchup covariates."""
    quality: float
    archetype: np.ndarray = field(default_factory=lambda: np.zeros(3))
    has_exploit: bool = False
    x_useful: np.ndarray = field(default_factory=lambda: np.zeros(N_USEFUL_XB))
    x_noise: np.ndarray = field(default_factory=lambda: np.zeros(N_NOISE_XB))
    heuristic: float = 0.0    # production-like scalar heuristic


def generate_xbuilds(n: int, rng: np.random.Generator) -> list[XBuild]:
    """Build quality + 4 useful + 4 noise pre-matchup covariates + heuristic."""
    builds: list[XBuild] = []
    for _ in range(n):
        has_exploit = rng.random() < EXPLOIT_FRACTION
        if has_exploit:
            q = EXPLOIT_UPLIFT + rng.normal(0, EXPLOIT_SUBVAR)
        else:
            q = rng.normal(0, 1)
        # Useful: q + moderate noise (ρ ≈ 0.45 — 0.85 individually)
        x_useful = np.array([
            q + rng.normal(0, 0.7),  # weak proxy
            q + rng.normal(0, 0.5),
            q + rng.normal(0, 0.35),
            q + rng.normal(0, 0.9),
        ])
        # Noise: independent N(0,1)
        x_noise = rng.normal(0, 1, size=N_NOISE_XB)
        # Production-style heuristic — lower fidelity than any single useful col
        heuristic = q + rng.normal(0, HEURISTIC_NOISE_STD)
        builds.append(XBuild(quality=q, archetype=rng.dirichlet([2, 2, 2]),
                             has_exploit=has_exploit, x_useful=x_useful,
                             x_noise=x_noise, heuristic=heuristic))
    return builds


def simulate_xmatchup(build: XBuild, opp: cs.Opponent,
                      rng: np.random.Generator,
                      noise_std: float = 0.5) -> tuple[float, np.ndarray]:
    """Return (Y, bad_controls(3,)). Bad controls are post-matchup colliders on Y."""
    rps = float(np.dot(build.archetype, opp.archetype_vuln))
    logit = opp.discrimination * (build.quality - opp.difficulty) + rps
    if build.has_exploit and opp.difficulty <= TRIVIAL_THRESHOLD:
        logit += EXPLOIT_LOGIT_BOOST
    p_win = 1.0 / (1.0 + np.exp(-logit))
    outcome = (p_win - 0.5) * 2.0
    y = float(np.clip(outcome + rng.normal(0, noise_std), -CEILING, CEILING))

    # Post-matchup colliders: each is a function of y itself (+ noise).
    duration = 1.0 - abs(y) + rng.normal(0, 0.2)
    damage_eff = y + 0.3 * opp.difficulty + rng.normal(0, 0.25)
    overload_diff = y * 0.8 + rng.normal(0, 0.3)
    return y, np.array([duration, damage_eff, overload_diff])


# ═══════════════════════════════════════════════════════════════════════════
# Schedule: anchor-first + incumbent overlap (mirrors production 5C)
# ═══════════════════════════════════════════════════════════════════════════

def build_schedule(n_builds: int, opponents: list[cs.Opponent],
                   active_size: int, n_anchors: int, n_incumbent: int,
                   rng: np.random.Generator) -> list[list[int]]:
    n_opp = len(opponents)
    anchors = list(range(n_anchors))
    remaining = list(range(n_anchors, n_opp))
    schedule: list[list[int]] = []
    prev_opps: list[int] | None = None
    for _ in range(n_builds):
        active = list(anchors)
        need = active_size - n_anchors
        if prev_opps is not None and n_incumbent > 0:
            inc_pool = [o for o in prev_opps if o not in active]
            inc_pick = list(rng.choice(
                inc_pool, size=min(n_incumbent, len(inc_pool)), replace=False
            )) if inc_pool else []
            active.extend(int(x) for x in inc_pick)
            need -= len(inc_pick)
        if need > 0:
            pool = [o for o in remaining if o not in active]
            fill = rng.choice(pool, size=need, replace=False)
            active.extend(int(x) for x in fill)
        rng.shuffle(active[n_anchors:])
        schedule.append(active)
        prev_opps = list(active)
    return schedule


def collect_run(builds: list[XBuild], opponents: list[cs.Opponent],
                schedule: list[list[int]], rng: np.random.Generator,
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n_b, n_o = len(builds), len(opponents)
    score_mat = np.full((n_b, n_o), np.nan)
    bi_list, oi_list, bc_list = [], [], []
    for bi, active in enumerate(schedule):
        for oi in active:
            y, bc = simulate_xmatchup(builds[bi], opponents[oi], rng)
            score_mat[bi, oi] = y
            bi_list.append(bi); oi_list.append(oi); bc_list.append(bc)
    return (score_mat, np.asarray(bi_list), np.asarray(oi_list),
            np.vstack(bc_list))


# ═══════════════════════════════════════════════════════════════════════════
# Core TWFE primitives
# ═══════════════════════════════════════════════════════════════════════════

def twfe_plain(score_mat: np.ndarray, n_iters: int = 20,
               ridge: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """A1 alternating projection — unchanged from shipped code."""
    return cs.twfe_decompose(score_mat, n_iters=n_iters, ridge=ridge)


def twfe_within_covariate(
    score_mat: np.ndarray, X_cells: np.ndarray,
    bi: np.ndarray, oi: np.ndarray,
    n_iters: int = 20, ridge: float = 0.01, ols_ridge: float = 1e-4,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Three-block alternating projection for Y_ij = α_i + β_j + γᵀ X_ij + ε.

    This is meaningful only when X_cells has within-variation (varies by j
    for each i). Build-only covariates tiled across opponents are collinear
    with α and end up in γ=0; use ``multi_cuped_between`` for those.
    """
    n_b, n_o = score_mat.shape
    observed = ~np.isnan(score_mat)
    alpha = np.zeros(n_b)
    beta = np.zeros(n_o)
    k = X_cells.shape[1]
    if k == 0:
        a, b = twfe_plain(score_mat, n_iters=n_iters, ridge=ridge)
        return a, b, np.zeros(0)
    XtX = X_cells.T @ X_cells + ols_ridge * np.eye(k)
    XtX_inv = np.linalg.inv(XtX)
    gamma = np.zeros(k)
    for _ in range(n_iters):
        y_cells = score_mat[bi, oi]
        r = y_cells - (alpha[bi] + beta[oi])
        gamma = XtX_inv @ (X_cells.T @ r)
        gx = X_cells @ gamma
        y_adj = score_mat.copy()
        y_adj[bi, oi] = y_cells - gx
        for j in range(n_o):
            mask = observed[:, j]
            if mask.sum() > 0:
                beta[j] = (np.sum(y_adj[mask, j] - alpha[mask])
                           / (mask.sum() + ridge))
        for i in range(n_b):
            mask = observed[i, :]
            if mask.sum() > 0:
                alpha[i] = (np.sum(y_adj[i, mask] - beta[mask])
                            / (mask.sum() + ridge))
    return alpha, beta, gamma


# ═══════════════════════════════════════════════════════════════════════════
# Multivariate CUPED — between-build adjustment on α̂
# ═══════════════════════════════════════════════════════════════════════════

def multi_cuped_between(
    alpha: np.ndarray, X_build: np.ndarray,
    sel_mask: np.ndarray | None = None, ols_ridge: float = 1e-4,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (α̂_adj, γ̂_between).

    α̂_adj_i = α̂_i − γ̂ᵀ (X_i − X̄)
    γ̂      = argmin_γ Σ (α̂_i − γᵀ X_i)² ≈ OLS coefficients.

    This is the multivariate extension of the shipped scalar control variate.
    When sel_mask is provided, only the selected columns enter γ̂.
    """
    if sel_mask is not None:
        X_use = X_build[:, sel_mask]
    else:
        X_use = X_build
    if X_use.shape[1] == 0:
        return alpha.copy(), np.zeros(X_build.shape[1])
    X_centered = X_use - X_use.mean(axis=0)
    XtX = X_centered.T @ X_centered + ols_ridge * np.eye(X_use.shape[1])
    XtX_inv = np.linalg.inv(XtX)
    gamma_sub = XtX_inv @ (X_centered.T @ (alpha - alpha.mean()))
    alpha_adj = alpha - X_centered @ gamma_sub
    gamma_full = np.zeros(X_build.shape[1])
    if sel_mask is not None:
        gamma_full[sel_mask] = gamma_sub
    else:
        gamma_full = gamma_sub
    return alpha_adj, gamma_full


# ═══════════════════════════════════════════════════════════════════════════
# Post-double-selection lasso (Belloni-Chernozhukov-Hansen 2014)
# ═══════════════════════════════════════════════════════════════════════════

def _lasso_selection(X: np.ndarray, y: np.ndarray,
                     cv_folds: int = 5, seed: int = 0) -> np.ndarray:
    """Return boolean mask of columns with |coef| > 1e-6 from LassoCV."""
    if X.shape[1] == 0 or y.std() < 1e-10:
        return np.zeros(X.shape[1], dtype=bool)
    col_std = X.std(axis=0)
    keep_cols = col_std > 1e-10
    if not keep_cols.any():
        return np.zeros(X.shape[1], dtype=bool)
    Xs = X[:, keep_cols]
    try:
        lasso = LassoCV(cv=cv_folds, max_iter=5000, alphas=30,
                        random_state=seed, n_jobs=1).fit(Xs, y)
    except Exception:
        return np.zeros(X.shape[1], dtype=bool)
    mask = np.zeros(X.shape[1], dtype=bool)
    sel_idx = np.where(np.abs(lasso.coef_) > 1e-6)[0]
    mask[np.where(keep_cols)[0][sel_idx]] = True
    return mask


def pds_between(alpha: np.ndarray, X_build: np.ndarray,
                cv_folds: int = 5, seed: int = 0) -> np.ndarray:
    """Post-double-selection lasso at the build level.

    S_α = columns predictive of α̂
    S_k = for each X_k, columns in X_{-k} predictive of X_k
    Final S = S_α ∪ (⋃_k S_k).
    """
    k = X_build.shape[1]
    if k == 0:
        return np.zeros(0, dtype=bool)
    # Centre so lasso's implicit intercept handling is clean.
    X_c = X_build - X_build.mean(axis=0)
    y_c = alpha - alpha.mean()
    S_alpha = _lasso_selection(X_c, y_c, cv_folds, seed)
    S = S_alpha.copy()
    for c in range(k):
        mask_rest = np.ones(k, dtype=bool); mask_rest[c] = False
        X_rest = X_c[:, mask_rest]
        y_c_col = X_c[:, c]
        sel = _lasso_selection(X_rest, y_c_col, cv_folds, seed)
        full_indices = np.where(mask_rest)[0][sel]
        S[full_indices] = True
    return S


def pds_within(y_resid: np.ndarray, X_resid: np.ndarray,
               cv_folds: int = 5, seed: int = 0) -> np.ndarray:
    """PDS on within-residualised Y, X (matchup-level)."""
    k = X_resid.shape[1]
    if k == 0:
        return np.zeros(0, dtype=bool)
    S_Y = _lasso_selection(X_resid, y_resid, cv_folds, seed)
    S = S_Y.copy()
    for c in range(k):
        mask_rest = np.ones(k, dtype=bool); mask_rest[c] = False
        X_rest = X_resid[:, mask_rest]
        sel = _lasso_selection(X_rest, X_resid[:, c], cv_folds, seed)
        full_indices = np.where(mask_rest)[0][sel]
        S[full_indices] = True
    return S


def within_demean(
    y_cells: np.ndarray, X_cells: np.ndarray,
    bi: np.ndarray, oi: np.ndarray, n_b: int, n_o: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Iterative within-transformation. Returns (y_resid, X_resid)."""
    def demean(v: np.ndarray) -> np.ndarray:
        for _ in range(5):
            for i in range(n_b):
                mask = (bi == i)
                if mask.any():
                    v[mask] = v[mask] - v[mask].mean()
            for j in range(n_o):
                mask = (oi == j)
                if mask.any():
                    v[mask] = v[mask] - v[mask].mean()
        return v
    y_resid = demean(y_cells.astype(float).copy())
    X_resid = np.zeros_like(X_cells, dtype=float)
    for c in range(X_cells.shape[1]):
        X_resid[:, c] = demean(X_cells[:, c].astype(float).copy())
    return y_resid, X_resid


# ═══════════════════════════════════════════════════════════════════════════
# Estimators
# ═══════════════════════════════════════════════════════════════════════════

def estimator_baseline_A(score_mat: np.ndarray,
                         heuristic_i: np.ndarray) -> np.ndarray:
    """A: shipped. TWFE α + scalar control variate on heuristic_i."""
    alpha, _ = twfe_plain(score_mat)
    h_centered = heuristic_i - heuristic_i.mean()
    denom = float((h_centered * h_centered).sum())
    beta_cv = float((h_centered * (alpha - alpha.mean())).sum() / denom) \
        if denom > 0 else 0.0
    return alpha - beta_cv * h_centered


def estimator_multi_full_B(score_mat: np.ndarray,
                           X_build: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """B: TWFE α + multivariate CUPED on FULL X_build (no selection)."""
    alpha, _ = twfe_plain(score_mat)
    alpha_adj, gamma = multi_cuped_between(alpha, X_build)
    return alpha_adj, gamma


def estimator_multi_pds_C(score_mat: np.ndarray, X_build: np.ndarray,
                           seed: int = 0
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """C: TWFE α + multivariate CUPED with PDS-selected S."""
    alpha, _ = twfe_plain(score_mat)
    sel = pds_between(alpha, X_build, seed=seed)
    alpha_adj, gamma = multi_cuped_between(alpha, X_build, sel_mask=sel)
    return alpha_adj, gamma, sel


def _passes_invariance(resid: np.ndarray, oi: np.ndarray,
                       alpha_level: float = 0.05) -> bool:
    """Levene's test for variance homogeneity across opponent environments."""
    unique_o = np.unique(oi)
    groups = [resid[oi == j] for j in unique_o if (oi == j).sum() >= 3]
    if len(groups) < 3:
        return True
    try:
        stat, p = stats.levene(*groups)
    except ValueError:
        return True
    return p > alpha_level / max(1, len(groups))


def icp_filter_between(alpha: np.ndarray, X_build: np.ndarray,
                       sel_mask: np.ndarray,
                       bi: np.ndarray, oi: np.ndarray,
                       score_mat: np.ndarray,
                       max_subset: int = 5) -> np.ndarray:
    """Greedy ICP: retain a PDS-selected column only if dropping it does not
    break residual invariance across opponent environments.

    Heinze-Deml et al. 2018: for each candidate subset, test residuals from
    the covariate-adjusted model. Intersection of accepted subsets = the
    invariance-certified columns.

    This operates on the BETWEEN-build regression: α̂ − γᵀX_i is the residual
    at the build level, and we ask whether its per-cell projection (one residual
    per matchup) has variance homogeneity across opponents.
    """
    cand = np.where(sel_mask)[0]
    if len(cand) == 0:
        return sel_mask.copy()
    n_cand = len(cand)
    # Full enumeration for small cand; greedy forward from each singleton otherwise.
    def residuals_for(subset_cols: np.ndarray) -> np.ndarray:
        if len(subset_cols) == 0:
            return score_mat[bi, oi] - (alpha[bi])
        X_sub = X_build[:, subset_cols] - X_build[:, subset_cols].mean(axis=0)
        gamma = np.linalg.lstsq(X_sub, alpha - alpha.mean(), rcond=None)[0]
        alpha_resid = alpha - X_sub @ gamma
        # Per-cell residual projected through the TWFE model
        return score_mat[bi, oi] - alpha_resid[bi]

    accepted: list[np.ndarray] = []
    if n_cand <= max_subset:
        for bits in range(1, 1 << n_cand):
            sub = cand[[i for i in range(n_cand) if bits & (1 << i)]]
            r = residuals_for(sub)
            if _passes_invariance(r, oi):
                accepted.append(sub)
    else:
        for c in cand:
            S = np.array([c])
            if not _passes_invariance(residuals_for(S), oi):
                continue
            improved = True
            while improved and len(S) < max_subset:
                improved = False
                for c2 in cand:
                    if c2 in S:
                        continue
                    S_new = np.sort(np.append(S, c2))
                    if _passes_invariance(residuals_for(S_new), oi):
                        S = S_new; improved = True; break
            accepted.append(S)
    if not accepted:
        return sel_mask.copy()
    inter = accepted[0]
    for s in accepted[1:]:
        inter = np.intersect1d(inter, s)
    mask = np.zeros_like(sel_mask)
    mask[inter] = True
    return mask


def estimator_pds_icp_D(score_mat: np.ndarray, X_build: np.ndarray,
                         bi: np.ndarray, oi: np.ndarray,
                         seed: int = 0,
                         ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """D: C followed by ICP invariance filter."""
    alpha, _ = twfe_plain(score_mat)
    pds_sel = pds_between(alpha, X_build, seed=seed)
    icp_sel = icp_filter_between(alpha, X_build, pds_sel, bi, oi, score_mat)
    alpha_adj, gamma = multi_cuped_between(alpha, X_build, sel_mask=icp_sel)
    return alpha_adj, gamma, pds_sel, icp_sel


# ═══════════════════════════════════════════════════════════════════════════
# Stage-1 violation stress tests: admit post-matchup bad-controls at cell level
# ═══════════════════════════════════════════════════════════════════════════

def estimator_within_with_bad_E(score_mat: np.ndarray,
                                 X_cells_bad: np.ndarray,
                                 bi: np.ndarray, oi: np.ndarray
                                 ) -> tuple[np.ndarray, np.ndarray]:
    """E: fit within-TWFE including 3 bad-control cells; no selection."""
    alpha, _, gamma = twfe_within_covariate(score_mat, X_cells_bad, bi, oi)
    return alpha, gamma


def estimator_within_pds_F(score_mat: np.ndarray, X_cells_bad: np.ndarray,
                            bi: np.ndarray, oi: np.ndarray,
                            seed: int = 0
                            ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """F: PDS on matchup-level cells, then within-TWFE on selected columns."""
    n_b, n_o = score_mat.shape
    y_cells = score_mat[bi, oi]
    y_resid, X_resid = within_demean(y_cells, X_cells_bad, bi, oi, n_b, n_o)
    sel = pds_within(y_resid, X_resid, seed=seed)
    if sel.sum() == 0:
        a, _ = twfe_plain(score_mat)
        return a, np.zeros(X_cells_bad.shape[1]), sel
    alpha, _, gamma_sub = twfe_within_covariate(
        score_mat, X_cells_bad[:, sel], bi, oi)
    gamma = np.zeros(X_cells_bad.shape[1])
    gamma[sel] = gamma_sub
    return alpha, gamma, sel


def estimator_within_pds_icp_G(score_mat: np.ndarray, X_cells_bad: np.ndarray,
                                bi: np.ndarray, oi: np.ndarray,
                                seed: int = 0
                                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """G: F + ICP invariance filter on the within-TWFE residuals."""
    alpha_F, gamma_F, pds_sel = estimator_within_pds_F(
        score_mat, X_cells_bad, bi, oi, seed=seed)
    if pds_sel.sum() == 0:
        return alpha_F, gamma_F, pds_sel, pds_sel
    n_b, n_o = score_mat.shape

    def residuals_for(subset_cols: np.ndarray) -> np.ndarray:
        if len(subset_cols) == 0:
            a, b = twfe_plain(score_mat)
            return score_mat[bi, oi] - (a[bi] + b[oi])
        X_sub = X_cells_bad[:, subset_cols]
        a, b, g = twfe_within_covariate(score_mat, X_sub, bi, oi)
        return score_mat[bi, oi] - (a[bi] + b[oi] + X_sub @ g)

    cand = np.where(pds_sel)[0]
    accepted: list[np.ndarray] = []
    if len(cand) <= 5:
        for bits in range(1, 1 << len(cand)):
            sub = cand[[i for i in range(len(cand)) if bits & (1 << i)]]
            if _passes_invariance(residuals_for(sub), oi):
                accepted.append(sub)
    else:
        for c in cand:
            S = np.array([c])
            if not _passes_invariance(residuals_for(S), oi):
                continue
            improved = True
            while improved and len(S) < 5:
                improved = False
                for c2 in cand:
                    if c2 in S:
                        continue
                    S_new = np.sort(np.append(S, c2))
                    if _passes_invariance(residuals_for(S_new), oi):
                        S = S_new; improved = True; break
            accepted.append(S)
    if not accepted:
        return alpha_F, gamma_F, pds_sel, pds_sel
    inter = accepted[0]
    for s in accepted[1:]:
        inter = np.intersect1d(inter, s)
    icp_mask = np.zeros_like(pds_sel)
    icp_mask[inter] = True
    if icp_mask.sum() == 0:
        a, _ = twfe_plain(score_mat)
        return a, np.zeros(X_cells_bad.shape[1]), pds_sel, icp_mask
    alpha, _, gamma_sub = twfe_within_covariate(
        score_mat, X_cells_bad[:, icp_mask], bi, oi)
    gamma = np.zeros(X_cells_bad.shape[1])
    gamma[icp_mask] = gamma_sub
    return alpha, gamma, pds_sel, icp_mask


# ═══════════════════════════════════════════════════════════════════════════
# Run-one-seed (synthetic)
# ═══════════════════════════════════════════════════════════════════════════

def run_one_seed(seed: int, n_builds: int, n_opp: int) -> dict:
    rng = np.random.default_rng(seed)
    opponents = cs.generate_opponents(n_opp, rng)
    builds = generate_xbuilds(n_builds, rng)
    schedule = build_schedule(n_builds, opponents, N_ACTIVE_DEFAULT,
                              N_ANCHORS_DEFAULT, N_INCUMBENT_OVERLAP, rng)
    score_mat, bi, oi, bad_controls = collect_run(builds, opponents, schedule, rng)

    truth = np.array([b.quality for b in builds])
    heuristic_i = np.array([b.heuristic for b in builds])

    # X_build: (n_builds, 8) — 4 useful + 4 noise
    X_build = np.vstack([np.concatenate([b.x_useful, b.x_noise]) for b in builds])
    # Standardize columns — lasso expects this; CUPED doesn't care but it helps ridge.
    X_build = (X_build - X_build.mean(axis=0)) / X_build.std(axis=0).clip(min=1e-10)

    # Per-cell bad-control matrix
    X_cells_bad = bad_controls    # (n_obs, 3)

    out = {"seed": seed, "n_obs": int(np.isfinite(score_mat).sum())}

    # A0: plain TWFE (no CV, reference against which all adjustments are judged)
    t0 = time.time()
    a_A0, _ = twfe_plain(score_mat)
    out["A0_wall"] = time.time() - t0

    # A: scalar CV (shipped)
    t0 = time.time()
    a_A = estimator_baseline_A(score_mat, heuristic_i)
    out["A_wall"] = time.time() - t0

    # B: full multivariate CUPED
    t0 = time.time()
    a_B, _ = estimator_multi_full_B(score_mat, X_build)
    out["B_wall"] = time.time() - t0

    # C: PDS-selected multivariate CUPED
    t0 = time.time()
    a_C, _, pds_mask_C = estimator_multi_pds_C(score_mat, X_build, seed=seed)
    out["C_wall"] = time.time() - t0
    out["C_useful_recall"] = float(pds_mask_C[:N_USEFUL_XB].mean())
    out["C_noise_fpr"] = float(pds_mask_C[N_USEFUL_XB:].mean())
    out["C_sel_k"] = int(pds_mask_C.sum())

    # D: PDS + ICP
    t0 = time.time()
    a_D, _, pds_sel_D, icp_sel_D = estimator_pds_icp_D(
        score_mat, X_build, bi, oi, seed=seed)
    out["D_wall"] = time.time() - t0
    out["D_useful_recall"] = float(icp_sel_D[:N_USEFUL_XB].mean())
    out["D_noise_fpr"] = float(icp_sel_D[N_USEFUL_XB:].mean())
    out["D_sel_k"] = int(icp_sel_D.sum())

    # E/F/G: Stage-1 violation stress tests using X_cells_bad (only 3 bad cols)
    t0 = time.time()
    a_E, _ = estimator_within_with_bad_E(score_mat, X_cells_bad, bi, oi)
    out["E_wall"] = time.time() - t0

    t0 = time.time()
    a_F, _, pds_mask_F = estimator_within_pds_F(score_mat, X_cells_bad, bi, oi,
                                                 seed=seed)
    out["F_wall"] = time.time() - t0
    out["F_bad_retained"] = float(pds_mask_F.mean())

    t0 = time.time()
    a_G, _, pds_sel_G, icp_sel_G = estimator_within_pds_icp_G(
        score_mat, X_cells_bad, bi, oi, seed=seed)
    out["G_wall"] = time.time() - t0
    out["G_bad_retained"] = float(icp_sel_G.mean())

    # Metrics
    is_exploit = np.array([b.has_exploit for b in builds])
    for name, a_est in [("A0", a_A0), ("A", a_A), ("B", a_B), ("C", a_C), ("D", a_D),
                        ("E", a_E), ("F", a_F), ("G", a_G)]:
        out[f"rho_{name}"] = float(stats.spearmanr(a_est, truth).statistic)
        if is_exploit.sum() >= 3:
            out[f"exploit_rho_{name}"] = float(
                stats.spearmanr(a_est[is_exploit], truth[is_exploit]).statistic)

    # Variance of α̂ (lower = better CUPED)
    for name, a_est in [("A0", a_A0), ("A", a_A), ("B", a_B), ("C", a_C), ("D", a_D)]:
        out[f"var_{name}"] = float(a_est.var())

    # MSE vs truth (fair variance-reduction comparison)
    truth_centered = truth - truth.mean()
    for name, a_est in [("A0", a_A0), ("A", a_A), ("B", a_B), ("C", a_C), ("D", a_D)]:
        a_centered = a_est - a_est.mean()
        scale = (a_centered @ truth_centered) / (a_centered @ a_centered + 1e-10)
        out[f"mse_{name}"] = float(((scale * a_centered - truth_centered) ** 2).mean())

    return out


def paired_wilcoxon(df: pd.DataFrame, a: str, b: str) -> tuple[float, float]:
    diff = df[a] - df[b]
    try:
        return float(diff.mean()), float(stats.wilcoxon(diff).pvalue)
    except ValueError:
        return float(diff.mean()), float("nan")


# ═══════════════════════════════════════════════════════════════════════════
# Hammerhead-log replay
# ═══════════════════════════════════════════════════════════════════════════

def load_hammerhead() -> tuple[list[dict], list[str]]:
    from starsector_optimizer.parser import load_game_data
    from starsector_optimizer.scorer import heuristic_score
    from starsector_optimizer.models import Build

    records = [json.loads(l) for l in HAMMERHEAD_LOG.read_text().splitlines()]
    gd = load_game_data(Path("/home/sdai/ClaudeCode/game/starsector"))
    hull = gd.hulls["hammerhead"]
    for r in records:
        b = r["build"]
        build = Build(
            hull_id=b["hull_id"],
            weapon_assignments={k: v for k, v in b["weapon_assignments"].items()
                                if v is not None},
            hullmods=frozenset(b["hullmods"]),
            flux_vents=b["flux_vents"],
            flux_capacitors=b["flux_capacitors"],
        )
        sr = heuristic_score(build, hull, gd)
        r["scorer"] = {
            "composite": sr.composite_score, "total_dps": sr.total_dps,
            "kinetic_dps": sr.kinetic_dps, "he_dps": sr.he_dps,
            "energy_dps": sr.energy_dps, "flux_balance": sr.flux_balance,
            "flux_efficiency": sr.flux_efficiency, "effective_hp": sr.effective_hp,
            "armor_ehp": sr.armor_ehp, "shield_ehp": sr.shield_ehp,
            "range_coherence": sr.range_coherence, "damage_mix": sr.damage_mix,
            "op_efficiency": sr.op_efficiency,
        }
        r["n_hullmods"] = len(b["hullmods"])
    scorer_keys = list(records[0]["scorer"].keys())
    return records, scorer_keys


def hammerhead_replay() -> dict:
    """Fit A/B/C/D on real Hammerhead log; measure ship-gate via LOOO on anchors."""
    print("  Loading + re-scoring 368 Hammerhead builds...")
    records, scorer_keys = load_hammerhead()
    records = [r for r in records if not r["pruned"]]
    n_b = len(records)

    opp_names = sorted({o["opponent"] for r in records for o in r["opponent_results"]})
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

    # X_build: 13 scorer + 3 extras = 16 cols
    scorer_mat = np.array([[r["scorer"][k] for k in scorer_keys] for r in records])
    extras = np.array([[r["n_hullmods"], r["build"]["flux_vents"],
                        r["build"]["flux_capacitors"]] for r in records],
                       dtype=float)
    X_build_raw = np.hstack([scorer_mat, extras])
    X_build = (X_build_raw - X_build_raw.mean(axis=0)) \
              / X_build_raw.std(axis=0).clip(min=1e-10)
    col_names = scorer_keys + ["n_hullmods", "flux_vents", "flux_capacitors"]

    heuristic_i = np.array([r["scorer"]["composite"] for r in records])

    # Fit on full data
    a_A0, _ = twfe_plain(score_mat)
    a_A = estimator_baseline_A(score_mat, heuristic_i)
    a_B, _ = estimator_multi_full_B(score_mat, X_build)
    a_C, _, pds_mask = estimator_multi_pds_C(score_mat, X_build, seed=0)
    a_D, _, pds_mask_D, icp_mask = estimator_pds_icp_D(
        score_mat, X_build, bi, oi, seed=0)

    # Ship gate: leave-one-anchor-opponent-out. For each of the 3 most-sampled
    # anchors, drop its column, refit, then measure ρ(α̂_refit, raw Y on probe).
    opp_counts = np.sum(np.isfinite(score_mat), axis=0)
    anchor_ordering = np.argsort(-opp_counts)
    probe_opps = anchor_ordering[:3]
    gate_rows: list[dict] = []
    for probe in probe_opps:
        probe_name = opp_names[probe]
        probe_y = score_mat[:, probe]
        score_red = score_mat.copy(); score_red[:, probe] = np.nan
        mask_cells = oi != probe
        bi_red = bi[mask_cells]; oi_red = oi[mask_cells]

        est_A0, _ = twfe_plain(score_red)
        est_A = estimator_baseline_A(score_red, heuristic_i)
        est_B, _ = estimator_multi_full_B(score_red, X_build)
        est_C, _, _ = estimator_multi_pds_C(score_red, X_build, seed=0)
        est_D, _, _, _ = estimator_pds_icp_D(
            score_red, X_build, bi_red, oi_red, seed=0)

        valid = np.isfinite(probe_y)
        for name, est in [("A0", est_A0), ("A", est_A), ("B", est_B),
                          ("C", est_C), ("D", est_D)]:
            rho = stats.spearmanr(est[valid], probe_y[valid]).statistic
            gate_rows.append({"probe_opp": probe_name, "estimator": name,
                              "rho_vs_probe": float(rho),
                              "n_valid": int(valid.sum())})

    gate_df = pd.DataFrame(gate_rows)
    mean_rhos = gate_df.groupby("estimator")["rho_vs_probe"].mean().to_dict()

    return {
        "gate_df": gate_df,
        "mean_rhos": mean_rhos,
        "pds_selected": pds_mask,
        "icp_selected": icp_mask,
        "pds_cols_selected": [col_names[i] for i in range(len(col_names))
                              if pds_mask[i]],
        "pds_cols_rejected": [col_names[i] for i in range(len(col_names))
                              if not pds_mask[i]],
        "icp_cols_selected": [col_names[i] for i in range(len(col_names))
                              if icp_mask[i]],
        "n_builds_fit": n_b, "n_obs": int(np.isfinite(score_mat).sum()),
        "n_opponents": n_o,
        "var_alpha": {"A0": float(a_A0.var()), "A": float(a_A.var()),
                       "B": float(a_B.var()), "C": float(a_C.var()),
                       "D": float(a_D.var())},
        "alpha": {"A0": a_A0, "A": a_A, "B": a_B, "C": a_C, "D": a_D},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Plotting
# ═══════════════════════════════════════════════════════════════════════════

def plot_synthetic(df: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    ax = axes[0, 0]
    data = [df[f"rho_{k}"].values for k in ["A", "B", "C", "D", "E", "F", "G"]]
    labels = ["A scalar-CV", "B multi-full", "C multi-PDS", "D PDS+ICP",
              "E within+bad", "F within+bad+PDS", "G within+bad+PDS+ICP"]
    ax.boxplot(data, tick_labels=labels, showmeans=True)
    ax.set_title("rho(alpha-hat, truth) — full sweep")
    ax.axhline(float(df["rho_A"].mean()), color="gray", ls=":", alpha=0.5,
               label="A mean")
    ax.set_ylabel("Spearman rho")
    ax.tick_params(axis="x", rotation=30)
    ax.legend(loc="lower right", fontsize=9)

    ax = axes[0, 1]
    data = [df[f"exploit_rho_{k}"].values for k in ["A", "B", "C", "D"]]
    ax.boxplot(data, tick_labels=["A", "B", "C", "D"], showmeans=True)
    ax.set_title("Exploit-cluster rho (ranking exploit builds among themselves)")
    ax.set_ylabel("Spearman rho")

    ax = axes[1, 0]
    vals = [df["C_useful_recall"].mean(), df["C_noise_fpr"].mean(),
            df["D_useful_recall"].mean(), df["D_noise_fpr"].mean(),
            df["F_bad_retained"].mean(), df["G_bad_retained"].mean()]
    errs = [df["C_useful_recall"].std(), df["C_noise_fpr"].std(),
            df["D_useful_recall"].std(), df["D_noise_fpr"].std(),
            df["F_bad_retained"].std(), df["G_bad_retained"].std()]
    ax.bar(["C useful recall", "C noise FPR", "D useful recall", "D noise FPR",
            "F bad-ctrl kept", "G bad-ctrl kept"], vals, yerr=errs,
           color=["C2", "C3", "C2", "C3", "C1", "C1"])
    ax.set_title("Selection quality — higher useful recall / lower noise & bad")
    ax.set_ylabel("fraction")
    ax.axhline(1.0, ls="--", color="gray", alpha=0.3)
    ax.tick_params(axis="x", rotation=15)

    ax = axes[1, 1]
    means = [df[f"var_{k}"].mean() for k in ["A", "B", "C", "D"]]
    ax.bar(["A", "B", "C", "D"], means)
    ax.set_title("var(alpha-hat) — lower = better CUPED variance reduction")
    ax.set_ylabel("variance of alpha-hat")

    plt.tight_layout()
    plt.savefig(out, dpi=130)
    plt.close()


def plot_hammerhead(ham: dict, out: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    df = ham["gate_df"]
    piv = df.pivot(index="probe_opp", columns="estimator", values="rho_vs_probe")
    piv.plot.bar(ax=axes[0])
    axes[0].set_title("Hammerhead LOOO: rho(alpha-hat without probe, raw Y on probe)")
    axes[0].set_ylabel("Spearman rho")
    axes[0].tick_params(axis="x", rotation=20)

    ax = axes[1]
    means = ham["mean_rhos"]
    ax.bar(list(means.keys()), list(means.values()), color="C0")
    for k, v in means.items():
        ax.text(list(means.keys()).index(k), v + 0.005, f"{v:.3f}",
                ha="center")
    ax.set_title("Ship-gate: mean rho across 3 anchor probes (higher = better)")
    ax.set_ylim(0, max(means.values()) * 1.15 if means else 1.0)
    plt.tight_layout()
    plt.savefig(out, dpi=130)
    plt.close()


# ═══════════════════════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════════════════════

def sweep(n_seeds: int, n_builds: int, n_opp: int) -> pd.DataFrame:
    rows = []
    for seed in range(n_seeds):
        t0 = time.time()
        r = run_one_seed(seed, n_builds, n_opp)
        r["total_wall"] = time.time() - t0
        rows.append(r)
        print(f"  seed {seed:2d} {r['total_wall']:5.1f}s  "
              f"rho: A0={r['rho_A0']:+.3f} A={r['rho_A']:+.3f} "
              f"B={r['rho_B']:+.3f} C={r['rho_C']:+.3f} D={r['rho_D']:+.3f} "
              f"E={r['rho_E']:+.3f} F={r['rho_F']:+.3f} G={r['rho_G']:+.3f}")
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--synthetic-only", action="store_true")
    ap.add_argument("--real-only", action="store_true")
    ap.add_argument("--n-seeds", type=int, default=20)
    args = ap.parse_args()
    n_seeds = 5 if args.quick else args.n_seeds

    if not args.real_only:
        print(f"[1/2] Synthetic sweep — n_seeds={n_seeds}, "
              f"n_builds={N_BUILDS_DEFAULT}, n_opp={N_OPPONENTS_DEFAULT}")
        df = sweep(n_seeds, N_BUILDS_DEFAULT, N_OPPONENTS_DEFAULT)
        df.to_csv(OUT_DIR / "results.csv", index=False)
        plot_synthetic(df, OUT_DIR / "synthetic_comparison.png")

        cols = [f"rho_{k}" for k in ["A0", "A", "B", "C", "D", "E", "F", "G"]]
        print("\n--- rho(alpha-hat, truth): mean ± std across seeds ---")
        summary = df[cols].describe().loc[["mean", "std"]].round(3)
        print(summary.to_string())

        print("\n--- Paired Wilcoxon vs A0 (plain TWFE, no adjustment) ---")
        for k in ["A", "B", "C", "D", "E", "F", "G"]:
            m, p = paired_wilcoxon(df, f"rho_{k}", "rho_A0")
            print(f"  {k}-A0:  mean Δρ = {m:+.3f}   p = {p:.4f}")
        print("\n--- Paired Wilcoxon vs A (shipped scalar CV) ---")
        for k in ["B", "C", "D"]:
            m, p = paired_wilcoxon(df, f"rho_{k}", "rho_A")
            print(f"  {k}-A:   mean Δρ = {m:+.3f}   p = {p:.4f}")

        print("\n--- MSE(α̂_scaled, truth): lower = better calibration ---")
        for k in ["A0", "A", "B", "C", "D"]:
            print(f"  {k}: {df[f'mse_{k}'].mean():.4f}")

        print("\n--- var(alpha-hat) — lower = stronger shrinkage ---")
        for k in ["A0", "A", "B", "C", "D"]:
            print(f"  {k}: {df[f'var_{k}'].mean():.4f}")

        print("\n--- Build-level PDS selection: k=8 "
              f"({N_USEFUL_XB} useful / {N_NOISE_XB} noise) ---")
        print(f"  C useful recall:  {df['C_useful_recall'].mean():.3f}")
        print(f"  C noise FPR:      {df['C_noise_fpr'].mean():.3f}")
        print(f"  C selected-k:     {df['C_sel_k'].mean():.1f} ± {df['C_sel_k'].std():.1f}")
        print(f"  D (post-ICP) useful recall: {df['D_useful_recall'].mean():.3f}")
        print(f"  D (post-ICP) noise FPR:     {df['D_noise_fpr'].mean():.3f}")
        print(f"  D (post-ICP) selected-k:    {df['D_sel_k'].mean():.1f} ± {df['D_sel_k'].std():.1f}")

        print("\n--- Stage-1 violation: bad-controls admitted at CELL level (3 cols) ---")
        print("  (E/F/G admit duration, damage_eff, overload_diff as "
              "within-TWFE covariates)")
        print(f"  E admit-all rho_E:            {df['rho_E'].mean():+.3f} "
              f"(Δ vs A = {df['rho_E'].mean() - df['rho_A'].mean():+.3f})")
        print(f"  F PDS rescue, bad retained:   {df['F_bad_retained'].mean():.3f} "
              f"(rho_F = {df['rho_F'].mean():+.3f})")
        print(f"  G PDS+ICP, bad retained:      {df['G_bad_retained'].mean():.3f} "
              f"(rho_G = {df['rho_G'].mean():+.3f})")

    if not args.synthetic_only:
        print(f"\n[2/2] Hammerhead replay — ship-gate")
        ham = hammerhead_replay()
        plot_hammerhead(ham, OUT_DIR / "hammerhead_gate.png")
        ham["gate_df"].to_csv(OUT_DIR / "hammerhead_gate.csv", index=False)
        print(f"  n_builds={ham['n_builds_fit']} n_obs={ham['n_obs']} n_opp={ham['n_opponents']}")
        print(f"  Mean rho across probes:  {ham['mean_rhos']}")
        print(f"  var(alpha-hat):          {ham['var_alpha']}")
        print(f"  PDS retained {len(ham['pds_cols_selected'])} of 16 build covariates")
        print(f"    kept: {ham['pds_cols_selected']}")
        print(f"    dropped: {ham['pds_cols_rejected']}")
        print(f"  ICP narrowed PDS to: {ham['icp_cols_selected']}")


if __name__ == "__main__":
    main()
