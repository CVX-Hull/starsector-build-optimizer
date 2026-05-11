---
plan_type: implementation
status: implemented
created: 2026-05-11
approved: 2026-05-11
implemented: 2026-05-11
owner: agent
related_docs:
  - docs/specs/31-phase7-matchup-data.md
  - docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md
  - docs/reports/2026-05-11-validation-to-phase7-roadmap.md
  - docs/reports/INDEX.md
implementation_commit: not_committed
post_impl_audit: passed
superseded_by: null
---

# Phase 7 Comparator-Backed Surrogate

## Goal

Turn the Phase 7 smoke baseline into the comparator-gate experiment:
non-atomic matchup features, train-only trivial/statistical comparators,
grouped diagnostics, and required honest-eval top-k recall. This plan does
not run CatBoost, sparse-interaction, or BoTorch work; those remain gated on
the comparator result.

## Context And Source Docs

Current authority:

- Spec 31 owns Phase 7 matchup data and baseline-evaluation contracts.
- The validation-to-Phase-7 roadmap says feature-table validation comes before
  CatBoost, sparse interactions, and BoTorch.
- The Phase 7 preliminary report currently contains only a random-forest smoke
  baseline and explicitly requires trivial comparators next.

## Scope

- Update spec 31 before implementation.
- Extend flat feature extraction with stable slot, weapon, hullmod, opponent,
  and interaction features.
- Add comparator evaluation support to the Phase 7 baseline script.
- Add tests for feature extraction, comparators, metrics, and script behavior.
- Run the comparator-backed experiment on the existing generated SQLite DB.
- Update the dated Phase 7 report with empirical metrics. Reference docs may
  link to the report or update qualitative staging text only; no measured
  internal-sim numbers go into reference docs.

## Out Of Scope

- No AWS runs.
- No BoTorch sampler or custom kernel implementation.
- No CatBoost or sparse-interaction model execution in this plan. Random
  forest remains in the full grid as the carryover smoke baseline.
- No optimizer integration.
- No broad literature sweep unless results expose a specific modeling gap.

## Critical Files

- `docs/specs/31-phase7-matchup-data.md`
- `src/starsector_optimizer/matchup_features.py`
- `src/starsector_optimizer/phase7_matchup_data.py`
- `scripts/analysis/phase7_baseline_surrogate.py`
- `tests/test_matchup_features.py`
- `tests/test_phase7_matchup_data.py`
- `tests/test_phase7_baseline_surrogate.py`
- `docs/reports/2026-05-11-phase7-matchup-surrogate-preliminary.md`
- `docs/reports/2026-05-11-validation-to-phase7-roadmap.md`
- `docs/reports/INDEX.md`

## Public Concepts And Canonical Owners

- Spec 31 owns feature-row, split, comparator, and baseline-evaluation public
  contracts.
- `matchup_features.py` owns generated row features.
- `phase7_baseline_surrogate.py` owns local experiment execution and printed
  JSON summaries.
- Reports own dated empirical metrics; references may point to those reports
  without copying measured values.

## Implementation Sequence

1. Update spec 31 with enriched feature rows, comparator names, learned
   baseline staging, metrics, feature schema version, provenance output, and
   honest-eval top-k recall semantics.
2. Add failing tests for enriched feature keys and comparator/metric behavior.
3. Extend `matchup_features.py`:
   - deterministic key naming by sorted slot ID,
   - per-slot categorical and numeric geometry features from parsed `GameData`,
   - per-slot weapon attributes from parsed `GameData` with `EMPTY` and
     `UNKNOWN` sentinels,
   - hullmod multi-hot plus tag counts from parsed `GameData`,
   - opponent categorical residuals and existing aggregates,
   - cheap interpretable interactions,
   - a `feature_schema_version` value emitted in every row.
4. Extend baseline script:
   - models: `global_mean`, `opponent_mean`, `build_mean`, `twfe_additive`,
     `ridge_hybrid`, and `random_forest`;
   - train-only fit/predict APIs with explicit unseen-group fallback to the
     train global mean and fallback counts in JSON output;
   - metrics: MAE, RMSE, Spearman, required top-k recall against honest-eval
     rankings, and stratified diagnostics by opponent family, score regime,
     and campaign cell where those labels are available;
   - JSON output for one split/model or all grouped splits/models, including
     feature schema version and data provenance.
5. Run focused tests after each changed module, then run the experiment against
   `data/phase7/wave1_matchups.sqlite`.
6. Update reports and reference docs with comparator-backed results.
7. Run full verification and post-implementation audit.

## Tests And Mechanical Gates

- `uv run pytest tests/test_matchup_features.py -v`
- `uv run pytest tests/test_phase7_matchup_data.py tests/test_phase7_baseline_surrogate.py -v`
- `uv run python scripts/analysis/phase7_baseline_surrogate.py --help`
- `uv run pytest tests/ -v`
- Mermaid validation on changed docs.
- Active plan validation.
- Link check on changed docs.
- `git diff --check`
- `python -m compileall` on changed Python modules/scripts.

## Review Findings And Dispositions

- Fresh-eye review found the original plan collapsed CatBoost/sparse work into
  the comparator gate. Disposition: CatBoost and sparse-interaction execution
  are out of scope; this plan implements only comparator-gate models.
- Fresh-eye review found top-k recall was optional. Disposition: top-k recall
  is now required and specified as leakage-safe train-on-training, rank-on
  honest-eval output.
- Fresh-eye review found train-only fallback behavior underspecified.
  Disposition: comparators must expose train-only fit/predict behavior with
  unseen groups falling back to train global mean and reporting fallback counts.
- Fresh-eye review found feature/provenance schema versioning missing.
  Disposition: spec and implementation must emit `feature_schema_version` and
  script provenance in JSON output.
- Fresh-eye review found reference-doc empirical-number risk. Disposition:
  empirical metrics go only in reports; references may link qualitatively.
- Fresh-eye review found grouped diagnostics underspecified. Disposition:
  opponent-family, score-regime, and campaign-cell diagnostics are required
  where labels are available.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11
- Findings:
  - Original plan mixed comparator-gate and learned-baseline phases.
  - Top-k recall and leakage controls were underspecified.
  - Feature schema versioning and provenance were missing.
  - Documentation ownership for empirical values was ambiguous.
- Dispositions:
  - Scope narrowed to comparator gate.
  - Required top-k recall and train-only fallback rules added.
  - Feature schema/provenance requirements added.
  - Empirical metrics restricted to reports.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-05-11
- Agents:
  - Pattern Consistency: passed with findings
  - Spec Alignment: passed with findings
  - Engineering & Design Invariants: passed with findings
- Findings:
  - Pattern Consistency: split CatBoost/sparse work from comparator gate; define
    fallback behavior; add schema versioning; add CLI smoke; add reports index.
  - Spec Alignment: enforce report/reference empirical ownership; make top-k
    recall required; add schema/provenance and grouped diagnostics.
  - Engineering & Design Invariants: add leakage tests, source-of-truth
    statements, config/named constants, error-path tests, and doc guardrails.
- Dispositions:
  - All accepted and incorporated into this plan.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-Implementation Audit Requirements

- Status: passed
- Audit source: `.claude/skills/post-impl-audit.md`
- Reviewed at: 2026-05-11
- Fresh-eye agents:
  - Spec Alignment
  - Plan-vs-Code
  - Engineering Invariants
- Findings:
  - The original exact-matchup repeat split fit on honest-eval targets and
    violated the top-k leakage guardrail.
  - JSON metric keys drifted from the spec contract.
  - Provenance output was thinner than the plan required.
  - Built-in weapons were handled in per-slot features but omitted from the
    small-slot aggregate composition.
  - Attribute type suppressions hid weak row-key handling.
- Dispositions:
  - Removed the replicate split from the comparator-gate script.
  - Restored `n_train`, `n_test`, and nested `top_k_recall` output shape.
  - Added run provenance to top-level and per-result JSON.
  - Fixed small-slot aggregate features to include built-in weapons.
  - Replaced attribute suppressions with explicit build-key handling.
  - Updated tests and reports to enforce the no-honest-target-fitting rule.

## Retirement Checklist

- Set `status: implemented`.
- Set `implemented: 2026-05-11`.
- Set `implementation_commit` after commit, or `not_committed`.
- Set `post_impl_audit: passed`.
- Move to `.claude/plans/archive/2026/`.
