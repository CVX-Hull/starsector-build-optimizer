# Formal Problem Formulation

This document defines the ship build optimization problem in formal mathematical terms, characterizes the search space, and analyzes the constraint structure.

---

## Problem Classification

This is a **constrained mixed-variable black-box optimization problem with expensive stochastic evaluations and a natural multi-fidelity structure**.

More precisely, it is a variant of the **Multiple-Choice Multidimensional Knapsack Problem (MMKP)** where:
- Each weapon slot is a "group" — pick at most one item from a constrained set
- The OP budget is the knapsack capacity
- The objective function is a noisy black-box requiring ~60s simulation per evaluation

---

## Decision Variables

For a given ship hull `H`:

### Categorical Variables (Weapon Assignments)

For each weapon slot `i ∈ {1, ..., N_slots}`:
```
w_i ∈ W_i ∪ {∅}
```
where `W_i` is the set of weapons compatible with slot `i` (matching type and size), and `∅` represents an empty slot.

Typical `|W_i|` ranges:
- Small ballistic slot: ~10-15 options
- Medium hybrid slot: ~20-30 options
- Large universal slot: ~40-50 options

### Binary Variables (Hullmod Selection)

For each installable hullmod `j ∈ {1, ..., N_mods}`:
```
h_j ∈ {0, 1}
```
where `h_j = 1` means hullmod `j` is installed.

Typical `N_mods ≈ 35` (after filtering hull-incompatible mods).

### Integer Variables (Flux Allocation)

```
v ∈ {0, 1, ..., V_max}    (flux vents)
c ∈ {0, 1, ..., C_max}    (flux capacitors)
```

where `V_max, C_max` depend on hull size (10/20/30/50).

### S-Mod Selection (Optional)

For each installed hullmod, whether to S-mod it:
```
s_j ∈ {0, 1}  where s_j ≤ h_j  (can only S-mod installed mods)
Σ s_j ≤ S_max  (typically 2-3)
```

### Total Variable Count

For a typical cruiser with 7 weapon slots, ~35 eligible hullmods, and 2 flux allocation integers:
- 7 categorical (weapon choices)
- ~35 binary (hullmod toggles)
- 2 integer (vents, caps)
- **Total: ~44 variables** (but many hullmods are conditionally irrelevant)

In practice, the effective dimensionality is **~15-25** because many hullmod decisions are trivial (clearly good or clearly bad) or conditional (irrelevant given other choices).

---

## Constraints

### C1: OP Budget (Knapsack Constraint)
```
Σ_i cost(w_i) + Σ_j h_j × cost_j(hull_size) + v + c ≤ OP_budget(H)
```
This is the dominant constraint. All other items share a single budget.

### C2: Slot Type Compatibility (Per-Slot)
```
w_i ∈ W_i  (weapon must match slot type and size)
```
Enforced by construction in the search space — each slot only offers compatible weapons.

### C3: Hullmod Incompatibilities (Mutual Exclusion)
```
h_shield_shunt + h_makeshift_shield ≤ 1
h_front_conversion + h_omni_conversion ≤ 1
h_safety_overrides + h_flux_shunt ≤ 1
h_safety_overrides = 0  if hull_size == Capital
```

### C4: Logistics Hullmod Limit
```
Σ_{j ∈ logistics} h_j ≤ 2  (unless S-modded)
```

### C5: Max Vents/Caps
```
v ≤ V_max(hull_size)
c ≤ C_max(hull_size)
```
(With Flux Regulation skill: V_max += 5, C_max += 5)

### C6: Conditional Relevance (Soft Constraint)
Not a hard constraint, but affects surrogate accuracy:
- Missile hullmods (Expanded Missile Racks) are irrelevant if no missiles equipped
- Shield hullmods (Hardened Shields) are irrelevant with Shield Shunt
- Safety Overrides makes weapon range largely irrelevant past ~450 units

### Constraint Properties

**All constraints are cheap and deterministic.** They can be evaluated in microseconds from the build specification alone — no simulation needed. This is a major structural advantage over generic constrained BO, where constraint evaluation is expensive.

---

## Objective Function

### Primary Objective: Combat Performance Score

```
f(build) = E[combat_score(build, enemy, scenario)]
```

where `combat_score` is computed from simulation results and may include:
- Win/loss (binary)
- Damage dealt
- Time to kill
- Flux efficiency during combat
- Hull/armor remaining at combat end

The expectation is over stochastic simulation runs (AI decisions, weapon spread, timing).

### Properties of the Objective

| Property | Value | Implication |
|---|---|---|
| Evaluation cost | ~60s per simulation | Budget-limited optimization |
| Stochasticity | Moderate (same build can win or lose) | Need replicates for confidence |
| Parallelizability | 8-16 parallel instances | Batch evaluation methods preferred |
| Gradient | Unavailable | Gradient-free methods only |
| Continuity | Build space is discrete/mixed | No gradient even in principle |
| Multi-modal | Likely (multiple good archetypes) | Need global, not local, optimization |

### Multi-Objective Variant

Optionally optimize multiple objectives simultaneously:
```
f₁(build) = win_rate
f₂(build) = flux_efficiency
f₃(build) = deployment_point_efficiency
f₄(build) = versatility (performance across enemy types)
```

This produces a Pareto front rather than a single optimum.

---

## Search Space Analysis

### Combinatorial Size (Before Constraints)

For a hull with 7 weapon slots averaging 15 options each:
```
Weapons: 15^7 ≈ 170 million
Hullmods: C(35, k) for k=0..5 ≈ 350,000
Vents/Caps: 31 × 31 = 961
Naive total: ~10^13 to 10^14
```

### After Constraint Pruning

The OP budget eliminates the vast majority of combinations:
- Most full weapon loadouts exceed the OP budget
- Hullmod combinations are limited by OP cost scaling
- Vents/caps consume remaining OP

**Effective search space: ~10^6 to 10^8 feasible configurations per hull.**

### Pruning Factors

1. **OP budget**: Eliminates ~99.9% of naive combinations (the dominant pruner)
2. **Slot type restrictions**: Reduces per-slot options from ~129 to ~5-50
3. **Hullmod incompatibilities**: Eliminates a few specific combinations
4. **SO range penalty**: Makes long-range weapons on SO builds pointless
5. **Built-in weapons/hullmods**: Some slots are pre-filled
6. **Conditional irrelevance**: Many hullmod combinations are semantically meaningless

---

## Comparison to Related Problems

| Problem | Similarity | Difference |
|---|---|---|
| Knapsack (MMKP) | OP budget, item groups | Objective is black-box, not additive |
| Algorithm configuration (SMAC) | Mixed categorical+continuous, expensive eval | Our constraints are cheap; theirs are often black-box |
| Hyperparameter optimization | Mixed types, expensive eval, noisy | Our space is more structured (slot constraints) |
| EVE Online fitting | Nearly identical structure | EVE uses heuristic eval; we use simulation |
| Hearthstone deckbuilding | Discrete item selection, game sim eval | Cards have no slot constraints; our slots are typed |
| Neural architecture search | Categorical + conditional structure | NAS has more continuous params; our sim is faster |

---

## Dimensionality Reduction Opportunities

### Vent/Cap Parameterization

Instead of two integers `(v, c)`, parameterize as:
```
remaining_OP = OP_budget - weapon_cost - hullmod_cost
vent_fraction ∈ [0, 1]
v = round(vent_fraction × remaining_OP)
c = remaining_OP - v
```
Reduces two integer variables to one continuous variable.

### Hullmod Clustering

Many hullmods form natural groups (defensive, offensive, logistics, flux). Could parameterize as "archetype" choice + within-archetype selection.

### Empty Slot Encoding

For optimization, "empty slot" is just another categorical option (∅) with 0 OP cost. No special handling needed.

---

## Summary

The Starsector build optimization problem is a well-structured instance of constrained mixed-variable black-box optimization. Its key distinguishing features are:
1. **Cheap, known constraints** (OP budget, compatibility) — not black-box
2. **Expensive, stochastic objective** (combat simulation)
3. **Natural multi-fidelity structure** (heuristic → short sim → full sim)
4. **Conditional parameter relevance** (missile hullmods only with missiles)
5. **Desire for diversity** (not just one optimum, but a map of archetypes)

These features together point toward a specific set of methods — see [05-OPTIMIZATION-METHODS.md](./05-OPTIMIZATION-METHODS.md) for the recommended approaches.
