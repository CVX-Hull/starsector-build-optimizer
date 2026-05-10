---
type: report
status: draft
last-validated: unvalidated
---

# Wave 2 validation report — cross-regime warm-start + frigate cross-cut

> **Status: scaffold.** Wave 2 has not yet launched. Sections marked
> `<<TBD>>` are filled in by the post-Wave-2 automation pass after
> `scripts/cloud/launch_wave2.sh` completes and `scripts/analyze_wave2.py`
> writes `data/wave2-gates.json`.

Wave 2 of the validation campaign defined in [2026-05-10-validation-plan.md](2026-05-10-validation-plan.md)
re-validates the cross-regime warm-start path (mechanism 13b) and the
frigate-gradient gate (mechanism 4 on Wolf) under V2 + the band-aid +
the Java root-cause fix shipped 2026-05-10.

## 1. Studies

| Study | Hull | Regime | Seeds | Trials | Warm-start source | Notes |
|---|---|---|---|---|---|---|
| Wave 2 step 1 | hammerhead | mid | {0} | 250 | `--warm-start-from-regime early` from Wave 1 c2 seed-0 (117 COMPLETE trials) | Mechanism 13b cross-regime warm-start |
| Wave 2 step 2 | wolf | early | {0} | 200 | (none) | Mechanism 4 frigate τ̂² gradient + F4 decision-tree gate |

## 2. Hard gates

| Gate | Threshold | Observation | Verdict |
|---|---|---|---|
| Mechanism 20 — `engine_stats` non-null | 0 nulls | <<TBD>> | <<TBD>> |
| Final-failure LOADOUT_MISMATCH (post-band-aid) | 0 | <<TBD>> | <<TBD>> |
| Throughput | ∈ [92, 152] m/hr/VM | <<TBD>> | <<TBD>> |
| Cost | ≤ budget_usd cap | <<TBD>> | <<TBD>> |

## 3. Mechanism 13 — regime tier conformance

| Run | Threshold | Observation | Verdict |
|---|---|---|---|
| hammerhead-early hullmods all tier ≤ 1 | 100 % | <<TBD: from analyze_wave2 regime_tier_gate>> | <<TBD>> |
| hammerhead-mid hullmods include some tier ≥ 2 | ≥ 1 row | <<TBD>> | <<TBD>> |

## 4. Mechanism 13b — cross-regime warm-start

The launcher (`launch_wave2.sh`) pre-seeds Wave 2 mid-warmstart's
SQLite by copying Wave 1 c2 seed-0's DB. This makes the
`hammerhead__early` study visible alongside the new `hammerhead__mid`
study in the same Optuna backend, so `_enqueue_warm_start_from_regime`
finds the source.

| Threshold | Observation | Verdict |
|---|---|---|
| Mid-study first ≤ 50 trials' params match early-study top-50 (Jaccard ≥ 0.80 OR presence_ratio ≥ 0.80) | <<TBD>> | <<TBD>> |

## 5. Mechanism 4 — Wolf frigate gradient (F4 decision tree)

| Threshold | Observation | Verdict |
|---|---|---|
| Wolf finalized count ≥ 150 (drop-out < 25 %) | <<TBD>> | <<TBD>> |
| `twfe_fitness` variance > 1e-3 (frigate gradient non-degenerate) | <<TBD>> | <<TBD>> |
| Player-win rate ≤ 80 % (F4a check — opponent pool not too easy) | <<TBD>> | <<TBD>> |

If F4a (player wins > 80 %) → too-easy pool, defer Wolf inclusion in
Wave 3.
If F4b (τ̂² < 1e-3 even with V2 + Java fix) → frigate-specific covariate
issue, defer Wolf-tuned covariates to Phase 7 (per validation plan §7).

## 6. Java root-cause fix validation

The 2026-05-10 06:21 EDT fix (`VariantBuilder.uniqueVariantId`) was
deployed via `serve_mod_jar.sh` tailnet override (env vars in
`data/.mod_jar_env`, sourced by `launch_wave2.sh`). Wave 2 is the
first post-fix campaign run.

| Cell | First-attempt mismatch rate | Final-failure rate | Verdict |
|---|---|---|---|
| wave2-mid-warmstart | <<TBD>> | <<TBD>> | <<TBD>> |
| wave2-wolf-early | <<TBD>> | <<TBD>> | <<TBD>> |

Expected: mismatch rate < 0.1 % (vs Wave 1 C2's 3.67 % / C3's 19 %
under the buggy variant).

If mismatch rate stays > 1 %, the Java fix is INSUFFICIENT — escalate
to deeper Java investigation before Wave 3.

## 7. Wave 3 cost re-forecast

Per Wave 1 § 6 we measured 27.3 m/trial for hammerhead-early-production
config. Wave 2 wolf provides the FIRST measurement of frigate
m/trial under V2 + Java fix.

| Hull | m/trial | Trial budget needed for $70 cap | Hulls fit |
|---|---|---|---|
| Hammerhead | 27.3 (Wave 1 C2) | <<TBD>> | <<TBD>> |
| Wolf | <<TBD: from Wave 2 step 2>> | <<TBD>> | <<TBD>> |

If wolf m/trial is much lower (< 10 m/trial), Wave 3 can run all 8
hulls × 600 trials within $70 cap. If close to 27 m/trial, hull-set
reduction is required.

## 8. Wave 2 verdict — gate for #65 unblock

<<TBD: PROCEED / DEFER / RECONFIGURE>>

Decision rule:
- **PROCEED** to Wave 3 with full 8 hulls × 600 trials: cross-regime
  warm-start gate passes, wolf finalized ≥ 150, τ̂² > 1e-3, total
  Wave 3 forecast ≤ $80.
- **PROCEED-WITH-REDUCED-SCOPE** to Wave 3 with 4-6 hulls × 600 trials:
  warm-start gate passes, wolf gates pass, total Wave 3 forecast ≤ $80
  only with reduced hull count.
- **DEFER**: Hard-gate failure (engine_stats null, final-failure
  mismatch > 0, throughput out of range), or wolf F4 trips and Wave 3
  budget can't be met.
- **RECONFIGURE**: Java fix didn't reduce mismatch rate as expected;
  investigate before Wave 3.

## 9. Artifacts

- `data/wave2-gates.json` — analyzer output JSON.
- `data/study_dbs/wave2-mid-warmstart/hammerhead__mid__tpe__seed0.db`
- `data/study_dbs/wave2-wolf-early/wolf__early__tpe__seed0.db`
- `data/logs/wave2-mid-warmstart/hammerhead__mid__tpe__seed0/evaluation_log.jsonl`
- `data/logs/wave2-wolf-early/wolf__early__tpe__seed0/evaluation_log.jsonl`
- `data/campaigns/wave2-{mid-warmstart,wolf-early}/{ledger.jsonl,orchestrator.log,events.log}`
