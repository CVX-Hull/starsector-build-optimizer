---
type: reference
status: shipped
last-validated: unvalidated
---

# Phase 5D — Empirical-Bayes Shrinkage of TWFE α̂ Toward a Heuristic-Derived Prior

> **Status**: Implemented 2026-04-18. Original ship gate was a Δρ improvement on V1 LOOO Hammerhead data; that measurement is suspect under the V1 loadout bug. Re-validation of the design threshold (Δρ ≥ +0.02 vs A0 and vs A) is pending under V2 — see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md). See §5 below for implementation notes + file-placement corrections relative to §3.1.
>
> **Empirical-claims status (2026-05-10):** All Δρ values, ρ tables, the synthetic feature-count sweep, the variance audit on the Hammerhead corpus, and the §7 TTK-signal investigation use V1 sim data. Theory, design rationale, and rejected-alternative chain (especially §4.5's refutation of the conditioning paradigm — the closed-form `ρ(α̂_CUPED, α) = √(1 − R)` derivation is paradigm-level and unaffected) are unchanged.

Design for using the auxiliary per-build signals (heuristic composite score and 13 scorer components) already produced by the Python data layer to improve the TWFE fitness estimate. The shipped pipeline (`combat_fitness.py` → `deconfounding.py` A1 → `optimizer.py` A2/A3) uses the `composite_score` only as a scalar control variate at A2; the other 12 scorer components are ignored. This doc specifies a bitter-lesson-compliant extension that **fuses** (`α̂_TWFE`, `heuristic`, `scorer components`) as multiple noisy measurements of the same latent build quality α via empirical-Bayes shrinkage toward a regression-predicted prior mean.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer pipeline. A1 (TWFE decomposition) → A2 (control-variate adjustment) → A3 (rank shaping) are the three stages; Phase 5D replaces A2, Phase 5E replaces A3. Both are orthogonal and compose cleanly. See [implementation-roadmap.md](implementation-roadmap.md) for phase overview and [phase5a-deconfounding-theory.md](phase5a-deconfounding-theory.md) for the TWFE foundation.

**Design history.** An earlier version of this doc (2026-04-13 through 2026-04-17) specified a *conditioning*-paradigm design — multivariate CUPED / FWL / post-double-selection lasso — treating the heuristic and scorer components as covariates to partial out of the outcome Y_ij. Synthetic validation and a real-data ship-gate on the 2026-04-17 Hammerhead run showed that paradigm is categorically wrong for this setting (Δρ vs plain TWFE was strongly negative). See §4.5 for the rejection. The current doc describes the fusion-paradigm replacement that was designed to clear the same ship-gate. Original-magnitude Δρ values are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

---

## 1. Problem

Three categories of pre-matchup signal are available per build:

1. **Python-computed heuristic composite** — `ScorerResult.composite_score`, the single calibrated scalar already used by the shipped A2 control variate.
2. **Python-computed scorer decomposition and build structure** — `total_dps`, per-damage-type DPS, flux_balance, effective_hp, range_coherence, etc., plus raw counts `n_hullmods`, `flux_vents`, `flux_capacitors`, `op_used_fraction`. Hand-engineered aggregates with known moderate ρ ≈ 0.3–0.5 to α.
3. **Engine-computed effective stats** — post-hullmod `MutableShipStats` values (`eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating`, `eff_hull_hp`) readable at the Java harness SETUP phase before combat starts. These are the game engine's authoritative computation of what hullmods and vents/caps produce — strictly more accurate than our Python-side `compute_effective_stats` recomputation.

The current shipped A2 uses only (1). The question this doc answers: **which subset of these signals should form the covariate vector `X_i` in the empirical-Bayes prior regression — and at what dimension?**

The answer — derived in §2.7 from (a) a feature-count × dataset-size sweep on a realistic-throughput simulator and (b) an empirical variance audit on the 2026-04-17 Hammerhead run — is a conservative **7-feature** vector: 3 engine-computed physics stats + 3 Python-raw offense/range aggregates + 1 calibrated heuristic scalar. Rationale and the rejected higher-dimensional alternatives are in §2.7.

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
- **`X_i ∈ ℝ^7`** is the pre-matchup covariate vector — 3 engine-computed physics stats + 3 Python-raw offense/range aggregates + 1 calibrated heuristic scalar, standardized column-wise. Full feature list and selection rationale in §2.7.
- **`γ ∈ ℝ^8`** (OLS coefficient vector including intercept) defines the regression prior mean `γᵀ [1, X_i]`.
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

The whole computation is closed-form, ~10 lines of NumPy, O(N·k) per pass where N = #builds, k = 7. Sub-millisecond at our scale.

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

For the formal derivation of why this beats CUPED — specifically the derivation `ρ(α̂_CUPED, α) = √(1 − R)` where R is the heuristic's reliability — see §4.5 below.

### 2.7 Feature selection — the 7-feature ship set

#### Dataset-size budget context

The feature-count design targets safety at N ≈ 200 and beneficial behaviour up to N ≈ 1000, where N is the number of non-pruned builds in a run. Specific throughput rates and dataset sizes from V1 runs are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

#### Feature-count × dataset-size sweep

A synthetic sweep mapped Δρ(EB − A0) as a function of `(p_useful, p_noise, N)`. Headline qualitative findings (specific Δρ magnitudes pending re-validation):

- **Diminishing returns** appear at p_useful around 8. Beyond that range the curve is flat-to-declining at realistic N.
- **Pure noise X is mildly harmful, not catastrophic.** The τ² floor (0.05 · Var(α̂)) prevents OLS over-fit of γ̂ from collapsing the prior.
- **p/N overfit** kicks in above roughly p/N ≈ 0.08.
- **Ship gate clears** in the large majority of (p_useful, p_noise, N) cells; failing cells were all p_useful = 0.

#### Variance audit against the Hammerhead run

Before finalizing the feature list, candidate engine-computed and Python-raw features were cross-checked against their empirical variance on the per-hull Hammerhead log. Several plausible candidates from first principles turned out to have near-zero variance in per-hull runs:

| Candidate feature | Modifying hullmods in our Python model | Variance verdict |
|---|---|---|
| `eff_max_flux` | flux_capacitors (raw 0–20) + any flux mods | **HIGH** ✓ |
| `eff_flux_dissipation` | flux_vents (raw 0–20) + safetyoverrides | **HIGH** ✓ |
| `eff_armor_rating` | heavyarmor, shield_shunt, assault_package | **MODERATE** ✓ |
| `eff_hull_hp` | reinforcedhull, assault_package partial | **LOW–MODERATE** ✗ |
| `eff_max_speed` | safetyoverrides only | **NEAR-ZERO** ✗ |
| `eff_shield_damage_mult` | hardenedshieldemitter only | **NEAR-ZERO** ✗ |

Specific usage percentages from V1 logs are pending re-validation under V2 (see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md)); the variance ranking is design-grade based on hullmod-population logic.

`eff_max_speed` and `eff_shield_damage_mult` are defensible *in theory* (speed governs engagement-range control, shield efficiency multiplies shield EHP in cross-hull runs), but **within a per-hull run** they are effectively hull-constants. `eff_hull_hp` sits on the margin — Reinforced-Bulkheads + Assault-Package partial bonus yields some spread, borderline informative — but the low-confidence variance argues for dropping in the initial ship set. All three can return if the design generalizes to cross-hull aggregation.

The three surviving engine features — `eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating` — each have well-established continuous variance from raw build primitives (capacitors, vents) plus the scattered effects of the exploit-cluster hullmods (shrouded_lens, escort_package, neural_integrator, assault_package). The Java engine's authoritative hullmod-effect computation captures these cleanly; our Python `compute_effective_stats` would miss the 50+ unmodeled hullmods' contributions.

#### The 7-feature ship set

Targeting p = 7 after the variance audit. Pending re-validation under V2 loadout fix; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

| # | Feature | Source | Tier |
|---|---|---|---|
| 1 | `eff_max_flux` | Java `MutableShipStats.getMaxFlux()` at SETUP | Engine-computed |
| 2 | `eff_flux_dissipation` | Java `MutableShipStats.getFluxDissipation().getModifiedValue()` | Engine-computed |
| 3 | `eff_armor_rating` | Java armor grid + stat bonuses, post-hullmod | Engine-computed |
| 4 | `total_weapon_dps` | Python — raw sum of equipped-weapon DPS, no type weighting | Python raw |
| 5 | `engagement_range` | Python `ScorerResult.engagement_range` (DPS-weighted mean) | Python raw |
| 6 | `kinetic_dps_fraction` | Python `kinetic_dps / max(total_dps, ε)` — hard-flux pressure axis | Python raw |
| 7 | `composite_score` | `ScorerResult.composite_score` | Python composite (calibrated) |

Coverage of information axes: flux-cap (1), flux-sustain (2), armor-defense (3), offense magnitude (4), engagement style (5), hard-flux pressure (6), calibrated ensemble (7). Missing axes — hull-HP, speed, shield — are hull-constants in per-hull runs and would add variance only in cross-hull contexts.

#### Why each Python-raw feature (4–6) is admissible

- **`total_weapon_dps`** — sum over equipped weapons of their sustained DPS (`damage_per_shot × burst_size / cycle_time`). No type weighting, no range gating, no burst adjustment. A pure sum of game-data primitives; admissible as a raw aggregate.
- **`engagement_range`** — DPS-weighted mean weapon range, already computed by `scorer.py:90–95`. Predicts kite-vs-brawl dynamics; high variance from weapon choice plus targeting-unit hullmods (16% usage). "DPS-weighted mean" is a pure statistic of the build's weapon list, not a hand-picked composite.
- **`kinetic_dps_fraction`** — the ratio of kinetic DPS to total DPS. Kinetic damage forces hard flux (per game-mechanics.md §5), which dissipates only when shields are down and soft flux is cleared. A build dealing 80% kinetic pressures flux very differently than one dealing 20% kinetic, even at identical `total_weapon_dps`. Added to recover the hard-flux axis the earlier 8-feature set missed.

#### What's dropped from all prior candidate sets

| Dropped | From | Reason |
|---|---|---|
| `kinetic_dps`, `he_dps`, `energy_dps`, `fragmentation_dps` | 16-feature v1 | Type decomposition is a theory; `total_weapon_dps` + `kinetic_dps_fraction` covers the relevant axes |
| `flux_balance`, `flux_efficiency` | 16-feature v1 | Subsumed by engine-computed flux capacity + dissipation |
| `effective_hp`, `armor_ehp`, `shield_ehp` | 16-feature v1 | Subsumed by engine-computed armor (+ eff_hull_hp/shield_eff dropped for variance) |
| `range_coherence`, `damage_mix`, `op_efficiency` | 16-feature v1 | Hand-weighted composites; conservative drop |
| `flux_vents`, `flux_capacitors` | 16-feature v1 | Baked into `eff_max_flux` / `eff_flux_dissipation` |
| `n_hullmods` | 8-feature v2 | Hammerhead empirical ρ with fitness was approximately zero (no signal); precise value pending re-validation. Type matters, not count, and type-indicators would bake exploit mods into the prior |
| `op_used_fraction` | 8-feature v2 | Repair operator greedily fills OP; observed 0.95–1.0 range → near-zero variance |
| `eff_hull_hp` | 9-feature v3 | Only 1 Python-modeled HP hullmod; Assault Package provides some variance but it's an exploit-cluster mod |
| `eff_max_speed` | 9-feature v3 | Few builds in the V1 corpus used the sole speed-modifying hullmod, Safety Overrides (precise count no longer cited; corpus invalidated by V1 loadout bug) |
| `eff_shield_damage_mult` | 9-feature v3 | Few builds in the V1 corpus used Hardened Shields (precise count no longer cited; corpus invalidated by V1 loadout bug); `advancedshieldemitter` modifies turn rate, not efficiency |

#### Expected Δρ at p = 7

The feature-count sweep projected positive Δρ(EB − A0) at p = 7 across the targeted N range. Specific magnitudes are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

#### When to revisit

- **Add back `eff_hull_hp`, `eff_max_speed`, `eff_shield_damage_mult`** for cross-hull aggregate runs where they regain real variance. Per-hull runs remain at p = 7.
- **Add `mean_damage_per_shot`** (DPS-weighted) only if ship-gate is tight post-validation and armor-penetration burst matters empirically — this is mild theory injection and is a last resort.
- **Drop to p = 6** (remove `kinetic_dps_fraction`) only if production runs routinely land at small N.

---

## 3. Integration plan

### 3.1 Code changes

**Python side:**

- **`src/starsector_optimizer/deconfounding.py`** — extend with:
  - `TWFEResult` named tuple returning `(alpha, beta, sigma_i)`, where `sigma_i` comes from pooled residual MSE / n_i.
  - `eb_shrinkage(alpha, sigma_i, X_build, tau2_floor_frac=0.05) → (alpha_eb, gamma, tau2)`: the two-level EB closed-form from §2.2.
  - `triple_goal_rank(posterior, raw) → alpha_ebt`: the one-line histogram substitution from §2.4.
- **`src/starsector_optimizer/optimizer.py`** — delete `_apply_control_variate`, `_refit_control_variate`, `_cv_beta`, `_cv_heuristic_mean`. Replace with one call to `eb_shrinkage` at `_finalize_build`. Route the build's 7-dim feature vector from the trial params + parsed `EngineStats`.
- **`src/starsector_optimizer/combat_fitness.py`** — expose per-build pre-matchup feature vector combining `ScorerResult.composite_score`, `total_weapon_dps`, `engagement_range`, `kinetic_dps_fraction` (new — simple ratio of existing per-type DPS) with the 3 engine-computed stats from `EngineStats`.
- **`src/starsector_optimizer/result_parser.py`** — add `EngineStats` dataclass (`eff_max_flux`, `eff_flux_dissipation`, `eff_armor_rating`) and parse the new `setup_stats` block from the matchup result JSON.
- **`src/starsector_optimizer/models.py`** — add `ShrinkageConfig` frozen dataclass with `enable: bool = True`, `tau2_floor_frac: float = 0.05`, `triple_goal: bool = True`. Replace the scalar `_cv_beta` fields in `OptimizerConfig` with this.
- **`docs/specs/28-deconfounding.md`** — extend to describe the EB shrinkage step with σ_i estimation and the triple-goal correction.
- **`docs/specs/09-combat-protocol.md`**, **`docs/specs/12-result-writer.md`**, **`docs/specs/13-combat-harness-plugin.md`** — extend `MatchupResult` schema with `setup_stats` block; document the SETUP-phase emit.

**Java side (new, required for features 1–4):**

- **`combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`** — at end of SETUP state (before the transition to FIGHTING), read from the player ship's `ShipAPI`:
  - `ship.getMutableStats().getMaxFlux().getModifiedValue()` → `eff_max_flux`
  - `ship.getMutableStats().getFluxDissipation().getModifiedValue()` → `eff_flux_dissipation`
  - Summed armor across the `ArmorGridAPI` (or bonus-adjusted base) → `eff_armor_rating`

  Stash the 3-tuple on the matchup state. (Hull-HP, speed, and shield-efficiency reads are deferred; they have near-zero empirical variance in per-hull runs — see §2.7 variance audit.)
- **`combat-harness/src/main/java/starsector/combatharness/ResultWriter.java`** — extend the emitted JSON with `"setup_stats": {eff_max_flux, eff_flux_dissipation, eff_armor_rating}` alongside the existing combat outcomes.
- Java tests and integration test covering the new emit (spec-driven — see `.claude/skills/ddd-tdd.md`).

No new Python runtime dependencies. `scipy.linalg.lstsq` + NumPy is sufficient. Estimated code size: ~80 lines net in `deconfounding.py`, ~15 lines deleted from `optimizer.py`, ~30 lines total Java (CombatHarnessPlugin + ResultWriter + test), ~20 lines Python parser + EngineStats dataclass, ~30 lines of spec updates.

### 3.2 Tests

- Unit: synthetic 2-level model with known `γ`, `τ²` — verify recovery of shrinkage weights and posterior mean within tolerance.
- Unit: degenerate cases — all `σ̂_i² → 0` (no shrinkage), all `σ̂_i² → ∞` (full shrinkage), `τ̂² = 0` floor behavior.
- Unit: `triple_goal_rank` preserves exact rank ordering; histogram equals the empirical α̂ histogram by construction.
- Integration: replay on a per-hull `evaluation_log.jsonl` from a post-V2 production campaign and verify mean α̂_EBT matches an in-tree fusion-validation reference (re-validation pending; see [../reports/INDEX.md](../reports/INDEX.md)).
- Regression: ablation on three pipelines — A0 plain TWFE, A shipped scalar CV, A1+A2' new EB — on the same log. Confirm LOOO ship-gate Δρ ≥ +0.02 vs both A0 and A.

### 3.3 Ship gate

Phase 5D is gated on **leave-one-opponent-out** rank correlation against a per-hull log, with the top-5 most-sampled anchor opponents as probes:

- Fit A0, A, A2' (new EB) on the log minus probe opponent.
- Measure Spearman ρ between the refit α̂ and the probe's raw `hp_differential` across all non-pruned builds.
- Require **Δρ(EB − A0) ≥ +0.02 AND Δρ(EB − A) ≥ +0.02** — both relative to plain TWFE *and* relative to the shipped scalar CV. Strict improvement on both required; the shipped A2 itself is not a safe floor (see §4.5).

Observed gate values from V1 fusion validation are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md).

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

**Why rejected — empirical refutation.** A synthetic sweep and a real-data ship-gate on the V1 Hammerhead evaluation log both showed the conditioning paradigm fails the ship gate by a wide margin. Specific Δρ magnitudes are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md). The qualitative result — conditioning paradigm catastrophically below the ship gate, fusion paradigm comfortably above it — is structural and confirmed by the closed-form derivation below.

**Why rejected — causal diagnosis.** The conditioning paradigm treats `X` as an exogenous covariate of the outcome Y and partials it out. This is valid when X is correlated with *noise* in Y (CUPED's A/B-testing use case, where pre-period user metrics are correlated with user-specific noise but orthogonal to treatment randomization). It is invalid when X is correlated with *the estimand*. Here X is correlated with α itself (the scorer components are predictive signals of combat quality) — Cinelli, Forney & Pearl 2022 arXiv:2106.10314 name this "Case 8: proxy of the treatment," a bad-control pattern. Conditioning on a noisy measurement of the estimand biases the coefficient on the estimand and, in the between-build projection, removes the signal α̂ shares with X.

Closed-form derivation: in the scalar-h case, if `h_i = c₀ + c₁ α_i + ν_i` and `R = c₁² Var(α) / (c₁² Var(α) + σ_ν²)` is h's reliability as a measurement of α, then after CUPED adjustment

```
ρ(α̂_CUPED, α) = √(1 − R)
```

compared to ρ(α̂, α) = 1 for plain TWFE. The more useful the heuristic, the more CUPED damages the rank correlation. This derivation is paradigm-level and unaffected by the V1 invalidation — the structural result holds regardless of the specific h reliability observed empirically.

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

**Why rejected.** CFA performed strongly on synthetic data but failed the ship gate on real Hammerhead. The one-factor assumption — that every indicator is a rescaled noisy measurement of the same underlying α — is approximately correct in the synthetic generative model but violated in real data, where scorer components measure genuinely different aspects of a build (kinetic-DPS vs shield-eHP vs flux-efficiency are not one-dimensional). The EB shrinkage in §2.1 makes a strictly weaker structural assumption (`α_i = γᵀ X_i + residual`, no factor structure on X) and is robust to indicator heterogeneity. Specific synthetic-vs-real Δρ magnitudes pending re-validation under V2.

A multi-factor CFA with k > 1 latent factors might recover performance, but at that point the simplicity advantage over §2 vanishes and the closed-form OLS prior suffices.

### 4.7 Inverse-variance combining without τ² shrinkage — CLOSE ALTERNATIVE

**What it was.** Graybill & Deal 1959 *Biometrics* 15(4):543 — combine α̂_TWFE and γ̂ᵀX by inverse variance:

```
α̂_IV_i = (α̂_i/σ̂_i² + ĥ_i/σ̂_h²) / (1/σ̂_i² + 1/σ̂_h²),   ĥ_i = γ̂ᵀ X_i
```

Passes the V1 ship gate but is systematically weaker than §2 EB. The difference: IV does not separate τ² (between-build variance around the prior) from `σ̂_h²` (OLS fit quality). When the OLS fit over-explains α̂ in small samples, IV under-weights the data. The EB method-of-moments τ̂² estimator corrects this — at the cost of a slightly more complex recipe. Specific magnitudes pending re-validation under V2.

Kept as a reference fallback if diagnostic evidence shows the MoM τ̂² estimate is unstable in production.

### 4.8 EB with PDS-selected prior regression — CLOSE ALTERNATIVE

**What it was.** Apply post-double-selection lasso (Belloni-Chernozhukov-Hansen 2014) *inside the prior regression* to select a subset of the columns before forming `γ̂ᵀ X_i`.

**Why not currently used.** PDS retained essentially all columns in both synthetic and real replays at the explored covariate counts — providing identical α̂_EB as §2 without selection, at ~200× the wall time (LassoCV cross-validation cost). Retain as a no-op default; promote only if the covariate pool grows substantially.

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

- [phase5a-deconfounding-theory.md](phase5a-deconfounding-theory.md) — TWFE decomposition theory (six-field literature synthesis).
- [phase5-signal-quality.md](phase5-signal-quality.md) — original Phase 5A/5B foundational research.
- [phase5c-opponent-curriculum.md](phase5c-opponent-curriculum.md) — opponent-selection design (anchor-first + incumbent-overlap).
- [phase5e-shape-revision.md](phase5e-shape-revision.md) — A3 rank-shape revision (Box-Cox).
- [implementation-roadmap.md](implementation-roadmap.md) — phase overview and status.
- [../specs/28-deconfounding.md](../specs/28-deconfounding.md) — implementation spec.
- [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) — the V1 loadout-bug invalidation that retired the original `experiments/phase5d-covariate-2026-04-17/` directory.
- `src/starsector_optimizer/deconfounding.py`, `src/starsector_optimizer/optimizer.py`, `src/starsector_optimizer/result_parser.py` — production Python code.
- `combat-harness/src/main/java/starsector/combatharness/CombatHarnessPlugin.java`, `ResultWriter.java` — production Java code for the `setup_stats` emit.

---

## 5. Implementation notes (2026-04-18)

### Code pointers

- `src/starsector_optimizer/deconfounding.py::eb_shrinkage` — pure function implementing §2.2 closed-form posterior mean. NumPy-only; no `scipy.linalg` dependency (the module was NumPy-only before 5D and stays that way).
- `src/starsector_optimizer/deconfounding.py::triple_goal_rank` — pure function for §2.4 rank correction.
- `src/starsector_optimizer/deconfounding.py::ScoreMatrix.build_sigma_sq` — exposes σ̂_i² = σ̂_ε² / n_i using the cached pooled residual MSE. Raises `ValueError` if called before `build_alpha()` populated the cache.
- `src/starsector_optimizer/optimizer.py::_apply_eb_shrinkage` — orchestrates per-trial shrinkage at `_finalize_build()`.
- `src/starsector_optimizer/optimizer.py::_build_covariate_vector` — assembles the 7-dim X_i from `_EBRecord` (scorer + engine_stats).
- `src/starsector_optimizer/optimizer.py::_completed_records: dict[int, _EBRecord]` — narrow finalized-build cache (~10× smaller than retaining full `_InFlightBuild` objects).

### §3.1 file-placement corrections

The design doc's §3.1 list listed two placements that were revised during implementation. The §2 math is unchanged; only file placements differ:

1. **`EngineStats` lives in `models.py`**, not `result_parser.py`. All frozen domain dataclasses live in `models.py` (project convention, see Design Principle 2). `CombatResult` gets a new optional `engine_stats: EngineStats | None = None` field.
2. **Covariate assembly (`_build_covariate_vector`) lives in `optimizer.py`**, not `combat_fitness.py`. Rationale: the vector requires `scorer_result` and `engine_stats`, both of which are optimizer-owned state — the scorer output plus the Java SETUP read. `combat_fitness.py` remains a pure scalar function of one `CombatResult` (single responsibility).

### `OptimizerConfig` — removed CV fields, added `eb` sub-config

Removed: `cv_min_samples`, `cv_rho_threshold`, `cv_recalc_interval` (shipped Phase 5A scalar CV plumbing).
Added: `eb: EBShrinkageConfig = field(default_factory=EBShrinkageConfig)`, sibling to `combat_fitness` and `twfe` — one config class per concern.

`EBShrinkageConfig` fields: `tau2_floor_frac=0.05`, `triple_goal=True`, `eb_min_builds=8`, `ols_ridge=1e-4`.

### Java-side `setup_stats` emission — NaN policy revised

The original plan called for always-emit with `Float.NaN` signaling failed reads. The game's bundled org.json rejects NaN in `put()`, so this was infeasible. Revised: the `setup_stats` block is emitted only when all three reads succeed (`!Float.isNaN(...)` check). If any read fails the key is OMITTED and Python treats absence as `engine_stats=None` (same as pre-5D log replay). Failed reads should never happen in production since SETUP runs right after a successful loadout swap — `MutableShipStats` and `HullSpec` are live.

### Engine/Python fallback (measurement-source confound risk)

`_build_covariate_vector` falls back to `ScorerResult.effective_stats` when `record.engine_stats is None`. This is a *replay / test-fixture* convenience; in production (deployed Java mod) `engine_stats` is always populated, so this branch never triggers. Mixing Java-sourced and Python-sourced rows in one X matrix would introduce a measurement-source confound that biases γ̂. `eb_min_builds=8` gates early trials before enough Java rows arrive; a long-lived miss (mod absent for many trials) would warrant a WARN that the operator can act on.

### Java API verification

`MutableShipStatsAPI.getFluxCapacity()` (NOT `getMaxFlux()` — this method does not exist) and `MutableShipStatsAPI.getArmorBonus().computeEffective(hullSpec.getArmorRating())` were confirmed via `javap -cp game/starsector/starfarer.api.jar …` on 2026-04-18. The `StatBonus.computeEffective(float base)` accessor applies flat + percent + mult bonuses to the base hull-spec rating; this is the canonical way to read effective armor, superseding the original plan's grid-sum approach (which would have read current damage state rather than rated armor).

### Replay-gate finding (2026-04-18) — pending re-validation under V2

The pre-5D Hammerhead log replay through the shipped production code at p=7 with Python-fallback for the engine-stat columns showed a Δρ smaller than the synthetic sweep projection. The gap is attributable to two structural effects (both unaffected by the V1 invalidation):

1. **Python fallback** for the 3 engine-stat columns: pre-5D logs lack `setup_stats`, so `_build_covariate_vector` falls back to `ScorerResult.effective_stats.{flux_capacity, flux_dissipation, armor_rating}`. These values miss the contributions of the ~50 hullmods our Python model does not track and are therefore less informative than Java-authoritative `MutableShipStats` reads.
2. **Information overlap in real data**: synthetic sweep features carry independent signal by construction. Real-data features (e.g. `composite_score`, `engagement_range`) overlap, so each additional column adds less than the sweep projects.

**Implication**: the ship gate is expected to clear only after a production run collects authoritative Java `engine_stats`. Specific Δρ numbers from the V1 replay (and from the synthetic sweep projection) are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md).

**What to do next**: re-run the Hammerhead overnight campaign under V2 with the deployed mod. Early trials exercise the Python fallback; later trials with `engine_stats` populated close the gap.

---

## 7. TTK-signal investigation (2026-04-18)

**Status**: benchmarked under V1; conclusions are pending re-validation under V2. Not shipped. The investigation is preserved here for design rationale and the rejected-alternative chain; specific Δρ tables are stripped because they were measured against V1 sim runs. See [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

### 7.1 Question

Should `duration_seconds` (combat time, right-censored at the harness timeout) enter the EB prior as an 8th covariate — either raw, as a pre-battle projection `log(effective_hp / total_dps)`, or as a Weibull-AFT residual — or alternatively be added as a lexicographic ε-tiebreaker inside `Y_ij`?

### 7.2 Literature triangulation

- **Causal inference** (Cinelli-Forney-Pearl 2022 Model 17; Rosenbaum 1984; Montgomery-Nyhan-Torres 2018): raw duration is a *descendant of Y* — the canonical "bad control" pattern that wrecked 5D v1. Placebo testing follows Eggers-Tuñón 2024 (AJPS doi 10.1111/ajps.12818).
- **Survival analysis** (Cragg 1971 hurdle; Atem et al. 2017 censored covariates; Collett AFT residuals): informative right-censoring at the timeout requires hurdle or AFT-residualization; naive substitution is biased.
- **Empirical Bayes methodology** (Morris 1983 self-correction; Riley et al. 2019 min-n rules): τ̂²-floor + ridge auto-regularization make moderate covariate growth safe; the hazard is noisy-covariate attenuation (Armstrong-Kline-Sun 2025), not degrees-of-freedom.
- **Multi-objective literature** (Miettinen 1999; Cococcioni-Pappalardo-Sergeyev 2018; SSCAIT/AIIDE tournament precedent): lexicographic (outcome ≫ duration) is the formally correct framing if "duration matters infinitesimally less than outcome." Weighted sum has known non-convex-front pathologies; multi-objective BO (qEHVI) is a major rework.
- **Game-AI precedent** (OpenAI Five, AlphaStar, FTW, DareFightingICE): time enters via the RL discount factor γ, not as a reward term. Blizzard/Riot treat TTK as a *diagnostic*, not a *target*. Lanchester square law argues for TTK as an attrition quality but breaks for alpha-strike regimes.
- **Starsector mechanics** (PPT + CR decay + "hard fought" recoup + fleet-scale amortization): TTK carries real *campaign-layer* value the sim cannot otherwise see. Hammerhead is a quick-kill/burst/SO-brawler archetype, so its TTK is an especially good α-mediator; attrition-oriented hulls (HEF Paragon, armor-tank Onslaught) may not share this property.

### 7.3 Empirical benchmark — qualitative

The V1 benchmark showed two regimes with opposite conclusions:

- **Calibration (sparse, large-N)**: EB7 saturates the available pre-battle signal; the 8th-covariate family lands inside the noise band.
- **Production-like (dense, smaller-N per build)**: EB shrinkage has room for an additional informative covariate; duration and its derivatives deliver significant lift.

Specific Δρ values pending re-validation under V2.

### 7.4 Synthetic multi-hull stress test

A regime grid (clean / collider / mediator duration generation × N × SNR × R²) showed duration helps across the (N, SNR, R²) grid **only in non-null regimes**. The null "clean" regime produces minimal Δρ loss — the bad-control hazard predicted by Case-17 theory does not catastrophically materialize in this EB setup because build-mean aggregation over many matchups shrinks the per-matchup ε-noise component, leaving the α-mediator signal dominant. Specific Δρ values pending re-validation under V2.

### 7.5 Lexicographic ε-tiebreaker — uniformly negative

`Y_new = Y − ε·(duration/timeout)` lost Δρ vs ε=0 across all explored ε, both real logs, and all estimators in the V1 benchmark. The tiebreaker does not compose with EB shrinkage when `hp_differential` is already a continuous score with no hard-tier structure to break. **Rejected.**

### 7.6 Implications for the 5D-as-shipped p=7 set

1. The single-hull data disagreed across regimes, so the shipped p=7 choice is *neither validated nor refuted* by this investigation. On the calibration log p=7 was saturating; on the overnight log p=7 was the weaker starting point and p=8 would have helped.
2. The §4.5 rejection of the conditioning-paradigm v1 still holds: v1 used covariates that were *transformations of the scorer* (i.e., scorer-component-derived Y-descendants), which is a strictly worse bad-control pattern than duration. Duration's build-mean-aggregation makes its ε-collider leakage small; scorer-transform covariates have no such protection.
3. The **"exclude post-matchup features by construction"** rule of §2.5 remains the safe default. Duration is not admitted unconditionally — only after hull/regime-conditioned validation.

### 7.7 Recommendation

- **Do not ship** TTK-related covariates or the lexicographic tiebreaker unconditionally. Keep EB7 shipped.
- **Route via Phase 5F**: treat duration (or its log-TTK pre-battle proxy) as a candidate *regime-conditioned* covariate. Archetypes where TTK is a strong α-mediator (quick-kill hulls, SO brawlers) opt in; attrition archetypes opt out.
- **Prerequisite for opt-in**: a live overnight V2 campaign on at least one non-Hammerhead hull covering a different archetype (e.g., Paragon or Onslaught for attrition) with a per-hull `RegimeConfig`-gated EB covariate toggle. The Eggers-Tuñón placebo should be run automatically as part of the ship gate.
- **Defer**: Weibull-AFT residual, Heckman two-step — complexity cost exceeds the demonstrated marginal benefit.
- **Defer**: multi-objective / Pareto / qEHVI — the single-scalar formulation is adequate given lexicographic is rejected and weighted-sum has the usual pathologies.

### 7.8 Open questions

- Does the small-N production-like regime gain reproduce under V2 across multiple hulls?
- Does the archetype hypothesis generalize? What is Δρ(EB8_dur − EB7) on an *attrition* hull's production-sized V2 log?
- Is the placebo test a useful *runtime* monitor — could `_apply_eb_shrinkage` compute partial-corr(duration, α̂ | X) on each update and emit a warn if it drifts below a threshold?
- Can we emit any of the other orphaned signals (`overload_count`, `peak_time_remaining`, AI flags) in a pre-battle-projected form that clears the Case-17 admissibility bar?
