---
type: reference
status: draft
last-validated: 2026-05-12
---

# Phase 7 — Structured Search-Space Representation

> **Status**: PLANNED. Research complete (2026-04-17). Targets the combinatorial-explosion vs expensive-evaluation bottleneck by replacing the Optuna TPE surrogate (CatCMAwM removed 2026-04-19; see spec 24) with a custom BoTorch-based Gaussian Process whose candidate design composes sparse-axis-aligned priors on hullmod booleans, transformed-overlap categoricals and attribute-Matérn on weapons, Matérn on slot coordinates, opponent-context features on small-slot posteriors, explicit conditional-slot handling, and item/slot residuals. Warm-starting and πBO-style archetype priors remain hypotheses for future optimizer experiments. No shipped code yet.

Design and research log for how the optimizer **represents and searches** the ship-build space. Phase 5 improves the *scoring* of builds (signal quality); Phase 7 improves the *surrogate model* that decides which builds to test next, by injecting stable structural priors (slot geometry, weapon attributes, archetype density, hullmod sparsity) that survive game updates.

Reading this doc cold: Phase 4 shipped the initial Optuna TPE optimizer over one-hot encoded weapons and hullmod booleans (CatCMAwM was in `_create_sampler` as a nominally-selectable alternative until 2026-04-19, when it was removed for being incompatible with our all-categorical search space). Phase 7 would replace that surrogate with a mixed-space GP whose kernel structure matches the known geometry of the ship-build problem — weapons have physics-driven attributes, slots have 2D coordinates, hullmods have sparse activity, and archetypes are stable across game patches. See `AGENTS.md` for the current phase status.

## Sequencing Update

The kernel design below remains the target optimizer architecture, but it is no
longer the first implementation step. The Phase 7 matchup-data substrate and
grouped surrogate validation must come first:

1. Rematerialize the generated matchup DB from completed Wave 1 and
   honest-eval artifacts.
2. Validate structured hull, weapon, hullmod, slot-geometry, opponent, and
   interaction features with grouped splits and trivial comparators.
3. Use the learned-surrogate research gate
   ([phase7-learned-surrogate-research.md](phase7-learned-surrogate-research.md))
   to derive the next learned-baseline experiment plan, including candidate
   model families, hyperparameter search spaces, nested grouped validation,
   calibration, feature-family selection, hierarchy-aware leakage controls,
   model-specific regularization, and provenance.
4. Try model-assisted search first: prior mean, candidate prefiltering, or
   active-learning allocation.
5. Implement the custom BoTorch sampler only after those cheaper gates show
   value.

The same hierarchy applies to optimizer-facing features. Before replacing the
sampler, the project should learn which feature families and interaction
templates generalize under held-out opponents, hulls, and archetypes. A custom
kernel over all available features is not automatically principled; kernel
blocks, priors, and acquisition features should be restricted or regularized
according to the nested grouped feature-selection evidence.

The current dated roadmap checkpoint is
[../reports/2026-05-11-validation-to-phase7-roadmap.md](../reports/2026-05-11-validation-to-phase7-roadmap.md).

This design is the synthesis of a multi-lane 2026-04-17 literature sweep plus
a follow-up compiler-autotuning deep-dive:

1. Hierarchical / tree-structured BO (Jenatton, Ma-Blaschko)
2. Mixed-categorical BO with attribute kernels (CoCaBO, SAASBO, HEBO, ICM)
3. Additive decomposition / factored kernels (Kandasamy, Gardner, Rolland, Ziomek)
4. NAS weight-sharing / supernet transferability (DARTS, ENAS, OFA, BigNAS)
5. Informed-priors / expert-guided BO (πBO, BOPrO, prior-mean GPs)
6. Game-AI deckbuilding / composition problems (Fontaine, Zhang DSA-ME, Ward MTG, Raidbots, DraftKings ILP)
7. Naval architecture design methodology (Evans spiral, SBD, SWBS, Andrews synthesis)
8. Starsector community meta (nine-archetype taxonomy, hullmod canon, opponent-conditional small slots)
9. Starsector data invariants (stable enum + CSV columns across 0.95→0.98)
10. Exploratory adjacent-field scan (compiler autotuning, protein engineering / MLDE, materials discovery, QD)

Plus the compiler-autotuning deep-dive that surfaced transformed-overlap
kernels and explicit conditional-parameter handling as candidate primitives to
validate, not as shipped implementation.

---

## 1. Problem

### 1.1 Combinatorial explosion meets expensive eval

A per-hull build is a tuple of `(hullmod subset ⊆ H, weapon-per-slot ∈ W × …, OP allocation ∈ ℝ^3)` where `|H| ≈ 30–80` available hullmods, `|W| ≈ 150` weapons per slot type, and there are ~8 slots per hull. The raw combinatorial set is ~2^80 × 150^8 ≈ 10^40. Each evaluation is expensive enough that sample efficiency is load-bearing. Per-hull trial budgets are pending re-validation under V2; see [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md).

The search problem is small-budget-expensive-combinatorial: typical BO regime. But two factors make the vanilla approach insufficient:

- **Effective dimensionality is far below the raw combinatorial count.** Most
  weapons at most slots are equivalent on coarse axes such as damage type,
  range, and flux efficiency. One-hot encoding over weapon IDs wastes
  information by treating equivalent items as independent.
- **Hullmod effects are expected to be sparse.** Community-meta analysis
  suggests a small subset of hullmods is usually load-bearing for a given
  archetype. This is a design hypothesis for sparse priors, not an internal
  empirical variance claim.

### 1.2 Current Optuna TPE pathologies

Phase 4's Optuna TPE with one-hot categoricals is a per-dimension KDE density-ratio sampler. It silently ignores cross-variable correlation (kernel-implicit independent-dim assumption), does not exploit known structure (damage-type semantics, slot coordinates, hullmod sparsity), and transfers zero prior knowledge across hulls or across game patches. Optuna's `multivariate=True, group=True` option partitions trials by active-parameter set but silently falls back to independent sampling when subspaces shrink — the dominant failure mode at our 200–2000 trial regime (Optuna issues #3325, #5631).

### 1.3 Game-update invariance constraint

The optimizer must not require re-tuning on game patches. Starsector ships roughly one major update per year; each adds/removes a handful of weapons and hullmods, occasionally rebalances flux costs or damage multipliers, very rarely changes enum semantics. The stable cross-version invariants are:

- **Enum sets**: `DamageType` (KINETIC/HE/ENERGY/FRAG), `SlotSize` (S/M/L), `SlotType` (BALLISTIC/ENERGY/MISSILE/COMPOSITE/HYBRID/SYNERGY/UNIVERSAL), `MountType` (TURRET/HARDPOINT/HIDDEN), `HullSize` (FRIGATE/DESTROYER/CRUISER/CAPITAL_SHIP). Never expanded across 0.95→0.98.
- **CSV schemas**: `weapon_data.csv`, `ship_data.csv`, `hull_mods.csv` column names are stable; values change but columns do not.
- **Slot geometry**: `.ship` JSON fields `location (x, y)`, `angle`, `arc`, `type`, `size`, `mount` are stable.

The optimizer must be able to encode its structural priors in terms of these invariants, not in terms of specific items. A new weapon with similar `(damage_type, weapon_type, size, op_cost, sustained_dps, flux_per_second, range)` attributes should land near existing weapons in the surrogate's input space *automatically*, without retraining the kernel.

### 1.4 Explicit requirement: small slots must remain addressable

The user's explicit correction during design review: rule-based small-slot fills are rejected because small slots are **opponent-conditional**, not cookie-cutter. Against missile boats, small slots carry Dual Flak + Vulcan + LR PD Laser + IPDAI; against carriers, small slots swap to fast-tracking flak (Vulcan is too short); against brawlers, HMG + LDAC; against phase flankers, wide-arc small energies for rear-arc coverage. The optimizer must retain the ability to tweak every small slot in response to opponent type. No silent hard fills.

---

## 2. Design grounding

### 2.1 Transformed-overlap kernel is the production categorical-handling primitive

Garrido-Merchán & Hernández-Lobato 2020 ("Dealing with Categorical and Integer-valued Variables in Bayesian Optimization with Gaussian Processes," *Neurocomputing*) introduce the transformed overlap kernel: for categorical `x_c ∈ {1..K}`, embed as a learnable K-dim real vector and apply a standard Matérn. The GP learns per-category lengthscales, so "kinetic small HVD" and "kinetic medium HVD" can be close on one axis but distant on another, without the Hamming brittleness of raw one-hot.

Hellsten et al. 2024 "BaCO: A Fast and Portable Bayesian Compiler Optimization Framework" (ASPLOS, [arXiv:2212.11142](https://arxiv.org/abs/2212.11142)) uses a weighted Matérn kernel over per-parameter distance measures for mixed compiler-optimization spaces, with Hamming-style distances for categorical parameters and explicit constraint handling. Its value here is evidence that carefully structured mixed-space BO can be practical in expensive discrete systems, not that BaCO itself supplies our transformed-overlap or sentinel design.

Inactive conditional slots in a future custom kernel should be modeled
explicitly, for example with sentinel categories or a tree/conditional kernel,
but the exact mechanism is a project design choice that must be validated
against alternatives. Do not cite BaCO as direct support for a learned
NA-similarity kernel.

### 2.2 Sparse axis-aligned subspaces (SAASBO)

Eriksson & Jankowiak 2021 "High-Dimensional Bayesian Optimization with Sparse Axis-Aligned Subspaces" (UAI, [arXiv:2103.00349](https://arxiv.org/abs/2103.00349)) place a half-Cauchy prior on inverse lengthscales: `τ ~ HC(0.1)`, inducing soft sparsity. Most dimensions get lengthscale → ∞ (effectively inactive), a few get learned small lengthscales (active). Crucially **dimensions remain addressable** — a dim that seemed irrelevant can activate as data accumulates. Validated to D=388 with 100–500 trials. BoTorch ships `SaasFullyBayesianSingleTaskGP`. Reported 2–5× vs vanilla GP-BO at D=60–388 in the 100–500-trial regime — *exactly our setting*.

SAASBO, additive BO, and tree-structured BO encode different assumptions. SAAS
targets sparse axis-aligned relevance; additive BO targets decomposable
low-dimensional structure; tree kernels target conditional structure. The
Phase 7 design should treat SAAS as a strong candidate for high-dimensional
hullmod-style blocks, not as a proof that additive or conditional kernels are
unnecessary.

SAAS fits the expected sparse hullmod-boolean subspace. The half-Cauchy prior
encodes "most dimensions should shrink toward irrelevance" as a structural
prior while keeping every dimension addressable.

### 2.3 Attribute kernels + per-item ICM residuals

Swersky, Snoek & Adams 2013 "Multi-task Bayesian Optimization" (NeurIPS, [arXiv:1309.6835](https://arxiv.org/abs/1309.6835)) and the broader Intrinsic Coregionalization Model (ICM) literature (Bonilla et al. 2007, Álvarez & Lawrence 2011 [arXiv:1106.6251](https://arxiv.org/abs/1106.6251)) support information sharing across related tasks. The following **attribute prior plus item residual** form is a project design hypothesis inspired by that literature, not a direct claim from Swersky et al.:

`f(w) = m(φ(w)) + g(w)`

where `m` is a GP/linear model over the attribute vector `φ(w)` and `g(w)` is a
per-item residual with shrinkage prior. A new weapon with similar stats should
inherit the attribute-side prior in this design, but future reports must
validate whether this zero-observation transfer works in actual game data.

This is structurally identical to the Phase 5D Empirical-Bayes fusion paradigm: `α̂_TWFE` and the 7-covariate heuristic prior are combined as noisy measurements of the same latent `α`. Re-using the fusion paradigm at the surrogate level keeps the architecture coherent across phases.

The initial attribute vector for weapons is the following tuple, chosen from
schema-stable weapon descriptors and prior Phase 5 feature work:

| Attribute | Source | Stability |
|---|---|---|
| `damage_type` | `weapon_data.csv:type` (enum) | Stable (enum never expanded) |
| `weapon_type` | `.wpn` JSON (enum: BALLISTIC/ENERGY/MISSILE) | Stable |
| `size` | `.wpn` JSON (enum: S/M/L) | Stable |
| `op_cost` | `weapon_data.csv:OPs` | Column stable; values may rebalance |
| `sustained_dps` | computed from `dps`, `burst size`, `chargeup`, `chargedown` | Derived; columns stable |
| `flux_per_second` | `weapon_data.csv:energy/second` | Column stable |
| `range` | `weapon_data.csv:range` | Column stable |

All seven are present in every Starsector version since 0.95a. A new weapon added in a patch inherits an informative prior from its attribute neighbors on day one.

**Hull-size normalization.** Raw `range` and `op_cost` do not transfer across hull sizes: 700 range is long for a frigate and short for a capital; 10 OP is a third of a frigate's budget and a tenth of a capital's. The kernel inputs use normalized forms — `range / max_weapon_range_per_hull_size` (per-hull-size maximum over reachable weapons) and `op_cost / hull.ordnance_points` — plus `hull_size` as a categorical kernel context feature (4-level ordinal over FRIGATE/DESTROYER/CRUISER/CAPITAL_SHIP). With this normalization, the attribute kernel learns hull-size-invariant structure (e.g., "high normalized range is good on forward-arc slots") and the `hull_size` context lets it still express hull-size-specific effects when the data demands.

### 2.4 Slot-feature vector enables cross-slot kernel similarity

Within a single hull, slot coordinates are constant across trials, so they cannot enter the kernel as varying inputs. But the kernel can use them to **share information across slots of the ship**. A ship with 8 slots labelled as opaque indices gives 8 independent decisions; the same 8 slots encoded by `(forward_projection, arc_width, is_turret, lateral_offset, longitudinal_offset)` reveal that (say) 3 forward hardpoints + 2 flank turrets + 3 rear PD smalls cluster into ~3 effective decisions. A kinetic HVD validated on slot-1 (forward hardpoint) now informs slot-2 and slot-5 posteriors if their slot features are nearby.

Slot-feature vector (5-dim, all sourced from `.ship` JSON):

| Feature | Definition | Meaning |
|---|---|---|
| `forward_projection` | `cos(slot.angle)` | 1 = pure bow, −1 = pure stern |
| `arc_width` | `slot.arc / 360` | Turret ≈ 1; hardpoint ≈ 0.03 |
| `is_turret` | `slot.mount == TURRET` | Swivel vs fixed-facing |
| `lateral_offset` | `|slot.x| / hull_half_width` | Centerline vs broadside |
| `longitudinal_offset` | `slot.y / hull_length` | Bow vs stern |

Invariance: `.ship` JSON fields are stable schema across all Starsector versions. The feature vector transfers to multi-hull surrogates for free (deferred; see §3.11).

### 2.5 πBO decay-weighted priors + hull-conditional archetype activation

Hvarfner et al. 2022 "πBO: Prior-guided Bayesian Optimization" (ICLR, [arXiv:2204.11051](https://arxiv.org/abs/2204.11051)) multiplies the acquisition function by a user-supplied prior density `π(x)` with a decay schedule, so prior influence weakens with trial count. The paper reports large benchmark-specific speedups when priors are useful and studies misleading-prior behavior, but it does not provide a universal constant-factor wrong-prior guarantee. Souza et al. 2021 BOPrO ([arXiv:2006.14608](https://arxiv.org/abs/2006.14608)) is related prior-guided BO evidence.

The community-meta research lane extracted **nine role archetypes** that appear
stable across recent vanilla releases:

| Archetype | Characteristic large-mount weapons | Defining trait |
|---|---|---|
| **SO brawler** | Heavy Mauler / HVD / Heavy Autocannon or Plasma Cannon; all range < 700 | Safety Overrides SMod, reckless officer, no long-range slots |
| **Long-range sniper** | Gauss, Mark IX, HIL, Autopulse, Tachyon Lance | ITU + Expanded Mags, max-range engagement |
| **Kinetic-HE brawler** | HVD + Mark IX + Hellbore / Mjolnir | Kinetic shield-break + HE armor-crack; two-weapon rule |
| **Broadside** | Gauss ×2 + Mark IX ×2 | Conquest-style, flux-coil hullmod |
| **Turret-flex / shield-tank** | Autopulse, Tachyon Lance, HIL | Paragon-style; ATC doubles non-PD range |
| **Burst-missile** | Squall + Locust + Hurricane MIRV | Expanded Missile Racks SMod; ITU *not* taken (missiles ignore ITU) |
| **PD-carrier / escort** | Mostly small/medium flak + Dual Flak | Flak + IPDAI; screens snipers |
| **Flanker / glass cannon** | Plasma Cannon + Heavy Blaster | Aurora-style Plasma Burn hit-and-run |
| **Phase striker** | Reaper + AM Blaster | Inverse flux rule (caps > vents) |

Each archetype can be represented as a region in **normalized** attribute ×
slot-feature × hullmod-subset space rather than as a fixed weapon list.
Encoding those regions as a πBO-style mixture density is a design hypothesis;
the decay schedule should be tuned and reported as part of the optimizer
experiment, not treated as a fixed guarantee.

**Three failures a naïve hull-agnostic weighting would hit.** The 9-archetype vocabulary above was induced from community analysis that concentrates on ~7 meta hulls (Onslaught, Paragon, Conquest, Aurora, Hammerhead, Eagle, Odyssey). Applied uniformly to any hull it fails on:

1. **Physical infeasibility.** Some archetypes require specific slot geometry or ship systems. Broadside needs 4+ large ballistic slots grouped broadside (Conquest-only). Turret-flex shield-tank needs Advanced Targeting Core as ship system (Paragon-only). Burst-missile needs sufficient missile mount count. Phase striker needs a phase cloak. A Wolf (frigate, 3 small slots, no capital-ship system) cannot realize six of the nine modes.
2. **Absolute-attribute mismatch.** "Long-range sniper" resolves to > 1500 range on a capital (Gauss, Mark IX) and > 700 on a frigate (Pulse Laser, LR PD Laser). If the mode's mean is a fixed absolute range, the frigate's best weapons never match it.
3. **Meta-hull coverage bias.** Community meta covers ~7 hulls in depth; the remaining ~40 combat hulls (Gemini, Vanguard, Falcon, Shrike, Omen, Medusa, etc.) have sparse published taxonomy, and mod or patch-added hulls have none. Hardcoding weights from meta-hull analysis projects that bias onto hulls the community has not analyzed.

**Three additions that make the archetype prior generalize.**

a. **Hull-feasibility mask.** For each (hull, archetype) pair, pre-compute physical realizability from `.ship` JSON + ship-system data (slot-size/type counts, ship-system availability, phase-cloak presence). Infeasible modes get zero weight in the πBO mixture. Automatic, data-driven, survives mod and patch additions.

b. **Attribute normalization + `hull_size` context.** Resolved at §2.3. Archetype modes defined in the normalized space work across all hull sizes without per-hull rescaling; `hull_size` as a categorical context feature lets the kernel still express hull-size-specific effects where data demands.

c. **Uniform initial mixture weights over feasible modes.** Instead of hardcoding
meta-derived weights, start uniform across the modes left feasible by (a) for
the given hull. Online reweighting of mode credibility is a possible future
extension, but it is project design work and must not be attributed to SCoreBO.
Hvarfner et al. 2023 "Self-Correcting Bayesian Optimization through Bayesian
Active Learning" ([arXiv:2304.11005](https://arxiv.org/abs/2304.11005)) is
relevant to active GP hyperparameter learning, not a ready-made archetype-mode
reweighting rule.

The community meta's role is therefore to supply the **vocabulary of modes** (what "sniper" vs "brawler" means in normalized attribute space), not the **per-hull weights** (which the optimizer learns from data). This preserves the community insight while removing the meta-hull bias.

A fourth failure — related but distinct — is that community meta builds are often designed for **player piloting** (SO brawler, phase striker, burst-missile) and the AI mispilots them in simulation. §2.10 covers this as a separate grounding point. Any future archetype prior should treat AI-pilotability as an empirical diagnostic and should not rely on an unvalidated adaptive-weighting rule to remove AI-hostile modes.

### 2.6 BOCA importance pilot as SAAS warm-start

Chen et al. 2021 "BOCA" (ICSE) runs a 30-trial random-forest-importance pilot before the main BO, partitioning compiler flags into impactful vs unimpactful and then running BO over only the impactful set. In isolation BOCA commits hard to the pilot's verdict — a miscalled flag gets randomized forever, no recovery.

SAAS provides a reversible sparse prior that addresses the same broad
"most dimensions may not matter" intuition without permanently removing
dimensions. BOCA-style random-forest importance is therefore best treated as a
possible warm-start diagnostic for SAAS priors, not as a hard dimensionality
reduction step. Trial counts and prior initializations must be chosen in a
future experiment plan.

### 2.7 Naval architecture: Evans spiral and platform/mission split

Evans 1959 "Basic Design Concepts" (*ASNE Journal*) introduced the Design Spiral: iterate (mission → hull form → propulsion → structure → weights → stability → cost) outer-to-inner, committing first to hull/propulsion/principal-dimensions and fitting armament into the remaining budget. Still taught in Lamb (ed.) *Ship Design and Construction* (SNAME 2003).

Brown & Salcedo 2003 "Multiple-Objective Optimization in Naval Ship Design" (*NEJ*) and Andrews 2003 "A Creative Approach to Ship Architecture" (*IJME*) formalize the **platform vs mission-system split**: platform (hull, propulsion, damage control) is fixed in early design; mission system (weapons, sensors, C4I) is modular to allow mid-life upgrades. Literal slots in USS flights: MK 41 VLS, Stanflex, USN Modular Mission Packages.

Mapping:

| Naval concept | Ship-build analog | Locked in Phase 7 design |
|---|---|---|
| Design Spiral outer ring | `(hull, regime)` study pair (Phase 5F) | Per-study outer loop |
| Design Spiral inner ring | `(weapons, hullmods, OP allocation)` | GP surrogate decision variables |
| Platform vs mission-system split | `hull` is fixed per study; weapons + hullmods search | Same split |
| SWBS weight-group decomposition | OP category allocation (weapons / flux / hullmods) | Continuous 3-vector in GP |
| Primary-armament-drives-hull (capitals) | Large slots condition medium/small via πBO archetype | Archetype prior is a soft expression of this |
| Hull-drives-armament (escorts) | Hull dictates max-size available | Already enforced by `repair_build` |

Set-Based Design (Singer-Doerry-Buckley 2009) *does not transfer*: SBD is a multi-team concurrent-engineering methodology driven by coordination cost, not by computation cost. We have one optimizer, not 12 engineering disciplines.

### 2.8 Starsector community meta: opponent-conditional small slots

The strongest consensus finding across fractalsoftworks forum threads (topics 24219 Ship Loadout Guide, 25973 Fossic Vanilla Weapon Guide, 23570 Builds for 0.95.1a), Starsector wiki Combat Guide / Refit Screen pages, and the IroncladLion SOG: **mediums and larges are hull-conditional; smalls are opponent-conditional.**

| Opponent type | Small-slot loadout shift | Explanation |
|---|---|---|
| Missile boats (Gryphon, LP fleets) | Dual Flak + Vulcan + IPDAI | Defeat missile alpha; IPDAI +50% vs missiles |
| Carriers | Flak + fast-tracking mediums (Vulcan *bad* — too short) | AoE vs fighter swarms |
| Brawlers / heavy armor | HMG + LDAC (anti-armor follow-up) | Kinetic cracks shields; HMG finishes armor |
| Phase flankers | Wide-arc small energies (IR Pulse, LR PD Laser) | Cover rear arc, time shields |

This is **the load-bearing empirical constraint**: the optimizer must retain the ability to tweak small slots in response to opponent features. The historical kernel proposal encoded this as **opponent summary features as additional kernel inputs on small-slot posteriors only** (see §3.9).

Hullmod canon — items that survived 4 major patches as S-tier: ITU, Hardened Shields, Expanded Magazines, Auxiliary Thrusters, Resistant Flux Conduits, IPDAI. Stable priors for πBO's hullmod-subspace density.

### 2.9 Starsector data invariants: stable across 0.95 → 0.98

Audit of `parser.py`, `models.py`, and the CSV + `.ship` JSON schemas confirms that every enum listed in §1.3 has never been expanded. Schema-stable columns used by the historical kernel proposal:

- `weapon_data.csv`: `id`, `name`, `type`, `damage/second`, `damage/shot`, `OPs`, `range`, `chargeup`, `chargedown`, `burst size`, `burst delay`, `energy/shot`, `energy/second`
- `ship_data.csv`: `id`, `name`, `designation`, `ordnance points`, `max flux`, `flux dissipation`, `hitpoints`, `armor rating`, `shield type`, `shield efficiency`
- `hull_mods.csv`: `id`, `name`, `tier`, `cost_frigate`, `cost_dest`, `cost_cruiser`, `cost_capital`, `hidden`
- `.ship` JSON per hull: `weaponSlots[].id`, `.type`, `.size`, `.mount`, `.angle`, `.arc`, `.locations`

Forward compatibility: `_ParseableEnum.from_str()` (`parser.py`) returns `None` for unknown values and the parser logs a warning and skips the record. The kernel design never hardcodes specific weapon IDs, specific hullmod IDs, or specific tags. Only the enums (stable forever) and CSV columns (stable forever) are referenced in the kernel specification.

Manifest-as-oracle caveat: hullmod applicability, conditional exclusions, and
damage multipliers are owned by the generated game manifest, not by Phase 7
reference text or Python hardcoded rule tables. Phase 7 consumes those domain
facts through the existing manifest-backed repair and search-space boundaries;
it should not reintroduce a parallel hullmod-rule registry.

### 2.10 AI pilotability is a load-bearing evaluation constraint

Combat simulation in the Phase 2 harness is **AI-vs-AI**: neither side is player-controlled. The deployment distribution for optimizer outputs is therefore "ship in a fleet piloted by the vanilla Starsector combat AI," not "ship piloted by the player." This is a substantive premise that interacts with the πBO archetype prior.

Community meta is not uniformly compatible with this premise. A well-documented pattern in the Starsector community (fractalsoftworks forum balance threads; wiki Combat Guide) is that the AI mispilots several archetypes:

| Archetype | AI pilotability | Why |
|---|---|---|
| Turret-flex shield-tank (Paragon) | High | AI manages shield + long-range engagement well |
| Kinetic-HE brawler (Onslaught) | High | AI handles brawling + armor trading |
| Long-range sniper | High | AI holds range, manages flux |
| PD-carrier / escort | High | AI does not need to micromanage weapons |
| Broadside (Conquest) | Medium | AI sometimes mismanages lateral maneuver to keep broadside facing |
| Kinetic-HE hybrid + missiles | Medium | AI uses missiles at suboptimal times |
| SO brawler | **Low** | AI does not always commit to close range; may hang back in SO's dead zone |
| Burst-missile (Gryphon) | **Low** | AI fires Squall/Locust/Hurricane volleys at wrong targets / wrong times |
| Phase striker | **Low** | AI cloaks and uncloaks at suboptimal moments; community consensus that phase ships require player control |
| Flanker (Aurora) | Medium-low | AI can overcommit Plasma Burn and get caught |

Builds that are top-tier in community meta but rely on player-only piloting (SO brawler with skill-5 Helmsmanship, phase striker with perfectly-timed cloaks, burst-missile with manually-ordered volleys) may score poorly in AI-vs-AI sim. Conversely, builds the community dismisses as "too AI-friendly" may genuinely dominate the sim because the simulator's evaluation context matches those builds' design assumption.

**Implication for Phase 7.** Community meta should contribute the vocabulary of
modes, not fixed per-hull weights. Uniform feasible-mode priors avoid
hardcoding AI-pilotability assumptions, but they do not by themselves solve
AI-hostile archetypes. Any future online mode-reweighting rule must be
implemented and validated as project design work before it can be used as a
claim-bearing mechanism.

**If the combat harness varies AI personality** (Cautious / Steady / Aggressive / Reckless from `PersonalityAPI`) or officer skills across trials, the varying fields enter the kernel as additional ordinal/boolean context features on the player side (same pattern as the opponent-summary features on small slots, §2.8). If the harness fixes personality to Steady and runs un-officered (the Phase 2 default), these are simulation constants and do not need to enter the kernel. The current Phase 7 ship assumes the latter — default-personality, un-officered — which matches the typical fleet deployment case (most ships in a campaign fleet have no officer). Officer-conditional or personality-conditional optimization is a Phase 7.1 extension, not part of the initial ship.

**Weapon-group assignment.** Stock `.variant` files hand-tune `weaponGroups` with per-group `autofire` and `mode` (LINKED / ALTERNATING) — notably `autofire=false` on ammo-limited missiles and ALTERNATING pairings on expensive ballistics. The combat harness builds variants via `autoGenerateWeaponGroups()` (`VariantBuilder.java:48`); `BuildSpec` carries no group metadata from Python. Every optimizer-produced build therefore runs on the engine's default grouping algorithm, which may not reproduce the hand-tuned behaviour of community reference builds. This is a fidelity limitation, not something the surrogate can make disappear. A future prior or residual model may learn that grouping-sensitive archetypes underperform, but lifting the limitation requires adding weapon-group decisions to the search space. The roadmap report records this as an explicit deferral.

**Fighter-wing assignment is out of scope.** `LAUNCH_BAY` slots are filtered out of `SearchSpace.weapon_options` (`search_space.py:157`); `BuildSpec` has no wing field; `VariantBuilder.java` never populates wings; `ManifestDumper.java` does not enumerate `FighterWingSpecAPI` or `wing_data.csv`. Carrier hulls (24 in vanilla: Astral 6 bays, Legion 4, Heron/Mora 3, Condor/Drover/Apex 2, …) are optimizable for non-bay slots only; their bays deploy empty. Per §3.8 below, the PD-carrier archetype's LAUNCH_BAY branch is currently infeasible — only the small-slot-PD branch (Hammerhead-style destroyer escorts) is realized. Mentioned here for completeness; feasible-mode masking should exclude carrier-only modes under the current search-space scope.

### 2.11 What the sweep ruled OUT

The research sweep and compiler-autotuning deep-dive disqualified several
directions that sounded plausible a priori. See §4 for the full
rejected-alternative chain. Short version: NAS weight-sharing does not transfer
to non-neural settings; REMBO/ALEBO linear-random-subspaces fail on within-group
interactions; BOCS requires binary-only indicators; GFlowNets need training
signal volumes we do not have; Hearthstone MESB behavior-descriptor search
needs a phenotype-to-genotype map we cannot build cheaply; conjoint-analysis
ACBC is a human-respondent paradigm, not a black-box-sim paradigm; full RL /
Q-DeckRec / MuZero require much larger evaluation volumes.

Two ideas survived as alternatives to compare rather than as fully rejected
primitives: tree/conditional kernels, and HyperMapper/BaCO-style off-the-shelf
mixed-space BO. See §4.

---

## 3. Target Design

### 3.1 Composite kernel

The surrogate is a single Gaussian Process whose kernel is the **product** of subspace-specific kernels:

```
k(x, x') =   k_hullmods(h, h')          # SAAS-prior Matérn on hullmod booleans
           · k_weapon_id(w, w')          # transformed-overlap per slot
           · k_weapon_attr(φ(w), φ(w'))  # 7-attribute Matérn per slot (range, op_cost hull-normalized)
           · k_hull_size(hs, hs')        # 4-level ordinal Matérn context (§2.3 normalization)
           · k_slot(s, s')               # 5-dim slot-feature Matérn
           · k_opp_small(o, o')          # opponent features, small slots only
           · k_op(op, op')               # OP allocation Matérn (3-dim)
           · 1_{conditional}(x, x')      # explicit conditional-slot handling
         + k_item_residual(w)            # ICM per-weapon residual (shrinks to 0)
         + k_slot_residual(s)            # ICM per-slot residual (shrinks to 0)
```

Product-kernel structure is inspired by mixed-space BO systems such as BaCO.
Additive per-item and per-slot residuals are project design hypotheses inspired
by ICM/multi-task GP literature. Conditional-slot handling remains an explicit
design choice to compare against tree/conditional kernels. SAAS prior follows
Eriksson-Jankowiak 2021.

### 3.2 Historical Candidate Design: BoTorch as Optuna sampler plugin

This section is historical rationale for a possible future implementation, not
an approved module contract or current plan. Any executable scope, module
names, defaults, and tests must be reintroduced through a future active plan
and, where appropriate, a spec.

BoTorch (Balandat et al. 2020) provides `SaasFullyBayesianSingleTaskGP` out of the box and supports custom kernels via the `Kernel` base class. Optuna supports custom samplers via `BaseSampler`. Integration:

- Custom BoTorch kernel implementing the §3.1 product structure.
- Custom Optuna sampler (`BoTorchSampler` subclass) that wraps the kernel and exposes the ask-tell interface `optimizer.py` already consumes.
- Posterior inference via NUTS / fully-Bayesian MCMC (SAASBO default) was the
  historical candidate. Runtime cost must be re-estimated in a future
  experiment plan before any implementation.
- HyperMapper ([github.com/luinardi/hypermapper](https://github.com/luinardi/hypermapper)) and BaCO-style implementations are useful references for mixed-space BO behavior; use them as comparison baselines or validation aids, not as proof that the custom kernel is correct.

Earlier schedule estimates are no longer authority. The current roadmap gates
learned baselines and model-assisted search before custom-kernel work.

### 3.3 SAAS prior on hullmod subspace

`k_hullmods` is a Matérn-5/2 with per-dimension inverse lengthscales `τ_d ~ HalfCauchy(0.1)` (Eriksson-Jankowiak 2021 default). The soft-sparsity prior encodes the design belief that only a subset of hullmods is load-bearing for a given build. All dimensions remain addressable; none are hard-zeroed.

### 3.4 Transformed-overlap + attribute-Matérn product on weapons

Per slot, two weapon-side kernels multiplied together:

- `k_weapon_id`: transformed-overlap kernel over weapon IDs (Garrido-Merchán & Hernández-Lobato 2020). Captures item-specific synergy quirks that attributes do not encode.
- `k_weapon_attr`: Matérn-5/2 over the 7-attribute vector `(damage_type, weapon_type, size, op_cost, sustained_dps, flux_per_second, range)`. Captures smooth response to physical attributes and enables game-update transfer.

A new weapon added in a future patch inherits the full attribute-kernel prior on day one; only the transformed-overlap residual needs observations to calibrate.

### 3.5 Slot-feature Matérn for cross-slot kernel similarity

`k_slot` is a Matérn-5/2 over the 5-dim slot-feature vector `(forward_projection, arc_width, is_turret, lateral_offset, longitudinal_offset)`. Within a hull, this pools observations across slots of similar geometry — a kinetic HVD validated in slot 1 (forward hardpoint, high forward-projection, low arc-width, is_turret = 0) informs posteriors for slots 2 and 5 if their slot features are nearby.

Bonus: for a future multi-hull surrogate, the slot-feature vector is hull-agnostic — a forward hardpoint on a Hammerhead looks similar in feature space to a forward hardpoint on an Onslaught. Built-in multi-hull transfer at zero marginal cost.

### 3.6 Conditional-slot handling

Inactive slots (e.g., hull `X` has only 7 weapon slots, so slot 8 is inactive)
must be represented explicitly. Candidate designs include sentinel categories,
masked kernels, or a tree/conditional kernel. A learned sentinel-similarity
kernel is a project proposal to validate; it should not be attributed to BaCO.

### 3.7 Per-item and per-slot ICM residuals

Two additive residual kernels, each with a strong shrinkage prior toward zero:

- `k_item_residual(w)`: GP over weapon-ID with a `HalfNormal(σ_item)` prior on amplitude. Captures item-specific effects not predicted by the 7-attribute vector (e.g., Antimatter Blaster's specific ship-vulnerability synergy).
- `k_slot_residual(s)`: GP over slot-ID with `HalfNormal(σ_slot)` prior. Captures slot-specific quirks not predicted by the 5-dim slot vector (e.g., a bow-occluded slot that can't actually fire forward).

Both shrink toward zero in the empirical-Bayes sense — the data must supply positive evidence of a quirk before the residual moves. This is structurally the same fusion paradigm used in Phase 5D (α̂ fusion with 7-covariate heuristic prior).

### 3.8 πBO archetype priors — hull-conditional mixture

The acquisition function `α(x)` may be multiplied by a Gaussian-mixture prior
density `π(x)` over modes defined in the **normalized** attribute × slot ×
hullmod space (§2.3 normalization), with a decay schedule following πBO-style
prior weakening. The nine modes are the community-meta archetypes (§2.5).
The decay parameter is a tunable design choice and must be selected under the
same optimizer-evaluation protocol as other acquisition settings.

**Per-hull activation (§2.5).** At hull-load time, compute the feasibility mask `{mode_k : hull can physically realize mode_k}` from `.ship` JSON + ship-system data. Concretely:

| Mode | Feasibility predicate |
|---|---|
| SO brawler | Has `safetyoverrides` as an applicable hullmod; hull_size ≤ CRUISER |
| Long-range sniper | Has ≥ 1 LARGE ballistic/energy mount with sufficient OP for ITU |
| Kinetic-HE brawler | Has ≥ 2 LARGE or MEDIUM mounts across mixed ballistic types |
| Broadside | Has ≥ 4 LARGE mounts grouped (max pairwise lateral spread < threshold) |
| Turret-flex shield-tank | Ship-system ∈ {Advanced Targeting Core} (Paragon-only in vanilla) |
| Burst-missile | Has ≥ 2 MEDIUM or LARGE missile mounts |
| PD-carrier / escort | Has hull_size ≤ DESTROYER with ≥ 4 smalls. (LAUNCH_BAY-slot branch deferred — fighter wings are not in the optimizer's decision space; see §2.10 fidelity floor.) |
| Flanker / glass cannon | Has Plasma Burn or equivalent high-mobility ship-system; base speed > threshold |
| Phase striker | Has phase-cloak capability |

Infeasible modes get zero mixture weight, so the prior density collapses onto only what the hull can actually be. Mod and patch-added hulls are handled automatically — the mask reads `.ship` JSON + ship-system registry without per-hull hardcoding.

**Initial weights uniform over feasible modes** (not community-derived) to avoid the meta-hull coverage bias. Mode *definitions* come from community meta; mode *weights* are learned from data.

**Optional future mode reweighting.** Online reweighting of feasible archetype
modes is a possible extension after static feasible-mode priors are validated.
It is not shipped and is not a direct result of SCoreBO. Any implementation
must define the update rule, compare it against fixed uniform feasible-mode
weights, and report whether it improves optimizer efficiency.

**Mode definition invariance.** Because modes are points in normalized attribute + slot-feature + hullmod space — never in weapon-ID space — patches that rename or rebalance individual weapons do not invalidate the mode definitions. A mode "long-range sniper" is `(range_normalized > 0.7, flux_per_damage_normalized < 0.4, forward_projection > 0.8)`; the next patch's new weapons that satisfy this triple automatically match the mode.

### 3.9 Opponent features on small-slot posteriors only

For each trial, a 3-dim opponent summary feature vector is computed from the opponent pool:

| Feature | Definition |
|---|---|
| `has_missiles_frac` | Fraction of opponents with any MISSILE-type weapon |
| `has_fighters_frac` | Fraction of opponents with any launch bay |
| `mean_armor_rating` | Mean of opponent-pool `armor_rating` |

These are concatenated only to small-slot kernel inputs (size = SMALL). Medium and large slots are hull-conditional (from §2.8) and do not get opponent features. This encodes the "smalls are opponent-conditional, larges are hull-conditional" empirical rule without hardcoding opponent-type buckets.

Alternative considered and rejected: separate GP per opponent-type bucket. Rejected because it fragments the dataset (200 trials → 50-per-bucket) and loses the smooth-generalization benefit of continuous opponent features.

### 3.10 BOCA 30-trial warm-start pilot

For each new `(hull, regime)` pair (Phase 5F units), the first 30 trials are a Latin-hypercube-seeded random sample, with random-forest importance scores computed over the resulting `(trial → fitness)` dataset at trial 30. The importance scores initialize SAAS lengthscale priors empirical-Bayes style: high-importance dimensions get tighter prior lengthscales (more active); low-importance get looser (more inactive).

Drops out once the main BO has accumulated enough of its own evidence. Cheap
insurance against the cold-start problem on hulls the optimizer has never seen.

### 3.11 Relationship to other phases

Phase 7 is **orthogonal to Phase 5**. It changes the optimizer's surrogate, not its fitness estimator, shape transform, opponent curriculum, or feasibility mask:

| Phase | What it does | Phase 7 impact |
|---|---|---|
| 4 | Optuna TPE over one-hot (CatCMAwM removed 2026-04-19) | Replaced by custom BoTorch sampler |
| 5A (TWFE) | α_i + β_j decomposition | Unchanged; TWFE output feeds GP like any fitness |
| 5B (WilcoxonPruner + ASHA) | Multi-fidelity pruning | Unchanged; pruning is upstream of the GP |
| 5C (anchor-first + incumbent overlap) | Opponent schedule | Provides the opponent features (§3.9) to the GP |
| 5D (EB shrinkage) | α̂ fusion with 7-covariate prior | Unchanged; 5D and 6 share the ICM fusion paradigm but at different layers |
| 5E (Box-Cox shape) | Fitness-shape transform at A3 | Unchanged; Box-Cox output is the GP's `y` |
| 5F (regime segmentation) | Feasible input set | Phase 7 runs one GP per `(hull, regime)` pair; one BOCA pilot per pair |
| 5G (adversarial curriculum) | Deferred | No dependency |

### 3.12 Single-hull and multi-hull scope

Initial ship is single-hull per study. Multi-hull surrogate (one GP across all hulls, transferring slot and archetype kernels) is a natural Phase 7+ extension once the single-hull kernel is validated. The slot-feature vector (§3.5) is hull-agnostic by construction, so multi-hull transfer requires only `hull_id` additional categorical features — not a redesign.

---

## 4. Rejected alternatives

### 4.1 Keep Optuna TPE with attribute-only warm-start — INSUFFICIENT

**What it was.** Leave the Optuna TPE sampler untouched; add attribute-vector-based warm-start seeding (30–60 Latin-hypercube seeds) and optional archetype-ranked seed ordering.

**Why rejected.** TPE's per-dimension independent KDE structure cannot exploit
kernel structure such as transformed-overlap categoricals, slot-feature
similarity, or sparse lengthscale priors. Warm-start helps only the initial
proposal set and provides no ongoing structural sharing. The expected
efficiency advantage of richer kernels remains a design hypothesis that future
reports must measure.

### 4.2 Ma-Blaschko additive tree kernel — DEFERRED COMPARATOR

**What it was.** Ma & Blaschko 2020 "Additive Tree-Structured Conditional Parameter Spaces" (TPAMI [arXiv:2010.03171](https://arxiv.org/abs/2010.03171)) — additive tree kernel where overlapping subtrees share priors via ancestors. Explicit hierarchical structure: `role → large-slot → medium → small`.

**Why deferred.** Tree kernels address conditional structure directly, while
SAAS addresses sparse axis-aligned relevance. They are not substitutes. The
first implementation should prefer simpler conditional-slot handling plus
SAAS-style sparsity, then compare tree/conditional kernels if residual errors
show that conditional structure is the bottleneck.

### 4.3 NAS weight-sharing (DARTS / ENAS / OFA / BigNAS) — DOES NOT TRANSFER

**What it was.** One-shot NAS trains a supernet whose weights are shared across candidate architectures; evaluating a sub-architecture is a cheap forward pass through a pre-trained tensor.

**Why rejected.** The "weights" in NAS are trained tensors; the analog in ship-build optimization does not exist. The expensive thing in our problem is the *simulation*, not any learned parameters, so there is nothing to share. Predictor-based NAS (White 2021 BANANAS [arXiv:1910.11858](https://arxiv.org/abs/1910.11858)) is materially the same as BO with a neural surrogate — no new primitive. The only genuinely transferable idea is component-level rating via TWFE / TrueSkill / hierarchical shrinkage, which Phase 5A already ships and Phase 5D will extend. Stay the course; do not rebrand as NAS.

### 4.4 HyperMapper / BaCO off-the-shelf — MISSING SAAS SUBSPACE

**What it was.** Wrap HyperMapper ([github.com/luinardi/hypermapper](https://github.com/luinardi/hypermapper)) or a BaCO-style mixed-space optimizer as an Optuna sampler.

**Why rejected.** HyperMapper does not implement SAAS sparsity on subspaces. For our hullmod-boolean subspace (~30–80 dims, ~5–10 active) this is the load-bearing advantage; losing it gives up a majority of the sample-efficiency gain. The 1–2 week shortcut turns into a worse kernel; the 2–3 week custom BoTorch build captures everything.

Use HyperMapper/BaCO-style implementations as comparison baselines for
mixed-space behavior, not as evidence for transformed-overlap or learned
sentinel claims that BaCO does not implement.

### 4.5 Pure SAASBO (no mixed-space kernel) — BAD CATEGORICAL HANDLING

**What it was.** `SaasFullyBayesianSingleTaskGP` with one-hot categoricals.

**Why rejected.** SAAS handles sparsity but not categorical structure — it treats 150-level one-hot weapon vectors as 150 independent sparse dimensions, wasting the attribute-vector information. Bad fit for the weapon subspace. Retain SAAS only on the hullmod subspace; use transformed-overlap + attribute-Matérn on weapons.

### 4.6 BOCS horseshoe monomials — BINARY-ONLY

**What it was.** Baptista & Poloczek 2018 "Bayesian Optimization of Combinatorial Structures" ([arXiv:1806.08838](https://arxiv.org/abs/1806.08838)) — sparse Bayesian linear regression over monomials of binary indicators with a horseshoe prior.

**Why rejected.** BOCS is designed for binary-only search spaces. Our weapon dimensions are 150-level categoricals; encoding as binary indicator clusters loses attribute-vector structure and balloons dimensionality. Retain the horseshoe idea as the SAAS prior on the hullmod subspace, where it fits natively.

### 4.7 GFlowNets for combinatorial assembly — BUDGET MISMATCH

**What it was.** GFlowNet family (SynFlowNet [arXiv:2405.01155](https://arxiv.org/abs/2405.01155), RGFN, Genetic-guided GFN [arXiv:2402.05961](https://arxiv.org/abs/2402.05961)) — generative flow network trained to sample from a proportional reward distribution.

**Why rejected.** Requires training-signal volumes we do not have at 200–2000 trials without a learned proxy. Would require Phase 8 neural surrogate as prerequisite. Defer indefinitely; not on critical path.

### 4.8 Fantasy-sports ILP — PRESUMES PRE-FIT PROJECTIONS

**What it was.** DraftKings/FanDuel-style integer linear programming over per-component projected points with salary-cap and position-slot constraints.

**Why rejected.** The "projected points per player" model is exactly the thing we are trying to build (the surrogate). ILP is not an optimization method *instead* of BO; it is a cheap downstream operator *given* a surrogate. If we had a reliable per-(weapon, slot) fitness-contribution model, ILP would be a candidate for the final pick step. Until then it is not applicable.

### 4.9 Hearthstone MESB behavior-descriptor priors — REQUIRES PHENOTYPE→GENOTYPE MAP

**What it was.** Fontaine et al. 2019 "Mapping Elites with Sliding Boundaries" ([arXiv:1904.10656](https://arxiv.org/abs/1904.10656)) — MAP-Elites over 2D behavior descriptor (mana curve, minion count) with a reversible phenotype → genotype decoder.

**Why rejected.** Ship-build phenotype (archetype, role, engagement range) does not have a cheap reversible map back to genotype (weapon assignment per slot + hullmod subset + OP allocation). Building such a decoder is a research project in itself. QD methods remain viable for archetype *diagnosis* (see Phase 7 Quality-Diversity placeholder) but not for surrogate construction. Hearthstone's MESB uses behavior descriptors *with* cheap forward evaluation (simulated games) and greedy card construction — a structurally different problem.

### 4.10 Conjoint analysis (ACBC) — HUMAN-RESPONDENT PARADIGM

**What it was.** Adaptive Choice-Based Conjoint — part-worth utility models over product attributes, standard in market research.

**Why rejected.** ACBC's evaluation budget is human respondents answering ~12 discrete-choice tasks; the mathematics of part-worth estimation is deeply relevant (mirrors the attribute-Matérn + ICM residual structure) but the culture of ACBC tools does not target black-box simulation. Import the hierarchical-Bayes part-worth model as a *surrogate-prior idea* (which §3.3 and §3.7 already do implicitly), not the methodology wholesale.

### 4.11 Definitive Screening Designs — WRONG TOOL

**What it was.** Jones & Nachtsheim 2011 DSD — classical factorial designs with three-level factors for efficient main-effect + curvature estimation.

**Why rejected.** Jones-Nachtsheim explicitly call high-cardinality categoricals "undesirable" for DSDs. Our 150-level weapon categoricals break the design's efficiency properties. Wrong tool.

### 4.12 Full RL / MuZero / Q-DeckRec — BUDGET MISMATCH

**What it was.** Deep RL from self-play (TFTMuZero, Q-DeckRec [arXiv:1806.09771](https://arxiv.org/abs/1806.09771), Chen & Amato 2018).

**Why rejected.** Requires 10⁵ – 10⁶ evaluations; we have 200–2000. Not even close. Budget mismatch of 2–4 orders of magnitude.

### 4.13 Silent rule-based small-slot fills — EXPLICITLY REJECTED BY USER

**What it was.** Deterministic auto-fit heuristic (e.g., "fill smalls with cheapest PD until OP runs out") executed before the surrogate sees the build.

**Why rejected.** User-explicit correction during design review: small slots must remain addressable because they are opponent-conditional (§2.8). The historical kernel proposal instead adds opponent summary features to small-slot posteriors (§3.9), preserving full tweakability while still letting a model learn opponent-conditional defaults from data.

### 4.14 REMBO / ALEBO / HeSBO random-linear subspaces — WITHIN-GROUP INTERACTIONS

**What it was.** Wang 2013 REMBO ([arXiv:1301.1942](https://arxiv.org/abs/1301.1942)), Letham 2020 ALEBO ([arXiv:2001.11659](https://arxiv.org/abs/2001.11659)), Nayebi 2019 HeSBO ([arXiv:1902.10675](https://arxiv.org/abs/1902.10675)) — optimize on a random linear projection to a low-dim active subspace.

**Why rejected.** Assumes a global low-dim linear active subspace. Our problem has within-group interactions (PD matters only if opponent has missiles; kinetic needs HE follow-up) that a single global projection cannot capture. SAAS's axis-aligned (non-linear combinations) assumption fits our problem; random-linear does not.

### 4.15 Fixed community-meta archetype weights per hull — REJECTED

**What it was.** Hardcode archetype-mode mixture weights per hull from community meta (e.g., "Paragon: 0.6 turret-flex, 0.3 long-range-sniper, 0.1 other"; "Onslaught: 0.5 kinetic-HE brawler, 0.3 SO brawler, 0.2 other"). Initial sketch of the πBO prior from the literature sweep.

**Why rejected.** Three failures (enumerated in §2.5): (i) the archetype
taxonomy was induced from community analysis of a limited set of meta hulls;
many combat hulls and all mod/patch-added hulls have sparse or absent weights;
(ii) absolute-attribute mode means would mismatch across hull sizes; (iii)
physical infeasibility — a Wolf cannot physically realize Broadside,
Turret-flex, or Phase-striker modes, and assigning nonzero weight to impossible
configurations wastes acquisition-function mass. The current design hypothesis
(§3.8) instead computes a per-hull feasibility mask from `.ship` data and
initializes weights uniform over feasible modes. Community meta contributes the
*vocabulary of modes* but not the *per-hull weights*.

### 4.16 Hardcoded AI-compatibility flags on archetype modes — REJECTED

**What it was.** Manually annotate each of the nine archetype modes with an AI-pilotability flag (low / medium / high, per §2.10 table) and multiply the πBO prior weight by a hardcoded pilotability factor at hull-load time. Example: SO-brawler weight halved because "community consensus says the AI mispilots it."

**Why rejected.** Three failures. (i) AI behavior changes across game patches;
hardcoded compatibility flags rot. (ii) AI-compatibility interacts with hull:
the AI may mispilot Gryphon's burst-missile pattern but handle burst-missile on
Hammerhead differently because the Hammerhead's smaller missile count is within
the AI's short-horizon planning. A global per-mode flag cannot encode this.
(iii) Hardcoded flags would prejudice a future empirical reweighting or
residual-learning mechanism before that mechanism is validated.

Community-meta insight about AI pilotability is preserved as *grounding* (§2.10 documents the known-hostile modes so the user can interpret why SO-brawler weights collapse fast on AI-vs-AI runs) but never enters the prior weights directly.

### 4.17 Optimize for player-piloted flagship builds — OUT OF SCOPE

**What it was.** Add a kernel context feature `is_flagship: bool` and expose per-hull player-piloted evaluation runs. Many community-top builds assume the player is the pilot (skill 5 Helmsmanship, Combat Endurance, Target Analysis). If we had player-in-the-loop simulation we could recover player-only optima.

**Why out of scope.** Phase 2 combat harness is AI-vs-AI by construction; no player-in-the-loop input mechanism exists and building one is outside Phase 7's scope (would require engine-level input injection, out of the current architectural envelope). The optimizer's deployment context is the fleet — most ships in a campaign fleet are AI-piloted, so the simulation already matches the dominant deployment case. Player-piloted flagship optimization is a distinct downstream problem that would require a separate simulation harness and is deferred indefinitely.

### 4.18 Standalone BOCA pilot — COMMIT-FOREVER RISK

**What it was.** Run 30 random-forest-importance trials, hard-partition flags into impactful / unimpactful, then BO over only impactful.

**Why rejected in standalone form.** If the pilot miscalls an important
hullmod, it is randomized forever. Sparse priors keep recovery possible because
a wrongly-estimated hullmod can activate as more data accumulates. BOCA is
retained only as a possible warm-start diagnostic, not as a standalone
dimension-reduction mechanism.

---

## 5. Historical Expected Impact Hypotheses

The table below records literature-inspired hypotheses from the historical
kernel design. It is not a project measurement and not a ship criterion. Any
future implementation plan must replace these hypotheses with current
experiment gates and report-owned measurements.

| Component | Regime | Literature claim or design hypothesis | Source |
|---|---|---:|---|
| SAAS sparsity on hullmods | high-dimensional hullmod block | Literature supports sparse-axis-aligned BO under matching assumptions | Eriksson-Jankowiak 2021 |
| Transformed-overlap categorical kernel | high-cardinality weapon IDs | Literature supports categorical kernels; BaCO supports mixed-space BO with different categorical handling | Garrido-Merchán 2020; Hellsten 2024 |
| Attribute-Matérn + item residual | schema-stable weapon attributes | Design hypothesis inspired by attribute transfer and multi-task GP literature | Swersky-Snoek-Adams 2013; ICM literature |
| Slot-feature Matérn | 5-dim slot feature | Hypothesized benefit from cross-slot pooling | No published benchmark |
| πBO archetype priors (hull-conditional) | Decay-weighted feasible-mode prior | Literature supports prior-guided BO; project must validate feasible archetype modes and any reweighting | Hvarfner 2022; BOPrO |
| BOCA warm-start | initial exploration | Possible warm-start diagnostic, not hard dimensionality reduction | Chen 2021 |
| Opponent features on smalls | 3-dim feature vec | Qualitative hypothesis: preserve small-slot addressability | §2.8 community-meta evidence |

Project-specific throughput and budget projections belong in dated reports.
This reference only records the design expectation that improved sample
efficiency should reduce required simulation budget if the gates below pass.

Game-update transfer remains a design hypothesis: new weapons with similar
attribute profiles should inherit an attribute-kernel prior. Future reports
must validate whether that hypothesis holds for actual Starsector data.

### 5.1 Historical Candidate Validation Ideas

The following ideas are retained as historical candidate checks. They are not
approved pass/fail gates until a future plan or spec adopts them.

1. **Rank-correlation gate**: on a held-out synthetic fitness function (Phase 5D-style 7-covariate ground truth + 200-trial eval budget), Phase 7 surrogate top-10 rank ρ vs ground truth should meet the design threshold defined by the validation plan for that experiment.
2. **Cold-start gate**: on a new hull (not in pilot data), Phase 7 surrogate should reach the top-10 of a 2000-trial Phase-4 run within 500 Phase-6 trials.
3. **Game-update gate**: on simulated addition of 5 new weapons with attribute profiles interpolated from existing weapons, posterior mean at the new-weapon points should be within 0.2 σ of the attribute-interpolation ground truth with zero observations.
4. **Addressability gate**: on a missile-heavy opponent pool, the Phase 7 surrogate's small-slot posterior mean should prefer IPDAI + Dual Flak over the opponent-pool-agnostic posterior at p < 0.05.
5. **Hull-generalization gate**: on a non-meta hull, the Phase 7 surrogate should demonstrate comparable sample-efficiency to meta hulls under a report-owned benchmark, verifying whether the feasibility mask and uniform feasible-mode prior reduce meta-hull coverage bias.

---

## 6. Historical Implementation Outline

The executable Phase 7 sequence is now owned by the dated roadmap and future
active plans. Historical notes from this section should be treated as design
inputs only:

- custom BoTorch kernels may become relevant after learned-baseline and
  model-assisted-search gates pass;
- candidate module/class names from the old outline are placeholders, not
  contracts;
- πBO, BOCA, opponent-feature plumbing, and custom sampler work each need a
  fresh plan, spec impact review, and post-implementation audit before coding.

---

## 7. References

### Mixed-categorical BO + compiler autotuning
- Garrido-Merchán & Hernández-Lobato 2020 "Dealing with Categorical and Integer-valued Variables in Bayesian Optimization with Gaussian Processes," *Neurocomputing*.
- Hellsten et al. 2024 "BaCO: A Fast and Portable Bayesian Compiler Optimization Framework," ASPLOS, [arXiv:2212.11142](https://arxiv.org/abs/2212.11142).
- Hellsten et al. 2024 "CATBench: A Compiler Autotuning Benchmarking Suite," [arXiv:2406.17811](https://arxiv.org/abs/2406.17811).
- Chen et al. 2021 "BOCA: Tuning Compilers with Bayesian Optimization," ICSE.
- Ru et al. 2020 "Bayesian Optimisation over Multiple Continuous and Categorical Inputs," ICML (CoCaBO), [arXiv:1906.08878](https://arxiv.org/abs/1906.08878).
- Cowen-Rivers et al. 2022 "HEBO: An Empirical Study of Assumptions in Bayesian Optimisation," *JAIR*, [arXiv:2012.03826](https://arxiv.org/abs/2012.03826).
- Deshwal et al. 2021 "Bayesian Optimization over Hybrid Spaces," ICML, [arXiv:2106.04682](https://arxiv.org/abs/2106.04682).

### High-dim / sparse BO
- Eriksson & Jankowiak 2021 "High-Dimensional Bayesian Optimization with Sparse Axis-Aligned Subspaces," UAI, [arXiv:2103.00349](https://arxiv.org/abs/2103.00349).
- Kandasamy et al. 2015 "High-Dimensional Bayesian Optimisation and Bandits via Additive Models," ICML, [arXiv:1503.01673](https://arxiv.org/abs/1503.01673).
- Gardner et al. 2017 "Discovering and Exploiting Additive Structure for Bayesian Optimization," AISTATS, [arXiv:1702.08608](https://arxiv.org/abs/1702.08608).
- Rolland et al. 2018 "High-Dimensional Bayesian Optimization via Additive Models with Overlapping Groups," AISTATS, [arXiv:1802.07028](https://arxiv.org/abs/1802.07028).
- Ziomek & Ammar 2023 "Are Random Decompositions All We Need in High Dimensional Bayesian Optimisation?", ICML, [arXiv:2301.12844](https://arxiv.org/abs/2301.12844).
- Eriksson et al. 2019 "Scalable Global Optimization via Local Bayesian Optimization" (TuRBO), NeurIPS, [arXiv:1910.01739](https://arxiv.org/abs/1910.01739).

### Hierarchical / conditional BO
- Jenatton et al. 2017 "Bayesian Optimization with Tree-Structured Dependencies," ICML.
- Ma & Blaschko 2020 "Additive Tree-Structured Conditional Parameter Spaces for Bayesian Optimization," TPAMI, [arXiv:2010.03171](https://arxiv.org/abs/2010.03171).
- Schrodi et al. 2022 "Construction of Hierarchical Neural Architecture Search Spaces based on Context-free Grammars," [arXiv:2211.01842](https://arxiv.org/abs/2211.01842).
- Falkner, Klein, Hutter 2018 "BOHB: Robust and Efficient Hyperparameter Optimization at Scale," ICML, [arXiv:1807.01774](https://arxiv.org/abs/1807.01774).
- Lindauer et al. 2022 "SMAC3: A Versatile Bayesian Optimization Package," *JMLR*, [arXiv:2109.09831](https://arxiv.org/abs/2109.09831).

### Informed priors
- Hvarfner et al. 2022 "πBO: Augmenting Acquisition Functions with User Beliefs," ICLR, [arXiv:2204.11051](https://arxiv.org/abs/2204.11051).
- Souza et al. 2021 "Bayesian Optimization with a Prior for the Optimum" (BOPrO), UAI, [arXiv:2006.14608](https://arxiv.org/abs/2006.14608).
- Hvarfner et al. 2023 "Self-Correcting Bayesian Optimization through Bayesian Active Learning," [arXiv:2304.11005](https://arxiv.org/abs/2304.11005).
- Mallik et al. 2023 "PriorBand: Practical Hyperparameter Optimization in the Age of Deep Learning," [arXiv:2306.12370](https://arxiv.org/abs/2306.12370).

### Multi-task / attribute priors (ICM)
- Swersky, Snoek, Adams 2013 "Multi-task Bayesian Optimization," NeurIPS, [arXiv:1309.6835](https://arxiv.org/abs/1309.6835).
- Bonilla, Chai, Williams 2007 "Multi-task Gaussian Process Prediction," NeurIPS.
- Álvarez & Lawrence 2011 "Computationally Efficient Convolved Multiple Output Gaussian Processes," *JMLR*, [arXiv:1106.6251](https://arxiv.org/abs/1106.6251).
- Perrone et al. 2018 "Scalable Hyperparameter Transfer Learning" (ABLR), NeurIPS, [arXiv:1712.02902](https://arxiv.org/abs/1712.02902).
- Feurer et al. 2018 "Scalable Meta-Learning for Bayesian Optimization using Ranking-Weighted Gaussian Process Ensembles" (RGPE), [arXiv:1802.02219](https://arxiv.org/abs/1802.02219).

### Game AI / deckbuilding
- Fontaine et al. 2019 "Mapping Hearthstone Deck Spaces through MAP-Elites with Sliding Boundaries," GECCO, [arXiv:1904.10656](https://arxiv.org/abs/1904.10656).
- Zhang et al. 2022 "Deep Surrogate Assisted MAP-Elites for Automated Hearthstone Deckbuilding," GECCO, [arXiv:2112.03534](https://arxiv.org/abs/2112.03534).
- Ward & Cowling 2009 "Monte Carlo Search Applied to Card Selection in Magic: The Gathering," IEEE CIG.
- Ward et al. 2020 "AI Solutions for Drafting in Magic: The Gathering," [arXiv:2009.00655](https://arxiv.org/abs/2009.00655).
- Chen & Amato 2018 "Q-DeckRec: A Fast Deck Recommendation System for Collectible Card Games," [arXiv:1806.09771](https://arxiv.org/abs/1806.09771).

### Naval architecture
- Evans 1959 "Basic Design Concepts," *ASNE Journal*.
- Rawson & Tupper 2001 *Basic Ship Theory* (5th ed., Butterworth-Heinemann).
- Andrews 1986 "An Integrated Approach to Ship Synthesis," *RINA Transactions* 128: 73–102.
- Singer, Doerry, Buckley 2009 "What Is Set-Based Design?," *Naval Engineers Journal* 121(4): 31–43.
- Brown & Salcedo 2003 "Multiple-Objective Optimization in Naval Ship Design," *NEJ* 115(4): 49–61.
- Watson 1998 *Practical Ship Design*, Elsevier.

### Protein engineering / MLDE (budget-matched analogs)
- Ding et al. 2024 "MODIFY: A Co-design Framework for Library Design in Machine Learning-Directed Evolution," *Nature Communications* 15.
- Wittmann, Yue, Arnold 2021 "Informed Training Set Design Enables Efficient Machine Learning-Assisted Directed Protein Evolution," *Cell Systems*.
- Bal, Sessa, Mutny, Krause 2024 "Optimistic Games for Combinatorial Bayesian Optimization with Application to Protein Design," [arXiv:2409.18582](https://arxiv.org/abs/2409.18582).

### Starsector community meta (archetype taxonomy + hullmod canon)
- [Ship Loadout Guide, fractalsoftworks forum topic 24219](https://fractalsoftworks.com/forum/index.php?topic=24219.0)
- [Fossic Vanilla Weapon Guide 0.97, forum topic 25973](https://fractalsoftworks.com/forum/index.php?topic=25973.0)
- [Best Hull Mods, forum topic 10007](https://fractalsoftworks.com/forum/index.php?topic=10007.0)
- [Looking for Meta Fleets, forum topic 31462](https://fractalsoftworks.com/forum/index.php?topic=31462.0)
- [Starsector Wiki Combat Guide](https://starsector.wiki.gg/wiki/Combat_Guide)
- [Starsector Wiki Refit Screen](https://starsector.wiki.gg/wiki/Refit_screen)
- IroncladLion SOG guide, patreon.com/posts/starsector-guide-89469893

### Software
- BoTorch — [botorch.org](https://botorch.org/) — `SaasFullyBayesianSingleTaskGP`, `Kernel`, `BaseAcquisitionFunction`.
- HyperMapper — [github.com/luinardi/hypermapper](https://github.com/luinardi/hypermapper) — reference implementation for BaCO kernels.
- BaCO — [github.com/baco-authors/baco](https://github.com/baco-authors/baco) — mixed-space BO comparison reference.
- Optuna — [github.com/optuna/optuna](https://github.com/optuna/optuna) — `BaseSampler` plugin API.

### See also
- `docs/reference/phase5d-covariate-adjustment.md` — EB fusion paradigm that §3.7 ICM residuals mirror.
- `docs/reference/phase5f-regime-segmented-optimization.md` — `(hull, regime)` study unit that Phase 7 operates on.
- `docs/reference/phase5-signal-quality.md` — TWFE + WilcoxonPruner + opponent curriculum upstream of Phase 7.
- [../reports/2026-05-10-v1-loadout-bug-invalidation.md](../reports/2026-05-10-v1-loadout-bug-invalidation.md) — V1 invalidation that retired the original Hammerhead-twfe directory whose throughput + concentration observation motivated the dimensionality argument; the structural argument is unaffected.
- `src/starsector_optimizer/parser.py` — `_ParseableEnum` forward-compatibility mechanism that underwrites the game-update-invariance design property.
