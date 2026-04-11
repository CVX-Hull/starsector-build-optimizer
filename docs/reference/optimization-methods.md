# Optimization Methods — Detailed Technical Guide

This document covers each optimization method in depth: how it works, when to use it, implementation details, and performance expectations.

**Updated based on Phase 4 research findings.** Key change: Optuna TPE replaces Bounce/SMAC3 as primary optimizer. See `phase4-research-findings.md` for full rationale.

---

## Table of Contents

1. [Method Selection Decision Tree](#1-method-selection-decision-tree)
2. [Optuna TPE — Primary Optimizer](#2-optuna-tpe--primary-optimizer)
3. [CatCMAwM — Evolutionary Alternative / Primary Sampler Option](#3-catcmawm--evolutionary-alternative--primary-sampler-option)
4. [SMAC3 — When Constraints Need ConfigSpace](#4-smac3--when-constraints-need-configspace)
5. [Bounce — Reference Only](#5-bounce--reference-only)
6. [Constraint Handling Strategy](#6-constraint-handling-strategy)
7. [Opponent Selection Strategy](#7-opponent-selection-strategy)
8. [Method Comparison Table](#8-method-comparison-table)

---

## 1. Method Selection Decision Tree

```
Is this a single-build optimization or archetype discovery?
├── Single-build optimization
│   ├── Budget < 200 evals? → Optuna TPE + heuristic warm-start
│   ├── Budget 200-1000 evals? → Optuna TPE or CatCMAwM (via --sampler catcma)
│   ├── Need refinement after TPE? → CatCMAwM sampler (via OptunaHub)
│   └── Need multi-objective? → Optuna NSGA-II (Pareto per opponent)
│
└── Archetype discovery (QD)
    ├── Heuristic evaluation only? → CMA-MAE via pyribs (200K+ evals)
    ├── Simulation budget 1K-5K? → DSA-ME pattern (surrogate-assisted)
    └── Full pipeline? → Phase A heuristic illumination → Phase B sim validation
```

---

## 2. Optuna TPE — Primary Optimizer

### Why Optuna Over Bounce/SMAC3

| Criterion | Bounce | SMAC3 | Optuna TPE |
|---|---|---|---|
| Constraint support | None | ConfigSpace (best) | Via repair + constraints_func |
| Batch parallelism | qEI (good) | Broken | constant_liar (adequate at 4-8) |
| PyPI package | No | Yes | Yes |
| API quality | Research code | Good | Excellent (ask-tell) |
| Sampler swappability | None | None | OptunaHub ecosystem |

**Key insight**: Since we have `repair_build()` that maps any raw proposal to a feasible build, we don't need the optimizer to understand constraints natively. This eliminates SMAC3's main advantage (ConfigSpace) and makes Optuna's simpler API the better choice.

### How TPE Works

Standard TPE factorizes density estimates as products of univariate Parzen window estimators. With `multivariate=True`, it builds a joint multivariate Parzen estimator using Scott's rule bandwidth. For categoricals, it uses Aitchison-Aitken kernels.

**Critical scaling caveat**: Scott's rule bandwidth has a `n^(-1/(4+d))` term. At d=60, this converges extremely slowly — useful density estimation requires many samples. TPE needs **100-200 random startup trials** before meaningfully outperforming random search at our dimensionality.

### Implementation

```python
import optuna
from optuna.samplers import TPESampler
from optuna.trial import create_trial

sampler = TPESampler(
    multivariate=True,
    constant_liar=True,
    n_ei_candidates=256,     # Default 24 is too few for 60D
    n_startup_trials=100,    # Default 10 is too few for 60D
)

study = optuna.create_study(
    sampler=sampler,
    direction="maximize",
)

# Warm-start with heuristic evaluations
for build, heuristic_score in top_500_heuristic_builds:
    trial = create_trial(
        params=build_to_params(build),
        distributions=search_space_distributions,
        values=[heuristic_score * 0.5],  # Scale down — heuristic != sim
    )
    study.add_trial(trial)

# Ask-tell loop with repair
for _ in range(budget):
    raw_trial = study.ask()
    raw_build = trial_to_build(raw_trial)
    repaired = repair_build(raw_build, hull, game_data)

    # Deduplicate
    build_hash = hash_build(repaired)
    if build_hash in cache:
        score = cache[build_hash]
    else:
        score = evaluate_against_opponent_pool(repaired)
        cache[build_hash] = score

    # Lamarckian: record repaired params, not raw
    trial = create_trial(
        params=build_to_params(repaired),
        distributions=search_space_distributions,
        values=[score],
    )
    study.add_trial(trial)
```

### Known Failure Modes at 50-70D

1. **High dimensionality**: Kernel density estimation degrades. TPE essentially becomes random search with mild bias past ~30D. Mitigation: increase `n_ei_candidates` to 256+, use 100+ startup trials.
2. **Irrelevant categoricals**: TPE wastes budget exploring unimportant hullmod toggles. Many hullmods have near-zero effect on combat. Mitigation: pre-filter eligible hullmods to only combat-relevant ones.
3. **Noisy objectives**: Combat simulation has variance from AI behavior randomness. Mitigation: average across 5+ opponent matchups (reduces noise).
4. **Local optima trapping**: TPE's density ratio creates attraction basins. Mitigation: restart studies with different seeds; use CatCMAwM for exploration.
5. **Constant liar degradation**: At batch size 4-8, expect ~1.5-2x the trial budget vs sequential to achieve same quality. Still far better than random search.

### Expected Performance

- At 100-200 trials: begins outperforming random search
- At 300-500 trials: finds good-quality builds for a single hull
- Wall-clock: ~3-4 hours with 8 instances (including warm-start + racing)

---

## 3. CatCMAwM — Evolutionary Alternative / Primary Sampler Option

### How It Works

CatCMAwM jointly optimizes continuous, integer, and categorical variables using a combined multivariate Gaussian + categorical distribution. Updated via natural gradient (CMA-ES rules for Gaussian, ASNG for categoricals).

### Population Size

Formula: `lambda = 4 + floor(3 * ln(N_co + N_ca))`. At d=60: lambda ≈ 16-17. This is small for multimodal problems — consider 2x-4x for difficult landscapes.

### When to Use

- **As Optuna sampler**: For refinement after TPE has identified promising regions
- **As pyribs emitter**: For quality-diversity (Phase 5) — natural batch parallelism matches instance count
- **NOT as primary optimizer**: Only tested up to ~40D, requires 1000-5000 evaluations (too many for expensive sim)

### Implementation via OptunaHub

```python
from optunahub.samplers import CatCMAwMSampler

sampler = CatCMAwMSampler(seed=42)
study = optuna.create_study(sampler=sampler)
# Same ask-tell loop as TPE
```

---

## 4. SMAC3 — When Constraints Need ConfigSpace

### When to Use Instead of Optuna

Only if you need **complex conditional parameter spaces** that repair_build() cannot express:
- e.g., missile hullmod active ONLY IF a missile weapon is equipped
- e.g., shield mods conditional on shield_type != NONE

For our current problem, repair_build() handles all constraints, so SMAC3's ConfigSpace advantage is not needed.

### Why NOT Primary

**Batch parallelism is broken.** SMAC3's own team acknowledges parallel SMAC is "about as good as random search" (GitHub issue #1131). No batch acquisition function like qEI. This is a dealbreaker with 4-8 parallel game instances.

### Still Available As Optuna Sampler

```python
from optuna_integration import SMACSSampler
sampler = SMACSSampler(search_space=smac_configspace)
study = optuna.create_study(sampler=sampler)
```

---

## 5. Bounce — Reference Only

### Why Downgraded

1. **No constraint support**: Cannot express OP budget, hullmod incompatibilities, or slot compatibility. Repair wrapper interaction is poorly defined because the internal binning/embedding maps the raw space non-trivially.
2. **No PyPI package**: Research code at github.com/lpapenme/bounce, Poetry-based. Not maintained as a library.
3. **Research-quality code**: Minimal documentation, no ask-tell API.

### Still Interesting For

- **Benchmarking**: If comparing optimizer methods on heuristic proxy, Bounce's progressive dimensionality increase is elegant.
- **Future**: If constraint support is added, Bounce's batch qEI and GP noise model would make it competitive.

---

## 6. Constraint Handling Strategy

### The Repair Operator Interaction Problem

When the optimizer proposes raw build X but evaluates `repair(X)`, two issues arise:

**Many-to-one mapping (collisions):** Many raw proposals map to the same repaired build. Any build that's 20% over budget repairs to the same trimmed version. Wastes simulation budget on duplicates.

**Landscape distortion:** The surrogate learns a distorted landscape where infeasible regions appear to have the score of the nearest feasible point, creating plateaus where gradient information is lost.

### Recommended: Lamarckian + Deduplication + Constraint Function

Based on literature review (Ishibuchi 2005, Koziel & Michalewicz 1999, Watanabe & Hutter c-TPE 2023):

1. **Lamarckian recording**: Record `(repaired_params, score)` via `study.add_trial()`, not `(raw_params, score)`. The TPE density estimators learn the feasible manifold directly. Avoids the "Baldwinian" landscape distortion where the surrogate sees infeasible coordinates with repaired scores.

2. **Deduplication**: Hash repaired builds before simulation. Return cached score for collisions. At our dimensionality with greedy drop repair, the collision rate is likely 10-30%.

   ```python
   build_hash = hash((frozenset(build.weapon_assignments.items()), build.hullmods, build.flux_vents, build.flux_capacitors))
   if build_hash in cache:
       return cache[build_hash]
   ```

3. **Constraint function**: Report OP budget violation to TPESampler's `constraints_func`. This biases the density estimators away from infeasible regions, reducing the collision rate over time.

   ```python
   def constraints_func(trial):
       op_overshoot = trial.user_attrs.get("op_overshoot", 0)
       return [op_overshoot]  # Positive = infeasible
   ```

### Why NOT Pure Penalty

- Infeasible builds cannot be loaded into the game — no meaningful simulation score exists
- Penalty weight λ is hard to tune
- But OP overshoot as a **soft constraint** (via `constraints_func`) gives the optimizer gradient information about the constraint boundary

### Why NOT Baldwinian

Baldwinian repair (record raw params with repaired score) preserves genotype diversity — good for evolutionary algorithms. But for TPE, it means the density estimators model the raw (infeasible) space and get confused when many raw points map to the same score. Lamarckian is better for surrogate-based optimization.

---

## 7. Opponent Selection Strategy

### The Problem

Starsector has strong rock-paper-scissors dynamics:
- **Kinetic** weapons: 200% damage to shields, 50% to armor
- **High Explosive** weapons: 50% to shields, 200% to armor
- **Energy** weapons: 100% to everything (generalist)

A single-opponent fitness produces counter-builds that exploit the opponent's weakness but fail against other archetypes.

### Recommended: Fixed Diverse Opponent Pool

Select 5-6 stock opponents per hull size, covering archetypes:

| Archetype | Example Ship | Role | What It Tests |
|---|---|---|---|
| Shield tank | Dominator (kinetic) | Absorbs kinetic, weak to HE | Anti-armor capability |
| Armor tank | Onslaught (HE) | Absorbs HE, weak to kinetic | Anti-shield capability |
| Fast kiter | Medusa / Hyperion | Maintains range, hit-and-run | Range control, tracking |
| Carrier | Heron / Astral | Fighter/bomber wings | PD coverage, sustained damage |
| Phase ship | Doom / Harbinger | Cloak, high burst damage | Burst survivability, tracking |
| Balanced | Eagle / Fury | Mixed weapons, balanced | All-around capability |

### Fitness Functions

- **Average win rate**: `mean(hp_differentials)`. Simple, interpretable. Rewards generalists.
- **Minimax**: `min(hp_differentials)`. Rewards robust builds with no bad matchups. Produces anti-fragile builds.
- **Weighted average**: Weight opponents by metagame frequency. E.g., weight common fleet compositions higher.

**Recommendation**: Start with average, switch to minimax if builds have exploitable weaknesses.

### Budget Impact

5 opponents per evaluation × 200 builds = 1000 sims.

### Why NOT These Alternatives

- **Elo/TrueSkill**: Needs 50-100+ games per build for stable ratings. Too expensive at 30s/sim.
- **Co-evolution**: We don't control opponents. One-sided optimization against fixed pool is correct.
- **Full Nash/PSRO**: Overhead of iterative expansion not worth it when opponent pool is small and curated.
- **Single opponent**: RPS dynamics make single-opponent fitness misleading.

---

## 8. Method Comparison Table

| Criterion | Optuna TPE | CatCMAwM | SMAC3 | Bounce |
|---|---|---|---|---|
| **Our primary use** | Main optimizer | QD emitter, refinement | Backup (if conditionals needed) | Reference only |
| **Categorical per-slot** | Native | Native | Native (ConfigSpace) | Native (typed bins) |
| **Binary toggles** | Native | Native (K=2) | Native | Native |
| **Bounded integers** | Native | Native (margin) | Native | Native (ordinal bins) |
| **OP budget constraint** | repair + constraints_func | Repair wrapper | Repair + ConfigSpace | Repair wrapper |
| **Batch/parallel** | constant_liar (B=4-8 OK) | Natural (pop_size) | Broken | qEI (good) |
| **Sample efficiency (<200)** | High (with warm-start) | Medium | High | High |
| **Sample efficiency (>500)** | Medium-High | Very High | Medium-High | High |
| **Scalability (dims)** | 100s (degrades past 30D) | ~40D tested | ~100D | 500D+ |
| **Noise handling** | Density ratio robust | Larger populations | RF handles noise | GP noise model |
| **Multi-objective** | NSGA-II, MOTPE | Bi-objective only | ParEGO | No |
| **Implementation maturity** | Production (PyPI) | Production (cmaes PyPI) | Production (PyPI) | Research code |
| **PyPI install** | `optuna` | `cmaes` + OptunaHub | `smac` | Not available |

### Recommendation Summary

1. **Use Optuna TPE** as primary optimizer with warm-start, repair, deduplication
2. **Use CatCMAwM** as MAP-Elites emitter (Phase 5) and optional refinement sampler
3. **Use SMAC3** only if we discover conditional parameter spaces that repair cannot handle
4. **Keep Bounce** as reference for benchmarking discussions only
5. **Benchmark on heuristic proxy first** before committing simulation budget
