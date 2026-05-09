---
type: always-loaded
status: shipped
last-validated: 2026-05-10
---

# Starsector Ship Build Optimizer

Automated ship build discovery for Starsector using Bayesian optimization and combat simulation.

> **Empirical-claims status (2026-05-10):** All Phase 5A–5F empirical magnitudes are pending re-validation under the V2 combat-harness loadout fix (commit `8a5b968`). Design rationale, phase-completion status, and architectural decisions are unchanged. See [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](docs/reports/2026-05-10-v1-loadout-bug-invalidation.md). Doc conventions: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

## Phase status

| Phase | Status | Primary doc |
|---|---|---|
| 1 — Data layer (parser, search space, repair, scorer, variant) | shipped | specs `01–08` |
| 2 — Combat harness mod (Java AI-vs-AI sim) | shipped | specs `09–16, 19, 27`; [combat-harness/CLAUDE.md](combat-harness/CLAUDE.md) |
| 3 — Instance manager (parallel JVMs via Xvfb) | shipped | specs `17, 18` |
| 4 — Optimizer integration (Optuna TPE + opponent pool + importance) | shipped | specs `23–26` |
| 5A–5F — Signal quality (TWFE / pruner / opponent curriculum / EB shrinkage / Box-Cox / regime segmentation) | shipped, pending re-val | [docs/reference/phase5*.md](docs/reference/) |
| 5G — Adversarial PSRO opponent curriculum | deferred | researched; revisit post-5E/5F |
| 6 — Cloud worker federation (AWS spot fleet, Tailscale mesh, Redis reliable queue) | shipped, Tier-2 live | [phase6-cloud-worker-federation.md](docs/reference/phase6-cloud-worker-federation.md), spec `22`, [.claude/skills/cloud-worker-ops.md](.claude/skills/cloud-worker-ops.md) |
| Phase-7-prep — Manifest-as-oracle refactor (deletes `hullmod_effects.py` + `timeout_tuner.py`) | shipped | spec `29` |
| 7 — Structured search-space rep (BoTorch composed-kernel GP) | planned | [phase7-search-space-compression.md](docs/reference/phase7-search-space-compression.md) |
| 7.5 — Infra & reproducibility | planned | [phase7.5-infrastructure-reproducibility.md](docs/reference/phase7.5-infrastructure-reproducibility.md) |

Spec number registry (gaps at 02/20/21): [docs/specs/README.md](docs/specs/README.md). Reports index: [docs/reports/INDEX.md](docs/reports/INDEX.md). Phase-grouped tour: [docs/project-overview.md](docs/project-overview.md).

## Commands

- Python tests: `uv run pytest tests/ -v`
- Combat harness build/test/deploy: `cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME" ./gradlew {jar,test,deploy}` — see [combat-harness/CLAUDE.md](combat-harness/CLAUDE.md)
- Optimizer (heuristic-only): `uv run python scripts/run_optimizer.py --hull <id> --game-dir game/starsector --heuristic-only`
- Optimizer (sim, local Linux): add `--num-instances <N> --sim-budget <N> --study-db data/<id>.db`. **Cap: `os.cpu_count() // 3`** (preflight-enforced; each JVM consumes ~2.5 cores).
- Optimizer (cloud): add `--worker-pool cloud`. Full SOP: [.claude/skills/cloud-worker-ops.md](.claude/skills/cloud-worker-ops.md).
- Manifest regen: `uv run python scripts/update_manifest.py --timeout 600`.
- `STARSECTOR_JDK_HOME` = JDK 17 path. macOS Homebrew: `/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`. Linux: `/usr/lib/jvm/java-17-openjdk` (Gradle 9.4 tolerates higher build hosts as long as `targetCompatibility=17`).
- Game data: `game/starsector/data/` (gitignored).

**Stop the optimizer** — preference order: (1) **Ctrl-C / `kill <pid>`** on the orchestrator (handles SIGINT/SIGTERM/SIGHUP, unwinds `with LocalInstancePool` cleanly); (2) `uv run python scripts/stop_optimizer.py` (panic button when the orchestrator is gone); (3) **never `pkill -f starsector`** — JVM cmdline matches `StarfarerLauncher`, not `starsector`.

## OS support

`LocalInstancePool` is **Linux-only** (Xvfb + xdotool dependencies, no working macOS/Windows equivalent for headless LWJGL — researched 2026-05-08, do not propose porting). On macOS/Windows the heuristic-only optimizer, the Gradle build, the full `pytest` suite, and `CampaignManager`-driven cloud campaigns all work; live local sim must use `--worker-pool cloud`. The Linux Starsector distribution staged into `game/starsector/` is platform-agnostic for everything except `LocalInstancePool`, `scripts/update_manifest.py`, and `worker_agent.py`. `mods/enabled_mods.json` must list `combat_harness` for `preflight_check` to pass.

## Workflow gates

For every module: spec first, then tests, then implementation. Skills enforce quality at each gate.

| Gate | When | Skill |
|---|---|---|
| Planning | Before non-trivial implementation | [`ddd-tdd`](.claude/skills/ddd-tdd.md) |
| Plan review | Before ExitPlanMode | [`plan-review`](.claude/skills/plan-review.md) |
| Implementation | During coding | [`ddd-tdd`](.claude/skills/ddd-tdd.md) step 3 |
| Post-implementation | After all tasks complete | [`post-impl-audit`](.claude/skills/post-impl-audit.md) |
| Invariant check | When reviewing changes | [`design-invariants`](.claude/skills/design-invariants.md) |

For Java modding pitfalls (sandbox, file I/O, Janino, combat plugin patterns), see [`starsector-modding`](.claude/skills/starsector-modding.md).

## Documentation

Six categories — full system: [docs/CONVENTIONS.md](docs/CONVENTIONS.md).

- **specs** ([docs/specs/](docs/specs/)) — module / protocol contracts.
- **reference** ([docs/reference/](docs/reference/)) — design rationale, research, theory.
- **reports** ([docs/reports/](docs/reports/)) — dated empirical evidence (the only place internal-sim numbers belong).
- **skills** ([.claude/skills/](.claude/skills/)) — procedural how-to / SOP.
- **always-loaded** — this file, [combat-harness/CLAUDE.md](combat-harness/CLAUDE.md), [docs/CONVENTIONS.md](docs/CONVENTIONS.md).
- **indices** — [docs/project-overview.md](docs/project-overview.md), [docs/reports/INDEX.md](docs/reports/INDEX.md), [experiments/INDEX.md](experiments/INDEX.md), [docs/specs/README.md](docs/specs/README.md).

**Empirical-numbers rule**: specs and references contain NO inline empirical numbers; reports own all dated measurements. See CONVENTIONS §"The empirical-numbers rule" for carve-outs (engine constants, list prices, designed thresholds).

## Engineering principles (apply globally, including in `auto` mode)

1. **Principled over expedient.** When a principled approach and an expedient shortcut both produce a working result, take the principled one. Concrete shortcuts to refuse: hardcoding to dodge a config refactor, duplicating logic instead of extracting an abstraction the codebase already implies, weakening an invariant or test to make a single failure go away, writing a narrow patch when the same bug exists elsewhere. If the principled fix is genuinely larger, name the trade-off explicitly (minimal fix + principled fix + reason for the gap) and ask which to take — silently picking the small one is the failure mode this rule exists to prevent.

2. **Address issues, don't paper over them.** When you observe a problem — flaky test, dormant code, known-wrong assertion, inconsistency between two files, load-bearing TODO, deprecated API call still in use, swallowed exception — fix the root cause in the same change. Do not defer with a new TODO, document and move on, add a skip/ignore/suppress, or treat "want me to also fix X?" as postponement. **Deferral requires explicit user consent — it is never the default.** If a principled fix is genuinely out of scope, surface it explicitly with a proposed fix and ask before deferring. Boy-scout rule, mandatory not optional.

Autonomy means "act without asking", not "take the easy path without asking".

## Design principles

1. **Manifest-as-oracle for game knowledge.** All hullmod applicability, conditional exclusions, and damage multipliers come from `game/starsector/manifest.json` (written by Java `ManifestDumper`, read by Python `GameManifest.load()`). Never hardcode hullmod logic; regen the manifest. See [spec 29](docs/specs/29-game-manifest.md).
2. **Immutable domain models.** `Build`, `EffectiveStats`, `ScorerResult`, `CombatFitnessConfig`, `ImportanceResult` are frozen dataclasses; `Build.hullmods` is `frozenset`. Repair returns new instances.
3. **Optimizer-space vs domain-space boundary.** Raw optimizer proposals go through `repair_build()` to produce valid `Build`s; everything downstream works with concrete, valid Builds.
4. **Data-driven over logic-driven.** Adding a new hullmod = regen the manifest, not edit if-else chains.
5. **Forward compatibility — warn, don't crash.** `from_str()` returns `None` on unknown enum values; parser logs warning and skips. Never crash on unknown game data.
6. **Structured scorer output.** `heuristic_score()` returns `ScorerResult` with all component metrics — used as Phase 7 behavior descriptors / Phase 8 features without refactoring.
7. **Verify game facts against actual game files.** `game/starsector/data/` and `manifest.json` are ground truth. See [`starsector-modding`](.claude/skills/starsector-modding.md) for known-pitfall list.

## Design invariants

- Every `Build` returned by `repair_build()` passes `is_feasible()`.
- `compute_effective_stats()` is the ONLY function that applies hullmod stat modifications.
- The manifest is the ONLY source of hullmod applicability, conditional exclusions, and damage multipliers. No hardcoded game-rule registries.
- All game constants come from `manifest.constants`, not scattered literals.
- **No magic numbers in function bodies.** Timeouts, coordinates, polling intervals, thresholds, batch sizes live in config dataclasses (`InstanceConfig`, `OptimizerConfig`, `CombatFitnessConfig`).

Full mechanical checklist + grep commands: [`design-invariants`](.claude/skills/design-invariants.md).

## Project layout

```
src/starsector_optimizer/   # Python — file ↔ spec map: docs/project-overview.md
combat-harness/             # Java mod — see combat-harness/CLAUDE.md
docs/{specs,reference,reports}/
.claude/skills/             # gates + SOPs
experiments/                # forward-looking experiment registry
game/starsector/            # gitignored — staged Linux distribution + data/ + mods/
```

I/O paths (game appends `.data` to all `saves/common/` filenames): queue / results / done / heartbeat / shutdown — see [spec 09](docs/specs/09-combat-protocol.md).
