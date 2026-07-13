---
plan_type: implementation
status: implemented
created: 2026-07-12
approved: 2026-07-12
implemented: 2026-07-12
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-07-12-phase7-attempt3-surrogate-results.md
  - docs/roadmap.md
implementation_commit: cdf2323 (+ audit closure in the retirement commit)
post_impl_audit: passed
superseded_by: null
---

# Seed-bank split-uniqueness enforcement

## Goal

Guarantee that rotated-seed evidence panels aggregate over *distinct*
realized splits. Attempt 3 (2026-07-12) discovered post-hoc that canonical
bank seeds 107 and 149 produced byte-identical component-vocab splits (both
hold out `weapon:pdlaser`), so the "10-seed" component-vocab panel was
effectively 9 splits with one double-counted — understating seed spread and
double-weighting one draw in seed aggregates. Roadmap AWS action item:
"dedupe-or-reject at split construction"; report decision 4.

## Context and source docs

- Evidence: [attempt-3 results §2.4](../../../docs/reports/2026-07-12-phase7-attempt3-surrogate-results.md)
  (duplicate pair; effective n = 9).
- Owning spec: [spec 31](../../../docs/specs/31-phase7-matchup-data.md)
  §"Seed policy", §"Inner validation", §"Learned AWS Batch Artifacts".
- Fresh empirical scan (2026-07-12, this plan's scoping, wave-1 DB at
  `data/phase7/wave1_matchups.sqlite`, holdout 0.2 / overshoot 0.35 /
  hpo_seed 23): **only** component-vocab collides, **only** 107≡149; the
  other five seeded families give 10/10 distinct realized splits; all inner
  component-vocab folds are 3/3 distinct for every bank seed. (Numbers live
  here as plan-scoping evidence; durable magnitudes stay in the attempt-3
  report.)

## Why splits can collide

`held_out_component_vocabulary_split` accumulates shuffled vocabulary items
until the realized test fraction crosses `holdout_fraction`. The vocabulary
is coarse, so many draws stop after one item; two seeds whose shuffles put
the same high-coverage component first realize the identical partition. The
same mechanism could, in principle, collide any grouped split when the
group count is small (opponent-family) — hence a generic check, not a
component-vocab special case.

## Design

Three enforcement layers plus one policy resolution, all deterministic:

1. **Core primitive** — `split_partition_sha256(split: SplitIds) -> str`
   in `phase7_matchup_data.py`: full 64-hex SHA-256. Canonicalization
   (pinned in spec 31, since the merge coherence invariant compares digests
   produced by independent workers): each row → compact
   (`separators=(",", ":")`) `json.dumps(dataclasses.asdict(row),
   sort_keys=True)`; sort each partition's row-JSON strings
   lexicographically; digest = SHA-256 of the compact JSON of
   `{"train": [...], "test": [...]}`. Row-order-invariant; distinct
   partitions ⇒ distinct digests. Works for both `TrainingMatchupRow` and
   `HonestEvalMatchupRow`. The digest covers full row content (including
   `source_path`/`target`), so it identifies a partition *of a specific DB
   materialization* and is not comparable across DB regens — the spec
   amendment says so explicitly (within one batch, `_common_key` pins
   `db_path`/`bundle_sha256`). Naming uses the codebase's `_sha256` digest
   convention (`bundle_sha256`, `feature_family_registry_sha256`);
   "fingerprint" is reserved for canonical JSON strings
   (`component_fingerprint_json`).

2. **Launch preflight (reject)** — `split_feasibility_report` (experiment
   script) additionally digests every *feasible* seeded cell and, per split
   family, reports any seed whose realized partition duplicates an earlier
   (config-order) seed's, with status `duplicate_realized_split` and a
   `detail` field naming the partner seed. Report entries gain the optional
   `detail` key; `check_split_feasibility`
   (`scripts/cloud/phase7_learned_batch.py`) renders it in the refusal
   message. `check_split_feasibility` already refuses to provision on any
   nonempty report. The status constants live beside
   `INSUFFICIENCY_STATUSES` in `phase7_matchup_data.py` but are **not**
   members: workers cannot observe cross-seed duplicates, so they are
   preflight-level statuses only, never worker artifact statuses.
   `SEEDLESS_SPLITS` are exempt (one instance by design).

3. **Worker-side inner guard (reject)** — the component-vocab branch of
   `inner_cv_splits` rejects a drawn fold whose realized partition
   duplicates an earlier fold's (return `()` → the existing
   `insufficient_inner_groups` structured insufficiency; a duplicated fold
   means fewer distinct folds than declared). The branch's `_progress`
   message distinguishes the duplicate-draw case from a degenerate draw,
   and spec 31's inner-validation section documents the status overload.
   `grouped_kfold` (disjoint round-robin) and rolling-origin (strictly
   growing prefixes) cannot produce duplicate folds, so no guard is needed
   there. Scan evidence: no current canonical cell trips this, so no
   comparability break.

4. **Artifact stamp + merge guard (defense in depth)** — results stamp
   `realized_split_sha256` inside `outer_split_lineage` (the C4 reuse
   ledger — signature gains an optional split argument). The digest is
   stamped whenever the outer split was constructed: completed results
   **and** `insufficient_inner_groups` artifacts (whose outer partition
   exists — this lets an operator see colliding outer draws even in cells
   that failed inner validation); `degenerate_component_vocab_split`,
   `empty_outer_split`, and missing-optional-model artifacts stamp `null`.
   `construct_splits` returns the constructed
   `(split, build_lookup, split_extras, ())` alongside the
   `insufficient_inner_groups` status so the stamp is possible.
   `_contract_ok` requires the 64-hex field on completed results;
   `EXPERIMENT_SCHEMA_VERSION` bumps 2 → 3 (precedented; version-equality
   checks already isolate older artifacts). `merge_job_artifacts` then
   enforces two invariants over accepted results:
   - same `(split, split_seed)` across models ⇒ digests **equal**
     (cross-worker split-construction determinism/coherence);
   - same split, different `split_seed` ⇒ digests **differ** (uniqueness).
   Corrected rationale (review finding): this layer is a within-batch
   coherence check and a defense against hand-assembled `results/`
   directories. It does **not** cover manual standalone runs published
   directly in reports (e.g. the seed-151 confirmatory, which never passes
   `merge_job_artifacts`); that residual gap is accepted — the stamp makes
   report-time human cross-checks possible, and honest-evaluation report
   discipline owns them. This layer is separable if review judges it
   excessive.

5. **Policy resolution for the known collision** — a generic designed
   constant `SPLIT_SEED_EXCLUSIONS: dict[str, frozenset[int]] =
   {"component-vocab": frozenset({149})}` in `phase7_matchup_data.py`
   (provenance: attempt-3 §2.4). Enforcement follows the two-layer
   burned-seed precedent rather than a single silent filter:
   - `generate_jobs` filters excluded seeds per family; the canonical
     matrix becomes 5 × 3 × 10 + 3 × 9 + 1 × 3 = 180 jobs.
   - `validate_batch_config` rejects (with an error naming the exclusion
     and its provenance) any configured split whose effective seed list is
     empty after exclusions — a subset/smoke config listing only excluded
     seeds must fail with the real reason, not the downstream
     "target_workers must be between 1 and the job count".
   - `_validate_claim_config` (experiment script) rejects excluded
     `(split, split_seed)` pairs via a new
     `reject_excluded_split_seed(split, seed)` beside
     `reject_burned_split_seed`, so a standalone
     `--split component-vocab --split-seed 149` run cannot produce an
     artifact stamped with the canonical bank label. The rejection
     deliberately lives at claim-validation level, **not** inside
     `_split_rows`/`construct_splits`: the stale-exclusion probe (below)
     must still be able to construct excluded cells, and the baseline
     script's diagnostic path stamps no bank label.
   - Merged artifacts stay self-describing: `merge_job_artifacts` stamps
     the applied exclusions (`split_seed_exclusions`, restricted to the
     configured splits) in merged provenance, and spec 31 amends the
     `split_seeds` stamp semantics to "configured seed list; per-family
     effective lists are the configured list minus the stamped
     exclusions".
   - **Stale-exclusion self-check**: the preflight duplicate detection
     also constructs each *excluded* `(family, seed)` cell (probe config
     via `dataclasses.replace` on that family's first probe) and verifies
     its realized partition duplicates a retained seed's; if not, it
     reports status `stale_split_seed_exclusion` and the launch refuses —
     an exclusion that no longer corresponds to a real collision (e.g.
     after a DB regen) must not silently drop a seed's evidence.

   Exclusion is chosen over substituting a fresh seed (e.g. 149 → 157)
   because: (a) any replacement chosen after observing the collision on
   the same frozen DB is mildly adaptive; (b) attempt-3's effective
   component-vocab panel was already these exact 9 distinct splits, so
   future waves stay directly comparable; (c) no new-seed bank governance
   is needed. If a future DB regen collides another family, the preflight
   failure forces the same explicit constant-plus-spec amendment before
   launch — the spec documents this procedure. `validate_batch_config`'s
   `publish_canonical` check (configured seed list == bank) is unchanged:
   exclusions are applied downstream by `generate_jobs`.

## Scope

- `src/starsector_optimizer/phase7_matchup_data.py`:
  `split_partition_sha256`; `reject_excluded_split_seed`;
  `SPLIT_SEED_EXCLUSIONS`; preflight status constants
  (`duplicate_realized_split`, `stale_split_seed_exclusion`);
  `EXPERIMENT_SCHEMA_VERSION = 3`.
- `scripts/analysis/phase7_learned_surrogate_experiment.py`: preflight
  duplicate + stale-exclusion detection in `split_feasibility_report`;
  `construct_splits` returning the outer split with
  `insufficient_inner_groups`; inner-fold duplicate guard with
  distinguishing `_progress` message; `outer_split_lineage` digest stamp;
  `reject_excluded_split_seed` in `_validate_claim_config`.
- `src/starsector_optimizer/phase7_learned_batch.py`: `generate_jobs` seed
  exclusion; `validate_batch_config` effective-empty-seed-list rejection;
  `_contract_ok` digest requirement; merge coherence + uniqueness
  invariants; merged-provenance `split_seed_exclusions` stamp.
- `scripts/cloud/phase7_learned_batch.py`: `check_split_feasibility`
  renders the report entries' `detail` field.
- `docs/specs/31-phase7-matchup-data.md`: seed-policy uniqueness +
  exclusion contract (incl. two-layer enforcement and the stale-exclusion
  self-check), pinned digest canonicalization + cross-DB caveat,
  inner-validation distinct-draw requirement + status-overload note,
  preflight amendment, matrix count 183 → 180 in **both** places it
  appears (§"Learned Baseline Experiment" and §"Learned AWS Batch
  Artifacts"; the "21 (split, model) cells" figure stays correct),
  artifact/merge contract fields (`realized_split_sha256`,
  `split_seed_exclusions`, `split_seeds` semantics), schema version 3, and
  a note that the next canonical wave needs a fresh dated output path (the
  "schema-v2 wave uses `learned_surrogate_full_v2_2026-07.json`" example
  is v2-specific).
- Tests in `tests/test_phase7_matchup_data.py`,
  `tests/test_phase7_learned_surrogate_experiment.py`,
  `tests/test_phase7_learned_batch.py`. Note the canonical job count in
  tests is a formula (`CANONICAL_JOB_COUNT` in
  `tests/test_phase7_learned_batch.py`), not a literal — it gains
  exclusion awareness rather than a hand-edited 180.

## Out of scope

- Resolving the honest-eval path (no rotated seed bank there).
- Adversarial-validation AUC and other M2 diagnostics (next backlog item).
- Any AWS launch; this is local code + spec work.
- Retroactive re-merge of attempt-3 artifacts (published, dated evidence;
  schema v3 intentionally does not accept them).
- Retiring `sparse_pairwise_ridge` from the canonical matrix (attempt-3
  report decision 3) — a separate amendment that will re-touch the same
  matrix-count sentence; noted here so the two changes don't collide, not
  bundled to keep this change reviewable.
- Automated cross-checking of manual standalone-run artifacts against bank
  panels (accepted residual gap; see Design item 4's corrected rationale).

## Critical files

| File | Change |
|---|---|
| `src/starsector_optimizer/phase7_matchup_data.py` | digest fn, rejection fn, 3 constants, schema bump |
| `scripts/analysis/phase7_learned_surrogate_experiment.py` | preflight dupe + stale-exclusion checks, construct_splits contract, inner guard, lineage stamp, claim-validation rejection |
| `src/starsector_optimizer/phase7_learned_batch.py` | seed exclusion, empty-list validation, contract field, merge invariants, exclusion stamp |
| `scripts/cloud/phase7_learned_batch.py` | preflight failure message renders `detail` |
| `docs/specs/31-phase7-matchup-data.md` | contract amendments (see Scope) |
| `tests/test_phase7_*.py` (3 files) | new tests + schema/formula updates |

## Public concepts and canonical owners

- `split_partition_sha256`, `reject_excluded_split_seed`,
  `SPLIT_SEED_EXCLUSIONS`, preflight status constants,
  `EXPERIMENT_SCHEMA_VERSION` — owned by `phase7_matchup_data.py`,
  contract in spec 31 (existing single-owner pattern for shared
  experiment-contract constants).
- `realized_split_sha256` and `split_seed_exclusions` artifact fields —
  spec 31 §"Learned AWS Batch Artifacts" + `outer_split_lineage` /
  merged provenance.

## Implementation sequence

1. Amend spec 31 (uniqueness + exclusion contract, canonicalization,
   schema v3, matrix count ×2, merge invariants, artifact fields, output
   path note).
2. Failing tests: digest properties (determinism, permutation invariance,
   partition sensitivity, 64-hex); preflight duplicate detection with
   `detail`; stale-exclusion detection; inner-fold guard + message;
   lineage stamp incl. `insufficient_inner_groups`; excluded-pair
   rejection in claim validation; generate_jobs exclusion + formula;
   effective-empty-seed-list config rejection; merge coherence +
   uniqueness raises; merged exclusion stamp; schema v3 pins.
3. Implement `phase7_matchup_data.py` additions.
4. Implement experiment-script changes.
5. Implement learned-batch + launch-CLI changes.
6. Update existing tests that pin schema 2 / the job-count formula /
   artifact fixtures.
7. Full gates: pytest, ruff check/format, mypy, deptry, validate_docs.
8. Real-data sanity run: preflight-style scan on the wave-1 DB confirms
   component-vocab passes with the exclusion applied (stale-exclusion
   probe confirms 149 still collides with 107) and all six families report
   distinct panels.

## Tests and mechanical gates

- `uv run pytest tests/ -v` (suite currently 1024 passed + 1 skipped).
- `uv run ruff check . && uv run ruff format --check . && uv run mypy &&
  uv run deptry .`
- `uv run python scripts/validate_docs.py`

## Review findings and dispositions

Twelve consolidated findings from the three fresh-eye auditors; all
accepted and folded into the Design/Scope sections above.

1. (A, C) Exclusion enforced only in `generate_jobs`; standalone runs of
   excluded cells would stamp the canonical bank label → **fixed**:
   `reject_excluded_split_seed` in `_validate_claim_config`, two-layer
   burned-seed pattern (Design 5).
2. (A, B) Config listing only excluded seeds fails with a misleading
   downstream error → **fixed**: `validate_batch_config` effective-empty
   rejection naming the exclusion (Design 5).
3. (A, B, C) Merged artifact misstates the seed panel; exclusion not
   stamped → **fixed**: `split_seed_exclusions` in merged provenance +
   `split_seeds` semantics amendment (Design 5).
4. (A) Partner-seed naming requires the launch-CLI formatter → **fixed**:
   `detail` field + `scripts/cloud/phase7_learned_batch.py` in scope
   (Design 2).
5. (A) `..._fingerprint` naming collides with the canonical-JSON-string
   convention; digests use `_sha256` → **fixed**: renamed
   `split_partition_sha256` / `realized_split_sha256` (Design 1).
6. (A, B, C) Digest canonicalization underspecified (dict rows are
   unorderable; digest binds full row content) → **fixed**: pinned
   canonicalization + cross-DB caveat in Design 1 and the spec amendment.
7. (A) Test job count is a formula, not a literal; ridge-retirement
   decision will re-touch the matrix sentence → **fixed**: formula gains
   exclusion awareness (Scope); ridge retirement listed as explicit
   out-of-scope interaction.
8. (B, C) Merge-guard rationale wrongly cited the seed-151 pattern (which
   never passes merge) → **fixed**: corrected rationale (within-batch
   coherence + hand-assembled-results defense); manual-path gap named as
   accepted out-of-scope residual (Design 4).
9. (B) `insufficient_inner_groups` artifacts do have an outer split →
   **fixed**: digest stamped whenever the outer split exists;
   `construct_splits` returns it with that status (Design 4).
10. (B) Spec 31 states 183 twice and pins a v2-specific output path →
    **fixed**: both mentions + fresh-dated-path note in Scope.
11. (C) Stale exclusion would silently drop a seed's evidence after a DB
    regen → **fixed**: preflight stale-exclusion self-check with
    `stale_split_seed_exclusion` status (Design 5).
12. (C) Duplicate-fold reporting overloads `insufficient_inner_groups`
    losing diagnosis detail → **fixed**: distinguishing `_progress`
    message + spec note of the overload (Design 3).

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 (self-review Phases 1–4 + consolidated fresh-eye
  findings folded in; see Review findings and dispositions)
- Findings: Phases 1–4 self-review clean (spec-first sequencing; no new
  magic numbers — designed policy constants follow the
  `SEEDLESS_SPLITS`/`DISPATCH_MODEL_RANK` precedent; no deferrals outside
  the explicit Out-of-scope list; error-path tests included).
- Dispositions: see Review findings and dispositions.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12
- Agents:
  - Pattern Consistency: findings (7; consolidated items 1, 2, 3, 4, 5, 6, 7)
  - Spec Alignment: findings (6; consolidated items 2, 3, 6, 8, 9, 10)
  - Engineering & Design Invariants: findings (6; consolidated items 1, 3,
    6, 8, 11, 12)
- Findings: 12 consolidated (see Review findings and dispositions).
- Dispositions: all accepted and folded into the plan before approval.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

- 3 fresh-eye audit sub-agents + mechanical checklist per
  `.claude/skills/post-impl-audit.md`.

## Post-implementation audit results (2026-07-12)

Mechanical checks: full suite 1042 passed + 1 skipped; ruff check/format,
mypy, deptry, validate_docs all green; real-data step-8 sanity run passed
(180 jobs, component-vocab on 9 seeds, 60 cells + 1 exclusion probe, and
an auditor's counterfactual full-bank scan reproduced
`duplicate_realized_split` for 149 vs 107).

Three audit sub-agents (plan-vs-code, invariants, spec alignment): all
passed the implementation; all 12 pre-approval dispositions verified.
Findings and dispositions:

1. (Spec) Merge bullet still said "configured splits × models × seeds
   matrix" — **fixed**: now "the exclusion-filtered `generate_jobs`
   matrix".
2. (All three) Seedless-split exemption from the duplicate check lived
   only in the launch CLI's cell dedupe — **fixed**: explicit
   `SEEDLESS_SPLITS` skip inside `split_feasibility_report` + test pin.
3. (All three) Top-level payload `outer_split_lineage` stamps a null
   digest (only per-result lineage carries it) — **dispositioned, no
   change**: the spec scopes the digest requirement to results, merge
   validates per-result only, and the multi-result payload has no single
   partition.
4. (Invariants) Stale-probe detail string can be imprecise when a
   family's retained cells are themselves infeasible — **dispositioned,
   no change**: the launch still refuses with the retained cells' own
   infeasibility entries listed; only cosmetic noise in an
   already-failing report.

## Retirement checklist

- [x] status: implemented; implementation_commit; post_impl_audit
- [x] Move to `.claude/plans/archive/2026/`
- [x] Groom `docs/roadmap.md` (delete the seed-bank action item; absorb
      follow-ups)
