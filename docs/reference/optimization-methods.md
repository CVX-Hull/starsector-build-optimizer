# Optimization Methods — Detailed Technical Guide

This document covers each optimization method in depth: how it works, when to use it, implementation details, and performance expectations.

---

## Table of Contents

1. [Method Selection Decision Tree](#1-method-selection-decision-tree)
2. [Bounce — Primary Optimizer](#2-bounce--primary-optimizer)
3. [SMAC3 — Conditional Parameter Handling](#3-smac3--conditional-parameter-handling)
4. [CatCMA with Margin — Evolutionary Alternative](#4-catcma-with-margin--evolutionary-alternative)
5. [MCBO Framework — Benchmarking](#5-mcbo-framework--benchmarking)
6. [MVRSM — Fast Baseline](#6-mvrsm--fast-baseline)
7. [Constraint Handling Strategy](#7-constraint-handling-strategy)
8. [Method Comparison Table](#8-method-comparison-table)

---

## 1. Method Selection Decision Tree

```
Is this a single-build optimization or archetype discovery?
├── Single-build optimization
│   ├── Budget < 100 evals? → SMAC3 or CASMOPOLITAN (surrogate advantage)
│   ├── Budget 100-500 evals? → Bounce (batch parallel) + SMAC3 (hedge)
│   ├── Budget > 500 evals? → CatCMA with Margin (scales better)
│   └── Need multi-objective? → Scalarize first; if true Pareto needed → pymoo NSGA-III
│
└── Archetype discovery (QD)
    ├── Heuristic evaluation only? → CMA-MAE via pyribs (200K+ evals)
    ├── Simulation budget 1K-5K? → SAIL / BOP-Elites (surrogate-assisted)
    └── Full pipeline? → DSA-ME pattern (online neural surrogate + MAP-Elites)
```

---

## 2. Bounce — Primary Optimizer

### How It Works

Bounce maps high-dimensional mixed variables into a lower-dimensional target space using **sparse count-sketch embeddings**, then progressively increases dimensionality.

**Phase 1 — Coarse search (low dimensionality):**
- Each input variable is assigned to a "bin" (target dimension)
- Variables of the SAME TYPE ONLY share bins (categorical with categorical, integer with integer)
- All variables in a bin share a single value
- GP surrogate is fitted in the low-dimensional space
- Acquisition function (Thompson Sampling) proposes candidates

**Phase 2 — Refinement (increasing dimensionality):**
- After a budget of evaluations, each bin splits into sub-bins
- Variables are redistributed
- All prior observations remain valid (nested property)
- Process repeats until target dimensionality equals input dimensionality

**Final phase — Full-dimensional search with trust regions.**

### Why Bounce for Our Problem

1. **Native batch parallelism**: Tested with B=1, 3, 5, 10, 20. Always benefits from more parallel workers. With 16 Starsector instances, set B=16.
2. **All our variable types**: Typed bins keep categoricals separate from integers.
3. **GP noise model**: Handles stochastic simulation naturally.
4. **No structure assumption**: Works even if the build space has irregular structure.
5. **Scales to 500d+**: More than enough for our ~15-25 effective dimensions.

### Implementation

```python
# Library: https://github.com/lpapenme/bounce
# Requirements: Python >= 3.10, Poetry

from bounce.bounce import Bounce
from bounce.util.benchmark import Parameter, ParameterType

# Define parameters
params = [
    Parameter("weapon_slot_1", ParameterType.CATEGORICAL, 
              categories=["empty", "heavymauler", "assaultchaingun", ...]),
    Parameter("weapon_slot_2", ParameterType.CATEGORICAL, ...),
    Parameter("hullmod_heavyarmor", ParameterType.BINARY),
    Parameter("hullmod_hardenedshields", ParameterType.BINARY),
    Parameter("vents", ParameterType.INTEGER, lower=0, upper=30),
    Parameter("caps", ParameterType.INTEGER, lower=0, upper=30),
]

optimizer = Bounce(
    parameters=params,
    batch_size=16,
    initial_target_dimensionality=5,
    n_initial=32,  # Random initial evaluations
)

for _ in range(budget // 16):
    candidates = optimizer.suggest(16)          # Batch of 16
    repaired = [repair_op_budget(c) for c in candidates]
    results = parallel_simulate(repaired)       # Evaluate on 16 instances
    optimizer.observe(repaired, results)
```

### Expected Performance

- At 300-500 evaluations: finds near-optimal builds for single hull
- Wall-clock: ~3-5 hours with 16 instances at 3x speed
- Typically finds good solutions within 100-200 evaluations, refines thereafter

### Limitations

- No native constraint handling → needs repair wrapper
- Research-quality code → may need adaptation
- GP scaling: becomes slow past ~2000 observations (not a concern at our budget)

---

## 3. SMAC3 — Conditional Parameter Handling

### How It Works

SMAC3 (Sequential Model-based Algorithm Configuration) uses a **random forest surrogate** instead of a GP. Key advantage: random forests handle conditional/hierarchical parameter spaces natively via tree splits.

**Loop:**
1. Fit random forest on all (config, performance) pairs
2. Compute Expected Improvement across configurations
3. Optimize EI via randomized local search in ConfigSpace
4. Evaluate best candidate
5. Repeat

### Why SMAC3 for Our Problem

1. **ConfigSpace**: Best-in-class conditional parameter definition
   - `EqualsCondition`: missile hullmod active only if missile weapon equipped
   - `ForbiddenClause`: Shield Shunt + Hardened Shields forbidden together
   - `InCondition`: weapon-specific mods conditional on weapon type
2. **Random forest surrogate**: Handles mixed categorical/integer/continuous natively
3. **Noise robustness**: Designed for algorithm configuration (inherently noisy)
4. **Intensification**: Adaptive replication — re-evaluates promising configs

### Implementation

```python
from smac import HyperparameterOptimizationFacade, Scenario
from ConfigSpace import (
    ConfigurationSpace, CategoricalHyperparameter,
    UniformIntegerHyperparameter, UniformFloatHyperparameter,
    EqualsCondition, ForbiddenAndConjunction, ForbiddenEqualsClause
)

cs = ConfigurationSpace(seed=42)

# Weapon slots
ws1 = CategoricalHyperparameter("weapon_WS001", 
    choices=["empty", "heavymauler", "assaultchaingun", ...])
cs.add_hyperparameter(ws1)

# Hullmods
hm_heavy_armor = CategoricalHyperparameter("hm_heavyarmor", [True, False])
hm_shield_shunt = CategoricalHyperparameter("hm_shieldshunt", [True, False])
hm_hardened_shields = CategoricalHyperparameter("hm_hardenedshields", [True, False])
cs.add_hyperparameters([hm_heavy_armor, hm_shield_shunt, hm_hardened_shields])

# Conditional: hardened shields only without shield shunt
cs.add_condition(EqualsCondition(hm_hardened_shields, hm_shield_shunt, False))

# Forbidden: mutual exclusions
cs.add_forbidden_clause(ForbiddenAndConjunction(
    ForbiddenEqualsClause(hm_shield_shunt, True),
    ForbiddenEqualsClause(hm_hardened_shields, True)
))

# Flux allocation
vent_frac = UniformFloatHyperparameter("vent_fraction", 0.0, 1.0, default_value=0.6)
cs.add_hyperparameter(vent_frac)

scenario = Scenario(cs, n_trials=500, n_workers=16)

def evaluate(config, seed=0):
    build = config_to_build(config)
    build = repair_op_budget(build)
    return simulate(build, seed=seed)

facade = HyperparameterOptimizationFacade(scenario, evaluate)
incumbent = facade.optimize()
```

### SMAC3 as Optuna Sampler

Available via [OptunaHub](https://hub.optuna.org/samplers/smac_sampler/):
```python
from optuna_integration import SMACSSampler
sampler = SMACSSampler(search_space=smac_configspace)
study = optuna.create_study(sampler=sampler)
```

---

## 4. CatCMA with Margin — Evolutionary Alternative

### How It Works

CatCMA maintains a joint distribution:
- **Multivariate Gaussian** over continuous/integer variables (mean `m`, covariance `C`, step-size `σ`)
- **Independent categorical distributions** `q_n` for each categorical variable

Each generation:
1. Sample `λ` candidates from the joint distribution
2. Evaluate all candidates (parallel)
3. Rank by fitness
4. Update distribution via **natural gradient** (CMA-ES rules for Gaussian, ASNG for categoricals)
5. Apply **margin correction** for integer variables (lower + upper bounds on marginal probabilities)

### Why CatCMA for Our Problem

1. **Natural parallel evaluation**: Population size = number of instances → one generation per batch
2. **Captures cross-type dependencies**: Joint distribution learns that "weapon X works well with hullmod Y"
3. **Outperforms BO at moderate dimensions**: At (10,10,10) scale, BO degrades while CatCMA maintains performance
4. **Simple implementation**: Clean ask-tell API

### Implementation

```python
from cmaes import CatCMAwM
import numpy as np

# Define variable spaces
# z_space: integer variable ranges [lower, upper]
# c_space: number of categories for each categorical variable

optimizer = CatCMAwM(
    x_space=None,  # No pure continuous variables
    z_space=[[0, 30], [0, 30]],  # vents, caps
    c_space=[
        len(compatible_weapons_slot_1),  # weapon choices per slot
        len(compatible_weapons_slot_2),
        ...,
        2, 2, 2, ...  # hullmod toggles (binary = 2 categories)
    ],
    population_size=16,  # Match parallel instances
    sigma=2.0,
    seed=42,
)

for generation in range(max_generations):
    solutions = []
    
    for _ in range(optimizer.population_size):
        x, z, c = optimizer.ask()
        build = decode_solution(x, z, c)
        build = repair_op_budget(build)
        fitness = evaluate(build)
        solutions.append(((x, z, c), fitness))
    
    optimizer.tell(solutions)
```

### When CatCMA Beats BO

- Budget > 200 evaluations
- > 15 total dimensions
- Strong dependencies between categorical and continuous variables
- Population size matches parallel capacity

### When BO Beats CatCMA

- Budget < 100 evaluations (surrogate gives BO a head start)
- < 15 dimensions
- Problem has local structure (trust regions exploit it)

---

## 5. MCBO Framework — Benchmarking

### Purpose

MCBO (from Huawei Noah's Ark Lab) provides a modular mix-and-match framework for systematic comparison of mixed-variable BO methods.

### How to Use for Our Problem

```python
from mcbo.search_space import SearchSpace
from mcbo.task_base import TaskBase

# Define our search space
params = [
    {"name": "weapon_slot_1", "type": "nominal", 
     "categories": ["empty", "heavymauler", ...]},
    {"name": "hullmod_heavyarmor", "type": "bool"},
    {"name": "vent_fraction", "type": "num", "lb": 0.0, "ub": 1.0},
]
space = SearchSpace(params)

# Define our objective
class StarsectorTask(TaskBase):
    def evaluate(self, x):
        build = x_to_build(x)
        build = repair_op_budget(build)
        return -heuristic_score(build)  # Minimize negative score

# Run different algorithm combinations
configs = [
    ("GP-TO", "TS", "local_search", True),   # CASMOPOLITAN-like
    ("GP-overlap", "EI", "ga", False),         # COMBO-like
    ("GP-TO", "EI", "interleaved", True),      # Novel combination
]

for surrogate, acq, opt, use_tr in configs:
    result = run_mcbo(space, task, surrogate, acq, opt, use_tr, budget=300)
```

### Implementation

- [GitHub](https://github.com/huawei-noah/HEBO/tree/master/MCBO)
- MIT license, BoTorch/GPyTorch backend
- Well-documented with tutorials

---

## 6. MVRSM — Fast Baseline

### When to Use

- Quick initial baseline to establish performance floor
- Prototyping (simplest implementation, ~200 lines)
- Integer-heavy subproblems where GP overhead is unnecessary

### Limitations for Our Problem

- No uncertainty quantification → no principled exploration
- No native categoricals → needs one-hot encoding
- No batch parallelism → sequential only
- No noise model → stochastic sim corrupts surrogate directly

### Implementation

```python
from MVRSM import MVRSM_minimize

# Encode categoricals as one-hot, integers directly
result = MVRSM_minimize(
    obj_func=evaluate_encoded_build,
    x0=initial_guess,
    lb=lower_bounds,
    ub=upper_bounds,
    num_int=num_integer_vars,
    max_evals=500,
    rand_evals=50,
)
```

---

## 7. Constraint Handling Strategy

### Recommended: Repair Operator Inside Acquisition Loop

Based on literature consensus (repair finds feasible solutions in 1 generation vs 7-72 for penalty):

```
Generate candidate → Repair to feasibility → Evaluate surrogate at REPAIRED point → Simulate REPAIRED build
```

Key: the surrogate sees the repaired configuration, not the original. This prevents mismatch between surrogate prediction and actual evaluation.

### Repair Implementation

```python
def repair_op_budget(build, hull):
    """Greedy repair: iteratively remove lowest value-per-OP items."""
    while compute_op_cost(build) > hull.op_budget:
        items = get_removable_items(build)  # weapons and optional hullmods
        worst = min(items, key=lambda i: item_value(i) / item_op_cost(i))
        remove_item(build, worst)
    
    # Allocate remaining OP to vents/caps
    remaining = hull.op_budget - compute_op_cost(build)
    allocate_flux(build, remaining)
    return build

def item_value(item):
    """Heuristic value of an item. Weapons: DPS/flux. Hullmods: estimated benefit."""
    if is_weapon(item):
        return item.sustained_dps / max(item.flux_per_second, 0.1)
    else:
        return hullmod_value_estimates[item.id]
```

### Hullmod Incompatibility Enforcement

Handled at search space level (ConfigSpace forbidden clauses) or by post-generation validation:

```python
INCOMPATIBLE_PAIRS = [
    ("shieldshunt", "makeshiftshieldgenerator"),
    ("shieldconversion_front", "shieldconversion_omni"),
    ("safetyoverrides", "fluxshunt"),
]

def enforce_incompatibilities(build):
    for a, b in INCOMPATIBLE_PAIRS:
        if build.get(f"hullmod_{a}") and build.get(f"hullmod_{b}"):
            # Remove the one with lower value
            if hullmod_value(a) < hullmod_value(b):
                build[f"hullmod_{a}"] = False
            else:
                build[f"hullmod_{b}"] = False
```

---

## 8. Method Comparison Table

| Criterion | Bounce | SMAC3 | CatCMA-wM | MCBO | MVRSM |
|---|---|---|---|---|---|
| **Categorical per-slot** | Native (typed bins) | Native (ConfigSpace) | Native | Native | One-hot encoding |
| **Binary toggles** | Native | Native | Native (K=2) | Native | Native (0/1 integer) |
| **Bounded integers** | Native (ordinal bins) | Native | Native (margin) | Native | Native (guaranteed) |
| **OP budget constraint** | Repair wrapper | Repair + ConfigSpace | Penalty/repair | Rejection sampling | Penalty/repair |
| **Conditional params** | No | YES (best-in-class) | No | No | No |
| **Noisy evaluations** | GP noise model | RF handles noise | Larger populations | GP noise model | No noise model |
| **Batch/parallel** | Native (B=1-20) | Native (n_workers) | Natural (pop_size) | Depends | No |
| **Sample efficiency (<100)** | High | High | Medium | High | Medium |
| **Sample efficiency (>200)** | High | Medium-High | Very High | High | Medium |
| **Scalability (dims)** | 500d+ | ~50d | ~30d (mixed) | ~60d | 238d+ |
| **Uncertainty quantification** | GP posterior | RF variance | No | GP posterior | No |
| **Multi-objective** | No (scalarize) | ParEGO | Bi-objective only | No | No |
| **Implementation maturity** | Research code | Production | Production (cmaes) | Research+ | Minimal |
| **Setup effort** | Medium | Low-Medium | Low | Low (benchmarking) | Very Low |

### Recommendation Summary

1. **Run Bounce + SMAC3 in parallel** as primary optimizers
2. **Use CatCMA** as MAP-Elites emitter for quality-diversity
3. **Use MCBO** for initial benchmarking on heuristic proxy
4. **Use MVRSM** as quick baseline only
5. **Calibrate via MCBO** which method works best on your specific hull before committing budget
