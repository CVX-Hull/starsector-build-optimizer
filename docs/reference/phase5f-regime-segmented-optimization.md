# Phase 5F — Regime-Segmented Optimization

> **Status**: PLANNED. Research complete (2026-04-17). Targets the 89% exploit-cluster concentration observed in the `experiments/hammerhead-twfe-2026-04-13/` run by aligning the optimizer's feasible action set with user-selectable progression regimes. No shipped code yet.

Design and research log for how the optimizer selects its feasible component pool per run, so that outputs are strong *within the progression regime the player actually inhabits* rather than strong within a nominally-complete but practically-unreachable pool. Shipped design will be **hard-masking of hullmods/weapons/hulls at `search_space.py` construction time + one Optuna study per `(hull, regime)`**, with `mid` as the default regime.

This document records the theoretical grounding (CMDP feasibility alignment, restricted-play balance evaluation, flow/ludology), the shipped-bot pattern consensus (one-run-per-tier across WoW BIS / MTG formats / StS ascension / Hearthstone / motorsport), and the rejected alternatives (scalar penalty, archive-over-single-run, curriculum, Pareto, QD).

Reading this doc cold: Phase 5 is the signal-quality stage of the optimizer pipeline; Phase 5A–C ship TWFE + pruning + opponent curriculum; Phase 5D adds EB shrinkage of the fitness estimator; Phase 5E replaces the fitness-shaping tail; Phase 5F (this doc) constrains the *input space* so Phase 5A–E don't expend their signal on builds a player cannot assemble. See `docs/reference/implementation-roadmap.md` for the full phase status.

---

## 1. Problem

The Hammerhead 2026-04-17 run (900 trials, `experiments/hammerhead-twfe-2026-04-13/`) produced a concentrated top cluster: **89% of non-pruned top-quartile builds** rely on a handful of rare-faction hullmods (`shrouded_lens`, `shrouded_mantle`, `fragment_coordinator`, `neural_integrator`). Every one of those hullmods carries a `no_drop_salvage` CSV tag, and most additionally carry `codex_unlockable` or `no_drop`. The CSV tags are Starsector's own declaration that these items are outside the normal campaign acquisition distribution — they drop only from narrative-gated endgame encounters.

This is not a reward-specification bug (the optimizer's `combat_fitness` correctly ranks combat strength) and not a game-balance complaint against the hullmods themselves (community consensus identifies `Onslaught_Mk.I + Heavy Adjudicator` as the live balance flashpoint, not shrouded/fragment mods — see §2.5). The mismatch is between the **deployment distribution** (what components a player can actually field in normal play) and the **simulator's feasible set** (currently: anything not tagged `restricted` in `search_space.py:31`). The optimizer has been optimizing over a superset of what the player can act on.

Three measurable consequences in the Hammerhead run:

1. **Wasted compute.** At the observed 89% concentration, roughly 800 of 900 trials spent their TWFE/Wilcoxon budget exploring a regime the target user cannot reach. Effective budget on the regime-that-matters was ~100 trials.
2. **Contaminated incumbent.** The TPE posterior pulled toward the exploit cluster; pre-exploit-tier builds could not compete, so the incumbent-overlap curriculum (Phase 5C) anchored comparisons against exploit builds. Non-exploit rankings were correspondingly noisy.
3. **Unusable outputs.** The top-ranked Hammerhead variant assumes the player has defeated the Shrouded Dwellers and Threat faction — content that is endgame-gated in vanilla Starsector. For a user asking "what Hammerhead should I fly in my current playthrough?" the answer is almost never the simulator's top-ranked build.

The goal of Phase 5F is to align the optimizer's feasible action set with a user-selected *progression regime*, so that compute is spent in the regime the user inhabits and outputs are deployable in that regime.

---

## 2. Design grounding

### 2.1 CMDP feasibility alignment is the principled framing

The textbook framing for "some actions must be forbidden regardless of reward" is the **Constrained MDP** (Altman 1999, *Constrained Markov Decision Processes*). Forbidden actions enter as inequality constraints on auxiliary cost functions rather than reward penalties. Huang & Ontañón 2020 ("Invalid Action Masking in Policy Gradients," arXiv:2006.14171) is the discrete counterpart: **action masking is mathematically valid when the masked actions are genuinely infeasible in the domain**, and empirically dominates reward shaping for known-a-priori constraints (faster convergence, no reward hacking, more predictable behaviour).

This is exactly our setting. `no_drop` items are literally marked infeasible in Starsector's campaign deployment distribution. Treating them as infeasible in the simulator is not "patching an exploit loophole" (Krakovna's DeepMind specification-gaming cautionary frame; see Krakovna et al. 2020 "Specification gaming: the flip side of AI ingenuity"); it is **feasibility alignment between the simulator's action space and the deployment environment's action space**. The test: if the user can ever legitimately field this component in their campaign, it is feasible; otherwise it is not. The ceiling parameter exposes this test as user-controllable.

### 2.2 Restricted-play balance evaluation is the direct game-AI precedent

Jaffe et al. 2012 ("Evaluating Competitive Game Balance with Restricted Play," AIIDE) formalizes balance by **restricting the agent's action/strategy space** and measuring performance against unrestricted agents. Restricted play is not a kludge — it is a first-class methodology for evaluating "how does this game play under this regime?" Holmgård-Green-Liapis-Togelius 2018 ("Automated Playtesting with Procedural Personas," IEEE ToG, arXiv:1802.06881) extends the idea to multiple *parallel* playtesters, each respecting a different subset of the action space — directly analogous to our per-regime Optuna studies.

Riot Games' production RL pipeline (GDC 2022 "Balancing League of Legends for Every Player," Vault 1024237; Anyscale 2022 "Deep RL at Riot Games") uses **separate RL bots per player skill tier** (Bronze-bot draws from a different effective action distribution than Challenger-bot). Same pattern: one optimizer per regime, not one optimizer reasoning across regimes.

### 2.3 Engagement theory gives a principled case for conservative defaults

Four independent formal arguments converge on "power without matched challenge reduces engagement":

- **Csikszentmihalyi's flow channel** (1975; 8-sector refinement Massimini-Csikszentmihalyi-Carli 1987). `challenge ≪ skill → boredom`. Trivially-winnable combat sits in the boredom quadrant.
- **Ryan-Rigby-Przybylski PENS / SDT** (*Glued to Games* 2011). Competence satisfaction requires "challenging but not overwhelmingly difficult" — a punished ceiling on over-strong builds.
- **Koster's mastery decay** (*Theory of Fun*, 2004). "Once someone reaches mastery, the game becomes boring." Direct formal analog of "late-game overpowered items reduce engagement."
- **Yannakakis-Togelius EDPCG** (2011 "Experience-Driven PCG"). The experience is the objective; combat optimality is a sub-objective *within* a chosen challenge level.

Ludological reinforcement:

- **Suits 1978** *The Grasshopper*: game-playing is "the voluntary attempt to overcome unnecessary obstacles." The "lusory attitude" is the player's acceptance of less-efficient means for the sake of play. A game without the constitutive constraint *is not a game* — it is just the prelusory goal being achieved.
- **Caillois 1961** *Man, Play and Games*: competitive skill-play (agon) requires "defined limits." Uncapped component pools push play from agon toward alea (luck of loot discovery).
- **Paul 2020** ("Optimizing play: How theorycraft changes gameplay and design," *Game Studies*): unconstrained optimization empirically collapses variety ("tragedy of the commons" on the meta).

The academic case for defaulting conservative (not running at `max_tier=3` by default) is unusually strong for what looks like a trivial config choice.

### 2.4 Shipped-bot consensus is one-run-per-regime

Every comparable optimizer in practice runs one instance per tier rather than one instance reasoning across tiers:

| System | Tier mechanism | Cross-tier handling |
|---|---|---|
| MTG Pauper / Modern / Legacy | Hard legality filter on card pool | Separate EA runs per format (García-Sánchez 2016, Bhatt 2018) |
| Hearthstone Arena / Standard / Wild | Gene alphabet = format card pool | Separate runs; no cross-format transfer |
| Raidbots / Ask Mr. Robot (WoW BIS) | UI dropdown for raid/M+/delve tier | SimC run per tier; no adjacent-tier diagnostic |
| Slay the Spire bots (Slay-I, Bottled AI) | Per-ascension evaluation | Global policy but A0-deck ≠ A20-deck (community consensus) |
| Teamfight Tactics (Riot) | Per-Set agent retrain | Set boundary = hard regime change |
| Gran Turismo PP / FIA class | Scalar PP budget / homologation cutoff | One setup-optimization per class |
| Speedrun categories (Heinrich 2021 arXiv:2106.01182) | Graph-filter constraints on state space | One routing per category |

Scalar cost penalties `fitness − λ·rarity` are **essentially absent from this literature**. The regime-segmentation agent's verdict: "penalties confound the objective, which is exactly the Cinelli-Forney-Pearl bad-control failure that torpedoed 5D-v1." Curriculum-conditioned single-policy approaches exist in deep RL (Narvekar et al. JMLR 2020) but require sample budgets 3-6 orders of magnitude above ours; at `N ≈ 300` per hull the one-run-per-regime pattern is the only viable one.

### 2.5 Starsector-specific data grounding

A full audit of `game/starsector/data/hullmods/hull_mods.csv` and `game/starsector/data/weapons/weapon_data.csv` against the Starfarer API (see `experiments/phase5d-covariate-2026-04-17/` audit output) yields the following usable rarity signals:

| Component | Signal | Coverage | Granularity |
|---|---|---|---|
| Hullmod | `tier` (CSV 0–3) | 57 / 291 (20%) | 4-level ordinal |
| Hullmod | Access tags (`no_drop`, `no_drop_salvage`, `codex_unlockable`, `hide_in_codex`) | 21 / 291 (7%) flagged | Categorical |
| Weapon | `tier` (CSV 0–3) | 148 / 148 (100%) | 4-level ordinal |
| Weapon | `rarity` float | 14 / 148 (9%) | Continuous (too sparse) |
| Weapon | Blueprint faction tags (`base_bp`, `rare_bp`, `lowtech_bp`, …) | 89 / 148 (60%) | Categorical |
| Ship hull | Faction `knownShips` + `codex_unlockable` | 52 / ~200 (26%) | Categorical |

No schema invention is required. The CSV already encodes the regime signal.

**Alex Mosolov's stated design intent** (Codex Overhaul dev post, 2024-05-11): `codex_unlockable` is primarily **spoiler avoidance, not power-gating** — "most things start out unlocked; if something could be found just by browsing a typical colony market, chances are it starts out unlocked." This partially decouples "unlock status" from "designer-intended overpowered," and suggests the primary regime-boundary tags are `no_drop` and `no_drop_salvage` (genuine campaign-acquisition gates) rather than `codex_unlockable` alone (which includes narrative-spoiler-hidden but campaign-reachable items).

**Community balance consensus** (Starsector wiki + fractalsoftworks forum): the live balance complaint is `Onslaught_Mk.I` + `Heavy Adjudicator` (2400 burst DPS at 0.13 flux/damage), not the shrouded/fragment hullmods. This matters for design: the regime filter should cover **ship hulls**, not just hullmods. A regime preset that allows `Onslaught_Mk.I` but forbids `shrouded_lens` would miss the actual balance flashpoint.

**Open territory.** No existing public Starsector optimizer (hidjgr/starsector-builder, kevinvanberlo/starsector-builds, CVX-Hull/starsector-build-optimizer, qcwxezda/Progressive-S-Mods) models progression ceilings. Phase 5F is the first.

---

## 3. Shipped design

### 3.1 RegimeConfig

```python
@dataclass(frozen=True)
class RegimeConfig:
    name: str                             # "early" | "mid" | "late" | "endgame"
    max_hullmod_tier: int                 # inclusive ceiling on CSV tier column; 3 = no filter
    exclude_hullmod_tags: frozenset[str]  # e.g. {"no_drop", "no_drop_salvage"}
    exclude_hull_ids: frozenset[str]      # e.g. {"onslaught_mk1"}
    exclude_weapon_tags: frozenset[str]   # e.g. {"rare_bp"}
```

Stored in `src/starsector_optimizer/models.py` alongside `CombatFitnessConfig` and `TWFEConfig` (frozen-dataclass convention, Design Principle 2 from CLAUDE.md).

### 3.2 Presets

| Preset | Hullmod filter | Weapon filter | Hull filter | Grounding |
|---|---|---|---|---|
| `early` | `tier ≤ 1 ∧ ¬codex_unlockable ∧ ¬no_drop ∧ ¬no_drop_salvage` | `¬rare_bp ∧ ¬codex_unlockable` | vanilla civilian + faction-common | Flow/Suits — meaningful-challenge regime |
| `mid` **(default)** | `¬no_drop ∧ ¬no_drop_salvage` | `¬rare_bp` | vanilla + faction-reachable (no Mk.I) | Jaffe restricted play; Alex-stated spoiler/access distinction |
| `late` | `¬no_drop` | none | all except `onslaught_mk1`, Remnant-only | CMDP feasibility (only truly never-obtainable items masked) |
| `endgame` | none | none | none | QA / exploit-discovery mode (current behaviour) |

Each preset is materialized at `search_space.py` construction time as a **mask over the hullmod / weapon / hull catalogues, applied before `repair_build()` sees any candidate**. This preserves the optimizer-space ↔ domain-space boundary (CLAUDE.md Design Principle 3) and keeps the TWFE/EB pipeline (Phases 5A / 5D) uncontaminated by regime information in the fitness signal.

### 3.3 One Optuna study per (hull, regime)

Each `(hull, regime)` pair runs an independent Optuna study. No cross-regime warm-start is required for correctness, but a transfer-BO optimization is available: seed regime-`K` with the top-M incumbents from regime-`K−1` via `study.enqueue_trial()`. This is a single-line change and gives regime-`mid` a head start from regime-`early` results when both are run in sequence. It is *not* curriculum learning (Bengio 2009 doesn't apply: we are restricting the feasible set, not ordering training data).

### 3.4 Default regime = `mid`

Four converging arguments:

1. **Engagement literature**: `endgame`'s uncapped pool pushes play into Csikszentmihalyi's boredom quadrant; `early`'s aggressive cap may be too constrained for the median player's stage.
2. **Ludology (Suits, Caillois, Paul)**: unconstrained optimization collapses variety; some constraint is constitutive of meaningful play.
3. **Alex's stated intent**: `no_drop` / `no_drop_salvage` are the genuine campaign-acquisition gates; `mid` is the smallest regime that respects those gates without over-filtering `codex_unlockable` items that are actually reachable.
4. **Compute efficiency**: in the Hammerhead 2026-04-17 run, `mid` would have redirected ~80% of trials from the exploit cluster to the regime-that-matters — the single biggest per-budget signal-quality improvement available without changing the optimizer's algorithms.

`endgame` remains accessible as `--regime endgame` for QA / exploit-discovery (Jaffe's "find-broken-stuff" use case — valid but distinct from the "player recommendation" use case which is our primary deliverable).

### 3.5 Relationship to other Phase 5 sub-phases

Phase 5F is **orthogonal** to 5A–E. It changes the optimizer's input space, not its fitness estimator or its shape transform:

- 5A (TWFE): still decomposes `Y_ij = α_i + β_j`, now restricted to in-regime builds.
- 5B (WilcoxonPruner + ASHA): unaffected.
- 5C (anchor-first + incumbent overlap): unaffected; anchor opponents are hull-matched regardless of regime.
- 5D (EB shrinkage of A2): `X_i` covariate vector is invariant to regime (engine-computed + scorer components), so the prior regression can in principle be fit on the union of regime trials. Default per-regime study is simpler and maps to the EB literature's one-population assumption; pooling is a research extension.
- 5E (Box-Cox shape): refit `λ̂` per regime.

---

## 4. Rejected alternatives

### 4.1 Scalar rarity penalty `U = fitness − λ·Σ rarity_tier_i` — REJECTED

**What it was.** Weight each component by an ordinal rarity cost; subtract `λ·Σ cost_i` from fitness at the A3 stage. Small `λ` approximates ε-lexicographic preference (Keeney-Raiffa additive MAU).

**Why rejected.**
1. Every real-world regime-segmented optimizer surveyed (MTG, WoW BIS, StS, Hearthstone, motorsport, speedrun) uses hard filters, not soft penalties. Scalar penalties are "essentially absent from the literature." (Regime-segmentation research agent, eight-field 2026-04-17 sweep.)
2. Academic CCG deckbuilding literature (Fontaine 2019 arXiv:1904.10656, Zhang 2021 arXiv:2112.03534, García-Sánchez 2016 Hearthstone EA) has **no principled `λ` value** reported — all empirical, all tuned by hand.
3. Rarity-in-objective is a bad-control pattern (Cinelli-Forney-Pearl 2022 "Causal Interpretations of Black-Box Models": treating a downstream proxy as a covariate of the outcome biases causal estimates). This is the same failure pattern that refuted Phase 5D v1 (`experiments/phase5d-covariate-2026-04-17/REPORT.md` — synthetic Δρ = −0.35 vs plain TWFE when conditioning paradigm was used instead of fusion paradigm). Entering rarity into `U` would contaminate the TWFE α̂ signal in the same way.
4. Strict lexicographic preferences on ℝ² are not continuously representable (Debreu 1954). Any `λ`-based encoding is an approximation that fails at edge cases where rarity would flip a meaningful fitness gap — the exact regime where we most need the preference to hold.

### 4.2 Archive over single run (QD / MAP-Elites with rarity-tier descriptor) — REJECTED

**What it was.** Maintain a per-rarity-tier elite archive layered on Optuna TPE's trial log; at each trial finalization, insert into the archive cell keyed by build's max-tier if it beats the cell incumbent on fitness. First-round (2026-04-17 nine-agent sweep) recommendation.

**Why rejected.** Every shipped comparable bot uses one-run-per-tier rather than archive-over-single-run:

- StS: Slay-I, Bottled AI, LLM StS agents — one policy per ascension (or global policy evaluated per-tier).
- TFT (Riot): per-Set retrain.
- WoW BIS: SimC run per tier.
- Hearthstone academic EA (Bhatt 2018 arXiv:1806.09771): separate run per format.

The archive pattern requires MAP-Elites-class evaluation budgets (10⁵–10⁶ trials per Mouret-Clune 2015 arXiv:1504.04909; SAIL reduces to ~10³ with GP surrogate per Gaier 2017 arXiv:1702.03713). At `N ≈ 300` per hull with 4-cell binning, the archive gives 75 trials per cell — below the noise floor for reliable illumination. One-run-per-tier concentrates all 300 trials in the regime-that-matters. Simpler and dominates.

The archive is legitimate for *diagnostic* use: post-hoc, the union of completed trials across regimes can be binned by max-tier to surface adjacent-regime alternatives. That is a reporting feature, not an optimization mechanism.

### 4.3 Curriculum learning across regimes — REJECTED

**What it was.** Bengio-Louradour-Collobert-Weston 2009 "Curriculum Learning" (ICML): present easier examples first to guide optimization into a better basin. Adapt to regime selection: optimize `early` first, progressively expand to `late` and `endgame`.

**Why rejected.** Bengio's mechanism is a **continuation method** over training-data order, not over search-space expansion — the training data is re-ordered for the same loss; the hypothesis space is fixed. In our setting the hypothesis space (feasible build set) differs per regime, and the best build in regime K is not a plausible intermediate toward the best build in regime K+1 (they may share few components). Kumar-Packer-Koller 2010 (NIPS) self-paced learning has the same structure; Graves et al. 2017 (arXiv:1704.03003) automated curriculum learning similarly operates on task distributions over a shared learner.

TuRBO (Eriksson et al. 2019 arXiv:1910.01739) and BAxUS (expanding subspaces) are the closest analogs — progressively-expanding trust regions within a single run. But TuRBO's expansion is a convergence heuristic, not a user-facing regime selector. No literature supports replacing per-regime independent studies with a curriculum across regimes at our sample budget.

### 4.4 Multi-fidelity BO with tier as fidelity dimension — REJECTED

**What it was.** Kandasamy-Dasarathy-Oliva-Schneider-Poczos 2017 "Multi-Fidelity Bayesian Optimization with Continuous Approximations" (ICML, arXiv:1703.06240). Fit a joint GP over `(build, tier)` with cheap-at-low-tier queries informing expensive-at-high-tier queries.

**Why rejected.** BOCA's fidelity parameter indexes the **evaluation cost for a fixed x** — every fidelity evaluates the same input. In our setting the input set itself changes with tier (feasible builds differ), and the target of optimization differs (the best build in `early` is often unreachable in `early` when considered as an `endgame` candidate because the regime-specific best draws on unavailable components). Mapping tier to fidelity is a structural mismatch.

### 4.5 Pareto / NSGA-II (fitness vs rarity, 2-objective) — REJECTED

**What it was.** Optuna has native multi-objective support (`NSGAIISampler`, MOTPE). Optimize `(combat_fitness, −rarity_cost)` jointly and return the Pareto front.

**Why rejected.** (a) The user does not want a Pareto front — they want a single recommended build within a chosen regime. (b) For 2-objective problems where one objective is a tiebreaker, scalar penalty is "simpler and correct" (multi-objective EA research agent) — but scalar penalty is itself rejected (§4.1). (c) Pareto methods add a hyperparameter (population size, reference point) and halve TPE's per-trial modeling efficiency. (d) The one-objective-is-tiebreaker framing still commits the bad-control contamination of §4.1.

### 4.6 Hand-curated tag blacklist (phase5c §4.5 re-examination) — SUPERSEDED

**What it was.** Phase 5C §4.5 rejected filtering rare-faction hullmods by CSV tag on bitter-lesson grounds — the filter would encode the claim "these hullmods are unintended" into the search space.

**Why that rejection still stands, but Phase 5F is not the same thing.** A silent hard filter would encode designer intent as a system-level claim. Phase 5F exposes tier as a **user-controllable regime**, not a hard-coded claim about the components. The epistemic act is different: the user explicitly opts into a regime; the system does not silently decide what is "intended." This is Jaffe 2012 restricted-play (valid methodology), not bitter-lesson human-knowledge injection (Sutton 2019). The `endgame` preset retains the original 5C §4.5 behaviour — no filter, QA/exploit-discovery mode available by user choice.

### 4.7 Weitzman reservation-value / Pandora's Box Gittins Index — DEFERRED, NOT REJECTED

**What it was.** Weitzman 1979 "Optimal Search for the Best Alternative" (Econometrica): each alternative `i` has a cost `c_i` and reward distribution; the reservation value `z_i` solves `c_i = E[max(X_i − z_i, 0)]`, and Pandora's Rule (open highest-`z_i`, stop when best reward exceeds max-`z_i`-of-unopened) is optimal. Xie et al. 2024 "Cost-aware Bayesian Optimization via the Pandora's Box Gittins Index" (NeurIPS arXiv:2406.20062) ports this directly to BayesOpt.

**Why deferred.** The formally cleanest mechanism in the 16-field sweep (nine agents round 1 + eight round 2), but requires per-component fitness-contribution distributions that we cannot estimate reliably at Hammerhead-scale N. A research extension once (a) Phase 5D EB shrinkage provides a stable posterior over per-component contributions via the γ̂ regression prior, and (b) the per-regime Phase 5F optimizer has accumulated enough cross-hull runs to identify shared component effects. Not on the critical path; revisit post-5D/5E ship.

---

## 5. Expected impact

Projected from the Hammerhead 2026-04-17 concentration (89% of non-pruned top-quartile trials in the exploit cluster):

| Metric | Current (endgame-equivalent) | After 5F (default `mid`) |
|---|---:|---:|
| Trials in deployment-reachable regime | ~11% (~100 of 900) | 100% (300 of 300) |
| Effective budget for player-useful builds | ~100 trials/hull | 300 trials/hull |
| Top-cluster variety (distinct component sets) | Single exploit cluster dominates | Expected broader cluster spread (no exploit attractor) |
| Output deployability | Requires endgame completion | Available from normal campaign |

No simulation experiment validates these numbers yet — they are projections from the observed concentration and trial budget. Ship-gate will be a replay on the 2026-04-17 eval log with the `mid` mask applied, measuring the change in top-10 composition and TPE convergence trace.

---

## 6. Implementation plan (outline — full plan to be drafted on approval)

```
Step 1: Data — enumerate rarity tags from hull_mods.csv, weapon_data.csv,
  ship_data.csv, faction files. Add a RarityTagSet reference constant
  to parser.py. One-shot read, cached.

Step 2: models.py — add RegimeConfig frozen dataclass and four presets
  (REGIME_EARLY, REGIME_MID, REGIME_LATE, REGIME_ENDGAME).

Step 3: search_space.py — gain a regime: RegimeConfig parameter;
  filter the hullmod / weapon / hull catalogues in get_eligible_hullmods,
  get_compatible_weapons, get_eligible_hulls before they reach
  repair_build.

Step 4: optimizer.py — thread regime through OptimizerConfig and the
  ask-tell loop. Separate studies per (hull, regime).

Step 5: scripts/run_optimizer.py — add --regime CLI flag with default
  mid. Preserve current behaviour as --regime endgame.

Step 6: Ship gate — replay the Hammerhead 2026-04-17 eval log with the
  mid mask. Verify (a) top-10 does not include shrouded_lens /
  fragment_coordinator / neural_integrator builds, (b) within-regime
  TPE convergence trace is comparable to the unmasked run at matched
  in-regime trial count.

Step 7: Docs — update spec 24 (optimizer), spec 26 (search-space),
  CLAUDE.md phase overview, implementation-roadmap.md.
```

Unit tests cover: mask correctness (each preset excludes the expected components), preset immutability, regime-scoped study independence, warm-start via `enqueue_trial` preserves determinism.

---

## 7. References

Constrained MDPs, action masking, restricted play:

- Altman, E. (1999). *Constrained Markov Decision Processes*. Chapman and Hall.
- Huang, S., & Ontañón, S. (2020). "A Closer Look at Invalid Action Masking in Policy Gradient Algorithms." arXiv:2006.14171.
- Jaffe, A., Miller, A., Andersen, E., Liu, Y.-E., Karlin, A., & Popović, Z. (2012). "Evaluating Competitive Game Balance with Restricted Play." AIIDE.
- Holmgård, C., Green, M. C., Liapis, A., & Togelius, J. (2018). "Automated Playtesting with Procedural Personas." IEEE Transactions on Games. arXiv:1802.06881.
- Krakovna, V., Uesato, J., Mikulik, V., Rahtz, M., Everitt, T., Kumar, R., … Legg, S. (2020). "Specification gaming: the flip side of AI ingenuity." DeepMind.

Player engagement, flow, ludology:

- Csikszentmihalyi, M. (1975). *Beyond Boredom and Anxiety*. Jossey-Bass.
- Ryan, R. M., Rigby, C. S., & Przybylski, A. (2011). *Glued to Games: How Video Games Draw Us In and Hold Us Spellbound*. Praeger / Self-Determination Theory.
- Koster, R. (2004). *A Theory of Fun for Game Design*. Paraglyph.
- Yannakakis, G. N., & Togelius, J. (2011). "Experience-Driven Procedural Content Generation." *IEEE Transactions on Affective Computing*.
- Suits, B. (1978). *The Grasshopper: Games, Life, and Utopia*. University of Toronto Press.
- Caillois, R. (1961). *Man, Play and Games*. Free Press.
- Juul, J. (2013). *The Art of Failure: An Essay on the Pain of Playing Video Games*. MIT Press.
- Paul, C. A. (2020). "Optimizing play: How theorycraft changes gameplay and design." *Game Studies*.

Regime-segmented optimization analogs:

- García-Sánchez, P., Tonda, A., Mora, A. M., Squillero, G., & Merelo, J. J. (2016). "Evolutionary Deckbuilding in HearthStone." IEEE CIG.
- Bhatt, A., Lee, S., de Mesentier Silva, F., Watson, C. W., Togelius, J., & Hoover, A. K. (2018). "Exploring the Hearthstone Deck Space." FDG.
- Heinrich, M. (2021). "Automating Speedrun Routing: Overview and Vision." arXiv:2106.01182.

Refuted / not-applicable alternatives (for rejection chain):

- Bengio, Y., Louradour, J., Collobert, R., & Weston, J. (2009). "Curriculum Learning." ICML.
- Kandasamy, K., Dasarathy, G., Oliva, J., Schneider, J., & Póczos, B. (2017). "Multi-Fidelity Bayesian Optimization with Continuous Approximations." ICML. arXiv:1703.06240.
- Eriksson, D., Pearce, M., Gardner, J. R., Turner, R. D., & Poloczek, M. (2019). "Scalable Global Optimization via Local Bayesian Optimization" (TuRBO). NeurIPS. arXiv:1910.01739.
- Deb, K., Pratap, A., Agarwal, S., & Meyarivan, T. (2002). "A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II." *IEEE Transactions on Evolutionary Computation*.
- Debreu, G. (1954). "Representation of a preference ordering by a numerical function." In *Decision Processes*.

Deferred formal cleanest mechanism:

- Weitzman, M. (1979). "Optimal Search for the Best Alternative." *Econometrica* 47:3.
- Xie, Q., Astudillo, R., Frazier, P. I., Scully, Z., & Terenin, A. (2024). "Cost-aware Bayesian Optimization via the Pandora's Box Gittins Index." NeurIPS. arXiv:2406.20062.

Starsector-specific:

- Mosolov, A. (2024-05-11). "Codex Overhaul." Fractal Softworks dev blog.
- `game/starsector/data/hullmods/hull_mods.csv` — tags column.
- `game/starsector/data/weapons/weapon_data.csv` — tier + blueprint tags.
- `experiments/hammerhead-twfe-2026-04-13/` — source of the 89% concentration observation.

---

## 8. See also

- `docs/reference/phase5-signal-quality.md` — original Phase 5A/5B research.
- `docs/reference/phase5a-deconfounding-theory.md` — TWFE decomposition (the fitness estimator Phase 5F restricts the input of).
- `docs/reference/phase5c-opponent-curriculum.md` — opponent-side selection; §4.5 rejects silent hullmod blacklist, which Phase 5F replaces with user-controllable regime.
- `docs/reference/phase5d-covariate-adjustment.md` — EB shrinkage; Phase 5F's `X_i` covariate set is regime-invariant.
- `docs/reference/phase5e-shape-revision.md` — Box-Cox A3; Phase 5F requires per-regime `λ̂` refit.
- `docs/reference/implementation-roadmap.md` — phase status overview. Phase 5G is the renumbered adversarial opponent curriculum (originally 5F, research complete, deferred post-5E).
- `docs/specs/24-optimizer.md`, `docs/specs/26-search-space.md` — implementation specs affected by Phase 5F.
