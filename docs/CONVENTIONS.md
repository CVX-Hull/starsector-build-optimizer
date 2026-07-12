---
type: always-loaded
status: shipped
last-validated: 2026-07-11
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
| **plan** | `.claude/plans/active/`, `.claude/plans/archive/YYYY/` | Temporary execution records for non-trivial implementation work. Plans coordinate scope, review, verification, and retirement; they are not durable design authority. | No internal-sim measurements except links to owning reports. |
| **always-loaded** | `AGENTS.md`, `combat-harness/AGENTS.md`, `docs/CONVENTIONS.md` | Cross-cutting context that must be in the model's window every turn. Status, conventions, invariants. | **No.** Status pointers + design decisions only; numbers link out to reports. |
| **index** | `docs/project-overview.md`, `docs/roadmap.md`, `docs/reference/README.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md`, `.claude/skills/README.md` | Navigational entry points and concise orientation. | **No internal-sim measurements.** Route readers to owning docs and reports. |

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
- **Plans**: `.claude/plans/active/YYYY-MM-DD-short-slug.md` while current; move to `.claude/plans/archive/YYYY/YYYY-MM-DD-short-slug.md` when implemented or superseded. Do not put dated implementation plans in `docs/reports/` unless the file is actually a dated empirical report.
- **Always-loaded**: fixed paths (`AGENTS.md`, `combat-harness/AGENTS.md`, `docs/CONVENTIONS.md`). Do not add new always-loaded files casually — every new always-loaded file is a permanent context-window cost.
- **Index**: fixed conventional paths (`docs/project-overview.md`, `docs/roadmap.md`, `docs/reference/README.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md`, `.claude/skills/README.md`).

## Documentation System

Use one canonical owner per kind of truth:

| Question | Canonical owner |
|---|---|
| What is the current phase/status map? | `AGENTS.md` |
| What is planned next (forward roadmap, open workstreams, paused debts)? | `docs/roadmap.md` |
| Which skills exist and when does each apply? | `.claude/skills/README.md` |
| When and how are docs/roadmap/skills groomed? | `doc-grooming` skill + `scripts/validate_docs.py` (pre-commit) |
| Where does documentation belong? | `docs/CONVENTIONS.md` |
| Which spec number maps to which module? | `docs/project-overview.md` plus `docs/specs/README.md` for gaps |
| Which reference docs exist? | `docs/reference/README.md` |
| Which reports are current, draft, or historical? | `docs/reports/INDEX.md` |
| Which repo-local operational procedure should an agent follow? | Repo-local skill directory |
| What scope was approved for current implementation work? | Active plan under `.claude/plans/active/` |
| What happened during a completed implementation? | Archived plan under `.claude/plans/archive/YYYY/`, plus owning commit/report |
| Which generic workflow is portable across repos? | Portable packaged skill `skill-name/SKILL.md` outside this repo |

When a doc changes category or authority, update the corresponding index in
the same change. Do not let broad reference docs become second owners for
current module contracts, phase status, or empirical verdicts; they should link
to the owning spec, index, or report.

### Doc-system principles

1. **Discoverability — two hops, always indexed.** Every doc is reachable as
   always-loaded → index → owning doc. Every report / reference / spec / skill
   file must be linked from its owning index, and every relative link in the
   doc system must resolve to an existing file (both mechanically enforced by
   `scripts/validate_docs.py` in pre-commit; links shown inside code fences or
   inline code spans are examples and exempt).
2. **Always-loaded is a paid budget.** The always-loaded set costs context
   every turn; it has numeric line caps (owned by the `doc-grooming` skill,
   step 5). Over budget → extract procedure to a skill or replace prose with a
   pointer; never add a new always-loaded file to dodge the cap.
3. **Supersede vs. banner.** A new doc that *replaces evidence* supersedes via
   frontmatter (both ends). A new doc that only *revises how older evidence
   should be read* adds a dated banner to the affected section instead — the
   old measurements still stand.
4. **Say it once.** One canonical statement per checklist, list, or decision;
   everything else links. Skills do not restate other skills' checklists;
   reports do not keep live next-step lists (the roadmap owns open work);
   indices route, they do not explain.
5. **Indices stay scannable.** One line per doc, grouped by currency (current
   / draft / superseded / historical), superseded entries quarantined in their
   own group, and no content in index rows beyond a routing hook.

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

### Plan Frontmatter

Plans use plan-specific frontmatter instead of the durable-doc `type` schema:

```
---
plan_type: implementation
status: draft | approved | active | implemented | superseded
created: YYYY-MM-DD
approved: YYYY-MM-DD | null
implemented: YYYY-MM-DD | null
owner: agent
related_docs: []
implementation_commit: <hash> | not_committed | null
post_impl_audit: passed | failed | <relative-link> | null
superseded_by: <relative path> | null
---
```

Plan frontmatter tracks execution state, not design authority. A plan reaching
`implemented` means the implementation work and post-implementation audit are
done; any durable contract changes must already be reflected in specs, reference
docs, reports, skills, or code.

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
5. Is this temporary execution scope for implementation? → plan.
6. Is this status / conventions / cross-cutting context the model needs every turn? → always-loaded (and think hard before adding to this set — it's expensive).
7. Is this navigation? → index.

When in doubt, write a report. Reports are cheap to add and cheap to deprecate.

## Empirical-report writing standard

The full procedural standard (Methods-before-Results structure, table rules,
chart production-quality requirements, embedding, pre-ship checklist) lives in
the [`empirical-report`](../.claude/skills/empirical-report.md) skill —
extracted 2026-07-11 to keep this always-loaded file lean. The one-line
contract: **every numerical report follows Abstract → Methods → Results →
Synthesis → Open questions → File-map appendix, with Methods defined before
any Results section cites them.** Apply the skill whenever writing or
retrofitting a report.
