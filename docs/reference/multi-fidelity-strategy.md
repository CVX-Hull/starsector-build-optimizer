# Multi-Fidelity Evaluation Strategy

This document covers the three-tier evaluation pipeline, surrogate model design, noise handling, and adaptive replication strategies.

---

## Three-Tier Fidelity Hierarchy

| Fidelity | Method | Cost | Accuracy (R² with Fidelity 2) | Use |
|---|---|---|---|---|
| **0 — Heuristic** | Static metrics from game data | ~0ms | ~0.5-0.7 | Screen 10,000+ candidates/second |
| **1 — Short Sim** | 15s game time, 3x speed | ~5s | ~0.8-0.9 | Capture combat dynamics approximately |
| **2 — Full Sim** | 60s game time, 3-5x speed, 5-10 replicates | ~60-300s | Ground truth (noisy) | Final validation |

### Why Multi-Fidelity Matters

At 60s per evaluation, 500 evaluations = 8.3 hours with a single instance. Multi-fidelity can achieve 2-5x speedup by spending most evaluations at cheap fidelities and promoting only promising candidates to expensive ones.

### Critical Caveat: Unreliable Low-Fidelity Sources

Our heuristic (Fidelity 0) has R² ≈ 0.5-0.7 with true combat performance. Standard multi-fidelity BO can actually perform **worse** than single-fidelity when low-fidelity sources are poor approximations (rMFBO, arXiv:2210.13937).

**Where the heuristic fails:**
- Weapon synergies (kinetic + HE coverage) — emergent, not captured by sum of DPS
- AI behavior adaptation — the AI manages flux, selects targets, toggles shields
- Range dynamics — mismatched ranges cause idle weapons, invisible to static metrics
- Armor penetration — the nonlinear damage formula favors high per-shot damage
- Safety Overrides interactions — the speed+range tradeoff plays out in positioning

**Where the heuristic works well:**
- Flux balance — universally predictive, easy to compute
- Gross capability (DPS, EHP) — rank-orders builds reasonably
- Obvious bad builds — clearly under-armed or over-fluxed builds score poorly

---

## Fidelity 0: Heuristic Scorer

### Static Metrics

```python
class HeuristicScorer:
    def score(self, build, hull, enemy=None):
        weapons = self.get_equipped_weapons(build)
        
        # 1. Flux Balance (most predictive)
        weapon_flux = sum(w.flux_per_second for w in weapons)
        dissipation = hull.flux_dissipation + build.vents * 10
        flux_balance = weapon_flux / max(dissipation, 1)
        flux_score = self._flux_balance_curve(flux_balance)
        
        # 2. DPS by damage type
        total_dps = sum(w.sustained_dps for w in weapons)
        kinetic_frac = sum(w.dps for w in weapons if w.type == "KINETIC") / max(total_dps, 1)
        he_frac = sum(w.dps for w in weapons if w.type == "HIGH_EXPLOSIVE") / max(total_dps, 1)
        damage_mix_score = 1.0 - abs(kinetic_frac - 0.5)  # Reward balanced mix
        
        # 3. Flux Efficiency
        total_flux = sum(w.flux_per_second for w in weapons)
        flux_efficiency = total_dps / max(total_flux, 1)
        
        # 4. Effective HP
        armor_ehp = self._compute_armor_ehp(hull, build)
        shield_ehp = self._compute_shield_ehp(hull, build)
        total_ehp = hull.hitpoints + armor_ehp + shield_ehp
        
        # 5. Range Coherence
        ranges = [w.range for w in weapons if w.range > 0]
        if ranges:
            range_coherence = 1.0 - min(1.0, np.std(ranges) / np.mean(ranges))
        else:
            range_coherence = 0
        
        # 6. OP Efficiency
        op_used = build.total_op_cost
        op_efficiency = (total_dps + total_ehp * 0.01) / max(op_used, 1)
        
        # Weighted composite (weights from regression calibration)
        return (self.w[0] * self._normalize(total_dps)
              + self.w[1] * self._normalize(flux_efficiency)
              + self.w[2] * self._normalize(total_ehp)
              + self.w[3] * range_coherence
              + self.w[4] * flux_score
              + self.w[5] * damage_mix_score
              + self.w[6] * self._normalize(op_efficiency))
    
    def _flux_balance_curve(self, ratio):
        """Sigmoid-like scoring: good below 0.8, penalty above 1.0."""
        if ratio <= 0.6:
            return 1.0
        elif ratio <= 0.8:
            return 1.0 - (ratio - 0.6) * 0.5
        elif ratio <= 1.0:
            return 0.9 - (ratio - 0.8) * 2.0
        else:
            return max(0.0, 0.5 - (ratio - 1.0) * 2.0)
```

### Calibration Procedure

1. Generate 200-300 diverse builds (Latin Hypercube sampling in build space)
2. Run each through Fidelity 2 (full sim, 5 replicates)
3. Compute all static metrics for each build
4. Fit weighted regression: `sim_score = Σ w_i × metric_i`
5. Validate with held-out set (80/20 split)
6. Expected R² ≈ 0.5-0.7

---

## Fidelity 1: Short Simulation

### Configuration

- Combat duration: 15 seconds of game time (vs 60s for full sim)
- Speed multiplier: 3x (stable physics)
- Replicates: 1-2 (for screening, not precision)
- All ships AI-controlled

### What It Captures That Heuristic Misses

- Weapon-to-shield flux dynamics (hard flux accumulation)
- AI engagement decisions (closing to range, shield toggling)
- Early combat trajectory (which side gains flux advantage)
- Missile effectiveness vs PD coverage
- Range dictation (who controls engagement distance)

### What It Misses

- Long-term attrition (armor degradation over minutes)
- PPT effects (Safety Overrides penalty only matters in long fights)
- Comeback mechanics (ships that win late after absorbing early damage)
- Full missile expenditure (ammo-limited builds may not fire all missiles)

### When Short Sim Is Sufficient

For **relative ranking** of builds (not absolute win rate), short sims correlate highly with full sims. They are sufficient for:
- Eliminating clearly bad builds (Phase 2 screening)
- Identifying which builds have good early-combat flux dynamics
- Ranking builds within the same archetype

---

## Fidelity 2: Full Simulation

### Configuration

- Combat duration: 60 seconds of game time
- Speed multiplier: 3-5x (3x preferred for accuracy; 5x acceptable)
- Replicates: 5-10 for final evaluation, 3 for optimization loop
- All ships AI-controlled
- Personality: Steady (default) unless testing specific scenarios

### Physics Accuracy at Speed

| Speed | Accuracy | Risk |
|---|---|---|
| 1x | Perfect | Too slow for batch optimization |
| 2x | Very good | Negligible physics artifacts |
| 3x | Good | Minor collision detection issues at extreme speeds |
| 5x | Acceptable | Occasional projectile passthrough, slight spread changes |
| 10x+ | Poor | Projectiles phase through ships, combat results unreliable |

**Recommendation:** Use 3x for final validation, 5x acceptable during exploration.

---

## Composite Surrogate Model

### Architecture: Heuristic as Mean Function + GP Correction

Based on the Kennedy-O'Hagan AR1 framework:

```
f_predicted(x) = heuristic(x) + GP_correction(x)
```

The GP learns the **residual** between heuristic and simulation. This is easier to learn than the raw simulation output because:
- The heuristic captures most of the variance
- The residual is smoother and lower-variance
- Less training data needed for the GP

### Implementation in BoTorch

```python
import torch
from botorch.models import SingleTaskGP
from gpytorch.means import GenericDeterministicMean

class HeuristicMean:
    """Use heuristic score as GP mean function."""
    def __init__(self, heuristic_scorer):
        self.scorer = heuristic_scorer
    
    def __call__(self, X):
        return torch.tensor([self.scorer.score(x) for x in X])

# Fit GP on residuals
train_X = observed_builds  # Tensor of build parameters
train_Y_sim = simulation_scores
train_Y_heuristic = heuristic_scores
train_Y_residual = train_Y_sim - train_Y_heuristic

model = SingleTaskGP(
    train_X, 
    train_Y_residual,
    mean_module=gpytorch.means.ZeroMean(),  # Residual should be zero-mean
)

# Prediction: heuristic + GP correction
def predict(x):
    heuristic = scorer.score(x)
    residual_mean, residual_var = model.posterior(x).mean, model.posterior(x).variance
    return heuristic + residual_mean, residual_var
```

### Alternative: Heuristic as Input Feature

Instead of a mean function, include the heuristic score as an additional input feature to the GP/RF:

```python
# Augment input with heuristic score
X_augmented = np.column_stack([build_features, heuristic_scores])
model.fit(X_augmented, sim_scores)
```

This is simpler and lets the model learn a nonlinear mapping from heuristic to sim (not just additive correction).

---

## Multi-Fidelity Optimization Methods

### Recommended: rMFBO (Safe Multi-Fidelity)

rMFBO ensures performance is bounded below by single-fidelity BO even when low-fidelity sources are misleading.

**When to use:** Always, as a safety wrapper around any multi-fidelity method.

### Alternative: MFES-HB (Ensemble Surrogate + HyperBand)

MFES-HB builds surrogates at ALL fidelity levels and uses Product of Experts with learned weights. Discordant fidelities are automatically downweighted.

**Implementation:** [GitHub](https://github.com/PKU-DAIR/MFES-HB)

**Fidelity mapping for HyperBand:**
- Budget 1 → Fidelity 0 (heuristic)
- Budget 2 → Fidelity 1 (short sim)
- Budget 3 → Fidelity 2 (full sim)

### Alternative: BoTorch Multi-Fidelity Knowledge Gradient

```python
from botorch.acquisition import qMultiFidelityKnowledgeGradient
from botorch.models import SingleTaskMultiFidelityGP

# Fidelity as an additional input dimension
# 0.0 = heuristic, 0.5 = short sim, 1.0 = full sim
model = SingleTaskMultiFidelityGP(
    train_X=train_X_with_fidelity,
    train_Y=train_Y,
    data_fidelities=[fidelity_dim_index],
)

acqf = qMultiFidelityKnowledgeGradient(
    model=model,
    target_fidelities={fidelity_dim_index: 1.0},  # Optimize at full fidelity
    cost_aware_utility=cost_model,  # Cost of each fidelity level
)
```

---

## Noise Handling and Adaptive Replication

### How Many Replicates?

| Phase | Replicates | Purpose |
|---|---|---|
| Heuristic screening | 0 (deterministic) | Pre-filter |
| Short sim screening | 1-2 | Quick ranking |
| Optimization loop | 3 | Sufficient for BO surrogate |
| Final validation | 5-10 | Confident ranking |
| Publication-quality | 20+ | Tight confidence intervals |

### Adaptive Replication via Knowledge Gradient

The Knowledge Gradient (KG) naturally handles the "new build vs re-evaluate" decision:
- For new builds: KG = expected information from exploring unknown territory
- For existing builds: KG = expected gain from reducing uncertainty at that point
- KG selects whichever gives highest marginal value of information

### Racing (irace-style) for Final Selection

After optimization identifies top-20 candidates:

1. Evaluate all 20 on scenario 1 (opponent type A), 1 replicate each
2. Apply Friedman test — eliminate statistically inferior builds
3. Evaluate survivors on scenario 2 (opponent type B)
4. Repeat until budget exhausted or winner emerges

This naturally allocates more evaluations to competitive builds.

### OCBA (Optimal Computing Budget Allocation)

For final-stage comparison of top-K builds:
- Allocate more replicates to builds that are close to the best (need precision)
- Allocate more replicates to builds with higher variance (need certainty)
- Maximizes Probability of Correct Selection given total budget

---

## Recommended Evaluation Pipeline

```
Phase 1: HEURISTIC SCREENING (minutes, no game needed)
    Input: Full search space (~10^6-10^8 feasible builds)
    Method: Random/Sobol sampling + heuristic scoring
    Output: Top 500-1000 candidates
    Budget: 100,000-500,000 heuristic evaluations

Phase 2: SHORT SIM SCREENING (hours)
    Input: Top 500-1000 from Phase 1
    Method: 1-2 replicates of short sim (15s game time)
    Output: Top 50-100 candidates
    Budget: 500-2000 short sim evaluations
    Wall-clock: ~30 min with 16 instances

Phase 3: OPTIMIZER-GUIDED FULL SIM (hours)
    Input: Top 100 as warm-start + optimizer exploration
    Method: Bounce/SMAC3 with full sim (60s, 3 replicates)
    Output: Top 10-20 builds with mean performance estimates
    Budget: 200-400 full sim evaluations
    Wall-clock: ~2-4 hours with 16 instances

Phase 4: FINAL VALIDATION (hours)
    Input: Top 10-20 from Phase 3
    Method: irace-style racing, 10+ replicates per survivor
    Output: Final ranked builds with confidence intervals
    Budget: 100-200 full sim evaluations
    Wall-clock: ~1-2 hours with 16 instances
```

**Total wall-clock: ~4-8 hours for a complete hull optimization.**
