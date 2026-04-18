# Phase 5D TTK-signal benchmark — 2026-04-18

## Bottom line — REVISED

**Two regimes emerge, and production sits in the one where duration helps.**

| Regime | Data | n | opps/build | Δρ(EB8_dur − EB7) | Ship decision |
|---|---|---|---|---|---|
| **Production-like** | overnight log 2026-04-13 | 56 | 10 (dense) | **+0.136 [+0.079, +0.194] sig** | EB8_dur or EB9_ttk_dur worth serious consideration |
| **Saturated** | calibration log 2026-04-13 | 485 | ~6 (sparse) | +0.004 [−0.003, +0.010] NS | EB7 already captures signal |

The synthetic multi-hull stress test (162 scenarios) confirms: when `duration` is an α-mediator (carries latent-quality info with limited realization noise after build-mean aggregation), adding it to the EB prior gives +0.01 to +0.03 Δρ vs EB7 across the full (n, opps, SNR, R²(X7)) grid — even at n=100. It hurts only in the "clean" null regime, and even there only by <0.002 Δρ.

**Revised recommendation: ship EB8_dur (or EB9_ttk_dur) in production** — but gate it with a live replay on a second hull before rolling out. The Hammerhead calibration-log "no gain" result is likely an n=485 saturation artifact, not a production signal.

**Still reject: lexicographic ε-tiebreaker.** Consistently loses 0.005–0.12 Δρ across both real logs and all ε values.

## Why the two regimes disagree

**Calibration log (n=485, sparse)**: X7 is aggregated across 485 builds; τ̂² from MoM is well-determined; γ̂ᵀX already explains most of Var(α̂). Adding an 8th covariate gives the prior nowhere new to go. The 8th-covariate family lands inside a ±0.01 noise band around EB7.

**Overnight log (n=56, dense)**: X7 estimation is noisy (n/p = 8, at the one-in-ten rule edge), build-level α̂ has high σ̂_i, and EB's weight w_i = τ̂²/(τ̂²+σ̂_i²) slides deep into shrinkage territory. Any additional informative covariate substantially sharpens the prior. Duration, projected TTK, and AFT residual all provide significant lift; raw duration gives the biggest (+0.14 Δρ).

**Production runs sit at n ≈ 200–400 per hull with 8–10 opps/build** → closer to overnight regime than to calibration. The production-relevant benchmark therefore favors admitting duration.

## Results by log

### Overnight log (production-like, n=56)

```
Mean ρ across 10 probes:
  A            +0.1849
  A0           +0.1929
  EB7          +0.1484      ← shipped
  EB8_ttk      +0.1517   (+0.003 vs EB7, NS)
  EB8_aft      +0.2525   (+0.104 vs EB7, sig)
  EB8_dur      +0.2847   (+0.136 vs EB7, sig)
  EB9_ttk_aft  +0.2659   (+0.118 vs EB7, sig)
  EB9_ttk_dur  +0.2924   (+0.144 vs EB7, sig)
```

Placebo (n=56): partial corr(duration, α̂ | X7) = **−0.631 (p = 1.8×10⁻⁷)** — very strong post-treatment contamination. The gain nonetheless persists.

### Calibration log (saturated, n=485)

```
Mean ρ across 10 probes:
  A            +0.1238
  A0           +0.1357
  EB7          +0.1543      ← shipped
  EB8_ttk      +0.1554   (+0.001 vs EB7, NS)
  EB8_aft      +0.1511   (−0.003 vs EB7, NS)
  EB8_dur      +0.1581   (+0.004 vs EB7, NS)
  EB9_ttk_aft  +0.1524   (−0.002 vs EB7, NS)
  EB9_ttk_dur  +0.1586   (+0.004 vs EB7, NS)
```

Placebo (n=485): partial corr(duration, α̂ | X7) = −0.098 (p = 0.032) — weaker, as X7 absorbs most of α's variance.

## Synthetic multi-hull stress test (162 scenarios)

Grid: n ∈ {100, 250, 500}, k ∈ {10, 30}, SNR ∈ {0.3, 1.0, 3.0}, R²(X7→α) ∈ {0.2, 0.5, 0.8}, duration_regime ∈ {clean, collider, mediator}. 10 reps per cell. Metric: Δρ(estimator − A0) against *true* α (ground truth available under synthetic generation).

```
Mean Δρ vs A0, averaged across all configs, by regime:
             EB7   EB8_dur  EB8_ttk  EB9_ttk_dur
clean       0.004   0.004    0.004    0.004       ← null: no effect
collider    0.004   0.014    0.004    0.014       ← +0.010 Δρ from duration
mediator    0.004   0.025    0.004    0.025       ← +0.021 Δρ from duration
```

**Interpretation:** duration "helps more when more contaminated" — because the synthetic mediator regime encodes pure α-information (no ε noise leakage). Real per-matchup duration is a mix: an α-mediator with ε-collider contamination; build-mean aggregation over ~7–10 matchups shrinks the ε-collider component ~√N×, leaving mostly α-mediator signal. This explains why raw duration works empirically despite flunking the Eggers-Tuñón placebo: the "bad control" flag is technically correct but practically dominated by mediator signal after aggregation.

**The synthetic shows this generalizes across n ∈ {100, 250, 500}, SNR ∈ {0.3, 3.0}, and R²(X7) ∈ {0.2, 0.8}** — 10 worst-case scenarios for EB8_dur are all in the "clean" (null) regime with Δρ loss < 0.002. No configuration produces catastrophic failure.

## What was tested

| Variant | Definition | Pre/Post-battle |
|---|---|---|
| A0 | TWFE α̂ only | – |
| A | TWFE + shipped scalar CV | – |
| **EB7** | TWFE + EB shrinkage with 7 pre-battle covariates (shipped) | pre |
| EB8_dur | EB7 + raw build-mean `duration_seconds` | **post** (negative control) |
| EB8_ttk | EB7 + log(effective_hp / total_dps) | pre |
| EB8_aft | EB7 + Weibull-AFT log-duration residual | post (residualized) |
| EB9_ttk_dur | EB7 + projected TTK + raw duration | mixed |
| EB9_ttk_aft | EB7 + projected TTK + AFT residual | mixed |
| *_LEX{ε} | Trained on `Y − ε · (duration / 300)` | – |

## Lexicographic ε-tiebreaker (uniformly negative)

Across both real logs and all ε ∈ {0.001, 0.01, 0.1}: LEX variants lose 0.005–0.12 Δρ vs their ε=0 counterparts. The within-tier tiebreaker does not compose with EB shrinkage, and `hp_differential` (which has continuous [-1, 1] tier structure) does not need a tiebreaker layered on top. **Reject lexicographic.**

## Generalization caveats

1. **Only one hull empirically tested.** No evaluation log exists for any non-Hammerhead hull in this repo. Both real-data points are Hammerhead under different run configurations. The synthetic stress test fills the hull-diversity gap but cannot substitute for live per-hull replication.

2. **Hull archetype matters for duration signal.** Agent 8 (Starsector mechanics research) established Hammerhead is a quick-kill / burst / SO-brawler archetype — its combat duration should be an especially good α-mediator. Attrition-oriented hulls (HEF Paragon, armor-tank Onslaught) plausibly have weaker duration→α coupling and could trend toward the synthetic "clean" null regime. Live validation on a representative attrition hull (e.g., Paragon or Onslaught) is needed before rolling out EB8_dur universally.

3. **Regime-dependent weighting (5F alignment).** If TTK importance is hull- or regime-dependent as Starsector mechanics research argues (stronger in early/mid, weaker in endgame vs Remnants), the correct integration is via Phase 5F `RegimeConfig` rather than a flat 8th covariate. A regime-segmented EB-covariate mask would let TTK contribute when useful and stay dark when it isn't.

4. **Placebo flags a theoretical hazard.** The Eggers-Tuñón partial-correlation test rejects admissibility at both n (−0.098 and −0.631). This is the Case-17 bad-control pattern doing exactly what Cinelli-Forney-Pearl predict it should do. The empirical survival is due to build-mean aggregation; in settings where the aggregation breaks down (e.g., single-matchup prediction, or very noisy per-matchup duration on new hull archetypes), the hazard could reassert itself.

## Decision framework

- **If we had live data for 3+ hulls**: ship EB8_dur if it wins on ≥2 of them, else keep EB7.
- **Given current evidence (Hammerhead + synthetic stress test)**: route to Phase 5F regime-conditioning. Make duration an opt-in covariate gated by hull / regime archetype rather than a universal prior feature.
- **Immediate conservative action**: keep EB7 shipped unchanged. Collect a live overnight run on a second hull (e.g., Sunder, Medusa, or a capital like Paragon) and re-run this benchmark before committing to duration.
- **Immediate aggressive action** (if willing to accept OOD risk for ~0.1 Δρ on production-sized runs): ship EB9_ttk_dur with an Eggers-Tuñón placebo monitor in the live optimizer that would flag if partial-correlation shifts dramatically, and a rollback to EB7 if the placebo hits some threshold.

## Files

- `ttk_signal_benchmark.py` — configurable via `TTK_BENCHMARK_LOG` env var
- `synthetic_multihull_benchmark.py` — 162-scenario synthetic stress test
- `ttk_benchmark_{results,sig,summary,subsample}_calibration.*` — calibration log (n=485)
- `ttk_benchmark_{results,sig,summary}_overnight.*` — overnight log (n=56)
- `synthetic_multihull_{results,summary}.*` — synthetic grid

## References

- Cinelli, Forney, Pearl 2022, *Sociological Methods & Research*, doi 10.1177/00491241221099552 — Model 17 (bad proxy of Y) taxonomy.
- Rosenbaum 1984, *JRSS-A* 147(5):656 — post-treatment conditioning bias.
- Eggers, Tuñón 2024, *AJPS*, doi 10.1111/ajps.12818 — placebo-outcome test recipe.
- Morris 1983, *JASA* 78(381) — parametric-EB auto-regularization (τ̂² floor absorbs mis-specified γ̂).
- Riley, Snell, Ensor et al. 2019, *Stat. Med.* — minimum sample size for prediction models; p=8 safe at n≥80 under shrinkage.
- Armstrong, Kline, Sun 2025, arXiv:2503.19095 — EB does not auto-correct measurement error in noisy covariates.
