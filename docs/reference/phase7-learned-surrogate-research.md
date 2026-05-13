---
type: reference
status: shipped
last-validated: 2026-05-12
---

# Phase 7 — Learned Surrogate Research Gate

This note records the literature-first gate for Phase 7 learned-surrogate model
development. It is design rationale, not a module contract and not an empirical
verdict. Spec 31 owns the Phase 7 data and comparator contracts; reports own
dated project measurements.

## Research Brief

**Date and cutoff.** Research was conducted on 2026-05-11. "Current" and
"recent" claims use that date as the cutoff.

**Question.** Given the Phase 7 matchup DB and comparator-gate result, what
model-development process should precede any learned-baseline or optimizer
integration work?

**Subquestions.**

- Which validation protocol is statistically valid for grouped, non-iid
  `(build, opponent)` rows?
- How should hyperparameters, preprocessing, feature selection, model
  selection, and calibration be separated from final evaluation?
- Which model families are justified as candidates by the literature and our
  data shape, without assuming a winner in advance?
- How should feature selection be represented so held-out-opponent validation
  tests transferable combat structure instead of memorized opponent identity?
- Which hierarchy and regularization schemes apply to feature families,
  interactions, and more complex compositional models?
- When is model-assisted search or Bayesian optimization justified?

**Inclusion criteria.** Primary papers, surveys, or implementation papers that
directly address grouped validation, leakage, HPO, calibration, tabular mixed
data, sparse interactions, contextual/ranking models, mixed-variable
optimization, expensive simulation, active learning, or ranking and selection.

**Exclusion criteria.** Sources that only benchmark iid random splits, assume
unconstrained continuous inputs without useful transfer, or require feedback
signals this project does not log yet are treated as inspiration or deferred,
not implementation authority.

**Corpus relation.** The existing `_research/phase7-featurized-matchup/`
corpus contains extracted papers for Phase 7 search-space and surrogate
design. This note reuses that corpus where relevant and adds the 2026-05-11
research lanes for validation/HPO, model families, and optimization/allocation.
Sources not full-text-read are marked `skimmed` or `metadata-only`.

| Corpus bucket | Sources |
|---|---|
| Reused from local extracted corpus | Gorishniy 2021, Eriksson & Jankowiak 2021, Hvarfner/πBO-family sources already cited by Phase 7 search-space design, Ru 2020, Oh 2019, and related BO/representation papers present under `_research/phase7-featurized-matchup/corpus/`. |
| Newly added by 2026-05-11 research lanes | Roberts 2017; Varma & Simon 2006; Cawley & Talbot 2010; Krstajic 2014; Vabalas 2019; Bischl 2023; Probst 2019; Kaufman 2012; Kapoor & Narayanan 2023; Dwork 2015; Gneiting 2007; Kuleshov 2018; Lei 2018; Romano 2019; Grinsztajn 2022; Borisov 2024; Prokhorenkova 2018; Rendle 2010; Schnabel 2016; Li 2018; Falkner 2018; Daxberger 2020; Kaufmann et al. 2016; Frazier et al. 2009. |
| Local full-text/extracted access verified | Sources listed in `_research/phase7-featurized-matchup/corpus/reading_index.md`; those entries have Markdown/text extraction paths. |
| Web/metadata access in this pass | DOI, arXiv, publisher, JMLR, PMLR, NeurIPS, Microsoft Research, and ACM/IEEE landing pages listed in the source table below. |

Local extracted sources used directly in this note:

| Source | Local corpus access |
|---|---|
| Gorishniy et al. 2021 | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-2106.11959.md` |
| Eriksson & Jankowiak 2021 | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-2103.00349.md` |
| Ru et al. 2020 | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-1906.08878.md` |
| Oh et al. 2019 | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-1902.00448.md` |
| BaCO / Hellsten et al. | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-2212.11142.md` |
| πBO / Hvarfner et al. | `_research/phase7-featurized-matchup/corpus/markdown/arxiv-2204.11051.md`; adaptive-prior BO citations require a refreshed corpus entry for arXiv 2304.11005 before use as design authority. |

**Research execution record.** Three independent research lanes were run on
2026-05-11 before synthesis:

- validation/HPO/calibration/leakage lane;
- tabular, sparse-interaction, ranking, and matchup-model lane;
- mixed-variable optimization, surrogate-assisted optimization, active
  learning, and simulation-allocation lane.

Their outputs were integrated into the source table, synthesis, evidence
matrix, and decision table below. The final note is the adjudicated synthesis,
not a verbatim paste of any single lane.

## Authority Boundaries

- Data contracts, feature-schema versioning, comparator names, provenance
  fields, and honest-eval top-k protocol are owned by
  [spec 31](../specs/31-phase7-matchup-data.md).
- The comparator-gate measurements are owned by
  [2026-05-11-phase7-matchup-surrogate-preliminary.md](../reports/2026-05-11-phase7-matchup-surrogate-preliminary.md).
- The current dated roadmap summary is
  [2026-05-11-validation-to-phase7-roadmap.md](../reports/2026-05-11-validation-to-phase7-roadmap.md).
- This reference can refine literature-derived rationale, but it does not
  silently supersede those contracts or report-owned empirical decisions.

Spec 31 constraints remain binding for all future learned baselines:

- No model may fit or tune on training-log outer-test targets or honest-eval
  targets.
- Any metrics artifact must emit `feature_schema_version` and source DB path.
- Honest-eval top-k is a post-fit diagnostic, not a tuning criterion.
- The comparator gate remains scikit-learn-only with the fixed model names
  `global_mean`, `opponent_mean`, `build_mean`, `twfe_additive`,
  `ridge_hybrid`, and `random_forest`.
- CatBoost, sparse interaction models, model-assisted search, and BoTorch are
  later-plan work after comparator outputs exist.

## Source Table

Status values: `read` means paper text/method/evidence/limits were inspected;
`skimmed` means abstract, intro/conclusion, or survey-level material was
inspected; `metadata-only` means only metadata/abstract-level evidence was
available in this pass.

| Field | Source | Status | Paper claim | Project inference |
|---|---|---:|---|---|
| Grouped validation | [Roberts et al. 2017](https://doi.org/10.1111/ecog.02881) | skimmed | Random CV under structured dependence can underestimate prediction error; block/group CV should match the prediction target. | Outer and inner splits must preserve build/opponent/component/seed/path grouping. |
| Nested model selection | [Varma & Simon 2006](https://doi.org/10.1186/1471-2105-7-91) | read | Tuning on the same CV used for error estimation is optimistically biased. | Hyperparameters must be selected inside each outer training fold. |
| Selection overfit | [Cawley & Talbot 2010](https://jmlr.org/papers/v11/cawley10a.html) | read | Model selection overfits finite validation criteria; evaluate the whole selection procedure. | Reports must define the full pipeline: features, HPO space, budget, seed policy, inner split, and refit rule. |
| CV pitfalls | [Krstajic et al. 2014](https://doi.org/10.1186/1758-2946-6-10) | read | Feature selection and tuning must happen inside CV; repeated/nested CV exposes split variance. | Feature filtering, encoders, transforms, HPO, and calibration are train-fold operations. |
| Effective small samples | [Vabalas et al. 2019](https://doi.org/10.1371/journal.pone.0224365) | read | Limited effective sample size makes validation bias worse. | Row count is not enough; shared builds/opponents/campaigns reduce effective independence. |
| CV variance | [Bengio & Grandvalet 2004](https://jmlr.org/papers/v5/grandvalet04a.html) | skimmed | There is no universal unbiased estimator of k-fold CV variance. | Do not present naive fold SE as clean confidence intervals. |
| HPO best practices | [Bischl et al. 2023](https://doi.org/10.1002/widm.1484) | read | HPO requires explicit spaces, budgets, evaluation protocol, parallelism, and reproducibility. | Every HPO run needs recorded search space, budget, objective, split builder, seeds, pruning policy, and refit rule. |
| Random HPO | [Bergstra & Bengio 2012](https://jmlr.org/papers/v13/bergstra12a.html) | read | Random search is a strong baseline when only some hyperparameters matter. | Start HPO with random/quasi-random search; avoid naive grids. |
| TPE HPO | [Bergstra et al. 2011](https://papers.nips.cc/paper/4443-algorithms-for-hyper-parameter-optimization) | skimmed | TPE helps conditional spaces but is not a validation guarantee. | TPE is allowed only inside nested grouped validation. |
| Conditional SMBO | [Hutter et al. 2011](https://doi.org/10.1007/978-3-642-25566-3_40), [Thornton et al. 2013](https://doi.org/10.1145/2487575.2487629), [Feurer et al. 2015](https://papers.nips.cc/paper/5872-efficient-and-robust-automated-machine-learning) | skimmed | Sequential model-based configuration and AutoML systems natively search conditional choices such as model family, preprocessing, and hyperparameters. | Feature-family enablement, selector choice, model family, and HPO should be one nested grouped selection procedure, not separate manual passes. |
| Tree-structured BO | [Jenatton et al. 2017](https://proceedings.mlr.press/v70/jenatton17a.html) | skimmed | Bayesian optimization can handle tree-structured dependencies. | Coarse-to-fine feature selection is preferable to a flat binary mask over all columns. |
| Multi-fidelity HPO | [Li et al. 2018](https://www.jmlr.org/papers/v18/16-558.html), [Falkner et al. 2018](https://proceedings.mlr.press/v80/falkner18a.html), [Klein et al. 2017](https://proceedings.mlr.press/v54/klein17a.html) | read / skimmed | Resource-aware HPO can promote candidates from cheap to expensive evaluations when fidelities correlate. | Screen feature families cheaply only after measuring whether cheap grouped scores rank candidates similarly to full grouped scores. |
| Tunability | [Probst et al. 2019](https://jmlr.org/papers/v20/18-444.html) | read | Hyperparameters vary greatly in practical importance; defaults and spaces matter. | Keep search spaces small, justified, and model-family-specific. |
| Grouped feature selection | [Yuan & Lin 2006](https://doi.org/10.1111/j.1467-9868.2005.00532.x), [Simon et al. 2013](https://doi.org/10.1080/10618600.2012.681250), [Jenatton et al. 2011](https://www.jmlr.org/papers/v12/jenatton11b.html) | skimmed | Group, sparse-group, and structured sparsity select coherent feature groups and within-group columns. | Select by feature family/template first; individual columns are secondary evidence. |
| Stability and interaction heredity | [Meinshausen & Buhlmann 2010](https://doi.org/10.1111/j.1467-9868.2010.00740.x), [Bien et al. 2013](https://pubmed.ncbi.nlm.nih.gov/26257447/) | skimmed | Stable selection across resamples and heredity constraints reduce fragile interaction discovery. | Explicit interaction features need stable parent families; selected-family frequency is a required diagnostic. |
| Leakage | [Kaufman et al. 2012](https://doi.org/10.1145/2382577.2382579) | skimmed | Leakage is illegitimate target information at prediction time. | No target-derived statistics from outer test or honest eval; every join needs provenance. |
| ML science leakage | [Kapoor & Narayanan 2023](https://doi.org/10.1016/j.patter.2023.100804) | skimmed | Leakage is common in ML science; explicit model information sheets help auditing. | Add a Phase 7 leakage/model card to experiment artifacts. |
| Reusable holdout | [Dwork et al. 2015](https://doi.org/10.1126/science.aaa9375) | skimmed | Adaptive repeated holdout use can overfit the holdout. | If honest-eval targets guide design changes, later final claims need fresh honest eval or exploratory labeling. |
| Forecast calibration | [Gneiting et al. 2007](https://doi.org/10.1111/j.1467-9868.2007.00587.x) | skimmed | Probabilistic forecasts should be sharp subject to calibration. | Only claim uncertainty when proper scoring and grouped calibration diagnostics exist. |
| Regression calibration | [Kuleshov et al. 2018](https://proceedings.mlr.press/v80/kuleshov18a.html) | read | Post-hoc CDF recalibration can improve regression uncertainty. | Calibration needs an inner training-log calibration fold, not outer or honest-eval targets. |
| Conformal regression | [Lei et al. 2018](https://doi.org/10.1080/01621459.2017.1307116), [Romano et al. 2019](https://papers.nips.cc/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html) | skimmed | Split conformal and CQR provide marginal coverage under exchangeability. | Report group-shift failures; do not infer top-k reliability from marginal coverage. |
| Quantile forests | [Meinshausen 2006](https://jmlr.org/papers/v7/meinshausen06a.html) | read | Forests can estimate conditional quantiles. | Practical uncertainty baseline if tree models remain strong. |
| Tabular benchmarks | [Grinsztajn et al. 2022](https://proceedings.neurips.cc/paper_files/paper/2022/hash/0378c7692da36807bdec87ab043cdadc-Abstract-Datasets_and_Benchmarks.html) | read | Tree ensembles remain strong on medium tabular data; neural nets often struggle with irregular functions and irrelevant features. | Tree ensembles are mandatory learned baselines before neural models. |
| Deep tabular survey | [Borisov et al. 2024](https://doi.org/10.1109/TNNLS.2022.3229161) | skimmed | Deep tabular learning remains difficult; GBDT often remains competitive. | Do not assume transformers or deep tabular nets win without local evidence. |
| Ordered categorical boosting | [Prokhorenkova et al. 2018](https://papers.neurips.cc/paper/7898-catboost-unbiased-boosting-with-categorical-features) | read | Ordered boosting and categorical statistics reduce prediction shift and target leakage. | CatBoost is a strong candidate, but still needs grouped nested validation. |
| Scalable boosting | [Chen & Guestrin 2016](https://doi.org/10.1145/2939672.2939785), [Ke et al. 2017](https://proceedings.neurips.cc/paper_files/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html) | skimmed | XGBoost and LightGBM provide scalable tree boosting for sparse/high-dimensional data. | Worth considering when one-hot feature width grows; categorical semantics still need care. |
| Deep tabular baselines | [Gorishniy et al. 2021](https://arxiv.org/abs/2106.11959) | read | Strong ResNet and FT-Transformer baselines are competitive among neural tabular models. | If neural tabular models are tested, use strong baselines rather than exotic first choices. |
| Tabular foundation model | [Hollmann et al. 2025](https://doi.org/10.1038/s41586-024-08328-6) | skimmed | TabPFN v2 is strong for many small tabular tasks. | Plausible benchmark only if schema size, categorical cardinality, and grouped transfer are compatible. |
| Sparse interactions | [Rendle 2010](https://doi.org/10.1109/ICDM.2010.127) | read | Factorization machines model pairwise sparse interactions efficiently. | Direct candidate for hullmod, slot, weapon, opponent interaction features. |
| Field interactions | [Juan et al. 2016](https://recsys.acm.org/recsys16/accepted-contributions/) | skimmed | Field-aware embeddings can improve sparse interaction models. | Natural mapping to slot, weapon, hullmod, opponent, and hull fields; overfit risk must be tested. |
| Hybrid interaction nets | [Guo et al. 2017](https://doi.org/10.24963/ijcai.2017/239), [Song et al. 2019](https://doi.org/10.1145/3357384.3357925) | skimmed / metadata-only | DeepFM and AutoInt model higher-order sparse interactions. | Defer until lower-capacity tree and FM baselines plateau. |
| Contextual recommendation | [Adomavicius et al. 2011](https://doi.org/10.1609/aimag.v32i3.2364) | skimmed | Context is part of the recommendation problem. | Matchup modeling must be contextual: build by opponent, not global build strength. |
| Latent factor ranking | [Koren et al. 2009](https://doi.org/10.1109/MC.2009.263), [Rendle et al. 2009](https://mlanthology.org/uai/2009/rendle2009uai-bpr/) | skimmed | Matrix factorization and pairwise ranking objectives model preference/ranking structure. | Useful diagnostics or later ranking losses; not enough for cold-start component counterfactuals alone. |
| Selection bias in recommenders | [Schnabel et al. 2016](https://proceedings.mlr.press/v48/schnabel16.html) | read | Recommendation data are selection-biased; propensities can help when available. | Optimizer logs are policy-selected; use honest eval and future randomized exploration to counter bias. |
| Rating uncertainty | [Herbrich et al. 2007](https://www.microsoft.com/en-us/research/publication/trueskilltm-a-bayesian-skill-rating-system/) | skimmed | Bayesian ratings track skill and uncertainty. | Rating-only models remain diagnostics; they miss transferable loadout features. |
| Set encoders | [Zaheer et al. 2017](https://papers.nips.cc/paper/6931-deep-sets), [Lee et al. 2019](https://proceedings.mlr.press/v97/lee19d.html) | skimmed | Permutation-invariant networks model sets; Set Transformer adds interactions. | Plausible migration path after tabular and sparse interaction baselines. |
| EGO / BO foundations | [Jones et al. 1998](https://doi.org/10.1023/A:1008306431147), [Frazier 2018](https://doi.org/10.1287/educ.2018.0188) | skimmed | BO is for expensive noisy black-box functions but relies on useful uncertainty. | Optimizer integration needs calibrated uncertainty and sample-efficiency evidence. |
| Practical BO and SMBO | [Snoek et al. 2012](https://papers.nips.cc/paper/4522-practical-bayesian-optimization-of-machine-learning-algorithms), [Hutter et al. 2011](https://doi.org/10.1007/978-3-642-25566-3_40) | skimmed / metadata-only | BO/HPO systems need cost-aware, conditional, and categorical handling. | Compare model-assisted search against practical SMBO-style baselines, not only custom GP theory. |
| Hyperband / BOHB | [Li et al. 2018](https://www.jmlr.org/papers/v18/16-558.html), [Falkner et al. 2018](https://proceedings.mlr.press/v80/falkner18a.html) | read | Adaptive allocation can improve HPO efficiency when fidelity budgets are meaningful. | Opponents/replicates can be treated as resources only after rank-reversal risk is measured. |
| Mixed-variable BO | [Ru et al. 2020](https://proceedings.mlr.press/v119/ru20a.html), [Daxberger et al. 2020](https://doi.org/10.24963/ijcai.2020/365), [Nguyen et al. 2020](https://doi.org/10.1609/aaai.v34i04.5971) | read | Bandit+BO and mixed-variable BO are promising for categorical/continuous spaces. | Phase 7 should validate representation quality before acquisition design. |
| Discrete GP kernels | [Garrido-Merchán & Hernández-Lobato 2020](https://doi.org/10.1016/j.neucom.2019.11.004), [Oh et al. 2019](https://arxiv.org/abs/1902.00448) | skimmed | Discrete/categorical BO needs kernels or graph structure that respect discreteness. | Do not treat repaired discrete builds as relaxed continuous vectors without calibration evidence. |
| High-dimensional BO | [Eriksson et al. 2019](https://papers.nips.cc/paper/8788-scalable-global-optimization-via-local-bayesian-optimization), [Eriksson & Jankowiak 2021](https://proceedings.mlr.press/v161/eriksson21a.html), [Kandasamy et al. 2015](https://proceedings.mlr.press/v37/kandasamy15.html) | read / skimmed | Trust regions, sparse priors, and additive structure address high dimension under assumptions. | Test whether sparse/additive assumptions match the matchup features before committing to composed kernels. |
| Best-arm and allocation | [Kaufmann et al. 2016](https://jmlr.csail.mit.edu/beta/papers/v17/kaufman16a.html), [Audibert et al. 2010](https://www.learningtheory.org/colt2010/papers/59Audibert.pdf), [Frazier et al. 2009](https://doi.org/10.1287/ijoc.1080.0314) | read / skimmed | Top-k identification and value-of-information allocation target terminal decision quality. | Simulation allocation should optimize ranking confidence, not just mean surrogate score. |
| Noisy constrained BO / active learning | [Letham et al. 2019](https://doi.org/10.1214/18-BA1110), [Settles 2009](https://minds.wisconsin.edu/handle/1793/60660), [Kirsch et al. 2019](https://papers.nips.cc/paper/8925-batchbald-efficient-and-diverse-batch-acquisition-for-deep-bayesian-active-learning) | skimmed | Noisy BO and active learning select informative expensive evaluations. | Batch cloud proposals should be uncertainty-aware and diverse only after uncertainty is validated. |

## Synthesis

### 1. Validation Before Model Choice

The established validation literature argues against choosing model families by
outer split performance after the fact. The Phase 7 learned-baseline workflow
must evaluate a complete model-development procedure:

```text
outer grouped split
  -> fit preprocessing/feature filters on outer train only
  -> inner grouped validation for HPO/model selection/calibration
  -> refit selected procedure on eligible outer train rows
  -> score untouched outer test rows
  -> apply final post-fit diagnostic to honest-eval rows
```

The grouping unit must match the question. Held-out build, opponent,
component-combination, seed/cell, and path-ordered splits answer different
questions; they should not be collapsed into one averaged score. Held-out
opponent remains the primary stress test because the current comparator report
shows it is the weakest transfer surface.

### 2. HPO Is Part Of The Estimator

The HPO literature treats hyperparameter search as part of the learning
algorithm. A report must therefore charge every model for its tuning budget and
record:

- search space and defaults;
- search algorithm and trial budget;
- objective metric and tie-breakers;
- inner split builder and grouping unit;
- random seeds;
- pruning or early-stopping rule;
- selected hyperparameters;
- final refit rule;
- wall-clock and compute budget.

Random or quasi-random search is the first HPO baseline. TPE or other
sequential HPO is allowed only inside the inner grouped validation loop. A
manual one-off tuning pass is not acceptable evidence for promotion.

### 3. Feature Selection Is Part Of The Estimator

Feature selection has the same leakage risk as hyperparameter tuning. A
feature set chosen after looking at outer-test opponents is part of the fitted
estimator and must be charged to the same nested grouped validation procedure.
The correct claim-bearing unit is therefore:

```text
feature generator
  + feature-family selector
  + preprocessing
  + model family
  + hyperparameter search
  + calibration rule
```

Bayesian optimization and other SMBO methods can select features, but the
search space should be hierarchical and conditional rather than a flat binary
mask over every materialized column. A practical hierarchy is:

```text
feature family enabled?
  -> subgroup/profile enabled?
     -> selector type?
        -> selector hyperparameters?
           -> individual top-k/threshold/mask only for small promoted pools
```

This matches the feature structure better than per-column masks. Candidate
families include hull/platform stats, mobility, flux economy, armor/shield
defense, weapon pressure by damage/range/arc, slot geometry, hullmod
descriptors, wing pressure for stock opponents, explicit matchup counters,
sparse component IDs, and regime/archetype descriptors.

The default selection protocol is:

1. Define a feature-family registry before the run. Every generated column
   gets `family`, `template`, `parents`, and `leakage_risk` metadata.
2. Run coarse family/profile screening inside each outer-training fold only.
3. Use grouped inner validation to choose selector type, model family, and
   hyperparameters jointly.
4. Promote only finalists to full-budget tuning.
5. Refit the selected full pipeline on the outer-training fold and score the
   untouched outer-test fold once.

Multi-objective selection is allowed when the objectives are declared before
the run. Useful objectives are grouped loss, ranking quality, calibration,
feature count, family count, train time, inference time, selected-family
stability, and robustness across opponent hierarchy levels. Any sparsity or
compute penalty is a modeling choice and must be reported as such.

Multi-fidelity screening is allowed only as a compute-saving device. Before it
becomes a gate, the experiment should estimate whether cheap fidelities such
as fewer rows, fewer groups, fewer folds, or fewer trees preserve the ranking
of candidate feature/model configurations under the full grouped protocol.
Cheap-fidelity pruning must never inspect outer-test or honest-eval targets.

### 4. Hierarchy Defines The Generalization Claim

There are three separate hierarchies and they should not be collapsed:

- **Opponent hierarchy**: faction or doctrine, hull class, hull ID,
  variant/loadout archetype, exact opponent build, combat replicate.
- **Player-build hierarchy**: role or doctrine, hull, slot geometry, weapon
  family, mounted weapon, hullmod set, exact repaired build.
- **Feature hierarchy**: combat subsystem, feature family, generator template,
  individual column.

The split boundary defines the supported claim. Random-row splits are smoke
tests only. The Phase 7 scorecard should report a ladder:

- replicate holdout for simulator noise only;
- exact-opponent holdout for opponent-ID memorization;
- hull holdout for hull-specific transfer;
- hull-family or archetype-cluster holdout for harder opponent transfer;
- campaign/regime or forward-time holdout when deployment claims cross those
  boundaries.

Hierarchy reduces leakage only when every preprocessing, clustering, feature
selection, HPO, calibration, and target transform is nested inside the same
hierarchical boundary. An archetype cluster learned from all rows before
splitting can leak just as badly as a target-derived aggregate. If archetypes
are used as validation groups or model features, they must be computed from
manifest/loadout descriptors without outcome labels, or fit inside the
training fold when learned.

Required hierarchy diagnostics for future Phase 7 reports:

- split unit and claim supported by that split;
- forbidden cross-split keys for the chosen hierarchy level;
- train/test overlap counts for opponent ID, hull ID, family/archetype, and
  exact matchup groups;
- adversarial-validation AUC for distinguishing train from test at each
  hierarchy level;
- rare feature-combination overlap and nearest-neighbor summaries;
- ablation without sparse categorical/fingerprint features.

### 5. Regularization By Model Family

Regularization should match the model's capacity and the leakage surface. The
shared rule is that all regularization strengths and early-stopping decisions
are selected inside grouped inner validation.

**Linear and generalized linear models.** Use ridge as a dense baseline,
elastic net for sparse individual columns, group lasso for whole feature
families, and sparse group lasso when a family is useful but only some columns
within it are stable. Report both selected-feature frequency and
selected-family frequency. Family-level stability can justify keeping a
descriptor group even when individual correlated columns swap across folds.

**Explicit interaction models.** Factorization machines, field-aware
factorization machines, and hand-authored counter interactions need capacity
limits: interaction rank, field grouping, L2 penalties on latent factors,
dropout or feature subsampling where available, and heredity constraints.
Explicit interaction families should be retained only when their parent
main-effect families are also stable under grouped resampling, unless a
pre-registered domain rule justifies the exception. For vanilla FM/FFM models,
heredity is a diagnostic and reporting lens rather than a native constraint
unless the implementation uses a hierarchical sparse interaction variant.

**Tree ensembles and boosted trees.** Use depth, minimum leaf size,
subsampling, column subsampling, learning rate, number of trees/rounds,
categorical-smoothing controls, monotone or interaction constraints only when
the domain rule is justified, and early stopping on inner grouped validation.
Tree feature importances are diagnostics, not selection proof; use grouped
permutation or block-permutation importance by feature family.

**Gaussian-process and kernel models.** Use shrinkage priors or additive
structure for high-dimensional feature blocks, separate kernels by feature
family only when the decomposition is declared before tuning, and constrain
kernel choices through nested validation. Sparse-axis-aligned priors and
multiple-kernel weights are regularizers; they should not be interpreted as
feature truth without stability diagnostics.

**Neural tabular, set, and token models.** Defer these until lower-capacity
baselines plateau, but define their regularization requirements now:
embedding-dimension caps, weight decay, dropout, early stopping, token/set
pooling constraints, small MLP heads, categorical embedding sharing by entity
type, and manifest-derived numeric side features so rare components are not
pure IDs. The first neural representation should be hierarchical Deep Sets
over weapon and hullmod tokens with slot-aware pooling; Set Transformer,
cross-attention, and graph networks require stronger evidence because they can
memorize sparse component combinations.

**Graph and relational models.** If graph models are later justified, regularize
by limiting relation types, message-passing depth, hidden width, edge dropout,
node/edge feature dropout, weight decay, and pooling hierarchy. Prefer typed
relations that correspond to known game structure (`mounted_in`, `same_arc`,
`range_compatible`, `counters_defense`) over arbitrary learned dense edges.

**Calibration and uncertainty models.** Calibration layers, conformal scores,
quantile heads, and uncertainty ensembles need their own inner calibration
folds. Coverage should be reported by hierarchy level and score regime; a
globally calibrated interval under row-random splits is not evidence for
held-out-opponent allocation.

### 6. Candidate Families Are Literature-Derived, Not Preselected

The literature supports a staged candidate set, but not a predeclared winner.

**Implement now, pending an experiment plan:**

- Tree-ensemble baselines for mixed tabular data, including the current random
  forest comparator and a serious categorical/boosted-tree candidate if its
  dependency and feature representation are compatible.
- Sparse interaction models, especially factorization-machine-style models,
  because the build representation has sparse hullmod, slot, weapon, and
  opponent fields.

**Needs experiment or deeper reading before implementation:**

- Direct ranking losses such as BPR or LambdaMART if regression metrics fail to
  align with top-k recall.
- Quantile forests or conformalized quantile models if uncertainty is needed
  for allocation.
- LightGBM/XGBoost-style boosted trees if the feature table becomes too wide
  for categorical-native boosting or if sparse one-hot throughput dominates.

**Defer:**

- Deep tabular transformers, TabPFN, DeepFM/AutoInt, Deep Sets, Set
  Transformers, and graph encoders until tree and sparse-interaction baselines
  plateau under grouped validation.
- Propensity-weighted or counterfactual learning until optimizer proposal
  propensities or randomized exploration logs are available.
- Rating-only, TWFE-only, matrix-factorization-only, or TrueSkill-only models
  as final surrogates. They remain diagnostics because they cannot explain
  transferable component counterfactuals.

### 7. Calibration And Uncertainty Are Second-Stage

Point prediction and ranking must pass grouped validation first. Then
uncertainty can be added with an inner calibration split and proper scoring or
coverage diagnostics. Marginal conformal coverage under exchangeability does
not imply reliable top-k selection under held-out opponents or campaign shift,
so coverage must be stratified by split, opponent group, and score regime.

### 8. Optimizer Integration Waits

The optimization literature supports surrogate-assisted expensive black-box
search only when the surrogate improves sample efficiency and its uncertainty
is useful for allocation. Phase 7 should not move directly from a better
offline model to a custom BoTorch optimizer. The next optimizer-facing gates
are:

- offline grouped validation beats the comparator ladder;
- honest-eval top-k diagnostic improves without tuning on honest-eval targets;
- any model/feature/promotion decision influenced by prior honest-eval
  diagnostics is labeled exploratory unless confirmed on a fresh honest-eval
  ledger;
- uncertainty or ranking confidence is calibrated enough for allocation;
- an online or replay-style allocation ablation beats current TPE/random/SMBO
  style baselines on simulation budget or wall-clock efficiency.

## Evidence Matrix

| Claim | Supporting sources | Limiting sources / caveats | Confidence | Design consequence |
|---|---|---|---:|---|
| Random row splits are invalid headline evidence for Phase 7. | Roberts 2017; Krstajic 2014; spec 31 | Grouped splits answer different questions and cannot be averaged naively. | High | Keep grouped outer splits and matching inner splits. |
| HPO must be nested inside outer evaluation. | Varma & Simon 2006; Cawley & Talbot 2010; Bischl 2023 | Full nested repeated CV can be expensive. | High | Use budgeted inner grouped HPO and record the budget. |
| Feature selection must be nested inside outer evaluation. | Varma & Simon 2006; Krstajic 2014; Cawley & Talbot 2010; group-selection literature | Full nested selection can be expensive; multi-fidelity gates require validation. | High | Treat feature generator, selector, model choice, and HPO as one fitted pipeline. |
| Hierarchical feature-family selection is preferable to flat masks. | TPE/SMBO; tree-structured BO; group/sparse-group lasso | Bad groups can hide useful features or preserve leakage. | Medium-high | Maintain a feature-family registry and report family-level stability. |
| Interaction features require heredity and stability checks. | Bien 2013; structured sparsity literature | Some true interactions may appear before weak main effects under finite data. | Medium | Keep explicit interactions only when parent families are stable or a domain rule pre-registers the exception. |
| Holdout hierarchy defines the empirical claim. | Roberts 2017; Kapoor & Narayanan 2023; grouped validation literature | Harder hierarchy levels may have high variance with few groups. | High | Publish a split ladder instead of one averaged transfer score. |
| Honest-eval targets must remain post-fit diagnostics. | Dwork 2015; leakage literature; spec 31 | Adaptive use may be acceptable only if labeled exploratory or followed by fresh honest eval. | High | Do not tune features, model choice, hyperparameters, or calibration on honest-eval targets. |
| Model-family winners require a predeclared or nested selection rule. | Cawley & Talbot 2010; Varma & Simon 2006 | Reporting a full model matrix is still useful as exploratory evidence. | High | State primary endpoint/split/k and whether model-family choice is fixed, nested, or exploratory. |
| Tree ensembles are mandatory learned baselines. | Grinsztajn 2022; Borisov 2024; CatBoost/XGBoost/LightGBM papers | They may not capture all sparse component synergies. | High | Include serious tree baselines before neural models. |
| Sparse interaction models fit the build representation. | Rendle 2010; FFM/DeepFM literature | Pairwise or field interactions can underfit high-order effects. | Medium-high | Add FM-style models before deep interaction networks. |
| Calibration is required before allocation claims. | Gneiting 2007; Kuleshov 2018; conformal literature; BO literature | Exchangeability failures under held-out opponent can break marginal guarantees. | High | Report coverage/proper scores by grouped split before active allocation. |
| Optimizer integration should be staged. | EGO/BO foundations; BOHB/Hyperband; mixed-variable BO; KG/OCBA | Offline ranking gains may not translate to online budget savings. | High | Require replay or online allocation ablation before replacing the sampler. |
| Neural set/graph models are plausible but not first-line. | Deep Sets; Set Transformer; tabular surveys | Data volume/effective sample size may be insufficient. | Medium | Defer until lower-capacity baselines plateau. |

## Decision Table

| Decision | Status | Rationale | Gate Before Implementation |
|---|---|---|---|
| Require nested grouped validation for learned baselines | implement now | Strong validation literature and spec 31 leakage constraints align. | Next experiment plan must define outer and inner grouping units. |
| Add feature-family registry and hierarchy metadata | implement now | Feature selection, block importance, leakage checks, and hierarchical diagnostics need stable family/template/parent labels. | Extend feature-schema/report artifacts before large feature-selection runs. |
| Use hierarchical feature selection rather than flat per-column masks | implement now | Conditional SMBO and grouped sparsity align with the feature structure and reduce search dimensionality. | Define families, leakage-risk labels, promotion rules, and multi-objective penalties in the next plan. |
| Use random/quasi-random HPO as first tuning baseline | implement now | Random HPO is strong, simple, and auditable; sequential HPO can overfit if not nested. | Declare search space, budget, seeds, objective, and refit rule. |
| Allow TPE/SMBO for feature selection | needs experiment | Literature supports conditional spaces, but it can optimize leakage if the objective is wrong. | Use only inside nested grouped validation with hierarchy diagnostics. |
| Add serious tree-ensemble learned baselines | implement now | Tabular literature supports tree ensembles as first-line for mixed tabular data. | Choose exact package/representation in next plan; compare default vs tuned. |
| Add sparse interaction baseline | implement now | Factorization-machine-style models match sparse slot/weapon/hullmod/opponent interactions. | Define fields, interaction order/rank, regularization, and grouped HPO. |
| Add ranking objective | needs experiment | Regression metrics may not align with top-k selection, but ranking queries must be defined carefully. | First inspect regression-vs-top-k disagreement under frozen protocol. |
| Add uncertainty/calibration | needs experiment | Useful for allocation, but only meaningful after point/ranking signal exists. | Use inner calibration split and grouped coverage/proper-score diagnostics. |
| Add neural tabular, set, or graph models | defer | Literature makes them plausible but not first-line under medium tabular/effective-small data. | Tree and sparse-interaction baselines plateau or expose flattening failure. |
| Add propensity/counterfactual learning | defer | Selection-bias literature is relevant, but proposal propensities are not logged. | Add randomized exploration or proposal-propensity logging first. |
| Integrate model-assisted optimizer | defer | BO/allocation literature requires calibrated uncertainty and sample-efficiency evidence. | Offline grouped validation, honest-eval diagnostic, and allocation ablation pass. |
| Replace sampler with custom BoTorch kernel | defer | Kernel design remains plausible but is downstream of cheaper gates. | Model-assisted search shows value over current TPE/random/SMBO-style baselines. |

## Derived Experiment-Plan Requirements

The next implementation plan should choose exact model families and
hyperparameter spaces from this research, but it must at minimum include:

- a model card / leakage checklist for every run;
- outer grouped split definitions and inner grouped validation definitions;
- a hierarchy scorecard defining the split ladder and supported claim for each
  split;
- a primary endpoint, primary split, primary top-k value, model-promotion rule,
  and exploratory-vs-confirmatory boundary;
- a feature-family registry with `family`, `template`, `parents`, and
  `leakage_risk` metadata for every generated feature;
- a declared feature-selection protocol, including whether selection is
  filter, wrapper, embedded, SMBO/TPE, grouped regularization, or a staged
  combination;
- a declared HPO search space, search algorithm, budget, seeds, and objective;
- regularization spaces for every model family, including interaction rank,
  tree depth/subsampling, embedding dimensions, dropout/weight decay, early
  stopping, and calibration-fold policy where applicable;
- default-vs-tuned comparisons where tuning is used;
- provenance fields: source DB path, feature schema version, code version,
  split seed, HPO seed, model family, chosen hyperparameters, and runtime;
- metrics for RMSE, MAE, rank correlation, honest-eval top-k diagnostic, and
  any calibration/coverage claims;
- stratified diagnostics for held-out opponent, opponent size/designation,
  score regime, and campaign cell;
- a rule that optimizer integration is out of scope until offline validation,
  top-k diagnostics, and allocation evidence all pass.

## Immediate Next Work

Write a new implementation plan for the first learned-baseline experiment
after this research note. That plan should derive candidate models and HPO
spaces explicitly from the sources above, preserve spec 31 leakage/provenance
constraints, and run plan review before code changes.
