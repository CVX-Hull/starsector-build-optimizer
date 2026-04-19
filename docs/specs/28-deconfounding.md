# Deconfounding Specification

Two-stage build quality estimation:

1. **A1 — Two-Way Fixed Effects (TWFE) decomposition** of the sparse build × opponent score matrix into additive build-quality and opponent-difficulty components.
2. **A2′ — Empirical-Bayes (EB) shrinkage** of the TWFE estimate α̂_i toward a regression prior on pre-matchup covariates, followed by Lin-Louis-Shen triple-goal rank correction. Replaces the scalar control variate shipped in Phase 5A.

All pure-math functions and the `ScoreMatrix` accumulator live in `src/starsector_optimizer/deconfounding.py`. Config dataclasses (`TWFEConfig`, `EBShrinkageConfig`) live in `src/starsector_optimizer/models.py`.

## Problem

When builds are evaluated against different opponent subsets, raw fitness scores are incomparable: a high score could mean "good build" or "easy opponents." When build quality improves over time (optimizer convergence), all opponents appear easier — opponent difficulty estimates are confounded with the build improvement trend.

TWFE solves both problems by decomposing the score matrix into additive build quality (α_i) and opponent difficulty (β_j) components. This decomposition is the consensus solution across six independent fields: IRT, game rating systems, causal inference, sports analytics, bandits/active learning, and coevolutionary algorithms. See `docs/reference/phase5a-deconfounding-theory.md` for the full literature synthesis.

## Overview

The score for build i against opponent j is modeled as:

```
Y_ij = α_i + β_j + ε_ij
```

Where α_i is build quality (what we want), β_j is opponent difficulty (nuisance), and ε_ij is residual noise. Estimation via alternating projection on the sparse observation matrix recovers schedule-adjusted build quality α_i that is comparable across different opponent subsets.

Synthetic simulation showed TWFE increases rank correlation with true build quality from ρ = 0.525 (z-score mean baseline) to ρ = 0.775 (+48%, p < 0.001).

## Classes

### `TWFEConfig`

Frozen dataclass in `models.py` configuring TWFE decomposition and opponent selection.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ridge` | `float` | `0.01` | Regularization strength for alternating projection (prevents divergence with sparse data) |
| `n_iters` | `int` | `20` | Alternating projection iterations (converges well within 20 for matrices up to 1000×100) |
| `trim_worst` | `int` | `2` | Drop this many worst residuals per build before averaging α_i (RPS robustness) |
| `n_incumbent_overlap` | `int` | `5` | Force this many opponents from incumbent's set into each new build's evaluation (guarantees TWFE comparability via shared observations) |
| `n_anchors` | `int` | `3` | Lock this many high-discrimination opponents at the front of the opponent order (early pruning signal) |
| `anchor_burn_in` | `int` | `30` | Builds to evaluate before computing discriminative power and locking anchors |
| `min_disc_samples` | `int` | `5` | Minimum observations per opponent required to compute discriminative power |

### `EBShrinkageConfig`

Frozen dataclass in `models.py` configuring the A2′ empirical-Bayes shrinkage stage.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tau2_floor_frac` | `float` | `0.05` | Method-of-moments floor for τ̂² expressed as a fraction of `Var(α̂)`. Prevents total collapse when the OLS fit over-explains α̂ in small samples. |
| `triple_goal` | `bool` | `True` | When True, apply Lin-Louis-Shen (1999) triple-goal rank correction to the posterior mean before returning. Preserves posterior rank ordering but substitutes the empirical TWFE α̂ histogram. |
| `eb_min_builds` | `int` | `8` | The optimizer skips shrinkage entirely and returns raw α̂ if fewer than this many builds have been finalized. Stability guard for the OLS fit in the first few trials. |
| `ols_ridge` | `float` | `1e-4` | Ridge regularization added to the normal equations `XᵀX` diagonal (intercept excluded) for numerical stability. Matches design doc §2.2. |

### `ScoreMatrix`

Mutable accumulator class maintaining a sparse build × opponent score matrix. Internal to `deconfounding.py` — not exported.

```python
class ScoreMatrix:
    def record(self, build_idx: int, opp_name: str, raw_score: float) -> None: ...
    def build_alpha(self, build_idx: int, config: TWFEConfig) -> float: ...
    def build_sigma_sq(self, build_idx: int) -> float: ...
    def opponent_beta(self, opp_name: str) -> float: ...
    @property
    def n_builds(self) -> int: ...
    @property
    def n_opponents(self) -> int: ...
```

**Internal state:**
- `_build_map: dict[int, int]` — build_idx (trial number) → row index
- `_opp_map: dict[str, int]` — opp_name (variant ID) → column index
- `_entries: list[tuple[int, int, float]]` — (row, col, value) triples
- `_dirty: bool` — True after any `record()`, cleared after decomposition
- `_alpha: np.ndarray | None` — cached build quality estimates
- `_beta: np.ndarray | None` — cached opponent difficulty estimates
- `_sigma_eps_sq: float | None` — cached pooled residual MSE (used by A2′ EB shrinkage)

**`record(build_idx, opp_name, raw_score)`:** Adds one observation. Auto-expands index maps for new builds/opponents. Sets `_dirty = True`.

**`build_alpha(build_idx, config)`:** If `_dirty`, materializes the dense matrix (NaN for unobserved), calls `twfe_decompose()`, caches `(alpha, beta, sigma_eps_sq)`, clears `_dirty`. Then calls `trimmed_alpha()` with `config.trim_worst` on the build's row. Returns the trimmed α_i.

**`build_sigma_sq(build_idx)`:** Returns σ̂_i² = σ̂_ε² / n_i using the cached decomposition, where n_i is the count of observed opponents for this build. Raises `ValueError` if the cache is empty or dirty (caller must first call `build_alpha()` at least once since the most recent `record()`). Used by EB shrinkage to weight `α̂_i` by per-build precision.

**`opponent_beta(opp_name)`:** Returns cached β_j. Raises `ValueError` if no decomposition has been computed yet.

**Pooled residual MSE (σ̂_ε²):** Computed during `_ensure_decomposed()` as `sum_{(i,j) observed} (Y_ij − α_i − β_j)² / max(n_obs − n_params, 1)` where `n_params = n_builds + n_opps − 1` (one identifying constraint: adding a constant to all α and subtracting from all β leaves the model unchanged).

**Complexity:** `build_alpha()` is O(n_builds × n_opps × n_iters) when the cache is dirty. At 200 builds × 10 opponents × 20 iterations, this is ~40K operations (~1ms). `build_sigma_sq()` is O(1) after decomposition.

## Functions

### `twfe_decompose(score_matrix, n_iters, ridge) -> (alpha, beta)`

```python
def twfe_decompose(
    score_matrix: np.ndarray,  # (n_builds, n_opps), NaN = unobserved
    n_iters: int = 20,
    ridge: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
```

Pure function. Alternating projection algorithm:

1. Initialize α = zeros(n_builds), β = zeros(n_opps)
2. Compute observed mask: `observed = ~isnan(score_matrix)`
3. For each iteration:
   - For each opponent j: β_j = sum(Y_ij − α_i for observed i) / (count_observed_i + ridge)
   - For each build i: α_i = sum(Y_ij − β_j for observed j) / (count_observed_j + ridge)
4. Return (α, β)

Ridge regularization prevents divergence when a column/row has very few observations. With ridge=0.01, estimates are pulled slightly toward zero — negligible bias for well-observed entries.

### `trimmed_alpha(scores, beta, trim_worst) -> float`

```python
def trimmed_alpha(
    scores: np.ndarray,  # row of score_matrix for build i (may contain NaN)
    beta: np.ndarray,    # opponent effects (full array, same length as scores)
    trim_worst: int,
) -> float:
```

Pure function. Computes build quality from residuals after removing opponent effects:

1. Compute residuals: `r_j = Y_ij − β_j` for all observed j
2. Sort residuals ascending
3. If `trim_worst >= len(residuals)`: warn and return mean of all residuals (degenerate case — never crash)
4. Drop the lowest `trim_worst` residuals
5. Return mean of remaining residuals

With `trim_worst=0`, this returns the plain TWFE α_i (equivalent to the alternating projection estimate). With `trim_worst=2` (default), the two worst opponent matchups are dropped before averaging — this makes the estimate robust to 1-2 bad RPS (rock-paper-scissors) matchups without being as conservative as minimax.

### `eb_shrinkage(alpha, sigma_sq, X, config) -> (alpha_eb, gamma, tau2, kept_cols)`

```python
def eb_shrinkage(
    alpha: np.ndarray,      # (n,) — TWFE point estimates α̂_i
    sigma_sq: np.ndarray,   # (n,) — per-build variance σ̂_i² = σ̂_ε² / n_i
    X: np.ndarray,          # (n, p) — pre-matchup covariate matrix
    config: EBShrinkageConfig,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
```

Pure function. Implements the closed-form two-level Gaussian empirical-Bayes posterior mean from design §2.2:

1. **Guard:** if `n < 3` raise `ValueError`. If `Var(α̂) < ε` issue `warnings.warn` and return `(alpha.copy(), zeros, 0.0, all_cols)` — fully degenerate.
2. **Standardize X columns:** compute per-column mean/std; keep only columns with `std > ε`. Dropped indices are reported via `warnings.warn` and excluded from `kept_cols`. Standardization `X_std = (X[:, kept] − μ) / σ` stabilizes γ̂ scaling when covariates have disparate units.
3. **Augment with intercept:** `X_aug = hstack([ones(n,1), X_std])`.
4. **Ridge-regularized OLS:** `γ̂ = (XᵀX + εI)⁻¹Xᵀα̂` with `ε = config.ols_ridge`, intercept row/col excluded from the ridge penalty:
   ```
   XtX = X_aug.T @ X_aug
   ridge = eye(XtX.shape[0]) * config.ols_ridge
   ridge[0, 0] = 0.0
   gamma = np.linalg.solve(XtX + ridge, X_aug.T @ alpha)
   ```
5. **τ̂² via method-of-moments with floor:**
   ```
   mu = X_aug @ gamma
   resid = alpha − mu
   tau2 = max(Var(resid) − mean(sigma_sq), config.tau2_floor_frac · Var(alpha))
   ```
6. **Posterior mean:**
   ```
   w = tau2 / (tau2 + sigma_sq)           # per-build weight
   alpha_eb = w · alpha + (1 − w) · mu    # convex combination
   ```
7. Return `(alpha_eb, gamma, tau2, kept_cols)`.

**Attenuation bias:** γ̂ is OLS-fitted against α̂ (a measurement of α), which would in theory induce attenuation. At n_i ≥ 5 the bias is first-order negligible; the triple-goal rank correction below further mitigates any residual bias.

### `triple_goal_rank(posterior, raw) -> np.ndarray`

```python
def triple_goal_rank(
    posterior: np.ndarray,  # (n,) — α̂_EB posterior means
    raw: np.ndarray,        # (n,) — α̂_TWFE raw estimates
) -> np.ndarray:
```

Pure function. Lin-Louis-Shen (1999) triple-goal correction:

```
ranks = argsort(argsort(posterior))   # 0..n−1 ordinal ranks
return sort(raw)[ranks]               # substitute raw histogram, preserve posterior rank
```

Output Spearman ρ with truth is identical to `α̂_EB` (ranks preserved); top/bottom-tail magnitudes are restored to the TWFE scale so Optuna TPE's expected-improvement acquisition sees the exploitation signal without EB's regression-to-mean compression. O(n log n).

## Usage in Optimizer

The optimizer's `StagedEvaluator` maintains a `ScoreMatrix` instance and a `_completed_records: dict[int, _EBRecord]` cache of finalized builds' covariates. On each matchup result:

```
_handle_result():
    raw = combat_fitness(result, config)
    _score_matrix.record(trial_number, opp_id, raw)
    trial.report(raw, step=rung_step)  # WilcoxonPruner — rung position (0-based)

_finalize_build():
    twfe_fitness = _score_matrix.build_alpha(trial_number, config.twfe)  # A1
    _completed_records[trial_number] = _EBRecord(...)
    eb_fitness, eb_diag = _apply_eb_shrinkage(trial_number, twfe_fitness) # A2′
    shaped_fitness, shape_diag = _shape_fitness(eb_fitness)               # A3 (Box-Cox)
    study.tell(trial, shaped_fitness)
    # eb_diag (None when shrinkage fell back to raw α̂) is persisted to the
    # JSONL under `eb_diagnostics`; see spec 24 §JSONL Evaluation Log.

_apply_eb_shrinkage(trial_number, twfe_fitness) -> (float, _EBDiagnostics | None):
    if score_matrix.n_builds < config.eb.eb_min_builds:
        return twfe_fitness, None
    indices = list(_completed_records)
    alphas = array([_score_matrix.build_alpha(i, config.twfe) for i in indices])
    sigma_sqs = array([_score_matrix.build_sigma_sq(i) for i in indices])
    X = vstack([_build_covariate_vector(_completed_records[i]) for i in indices])
    alpha_eb, gamma, tau2, kept = eb_shrinkage(alphas, sigma_sqs, X, config.eb)
    if config.eb.triple_goal:
        alpha_eb = triple_goal_rank(alpha_eb, alphas)
    idx = indices.index(trial_number)
    if tau2 == 0.0:                                 # var(α̂)≈0 short-circuit
        return float(alpha_eb[idx]), None
    sigma_sq_twfe = float(sigma_sqs[idx])
    sigma_sq_eb = tau2 * sigma_sq_twfe / (tau2 + sigma_sq_twfe)  # posterior var
    diag = _EBDiagnostics(sigma_sq_twfe, sigma_sq_eb, tau2, gamma, kept)
    return float(alpha_eb[idx]), diag
```

The returned `_EBDiagnostics` (or `None` on either fallback path) is written to the JSONL under `eb_diagnostics` so analysis code can reconstruct per-trial posterior credible intervals via `eb_fitness ± 1.96·√sigma_sq_eb` and audit per-trial shrinkage weights via `τ̂² / (τ̂² + σ̂_i²)`. See spec 24 for the full signal quality pipeline including A3 Box-Cox shaping and JSONL evaluation log schema.

## Design Rationale

**Why TWFE over raw Elo?** Standard Elo is confounded by non-stationary build quality — simulation showed ρ(Elo, true difficulty) = 0.024 with improving builds. TWFE treats builds as independent observations and opponents as fixed effects, naturally handling the non-stationarity.

**Why trimmed mean over minimax?** Minimax (worst-case opponent) is too conservative in games with RPS dynamics — it penalizes builds for one bad matchup. Trimmed mean drops the worst 1-2 matchups, balancing robustness and discrimination. Simulation showed trimmed TWFE (ρ = 0.811) outperforms z-score minimax.

**Why alternating projection over full matrix factorization (ALS)?** The additive model (rank 1 with known structure) has a closed-form iterative solution that converges in ~20 iterations. Full rank-r ALS is more powerful but adds complexity for marginal gain — simulation showed ALS imputation at ρ = 0.726 vs TWFE at ρ = 0.775. The additive assumption is well-validated by the literature convergence across 6 fields.

**Why EB shrinkage (A2′) over the shipped scalar control variate (A2)?** The shipped A2 used only the scalar `composite_score` as a single covariate and applied a subtractive correction `α̂_i − β̂·(h_i − h̄)`. An earlier v1 design attempted to generalize this to multivariate conditioning (CUPED / FWL / PDS lasso on 13 scorer components) and failed catastrophically: synthetic Δρ = −0.35 vs plain TWFE (Cinelli-Forney-Pearl 2022 "Case 8" bad-control pattern — the scorer components are noisy *proxies of the estimand* α, not orthogonal covariates of Y). EB shrinkage reframes the problem as **fusion**: α̂_TWFE and γ̂ᵀX are treated as two noisy measurements of the same latent α and combined by precision weighting (Bayes rule), never subtracted. Validated at Δρ = +0.036 vs plain TWFE / +0.057 vs shipped A2 on LOOO Hammerhead 2026-04-17. See `docs/reference/phase5d-covariate-adjustment.md` for the full derivation.

**Why triple-goal rank correction?** Pure EB posterior means suffer regression-to-mean compression at the tails (Louis 1984). Since Optuna TPE's acquisition reads the posterior as a magnitude, compression dulls exploitation. Triple-goal (Lin, Louis & Shen 1999) preserves the improved rank ordering but substitutes the empirical α̂ histogram, restoring the exploitation signal with zero Spearman-ρ cost.
