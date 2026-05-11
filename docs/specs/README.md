---
type: index
status: shipped
last-validated: 2026-05-10
---

# Specs — Index and number registry

Module / protocol contracts. See [../CONVENTIONS.md](../CONVENTIONS.md) for the category contract.

## Numbering gaps

Spec numbers are monotonically assigned and never reused. The following gaps are intentional and reflect deletions / renumbering through the project's history:

| # | Status | Reason |
|---|---|---|
| 02 | deleted | Original `02-hullmod-effects.md`. The Python `hullmod_effects.py` registry was the source of 14 audit-discovered game-rule drift bugs (Phase 7 prep, 2026-04-19). The module was deleted in favour of [29-game-manifest.md](29-game-manifest.md), which sources hullmod applicability and damage multipliers from the live engine via `ManifestDumper`. Deletion landed in commit `fd1b4fb` (Phase-7-prep relaunch: spec + CI updates, Commit F). |
| 20 | deleted | Original `20-curtailment-monitor.md`. Stochastic curtailment was superseded by Phase 5B `WilcoxonPruner` + ASHA. Deletion landed in commit `a3f854a` (Hull-fraction combat fitness, WilcoxonPruner, and curtailment removal). |
| 21 | deleted | Original `21-timeout-tuner.md`. Phase-3.5 timeout self-tuning went dormant once `WilcoxonPruner` started terminating runs by stat-significance instead of wall-clock; the module was deleted in commit `fd1b4fb` (Phase-7-prep relaunch). The dormancy is captured in [../reports/2026-04-19-phase6-deferred-audit.md](../reports/2026-04-19-phase6-deferred-audit.md) under H1. |

When adding a new spec, take the next unused number after `31` (currently `32`). Never reuse `02`, `20`, or `21`.

## Spec catalogue

See [../project-overview.md](../project-overview.md) for the canonical phase-grouped spec listing. This file is the number-registry and gap-rationale; the project-overview is the human-friendly tour.

## Empirical-numbers rule

Specs are pure contracts. They do not contain inline empirical numbers, throughput rates, ablation tables, or dated measurements. When a spec needs to reference such a number (e.g. a default value derived from benchmarks), it links to a report under [../reports/](../reports/) instead of inlining.

See [../CONVENTIONS.md](../CONVENTIONS.md) §"The empirical-numbers rule" for the full policy.
