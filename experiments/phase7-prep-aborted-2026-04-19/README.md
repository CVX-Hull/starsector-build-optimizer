# Phase 7 prep aborted run — 2026-04-19

Cloud-federated run aborted at T+48 min after detecting a systemic win-rate
anomaly (wolf / lasher 0% wins across 1000+ matchups each). Full artifact
backup for postmortem analysis.

## Config at launch
- Commit: see `config/git_head.txt`
- YAML: `config/phase7-prep.yaml` (8 hulls × early × 1 seed × 600 trials,
  96 c7a.2xlarge spot VMs across us-east-1 + us-east-2, budget cap $70)
- Sampler: TPE (CatCMAwM removed 2026-04-19 — fully-categorical search
  space incompatible with `cmaes.CatCMAwM.x_space`)

## What was observed
| Hull       | Hull size | Trials | Matchups | Wins | Win % |
|------------|-----------|--------|----------|------|-------|
| hammerhead | DESTROYER | 109    | 1108     | 795  | 71.8% |
| dominator  | CRUISER   | 96     | 901      | 507  | 56.3% |
| sunder     | DESTROYER | 136    | 1384     | 714  | 51.6% |
| onslaught  | CAPITAL   | 95     | 943      | 300  | 31.8% |
| gryphon    | CRUISER   | 96     | 990      | 58   | 5.9%  |
| eagle      | CRUISER   | 75     | 744      | 5    | 0.7%  |
| wolf       | FRIGATE   | 101    | 1040     | **0**| **0.0%** |
| lasher     | FRIGATE   | 147    | 1500     | **0**| **0.0%** |

## Diagnostic clues inline at abort
- Working hulls all have **ballistic hardpoints** as primary weapon slot.
- Failing hulls are **energy-primary / missile-primary / diverse-slot**.
- Wolf's "best" build (fitness=1.0) still lost 8/10 + 2 timeouts.
- Wolf vs `kite_Support` (unarmed civilian frigate): 89/89 = 100% TIMEOUT.
- Wolf vs `wolf_Starting` (stock beginner wolf): 0/90 = 100% loss.
- Medium energy hardpoint (`WS 004`) often empty in wolf builds.

## Known gap in this run
- `CampaignManager.monitor_loop` ledger-tick is stubbed (H5 in
  `docs/reference/phase6-deferred-audit-findings-2026-04-19.md`). The
  `ledger.jsonl` in `config/ledger-dir-snapshot/` is empty; budget cap
  was decorative. Actual spend ≈ $12 (48 min × 96 VM × $0.15/hr spot).

## Files
```
config/
├── phase7-prep.yaml               the launched YAML
├── git_head.txt                   commit ref at launch
├── git_head_msg.txt               commit message
└── ledger-dir-snapshot/           empty — H5 stub
logs/
└── launch.log.gz                  supervisor + all 8 subprocesses merged
per-study/
├── wolf__early__tpe__seed_idx0/evaluation_log.jsonl        (119 rows)
├── lasher__early__tpe__seed_idx0/evaluation_log.jsonl      (171)
├── hammerhead__early__tpe__seed_idx0/evaluation_log.jsonl  (123)
├── sunder__early__tpe__seed_idx0/evaluation_log.jsonl      (158)
├── eagle__early__tpe__seed_idx0/evaluation_log.jsonl       (94)
├── dominator__early__tpe__seed_idx0/evaluation_log.jsonl   (112)
├── gryphon__early__tpe__seed_idx0/evaluation_log.jsonl     (120)
└── onslaught__early__tpe__seed_idx0/evaluation_log.jsonl   (103)
```

## Analysis notebook
`notebooks/phase7_prep_postmortem.ipynb` loads this directory and produces
per-hull and per-opponent diagnostics. Executed output saved to
`analysis.html` next to this README.

## Findings after analysis (2026-04-19)

### The initial "primary-slot-fill" hypothesis is FALSIFIED
Pearson correlation of primary-(MEDIUM+LARGE)-slot fill% vs win% across the
8 hulls is **−0.32** (slightly *negative*). Concretely:
- `wolf` fills 89.9 % of primary slots → 0.0 % win rate
- `sunder` fills 43.7 % of primary slots → 50.9 % win rate
- `hammerhead` 69.9 % / 71.6 %, `gryphon` 50.8 % / 7.3 %.

The optimizer is putting weapons *into* the right slots; the problem is
elsewhere.

### Vs-stock-same-hull gap is the real signal
Optimizer build vs stock variants of its own hull:
| Hull | Matchups | Wins | Win % |
|------|----------|------|-------|
| hammerhead | 17 | 15 | **88.2** |
| sunder | 129 | 114 | **88.4** |
| gryphon | 123 | 32 | 26.0 |
| dominator | 159 | 13 | 8.2 |
| onslaught | 198 | 7 | 3.5 |
| eagle | 3 | 0 | 0.0 |
| wolf | 216 | 0 | **0.0** |
| lasher | 390 | 0 | **0.0** |

Even `dominator` and `onslaught` (healthy overall win rate) lose to their
own stock variants. The optimizer is beating opponents that happen to be
weak (civilian freighters, non-combat hulls) but systematically loses
head-to-head against well-tuned stock variants of the same hull.

### Root cause: `HULLMOD_EFFECTS` registry is ~13 % complete

Cross-referencing every hullmod selected in the 8 studies against
`src/starsector_optimizer/hullmod_effects.py::HULLMOD_EFFECTS`:

| Hull | Hullmod slots filled | In HULLMOD_EFFECTS | Not in registry | % unregistered |
|------|---------------------:|--------------------:|----------------:|---------------:|
| lasher | 1073 | 92 | 981 | **91.4 %** |
| onslaught | 577 | 59 | 518 | 89.8 % |
| sunder | 1166 | 173 | 993 | 85.2 % |
| gryphon | 818 | 163 | 655 | 80.1 % |
| eagle | 490 | 102 | 388 | 79.2 % |
| hammerhead | 765 | 161 | 604 | 79.0 % |
| dominator | 650 | 177 | 473 | 72.8 % |
| wolf | 624 | 181 | 443 | 71.0 % |

`HULLMOD_EFFECTS` has only **8 entries**:
`hardenedshieldemitter, heavyarmor, magazines, reinforcedhull,
safetyoverrides, shield_shunt, stabilizedshieldemitter, targetingunit`.

Of the ~34 tier-1 hullmods the `early` regime admits per hull, the
heuristic scorer has **no stat-effect knowledge** for ~26 of them.
`compute_effective_stats` reads only `build.hullmods` against
`HULLMOD_EFFECTS` — a hullmod not in the registry provides zero stat
modification to the heuristic prior, even when the Java engine applies
its effect at combat time.

### Two classes of unregistered hullmods, different severity

**Class A — combat-relevant but invisible to the heuristic** (top 15 in usage):
`turretgyros, auxiliarythrusters, hardened_subsystems, armoredweapons,
dedicated_targeting_core, fluxdistributor, fluxcoil, extendedshieldemitter,
advancedshieldemitter, unstable_injector`. These DO affect combat — the
Java engine applies them — but the heuristic scorer can't attribute
their benefit when selecting the top-500 warm-start candidates.

**Class B — genuinely no-op in 1v1 combat** (also frequently installed):
`expanded_cargo_holds` (cargo), `auxiliary_fuel_tanks` (fuel),
`blast_doors` (out-of-combat crew deaths), `recovery_shuttles`
(crew recovery), `surveying_equipment` (survey cost), `converted_hangar`
(useless without fighter LPCs in our search space), `additional_berthing`
(crew capacity), `escort_package` (conditional civilian buff).
Installing these burns OP for zero combat benefit.

### Why frigates fail hardest
Frigates have 40–50 OP. Losing 10–20 OP to class-A-mispriced + class-B
no-op hullmods kills 30–50 % of combat budget — enough to leave
weapons/vents under-funded. Capitals have 400+ OP; the same waste is
<5 %. This matches the observed failure gradient: FRIGATE → CRUISER
(mixed) → DESTROYER (works) → CAPITAL (works). Capital's 31 % win rate
is consistent with ~10 % OP waste being survivable.

### Why warm-start dominates 600-trial budget
`StudyConfig.budget_per_study=600` with the default warm-start ratio of
500 heuristic + ~100 TPE leaves only ~100 trials for TPE to learn
which Class-A hullmods actually matter. Frigates run out of budget
before TPE can correct the heuristic's blind spot.

### Fix options (ordered by leverage)

1. **Expand `HULLMOD_EFFECTS` to cover the ~20 Class-A hullmods**
   (turretgyros, auxiliarythrusters, hardened_subsystems, armoredweapons,
   dedicated_targeting_core, fluxdistributor, fluxcoil, extendedshieldemitter,
   advancedshieldemitter, unstable_injector, …). Encode each hullmod's
   game-truth effect values (% changes, conditional caps). Structural fix;
   benefits every regime and every future phase. Grunt work but bounded.
2. **Add `exclude_hullmods: frozenset[str]` field to `RegimeConfig`** for
   Class-B no-ops (cargo / fuel / survey / crew / convert-hangar). Shrinks
   search space by 6–8 options per hull. Simple, high-yield.
3. **Lower the warm-start fraction** from 500/600 (~83 %) to e.g. 50/500
   (~10 %). Give TPE room to correct the heuristic. Config-only change.
4. **Add a local-sim regression gate**: a 50-trial heuristic-only pass
   for wolf must beat a fixed stock pool at ≥30 % win rate. Catches
   heuristic regressions before cloud launches. This is the missing test.

Recommend all four. (1) is the correct structural fix. (2) is a 1-line
config change. (3) is a parameter flip. (4) is the missing integration
test that would have flagged this 48 h ago.

### Specific low-fill anomalies (open for separate traces)
- `gryphon WS 001` (LARGE missile hardpoint on a missile cruiser) filled
  only **5.8 %**. Either heuristic penalises large missiles by OP or
  repair forces empty. Either way, a missile cruiser with an empty large
  missile mount is nonsensical.
- `sunder WS 003` filled **7.0 %**. Needs slot-meta cross-check against
  `sunder.ship` — should not be built-in (built-ins are excluded at
  `search_space.py:100`). If legitimately in search space and the
  heuristic is choosing empty, that's a signal issue.
- Why `dominator` / `onslaught` beat many size-matched opponents overall
  but lose 8 %/3.5 % to own-hull stock variants. Likely the same
  hullmod-scoring gap; capitals tolerate the waste but can't beat
  hand-tuned stock head-to-head.
- Side-bias sanity check: all symmetric wolf-vs-`wolf_*` matchups
  (216/216) go to ENEMY. Rule out spawn-distance / orientation
  asymmetry in `MissionDefinition.java` before finalising the
  hullmod-registry hypothesis.
