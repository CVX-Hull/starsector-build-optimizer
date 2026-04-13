# Deconfounding Specification

Two-Way Fixed Effects (TWFE) decomposition for schedule-adjusted build quality estimation. Defined in `src/starsector_optimizer/deconfounding.py`, with `TWFEConfig` in `src/starsector_optimizer/models.py`.

## Problem

When builds are evaluated against different opponent subsets, raw fitness scores are incomparable: a high score could mean "good build" or "easy opponents." When build quality improves over time (optimizer convergence), all opponents appear easier — opponent difficulty estimates are confounded with the build improvement trend.

TWFE solves both problems by decomposing the score matrix into additive build quality (α_i) and opponent difficulty (β_j) components. This decomposition is the consensus solution across six independent fields: IRT, game rating systems, causal inference, sports analytics, bandits/active learning, and coevolutionary algorithms. See `docs/reference/phase5b-deconfounding-research.md` for the full literature synthesis.

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

### `ScoreMatrix`

Mutable accumulator class maintaining a sparse build × opponent score matrix. Internal to `deconfounding.py` — not exported.

```python
class ScoreMatrix:
    def record(self, build_idx: int, opp_name: str, raw_score: float) -> None: ...
    def build_alpha(self, build_idx: int, config: TWFEConfig) -> float: ...
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

**`record(build_idx, opp_name, raw_score)`:** Adds one observation. Auto-expands index maps for new builds/opponents. Sets `_dirty = True`.

**`build_alpha(build_idx, config)`:** If `_dirty`, materializes the dense matrix (NaN for unobserved), calls `twfe_decompose()`, caches `(alpha, beta)`, clears `_dirty`. Then calls `trimmed_alpha()` with `config.trim_worst` on the build's row. Returns the trimmed α_i.

**`opponent_beta(opp_name)`:** Returns cached β_j. Raises `ValueError` if no decomposition has been computed yet.

**Complexity:** `build_alpha()` is O(n_builds × n_opps × n_iters) when the cache is dirty. At 200 builds × 10 opponents × 20 iterations, this is ~40K operations (~1ms). Acceptable for the current scale; if scale exceeds 1000+ trials, consider incremental updates using cached β.

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

## Usage in Optimizer

The optimizer's `StagedEvaluator` maintains a `ScoreMatrix` instance. On each matchup result:

```
_handle_result():
    raw = combat_fitness(result, config)
    _score_matrix.record(trial_number, opp_id, raw)
    trial.report(raw, step=opp_step)  # WilcoxonPruner (unchanged)

_finalize_build():
    twfe_fitness = _score_matrix.build_alpha(trial_number, config.twfe)
    cv_fitness = _apply_control_variate(twfe_fitness, heuristic_val)  # A2
    ranked_fitness = _rank_fitness(cv_fitness)  # A3
    study.tell(trial, ranked_fitness)
```

See spec 24 for the full signal quality pipeline.

## Design Rationale

**Why TWFE over raw Elo?** Standard Elo is confounded by non-stationary build quality — simulation showed ρ(Elo, true difficulty) = 0.024 with improving builds. TWFE treats builds as independent observations and opponents as fixed effects, naturally handling the non-stationarity.

**Why trimmed mean over minimax?** Minimax (worst-case opponent) is too conservative in games with RPS dynamics — it penalizes builds for one bad matchup. Trimmed mean drops the worst 1-2 matchups, balancing robustness and discrimination. Simulation showed trimmed TWFE (ρ = 0.811) outperforms z-score minimax.

**Why alternating projection over full matrix factorization (ALS)?** The additive model (rank 1 with known structure) has a closed-form iterative solution that converges in ~20 iterations. Full rank-r ALS is more powerful but adds complexity for marginal gain — simulation showed ALS imputation at ρ = 0.726 vs TWFE at ρ = 0.775. The additive assumption is well-validated by the literature convergence across 6 fields.
