"""Post-hoc top-K candidate selection from completed evaluation logs.

Reads `evaluation_log.jsonl` rows produced by the optimizer and ranks builds
using multiple estimators. Designed to consume the per-cell logs produced
post-task #90 / migrated by `scripts/migrate_wave1_eval_logs.py`.

Estimators (rigor / tractability rationale: docs/reports/2026-05-10-posthoc-ranker-research.md):

  - raw_mean         : mean(hp_differential) per build. Spec 30 baseline.
  - twfe             : α_i from `score_ij = α_i + β_j + ε_ij` with opponents
                       pooled across all input studies. Removes opponent
                       confounding when builds face non-overlapping subsets.
  - twfe_eb          : TWFE α̂ + heteroscedastic EB shrinkage toward the
                       global mean (Stein-style; phase5a + phase5d-without-X).
  - bradley_terry    : per-match logistic skill: P(build beats opp) =
                       σ(α_i − β_j). MAP via L-BFGS with ridge prior; per-arm
                       Fisher-information variance.

The set of "completed" rows passed to a ranker is the caller's choice
(typically `not pruned and not cache_hit and not invalid_spec`). All
estimators operate on the same record set so top-K agreement is meaningful.

Pooling: when records span multiple studies, opponent identities are merged
across studies (sharpens β estimation); build identities are not assumed to
collide (they typically don't in TPE searches). Cross-study build-effect
shrinkage uses the pooled population.
"""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

import numpy as np
from scipy.optimize import minimize

from .deconfounding import eb_shrinkage, twfe_decompose
from .models import EBShrinkageConfig, TWFEConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- records ---


@dataclass(frozen=True)
class _BuildId:
    """Canonical, stable hash of a build's identity across studies."""
    hull_id: str
    weapons: tuple[tuple[str, str | None], ...]   # sorted (slot, weapon)
    hullmods: tuple[str, ...]                     # sorted
    flux_vents: int
    flux_capacitors: int

    @property
    def short(self) -> str:
        h = hashlib.sha256(repr(self).encode()).hexdigest()
        return h[:12]


@dataclass(frozen=True)
class TrialRecord:
    """One completed trial's matchup row, normalised for ranker input."""
    study: str           # e.g. "wave1-c2/seed1"
    trial_number: int
    build_id: _BuildId
    raw_build: dict      # original JSONL "build" dict (for human inspection)
    matches: tuple[tuple[str, float, str], ...]   # (opponent, hp_diff, winner)


def load_records(
    jsonl_paths: Sequence[Path],
    *,
    require_field: str = "opponent_results",
) -> list[TrialRecord]:
    """Load completed (non-pruned, non-cache-hit, non-invalid-spec) trials.

    Skips rows missing `opponent_results` or with empty matches. Caller is
    responsible for the path glob.
    """
    out: list[TrialRecord] = []
    for fp in jsonl_paths:
        # study label = parent-dir name (e.g. wave1-c2/hammerhead__early__tpe__seed1)
        # we shorten to "<cell>/seed<N>"
        cell = fp.parent.parent.name.removeprefix("wave1-")
        seed_part = fp.parent.name.rsplit("__seed", 1)
        seed = seed_part[1] if len(seed_part) == 2 else "?"
        study = f"{cell}/seed{seed}"

        with fp.open() as f:
            for lineno, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{fp}:{lineno}: malformed JSON — {exc}. "
                        f"This is a data-integrity signal; investigate "
                        f"the producer (optimizer or migrator) before "
                        f"trusting the ranking."
                    ) from exc
                if d.get("pruned") or d.get("cache_hit") or d.get("invalid_spec"):
                    continue
                results = d.get(require_field) or []
                if not results:
                    continue
                try:
                    b = d["build"]
                    bid = _BuildId(
                        hull_id=b["hull_id"],
                        weapons=tuple(sorted(b["weapon_assignments"].items())),
                        hullmods=tuple(sorted(b["hullmods"])),
                        flux_vents=int(b["flux_vents"]),
                        flux_capacitors=int(b["flux_capacitors"]),
                    )
                    matches = tuple(
                        (r["opponent"], float(r["hp_differential"]), r["winner"])
                        for r in results
                    )
                    trial_number = int(d["trial_number"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{fp}:{lineno}: missing/malformed required field "
                        f"({type(exc).__name__}: {exc}). Expected schema: "
                        f"`build.{{hull_id, weapon_assignments, hullmods, "
                        f"flux_vents, flux_capacitors}}`, "
                        f"`opponent_results[].{{opponent, hp_differential, "
                        f"winner}}`, `trial_number`."
                    ) from exc
                out.append(TrialRecord(
                    study=study,
                    trial_number=trial_number,
                    build_id=bid,
                    raw_build=b,
                    matches=matches,
                ))
    return out


# -------------------------------------------------------- ranking outputs ---


@dataclass(frozen=True)
class RankedBuild:
    """One row of a ranking output: build identity + estimator's score."""
    build_id: _BuildId
    score: float           # estimator point estimate (raw mean, α̂, α̂_EB, or BT-skill)
    sigma: float           # 1-sigma std error of `score` (NaN if unavailable)
    n_matches: int         # total opponent matchups for this build (across studies)
    studies: tuple[str, ...]  # studies in which this build appeared
    raw_build: dict        # untyped build dict (for human inspection)


# --------------------------------------------------------------- raw mean ---


def rank_raw_mean(records: Sequence[TrialRecord], k: int) -> list[RankedBuild]:
    """Rank by mean per-match hp_differential (≈ spec 30's intermediate-mean).

    Pools across studies if a build_id appears in multiple records.
    """
    by_build: dict[_BuildId, list[float]] = {}
    studies: dict[_BuildId, set[str]] = {}
    samples: dict[_BuildId, dict] = {}
    for rec in records:
        by_build.setdefault(rec.build_id, []).extend(m[1] for m in rec.matches)
        studies.setdefault(rec.build_id, set()).add(rec.study)
        samples.setdefault(rec.build_id, rec.raw_build)
    ranked = []
    for bid, scores in by_build.items():
        arr = np.asarray(scores, dtype=float)
        n = len(arr)
        mean = float(arr.mean())
        sigma = float(arr.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
        ranked.append(RankedBuild(
            build_id=bid, score=mean, sigma=sigma,
            n_matches=n, studies=tuple(sorted(studies[bid])),
            raw_build=samples[bid],
        ))
    ranked.sort(key=lambda r: -r.score)
    return ranked[:k]


# ----------------------------------------------------------- TWFE / EB-α̂ ---


def _build_score_matrix(
    records: Sequence[TrialRecord],
) -> tuple[np.ndarray, list[_BuildId], list[str]]:
    """Materialise (n_builds × n_opponents) matrix; NaN where unobserved.

    Multiple records of the same build pool their matches; if the same
    (build, opponent) cell is observed in more than one record, the mean
    of observations fills that cell.
    """
    builds: dict[_BuildId, int] = {}
    opps: dict[str, int] = {}
    cells: dict[tuple[int, int], list[float]] = {}
    for rec in records:
        if rec.build_id not in builds:
            builds[rec.build_id] = len(builds)
        bi = builds[rec.build_id]
        for opp, hp_diff, _ in rec.matches:
            if opp not in opps:
                opps[opp] = len(opps)
            cells.setdefault((bi, opps[opp]), []).append(hp_diff)

    n_b, n_o = len(builds), len(opps)
    matrix = np.full((n_b, n_o), np.nan)
    for (i, j), vs in cells.items():
        matrix[i, j] = float(np.mean(vs))

    inv_builds = sorted(builds, key=builds.get)
    inv_opps = sorted(opps, key=opps.get)
    return matrix, inv_builds, inv_opps


def rank_twfe(
    records: Sequence[TrialRecord],
    k: int,
    config: TWFEConfig | None = None,
) -> list[RankedBuild]:
    """TWFE-only: α̂_i without shrinkage. Useful as the EB-off ablation."""
    cfg = config or TWFEConfig()
    matrix, inv_builds, _ = _build_score_matrix(records)
    alpha, beta = twfe_decompose(matrix, n_iters=cfg.n_iters, ridge=cfg.ridge)

    # Per-build n_i and pooled σ̂_ε² for std-error reporting — same formula
    # as `deconfounding.ScoreMatrix._ensure_decomposed`.
    observed = ~np.isnan(matrix)
    pred = alpha[:, None] + beta[None, :]
    diff = np.where(observed, matrix - pred, 0.0)
    n_obs = int(observed.sum())
    n_b, n_o = matrix.shape
    denom = max(n_obs - (n_b + n_o - 1), 1)
    sigma_eps_sq = float(np.sum(diff * diff)) / denom
    n_per_build = observed.sum(axis=1)

    ranked = _alpha_to_ranked(
        alpha, sigma_eps_sq, n_per_build, inv_builds, records,
    )
    ranked.sort(key=lambda r: -r.score)
    return ranked[:k]


def rank_twfe_eb(
    records: Sequence[TrialRecord],
    k: int,
    twfe_config: TWFEConfig | None = None,
    eb_config: EBShrinkageConfig | None = None,
) -> list[RankedBuild]:
    """TWFE α̂ + heteroscedastic EB shrinkage toward global mean (no covariates).

    Spec 30 ranks by raw mean for pragmatic reasons; this is the principled
    upgrade path: phase5a + phase5d-without-X. Calls `eb_shrinkage` from
    `deconfounding` with X = column of zeros so the regression prior degenerates
    to the grand mean (γ̂ = [μ̂_α]).
    """
    tcfg = twfe_config or TWFEConfig()
    ecfg = eb_config or EBShrinkageConfig()

    matrix, inv_builds, _ = _build_score_matrix(records)
    alpha, beta = twfe_decompose(matrix, n_iters=tcfg.n_iters, ridge=tcfg.ridge)

    # Pooled residual MSE σ̂_ε² (same formula as ScoreMatrix._ensure_decomposed).
    observed = ~np.isnan(matrix)
    pred = alpha[:, None] + beta[None, :]
    diff = np.where(observed, matrix - pred, 0.0)
    n_obs = int(observed.sum())
    n_b, n_o = matrix.shape
    denom = max(n_obs - (n_b + n_o - 1), 1)
    sigma_eps_sq = float(np.sum(diff * diff)) / denom
    n_per_build = observed.sum(axis=1)
    sigma_sq_per_build = sigma_eps_sq / np.maximum(n_per_build, 1)

    # No covariates → degenerate X (single zero column gets dropped → γ̂ = [μ̂_α]).
    # The "zero-std X columns" warning from `eb_shrinkage` is expected here
    # (we *want* the regression prior to collapse to the grand mean); silence
    # it so it doesn't drown real warnings in honest-eval logs. The string
    # match couples to `deconfounding.eb_shrinkage`'s warning text — if that
    # message changes the filter silently stops working, but the failure
    # mode is "warning re-appears", not "wrong answer".
    X_zero = np.zeros((n_b, 1))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="eb_shrinkage dropped zero-std X columns",
        )
        alpha_eb, _gamma, tau2, _kept = eb_shrinkage(
            alpha=alpha, sigma_sq=sigma_sq_per_build, X=X_zero, config=ecfg,
        )
    logger.debug("rank_twfe_eb: tau2=%.4f sigma_eps_sq=%.4f n_b=%d n_o=%d",
                 tau2, sigma_eps_sq, n_b, n_o)

    ranked = _alpha_to_ranked(
        alpha_eb, sigma_eps_sq, n_per_build, inv_builds, records,
    )
    ranked.sort(key=lambda r: -r.score)
    return ranked[:k]


def _alpha_to_ranked(
    alpha: np.ndarray,
    sigma_eps_sq: float,
    n_per_build: np.ndarray,
    inv_builds: list[_BuildId],
    records: Sequence[TrialRecord],
) -> list[RankedBuild]:
    """Wrap a per-build α vector + n_i + σ̂_ε² into RankedBuild rows."""
    by_build_studies: dict[_BuildId, set[str]] = {}
    by_build_raw: dict[_BuildId, dict] = {}
    for rec in records:
        by_build_studies.setdefault(rec.build_id, set()).add(rec.study)
        by_build_raw.setdefault(rec.build_id, rec.raw_build)

    ranked: list[RankedBuild] = []
    for i, bid in enumerate(inv_builds):
        n = int(n_per_build[i])
        sigma_i = float(np.sqrt(sigma_eps_sq / max(n, 1)))
        ranked.append(RankedBuild(
            build_id=bid,
            score=float(alpha[i]),
            sigma=sigma_i,
            n_matches=n,
            studies=tuple(sorted(by_build_studies.get(bid, set()))),
            raw_build=by_build_raw.get(bid, {}),
        ))
    return ranked


# ---------------------------------------------------------- Bradley–Terry ---


@dataclass(frozen=True)
class BradleyTerryConfig:
    """MAP-BT hyperparameters.

    `ridge` is the precision of an isotropic Gaussian prior on (α, β)
    (= 1/σ² of N(0, σ²)). 0.1 chosen so a build that beats every opponent
    saturates around α ≈ 3.5 (logit), keeping per-arm posteriors well-
    behaved without a 60-match all-win run blowing α to infinity. The
    test `test_timeout_weighted_as_draw` overrides to 0.5 (strong prior)
    to stress-test the all-TIMEOUT case.
    """
    ridge: float = 0.1
    timeout_weight: float = 0.5       # weight on TIMEOUT matches (treated as draw)
    max_iters: int = 200
    gtol: float = 1e-6                # L-BFGS-B convergence tolerance


def rank_bradley_terry(
    records: Sequence[TrialRecord],
    k: int,
    config: BradleyTerryConfig | None = None,
) -> list[RankedBuild]:
    """Bradley–Terry over all matches: P(build beats opp) = σ(α_i − β_j).

    MAP estimation via L-BFGS with a Gaussian ridge prior on (α, β) for
    identifiability. Per-build std error from the Fisher-information
    diagonal. TIMEOUTs become weighted half-wins (weight = config.timeout_weight).
    """
    cfg = config or BradleyTerryConfig()
    builds: dict[_BuildId, int] = {}
    opps: dict[str, int] = {}
    bidx: list[int] = []
    oidx: list[int] = []
    y: list[float] = []   # 1.0 = build won, 0.0 = build lost, 0.5 = draw
    weight: list[float] = []
    by_build_studies: dict[_BuildId, set[str]] = {}
    by_build_raw: dict[_BuildId, dict] = {}

    for rec in records:
        if rec.build_id not in builds:
            builds[rec.build_id] = len(builds)
        bi = builds[rec.build_id]
        by_build_studies.setdefault(rec.build_id, set()).add(rec.study)
        by_build_raw.setdefault(rec.build_id, rec.raw_build)
        for opp, _hp, winner in rec.matches:
            if opp not in opps:
                opps[opp] = len(opps)
            bidx.append(bi)
            oidx.append(opps[opp])
            if winner == "PLAYER":
                y.append(1.0)
                weight.append(1.0)
            elif winner == "ENEMY":
                y.append(0.0)
                weight.append(1.0)
            else:  # TIMEOUT or unknown
                y.append(0.5)
                weight.append(cfg.timeout_weight)

    n_b, n_o = len(builds), len(opps)
    bidx_a = np.asarray(bidx, dtype=np.int64)
    oidx_a = np.asarray(oidx, dtype=np.int64)
    y_a = np.asarray(y, dtype=float)
    w_a = np.asarray(weight, dtype=float)

    def nll_grad(theta: np.ndarray) -> tuple[float, np.ndarray]:
        alpha = theta[:n_b]
        beta = theta[n_b:]
        z = alpha[bidx_a] - beta[oidx_a]
        # Stable: log(1 + exp(z)) = logaddexp(0, z); CE = log1pexp - y*z.
        log1pexp = np.logaddexp(0.0, z)
        ce = log1pexp - y_a * z
        nll = float(np.sum(w_a * ce))
        sig = 1.0 / (1.0 + np.exp(-z))
        resid = w_a * (sig - y_a)
        d_alpha = np.bincount(bidx_a, weights=resid, minlength=n_b)
        d_beta = -np.bincount(oidx_a, weights=resid, minlength=n_o)
        # Ridge prior on all parameters.
        nll += 0.5 * cfg.ridge * float(np.dot(theta, theta))
        d_alpha = d_alpha + cfg.ridge * alpha
        d_beta = d_beta + cfg.ridge * beta
        return nll, np.concatenate([d_alpha, d_beta])

    theta0 = np.zeros(n_b + n_o)
    res = minimize(
        nll_grad, theta0, jac=True, method="L-BFGS-B",
        options={"maxiter": cfg.max_iters, "gtol": cfg.gtol},
    )
    alpha = res.x[:n_b]
    beta = res.x[n_b:]

    # Fisher information diagonal: I_α_i = Σ_matches w · σ(z) · (1−σ(z))
    z = alpha[bidx_a] - beta[oidx_a]
    sig = 1.0 / (1.0 + np.exp(-z))
    info = w_a * sig * (1.0 - sig)
    fisher_alpha = np.bincount(bidx_a, weights=info, minlength=n_b) + cfg.ridge
    var_alpha = 1.0 / fisher_alpha

    # n per build = number of weighted matches (non-zero weight)
    n_per = np.bincount(bidx_a, weights=(w_a > 0).astype(float), minlength=n_b).astype(int)

    inv_builds = sorted(builds, key=builds.get)
    ranked = []
    for i, bid in enumerate(inv_builds):
        ranked.append(RankedBuild(
            build_id=bid,
            score=float(alpha[i]),
            sigma=float(np.sqrt(var_alpha[i])),
            n_matches=int(n_per[i]),
            studies=tuple(sorted(by_build_studies.get(bid, set()))),
            raw_build=by_build_raw.get(bid, {}),
        ))
    ranked.sort(key=lambda r: -r.score)
    return ranked[:k]


# ----------------------------------------------------- comparison helpers ---


def topk_overlap(a: Sequence[RankedBuild], b: Sequence[RankedBuild]) -> int:
    """Number of build_ids in common between two ranked lists."""
    sa = {r.build_id for r in a}
    sb = {r.build_id for r in b}
    return len(sa & sb)


def spearman_rho(a: Sequence[RankedBuild], b: Sequence[RankedBuild]) -> float:
    """Spearman ρ of the ranks of build_ids that appear in both lists.

    NaN if fewer than 2 builds overlap.
    """
    common = {r.build_id for r in a} & {r.build_id for r in b}
    if len(common) < 2:
        return float("nan")
    rank_a = {r.build_id: i for i, r in enumerate(a) if r.build_id in common}
    rank_b = {r.build_id: i for i, r in enumerate(b) if r.build_id in common}
    xs = np.asarray([rank_a[bid] for bid in common], dtype=float)
    ys = np.asarray([rank_b[bid] for bid in common], dtype=float)
    if xs.std() == 0 or ys.std() == 0:
        return float("nan")
    return float(np.corrcoef(xs, ys)[0, 1])
