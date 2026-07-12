---
plan_type: implementation
status: active
created: 2026-07-12
approved: 2026-07-12
implemented: null
owner: agent
related_docs:
  - docs/reports/2026-07-12-quality-tooling-research.md
  - docs/roadmap.md
  - CLAUDE.md
  - .claude/skills/design-invariants.md
  - .claude/skills/post-impl-audit.md
implementation_commit: null
post_impl_audit: null
superseded_by: null
---

# Quality Tooling Tightening (B905 · mypy-on-scripts · import checking · format)

## Goal

Close the bug-prevention deferrals from the 2026-07-12 quality-tooling
adoption so the roadmap carries no groomable deferred-tooling items.
User directive (2026-07-12): "Proceed with the B905. Also, let's perform
any tightening to avoid bugs in future. Do perform reformat if it will
improve the signal in future. Ensure we don't need to roadmap grooming
for deferred items."

## Context and source docs

Parent plan (archived): 2026-07-12-quality-tooling-adoption. Evidence:
docs/reports/2026-07-12-quality-tooling-research.md. All gates from the
parent plan are live (hook + pytest + Gradle) and must stay green after
every workstream here.

## Scope

### W-1: B905 `zip(strict=)` pass

1. Remove `"B905"` from the ruff ignore list.
2. Fix all **18** sites (review-verified count: 1 in
   `src/starsector_optimizer/optimizer.py:1128`, 3 in tests, 14 in
   `scripts/`) with a per-site judgment: `strict=True` where the
   iterables must be equal-length (misalignment = bug), `strict=False` +
   nearby rationale only where truncation is genuinely intended.
   `strict=True` is a behavior change (raises instead of silently
   truncating) — that is the point; full suite must stay green, and any
   test failure is investigated as a potential live truncation bug, not
   switched to `strict=False` for convenience.
   - The optimizer site is `strict=True` by review verdict (equal
     lengths by construction; a mismatch would silently corrupt anchor
     discrimination — fail-fast intended, even mid-paid-run).
   - **Scripts-side verification (review finding T-F3):** pytest never
     executes the 14 `scripts/` sites, so the suite is not a gate there.
     For each, record the per-site judgment + reasoning in this plan's
     findings section, and smoke-execute the touched analysis functions
     where local inputs exist; known pre-judged examples:
     `wave1_comprehensive_analysis.py:332` `zip(bodies, palette)` pairs
     N violins against the 10-color cycle → truncation intended,
     `strict=False`; `:197` `zip(axes, CELLS)` pairs a hardcoded
     subplot count against CELLS → `strict=True`.

### W-2: mypy on scripts/

3. **Precondition (review finding T-F1 — W-2 is infeasible without
   this):** `tests/test_wave1_honest_eval_partial.py:6` imports the
   analysis script as a namespace package
   (`from scripts.analysis.wave1_honest_eval_partial import …`), which
   collides with mypy's file-based module naming for `scripts/` and
   hard-aborts the combined run ("Source file found twice under
   different module names"). Normalize that one test to the repo's
   established `importlib.util.spec_from_file_location` pattern (used by
   the module's other consumers and test_validate_docs) — pattern
   consistency over mypy config contortions (`explicit_package_bases`).
4. Then `[tool.mypy] files` gains `"scripts"`. Fix all findings (118 at
   review time, analysis-code dominated) with the parent plan's W-C
   rules: annotations and narrowing, no assertion weakening, `cast` with
   reason only for deliberate-invalid cases, targeted
   `# type: ignore[code]  # reason` only where matplotlib stubs are
   genuinely wrong; zero blanket ignores.

### W-3: import-checking tightening

5. Remove the global `ignore_missing_imports = true`. Review-verified
   actual missing-stub imports: `yaml`, `scipy.stats`/`scipy.optimize`,
   `boto3`, `botocore.exceptions`, `requests`, `sklearn.*` (optuna,
   fakeredis, moto all ship `py.typed` — the plan's original candidate
   list was wrong; review finding T-F2). Mechanism per the repo's
   pandas-stubs precedent: **install stub packages where first-class
   ones exist** (`types-PyYAML`, `types-requests`, `boto3-stubs` — dev
   group), and add per-module `[[tool.mypy.overrides]]`
   (`ignore_missing_imports = true`) ONLY for scipy/sklearn (no usable
   stubs). Fix the stale pyproject comment ("per-module overrides for
   optuna/sklearn/scipy") in the same change. Result: a typo'd internal
   import becomes a mypy error instead of being silently ignored.

### W-4: ruff DTZ + ISC enablement (bug-class tightening)

6. Review pre-verified both clean today: `--select DTZ` → 0 findings,
   `--select ISC --ignore ISC001` → 0 findings (T-F6). Enable both in
   the select list (ISC001 in ignore with the formatter-conflict
   comment) — free forward protection for the naive-datetime and
   implicit-concat bug classes; the original false-positive contingency
   branch is dead.

### W-5: ruff format one-shot + enforcement

7. `uv run ruff format` across the repo (92/96 files at review time; one
   dedicated commit containing ONLY the reformat — no logic changes
   mixed in). The format touches `game_manifest.py`, which trips the
   manifest hook (review finding T-F5): commit with
   `MANIFEST_REVIEWED="format-only, no semantic change"` + the message
   audit line. Add the commit hash to a new `.git-blame-ignore-revs`;
   document `git config blame.ignoreRevsFile .git-blame-ignore-revs` in
   the file header.
8. Enforce: add `uv run ruff format --check .` to `.githooks/pre-commit`
   and to the design-invariants/post-impl-audit mechanical lists.
   Enforcement boundary is the same as every other gate (opt-in hook +
   skill procedure; deliberate CI rejection stands — T-F4 verified
   consistency).
9. Post-format E501 decision: pre-format count is 251 and the formatter
   does not wrap long strings/comments, so the large-residue branch is
   expected — in that case leave E501 off and record the post-format
   count + decision in the plan findings. Enable only if the residue
   comes out small (≈ dozens).
10. Formatter/linter interaction: keep `line-length = 100`; `ruff
    check` after `ruff format` must be clean (no rule conflicts).

### W-6: roadmap + docs closure

11. docs/roadmap.md: delete the quality-tooling staged-follow-ups bullet
    entirely — delivered items die with it (B905, mypy-on-scripts,
    import tightening, format decision).
12. **Report amendment (review finding T-F2/HIGH — without this, ty and
    vulture become ownerless and the report's pointer goes stale):**
    amend docs/reports/2026-07-12-quality-tooling-research.md in the
    same change: (a) Synthesis §3 — delivered items get a dated
    "delivered 2026-07-12" note and the "(tracked in docs/roadmap.md)"
    pointer is removed; (b) Open questions gains the two surviving
    watches it lacks (ty at 1.0; vulture as an after-large-refactor
    audit) alongside the JDK-21/xdist/CI entries it already has;
    (c) Appendix "Dependent docs" drops the "docs/roadmap.md (staged
    follow-ups)" line.
13. **Vulture operative home (T-F2):** it is recurring work, not a
    premise watch — add one line to the post-impl-audit skill's
    mechanical block: after large refactors/deletions, run
    `uvx vulture src/ --min-confidence 80` and judge findings. That is
    the procedure that naturally follows the refactors vulture exists
    to check.
14. CLAUDE.md quality-gates line + skills mechanical lists updated for
    `ruff format --check`. Doc-grooming pass on touched skill/doc
    frontmatter.
15. AMI note: more `src/` changes — the already-owed re-bake before the
    next cloud launch covers this; no new obligation.

## Public concepts and canonical owners

- **Format gate** (`ruff format --check .`): command owned by CLAUDE.md
  Commands; enforcement by `.githooks/pre-commit`; procedure by the
  design-invariants + post-impl-audit mechanical lists (same four
  surfaces as the parent plan's gates — no fifth surface exists).
- **`.git-blame-ignore-revs`**: owned by the repo root; its header
  documents the one-time `git config blame.ignoreRevsFile` setup; only
  dedicated format-only commits are ever added to it.
- **Deferred-tooling watches**: ownership moves roadmap → research
  report Open questions (W-6 item 12). The roadmap remains canonical for
  *actionable* open work; premise-conditional watches are report-owned.
- **Vulture periodic audit**: owned by the post-impl-audit skill
  (mechanical block) as of W-6 item 13.
- **Import-stub policy**: stub packages in the dev dependency group
  (pandas-stubs precedent); per-module ignore-overrides reserved for
  packages with no usable stubs (scipy, sklearn).

## Out of scope

- `disallow_untyped_defs` ratchet (parent-plan rejection stands: ~60%
  bureaucracy; revisit only if annotation drift recurs despite
  check_untyped_defs).
- ty / pytest-xdist / JDK-21 toolchain — premise-conditional watches
  owned by the research report; not action items.
- Any Java-side changes (Java gates complete; nothing tightens further
  without the toolchain move).

## Critical files

`pyproject.toml`; ~20 zip sites across `src/` + `scripts/` + `tests/`
(enumerate via `ruff check --select B905`); `scripts/**/*.py` (mypy);
`.githooks/pre-commit`; `.git-blame-ignore-revs` (new); repo-wide
`*.py` (format); `docs/roadmap.md`; CLAUDE.md; the two mechanical-list
skills.

## Step-by-step implementation sequence

1. W-1 B905 (fix-to-green, full suite after).
2. W-4 DTZ/ISC evaluation (before format so any fixes get formatted).
3. W-2 + W-3 mypy scripts + import tightening (fix-to-green).
4. Commit "tightening" cluster (logic changes).
5. W-5 format one-shot commit (format only) + blame-ignore + hook/skill
   enforcement + E501 decision commit.
6. W-6 roadmap/docs closure.
7. Full verification: pytest randomized, ruff check, ruff format
   --check, mypy, deptry, validate_docs, validate_active_plans; gradle
   untouched (verify no Java files formatted — ruff is Python-only).
8. Post-impl audit (mechanical + sub-agent lanes), retire plan.

## Tests and mechanical gates

- Full suite green after W-1 specifically (strict=True is a runtime
  behavior change).
- `ruff format --check .` exit 0 becomes a standing gate.
- `uv run mypy` covers src+tests+scripts, exit 0.
- grep gate: `grep -n "ignore_missing_imports = true" pyproject.toml`
  shows only per-module override blocks, not the global.
- Roadmap grep: no "Quality-tooling staged follow-ups" bullet remains.

## Review findings and dispositions

Three fresh-eye lanes (Pattern Consistency, Docs/Spec Alignment,
Engineering & Design Invariants). Consolidated (T-labels):

- **T-F1 (MEDIUM, pattern):** W-2 infeasible as written — namespace-vs-
  file module-name collision aborts combined mypy → resolved: normalize
  the one deviant test import to the importlib pattern (W-2 item 3).
- **T-F2 (HIGH, docs + invariants + pattern):** W-6 ownership claim
  false — ty/vulture live only in Synthesis §3 which delegates to the
  roadmap bullet W-6 deletes → resolved: report amendment (item 12),
  vulture operative home in post-impl-audit skill (item 13).
- **T-F3 (MEDIUM, invariants):** 14 of 18 B905 sites live in scripts
  pytest never runs → resolved: per-site recorded judgments +
  smoke-execution where inputs exist (W-1 item 2).
- **T-F4 (LOW, invariants):** format "enforcement" boundary = opt-in
  hook, consistent with all existing gates → recorded in W-5 item 8.
- **T-F5 (LOW, invariants):** format-only commit trips the manifest
  hook on game_manifest.py → override noted in W-5 item 7.
- **T-F6 (INFO):** counts corrected (18 B905 sites, 118 mypy-scripts
  findings, 92/96 format files, 251 pre-format E501); DTZ + ISC
  pre-verified clean → enable-free (W-4); E501 large-residue branch
  expected (W-5 item 9).
- **T-F7 (MEDIUM, pattern):** W-3 candidate list wrong; stub-package
  mechanism (pandas-stubs precedent) for yaml/requests/boto3, overrides
  only for scipy/sklearn → rewritten (W-3 item 5).
- **T-F8 (LOW, both doc lanes):** missing "Public concepts and
  canonical owners" section → added; `related_docs` corrected (archived
  plan dropped, touched skills/CLAUDE.md added).

### Implementation findings

- **B905 site-by-site record (T-F3 obligation):** 18 sites → 17
  `strict=True`, 1 `strict=False`
  (`wave1_comprehensive_analysis.py:332` `zip(bodies, palette)` — the
  10-color prop cycle is intentionally longer than the violin count;
  inline comment added). All four src/tests sites are equal-length by
  construction (optimizer burn-in pairing; deterministic-generation
  comparisons in tests where truncation would weaken the assertion).
  The 13 other scripts sites: five are the `plt.subplots(1, 5)` vs
  `CELLS` hardcoding (latent drift risk → loud failure now), the rest
  are lockstep-append pairs; one (`validate_optimizer.py:127`
  `zip(trials, builds, variant_ids)`) is the canonical
  tell-fitness-to-wrong-trial hazard. Verification: 13/14 scripts sites
  smoke-executed against the real wave-1 logs (all 15 studies) with
  strict active — zero mismatches; `validate_optimizer.py` is
  Linux-only live-sim (py_compile + construction argument only). No
  pre-existing truncation bug found.
- **Shadowing hazard fixed en route:**
  `scripts/cloud/phase7_learned_batch.py` display string `cleanup`
  shadowed by `def cleanup()` (runtime-correct, mypy no-redef) →
  renamed `cleanup_command`.
- **mypy-on-scripts real bugs (7):** three scripts dead against the
  current API (`test_instance_manager.py` used a removed MatchupConfig
  field; `test_optimizer_integration.py` + `estimate_throughput.py`
  missing the post-manifest-refactor `manifest` argument);
  `validate_optimizer.py` stored a bare float where `BuildCache` expects
  `_CachedTrialResult`; a lying 2-tuple annotation over 3-tuple data; a
  wrong `dict[str, float]` return contract (now a TypedDict); the
  learned-experiment `fit/predict` signatures under-declared their row
  union. Tallies: ~55 typed changes, 1 justified cast (matplotlib
  violinplot stub genuinely wrong), 0 new type-ignores, 1 stale ignore
  removed. One remaining override added: `catboost.*` (no stubs exist).
- **E501 decision (W-5 item 9):** post-format residue was 52 — the
  small branch, so E501 is ENABLED: 44 wrapped (rendered/asserted text
  byte-identical), 8 findings suppressed via 2 `# noqa: E501`
  directives on the closing quotes of the two bash userdata template
  strings, each with a reason comment (wrapping would change rendered
  script bytes that tests pin).
- **Format one-shot:** commit `0e42359` (93/96 files), listed in
  `.git-blame-ignore-revs`; `blame.ignoreRevsFile` configured in this
  clone.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 16:55
- Findings: Phases 1–4 self-review during drafting; sub-agent lanes
  surfaced everything material beyond it (notably T-F1's infeasibility
  and T-F7's wrong candidate list — both would have burned
  implementation time).
- Dispositions: all resolved by plan edits; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 16:55
- Agents:
  - Pattern Consistency: findings (6) — all resolved
  - Spec Alignment: findings (5, incl. the HIGH) — all resolved
  - Engineering & Design Invariants: findings (6) — all resolved
- Findings: see consolidated list above.
- Dispositions: all resolved by plan edits; no open items.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

Parent-plan battery (pytest randomized / ruff check / format --check /
mypy / deptry / validate_docs) + the grep gates above + sub-agent lanes
per post-impl-audit skill.

## Retirement checklist

- [ ] All scope items DONE or DEFERRED (with user approval).
- [ ] Frontmatter lifecycle fields set; archive to `.claude/plans/archive/2026/`.
- [ ] Roadmap carries zero deferred-tooling items (the user directive).
- [ ] AMI re-bake note still visible for the next launch.
