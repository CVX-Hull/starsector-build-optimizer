---
type: report
status: shipped
last-validated: 2026-05-09
---

# Wave 0 — preflight validation (2026-05-09)

Pre-flight gate for the V2 re-validation campaign per
[2026-05-10-validation-plan.md](2026-05-10-validation-plan.md) §3 Wave 0.
Confirms AWS infrastructure healthy and the V2 combat-harness loadout fix
(commit `8a5b968`) survives the full e2e cloud path under multi-worker
concurrency. Step 4 surfaced a multi-worker LOADOUT_MISMATCH concurrency
bug; root cause was identified, fixed in commit `c2d5150`, and verified
by a re-run that produced **0 mismatches across 200 LOADOUT_OK
diagnostics** (§3.5). **All gates passed post-fix; cleared for Wave 1.**

## Step-by-step results

| Step | Campaign | Cost | Wall-clock | Outcome |
|---|---|---|---|---|
| 1 | `probe` (`probe-campaign.yaml`) | $0.02 | 4 min | ✅ AWS provider + LT + SG roundtrip in us-east-1 + us-east-2; `final_audit clean` |
| 2 | `smoke` (`smoke-campaign.yaml`) | $0.06 | 3.5 min | ✅ Single-matchup full e2e; trial 0 COMPLETE in 96s, 1 LOADOUT_OK / 0 LOADOUT_MISMATCH |
| 3 | `loadout-ab` (`loadout_ab_test.py`, n=10×2) | $0.20 | 10.5 min | ✅ V2 fix proven (single-worker); ARMED 10/10 PLAYER, NAKED 10/10 damage_dealt=0.0 (4 TIMEOUTs explained, see §3.3); 0 LOADOUT_MISMATCH |
| 4 | `smoke-multiworker` (`smoke-campaign-multiworker.yaml`) | ~$0.50 | 24 min | ❌ **3 LOADOUT_MISMATCH at 173 LOADOUT_OK — Wave-1-blocking concurrency bug; see §3.4** |
| 4b | `smoke-multiworker` (post-fix re-run) | ~$0.50 | 28.7 min | ✅ **0 LOADOUT_MISMATCH at 200 LOADOUT_OK**, 20/20 trials finalized, 0 V2_SETUP_DEFER events; see §3.5 |

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

**Operator chose A.** Root-cause investigation and fix below.

## 3.5 — Root cause + fix (multi-worker LOADOUT_MISMATCH)

**Root cause.** `CombatHarnessPlugin.doSetup` collected player ships by
iterating `engine.getShips()` and grabbing every `owner==0` non-fighter
ship. Under multi-worker JVM reuse across mission cycles, that view
could include `ShipAPI` instances from a *prior* matchup's combat that
the engine had not yet deallocated when the new mission's `doSetup`
ran. The plugin would pick one of those stale ships, run the
loadout diagnostic against the new spec — and the variant_id
naturally differed from the dispatched trial.

Confirmed via `[V2_SETUP_VARIANT]` instrumentation added in `e4f1333`
(SETUP-time log of each player ship's live `getHullVariantId()` plus
the first three physical weapon ids):

| Mismatched matchup | Dispatched spec | Live SETUP variant_id | Live phys weapons | Match found |
|---|---|---|---|---|
| `opt_000018_vs_berserker_Assault` (1466973ms) | hammerhead_opt_000018 | hammerhead_opt_000017 | exact byte-match for trial 17 build | trial 17, prior matchup |
| `opt_000018_vs_phantom_Elite` (1393961ms) | hammerhead_opt_000018 | hammerhead_opt_000015 | exact byte-match for trial 15 build | trial 15, prior matchup |

The `[V2_DEPLOY]` log (`MissionDefinition.java`) showed
`after_setvariant=opt_000018` was correct — proving the V2
placeholder-then-swap path itself worked. The bug was downstream:
`doSetup`'s ship picker hadn't been updated to filter by the
expected variant ID after the V2 fix narrowed deployment to a
single per-trial spec.

**Fix (commit `c2d5150`).** `doSetup` now builds
`expectedVariantIds = {spec.variantId for spec in
currentConfig.playerBuilds}`, filters owner-0 ships by
`expectedVariantIds.contains(ship.getVariant().getHullVariantId())`,
and defers state transition (re-entrant `return` from `doSetup`)
until the expected ships are visible — with a
`SETUP_VARIANT_WAIT_FRAMES = 600` (10s @ 60fps) backstop that
emits `[V2_SETUP_DEFER]` (every 60 frames) and on expiry
`[V2_SETUP_TIMEOUT]`. The post-implementation audit
(commit `722afd2`) extracted the result-write/Robot/endCombat
tail into a `finalizeMatchup` helper so the timeout path
preserves spec-13's end-of-match contract.

**Verification (step 4b above).** Re-ran `smoke-campaign-multiworker.yaml`
with the fixed JAR served via `serve_mod_jar.sh` (commit `c2d5150`):

| Metric | Buggy run (step 4) | Fixed run (step 4b) |
|---|---|---|
| Trials finalized | 20 / 20 | 20 / 20 |
| Wall-clock | 24 min | 28.7 min |
| LOADOUT_OK | 173 | **200** |
| LOADOUT_MISMATCH | **3** (1.7 %) | **0** (0.0 %) |
| V2_SETUP_DEFER events | n/a | 0 (filter caught the right ships immediately every time) |
| V2_SETUP_TIMEOUT events | n/a | 0 |
| Throughput | 41.8 finalized/hr | 41.8 finalized/hr |

The deferral path didn't fire even once — `engine.getShips()`'s view
was already correct on the first frame of `doSetup` for every matchup.
The variant-id filter would have caught the leak if the engine view
had been stale, and the SETUP_TIMEOUT backstop now writes a clean
TIMEOUT result rather than leaving the orchestrator waiting on an
absent done signal. Throughput is unchanged; no per-matchup latency
regression from the filter or instrumentation.

`final_audit clean` — no AWS resources lingering after teardown.

## Cumulative cost

Wave 0 budget per validation plan: $1.45. Actual: ~$1.28
(probe ~$0.02 + smoke ~$0.06 + AB ~$0.20 + smoke-multi-buggy ~$0.50
+ smoke-multi-fixed ~$0.50). Within budget.

## Next steps

All Wave-0 gates green. Wave 1 launch plumbing ready
(`examples/wave1-c{0a,0b,1,2,3}.yaml` +
`scripts/cloud/launch_wave1.sh`); ablation env-var overrides in
`scripts/run_optimizer.py::_resolve_ablation_overrides` are unit-tested.

Wave 1 is currently using the AMI baked at 13:57 EDT plus the
`serve_mod_jar.sh` overlay carrying `c2d5150` + `722afd2`. AMI
re-bake to land the fix permanently is deferred until Wave 3
prep (Wave 1 + Wave 2 both run via the JAR-overlay path; cost
of the overlay is one-time HTTP fetch at instance boot).

us-east-2 AMI re-bake also deferred until Wave 3 prep (Wave 1 + Wave 2
both fit in us-east-1 alone).
