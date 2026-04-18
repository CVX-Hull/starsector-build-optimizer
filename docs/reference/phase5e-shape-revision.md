# Phase 5E — A3 Shape Revision

> **Status**: Research complete, implementation planned. Simulation validation in `experiments/signal-quality-2026-04-17/`.

Findings from the 900-trial Hammerhead TWFE run (`experiments/hammerhead-twfe-2026-04-13/`, 2026-04-17) that motivate a revision to the A3 fitness-shaping step of the Phase 5A signal-quality pipeline.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer. The shipped pipeline is A1 TWFE decomposition → A2 single-channel control variate → A3 rank-shape-with-top-quartile-clamp. Phase 5E replaces A3. Phase 5D (`docs/reference/phase5d-covariate-adjustment.md`) separately replaces A2 with empirical-Bayes shrinkage of α̂ toward a heuristic-predicted regression prior; 5D and 5E are orthogonal and compose (5E reads α̂_EBT from 5D). See `docs/reference/implementation-roadmap.md` for the full Phase 5 overview and `docs/reference/phase5a-deconfounding-theory.md` for the TWFE foundation this doc builds on.

---

## 1. What went wrong on the Hammerhead run

The optimizer ran 900 trials against the 10-active-opponent pool with:
- A1: TWFE additive decomposition (`deconfounding.py`) — *working correctly*
- A2: control variate on TWFE α — *working correctly*
- A3: quantile rank with top-quartile clamp at 1.0 — **the problem**

Three pathologies emerged, quantified from `experiments/hammerhead-twfe-2026-04-13/evaluation_log.jsonl` (368 completed trials after fresh-run cutoff):

| Pathology | Observation |
|---|---|
| **Exploit-cluster convergence** | 280/313 (89%) of completed builds share at least one of `shrouded_lens`, `shrouded_mantle`, `fragment_coordinator`, `neural_integrator` — hullmods whose in-game acquisition requires specific endgame faction content. These add passive AoE damage and damage-recoil effects independent of flux state, so builds running `vents=0, caps=1` still achieve full wins. |
| **A3 ceiling saturation** | 7/313 builds map to `fitness = 1.000` with raw TWFE α ranging only 0.48–0.82 (theoretical max 1.5). The top-quartile clamp destroys any gradient among the top 25%. |
| **Opponent pool ceiling** | Peer Hammerhead variants can force timeouts but not kills (e.g. `hammerhead_Support`: 0/31 player wins, all timeouts). The stronger an exploit build, the more opponents saturate at `hp_differential ≈ 1.0`, so raw matchup scores censor at the fitness tier boundary. |

Bottom line: **the A3 rank shape discards precisely the gradient the TPE sampler needs to distinguish the exploit-cluster winners from one another**. A1 and A2 are not the bottleneck.

### The search-space question (bitter lesson)

The obvious patch is "filter out hullmods whose CSV tags include `no_drop`, `no_drop_salvage`, `codex_unlockable`, or `hide_in_codex`." We reject this as a bitter-lesson violation (Sutton 2019):

> Methods leveraging computation scale better than methods leveraging human knowledge.

The bitter-lesson framing rejects a *silent hard-coded filter* of rare-faction hullmods. Two distinct, non-silent follow-ups are on the roadmap: **Phase 5F** (regime-segmented optimization — user explicitly opts into a progression tier; `search_space.py` masks components per the user's chosen regime; framed as CMDP feasibility alignment rather than human-knowledge injection — see `phase5f-regime-segmented-optimization.md` §2.1 and §4.6 for why this is not a bitter-lesson violation), and **Phase 5G** (adversarial opponent curriculum — grow the opponent pool until the signal itself exposes exploits; research continues in §2.1 below).

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

`experiments/signal-quality-2026-04-17/signal_validation.py` (~600 lines) extends the proven `experiments/phase5b-curriculum-simulation/` harness with:

- Generative model matching observed Hammerhead statistics: 90% exploit cluster with within-cluster sub-gradient `N(0.8, 0.3)`, ceiling clip at ±1.2 (11.6% of cells censored, calibrated to reproduce the 25% A3 saturation observed in baseline).
- 300 builds × 50 opponents × 10 active-per-trial × 20 random seeds.
- Four metrics: Spearman ρ vs true quality, Spearman ρ on raw α before shape step, within-exploit-cluster spread ρ, and A3 ceiling fraction (predicted fitness ≥ 0.99).

### 3.1 Results — eleven strategies

Final table (see `experiments/signal-quality-2026-04-17/REPORT.md` for full details, including paired Wilcoxon tests):

| Strategy | ρ vs truth | ρ α vs truth | Exploit-spread ρ | Ceiling % |
|---|---|---|---|---|
| A — Baseline (production TWFE + rank shape) | 0.401 ± 0.072 | 0.411 | 0.308 | 25.3% |
| B — CFS-weighted TWFE | −0.013 ± 0.054 | 0.000 | −0.009 | 25.3% |
| C — EM-Tobit TWFE | 0.463 ± 0.041 | 0.465 | 0.371 | 25.3% |
| **D — TWFE + Box-Cox A3** | **0.471 ± 0.057** | **0.471** | **0.382** | **0.4%** |
| E — TWFE + Dominated Novelty A3 | 0.419 ± 0.045 | n/a | 0.335 | 12.4% |
| F — B + C + E combined | 0.419 ± 0.064 | 0.457 | 0.336 | 12.4% |
| G — AlphaStar main-exploiter loop | 0.416 ± 0.085 | 0.419 | 0.320 | 25.3% |
| H — CAT Fisher-info opponent selection | **0.499 ± 0.071** | **0.506** | **0.412** | 25.3% |
| I — Tobit α → Box-Cox | 0.463 ± 0.048 | 0.463 | 0.377 | 0.4% |
| J — Box-Cox + CAT | **0.485 ± 0.076** | 0.485 | 0.395 | 0.4% |
| K — Tobit + Box-Cox + CAT | 0.472 ± 0.081 | 0.472 | 0.383 | 0.4% |

### 3.2 Key deltas and conclusions

**Box-Cox A3 (D) is the biggest single win.** Δρ vs baseline = +0.070 (p = 0.0001); ceiling saturation collapses from 25.3% → 0.4% (a 63× reduction). It's a monotone transform that introduces no ties, so Spearman ρ on the α stage is preserved exactly while the top quartile is no longer clamped.

**CAT Fisher-info opponent selection (H) is orthogonal and also helps.** Δρ = +0.098 (p = 0.048). It changes *which* matchups we observe, not *how* we aggregate them. It composes with D (J = D + H shows Δρ = +0.084 vs baseline) but the marginal over D alone is not significant at n = 20.

**Tobit, CFS, and the main-exploiter loop under-performed in the validated regime, for explicable reasons:**
- **CFS (B)** *collapsed catastrophically* (Δρ = −0.42) because with only ~5 hard opponents in a 50-opponent pool and 10 active per trial, the CFS re-weighting sets hard-opponent weights ≈ 1.0 vs trivial weights ≈ 0.013; a handful of high-variance matchups dominate α and noise wins. Not a bug in CFS; a regime mismatch. Would likely succeed at 100+ hard opponents or 50+ active-per-trial.
- **EM-Tobit (C, I, K)** produced a modestly better raw α (ρ_α_truth 0.411 → 0.465) but the imputation variance approximately cancels the bias correction. Amemiya (1984) MSE-gain condition not met at 11.6% censoring. Would become decisive at ≥30% censoring (e.g. if the fitness ceiling were tightened).
- **Main-exploiter loop (G)** targets the RPS-adversarial failure mode, which the synthetic generative model does not reproduce (the exploit feature is a flat global uplift, not a counterable strategy). Still valuable for the real opponent-pool ceiling but we have no simulation evidence either way.

### 3.3 The methodological diagnostic

The simulation introduced a `ρ_α_truth` metric — Spearman ρ on the raw α *before* any A3 shape step. This isolates the estimator contribution (A1) from the post-processor contribution (A3):

- Baseline A has ρ_α_truth = 0.411 and ρ_truth = 0.401. The 0.010 gap is the A3 rank-shape destroying information via top-quartile ties.
- Box-Cox D has ρ_α_truth = 0.471 and ρ_truth = 0.471. The A3 step preserves information exactly.

This diagnostic proves that on the Hammerhead regime the bottleneck is A3, not A1.

---

## 4. Accepted path forward

Ranked by leverage × confidence × engineering cost:

1. **Replace A3 rank-shape-with-top-quartile-clamp with Box-Cox output warping.** Single-function swap at the production `_rank_fitness` site. Fit `λ̂` via `scipy.stats.boxcox` over all completed trials' `cv_fitness`, refit every N trials, then min-max scale to [0, 1] for Optuna reporting consistency. Expected production effect: exploit-cluster builds spread out on the fitness scale, 7-way tie at fitness=1.0 dissolves, TPE's l(x) gets gradient to act on at the top.

2. **Add CAT Fisher-info opponent selection as a secondary enhancement.** Reuses the existing anchor infrastructure: keep the 3 locked anchors (stability for WilcoxonPruner step IDs), replace the random-fill remainder with posterior-variance-maximising opponent selection, capped by Sympson-Hetter exposure control. Expected marginal gain ~+0.014 ρ on top of Box-Cox (not significant at n=20 in simulation, directionally consistent, orthogonal mechanism).

3. **Defer Tobit, CFS, POET MC, main-exploiter loop** to later phases. Each targets a failure mode that will become dominant at a different regime:
   - Tobit: when per-matchup censoring exceeds ~30%.
   - CFS: when the opponent pool contains many more hard opponents than active-per-trial.
   - Main-exploiter: when the failure mode is exploit-vs-counter adversarial dynamics (requires a real rock-paper-scissors signal, not a flat-uplift feature).

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

Our 1000-trial budget is 2–4 orders of magnitude below the MAP-Elites regime (typically 10^5–10^7 real evaluations). The simulation confirmed: pure MAP-Elites is not viable here. The tractable QD variant — **Dominated Novelty Search** (strategy E in the simulation) — offers a +0.022 Δρ vs baseline (p = 0.143, not significant) and halves ceiling saturation, but Box-Cox dominates it on every metric. Keep DNS as an available research alternative; don't ship it.

---

## 6. Files

Research artifacts:
- `experiments/hammerhead-twfe-2026-04-13/` — 900-trial run, eval log, executed notebooks (`trial_analysis.ipynb`, `build_analysis.ipynb`).
- `experiments/signal-quality-2026-04-17/signal_validation.py` — simulation harness (11 strategies × 20 seeds).
- `experiments/signal-quality-2026-04-17/REPORT.md` — auto-generated simulation report with full Wilcoxon tables.
- `experiments/signal-quality-2026-04-17/results.csv`, `comparison.png`, `exploit_dispersion.png`, `ceiling_saturation.png`.

Related docs:
- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B foundational research (unchanged).
- `docs/reference/phase5a-deconfounding-theory.md` — TWFE 6-field literature synthesis (unchanged).
- `docs/reference/phase5c-opponent-curriculum.md` — Phase 5C opponent selection + rejected per-frame-Java rationale.
- `docs/reference/phase5d-covariate-adjustment.md` — Phase 5D EB shrinkage of α̂ toward a heuristic prior (replaces the rejected composite-weighted-sum approach and itself replaces an earlier rejected CUPED/FWL/PDS design — see §4.5 of that doc).
- `docs/reference/implementation-roadmap.md` — Phase 5E entry on the roadmap.
- `docs/specs/24-optimizer.md` — to be updated when Phase 5E ships (A0 Box-Cox layer before A1 input, or A3 replacement — implementation choice deferred to spec-time).

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
