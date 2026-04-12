# Throughput Optimization — Research Findings and Recommended Approach

Maximizing combat simulation throughput through persistent game sessions, programmatic variant creation, intelligent batch scheduling, and cloud scaling. This document is self-contained — it explains the current architecture, identifies bottlenecks, presents research findings, and recommends a phased implementation plan.

---

## 1. Current Architecture

### How Simulation Works (Phase 3 + Phase 4)

The optimizer evaluates ship builds by running AI-vs-AI combat in parallel Starsector game instances:

1. **Python writes** a matchup queue (JSON) and .variant files to per-instance work directories
2. **Game launches** — JVM starts, Starsector loads assets, launcher GUI appears
3. **Launcher click** — `xdotool` clicks "Play Starsector" on the Swing launcher window
4. **Title screen** — `TitleScreenPlugin` waits ~2s for stability, then triggers `MenuNavigator`
5. **Menu navigation** — `java.awt.Robot` clicks through: Missions → scroll → Optimizer Arena → Play Mission (~5s of Thread.sleep delays)
6. **MissionDefinition** runs — reads queue from `saves/common/`, deploys first matchup's ships
7. **CombatHarnessPlugin** state machine: INIT → SPAWNING → FIGHTING → CLEANING → ... → DONE
8. **Between matchups** — entity cleanup (remove ships, projectiles, missiles), spawn new ships
9. **After all matchups** — `ResultWriter` writes results + done signal, then `System.exit(0)`
10. **Python detects** done signal, parses results, kills processes, assigns next batch

Each game instance runs on its own Xvfb virtual display (1920x1080x24) with Mesa/llvmpipe software rendering. The `InstancePool` manages 4-8 parallel instances with health monitoring via heartbeat files.

### File Protocol

| File | Written By | Read By | Purpose |
|------|-----------|---------|---------|
| `combat_harness_queue.json.data` | Python | Java | Batch of matchup configs |
| `*.variant` files in `data/variants/` | Python | Java (at load) | Ship build specifications |
| `combat_harness_results.json.data` | Java | Python | Combat results array |
| `combat_harness_done.data` | Java | Python | Completion signal |
| `combat_harness_heartbeat.txt.data` | Java | Python | Liveness + HP telemetry |
| `combat_harness_stop.data` | Python | Java | Curtailment stop signal |

### Per-Instance Work Directory

Symlinks to shared game files (~20GB read-only), real directories for `saves/`, `data/config/`, `data/variants/`, and `mods/` (~4MB per instance). Created by `InstancePool._create_work_dir()`.

### Current Timing Breakdown

| Phase | Duration | Notes |
|-------|----------|-------|
| JVM startup + asset loading | ~15-20s | Class loading, graphics, ship/weapon data |
| Launcher GUI + xdotool click | ~5-10s | Swing window detection + click |
| Title screen stabilization | ~2s | TitleScreenPlugin waits 120 frames |
| Menu navigation (Robot clicks) | ~5s | Missions → scroll → Arena → Play Mission |
| Mission setup | ~1s | MissionDefinition + first spawn |
| **Total startup overhead** | **~25-35s** | Steps 2-6, repeated every batch |
| Combat per matchup | ~10s | Wall-clock at 3x speed with curtailment |
| Combat per batch (6 matchups) | ~60s | One build × 5 opponents |
| **Total per batch** | **~85-95s** | 35s startup + 60s combat |
| **Startup overhead fraction** | **~37%** | 35 / 95 of wall-clock time |

### Current Throughput (async parallel dispatch)

Measured on wolf hull, 10 trials, 5 active opponents (2026-04-12):

| Instances | Wall time | Sim-only time | Sim speedup | Efficiency |
|-----------|-----------|---------------|-------------|------------|
| 1 | 5m07s | 272s | 1.0x | — |
| 2 | 3m34s | 182s | 1.49x | 75% |
| 4 | 2m52s | 139s | 1.96x | 49% |

Instance dispatch is perfectly balanced (equal matchups per instance). Sub-linear scaling at 4 instances is due to limited queue depth with 10 trials — at most 2-3 builds in queue simultaneously. Larger budgets (200+ trials) keep the queue fuller, improving efficiency toward 3-4x.

Previous serial design (batch `evaluate()`) used only 1 of 4 instances: 210 trials in 14 hours (~15 trials/hour). Async dispatch expected to deliver ~40-60 trials/hour with 4 instances.

---

## 2. Bottleneck Analysis

### Bottleneck 1: Game Restart Per Batch (37% overhead)

Each game instance persists across matchups via the `new_queue` signal protocol. The instance processes one matchup at a time, signaling done after each. The coordinator dispatches the next matchup immediately via `run_matchup()`. A clean restart (`clean_restart_matchups=200`) is forced periodically to prevent memory accumulation.

**Persistent sessions eliminate restart overhead.** With `run_matchup()`, each matchup is dispatched to an already-running game instance. The file I/O overhead (queue write + signal + poll) is ~ms, negligible vs 10-120s combat time.

### Bottleneck 2: Variant File I/O

Each build evaluation requires:
1. Python generates `.variant` JSON and writes it to every instance's `data/variants/`
2. Game loads variant files at startup (cached — new files not visible mid-session)
3. Between optimizer batches, `clean_optimizer_variants()` removes accumulated files

This creates:
- Disk I/O proportional to `num_instances × builds_per_batch`
- Variant accumulation that slows game startup over time
- A dependency between Python file writes and Java file reads that complicates persistent sessions

### Bottleneck 3: Instance Idle Time During Pruning

With Phase 5 sequential evaluation, when a build is pruned after 1-2 opponents, the instance finishes early and sits idle until Python assigns the next batch and a new game launches. The startup overhead for the next build erodes the time saved by pruning.

---

## 3. Research Findings

### 3.1 Programmatic Variant Creation (API Discovery)

Decompiling the Starsector 0.98a API (`starfarer.api.jar`) revealed that the game supports full programmatic variant construction:

```java
// SettingsAPI methods (confirmed via javap decompilation)
ShipHullSpecAPI getHullSpec(String hullId);
ShipVariantAPI createEmptyVariant(String variantId, ShipHullSpecAPI hullSpec);
FleetMemberAPI createFleetMember(FleetMemberType type, ShipVariantAPI variant);
boolean doesVariantExist(String variantId);

// ShipVariantAPI methods for populating the variant
void addWeapon(String slotId, String weaponId);
void addMod(String hullmodId);
void setNumFluxVents(int vents);
void setNumFluxCapacitors(int capacitors);
void autoGenerateWeaponGroups();
void clearHullMods();
void clearSlot(String slotId);

// CombatFleetManagerAPI for spawning
ShipAPI spawnFleetMember(FleetMemberAPI member, Vector2f location, float facing, float delay);

// MissionDefinitionAPI for initial fleet setup
void addFleetMember(FleetSide side, FleetMemberAPI member);
```

**Full construction chain:**
```java
ShipHullSpecAPI hull = Global.getSettings().getHullSpec("eagle");
ShipVariantAPI variant = Global.getSettings().createEmptyVariant("eagle_opt_001", hull);
variant.addWeapon("WS 001", "heavymauler");
variant.addMod("hardened_shields");
variant.setNumFluxVents(20);
variant.setNumFluxCapacitors(10);
variant.autoGenerateWeaponGroups();

FleetMemberAPI member = Global.getSettings().createFleetMember(FleetMemberType.SHIP, variant);
ShipAPI ship = engine.getFleetManager(FleetSide.PLAYER)
    .spawnFleetMember(member, new Vector2f(-2000f, 0f), 0f, 0f);
```

**Impact:** Eliminates all .variant file I/O. Build specifications can be sent as JSON fields within the matchup queue itself. The Java harness constructs variants in memory at spawn time. No file writes, no file caching concerns, no variant accumulation, no `clean_optimizer_variants()`.

### 3.2 Persistent Game Session

Instead of `System.exit(0)` after each batch, the combat harness can stay running and poll for new work.

**Approach A — Stay in combat, poll for new queue (highest throughput):**

The `CombatHarnessPlugin` already uses `engine.setDoNotEndCombat(true)` to prevent auto-ending. After all matchups complete, instead of exiting, it can enter a WAITING state:

```
Current:  INIT → SPAWNING → FIGHTING → CLEANING → ... → DONE → System.exit(0)
Proposed: INIT → SPAWNING → FIGHTING → CLEANING → ... → DONE → WAITING → SPAWNING → ...
```

In WAITING, the plugin polls `saves/common/` for a new queue signal (via `fileExistsInCommon()`). When detected, it loads the new queue and transitions back to SPAWNING.

With programmatic variant creation, the variant caching problem is eliminated — no files to cache. Build specs arrive as JSON in the queue and are constructed in memory at spawn time.

**Engine state accumulation concerns:**
- `FleetManager.getAllEverDeployedCopy()` accumulates across matchups (confirmed). We track ships directly and don't use this method, so it's not a functional issue, but memory grows.
- No `reset()` or `clear()` on `CombatFleetManagerAPI`.
- Listener cleanup works (we already `removeListener()` per DamageTracker).
- Ship/projectile/missile cleanup works (we already `removeEntity()` on all).
- Particle effects and sound system have no cleanup API — potential slow leak.
- `engine.getCustomData()` map may accumulate internal state.

**Approach B — Robot-click mission re-entry (clean state, moderate overhead):**

After batch completes, call `engine.endCombat()` instead of `System.exit(0)`. The game shows a results screen. `java.awt.Robot` clicks dismiss → returns to mission select → clicks "Play Mission" → `MissionDefinition.defineMission()` runs again with new queue → fresh `CombatEngineAPI`.

Overhead: ~5-8s (vs 25-35s for full restart). Gives completely clean engine state.

**Approach C — Hybrid (recommended):**

Stay in combat for N matchups (~100-200), then Robot-click restart for clean state. Bounds memory accumulation while keeping startup overhead near zero for most matchups.

**Approach D — Launcher bypass:**

Some modders have found JVM flags or config changes that skip the launcher GUI. This shaves ~5-10s from a full restart but doesn't eliminate JVM/asset loading. Minor optimization on top of the others.

### 3.3 Mixed-Build Batching with ASHA Scheduling

Phase 5's sequential opponent evaluation requires a new scheduling strategy. Three options were analyzed:

**Option A — Instance-local sequential evaluation:**
Each instance evaluates one build, running opponents sequentially. After each, Python signals continue/prune.

- Pro: No startup overhead for pruned opponents
- Con: When pruned early, instance sits idle until next game restart
- Throughput: ~554 trials/hr (+44% vs current)

**Option B — Single-matchup scheduling:**
Each game launch evaluates exactly 1 matchup. Maximum pruning granularity.

- Con: 35s startup + 10s combat = 78% overhead — **worse than current**
- Throughput: ~305 trials/hr (-21%)
- **Rejected.**

**Option C — Mixed-build batching (recommended):**
Each game launch gets a batch of matchups from **different builds at different stages**. The Java harness doesn't know or care which build a matchup belongs to — it runs them all.

- Python maintains a priority queue of `(trial, build, next_opponent_index)`
- ASHA rung-priority: builds closer to completion are scheduled first, remaining slots filled with new builds at rung 0
- When a build is pruned, its slots are immediately filled with other work
- Instance utilization stays at ~90%+ because there's always a mix of work available

The existing Java harness already supports this — `CombatHarnessPlugin` processes `MatchupQueue` entries agnostically. The `matchup_id` field routes results back to the correct trial in Python.

**Throughput comparison (8 instances, 50% pruned after opponent 1):**

| Strategy | Trials/hr | Instance Utilization | Java Changes |
|----------|-----------|---------------------|--------------|
| Current (all opponents per build) | ~384 | ~63% | — |
| Instance-local sequential (A) | ~554 | ~52% | Medium |
| Single-matchup scheduling (B) | ~305 | ~29% | None |
| **Mixed-build batching (C)** | **~753** | **~90%** | **None** |

**Optuna integration:** The ask-tell pattern supports this directly. Use `MedianPruner` (not HyperbandPruner — only 5 steps are too few for Hyperband's bracket structure):

```python
study = optuna.create_study(
    pruner=optuna.pruners.MedianPruner(n_startup_trials=20, n_warmup_steps=0)
)

trial = study.ask(distributions)
# After each opponent result:
trial.report(normalized_cumulative_fitness, step=opponent_index)
if trial.should_prune():
    study.tell(trial, state=TrialState.PRUNED)
else:
    # Continue to next opponent...
    study.tell(trial, final_fitness)  # After all opponents
```

**ASHA reference:** The Asynchronous Successive Halving Algorithm (Li et al., 2018) directly addresses the scheduling of multi-fidelity evaluations across parallel workers. The key principle: when composing a batch, prioritize higher-rung work (builds with more investment), fill remaining slots with rung-0 work (new builds).

**Common Random Numbers compatibility:** CRN requires identical seeds when comparing builds against the same opponent. With mixed-build batching, each matchup carries its own `(opponent_id, seed)` pair. Seeds are per-opponent, not per-batch. No conflict.

### 3.4 Cloud Simulation Infrastructure

**Key findings:**

**No GPU needed.** The current local setup already uses Xvfb + Mesa/llvmpipe (CPU software rendering). Starsector is CPU-bound for simulation logic; rendering is secondary. GPU instances cost 3-10x more for zero benefit.

**ARM instances won't work.** Starsector ships its own x86_64 JRE (`jre_linux/`) and native LWJGL libraries. Running on ARM (AWS Graviton, etc.) would require replacing the JRE and native libraries — not practical.

**Docker is the right deployment model.** Bake the game files (~20GB, read-only) into a Docker image. Per-container writable directories for `saves/`, `data/config/`. Near-zero overhead for CPU-bound JVM workloads.

**Spot instances are ideal.** The workload is fault-tolerant (crashed batches are re-queued), short-lived (3-4 hours), and flexible on instance type.

**Xvfb alternatives assessed:**

| Approach | Viable | Recommendation |
|----------|--------|----------------|
| Xvfb (current) | Yes | **Use this.** Proven, lightweight. |
| Xdummy | Yes | No advantage over Xvfb. |
| EGL headless | Theoretically | LWJGL/Starsector expects GLX, would require patching. |
| No display server | No | LWJGL requires a display context. |
| VirtualGL + GPU | Overkill | GPU cost for zero benefit. |

**Cloud cost estimates (per single-hull optimization run, ~1500 matchups):**

| Scale | Instances | VMs (c7i.2xlarge) | Spot Cost | Wall Time |
|-------|-----------|-------------------|-----------|-----------|
| Local (current) | 8 | — | $0 | 3.5 hr |
| Small cloud | 16 | 4 | ~$1.70 | 1.75 hr |
| Medium cloud | 32 | 7 | ~$3.00 | ~50 min |
| Large cloud | 64 | 13 | ~$5.50 | ~25 min |

These assume current throughput. With persistent sessions + mixed batching, times drop by 2-3x further.

**Game AI research patterns:** OpenAI Five (Dota 2) and AlphaStar (StarCraft II) both used CPU-only game simulation at massive scale, but both games have native headless modes. For games without headless support, Xvfb + Docker is the standard approach (also used in browser testing, CI/CD, and game bots).

---

## 4. Recommended Approach

### Design Principle: Bitter Lesson Compliance

Every change must be:
- **General** — works for any hull, any opponent pool, any game version
- **Automatic** — no per-hull or per-machine tuning
- **Scalable** — benefits compound with more instances and more compute

### Implementation Phases

#### Phase T1: Programmatic Variant Creation (IMPLEMENTED)

Modify the combat harness to accept build specifications as JSON fields within the matchup queue, construct `ShipVariantAPI` objects in memory, and spawn via `spawnFleetMember()`.

**MatchupConfig changes:**
```json
{
"matchup_id": "eagle_000042_vs_dominator_Assault",
"player_builds": [{
"hull_id": "eagle",
"weapon_assignments": {"WS 001": "heavymauler", "WS 002": "hveldriver"},
"hullmods": ["hardened_shields", "expanded_missile_racks"],
"flux_vents": 20,
"flux_capacitors": 10
}],
"enemy_variants": ["dominator_Assault"],
"time_limit_seconds": 180,
"time_mult": 3.0
}
```

Player builds are constructed programmatically. Enemy variants (stock opponents) continue to use variant IDs loaded at game startup.

**Python-side impact:**
- Remove `write_variant_to_all()` calls from `evaluate_build()` / `optimize_hull()`
- Remove `clean_optimizer_variants()`
- Include build specs in matchup queue JSON instead of variant file references
- `InstancePool._create_work_dir()` no longer needs variant directory management

#### Phase T2: Persistent Game Session (Java change, highest throughput impact) — IMPLEMENTED

Replace `System.exit(0)` in `CombatHarnessPlugin.doDone()` with WAITING state. Add periodic clean restart via Robot-click re-entry.

**New state machine:**
```
INIT → SPAWNING → FIGHTING → CLEANING → ... → DONE → WAITING
                                                 ↑        |
                                                 |  (new queue detected)
                                                 └────────┘
```

**WAITING state behavior:**
1. Write results + done signal (existing behavior)
2. Poll `fileExistsInCommon("combat_harness_new_queue")` every ~60 frames
3. When detected: delete signal file, load new queue, reset state, transition to SPAWNING
4. If no new queue after configurable timeout (e.g., 60s): `System.exit(0)` for clean shutdown

**Python-side protocol change:**
1. Detect done signal, read results (existing)
2. Write new queue file to `saves/common/`
3. Write `combat_harness_new_queue.data` signal file
4. Resume polling for next done signal

**Clean restart trigger:** After N matchups (configurable, default ~200), Python writes a `combat_harness_shutdown.data` signal instead of a new queue. The harness exits cleanly. Python restarts the game for a fresh engine state.

#### Phase T3: Mixed-Build Batching / Staged Evaluator (Python change, required for Phase 5)

New `StagedEvaluator` class in `optimizer.py` that replaces the current flat `evaluate all matchups` pattern:

**Core data structures:**
- Priority queue of `(rung, trial, build, next_opponent_index)` — higher rung = more investment = higher priority
- In-flight map: `matchup_id → (trial, build, opponent_index)` for routing results
- Running opponent statistics for normalization (Phase 5A)

**Batch composition (ASHA-style):**
1. When an instance needs work, compose a batch of N matchups
2. First: promote builds at highest rungs (closest to completion)
3. Then: fill remaining slots with new builds at rung 0 (ask new trials from Optuna)
4. Mix freely across builds — each matchup is independent

**Result routing:**
1. Instance returns results for N matchups
2. Route each result to its trial via `matchup_id`
3. Call `trial.report(normalized_fitness, step=opponent_index)`
4. If `trial.should_prune()`: tell Optuna, discard remaining opponents
5. If not pruned: enqueue build for next opponent

**Instance pool interaction:** `InstancePool.evaluate()` is unchanged — it receives a list of matchups and returns results. The staged evaluator composes and routes; the pool executes.

#### Phase T4: Cloud Deployment (Infrastructure, when local isn't enough)

**Dockerfile:**
```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    xvfb xdotool libgl1 libasound2t64 libxi6 libxrender1 libxtst6 libxrandr2
COPY game/ /opt/starsector/
COPY .java/ /root/.java/
COPY optimizer/ /opt/optimizer/
```

**Deployment model:**
- AWS EC2 Spot Fleet or Hetzner Cloud (existing spec)
- c7i.2xlarge (8 vCPU, 16GB RAM) fits 5 game instances per VM
- Docker Compose launches N containers per VM, each with unique DISPLAY
- Coordinator (local machine or t3.small) distributes matchup queues

**No Kubernetes at this scale.** 8-64 instances with 3-4 hour runs doesn't justify the operational complexity.

---

## 5. Expected Impact

### Per-Phase Improvements

| Metric | Current | +Phase T1 | +Phase T2 | +Phase T3 | +Phase T4 (32 inst) |
|--------|---------|-----------|-----------|-----------|---------------------|
| Startup overhead | 37% | 37% | ~2% | ~2% | ~2% |
| Variant I/O | Per-build writes | **None** | None | None | None |
| Matchups per build | 5.0 (fixed) | 5.0 | 5.0 | **~2.7** (pruning) | ~2.7 |
| Instance utilization | ~63% | ~63% | **~95%** | **~90%** | ~90% |
| Trials/hr (8 inst) | ~384 | ~400 | **~600** | **~1200** | — |
| Trials/hr (32 inst) | — | — | — | — | **~5000** |
| Time per hull | 3.5 hr | 3.3 hr | 2.2 hr | **1.1 hr** | **~15 min** |

### Throughput Math

**Current:** 8 instances × (3600s / (35s startup + 60s combat)) × 1 build/batch = ~384 builds/hr

**With Phase T2 (persistent session):** 8 instances × (3600s / (0s startup + 60s combat)) × 1 build/batch ≈ 480 builds/hr. Actual throughput higher because batches can be larger without restart penalty.

**With Phase T3 (mixed batching + pruning):** Average 2.7 matchups per build (50% pruned after opponent 1). Each instance runs ~6 matchups per batch in ~60s. 8 instances × (3600/60) × 6 matchups / 2.7 matchups/build ≈ 1067 builds/hr. With pipeline overlap and minimal startup: ~1200 builds/hr.

**With Phase T4 (32 instances):** Linear scaling: ~1200 × (32/8) = ~4800 builds/hr.

---

## 6. Interaction with Other Phases

**Phase 5 (Signal Quality):** Mixed-build batching (Phase T3) is a prerequisite for Phase 5B (sequential opponent evaluation with Hyperband pruning). The staged evaluator also naturally supports Phase 5A (opponent normalization — z-score as results arrive), Phase 5C (curriculum learning — easy opponents as rung 0), and Phase 5D (CRN — seed field in MatchupConfig).

**Phase 4 (Optimizer):** The `optimize_hull()` function's inner loop changes from flat batch evaluation to staged evaluation. The ask-tell interface with Optuna is preserved. `OptimizerConfig` gains new fields for staged evaluation parameters.

**Phase 6 (Quality-Diversity):** Higher throughput directly benefits MAP-Elites archive construction. More evaluations per hour means faster archive filling and more refinement rounds.

**Phase 7 (Neural Surrogate):** Richer per-matchup telemetry from the staged evaluator (individual opponent results with timestamps) provides better training data for surrogate models.

---

## 7. Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Engine memory leak in persistent session | Medium | Medium | Periodic clean restart every ~200 matchups |
| `createEmptyVariant()` doesn't register for `spawnShipOrWing()` | High | Low | Use `spawnFleetMember(FleetMemberAPI, ...)` path instead (confirmed available) |
| `readTextFileFromCommon()` caches reads | Medium | Low | Heartbeat polling already depends on fresh reads; would have broken Phase 3 |
| Robot-click coordinates shift between game versions | Low | Medium | Coordinates are calibrated once per version; already accepted for MenuNavigator |
| Mixed-build batching increases result routing complexity | Low | Medium | `matchup_id` already carries enough information for routing |
| Spot instance interruption during batch | Low | Low | Workload is fault-tolerant; re-queue incomplete matchups |

### Empirical Validation Needed

Before full implementation, two tests should be run:

1. **Programmatic variant spawning:** Create a variant via `createEmptyVariant()` + `addWeapon()` etc., create a FleetMember, spawn via `spawnFleetMember()`. Verify the ship appears correctly with the right weapons/hullmods.

2. **Long-running session stability:** Run 100+ matchups in a single combat session (current max tested: 6). Monitor memory usage, frame rate, and result correctness over time.

---

## 8. Key References

### Starsector Modding API
- `ShipVariantAPI` — programmatic variant construction (addWeapon, addMod, setNumFluxVents)
- `SettingsAPI.createEmptyVariant()` — create variant in memory without file I/O
- `SettingsAPI.createFleetMember()` — create fleet member from ShipVariantAPI
- `CombatFleetManagerAPI.spawnFleetMember()` — spawn ship from FleetMemberAPI
- `MissionDefinitionAPI.addFleetMember()` — add pre-constructed fleet member to initial fleet
- `CombatEngineAPI.endCombat()` — end combat session (for Robot-click restart path)
- Security sandbox: `java.io.File` blocked, `java.awt.Robot` allowed, SettingsAPI for all file I/O

### Scheduling / Multi-Fidelity
- Li, L. et al. (2018). "Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization." JMLR.
- Li, L. et al. (2018). "A System for Massively Parallel Hyperparameter Tuning." arXiv:1810.05934 (ASHA).
- Falkner, S. et al. (2018). "BOHB: Robust and Efficient Hyperparameter Optimization at Scale." ICML.

### Optuna Integration
- Optuna ask-tell interface: `study.ask()` / `trial.report()` / `trial.should_prune()` / `study.tell()`
- `MedianPruner(n_startup_trials=20, n_warmup_steps=0)` — appropriate for 5-step opponent evaluation
- `constant_liar=True` — handles concurrent pending trials in mixed-build batching

### Cloud / Headless Rendering
- Mesa/LLVMpipe — CPU software rendering for OpenGL, sufficient for Starsector
- Xvfb — virtual framebuffer, standard approach for headless X11 applications
- `utensils/docker-opengl` — Docker base image for Mesa + LLVMpipe + Xvfb
- AWS c7i family (Sapphire Rapids) — x86_64 compute-optimized, supports AVX-512 for JVM flags

### Game AI Infrastructure Patterns
- OpenAI Five (Dota 2) — 128K CPU cores, native headless mode
- AlphaStar (StarCraft II) — PySC2/s2client-proto, native headless Linux binary
- General pattern for games without headless mode: Xvfb + Docker containers
