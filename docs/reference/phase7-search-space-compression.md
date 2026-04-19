# Phase 6 — Structured Search-Space Representation

> **Status**: PLANNED. Research complete (2026-04-17). Targets the combinatorial-explosion vs expensive-evaluation bottleneck by replacing the Optuna TPE surrogate (CatCMAwM removed 2026-04-19; see spec 24) with a custom BoTorch-based Gaussian Process whose kernel composes sparse-axis-aligned priors on hullmod booleans, transformed-overlap categoricals and attribute-Matérn on weapons, Matérn on slot coordinates, opponent-context features on small-slot posteriors, gated-sentinel for conditional slots, and ICM-style per-item and per-slot residuals. Warmed by a BOCA-style 30-trial random-forest importance pilot and biased (but not locked) by πBO decay-weighted priors over nine community-stable role archetypes. No shipped code yet.

Design and research log for how the optimizer **represents and searches** the ship-build space. Phase 5 improves the *scoring* of builds (signal quality); Phase 6 improves the *surrogate model* that decides which builds to test next, by injecting stable structural priors (slot geometry, weapon attributes, archetype density, hullmod sparsity) that survive game updates.

Reading this doc cold: Phase 4 shipped the initial Optuna TPE optimizer over one-hot encoded weapons and hullmod booleans (CatCMAwM was in `_create_sampler` as a nominally-selectable alternative until 2026-04-19, when it was removed for being incompatible with our all-categorical search space). Phase 6 replaces that surrogate with a mixed-space GP whose kernel structure matches the known geometry of the ship-build problem — weapons have physics-driven attributes, slots have 2D coordinates, hullmods have sparse activity, and archetypes are stable across game patches. See `docs/reference/implementation-roadmap.md` for the full phase status.

This design is the synthesis of a 10-agent, 2026-04-17 literature sweep plus a follow-up compiler-autotuning deep-dive:

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

Plus the compiler-autotuning deep-dive that surfaced the transformed-overlap kernel and gated-sentinel conditional handling as production-grade primitives.

---

## 1. Problem

### 1.1 Combinatorial explosion meets expensive eval

A per-hull build is a tuple of `(hullmod subset ⊆ H, weapon-per-slot ∈ W × …, OP allocation ∈ ℝ^3)` where `|H| ≈ 30–80` available hullmods, `|W| ≈ 150` weapons per slot type, and there are ~8 slots per hull. The raw combinatorial set is ~2^80 × 150^8 ≈ 10^40. Each evaluation is a ~2-minute combat simulation. Overnight budget is 215 trials, 24-hour is 650, 3-day is ~1950 (measured from the 2026-04-13 Hammerhead run, `experiments/hammerhead-twfe-2026-04-13/optimizer.log`).

The search problem is small-budget-expensive-combinatorial: typical BO regime. But two factors make the vanilla approach insufficient:

- **Effective dimensionality is ~30–50, not 150^8.** Most weapons at most slots are equivalent on the coarse axes (damage type, range, flux-efficiency). One-hot encoding over weapon IDs wastes information by treating equivalent items as independent.
- **Most hullmods are irrelevant per build.** The community meta identifies ~5–10 load-bearing hullmods per archetype; the remaining 70–75 add near-zero variance to fitness. Dense BO over a 70-dim boolean space is sample-inefficient.

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

Hellsten et al. 2024 "BaCO: A Fast and Portable Bayesian Compiler Optimization Framework" (ASPLOS, [arXiv:2212.11142](https://arxiv.org/abs/2212.11142), Eq. 3–5) compose this as a **product kernel** over real / ordinal / categorical / permutation / conditional parameter groups. It is the production state-of-the-art for exactly our problem shape: discrete + conditional + mixed-type + expensive-eval + budget-limited + must-transfer-across-versions. Reported 1.36×–1.56× speedup vs prior SOTA on TACO / RISE-ELEVATE / HPVM2FPGA compiler benchmarks.

Conditional parameters in BaCO are handled by **gated-sentinel imputation** (§4.3 of the paper): inactive parameters take a special "NA" category; the categorical kernel learns NA↔NA similarity as a free hyperparameter, so the GP learns *how much* inactivity matters per parameter rather than forcing a zero-gradient. This is materially lighter than Jenatton 2017 / Ma-Blaschko 2020 tree-structured GPs and empirically matches or beats them when the conditional structure is simple.

### 2.2 Sparse axis-aligned subspaces (SAASBO)

Eriksson & Jankowiak 2021 "High-Dimensional Bayesian Optimization with Sparse Axis-Aligned Subspaces" (UAI, [arXiv:2103.00349](https://arxiv.org/abs/2103.00349)) place a half-Cauchy prior on inverse lengthscales: `τ ~ HC(0.1)`, inducing soft sparsity. Most dimensions get lengthscale → ∞ (effectively inactive), a few get learned small lengthscales (active). Crucially **dimensions remain addressable** — a dim that seemed irrelevant can activate as data accumulates. Validated to D=388 with 100–500 trials. BoTorch ships `SaasFullyBayesianSingleTaskGP`. Reported 2–5× vs vanilla GP-BO at D=60–388 in the 100–500-trial regime — *exactly our setting*.

This subsumes the older Add-GP-UCB (Kandasamy 2015, [arXiv:1503.01673](https://arxiv.org/abs/1503.01673)) and Gardner 2017 (learned additive decomposition) for our purposes. Rolland et al. 2018 ([arXiv:1802.07028](https://arxiv.org/abs/1802.07028)) and Ziomek & Ammar 2023 ([arXiv:2301.12844](https://arxiv.org/abs/2301.12844)) show that *random* additive decompositions often match *learned* ones at fixed budget — arguing against the complexity of Ma-Blaschko tree kernels when SAAS sparsity is available.

SAAS fits our hullmod-boolean subspace exactly. The community meta identifies ~5–10 load-bearing hullmods per archetype; the half-Cauchy prior encodes "most dimensions should shrink to irrelevance" as a structural prior, not as a heuristic pre-filter.

### 2.3 Attribute kernels + per-item ICM residuals

Swersky, Snoek & Adams 2013 "Multi-task Bayesian Optimization" (NeurIPS, [arXiv:1309.6835](https://arxiv.org/abs/1309.6835)) and the broader Intrinsic Coregionalization Model (ICM) literature (Bonilla et al. 2007, Álvarez & Lawrence 2011 [arXiv:1106.6251](https://arxiv.org/abs/1106.6251)) provide the principled template for combining **attribute priors** with **item-specific residuals**:

`f(w) = m(φ(w)) + g(w)`

where `m` is a GP/linear model over the attribute vector `φ(w)` (transfers across items: a new weapon with similar stats inherits the prior mean for free), and `g(w)` is a per-item GP with shrinkage prior (captures quirks — e.g., Antimatter Blaster's specific synergy with short-range brawl — that the attribute vector does not encode).

This is structurally identical to the Phase 5D Empirical-Bayes fusion paradigm: `α̂_TWFE` and the 7-covariate heuristic prior are combined as noisy measurements of the same latent `α`. Re-using the fusion paradigm at the surrogate level keeps the architecture coherent across phases.

The attribute vector for weapons is the **7-dim tuple** sized by the Phase 5D sweep + variance audit:

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

Hvarfner et al. 2022 "πBO: Prior-guided Bayesian Optimization" (ICLR, [arXiv:2204.11051](https://arxiv.org/abs/2204.11051)) multiplies the acquisition function by a user-supplied prior density `π(x)` with a **decay schedule** `π(x)^(β/t)` — prior weight shrinks with trial count. No-regret preserved under mild conditions; the decay lets the data override the prior as evidence accumulates. Reported 6×–12× speedup when the prior is accurate and ~1.5× worst-case overhead when the prior is adversarial. Souza et al. 2021 BOPrO ([arXiv:2006.14608](https://arxiv.org/abs/2006.14608)) gives the same mechanism in pseudo-posterior form with explicit misleading-prior recovery results.

The community-meta agent (sweep round 2, 2026-04-17) extracted **nine role archetypes** stable across 0.95 → 0.96 → 0.97 → 0.98 (four major patches):

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

Each archetype is a point in **normalized** attribute × slot-feature × hullmod-subset space (not a specific weapon list — weapons change between patches; attribute profiles do not; normalized range + normalized OP cost transfer across hull sizes per §2.3). πBO encodes the modes as a mixture density `π(x) = Σ_k w_k · N(x ; μ_k, Σ_k)`. Small `β` (≤ 10) bounds worst-case regret to ≤ 1.5× vanilla BO when all modes are wrong.

**Three failures a naïve hull-agnostic weighting would hit.** The 9-archetype vocabulary above was induced from community analysis that concentrates on ~7 meta hulls (Onslaught, Paragon, Conquest, Aurora, Hammerhead, Eagle, Odyssey). Applied uniformly to any hull it fails on:

1. **Physical infeasibility.** Some archetypes require specific slot geometry or ship systems. Broadside needs 4+ large ballistic slots grouped broadside (Conquest-only). Turret-flex shield-tank needs Advanced Targeting Core as ship system (Paragon-only). Burst-missile needs sufficient missile mount count. Phase striker needs a phase cloak. A Wolf (frigate, 3 small slots, no capital-ship system) cannot realize six of the nine modes.
2. **Absolute-attribute mismatch.** "Long-range sniper" resolves to > 1500 range on a capital (Gauss, Mark IX) and > 700 on a frigate (Pulse Laser, LR PD Laser). If the mode's mean is a fixed absolute range, the frigate's best weapons never match it.
3. **Meta-hull coverage bias.** Community meta covers ~7 hulls in depth; the remaining ~40 combat hulls (Gemini, Vanguard, Falcon, Shrike, Omen, Medusa, etc.) have sparse published taxonomy, and mod or patch-added hulls have none. Hardcoding weights from meta-hull analysis projects that bias onto hulls the community has not analyzed.

**Three additions that make the archetype prior generalize.**

a. **Hull-feasibility mask.** For each (hull, archetype) pair, pre-compute physical realizability from `.ship` JSON + ship-system data (slot-size/type counts, ship-system availability, phase-cloak presence). Infeasible modes get zero weight in the πBO mixture. Automatic, data-driven, survives mod and patch additions.

b. **Attribute normalization + `hull_size` context.** Resolved at §2.3. Archetype modes defined in the normalized space work across all hull sizes without per-hull rescaling; `hull_size` as a categorical context feature lets the kernel still express hull-size-specific effects where data demands.

c. **Uniform initial mixture weights over feasible modes + online self-correction.** Instead of hardcoding meta-derived weights, start uniform across the modes left feasible by (a) for the given hull. Hvarfner 2023 "Self-Correcting Bayesian Optimization through Bayesian Active Learning" ([arXiv:2304.00397](https://arxiv.org/abs/2304.00397)) gives the mechanism: online estimate prior credibility via a marginal likelihood ratio; when the data-observed posterior disagrees with a prior mode, downweight that mode's mixture weight. Covers both the "community missed this hull" case and the "this hull's actual best archetype is unusual" case.

The community meta's role is therefore to supply the **vocabulary of modes** (what "sniper" vs "brawler" means in normalized attribute space), not the **per-hull weights** (which the optimizer learns from data). This preserves the community insight while removing the meta-hull bias.

A fourth failure — related but distinct — is that community meta builds are often designed for **player piloting** (SO brawler, phase striker, burst-missile) and the AI mispilots them in simulation. §2.10 covers this as a separate grounding point. The self-correcting mixture mechanism above handles it by the same route: AI-hostile modes produce weaker-than-predicted fitness and get online-downweighted; no hardcoded AI-compatibility flag is needed.

### 2.6 BOCA importance pilot as SAAS warm-start

Chen et al. 2021 "BOCA" (ICSE) runs a 30-trial random-forest-importance pilot before the main BO, partitioning compiler flags into impactful vs unimpactful and then running BO over only the impactful set. In isolation BOCA commits hard to the pilot's verdict — a miscalled flag gets randomized forever, no recovery.

At our scale SAAS subsumes the principled form of this: its half-Cauchy prior encodes the same "most dims don't matter" belief and learns online which dims activate, with full reversibility. BOCA's role shrinks to **warm-starting SAAS**: run 30 trials of Latin-hypercube-seeded evaluations, compute random-forest importance, use the importance ordering to set the initial prior scale on each lengthscale (empirical-Bayes initialization). This gives SAASBO a head start on the cold-start problem without the commit-forever risk of vanilla BOCA. Cheap insurance for the new-hull case; drops out once SAAS has seen ~100 trials of evidence.

### 2.7 Naval architecture: Evans spiral and platform/mission split

Evans 1959 "Basic Design Concepts" (*ASNE Journal*) introduced the Design Spiral: iterate (mission → hull form → propulsion → structure → weights → stability → cost) outer-to-inner, committing first to hull/propulsion/principal-dimensions and fitting armament into the remaining budget. Still taught in Lamb (ed.) *Ship Design and Construction* (SNAME 2003).

Brown & Salcedo 2003 "Multiple-Objective Optimization in Naval Ship Design" (*NEJ*) and Andrews 2003 "A Creative Approach to Ship Architecture" (*IJME*) formalize the **platform vs mission-system split**: platform (hull, propulsion, damage control) is fixed in early design; mission system (weapons, sensors, C4I) is modular to allow mid-life upgrades. Literal slots in USS flights: MK 41 VLS, Stanflex, USN Modular Mission Packages.

Mapping:

| Naval concept | Ship-build analog | Locked in Phase 6 design |
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

This is **the load-bearing empirical constraint**: the optimizer must retain the ability to tweak small slots in response to opponent features. The shipped design encodes this as **opponent summary features as additional kernel inputs on small-slot posteriors only** (see §3.9).

Hullmod canon — items that survived 4 major patches as S-tier: ITU, Hardened Shields, Expanded Magazines, Auxiliary Thrusters, Resistant Flux Conduits, IPDAI. Stable priors for πBO's hullmod-subspace density.

### 2.9 Starsector data invariants: stable across 0.95 → 0.98

Audit of `parser.py`, `models.py`, and the CSV + `.ship` JSON schemas confirms that every enum listed in §1.3 has never been expanded. Schema-stable columns used by the shipped design:

- `weapon_data.csv`: `id`, `name`, `type`, `damage/second`, `damage/shot`, `OPs`, `range`, `chargeup`, `chargedown`, `burst size`, `burst delay`, `energy/shot`, `energy/second`
- `ship_data.csv`: `id`, `name`, `designation`, `ordnance points`, `max flux`, `flux dissipation`, `hitpoints`, `armor rating`, `shield type`, `shield efficiency`
- `hull_mods.csv`: `id`, `name`, `tier`, `cost_frigate`, `cost_dest`, `cost_cruiser`, `cost_capital`, `hidden`
- `.ship` JSON per hull: `weaponSlots[].id`, `.type`, `.size`, `.mount`, `.angle`, `.arc`, `.locations`

Forward compatibility: `_ParseableEnum.from_str()` (`parser.py`) returns `None` for unknown values and the parser logs a warning and skips the record. The kernel design never hardcodes specific weapon IDs, specific hullmod IDs, or specific tags (Design Principle 5, CLAUDE.md). Only the enums (stable forever) and CSV columns (stable forever) are referenced in the kernel specification.

Caveat: hullmod incompatibility pairs (`INCOMPATIBLE_PAIRS`) and hull-size restrictions (`HULL_SIZE_RESTRICTIONS`) are hardcoded in Python (`hullmod_effects.py`), not in CSV. These require manual patching per game version. Phase 6 does not change this — it is orthogonal.

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

**Implication for Phase 6.** The uniform-initial-weights + self-correcting π design from §2.5 already addresses this: community meta contributes the vocabulary of modes, not the weights, and the per-hull self-correcting mechanism online-downweights modes that underperform under the actual simulation context. An SO-brawler mode that the AI mispilots will empirically produce worse fitness than the community-meta prior predicts, and the Hvarfner 2023 marginal-likelihood-ratio update will reduce its mixture weight on the next acquisition. No hardcoding of AI-hostility flags is needed; the data does the work.

**If the combat harness varies AI personality** (Cautious / Steady / Aggressive / Reckless from `PersonalityAPI`) or officer skills across trials, the varying fields enter the kernel as additional ordinal/boolean context features on the player side (same pattern as the opponent-summary features on small slots, §2.8). If the harness fixes personality to Steady and runs un-officered (the Phase 2 default), these are simulation constants and do not need to enter the kernel. The current Phase 6 ship assumes the latter — default-personality, un-officered — which matches the typical fleet deployment case (most ships in a campaign fleet have no officer). Officer-conditional or personality-conditional optimization is a Phase 6.1 extension, not part of the initial ship.

**Weapon-group assignment.** Stock `.variant` files hand-tune `weaponGroups` with per-group `autofire` and `mode` (LINKED / ALTERNATING) — notably `autofire=false` on ammo-limited missiles and ALTERNATING pairings on expensive ballistics. The combat harness builds variants via `autoGenerateWeaponGroups()` (`VariantBuilder.java:48`); `BuildSpec` carries no group metadata from Python. Every optimizer-produced build therefore runs on the engine's default grouping algorithm, which may not reproduce the hand-tuned behaviour of community reference builds. Absorbed by the same §2.10 mechanism: builds whose fitness depends on specific grouping (burst-missile timing, mixed-flux ballistic swaps) will underperform their community prior and get downweighted by the self-correcting mixture. Documented as a fidelity floor; lifting it would require adding weapon-group decisions to the search space (deferred, Phase 7.1+).

**Fighter-wing assignment is out of scope.** `LAUNCH_BAY` slots are filtered out of `SearchSpace.weapon_options` (`search_space.py:157`); `BuildSpec` has no wing field; `VariantBuilder.java` never populates wings; `ManifestDumper.java` does not enumerate `FighterWingSpecAPI` or `wing_data.csv`. Carrier hulls (24 in vanilla: Astral 6 bays, Legion 4, Heron/Mora 3, Condor/Drover/Apex 2, …) are optimizable for non-bay slots only; their bays deploy empty. Per §3.8 below, the PD-carrier archetype's LAUNCH_BAY branch is currently infeasible — only the small-slot-PD branch (Hammerhead-style destroyer escorts) is realized. Mentioned here for completeness; the self-correcting mixture would collapse a carrier-only mode's weight to zero under this configuration, which is the correct behaviour given the scope.

### 2.11 What the sweep ruled OUT

The 10-agent sweep and compiler-autotuning deep-dive disqualified several directions that sounded plausible a priori. See §4 for the full rejected-alternative chain. Short version: NAS weight-sharing doesn't transfer to non-neural settings; REMBO/ALEBO linear-random-subspaces fail on within-group interactions; BOCS requires binary-only indicators (our categoricals break it); GFlowNets need training-signal volumes we don't have; Hearthstone MESB behavior-descriptor search needs a phenotype→genotype map we cannot build cheaply; conjoint-analysis ACBC is a human-respondent paradigm, not a black-box-sim paradigm; Definitive Screening Designs are "explicitly undesirable for high-cardinality categoricals" (Jones-Nachtsheim 2011); full RL / Q-DeckRec / MuZero require 10⁵+ evaluations.

Two ideas survived as *considered-and-rejected* rather than *considered-and-subsumed*: Ma-Blaschko additive tree kernel (subsumed by gated-sentinel + SAAS), and HyperMapper/BaCO off-the-shelf (missing SAAS on the hullmod subspace). See §4.

---

## 3. Shipped design

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
           · 1_{conditional}(x, x')      # gated-sentinel for inactive slots
         + k_item_residual(w)            # ICM per-weapon residual (shrinks to 0)
         + k_slot_residual(s)            # ICM per-slot residual (shrinks to 0)
```

Product kernel structure follows BaCO (Hellsten 2024, Eq. 3–5). Additive per-item and per-slot residuals follow the ICM template (Bonilla 2007, Álvarez 2011). Gated-sentinel handling follows BaCO §4.3. SAAS prior follows Eriksson-Jankowiak 2021.

### 3.2 Implementation plan: BoTorch as Optuna sampler plugin

BoTorch (Balandat et al. 2020) provides `SaasFullyBayesianSingleTaskGP` out of the box and supports custom kernels via the `Kernel` base class. Optuna supports custom samplers via `BaseSampler`. Integration:

- Custom BoTorch kernel implementing the §3.1 product structure.
- Custom Optuna sampler (`BoTorchSampler` subclass) that wraps the kernel and exposes the ask-tell interface `optimizer.py` already consumes.
- Posterior inference via NUTS / fully-Bayesian MCMC (SAASBO default). Expected overhead: 10–100× MAP-GP per acquisition, but 1–2 hours total wallclock over a 200-trial run — acceptable.
- HyperMapper ([github.com/luinardi/hypermapper](https://github.com/luinardi/hypermapper)) is the reference implementation for BaCO's kernel choices; use it as a validation oracle, not a drop-in library (HyperMapper lacks SAAS on subspaces).

Estimated integration: ~2–3 weeks of custom-kernel + sampler work; ~1 week plumbing for attribute + slot + opponent feature extraction; ~1 week for πBO acquisition layer; ~1 week for BOCA pilot phase. ~6 weeks total for a compelling Phase 6 implementation.

### 3.3 SAAS prior on hullmod subspace

`k_hullmods` is a Matérn-5/2 with per-dimension inverse lengthscales `τ_d ~ HalfCauchy(0.1)` (Eriksson-Jankowiak 2021 default). The soft-sparsity prior encodes the empirical observation (community meta + variance audit) that ~5–10 hullmods per build are load-bearing and the remaining 70+ add near-zero fitness variance. All dimensions remain addressable; none are hard-zeroed.

### 3.4 Transformed-overlap + attribute-Matérn product on weapons

Per slot, two weapon-side kernels multiplied together:

- `k_weapon_id`: transformed-overlap kernel over weapon IDs (Garrido-Merchán & Hernández-Lobato 2020). Captures item-specific synergy quirks that attributes do not encode.
- `k_weapon_attr`: Matérn-5/2 over the 7-attribute vector `(damage_type, weapon_type, size, op_cost, sustained_dps, flux_per_second, range)`. Captures smooth response to physical attributes and enables game-update transfer.

A new weapon added in a future patch inherits the full attribute-kernel prior on day one; only the transformed-overlap residual needs observations to calibrate.

### 3.5 Slot-feature Matérn for cross-slot kernel similarity

`k_slot` is a Matérn-5/2 over the 5-dim slot-feature vector `(forward_projection, arc_width, is_turret, lateral_offset, longitudinal_offset)`. Within a hull, this pools observations across slots of similar geometry — a kinetic HVD validated in slot 1 (forward hardpoint, high forward-projection, low arc-width, is_turret = 0) informs posteriors for slots 2 and 5 if their slot features are nearby.

Bonus: for a future multi-hull surrogate, the slot-feature vector is hull-agnostic — a forward hardpoint on a Hammerhead looks similar in feature space to a forward hardpoint on an Onslaught. Built-in multi-hull transfer at zero marginal cost.

### 3.6 Gated-sentinel for conditional slots

Inactive slots (e.g., hull `X` has only 7 weapon slots, so slot 8 is inactive) get a special "NA" category in both the weapon-ID transformed-overlap and the attribute Matérn. BaCO §4.3 approach: the kernel learns a free NA↔NA similarity hyperparameter, so it decides empirically how much inactivity matters per slot — neither a hard zero nor a pure sentinel. Lighter than Jenatton / Ma-Blaschko tree-structured GPs.

### 3.7 Per-item and per-slot ICM residuals

Two additive residual kernels, each with a strong shrinkage prior toward zero:

- `k_item_residual(w)`: GP over weapon-ID with a `HalfNormal(σ_item)` prior on amplitude. Captures item-specific effects not predicted by the 7-attribute vector (e.g., Antimatter Blaster's specific ship-vulnerability synergy).
- `k_slot_residual(s)`: GP over slot-ID with `HalfNormal(σ_slot)` prior. Captures slot-specific quirks not predicted by the 5-dim slot vector (e.g., a bow-occluded slot that can't actually fire forward).

Both shrink toward zero in the empirical-Bayes sense — the data must supply positive evidence of a quirk before the residual moves. This is structurally the same fusion paradigm used in Phase 5D (α̂ fusion with 7-covariate heuristic prior).

### 3.8 πBO archetype priors — hull-conditional mixture

The acquisition function `α(x)` is multiplied by a Gaussian-mixture prior density `π(x)` over modes defined in the **normalized** attribute × slot × hullmod space (§2.3 normalization), with decay schedule `π(x)^(β/t)`. The nine modes are the community-meta archetypes (§2.5). `β = 5` chosen from the Hvarfner 2022 Fig. 3 tuning curves — 2–3× speedup when priors are correct, ≤ 1.3× overhead when wrong.

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

**Self-correcting π — shipped, not optional.** Hvarfner 2023 ([arXiv:2304.00397](https://arxiv.org/abs/2304.00397)): after each trial, update each feasible mode's weight by the marginal-likelihood ratio `p(y_observed | prior mode_k active) / p(y_observed | flat prior)`. Modes that consistently disagree with data get downweighted; modes that match get amplified. This covers both "community missed this hull" (Gemini, Mudskipper, mod hulls) and "this hull's actual best archetype is unusual" (e.g., a Vanguard running non-SO). Combined with the β/t decay, the prior becomes information only until the data provides it more cheaply.

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

Drops out once the main BO has accumulated ~100 trials of its own evidence. Cheap insurance against the cold-start problem on hulls the optimizer has never seen. 30 trials ≈ 1 hour at 27 trials/hr throughput — small fraction of a 200-trial overnight budget.

### 3.11 Relationship to other phases

Phase 6 is **orthogonal to Phase 5**. It changes the optimizer's surrogate, not its fitness estimator, shape transform, opponent curriculum, or feasibility mask:

| Phase | What it does | Phase 6 impact |
|---|---|---|
| 4 | Optuna TPE over one-hot (CatCMAwM removed 2026-04-19) | Replaced by custom BoTorch sampler |
| 5A (TWFE) | α_i + β_j decomposition | Unchanged; TWFE output feeds GP like any fitness |
| 5B (WilcoxonPruner + ASHA) | Multi-fidelity pruning | Unchanged; pruning is upstream of the GP |
| 5C (anchor-first + incumbent overlap) | Opponent schedule | Provides the opponent features (§3.9) to the GP |
| 5D (EB shrinkage) | α̂ fusion with 7-covariate prior | Unchanged; 5D and 6 share the ICM fusion paradigm but at different layers |
| 5E (Box-Cox shape) | Fitness-shape transform at A3 | Unchanged; Box-Cox output is the GP's `y` |
| 5F (regime segmentation) | Feasible input set | Phase 6 runs one GP per `(hull, regime)` pair; one BOCA pilot per pair |
| 5G (adversarial curriculum) | Deferred | No dependency |

### 3.12 Single-hull and multi-hull scope

Initial ship is single-hull per study. Multi-hull surrogate (one GP across all hulls, transferring slot and archetype kernels) is a natural Phase 6+ extension once the single-hull kernel is validated. The slot-feature vector (§3.5) is hull-agnostic by construction, so multi-hull transfer requires only `hull_id` additional categorical features — not a redesign.

---

## 4. Rejected alternatives

### 4.1 Keep Optuna TPE with attribute-only warm-start — INSUFFICIENT

**What it was.** Leave the Optuna TPE sampler untouched; add attribute-vector-based warm-start seeding (30–60 Latin-hypercube seeds) and optional archetype-ranked seed ordering.

**Why rejected.** TPE's per-dimension independent KDE structure cannot exploit kernel structure (transformed-overlap categoricals, slot-feature similarity, SAAS sparsity). Warm-start helps the first ~30 trials but provides no ongoing benefit. Community meta → surrogate correlation would be lost after the initial seeds. ~1.3× sample efficiency gain vs ~3× for the full composite kernel — not worth freezing the design at the worst option.

### 4.2 Ma-Blaschko additive tree kernel — SUBSUMED

**What it was.** Ma & Blaschko 2020 "Additive Tree-Structured Conditional Parameter Spaces" (TPAMI [arXiv:2010.03171](https://arxiv.org/abs/2010.03171)) — additive tree kernel where overlapping subtrees share priors via ancestors. Explicit hierarchical structure: `role → large-slot → medium → small`.

**Why rejected.** Gated-sentinel imputation (§2.1, §3.6) + SAAS sparsity (§2.2, §3.3) achieves the same information-pooling without a tree-kernel commitment. Ziomek & Ammar 2023 ([arXiv:2301.12844](https://arxiv.org/abs/2301.12844)) showed random decompositions often match learned ones at our budget, further eroding the case for the tree-kernel complexity. The hierarchy is still encoded — via πBO archetype priors as a *soft* bias rather than a *hard* tree.

### 4.3 NAS weight-sharing (DARTS / ENAS / OFA / BigNAS) — DOES NOT TRANSFER

**What it was.** One-shot NAS trains a supernet whose weights are shared across candidate architectures; evaluating a sub-architecture is a cheap forward pass through a pre-trained tensor.

**Why rejected.** The "weights" in NAS are trained tensors; the analog in ship-build optimization does not exist. The expensive thing in our problem is the *simulation*, not any learned parameters, so there is nothing to share. Predictor-based NAS (White 2021 BANANAS [arXiv:1910.11858](https://arxiv.org/abs/1910.11858)) is materially the same as BO with a neural surrogate — no new primitive. The only genuinely transferable idea is component-level rating via TWFE / TrueSkill / hierarchical shrinkage, which Phase 5A already ships and Phase 5D will extend. Stay the course; do not rebrand as NAS.

### 4.4 HyperMapper / BaCO off-the-shelf — MISSING SAAS SUBSPACE

**What it was.** Wrap HyperMapper ([github.com/luinardi/hypermapper](https://github.com/luinardi/hypermapper)) as an Optuna sampler (~1–2 weeks) for its BaCO-style product kernel + gated-sentinel.

**Why rejected.** HyperMapper does not implement SAAS sparsity on subspaces. For our hullmod-boolean subspace (~30–80 dims, ~5–10 active) this is the load-bearing advantage; losing it gives up a majority of the sample-efficiency gain. The 1–2 week shortcut turns into a worse kernel; the 2–3 week custom BoTorch build captures everything.

Use HyperMapper as a validation oracle to verify the custom kernel's categorical handling matches the BaCO reference implementation, not as a drop-in.

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

**Why rejected.** User-explicit correction during design review: small slots must remain addressable because they are opponent-conditional (§2.8). The shipped design instead adds opponent summary features to small-slot posteriors (§3.9), preserving full tweakability while still letting the GP learn opponent-conditional defaults from data.

### 4.14 REMBO / ALEBO / HeSBO random-linear subspaces — WITHIN-GROUP INTERACTIONS

**What it was.** Wang 2013 REMBO ([arXiv:1301.1942](https://arxiv.org/abs/1301.1942)), Letham 2020 ALEBO ([arXiv:2001.11659](https://arxiv.org/abs/2001.11659)), Nayebi 2019 HeSBO ([arXiv:1902.10675](https://arxiv.org/abs/1902.10675)) — optimize on a random linear projection to a low-dim active subspace.

**Why rejected.** Assumes a global low-dim linear active subspace. Our problem has within-group interactions (PD matters only if opponent has missiles; kinetic needs HE follow-up) that a single global projection cannot capture. SAAS's axis-aligned (non-linear combinations) assumption fits our problem; random-linear does not.

### 4.15 Fixed community-meta archetype weights per hull — REJECTED

**What it was.** Hardcode archetype-mode mixture weights per hull from community meta (e.g., "Paragon: 0.6 turret-flex, 0.3 long-range-sniper, 0.1 other"; "Onslaught: 0.5 kinetic-HE brawler, 0.3 SO brawler, 0.2 other"). Initial sketch of the πBO prior from the literature sweep.

**Why rejected.** Three failures (enumerated in §2.5): (i) the 9-archetype taxonomy was induced from community analysis of ~7 meta hulls; the remaining ~40 combat hulls and all mod/patch-added hulls have sparse or absent weights; (ii) absolute-attribute mode means would mismatch across hull sizes (700-range sniper weapons don't match a capital-scale "sniper" mode defined at 1500 range); (iii) physical infeasibility — a Wolf cannot physically realize Broadside, Turret-flex, or Phase-striker modes, and assigning nonzero weight to impossible configurations wastes acquisition-function mass. The shipped design (§3.8) instead computes a per-hull feasibility mask from `.ship` data, initializes weights uniform over feasible modes, and uses self-correcting π (Hvarfner 2023) to online-downweight modes that disagree with per-hull evidence. Community meta contributes the *vocabulary of modes* (what characterizes a sniper vs a brawler in normalized attribute space) but not the *per-hull weights*.

### 4.16 Hardcoded AI-compatibility flags on archetype modes — REJECTED

**What it was.** Manually annotate each of the nine archetype modes with an AI-pilotability flag (low / medium / high, per §2.10 table) and multiply the πBO prior weight by a hardcoded pilotability factor at hull-load time. Example: SO-brawler weight halved because "community consensus says the AI mispilots it."

**Why rejected.** Three failures. (i) AI behavior changes across game patches — Alex periodically revises the combat AI's target selection, flux management, and ship-system usage; hardcoded compatibility flags rot. (ii) AI-compatibility interacts with hull: the AI may mispilot Gryphon's burst-missile pattern but handle burst-missile on Hammerhead fine because the Hammerhead's smaller missile count is within the AI's short-horizon planning. A global per-mode flag cannot encode this. (iii) The self-correcting mixture mechanism from §3.8 + §2.10 already handles AI-hostility via empirical evidence — an AI-hostile mode produces worse-than-predicted fitness and its mixture weight is reduced on the next Hvarfner 2023 update. Hardcoding the flag would both duplicate and prejudice the mechanism that is already running.

Community-meta insight about AI pilotability is preserved as *grounding* (§2.10 documents the known-hostile modes so the user can interpret why SO-brawler weights collapse fast on AI-vs-AI runs) but never enters the prior weights directly.

### 4.17 Optimize for player-piloted flagship builds — OUT OF SCOPE

**What it was.** Add a kernel context feature `is_flagship: bool` and expose per-hull player-piloted evaluation runs. Many community-top builds assume the player is the pilot (skill 5 Helmsmanship, Combat Endurance, Target Analysis). If we had player-in-the-loop simulation we could recover player-only optima.

**Why out of scope.** Phase 2 combat harness is AI-vs-AI by construction; no player-in-the-loop input mechanism exists and building one is outside Phase 6's scope (would require engine-level input injection, out of the current architectural envelope). The optimizer's deployment context is the fleet — most ships in a campaign fleet are AI-piloted, so the simulation already matches the dominant deployment case. Player-piloted flagship optimization is a distinct downstream problem that would require a separate simulation harness and is deferred indefinitely.

### 4.18 Standalone BOCA pilot — COMMIT-FOREVER RISK

**What it was.** Run 30 random-forest-importance trials, hard-partition flags into impactful / unimpactful, then BO over only impactful.

**Why rejected in standalone form.** If the pilot miscalls an important hullmod, it is randomized forever. At 30 pilot trials there is real risk of this. SAAS subsumes the principled form of the same idea with full reversibility — a wrongly-estimated hullmod can activate as more data accumulates. BOCA retained only as a SAAS *warm-start* (§3.10), not as a standalone dim-reduction mechanism.

---

## 5. Expected impact

Projected from sweep-reported sample-efficiency numbers at our 200–2000 trial regime. Each component's gain composes multiplicatively up to diminishing-returns saturation; conservative aggregate below.

| Component | Regime | Expected gain vs Optuna TPE | Source |
|---|---|---:|---|
| SAAS sparsity on hullmods | 30–80 hullmod dims | 2–5× at N = 200–500 | Eriksson-Jankowiak 2021 Table 2 |
| Transformed-overlap categorical kernel | 150-level weapon IDs × 8 slots | 1.3–1.6× | Hellsten 2024 (BaCO) Table 2 |
| Attribute-Matérn + ICM item residual | 7-dim attribute vector | 1.2–1.5× + zero-shot new-item transfer | Swersky-Snoek-Adams 2013 |
| Slot-feature Matérn | 5-dim slot feature | 1.1–1.3× (8 slots → ~3 effective) | Cross-slot pooling, no published benchmark |
| πBO archetype priors (hull-conditional) | Decay-weighted + self-correcting mixture weights | 2–5× if correct, ≤ 1.5× worst-case; hull-generalizable via feasibility mask (§3.8) | Hvarfner 2022 Fig. 3 + Hvarfner 2023 |
| BOCA warm-start | First 30 trials | 1.1–1.3× on cold-start hulls | Chen 2021 |
| Opponent features on smalls | 3-dim feature vec | Not benchmarked — qualitative: preserve user-required addressability | §2.8 community-meta evidence |
| **Aggregate (conservative)** | | **≈ 2–4× sample efficiency at N = 200–500** | |

At Hammerhead-scale 650 trials/24h, aggregate 3× efficiency is equivalent to ~1950 Phase-4 trials — matches the current 3-day run output in 24 hours. If the πBO priors hit their high end (5×), 24-hour output is equivalent to a 5-day Phase-4 run.

Game-update transfer: new weapons with similar 7-attribute profile inherit the kernel prior on day one. Kernel lengthscales remain stale only if the update rebalances the attribute-to-fitness function itself; BaCO's follow-up transfer paper (Hellsten 2024) quantifies this: ≥ 70% parameter-space preservation → clean transfer; < 70% → partial regression. Starsector patches historically change ≤ 20% of the weapon-attribute mass; safe.

### 5.1 Ship gate

Synthetic validation before any live run:

1. **Rank-correlation gate**: on a held-out synthetic fitness function (Phase 5D-style 7-covariate ground truth + 200-trial eval budget), Phase 6 surrogate top-10 rank ρ vs ground truth should be ≥ 0.70. Current Optuna TPE baseline ≈ 0.45.
2. **Cold-start gate**: on a new hull (not in pilot data), Phase 6 surrogate should reach the top-10 of a 2000-trial Phase-4 run within 500 Phase-6 trials.
3. **Game-update gate**: on simulated addition of 5 new weapons with attribute profiles interpolated from existing weapons, posterior mean at the new-weapon points should be within 0.2 σ of the attribute-interpolation ground truth with zero observations.
4. **Addressability gate**: on a missile-heavy opponent pool, the Phase 6 surrogate's small-slot posterior mean should prefer IPDAI + Dual Flak over the opponent-pool-agnostic posterior at p < 0.05.
5. **Hull-generalization gate**: on a non-meta hull (Gemini, Mudskipper, or any hull without published community meta), the Phase 6 surrogate should reach top-10 of a 1000-trial Phase-4 baseline within 500 Phase-6 trials — same sample-efficiency as meta hulls, verifying the feasibility-mask + uniform-initial-weights + self-correcting mechanism removes meta-hull coverage bias.

Pass all five → ship. Fail any → revise design.

---

## 6. Implementation plan (outline — full plan to be drafted on approval)

```
Step 1: Attribute + slot feature extraction + hull-size normalization
  - src/starsector_optimizer/models.py: extend WeaponAttributes (7 fields)
    and add SlotFeatures (5 fields). Add normalized variants
    (range / hull_size_max_range, op_cost / hull.op_budget) computed
    per-hull at search-space construction.
  - models.py: ensure HullSize enum feeds hull_size context feature
    (already present; just expose to kernel).
  - parser.py: populate SlotFeatures from .ship JSON during load.
  - Ship gate: round-trip tests, all existing hulls; cross-hull-size
    normalization sanity (range_normalized ∈ [0, 1] for every hull).

Step 2: Custom BoTorch kernel
  - src/starsector_optimizer/surrogate.py (new module):
    * CompositeShipBuildKernel class (product kernel from §3.1)
    * SAASHullmodKernel (half-Cauchy ARD on ~80 boolean dims)
    * TransformedOverlapKernel (Garrido-Merchán 2020)
    * AttributeMaternKernel (7-dim)
    * SlotFeatureMaternKernel (5-dim)
    * OpponentFeatureKernel (3-dim, applied to small-slot inputs only)
    * GatedSentinelEncoder (conditional handling)
    * ItemResidualKernel, SlotResidualKernel (ICM)
  - Unit tests with synthetic data.

Step 3: Optuna sampler wrapper
  - src/starsector_optimizer/botorch_sampler.py: BaseSampler subclass
    wrapping BoTorch GP + acquisition optimizer.
  - Integration test: sampler ↔ existing optimizer.py ask-tell loop.

Step 4: BOCA warm-start
  - 30-trial Latin-hypercube + RF-importance pilot.
  - Empirical-Bayes initialization of SAAS lengthscale priors.
  - Integration as first phase of ask-tell loop.

Step 5: πBO acquisition — hull-conditional mixture
  - Nine-mode GMM over (normalized attribute × slot × hullmod) feature
    space; mode definitions in hull-size-invariant units.
  - Per-hull feasibility mask computed at hull-load from .ship JSON +
    ship-system registry (table in §3.8). Infeasible modes → weight 0.
  - Uniform initial weights over feasible modes (no meta-hull bias).
  - Self-correcting mixture weight updates (Hvarfner 2023
    arXiv:2304.00397) via marginal likelihood ratio per trial.
  - Decay-weighted EI (Hvarfner 2022 Eq. 4); β = 5 default; CLI flag
    to disable.

Step 6: Opponent feature plumbing
  - Compute (has_missiles_frac, has_fighters_frac, mean_armor_rating)
    per trial in optimizer.py.
  - Thread into small-slot kernel input only.

Step 7: Validation
  - Synthetic ship-gate harness (§5.1 gates 1-4).
  - Hammerhead replay (2026-04-13 log) — compare top-10 composition
    and TPE convergence trace against Phase-6 surrogate.
```

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
- Hvarfner et al. 2023 "Self-Correcting Bayesian Optimization through Bayesian Active Learning," [arXiv:2304.00397](https://arxiv.org/abs/2304.00397).
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
- BaCO — [github.com/baco-authors/baco](https://github.com/baco-authors/baco) — validation oracle.
- Optuna — [github.com/optuna/optuna](https://github.com/optuna/optuna) — `BaseSampler` plugin API.

### See also
- `docs/reference/phase5d-covariate-adjustment.md` — EB fusion paradigm that §3.7 ICM residuals mirror.
- `docs/reference/phase5f-regime-segmented-optimization.md` — `(hull, regime)` study unit that Phase 6 operates on.
- `docs/reference/phase5-signal-quality.md` — TWFE + WilcoxonPruner + opponent curriculum upstream of Phase 6.
- `experiments/hammerhead-twfe-2026-04-13/` — throughput + 89%-concentration observation motivating the dimensionality argument.
- `src/starsector_optimizer/parser.py` — `_ParseableEnum` forward-compatibility mechanism that underwrites the game-update-invariance design property.
