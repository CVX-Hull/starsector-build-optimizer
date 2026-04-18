# Phase 5D — Empirical-Bayes Shrinkage of TWFE α̂ Toward a Heuristic-Derived Prior

> **Status**: Design finalized after two-stage research and validation. Implementation pending.

Design for using the auxiliary per-build signals (heuristic composite score and 13 scorer components) already produced by the Python data layer to improve the TWFE fitness estimate. The shipped pipeline (`combat_fitness.py` → `deconfounding.py` A1 → `optimizer.py` A2/A3) uses the `composite_score` only as a scalar control variate at A2; the other 12 scorer components are ignored. This doc specifies a bitter-lesson-compliant extension that **fuses** (`α̂_TWFE`, `heuristic`, `scorer components`) as multiple noisy measurements of the same latent build quality α via empirical-Bayes shrinkage toward a regression-predicted prior mean.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer pipeline. A1 (TWFE decomposition) → A2 (control-variate adjustment) → A3 (rank shaping) are the three stages; Phase 5D replaces A2, Phase 5E replaces A3. Both are orthogonal and compose cleanly. See `docs/reference/implementation-roadmap.md` for phase overview and `docs/reference/phase5a-deconfounding-theory.md` for the TWFE foundation.

**Design history.** An earlier version of this doc (2026-04-13 through 2026-04-17) specified a *conditioning*-paradigm design — multivariate CUPED / FWL / post-double-selection lasso — treating the heuristic and scorer components as covariates to partial out of the outcome Y_ij. Synthetic validation (20 seeds, p<0.0001) and a real-data ship-gate on the 2026-04-17 Hammerhead run (368 builds × 54 opponents, LOOO across 5 anchor probes) showed that paradigm is categorically wrong for this setting — the Δρ vs plain TWFE was **−0.35 synthetic and −0.13 real**. See §4.5 for the rejection, and `experiments/phase5d-covariate-2026-04-17/REPORT.md` for the full refutation. The current doc describes the fusion-paradigm replacement that passed the same ship-gate at Δρ = +0.036 on real Hammerhead data.

---

## 1. Problem

Three categories of pre-matchup signal are available per build:

1. **Python-computed heuristic composite** — `ScorerResult.composite_score`, the single calibrated scalar already used by the shipped A2 control variate.
2. **Python-computed scorer decomposition and build structure** — `total_dps`, per-damage-type DPS, flux_balance, effective_hp, range_coherence, etc., plus raw counts `n_hullmods`, `flux_vents`, `flux_capacitors`, `op_used_fraction`. Hand-engineered aggregates with known moderate ρ ≈ 0.3–0.5 to α.
3. **Engine-computed effective stats** — post-hullmod `MutableShipStats` values (`eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating`, `eff_hull_hp`) readable at the Java harness SETUP phase before combat starts. These are the game engine's authoritative computation of what hullmods and vents/caps produce — strictly more accurate than our Python-side `compute_effective_stats` recomputation.

The current shipped A2 uses only (1). The question this doc answers: **which subset of these signals should form the covariate vector `X_i` in the empirical-Bayes prior regression — and at what dimension?**

The answer — derived in §2.7 from a feature-count × dataset-size sweep on a realistic-throughput simulator — is a conservative **8-feature** vector: 4 engine-computed stats from the Java harness + 3 Python primitives + 1 calibrated heuristic scalar. Rationale and the rejected higher-dimensional alternative are in §2.7.

The post-matchup outputs of the harness (`duration_seconds`, `hp_differential`, `damage_efficiency`, `overload_count_differential`, `peak_time_remaining`, `armor_fraction_remaining`) are **excluded by design** — see §2.5.

---

## 2. Accepted Design — EB Shrinkage with Covariate-Powered Prior

### 2.1 Model (two-level Gaussian)

The key reframe: heuristic signals are **noisy estimators of the same latent α** as the TWFE estimate, not orthogonal covariates of Y. Phase 5D is a **fusion** problem (combine multiple noisy views of one quantity by precision weighting), not a **conditioning** problem (project out explained variance).

Two-level hierarchical Gaussian model:

```
Likelihood (A1 TWFE, shipped):     α̂_i | α_i   ~ N(α_i, σ̂_i²)
Prior (learned from the data):     α_i | X_i    ~ N(γᵀ [1, X_i], τ²)
```

where:

- **`α̂_i`** is the Phase 5A TWFE estimate of build i's fitness.
- **`σ̂_i²`** is its squared standard error. Computed from TWFE residuals as `σ̂_ε² / n_i`, where `σ̂_ε²` is the pooled residual variance and `n_i` is the number of opponents build i was evaluated against.
- **`X_i ∈ ℝ^8`** is the pre-matchup covariate vector — 4 engine-computed stats + 3 Python primitives + 1 calibrated heuristic scalar, standardized column-wise. Full feature list and selection rationale in §2.7.
- **`γ ∈ ℝ^9`** (OLS coefficient vector including intercept) defines the regression prior mean `γᵀ [1, X_i]`.
- **`τ²`** is the residual variance of α around the regression prior — the unexplained between-build variance.

Both `γ` and `τ²` are estimated empirically from the same data they will be applied to — this is the "empirical" in empirical Bayes, after Robbins 1956 / Efron & Morris 1975 / Ignatiadis & Wager 2022.

### 2.2 Estimator (closed-form posterior mean)

Under the conjugate Gaussian-Gaussian model, the posterior mean of α_i given α̂_i and the prior is a precision-weighted convex combination:

```
α̂_EB_i  =  w_i · α̂_i  +  (1 − w_i) · γ̂ᵀ [1, X_i]
w_i    =  τ̂² / (τ̂² + σ̂_i²)
```

**`γ̂`** is the ordinary-least-squares fit of `α̂_i` on `[1, X_i]` at the build level:

```
γ̂ = (Xᵀ X + ε I)⁻¹ Xᵀ α̂
```

with a small ε (1e-4) for numerical stability. Attenuation bias from measurement error in α̂ is first-order negligible when TWFE is reasonably precise (n_i ≥ ~5); re-examination is deferred to revisits under §4.4.

**`τ̂²`** is estimated by the method of moments:

```
τ̂² = max( Var(α̂ − γ̂ᵀ [1, X])  −  mean(σ̂_i²),   0.05 · Var(α̂) )
```

The residual variance of `α̂` around the OLS fit decomposes additively into `τ²` (between-build variance) plus the average measurement variance `E[σ̂_i²]`, so subtracting the latter isolates the former. The 5% floor prevents total collapse when the OLS fit over-explains α̂ in small samples.

**`w_i`** is the per-build shrinkage weight:

- High `σ̂_i²` (few opponents faced) → `w_i → 0` → shrink toward the prior. Builds with sparse matchup data rely on the heuristic.
- Low `σ̂_i²` (many opponents faced) → `w_i → 1` → trust the data. Well-evaluated builds keep their α̂.
- High `τ̂²` (heuristic is a weak predictor of α overall) → `w_i → 1` uniformly → prior contributes little.
- Low `τ̂²` (heuristic strongly predicts α) → `w_i < 1` → prior pulls hard.

The whole computation is closed-form, ~10 lines of NumPy, O(N·k) per pass where N = #builds, k = 8. Sub-millisecond at our scale.

### 2.3 Where it lives in the pipeline

HN replaces the shipped A2:

```
Shipped:   A1 TWFE(Y)                                        →  α̂_i
           A2 α̂_i − β̂_cv · (h_i − h̄)                        →  cv_fitness_i
           A3 rank-shape(cv_fitness)                          →  reported_fitness

Phase 5D:  A1 TWFE(Y)                                        →  α̂_i, σ̂_i
           A2' EB-shrinkage(α̂, σ̂, X) via §2.2                 →  α̂_EB_i
           A2''  triple-goal-rank(α̂_EB; α̂) via §2.4           →  α̂_EBT_i
           A3 Box-Cox-or-rank-shape(α̂_EBT)                    →  reported_fitness
```

The A3 stage is the Phase 5E Box-Cox rewrite (`docs/reference/phase5e-shape-revision.md`); its input is `α̂_EBT`, not `α̂_EB`. The two phases compose cleanly.

### 2.4 Rank correction via triple-goal estimation

Pure EB shrinkage (α̂_EB) has a known failure mode for ranking: the top and bottom of the distribution shrink toward the mean more than the middle, compressing α̂_EB's histogram. This is the "regression to the mean" effect Louis 1984 *JASA* 79:393 documented. When Optuna TPE's expected-improvement acquisition reads α̂_EB, the compression dulls the exploitation signal at the top of the distribution.

Lin, Louis & Shen 1999 *Stat. Med.* 18:2135 give the fix — **triple-goal estimation**:

```
rank_i = argsort(argsort(α̂_EB))                           # 0..N-1
α̂_EBT_i = sort(α̂_TWFE)[rank_i]                            # substitute histogram
```

The output preserves the EB-improved *rank ordering* of builds while substituting the empirical TWFE α̂ histogram for the shrunken posterior-mean histogram. Spearman ρ with truth is identical to α̂_EB's (ranks are preserved); the top-tail magnitudes are preserved for acquisition. Cost: O(N log N). Call this **EBT** (empirical Bayes + triple-goal).

EBT is strictly a rank-preserving post-processing step; it can be turned off via a config flag if downstream analysis prefers pure EB posteriors.

### 2.5 Stage-1 timing filter — excluded-by-design covariates

`X_i` includes *only* pre-matchup build-level features. Post-matchup outputs of the harness are **not** admissible candidates:

| Signal | Origin | Tier | Admissible? |
|---|---|---|---|
| `eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating`, `eff_hull_hp` | pre-matchup, engine-computed at Java SETUP | **Engine (preferred)** | ✓ |
| `composite_score`, scorer decomposition | pre-matchup (from `ScorerResult`) | Python composite | ✓ |
| `n_hullmods`, `flux_vents`, `flux_capacitors`, `op_used_fraction`, `n_weapons_filled` | pre-matchup (build structure) | Python raw | ✓ |
| Opponent-side heuristic, identity features | pre-matchup | Python raw | ✓ (future — not in initial ship) |
| `duration_seconds`, `hp_differential` | post-matchup | — | ✗ |
| `damage_efficiency`, `overload_count_differential` | post-matchup | — | ✗ |
| `peak_time_remaining`, `armor_fraction_remaining` | post-matchup | — | ✗ |

The reasoning: post-matchup signals are mechanical consequences of Y_ij (colliders on α). Including them in the prior regression `α̂ ~ X` is fine *in the regression step* (OLS is attenuation-safe), but they become bad controls if the design ever reverts to within-TWFE covariate adjustment. The exclusion is defensive and zero-cost — we have no business routing post-matchup signals through the A2 pipeline.

Among admissible signals, engine-computed stats are preferred over Python composites when equivalent — the engine's hullmod-effect computation is authoritative; our `compute_effective_stats` recomputation can drift. See §2.7 for which specific features survived selection.

This is the same convention as CUPED (Deng et al. 2013), MLRATE (Guo et al. 2021), and DoorDash CUPAC — one operational rule per output, no per-covariate causal reasoning.

### 2.6 Connections to adjacent literatures

The same two-level Gaussian model appears under different names across six fields:

| Field | Name | Canonical reference |
|---|---|---|
| Empirical Bayes (stats) | Covariate-powered EB | Ignatiadis & Wager 2022 *Ann. Stat.* 50:2467, arXiv:1810.02333 |
| Classical shrinkage (stats) | Efron-Morris with covariate target | Efron & Morris 1975 *JASA* 70:311 |
| Parametric empirical Bayes | Tweedie / regression prior | Efron 2010 *Large-Scale Inference* ch. 1, 11 |
| Hierarchical Bayes (stats) | BLUP with regression prior | Lindley & Smith 1972 *JRSS-B* 34:1; Gelman 2006 *Technometrics* 48:432 |
| Actuarial | Hachemeister credibility regression | Hachemeister 1975 |
| Psychometrics | Mislevy collateral-info IRT | Mislevy 1987 *Appl. Psych. Meas.* 11:81 |
| Games rating | TrueSkill 2 w/ feature-based prior | Minka, Cleven & Zaykov 2018 MSR-TR-2018-8 |

All seven are mathematically identical: α_i ~ N(γᵀX_i, τ²), α̂_i | α_i ~ N(α_i, σ_i²), posterior by Bayes rule. The convergence across independent fields is the best available evidence that this is the right formulation for "rate many things with sparse data plus side info."

For the formal derivation of why this beats CUPED — specifically the derivation ρ(α̂_CUPED, α) = √(1 − R) where R is the heuristic's reliability — see `experiments/phase5d-covariate-2026-04-17/FUSION_REPORT.md` §4.

### 2.7 Feature selection — the 8-feature ship set

#### Dataset-size budget context

Measured from `experiments/hammerhead-twfe-2026-04-13/optimizer.log` (4 parallel instances, WilcoxonPruner + ASHA): ~27 completed trials per hour. Realistic dataset sizes:

| Run duration | Expected N (non-pruned builds) |
|---|---:|
| 8h overnight | ~215 |
| 24h full day | ~650 |
| 72h multi-day | ~1950 |

The Hammerhead 2026-04-17 run (N = 313 non-pruned builds) sits in the middle of this range. The feature-count design must be safe at N ≈ 200 and beneficial up to N ≈ 1000.

#### Feature-count × dataset-size sweep

A synthetic sweep (504 cells = 6 seeds × {200, 368, 900} N × {0,1,2,4,8,13,20} useful features × {0,2,6,12} noise features) at `experiments/phase5d-covariate-2026-04-17/feature_count_sweep.py` mapped Δρ(HN − A0) as a function of `(p_useful, p_noise, N)`. Headline findings:

- **Diminishing returns at p_useful ≈ 8.** Marginal Δρ from the 9th–13th feature is +0.03 total; beyond p = 13 the curve is flat-to-declining at realistic N.
- **Pure noise X is mildly harmful (≤ 0.03 Δρ), not catastrophic.** The τ² floor (0.05 · Var(α̂)) prevents OLS over-fit of γ̂ from collapsing the prior.
- **p/N overfit kicks in above p/N ≈ 0.08.** At N = 200, `p_total = 32` halves the gain vs the p = 13 peak. At N = 900 no penalty up to p = 32.
- **Ship gate (Δρ ≥ +0.02) clears in 72/84 cells.** All 12 failing cells had p_useful = 0.

Full report: `experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md`.

#### The 8-feature ship set

Targeting p = 8 (the knee of the diminishing-returns curve, p/N = 0.04 at N = 200):

| # | Feature | Source | Tier |
|---|---|---|---|
| 1 | `eff_max_flux` | Java `MutableShipStats.getMaxFlux()` at SETUP | Engine-computed |
| 2 | `eff_flux_dissipation` | Java `MutableShipStats.getFluxDissipation().getModifiedValue()` | Engine-computed |
| 3 | `eff_armor_rating` | Java armor grid + stat bonuses, post-hullmod | Engine-computed |
| 4 | `eff_hull_hp` | Java `ship.getHullSpec().getHitpoints() × hull_hp_mult` | Engine-computed |
| 5 | `total_weapon_dps` | Python — raw sum of equipped-weapon DPS, no type weighting | Python raw |
| 6 | `n_hullmods` | `len(Build.hullmods)` | Python raw |
| 7 | `op_used_fraction` | `Build.op_spent / hull.ordnance_points` | Python raw |
| 8 | `composite_score` | `ScorerResult.composite_score` | Python composite (calibrated) |

Coverage of information axes: flux-cap (1), flux-sustain (2), armor-defense (3), hull-defense (4), offense (5), build-complexity (6), investment-intensity (7), calibrated ensemble (8). Each axis gets exactly one feature.

#### What's dropped from the earlier 16-feature candidate

| Dropped | Reason |
|---|---|
| `kinetic_dps`, `he_dps`, `energy_dps`, `fragmentation_dps` | Type decomposition is a theory; raw `total_weapon_dps` is admissible as a pure sum |
| `flux_balance`, `flux_efficiency` | Subsumed by engine-computed flux capacity + dissipation |
| `effective_hp`, `armor_ehp`, `shield_ehp` | Subsumed by engine-computed armor + hull HP + flux cap |
| `range_coherence`, `engagement_range`, `damage_mix`, `op_efficiency` | Hand-weighted composites; drop for conservatism |
| `flux_vents`, `flux_capacitors` | Already baked into `eff_max_flux` / `eff_flux_dissipation` — direct redundancy |

Features 1–4 **replace** hand-engineered Python equivalents rather than supplementing them. This is the bitter-lesson move — the engine's hullmod-effect computation is authoritative.

#### Expected Δρ at p = 8

Interpolating from the sweep (FEATURE_COUNT_REPORT.md §Findings):

| N | Expected Δρ(HN − A0) at p = 8, p_noise = 0 |
|---|---:|
| 200 | +0.337 |
| 368 | +0.323 |
| 900 | +0.352 |

All three clear the ship gate by ≥ 16×. The current 16-feature validation (§3.3) observed +0.036 on 5-probe Hammerhead LOOO. The 8-feature set's projected Δρ drops by ~0.03 on synthetic but is expected to recover most of that on real data where the engine-computed features strictly dominate the dropped Python composites.

#### When to revisit

- **Add the 9th / 10th feature** (kinetic_dps and he_dps) only if ship-gate is tight post-validation. Sweep shows Δρ gain ≈ +0.02 at N ≥ 368; negligible at N = 200.
- **Drop to p = 6** (remove n_hullmods + op_used_fraction) only if production runs routinely land at N < 150. Not expected given current throughput.

---

## 3. Integration plan

### 3.1 Code changes

**Python side:**

- **`src/starsector_optimizer/deconfounding.py`** — extend with:
  - `TWFEResult` named tuple returning `(alpha, beta, sigma_i)`, where `sigma_i` comes from pooled residual MSE / n_i.
  - `eb_shrinkage(alpha, sigma_i, X_build, tau2_floor_frac=0.05) → (alpha_eb, gamma, tau2)`: the two-level EB closed-form from §2.2.
  - `triple_goal_rank(posterior, raw) → alpha_ebt`: the one-line histogram substitution from §2.4.
- **`src/starsector_optimizer/optimizer.py`** — delete `_apply_control_variate`, `_refit_control_variate`, `_cv_beta`, `_cv_heuristic_mean`. Replace with one call to `eb_shrinkage` at `_finalize_build`. Route the build's 8-dim feature vector from the trial params + parsed `EngineStats`.
- **`src/starsector_optimizer/combat_fitness.py`** — expose per-build pre-matchup feature vector combining `ScorerResult.composite_score`, `total_weapon_dps`, `n_hullmods`, `op_used_fraction` with the 4 engine-computed stats from `EngineStats`.
- **`src/starsector_optimizer/result_parser.py`** — add `EngineStats` dataclass (`eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating`, `eff_hull_hp`) and parse the new `setup_stats` block from the matchup result JSON.
- **`src/starsector_optimizer/models.py`** — add `ShrinkageConfig` frozen dataclass with `enable: bool = True`, `tau2_floor_frac: float = 0.05`, `triple_goal: bool = True`. Replace the scalar `_cv_beta` fields in `OptimizerConfig` with this.
- **`docs/specs/28-deconfounding.md`** — extend to describe the EB shrinkage step with σ_i estimation and the triple-goal correction.
- **`docs/specs/09-combat-protocol.md`**, **`docs/specs/12-result-writer.md`**, **`docs/specs/13-combat-harness-plugin.md`** — extend `MatchupResult` schema with `setup_stats` block; document the SETUP-phase emit.

**Java side (new, required for features 1–4):**

- **`combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`** — at end of SETUP state (before the transition to FIGHTING), read from the player ship's `ShipAPI`:
  - `ship.getMutableStats().getMaxFlux().getModifiedValue()` → `eff_max_flux`
  - `ship.getMutableStats().getFluxDissipation().getModifiedValue()` → `eff_flux_dissipation`
  - Summed armor across the `ArmorGridAPI` (or bonus-adjusted base) → `eff_armor_rating`
  - `ship.getHullSpec().getHitpoints() × ship.getMutableStats().getHullBonus().getModifiedValue()` → `eff_hull_hp`

  Stash the 4-tuple on the matchup state.
- **`combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`** — extend the emitted JSON with `"setup_stats": {eff_max_flux, eff_flux_dissipation, eff_armor_rating, eff_hull_hp}` alongside the existing combat outcomes.
- Java tests and integration test covering the new emit (spec-driven — see `.claude/skills/ddd-tdd.md`).

No new Python runtime dependencies. `scipy.linalg.lstsq` + NumPy is sufficient. Estimated code size: ~80 lines net in `deconfounding.py`, ~15 lines deleted from `optimizer.py`, ~40 lines total Java (CombatHarnessPlugin + ResultWriter + test), ~25 lines Python parser + EngineStats dataclass, ~30 lines of spec updates.

### 3.2 Tests

- Unit: synthetic 2-level model with known `γ`, `τ²` — verify recovery of shrinkage weights and posterior mean within tolerance.
- Unit: degenerate cases — all `σ̂_i² → 0` (no shrinkage), all `σ̂_i² → ∞` (full shrinkage), `τ̂² = 0` floor behavior.
- Unit: `triple_goal_rank` preserves exact rank ordering; histogram equals the empirical α̂ histogram by construction.
- Integration: replay on `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl`. Verify mean α̂_EBT matches the reference value computed by `experiments/phase5d-covariate-2026-04-17/phase5d_fusion_validation.py::hammerhead_replay`.
- Regression: ablation on three pipelines — A0 plain TWFE, A shipped scalar CV, A1+A2' new EB — on the Hammerhead log. Confirm LOOO ship-gate Δρ ≥ +0.02 vs both A0 and A (already validated at Δρ = +0.036 vs A0 and +0.057 vs A in the fusion validation).

### 3.3 Ship gate

Phase 5D is gated on **leave-one-opponent-out** rank correlation against the 2026-04-17 Hammerhead log, with the top-5 most-sampled anchor opponents as probes:

- Fit A0, A, A2' (new EB) on the log minus probe opponent.
- Measure Spearman ρ between the refit α̂ and the probe's raw `hp_differential` across all 313 non-pruned builds.
- Require **Δρ(EB − A0) ≥ +0.02 AND Δρ(EB − A) ≥ +0.02** — both relative to plain TWFE *and* relative to the shipped scalar CV. Strict improvement on both required; the shipped A2 itself is not a safe floor (see §4.5).

Observed gate values in fusion validation (5-probe mean, 200-bootstrap CI): A0 = 0.280, A = 0.259, EB = 0.316, EBT = 0.316. Both Δρ margins ≥ +0.028.

---

## 4. Rejected alternatives

### 4.1 Hand-weighted composite fitness — REJECTED

**What it was.** Compute `damage_efficiency`, `overload_differential`, `duration_normalized_damage` per matchup; sum into a weighted composite alongside the existing tiered `combat_fitness`; use the composite as the fitness scalar.

**Why rejected.** The weights have to be picked by humans, and they encode a prior about which combat behaviors indicate quality. This is the anti-pattern the bitter lesson names (Sutton 2019, "The Bitter Lesson"). The EB design above has only one learned linear fit (`γ̂`) at the build level; all magnitudes are data-derived.

### 4.2 Per-frame Java harness tracking — REJECTED

**What it was.** Extend `CombatHarnessPlugin.java` with per-frame accumulators for time-weighted flux, cumulative overload duration, engagement-distance trajectories, time-to-first-hull-damage; fold into a richer composite.

**Why rejected.** Same pathology as 4.1, compounded — the intermediate signals don't just need weights, they inject strategy taxonomy ("kiting vs brawling detection") into the reward signal. The match outcome primitives already collected (`win`/`loss`, `hp_differential`, `duration`, `TIMEOUT` count) are the correct scalar outcomes, and Phase 5E (Box-Cox) will re-warp their distribution without introducing new channels.

See `docs/reference/phase5c-opponent-curriculum.md` §4.4 for the original rejection context.

### 4.3 Multi-information-source Bayesian optimization (MISO) — REJECTED

**What it was.** Treat each auxiliary signal as a separate information source of build quality; aggregate via multi-output GP + knowledge-gradient-per-cost acquisition (Poloczek, Wang & Frazier 2017, NeurIPS).

**Why rejected.** MISO's structural assumption is that each source gives an independent noisy estimate of the *same* objective (cheap simulator vs expensive simulator). The heuristic and scorer components here are **noisy views of the same α**, not alternative estimators of a common cost. EB shrinkage (§2.1) is the correct frame for that — MISO would re-derive the same fusion up to a heavier GP backend. Adopting MISO also requires replacing Optuna TPE with a custom GP/knowledge-gradient sampler — a Phase-7-scale rewrite for modest marginal gain.

### 4.4 Heteroscedastic-TWFE with learned σ²(X) — DEFERRED

**What it was.** Generalise the error model from `ε ~ N(0, σ²)` homoscedastic to `ε_ij ~ N(0, σ²(X_ij))` with learned variance function, then fit TWFE by GLS.

**Why deferred.** Heteroscedasticity is real (glass-cannon builds have bimodal outcomes; peer-Hammerhead matchups produce near-deterministic timeouts). The EB shrinkage partially absorbs this through per-build σ̂_i, but not through per-matchup σ_ij. Revisit after the fusion design ships and diagnostics show residual heteroscedasticity matters. References: Binois, Gramacy & Ludkovski 2018 *JCGS* 27(4), arXiv:1611.05902; Kersting et al. 2007 ICML.

### 4.5 Covariate-adjusted TWFE (CUPED / FWL / PDS / ICP) — REJECTED

**What it was.** An earlier version of this document (2026-04-13 through 2026-04-17) specified a *conditioning-paradigm* design:

```
Y_ij = α_i + β_j + γᵀ X_ij + ε_ij       (multivariate OLS, not EB shrinkage)
```

with `γ` estimated by three-block alternating projection (Frisch-Waugh-Lovell 1933), automatic column selection via Belloni-Chernozhukov-Hansen post-double-selection lasso (2014, arXiv:1201.0220), and an optional ICP invariance safety net (Peters-Bühlmann-Meinshausen 2016).

**Why rejected — empirical refutation.** Full synthetic sweep (20 seeds, 368 builds × 54 opponents matched to Hammerhead characteristics) and real-data ship-gate on the 2026-04-17 Hammerhead evaluation log (313 non-pruned builds × 54 opponents, LOOO across 5 anchor probes) showed:

| Estimator | Synthetic ρ(α̂, truth) | Hammerhead LOOO ρ |
|---|---|---|
| Plain TWFE (A0, baseline) | 0.407 | 0.280 |
| Shipped scalar CV (A) | 0.347 (p<0.0001 worse than A0) | 0.259 |
| CUPED multi-covariate (rejected 5D.v1) | 0.055 (p<0.0001 worse than A0) | 0.118 |
| CUPED + PDS lasso | 0.055 (identical — PDS kept all cols) | 0.118 |
| CUPED + PDS + ICP | 0.077 (marginal rescue) | 0.118 |
| **EB shrinkage (new 5D)** | **0.744** | **0.316** |

Ship gate was +0.02; the conditioning paradigm missed it by −0.14. Full writeup in `experiments/phase5d-covariate-2026-04-17/REPORT.md`.

**Why rejected — causal diagnosis.** The conditioning paradigm treats `X` as an exogenous covariate of the outcome Y and partials it out. This is valid when X is correlated with *noise* in Y (CUPED's A/B-testing use case, where pre-period user metrics are correlated with user-specific noise but orthogonal to treatment randomization). It is invalid when X is correlated with *the estimand*. Here X is correlated with α itself (the scorer components are predictive signals of combat quality) — Cinelli, Forney & Pearl 2022 arXiv:2106.10314 name this "Case 8: proxy of the treatment," a bad-control pattern. Conditioning on a noisy measurement of the estimand biases the coefficient on the estimand and, in the between-build projection, removes the signal α̂ shares with X.

Closed-form derivation (see `FUSION_REPORT.md` §4): in the scalar-h case, if `h_i = c₀ + c₁ α_i + ν_i` and `R = c₁² Var(α) / (c₁² Var(α) + σ_ν²)` is h's reliability as a measurement of α, then after CUPED adjustment

```
ρ(α̂_CUPED, α) = √(1 − R)
```

compared to ρ(α̂, α) = 1 for plain TWFE. The more useful the heuristic, the more CUPED damages the rank correlation. At observed R ≈ 0.2 on our data, plain TWFE beats CUPED by factor √(1 − R) = 0.89 — matching the empirical ratio 0.85 in the simulation.

**Philosophical resolution.** The bad-control fix and the correct design share the same auxiliary data and the same general hygiene concerns (Stage-1 timing filter, rejection of human-designed weights), but differ in the *mathematical operation* applied to the data. The conditioning paradigm subtracts `γ̂ᵀ X` from Y; the fusion paradigm averages `α̂` with `γ̂ᵀ X` by relative precision. That sign flip is the entire difference.

Citations kept in this section for historical completeness; they do not guide the active design:

- Frisch & Waugh 1933 *Econometrica* 1(4):387. Lovell 1963 *JASA* 58(304):993.
- Belloni, Chernozhukov & Hansen 2014 *RES* 81(2):608, arXiv:1201.0220.
- Chernozhukov et al. 2018 *Econometrics J.* 21(1):C1, arXiv:1608.00060.
- Peters, Bühlmann & Meinshausen 2016 *JRSS-B* 78(5), arXiv:1501.01332.
- Deng, Xu, Kohavi & Walker 2013 WSDM (CUPED).
- Cinelli, Forney & Pearl 2022 *Soc. Meth. Res.*, arXiv:2106.10314 (the bad-control taxonomy that names the mistake).

### 4.6 One-factor confirmatory factor analysis (CFA) — REJECTED

**What it was.** Treat `(α̂_TWFE, h, scorer_1, ..., scorer_13)` as 14 noisy indicators of a single latent factor α and estimate factor scores by ML (Jöreskog 1967 `Psychometrika` 32:443; Bollen 1989 *Structural Equations with Latent Variables*).

**Why rejected.** CFA dominated synthetic (ρ = 0.806, best overall) but failed the ship gate on real Hammerhead (ρ = 0.135, Δρ = −0.145 vs A0). The one-factor assumption — that every indicator is a rescaled noisy measurement of the same underlying α — is approximately correct in the synthetic generative model but violated in real data, where scorer components measure genuinely different aspects of a build (kinetic-DPS vs shield-eHP vs flux-efficiency are not one-dimensional). The EB shrinkage in §2.1 makes a strictly weaker structural assumption (`α_i = γᵀ X_i + residual`, no factor structure on X) and is robust to indicator heterogeneity.

A multi-factor CFA with k > 1 latent factors might recover performance, but at that point the simplicity advantage over §2 vanishes and the closed-form OLS prior suffices.

### 4.7 Inverse-variance combining without τ² shrinkage — CLOSE ALTERNATIVE

**What it was.** Graybill & Deal 1959 *Biometrics* 15(4):543 — combine α̂_TWFE and γ̂ᵀX by inverse variance:

```
α̂_IV_i = (α̂_i/σ̂_i² + ĥ_i/σ̂_h²) / (1/σ̂_i² + 1/σ̂_h²),   ĥ_i = γ̂ᵀ X_i
```

Passes the ship gate (Δρ = +0.028 vs A0 on Hammerhead) but is systematically weaker than §2 EB (Δρ = +0.036). The difference: IV does not separate τ² (between-build variance around the prior) from `σ̂_h²` (OLS fit quality). When the OLS fit over-explains α̂ in small samples, IV under-weights the data. The EB method-of-moments τ̂² estimator corrects this — at the cost of a slightly more complex recipe.

Kept as a reference implementation in `experiments/phase5d-covariate-2026-04-17/phase5d_fusion_validation.py::estimator_IV_inverse_variance` and a fallback if diagnostic evidence shows the MoM τ̂² estimate is unstable in production.

### 4.8 EB with PDS-selected prior regression — CLOSE ALTERNATIVE

**What it was.** Apply post-double-selection lasso (Belloni-Chernozhukov-Hansen 2014) *inside the prior regression* to select a subset of the 16 columns before forming `γ̂ᵀ X_i`.

**Why not currently used.** At N = 313 builds × 16 columns, PDS retained **all 16 columns** in both synthetic and real replays — providing identical α̂_EB as §2 without selection, at ~200× the wall time (LassoCV CV cost). Retain as a no-op default; promote only if the covariate pool grows past ~50 columns.

---

## 5. References

**Fusion-family core (prior mean + likelihood, closed-form shrinkage)**
- Stein 1956, "Inadmissibility of the Usual Estimator for the Mean of a Multivariate Normal Distribution," *Proc. 3rd Berkeley Symp.* 1:197.
- James & Stein 1961, "Estimation with Quadratic Loss," *Proc. 4th Berkeley Symp.* 1:361.
- Efron & Morris 1975, "Data Analysis Using Stein's Estimator and Its Generalizations," *JASA* 70:311.
- Lindley & Smith 1972, "Bayes Estimates for the Linear Model," *JRSS-B* 34:1.
- Efron 2010, *Large-Scale Inference*, Cambridge, chap. 1–3, 11. *(Parametric EB + Tweedie + rank-preserving post-hoc corrections.)*
- Ignatiadis & Wager 2022, "Covariate-Powered Empirical Bayes Estimation," *Annals of Statistics* 50:2467, arXiv:1810.02333. *(The most direct modern reference for §2.1.)*

**Rank-preservation after shrinkage (§2.4 triple-goal)**
- Louis 1984, "Estimating a Population of Parameter Values Using Bayes and Empirical Bayes Methods," *JASA* 79:393.
- Lin, Louis & Shen 1999, "Triple-goal estimates for the evaluation of healthcare providers," *Stat. Med.* 18:2135.
- Laird & Louis 1989, "Empirical Bayes ranking methods," *JASA* 84:739.

**Standard error of TWFE α̂ (§2.2 σ̂_i)**
- Arellano 1987, "Computing robust standard errors for within-groups estimators," *Oxford Bull. Econ. Stat.* 49:431.
- Wooldridge 2010, *Econometric Analysis of Cross Section and Panel Data* (2e), MIT, chap. 10.

**Cross-field equivalences (§2.6)**
- Hachemeister 1975, "Credibility for regression models with application to trend," *Credibility: Theory and Applications*, Academic Press.
- Bühlmann & Gisler 2005, *A Course in Credibility Theory and its Applications*, Springer.
- Mislevy 1987, "Exploiting auxiliary information about examinees in the estimation of item parameters," *Applied Psychological Measurement* 11:81.
- Minka, Cleven & Zaykov 2018, "TrueSkill 2: An Improved Bayesian Skill Rating System," MSR-TR-2018-8.
- Kennedy & O'Hagan 2000, "Predicting the output from a complex computer code when fast approximations are available," *Biometrika* 87(1):1. *(Co-kriging — same math, GP framing.)*

**Bad-control / refutation of the conditioning paradigm (§4.5)**
- Cinelli, Forney & Pearl 2022, "A Crash Course in Good and Bad Controls," *Sociological Methods & Research*, arXiv:2106.10314.
- Fuller 1987, *Measurement Error Models*, Wiley. *(The proxy-of-treatment case.)*

**Bitter lesson / design philosophy**
- Sutton 2019, "The Bitter Lesson."

---

## 6. See also

- `docs/reference/phase5a-deconfounding-theory.md` — TWFE decomposition theory (six-field literature synthesis).
- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B foundational research.
- `docs/reference/phase5c-opponent-curriculum.md` — opponent-selection design (anchor-first + incumbent-overlap).
- `docs/reference/phase5e-shape-revision.md` — A3 rank-shape revision (Box-Cox).
- `docs/reference/implementation-roadmap.md` — phase overview and status.
- `docs/specs/28-deconfounding.md` — implementation spec (to be extended when 5D ships).
- `experiments/phase5d-covariate-2026-04-17/REPORT.md` — refutation of the conditioning-paradigm v1 design.
- `experiments/phase5d-covariate-2026-04-17/FUSION_REPORT.md` — validation of the fusion-paradigm v2 design (this doc).
- `experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md` — feature-count × dataset-size sweep backing the §2.7 selection of the 8-feature set.
- `experiments/phase5d-covariate-2026-04-17/feature_count_sweep.py` — reproducible harness for the sweep.
- `src/starsector_optimizer/deconfounding.py`, `src/starsector_optimizer/optimizer.py`, `src/starsector_optimizer/result_parser.py` — production Python code to modify.
- `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`, `ResultWriter.java` — production Java code to modify for the `setup_stats` emit.
