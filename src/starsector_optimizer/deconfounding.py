"""Deconfounding — TWFE decomposition + EB shrinkage for schedule-adjusted
and prior-informed build quality estimation.

A1 — TWFE decomposition: score_ij = α_i (build quality) + β_j (opponent
difficulty) + ε_ij. The α_i estimates are comparable across builds that faced
different opponent subsets. Alternating projection converges in ~20 iterations
for matrices up to 1000×100.

A2′ — Empirical-Bayes shrinkage (Phase 5D): fuses α̂_i with a regression
prior γ̂ᵀ[1, X_i] over pre-matchup covariates, using per-build precision
weights w_i = τ̂²/(τ̂² + σ̂_i²). Followed by optional Lin-Louis-Shen (1999)
triple-goal rank correction to preserve ranks while restoring the raw
histogram.

See spec 28 for algorithm details and design rationale.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

from .models import EBShrinkageConfig, TWFEConfig

logger = logging.getLogger(__name__)

_EPSILON = 1e-12


def twfe_decompose(
    score_matrix: np.ndarray,
    n_iters: int = 20,
    ridge: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """Alternating projection: score_ij = α_i + β_j + ε_ij.

    Args:
        score_matrix: (n_builds, n_opps) array. NaN = unobserved.
        n_iters: Number of alternating projection iterations.
        ridge: Regularization added to observation counts (prevents divergence
            when a row/column has very few observations).

    Returns:
        (alpha, beta) — build quality and opponent difficulty arrays.
    """
    n_builds, n_opps = score_matrix.shape
    observed = ~np.isnan(score_matrix)
    alpha = np.zeros(n_builds)
    beta = np.zeros(n_opps)

    for _ in range(n_iters):
        # Update beta: β_j = mean(Y_ij − α_i) for observed i
        for j in range(n_opps):
            mask = observed[:, j]
            count = mask.sum()
            if count > 0:
                beta[j] = np.sum(score_matrix[mask, j] - alpha[mask]) / (
                    count + ridge
                )

        # Update alpha: α_i = mean(Y_ij − β_j) for observed j
        for i in range(n_builds):
            mask = observed[i, :]
            count = mask.sum()
            if count > 0:
                alpha[i] = np.sum(score_matrix[i, mask] - beta[mask]) / (
                    count + ridge
                )

    return alpha, beta


def trimmed_alpha(
    scores: np.ndarray,
    beta: np.ndarray,
    trim_worst: int,
) -> float:
    """Build quality from trimmed mean of residuals after removing opponent effects.

    Args:
        scores: Row of score_matrix for build i (may contain NaN).
        beta: Opponent difficulty array (same length as scores).
        trim_worst: Drop this many lowest residuals before averaging.
            0 = plain mean. If >= n_observed, warns and returns untrimmed mean.

    Returns:
        Trimmed mean of (Y_ij − β_j) residuals.
    """
    observed = ~np.isnan(scores)
    if not np.any(observed):
        return 0.0

    residuals = scores[observed] - beta[observed]
    n = len(residuals)

    if trim_worst >= n:
        warnings.warn(
            f"trim_worst={trim_worst} >= n_observed={n}; "
            f"returning untrimmed mean",
            UserWarning,
            stacklevel=2,
        )
        return float(np.mean(residuals))

    if trim_worst > 0:
        sorted_residuals = np.sort(residuals)
        residuals = sorted_residuals[trim_worst:]

    return float(np.mean(residuals))


class ScoreMatrix:
    """Sparse build × opponent score accumulator with cached TWFE decomposition.

    Records raw combat_fitness scores incrementally. On demand, materializes
    the dense matrix and runs TWFE decomposition. Caches the result until
    new observations arrive.
    """

    def __init__(self) -> None:
        self._build_map: dict[int, int] = {}  # build_idx → row index
        self._opp_map: dict[str, int] = {}    # opp_name → col index
        self._entries: list[tuple[int, int, float]] = []
        self._dirty: bool = True
        self._alpha: np.ndarray | None = None
        self._beta: np.ndarray | None = None
        self._sigma_eps_sq: float | None = None  # pooled residual MSE (Phase 5D)

    def record(self, build_idx: int, opp_name: str, raw_score: float) -> None:
        """Add one observation. Auto-expands index maps for new builds/opponents."""
        if build_idx not in self._build_map:
            self._build_map[build_idx] = len(self._build_map)
        if opp_name not in self._opp_map:
            self._opp_map[opp_name] = len(self._opp_map)

        row = self._build_map[build_idx]
        col = self._opp_map[opp_name]
        self._entries.append((row, col, raw_score))
        self._dirty = True

    def build_alpha(self, build_idx: int, config: TWFEConfig) -> float:
        """Decompose and return trimmed α_i for the given build.

        Re-runs TWFE decomposition only when the cache is dirty (new
        observations since last decomposition). Then applies trimmed_alpha
        with config.trim_worst.
        """
        self._ensure_decomposed(config)

        row = self._build_map.get(build_idx)
        if row is None:
            return 0.0

        matrix = self._materialize()
        assert self._beta is not None
        return trimmed_alpha(matrix[row], self._beta, config.trim_worst)

    def opponent_beta(self, opp_name: str) -> float:
        """Return cached β_j for the given opponent.

        Raises ValueError if no decomposition has been computed.
        """
        if self._beta is None:
            raise ValueError(
                "No decomposition computed yet — call build_alpha() first"
            )
        col = self._opp_map.get(opp_name)
        if col is None:
            raise ValueError(f"Unknown opponent: {opp_name}")
        return float(self._beta[col])

    def build_sigma_sq(self, build_idx: int) -> float:
        """Return per-build variance σ̂_i² = σ̂_ε² / n_i (Phase 5D).

        Used by `eb_shrinkage` to precision-weight the build's TWFE estimate.
        Raises ValueError if the cache is empty or dirty — callers must
        invoke `build_alpha()` at least once since the most recent `record()`
        before calling this.
        """
        if self._sigma_eps_sq is None or self._dirty:
            raise ValueError(
                "No decomposition computed yet (or dirty) — "
                "call build_alpha() first"
            )
        row = self._build_map.get(build_idx)
        if row is None:
            raise ValueError(f"Unknown build: {build_idx}")

        # n_i = count of observed (non-NaN) entries for this build
        matrix = self._materialize()
        n_i = int(np.sum(~np.isnan(matrix[row])))
        return self._sigma_eps_sq / max(n_i, 1)

    @property
    def n_builds(self) -> int:
        return len(self._build_map)

    @property
    def n_opponents(self) -> int:
        return len(self._opp_map)

    def _ensure_decomposed(self, config: TWFEConfig) -> None:
        """Run TWFE decomposition if the cache is dirty.

        Also computes and caches the pooled residual MSE (σ̂_ε²) needed by
        `build_sigma_sq()` for EB shrinkage.
        """
        if not self._dirty and self._alpha is not None:
            return

        matrix = self._materialize()
        self._alpha, self._beta = twfe_decompose(
            matrix, n_iters=config.n_iters, ridge=config.ridge
        )

        # Pooled residual MSE over observed cells (Phase 5D).
        # n_params = n_builds + n_opps - 1 (one identifying constraint:
        # α can absorb a constant that β offsets, so the degrees of freedom
        # are reduced by one relative to the naive sum).
        observed_mask = ~np.isnan(matrix)
        if np.any(observed_mask):
            pred = self._alpha[:, None] + self._beta[None, :]
            diff = np.where(observed_mask, matrix - pred, 0.0)
            resid_sq = float(np.sum(diff * diff))
            n_obs = int(observed_mask.sum())
            n_builds, n_opps = matrix.shape
            denom = max(n_obs - (n_builds + n_opps - 1), 1)
            self._sigma_eps_sq = resid_sq / denom
        else:
            self._sigma_eps_sq = 0.0

        self._dirty = False

    def _materialize(self) -> np.ndarray:
        """Build dense matrix from recorded entries. NaN = unobserved."""
        n_rows = len(self._build_map)
        n_cols = len(self._opp_map)
        matrix = np.full((max(n_rows, 1), max(n_cols, 1)), np.nan)
        for row, col, val in self._entries:
            matrix[row, col] = val
        return matrix


def eb_shrinkage(
    alpha: np.ndarray,
    sigma_sq: np.ndarray,
    X: np.ndarray,
    config: EBShrinkageConfig,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """Empirical-Bayes shrinkage of TWFE α̂ toward a regression prior.

    Implements the closed-form two-level Gaussian posterior mean:
        α̂_i | α_i  ~ N(α_i, σ̂_i²)              (A1 TWFE)
        α_i  | X_i  ~ N(γ̂ᵀ [1, X_i], τ̂²)       (learned prior)
        α̂_EB_i    = w_i · α̂_i + (1 − w_i) · γ̂ᵀ[1, X_i]
        w_i       = τ̂² / (τ̂² + σ̂_i²)

    Args:
        alpha: (n,) TWFE point estimates α̂_i.
        sigma_sq: (n,) per-build variances σ̂_i² = σ̂_ε² / n_i.
        X: (n, p) pre-matchup covariate matrix.
        config: shrinkage parameters (ridge, floor, triple-goal toggle).

    Returns:
        (alpha_eb, gamma, tau2, kept_cols) where kept_cols indexes the
        non-degenerate columns of X that made it into the fit.

    Raises:
        ValueError: if n < 3 (not enough builds for a stable fit).
    """
    n = len(alpha)
    if n < 3:
        raise ValueError(f"eb_shrinkage needs n >= 3 builds, got {n}")

    var_alpha = float(np.var(alpha, ddof=0))
    if var_alpha < _EPSILON:
        warnings.warn(
            "Var(alpha) is effectively zero — returning raw alpha unchanged",
            UserWarning,
            stacklevel=2,
        )
        kept = np.arange(X.shape[1], dtype=np.int_)
        return alpha.copy(), np.zeros(1 + X.shape[1]), 0.0, kept

    # Standardize X columns, dropping any with zero std.
    col_mean = X.mean(axis=0)
    col_std = X.std(axis=0, ddof=0)
    kept = np.where(col_std > _EPSILON)[0]
    if len(kept) < X.shape[1]:
        dropped = [int(i) for i in range(X.shape[1]) if i not in kept]
        warnings.warn(
            f"eb_shrinkage dropped zero-std X columns: {dropped}",
            UserWarning,
            stacklevel=2,
        )
    X_std = (X[:, kept] - col_mean[kept]) / col_std[kept]

    # Augment with intercept column.
    X_aug = np.hstack([np.ones((n, 1)), X_std])

    # Ridge-regularized OLS; intercept row/col unpenalized.
    XtX = X_aug.T @ X_aug
    ridge_diag = np.eye(XtX.shape[0]) * config.ols_ridge
    ridge_diag[0, 0] = 0.0
    gamma = np.linalg.solve(XtX + ridge_diag, X_aug.T @ alpha)

    # Method-of-moments τ̂² with floor.
    mu = X_aug @ gamma
    resid = alpha - mu
    tau2 = max(
        float(np.var(resid, ddof=0) - np.mean(sigma_sq)),
        config.tau2_floor_frac * var_alpha,
    )

    # Per-build shrinkage weights and posterior mean.
    w = tau2 / (tau2 + sigma_sq)
    alpha_eb = w * alpha + (1.0 - w) * mu

    return alpha_eb, gamma, tau2, kept


def triple_goal_rank(posterior: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """Lin-Louis-Shen (1999) triple-goal rank correction.

    Preserves the posterior rank ordering but substitutes the empirical raw
    histogram, restoring the magnitude information that pure EB compresses
    at the tails (Louis 1984). Spearman ρ vs truth is identical to α̂_EB;
    the top/bottom magnitudes match α̂_TWFE, feeding richer exploitation
    signal to Optuna TPE.

    Args:
        posterior: (n,) α̂_EB posterior means (used for rank ordering).
        raw: (n,) α̂_TWFE values (used for histogram substitution).

    Returns:
        (n,) array of substituted values.
    """
    ranks = np.argsort(np.argsort(posterior))
    return np.sort(raw)[ranks]
