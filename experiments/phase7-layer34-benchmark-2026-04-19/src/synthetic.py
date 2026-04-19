"""Synthetic landscapes that replicate the aborted Phase-7-prep run's pathology.

We observed per-hull TWFE α̂ variance (τ̂²) ranging from 2e-7 (lasher, frigate)
to 1.3e-3 (sunder, destroyer). Per-build σ̂² was 2e-5 (wolf) to 3e-2 (hammerhead).

Synthetic recipe for a "hull landscape":
    y_i = f(x_i) + ε_i,  ε_i ~ N(0, σ²_hull)
    f(x) : [0,1]^d → R with intrinsic variance τ²_hull

We replicate three archetypes observed in the data:
    - FLAT-LANDSCAPE (lasher-like): τ² ≈ 2e-7, σ² ≈ 6e-5.
      No meaningful gradient. Optimizer cannot distinguish builds.
    - NARROW-SIGNAL (wolf-like): τ² ≈ 1e-4, σ² ≈ 2e-3.
      Weak but present signal. Extractable with enough budget.
    - LEARNABLE (hammerhead-like): τ² ≈ 8e-4, σ² ≈ 3e-2.
      Clear gradient, matches hulls the optimizer succeeded on.

Each landscape is a GP sample on [0,1]^d so the ground truth is known
analytically — we draw τ² · (n-dim RBF sample) as f(x).
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class Landscape:
    """A synthetic fitness function with known noise structure."""
    name: str
    dim: int
    tau2: float           # between-build intrinsic variance
    sigma2: float         # per-observation noise variance
    rbf_length: float     # smoothness of the true fitness function
    rng_seed: int
    anchors_X: np.ndarray | None = None
    anchors_y: np.ndarray | None = None

    def __post_init__(self):
        # Build a deterministic reference via 400 anchor points so every call
        # to `f(x)` gives a consistent answer. Pre-draw anchors from RBF.
        rng = np.random.default_rng(self.rng_seed)
        n_anchor = 400
        self.anchors_X = rng.uniform(0, 1, size=(n_anchor, self.dim))
        # Covariance matrix for anchors
        K = self._rbf(self.anchors_X, self.anchors_X)
        K += 1e-6 * np.eye(n_anchor)  # jitter
        L = np.linalg.cholesky(K)
        u = rng.standard_normal(n_anchor)
        # Scale so Var(f) ≈ tau2 across the domain
        self.anchors_y = L @ u * np.sqrt(self.tau2)
        # Precompute K^-1 y for fast interpolation
        self._Kinv_y = np.linalg.solve(K, self.anchors_y)
        self._rng = np.random.default_rng(self.rng_seed + 1)

    def _rbf(self, X1, X2):
        X1 = np.atleast_2d(X1); X2 = np.atleast_2d(X2)
        d2 = np.sum(X1**2, 1)[:, None] + np.sum(X2**2, 1)[None, :] - 2 * X1 @ X2.T
        return np.exp(-d2 / (2 * self.rbf_length**2))

    def f(self, x):
        """Noiseless ground-truth fitness at a single point x."""
        x = np.atleast_2d(x)
        k_star = self._rbf(x, self.anchors_X)
        return float((k_star @ self._Kinv_y)[0])

    def f_batch(self, X):
        """Noiseless ground-truth at a batch of points, shape (n, d)."""
        X = np.atleast_2d(X)
        return self._rbf(X, self.anchors_X) @ self._Kinv_y

    def sample(self, x, rng=None):
        """Noisy observation y = f(x) + ε."""
        r = rng or self._rng
        return self.f(x) + r.normal(0, np.sqrt(self.sigma2))


# Preset archetypes matching the aborted run's per-hull diagnostics
PRESETS = {
    "frigate_flat":       Landscape("frigate_flat",       dim=6, tau2=2e-7, sigma2=6e-5, rbf_length=0.3, rng_seed=1),
    "frigate_weak":       Landscape("frigate_weak",       dim=6, tau2=1e-4, sigma2=2e-3, rbf_length=0.3, rng_seed=2),
    "destroyer_signal":   Landscape("destroyer_signal",   dim=6, tau2=8e-4, sigma2=3e-2, rbf_length=0.3, rng_seed=3),
    "cruiser_signal":     Landscape("cruiser_signal",     dim=6, tau2=1.3e-3, sigma2=3e-2, rbf_length=0.3, rng_seed=4),
}
