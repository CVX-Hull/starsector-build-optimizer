---
type: reference
status: shipped
last-validated: unvalidated
---

# Phase 5E — A3 Shape Revision

> **Status**: Implemented 2026-04-18. Original validation showed ceiling collapse and top-k overlap improvements on V1 synthetic re-validation; specific magnitudes are pending re-validation under V2. See [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) and [../reports/INDEX.md](../reports/INDEX.md).
>
> **Empirical-claims status (2026-05-10):** Every numeric strategy table (the 11-strategy validation grid in §3.1, the 7-strategy 5D-revalidation grid in §3.4, the calibration sweep) used V1 sim data. Theory (Box-Cox monotonicity preserves Spearman ρ; ceiling-saturation mechanism), and the ranking of strategies (Box-Cox dominates the rank-shape baseline; Tobit and CFS underperform in the validated regime) are design-grade and unaffected.

Findings from the V1 Hammerhead TWFE run that motivate a revision to the A3 fitness-shaping step of the Phase 5A signal-quality pipeline.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer. The shipped pipeline is A1 TWFE decomposition → A2 single-channel control variate → A3 rank-shape-with-top-quartile-clamp. Phase 5E replaces A3. Phase 5D ([phase5d-covariate-adjustment.md](phase5d-covariate-adjustment.md)) separately replaces A2 with empirical-Bayes shrinkage of α̂ toward a heuristic-predicted regression prior; 5D and 5E are orthogonal and compose (5E reads α̂_EBT from 5D). See [implementation-roadmap.md](implementation-roadmap.md) for the full Phase 5 overview and [phase5a-deconfounding-theory.md](phase5a-deconfounding-theory.md) for the TWFE foundation this doc builds on.

---

## 1. What went wrong on the Hammerhead run

The V1 Hammerhead optimizer run against the 10-active-opponent pool exhibited three qualitative pathologies (specific counts and percentages pending re-validation under V2 — see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md)):

- A1: TWFE additive decomposition — *working correctly*
- A2: control variate on TWFE α — *working correctly*
- A3: quantile rank with top-quartile clamp at 1.0 — **the problem**

| Pathology | Observation |
|---|---|
| **Exploit-cluster convergence** | The large majority of completed builds share at least one of `shrouded_lens`, `shrouded_mantle`, `fragment_coordinator`, `neural_integrator` — hullmods whose in-game acquisition requires specific endgame faction content. These add passive AoE damage and damage-recoil effects independent of flux state, so builds running `vents=0, caps=1` still achieve full wins. |
| **A3 ceiling saturation** | A non-trivial subset of builds map to `fitness = 1.000` despite raw TWFE α spanning a much narrower range than the theoretical max. The top-quartile clamp destroys any gradient among the top 25%. |
| **Opponent pool ceiling** | Peer Hammerhead variants can force timeouts but not kills. The stronger an exploit build, the more opponents saturate at `hp_differential ≈ 1.0`, so raw matchup scores censor at the fitness tier boundary. |

Bottom line: **the A3 rank shape discards precisely the gradient the TPE sampler needs to distinguish the exploit-cluster winners from one another**. A1 and A2 are not the bottleneck.

### The search-space question (bitter lesson)

The obvious patch is "filter out hullmods whose CSV tags include `no_drop`, `no_drop_salvage`, `codex_unlockable`, or `hide_in_codex`." We reject this as a bitter-lesson violation (Sutton 2019):

> Methods leveraging computation scale better than methods leveraging human knowledge.

The bitter-lesson framing rejects a *silent hard-coded filter* of rare-faction hullmods. Two distinct, non-silent follow-ups are on the roadmap: **Phase 5F** (regime-segmented optimization — user explicitly opts into a progression tier; `search_space.py` masks components per the user's chosen regime; framed as CMDP feasibility alignment rather than human-knowledge injection — see [phase5f-regime-segmented-optimization.md](phase5f-regime-segmented-optimization.md) §2.1 and §4.6 for why this is not a bitter-lesson violation), and **Phase 5G** (adversarial opponent curriculum — grow the opponent pool until the signal itself exposes exploits; research continues in §2.1 below).

---

## 2. Literature synthesis (three parallel surveys)

Three independent literature surveys were run against the three distinct failure modes. Citations are concrete so this doc stands alone.

### 2.1 Adversarial curriculum / co-evolution

Attacks the **opponent pool ceiling**. Core ideas:

- **Competitive Fitness Sharing** (Rosin & Belew 1997, *Evolutionary Computation* 5(1)): weight a win against opponent *o* by `1 / (1 + n_beat[o])`, so beating a trivial opponent everyone beats contributes little; beating a rare tough opponent contributes a lot. One-line change.
- **PSRO / main-exploiter loop** (Lanctot et al. 2017, NeurIPS, arXiv:1711.00832; Vinyals et al. 2019, *Nature* for AlphaStar league). Dedicate a parallel optimizer run to maximizing `−win_rate(incumbents)`; promote its output into the opponent pool.
- **POET minimum criterion** (Wang et al. 2019, arXiv:1901.01753): keep an opponent only if current population win-rate falls in a Goldilocks band (e.g. 30–70%). Retires freighters; retires unreachable peers.
- **Prioritized Level Replay + ACCEL** (Jiang et al. 2021, arXiv:2010.03934; Parker-Holder et al. 2022, arXiv:2203.01302): sample opponents proportional to estimated regret.

### 2.2 Censored / ceiling-saturated signal statistics

Attacks the **A3 ceiling saturation** at the estimator / shape level:

- **Tobit regression** (Tobin 1958, *Econometrica* 26): censored-MLE for `Y = min(Y*, c)`. Fixed-effect Tobit (Honoré 1992, *Econometrica*) allows replacing OLS TWFE with censoring-aware α.
- **Box-Cox output warping** (Box & Cox 1964, *JRSS-B*; Snoek et al. 2014 "input warping" ICML; Cowen-Rivers et al. 2022 HEBO, *JAIR*): fit `λ̂` to maximise Gaussian likelihood of the transformed objective, use `(Y^λ − 1)/λ` as the TPE objective.
- **Computerized Adaptive Testing** (Lord 1980, *Applications of IRT to Practical Testing*; Chang & Ying 1996): select opponents to maximise Fisher information `I_j ∝ a_j² · P_j · (1−P_j)` against the current posterior on α.
- **Sympson-Hetter exposure control** (Sympson & Hetter 1985): cap reuse rate of any single opponent so anchors don't become over-fit proxies.

### 2.3 Quality-diversity archives

Attacks the **exploit-cluster convergence** by replacing scalar ranking with per-niche local ranking:

- **MAP-Elites** (Mouret & Clune 2015, arXiv:1504.04909), **CVT-MAP-Elites** (Vassiliades et al. 2018, *IEEE TEVC* 22(4)), **CMA-MAE** (Fontaine & Nikolaidis 2023, arXiv:2205.10752) — foundational but require 10^5–10^7 evaluations; **infeasible at 1000-trial budget**.
- **Surrogate-Assisted MAP-Elites (SAIL)** (Gaier et al. 2018, *Evol. Comp.* 26(3)); **Deep SA-ME** on Hearthstone decks (Zhang et al. 2022, arXiv:2112.03534) — bring budget down to ~1000 real evals via GP/NN surrogate.
- **Dominated Novelty Search** (Bahlous-Boldi et al. 2025, arXiv:2502.00593): replaces scalar rank with "fraction of k-nearest neighbours in behaviour space that this build dominates." Structurally a drop-in A3 replacement. Descriptor can be the per-opponent win-rate vector — free from what we already log.
- **qNEHVI** (Daulton et al. 2020/2021) — multi-objective BO over per-opponent-class objectives as an alternative to QD archives.

---

## 3. Simulation validation

The Phase 5E validation harness extended the curriculum-simulation framework with a generative model calibrated to Hammerhead-shaped statistics, run across hundreds of builds × tens of opponents × multiple seeds. Four metrics were tracked: Spearman ρ vs true quality, Spearman ρ on raw α before shape step, within-exploit-cluster spread ρ, and A3 ceiling fraction.

### 3.1 Strategy ranking (qualitative)

Eleven strategies were compared. The qualitative ranking — design-grade and unaffected by V1 invalidation:

- **D (TWFE + Box-Cox A3)** dominates the rank-shape baseline on every metric, with the largest gain in ceiling fraction (the metric Box-Cox is designed to fix) and a smaller but positive gain in ρ vs truth.
- **H (CAT Fisher-info opponent selection)** is orthogonal to D — it changes *which* matchups we observe, not *how* we aggregate them — and produces a comparable independent gain.
- **J (D + H combined)** is the best overall, with marginal gain over D alone smaller than D's gain over baseline.
- **CFS (B)** *collapsed* in this regime: with the validated opponent-pool composition, CFS re-weighting concentrates almost all weight on a handful of hard opponents and noise dominates. A regime mismatch, not a bug.
- **EM-Tobit (C, I, K)** improved raw α modestly but the imputation variance approximately cancelled the bias correction at the validated censoring rate (Amemiya 1984 MSE-gain condition not met). Becomes decisive at higher censoring rates.
- **Main-exploiter loop (G)** targets RPS-adversarial failure modes which the synthetic generative model does not reproduce (the exploit feature is a flat global uplift, not a counterable strategy). Still potentially valuable against the real opponent-pool ceiling.

Specific Δρ values, ceiling fractions, and significance levels are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

### 3.2 Why Box-Cox dominates structurally

Box-Cox is a *monotone transform* with no ties, so Spearman ρ on the α stage is preserved exactly while the top quartile is no longer clamped at 1.0. The ρ-vs-truth gain over the rank-shape baseline is small by design (both are monotone); the *mechanical* gain — replacing top-quartile ties with a continuous gradient TPE's `l(x)` can act on — is the actual deliverable.

### 3.3 The methodological diagnostic

The simulation introduced a `ρ_α_truth` metric — Spearman ρ on the raw α *before* any A3 shape step. This isolates the estimator contribution (A1) from the post-processor contribution (A3):

- Baseline A has ρ_α_truth slightly above ρ_truth — the gap is the A3 rank-shape destroying information via top-quartile ties.
- Box-Cox D has ρ_α_truth equal to ρ_truth — the A3 step preserves information exactly.

This diagnostic proves that on the Hammerhead regime the bottleneck was A3, not A1. The gap-size measurement is V1-derived and pending re-validation; the structural diagnostic (Box-Cox preserves rank information; rank-shape-with-clamp does not) is paradigm-level.

### 3.4 Post-5D revalidation — 2026-04-18

Phase 5D shipped on 2026-04-18 (EB shrinkage + triple-goal at A2′). The 5E decision was re-validated against the new baseline by layering 5D's EB step into the harness and re-running seven strategies: A0 (pre-5D baseline), A (5D + rank), D (5D + Box-Cox), H (CAT + 5D + rank), I (Tobit + 5D + Box-Cox), J (CAT + 5D + Box-Cox), K (full stack).

Qualitative ranking re-confirmed:

- **A (5D + rank)** produces the dominant ρ gain over A0 (5D itself is the heavy lift).
- **D (5D + Box-Cox) vs A**: ρ delta is near-zero by design (both monotone post-EB). The win is mechanical — ceiling fraction collapses and top-k overlap improves substantially.
- **J (CAT + 5D + Box-Cox)**: small but positive marginal gain over D from the CAT observation-side change.
- **I / K (Tobit variants)**: regress because σ̂_i² is computed from OLS residuals inside the EB step but Tobit produces a different α̂ distribution, so the EB step shrinks Tobit α̂ in the wrong direction. Not a production concern (we never ship Tobit).

A calibration sweep across 4 regimes of covariate-noise multiplier confirmed that **Box-Cox's A3 effect is invariant across the covariate-strength range** — the A3 transform sits downstream of α̂_EBT and doesn't care how strong the α̂ is. This is the structural argument for shipping Box-Cox: its mechanical win does not depend on a particular calibration of the upstream EB prior.

Specific magnitudes (Δρ tables, ceiling-fraction collapses, top-k overlap multipliers, calibration-sweep numbers) are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

---

## 4. Accepted path forward

Ranked by leverage × confidence × engineering cost:

1. **Replace A3 rank-shape-with-top-quartile-clamp with Box-Cox output warping.** Single-function swap at the production `_rank_fitness` site. Fit `λ̂` via `scipy.stats.boxcox` over all completed trials' `cv_fitness`, refit every N trials, then min-max scale to [0, 1] for Optuna reporting consistency. Expected production effect: exploit-cluster builds spread out on the fitness scale, 7-way tie at fitness=1.0 dissolves, TPE's l(x) gets gradient to act on at the top.

2. **Add CAT Fisher-info opponent selection as a secondary enhancement.** Reuses the existing anchor infrastructure: keep the 3 locked anchors (stability for WilcoxonPruner step IDs), replace the random-fill remainder with posterior-variance-maximising opponent selection, capped by Sympson-Hetter exposure control. Expected directional gain on top of Box-Cox; specific marginal magnitude pending re-validation under V2.

3. **Defer Tobit, CFS, POET MC, main-exploiter loop** to later phases. Each targets a failure mode that will become dominant at a different regime:
   - Tobit: when per-matchup censoring exceeds ~30%.
   - CFS: when the opponent pool contains many more hard opponents than active-per-trial.
   - Main-exploiter: when the failure mode is exploit-vs-counter adversarial dynamics (requires a real rock-paper-scissors signal, not a flat-uplift feature).

### 4.1 Implementation notes (shipped 2026-04-18)

- **Entry point**: `src/starsector_optimizer/optimizer.py::_shape_fitness(eb_fitness, completed_values, config)`. Pure function returning `(float, _ShapeDiag)`. The evaluator's `_finalize_build` calls it after appending `eb_fitness` to `_completed_fitness_values` so the current trial is always inside the population range.
- **Config**: `ShapeConfig(min_samples: int = 8, positivise_epsilon: float = 1e-6)` in `models.py`, accessed via `OptimizerConfig.shape`.
- **Refit cadence — deviation from §4 item 1**: λ is refit on every `_finalize_build` call, not "every N trials" as the original proposal suggested. Rationale: at n=300 the scipy MLE costs ~1ms per call (0.3s total wall-clock vs matchup latency measured in minutes). Batched refit would save nothing and would introduce a `(λ, shift, min, max)` cache-coherence burden against the rolling min-max rescale window.
- **`min_samples=8` rationale — plan-introduced floor, not research-doc prescription**. Chosen by analogy to `EBShrinkageConfig.eb_min_builds=8`: Box-Cox MLE is part of the same MLE family and destabilises under ~8 samples. Below the floor, `_shape_fitness` returns the min-max-scaled `eb_fitness` — monotone in `eb_fitness`, preserves warm-up ordering for TPE before Box-Cox activates.
- **Non-finite input contract**: raises `ValueError`. Contract shift vs `_apply_eb_shrinkage` (which does not guard) — upstream NaN is an invariant violation in TWFE or EB shrinkage, not unknown game data, so the forward-compat "warn, don't crash" principle does not apply. Documented here + pinned at `test_shape_fitness_raises_on_non_finite_input`.
- **Failure-score bypass**: inherited from the existing `_finalize_build` structure — `failure_score` is told to Optuna directly at the failure sites and never enters `_completed_fitness_values`.
- **Outlier handling**: in production `eb_fitness` is always appended to `_completed_fitness_values` before `_shape_fitness` is called, so the input is always inside the population range. The pre-transform clamp in `_shape_fitness` (clipping to `[positivised.min(), positivised.max()]` before `boxcox(..., lmbda=λ)`) is a defence against test fixtures where a caller passes an out-of-range value; under that path the output saturates at 0 or 1.
- **Logging**: three new log events.
  - First-activation INFO log fires once when Box-Cox first runs (n ≥ min_samples, ptp ≥ eps): `"A3 Box-Cox activated at n=%d completed builds (first λ=%.3f)"`.
  - Per-trial completion line replaces `ranked=%.3f` with `shaped=%.3f, λ=%s` where `λ` is either the fitted value or `pt:<reason>` during passthrough.
  - End-of-run summary sibling to the EB summary: `"A3 Box-Cox summary: %d Box-Cox trials (λ mean=%.3f, std=%.3f), %d passthrough (%s)"`.
- **JSONL schema additions**: `shape_lambda: float | null` and `shape_passthrough_reason: str | null`. Absent on pruned records; present on all completed records. See spec 24 §JSONL schema.

---

## 5. Rejected directions

### 5.1 Hand-curated hullmod blacklist — REJECTED (bitter lesson)

The Hammerhead exploit hullmods all carry CSV tags like `no_drop, no_drop_salvage, codex_unlockable, hide_in_codex`. A one-line filter in `search_space.py:get_eligible_hullmods` would remove them. We reject this on principle: it encodes a claim ("these hullmods are unintended") that the adversarial signal should be able to discover. If the opponent pool is insufficient to discriminate exploits from genuinely strong builds, expand the pool; don't constrain the search.

This is the same rationale Sutton applies to hand-crafted features in RL. Encoding "this is an exploit" is a claim about the target distribution that belongs in the adversarial signal, not in the search space.

### 5.2 Per-frame Java flux-pressure / overload-duration tracking — REJECTED (bitter lesson)

An early draft of Phase 5D had a sub-phase proposing additions to the Java combat harness to collect time-weighted flux averages, cumulative overload duration, engagement-distance trajectories, etc., then fold them into a richer `combat_fitness` composite. We reject the per-frame tracking sub-phase for the same bitter-lesson reason: these are *human-designed* signal channels that try to inject a prior about which combat behaviors are "good." Their weights in `combat_fitness` would then be hand-tuned.

The accepted sub-phase — using data the harness already collects (win/loss, HP differential, duration, timeout state) — stays on the roadmap because those are the primitive outcome variables the optimization target is already defined over. What we reject is adding *engineered* intermediate quantities.

See `docs/reference/phase5c-opponent-curriculum.md` §4.4 and `docs/reference/phase5d-covariate-adjustment.md` §4 for the full rejection rationale.

### 5.3 Full MAP-Elites — REJECTED (scale mismatch)

Our trial budget is orders of magnitude below the MAP-Elites regime (typically 10^5–10^7 real evaluations). Pure MAP-Elites is not viable at this scale. The tractable QD variant — **Dominated Novelty Search** — appeared in the validation grid (strategy E) and halved ceiling saturation, but Box-Cox dominated it on every metric in the V1 validation. Keep DNS as an available research alternative; don't ship it.

---

## 6. Files

Related docs:
- [phase5-signal-quality.md](phase5-signal-quality.md) — original Phase 5A/5B foundational research.
- [phase5a-deconfounding-theory.md](phase5a-deconfounding-theory.md) — TWFE 6-field literature synthesis.
- [phase5c-opponent-curriculum.md](phase5c-opponent-curriculum.md) — Phase 5C opponent selection + rejected per-frame-Java rationale.
- [phase5d-covariate-adjustment.md](phase5d-covariate-adjustment.md) — Phase 5D EB shrinkage of α̂ toward a heuristic prior (replaces the rejected composite-weighted-sum approach and itself replaces an earlier rejected CUPED/FWL/PDS design — see §4.5 of that doc).
- [implementation-roadmap.md](implementation-roadmap.md) — Phase 5E entry on the roadmap.
- [../specs/24-optimizer.md](../specs/24-optimizer.md) — implementation spec.
- [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) — V1 invalidation that retired the original `experiments/signal-quality-*` directories.

---

## 7. Key references

- Bahlous-Boldi et al. (2025), "Dominated Novelty Search: Rethinking QD as an End-to-End Fitness Transformation," arXiv:2502.00593.
- Box & Cox (1964), "An Analysis of Transformations," *JRSS-B* 26(2).
- Chang & Ying (1996), "A Global Information Approach to Computerized Adaptive Testing," *Applied Psychological Measurement*.
- Honoré (1992), "Trimmed LAD and Least Squares Estimation of Truncated and Censored Regression Models with Fixed Effects," *Econometrica* 60(3).
- Lanctot et al. (2017), "A Unified Game-Theoretic Approach to Multiagent RL," arXiv:1711.00832.
- Lord (1980), *Applications of Item Response Theory to Practical Testing Problems*, Erlbaum.
- Rosin & Belew (1997), "New Methods for Competitive Coevolution," *Evolutionary Computation* 5(1).
- Sutton (2019), "The Bitter Lesson."
- Tobin (1958), "Estimation of Relationships for Limited Dependent Variables," *Econometrica* 26.
- Vinyals et al. (2019), "Grandmaster Level in StarCraft II Using Multi-Agent Reinforcement Learning," *Nature* 575.
