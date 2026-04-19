"""Layer 4 benchmark — TurBO-style trust-region restart on flat-plateau-with-peak.

Claim: when the fitness landscape has large flat regions and isolated peaks,
a vanilla BO with EI over a global candidate pool wastes budget sampling
inside the plateau (EI ~= 0 almost everywhere). TurBO's trust region
adapts: it shrinks on failure, and restarts when it falls below threshold,
re-seeding exploration in a fresh location. In landscapes where the best
region is tiny relative to the domain, this finds the peak faster.

Setup:
    - 6-D domain, y = f(x) with a broad flat plateau + one narrow Gaussian
      peak located at a random corner.
    - Per-seed: run vanilla BO (200 iters) and TurBO (200 iters) from the
      same initial 10-point Latin-hypercube.
    - Metric: best true-fitness reached at N iterations.

Success criteria:
    - TurBO's best-found regret at 200 iters is lower than vanilla's,
      especially when the peak is far from the initial best point.
    - TurBO restart events are concentrated after plateau stagnation.
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.bo import vanilla_bo, turbo_bo


class PlateauPeakLandscape:
    """6D landscape: flat plateau value ~0 everywhere + one narrow Gaussian peak.

    Mirrors the aborted-run pattern where most of the search space produces
    identical fitness (plateau) and only a narrow region gives signal.
    """
    def __init__(self, dim=6, peak_center=None, peak_width=0.05, peak_height=1.0,
                 noise=1e-4, plateau_curvature=0.02, seed=0):
        self.dim = dim
        self.noise = noise
        self.peak_height = peak_height
        self.peak_width = peak_width
        self.plateau_curvature = plateau_curvature
        rng = np.random.default_rng(seed)
        if peak_center is None:
            peak_center = rng.uniform(0.1, 0.9, size=dim)
        self.peak_center = peak_center
        self._rng = np.random.default_rng(seed + 1)

    def f(self, x):
        x = np.atleast_1d(x)
        d2 = np.sum((x - self.peak_center) ** 2)
        peak = self.peak_height * np.exp(-d2 / (2 * self.peak_width ** 2))
        # tiny quadratic slope so the plateau isn't pathologically flat
        plateau = -self.plateau_curvature * np.sum((x - 0.5) ** 2)
        return float(peak + plateau)

    def f_batch(self, X):
        return np.array([self.f(x) for x in np.atleast_2d(X)])

    def sample(self, x, rng=None):
        r = rng or self._rng
        return self.f(x) + r.normal(0, np.sqrt(self.noise))


def run_once(seed, n_init=10, n_iter=190):
    L = PlateauPeakLandscape(dim=6, seed=seed)
    true_max = L.peak_height + (-L.plateau_curvature * 0)  # at peak_center

    _, _, _, vanilla_trace = vanilla_bo(L, n_init=n_init, n_iter=n_iter, seed=seed)
    _, _, _, turbo_trace, restart_events = turbo_bo(L, n_init=n_init, n_iter=n_iter, seed=seed)
    return vanilla_trace, turbo_trace, restart_events, true_max


def main():
    n_seeds = 10
    n_init = 10
    n_iter = 190
    vanilla_traces = []
    turbo_traces = []
    all_restarts = []
    true_maxes = []
    for seed in range(n_seeds):
        vt, tt, re, tm = run_once(seed, n_init=n_init, n_iter=n_iter)
        vanilla_traces.append(vt); turbo_traces.append(tt)
        all_restarts.append(len(re)); true_maxes.append(tm)
        print(f"  seed {seed}: vanilla best={vt[-1]:.3f}  turbo best={tt[-1]:.3f}  "
              f"true_max≈{tm:.3f}  restarts={len(re)}")
    V = np.array(vanilla_traces); T = np.array(turbo_traces); M = np.array(true_maxes)
    # Regret at end
    regret_vanilla = M - V[:, -1]
    regret_turbo = M - T[:, -1]
    print()
    print("=" * 80)
    print(f"Plateau-peak landscape ({n_seeds} seeds, {n_init} init + {n_iter} iter = 200 total)")
    print("=" * 80)
    print(f"\nMedian regret (true_max - best_found) at end:")
    print(f"  vanilla BO : {float(np.median(regret_vanilla)):.4f}")
    print(f"  TurBO      : {float(np.median(regret_turbo)):.4f}")
    print(f"  TurBO advantage: {float(np.median(regret_vanilla) - np.median(regret_turbo)):+.4f}")

    print(f"\nRegret at every 40 iter (median across seeds):")
    print(f"  {'iter':>4s} {'vanilla':>10s} {'turbo':>10s} {'delta':>10s}")
    for k in [40, 80, 120, 160, 200]:
        rv = float(np.median(M - V[:, k - 1]))
        rt = float(np.median(M - T[:, k - 1]))
        print(f"  {k:>4d} {rv:10.4f} {rt:10.4f} {rv - rt:+10.4f}")

    print(f"\nTurBO restart events per run: median={int(np.median(all_restarts))}, "
          f"range=[{min(all_restarts)}, {max(all_restarts)}]")

    win_count = int(np.sum(regret_turbo < regret_vanilla))
    print(f"\nTurBO beats vanilla in {win_count}/{n_seeds} seeds")

    # Save traces for plotting
    import pandas as pd
    df = pd.DataFrame({
        "iter": np.tile(np.arange(V.shape[1]), n_seeds),
        "seed": np.repeat(np.arange(n_seeds), V.shape[1]),
        "vanilla": V.flatten(),
        "turbo": T.flatten(),
    })
    df.to_csv(ROOT / "layer4_traces.csv", index=False)
    print(f"\nSaved layer4_traces.csv to {ROOT}")


if __name__ == "__main__":
    main()
