---
type: always-loaded
status: shipped
last-validated: 2026-05-10
---

# Documentation Conventions

This document defines the categorical structure of the project's documentation, the file-naming rules per category, and the per-file frontmatter convention. It is the source of truth for "where does this content live, and how do I label it?"

The system was formalized 2026-05-10 alongside the V1 loadout-bug invalidation cleanup (see [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](reports/2026-05-10-v1-loadout-bug-invalidation.md)).

## Categories

| Category | Location | Purpose | Empirical numbers? |
|---|---|---|---|
| **spec** | `docs/specs/NN-name.md` | Module / protocol contracts. Schemas, signatures, invariants, public interfaces. | **No.** Pure contract. |
| **reference** | `docs/reference/<topic>.md` | Design rationale, research synthesis, theory, rejected alternatives. | **No internal-sim numbers.** Published-academic citations are fine. |
| **report** | `docs/reports/YYYY-MM-DD-<slug>.md` | Dated empirical evidence: campaign results, validation outcomes, ablation tables, audit findings, retrospectives. | **Yes — reports own all dated measurements.** |
| **skill** | repo-local skill directory | Repo-local procedural how-to / SOP. Step-by-step instructions for repeatable operations. | Operational thresholds OK; benchmark numbers go in reports. |
| **always-loaded** | `AGENTS.md`, `combat-harness/AGENTS.md`, `docs/CONVENTIONS.md` | Cross-cutting context that must be in the model's window every turn. Status, conventions, invariants. | **No.** Status pointers + design decisions only; numbers link out to reports. |
| **index** | `docs/project-overview.md`, `docs/reference/README.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md` | Navigational entry points and concise orientation. | **No internal-sim measurements.** Route readers to owning docs and reports. |

### The empirical-numbers rule

> **Specs and references contain NO inline empirical numbers. Reports own all dated measurements. References that need to cite a measurement link to a report file by path.**

A "measurement" is anything derived from running this project's code:

- Δρ values, Cohen's d, p-values
- ceiling-saturation %, top-k overlap
- LOOO ρ, correlation coefficients on sim data
- throughput rates (matchups/hr, trials/hr, speedup ratios)
- $-figures derived from throughput
- "X out of Y trials had property Z" on a specific run

Citations from the published academic literature (e.g. "SAASBO reports 2-5× sample efficiency at d≈100") are reference-grade — they're not derived from this project's runs and don't go stale when our code changes. Cite the paper inline.

**Game constants and engine list-prices are reference-grade**, not measurements:
- Constants from `game/starsector/data/` (`MAX_FLUX_VENTS = 30`, weapon DPS values, hullmod tier definitions) are upstream-game-defined and stable per Starsector minor version. Inline freely.
- Engine clamps (the 5.0× `time_mult` ceiling, `setRetreating(false, false)` semantics) are engine-defined, not measured.
- AWS / Hetzner public list prices ($0.15/hr c7a.2xlarge spot, $0.05/GB·month EBS snapshot) are provider-published, not derived from our runs. Inline freely; flag the date if pinned to a specific quote.
- Algorithmic floors and config defaults (`eb_min_builds = 8`, `min_samples = 8`, `tau2_floor_frac = 0.05`) are designed parameters, not measurements.
- Threshold definitions in pass/fail gates ("Pass: Δρ ≥ +0.02") are designed gates, not measurements — but the *measured* values that motivated the threshold belong in a report.

When a reference doc previously asserted an internal-sim number, replace with one of:

1. **If a design threshold exists** —
   > "Empirically validated to ≥ \<threshold\> on production data. See [docs/reports/INDEX.md](reports/INDEX.md) for the latest validation report."
2. **If no threshold is defined or re-validation is pending** —
   > "Pending re-validation under V2 loadout fix; see [docs/reports/2026-05-10-v1-loadout-bug-invalidation.md](reports/2026-05-10-v1-loadout-bug-invalidation.md)."
3. **If the claim is purely qualitative** ("the effect was observed", "the design was validated") — leave it; it's not a measurement, it's a design statement.

## File naming

- **Specs**: `NN-kebab-case.md`, two-digit zero-padded, monotonically assigned. Gaps from deleted/renumbered specs are documented in [docs/specs/README.md](specs/README.md). Never reuse a number.
- **Reference**: `kebab-case.md` or `phaseN-topic.md` for phase-scoped research synthesis. No dates in filenames — references should be stable across phases unless explicitly superseded.
- **Reports**: `YYYY-MM-DD-kebab-case-slug.md`. The date is the date the evidence was gathered (or the date of the audit / retrospective for non-experimental reports). Reports are append-only; supersession is via frontmatter, not deletion.
- **Repo-local skills**: `kebab-case.md` under the repo-local skill directory. These are local workflow files. They can wrap portable packaged skills, but they are not themselves portable packages.
- **Portable packaged skills**: live outside this repo as `skill-name/SKILL.md` with optional `references/`, `scripts/`, and `assets/`. Do not promote a flat repo-local skill file verbatim; split generic workflow into `SKILL.md`, move long detail into `references/`, and leave repo paths/commands/invariants in the local wrapper.
- **Always-loaded**: fixed paths (`AGENTS.md`, `combat-harness/AGENTS.md`, `docs/CONVENTIONS.md`). Do not add new always-loaded files casually — every new always-loaded file is a permanent context-window cost.
- **Index**: fixed conventional paths (`docs/project-overview.md`, `docs/reference/README.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md`).

## Documentation System

Use one canonical owner per kind of truth:

| Question | Canonical owner |
|---|---|
| What is the current phase/status map? | `AGENTS.md` |
| Where does documentation belong? | `docs/CONVENTIONS.md` |
| Which spec number maps to which module? | `docs/project-overview.md` plus `docs/specs/README.md` for gaps |
| Which reference docs exist? | `docs/reference/README.md` |
| Which reports are current, draft, or historical? | `docs/reports/INDEX.md` |
| Which repo-local operational procedure should an agent follow? | Repo-local skill directory |
| Which generic workflow is portable across repos? | Portable packaged skill `skill-name/SKILL.md` outside this repo |

When a doc changes category or authority, update the corresponding index in
the same change. Do not let broad reference docs become second owners for
current module contracts, phase status, or empirical verdicts; they should link
to the owning spec, index, or report.

### General Applicability

Durable project docs and portable skills should be tool-agnostic. Prefer
generic nouns such as "agent", "root workflow file", "repo-local skill",
"portable packaged skill", and "sub-agent" over assistant- or vendor-specific
product names.

Use a literal product name or filename only when it is the object being
documented, such as a real path, command, compatibility note, migration record,
or historical report. When possible, point readers at the role of the file
instead of the filename:

- Say "root workflow file" when the doc is describing policy ownership.
- Say `AGENTS.md` only when the exact file path matters.
- Say "repo-local skill directory" for local workflow files.
- Say `skill-name/SKILL.md` for portable packaged skill layout.

## Frontmatter

Every doc file starts with a YAML frontmatter block:

```
---
type: spec | reference | report | skill | always-loaded | index
status: shipped | draft | superseded | deprecated
last-validated: YYYY-MM-DD | unvalidated
supersedes: <relative path>      # if this file replaces another
superseded-by: <relative path>   # if this file is now superseded
---
```

Field semantics:

- **type** — required. One of the six categories above.
- **status** — required.
  - `shipped` — describes code/process currently in use.
  - `draft` — describes code/process not yet landed; do not consult for production decisions.
  - `superseded` — content was correct at the time but a newer file (`superseded-by`) now applies; kept for historical context.
  - `deprecated` — content is wrong or no longer applies; do not delete (link integrity), but readers should not act on it.
- **last-validated** — required.
  - For specs/references: date the content was last reconciled against the code or the cited literature.
  - For reports: same as the date in the filename (the date of the measurement).
  - For skills: date the procedure was last walked end-to-end.
  - `unvalidated` is a legitimate value during cleanup transitions (e.g. a reference doc whose empirical claims have just been stripped pending re-validation).
- **supersedes** / **superseded-by** — optional. Use relative paths from the file's own directory. Both ends should be set when supersession lands.

Frontmatter `status` describes the document's authority. Phase/status-map
labels such as `pending re-val`, `planned`, `deferred`, or `Tier-2 live`
describe project work state and are tracked by the root workflow file; they
are not frontmatter values.

Frontmatter is parsed by humans, not tools — treat the schema as disciplined-but-flexible. If a field doesn't apply to a file, omit it; don't use null/N/A.

## Cross-references

- Use relative-path Markdown links: `[name](relative/path.md)`.
- Link to specific sections with anchors: `[name](path.md#anchor)`.
- Never use absolute filesystem paths in links.
- Dangling references (target deleted) should be replaced with a link to the report that documents the deletion, or removed if no replacement exists.

## Where to put new content

When writing a new doc, ask:

1. Is this a contract for code? → spec.
2. Is this design rationale or research that will outlast a specific run? → reference.
3. Is this a measurement, a campaign log, an audit, or a retrospective? → report.
4. Is this a step-by-step procedure? → skill.
5. Is this status / conventions / cross-cutting context the model needs every turn? → always-loaded (and think hard before adding to this set — it's expensive).
6. Is this navigation? → index.

When in doubt, write a report. Reports are cheap to add and cheap to deprecate.

## Empirical-report writing standard

Reports that present numerical analyses (ranker comparisons, ablation
sweeps, gate verdicts, validation campaigns) follow a paper-style
structure: **Methods before Results.** This is mandatory for every new
empirical report; retrofit at next-edit if an existing report drifts.

### Required structure

1. **Abstract** (~one paragraph) — the headline finding stated in
   plain language with the central numbers, plus one sentence on what
   the report does *not* cover (so readers don't expect overlap with
   sibling reports).
2. **Methods** (single section, before any Results section)
   - **Data**: unit of analysis, source paths, filters applied,
     sample size N. Define every column / field you reference later.
   - **Estimators / models**: every metric or estimator gets a
     one-paragraph definition with the explicit formula. Cite the
     module path that implements it. If a metric is project-specific
     (e.g. "imbalance index"), define it inline; if it is standard
     (Pearson r, Spearman ρ), name it.
   - **Comparison statistics**: define the test statistics used. If
     bootstrap is used, name the iteration count, RNG seed, and
     resampling unit (rows? builds? matchups?).
   - **Diagnostics & thresholds**: list every doc gate or design
     threshold the report will judge against, with its source doc.
3. **Results** (one section per measurement)
   - Each results section opens with a 1-3-line preamble:
     `**Method (§N.N).**`, `**Statistic (§N.N).**`,
     `**Threshold (§N.N).**` referring back to the Methods section.
   - Then the table or chart.
   - Then the **Reading**: what the numbers say in plain language,
     what they imply for the design.
4. **Synthesis & decisions** — what the body of evidence implies,
   what changes (if any) follow.
5. **Open questions / next steps** — hypotheses the report cannot
   close on its own.
6. **Appendix — file map** — explicit pointers to: producer script,
   raw data, charts directory, dependent reports.

### Tables

- **Right-align numeric columns** with `|---:|`; left-align text.
- **Pad rows visually**: align pipe positions so a quick visual scan
  catches outliers. A formatter is fine; doing it by hand is fine.
- **Escape pipes inside cells** as `\|` whenever a metric name
  contains the pipe character (e.g. `median \|z\|`, `\|Δz\| > 1`).
  Unescaped `|` inside a table cell breaks the markdown parser and
  silently splits the row into the wrong number of columns.
- **Mark guardrail breaches** with `**bold**` and a verdict column
  rather than relying on color alone.
- **Always include sample sizes** (`n =`, `N_finalized =`, etc.) so
  readers can judge precision.

### Charts

Charts are produced by a checked-in script under `scripts/analysis/`,
not assembled in a notebook. Output goes to
`data/<campaign>/charts/NN_*.png` (zero-padded numeric prefix matching
the report figure number). The script must be runnable end-to-end via
`uv run python …` and write deterministic output (fixed RNG seeds).

**Chart + `headline_numbers.json` outputs are tracked in git** so reports
render out-of-the-box for any clone or web viewer. Raw inputs (per-trial
JSONL ledgers, study DBs) stay gitignored — they are too large and can
be reproduced from the campaign config. The `.gitignore` pattern is
`/data/*` plus a per-campaign `!/data/<campaign>/` negation; add the
negation line when introducing a new campaign output directory.

**Production-quality requirements** (apply at module import via
matplotlib `rcParams`, not per-figure):

- **Resolution**: `savefig.dpi ≥ 200`. Screen-preview `figure.dpi` may
  be lower for speed; the production save dpi is the on-disk number.
- **Layout**: `figure.constrained_layout.use = True`. Do not rely on
  `bbox_inches="tight"` + manual `y=1.02` suptitle positioning.
- **Color cycle**: `Tableau-Colorblind-10` or equivalent
  colorblind-safe palette. Do not use `tab:red` / `tab:green` for
  pass / fail signals (use orange / blue and a verdict label).
- **Grids**: light grey, `axes.axisbelow = True` so data lines
  overlay grids, never the reverse.
- **Spines**: top + right off; left + bottom thin (≤ 0.8 pt).
- **Axes labels**: every axis labelled with units in parentheses
  where applicable (`hp-differential, dimensionless`, `% trials`,
  `logit units`). For ratio metrics, write the formula in math:
  `Spearman $\rho$`, `$N_{\mathrm{pruned}} / N_{\mathrm{total}}$`.
- **Titles**: factual, no trailing punctuation, ≤ 2 lines. Multi-panel
  figures get panel letters `(a)`, `(b)` in each subplot title.
- **Annotations**: when annotating bar values, place the text above
  the bar with a margin proportional to y-axis range (`y_max * 0.02`),
  not a hardcoded `+0.05`.
- **Legends**: `frameon=False`, positioned to avoid overlap; for
  grouped bars, prefer a 2-column legend over crowding.
- **Captions**: every embedded chart in the report gets a Markdown
  caption immediately below the image (italic, prefixed with
  `*Figure N — …*`), describing what is plotted, the units, and any
  reference lines or thresholds.

### Embedding charts in the report

- Use `![alt-text](../../data/<campaign>/charts/NN_name.png)`. Alt
  text is descriptive (a screen reader should learn what the chart
  shows), not just the filename.
- Place the embed *immediately after* the table or numeric statement
  it visualises, before the **Reading** paragraph.
- Number figures globally within the report (`Figure 1`, `Figure 2`,
  …). The chart filename's numeric prefix (`01_…`, `02_…`) and the
  Figure number do not have to match — the producer script's order
  reflects code structure; the report's order reflects narrative
  structure.

### Checking your report before shipping

- Run the producer script end-to-end; confirm every chart referenced
  in the report exists at the expected path.
- Render the markdown locally (IDE preview, mkdocs, or
  `gh markdown-render`) and visually scan: every table renders with
  the correct column count, every figure embed resolves, every
  internal link reaches its target.
- Confirm the **Methods → Results** dependency: every Results section
  preamble references a Methods sub-section that actually exists.
