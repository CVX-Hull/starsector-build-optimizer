"""Deconfounding — TWFE decomposition for schedule-adjusted build quality.

Decomposes the build × opponent score matrix into additive components:
    score_ij = α_i (build quality) + β_j (opponent difficulty) + ε_ij

The α_i estimates are comparable across builds that faced different opponent
subsets, solving the cross-subset comparability problem. The alternating
projection algorithm converges in ~20 iterations for matrices up to 1000×100.

See spec 28 for algorithm details and design rationale.
"""

from __future__ import annotations

import logging
import warnings

import numpy as np

from .models import TWFEConfig

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

    @property
    def n_builds(self) -> int:
        return len(self._build_map)

    @property
    def n_opponents(self) -> int:
        return len(self._opp_map)

    def _ensure_decomposed(self, config: TWFEConfig) -> None:
        """Run TWFE decomposition if the cache is dirty."""
        if not self._dirty and self._alpha is not None:
            return

        matrix = self._materialize()
        self._alpha, self._beta = twfe_decompose(
            matrix, n_iters=config.n_iters, ridge=config.ridge
        )
        self._dirty = False

    def _materialize(self) -> np.ndarray:
        """Build dense matrix from recorded entries. NaN = unobserved."""
        n_rows = len(self._build_map)
        n_cols = len(self._opp_map)
        matrix = np.full((max(n_rows, 1), max(n_cols, 1)), np.nan)
        for row, col, val in self._entries:
            matrix[row, col] = val
        return matrix
