---
type: reference
status: shipped
last-validated: 2026-07-11
---

# Phase 7 Surrogate Methodology Gaps — Literature Synthesis

Literature-grounded remedies for the gaps identified by the 2026-07-11
methodology review
([../reports/2026-07-11-phase7-methodology-review.md](../reports/2026-07-11-phase7-methodology-review.md)).
Extends [phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md)
(the original literature-first gate) with five themes it did not cover in
depth: opponent/context representation, low-rank interaction modeling,
adaptive holdout reuse, ranking-aware surrogate validation, and post-SAAS
mixed-space BO. Per the empirical-numbers rule, this doc carries academic
citations only; project measurements live in the owning reports.

## 1. Opponent/context representation

The review found opponent transfer is the binding constraint and that it is
jointly a representation and an effective-sample-size problem. Relevant
literature:

- **Contextual GP bandits** (Krause &amp; Ong, NIPS 2011): optimize f(x, c)
  with a product kernel k_build · k_opponent; context handling is purely
  kernel design, and aggregate fitness is the posterior integrated over the
  opponent distribution — natively supported in BoTorch. This is the correct
  formal frame for the Phase 7 GP: opponents enter as kernel context, not as
  separate models or pre-averaged targets.
- **Multi-task GPs / ICM** (Bonilla et al., NIPS 2008; Swersky et al., NIPS
  2013; LMC review: Álvarez et al., arXiv:1106.6251): a learned low-rank task
  covariance over opponents = a learned opponent embedding from residual
  correlations. Caveat: free-form ICM only interpolates *seen* opponents;
  unseen-family generalization requires the task kernel be parameterized by
  opponent **features**, with the low-rank part as residual —
  K_task(c,c′) = k_feat(c,c′) + low-rank.
- **Transfer-BO survey** (Bai et al., arXiv:2302.05927; RGPE: Feurer et al.,
  arXiv:1802.02219; FSBO: arXiv:2101.07667): per-task scale mismatch is the
  dominant failure of naive pooling; methods transferring rankings or
  quantiles beat those transferring raw values. Maps directly to
  per-opponent score-scale heterogeneity.
- **Blade-chest matchup models** (Chen &amp; Joachims, WSDM/KDD 2016): a
  low-rank bilinear term u(x)ᵀ·W·v(c) is the smallest model class that
  represents intransitive matchup cycles (kiting &gt; brawler &gt; artillery &gt;
  kiting); Elo/Bradley-Terry-style scalar strength is the rank-1 special case
  with an irreducible error floor under intransitivity. Ship combat is
  intransitive by construction, so any scalar build-strength latent is
  structurally insufficient.
- **Hearthstone winrate prediction** (AAIA'18 challenge; Grad, FedCSIS 2018):
  the closest published analogue to our data regime (grouped build holdout,
  bot-vs-bot outcomes). Findings: aggregate hand-crafted features + GBM beat
  raw indicator models; embeddings pre-trained on cheap auxiliary tasks
  transfer to the scarce supervised task; **archetype-cluster-mean is a
  mandatory baseline** before claiming any learned opponent representation
  helps.
- **TrueSkill 2** (Minka et al., 2018) / **OptMatch** (KDD 2020): latent
  ratings and observable features are complements. Pattern for us: fit
  build/opponent main effects with shrinkage (the existing TWFE + EB
  machinery), then train interaction models on residuals so interaction
  capacity is not spent on main effects.

## 2. Sparse high-cardinality interaction modeling

The review found the sparse-interaction verdict attached to an unscaled
count-sketch ridge, not to interaction modeling itself.

- **Factorization machines** (Rendle, ICDM 2010): pairwise weights
  w_ij = ⟨v_i, v_j⟩ estimate interactions between features that never
  co-occur in training via shared factors — precisely what tree models cannot
  do (a tree must observe a cell to split on it). At our N (tens of
  thousands of rows), plain FM with small rank and strong L2 is the right
  point on the ladder; FFM (RecSys 2016) and DeepFM (arXiv:1703.04247) are
  overparameterized for this regime. Best used as a **feature factory**:
  export latent factors and the FM interaction score as dense GBDT features.
  FM ≡ blade-chest ≡ ICM low-rank bilinear — three literatures converge on
  the same inductive bias, which is strong evidence it is the right one.
- **Entity embeddings** (Guo &amp; Berkhahn, arXiv:1604.06737): learned
  categorical embeddings exported to GBMs beat one-hot under sparsity; weapon
  and hullmod embeddings learned this way later seed the GP categorical
  kernel (Matérn over embedding space as an alternative to
  transformed-overlap).
- **Regularized/GLMM target encoding** (Pargent et al., arXiv:2104.00629;
  nuanced by arXiv:2307.09191): cross-fitted GLMM encoding dominates one-hot
  and hashing for high-cardinality categoricals, and its random-effect
  shrinkage is the same mathematics as the Phase 5D/5E EB machinery. Hard
  constraint: all target-style encodings must be cross-fitted **within the
  grouped-CV structure** (folds by build/opponent, not rows) or the encoder
  itself leaks holdout group means. CatBoost's ordered target statistics
  (arXiv:1706.09516) already handle this internally for its native
  categoricals.
- **Trees vs deep tabular** (Grinsztajn et al., arXiv:2207.08815): at our
  row counts, tuned GBTs beat deep models; the fix for weak interactions is
  injecting low-rank bilinear structure into the feature space, not replacing
  CatBoost.

## 3. Model selection under grouped data and dataset reuse

- **Leakage taxonomy** (Kapoor &amp; Narayanan, Patterns 2023,
  arXiv:2207.07048): of the eight types, the live risks here are group
  non-independence + preprocessing fit outside folds, and
  test-distribution ≠ deployment-distribution (the surrogate deploys on
  optimizer-shifted proposals). Adopt a model-info sheet per evidence wave.
- **Reusable holdout / adaptive data analysis** (Dwork et al., Science 2015,
  arXiv:1506.02629; Ladder: Blum &amp; Hardt, arXiv:1502.04585): repeated
  evidence waves against one dataset overfit the holdout through the
  analyst. Operational adoptions: (i) **Ladder discipline** — a candidate
  only replaces the incumbent if it wins by a pre-registered margin derived
  from between-fold spread; (ii) a **lockbox** of held-back opponent families
  opened once per phase gate; (iii) since sim data is manufacturable, final
  claims confirm on **fresh simulations** — the one setting where adaptive
  reuse has a free exact cure (this aligns with the honest-evaluation skill).
- **Block CV** (Roberts et al., Ecography 2017): random-row CV estimates
  interpolation, grouped CV estimates extrapolation; report the split ladder
  and its gaps as first-class evidence. **Nested CV** (Varma &amp; Simon 2006)
  matters for the reported number, less for the choice (Wainer &amp; Cawley,
  arXiv:1809.09446). With few groups prefer repeated grouped k-fold over
  leave-one-group-out (LOO distributional bias: arXiv:2406.01652). For
  cross-model comparisons across few groups use rank tests (Demšar, JMLR
  2006) and variance accounting (Bouthillier et al., arXiv:2103.03098).

## 4. Ranking-aware surrogate training and evaluation

The NAS performance-predictor literature is the closest methodological
neighbor to "surrogate whose only job is steering search":

- **Predictor evaluation standards** (White et al., arXiv:2104.01177):
  Kendall τ, **sparse Kendall τ** (only pairs separated beyond noise),
  precision@k, regret@k; simple GBM predictors are Pareto-optimal at small
  budgets; cheap-proxy + learned-predictor combinations win — our
  `heuristic_score()` is exactly such a zero-cost proxy.
- **Surrogate-benchmark validation protocol** (NAS-Bench-301, arXiv:2008.09777):
  tail-stratified rank metrics (the optimizer only exploits the top decile;
  global τ can be excellent while tail τ is chance) and trajectory-based
  holdouts (rows from different search algorithms) to measure
  deployment-distribution robustness.
- **Comparison-based surrogates** (BRP-NAS, arXiv:2007.08668; ACM-ES,
  Loshchilov et al., PPSN 2010): train on pairwise "A beats B" relations.
  For us, **within-opponent pairing cancels opponent main effects exactly**
  (the TWFE opponent term drops out of every pair) and makes score-scale
  transforms moot — CatBoost PairLogit/YetiRank with groups = opponent is a
  drop-in.
- **Ranking/quantile surrogates for BO** (Deep Ranking Ensembles,
  arXiv:2303.15212; Gaussian-copula quantile transfer, Salinas et al.,
  arXiv:1909.13595): a per-opponent copula/quantile transform is a
  principled, hyperparameter-free alternative to a global Box-Cox for
  opponent-scale heterogeneity.
- **Likelihood-free BO** (Song et al., arXiv:2206.13035): acquisition
  functions reduce to weighted classification ("in the top-γ quantile?"),
  solvable by any classifier including GBTs — a CatBoost classifier is a
  valid BO acquisition. This is the fallback Phase 7 architecture requiring
  zero new machinery, and the baseline any GP must beat.

## 5. Mixed-space GP BO, post-SAAS state of the art

Updates to [phase7-search-space-compression.md](phase7-search-space-compression.md)'s
kernel plan:

- **D-scaled vanilla BO** (Hvarfner et al., ICML 2024, arXiv:2402.02229):
  dimension-scaled lengthscale priors make vanilla GPs match or beat SAASBO
  and TuRBO on high-dim suites at trivial cost; BoTorch's default prior since
  0.12. **The Phase 7 GP baseline should be the D-scaled vanilla mixed GP,
  with SAAS-MCMC added only if small-budget performance demands it** — this
  reverses the emphasis in the current compression doc. (Independent
  corroboration: arXiv:2303.00890.)
- **Casmopolitan** (arXiv:2102.07188): source of the planned
  transformed-overlap kernel; contributes the missing piece — trust-region
  management with Hamming-ball local search for the categorical acquisition
  step, which is the practical failure point in large mixed spaces.
- **Bounce** (arXiv:2307.00618) and **BODi** (arXiv:2303.01774): nested
  subspace embeddings (reliability-first) and dictionary Hamming embeddings
  respectively; alternatives if per-slot composed kernels struggle.
- **MCBO benchmark** (arXiv:2306.09803): implements the above behind one API;
  headline finding is that no surrogate/acquisition composition dominates and
  trust regions matter more than kernel refinements. The right harness for a
  project bake-off.
- **Foundation-model surrogates** (PFNs4BO, arXiv:2305.17535; GIT-BO,
  arXiv:2505.20685) and **LLM warmstarts** (LLAMBO, arXiv:2402.03921):
  constant-time surrogate refits and early-trial priors; additive options,
  not replacements.
- **When GPs beat tree surrogates** (BBO-2020 analysis, arXiv:2104.10201;
  SMAC3, JMLR 2022; Optuna GPSampler): GPs win at small budgets on modest
  dimensionality; RF/GBT surrogates remain competitive on large conditional
  categorical spaces. The literature does **not** promise the composed-kernel
  GP wins at our budget/dimensionality — it wins if the space is compressed
  to where GP uncertainty is calibrated. This confirms the planned ordering
  (compression first, GP second) and mandates tree-based acquisition
  baselines in the same harness.

## 6. Ranked adoption shortlist

1. **Within-opponent pairwise-ranking surrogate + rank-fidelity evaluation
   suite** (per-opponent τ, sparse τ with noise-floor ties, top-decile τ,
   precision@k, regret@k). Highest value-to-effort; fixes the review's C1
   directly.
2. **FM / blade-chest low-rank bilinear features fed to CatBoost**, trained
   on residuals after TWFE main effects; opponent embeddings double as future
   GP kernel coordinates. Fixes H4; attacks H5's representation half.
3. **Split ladder + lockbox + Ladder-margin discipline + per-wave model-info
   sheet**, with all fitted preprocessing cross-fitted inside grouped folds.
   Fixes C4; cheap.
4. **Contextual-kernel GP**: K_build ⊗ (K_opp-feat + low-rank residual), on
   D-scaled vanilla priors first, Casmopolitan-style trust regions for
   acquisition; aggregate-fitness acquisition over the opponent distribution.
5. **Pre-registered offline bake-off in the MCBO harness** (GP vs Bounce vs
   BODi vs LFBO-CatBoost vs SMAC-RF vs incumbent TPE), replaying trajectories
   against the existing matchup dataset as oracle, scored by regret@k and
   tail rank fidelity — converts the Phase 7 architecture decision into a
   compute experiment on data already in hand.

## Full source list

Krause &amp; Ong NIPS 2011; Bonilla et al. NIPS 2008; Swersky et al. NIPS 2013;
Álvarez et al. arXiv:1106.6251; Bai et al. arXiv:2302.05927; Feurer et al.
arXiv:1802.02219; Wistuba &amp; Grabocka arXiv:2101.07667; Chen &amp; Joachims
WSDM/KDD 2016 (blade-chest); AAIA'18 Hearthstone winrate challenge; Grad
FedCSIS 2018; Minka et al. TrueSkill 2 (MSR 2018); Gong et al. OptMatch KDD
2020; Rendle ICDM 2010; Juan et al. RecSys 2016; Guo et al. arXiv:1703.04247;
Guo &amp; Berkhahn arXiv:1604.06737; Weinberger et al. arXiv:0902.2206; Pargent
et al. arXiv:2104.00629; Matteucci et al. arXiv:2307.09191; Prokhorenkova et
al. arXiv:1706.09516; Grinsztajn et al. arXiv:2207.08815; Kapoor &amp;
Narayanan arXiv:2207.07048; Dwork et al. arXiv:1506.02629 / Science 2015;
Blum &amp; Hardt arXiv:1502.04585; Roberts et al. Ecography 2017; Varma &amp;
Simon BMC Bioinformatics 2006; Wainer &amp; Cawley arXiv:1809.09446;
arXiv:2406.01652 (LOO distributional bias); Demšar JMLR 2006; Bouthillier et
al. arXiv:2103.03098; White et al. arXiv:2104.01177; Zela et al.
arXiv:2008.09777; Dudziak et al. arXiv:2007.08668; Loshchilov et al. PPSN
2010; Khazi et al. arXiv:2303.15212; Salinas et al. arXiv:1909.13595; Song et
al. arXiv:2206.13035; Eriksson &amp; Jankowiak arXiv:2103.00349; Eriksson et
al. arXiv:1910.01739; Hvarfner et al. arXiv:2402.02229; Santoni et al.
arXiv:2303.00890; Wan et al. arXiv:2102.07188; Papenmeier et al.
arXiv:2307.00618; Deshwal et al. arXiv:2303.01774; Dreczkowski et al.
arXiv:2306.09803; Müller et al. arXiv:2305.17535; Yu et al. arXiv:2505.20685;
Liu et al. arXiv:2402.03921; Turner et al. arXiv:2104.10201; Lindauer et al.
JMLR 2022 (SMAC3).
