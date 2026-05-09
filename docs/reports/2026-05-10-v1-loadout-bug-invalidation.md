---
type: report
status: shipped
last-validated: 2026-05-10
---

# V1 combat-harness loadout bug — empirical-claim invalidation (2026-05-10)

**Status:** All Phase 5A–5F empirical claims pending re-validation.

This report is the master invalidation index for the V1 combat-harness loadout bug discovered and fixed 2026-05-10. Every measurement in this project derived from sim runs prior to commit `8a5b968` (V2 fix) is suspect: the sim was not measuring what we thought it was measuring.

## Bug description

### What was wrong

In V1, the combat harness mod constructed `MissionDefinition` by calling `addToFleet(stockVariant)` and then mutated the loadout mid-combat from `CombatHarnessPlugin.doSetup` via `variant.clear()` + `addWeapon()` against the `ShipVariantAPI`. Flux vents and capacitors **did** propagate to the deployed ship because the engine reads them live from `MutableShipStatsAPI`. Weapons and built-in-vs-installed hullmods **did not** propagate: the deployed ship's physical `WeaponAPI` instances are bound at deployment time and the post-deployment `variant.addWeapon()` only mutated the data structure, not the ship.

Net effect: every cloud and local sim run before 2026-05-10 evaluated player ships with **stock-variant weapons**, **partial hullmod fidelity** (the data structure carried the optimizer's choices but the deployed ship's behavior reflected the stock variant), and the optimizer's correct flux/vents allocation. The composite_score, all per-trial fitness values, every Δρ, every LOOO ρ, every ceiling-saturation %, every top-k overlap, and every throughput rate (matchups/hr/VM, trials/hr, cloud-vs-local speedup) was computed against this confounded measurement.

### How it was found

Smoke run #12's `LoadoutDiagnostic` block (added during the V2 design pass) compared `spec_weapons` (what the optimizer asked for) against `live_weapons` (what was actually mounted on the deployed `ShipAPI`). The mismatch was unambiguous: `spec_weapons` carried 8 entries, `live_weapons` was empty.

### V2 fix

Commit `8a5b968` ("Loadout fix v2: addToFleet placeholder + setVariant — works around addFleetMember retreat bug") changed the deployment path:

1. Deploy a stock placeholder via `addToFleet(side, anyStockVariantForHull, FleetMemberType.SHIP, fleetMemberId, false)`.
2. Swap to the optimizer-generated variant via `member.setVariant(VariantBuilder.createVariant(spec), false, true)` BEFORE the deployment screen processes the fleet — pre-deployment swap propagates to the physical ship.
3. `CombatHarnessPlugin.doSetup` sets CR live on each deployed `ShipAPI` (`setCurrentCR` + `setCRAtDeployment` + `setRetreating(false, false)`) — `getCurrentCR()` does not inherit from the FleetMember's repair tracker and CR=0 triggers auto-retreat.

Validated end-to-end via `scripts/cloud/loadout_ab_test.py` (commit `f600678`):
- ARMED Hammerhead ×3: dealt damage and won.
- NAKED Hammerhead ×3 (0 weapons + `shield_shunt`): dealt EXACTLY 0.0 damage and lost.
- Flux profile (6, 6) used as the control.

The `LoadoutDiagnostic` block in `doSetup` is retained as a permanent canary; the orchestrator emits one `LOADOUT_OK` INFO line per matchup on success and a structured `LOADOUT_MISMATCH` WARN on any field divergence.

### Related fix

Commit `913883a` raised default `MatchupConfig.time_mult` from 3.0 to 5.0 (engine ceiling). Pre-fix runs had an additional confound where matchups timed out before resolution under particular fleet compositions.

## Invalidated artifacts — pre-V2 experiment directories

The following dated experiment directories under `experiments/` produced numbers that fed Phase 5A–5F design decisions and reference docs. **All are being deleted as part of the V1 invalidation cleanup.** A historical pointer remains in [experiments/INDEX.md](../../experiments/INDEX.md). The "what it claimed" column captures the design intent so a reader of an old reference doc can map a stale citation back to its origin.

| Directory | Date | What it claimed |
|---|---|---|
| `experiments/hammerhead-overnight-2026-04-13` | 2026-04-13 | First Hammerhead overnight cloud run; baseline for Phase 5A TWFE deconfounding gate. |
| `experiments/hammerhead-twfe-2026-04-13` | 2026-04-13 | TWFE A0/A1/A2/A3 ablation on the overnight Hammerhead corpus; Phase 5A "shipped" gate. |
| `experiments/phase5b-curriculum-simulation` | 2026-04-1X | Synthetic Wilcoxon-pruner + ASHA simulation feeding Phase 5B parameter choices. |
| `experiments/signal-quality-2026-04-17` | 2026-04-17 | First-pass signal-quality LOOO grid that motivated EB shrinkage adoption. |
| `experiments/phase5d-covariate-2026-04-17` | 2026-04-17 | EB covariate-count sweep + FEATURE_COUNT_REPORT.md; sized p=7 from a synthetic + Hammerhead corpus. |
| `experiments/phase5d-ttk-signal-2026-04-18` | 2026-04-18 | TTK-as-8th-EB-covariate benchmark; the +0.136 Δρ "production-sized" claim deferred to Phase 5F. |
| `experiments/signal-quality-5d-2026-04-18` | 2026-04-18 | 4-regime calibration sweep that argued Box-Cox A3 holds across covariate-strength regimes. |
| `experiments/phase5e-cloud-validation-2026-04-18` | 2026-04-18 | Cloud-side Box-Cox validation; ceiling 25% → 0.5% and top-5 14× claim. |
| `experiments/cloud-benchmark-2026-04-18` | 2026-04-18 | First full c7a.2xlarge × workers throughput benchmark; 122 matchups/hr/VM and 27 trials/hr aggregate. |
| `experiments/phase6-planning` | 2026-04-1X | Phase 6 cost / sizing plans derived from the throughput numbers above. |
| `experiments/phase7-layer34-benchmark-2026-04-19` | 2026-04-19 | Phase 7 prep layer-3/4 sampler comparison aborted at T+48m. |
| `experiments/phase7-prep-2026-04-19` | 2026-04-19 | First Phase 7 prep relaunch attempt; aborted on winning-rate diagnostics. |
| `experiments/phase7-prep-aborted-2026-04-19` | 2026-04-19 | Same campaign retained for post-mortem. |
| `experiments/shakedown-2026-04-19-gate` | 2026-04-19 | Concurrent-dispatch shakedown that surfaced the three SG-replication / score-matrix / per-study-eval-log fixes. |
| `experiments/smoke-2026-04-19-gate` | 2026-04-19 | Tier-2 smoke gate run; passed under V1 sim. |
| `experiments/stale-pre-seed0-fix-2026-04-19` | 2026-04-19 | Pre-fix corpus retained for the seed-disambiguation diff. |

The deletion is intentional. Keeping directories whose numbers are confounded is worse than deleting them because (a) future readers will accidentally treat them as authoritative, and (b) re-validation reports will produce the canonical numbers under V2 — the V1 numbers are not "old data we'll re-analyze", they're "data from a different experiment than we thought we were running".

Post-V2 evidence currently lives only at `data/campaigns/smoke*` (gitignored campaign artifacts from the 2026-05-09 Tier-2 smoke gate plus the multi-hull regression smoke). Those directories are not under version control; the canonical record of the smoke gate's pass is the launch evidence in [docs/reports/2026-04-19-phase7-prep-relaunch.md](2026-04-19-phase7-prep-relaunch.md) plus the V2 fix commit chain.

## Doc passages stripped

The following reference and spec docs had inline empirical numbers replaced with re-validation-pending pointers as part of the 2026-05-10 cleanup commit. The cleanup commit's diff is the canonical record of exactly what changed; this list is the directory of files touched.

- `CLAUDE.md` — Phase 4, 5A–5F, 6, 7-prep paragraph runs restructured to sub-bullets; throughput, Δρ, and ceiling/overlap numbers stripped.
- `docs/project-overview.md` — Phase 5E saturation/overlap callout stripped.
- `docs/reference/phase4-research-findings.md` — 203-trial Eagle / 0.4% win-rate / LOOO ρ / Cohen's d numbers stripped.
- `docs/reference/phase5-signal-quality.md` — full results sections rewritten.
- `docs/reference/phase5a-deconfounding-theory.md` — synthetic-sim numbers tied to the invalid Hammerhead generative model stripped.
- `docs/reference/phase5c-opponent-curriculum.md` — overnight Hammerhead numbers stripped.
- `docs/reference/phase5d-covariate-adjustment.md` — REPORT.md references, full Δρ tables, TTK §7 numbers stripped (the design rationale is preserved; only the dated numbers are moved out).
- `docs/reference/phase5e-shape-revision.md` — every numeric strategy table stripped.
- `docs/reference/phase5f-regime-segmented-optimization.md` — Hammerhead 89% concentration / ~80% redirect / etc. stripped.
- `docs/reference/phase6-cloud-worker-federation.md` — throughput rates (122/hr, 27/hr, 2.4×) and all derived $-figures stripped.
- `docs/reference/throughput-optimization.md` — full 384/554/305/753 trials/hr table stripped.
- `docs/reference/implementation-roadmap.md` — every duplicated number stripped (this was the biggest aggregator).
- `docs/specs/17-throughput-estimator.md` — 64 matchups/hr c7i reference stripped.
- `docs/specs/22-cloud-deployment.md` — cloud-vs-local throughput row stripped.
- `docs/specs/28-deconfounding.md` — inline ρ values at the result-citing lines stripped.
- `.claude/skills/cloud-worker-ops.md` — campaign budget figures stripped.

The replacement pattern is: "Empirically validated to ≥ \<threshold\> on production data. See [docs/reports/INDEX.md](INDEX.md) for the latest validation report" when a design threshold exists, or "Pending re-validation under V2 loadout fix; see [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](2026-05-10-v1-loadout-bug-invalidation.md)" otherwise.

## Status

**All Phase 5A–5F empirical claims pending re-validation.**

The design rationale for each phase remains valid — TWFE, EB shrinkage, Box-Cox warping, regime segmentation, and concurrent-dispatch correctness are theory-driven decisions whose principled grounding does not depend on the V1 numbers. What needs to be re-established under V2 is the empirical magnitude of each effect on production data:

- Whether Phase 5A's TWFE A0/A1/A2/A3 ablation crosses its design threshold.
- Whether Phase 5D's EB shrinkage produces the ≥+0.03 Δρ vs A0 it was scoped against.
- Whether Phase 5E's Box-Cox warping delivers the ceiling-saturation and top-k benefits at production-relevant N.
- Whether Phase 5F's regime segmentation is empirically distinguishable from the unrestricted endgame regime under the Hammerhead and Eagle archetypes.
- Whether the cloud throughput numbers used in the Phase 6 cost model (and therefore the Phase 7-prep budget) hold under V2's setup-time overhead (per-matchup `setVariant` + `LoadoutDiagnostic` is non-zero).

## Forward link

See [docs/reports/INDEX.md](INDEX.md) for re-validation reports as they land. Each re-validation report should reference this file under `supersedes` (frontmatter) once it produces a number that replaces a stripped claim.
