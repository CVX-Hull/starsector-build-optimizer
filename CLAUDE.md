# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

- **Phase 1** (complete): Data layer — game data parsing, search space, constraint repair, heuristic scoring, variant generation.
- **Phase 2** (complete): Java combat harness mod — automated AI-vs-AI combat simulation with JSON result export.
- **Phase 3** (complete): Instance manager — N parallel Starsector instances via Xvfb, batch evaluation, health monitoring.
- **Phase 3.5** (complete): Data-driven timeout tuning (Weibull AFT).
- **Phase 4** (complete): Optimizer integration — Optuna TPE/CatCMAwM, opponent pool, heuristic warm-start, parameter importance.
- **Phase 5** (5A–5D complete; 5E, 5F planned; 5G deferred): Signal quality.
  - **5A** TWFE deconfounding + control variate + rank shape; **5B** WilcoxonPruner + ASHA; **5C** anchor-first + incumbent-overlap opponent selection — all shipped and documented in `docs/reference/phase5-signal-quality.md`, `phase5a-deconfounding-theory.md`, `phase5c-opponent-curriculum.md`.
  - **5D** (complete, 2026-04-18) — EB shrinkage of A2: replaces the scalar control variate with empirical-Bayes shrinkage of α̂ toward a 7-covariate heuristic regression prior (HN + triple-goal rank correction; closed-form two-level Gaussian model). **Fusion paradigm** — `α̂_TWFE` and the 7 prior features are treated as noisy measurements of the same latent α and combined by Bayes rule. Covariate set: 3 engine-computed `MutableShipStats` reads (eff_max_flux, eff_flux_dissipation, eff_armor_rating, emitted by a new Java-side SETUP hook in `CombatHarnessPlugin`) + 3 Python-raw aggregates (total_weapon_dps, engagement_range, kinetic_dps_fraction) + `composite_score`. Feature count sized by sweep (`experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md`) targeting the p≈8 diminishing-returns knee, further reduced to p=7 by an empirical variance audit on the 2026-04-17 Hammerhead run — candidate engine features `eff_hull_hp`, `eff_max_speed`, and `eff_shield_damage_mult` were dropped because the relevant hullmods are used by 0.3–3% of builds in per-hull runs (near-zero variance). Safe at N≥200 (8h overnight) to N≈2000 (3-day run) given the 27 trials/hr throughput. The original conditioning-paradigm v1 (CUPED / FWL / PDS lasso / ICP) was refuted empirically: synthetic Δρ = −0.35 vs plain TWFE (p<0.0001, n=20), Hammerhead LOOO Δρ = −0.13, missed +0.02 ship gate by 7×. Root cause: bad-control pattern (Cinelli-Forney-Pearl 2022 "Case 8": the scorer components are noisy proxies of the estimand α, not orthogonal covariates of Y). The replacement design passes the same gate at Δρ = +0.036 vs A0 and +0.057 vs A (at p=16; p=7 projected ≈ +0.31 on synthetic). See `docs/reference/phase5d-covariate-adjustment.md` and `experiments/phase5d-covariate-2026-04-17/`. Post-ship **TTK-signal investigation** (2026-04-18, `experiments/phase5d-ttk-signal-2026-04-18/`, §7 of phase5d-covariate-adjustment.md) benchmarked raw `duration_seconds`, pre-battle `log(effective_hp/total_dps)`, Weibull-AFT residual as 8th EB covariate, plus lexicographic ε-tiebreaker on `Y_ij`. Duration is a Case-17 bad control by theory (Eggers-Tuñón 2024 AJPS placebo rejects admissibility) but empirically delivers +0.136 Δρ at n=56 production-sized overnight log and +0.004 NS at n=485 sparse calibration log; build-mean aggregation shrinks ε-collider leakage and leaves the α-mediator signal dominant on production-sized runs. Lexicographic tiebreaker uniformly loses 0.005–0.12 Δρ. Not shipped; routed to Phase 5F as an opt-in regime-conditioned `eb_extra_covariates` extension pending cross-hull validation (Hammerhead is quick-kill archetype; attrition hulls untested).
  - **5E** (planned) — Box-Cox replaces the top-quartile-clamped rank shape at A3. Simulation-validated (Δρ = +0.070, ceiling 25.3% → 0.4%) in `experiments/signal-quality-2026-04-17/`; see `docs/reference/phase5e-shape-revision.md`.
  - **5F** (planned) — **regime-segmented optimization**: user-selectable progression tier applied as a hard mask on the hullmod, weapon, and hull catalogues at `search_space.py` construction time. `RegimeConfig` with four presets (`early` / `mid` / `late` / `endgame`); one Optuna study per `(hull, regime)`; default `mid` (`¬no_drop ∧ ¬no_drop_salvage`) redirects ~80% of the Hammerhead trial budget from the 89% rare-faction exploit cluster to the deployment-reachable regime. **Extension** (post initial ship): per-regime `eb_extra_covariates` opt-in for archetype-dependent post-battle signals (TTK on quick-kill archetypes; see §3.5.1 of `phase5f-regime-segmented-optimization.md` and the 2026-04-18 TTK benchmark). Framed as **CMDP feasibility alignment** (Altman 1999, Huang & Ontañón 2020) rather than reward-shaping — explicitly distinct from the §4.5 silent-filter rejection in phase5c (the user opts into a regime; no hard-coded claim is made about components). Grounded in Jaffe 2012 restricted-play, Csikszentmihalyi flow / Ryan-Rigby PENS / Koster mastery-decay (engagement case for conservative default), Suits' lusory attitude and Caillois' agon (ludology), and Alex Mosolov's stated design intent that `codex_unlockable` is spoiler-avoidance while `no_drop` / `no_drop_salvage` are genuine campaign-acquisition gates. Rejected alternatives: scalar penalty (bad-control contamination like 5D v1), archive-over-single-run (insufficient budget per cell), curriculum across regimes (Bengio 2009 applies to data-order, not search-space), multi-fidelity (BOCA requires same-x), Pareto / NSGA-II (user wants a single in-regime recommendation, not a front), Weitzman reservation-value / PBGI (deferred — formally cleanest but needs per-component posteriors). Full research + rejected-alternative chain in `docs/reference/phase5f-regime-segmented-optimization.md` (synthesis of the 2026-04-17 16-field literature sweep).
  - **5G** (deferred) — adversarial opponent curriculum (PSRO-style pool growth). Renumbered from 5F. Research complete; revisit post-5E/5F if exploit convergence persists even within the unrestricted `endgame` regime.
- **Phase 6** (planned): **Structured search-space representation**. Replaces the Phase 4 Optuna TPE/CatCMAwM surrogate with a custom BoTorch Gaussian Process whose kernel composes subspace-specific priors: SAAS sparsity (Eriksson-Jankowiak 2021 [arXiv:2103.00349](https://arxiv.org/abs/2103.00349)) on the hullmod-boolean subspace; transformed-overlap kernel (Garrido-Merchán & Hernández-Lobato 2020) + 7-attribute Matérn on weapon slots; 5-dim slot-feature Matérn (`forward_projection`, `arc_width`, `is_turret`, `lateral_offset`, `longitudinal_offset`) for cross-slot kernel similarity; opponent-summary features (has-missiles-frac, has-fighters-frac, mean-armor-rating) injected into *small-slot* posteriors only (preserves opponent-conditional small-slot addressability — the load-bearing empirical constraint from community meta); BaCO-style gated-sentinel for conditional slots (Hellsten 2024 [arXiv:2212.11142](https://arxiv.org/abs/2212.11142), §4.3); ICM per-item and per-slot residuals (Bonilla 2007, Álvarez 2011) that shrink to zero unless data forces a quirk — structurally the same fusion paradigm as 5D. Warmed by a BOCA-style 30-trial RF-importance pilot (Chen 2021) that empirical-Bayes-initializes SAAS lengthscales. Biased (but not locked) by πBO decay-weighted priors (Hvarfner 2022 [arXiv:2204.11051](https://arxiv.org/abs/2204.11051)) over nine community-stable role modes (SO brawler, long-range sniper, kinetic-HE brawler, broadside, turret-flex, burst-missile, PD-carrier, flanker, phase striker — stable across 0.95→0.98). **Hull-conditional activation**: a per-hull feasibility mask (computed at hull-load from `.ship` JSON + ship-system registry) drops infeasible modes to zero weight (Wolf can't realize broadside or turret-flex); initial weights uniform over feasible modes (no meta-hull coverage bias); self-correcting mixture weights (Hvarfner 2023 [arXiv:2304.00397](https://arxiv.org/abs/2304.00397)) online-downweight modes that disagree with per-hull data. Community meta supplies the *vocabulary of modes*, not the *per-hull weights*. `range` and `op_cost` are hull-size-normalized so mode definitions transfer across FRIGATE→CAPITAL without rescaling; `hull_size` enters the kernel as an ordinal context feature. **AI pilotability absorbed by the same mechanism**: combat sim is AI-vs-AI by construction and the AI mispilots several community-top archetypes (SO brawler, phase striker, burst-missile) that are designed for player piloting; rather than hardcode AI-compatibility flags (rejected §4.16 because AI behavior changes across patches and pilotability interacts with hull), the self-correcting mixture update lets AI-hostile modes empirically collapse their weight under simulation evidence. Player-piloted flagship optimization is out of Phase 6 scope (§4.17) — would require engine-level input injection — and is deferred indefinitely. BoTorch-as-Optuna-sampler integration; ~6 weeks build. Expected 2–4× sample efficiency at N = 200–500 (conservative aggregate from SAASBO 2–5×, BaCO 1.36–1.56×, πBO 2–5× if priors correct). Game-update invariant by construction — new weapons inherit the attribute-kernel prior zero-shot via the 7-attribute vector. Rejected alternatives: NAS weight-sharing (no trainable object to share), Ma-Blaschko tree kernel (subsumed by gated-sentinel + SAAS), HyperMapper off-the-shelf (missing SAAS), pure SAASBO (bad categoricals), BOCS (binary-only), GFlowNets (need 10⁵+ evals), Hearthstone MESB (no phenotype→genotype map), silent rule-based small-slot fills (explicitly rejected by user because smalls are opponent-conditional vs missile boats/carriers). Full grounding + rejected-alternative chain in `docs/reference/phase6-search-space-compression.md` (synthesis of the 2026-04-17 10-field literature sweep + compiler-autotuning deep-dive).

## Commands

- Run Python tests: `uv run pytest tests/ -v`
- Run single test file: `uv run pytest tests/test_parser.py -v`
- Run single test: `uv run pytest tests/test_models.py::test_weapon_sustained_dps -v`
- Build combat harness: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew jar`
- Run Java tests: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew test`
- Deploy mod: `cd combat-harness && JAVA_HOME=/usr/lib/jvm/java-26-openjdk ./gradlew deploy`
- Run optimizer (heuristic-only): `uv run python scripts/run_optimizer.py --hull eagle --game-dir game/starsector --heuristic-only`
- Game data location: `game/starsector/data/` (gitignored, not in repo)
- See `combat-harness/CLAUDE.md` for Java-specific instructions

## Running the live optimizer (sim mode)

Each Starsector JVM consumes ~2.5 cores under active combat (measured 2026-04-18: `ps -eo pcpu` showed 232–254% per JVM on a 12-core host). Over-subscribing CPU causes load-thrash that slows throughput to a crawl. Pick instance count, launch, and stop as follows:

**Sizing (enforced)**: `--num-instances ≤ os.cpu_count() // 3`. `InstancePool.setup()` preflights this and raises `InstanceError` otherwise. On a 12-core host, max is 4; on 9-core, 3.

**Launch**:
```
uv run python scripts/run_optimizer.py --hull <id> --game-dir game/starsector \
    --num-instances <≤nproc/2> --sim-budget <N> --study-db data/<id>.db
```

**Stop — three options in preference order**:
1. **Ctrl-C (preferred)** — `run_optimizer.py` installs SIGINT/SIGTERM/SIGHUP handlers that raise `KeyboardInterrupt`, unwinding `with InstancePool(...)` → `teardown()` writes shutdown signals and terminates JVMs + Xvfb cleanly. `kill <pid>` on the Python orchestrator works the same.
2. **`uv run python scripts/stop_optimizer.py`** — panic button when the orchestrator is gone (crash, `kill -9`, tmux session lost). Writes shutdown signals to every work dir, then SIGTERM → wait → SIGKILL on `StarfarerLauncher` JVMs and `Xvfb :1XX` processes.
3. **Never `pkill -f starsector`** — the JVM cmdline contains `StarfarerLauncher` / `com.fs.starfarer`, not the literal string `starsector`. The correct patterns are `StarfarerLauncher` (JVMs) and `Xvfb :1\d\d` (displays 100–199).

## Workflow Gates

For every module: spec first (`docs/specs/`), then tests, then implementation. The four skills in `.claude/skills/` enforce quality at each gate:

| Gate | When | Skill |
|------|------|-------|
| **Planning** | Before any non-trivial implementation | Enter plan mode. Follow `ddd-tdd` lifecycle. |
| **Plan review** | Before calling ExitPlanMode | Run the `plan-review` checklist: self-review + 3 parallel audit sub-agents. |
| **Implementation** | During coding | Follow `ddd-tdd` step 3: one concern per change, verify after each module. |
| **Post-implementation** | After all implementation tasks complete | Run the `post-impl-audit` checklist: mechanical checks + 3 parallel audit sub-agents. |
| **Invariant check** | When reviewing any code change | Reference `design-invariants` for the full checklist. |

For Starsector Java modding specifics (sandbox, file I/O, Janino, combat plugin patterns), see `.claude/skills/starsector-modding.md`.

## Design Principles

1. **Single source of truth for game knowledge.** All hardcoded hullmod effects, incompatibilities, and game constants live in `hullmod_effects.py`. Never duplicate hullmod logic in scorer, repair, or search_space.

2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult`, `CombatFitnessConfig`, `ImportanceResult` are frozen dataclasses. Repair returns new instances. `Build.hullmods` is `frozenset`.

3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals (potentially infeasible) go through `repair_build()` to produce valid `Build` objects. Everything downstream of repair works with concrete, valid Builds.

4. **Data-driven over logic-driven.** Hullmod effects are a declarative `HULLMOD_EFFECTS` registry dict, not scattered if-else chains. Adding a hullmod effect = one dict entry.

5. **Forward compatibility — warn, don't crash.** Unknown enum values from future game versions: `from_str()` returns `None`, parser logs warning and skips the record. Never crash on unknown game data.

6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics. These become Phase 7 behavior descriptors and Phase 8 features without refactoring.

7. **Verify game facts against actual game files, never assume.** The game data files at `game/starsector/data/` are the ground truth. Specific pitfalls: hullmod IDs are non-obvious (check `hull_mods.csv`), `weapon_data.csv` `type` is damage type not weapon type, `ship_data.csv` `designation` is a role string not hull size. See `.claude/skills/starsector-modding.md` for the full list.

## Design Invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications
- `HULLMOD_EFFECTS`, `INCOMPATIBLE_PAIRS`, `HULL_SIZE_RESTRICTIONS` are the ONLY locations for hardcoded hullmod game knowledge
- All game constants (MAX_VENTS, damage multipliers, etc.) are in `hullmod_effects.py`, not scattered
- **No magic numbers in function bodies.** Timeouts, coordinates, polling intervals, thresholds, and batch sizes must live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`) — never as literals in function bodies.

For the full mechanical checklist with runnable grep commands, see `.claude/skills/design-invariants.md`.

## Project Layout

```
src/starsector_optimizer/          # Python modules
├── models.py                      # Dataclasses + enums (ShipHull, Weapon, Build, BuildSpec, CombatFitnessConfig, TWFEConfig, EBShrinkageConfig, EngineStats, etc.)
├── hullmod_effects.py             # Game constants, hullmod effect registry
├── parser.py                      # CSV + loose JSON → model objects
├── search_space.py                # Per-hull weapon/hullmod compatibility
├── repair.py                      # Constraint enforcement (optimizer→domain boundary)
├── scorer.py                      # Heuristic scoring → ScorerResult
├── variant.py                     # Build ↔ .variant JSON / BuildSpec (generate, load, stock builds, build_to_build_spec)
├── calibration.py                 # Random build generation + feature extraction
├── estimator.py                   # Throughput + cost estimation for simulation campaigns
├── result_parser.py               # Parse combat result JSON ↔ Python dataclasses
├── instance_manager.py            # Manage N parallel Starsector game instances
├── timeout_tuner.py               # Data-driven timeout prediction (Weibull AFT)
├── combat_fitness.py              # Hierarchical composite combat fitness score
├── opponent_pool.py               # Diverse opponent pool per hull size
├── deconfounding.py               # TWFE decomposition + EB shrinkage + triple-goal rank correction
├── importance.py                  # Parameter importance analysis (fANOVA) + fixed params
└── optimizer.py                   # Optuna integration, ask-tell loop, warm-start

combat-harness/                    # Java combat harness mod
├── CLAUDE.md                      # Java-specific instructions
├── build.gradle.kts               # Gradle build
├── src/main/java/starsector/combatharness/
│   ├── MatchupConfig.java         # Single matchup config POJO + BuildSpec inner class
│   ├── MatchupQueue.java          # Batch queue — reads JSON array from saves/common/
│   ├── VariantBuilder.java        # Programmatic ShipVariantAPI construction from BuildSpec
│   ├── DamageTracker.java         # DamageListener — per-ship damage accumulation
│   ├── ResultWriter.java          # Batch results + done signal via SettingsAPI
│   ├── CombatHarnessPlugin.java   # State machine: INIT→SETUP→FIGHTING→DONE→WAITING
│   ├── CombatHarnessModPlugin.java # BaseModPlugin — mod entry point
│   ├── TitleScreenPlugin.java     # Auto-navigates to mission on title screen
│   └── MenuNavigator.java         # java.awt.Robot menu clicking (1920x1080 calibrated)
├── src/main/java/data/missions/optimizer_arena/
│   └── MissionDefinition.java     # Mission setup (compiled in JAR, not Janino)
└── mod/                           # Deployed to game/starsector/mods/combat-harness/
    └── mod_info.json

# I/O paths (game appends .data to all saves/common/ filenames):
#   Input:  saves/common/combat_harness_queue.json.data
#   Output: saves/common/combat_harness_results.json.data
#   Done:   saves/common/combat_harness_done.data
#   Health: saves/common/combat_harness_heartbeat.txt.data

docs/
├── specs/                         # DDD module specifications (drive implementation)
└── reference/                     # Background research and game mechanics reference

.claude/skills/                    # Quality gate skills
├── ddd-tdd.md                     # Spec → test → impl → verify lifecycle
├── design-invariants.md           # Full invariant checklist with mechanical checks
├── plan-review.md                 # Pre-approval review (self-review + 3 audit agents)
├── post-impl-audit.md             # Post-implementation verification (checks + 3 audit agents)
└── starsector-modding.md          # Java modding pitfalls (sandbox, file I/O, Janino, etc.)
```
