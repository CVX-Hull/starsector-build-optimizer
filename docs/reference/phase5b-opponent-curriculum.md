# Phase 5B: Opponent Curriculum and Adaptive Pool Evolution

> **Superseded**: The epoch-based rotation approach was superseded by TWFE deconfounding (spec 28) + incumbent overlap after simulation showed rotation hurts cross-epoch comparability. See `docs/reference/phase5b-deconfounding-research.md` for the research synthesis and `docs/specs/28-deconfounding.md` for the implemented approach.

Research findings and design for improving evaluation signal quality through better opponent selection, ordering, and difficulty-aware fitness scoring.

---

## 1. Problem Statement

The first Hammerhead overnight run (2026-04-13, 63 sim trials) revealed three issues with the current fixed-pool random-order evaluation:

1. **Opponent pool bias**: Only 10/54 destroyer variants are used, selected alphabetically. The active set is dominated by freighters (buffalo) and light carriers (condor, drover) — no combat destroyers (enforcer, sunder, medusa, manticore, shrike). The optimizer finds builds that beat carriers but may fail against real combat ships.

2. **No difficulty weighting**: All 10 opponents contribute equally to fitness. Beating a buffalo (trivial) counts the same as beating condor_Attack (hard). The z-score normalization partially compensates but does not explicitly reward generalist builds.

3. **Inefficient pruning**: Random opponent ordering means the WilcoxonPruner doesn't get discriminating signal until later rungs. Only 7/63 trials (11%) were pruned. Ordering by discriminative power would let the pruner cut bad builds at rung 1-2 instead of rung 5-6.

### Budget constraint

Evaluating all 54 opponents per build is infeasible (~4.5 hours/trial at 5 min/matchup). We need to evaluate ~10 opponents per build while gradually covering the full pool.

---

## 2. Research Summary

### 2.1 Curriculum Learning in Games

**AlphaStar's PFSP** (Vinyals et al., 2019): Prioritized Fictitious Self-Play selects opponents with probability proportional to `f(win_rate)`. Two key functions:
- `f_hard(x) = (1-x)^p` — prioritize opponents the agent loses to
- `f_var(x) = x(1-x)` — prioritize opponents near 50% win rate (maximum information)

The `f_var` formulation is information-theoretically motivated: a 50/50 matchup has maximum outcome entropy (1 bit), while a 95/5 matchup yields only 0.29 bits.

**POET** (Wang & Lehman, 2019): Coevolves environments and solutions. Key lesson: a simple easy→hard curriculum doesn't work. "The right opponent at the right time" matters more than fixed ordering.

**OpenAI Five**: 80% current self, 20% historical — prevents strategy collapse against diverse opponents. Analogous to our need for diversity in the active set.

### 2.2 Adaptive Opponent Selection

**Active Testing** (Kossen et al., ICML 2021): Select test points that maximize information gain about the quantity of interest. Uses importance weighting to debias actively-selected evaluations. Directly applicable: select the next opponent to maximize information about build rank.

**Computerized Adaptive Testing / IRT**: Educational testing framework with decades of work on this exact problem. Each opponent has:
- **Difficulty** parameter — how hard it is (analogous to Elo)
- **Discrimination** parameter — how well it separates good from bad builds

High-discrimination opponents should be evaluated first for efficient pruning.

### 2.3 Racing Algorithms

**irace** (Lopez-Ibanez et al., 2016): Iterated racing with Friedman test elimination. Key insight: different iterations can use different instance sets. Elites carry forward and are re-evaluated on the new set. Comparability maintained through statistical testing, not identical evaluation sets.

**SMAC intensification** (Hutter et al., 2011): Doubling scheme — evaluate challenger on 1 instance, then 2 more, then 4 more. Incumbent accumulates evaluations across challengers. Challengers that fail early waste minimal budget.

### 2.4 Coevolutionary Archive Management

**IPCA** (de Jong, 2007): A test (opponent) is informative if it distinguishes between solutions that no other test distinguishes. Archive grows monotonically.

**Diversity-aware selection**: Pairwise rank correlation between opponents identifies redundancy. If two opponents rank builds identically, one is redundant. Maximize Elo-spread of the active set for maximum coverage.

### 2.5 Difficulty Weighting and the Bitter Lesson

**The bitter lesson** (Sutton, 2019): Methods leveraging computation scale better than methods leveraging human knowledge. Applied here: don't hand-engineer difficulty weights — let them emerge from data.

**Elo/TrueSkill for opponents**: Maintain Elo ratings for opponents, updated after every matchup. Weight fitness by opponent Elo. This is bitter-lesson compliant — difficulty emerges from data.

**Caution — intransitivity** (Harris & Tauritz, GECCO 2021): Elo assumes transitivity. Starsector has rock-paper-scissors dynamics (kinetic vs shields, HE vs armor). Don't replace fitness with Elo — use Elo to weight the existing z-scored fitness.

**Practical hybrid**: Maintain Elo only for opponents (stable with 60+ data points each). Weight z-scored fitness by opponent Elo. Temperature parameter controls how much difficulty matters vs equal weighting.

---

## 3. Recommended Design: Epoch-Based Opponent Pool Evolution

### 3.1 Three-Tier Evaluation

**Tier 1 — Gate set (3 anchor opponents, always evaluated first)**

After burn-in, identify the 3 opponents with highest *discriminative power* — those whose matchup z-scores best predict final build quality (highest absolute Spearman correlation with overall fitness). These anchors:
- Are always evaluated first (steps 0-2)
- Give the WilcoxonPruner maximum early signal
- Provide cross-epoch calibration (same opponents across all epochs)

**Tier 2 — Rotating diagnostic set (7 opponents, epoch-rotated)**

The remaining 7 slots rotate every epoch (~30 trials). Selection uses:

```
score(opp) = discriminative_power × diversity + α × exploration_bonus
```

- `discriminative_power`: |spearman_rho(z_scores_opp, final_fitness)|
- `diversity`: min(1 - |corr(opp, existing)|) for each existing active opponent
- `exploration_bonus`: sqrt(ln(N) / n_j) — UCB-style, favors under-tested opponents

This gradually covers the full 54-opponent pool while concentrating budget on informative opponents.

**Tier 3 — Extended validation (top builds only)**

Builds in the top ~10% of fitness get promoted to extended evaluation against 10-20 additional opponents. This catches overfitting to the active set.

### 3.2 Opponent Ordering Within Each Trial

Within each trial, order opponents by information gain rather than random shuffle:

1. **Anchors first** (steps 0-2): highest discriminative power, enables early pruning
2. **Rotating set ordered by f_var**: `weight(opp) = win_rate × (1 - win_rate)`, opponents near 50% win rate first (most informative for pruning decisions)

### 3.3 Elo-Weighted Fitness

Replace equal-weight averaging with Elo-weighted z-score aggregation:

```python
opponent_weight = softmax(opponent_elo / temperature)
weighted_fitness = sum(z_score_i * weight_i) / sum(weight_i)
```

Opponent Elo is updated after every matchup via standard Elo update. The temperature parameter controls sensitivity — high temperature ≈ equal weighting, low temperature ≈ only hard opponents matter.

### 3.4 Epoch Lifecycle

```
Epoch 0 (burn-in, trials 0-29):
  - Random 10 from full pool
  - Accumulate z-score stats, matchup data, and opponent Elo
  - No rotation — build reliable statistics

Epoch N (every 30 trials thereafter):
  - Compute per-opponent: discriminative power, diversity, Elo
  - Lock top-3 as anchors (gate set)
  - Select 7 rotating by UCB-informativeness score
  - Order: anchors first, then rotating by f_var
  - Run 30 trials
  - Promote top-10% builds to extended validation
  - Update all statistics
```

### 3.5 Cross-Epoch Comparability

Three mechanisms ensure Optuna TPE can compare trials across epochs:

1. **Z-score normalization**: Already in place. Z-scored fitness is on a common scale regardless of which opponents were used.
2. **Anchor stability**: The 3 gate opponents are always the same (or change very slowly). WilcoxonPruner gets stable signal at steps 0-2 across all epochs.
3. **Monotonic step IDs**: New opponents get new step IDs. The pruner only compares steps present in both trials, handling missing steps gracefully.

---

## 4. Additional Signal: Richer Combat Fitness

The combat harness already collects data that `combat_fitness` ignores. These can provide gradient where win/loss is flat.

### 4.1 Already Collected, Not Used

| Signal | Source | Value |
|--------|--------|-------|
| Damage dealt/taken breakdown | DamageTracker | Damage efficiency ratio |
| Armor vs shield damage | DamageTracker | Permanent vs recoverable damage |
| Overload count | DamageTracker | Flux pressure effectiveness |
| Armor fraction | ResultWriter | Survivability beyond hull HP |
| CR / peak time remaining | ResultWriter | Time efficiency |
| Disabled weapons, flameouts | ResultWriter | Degradation state |

### 4.2 Requires Java Harness Changes

| Signal | Implementation | Value |
|--------|---------------|-------|
| Time-weighted average flux | Per-frame accumulator in doFighting() | Flux economy |
| Cumulative overload duration | Per-frame isOverloaded() check | Pressure applied |
| Engagement distance over time | Per-frame position sampling | Kiting vs brawling detection |
| Time to first hull damage | One-time threshold check | Shield-breaking speed |

### 4.3 Priority

**Phase 1** (no Java changes): Incorporate damage efficiency, overload differential, and duration-normalized damage from already-collected data into `combat_fitness.py`.

**Phase 2** (lightweight Java): Add per-frame flux pressure and overload duration tracking.

---

## 5. Expected Impact

| Improvement | Mechanism | Expected Effect |
|-------------|-----------|-----------------|
| Full-pool coverage | Epoch rotation | Builds tested against combat destroyers, not just carriers |
| Discriminative ordering | Anchors first | Pruning rate: 11% → 30-40% (saves ~35 min per pruned build) |
| Elo-weighted fitness | Data-driven difficulty | Better signal for generalist builds |
| Richer combat_fitness | More gradient | Distinguish timeout quality, faster TPE convergence |
| Extended validation | Top-build deep test | Catch overfitting to active set |

---

## 6. References

- Vinyals et al. (2019), "Grandmaster Level in StarCraft II Using Multi-Agent RL" — Nature
- Wang & Lehman (2019), "POET: Endlessly Generating Increasingly Complex and Diverse Learning Environments" — arXiv:1901.01753
- Kossen et al. (2021), "Active Testing: Sample-Efficient Model Evaluation" — ICML, arXiv:2103.05331
- Lopez-Ibanez et al. (2016), "The irace Package: Iterated Racing for Automatic Algorithm Configuration" — Operations Research Perspectives
- Hutter et al. (2011), "Sequential Model-Based Optimization for General Algorithm Configuration" — LION
- Harris & Tauritz (2021), "Elo-based Similar-Strength Opponent Sampling" — GECCO
- de Jong (2007), "Pareto-Coevolution Archive" — Evolutionary Computation
- Sutton (2019), "The Bitter Lesson"
- AGI-Elo (2025), "How Far Are We From Mastering A Task?" — arXiv:2505.12844
- Coulom (2008), "Whole-History Rating" — CGW
