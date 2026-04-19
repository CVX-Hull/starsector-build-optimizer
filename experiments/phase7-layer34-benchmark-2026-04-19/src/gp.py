"""Minimal GP regressor with homoscedastic OR class-blocked heteroscedastic noise.

RBF kernel: k(x,x') = s_f^2 exp(-||x-x'||^2 / (2 l^2))

Two variants:
    HomoscedasticGP : single noise variance σ² shared across all points
    HeteroscedasticGP : noise variance σ²(class) varies by a categorical
                        group label attached to each observation. Here each
                        "class" corresponds to one synthetic hull.

The heteroscedastic variant is the simplest instantiation of RAHBO /
hetGP's structure (Makarova 2021 / Binois & Gramacy) that is relevant to
our domain: hull identity is the class, and the observed σ² varies by
3+ orders of magnitude across hulls.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize


def _rbf(X1, X2, length, sf2):
    X1 = np.atleast_2d(X1); X2 = np.atleast_2d(X2)
    d2 = np.sum(X1**2, 1)[:, None] + np.sum(X2**2, 1)[None, :] - 2 * X1 @ X2.T
    return sf2 * np.exp(-d2 / (2 * length**2))


class HomoscedasticGP:
    """Single noise variance σ² shared across every observation."""

    def __init__(self, length_init=0.3, sf2_init=1e-3, noise_init=1e-2):
        self.length = length_init
        self.sf2 = sf2_init
        self.noise = noise_init

    def _nll(self, theta, X, y):
        length, log_sf2, log_noise = theta
        length = np.abs(length) + 1e-3
        sf2 = np.exp(log_sf2); noise = np.exp(log_noise)
        K = _rbf(X, X, length, sf2) + noise * np.eye(len(X)) + 1e-9 * np.eye(len(X))
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            return 1e10
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
        nll = 0.5 * y @ alpha + np.sum(np.log(np.diag(L))) + 0.5 * len(X) * np.log(2 * np.pi)
        return float(nll)

    def fit(self, X, y):
        self.X = np.atleast_2d(X); self.y = np.asarray(y)
        res = minimize(
            self._nll,
            x0=[self.length, np.log(self.sf2), np.log(self.noise)],
            args=(self.X, self.y),
            method="L-BFGS-B",
            options={"maxiter": 50},
        )
        self.length = np.abs(res.x[0]) + 1e-3
        self.sf2 = np.exp(res.x[1])
        self.noise = np.exp(res.x[2])
        K = _rbf(self.X, self.X, self.length, self.sf2) + self.noise * np.eye(len(X)) + 1e-9 * np.eye(len(X))
        self.L = np.linalg.cholesky(K)
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y))
        return self

    def predict(self, X_star):
        k_star = _rbf(np.atleast_2d(X_star), self.X, self.length, self.sf2)
        mu = k_star @ self.alpha
        v = np.linalg.solve(self.L, k_star.T)
        var = self.sf2 - np.sum(v ** 2, 0) + self.noise
        return mu, np.clip(var, 1e-12, None)


class HeteroscedasticGP:
    """GP with noise variance σ²(class) that varies by observation class.

    The `classes` vector attached to each observation indexes into
    `noise_per_class`. Fitted jointly with length/sf2.
    """

    def __init__(self, n_classes, length_init=0.3, sf2_init=1e-3,
                 noise_init_per_class=None):
        self.n_classes = n_classes
        self.length = length_init
        self.sf2 = sf2_init
        if noise_init_per_class is None:
            self.noise_per_class = np.full(n_classes, 1e-2)
        else:
            self.noise_per_class = np.asarray(noise_init_per_class, dtype=float)

    def _nll(self, theta, X, y, cls):
        length, log_sf2 = theta[0], theta[1]
        log_noise = theta[2:]
        length = np.abs(length) + 1e-3
        sf2 = np.exp(log_sf2); noise = np.exp(log_noise)
        noise_diag = noise[cls]
        K = _rbf(X, X, length, sf2) + np.diag(noise_diag) + 1e-9 * np.eye(len(X))
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            return 1e10
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
        nll = 0.5 * y @ alpha + np.sum(np.log(np.diag(L))) + 0.5 * len(X) * np.log(2 * np.pi)
        return float(nll)

    def fit(self, X, y, cls):
        self.X = np.atleast_2d(X); self.y = np.asarray(y); self.cls = np.asarray(cls, dtype=int)
        x0 = np.concatenate([[self.length, np.log(self.sf2)], np.log(self.noise_per_class)])
        res = minimize(
            self._nll, x0=x0, args=(self.X, self.y, self.cls),
            method="L-BFGS-B", options={"maxiter": 100},
        )
        self.length = np.abs(res.x[0]) + 1e-3
        self.sf2 = np.exp(res.x[1])
        self.noise_per_class = np.exp(res.x[2:])
        noise_diag = self.noise_per_class[self.cls]
        K = _rbf(self.X, self.X, self.length, self.sf2) + np.diag(noise_diag) + 1e-9 * np.eye(len(X))
        self.L = np.linalg.cholesky(K)
        self.alpha = np.linalg.solve(self.L.T, np.linalg.solve(self.L, self.y))
        return self

    def predict(self, X_star, cls_star):
        k_star = _rbf(np.atleast_2d(X_star), self.X, self.length, self.sf2)
        mu = k_star @ self.alpha
        v = np.linalg.solve(self.L, k_star.T)
        var = self.sf2 - np.sum(v ** 2, 0)
        var += self.noise_per_class[np.asarray(cls_star, dtype=int)]
        return mu, np.clip(var, 1e-12, None)
