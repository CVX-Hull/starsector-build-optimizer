# Starsector Ship Build Optimizer — Project Overview

## What This Project Is

An automated system for discovering optimal and diverse ship builds in [Starsector](https://fractalsoftworks.com/), a space combat game by Fractal Softworks. The system combines:

1. **A Java mod** that runs automated combat simulations inside the game engine
2. **A Python optimizer** that uses state-of-the-art Bayesian optimization and evolutionary methods to search the build space
3. **A multi-fidelity evaluation pipeline** that balances cheap heuristic scoring with expensive but accurate combat simulation
4. **A quality-diversity discovery engine** that maps the full landscape of viable build archetypes

## Why This Exists

Ship building in Starsector is a complex constrained optimization problem. A single ship hull has ~10^6 to 10^8 feasible build configurations after constraint pruning (from a naive ~10^13-14). Players rely on intuition, community guides, and manual trial-and-error. No automated optimization tool exists — this would be the first.

The closest academic analogue is the Hearthstone deckbuilding problem (DSA-ME, arXiv:2112.03534), where discrete item selection from constrained pools is optimized via expensive game simulation.

## Game Version

- **Starsector 0.98a-RC8** (released March 27, 2025)
- Game runs on **Java 17** (upgraded from Java 7 in 0.98a)

## Development Approach

DDD (Document Driven Development) + TDD. Module specifications in `docs/specs/` drive tests, which drive implementation.

## Document Index

### Module Specifications (DDD)

**Phase 1: Python Data Layer** (complete)

| Spec | Module | Contents |
|---|---|---|
| [01-data-models](./specs/01-data-models.md) | `models.py` | Dataclasses, enums, type definitions |
| [02-hullmod-effects](./specs/02-hullmod-effects.md) | `hullmod_effects.py` | Game constants, hullmod effect registry |
| [03-game-data-parser](./specs/03-game-data-parser.md) | `parser.py` | CSV + JSON parsing |
| [04-search-space](./specs/04-search-space.md) | `search_space.py` | Per-hull weapon/hullmod compatibility |
| [05-repair-operator](./specs/05-repair-operator.md) | `repair.py` | Constraint enforcement |
| [06-heuristic-scorer](./specs/06-heuristic-scorer.md) | `scorer.py` | Static build quality metrics |
| [07-variant-generator](./specs/07-variant-generator.md) | `variant.py` | .variant file generation |
| [08-calibration-pipeline](./specs/08-calibration-pipeline.md) | `calibration.py` | Build sampling + weight fitting |

**Phase 2: Java Combat Harness Mod** (complete)

| Spec | Module | Contents |
|---|---|---|
| [09-combat-protocol](./specs/09-combat-protocol.md) | Python ↔ Java | JSON schemas, workdir layout |
| [10-matchup-config](./specs/10-matchup-config.md) | `MatchupConfig.java` | matchup.json parsing + validation |
| [11-damage-tracker](./specs/11-damage-tracker.md) | `DamageTracker.java` | DamageListener, per-ship accumulation |
| [12-result-writer](./specs/12-result-writer.md) | `ResultWriter.java` | Atomic result.json output |
| [13-combat-harness-plugin](./specs/13-combat-harness-plugin.md) | `CombatHarnessPlugin.java` | EveryFrameCombatPlugin |
| [14-mod-skeleton](./specs/14-mod-skeleton.md) | Mod files | ModPlugin, MissionDefinition, mod_info |

### Reference Documents

| Document | Contents |
|---|---|
| [game-mechanics](./reference/game-mechanics.md) | Starsector combat mechanics, ship fitting, weapons, flux, armor, shields, AI behavior |
| [problem-formulation](./reference/problem-formulation.md) | Formal optimization problem definition, decision variables, constraints, search space analysis |
| [literature-review](./reference/literature-review.md) | Survey of 40+ papers across BO, evolutionary methods, QD, multi-fidelity, surrogates, game optimization |
| [system-architecture](./reference/system-architecture.md) | Full system design: Java mod, Python orchestrator, parallel instances, optimizer integration |
| [optimization-methods](./reference/optimization-methods.md) | Technical guide to each optimization method and implementation specifics |
| [multi-fidelity-strategy](./reference/multi-fidelity-strategy.md) | Three-tier evaluation pipeline, surrogate models, noise handling |
| [quality-diversity](./reference/quality-diversity.md) | MAP-Elites for build archetype discovery, behavior descriptors |
| [implementation-roadmap](./reference/implementation-roadmap.md) | Phased build plan with dependencies and technology choices |
| [game-data-reference](./reference/game-data-reference.md) | File formats, CSV schemas, .variant and .ship file structures |

## Key Design Decisions

1. **Optuna TPE** as primary optimizer — clean ask-tell API, batch parallelism via constant_liar, swappable samplers via OptunaHub
2. **CatCMAwM** as QD emitter for Phase 6 — joint Gaussian + categorical distribution via cmaes library
3. **Heuristic warm-start** — 50K random builds scored with heuristic, top-500 seed the Optuna study
4. **Repair operators** for constraint handling — literature consensus over penalty or constrained generation
5. **CMA-MAE + CatCMA** for quality-diversity — discovers diverse build archetypes, not just one optimum
6. **TabPFN v2 + CatBoost** for neural surrogate — TabPFN for cold-start (N<300), CatBoost for scale (N>300)

## Technology Stack

| Component | Technology |
|---|---|
| Combat harness mod | Java (Starsector API, LWJGL) |
| Game data parsing | Python (pandas, stdlib json) |
| Optimizer | Python (Optuna, cmaes) |
| Quality-diversity | Python (pyribs, cmaes library) |
| Neural surrogate | Python (TabPFN, CatBoost, scikit-learn) |
| Multi-fidelity | Heuristic warm-start + full sim with curtailment |
| Instance management | Python + Bash (Xvfb for virtual displays) |
| Visualization | Python (matplotlib, plotly) |

## External Resources

- [Starsector Official Site](https://fractalsoftworks.com/)
- [Starsector Wiki](https://starsector.wiki.gg/)
- [Starsector API Javadoc](https://fractalsoftworks.com/starfarer.api/) / [Community Mirror](https://jaghaimo.github.io/starsector-api/)
- [Starsector Forums](https://fractalsoftworks.com/forum/)
