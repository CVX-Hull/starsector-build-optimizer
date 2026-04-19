"""Two BO loops: vanilla expected-improvement vs TurBO-style trust-region.

Vanilla BO : fit GP → select argmax EI across a global candidate pool.
TurBO      : maintain a trust region centered on best observation, shrink/grow
             it on failure/success, restart when it shrinks below threshold.

Reference: Eriksson et al. 2019 (arXiv:1910.01739). Trust-region rules:
    - Start TR half-side length = 0.8.
    - On `n_success=3` consecutive improvements → TR *= 2 (capped at 1.6).
    - On `n_failure=d` consecutive non-improvements → TR /= 2.
    - Below 2^-5 ≈ 0.03 → restart with fresh random init + new TR.

Candidate selection inside TR: sample k random candidates in a box of
half-side TR around current best (clamped to domain).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy.stats import norm

from .gp import HomoscedasticGP, HeteroscedasticGP


def expected_improvement(mu, sigma, y_best):
    sigma = np.clip(sigma, 1e-9, None)
    z = (mu - y_best) / sigma
    return (mu - y_best) * norm.cdf(z) + sigma * norm.pdf(z)


def vanilla_bo(
    landscape,
    n_init: int,
    n_iter: int,
    n_candidates: int = 1000,
    seed: int = 0,
):
    """Standard BO with single-task homoscedastic GP and EI acquisition."""
    rng = np.random.default_rng(seed)
    d = landscape.dim
    X = rng.uniform(0, 1, size=(n_init, d))
    y = np.array([landscape.sample(x, rng=rng) for x in X])
    best_trace = [float(np.max(y))]
    for _ in range(n_iter):
        gp = HomoscedasticGP().fit(X, y)
        cands = rng.uniform(0, 1, size=(n_candidates, d))
        mu, var = gp.predict(cands)
        ei = expected_improvement(mu, np.sqrt(var), np.max(y))
        xi = cands[np.argmax(ei)]
        yi = landscape.sample(xi, rng=rng)
        X = np.vstack([X, xi])
        y = np.append(y, yi)
        best_trace.append(float(np.max(y)))
    true_fits = landscape.f_batch(X)
    return X, y, true_fits, np.array(best_trace)


def turbo_bo(
    landscape,
    n_init: int,
    n_iter: int,
    tr_init: float = 0.8,
    tr_min: float = 0.03,
    tr_max: float = 1.6,
    n_success: int = 3,
    n_failure: int | None = None,
    n_candidates: int = 1000,
    seed: int = 0,
):
    """BO with TurBO-style adaptive trust region + restart."""
    rng = np.random.default_rng(seed)
    d = landscape.dim
    if n_failure is None:
        n_failure = d
    X = rng.uniform(0, 1, size=(n_init, d))
    y = np.array([landscape.sample(x, rng=rng) for x in X])
    best_trace = [float(np.max(y))]
    tr = tr_init
    succ_streak, fail_streak = 0, 0
    restart_events = []
    for it in range(n_iter):
        gp = HomoscedasticGP().fit(X, y)
        best_idx = int(np.argmax(y)); x_center = X[best_idx]
        lo = np.clip(x_center - tr / 2, 0, 1)
        hi = np.clip(x_center + tr / 2, 0, 1)
        cands = rng.uniform(lo, hi, size=(n_candidates, d))
        mu, var = gp.predict(cands)
        ei = expected_improvement(mu, np.sqrt(var), np.max(y))
        xi = cands[np.argmax(ei)]
        yi = landscape.sample(xi, rng=rng)
        improved = yi > np.max(y)
        X = np.vstack([X, xi]); y = np.append(y, yi)
        best_trace.append(float(np.max(y)))
        # Trust-region adaptation
        if improved:
            succ_streak += 1; fail_streak = 0
            if succ_streak >= n_success:
                tr = min(tr_max, tr * 2); succ_streak = 0
        else:
            fail_streak += 1; succ_streak = 0
            if fail_streak >= n_failure:
                tr = tr / 2; fail_streak = 0
        if tr < tr_min:
            restart_events.append(it)
            tr = tr_init
            X_new = rng.uniform(0, 1, size=(n_init, d))
            y_new = np.array([landscape.sample(x, rng=rng) for x in X_new])
            X = np.vstack([X, X_new]); y = np.append(y, y_new)
            succ_streak = fail_streak = 0
    true_fits = landscape.f_batch(X)
    return X, y, true_fits, np.array(best_trace), restart_events
