# Phase 5C — Opponent Curriculum

> **Status**: Shipped in `src/starsector_optimizer/optimizer.py` and `src/starsector_optimizer/deconfounding.py`. Current design documented in `docs/specs/24-optimizer.md` and `docs/specs/28-deconfounding.md`.

Design and research log for how opponent subsets are selected per trial in the Starsector ship-build optimizer. The shipped design is **anchor-first ordering + incumbent overlap + fixed pre-burn-in opponent set**, backed by TWFE decomposition (Phase 5A). This document records the research trail and the alternatives considered and rejected.

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer pipeline; Phase 5A handles fitness aggregation (TWFE decomposition), Phase 5B handles pruning (WilcoxonPruner + ASHA), Phase 5C (this doc) handles *which opponents each trial faces*, Phase 5D replaces the scalar A2 control variate with EB shrinkage of α̂ toward a heuristic-predicted prior, Phase 5E revises the fitness-shaping post-processor. See `docs/reference/implementation-roadmap.md` for the full phase status.

---

## 1. Problem

At ~10 active opponents per trial against a pool of ~50, random selection produced three measurable pathologies in the first Hammerhead overnight run (63 trials, `experiments/hammerhead-overnight-2026-04-13/`):

1. **Pool bias**: alphabetical selection concentrated active opponents on freighters and light carriers, ignoring the combat destroyers the optimizer most needed to train against. Only 10/54 destroyer variants seen.
2. **Equal opponent weighting**: beating a trivial freighter contributed the same raw score as beating a peer combat destroyer, making cross-build comparability poor.
3. **Poor pruning signal**: random opponent ordering left the WilcoxonPruner starved of discriminative paired-comparison data until late rungs. Pruning rate 11%.

The goal of Phase 5C is to produce opponent subsets that (a) support TWFE identification of build quality α and opponent difficulty β, (b) give the Wilcoxon pruner stable step IDs for paired comparison, and (c) concentrate evaluation budget on informative matchups — without encoding a human prior about what "informative" means.

---

## 2. Shipped Design

### 2.1 Three invariants

- **Fixed pre-burn-in opponent set** (trials 0 through `twfe.anchor_burn_in`): every build faces the same opponents, drawn with `random.Random(0)`. This maximises Wilcoxon step-ID overlap during the cold-start phase, when no α / β estimates are yet available.
- **Anchors locked after burn-in**: the `twfe.n_anchors` opponents with the highest absolute Spearman correlation between raw matchup score and finalised build fitness are pinned to the front of every subsequent trial's opponent list. Once locked they never change, so Wilcoxon's paired comparison at steps 0 through `n_anchors − 1` is always over the same opponents across trials.
- **Incumbent overlap**: each new post-burn-in trial forces `twfe.n_incumbent_overlap` opponents from the set the current incumbent faced. This guarantees direct pair-wise TWFE comparability between any challenger and the current best build, which is what TPE's acquisition function ultimately depends on.

### 2.2 Per-trial selection order

1. Anchors first (steps `0 … n_anchors − 1`).
2. Incumbent-overlap fill (`n_incumbent_overlap` additional opponents drawn from the incumbent's set, shuffled).
3. Remainder drawn from the full pool, shuffled.

Total set size is bounded by `active_opponents` in `OptimizerConfig`.

### 2.3 Relationship to TWFE (Phase 5A)

Phase 5C does not itself produce a fitness score; it decides which cells `(build_i, opponent_j)` get observed. TWFE decomposition (Phase 5A, `deconfounding.py:twfe_decompose`) then estimates build quality α from the accumulated `score_matrix`, with opponent difficulty β absorbed as a fixed effect. The curriculum's job is to give TWFE a well-connected observation graph — which is exactly what the three invariants achieve.

Validated effects (from `experiments/hammerhead-twfe-2026-04-13/`, 900 trials, 2026-04-17):
- Pruning rate rose from 11% baseline to 15% (moderate gain; bounded by Wilcoxon's requirement of paired data).
- Cross-subset comparability: TWFE α identifies the true ranking better than z-scored mean fitness in simulation (`ρ ≈ 0.775` vs `0.525`, see `experiments/phase5b-curriculum-simulation/`).
- Stable Wilcoxon step IDs confirmed by trace analysis of the intermediate-value table in the study DB.

---

## 3. Research trail — methods that shaped what shipped

The design above emerged from a literature survey spanning curriculum learning, adaptive testing, racing algorithms, coevolutionary archive management, and rating systems. The key references:

- **AlphaStar PFSP** (Vinyals et al. 2019, *Nature* 575, "Grandmaster level in StarCraft II"): opponent informativeness peaks at `p_win · (1 − p_win)` (50% win rate). Motivated the absolute-Spearman discriminative-power metric used for anchor selection.
- **Computerized Adaptive Testing / IRT** (Lord 1980, *Applications of IRT to Practical Testing Problems*; van der Linden & Glas 2010, *Elements of Adaptive Testing*): high-discrimination items should be presented first. Our anchor-first ordering is the CAT analogue.
- **POET** (Wang & Lehman 2019, arXiv:1901.01753): the lesson that a naive easy-to-hard curriculum fails. Drove the design toward information-based ordering rather than difficulty-based.
- **OpenAI Five**: 80/20 mix of current and historical opponents. Analogue of our incumbent-overlap + pool-fill split.
- **irace / SMAC intensification** (Lopez-Ibanez et al. 2016, *Operations Research Perspectives*; Hutter et al. 2011, *LION*): budget re-use across incumbents, paired comparisons rather than absolute scoring. Directly informed the Wilcoxon pruner configuration in Phase 5B.
- **Pareto-coevolution archive** (de Jong 2007, *Evolutionary Computation*): informative-test archives. The "discriminative power" anchor metric is the Pareto-coevolution practitioner heuristic for this archive concept.

For the TWFE-deconfounding research that is the estimator *downstream* of 5C's opponent selection, see `docs/reference/phase5a-deconfounding-theory.md`. That doc's 6-field synthesis is the basis for using additive `Y = α + β + ε` at all.

---

## 4. Rejected alternatives — with rationale

### 4.1 Elo-weighted fitness — REJECTED

**What it was.** Maintain running Elo / TrueSkill ratings for each opponent; weight per-matchup fitness by a softmax of opponent Elo (low temperature → hard opponents dominate).

**Why rejected.** Simulation (`experiments/phase5b-curriculum-simulation/curriculum_simulation.py`) showed Elo correlation with true opponent difficulty is `ρ ≈ 0.024` when build quality is non-stationary — the dominant regime in Bayesian optimisation, where later trials are systematically better. The opponent rating absorbs the improving-build trend and stops tracking actual difficulty. TWFE's β captures opponent difficulty unconditional on build improvement, with `ρ ≈ 0.96` in the same simulation. Elo-based weighting would have actively fought the TPE loop.

**Bitter-lesson verdict.** Elo itself is not hand-tuned, but using it to weight fitness requires choosing a *temperature*, which is. TWFE β avoids the temperature knob entirely.

### 4.2 Epoch-based opponent rotation — REJECTED

**What it was.** Divide the trial budget into ~30-trial epochs. Within an epoch, use a fixed 10-opponent subset chosen to maximise a UCB-style `discriminative_power × diversity + α · exploration_bonus`. Between epochs, rotate some opponents out and introduce new ones.

**Why rejected.** Simulation showed cross-epoch comparability degrades sharply whenever the rotation changes more than one or two opponents, because the matchup graph becomes disconnected into per-epoch cliques. TWFE needs a connected observation graph to identify α and β jointly; epoch rotation destroys that connectivity. Anchor-locking (the shipped design) preserves global connectivity through the three fixed anchors.

**Bitter-lesson verdict.** The UCB weighting combines multiple hand-chosen factors (discriminative power, diversity, exploration bonus). Anchor-first + incumbent overlap is a simpler data-driven substitute with no weighting to pick.

### 4.3 Extended validation (top-10% deep re-evaluation) — DEFERRED, NOT REJECTED

**What it was.** After a trial completes, if its fitness is in the top 10%, re-evaluate against 10-20 additional opponents to catch overfitting to the active set.

**Why deferred.** The accepted curriculum (anchors + incumbent overlap) already keeps the active set diverse and stable. Extended validation is a budget-efficiency optimisation for cases where the accepted design itself isn't enough — i.e. when the top 10% overfits to the anchor set. No such overfitting has been observed so far. If post-Phase-5E Hammerhead runs show top-tier builds that fail on out-of-pool opponents, this becomes a candidate re-introduction.

### 4.4 Per-frame Java harness tracking (flux / overload / distance) — REJECTED

**What it was.** Extend `CombatHarnessPlugin.java` with per-frame accumulators: time-weighted flux, cumulative overload duration, engagement-distance trajectories, time-to-first-hull-damage. Fold into a richer `combat_fitness` composite.

**Why rejected.** Each of these four signals is a *human-designed intermediate quantity*, not a primitive outcome of the match. Their composite weights in `combat_fitness` would have to be hand-chosen, violating the bitter lesson (Sutton 2019) — methods leveraging computation scale better than methods leveraging human knowledge. The match outcome primitives already collected (win/loss, HP differential, duration, timeout state, overload count) are the correct target variables; richer use of the already-computed pre-matchup scorer components happens in Phase 5D via empirical-Bayes shrinkage toward an OLS-fitted regression prior — no hand-tuned weights.

**Bitter-lesson verdict.** The rejected design is exactly the anti-pattern the bitter lesson names. `docs/reference/phase5d-covariate-adjustment.md` replaces it with a statistically-principled alternative (empirical-Bayes shrinkage of α̂ toward a heuristic-predicted regression prior — fusion paradigm, not conditioning).

### 4.5 Hand-curated hullmod blacklist (filter "no_drop" / "codex_unlockable" tags) — REJECTED

**What it was.** The 2026-04-17 Hammerhead run revealed 89% of top builds exploited rare-faction hullmods (`shrouded_lens`, `fragment_coordinator`, `neural_integrator`) that carry CSV tags indicating they're supposed to be unobtainable in normal play. A one-line filter in `search_space.py:get_eligible_hullmods` would remove them.

**Why rejected.** Encodes the claim "these hullmods are unintended" into the search space rather than letting the adversarial signal expose it. If the opponent pool is insufficient to discriminate exploits from genuinely strong builds, the correct fix is pool growth (Phase 5F in the roadmap — adversarial curriculum via PSRO / main-exploiter loop). Filtering the search space is a Sutton-style human-knowledge injection we should not make permanent.

---

## 5. References

- Vinyals et al. (2019), "Grandmaster Level in StarCraft II Using Multi-Agent Reinforcement Learning," *Nature* 575.
- Wang & Lehman (2019), "POET: Endlessly Generating Increasingly Complex and Diverse Learning Environments," arXiv:1901.01753.
- Lord (1980), *Applications of Item Response Theory to Practical Testing Problems*, Erlbaum.
- van der Linden & Glas (eds., 2010), *Elements of Adaptive Testing*.
- Lopez-Ibanez et al. (2016), "The irace Package: Iterated Racing for Automatic Algorithm Configuration," *Operations Research Perspectives*.
- Hutter, Hoos & Leyton-Brown (2011), "Sequential Model-Based Optimization for General Algorithm Configuration," *LION*.
- de Jong (2007), "Pareto-Coevolution Archive," *Evolutionary Computation*.
- Sutton (2019), "The Bitter Lesson."

---

## 6. See also

- `docs/reference/phase5a-deconfounding-theory.md` — TWFE decomposition theory (6-field literature synthesis).
- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B foundational research.
- `docs/reference/phase5d-covariate-adjustment.md` — EB shrinkage of α̂ toward a heuristic prior (replaces the rejected per-frame Java approach; and itself replaces an earlier rejected CUPED/FWL/PDS design, see §4.5 of that doc).
- `docs/reference/phase5e-shape-revision.md` — A3 rank-shape revision (Box-Cox replaces top-quartile clamp).
- `docs/reference/implementation-roadmap.md` — phase overview and status for all Phase 5 sub-phases.
- `docs/specs/24-optimizer.md`, `docs/specs/28-deconfounding.md` — implemented specs.
