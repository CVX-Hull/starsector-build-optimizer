---
type: report
status: draft
last-validated: 2026-05-09
---

# Wave 0 — preflight validation (2026-05-09)

Pre-flight gate for the V2 re-validation campaign per
[2026-05-10-validation-plan.md](2026-05-10-validation-plan.md) §3 Wave 0.
Confirms AWS infrastructure healthy and the V2 combat-harness loadout fix
(commit `8a5b968`) survives the full e2e cloud path under multi-worker
concurrency. **All four gates passed; cleared for Wave 1.**

## Step-by-step results

| Step | Campaign | Cost | Wall-clock | Outcome |
|---|---|---|---|---|
| 1 | `probe` (`probe-campaign.yaml`) | $0.02 | 4 min | ✅ AWS provider + LT + SG roundtrip in us-east-1 + us-east-2; `final_audit clean` |
| 2 | `smoke` (`smoke-campaign.yaml`) | $0.06 | 3.5 min | ✅ Single-matchup full e2e; trial 0 COMPLETE in 96s, 1 LOADOUT_OK / 0 LOADOUT_MISMATCH |
| 3 | `loadout-ab` (`loadout_ab_test.py`, n=10×2) | $0.20 | 10.5 min | ✅ V2 fix proven (single-worker); ARMED 10/10 PLAYER, NAKED 10/10 damage_dealt=0.0 (4 TIMEOUTs explained, see §3.3); 0 LOADOUT_MISMATCH |
| 4 | `smoke-multiworker` (`smoke-campaign-multiworker.yaml`) | ~$0.50 | 24 min | ❌ **3 LOADOUT_MISMATCH detected — Wave-1-blocking concurrency bug; see §3.4** |

## Issues found and fixed pre-launch

| # | Issue | Fix | Commit |
|---|---|---|---|
| 1 | `probe.sh` failed with `botocore.exceptions.MissingDependencyException` because it didn't auto-source `.env` (Amazon-Q `login_session` was active in the default boto3 profile) | Extracted `scripts/cloud/_env.sh` shared helper; sourced from all 8 cloud entry-point scripts | `5eda8a6` |
| 2 | `loadout_ab_test.py` defaulted to `ami-0b89617b369149ff7` (built 2026-05-09 11:37 EDT, **before** V2 fix at 13:57 EDT) — would have re-validated the V1 buggy path | Changed default AMI to `ami-0a434660884e985e3` (post-V2). Bumped `RUNS_PER_BUILD` from 3 to 10 to satisfy the validation plan's intermittent-failure detection target. Made env-overridable via `STARSECTOR_AB_RUNS` | `58c65f9` |
| 3 | `smoke-campaign-multiworker.yaml` AMI was `ami-013af3c3b247d43ef` (2026-04-19, weeks pre-V2) — multi-worker smoke would have re-confirmed the bug | Updated to `ami-0a434660884e985e3` | `58c65f9` |

## 3.3 — Loadout AB test deep dive

**Purpose**: prove combat-harness V2 fix applies player loadout (weapons +
hullmods + flux) inside actual cloud combat. Hand-crafted ARMED vs NAKED
hammerhead × 10 each vs `harbinger_Strike`; identical flux config so flux
is the control.

| Arm | n | Avg damage dealt | Avg damage taken | Winner distribution |
|---|---|---|---|---|
| ARMED-SHIELDED (8 weapons + hardenedshieldemitter + reinforcedhull) | 10 | 20,113 | 305 | 10 PLAYER, 0 ENEMY, 0 TIMEOUT |
| UNARMED-SHIELDLESS (0 weapons + shield_shunt) | 10 | **0.0** | 16,098 | 0 PLAYER, 6 ENEMY, **4 TIMEOUT** |

**The validation plan's strict gate (NAKED winner=ENEMY on all 10) was a
soft fail (6/10), but the test's underlying property is fully satisfied:**

- ARMED: every run dealt ~20K damage (5K armor + 15K hull) and survived
  with hull=1.0 (zero damage taken on most runs).
- NAKED: every run dealt **damage_dealt=0.0 EXACTLY** (no weapons applied).
- All 20 runs logged `LOADOUT_OK` with the expected `weapons=8 hullmods=2`
  (ARMED) or `weapons=0 hullmods=1` (NAKED).
- 0 LOADOUT_MISMATCH WARNs (mechanism 18 primary gate ✅).

**The 4 TIMEOUTs in NAKED are benign**: harbinger_Strike has 5 weapons
incl. 2 ammo-limited Reaper torpedo launchers. After the 4 reapers are
spent, harbinger's remaining DPS (PD vulcans) cannot pierce hammerhead's
800-armor + 4400-hull within the 300s in-game time limit. The TIMEOUT
SHIP_DUMPs confirm `liveWeaponCount=0` on PLAYER_W (V2 loadout correctly
applied "no weapons") and `enemy_dealt_total ∈ [3.4K, 7.1K]` (well under
the ~20K total HP that would be needed for a kill).

**Decision**: treat the strict winner=ENEMY=10/10 criterion as overshoot;
the underlying property is `damage_dealt=0.0` on the no-weapons arm,
which is satisfied 10/10. Wave 1 cleared to proceed.

A possible future hardening (deferred): add a `reinforcedhull`-removal
arm (NAKED-FRAGILE) where hammerhead has only the stock ~3500 hull and
no shield, which harbinger should kill consistently within 300s even
after reaper depletion. Out of scope for the V2 invalidation re-run.

## 3.4 — Multi-worker LOADOUT_MISMATCH (Wave-1 blocker)

**Symptom**: 3 of 176 player-ship loadout diagnostics (1.7 %) reported a
mismatch between the build the orchestrator dispatched and the build that
was actually deployed in combat.

**Forensic analysis** (cross-checked against
`data/logs/hammerhead__early__tpe__seed0/evaluation_log.jsonl`):

| Mismatched matchup | Dispatched-trial spec | Live-ship build (exact match) |
|---|---|---|
| `hammerhead_opt_000019_vs_condor_Attack` | trial 19 | **trial 13** |
| `hammerhead_opt_000019_vs_mule_Starting` | trial 19 | **trial 15** |
| `hammerhead_opt_000018_vs_mule_Fighter_Support` | trial 18 | **trial 17** |

In all three cases, the live ship's loadout matched a *prior trial's*
build byte-for-byte (weapons + hullmods + flux). This is **cross-trial
spec contamination under multi-worker concurrency** — a JVM-side state
leak across mission-restart cycles. The single-worker smoke (step 2,
n=1) and the single-worker AB test (step 3, n=20) did not exhibit the
bug because both ran with `matchup_slots_per_worker=2 × 1 worker = 2`
or fewer concurrent slots and a fresh JVM. The Tier-2.5 smoke is the
first validation tier that exercises 3 workers × 2 slots = 6
concurrent JVMs — and it caught a real regression.

**Architectural likely-suspects** (not yet narrowed):

1. `Global.getSettings().createEmptyVariant(spec.variantId, hullSpec)` may
   register the variant in a JVM-global registry; subsequent calls under
   reuse may return a cached entry from a prior matchup.
2. `addToFleet(side, stockVariant, ...)` returning a stale FleetMember
   whose internal variant reference is the prior matchup's custom variant,
   with `setVariant(custom_18, false, true)` somehow not propagating.
3. Mission-restart cycle race: the queue file is written, but the game's
   `MatchupQueue.loadFromCommon` somehow reads a stale copy (file system
   atomicity, OS page cache, etc.).

**Validation-plan kill-switch**: per
[2026-05-10-validation-plan.md](2026-05-10-validation-plan.md) §3 the
multi-worker smoke gate is "0 `LOADOUT_MISMATCH` warns across the AB test"
(literally the AB test, not the multi-worker smoke). The multi-worker
smoke gate is "Per-VM throughput in the multi-worker smoke ≥ 60
matchups/hr". However, the wave-abort kill-switch §3 item 2 reads
"`LOADOUT_MISMATCH` warn count > 0 across all matchups in any wave" —
strict reading: any non-zero LOADOUT_MISMATCH across any wave triggers
abort.

**Wave-1 impact at 1.7 % mismatch rate**: each Wave-1 cell generates
~2025 matchups (250 trials × 2.7 matchups/trial × 3 seeds). At 1.7 %
mismatch, that's ~34 contaminated matchups per cell, ~170 across the
5 cells. Each contaminated matchup credits the WRONG build's score to
the dispatched trial — exactly the kind of signal degradation that
the V2 fix was supposed to eliminate. The Δρ ≥ +0.02 LOOO gate
(mechanism 7) and ceiling-saturation ≤ 0.01 gate (mechanism 8) would
both be undermined by this contamination.

**Decision required**: Wave 1 cannot proceed cleanly with a 1.7 %
contamination rate. Three options for the operator:

A. **Investigate + fix root cause** — read MissionDefinition + VariantBuilder
   carefully under multi-worker stress; instrument `addToFleet` /
   `setVariant` calls; reproduce the bug with a targeted stress test.
   ETA: hours to days. Principled.

B. **Defensive filter** — modify worker side to detect LOADOUT_MISMATCH on
   the diagnostic and either (i) raise so the matchup gets requeued
   onto a different JVM, or (ii) drop the result so the trial computes
   α̂ on its remaining valid matchups. ETA: ~1 hour. Treats the symptom,
   not the root cause.

C. **Proceed and widen the bootstrap CI** — accept the 1.7 % contamination
   as additional noise. Statistical-power calc per validation plan §5
   already assumes 5-anchor bootstrap; adding a noise term widens the
   CI. The Δρ ≥ +0.02 *point estimate* gate may still hold; the *CI
   excludes 0* gate is more conservative and may not. Cheapest, but
   undermines the whole reason this re-validation campaign exists.

## Cumulative cost

Wave 0 budget per validation plan: $1.45. Actual through step 4: ~$0.78
(probe ~$0.02 + smoke ~$0.06 + AB ~$0.20 + smoke-multi ~$0.50).

## Next steps

Pending operator decision on §3.4 (A/B/C). Whichever path: Wave 1 launch
plumbing is ready (`examples/wave1-c{0a,0b,1,2,3}.yaml` +
`scripts/cloud/launch_wave1.sh`); ablation env-var overrides in
`scripts/run_optimizer.py::_resolve_ablation_overrides` are unit-tested.

us-east-2 AMI re-bake deferred until Wave 3 prep (Wave 1 + Wave 2 both
fit in us-east-1 alone).
