---
plan_type: implementation
status: active
created: 2026-07-12
approved: 2026-07-12
implemented: null
owner: agent
related_docs:
  - CLAUDE.md
  - docs/CONVENTIONS.md
  - .claude/skills/design-invariants.md
  - .claude/skills/post-impl-audit.md
  - combat-harness/CLAUDE.md
implementation_commit: null
post_impl_audit: null
superseded_by: null
---

# Quality Tooling Adoption (Tier 1 + Tier 2)

## Goal

Adopt the static-analysis / lint / hygiene toolchain selected by the
2026-07-12 three-agent empirical research sweep, and fix every real defect
that research surfaced, so that:

- `uv run ruff check .` is green and stays green (pre-commit hook gate).
- `uv run mypy` is green on `src/` + `tests/` and stays green (hook gate).
- `uv run deptry .` is green and stays green (hook gate).
- `combat-harness` compiles warning-free under `-Xlint:all -Werror` with
  Error Prone 2.42.0 + NullAway 0.13.7 enforced as errors.
- All rendered EC2 userdata templates and all repo shell scripts pass
  shellcheck, enforced as pytest tests (auto-covered by post-impl audit).
- The test suite runs under randomized order (`pytest-randomly`).
- `scripts/validate_docs.py` additionally validates internal markdown link
  targets (the one measured docs-coverage gap; 569 cross-links unchecked
  today).

Research provenance (empirical finding counts, tool trials, rejection
rationale) is filed as a dated report by workstream W-J
(`docs/reports/2026-07-12-quality-tooling-research.md`) — plans are not
durable design authority, and the rejection evidence must survive this
plan's archival (review finding C-F3/A-F4). The ranked recommendation was
delivered in-chat and approved by the user ("Proceed with tier 1 and tier 2
per our workflow"); the staged/rejected items in Out of scope below were
enumerated in that approved recommendation. Explicitly rejected tools
(bandit, SpotBugs, PMD, Checkstyle, codespell, markdownlint, shfmt,
pre-commit framework, CI expansion, ty, pyright-as-second-checker) are out
of scope; their rejection rationale is summarized below and owned in detail
by the W-J report.

## Context and source docs

- CLAUDE.md "Engineering principles" + "Design invariants" (no magic
  numbers; principled over expedient; address issues at root cause).
- [design-invariants](../../skills/design-invariants.md) — mechanical
  checklist this plan extends.
- [post-impl-audit](../../skills/post-impl-audit.md) — the full-pytest
  mechanical step is what makes pytest-embedded checks self-enforcing.
- [docs/CONVENTIONS.md](../../../docs/CONVENTIONS.md) — owns the docs
  validation contract that gains the link check.
- [combat-harness/CLAUDE.md](../../../combat-harness/CLAUDE.md) — Gradle
  build contract (`JAVA_HOME="$STARSECTOR_JDK_HOME"`, JDK 17,
  `targetCompatibility=17`).
- `.githooks/pre-commit` — existing hook (symlink check, active-plan
  validation, docs validation, manifest-regen gate) that gains the new
  fast sweeps.
- [docs/reports/2026-07-12-phase7-batch-v2-incidents.md](../../../docs/reports/2026-07-12-phase7-batch-v2-incidents.md)
  — motivates the rendered-userdata shellcheck layer; also documents why
  shellcheck is NOT a guard for the ERR-trap bug class (measured false
  negative on the exact $24 incident pattern — the executable
  bash-semantics tests remain that guard).

## Scope

### W-A: pyproject hygiene (prerequisite, real defects)

1. `requires-python = ">=3.10"` → `">=3.11"`. Three src modules use
   `enum.StrEnum` (3.11+): `models.py`, `phase7_matchup_data.py`,
   `instance_manager.py`. The declared floor is a lie today. `>=3.11` is
   the empirically-derived floor; `.python-version` (3.13) remains the
   pinned interpreter.
2. Delete unused runtime deps `cmaes`, `lifelines`, `optunahub` — zero
   references repo-wide per research grep. **Re-verify at implementation
   with case-insensitive grep over `src/ scripts/ tests/ notebooks/
   experiments/` including lazy-loading spellings (`CmaEs`, `optunahub`,
   `lifelines`) before deleting** — optuna loads `cmaes` lazily only if a
   `CmaEsSampler` is instantiated, so absence of the string is the proof.
3. Move `pandas-stubs` from runtime deps to the dev dependency group
   (it is a type-stub package; runtime placement was a misfile).
4. Remove the stale `[project.optional-dependencies].dev` extra
   (`pytest`, `pytest-cov`) — duplicated by `[dependency-groups].dev`,
   which is the uv-native mechanism actually used.
5. Declare direct imports: `werkzeug` (imported directly in 2 src files,
   currently only transitive via flask) as a runtime dep; `matplotlib`
   (used by `scripts/analysis/`) in the dev group.
6. Add dev-group tools: `ruff`, `mypy`, `deptry`, `shellcheck-py`,
   `pytest-randomly` (version-pinned by uv.lock as usual).

### W-B: ruff adoption + fix-to-green

7. Add `[tool.ruff]` / `[tool.ruff.lint]` to pyproject:
   - `target-version = "py311"`, `line-length = 100`, `src = ["src"]`.
   - `select = ["E4","E7","E9","F","B","UP","C4","PLE","PLW","RUF"]`
     (bug-finder families; measured signal).
   - `ignore = ["RUF001","RUF002","RUF003"]` (deliberate ×/—/§ house
     typography — 304 pure-noise hits), `"B905"` (20 `zip()` sites each
     need a human `strict=` judgment — staged later, see Out of scope).
     E501 is NOT listed: the selected families exclude it, so an ignore
     entry would be dead configuration (review finding B-F8); line-length
     enforcement stays deferred via Out of scope only.
   - per-file-ignores: `tests/**: ["E741"]` (single-letter names in tests,
     churn without value); `scripts/analysis/**: ["E402"]`
     (`matplotlib.use("Agg")` before pyplot import is load-bearing).
8. `uv run ruff check --fix` for the mechanical bulk (F401 unused imports,
   UP deprecated typing imports, C4), then hand-fix the remainder to zero
   findings. Named hand-fixes (real defects from research):
   - `tests/test_campaign.py:1018` **F821**: nested `_describe_ami_tag`
     mock imports only `manifest_sha256`; the `WorkerSourceSha` branch
     would `NameError`. Fix by importing both names (sibling test at
     line ~518 is the correct pattern). Check whether the branch is
     exercised; if not, extend the test so the fixed import is load-bearing
     rather than decorative.
   - `tests/test_cloud_worker_pool.py:749` F811 shadowed re-imports —
     delete the inner imports.
   - `src/starsector_optimizer/estimator.py:200` F841: delete the dead
     `machines` computation (its own comment supersedes it); also drop the
     always-empty `Machines` column from the rendered table rather than
     printing a header for a value never computed.
   - `src/starsector_optimizer/campaign.py:37-43` E402: move the
     `_SECONDS_PER_HOUR` constant below the import block (style-only
     reorder; the named-constant rule is unaffected).
   - `scripts/analysis/phase7_baseline_surrogate.py:128` RUF009: dataclass
     default `EvalMetricsConfig()` → `field(default_factory=...)` (shared
     mutable-singleton hazard; benign today only because frozen).
   - E702/E741/F841 stragglers in `scripts/` — fix, don't ignore.

### W-C: mypy adoption + fix-to-green (src + tests)

9. Add `[tool.mypy]`: `python_version = "3.11"`,
   `files = ["src", "tests"]`, `check_untyped_defs = true`,
   `warn_redundant_casts`, `warn_unused_ignores`, `no_implicit_optional`,
   `ignore_missing_imports = true` (per-module override tightening is a
   later stage). `scripts/` excluded initially by the `files` list
   (115 findings of analysis-code friction; staged later).
10. Fix ~70 findings to zero. Named real defects:
    - `optimizer.py:594-595` annotation drift: `_burn_in_scores` /
      `_burn_in_fitness` annotated as float collections but store
      `(build_idx, raw)` tuples. Fix the annotations to match the code
      (runtime behavior is correct; the annotations lie).
    - `cloud_worker_pool.py:317` `self._server = None` → annotate
      `BaseWSGIServer | None` (kills the downstream cluster).
    - `game_manifest.py` `X | None` narrowing (review finding A-F3
      correction: `from_str` itself lives in `models.py:30`; the
      narrowing sites in `game_manifest.py` are its consumers
      `_parse_weapon`/`_parse_hullmod`/`_parse_slot`/`_parse_hull` at
      ~312/338/357/383): add one explicit narrowing guard/helper
      preserving the "warn, don't crash" forward-compatibility principle
      (CLAUDE.md design principle 5) — the guard must keep
      skip-with-warning semantics, not raise.
    - `game_manifest.py:249-261` loop-variable type reuse — rename.
11. `parser.py:373` `mod_dirs` parameter (vulture 100%-confidence, real
    API-contract defect: documented, accepted, never read; review
    confirmed all 10 call sites are single-argument). **Remove the
    parameter and its docstring line** (dead API surface; implementing
    mod-merge is an unrequested feature). Spec 03
    (`docs/specs/03-game-data-parser.md`) updates in the same change
    (review findings B-F1, B-F2):
    - the `load_game_data` signature at ~:114;
    - **delete step 5** ("If mod_dirs: merge mod data …") — otherwise the
      spec documents behavior with no input that triggers it;
    - reconcile adjacent drift in the same section: delete step 6's
      `validate_registry(game_data)` (the function no longer exists;
      registry validation moved to the manifest in Phase-7-prep, per the
      comment at `parser.py:420-423`) and add the wings parse/populate
      step the spec omits (`parse_wing_csv` → `GameData.wings`,
      `parser.py:395,402`).

### W-D: Java — Xlint/Werror + Error Prone + NullAway + fix-to-green

12. `combat-harness/build.gradle.kts`:
    - `options.compilerArgs += listOf("-Xlint:all", "-Werror")` on all
      `JavaCompile` tasks.
    - Plugin `net.ltgt.errorprone` **5.1.0**;
      `error_prone_core` **2.42.0 — pinned, do not bump**: 2.43.0+ ship
      JDK-21 class files and crash the JDK-17 compiler JVM
      (empirically reproduced). Record the pin rationale as a comment at
      the dependency line. Upgrade path (JDK 21 toolchain +
      `options.release = 17`) is out of scope — it changes the
      compiler-host convention in combat-harness/CLAUDE.md.
    - `nullaway` **0.13.7** + `compileOnly("org.jspecify:jspecify:1.0.0")`.
    - Error Prone config: `disable("EmptyCatch")` (the deliberate
      `catch (Throwable ignored)` wrappers around obfuscated game API are
      a documented project pattern), `error("NullAway")`,
      `NullAway:AnnotatedPackages = starsector.combatharness,data.missions`,
      `NullAway:KnownInitializers` for the plugin lifecycle `init`.
      NullAway on `compileTestJava`: **first characterize all 15 test
      findings** (review finding C-F1 — research counted 14/15 as the
      intentional pass-null-to-exercise-defaults pattern, leaving one
      uncharacterized). Fix the 15th if it is real; record it in this
      plan's findings section either way; only then disable NullAway on
      test compilation with the intentional-null rationale.
    - Game-API jars stay `compileOnly(files(...))` — classpath-only, never
      analyzed; no change needed, but assert this in review.
13. Fix all findings to a clean `-Werror` build:
    - 3 `[unchecked]` raw-`Iterator` warnings (`MatchupConfig.java:170`,
      `ManifestDumper.java:125`, `CombatHarnessPlugin.java:1073`) —
      `Iterator<?>` + cast (org.json keys are Strings; prefer the typed
      fix over `@SuppressWarnings`).
    - `ResultWriter.java:74,83` **real bug**: `float +=` accumulation of
      `getDouble(...)` damage totals feeding the results JSON — switch
      accumulators to `double`, **with a regression test, no escape
      hatch** (review finding C-F2): extend the existing ResultWriter
      test coverage to pin double-width accumulation (values whose sum is
      lossy in `float` but exact in `double`); if no test seam exists,
      extract the accumulation into a testable method and create one.
    - Spec 12 reconciliation in the same change (review finding B-F6):
      the documented `buildMatchupResult(...)` signature has drifted
      (spec says `... JSONArray loadoutDiagnosticPlayer, JSONArray
      debugDumps`; code ends `JSONObject traceContext` at
      `ResultWriter.java:60`) — update the spec signature while editing
      this file.
    - ~7 missing `@org.jspecify.annotations.Nullable` on internal
      contracts (all verified null-checked downstream; annotations make
      the contracts enforced).
    - 2–3 `@SuppressWarnings("NullAway.Init")` on state-machine fields
      assigned outside `init` (`probeHullIter`, `condHullQueueIter`).
    - 4 missing `@Override`; delete dead `MenuNavigator.LAUNCHER_X/Y`
      (stale UI coordinates, never read); delete unused import
      `TitleScreenPlugin.java:6`.
14. Commit note: `combat-harness/src/main/` edits trip the pre-commit
    manifest gate. Expected outcome: these changes do not shift game
    rules (annotations, types, dead-code removal; the float→double fix
    changes result *precision*, not manifest content) → a
    `MANIFEST_REVIEWED:` override is anticipated — but the override text
    is written at commit time **after re-confirming against the actual
    diff**, not copied from this plan (review finding C-F6).
15. Verify: `cd combat-harness && JAVA_HOME="$STARSECTOR_JDK_HOME"
    ./gradlew jar test` green.

### W-E: shellcheck — dep, rendered-template pytest, script sweep

16. New test module `tests/test_shellcheck.py`:
    - `test_rendered_user_data_passes_shellcheck`: render all THREE
      userdata templates — `render_phase7_learned_batch_user_data`
      (src/starsector_optimizer/phase7_learned_batch.py),
      `render_user_data` + `render_probe_user_data`
      (src/starsector_optimizer/cloud_userdata.py) — with every optional
      branch populated (fresh-ledger flag, noise floor, mod-jar override,
      debug pubkey) AND a second all-optionals-empty variant; shellcheck
      each (`--shell=bash`, default severity); assert exit 0.
    - `test_repo_shell_scripts_pass_shellcheck`: sweep `scripts/**/*.sh`
      + `.githooks/pre-commit` (discovered by glob, not a hand-list).
    - Both `skipif` shellcheck absent; with `shellcheck-py` in the dev
      group the skip never fires on dev machines or workers.
    - Framing (from the incident report): this layer catches
      quoting/globbing/syntax defects. It does NOT catch ERR-trap/`wait`
      semantics (measured false negative on the $24 bug) — the executable
      bash-semantics tests in `tests/test_phase7_learned_batch.py` remain
      the guard for that class. Record this in the test module docstring.
17. Fix the SC2046 unquoted `$(date …)` in the probe template
    (`cloud_userdata.py` ~:231, inside `render_probe_user_data`) — TDD:
    the new shellcheck test fails first, then quote. Spec 22's probe
    description is already stale for this template (review finding B-F7:
    spec says minimal `echo probe-boot-ok > …`; the template emits
    `probe-boot-ok campaign_id=<id> <timestamp>`) — update the spec 22
    description to match the actual (post-fix) output in the same change.
18. Fix/annotate the 15 script findings so the sweep is zero-finding:
    - `cleanup_amis.sh:48` dead `CALLER_ACCT`: looks like an unfinished
      account-safety guard in a destructive script. Preferred fix:
      complete the guard (compare caller account to the expected account,
      abort on mismatch) — matches the personal-resource-protection
      posture. If reading the script shows the guard is genuinely
      redundant with an existing check, delete the variable instead; the
      decision and reason go in this plan's review-findings section at
      implementation time.
    - `audit_amis.sh:23` unguarded `cd` (script runs `set -uo pipefail`
      WITHOUT `-e`): `cd … || exit 1`.
    - `teardown.sh:63` + `cleanup_amis.sh:127` unused `attempt` → `_`.
    - `teardown.sh:44` unquoted `$ids` word-splitting: convert to a bash
      array (principled fix per CLAUDE.md; not an inline disable).
    - SC2064 ×6 (`launch_campaign.sh`, `probe.sh`): expand-now trap
      strings are intentional — inline
      `# shellcheck disable=SC2064` + one-line reason at each site.
    - SC2329 ×2 (`evaluate_campaign.sh` trap-invoked functions),
      SC2016 (`bake_image.sh` JMESPath backticks), SC2001
      (`final_audit.sh` multi-line sed): inline disables + reason —
      all three are measured false positives.

### W-F: deptry adoption

19. `[tool.deptry]`: `known_first_party = ["starsector_optimizer"]`;
    per-rule ignores for the `literature` and `surrogate` extras
    (declared-but-unimported by design — they are opt-in extras).
    Green after W-A's dependency cleanup.

### W-G: pytest-randomly

20. Add to dev group. Full suite already verified green under randomized
    order (1003 passed) during research; re-verify post-change. No config
    needed. If a future order-dependence flake appears, that is signal to
    fix the test, not to remove the plugin.

### W-H: validate_docs.py internal-link check (TDD)

21. Extend `scripts/validate_docs.py`: validate internal link targets over
    an **explicitly enumerated file set** (review finding B-F4 — the
    validator's member globs exclude the highest-value link surface):
    all `docs/**/*.md` **including** `INDEX.md`/`README.md` index files,
    `docs/roadmap.md`, `docs/project-overview.md`; `.claude/skills/*.md`;
    root `CLAUDE.md` and `combat-harness/CLAUDE.md`. Extraction rule:
    inline links `[...](target)` outside fenced code blocks **and outside
    inline code spans** (review findings B-F3/A-F2 — the known
    placeholder examples in CONVENTIONS.md §Cross-references are inline
    `` `…` `` spans in prose bullets, not fenced blocks; stripping spans
    is the principled false-positive defense — do NOT restructure
    CONVENTIONS prose into fences). Skip `http(s)://`, `mailto:`,
    pure-fragment `#...`, and targets containing `<`; strip any
    `#fragment` suffix; resolve relative to the containing file; error if
    the target does not exist.
22. Tests first: create `tests/test_validate_docs.py` (none exists —
    verified) against a tmp-dir docs tree covering: broken link detected;
    link inside a fenced code block ignored; link inside an inline code
    span ignored; http/mailto/fragment/`<placeholder>` skipped;
    fragment-suffix stripped; link resolved relative to the containing
    file (not CWD); index files and root CLAUDE.md included in the walk.
23. Update docs/CONVENTIONS.md's validation description to mention link
    checking (one sentence, no empirical numbers).

### W-J: research provenance report

23a. File `docs/reports/2026-07-12-quality-tooling-research.md` (per the
     empirical-report skill): the three-lane tool-trial evidence — per-tool
     finding counts, judged samples, adoption-cost estimates, and the
     rejection rationale for every non-adopted tool (bandit, SpotBugs,
     PMD, Checkstyle, codespell/typos, markdownlint, shfmt, pre-commit
     framework, CI expansion, ty, pyright-as-second) — plus the measured
     shellcheck false-negative on the ERR-trap pattern (cross-linking the
     2026-07-12 incidents report, which owns that incident). Add the
     INDEX.md row. This report is the durable owner of every empirical
     count this plan cites (review findings C-F3/A-F4); future
     revisitations (e.g. ty at 1.0, wave-2 cap redesign) cite it, not the
     archived plan.

### W-I: gate wiring + docs

24. `.githooks/pre-commit`: after the existing validator calls, append
    `uv run ruff check .`, `uv run mypy`, `uv run deptry .` (all
    seconds-fast; mypy incremental). The shellcheck sweeps live in pytest
    (post-impl-audit's full-suite step covers them), not the hook —
    one mechanism per check, no duplication.
25. CLAUDE.md Commands section: add one line for the quality gates
    (`uv run ruff check . && uv run mypy && uv run deptry .`; Java gates
    run inside the Gradle build).
26. design-invariants skill: add the new mechanical commands to its
    checklist. post-impl-audit skill: note that the mechanical step now
    includes the lint/type/dep gates. combat-harness/CLAUDE.md (review
    finding B-F5): one short paragraph owning the harness build gates —
    the Error Prone 2.42.0 pin + JDK-17 rationale, the EmptyCatch
    carve-out, and the NullAway annotated-packages boundary — since that
    file is the always-loaded owner of the harness build contract.
26a. Doc-grooming pass after the skill/docs edits (review finding A-F6):
    refresh `last-validated` frontmatter on every touched skill/doc and
    run the doc-grooming skill's checklist — the workflow-gates table
    triggers it on "changing skills".
27. AMI note: `src/` and combat-harness changes extend the already-owed
    re-bake before the NEXT cloud launch (attempt 3 unaffected). Recorded
    here so plan retirement doesn't lose it.

## Out of scope (with rejection rationale, so the plan is self-contained)

- **B905 `zip(strict=)` pass** — 20 sites each needing a human judgment;
  staged as follow-up. The rule stays in `ignore` until then.
- **ruff format** — would reformat 87/94 files; separate one-shot decision
  with git-blame cost; not bundled.
- **mypy on `scripts/`** and per-module `disallow_untyped_defs` ratchet —
  staged later; `--strict` globally measured as ~60% bureaucracy.
- **mypy `ignore_missing_imports = true` tightening** (explicit DEFERRED,
  review finding C-F4/F5): the global setting weakens import checking;
  replacing it with per-module overrides for optuna/sklearn/scipy is the
  named follow-up, staged with the mypy-on-scripts item.
- **pytest-xdist** — 22s saved on a 55s suite; not worth mandating now.
- **vulture in CI** — periodic manual audit only (noise floor below 80%
  confidence); its two 100%-confidence findings are fixed in this plan.
- **bandit** (79 findings ≈ 0 actionable for a local single-tenant
  orchestration tool), **SpotBugs** (10/10 deliberate-by-design),
  **PMD** (229 findings, 1 unique real), **Checkstyle** (style-only),
  **codespell/typos** (~0/25 true positives on this corpus),
  **markdownlint** (style noise vs. house conventions), **shfmt**
  (churns 23/27 stable scripts), **pre-commit framework** (breaks the
  `MANIFEST_REVIEWED:` commit-message override), **CI expansion**
  (direct-to-master workflow makes PR gates dormant), **ty** (pre-1.0;
  revisit at 1.0), **pyright as second checker** (overlap with mypy).
- **JDK 21 compiler toolchain** (Error Prone >2.42 path) — changes the
  build-host convention; revisit deliberately, not as a side effect.
- **lychee external-link gate** — occasional ad-hoc command only;
  arxiv/doi hosts are stable and rate-limited.

## Critical files

- `pyproject.toml` (deps, ruff/mypy/deptry config)
- `combat-harness/build.gradle.kts`
- `.githooks/pre-commit`
- `scripts/validate_docs.py` + new `tests/test_validate_docs.py`
- new `tests/test_shellcheck.py`
- `src/starsector_optimizer/`: `cloud_userdata.py`, `optimizer.py`,
  `cloud_worker_pool.py`, `game_manifest.py`, `parser.py`, `estimator.py`,
  `campaign.py`, `models.py` (+ mechanical F401/UP touches elsewhere)
- `combat-harness/src/main/java/starsector/combatharness/`:
  `ResultWriter.java`, `MenuNavigator.java`, `TitleScreenPlugin.java`,
  `MatchupConfig.java`, `ManifestDumper.java`, `CombatHarnessPlugin.java`,
  `DamageTracker.java` (+ annotation touches)
- `scripts/cloud/*.sh`, `scripts/*.sh`
- `CLAUDE.md`, `docs/CONVENTIONS.md`,
  `.claude/skills/{design-invariants,post-impl-audit}.md`

## Public concepts and canonical owners

- **Quality gates** (ruff/mypy/deptry/shellcheck/Xlint/ErrorProne):
  commands owned by CLAUDE.md Commands; mechanical checklist owned by the
  design-invariants skill; enforcement mechanics owned by
  `.githooks/pre-commit` (repo sweeps) and `tests/` (context-dependent
  checks). No new spec: tool configuration is not a module contract.
- **Docs link validation**: owned by `scripts/validate_docs.py`,
  described in docs/CONVENTIONS.md.
- **`load_game_data` signature change** (mod_dirs removal): owned by the
  parser spec — check `docs/specs/01–08` for the parser spec and update
  the signature there in the same change.

## Step-by-step implementation sequence

1. W-A pyproject hygiene (incl. the unused-dep re-verification greps).
2. W-B ruff config + `--fix` + hand-fixes → `ruff check .` green;
   full pytest green.
3. W-E shellcheck: failing tests first (probe template SC2046 reproduces),
   then template fix + script fixes → new tests green.
4. W-G pytest-randomly in; full suite green under random order.
5. W-C mypy config + fix-to-green on src+tests (incl. mod_dirs removal +
   parser spec update); full pytest green.
6. W-F deptry config → green.
7. W-D Java: gradle config, fix-to-green, `gradlew jar test` green.
8. W-H validate_docs link check (tests first) → `validate_docs.py` green
   on the real tree.
9. W-J research-provenance report + INDEX row; then W-I gate wiring +
   doc/skill updates + doc-grooming pass (26a).
10. Full verification: `uv run pytest tests/ -v`, `ruff check .`, `mypy`,
    `deptry .`, gradle build+test, `validate_docs.py`,
    `validate_active_plans.py`; then post-impl audit per skill.

Commit strategy: one commit per workstream cluster (A+B+E+G tier-1 Python;
C+F tier-2 Python; D Java; H+J+I docs/gates) so review and revert
boundaries stay clean. Manifest-gate note (review finding A-F1): the hook
gates `src/starsector_optimizer/game_manifest.py` as well as
`combat-harness/src/main/` — so BOTH the C+F commit (game_manifest
narrowing/renames) and the D commit need a `MANIFEST_REVIEWED:` override,
each written after re-confirming the actual diff shifts no game rules
(type-narrowing guards and annotations do not change what the manifest
contains or how it is read).

## Tests and mechanical gates

- New: `tests/test_shellcheck.py` (2 tests + variants),
  `tests/test_validate_docs.py` (link-check behaviors).
- Adjusted: `tests/test_campaign.py` F821 fix (+ possible branch-exercise
  extension), `tests/test_cloud_worker_pool.py` F811, any test updated by
  the mod_dirs removal, Java tests for ResultWriter accumulator width if a
  seam exists.
- Gates that must be green at completion: full pytest (randomized), ruff,
  mypy, deptry, gradle `jar test` with `-Werror`+ErrorProne,
  validate_docs, validate_active_plans, pre-commit hook end-to-end on the
  actual commits.

## Review findings and dispositions

Sixteen findings across the three fresh-eye lanes (labels: A = Pattern
Consistency, B = Spec Alignment, C = Engineering & Design Invariants).
No high-severity findings. All valid findings resolved by plan edits:

- **C-F1** (uncharacterized 15th NullAway test finding) → W-D item 12:
  characterize all 15 before disabling; fix/record the 15th.
- **C-F2** (ResultWriter regression-test escape hatch) → W-D item 13:
  test committed, seam created if absent.
- **C-F3 / A-F4** (research provenance transcript-only) → new W-J dated
  report owns all empirical counts.
- **C-F4** (deferral consent granularity) → Out of scope now names the
  `ignore_missing_imports` tightening explicitly; staged items were
  enumerated in the user-approved in-chat recommendation.
- **C-F5** (suppression inventory) → informational; all carry reasons.
- **C-F6** (pre-scripted MANIFEST_REVIEWED) → W-D item 14: override text
  written at commit time against the actual diff.
- **B-F1/B-F2** (spec 03: mod-merge step, validate_registry drift, wings
  omission) → W-C item 11 enumerates all three spec edits.
- **B-F3 / A-F2** (placeholders are inline code spans, not fences) →
  W-H item 21: extractor strips fences AND spans; "fence it" fallback
  removed.
- **B-F4** (link-check file set excluded indices/roadmap/overview) →
  W-H item 21: explicit enumeration including index files and both
  CLAUDE.md files.
- **B-F5** (combat-harness/CLAUDE.md silent on new build gates) → W-I
  item 26: gate paragraph added there.
- **B-F6** (spec 12 signature drift at the edited site) → W-D item 13:
  reconcile in same change.
- **B-F7** (spec 22 probe description stale) → W-E item 17: update with
  the quoting fix.
- **B-F8** (E501 dead config) → W-B item 7: removed from ignore list.
- **A-F1** (manifest gate also fires on game_manifest.py) → commit
  strategy: override for the C+F commit too.
- **A-F3** (`from_str` lives in models.py, not game_manifest.py) → W-C
  item 10 corrected with the actual `_parse_*` narrowing sites.
- **A-F5** (probe SC2046 at ~:231 not :229) → W-E item 17 corrected.
- **A-F6** (doc-grooming not invoked after skill edits) → W-I item 26a.

### Implementation-discovered findings

- **I-F1 (real hook bug, fixed in scope):** the `MANIFEST_REVIEWED:`
  commit-message override in `.githooks/pre-commit` could never fire — a
  pre-commit hook receives no message argument (`$1` is never set; that is
  commit-msg territory) and its `git log -1` fallback reads the *previous*
  commit. Verified no past commit ever exercised the override on a gated
  path (the one commit carrying the line touched only example YAMLs).
  Fixed root-cause: enforcement moved to the `MANIFEST_REVIEWED` env var
  (`MANIFEST_REVIEWED="<reason>" git commit …`); the message line remains
  the durable audit record; hook header + error text updated.
- **I-F2 (disposition recorded per W-E item 18):** `cleanup_amis.sh`
  `CALLER_ACCT` deleted rather than completed — the account guard it
  presumably drafted already exists as `--owners self` on the
  describe-images lookup plus the Project-tag refusal; a second
  account check would be redundant.
- **I-F3 (scope note):** deleting dead `n_active` in `optimizer.py`
  exposed that its feeder `opponents = get_opponents(...)` was also
  unused at that scope (pure lookup, re-done by both `preflight_check`
  and the objective); both removed.

## Plan Review Gate

- Status: passed
- Review source: `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 14:09
- Findings: Phases 1–4 self-review ran during drafting; the sub-agent
  lanes below surfaced everything material beyond it (self-review missed
  B-F3/A-F2, B-F4, A-F1 in particular).
- Dispositions: see "Review findings and dispositions" — every valid
  finding resolved by plan edit; none deferred.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Fresh-Eye Review Gate

- Status: passed
- Review source: sub-agents via `.claude/skills/plan-review.md`
- Reviewed at: 2026-07-12 14:09
- Agents:
  - Pattern Consistency: findings (6: A-F1..A-F6) — all resolved
  - Spec Alignment: findings (8: B-F1..B-F8) — all resolved
  - Engineering & Design Invariants: findings (7: C-F1..C-F7; C-F7 was a
    positive verification, C-F5 informational) — all resolved
- Findings: see "Review findings and dispositions".
- Dispositions: all resolved by plan edits above; no open items.
- Approval rule: frontmatter `status: approved` is invalid unless this gate is `passed`.

## Post-implementation audit requirements

Per post-impl-audit skill: full suite + mechanical greps + 3 independent
sub-agents. Additional mechanical checks specific to this plan:

- `ruff check .`, `mypy`, `deptry .` exit 0.
- `gradlew jar test` exits 0 (proving `-Werror` + ErrorProne gates hold).
- `grep -rn "cmaes\|lifelines\|optunahub" pyproject.toml` → empty.
- `grep -n "mod_dirs" -r src/ tests/ scripts/ docs/specs/` → empty
  (or the parameter retained with a caller, per the W-C disposition).
- New shellcheck tests are NOT skipped in the local run (shellcheck-py
  resolvable).

## Retirement checklist

- [ ] All scope items classified DONE or DEFERRED (with user approval).
- [ ] `status: implemented`, `implemented:`, `implementation_commit` set.
- [ ] `post_impl_audit` recorded.
- [ ] Move to `.claude/plans/archive/2026/`.
- [ ] Groom docs/roadmap.md (absorb the staged follow-ups: B905 pass,
      mypy-on-scripts, format decision, vulture cadence, ty-at-1.0).
- [ ] AMI re-bake reminder still visible wherever the next-launch checklist
      lives (owed for src/ + combat-harness changes).
