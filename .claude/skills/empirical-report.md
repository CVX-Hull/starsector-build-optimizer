# Empirical-report writing standard

Procedural standard for writing empirical reports (`docs/reports/`). Extracted
verbatim from `docs/CONVENTIONS.md` on 2026-07-11 so the always-loaded
conventions file stays lean; CONVENTIONS §"Empirical-report writing standard"
now points here. Apply this skill whenever writing or retrofitting a report
that presents numerical analyses (ranker comparisons, ablation sweeps, gate
verdicts, validation campaigns).

Reports follow a paper-style structure: **Methods before Results.** This is
mandatory for every new empirical report; retrofit at next-edit if an existing
report drifts.

## Required structure

1. **Abstract** (~one paragraph) — the headline finding stated in
   plain language with the central numbers, plus one sentence on what
   the report does *not* cover (so readers don't expect overlap with
   sibling reports).
2. **Methods** (single section, before any Results section)
   - **Data**: unit of analysis, source paths, filters applied,
     sample size N. Define every column / field you reference later.
   - **Estimators / models**: every metric and closed-form estimator
     gets a one-paragraph definition with the explicit formula. Cite
     the module path that implements it. Algorithmic estimators
     (random forests, gradient boosting, Gaussian processes, neural
     nets, etc.) must name the algorithm, implementation owner, input
     representation, training objective / loss where applicable,
     hyperparameters, defaults, and tuning policy. If a metric is
     project-specific (e.g. "imbalance index"), define it inline; if it
     is standard (Pearson r, Spearman ρ), name it.
   - **Statistical-learning setup**: supervised-learning reports must
     define the unit of observation, target variable, prediction target
     population, feature schema or feature groups, preprocessing, data
     partition semantics, leakage controls, hyperparameters, tuning
     policy, random seed, and model-selection criterion. Data
     partition semantics include train / validation / test /
     calibration / honest-eval partitions or cross-validation folds,
     plus the exact unit excluded across partitions. Present these as
     explicit labeled paragraphs or a compact table so reviewers do not
     have to infer them from prose. If a feature list is too large to
     enumerate inline, list feature families and cite the schema owner
     that defines exact keys.
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
   raw data, charts directory, dependent reports. Write `none` for
   artifact classes that do not exist for the report.

## Tables

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

## Charts

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

## Embedding charts in the report

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

## Checking your report before shipping

- Run the producer script end-to-end; confirm every chart referenced
  in the report exists at the expected path.
- Render the markdown locally (IDE preview, mkdocs, or
  `gh markdown-render`) and visually scan: every table renders with
  the correct column count, every figure embed resolves, every
  internal link reaches its target.
- Confirm the **Methods → Results** dependency: every Results section
  preamble references a Methods sub-section that actually exists.
