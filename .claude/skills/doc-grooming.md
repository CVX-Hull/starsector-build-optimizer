# Doc, roadmap & skill grooming

Event-driven grooming SOP that keeps the documentation system navigable, the
roadmap canonical, and the always-loaded context lean. The mechanical half is
enforced by `scripts/validate_docs.py` (runs in pre-commit); this skill covers
the judgment calls the validator cannot make.

## Triggers (groom at these moments, not on a calendar)

| Event | Grooming required |
|---|---|
| Filing a new report | Steps 1–3 |
| Retiring/archiving a plan | Steps 2–3 |
| Completing an evidence wave or phase transition | Steps 1–5 (full pass) |
| Adding, splitting, or removing a skill | Steps 4–5 |
| Any decision that changes prior scope (e.g. lifting an out-of-scope ruling) | Steps 2–3 |

## Step 1 — Report hygiene

- Add the INDEX.md row; if it supersedes, set `supersedes:` here and
  `status: superseded` + `superseded-by:` in the older file, and move the
  older row to the INDEX "Superseded" section.
- If the new report changes how an older report should be *read* without
  replacing its evidence, add a banner to the affected section of the older
  report (see the 2026-07-11 banners for the pattern) instead of superseding.

## Step 2 — Roadmap grooming (`docs/roadmap.md` is the single owner)

- **Absorb**: move any adopted "next steps" out of the new report/plan into
  the roadmap (link back to the report for rationale; do not duplicate the
  list in both places). Leave unadopted hypotheses in the report's Open
  Questions.
- **Retire**: mark delivered roadmap items done by deleting them (the owning
  report/commit is the record) — the roadmap lists only open work.
- **Banner**: when a roadmap revision obsoletes a next-step list still living
  in an older report, banner that section and add it to the roadmap's
  "Superseded next-step lists".
- **Re-date**: bump `last-validated` whenever groomed.

## Step 3 — Status-map sync

- If phase status changed, update the `AGENTS.md` phase table in the same
  change (shipped rows only; forward-looking detail stays in the roadmap).
- Check the affected reference docs' pointer lines (e.g. "current dated
  roadmap checkpoint is …") still point at the right report.

## Step 4 — Skill hygiene

- New/renamed/removed skill → update `.claude/skills/README.md` and, if it is
  a workflow gate, the `AGENTS.md` gate table.
- Skills must not restate another skill's checklist — link to the canonical
  one (`design-invariants` owns invariants; `empirical-report` owns report
  structure). If two skills drift toward the same content, merge or split by
  owner.
- A skill that references removed modules/flags as *guards* ("X stays
  deleted") is correct; one that references them as *live instructions* is
  stale — fix it.

## Step 5 — Always-loaded budget audit

The always-loaded set (`AGENTS.md`, `combat-harness/AGENTS.md`,
`docs/CONVENTIONS.md`) is paid for every turn. Designed budgets:

- `AGENTS.md` ≤ ~130 lines; `docs/CONVENTIONS.md` ≤ ~220 lines;
  combined set ≤ ~420 lines (`wc -l`).
- Over budget → extract procedure into a skill (the 2026-07-11
  `empirical-report` extraction is the pattern), move forward-looking detail
  to the roadmap, or replace prose with a pointer. Never add a new
  always-loaded file to dodge the budget.
- While here: sweep `last-validated` dates on anything touched; a date more
  than one phase old on a doc you know changed meaning is a grooming bug.

## Mechanical validator

`uv run python scripts/validate_docs.py` — index completeness (reports /
reference / specs / skills), frontmatter sanity, superseded-by discipline,
roadmap presence. Pre-commit runs it; run it manually after any doc
restructuring. Extend the validator rather than this checklist whenever a
judgment rule hardens into something greppable.
