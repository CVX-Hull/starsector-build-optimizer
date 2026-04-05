# System Architecture

Complete design for the Starsector Ship Build Optimizer system, covering all components from game integration to optimizer output.

---

## High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      Python Orchestrator                         │
│                                                                  │
│  ┌──────────────┐  ┌───────────────┐  ┌───────────────────────┐ │
│  │  Game Data    │  │  Heuristic    │  │  Optimizer Engine     │ │
│  │  Parser       │  │  Scorer       │  │  (Optuna/             │ │
│  │              │  │  (Fidelity 0) │  │   CatCMA/pyribs)      │ │
│  └──────┬───────┘  └───────┬───────┘  └──────────┬────────────┘ │
│         │                  │                      │              │
│  ┌──────┴──────────────────┴──────────────────────┴───────────┐ │
│  │                    Evaluation Pipeline                      │ │
│  │  Heuristic (0ms) → Full Sim + Curtailment (22-35s)        │ │
│  └────────────────────────────┬────────────────────────────────┘ │
│                               │                                  │
│  ┌────────────────────────────┴────────────────────────────────┐ │
│  │                   Instance Manager                          │ │
│  │  Launch / Monitor / Restart / Collect Results               │ │
│  └───────┬──────────┬──────────┬──────────┬────────────────────┘ │
└───��──────┼──────────┼──────────┼──────────┼──────────────────────┘
           ▼          ▼          ▼          ▼
    ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
    │ Starsector │ │ Starsector │ │ Starsector │ │ Starsector │
    │ Instance 0 │ │ Instance 1 │ │ Instance 2 │ │ Instance N │
    │ Xvfb :10   │ │ Xvfb :11   │ │ Xvfb :12   │ │ Xvfb :10+N │
    │ workdir/0/ │ │ workdir/1/ │ │ workdir/2/ │ │ workdir/N/ │
    └────────────┘ └────────────┘ └────────────┘ └────────────┘
```

---

## Component 1: Game Data Parser

### Purpose
Parse Starsector's data files into structured Python objects for use by the heuristic scorer, search space definition, and variant generator.

### Input Files

| File | Location | Format | Contents |
|---|---|---|---|
| `ship_data.csv` | `data/hulls/ship_data.csv` | CSV | Ship stats: HP, armor, flux, OP, speed, shields, etc. (~34 columns) |
| `*.ship` | `data/hulls/*.ship` | Loose JSON | Weapon slot definitions: ID, type, size, arc, angle, mount, position |
| `weapon_data.csv` | `data/weapons/weapon_data.csv` | CSV | Weapon stats: damage, flux cost, range, accuracy, OP cost (~34 columns) |
| `hull_mods.csv` | `data/hullmods/hull_mods.csv` | CSV | Hullmod definitions, OP costs by hull size |
| `ship_systems.csv` | `data/shipsystems/ship_systems.csv` | CSV | Ship system stats |

### Output Objects

```python
@dataclass
class ShipHull:
    id: str
    name: str
    hull_size: str  # FRIGATE, DESTROYER, CRUISER, CAPITAL
    hitpoints: float
    armor_rating: float
    max_flux: float
    flux_dissipation: float
    ordnance_points: int
    max_speed: float
    shield_type: str  # NONE, OMNI, FRONT, PHASE
    shield_arc: float
    shield_efficiency: float
    weapon_slots: list[WeaponSlot]
    built_in_mods: list[str]
    built_in_weapons: dict[str, str]  # slot_id -> weapon_id

@dataclass
class WeaponSlot:
    id: str
    slot_type: str  # BALLISTIC, ENERGY, MISSILE, HYBRID, COMPOSITE, SYNERGY, UNIVERSAL
    slot_size: str  # SMALL, MEDIUM, LARGE
    mount_type: str  # TURRET, HARDPOINT
    angle: float
    arc: float
    position: tuple[float, float]

@dataclass
class Weapon:
    id: str
    name: str
    size: str
    weapon_type: str  # BALLISTIC, ENERGY, MISSILE
    mount_type: str   # TURRET, HARDPOINT, etc.
    damage_per_shot: float
    damage_type: str  # KINETIC, HIGH_EXPLOSIVE, ENERGY, FRAGMENTATION
    flux_per_shot: float
    flux_per_second: float  # for beams
    range: float
    op_cost: int
    # ... burst, spread, ammo fields

@dataclass
class HullMod:
    id: str
    name: str
    op_cost_frigate: int
    op_cost_destroyer: int
    op_cost_cruiser: int
    op_cost_capital: int
    is_logistics: bool
    tags: list[str]
```

### Mod Support

The parser should accept a list of mod directories. Mods follow the same directory structure under `starsector/mods/<ModName>/data/`. Mod data overrides or extends vanilla data.

---

## Component 2: Search Space Definition

### Per-Hull Search Space

Given a `ShipHull`, generate the optimization search space:

```python
def build_search_space(hull: ShipHull, weapons: list[Weapon], hullmods: list[HullMod]):
    space = {}
    
    # Weapon slots: categorical, constrained by type/size
    for slot in hull.weapon_slots:
        compatible = get_compatible_weapons(slot, weapons)
        space[f"weapon_{slot.id}"] = Categorical(
            options=["empty"] + [w.id for w in compatible]
        )
    
    # Hullmods: binary toggles
    eligible_mods = get_eligible_mods(hull, hullmods)
    for mod in eligible_mods:
        space[f"hullmod_{mod.id}"] = Binary()
    
    # Flux allocation: parameterized as fraction of remaining OP
    space["vent_fraction"] = Float(0.0, 1.0)
    
    return space
```

### Constraint Handling via Repair

All constraints are enforced by `repair_build()` after the optimizer proposes a candidate. This is simpler and more robust than expressing constraints in the search space definition:

- **OP budget:** Greedy drop of lowest value-per-OP items until feasible
- **Hullmod incompatibilities:** Remove the lower-value hullmod from each conflicting pair
- **Logistics limit:** Keep max 3 logistics hullmods
- **Slot compatibility:** Already enforced in search space (only eligible weapons per slot)

Optuna's `constraints_func` reports OP budget violation to bias TPE sampling away from infeasible regions (c-TPE approach), reducing wasted repair operations over time.

### Repair Operator

Applied before every evaluation to enforce the OP budget:

```python
def repair_build(build: dict, hull: ShipHull) -> dict:
    """Greedy repair: drop lowest value-per-OP items until budget met."""
    total_op = compute_total_op(build, hull)
    
    while total_op > hull.ordnance_points:
        # Find item with lowest value-per-OP ratio
        worst_item = find_worst_value_per_op(build)
        if worst_item.startswith("weapon_"):
            build[worst_item] = "empty"
        elif worst_item.startswith("hullmod_"):
            build[worst_item] = False
        total_op = compute_total_op(build, hull)
    
    # Allocate remaining OP to vents/caps
    remaining = hull.ordnance_points - total_op
    vents = round(build["vent_fraction"] * remaining)
    caps = remaining - vents
    build["vents"] = min(vents, hull.max_vents)
    build["caps"] = min(caps, hull.max_caps)
    
    return build
```

---

## Component 3: Heuristic Scorer (Fidelity 0)

### Static Metrics (computed in ~0ms)

```python
def heuristic_score(build: dict, hull: ShipHull, enemy: ShipHull) -> float:
    weapons = get_equipped_weapons(build)
    
    # Flux balance (most predictive single metric)
    weapon_flux_gen = sum(w.flux_per_second for w in weapons)
    dissipation = hull.flux_dissipation + build["vents"] * 10
    flux_balance = weapon_flux_gen / max(dissipation, 1)
    
    # DPS by damage type
    kinetic_dps = sum(w.sustained_dps for w in weapons if w.damage_type == "KINETIC")
    he_dps = sum(w.sustained_dps for w in weapons if w.damage_type == "HIGH_EXPLOSIVE")
    energy_dps = sum(w.sustained_dps for w in weapons if w.damage_type == "ENERGY")
    
    # Effective HP
    armor_ehp = compute_armor_ehp(hull, build)
    shield_ehp = compute_shield_ehp(hull, build)
    total_ehp = armor_ehp + shield_ehp + hull.hitpoints
    
    # Range coherence (penalty for mismatched weapon ranges)
    ranges = [w.range for w in weapons]
    range_coherence = 1.0 - (np.std(ranges) / np.mean(ranges)) if ranges else 0
    
    # Composite score (weights calibrated via regression on sim data)
    score = (w1 * normalize(kinetic_dps + he_dps + energy_dps)
           + w2 * normalize(flux_efficiency(weapons))
           + w3 * normalize(total_ehp)
           + w4 * range_coherence
           + w5 * flux_balance_score(flux_balance)
           - w6 * overflux_penalty(flux_balance))
    
    return score
```

### Calibration

Run 200-300 diverse builds through full simulation. Fit linear regression from static metrics to simulation outcomes. This gives data-driven weights `w1..w6`. Expected R² ≈ 0.5-0.7.

---

## Component 4: Java Combat Harness Mod

### Mod Structure

```
starsector-build-optimizer/
├── mod_info.json
├── data/
│   └── config/
│       └── settings.json  (registers EveryFrameCombatPlugin)
└── jars/
    └── build-optimizer.jar
```

### Core Classes

**QueueProcessor** — reads matchup queue, sets up missions:
```java
// Reads from: workdir/queue.json
// Writes to: workdir/results/matchup_001.json
[{
    "id": "matchup_001",
    "player_variants": ["eagle_test_v42"],
    "enemy_variants": ["dominator_Standard"],
    "time_limit": 60,
    "replicates": 3
}]
```

**CombatHarnessPlugin** (implements EveryFrameCombatPlugin):
```java
public class CombatHarnessPlugin implements EveryFrameCombatPlugin {
    public void init(CombatEngineAPI engine) {
        // Set time multiplier for speedup
        engine.getTimeMult().modifyMult("optimizer", 3.0f);
        // Register damage listener
        engine.addPlugin(new DamageTracker());
    }
    
    public void advance(float amount, List<InputEventAPI> events) {
        if (engine.isCombatOver() || elapsed > timeLimit) {
            collectResults();
            writeResultsToFile();
            advanceToNextMatchup();
        }
    }
}
```

**DamageTracker** (implements DamageListener):
```java
// Tracks per-weapon, per-ship damage events
// Records: damage dealt, damage type, source weapon, target ship, timestamp
// Aggregates: total DPS by type, flux generated/dissipated, time-to-kill
```

### Result JSON Schema

```json
{
    "matchup_id": "matchup_001",
    "replicate": 1,
    "winner": "PLAYER",
    "duration_seconds": 45.2,
    "player_stats": {
        "hull_remaining_pct": 0.72,
        "armor_remaining_pct": 0.45,
        "total_damage_dealt": 28500,
        "damage_by_type": {"KINETIC": 12000, "HIGH_EXPLOSIVE": 10500, "ENERGY": 6000},
        "flux_generated": 42000,
        "flux_dissipated": 38000,
        "time_at_max_flux_pct": 0.15,
        "overload_count": 0,
        "weapons_disabled": 0
    },
    "enemy_stats": { ... }
}
```

### Variant Generation

The Python orchestrator writes `.variant` files for each candidate build:

```json
{
    "variantId": "eagle_test_v42",
    "hullId": "eagle",
    "displayName": "Optimizer Build #42",
    "fluxVents": 15,
    "fluxCapacitors": 10,
    "hullMods": ["heavyarmor", "hardenedshieldemitter"],
    "weaponGroups": [
        {
            "autofire": true,
            "mode": "LINKED",
            "weapons": {
                "WS 001": "heavymauler",
                "WS 002": "heavyblaster"
            }
        }
    ]
}
```

---

## Component 5: Instance Manager

### Responsibilities

1. **Launch**: Start N Starsector instances, each with its own Xvfb display and working directory
2. **Monitor**: Detect hung/crashed instances via heartbeat files
3. **Distribute**: Write matchup queues to per-instance working directories
4. **Collect**: Read result JSONs as they appear
5. **Restart**: Respawn crashed instances automatically

### Per-Instance Setup

```bash
# Instance i gets:
DISPLAY=:${10+i}
WORKDIR=workdir/instance_${i}/

# Symlink game install, copy per-instance data
ln -s /path/to/starsector ${WORKDIR}/starsector
cp -r mod_data ${WORKDIR}/mods/build-optimizer/

# Launch Xvfb
Xvfb :${10+i} -screen 0 1024x768x24 &

# Launch Starsector
cd ${WORKDIR} && DISPLAY=:${10+i} ./starsector.sh &
```

### Memory Budget

| Component | Per Instance |
|---|---|
| JVM heap | ~1-2 GB (configurable in vmparams) |
| LWJGL/native | ~200-400 MB |
| Xvfb framebuffer | ~50-100 MB |
| **Total** | **~1.5-2.5 GB** |

32 GB machine → ~12-16 instances. 64 GB → ~25-30.

### Throughput Estimates

| Speed | Combat Duration | Replicates | Per Instance/Hour | 16 Instances/Hour |
|---|---|---|---|---|
| 3x | 60s game / 20s real | 1 | ~180 | ~2,880 |
| 3x | 60s game / 20s real | 3 | ~60 | ~960 |
| 5x | 60s game / 12s real | 3 | ~100 | ~1,600 |
| 3x | 15s game / 5s real | 1 | ~720 | ~11,520 |

---

## Component 6: Optimizer Engine

### Primary: Optuna TPE

```python
import optuna
from optuna.samplers import TPESampler
from optuna.pruners import WilcoxonPruner

sampler = TPESampler(
    multivariate=True,
    constant_liar=True,       # Batch parallelism for 4-8 instances
    n_ei_candidates=256,      # Default 24 too few for 70D
    n_startup_trials=100,     # Default 10 too few for 70D
)
pruner = WilcoxonPruner(p_threshold=0.1)

study = optuna.create_study(
    sampler=sampler,
    pruner=pruner,
    direction="maximize",
    storage="sqlite:///study.db",
)

# Warm-start with heuristic
warm_start(study, hull, game_data, config)

# Ask-tell loop with repair + Lamarckian recording
for _ in range(sim_budget):
    trial = study.ask(distributions)
    raw_build = trial_params_to_build(trial.params, hull_id)
    repaired = repair_build(raw_build, hull, game_data)
    score = evaluate_against_opponent_pool(repaired)
    study.add_trial(create_trial(
        params=build_to_trial_params(repaired, space),  # Lamarckian
        distributions=distributions,
        values=[score],
    ))
```

### Quality-Diversity: pyribs + CatCMA

```python
from ribs.archives import CVTArchive
from ribs.emitters import EvolutionStrategyEmitter
from ribs.schedulers import Scheduler

archive = CVTArchive(
    solution_dim=total_dims,
    cells=5000,
    ranges=[(range_min, range_max)] * 4,  # 4 behavior dimensions
)

# Custom CatCMA emitter (wraps cmaes.CatCMAwM)
emitter = CatCMAEmitter(archive, ...)

scheduler = Scheduler(archive, [emitter])

for _ in range(n_generations):
    solutions = scheduler.ask()
    repaired = [repair_build(s) for s in solutions]
    objectives, measures = evaluate_batch(repaired)
    scheduler.tell(objectives, measures)
```

---

## Data Flow Summary

```
1. Parse game data → ShipHull, Weapon, HullMod objects
2. Build search space for target hull
3. Optimizer proposes candidate builds
4. Repair operator enforces OP budget
5. Heuristic scorer provides Fidelity 0 score
6. If promoted: generate .variant file, queue for simulation
7. Instance manager distributes to Starsector instances
8. Combat harness runs matchup, writes result JSON
9. Instance manager collects results
10. Optimizer updates surrogate, proposes next batch
11. Repeat until budget exhausted
12. Output: ranked builds (Tier A) or archive of diverse builds (Tier B)
```

---

## Deployment Modes

### Mode 1: Heuristic-Only (No Game Required)
- Parse game data files only
- Use heuristic scorer for all evaluations
- Run MAP-Elites or BO with heuristic as objective
- Produces approximate results in minutes
- Good for exploration and prototyping

### Mode 2: Single-Instance Development
- One Starsector instance (can use a real display)
- Sequential evaluation
- Good for testing the combat harness and result collection

### Mode 3: Full Parallel Production
- N Starsector instances with Xvfb
- Heuristic warm-start → Full sim with curtailment
- Optuna TPE with constant_liar (batch size = N instances)
- Full throughput

---

## Error Handling

| Failure Mode | Detection | Recovery |
|---|---|---|
| Instance crash (JVM) | No heartbeat for >60s | Restart instance, re-queue matchup |
| Combat hang (infinite loop) | Matchup exceeds 2x expected time | Kill via Backspace key injection, record as timeout |
| Invalid variant (bad weapon/slot) | Game logs error on load | Skip matchup, log constraint violation |
| Xvfb crash | Instance can't render | Restart Xvfb, restart instance |
| Out of memory | JVM OOM error | Reduce instances, increase per-instance heap |
| Disk full | Write fails | Alert, clean old results |
