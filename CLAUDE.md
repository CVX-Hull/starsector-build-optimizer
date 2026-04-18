# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

- **Phase 1** (complete): Data layer — game data parsing, search space, constraint repair, heuristic scoring, variant generation.
- **Phase 2** (complete): Java combat harness mod — automated AI-vs-AI combat simulation with JSON result export.
- **Phase 3** (complete): Instance manager — N parallel Starsector instances via Xvfb, batch evaluation, health monitoring.
- **Phase 3.5** (complete): Data-driven timeout tuning (Weibull AFT).
- **Phase 4** (complete): Optimizer integration — Optuna TPE/CatCMAwM, opponent pool, heuristic warm-start, parameter importance.
- **Phase 5** (5A–5F complete; 5G deferred): Signal quality.
  - **5A** TWFE deconfounding + control variate + rank shape; **5B** WilcoxonPruner + ASHA; **5C** anchor-first + incumbent-overlap opponent selection — all shipped and documented in `docs/reference/phase5-signal-quality.md`, `phase5a-deconfounding-theory.md`, `phase5c-opponent-curriculum.md`.
  - **5D** (complete, 2026-04-18) — EB shrinkage of A2: replaces the scalar control variate with empirical-Bayes shrinkage of α̂ toward a 7-covariate heuristic regression prior (HN + triple-goal rank correction; closed-form two-level Gaussian model). **Fusion paradigm** — `α̂_TWFE` and the 7 prior features are treated as noisy measurements of the same latent α and combined by Bayes rule. Covariate set: 3 engine-computed `MutableShipStats` reads (eff_max_flux, eff_flux_dissipation, eff_armor_rating, emitted by a new Java-side SETUP hook in `CombatHarnessPlugin`) + 3 Python-raw aggregates (total_weapon_dps, engagement_range, kinetic_dps_fraction) + `composite_score`. Feature count sized by sweep (`experiments/phase5d-covariate-2026-04-17/FEATURE_COUNT_REPORT.md`) targeting the p≈8 diminishing-returns knee, further reduced to p=7 by an empirical variance audit on the 2026-04-17 Hammerhead run — candidate engine features `eff_hull_hp`, `eff_max_speed`, and `eff_shield_damage_mult` were dropped because the relevant hullmods are used by 0.3–3% of builds in per-hull runs (near-zero variance). Safe at N≥200 (8h overnight) to N≈2000 (3-day run) given the 27 trials/hr throughput. The original conditioning-paradigm v1 (CUPED / FWL / PDS lasso / ICP) was refuted empirically: synthetic Δρ = −0.35 vs plain TWFE (p<0.0001, n=20), Hammerhead LOOO Δρ = −0.13, missed +0.02 ship gate by 7×. Root cause: bad-control pattern (Cinelli-Forney-Pearl 2022 "Case 8": the scorer components are noisy proxies of the estimand α, not orthogonal covariates of Y). The replacement design passes the same gate at Δρ = +0.036 vs A0 and +0.057 vs A (at p=16; p=7 projected ≈ +0.31 on synthetic). See `docs/reference/phase5d-covariate-adjustment.md` and `experiments/phase5d-covariate-2026-04-17/`. Post-ship **TTK-signal investigation** (2026-04-18, `experiments/phase5d-ttk-signal-2026-04-18/`, §7 of phase5d-covariate-adjustment.md) benchmarked raw `duration_seconds`, pre-battle `log(effective_hp/total_dps)`, Weibull-AFT residual as 8th EB covariate, plus lexicographic ε-tiebreaker on `Y_ij`. Duration is a Case-17 bad control by theory (Eggers-Tuñón 2024 AJPS placebo rejects admissibility) but empirically delivers +0.136 Δρ at n=56 production-sized overnight log and +0.004 NS at n=485 sparse calibration log; build-mean aggregation shrinks ε-collider leakage and leaves the α-mediator signal dominant on production-sized runs. Lexicographic tiebreaker uniformly loses 0.005–0.12 Δρ. Not shipped; routed to Phase 5F as an opt-in regime-conditioned `eb_extra_covariates` extension pending cross-hull validation (Hammerhead is quick-kill archetype; attrition hulls untested).
  - **5E** (complete, 2026-04-18) — Box-Cox output warping replaces the quantile-rank A3. `scipy.stats.boxcox` fits λ on the post-5D EB posterior-mean population every `_finalize_build` call (~1ms at n=300, refit cadence documented as a deviation from the research doc's "every N trials" — batched refit saves nothing and adds a (λ, shift, min, max) cache-coherence burden). Min-max rescaled to `[0, 1]` for JSONL schema stability. Below `ShapeConfig.min_samples=8` (analogy to `eb_min_builds`; plan-introduced floor, not spec'd — Box-Cox MLE destabilises under ~8 samples) A3 falls through to min-max scaling; non-finite input raises `ValueError` (upstream NaN is an invariant violation, fail fast). New JSONL fields `shape_lambda` + `shape_passthrough_reason` + per-trial λ logger + end-of-run "A3 Box-Cox summary" log. The effect on ρ_truth is near-zero by design (both transforms are monotone) — the mechanical win is **ceiling saturation 25% → 0.5% and top-5 identification overlap 0.02 → 0.44 (14×)**, validated invariant across 4 covariate-strength regimes at `experiments/signal-quality-5d-2026-04-18/` (calibration sweep: Δρ A vs A0 tracks prior strength from +0.38 down to +0.05, matching production Hammerhead LOOO +0.036; Box-Cox's A3 effect holds across the whole range). CAT Fisher-info opponent selection (J strategy) showed +0.014 ρ marginal gain and remains **deferred** — observation-side change, revisit post-5F. See `docs/reference/phase5e-shape-revision.md`.
  - **5F** (complete, 2026-04-18) — **regime-segmented optimization**: user-selectable loadout regime hard-masks the hullmod and weapon catalogues at `search_space.py` construction time. `RegimeConfig` with four presets (`early` (default) / `mid` / `late` / `endgame`); one Optuna study per `(hull, regime)` named `f"{hull_id}__{regime.name}"`; cross-regime warm-start via `--warm-start-from-regime` (enqueues feasibility-checked prior-regime incumbents through `repair_build` + `is_feasible`, re-encodes against the target regime's search space to avoid structural-distribution mismatch). **Open-world framing**: regime filters OUR loadout only (what components the user has unlocked); opponents remain drawn from the full hull-size-matched pool (`opponent_pool.py` — any build can face any opponent); hull choice remains user-controlled via `--hull`. Default `early` is the most conservative component-availability baseline, not a difficulty tier (deviation from research doc §3.4's linear-progression `mid`-default argument). New JSONL field `regime` per trial row. Framed as **CMDP feasibility alignment** (Altman 1999, Huang & Ontañón 2020) rather than reward-shaping — explicitly distinct from the §4.5 silent-filter rejection in phase5c (the user opts into a regime; no hard-coded claim). Grounded in Jaffe 2012 restricted-play, Csikszentmihalyi flow / Ryan-Rigby PENS / Koster mastery-decay (engagement case for conservative default), Suits' lusory attitude and Caillois' agon (ludology), and Alex Mosolov's stated design intent that `codex_unlockable` is spoiler-avoidance while `no_drop` / `no_drop_salvage` are genuine campaign-acquisition gates. Rejected alternatives: scalar penalty (bad-control contamination like 5D v1), archive-over-single-run (insufficient budget per cell), curriculum across regimes (Bengio 2009 applies to data-order, not search-space), multi-fidelity (BOCA requires same-x), Pareto / NSGA-II (user wants a single in-regime recommendation, not a front), hull-filter presets (open-world framing — any hull, any opponent), Weitzman reservation-value / PBGI (deferred — formally cleanest but needs per-component posteriors). TTK opt-in `eb_extra_covariates` extension (§3.5.1 of `phase5f-regime-segmented-optimization.md` and the 2026-04-18 TTK benchmark) remains deferred. Full research + rejected-alternative chain in `docs/reference/phase5f-regime-segmented-optimization.md`.
  - **5G** (deferred) — adversarial opponent curriculum (PSRO-style pool growth). Renumbered from 5F. Research complete; revisit post-5E/5F if exploit convergence persists even within the unrestricted `endgame` regime.
- **Phase 6** (planned, NEXT — 2026-04-18): **Cloud Worker Federation**. Moves bulk simulation from the workstation to CPU spot instances. **Hetzner CCX33 Ashburn** ($0.13/hr, no preemption) primary, **AWS c7a.2xlarge us-west-2** ($0.15/hr spot) fallback. Benchmarks 2026-04-18 prove CPU cloud is **2.2–2.4× local per-instance throughput** ($0.00109 per matchup on Hetzner) after fixing an LWJGL 2.x XRandR bug — the original "GPU required" claim (spec 22, 2026-04-12) was a misdiagnosis of Xvfb's XRandR extension not populating its mode list until a client queries it. `instance_manager.py::_start_xvfb` now runs `xrandr --query` after the Xvfb socket is ready (requires `x11-xserver-utils` in cloud-init; both applied 2026-04-18). **Study federation is the #1 architectural lever**: each `(hull, regime, seed)` tuple is an independent Optuna study with ≤24 workers to keep TPE's `constant_liar` imputation in its efficient regime — a 100-worker mega-study on one `(hull, regime)` would waste ~85% of budget as random sampling (TPE saturation ceiling, confirmed in Optuna 4.2 gRPC storage-proxy benchmarks). Above 24 workers per study, switch sampler to `CatCMAwM` (already supported via `--sampler=catcma` since Phase 4) — CMA-ES is natively parallel and doesn't suffer from liar-imputation collapse. Optional **hybrid schedule** (random warm-up → CatCMAwM batch → TPE exploit) for studies needing both breadth and precision. Campaign manager (`src/starsector_optimizer/campaign.py`, ~500 LOC) reads YAML config, launches workers via **Apache Libcloud** (primary library choice — only mature multi-cloud Python SDK covering both AWS and Hetzner; Pulumi-Python is the durable fallback if Libcloud is archived), tracks cost in an append-only ledger with hard cap + 50/80/95% warnings, auto-terminates studies on best-fitness plateau to reallocate workers. **Pre-baked Packer images** cut worker bootstrap from ~5 min to ~30s (saves ~3 hours wall-clock at 200-worker bursts). Orchestrator topology: workstation holds the single Optuna Study; workers are pure `BuildSpec → CombatResult` evaluators; per-study local SQLite rsync'd back from workers. **No distributed storage** (no PostgreSQL, no JournalStorage + GrpcProxy — that combo is broken in Optuna 4.2-4.4 per issue #6084). Spot preemption handled via Redis visibility-timeout + idempotent `(study_id, trial_number, opponent_id)` keys; AWS c7a us-west-2 with `price-capacity-optimized` + `CapacityRebalancing` drops interruption rate from 20% → 3%. Cost discipline is non-negotiable: campaign YAML hard cap, per-worker `max_lifetime_hours` (default 4), tag-based sweeper cron as orthogonal backstop, `trap EXIT` in all launch scripts, `final_audit.sh` post-campaign. At **$1000 budget = ~49,000 builds** = full Phase 5F regime validation (40 hulls × 4 regimes × ~300 builds) in 1-2 weeks of burst runs — free the workstation for interactive work. Precondition for Phase 7 (BoTorch GP needs cross-hull validation at scale). Rejected alternatives: one mega-study (TPE saturation), PostgreSQL + GrpcStorageProxy (ops burden when federation keeps per-study SQLite local), JournalStorage + GrpcProxy (broken combo Optuna #6084), Ray / SkyPilot / dstack / Modal / Covalent (no Hetzner support — all GPU/AI-focused; Ray needs 100-LOC custom node_provider), Kubernetes / EKS / Fargate (overkill for single-operator bursts), GPU cloud instances (not needed — CPU is 2.4× local), Facebook Ax (3-day port cost; loses existing WilcoxonPruner + warm-start infra), AWS Batch (~30-60s scheduler latency per job transition), EBS Fast Snapshot Restore (10-concurrent credit cap breaks at 50-worker bursts), AWS Auto Scaling Warm Pools (~$16/mo EBS idle not cost-effective at $1000 scale — worth it at $10k+), ARM / Graviton (LWJGL 2.x x86_64 only), DigitalOcean / Vultr / Linode / Fly.io (no spot tier, 2-3× AWS baseline). SOP for running campaigns in `.claude/skills/cloud-worker-ops.md`. Full design + benchmarks + rejected-alternative chain in `docs/reference/phase6-cloud-worker-federation.md` and `experiments/cloud-benchmark-2026-04-18/` (empirical validation: AWS c7i.2xlarge 64.2 matchups/hr/inst, Hetzner CCX33 59.9 matchups/hr/inst, identical 57-matchup 6-build workload, ~$0.065 per run, $0.14 total spent on the investigation).
- **Phase 7** (planned, renumbered from 6): **Structured search-space representation**. Replaces the Phase 4 Optuna TPE/CatCMAwM surrogate with a custom BoTorch Gaussian Process whose kernel composes subspace-specific priors: SAAS sparsity (Eriksson-Jankowiak 2021 [arXiv:2103.00349](https://arxiv.org/abs/2103.00349)) on the hullmod-boolean subspace; transformed-overlap kernel (Garrido-Merchán & Hernández-Lobato 2020) + 7-attribute Matérn on weapon slots; 5-dim slot-feature Matérn (`forward_projection`, `arc_width`, `is_turret`, `lateral_offset`, `longitudinal_offset`) for cross-slot kernel similarity; opponent-summary features (has-missiles-frac, has-fighters-frac, mean-armor-rating) injected into *small-slot* posteriors only (preserves opponent-conditional small-slot addressability — the load-bearing empirical constraint from community meta); BaCO-style gated-sentinel for conditional slots (Hellsten 2024 [arXiv:2212.11142](https://arxiv.org/abs/2212.11142), §4.3); ICM per-item and per-slot residuals (Bonilla 2007, Álvarez 2011) that shrink to zero unless data forces a quirk — structurally the same fusion paradigm as 5D. Warmed by a BOCA-style 30-trial RF-importance pilot (Chen 2021) that empirical-Bayes-initializes SAAS lengthscales. Biased (but not locked) by πBO decay-weighted priors (Hvarfner 2022 [arXiv:2204.11051](https://arxiv.org/abs/2204.11051)) over nine community-stable role modes (SO brawler, long-range sniper, kinetic-HE brawler, broadside, turret-flex, burst-missile, PD-carrier, flanker, phase striker — stable across 0.95→0.98). **Hull-conditional activation**: a per-hull feasibility mask (computed at hull-load from `.ship` JSON + ship-system registry) drops infeasible modes to zero weight (Wolf can't realize broadside or turret-flex); initial weights uniform over feasible modes (no meta-hull coverage bias); self-correcting mixture weights (Hvarfner 2023 [arXiv:2304.00397](https://arxiv.org/abs/2304.00397)) online-downweight modes that disagree with per-hull data. Community meta supplies the *vocabulary of modes*, not the *per-hull weights*. `range` and `op_cost` are hull-size-normalized so mode definitions transfer across FRIGATE→CAPITAL without rescaling; `hull_size` enters the kernel as an ordinal context feature. **AI pilotability absorbed by the same mechanism**: combat sim is AI-vs-AI by construction and the AI mispilots several community-top archetypes (SO brawler, phase striker, burst-missile) that are designed for player piloting; rather than hardcode AI-compatibility flags (rejected §4.16 because AI behavior changes across patches and pilotability interacts with hull), the self-correcting mixture update lets AI-hostile modes empirically collapse their weight under simulation evidence. Player-piloted flagship optimization is out of Phase 7 scope (§4.17) — would require engine-level input injection — and is deferred indefinitely. BoTorch-as-Optuna-sampler integration; ~6 weeks build (on top of Phase 6 federation). Expected 2–4× sample efficiency at N = 200–500 (conservative aggregate from SAASBO 2–5×, BaCO 1.36–1.56×, πBO 2–5× if priors correct). Game-update invariant by construction — new weapons inherit the attribute-kernel prior zero-shot via the 7-attribute vector. Rejected alternatives: NAS weight-sharing (no trainable object to share), Ma-Blaschko tree kernel (subsumed by gated-sentinel + SAAS), HyperMapper off-the-shelf (missing SAAS), pure SAASBO (bad categoricals), BOCS (binary-only), GFlowNets (need 10⁵+ evals), Hearthstone MESB (no phenotype→genotype map), silent rule-based small-slot fills (explicitly rejected by user because smalls are opponent-conditional vs missile boats/carriers). Full grounding + rejected-alternative chain in `docs/reference/phase7-search-space-compression.md` (synthesis of the 2026-04-17 10-field literature sweep + compiler-autotuning deep-dive).

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
├── models.py                      # Dataclasses + enums (ShipHull, Weapon, Build, BuildSpec, CombatFitnessConfig, TWFEConfig, EBShrinkageConfig, ShapeConfig, RegimeConfig, EngineStats, etc.)
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
