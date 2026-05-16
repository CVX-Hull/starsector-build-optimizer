---
type: reference
status: draft
last-validated: 2026-05-12
---

# Phase 7 — Featurized Matchup Surrogate

This note extends [phase7-search-space-compression.md](phase7-search-space-compression.md).
The existing Phase 7 design is a structured Bayesian-optimization kernel for
choosing the next build to simulate. The additional question is whether the
large matchup table from Wave 1 and the current validation run can train a
model over non-atomic hull, weapon, hullmod, and opponent features.

Answer: yes, but the target should be a **contextual matchup model**, not a
single global build-strength model.

```text
combat_fitness
  ~ player_build_features
  + opponent_features
  + player_build × opponent interactions
  + source/run/replicate noise
```

Atomic build IDs remain useful as provenance and residual effects. They should
not be the primary representation because they prevent the model from sharing
evidence across builds that use similar hull geometry, weapon attributes,
hullmod sets, and opponent contexts.

## Why This Fits The Data

The project already emits the two raw inputs needed for this track:

- Optimizer `evaluation_log.jsonl` rows contain concrete repaired builds plus
  per-opponent scores.
- Honest-evaluation ledgers contain a balanced or partially balanced
  `(build, opponent, replicate) -> fitness` panel.

That means we can fit two related datasets:

1. **Training-log surrogate table**: many optimizer-proposed builds, usually
   unbalanced over opponents because pruning and curricula change which
   opponents are observed. This is useful for model training and active
   learning, but needs deconfounding or grouped validation.
2. **Honest-eval matchup table**: fewer builds, much denser opponent and
   replicate coverage. This is useful for validation, calibration, noise
   modeling, and final ranking. It should not be used to tune the optimizer
   policy that produced the candidates being judged.

The model row should be a matchup row, not a build row. Repeated replicates
then estimate aleatoric combat variance, opponent terms estimate matchup
difficulty, and build features estimate transferable structure.

This surrogate is **decision support**, not evaluation authority. It may rank
candidates, allocate future simulations, or provide a prior mean to Phase 7 BO.
It must not replace the honest-evaluation oracle, and it must not tune itself
on the same honest-eval rows later cited as final evidence.

## Representation

### Player Build Features

Use three layers of features, ordered from low-risk to high-capacity.

**Manifest/scorer aggregates**:

- Hull stats: hull size, OP budget, hitpoints, armor, max flux, dissipation,
  speed, shield type, shield arc, shield efficiency, ship system, fighter bays.
- Weapon totals: DPS by damage type, sustained flux, OP spent, missile count,
  PD count, range mean/min/max/std, range coherence, beam fraction, EMP total.
- Flux economy: vents, capacitors, dissipation after vents, weapon flux load,
  flux margin, shield efficiency times effective dissipation.
- Hullmod totals: count, OP spent, tag/UI-tag counts, built-in-mod overlap.
- Existing `heuristic_score()` outputs from `calibration.compute_build_features`.

**Sparse component features**:

- Multi-hot hullmods.
- Weapon ID per stable slot.
- Empty-slot sentinels.
- Slot-local attributes: slot type, slot size, mount, angle, arc, position,
  normalized position, forward projection, lateral/longitudinal offset, and
  tactical arc bucket.
- Weapon-local attributes: type, size, damage type, OP, sustained DPS,
  sustained flux, range, ammo, projectile speed, turn rate, tags/hints.
- Static hull geometry from `.ship`: bounds, width, height, center, collision
  radius, shield center/radius, engine-slot summaries, hull style, and sprite
  name as metadata. These are structured file fields, not sprite-pixel,
  video, or audio features.
- Arc-pressure summaries: front/aft/port/starboard slot counts, arc-weighted
  weapon pressure, PD coverage, and broadside/frontal/aft pressure totals.

**Structured token features**:

- Hullmod set tokens: `hullmod_id + op_cost + tags`.
- Slot tokens: `slot_geometry + assigned_weapon_id + weapon_attributes`.
- Optional hull token: all hull stats plus ship system.

The first implementation should flatten these into a tabular model. The token
form is the migration path to Deep Sets, Set Transformers, or heterogeneous
graph encoders.

### Opponent Features

Opponent context must be explicit. Otherwise the model learns "good average
build" and misses counters.

For a single opponent variant:

- Opponent hull stats and hull size.
- Opponent weapon totals by damage type, range, missile/PD/fighter pressure.
- Opponent armor/shield/flux profile.
- Opponent variant vents/capacitors, hullmod OP, hull system, phase stats,
  static geometry, arc-pressure summaries, and stock-variant fighter-wing
  pressure from `wing_data.csv`.
- Opponent role summaries derived from manifest/scorer features.

For a pool or curriculum stage:

- Mean and dispersion of the above features across the pool.
- Fractions with missiles, fighters, phase cloak, high armor, long range, and
  high speed.
- The opponent identifier as a categorical residual when enough observations
  exist.

### Interaction Features

Use cheap, interpretable interactions before deep models:

- Player minus opponent range, speed, armor, flux, and shield-efficiency
  summaries.
- Kinetic pressure versus opponent shield profile.
- High-explosive pressure versus opponent armor.
- PD and flak coverage versus opponent missile/fighter pressure.
- Weapon range coherence versus opponent preferred range.
- Player flux margin under estimated opponent pressure.
- Small-slot PD/kinetic/energy composition versus opponent type.

These features preserve the Phase 7 requirement that small slots remain
addressable. They let the model learn opponent-conditional small-slot value
without hard-filling those slots.

Feature schema v3 implements these as flat tabular features plus deterministic
feature profiles (`all`, `aggregate`, `geometry`, `opponent-parity`,
`sparse-component`, and `sparse-cross`) so ablations can test representation
families without changing the underlying row materialization.

Future feature-selection experiments should treat these profiles as the first
level of a hierarchy, not as the whole selection space. Each materialized
feature should also be assignable to a family, template, parent set, and
leakage-risk class so reports can distinguish "this combat subsystem is
stable" from "this exact sparse fingerprint happened to win one split." The
learned-surrogate research gate owns the full selection protocol:
[phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md#3-feature-selection-is-part-of-the-estimator).

## Modeling Sequence

The sequence below is no longer a fixed prescription. Model-family and
hyperparameter choices are gated by
[phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md),
which requires nested grouped validation, explicit HPO budgets, leakage
controls, and honest-eval top-k as a post-fit diagnostic. Spec 31 remains the
contract owner for the comparator gate and data provenance.

### 1. Baseline Table Model

Start with literature-justified tree-ensemble baselines for continuous
`combat_fitness`, chosen in the next experiment plan rather than hardcoded in
this reference. CatBoost is a strong candidate because the data is mixed
categorical/continuous, irregular, noisy, and medium-sized, and because it
handles categorical columns with ordered target statistics. XGBoost/LightGBM
style baselines may also be justified if the feature representation is sparse
one-hot or throughput becomes the dominant constraint.

Keep the scikit-learn comparator ladder from spec 31 as the locked baseline.
If a tuned learned model does not beat those comparators under nested grouped
validation, the feature table, model family, or tuning protocol needs further
diagnosis before optimizer integration.

The first uncertainty baseline should be ensemble or quantile based rather than
a full heteroscedastic GP:

- Quantile regression forests or quantile gradient boosting for intervals.
- NGBoost-style probabilistic boosting if distributional outputs are useful.
- Grouped split-conformal calibration over the chosen point/quantile model.

Repeated matchups should support both raw repeat rows and derived aggregate
analysis views:
`mean_fitness`, `n_repeats`, and `sample_variance` by exact
`(build, opponent)` group. Spec 31 currently owns the raw SQLite tables; any
persistent aggregate table or materialized view requires a spec update before
implementation. Raw rows are useful for variance modeling; aggregate views are
cleaner for ranking and calibration.

### 2. Sparse Interaction Model

Fit a factorization-machine-style model over sparse component indicators:
hull, slot weapon IDs, hullmods, opponent ID/type, and binned numeric
features. This is the most direct model family for sparse composition with
pairwise synergy and counter effects.

Regularization is load-bearing for this model family. The experiment plan must
declare interaction rank, field definitions, L2 penalties, feature-family
dropout or subsampling where available, and heredity rules for explicit
interaction features. For vanilla factorization-machine models, heredity is a
diagnostic lens rather than a native constraint; it becomes a hard rule only
for explicit counter features or hierarchical sparse interaction variants. A
hand-authored matchup interaction should normally be retained only when its
parent families are stable under grouped validation, such as keeping
`kinetic_pressure x opponent_shield_profile` only if weapon-pressure and
opponent-defense families both survive family-level selection.

The key value is not just prediction. Learned component interactions can
answer questions such as:

- Which hullmod pairs are consistently positive after opponent adjustment?
- Which weapon-slot assignments transfer across similar slots?
- Which opponent features flip a small-slot weapon from useful to harmful?

### 3. Rating Hybrid

Keep a rating-style model as a baseline and diagnostic:

```text
fitness_{build,opp}
  = build_skill
  - opponent_difficulty
  + gamma^T matchup_features
  + error
```

This generalizes the existing TWFE/Bradley-Terry ranker path. The rating terms
capture residual strength; the covariates capture transfer and counters. A
rating-only model is insufficient because it cannot represent rock-paper-scissor
matchups or component-level counterfactuals.

### 4. Neural Token Model

Move here only after the table and sparse-interaction baselines plateau.

Recommended architecture:

- Hull tower: hull continuous stats plus hull ID/system embeddings.
- Hullmod tower: Deep Sets first; Set Transformer only if pairwise hullmod
  interactions are visibly important.
- Slot/weapon tower: one token per slot, with slot geometry and assigned weapon
  attributes inside the token. Use Set Transformer over slots so ordering is
  not load-bearing.
- Opponent tower: opponent or opponent-pool feature encoder.
- Fusion head: concatenate towers, then predict combat fitness and optional
  win probability.

A heterogeneous graph model is a later extension if token models plateau:
nodes for hull, slots, weapon instances, hullmods, opponent, and typed edges
for `has_slot`, `equipped_with`, `has_hullmod`, and `faces_opponent`.

The first neural representation should remain small and regularized:
manifest-derived side features plus capped entity embeddings, weight decay,
dropout, early stopping on grouped inner validation, and simple sum/mean
pooling before attention. Set Transformer, cross-attention between own and
opponent component tokens, and graph message passing are later ablations
because they add enough capacity to memorize rare opponent/loadout
fingerprints unless hierarchy-aware diagnostics stay clean.

If a graph model is later justified, relation types should be predeclared from
game structure, such as `mounted_in`, `same_arc`, `range_compatible`,
`flux_competes_with`, `counters_defense`, and `enabled_by_hullmod`. Arbitrary
learned dense edges are not first-line evidence because they are harder to
audit for leakage.

## Validation Protocol

Random row splits are invalid for this project because replicates, near-duplicate
builds, and repeated opponents leak across train/test.

Use at least these splits:

- **Held-out replicate**: checks noise modeling only. This is the easiest split
  and should not be reported as transfer. This split is the only exception to
  the rule that all repeats of an exact matchup stay in the same fold.
- **Held-out opponent**: train on some opponents, test on unseen opponents.
- **Held-out build**: all rows for selected builds are test-only.
- **Held-out component combination**: test on builds containing weapon/hullmod
  combinations not seen together in train.
- **Held-out seed/cell**: train on some campaign cells or seeds, test on others.
- **Forward-time split**: train on earlier optimizer proposals, test on later
  proposals to mimic online deployment.

For hold-out-opponent claims, report a hierarchy ladder instead of one averaged
score:

- **Random row split**: smoke/debug only; not transfer evidence.
- **Exact-opponent holdout**: minimum anti-memorization gate.
- **Hull holdout**: all variants/loadouts for selected hulls are test-only
  through the implemented `opponent-hull` split.
- **Hull-family or archetype holdout**: harder transfer across related
  opponent families through the implemented `opponent-family` split, currently
  keyed by outcome-free hull size, designation, and tech/manufacturer.
  Learned clusters require a later train-fold-fitted implementation.
- **Campaign/regime holdout**: only for claims that cross campaign cells,
  curricula, or proposal policies.

Metrics:

- RMSE/MAE on `combat_fitness`.
- Spearman rank correlation by held-out build.
- Calibration of predictive intervals.
- Top-k recall against honest-eval oracle rankings.
- Counterfactual stability under opponent-group holdout.
- Feature-family stability and selected-family frequency.
- Rare-combination overlap and nearest-neighbor leakage diagnostics.

The honest-eval ledger should be the final judge for top-k ranking, not the
training target used to tune feature choices.

## Bias And Leakage Guardrails

Training-log rows are optimizer-selected, not iid samples. A model trained on
them can become overconfident in regions the optimizer already liked and weak
elsewhere.

Guardrails:

- Keep randomized exploration batches in future campaigns for coverage.
- Record enough proposal metadata to reconstruct which policy produced each
  build.
- Do not tune the surrogate on the same honest-eval rows used to claim final
  candidate superiority.
- Report grouped split performance, not only random-row performance.
- Keep opponent identity out of "transfer" metrics unless the split explicitly
  allows memorizing known opponents.
- Preserve raw atomic IDs as residual terms, but require aggregate/structured
  features to carry cross-build predictions.
- Treat random-row validation as a debugging metric only.
- Keep all repeats of an exact matchup in the same fold.
- Track prediction-interval coverage by opponent family, score regime, and
  campaign/time split.
- Make active-learning objectives explicit: global surrogate accuracy, best-build
  search, opponent coverage, and honest evaluation are different goals.

No counterfactual policy-value claim should be made from historical logs unless
the report includes behavior-policy metadata, propensities or a defensible
propensity model, overlap diagnostics, clipping rules, effective sample size,
and a variance-aware estimator. Without those, call the result model-assisted
search, not offline policy evaluation.

### Future Logging Requirements

Future simulation rows should carry enough metadata to support active learning
and offline evaluation:

- Build ID plus the full repaired build spec.
- Materialized build features and opponent features, or versioned feature-code
  provenance sufficient to regenerate them.
- Exact matchup group ID, replicate seed/index, campaign ID, study/cell/seed,
  optimizer version, and feature schema version.
- Acquisition reason: random exploration, warm-start, BO acquisition,
  uncertainty sampling, repeat-allocation, or honest evaluation.
- Behavior policy and action probability when the row came from randomized
  exploration.
- Whether the row was eligible for model training, model selection, calibration,
  or honest evaluation.

## Package Setup

Optional extras are split by use:

```bash
uv sync --extra literature
uv sync --extra surrogate
```

`literature` installs PDF and paper-extraction tools for research corpora.
`surrogate` installs BoTorch/GPyTorch/Pyro for the Phase 7 kernel path and
CatBoost for the featurized matchup baseline.

The local corpus seed list for this review is:

```text
_research/phase7-featurized-matchup/sources.txt
```

The downloaded/extracted corpus lives under:

```text
_research/phase7-featurized-matchup/corpus/
```

## Implementation Substrate

The first implementation layer is a generated SQLite dataset rather than
Parquet. SQLite keeps provenance, recovered builds, training-log matchups, and
honest-eval matchups inspectable with no extra data-service dependency. The
generated DB is local data under:

```text
data/phase7/
```

Current implementation entry points are listed here for orientation; the
module contracts are owned by
[../specs/31-phase7-matchup-data.md](../specs/31-phase7-matchup-data.md):

- `src/starsector_optimizer/matchup_features.py` for flat player, opponent,
  and matchup feature rows.
- `src/starsector_optimizer/phase7_matchup_data.py` for build recovery,
  SQLite materialization, and grouped split builders.
- `scripts/analysis/phase7_materialize_matchups.py` for creating the derived
  DB from JSONL logs, Optuna study DBs, and honest-eval ledgers.
- `scripts/analysis/phase7_baseline_surrogate.py` for the first scikit-learn
  grouped-split baseline.

The current baseline CLI is the comparator-gate harness, not the end-state
model. The first grouped comparator report has been filed at
[../reports/2026-05-11-phase7-matchup-surrogate-preliminary.md](../reports/2026-05-11-phase7-matchup-surrogate-preliminary.md).
The literature gate for choosing the next learned baselines is
[phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md).

Next steps after materialization:

1. Inspect comparator-gate error by opponent family, build family, campaign
   cell, and path-ordered forward bucket.
2. Write an experiment plan that derives candidate models, hyperparameter
   spaces, nested grouped validation, calibration, and artifact provenance from
   the learned-surrogate research gate.
3. Promote learned tree and sparse interaction baselines only if they beat the
   scikit-learn comparator ladder under the declared protocol.
4. Feed validated surrogate predictions into Phase 7 as either:
   - a prior mean for the BoTorch GP,
   - a candidate prefilter before expensive acquisition optimization,
   - or an active-learning uncertainty model for selecting which matchups to
     simulate next.

The Phase 7 BO layer should be reframed as online residual optimization over
structured features:

```text
observed fitness
  = supervised_matchup_surrogate(build, opponent)
  + online_BO_residual(build, opponent)
```

This lets the large historical table supply broad descriptor-level structure,
while BoTorch/SAAS/combinatorial kernels focus on calibrated uncertainty and
local residual corrections where new simulations are most valuable.

## Evidence Base

Key paper families represented in the 2026-05-11 learned-surrogate research
gate:

- Sparse composition and matchup models: factorization machines,
  field-aware factorization machines, contextual recommendation, matrix
  factorization, pairwise ranking, and rating-system diagnostics.
- Set representations: Deep Sets and Set Transformer as deferred token-model
  candidates.
- Practical tabular baselines: CatBoost, XGBoost, LightGBM, FT-Transformer,
  tabular neural-network benchmarks, and TabPFN.
- Mixed-variable and residual-BO tools: EGO/BO foundations, SMAC, CoCaBO,
  MiVaBo, category-specific BO, COMBO, TuRBO, SAASBO, additive BO, Hyperband,
  and BOHB.
- Offline and active-learning guardrails: grouped/nested validation, leakage,
  reusable holdouts, selection bias, quantile forests, conformal calibration,
  active learning, best-arm identification, knowledge gradient, and OCBA-style
  simulation allocation.

The immediate engineering conclusion is conservative: build the feature table
and grouped comparator ladder first, then add literature-derived learned tree
and sparse-interaction baselines through a reviewed experiment plan. Use neural
set/graph models only after lower-capacity models establish a real signal and
expose where flattened features fail. The detailed source table and decision
matrix live in
[phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md).

## Current Roadmap Position

The staged roadmap is:

1. Keep the completed honest-eval ledger and per-cell outputs materialized in
   the Phase 7 matchup DB with zero unresolved build keys.
2. Validate the feature substrate with grouped splits and trivial comparators.
3. Use the learned-surrogate research gate to run opponent-transfer hierarchy
   diagnostics: exact opponent, opponent hull, opponent family, and then
   feature-profile ablations under the same nested grouped validation contract.
4. Promote learned baselines only after they beat the comparator ladder under
   that protocol.
5. Feed validated predictions into online search as a prior mean, candidate
   prefilter, or active-learning signal.
6. Implement the custom structured BO sampler only after the cheaper
   model-assisted-search gates show value.

The current roadmap checkpoint is recorded in
[2026-05-11-validation-to-phase7-roadmap.md](../reports/2026-05-11-validation-to-phase7-roadmap.md).
That report owns dated measurements; final honest-eval verdict details live in
[2026-05-11-wave1-honest-eval-final.md](../reports/2026-05-11-wave1-honest-eval-final.md).
