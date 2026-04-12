# Quality-Diversity for Build Archetype Discovery

This document covers using MAP-Elites and related quality-diversity (QD) methods to discover the full landscape of viable ship build archetypes — not just one optimal build, but ALL the ways a ship can be effectively built.

---

## Why Quality-Diversity?

Standard optimization finds a single best build. But Starsector players need to understand:
- What archetypes exist for a given hull? (SO brawler, sniper, shield tank, armor tank, carrier support...)
- How does each archetype compare?
- What are the tradeoffs between archetypes?
- Are there surprising viable builds that the community hasn't discovered?

QD answers all of these by producing an **archive** — a map of the best build found for every combination of behavioral characteristics.

---

## MAP-Elites Overview

MAP-Elites maintains a grid over a user-defined **behavior space**. Each cell stores the single highest-performing solution (elite) that falls within that cell's region.

**Algorithm:**
1. Initialize: randomly sample builds, evaluate fitness + behavior descriptors, place in grid
2. Loop:
   a. Pick a random occupied cell
   b. Mutate its elite to create offspring
   c. Evaluate offspring's fitness and behavior descriptors
   d. If offspring's cell is empty OR offspring beats current occupant → insert
3. Output: archive of diverse, locally-optimized builds

---

## Behavior Descriptors for Ship Builds

### Recommended 4D Behavior Space

| Dimension | Computation | Range | What It Separates |
|---|---|---|---|
| **Engagement Range** | DPS-weighted average weapon range | [0, 1500] units | Brawlers vs mid-range vs snipers |
| **Survivability Style** | shield_EHP / (shield_EHP + armor_EHP) | [0, 1] | Shield tanks vs armor tanks |
| **Damage Type Profile** | kinetic_dps / total_dps | [0, 1] | Anti-shield focus vs anti-armor focus |
| **Offense/Defense Ratio** | total_dps / (total_dps + total_ehp×0.01) | [0, 1] | Glass cannons vs fortresses |

### Why These 4?

- **Engagement Range** is the most defining axis — it determines the ship's fundamental role
- **Survivability Style** splits the two major defensive philosophies (shield-stacking vs armor-tanking)
- **Damage Type Profile** captures anti-shield vs anti-armor specialization
- **Offense/Defense Ratio** captures the glass-cannon-to-fortress spectrum

### Secondary Descriptors (Reported but Not Gridded)

- Speed/maneuverability
- Flux strategy (dissipation-focused vs capacity-focused)
- Missile dependence (% DPS from missiles)
- PD coverage quality

---

## Archive Types

### Grid Archive (Simple)

Divide each behavior dimension into equal bins:
- 4 dimensions × 10 bins each = 10,000 cells
- 4 dimensions × 15 bins each = 50,625 cells

**Pros:** Simple, interpretable. **Cons:** Many cells may be impossible (no build exists with those behavior characteristics), wasting archive space.

### CVT Archive (Recommended for 4+ Dimensions)

Centroidal Voronoi Tessellation: place a fixed number of centroids in behavior space, each cell is the Voronoi region around a centroid.

```python
from ribs.archives import CVTArchive

archive = CVTArchive(
    solution_dim=build_vector_length,
    cells=5000,                          # Fixed number of cells
    ranges=[
        (0, 1500),   # Engagement range
        (0, 1),      # Survivability style
        (0, 1),      # Damage type profile
        (0, 1),      # Offense/defense ratio
    ],
    seed=42,
)
```

**Pros:** Decouples archive size from dimensionality. Handles arbitrary dimensions. **Cons:** Cells are not axis-aligned, harder to visualize.

### Sliding Boundaries (MAP-Elites-SB)

Adaptively adjusts bin boundaries based on the distribution of discovered solutions. Prevents the common problem where 80% of builds cluster in a narrow range while most cells remain empty.

**Best for:** When you don't know the natural distribution of builds along descriptor axes.

---

## QD Algorithm: CMA-MAE

### Why CMA-MAE Over Standard MAP-Elites

CMA-MAE (Covariance Matrix Adaptation MAP-Annealing) addresses three problems:

1. **Premature exploration**: Standard MAP-Elites/CMA-ME abandon fitness optimization too early in favor of diversity. CMA-MAE starts as a pure optimizer and gradually shifts to diversity.

2. **Flat objectives**: When many builds have similar fitness, CMA-ME struggles. CMA-MAE's annealing threshold handles this gracefully.

3. **Low-resolution archives**: CMA-ME has poor performance with coarse grids. CMA-MAE performs well regardless of grid resolution.

### How It Works

CMA-MAE introduces an **annealing threshold** per cell. A new solution must exceed the cell's threshold (not just the occupant's fitness) to count as an improvement:

```
acceptance_threshold[cell] = (1 - learning_rate) * acceptance_threshold[cell] 
                           + learning_rate * new_fitness
```

- Learning rate = 0: Pure CMA-ES (optimize fitness only)
- Learning rate = 1: Standard CMA-ME (diversity-first)
- Learning rate anneals from 0 → 1: smooth transition from optimization to illumination

---

## Handling Mixed Variables: CatCMA as Emitter

Standard CMA-ME uses CMA-ES emitters (continuous variables only). For our mixed categorical + integer + binary build space, we need a **CatCMA emitter**.

### CatCMA-ME Architecture

```python
from ribs.archives import CVTArchive
from ribs.schedulers import Scheduler

class CatCMAEmitter:
    """Custom emitter wrapping CatCMAwM for mixed-variable QD."""
    
    def __init__(self, archive, z_space, c_space, batch_size=16):
        self.archive = archive
        self.optimizer = CatCMAwM(
            x_space=None,
            z_space=z_space,    # Integer variables (vents, caps)
            c_space=c_space,    # Categorical variables (weapons, hullmods)
            population_size=batch_size,
        )
    
    def ask(self):
        """Generate batch of candidate builds."""
        candidates = []
        for _ in range(self.optimizer.population_size):
            x, z, c = self.optimizer.ask()
            candidates.append(encode_build(x, z, c))
        return candidates
    
    def tell(self, solutions, objectives, measures):
        """Update CatCMA based on archive improvement."""
        # Rank by archive improvement, not just fitness
        improvements = compute_archive_improvements(solutions, objectives, measures)
        self.optimizer.tell(
            [(decode_build(s), -imp) for s, imp in zip(solutions, improvements)]
        )

archive = CVTArchive(solution_dim=..., cells=5000, ranges=...)
emitter = CatCMAEmitter(archive, z_space=..., c_space=...)
scheduler = Scheduler(archive, [emitter])

for generation in range(max_generations):
    solutions = scheduler.ask()
    repaired = [repair_op_budget(s) for s in solutions]
    
    objectives = [evaluate(s) for s in repaired]      # Fitness
    measures = [compute_descriptors(s) for s in repaired]  # Behavior
    
    scheduler.tell(objectives, measures)
```

### Alternative: Continuous Relaxation + Rounding

If implementing a custom CatCMA emitter is too complex initially:

1. Encode all categoricals as continuous (weapon_id → float in [0, N_weapons])
2. Use standard CMA-ES emitters from pyribs
3. Round to nearest valid integer/categorical before evaluation
4. Works reasonably for small categorical spaces but is theoretically suboptimal

---

## Surrogate-Assisted QD

### The Data Efficiency Problem

MAP-Elites needs many evaluations to fill a large archive. With 5000 cells and simulation at ~25s each (wall-clock), filling even 50% of cells with 5 evals each = 12,500 simulations = ~90 hours on 8 instances.

### Solution: Two-Phase Approach

**Phase A — Heuristic Illumination (~1 hour, no simulation)**

```python
# Use heuristic scorer as fitness function (free evaluations)
for _ in range(500_000):
    solutions = scheduler.ask()
    repaired = [repair_build(s, hull, game_data) for s in solutions]
    objectives = [heuristic_score(s, hull, game_data).composite_score for s in repaired]
    measures = [compute_descriptors(s) for s in repaired]
    scheduler.tell(objectives, measures)

# Archive now has diverse builds optimized by heuristic
```

**Phase B — Simulation Validation Against Opponent Pool**

Each archive elite is evaluated against the full opponent pool (5-6 opponents from Phase 4). Fitness = average HP differential across opponents. This ensures builds are robust, not just good against one archetype.

```python
# Select elites from occupied cells
elites = [archive.get_elite(cell) for cell in archive.occupied_cells()]

# Evaluate against opponent pool (reuse Phase 4 infrastructure)
for elite in elites:
    matchups = [MatchupConfig(player=[elite], enemy=[opp]) for opp in opponent_pool]
    results = instance_pool.evaluate(matchups)
    sim_fitness = mean([r.hp_differential for r in results])
    archive.update(elite, sim_fitness, compute_descriptors(elite))
```

**Phase C — Surrogate Refinement (DSA-ME Pattern)**

Train a correction model (TabPFN at N<300, CatBoost at N>300) on Phase B sim results. Re-illuminate with `heuristic + correction` as fitness. Validate changed/new elites with simulation. 2-3 rounds until archive stabilizes.

```python
# Train correction model on (build_features, sim_score - heuristic_score)
correction = train_correction_model(
    X=build_features(validated_elites),
    y=sim_scores - heuristic_scores,
)

# Re-illuminate with corrected fitness (cheap — uses the model, not simulation)
corrected_scorer = lambda build: heuristic_score(build) + correction.predict(build)
# Run another CMA-MAE pass with corrected scorer...

# Validate new/changed elites with simulation
changed_elites = find_changed_cells(old_archive, new_archive)
sim_validate(changed_elites, opponent_pool)
```

### DSA-ME Pattern (Online Neural Surrogate)

From the Hearthstone deckbuilding paper (arXiv:2112.03534):

1. Initialize MAP-Elites with random builds
2. Train neural network on accumulated (build → sim_score) data
3. Use NN as fitness function for a burst of MAP-Elites iterations
4. Select most uncertain/promising builds for simulation
5. Add simulation results to training set
6. Retrain NN
7. Repeat

**Expected budget:** 2000-5000 simulation evaluations for a useful archive (~5-10 hours on 8 instances).

---

## Visualization

### 2D Heatmaps (Slicing the 4D Archive)

For each pair of behavior dimensions, plot a heatmap of fitness:

```
Engagement Range vs Survivability Style
┌──────────────────────────────────────┐
│  [Shield Sniper]  ←→  [Armor Sniper]│  Long
│         ↕                   ↕        │  Range
│  [Shield Brawler] ←→ [Armor Brawler]│  Close
└──────────────────────────────────────┘
   Shield-focused        Armor-focused
```

### Build Cards

For each archetype region, display the representative build:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━
 Eagle — SO Brawler
 Score: 8.7/10
━━━━━━━━━━━━━━━━━━━━━━━━━━
 Weapons:
   L: Heavy Mauler
   M: Assault Chaingun × 2
   S: PD Laser × 2
 Hullmods:
   Safety Overrides
   Heavy Armor
   Hardened Subsystems
 Flux: 22 vents, 8 caps
 OP: 150/150
━━━━━━━━━━━━━━━━━━━━━━━━━━
 DPS: 450 | EHP: 12,500
 Range: 380 | Flux Bal: 0.72
━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Libraries

| Library | Use | Link |
|---|---|---|
| **pyribs** | MAP-Elites archive, CMA-MAE, CVT, scheduling | [pyribs.org](https://pyribs.org/) |
| **cmaes** | CatCMAwM optimizer (emitter backend) | [GitHub](https://github.com/CyberAgentAILab/cmaes) |
| **QDax** | GPU-accelerated QD (if scaling to massive runs) | [GitHub](https://github.com/adaptive-intelligent-robotics/QDax) |

### pyribs Installation

```bash
pip install ribs[visualize]  # Includes visualization dependencies
```

### Key pyribs Classes

- `CVTArchive` — Centroidal Voronoi tessellation archive (recommended)
- `GridArchive` — Regular grid archive (simpler, for ≤3 dims)
- `EvolutionStrategyEmitter` — CMA-ES based emitter
- `Scheduler` — Coordinates emitters and archive

---

## Expected Outcomes

For a single hull (e.g., Eagle cruiser) with the full pipeline:

1. **Heuristic phase** discovers 50-100+ distinct archetype regions
2. **Simulation validation** confirms ~60-80% of heuristic-optimal builds are also sim-optimal
3. **Refinement** improves the remaining 20-40% where heuristic-sim correlation is weak
4. **Final archive** contains 200-500 validated diverse builds spanning all viable playstyles

This gives a player or game designer a **complete map of what's possible** with a given hull.
