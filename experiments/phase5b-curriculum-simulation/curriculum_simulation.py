"""Phase 5B Opponent Curriculum Simulation — Full Research Comparison.

Tests approaches from 6 literature surveys against two core problems:
  P1: Cross-subset comparability (builds face different opponent subsets)
  P2: Temporal confounding (builds improve, masking opponent difficulty)

Strategies tested:
  1. Baseline         — random fixed pool, z-score mean
  2. TWFE             — Two-Way Fixed Effects additive decomposition (α_i + β_j)
  3. Disc + TWFE      — Discriminative ordering + TWFE
  4. Incumbent + TWFE — SMAC-style incumbent overlap + TWFE
  5. ALS imputation   — Rank-3 matrix completion, score on full imputed row
  6. Active + LURE    — Adaptive opponent selection + LURE debiasing
  7. WHR asymmetric   — Whole-History Rating (time-varying builds, static opps)
  8. Trimmed TWFE     — TWFE + trimmed mean (drop worst 2 opponents)
  9. Full pipeline    — Anchors + incumbent overlap + active + TWFE + trimmed
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass, field
from scipy import stats, optimize

plt.style.use("seaborn-v0_8-darkgrid")
plt.rcParams["figure.figsize"] = (14, 8)


# ═══════════════════════════════════════════════════════════════════════════
# Generative Model
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Opponent:
    name: str
    difficulty: float
    discrimination: float
    archetype_vuln: np.ndarray = field(default_factory=lambda: np.zeros(3))


@dataclass
class Build:
    quality: float
    archetype: np.ndarray = field(default_factory=lambda: np.zeros(3))


def generate_opponents(n: int, rng: np.random.Generator) -> list[Opponent]:
    """Bimodal: ~40% trivial, ~40% moderate, ~20% hard combat ships."""
    opponents = []
    for i in range(n):
        if i < int(n * 0.4):
            difficulty = rng.normal(-1.5, 0.3)
            discrimination = rng.uniform(0.1, 0.4)
        elif i < int(n * 0.8):
            difficulty = rng.normal(0.5, 0.8)
            discrimination = rng.uniform(0.5, 1.0)
        else:
            difficulty = rng.normal(2.0, 0.5)
            discrimination = rng.uniform(0.7, 1.5)
        vuln = rng.dirichlet([1, 1, 1]) * rng.uniform(0.5, 2.0)
        vuln -= vuln.mean()
        opponents.append(Opponent(
            name=f"opp_{i:03d}", difficulty=difficulty,
            discrimination=discrimination, archetype_vuln=vuln,
        ))
    return opponents


def generate_builds(n: int, rng: np.random.Generator,
                    improving: bool = True) -> list[Build]:
    builds = []
    for i in range(n):
        base = rng.normal(0, 1) + (0.3 * np.sqrt(i) if improving else 0)
        builds.append(Build(quality=base, archetype=rng.dirichlet([2, 2, 2])))
    return builds


def simulate_matchup(build: Build, opp: Opponent,
                     rng: np.random.Generator, noise_std: float = 0.5) -> float:
    rps = np.dot(build.archetype, opp.archetype_vuln)
    logit = opp.discrimination * (build.quality - opp.difficulty) + rps
    p_win = 1 / (1 + np.exp(-logit))
    outcome = (p_win - 0.5) * 2
    return float(np.clip(outcome + rng.normal(0, noise_std), -1.5, 1.5))


# ═══════════════════════════════════════════════════════════════════════════
# Shared Infrastructure
# ═══════════════════════════════════════════════════════════════════════════

class RunningStats:
    """Welford's online mean/variance for z-score normalization."""
    def __init__(self) -> None:
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0

    def update(self, x: float) -> None:
        self._n += 1
        d = x - self._mean
        self._mean += d / self._n
        self._m2 += d * (x - self._mean)

    @property
    def std(self) -> float:
        return (self._m2 / (self._n - 1)) ** 0.5 if self._n >= 2 else 0.0

    def z_score(self, x: float) -> float:
        if self._n < 2 or self.std < 1e-9:
            return 0.0
        return (x - self._mean) / self.std


class EloTracker:
    """Standard Elo for opponents."""
    def __init__(self, k: float = 32.0, initial: float = 1500.0):
        self.ratings: dict[str, float] = {}
        self.k = k
        self.initial = initial

    def get(self, name: str) -> float:
        return self.ratings.get(name, self.initial)

    def update(self, opp_name: str, build_won: bool) -> None:
        r = self.get(opp_name)
        expected = 1 / (1 + 10 ** ((1500 - r) / 400))
        actual = 0.0 if build_won else 1.0
        self.ratings[opp_name] = r + self.k * (actual - expected)


def should_prune(current: list[float], historical: list[list[float]],
                 step: int, min_steps: int = 2, p_thresh: float = 0.1) -> bool:
    if step < min_steps or len(historical) < 5:
        return False
    medians = []
    for s in range(step + 1):
        vals = [h[s] for h in historical if len(h) > s]
        if vals:
            medians.append(np.median(vals))
    if len(medians) < min_steps:
        return False
    diffs = [current[s] - medians[s] for s in range(len(medians))]
    if all(d == 0 for d in diffs):
        return False
    try:
        _, p = stats.wilcoxon(diffs, alternative="less")
        return p < p_thresh
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# TWFE: Two-Way Fixed Effects Decomposition
# ═══════════════════════════════════════════════════════════════════════════

def twfe_decompose(
    score_matrix: np.ndarray,  # (n_builds, n_opps), NaN = unobserved
    n_iters: int = 20,
    ridge: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """Alternating projection to decompose score_ij = α_i + β_j + ε.

    Returns (alpha, beta): build quality and opponent difficulty arrays.
    """
    n_builds, n_opps = score_matrix.shape
    observed = ~np.isnan(score_matrix)
    alpha = np.zeros(n_builds)
    beta = np.zeros(n_opps)

    for _ in range(n_iters):
        # Update beta: β_j = mean(Y_ij - α_i) for observed (i,j)
        for j in range(n_opps):
            mask = observed[:, j]
            if mask.sum() > 0:
                beta[j] = (np.sum(score_matrix[mask, j] - alpha[mask])
                           / (mask.sum() + ridge))

        # Update alpha: α_i = mean(Y_ij - β_j) for observed (i,j)
        for i in range(n_builds):
            mask = observed[i, :]
            if mask.sum() > 0:
                alpha[i] = (np.sum(score_matrix[i, mask] - beta[mask])
                            / (mask.sum() + ridge))

    return alpha, beta


# ═══════════════════════════════════════════════════════════════════════════
# ALS Matrix Completion (rank-r)
# ═══════════════════════════════════════════════════════════════════════════

def als_complete(
    score_matrix: np.ndarray,
    rank: int = 3,
    n_iters: int = 20,
    ridge: float = 0.1,
) -> np.ndarray:
    """Alternating Least Squares matrix completion. Returns completed matrix."""
    n_builds, n_opps = score_matrix.shape
    observed = ~np.isnan(score_matrix)
    rng = np.random.default_rng(0)

    U = rng.normal(0, 0.1, (n_builds, rank))
    V = rng.normal(0, 0.1, (n_opps, rank))

    Y = np.nan_to_num(score_matrix, nan=0.0)

    for _ in range(n_iters):
        # Fix U, solve for V
        for j in range(n_opps):
            mask = observed[:, j]
            if mask.sum() == 0:
                continue
            U_obs = U[mask]
            y_obs = Y[mask, j]
            V[j] = np.linalg.solve(
                U_obs.T @ U_obs + ridge * np.eye(rank), U_obs.T @ y_obs)

        # Fix V, solve for U
        for i in range(n_builds):
            mask = observed[i]
            if mask.sum() == 0:
                continue
            V_obs = V[mask]
            y_obs = Y[i, mask]
            U[i] = np.linalg.solve(
                V_obs.T @ V_obs + ridge * np.eye(rank), V_obs.T @ y_obs)

    return U @ V.T


# ═══════════════════════════════════════════════════════════════════════════
# WHR: Whole-History Rating (asymmetric)
# ═══════════════════════════════════════════════════════════════════════════

def whr_fit(
    matchups: list[tuple[int, int, float]],  # (build_idx, opp_idx, score)
    n_builds: int,
    n_opps: int,
    build_w2: float = 1.0,   # Wiener variance for builds (time-varying)
    opp_w2: float = 0.0,     # Wiener variance for opponents (static)
    n_iters: int = 15,
) -> tuple[np.ndarray, np.ndarray]:
    """Simplified asymmetric WHR via iterative Bradley-Terry with time prior.

    Treats each build as a single time-point (evaluated once).
    Returns (build_ratings, opp_ratings).
    """
    # Initialize
    build_r = np.zeros(n_builds)
    opp_r = np.zeros(n_opps)

    # Group matchups
    build_matchups: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n_builds)}
    opp_matchups: dict[int, list[tuple[int, float]]] = {j: [] for j in range(n_opps)}
    for bi, oj, score in matchups:
        build_matchups[bi].append((oj, score))
        opp_matchups[oj].append((bi, score))

    # Build adjacency for temporal prior on builds
    # Builds are ordered by index (= trial order). Adjacent builds are coupled.
    for _ in range(n_iters):
        # Update build ratings (with temporal prior)
        for i in range(n_builds):
            if not build_matchups[i]:
                continue
            # Gradient and Hessian of log-likelihood
            grad = 0.0
            hess = 0.0
            for oj, score in build_matchups[i]:
                diff = build_r[i] - opp_r[oj]
                p = 1 / (1 + np.exp(-diff))
                # score is continuous in [-1.5, 1.5]; map to [0,1] for BT
                y = (score + 1.5) / 3.0
                grad += y - p
                hess -= p * (1 - p)

            # Temporal prior: penalize deviation from neighbors
            if build_w2 > 0:
                if i > 0:
                    grad -= (build_r[i] - build_r[i - 1]) / build_w2
                    hess -= 1.0 / build_w2
                if i < n_builds - 1:
                    grad -= (build_r[i] - build_r[i + 1]) / build_w2
                    hess -= 1.0 / build_w2

            if abs(hess) > 1e-9:
                build_r[i] -= grad / hess  # Newton step

        # Update opponent ratings (static: no temporal prior beyond ridge)
        for j in range(n_opps):
            if not opp_matchups[j]:
                continue
            grad = 0.0
            hess = 0.0
            for bi, score in opp_matchups[j]:
                diff = build_r[bi] - opp_r[j]
                p = 1 / (1 + np.exp(-diff))
                y = (score + 1.5) / 3.0
                grad -= (y - p)  # opponent's perspective: inverted
                hess -= p * (1 - p)

            # Weak ridge prior toward mean
            if opp_w2 > 0:
                grad -= opp_r[j] / (opp_w2 * 100)
                hess -= 1.0 / (opp_w2 * 100)
            else:
                grad -= opp_r[j] * 0.001
                hess -= 0.001

            if abs(hess) > 1e-9:
                opp_r[j] -= grad / hess

    return build_r, opp_r


# ═══════════════════════════════════════════════════════════════════════════
# LURE Debiasing (Kossen et al. 2021)
# ═══════════════════════════════════════════════════════════════════════════

def lure_debias(
    scores: list[float],
    selection_probs: list[float],
    n_total: int,
    n_selected: int,
) -> float:
    """LURE unbiased estimator for non-random opponent selection."""
    total = 0.0
    for m, (score, prob) in enumerate(zip(scores, selection_probs)):
        prob = max(prob, 0.01)  # floor to avoid extreme weights
        v = 1 + ((n_total - n_selected) / max(1, n_total - m)) * (
            1.0 / (max(1, n_total - m + 1) * prob) - 1)
        total += v * score
    return total / n_selected


# ═══════════════════════════════════════════════════════════════════════════
# Evaluation Strategies
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EvalResult:
    """Result of evaluating all builds under a strategy."""
    fitness: list[float]        # one per build
    total_matchups: int
    pruned_count: int


def _select_random(opponents: list[Opponent], n: int,
                   rng: np.random.Generator) -> list[Opponent]:
    return list(rng.choice(opponents, size=n, replace=False))


def _select_active(
    build_idx: int,
    opponents: list[Opponent],
    completed_matrix: np.ndarray,  # from ALS
    n: int,
    rng: np.random.Generator,
    forced: list[Opponent] | None = None,
    epsilon: float = 0.1,
) -> tuple[list[Opponent], list[float]]:
    """Active testing: select opponents with highest predictive variance.

    Returns (selected_opponents, selection_probabilities).
    """
    forced_set = set(o.name for o in (forced or []))
    forced_list = list(forced or [])
    remaining = [o for o in opponents if o.name not in forced_set]
    n_remaining = n - len(forced_list)

    if n_remaining <= 0 or completed_matrix is None:
        sel = forced_list[:n]
        return sel, [1.0 / len(opponents)] * len(sel)

    # Compute variance proxy from completed matrix rows for this build
    # Use variance of predictions across recent builds as uncertainty
    opp_indices = {o.name: i for i, o in enumerate(opponents)}
    row = completed_matrix[build_idx] if build_idx < completed_matrix.shape[0] else None

    if row is not None:
        # Variance proxy: how much does the prediction differ from the mean?
        mean_score = np.nanmean(row)
        variances = {o.name: abs(row[opp_indices[o.name]] - mean_score) + 0.01
                     for o in remaining}
    else:
        variances = {o.name: 1.0 for o in remaining}

    # ε-greedy: some random, rest by variance
    n_explore = max(1, int(n_remaining * epsilon))
    n_exploit = n_remaining - n_explore

    # Sort by variance (descending)
    sorted_rem = sorted(remaining, key=lambda o: variances[o.name], reverse=True)
    exploit = sorted_rem[:n_exploit]
    explore_pool = sorted_rem[n_exploit:]
    if explore_pool:
        explore = list(rng.choice(explore_pool,
                                  size=min(n_explore, len(explore_pool)),
                                  replace=False))
    else:
        explore = []

    selected = forced_list + exploit + explore

    # Compute selection probabilities (for LURE)
    total_var = sum(variances.values())
    probs = []
    for o in selected:
        if o.name in forced_set:
            probs.append(1.0)  # always selected
        else:
            # Approximate: variance share + epsilon uniform
            var_prob = (1 - epsilon) * variances[o.name] / max(total_var, 1e-9)
            uni_prob = epsilon / max(len(remaining), 1)
            probs.append(min(var_prob + uni_prob, 1.0))

    return selected[:n], probs[:n]


# ── Strategy 1: Baseline ────────────────────────────────────────────────────

def eval_baseline(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10,
) -> EvalResult:
    """Random fixed pool, random ordering, z-score mean."""
    active = _select_random(opponents, active_size, rng)
    rng.shuffle(active)
    opp_stats = {o.name: RunningStats() for o in active}
    historical: list[list[float]] = []
    fitness: list[float] = []
    matchups = 0
    pruned = 0

    for build in builds:
        scores: list[float] = []
        was_pruned = False
        for step, opp in enumerate(active):
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            scores.append(opp_stats[opp.name].z_score(raw))
            matchups += 1
            if should_prune(scores, historical, step):
                was_pruned = True
                pruned += 1
                break
        if not was_pruned:
            historical.append(scores)
        fitness.append(float(np.mean(scores)))

    return EvalResult(fitness, matchups, pruned)


# ── Strategy 2: TWFE ────────────────────────────────────────────────────────

def eval_twfe(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10,
) -> EvalResult:
    """Random fixed pool, but use TWFE α_i as fitness."""
    active = _select_random(opponents, active_size, rng)
    rng.shuffle(active)
    opp_idx = {o.name: i for i, o in enumerate(active)}
    opp_stats = {o.name: RunningStats() for o in active}
    historical: list[list[float]] = []

    # Score matrix: builds × active opponents
    score_mat = np.full((len(builds), active_size), np.nan)
    matchups = 0
    pruned = 0

    for bi, build in enumerate(builds):
        scores: list[float] = []
        was_pruned = False
        for step, opp in enumerate(active):
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            z = opp_stats[opp.name].z_score(raw)
            scores.append(z)
            score_mat[bi, opp_idx[opp.name]] = raw  # store raw for TWFE
            matchups += 1
            if should_prune(scores, historical, step):
                was_pruned = True
                pruned += 1
                break
        if not was_pruned:
            historical.append(scores)

    # TWFE decomposition on raw scores
    alpha, _ = twfe_decompose(score_mat)
    return EvalResult(list(alpha), matchups, pruned)


# ── Strategy 3: Disc ordering + TWFE ────────────────────────────────────────

def eval_disc_twfe(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10, n_anchors: int = 3, burn_in: int = 30,
) -> EvalResult:
    """Discriminative ordering for pruning + TWFE for fitness."""
    active = _select_random(opponents, active_size, rng)
    opp_idx = {o.name: i for i, o in enumerate(active)}
    opp_stats = {o.name: RunningStats() for o in active}

    score_mat = np.full((len(builds), active_size), np.nan)
    burn_in_z: dict[str, list[float]] = {o.name: [] for o in active}
    burn_in_fitness: list[float] = []
    matchups = 0
    pruned = 0

    # Phase 1: Burn-in (fixed random order, no pruning)
    burn_order = list(active)
    rng.shuffle(burn_order)
    for bi in range(min(burn_in, len(builds))):
        build = builds[bi]
        scores = []
        for opp in burn_order:
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            z = opp_stats[opp.name].z_score(raw)
            scores.append(z)
            score_mat[bi, opp_idx[opp.name]] = raw
            burn_in_z[opp.name].append(z)
            matchups += 1
        burn_in_fitness.append(float(np.mean(scores)))

    # Compute discriminative power
    disc: dict[str, float] = {}
    for opp in active:
        zv = burn_in_z[opp.name]
        if len(zv) >= 5:
            c, _ = stats.spearmanr(zv, burn_in_fitness[:len(zv)])
            disc[opp.name] = abs(c) if not np.isnan(c) else 0.0
        else:
            disc[opp.name] = 0.0

    sorted_opps = sorted(active, key=lambda o: disc[o.name], reverse=True)
    anchors = sorted_opps[:n_anchors]
    rotating = sorted_opps[n_anchors:]

    def f_var(opp: Opponent) -> float:
        s = opp_stats[opp.name]
        wr = 1 / (1 + np.exp(-s._mean)) if s._n > 0 else 0.5
        return wr * (1 - wr)
    rotating.sort(key=f_var, reverse=True)
    ordered = anchors + rotating

    # Phase 2: Discriminative ordering with pruning
    ordered_hist: list[list[float]] = []
    for bi in range(burn_in, len(builds)):
        build = builds[bi]
        scores = []
        was_pruned = False
        for step, opp in enumerate(ordered):
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            z = opp_stats[opp.name].z_score(raw)
            scores.append(z)
            score_mat[bi, opp_idx[opp.name]] = raw
            matchups += 1
            if should_prune(scores, ordered_hist, step):
                was_pruned = True
                pruned += 1
                break
        if not was_pruned:
            ordered_hist.append(scores)

    alpha, _ = twfe_decompose(score_mat)
    return EvalResult(list(alpha), matchups, pruned)


# ── Strategy 4: Incumbent overlap + TWFE ────────────────────────────────────

def eval_incumbent_twfe(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10, n_incumbent: int = 5,
) -> EvalResult:
    """SMAC-style: force overlap with incumbent's opponents + TWFE."""
    opp_idx = {o.name: i for i, o in enumerate(opponents)}
    score_mat = np.full((len(builds), len(opponents)), np.nan)
    opp_stats = {o.name: RunningStats() for o in opponents}
    matchups = 0
    pruned = 0

    # First build: random opponents
    incumbent_opps = _select_random(opponents, active_size, rng)
    incumbent_idx = 0
    best_raw_mean = -np.inf

    for bi, build in enumerate(builds):
        if bi == 0:
            active = list(incumbent_opps)
        else:
            # Force n_incumbent from incumbent, fill rest randomly
            forced = list(rng.choice(incumbent_opps,
                                     size=min(n_incumbent, len(incumbent_opps)),
                                     replace=False))
            forced_names = {o.name for o in forced}
            pool = [o for o in opponents if o.name not in forced_names]
            extra = list(rng.choice(pool, size=active_size - len(forced),
                                    replace=False))
            active = forced + extra

        rng.shuffle(active)
        raw_scores = []
        for opp in active:
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            score_mat[bi, opp_idx[opp.name]] = raw
            raw_scores.append(raw)
            matchups += 1

        mean_raw = float(np.mean(raw_scores))
        if mean_raw > best_raw_mean:
            best_raw_mean = mean_raw
            incumbent_opps = active
            incumbent_idx = bi

    alpha, _ = twfe_decompose(score_mat)
    return EvalResult(list(alpha), matchups, pruned)


# ── Strategy 5: ALS matrix completion ───────────────────────────────────────

def eval_als(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10,
) -> EvalResult:
    """Random selection, but score builds on full imputed row from ALS."""
    opp_idx = {o.name: i for i, o in enumerate(opponents)}
    score_mat = np.full((len(builds), len(opponents)), np.nan)
    matchups = 0
    pruned = 0

    for bi, build in enumerate(builds):
        active = _select_random(opponents, active_size, rng)
        rng.shuffle(active)
        for opp in active:
            raw = simulate_matchup(build, opp, rng)
            score_mat[bi, opp_idx[opp.name]] = raw
            matchups += 1

    # Complete the matrix, then score each build as mean of imputed row
    completed = als_complete(score_mat, rank=3)
    fitness = [float(np.mean(completed[i])) for i in range(len(builds))]
    return EvalResult(fitness, matchups, pruned)


# ── Strategy 6: Active testing + LURE ───────────────────────────────────────

def eval_active_lure(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10, burn_in: int = 30,
) -> EvalResult:
    """Adaptive opponent selection with LURE debiasing."""
    opp_idx = {o.name: i for i, o in enumerate(opponents)}
    opp_stats = {o.name: RunningStats() for o in opponents}
    score_mat = np.full((len(builds), len(opponents)), np.nan)
    matchups = 0
    pruned = 0
    fitness: list[float] = []
    completed = None

    for bi, build in enumerate(builds):
        if bi < burn_in:
            # Burn-in: random selection
            active = _select_random(opponents, active_size, rng)
            sel_probs = [active_size / len(opponents)] * active_size
        else:
            # Refit ALS periodically
            if bi == burn_in or bi % 20 == 0:
                completed = als_complete(score_mat, rank=3)
            active, sel_probs = _select_active(
                bi, opponents, completed, active_size, rng, epsilon=0.15)

        rng.shuffle(active)
        raw_scores = []
        z_scores = []
        for opp in active:
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            score_mat[bi, opp_idx[opp.name]] = raw
            raw_scores.append(raw)
            z_scores.append(opp_stats[opp.name].z_score(raw))
            matchups += 1

        # LURE debiased z-score
        if bi >= burn_in and any(p < 0.99 for p in sel_probs):
            fit = lure_debias(z_scores, sel_probs, len(opponents), active_size)
        else:
            fit = float(np.mean(z_scores))
        fitness.append(fit)

    return EvalResult(fitness, matchups, pruned)


# ── Strategy 7: WHR asymmetric ─────────────────────────────────────────────

def eval_whr(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10,
) -> EvalResult:
    """Asymmetric WHR: time-varying builds, static opponents."""
    opp_idx = {o.name: i for i, o in enumerate(opponents)}
    matchup_list: list[tuple[int, int, float]] = []
    matchups = 0
    pruned = 0

    for bi, build in enumerate(builds):
        active = _select_random(opponents, active_size, rng)
        rng.shuffle(active)
        for opp in active:
            raw = simulate_matchup(build, opp, rng)
            matchup_list.append((bi, opp_idx[opp.name], raw))
            matchups += 1

    build_r, opp_r = whr_fit(
        matchup_list, len(builds), len(opponents), build_w2=1.0, opp_w2=0.0)
    return EvalResult(list(build_r), matchups, pruned)


# ── Strategy 8: Trimmed TWFE ───────────────────────────────────────────────

def eval_trimmed_twfe(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10, trim_worst: int = 2,
) -> EvalResult:
    """TWFE + trimmed mean: drop worst trim_worst opponents before scoring."""
    active = _select_random(opponents, active_size, rng)
    rng.shuffle(active)
    opp_idx = {o.name: i for i, o in enumerate(active)}
    opp_stats = {o.name: RunningStats() for o in active}

    score_mat = np.full((len(builds), active_size), np.nan)
    matchups = 0
    pruned = 0

    for bi, build in enumerate(builds):
        for step, opp in enumerate(active):
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            score_mat[bi, opp_idx[opp.name]] = raw
            matchups += 1

    # TWFE decomposition
    alpha_full, beta = twfe_decompose(score_mat)

    # Trimmed: recompute alpha dropping worst-scoring opponents per build
    fitness = []
    for i in range(len(builds)):
        residuals = score_mat[i] - beta  # α_i + ε_ij
        mask = ~np.isnan(residuals)
        valid = residuals[mask]
        if len(valid) > trim_worst:
            # Drop the worst trim_worst
            sorted_r = np.sort(valid)
            trimmed = sorted_r[trim_worst:]  # drop lowest
            fitness.append(float(np.mean(trimmed)))
        else:
            fitness.append(float(np.mean(valid)) if len(valid) > 0 else 0.0)

    return EvalResult(fitness, matchups, pruned)


# ── Strategy 9: Full pipeline ──────────────────────────────────────────────

def eval_full_pipeline(
    builds: list[Build], opponents: list[Opponent], rng: np.random.Generator,
    active_size: int = 10, n_anchors: int = 3, burn_in: int = 30,
    n_incumbent: int = 3, trim_worst: int = 2,
) -> EvalResult:
    """Anchors + incumbent overlap + active selection + TWFE + trimmed."""
    opp_idx = {o.name: i for i, o in enumerate(opponents)}
    opp_stats = {o.name: RunningStats() for o in opponents}
    score_mat = np.full((len(builds), len(opponents)), np.nan)
    matchups = 0
    pruned = 0

    burn_in_z: dict[str, list[float]] = {o.name: [] for o in opponents}
    burn_in_fitness: list[float] = []
    completed = None

    # Initial opponents
    initial_active = _select_random(opponents, active_size, rng)
    incumbent_opps = list(initial_active)
    best_fitness = -np.inf

    # Phase 1: Burn-in
    burn_order = list(initial_active)
    rng.shuffle(burn_order)
    for bi in range(min(burn_in, len(builds))):
        build = builds[bi]
        scores = []
        for opp in burn_order:
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            z = opp_stats[opp.name].z_score(raw)
            scores.append(z)
            score_mat[bi, opp_idx[opp.name]] = raw
            burn_in_z[opp.name].append(z)
            matchups += 1
        f = float(np.mean(scores))
        burn_in_fitness.append(f)
        if f > best_fitness:
            best_fitness = f
            incumbent_opps = list(burn_order)

    # Compute discriminative power for anchors
    disc: dict[str, float] = {}
    for opp in initial_active:
        zv = burn_in_z[opp.name]
        if len(zv) >= 5:
            c, _ = stats.spearmanr(zv, burn_in_fitness[:len(zv)])
            disc[opp.name] = abs(c) if not np.isnan(c) else 0.0
        else:
            disc[opp.name] = 0.0
    anchors = sorted(initial_active, key=lambda o: disc[o.name], reverse=True)[:n_anchors]
    anchor_names = {a.name for a in anchors}

    # Phase 2: Adaptive selection
    ordered_hist: list[list[float]] = []
    for bi in range(burn_in, len(builds)):
        build = builds[bi]

        # Refit ALS periodically
        if bi == burn_in or bi % 20 == 0:
            completed = als_complete(score_mat, rank=3)

        # Force anchors + some incumbent opponents
        inc_non_anchor = [o for o in incumbent_opps if o.name not in anchor_names]
        forced = list(anchors)
        if inc_non_anchor:
            forced += list(rng.choice(inc_non_anchor,
                                      size=min(n_incumbent, len(inc_non_anchor)),
                                      replace=False))

        active, sel_probs = _select_active(
            bi, opponents, completed, active_size, rng,
            forced=forced, epsilon=0.1)

        # Evaluate with pruning (anchors first)
        anchor_first = [o for o in active if o.name in anchor_names]
        rest = [o for o in active if o.name not in anchor_names]
        ordered_active = anchor_first + rest

        scores = []
        was_pruned = False
        for step, opp in enumerate(ordered_active):
            raw = simulate_matchup(build, opp, rng)
            opp_stats[opp.name].update(raw)
            z = opp_stats[opp.name].z_score(raw)
            scores.append(z)
            score_mat[bi, opp_idx[opp.name]] = raw
            matchups += 1
            if should_prune(scores, ordered_hist, step):
                was_pruned = True
                pruned += 1
                break

        if not was_pruned:
            ordered_hist.append(scores)

    # Final scoring: TWFE + trimmed mean
    alpha, beta_full = twfe_decompose(score_mat)

    # Trimmed: remove worst-scoring opponents per build
    fitness = []
    for i in range(len(builds)):
        observed_mask = ~np.isnan(score_mat[i])
        if observed_mask.sum() == 0:
            fitness.append(alpha[i])
            continue
        residuals = score_mat[i, observed_mask] - beta_full[observed_mask]
        if len(residuals) > trim_worst:
            sorted_r = np.sort(residuals)
            trimmed = sorted_r[trim_worst:]
            fitness.append(float(np.mean(trimmed)))
        else:
            fitness.append(float(np.mean(residuals)))

    # Update incumbent
    for i, f in enumerate(fitness):
        if f > best_fitness:
            best_fitness = f

    return EvalResult(fitness, matchups, pruned)


# ═══════════════════════════════════════════════════════════════════════════
# Metrics & Experiment Runner
# ═══════════════════════════════════════════════════════════════════════════

def rank_correlation(fitness: list[float], builds: list[Build]) -> float:
    rho, _ = stats.spearmanr(fitness, [b.quality for b in builds])
    return rho if not np.isnan(rho) else 0.0


def top_k_overlap(fitness: list[float], builds: list[Build], k: int = 10) -> float:
    true_top = set(np.argsort([b.quality for b in builds])[-k:])
    est_top = set(np.argsort(fitness)[-k:])
    return len(true_top & est_top) / k


STRATEGIES: dict[str, callable] = {
    "baseline":       eval_baseline,
    "twfe":           eval_twfe,
    "disc_twfe":      eval_disc_twfe,
    "incumb_twfe":    eval_incumbent_twfe,
    "als_impute":     eval_als,
    "active_lure":    eval_active_lure,
    "whr":            eval_whr,
    "trimmed_twfe":   eval_trimmed_twfe,
    "full_pipeline":  eval_full_pipeline,
}

LABELS = {
    "baseline":       "Baseline",
    "twfe":           "TWFE",
    "disc_twfe":      "Disc+TWFE",
    "incumb_twfe":    "Incumb+TWFE",
    "als_impute":     "ALS impute",
    "active_lure":    "Active+LURE",
    "whr":            "WHR",
    "trimmed_twfe":   "Trimmed TWFE",
    "full_pipeline":  "Full pipeline",
}

COLORS = {
    "baseline":       "#95a5a6",
    "twfe":           "#2ecc71",
    "disc_twfe":      "#3498db",
    "incumb_twfe":    "#9b59b6",
    "als_impute":     "#e67e22",
    "active_lure":    "#1abc9c",
    "whr":            "#e74c3c",
    "trimmed_twfe":   "#f1c40f",
    "full_pipeline":  "#2c3e50",
}


def run_experiment(
    n_builds: int = 200, n_opponents: int = 54, n_repeats: int = 30,
    improving: bool = True,
) -> pd.DataFrame:
    records = []
    for rep in range(n_repeats):
        seed = 42 + rep * 1000
        world_rng = np.random.default_rng(seed)
        opponents = generate_opponents(n_opponents, world_rng)
        builds = generate_builds(n_builds, world_rng, improving)

        for name, fn in STRATEGIES.items():
            sim_rng = np.random.default_rng(seed + hash(name) % 10000)
            result = fn(builds, opponents, sim_rng)
            assert len(result.fitness) == n_builds, (
                f"{name}: {len(result.fitness)} != {n_builds}")

            records.append({
                "strategy": name, "repeat": rep,
                "rank_corr": rank_correlation(result.fitness, builds),
                "top10_overlap": top_k_overlap(result.fitness, builds, k=10),
                "top20_overlap": top_k_overlap(result.fitness, builds, k=20),
                "total_matchups": result.total_matchups,
                "pruned_count": result.pruned_count,
                "prune_rate": result.pruned_count / n_builds,
                "matchups_saved": n_builds * 10 - result.total_matchups,
            })

        if (rep + 1) % 5 == 0:
            print(f"  ... {rep + 1}/{n_repeats} repeats done")

    return pd.DataFrame(records)


# ═══════════════════════════════════════════════════════════════════════════
# Plotting
# ═══════════════════════════════════════════════════════════════════════════

def plot_main_comparison(df: pd.DataFrame, save: str = "") -> None:
    strategies = list(df["strategy"].unique())
    fig, axes = plt.subplots(2, 2, figsize=(18, 12))

    for ax, metric, title, ylabel in [
        (axes[0, 0], "rank_corr",
         "A. Rank Correlation with True Build Quality", "Spearman ρ"),
        (axes[0, 1], "top10_overlap",
         "B. Top-10 Build Identification", "Fraction of true top-10 found"),
        (axes[1, 0], "top20_overlap",
         "C. Top-20 Build Identification", "Fraction of true top-20 found"),
        (axes[1, 1], "matchups_saved",
         "D. Budget Savings (matchups saved vs 10/build)", "Matchups saved"),
    ]:
        data = [df[df["strategy"] == s][metric] for s in strategies]
        bp = ax.boxplot(data, tick_labels=[LABELS[s] for s in strategies],
                        patch_artist=True, widths=0.6)
        for patch, s in zip(bp["boxes"], strategies):
            patch.set_facecolor(COLORS[s])
            patch.set_alpha(0.7)
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontweight="bold")
        ax.tick_params(axis="x", rotation=30, labelsize=8)

        # Add baseline reference line
        baseline_med = df[df["strategy"] == "baseline"][metric].median()
        ax.axhline(y=baseline_med, color="#95a5a6", linestyle="--",
                    alpha=0.5, linewidth=1)

    plt.suptitle(
        "Phase 5B: Research-Derived Approaches — Synthetic Simulation\n"
        f"(200 builds × 54 opponents, {len(df) // len(strategies)} repeats, "
        "improving build quality)",
        fontsize=13, fontweight="bold", y=1.02,
    )
    plt.tight_layout()
    if save:
        plt.savefig(f"{save}main_comparison.png", dpi=150, bbox_inches="tight")
    plt.close()


def plot_significance_heatmap(df: pd.DataFrame, save: str = "") -> None:
    """Pairwise significance matrix for rank correlation."""
    strategies = list(df["strategy"].unique())
    n = len(strategies)
    pval_matrix = np.ones((n, n))
    effect_matrix = np.zeros((n, n))

    for i, si in enumerate(strategies):
        for j, sj in enumerate(strategies):
            if i == j:
                continue
            vi = df[df["strategy"] == si]["rank_corr"].values
            vj = df[df["strategy"] == sj]["rank_corr"].values
            diff = vi - vj
            try:
                _, p = stats.wilcoxon(diff)
                pval_matrix[i, j] = p
                effect_matrix[i, j] = np.mean(diff)
            except ValueError:
                pass

    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(effect_matrix, cmap="RdBu", vmin=-0.15, vmax=0.15)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels([LABELS[s] for s in strategies], rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels([LABELS[s] for s in strategies], fontsize=8)

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            sig = "***" if pval_matrix[i, j] < 0.001 else (
                  "**" if pval_matrix[i, j] < 0.01 else (
                  "*" if pval_matrix[i, j] < 0.05 else ""))
            ax.text(j, i, f"{effect_matrix[i,j]:+.3f}\n{sig}",
                    ha="center", va="center", fontsize=7,
                    color="white" if abs(effect_matrix[i, j]) > 0.08 else "black")

    plt.colorbar(im, label="Δ rank correlation (row − column)")
    ax.set_title("Pairwise Significance: Rank Correlation Differences\n"
                 "(* p<.05, ** p<.01, *** p<.001, paired Wilcoxon)",
                 fontweight="bold")
    plt.tight_layout()
    if save:
        plt.savefig(f"{save}significance_heatmap.png", dpi=150, bbox_inches="tight")
    plt.close()


def plot_whr_deconfounding(save: str = "") -> None:
    """Show WHR deconfounding: Elo vs WHR opponent ratings vs true difficulty."""
    seed = 42
    rng = np.random.default_rng(seed)
    opponents = generate_opponents(54, rng)
    builds = generate_builds(200, rng, improving=True)

    # Select diverse subset
    easy = [o for o in opponents if o.difficulty < -0.5][:5]
    med = [o for o in opponents if -0.5 <= o.difficulty <= 1.5][:5]
    hard = [o for o in opponents if o.difficulty > 1.5][:5]
    active = easy + med + hard

    # Simulate matchups
    elo = EloTracker()
    sim_rng = np.random.default_rng(seed + 50)
    matchup_list = []
    opp_idx = {o.name: i for i, o in enumerate(active)}

    for bi, build in enumerate(builds):
        for opp in active:
            raw = simulate_matchup(build, opp, sim_rng)
            elo.update(opp.name, build_won=(raw > 0))
            matchup_list.append((bi, opp_idx[opp.name], raw))

    # WHR fit
    build_r, opp_r = whr_fit(matchup_list, len(builds), len(active),
                              build_w2=1.0, opp_w2=0.0)

    true_diff = [o.difficulty for o in active]
    elo_ratings = [elo.get(o.name) for o in active]
    whr_ratings = [opp_r[opp_idx[o.name]] for o in active]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Elo vs true
    ax1.scatter(true_diff, elo_ratings, alpha=0.7, s=50, c="steelblue")
    rho_elo, p_elo = stats.spearmanr(true_diff, elo_ratings)
    ax1.set_xlabel("True opponent difficulty")
    ax1.set_ylabel("Elo rating")
    ax1.set_title(f"Standard Elo (confounded)\nρ = {rho_elo:.3f}, p = {p_elo:.2e}")
    z = np.polyfit(true_diff, elo_ratings, 1)
    x_line = np.linspace(min(true_diff), max(true_diff), 100)
    ax1.plot(x_line, np.polyval(z, x_line), "r--", alpha=0.5)

    # WHR vs true
    ax2.scatter(true_diff, whr_ratings, alpha=0.7, s=50, c="#e74c3c")
    rho_whr, p_whr = stats.spearmanr(true_diff, whr_ratings)
    ax2.set_xlabel("True opponent difficulty")
    ax2.set_ylabel("WHR opponent rating")
    ax2.set_title(f"WHR asymmetric (deconfounded)\nρ = {rho_whr:.3f}, p = {p_whr:.2e}")
    z2 = np.polyfit(true_diff, whr_ratings, 1)
    ax2.plot(x_line, np.polyval(z2, x_line), "r--", alpha=0.5)

    plt.suptitle("Deconfounding: Standard Elo vs WHR with Improving Builds",
                 fontweight="bold")
    plt.tight_layout()
    if save:
        plt.savefig(f"{save}whr_deconfounding.png", dpi=150, bbox_inches="tight")
    plt.close()
    return rho_elo, rho_whr


def print_summary(df: pd.DataFrame) -> None:
    strategies = list(df["strategy"].unique())
    metrics = [
        ("rank_corr", "Rank corr (ρ)"),
        ("top10_overlap", "Top-10 accuracy"),
        ("top20_overlap", "Top-20 accuracy"),
        ("prune_rate", "Prune rate"),
    ]

    print("\n" + "=" * 120)
    print(f"SUMMARY: Mean ± Std across {df['repeat'].nunique()} repeats "
          "(200 builds, 54 opponents, improving quality)")
    print("=" * 120)

    header = f"{'Metric':<20}"
    for s in strategies:
        header += f"{LABELS[s]:>13}"
    print(header)
    print("-" * 120)

    for key, label in metrics:
        row = f"{label:<20}"
        for s in strategies:
            vals = df[df["strategy"] == s][key]
            row += f"  {vals.mean():.3f}±{vals.std():.3f}"
        print(row)
    print("=" * 120)

    # Significance vs baseline
    print("\nPaired Wilcoxon tests vs Baseline (rank correlation):")
    baseline = df[df["strategy"] == "baseline"]["rank_corr"].values
    for s in strategies:
        if s == "baseline":
            continue
        other = df[df["strategy"] == s]["rank_corr"].values
        diff = other - baseline
        try:
            _, p = stats.wilcoxon(diff)
            d = "↑" if np.mean(diff) > 0 else "↓"
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
            print(f"  {LABELS[s]:>15}: Δ={np.mean(diff):+.4f}  p={p:.4f}  {d} {sig}")
        except ValueError:
            print(f"  {LABELS[s]:>15}: no variation")


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    save = "experiments/phase5b-curriculum-simulation/"

    print("=" * 70)
    print("Phase 5B: Full Research Comparison — Synthetic Simulation")
    print("=" * 70)
    print()
    print("Testing 9 strategies from 6 literature surveys:")
    for i, (k, v) in enumerate(LABELS.items(), 1):
        print(f"  {i}. {v}")
    print()
    print("Running 30 repeats × 9 strategies × 200 builds...")
    print()

    df = run_experiment(n_builds=200, n_opponents=54, n_repeats=30, improving=True)

    print_summary(df)

    print("\nGenerating plots...")
    plot_main_comparison(df, save)
    print("  main_comparison.png")
    plot_significance_heatmap(df, save)
    print("  significance_heatmap.png")
    rho_elo, rho_whr = plot_whr_deconfounding(save)
    print(f"  whr_deconfounding.png  (Elo ρ={rho_elo:.3f}, WHR ρ={rho_whr:.3f})")

    print(f"\nAll plots saved to {save}")
