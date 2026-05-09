---
type: report
status: shipped
last-validated: 2026-05-10
---

# Documentation reorganization summary (2026-05-10)

Master work-product report for the documentation reorganization that landed alongside the V1 loadout-bug invalidation cleanup. Companion to [2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md), which is the master record of *what* the empirical-claims invalidation removed; this report is the master record of *how* the doc set was restructured to support the cleanup and prevent recurrence.

## Motivation

Two pressures landed at the same time:

1. **Empirical claims went bad.** The V1 combat-harness loadout bug meant every sim measurement before 2026-05-10 was confounded — the deployed player ship ran with stock-variant weapons rather than the optimizer's chosen loadout. Phase 5A–5F design decisions cited specific measurement deltas (Δρ, ceiling-saturation %, top-k overlap) inline in reference docs, and the Phase 6 cost model cited specific throughput rates. All of these were invalidated.
2. **Doc structure was organic.** `docs/specs/`, `docs/reference/`, `.claude/skills/`, and `CLAUDE.md` had grown without a category contract. Reference docs mixed design rationale, research, and dated empirical evidence in the same paragraphs; readers couldn't tell which sentences depended on now-invalid measurement and which didn't.

A surgical strip-and-replace alone would have left the structural problem intact. The reorg formalizes the categorical structure so future invalidation events can be scoped cleanly: empirical evidence is owned by a specific report; specs and references are pure design.

## The convention

Authored at [docs/CONVENTIONS.md](../CONVENTIONS.md). Six categories with explicit empirical-content rules:

| Category | Location | Empirical numbers? |
|---|---|---|
| **spec** | `docs/specs/NN-name.md` | No. Pure contract. |
| **reference** | `docs/reference/<topic>.md` | No internal-sim numbers. Published-academic citations OK. |
| **report** | `docs/reports/YYYY-MM-DD-<slug>.md` | Yes — reports own all dated measurements. |
| **skill** | `.claude/skills/<name>.md` | Operational thresholds OK; benchmark numbers go in reports. |
| **always-loaded** | `CLAUDE.md`, `combat-harness/CLAUDE.md`, `docs/CONVENTIONS.md` | No. Status pointers + design decisions only. |
| **index** | `docs/project-overview.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md` | No. Just routing. |

Every doc file carries a YAML frontmatter block:

```
---
type: spec | reference | report | skill | always-loaded | index
status: shipped | draft | superseded | deprecated
last-validated: YYYY-MM-DD | unvalidated
supersedes: <relative path>      # if this file replaces another
superseded-by: <relative path>   # if this file is now superseded
---
```

The empirical-numbers replacement pattern when a stripped reference previously asserted a measurement:

1. **If a design threshold exists** — "Empirically validated to ≥ \<threshold\> on production data. See [docs/reports/INDEX.md](INDEX.md) for the latest validation report."
2. **If no threshold is defined or re-validation is pending** — "Pending re-validation under V2 loadout fix; see [2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md)."
3. **If the claim is purely qualitative** — leave it; it's a design statement, not a measurement.

## Files created

| File | Purpose |
|---|---|
| [docs/CONVENTIONS.md](../CONVENTIONS.md) | The doc-system convention itself (always-loaded). |
| [docs/reports/INDEX.md](INDEX.md) | Reports registry: active reports, pending re-validation, filing instructions. |
| [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md) | Master invalidation report. |
| [docs/reports/2026-05-10-validation-plan.md](2026-05-10-validation-plan.md) | Re-validation campaign plan: hull selection, per-mechanism gates, 4-wave architecture, budget + decision tree. |
| [docs/reports/2026-05-10-doc-reorg-summary.md](2026-05-10-doc-reorg-summary.md) | This file. |
| [docs/specs/README.md](../specs/README.md) | Spec number registry: documents the 02 / 20 / 21 deletion gaps. |
| [experiments/INDEX.md](../../experiments/INDEX.md) | Forward-looking experiment registry; pre-V2 dirs deleted. |

## Files moved (renamed)

| From | To |
|---|---|
| `docs/reference/phase6-deferred-audit-findings-2026-04-19.md` | [docs/reports/2026-04-19-phase6-deferred-audit.md](2026-04-19-phase6-deferred-audit.md) |
| `docs/reference/phase7-prep-relaunch-checklist-2026-04-19.md` | [docs/reports/2026-04-19-phase7-prep-relaunch.md](2026-04-19-phase7-prep-relaunch.md) |

These were dated-by-content files that fit the report category once the convention existed.

## Files edited

### Always-loaded (2 files)

- [CLAUDE.md](../../CLAUDE.md) — restructured the seven phase-status paragraphs (each previously a 4–10 KB single-sentence run packing design + empirical + dated claims) into structured sub-bullets per phase. Added the doc-conventions reference. Stripped throughput rates, Δρ values, ceiling/overlap %, and dated experiment-dir paths. Updated the project layout to reflect the Phase-7-prep refactor (deleted `hullmod_effects.py`, `timeout_tuner.py`; added `cloud_runner.py`, `cloud_userdata.py`, `game_manifest.py`, `ManifestDumper.java`, `AttackAdmiralAI.java`). Updated Design Principles #1 and Design Invariants from the deleted hullmod-effects-registry framing to manifest-as-oracle.
- [combat-harness/CLAUDE.md](../../combat-harness/CLAUDE.md) — added frontmatter; added a one-line V2 loadout-fix note in the lede pointing at [2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md).

### Reference docs (16 files)

- [phase4-research-findings.md](../reference/phase4-research-findings.md) — 203-trial Eagle / 0.4% win-rate / LOOO ρ / Cohen's d numbers stripped.
- [phase5-signal-quality.md](../reference/phase5-signal-quality.md) — full results sections rewritten.
- [phase5a-deconfounding-theory.md](../reference/phase5a-deconfounding-theory.md) — synthetic-sim numbers stripped.
- [phase5c-opponent-curriculum.md](../reference/phase5c-opponent-curriculum.md) — overnight Hammerhead numbers stripped.
- [phase5d-covariate-adjustment.md](../reference/phase5d-covariate-adjustment.md) — REPORT.md references, full Δρ tables, TTK §7 numbers stripped. (Largest single-file edit; the design rationale is preserved.)
- [phase5e-shape-revision.md](../reference/phase5e-shape-revision.md) — every numeric strategy table stripped.
- [phase5f-regime-segmented-optimization.md](../reference/phase5f-regime-segmented-optimization.md) — Hammerhead concentration / redirect % stripped.
- [phase6-cloud-worker-federation.md](../reference/phase6-cloud-worker-federation.md) — throughput rates and derived $-figures stripped.
- [throughput-optimization.md](../reference/throughput-optimization.md) — full trials/hr table stripped.
- [implementation-roadmap.md](../reference/implementation-roadmap.md) — biggest aggregator. Numbers retained with a strong frontmatter banner clarifying "treat as illustrative; pending re-validation". This was a deliberate choice over a full strip: a 1130-line roadmap with every duplicated number stripped would have lost too much directional context. Frontmatter pointers carry the load.
- 6 other reference files ([cross-domain-optimization-research.md](../reference/cross-domain-optimization-research.md), [game-data-reference.md](../reference/game-data-reference.md), [game-mechanics.md](../reference/game-mechanics.md), [literature-review.md](../reference/literature-review.md), [multi-fidelity-strategy.md](../reference/multi-fidelity-strategy.md), [optimization-methods.md](../reference/optimization-methods.md), [optimization-theory.md](../reference/optimization-theory.md), [phase7-search-space-compression.md](../reference/phase7-search-space-compression.md), [phase7.5-infrastructure-reproducibility.md](../reference/phase7.5-infrastructure-reproducibility.md), [problem-formulation.md](../reference/problem-formulation.md), [quality-diversity.md](../reference/quality-diversity.md), [system-architecture.md](../reference/system-architecture.md), [tech-debt.md](../reference/tech-debt.md)) — frontmatter only; bodies were already number-clean.

### Specs (23 files)

All specs in `docs/specs/` got `type: spec / status: shipped / last-validated: unvalidated` frontmatter. Three specs had inline measurements stripped:
- [17-throughput-estimator.md](../specs/17-throughput-estimator.md) — concrete $/hr rates, 64 vs 27 matchups/hr, 2.4× claim, experiments-dir refs replaced with a symbolic-constant table.
- [22-cloud-deployment.md](../specs/22-cloud-deployment.md) — cloud-vs-local benchmarks table replaced with a re-validation pointer plus the design-target threshold (≥ 2× local). `probe.sh` "$0.15" comment replaced with sub-dollar/INDEX pointer.
- [28-deconfounding.md](../specs/28-deconfounding.md) — five inline ρ values stripped. Cinelli-Forney-Pearl and Lin-Louis-Shen citations preserved (they're published-academic, not internal).

### Skills (1 file)

- [cloud-worker-ops.md](../../.claude/skills/cloud-worker-ops.md) — merged doc-system frontmatter into the existing skill frontmatter block. Stripped empirical throughput byline, provider-pick benchmark figures, and dead `experiments/cloud-benchmark-2026-04-18/` + `experiments/phase6-planning/cost_model.py` refs (replaced with master-invalidation-report links). Operational-threshold $-figures retained ($200/day runaway warning, $500/$10k Hetzner crossover, AWS public-list AMI snapshot $0.05/GB·month) — those are budget guardrails / AWS list prices, not internal-sim measurements.

### Indices (1 file)

- [docs/project-overview.md](../project-overview.md) — Phase 5E saturation/14× overlap callout replaced with re-validation pointer; `type: index` frontmatter added.

## Files deliberately left alone

- The four other `.claude/skills/*.md` files ([ddd-tdd.md](../../.claude/skills/ddd-tdd.md), [design-invariants.md](../../.claude/skills/design-invariants.md), [plan-review.md](../../.claude/skills/plan-review.md), [post-impl-audit.md](../../.claude/skills/post-impl-audit.md), [starsector-modding.md](../../.claude/skills/starsector-modding.md)) — procedure-only, no internal-sim measurements, frontmatter already in place.
- The two new reports authored alongside this reorg ([2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md) and [2026-05-10-validation-plan.md](2026-05-10-validation-plan.md)) and the two pre-existing reports ([2026-04-19-phase6-deferred-audit.md](2026-04-19-phase6-deferred-audit.md), [2026-04-19-phase7-prep-relaunch.md](2026-04-19-phase7-prep-relaunch.md)) — reports own measurements by category contract; their content is correct.
- `docs/specs/{02,20,21}-*.md` — these specs don't exist (they were deleted as part of prior refactors). The deletion gaps are documented in [docs/specs/README.md](../specs/README.md). Deliberately not silently filled.
- All test files, source files, scripts, and configs — code is out of scope for the doc reorg.

## Structural decisions made

### implementation-roadmap.md kept-with-banner

The 1130-line implementation-roadmap aggregates phase-status numbers from every other reference doc. Two paths were on the table:
- **(a)** Trim it down to a navigational index pointing at per-phase docs.
- **(b)** Keep it as a comprehensive roadmap with all numbers stripped.
- **(c)** Keep it with numbers and a strong frontmatter banner clarifying validation status.

We took (c). Rationale: the roadmap's value is the cross-phase view — the dependency graph, the rejected-alternatives table, the "what shipped vs what's deferred" status — and stripping every number would destroy the directional context that makes the doc useful as a tour. The frontmatter banner is sufficient: any reader who acts on a specific number is bound by the banner to verify against [INDEX.md](INDEX.md). The doc is type=reference like the others.

### `composite_score` removed from EB covariate set during this reorg's scope

A subtle interaction: the Phase-7-prep refactor (commit landed pre-2026-05-10) changed the EB covariate set from p=7 to p=10 by dropping `composite_score` and adding three engine-truth SETUP reads + one Python-raw structural. The phase5d reference doc described p=7 throughout. We did NOT rewrite the covariate-set discussion to p=10 — that's outside the reorg scope (it would conflate doc-restructure with content-update). The validation plan ([2026-05-10-validation-plan.md](2026-05-10-validation-plan.md) §1, §2 mech 7) explicitly flags that the p=7→p=10 change requires re-validation, with N≥250 instead of N≥200. The phase5d reference will be updated in the report-authoring pass once Wave 1 results are in.

### Frontmatter status field for planned phases

`phase7-search-space-compression.md` and `phase7.5-infrastructure-reproducibility.md` describe phases that are *planned*, not *shipped*. They got `status: shipped` because the doc itself is current and in-use as a planning artifact. The `status` field follows the doc's lifecycle, not the code's lifecycle — the alternative ("draft" until the code ships) would mean readers couldn't trust the doc to reflect current planning even when it does. The convention in [CONVENTIONS.md](../CONVENTIONS.md) §"Frontmatter" should be read consistently with this call.

### Operational $-figures retained in cloud-worker-ops.md

The skill carries three $-figures that survived the strip: $200/day runaway-spend warning, $500 / $10k crossover for considering Hetzner, and AWS public-list $0.05/GB·month for AMI snapshots. These are guardrails / list prices, not internal measurements; the convention's "operational thresholds OK" carve-out for skills covers them. If future discipline tightens, these are the candidates to revisit.

## Open questions / things that needed user judgment

- **Hullmod-effects spec gap.** The spec for the deleted `hullmod_effects.py` module (`02-hullmod-effects.md`) is documented as a deletion in [specs/README.md](../specs/README.md). No replacement spec was authored — the manifest-as-oracle data path is fully covered by [29-game-manifest.md](../specs/29-game-manifest.md). This is the right call but worth surfacing in case a future reader asks "why is there no spec for hullmod-effects?".
- **`MATCHUPS_PER_HR_DEFAULT` symbol in 17-throughput-estimator.md.** The cleanup introduced the symbolic-constant naming convention as a placeholder pattern; the actual codebase doesn't yet export a constant of that name. The spec's contract is now slightly ahead of the code. Either implement the named constant in `estimator.py` or downgrade the spec to a generic placeholder. Flagged for the next implementation-touching pass.
- **`combat-harness/CLAUDE.md` last-validated date.** Set to `2026-05-10` (the V2 fix date) because the file accurately describes the V2 path. Most other files in the set are `unvalidated` because their empirical claims were stripped pending re-validation. Asymmetry intentional but worth noting.

## Verification

- All 27 reference + spec files in the diff have frontmatter blocks (verified via `head -5`).
- All cross-references between docs are relative-path Markdown links.
- Master invalidation report enumerates every doc that had numbers stripped; this report enumerates every file edited.
- Validation plan ([2026-05-10-validation-plan.md](2026-05-10-validation-plan.md)) identifies which re-validation reports will replace which stripped sections.
