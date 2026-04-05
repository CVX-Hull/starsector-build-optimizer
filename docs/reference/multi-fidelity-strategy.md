# Multi-Fidelity Evaluation Strategy

This document covers the two-tier evaluation pipeline, surrogate model design, noise handling, and adaptive replication strategies.

**Updated based on Phase 3.5 + Phase 4 research findings.** Key change: "short sim" fidelity level removed — empirically shown to corrupt optimizer signal. Pipeline is now heuristic + full sim with curtailment.

---

## Two-Tier Fidelity Hierarchy

| Fidelity | Method | Cost | Accuracy | Use |
|---|---|---|---|---|
| **0 — Heuristic** | Static metrics from game data | ~0ms | R² ≈ 0.49 with sim | Screen 100K+ candidates/second |
| **1 — Full Sim** | 180s game time, 5x speed, curtailment | ~22-35s wall-clock | Ground truth (noisy) | Optimization + validation |

### Why NOT Three Tiers (No Short Sim)

Phase 3.5 research proved short simulations corrupt the optimizer:

| Timeout | Timeout rate (cruiser) | Rank correlation | Convergence penalty |
|---|---|---|---|
| 60s | 100% | N/A (flat) | +20% iterations (+18h) |
| 120s | 94% | 0.65 | +7% iterations (+6h) |
| 180s | 46% | 0.93 | +2% iterations (+2h) |
| 300s | 0.3% | 0.87 | Baseline |

A "short sim" at 15-30s game time would have near-100% timeout rate, producing a flat fitness landscape. The approach time alone (ships closing from 4000 units) consumes ~6s wall-clock at 5x. By the time ships engage, there's barely time for meaningful combat.

**Curtailment replaces short sim for time savings.** Decisive fights (Onslaught vs Lasher) end naturally in ~47s game-time. Curtailment stops one-sided fights 12-24% faster. The fights that run long are the close, interesting ones where you *need* the full duration for a meaningful signal.

### Where the Heuristic Works and Fails

**Works well (captured by static metrics):**
- Flux balance — universally predictive
- Gross capability (DPS, EHP) — rank-orders builds reasonably
- Obvious bad builds — clearly under-armed or over-fluxed builds score poorly

**Fails (emergent from combat dynamics):**
- Weapon synergies (kinetic + HE coverage) — emergent, not captured by sum of DPS
- AI behavior adaptation — the AI manages flux, selects targets, toggles shields
- Range dynamics — mismatched ranges cause idle weapons, invisible to static metrics
- Armor penetration — the nonlinear damage formula favors high per-shot damage
- Safety Overrides interactions — the speed+range tradeoff plays out in positioning

**R² ≈ 0.49 is below the 0.75 threshold for reliable MFBO** (per best-practices paper, arXiv 2410.00544). Full MFBO (jointly modeling heuristic + sim) can actually perform *worse* than single-fidelity when low-fidelity sources are poor approximations. This is why we use the heuristic as a warm-start prior, NOT as a co-modeled fidelity level.

---

## Heuristic as Prior Mean (NOT Full MFBO)

### The Architecture

Based on the particle accelerator BO paper (Scientific Reports 2025): use the heuristic as the GP's prior mean function. The GP learns the residual between heuristic and simulation.

```
f_predicted(x) = heuristic(x) + GP_correction(x)
```

**Why this works even at R² = 0.49:**
- The GP correction term absorbs the heuristic's systematic biases
- The heuristic provides a reasonable starting point (better than constant mean)
- The residual `f_sim - f_heuristic` is smoother and lower-variance than `f_sim` alone
- Convergence guarantee: piBO (ICLR 2022) proves convergence at regular rates regardless of prior quality

**Key safeguard:** Linear decay weight transitions from heuristic-prior to constant-prior over time, preventing a bad prior from biasing late-stage refinement.

### Implementation with Optuna + Heuristic Warm-Start

We don't use BoTorch's GP directly (TPE is our primary sampler). Instead, the heuristic informs optimization via warm-starting:

```python
# Phase A: Heuristic exploration (cost: ~0, time: seconds)
builds = generate_diverse_builds(hull, game_data, n=50_000)
scores = [heuristic_score(b, hull, game_data).composite_score for b in builds]
top_500 = sorted(zip(builds, scores), key=lambda x: -x[1])[:500]

# Phase B: Warm-start Optuna study
for build, score in top_500:
    trial = create_trial(
        params=build_to_params(build),
        distributions=search_space_distributions,
        values=[score * 0.5],  # Scale down — heuristic != sim
    )
    study.add_trial(trial)

# Phase C: Simulation-guided optimization
# TPE now has 500 "observations" informing its density estimators
# It will explore near heuristically-good regions first
for _ in range(sim_budget):
    trial = study.ask()
    build = repair_build(trial_to_build(trial), hull, game_data)
    sim_score = evaluate_against_opponent_pool(build)
    study.add_trial(create_trial(
        params=build_to_params(build),
        distributions=search_space_distributions,
        values=[sim_score],
    ))
```

### When to Upgrade to Full MFBO

If heuristic calibration improves R² above 0.75 (after Phase 6 surrogate correction), switch to BoTorch's `SingleTaskMultiFidelityGP` with `qMultiFidelityKnowledgeGradient`. The infrastructure is the same — just swap the acquisition function.

---

## Noise Handling and Adaptive Replication

### Sources of Noise

1. **AI behavior randomness**: Starsector's AI makes different micro-decisions each run
2. **Weapon projectile spread**: Random within specified arcs
3. **Timing jitter**: Shield toggling, target selection slightly stochastic

### How Many Replicates?

| Phase | Replicates per Opponent | Purpose |
|---|---|---|
| Heuristic screening | 0 (deterministic) | Pre-filter |
| Optimization loop | 1 per opponent × 5 opponents = 5 total | Sufficient for TPE |
| Final validation (racing) | 5 per opponent × 5 opponents = 25 total | Confident ranking |

The opponent pool already provides noise reduction: averaging across 5 diverse opponents smooths out matchup-specific variance.

### WilcoxonPruner for Adaptive Budget

WilcoxonPruner (Optuna) runs a Wilcoxon signed-rank test comparing each build's per-opponent scores to the best build's. If the build is statistically worse (p < 0.1) after 2-3 opponents, prune it. This saves 40-60% of simulation budget on clearly bad builds.

### Racing for Final Selection (irace-style)

After optimization identifies top-10 candidates:

1. Evaluate all 10 on opponent 1, 5 replicates each
2. Friedman test — eliminate statistically inferior builds
3. Evaluate survivors on opponent 2, 5 replicates each
4. Repeat until budget exhausted or winner emerges

This naturally allocates more replicates to competitive builds.

---

## Recommended Evaluation Pipeline

```
Phase 1: HEURISTIC SCREENING (seconds, no game needed)
    Input: Full search space
    Method: generate_diverse_builds(50K-100K) + heuristic_score()
    Output: Top 500 candidates as warm-start for Optuna
    Budget: 0 simulation evaluations

Phase 2: OPTIMIZER-GUIDED FULL SIM (hours)
    Input: Warm-started Optuna study + optimizer exploration
    Method: TPE with constant_liar, WilcoxonPruner, opponent pool (5 opponents)
    Output: Top 10-20 builds with mean HP differentials
    Budget: 200-400 builds × ~3 opponents avg (WilcoxonPruner) = 600-1200 sims
    Wall-clock: ~2-3 hours with 8 instances

Phase 3: RACING VALIDATION (hours)
    Input: Top 10-20 from Phase 2
    Method: irace-style racing, 5+ replicates per opponent per survivor
    Output: Final ranked builds with confidence intervals + matchup profiles
    Budget: 250-500 sims
    Wall-clock: ~1 hour with 8 instances

Total per hull: ~3-4 hours, ~1000-1700 sims, ~$11
```

**Total wall-clock: ~3-4 hours for a complete hull optimization.**

---

## Comparison: Old Pipeline vs New Pipeline

| Aspect | Old (3-tier) | New (2-tier + curtailment) |
|---|---|---|
| Fidelity levels | Heuristic → Short sim → Full sim | Heuristic → Full sim + curtailment |
| Short sim risk | 100% timeout rate at 15s → corrupted signal | Eliminated |
| Time savings mechanism | Short sim screening | Curtailment (12-24%) + WilcoxonPruner (40-60%) |
| Warm-start method | Feed short-sim survivors to full-sim | Feed heuristic top-500 directly to TPE |
| Opponent strategy | Not specified | Fixed diverse pool (5-6 archetypes) |
| Budget per hull | ~500-2000 sims + 500-2000 short sims | ~1000-1700 sims total |
| Wall-clock per hull | ~4-8 hours | ~3-4 hours |
