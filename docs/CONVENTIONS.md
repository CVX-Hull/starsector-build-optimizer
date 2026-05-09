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
| **skill** | `.claude/skills/<name>.md` | Procedural how-to / SOP. Step-by-step instructions for repeatable operations. | Operational thresholds OK; benchmark numbers go in reports. |
| **always-loaded** | `CLAUDE.md`, `combat-harness/CLAUDE.md`, `docs/CONVENTIONS.md` | Cross-cutting context that must be in the model's window every turn. Status, conventions, invariants. | **No.** Status pointers + design decisions only; numbers link out to reports. |
| **index** | `docs/project-overview.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md` | Navigational entry points. | **No.** Just routing. |

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
- **Skills**: `kebab-case.md`. The five generic engineering skills (`ddd-tdd`, `plan-review`, `post-impl-audit`, `design-invariants`, `starsector-modding`) are procedure-only and stable. Project-specific operational skills (e.g. `cloud-worker-ops.md`) carry the same frontmatter as everything else.
- **Always-loaded**: fixed paths (`CLAUDE.md`, `combat-harness/CLAUDE.md`, `docs/CONVENTIONS.md`). Do not add new always-loaded files casually — every new always-loaded file is a permanent context-window cost.
- **Index**: fixed conventional paths (`docs/project-overview.md`, `docs/reports/INDEX.md`, `experiments/INDEX.md`, `docs/specs/README.md`).

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
