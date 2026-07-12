---
type: report
status: shipped
last-validated: 2026-07-12
---

# Quality-Tooling Research: Empirical Tool Trials and Adoption/Rejection Evidence

## Abstract

Three parallel research lanes trial-ran static-analysis, lint, and hygiene
tooling against this repository on 2026-07-12 to decide the quality-gate
toolchain. Every recommendation below is backed by an actual run against the
repo, not vendor claims. Adopted (tier 1+2, implemented under the
2026-07-12 quality-tooling-adoption plan): ruff (135 default-rule findings,
including one genuine undefined-name bug in a test mock), mypy
(`check_untyped_defs`; 51 src errors concentrated in 8 of 30 files, three
real annotation-drift defects), `javac -Xlint:all -Werror` (3 warnings from
clean), Error Prone 2.42.0 + NullAway 0.13.7 (62 warnings; found 2 lossy
float accumulations and 2 dead constants; **2.43.0+ crashes the JDK-17
compiler** — reproduced), shellcheck via `shellcheck-py` (15 findings over
27 scripts; a live quoting bug in the probe userdata template), deptry
(3 phantom runtime deps), pytest-randomly (suite verified green under
randomized order), and an internal-link check added to `validate_docs.py`
(569 cross-links previously unvalidated). Rejected with measured evidence:
bandit (79 findings, ~0 actionable), SpotBugs (10/10 noise), PMD (229
findings, 1 unique real), Checkstyle, codespell/typos (~0/25 true
positives), markdownlint (77 style-noise findings on 2 files), shfmt
(would churn 23/27 stable scripts), the pre-commit framework, CI
expansion, `ty` (pre-1.0), and pyright-as-second-checker. A critical
negative result: **shellcheck passes the exact ERR-trap `wait` pattern
that caused the attempt-1 worker loss** — executable bash-semantics tests
remain the only guard for that bug class. This report covers only the
tool-selection evidence; the adoption implementation itself (commits,
fix-to-green details, discovered defect dispositions) is recorded in the
2026-07-12 quality-tooling-adoption plan, and the ERR-trap incident is
owned by the batch-v2 incidents report.

## Methods

### §1.1 Data

- **Unit of analysis:** tool runs against the working tree at commit
  `4e24f00` (2026-07-12), before any tooling was adopted. Codebase at
  measurement time: 30 Python files in `src/starsector_optimizer/` plus
  `scripts/` and ~36 test files (1,003 passing tests); ~20 Java files
  (~2,600 main + ~1,550 test LOC) in `combat-harness/`; 27 shell scripts
  (~1,940 lines) in `scripts/` + `.githooks/`; three bash userdata
  templates rendered from Python (`cloud_userdata.py` ×2,
  `phase7_learned_batch.py`); ~50 markdown docs with 569 relative
  cross-links and 216 unique external links.
- **Runners:** Python tools via `uvx` (ruff 0.15.21, mypy 2.2.0, pyright
  1.1.411, ty 0.0.58, vulture, deptry, codespell, pymarkdownlnt); Java
  tools via scratch Gradle init scripts (`gradlew -I`, zero repo
  modification): Error Prone 2.42.0/2.43.0, NullAway 0.13.7, SpotBugs
  4.10.2 (plugin 6.5.8), PMD 7.26.0 quickstart, `javac -Xlint:all`;
  shellcheck 0.11.0 via the `shellcheck-py` wheel.
- Environment: macOS (darwin), JDK 17.0.19 (Homebrew), Gradle 9.4.1,
  Python 3.13 (`.python-version`).

### §1.2 Definitions

- **True/false positive judgments** are the researching agent's read of
  each sampled finding against the surrounding code, spot-verified for the
  findings that drove decisions (every "real defect" named below was
  independently re-verified at implementation time).
- **"Fix-to-green cost"** = the count of findings that must be resolved
  before the tool can gate commits.

## Results

### Python

**Method (§1.1, §1.2).** Tool runs via `uvx` against the pre-adoption
tree; per-finding judgments per §1.2.

| Tool | Config | Findings | Judgment |
|---|---|---:|---|
| ruff | default (E4/E7/E9/F) over src+scripts+tests | 135 | 1 real bug (F821, below), 4 F811 shadowed imports, 68 dead imports, rest mechanical |
| ruff | broad families (B, UP, SIM, C4, RUF, DTZ, PL, ARG, PTH, ISC, PIE, RET) | 824 | dominated by RUF001-003 Unicode (304, house typography) and ARG (331, interface conformance) — both excluded from adoption |
| mypy | default + ignore-missing-imports, src only | 50 in 8/30 files | 3 real annotation-drift defects; worst: optimizer.py (11), game_manifest.py (11), parser.py (9) |
| mypy | + `check_untyped_defs` (the adopted config), src only | 51 in 8/30 files | same defect set; the adopted-config number the Abstract cites |
| mypy | `--strict`, src | 125 in 18 files | ~60% bureaucracy (43 bare generics, 18 no-untyped-def) — rejected |
| pyright | basic, src | 44 | heavy overlap with mypy — rejected as second checker |
| ty 0.0.58 | default, src | 52 | only checker to flag the `requires-python`/StrEnum mismatch, but FP cascade from the same resolution — pre-1.0, revisit later |
| vulture | ≥80% confidence, src | 2 | both real (`parser.py` dead `mod_dirs` param; `calibration.py` documented stub) |
| vulture | 60% confidence, src | 98 | too noisy for CI — periodic manual audit only |
| deptry | tuned config | 3 unused runtime deps + misfiled stubs/test deps | `cmaes`, `lifelines`, `optunahub`: zero references repo-wide |
| bandit | src+scripts | 79 | ~0 actionable: subprocess/tmp/bind patterns inherent to a local single-tenant orchestration tool — rejected |
| pytest-randomly | full suite | 1,003 passed | green under randomized order at adoption time |
| pytest-xdist | `-n auto` | 54.8s → 32.6s | marginal at this suite size — optional |

Real Python defects found by the trials (all fixed in the adoption
commits): `tests/test_campaign.py:1018` F821 — the `_describe_ami_tag`
mock's `WorkerSourceSha` branch referenced an unimported name and would
`NameError` if exercised; `optimizer.py:594` annotations declared float
collections while the code stored `(build_idx, value)` tuples;
`cloud_worker_pool.py:317` `_server` inferred as `None` type;
`parser.py:373` documented-but-unread `mod_dirs` parameter;
`estimator.py:200` dead capacity computation; `requires-python = ">=3.10"`
while three modules use `StrEnum` (3.11+); a wrong `Counter[str]`
annotation hidden behind a pre-existing `type: ignore` in
`wave1_honest_eval_partial.py:304`.

### Java (combat-harness)

**Method (§1.1).** Scratch Gradle init-script trials (`gradlew -I`),
read-only against the repo; JDK 17.0.19 host.

| Tool | Findings | Judgment |
|---|---:|---|
| `javac -Xlint:all` | 3 | all one pattern: raw `Iterator` from the game's old org.json — fix then `-Werror` |
| Error Prone 2.42.0 + NullAway | 62 (47 main / 15 test) | EmptyCatch 21 = deliberate obfuscated-API wrappers (disable); NullAway 27 (see below); 2 real dead constants; 2 real lossy `float +=` accumulations (`ResultWriter.java:74,83`, feeds results JSON) |
| NullAway detail | 13 main / 14 test | main: 6 lifecycle-init + 7 missing `@Nullable` on verified-null-checked contracts (no latent NPEs); test: intentional pass-null-to-exercise-defaults. The 15th *test* finding is not NullAway — it is a StringSplitter finding (`ResultWriterTest.java:42`); an earlier synthesis conflated the two tallies (implementation recount, 2026-07-12) |
| SpotBugs 4.10.2 | 10 | 10/10 deliberate-by-design (System.exit shutdown protocol, exposed internals inherent to plugin architecture) — rejected |
| PMD 7.26.0 quickstart | 229 | style-dominated; exactly 1 unique real signal (unused import) — rejected |
| Checkstyle | not trialed | format-only, zero bug-finding — rejected |

**Version pin (reproduced, decision-grade):** Error Prone 2.42.0 is the
last release compiled for JDK 17 (class-file 61); 2.43.0+ ship class-file
65 and crash the JDK-17 compiler JVM with `UnsupportedClassVersionError`.
The pin is recorded in `build.gradle.kts`; the upgrade path (JDK 21
toolchain + `options.release = 17`) would change the compiler-host
convention and is deliberately not taken.

**NullAway noise concern resolved:** NullAway treats the unannotated
Starsector API optimistically, so API calls contribute zero warnings;
`AnnotatedPackages` scopes enforcement to `starsector.combatharness` +
`data.missions`. The flip side: nulls *returned by* the game API are not
checked — the existing defensive-catch pattern remains that mitigation.

### Shell / docs

**Method (§1.1, §1.2).** shellcheck 0.11.0 over `scripts/**` and the
rendered userdata templates; docs tools over `docs/` + skills.

- **Repo scripts:** 15 findings over 27 files — 10 warning / 4 info /
  1 style. Real: dead `CALLER_ACCT` in `cleanup_amis.sh` (redundant with
  `--owners self`), unguarded `cd` in `audit_amis.sh` (runs without
  `-e`), unquoted `$(date)` in the probe userdata template (SC2046, live
  in production template code). False positives: SC2064 expand-now trap
  strings ×6 (intentional pinning), SC2329 trap-invoked functions ×2,
  SC2016 JMESPath backticks, SC2001 multi-line sed.
- **Rendered userdata templates:** worker + batch templates clean at
  default severity; probe template carried the SC2046 above.
  `--enable=all` adds only brace-pedantry noise (~40× SC2250) —
  default severity is the right gate level.
- **Critical negative result:** the exact pre-fix attempt-1 pattern
  (bare `wait "$PID"` under `set +e` with an ERR trap — see
  [2026-07-12-phase7-batch-v2-incidents.md](2026-07-12-phase7-batch-v2-incidents.md))
  **passes shellcheck clean**. shellcheck does not model trap/reaping
  semantics. The executable bash-semantics tests in
  `tests/test_phase7_learned_batch.py` are the only guard for that class;
  the shellcheck layer complements them for quoting/globbing/syntax.
- **shfmt:** would rewrite 23/27 files in style-preserving mode — pure
  churn, rejected.
- **codespell:** 25 hits, ~0 true positives (ALS, SEMs, TPE, Dota, MIS,
  `patter` in a DOI, `retuned` intentional). **typos:** flags `TPE` and
  `yhat` — worse. Both rejected: high-acronym-density corpus is the worst
  case for dictionary spellcheckers.
- **markdownlint:** 77 findings on 2 sampled files (MD013 line-length,
  MD003 heading-style) — thousands extrapolated repo-wide, ~all noise
  against deliberate house conventions. Rejected.
- **Internal links:** 569 relative cross-links had no mechanical
  validation (the only real docs-coverage gap; `validate_docs.py` covered
  index membership + frontmatter only). Closed by extending
  `validate_docs.py` rather than adopting an external tool. External
  links (216 unique, dominated by arxiv/doi) get no gate — stable hosts;
  `lychee` remains an occasional ad-hoc command.

### Integration

**Method (§1.1).** Read-only inventory of existing gate mechanisms plus
the framework-fit assessments below; no tool runs.

The repo already had: an active opt-in `.githooks/pre-commit` (symlink
check, plan/docs validators, manifest gate), a dormant PR-only GitHub
workflow, and a strong culture of executable checks as pytest tests.
Decision: pytest-embedded checks for context-dependent gates (rendered
templates), pre-commit hook lines for whole-repo sweeps, no new
frameworks. The pre-commit framework was rejected because stage-based
framework hooks cannot see the commit message and would break the
`MANIFEST_REVIEWED` override flow; CI expansion was rejected because
direct-to-master commits make PR-triggered workflows dormant by
construction. (Adoption then discovered the message-based override never
worked at all — pre-commit hooks run before the message exists — and
moved enforcement to an env var; see the adoption plan's I-F1.)

## Synthesis & decisions

1. **Adopted tier 1:** ruff (bug-finder families, line-length 100,
   notebooks excluded), shellcheck-py + rendered-template and script-sweep
   pytest gates, pytest-randomly, pyproject hygiene
   (`requires-python >= 3.11`, phantom deps deleted, werkzeug/matplotlib
   declared, stubs/test deps re-homed).
2. **Adopted tier 2:** mypy (`check_untyped_defs`, src+tests, scripts
   excluded initially), Error Prone 2.42.0 (pinned) + NullAway 0.13.7 +
   `-Xlint:all -Werror`, deptry, internal-link validation in
   `validate_docs.py`.
3. **Staged follow-ups** — all delivered 2026-07-12 by the tightening
   plan (same day, per user directive): B905 `zip(strict=)` pass, mypy on
   `scripts/` + per-module import-checking tightening (stub packages for
   yaml/requests/boto3; overrides only for scipy/sklearn), `ruff format`
   one-shot + `--check` gate. The vulture periodic audit's operative home
   is the post-impl-audit skill's mechanical block; remaining
   premise-conditional watches live in §Open questions below.
4. **Standing rejections** (revisit only on changed premises): bandit,
   SpotBugs, PMD, Checkstyle, codespell/typos, markdownlint, shfmt,
   pre-commit framework, CI expansion, pyright-as-second-checker.

## Open questions / next steps

- The Error Prone pin blocks upgrades past 2.42.0 until the build adopts
  a JDK 21 compiler host with `options.release = 17` — a deliberate
  convention change to take separately if ever needed.
- pytest-xdist becomes worth adopting if the suite crosses ~2–3 minutes
  serial.
- If collaborators join (PRs stop being dormant), the CI rejection
  premise changes.
- Re-evaluate `ty` when it reaches 1.0 (rejected at v0.0.58 for FP
  cascades, but it was the only checker to catch the requires-python
  floor bug).
- `vulture --min-confidence 80` after large refactors/deletions (2/2
  true-positive rate at that threshold; too noisy below it) — operative
  procedure owned by the post-impl-audit skill.

## Appendix — file map

- **Producer:** three research sub-agent transcripts, session of
  2026-07-12 (tool invocations listed in §Methods are reproducible
  directly).
- **Raw data:** none retained beyond this report (tool runs are
  regenerable from the pinned versions above against commit `4e24f00`).
- **Charts:** none.
- **Dependent docs:** the adoption and tightening plans
  (`.claude/plans/` → archived under 2026 after implementation);
  [2026-07-12-phase7-batch-v2-incidents.md](2026-07-12-phase7-batch-v2-incidents.md)
  (owns the ERR-trap incident the shellcheck negative result references).
