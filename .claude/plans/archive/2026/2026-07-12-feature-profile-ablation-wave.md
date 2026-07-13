---
plan_type: implementation
status: implemented
created: 2026-07-12
approved: 2026-07-12
implemented: 2026-07-13
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/specs/22-cloud-deployment.md
  - docs/roadmap.md
  - docs/reports/2026-07-11-phase7-methodology-review.md
  - docs/reports/2026-07-12-phase7-attempt3-surrogate-results.md
  - docs/reports/2026-07-12-phase7-tail-walltime.md
  - docs/reference/phase7-featurized-matchup-surrogate.md
implementation_commit: 96d33cc
post_impl_audit: passed
superseded_by: null
---

# Feature-Profile Ablation Wave (Roadmap Item 2)

> **Retirement note (2026-07-13):** sections A–C (profile axis, profile
> fixes, ridge retirement) are implemented, audited, and committed
> (`96d33cc`, `9ab670c`, `1c377be`); the AMI re-bake, quota finding, and
> two-batch restructure are recorded in §D. Sections §D (launch) and §E
> (report) were **not executed**: the user-ratified 2026-07-13 re-groom
> folded the wave into the post-data-wave re-baseline
> ([docs/reports/2026-07-13-roadmap-regroom.md](../../../docs/reports/2026-07-13-roadmap-regroom.md),
> decision D1). The b1/b2 configs stay staged; the four predeclared
> primary contrasts carry over to the re-baseline plan when it is drafted.

## Goal

Run the item-2 feature-profile ablation wave on AWS learned-batch under the
fixed evaluation harness: measure what each feature representation family
(aggregate, geometry, opponent-parity, sparse-component) contributes to
surrogate rank fidelity, relative to the attempt-3 `all`-profile canonical
baseline, across the seven split families. Enabling code changes ship first:
(1) `feature_profiles` becomes a real job-matrix axis in the learned batch,
(2) the tautological `sparse-cross` profile is removed and the `geometry`
profile's interaction-column leak is fixed, (3) the attempt-3-ratified
retirement of `sparse_pairwise_ridge` from the canonical matrix lands.

## Context and source docs

- Roadmap item 2 (`docs/roadmap.md`), rationale in the 2026-07-11 methodology
  review §6 item 2; the wave was explicitly unblocked by attempt-3
  ("Open questions", 2026-07-12 attempt-3 report). **Scope note:** the review
  wording is "on the repeated opponent-family/opponent splits"; this wave
  deliberately widens to the full seven-family matrix because (a) the M2
  sparse-ID reading needs build-like splits, and (b) attempt-3 §2.6 showed
  opponent-side splits alone are underpowered — running only there would
  produce an unreadable wave. The widening is named here, not silent.
- H5 remedy 3 (methodology review §H5): the `opponent-parity` profile tests
  pressure-axis opponent compression, scored with per-opponent metrics.
- M2 residue: the "sparse-ID ablation" leakage diagnostic parked under this
  wave is *delivered by* the wave design — the clean predeclared contrast is
  `aggregate` vs `sparse-component`, which differ **only** in sparse
  component keys (both drop interaction fields). `aggregate`-vs-`all` is NOT
  the sparse-ID ablation (it confounds sparse IDs with interactions).
  Nearest-neighbor overlap and rare-combination overlap remain
  roadmap-parked (see Out of scope).
- Attempt-3 decision 3 ratified retiring `sparse_pairwise_ridge` from the
  canonical matrix (the roadmap-grooming step updates the matrix-count
  references that mention it; there is no pre-existing parked roadmap item
  to delete).
- Adversarial-AUC evidence (2026-07-12) qualifies interpretation: build-like
  splits measure interpolation; component-vocab/forward-time measure genuine
  shift; opponent-hierarchy AUCs are unstable at current group counts.

### Predeclared primary contrasts (fixed before any result is seen)

One primary contrast per question; everything else in the readout grid is
exploratory context. Metric: the promotion metric
(`mean_per_opponent_spearman`); model: `catboost_regressor` (the ratified
default family); seed spread reported as spread, never SE.

1. **Sparse component IDs** — `aggregate` vs `sparse-component`, build split.
2. **Interaction fields** — `sparse-component` vs `all`, build split.
3. **Geometry/arc placement** — `aggregate` vs `geometry`, build split.
4. **Opponent-parity compression (H5 remedy 3)** — `opponent-parity` vs
   `aggregate`, opponent-family split — read with the underpowered caveat
   (attempt-3 §2.6; adversarial-AUC instability at these group counts).

## Scope

### A. Profile fixes in `matchup_features.py` + spec 31

**A1 — `sparse-cross` removal.** The filter branch
(`matchup_features.py:645-651`) ends in `_is_sparse_component_key(key) or
not _is_sparse_component_key(key)` — a tautology, so `sparse-cross` returns
the row byte-identical to `all`. This is faithful to spec 31's own
definition ("sparse-component features plus explicit interaction fields")
because `sparse-component` already keeps every non-interaction feature — the
spec definition is itself the tautology. No artifact has ever run the
profile (every canonical run pinned `all`). Fix at root: remove
`sparse-cross` from `FEATURE_PROFILES`, delete the branch, rewrite the test
(currently asserts three inclusions and no exclusion — it cannot
distinguish sparse-cross from `all`) to assert the profile is rejected, and
amend spec 31 with a removal note: the interaction-field ablation is the
`sparse-component` (no interactions) vs `all` (with interactions) contrast.

**A2 — `geometry` interaction leak (found in plan review).** Spec 31 defines
`geometry` as "aggregate features plus geometry, slot placement, and arc
pressure fields", and `aggregate` excludes interaction columns — but the
implementation predicate (`matchup_features.py:625-630`,
`not _is_sparse_component_key(key) or <geometry parts>`) keeps every
`interaction_*` key (not sparse ⇒ kept). Fix to match spec:
`(not _is_sparse_component_key(key) and not key.startswith("interaction_"))
or any(part in key for part in PROFILE_GEOMETRY_INCLUDE_PARTS)`; add a test
asserting `interaction_range_delta` is excluded from `geometry`. Without
this fix the geometry-vs-aggregate contrast confounds slot placement with
cross-side interactions. No artifact has ever run `geometry`, so no compat
concern.

Both fixes: update the profile enumeration in
`docs/reference/phase7-featurized-matchup-surrogate.md` (~line 146). Dated
reports mentioning sparse-cross are historical evidence and stay unchanged.
`FEATURE_SCHEMA_VERSION` is NOT bumped: `all`-profile vectors (the only
profile with existing artifacts) are unchanged; profile membership changes
only affect profiles with zero artifacts, and every artifact stamps its
profile.

### B. `sparse_pairwise_ridge` retirement from the canonical matrix

- New `SUPPORTED_MODELS` = current three families becomes the validation
  universe (`validate_batch_config` line 549 and the "unknown model(s)"
  check); `CANONICAL_MODELS` → `("random_forest_tuned",
  "catboost_regressor")` and remains the config default + canonical-publish
  requirement. Without this split, shrinking `CANONICAL_MODELS` would make
  ridge unconfigurable entirely (line 549 validates against it), which
  contradicts the retirement's intent (canonical default only; family stays
  runnable in non-canonical configs and in the experiment script).
- Canonical matrix count: 180 → **120** jobs (5 seeded splits × 2 × 10 +
  component-vocab 2 × 9 + forward-time 2 × 1). Spec 31 matrix-count
  sentences (~lines 889-890, 1163-1177) updated.
- `DISPATCH_MODEL_RANK` keeps the ridge entry (measured, harmless).
- Stale comment fix (boy-scout): `examples/phase7-learned-batch.yaml:17`
  says "183 jobs" — update to the new count.

### C. `feature_profiles` as a job-matrix axis (`phase7_learned_batch.py`)

- `LearnedBatchConfig.feature_profile: str` → `feature_profiles:
  tuple[str, ...]` (default `(DEFAULT_FEATURE_PROFILE,)`). Config loader
  reads yaml key `feature_profiles` (list). Verified: the loader silently
  ignores unknown yaml keys, so two loader changes land together
  (principled fix for the whole typo class, not just this key):
  `load_batch_config` gains a known-keys allowlist and **rejects any
  unrecognized yaml key** with a ValueError naming it — which subsumes
  rejection of the retired scalar `feature_profile` key (a stale config
  must fail loudly, not silently run `all`).
- `LearnedBatchJob` and `JobLease` gain `feature_profile: str`. The `/lease`
  response serializes `JobLease.__dict__`, so the field propagates to
  workers without handler changes.
- `generate_jobs`: outer loop over `config.feature_profiles`. Job ID scheme:
  `{split}__{model}__s{seed}__p{profile}` with the `__p{profile}` segment
  present only when `profile != DEFAULT_FEATURE_PROFILE` (mirrors the
  SEEDLESS_SPLITS conditional-ID idiom; keeps every canonical job ID and the
  spec 31 job-ID contract stable). Seedless variant:
  `{split}__{model}__p{profile}`.
- `validate_batch_config`: `feature_profiles` non-empty, each in
  `FEATURE_PROFILES`, no duplicates; `publish_canonical` additionally
  requires `feature_profiles == (DEFAULT_FEATURE_PROFILE,)` (both the
  config-time check ~line 661 and the merge-time re-check ~line 2165).
- `build_job_command` (line 761) and the worker user-data template
  (line 1185): `--feature-profile` comes from the job/lease
  (`FEATURE_PROFILE=$(...json ["feature_profile"])`), not the config scalar.
- Job-identity enforcement (spec amendment states this explicitly): the
  `batch_job` object and the experiment-script interface are **unchanged**
  (`--feature-profile` is already a per-invocation flag; the script stamps
  the profile in the artifact top level, `provenance`, `feature_families`,
  and `feature_selection_protocol`). Profile identity is enforced in two
  places: `validate_job_payload` compares all four stamped sites against
  `job.feature_profile` (lines 1810, 1831, 1924, 1969 become per-job
  expectations), and `BatchState.record_result`'s cheap identity check
  (~line 316-320, currently split/model) additionally compares the result's
  `feature_selection_protocol.feature_profile` against the leased job —
  mirroring the established split/model pattern for config-less app
  instances.
- `_common_key` (line 2016): drop `feature_profile` from the batch-common
  provenance key — per-job now, validated per-job (mirrors the split-seed
  precedent noted at lines 1998-1999).
- Merged artifact: top-level `feature_profile` → `feature_profiles`
  (list from config). Profile-dependent top-level singletons copied from
  `first` become per-profile maps keyed by profile
  (`feature_selection_protocol` at minimum; audit the other `first`-copied
  fields — `claim_boundary`, `model_family_policy`, `deployment_policy`,
  `hierarchy_scorecard`, `leakage_diagnostics` — and map any that vary by
  profile, keep profile-independent ones as-is; assert the classification
  with tests).
- `_seed_aggregates`: grouping key gains the profile —
  `f"{split}:{model}:{profile}"` (profile read from the result's
  `feature_selection_protocol.feature_profile`), uniformly (also for
  single-profile batches; self-describing).
- `_validate_adversarial_auc_coherence`: cell key becomes
  `(split, split_seed, feature_profile)` — matches the function's own
  docstring ("pure function of (partition, feature profile, split seed)").
  `_validate_realized_split_digests` keeps its `(split, seed)` key:
  partitions are row-level and profile-independent, so multi-profile
  results within a cell must agree on the digest — a free cross-profile
  determinism check. Both asserted with tests.
- LPT dispatch: add `DISPATCH_PROFILE_RANK` as a third stable-sort key,
  ranked by expected feature-set width (wider = slower = earlier):
  `all: 0, sparse-component: 1, geometry: 2, aggregate: 3,
  opponent-parity: 4`. Documented as a **designed heuristic, not measured**
  (the tail-walltime evidence has no profile axis); unknown profiles rank
  first (pessimistic, mirrors `_DISPATCH_UNKNOWN_RANK`).
- Preflight split-feasibility: `check_split_feasibility`
  (`scripts/cloud/phase7_learned_batch.py:142-151`) **already dedupes** by
  `(split, split_seed)`, so the profile axis does not multiply local
  preflight work — verify with a test, no new logic.
- CLI driver `scripts/cloud/phase7_learned_batch.py` consumes the renamed
  field (dry-run summary line 232 `cfg.feature_profile`) — update to the
  plural field.
- `EXPERIMENT_SCHEMA_VERSION` 4 → **5** (merged-artifact contract changes:
  `feature_profiles` list, per-profile top-level maps, seed-aggregate
  keying, job-ID scheme). Per-result shape is unchanged. No v4 canonical
  wave artifact exists (the v4 artifact is the local adversarial-AUC sweep
  JSON, already published as dated report evidence). Spec 31 mention sites
  to update: seed-policy constants list, the exposed-constant code-block
  comment, the JSON-output list, the merge invariant ("same
  `experiment_schema_version`"), and the canonical-path note ("the next
  canonical wave, on schema v4, …" → v5).

### D. Ablation wave config + launch (AWS)

- New `examples/phase7-learned-batch-ablation.yaml`:
  `feature_profiles: [aggregate, geometry, opponent-parity,
  sparse-component]`, models = canonical (2 post-retirement), canonical
  seed bank, `publish_canonical: false`, `claim_label: exploratory`,
  `honest_eval_usage: exploratory_selection`,
  `output_dir: data/phase7/learned_surrogate_ablation_2026-07`, all other
  hpo/eval knobs identical to the canonical yaml (comparability with the
  attempt-3 baseline requires identical hpo_trials/hpo_seed/inner-CV/
  bootstrap settings — asserted by the report driver against the baseline
  artifact's provenance).
- Matrix: 4 profiles × 2 models × (5 splits × 10 seeds + component-vocab
  9 + forward-time 1) = **480 jobs**.
- The `all` arm is NOT re-run: the attempt-3 canonical artifact
  (`data/phase7/learned_surrogate_full_v2_2026-07.json`) is the baseline.
  **Baseline comparability verification (redesigned in plan review — the
  baseline is `experiment_schema_version: 2` and carries NO
  `realized_split_sha256`, no `split_seed_exclusions`, and 183 results
  including the component-vocab seed-149 cell):**
  1. The report driver recomputes every (split, seed) partition locally
     from the frozen wave-1 DB with HEAD code, digests it, and requires
     equality with the **ablation** artifacts' stamped
     `realized_split_sha256` — a hard determinism check on the wave side.
  2. Baseline linkage is established by code archaeology, not stored
     digests: `git diff` of the split-construction path
     (`phase7_matchup_data.py` split functions and their callees) between
     the baseline artifact's recorded `code_version` and HEAD must show no
     behavioral change; the wave-1 DB is a frozen artifact with no writer
     since attempt-3 (recorded in the report with file mtime/sha256).
  3. Dependency drift: `git diff` of `uv.lock` between the baseline
     `code_version` and HEAD must show no change to the model-training
     stack (sklearn, catboost, numpy, scipy); any change is disqualifying
     for delta claims and forces a re-run of the `all` arm (decision
     escalated to the user with cost).
  4. Baseline aggregates are **recomputed by the driver over the retained
     seed panel** (dropping the baseline's component-vocab seed-149 cell,
     which duplicates seed 107's partition — attempt-3 §2.4); the baseline
     artifact's own `seed_aggregates` double-count that partition and are
     not used. Per-cell deltas skip seed 149 likewise.
  5. Version framing: the comparison is v2 baseline vs v5 wave; rank-metric
     and HPO definitions are unchanged across v2→v5 (v3 = digest
     bookkeeping, v4 = adversarial-AUC diagnostic, v5 = profile-axis
     bookkeeping) — stated in the report, verified by the same code diff
     as step 2.
- Budget and capacity (refined 2026-07-13 from the tail-walltime ledger):
  **walltime, not cost, is the binding constraint**. Measured attempt-3
  rates: RF 2.36 h/job, CatBoost 0.80 h/job at full feature width,
  effective $0.274/worker-hour → ~190 wh per full-width profile.
  Width-scaled conservative estimate ~512 wh (~$140); implausible
  all-full-width worst case ~759 wh (~$208). Config: `target_workers: 64`,
  `max_lifetime_hours: 12` (768 wh capacity ≥ worst case; scale-down-on-
  drain makes lifetime a deadline, not a cost driver), `budget_usd: 300`
  hard cap.
- **Quota finding (2026-07-13)**: L-34B43A08 (Standard-family spot,
  us-east-1) is 640 vCPU — 64 workers × 16 = 1024 does NOT fit
  (attempt-3's 36 workers fit under 640, which is why this never surfaced
  before). **User decision 2026-07-13: two sequential 40-worker batches**
  — `examples/phase7-learned-batch-ablation-b1.yaml`
  (sparse-component + geometry, 240 jobs, ~323 wh ≈ 8.1 h, budget $160)
  then `-b2.yaml` (aggregate + opponent-parity, 240 jobs, ~190 wh ≈ 4.8 h,
  budget $140); 40 × 12 h = 480 wh capacity per batch covers each batch's
  worst case (~380 wh). Both configs preflight-passed against the fresh
  AMI. **Spend approval: held ("Not yet", 2026-07-13)** — launch waits for
  explicit user go-ahead; everything else is staged. Pre-launch gates
  completed: AMI re-baked (ami-0dfbd09e1d9420a3a / us-east-2 copy),
  superseded AMIs deregistered (user-approved), stale-resource sweep
  clean, post-impl audit passed, split-feasibility preflight passed.
- Launch gates (all mandatory, in order): owed **AMI re-bake**; post-bake
  `audit_amis.sh` + `cleanup_amis.sh`; post-impl audit passed; stale-AWS
  -resource sweep; **spot-quota check** (L-34B43A08 Standard-family vCPU ≥
  64 × 16 = 1024); preflight dry-run (verifies 480-job matrix + local
  split feasibility); **fresh per-launch user spend approval**. First live
  exercise of scale-down-on-drain at scale — monitor the drain curve and
  record it in the report. Post-run: `scripts/cloud/final_audit.sh` (spec
  22 completion path) before the wave is declared closed.

### E. Evidence report + grooming

- Dated report `docs/reports/2026-07-1X-phase7-feature-profile-ablations.md`
  (empirical-report skill; honest-evaluation gate before publishing):
  the four predeclared primary contrasts first, then exploratory grid with
  seed spread; opponent-side reads caveated as underpowered; the M2
  sparse-ID-ablation reading from contrast 1; baseline-comparability
  verification results (§D steps 1–5); **forward-time partition third-use
  caveat and the C4 note that the bank-seed outer-test panel is on its
  second wave of reuse** (spec 31 reuse-lineage requirement); drain-curve
  observation; cost ledger actuals.
- Roadmap grooming: item 2 closed; Deferred→M2 bullet updated (sparse-ID
  ablation delivered); matrix-count references touched by the retirement
  updated; report indexed.

## Out of scope

- Nearest-neighbor overlap and rare-combination overlap diagnostics — stay
  roadmap-parked (Deferred → M2). They qualify build-split claims that the
  adversarial-AUC evidence has already qualified; implementing them is not
  needed to read this wave. This is a pre-existing roadmap parking, not a
  new deferral.
- FM/bilinear family (item 3), pairwise-ranking CatBoost (item 4) — next
  items, unchanged.
- Removing `sparse_pairwise_ridge` from the experiment script itself (the
  family remains in `SUPPORTED_MODELS` for non-canonical configs).
- Opponent-panel data wave (item 7).
- Learned feature-selection artifacts / feature-family registry work (spec
  31 forward-looking section) — profiles are fixed subsets, no selection.

## Critical files

- `src/starsector_optimizer/matchup_features.py` — FEATURE_PROFILES,
  filter_feature_profile (sparse-cross removal, geometry fix).
- `src/starsector_optimizer/phase7_matchup_data.py` —
  EXPERIMENT_SCHEMA_VERSION.
- `src/starsector_optimizer/phase7_learned_batch.py` — config + loader
  allowlist, SUPPORTED_MODELS/CANONICAL_MODELS, job/lease dataclasses,
  generate_jobs, validate_batch_config, build_job_command, user-data
  template, validate_job_payload, record_result identity check,
  _common_key, merge, seed aggregates, coherence validators, dispatch
  ranks.
- `scripts/cloud/phase7_learned_batch.py` — dry-run summary field rename;
  feasibility-dedupe verification.
- `scripts/analysis/phase7_learned_surrogate_experiment.py` — no interface
  change (verify `--feature-profile` choices pick up the shrunk enum).
- `examples/phase7-learned-batch.yaml`, `examples/phase7-learned-batch-
  smoke.yaml`, new `examples/phase7-learned-batch-ablation.yaml`.
- `tests/test_matchup_features.py`, `tests/test_phase7_learned_batch.py`,
  `tests/test_phase7_matchup_data.py`.
- `docs/specs/31-phase7-matchup-data.md`; reference doc profile enumeration;
  `docs/roadmap.md`.

## Public concepts and canonical owners

- Feature profile enum — `matchup_features.FEATURE_PROFILES`, specced in
  spec 31 §feature profiles.
- Supported vs canonical model families — `SUPPORTED_MODELS` (validation
  universe) vs `CANONICAL_MODELS` (default matrix + publish guard), both in
  `phase7_learned_batch.py`, specced in spec 31.
- Canonical matrix (splits × models × seeds × profiles) — spec 31
  §"Learned AWS Batch Artifacts".
- Experiment schema version — `phase7_matchup_data.EXPERIMENT_SCHEMA_VERSION`.
- Job-ID scheme — spec 31 (amended with the `__p{profile}` rule).

## Implementation sequence

1. Spec 31 amendment (profiles section incl. sparse-cross removal +
   geometry clarification, SUPPORTED_MODELS/retirement + matrix counts,
   profile axis + job IDs + identity enforcement, merged-artifact contract,
   schema v5 at all five mention sites, dispatch rank).
2. Tests first (TDD) per §A–C.
3. Implementation of A, B, C; module-local pytest after each.
4. Example configs (canonical yaml key migration + count-comment fix,
   smoke yaml, ablation yaml).
5. Full gates: pytest, ruff check + format, mypy, deptry,
   validate_docs.py. Post-impl audit (3 sub-agents + mechanical checks).
   Commit.
6. AMI re-bake + post-bake audit/cleanup + stale-resource sweep + quota
   check + preflight dry-run of the ablation config.
7. Spend approval from user (refined worker-hour + cost arithmetic) →
   `launch --execute` → monitor (drain curve) → merge → post-run
   `final_audit.sh`.
8. Baseline-comparability driver (scratchpad, per evidence-driver
   precedent; §D steps 1–5) → honest-evaluation gate → dated report +
   INDEX → roadmap grooming → plan retirement → commit + push.

## Tests and mechanical gates

- New/updated tests per §A–C (profile-axis matrix counts: 480 for the
  ablation config, 120 for canonical; job-ID conditional segment; canonical
  publish guard incl. profiles; per-job payload profile validation;
  record_result profile identity; multi-profile merge with per-profile
  seed aggregates + coherence keys + profile-independent digest agreement;
  dispatch triple key; user-data lease-derived profile; loader unknown-key
  rejection (incl. the retired scalar key); FEATURE_PROFILES excludes
  sparse-cross and filter rejects it; geometry excludes interaction keys;
  SUPPORTED_MODELS accepts ridge while canonical guard rejects it; schema
  version == 5).
- `uv run pytest tests/ -v`; `uv run ruff check . && uv run ruff format
  --check . && uv run mypy && uv run deptry .`;
  `uv run python scripts/validate_docs.py`.
- Design invariants: no magic numbers (budget/worker counts in yaml config;
  dispatch ranks as module constants with provenance comments).

## Review findings and dispositions

Three fresh-eye auditors (pattern consistency, spec alignment, design
invariants) ran 2026-07-12 on the initial draft; the coordinating session
independently verified the loader and CANONICAL_MODELS facts. All valid
findings are folded into the sections above. Summary:

1. **Baseline digest verification impossible as drafted** (all 3 auditors,
   blocking): attempt-3 artifact is schema v2 with no
   `realized_split_sha256`. → §D verification redesigned (local recompute +
   code archaeology + dependency pinning + aggregate recomputation).
2. **CANONICAL_MODELS doubles as validation universe** (pattern auditor +
   session, blocking): retirement would make ridge unconfigurable. →
   `SUPPORTED_MODELS` split in §B.
3. **`geometry` profile leaks interaction columns vs spec** (spec auditor,
   blocking): confounds the geometry contrast. → §A2 fix + test.
4. **48×8h lifetime capacity likely insufficient** (spec auditor,
   blocking): walltime is the binding constraint. → 64×10h designed caps +
   worker-hour arithmetic at approval; quota gate added.
5. Missing critical file `scripts/cloud/phase7_learned_batch.py` (pattern
   auditor). → added.
6. Loader silently ignores unknown keys; general typo hazard (both,
   + design auditor's principled-fix push). → known-keys allowlist
   rejection, unconditional.
7. Preflight dedupe already exists (pattern + spec auditors). → reworded to
   verification-only.
8. Sparse-ID ablation contrast confounded (design auditor). → predeclared
   `aggregate` vs `sparse-component` contrast; primary-contrast list added
   (also addresses the multiplicity concern).
9. Dependency/code drift confound in baseline reuse (design auditor). →
   §D step 3.
10. Baseline schema v2 not v3; "DB/exclusions unchanged" literally false;
    seed-149 baseline cell (all 3). → §D steps 4–5 corrected text.
11. Roadmap grooming targeted a non-existent parked item; "§6.2" citation
    (pattern + spec auditors). → Context/§E corrected.
12. `batch_job` identity under-specified (spec auditor). → §C identity
    enforcement paragraph; record_result check added.
13. Forward-time third use + C4 panel-reuse caveat; post-run
    `final_audit.sh` (spec auditor). → §D/§E.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12
- Findings: Phases 1–4 self-review passed after revision (spec-first
  sequence, numeric literals in config/constants, no new
  TODO/skip/suppression, no weakened tests — the sparse-cross test is
  replaced with a rejection assertion justified by the spec change).
- Dispositions: see "Review findings and dispositions".
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12
- Agents:
  - Pattern Consistency: findings (9 items; 3 valid-concern) — all
    dispositioned
  - Spec Alignment: findings (10 items; 3 valid-concern) — all
    dispositioned
  - Engineering & Design Invariants: findings (9 items; 4 valid-concern) —
    all dispositioned
- Findings: consolidated in "Review findings and dispositions".
- Dispositions: every valid finding folded into §A–E; invalid/verified-
  accurate findings recorded by the auditors confirm the code-level claims.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- 3 fresh-eye audit sub-agents (post-impl-audit skill) on the implementation
  diff before the AWS launch (pre-launch gate).
- Mechanical invariant checks (design-invariants skill greps).
- Honest-evaluation skill gate before the report publishes any findings.

### Code-phase audit record (2026-07-13; sections A–C + configs)

Three auditors (plan-vs-code, design invariants, spec alignment): **all
pass**, no valid-concern findings. Minor findings and dispositions:

1. Geometry predicate implemented stricter than the plan's literal formula —
   the plan's own expression would have resurrected
   `interaction_pd_arc_vs_missile` (contains `_arc_`); implementation
   excludes interactions unconditionally, spec amended to match. Correct
   deviation, acknowledged here (plan auditor).
2. Preflight-dedupe multi-profile coverage gap → test parametrized over a
   3-profile config (fixed).
3. Profile-independent merged fields not asserted non-map → assertions added
   to the multi-profile merge test (fixed).
4. `record_result` insufficiency carve-out was unforced leniency (both spec
   and design auditors; insufficiency rows do stamp the protocol) → check
   made uniform (fixed).
5. Scalar-string `feature_profiles:` yaml misparse (per-character explosion)
   → `_string_list` shape guard for feature_profiles/splits/models + test
   (fixed).
6. `_KNOWN_CONFIG_KEYS` hand-sync drift risk → lockstep test comparing the
   allowlist against the loader's actually-read keys (fixed).
7. Dead pre-existing constant `PROFILE_AGGREGATE_EXCLUDE_PARTS` → removed,
   boy-scout (fixed).
8. Spec `aggregate` wording "per-slot sparse component columns" imprecise →
   "sparse ID/component columns" (fixed).
9. Geometry errata trail vs old reports → **invalid**: the 2026-05-14 /
   2026-05-16 mentions are forward-looking next-steps; no geometry-profile
   results were ever produced under the leaky definition. The wave report
   will state the normative definition.
10. `BatchState.status()` rows omit profile → declined: convention already
    omits split_seed; job IDs embed non-default profiles.

Gates after fixes: full suite 1080 passed + 1 pre-existing skip; ruff
check/format, mypy, deptry, validate_docs all clean.

## Retirement checklist

- [ ] status: implemented; implemented date; implementation_commit;
      post_impl_audit recorded.
- [ ] Wave merged artifact + report published and indexed.
- [ ] Roadmap groomed (item 2 closed, M2 bullet updated, matrix-count
      references updated).
- [ ] Plan moved to `.claude/plans/archive/2026/`.
