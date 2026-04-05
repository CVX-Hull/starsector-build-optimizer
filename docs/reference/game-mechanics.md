# Starsector Combat Mechanics and Ship Fitting

This document covers every aspect of Starsector's combat system relevant to build optimization. Understanding these mechanics is essential for designing the heuristic scorer, interpreting simulation results, and defining meaningful behavior descriptors for quality-diversity search.

---

## Table of Contents

1. [Ship Hulls and Classification](#1-ship-hulls-and-classification)
2. [Ordnance Points and the Fitting Budget](#2-ordnance-points-and-the-fitting-budget)
3. [Weapon Slots](#3-weapon-slots)
4. [Weapons](#4-weapons)
5. [Flux System](#5-flux-system)
6. [Armor System](#6-armor-system)
7. [Shield System](#7-shield-system)
8. [Overload Mechanics](#8-overload-mechanics)
9. [Hull Modifications](#9-hull-modifications)
10. [Ship Systems](#10-ship-systems)
11. [Combat Readiness and Peak Performance](#11-combat-readiness-and-peak-performance)
12. [AI Behavior](#12-ai-behavior)
13. [Officer Skills](#13-officer-skills)
14. [Fleet-Wide Player Skills](#14-fleet-wide-player-skills)
15. [The Meta: What Makes Builds Good](#15-the-meta-what-makes-builds-good)

---

## 1. Ship Hulls and Classification

### Hull Sizes

| Property | Frigate | Destroyer | Cruiser | Capital |
|---|---|---|---|---|
| Average OP | ~50 | ~100 | ~150 | ~300-400 |
| Hullmod cost multiplier | 1x | 2x | 3x | 5x |
| Max capacitors/vents | 10 each | 20 each | 30 each | 50 each |
| Overload min duration | 4s | 6s | 8s | 10s |
| Strafe acceleration | 100% | 75% | 50% | 25% |

### Tech Lines

Three design philosophies define ship characteristics:

- **Low Tech** (red/brown): High armor, inefficient shields, ballistic-focused, slow. Tanky brawlers. Examples: Onslaught, Dominator, Enforcer, Lasher.
- **Midline** (yellow): Balanced armor/shields, mixed weapon types, average mobility. Versatile generalists. Examples: Eagle, Falcon, Hammerhead, Medusa.
- **High Tech** (white/blue): Low armor, efficient shields, energy-focused, fast. Agile glass cannons or shield tanks. Examples: Paragon, Odyssey, Aurora, Tempest.

### Vanilla Ship Count

- ~200 hulls in 0.98a ship_data.csv (including faction/LP variants with different built-in hullmods and modified weapon slots)
- Hull stats defined in `data/hulls/ship_data.csv`
- Slot layouts defined in `data/hulls/*.ship` (JSON)

---

## 2. Ordnance Points and the Fitting Budget

Every ship has a **fixed OP budget** that cannot be increased. All fitting decisions consume OP:

- Weapons: 1-50 OP each (varies by weapon)
- Hull modifications: varies by hullmod and hull size
- Flux vents: 1 OP each (up to max by hull size)
- Flux capacitors: 1 OP each (up to max by hull size)

The OP budget is THE dominant constraint in build optimization. It forces tradeoffs between offense (weapons), defense (hullmods), and flux management (vents/caps).

---

## 3. Weapon Slots

### Mount Types (7 types)

| Type | Symbol | Accepts |
|---|---|---|
| Ballistic | Yellow square | Ballistic weapons only |
| Energy | Blue circle | Energy weapons only |
| Missile | Green diamond | Missile weapons only |
| Hybrid | Orange | Ballistic OR energy |
| Composite | Lime | Ballistic OR missile |
| Synergy | Turquoise | Energy OR missile |
| Universal | Grey | All weapon types |

### Sizes (3 sizes)

- **Small**: Accepts small weapons only
- **Medium**: Accepts medium or small weapons (same type)
- **Large**: Accepts large or medium weapons (same type)

### Mount Modes

- **Turrets**: Wide firing arcs, standard durability (250/500/800 HP by size)
- **Hardpoints**: Narrow arcs (≤20°), doubled durability, 75% slower rotation, 50% less recoil

### Slot Properties (from .ship files)

Each slot has: ID, type, size, mount mode, position (x,y), angle, arc (firing cone width). These properties constrain weapon placement and affect combat effectiveness (rear-facing turrets rarely fire at targets in front).

---

## 4. Weapons

### Weapon Count

**129 weapons** in vanilla 0.98a (31 ballistic, 42 energy, 46 missile, plus 9 fighter-only).

### Delivery Mechanisms

| Property | Beam | Projectile | Missile |
|---|---|---|---|
| Hit detection | Hitscan (instant) | Travel time | Travel time + guided |
| Flux to fire | Yes (flux/second) | Yes (flux/shot) | **No flux cost** |
| Flux on shields | **Soft flux only** | **Hard flux** | **Hard flux** |
| Ammo | Unlimited | Usually unlimited | **Limited ammo** |
| Interceptable | No | No | **Yes (by PD)** |

**Critical implication**: Beams cannot force overloads alone (soft flux only). Projectile kinetics are the primary shield-breaking tool. Missiles provide free burst damage but are ammo-limited and interceptable.

### Damage Types

| Type | vs Shields | vs Armor | vs Hull | Role |
|---|---|---|---|---|
| Kinetic | 200% | 50% | 100% | Shield pressure |
| High Explosive | 50% | 200% | 100% | Armor cracking |
| Energy | 100% | 100% | 100% | Balanced |
| Fragmentation | 25% | 25% | 100% | Anti-hull (low armor) |
| EMP | Blocked (0%) | Bypasses | Special | Disruption |

### Burst Weapon Mechanics

- **chargeup**: Seconds before first shot fires
- **burst size**: Projectiles per trigger pull
- **burst delay**: Time between shots within a burst
- **chargedown**: Cooldown after burst before next fire cycle
- **Total cycle time** = chargeup + (burst_size - 1) × burst_delay + chargedown
- **Sustained DPS** = damage_per_shot × burst_size / total_cycle_time

### Weapon Accuracy / Spread

- **min spread**: Base inaccuracy in degrees (0 = perfect)
- **max spread**: Maximum inaccuracy cap
- **spread/shot**: Degrees added per shot fired
- **spread decay/sec**: Recovery per second of not firing
- Weapon starts at min_spread; each shot adds spread/shot; decays at spread_decay/sec; capped at max_spread

### Point Defense (PD)

- PD-tagged weapons automatically target incoming missiles on autofire
- PD weapons effectively engage fighters (high turret slew rates)
- Missiles have HP; PD depletes it before impact
- **Integrated Point Defense AI** hullmod improves PD targeting
- **Critical for shieldless builds**: Without PD, Shield Shunt builds get destroyed by missiles

### Weapon Groups and Autofire

- Up to **7 weapon groups** per ship
- AI evaluates flux cost vs benefit **per group**, not per weapon
- Linked weapons fire simultaneously; alternating cycles through them
- Grouping expensive with cheap weapons causes AI hesitation
- Weapons tagged with roles (Anti-Armor, Anti-Shield, PD, Strike) influence AI firing priority

### Key Optimization Metrics for Weapons

| Metric | Formula | Significance |
|---|---|---|
| Sustained DPS | damage/cycle_time | Raw offensive output |
| Flux efficiency | damage/flux_cost | Sustainability |
| Shield DPS | sustained_DPS × shield_multiplier | Kinetic = 2x here |
| Alpha strike | total burst damage in one salvo | Overwhelming defenses |
| Range | weapon range stat | Engagement envelope |

---

## 5. Flux System

Flux is the central combat resource — a heat/power limitation system.

### Flux Generation Sources

- Firing weapons (per-shot or per-second flux cost)
- Shield upkeep (soft flux while active)
- Blocking damage with shields (hard flux = shield_efficiency × damage)
- Phase cloak activation (hard flux)
- Some ship systems

### Two Flux Types

- **Soft Flux**: Generated by most actions. Dissipates continuously and passively.
- **Hard Flux**: Generated by shields blocking attacks and phase cloaks. Only dissipates when shields/cloaks are DOWN. Dissipates only after all soft flux is gone.

This distinction is critical: beams generate only soft flux on shields, while projectiles and missiles generate hard flux. A ship pressured entirely by beams can simply lower shields momentarily to vent. A ship pressured by kinetic projectiles accumulates hard flux that persists.

### Flux Capacity and Dissipation

- **Flux Capacity** = maximum flux before overload. Each **capacitor** adds +200 capacity (1 OP each).
- **Flux Dissipation** = passive drain rate (flux units/second). Each **vent** adds +10 dissipation (1 OP each).
- Max vents/caps by hull size: Frigate 10, Destroyer 20, Cruiser 30, Capital 50.
- **Flux Regulation** player skill adds +5 to both caps and vents regardless of hull size.

### Active Venting

Manually venting dissipates flux at 2x normal rate but prevents all non-movement actions. Cannot be used during overload.

### The Flux Balance Rule

Community consensus: **weapon flux generation should not exceed ~60-80% of dissipation rate** for sustained combat. Exceeding this causes the ship to gradually flux up and eventually overload.

Formula: `flux_balance = total_weapon_flux_per_second / flux_dissipation`
- < 0.6: Very conservative, can sustain fire indefinitely
- 0.6-0.8: Good balance
- 0.8-1.0: Aggressive, will need to manage flux actively
- \> 1.0: Will flux out without careful management
- \> 2.0: Unworkable — ship overloads almost immediately

---

## 6. Armor System

### Armor Grid

Every ship has a rectangular armor grid overlaid on its sprite. Each cell holds armor equal to **1/15th of the base armor rating**. Grid dimensions vary by hull.

### Damage Calculation Pipeline

**Step 1: Determine hit location** on the armor grid.

**Step 2: Pool effective armor from 5x5 neighborhood.**
- 9 inner cells (3x3 center): full current armor value
- 12 outer cells (ring): half current armor value
- When pristine: effective armor = base armor rating (9/15 + 6/15 = 1.0x)

**Step 3: Apply damage type armor multiplier** to get "hit strength."

**Step 4: Compute damage reduction factor.**
```
damageMultiplier = max(0.15, hitStrength / (hitStrength + effectiveArmor))
```
- If hitStrength == effectiveArmor: 50% gets through
- Minimum multiplier: **0.15** (max 85% reduction)
- With Polarized Armor skill: floor drops to 0.10 (90% reduction)
- **Key insight**: High per-shot damage penetrates armor exponentially better than equivalent DPS from many small hits

**Step 5: Distribute damage to armor cells** in the same 5x5 pattern.

**Step 6: Residual armor floor.** Even when cells reach zero, a **5% of base armor** floor is used for hull hit calculations.

**Step 7: Hull damage.** Excess damage passes to hull HP, still reduced by residual armor.

### Implications for Optimization

- Weapons with high damage-per-shot are disproportionately effective against armor
- Many small hits (e.g., Vulcan Cannon) are almost completely stopped by intact armor
- Armor-stripping is spatial — concentrated fire on one area degrades armor faster
- The 0.15 minimum multiplier means even the heaviest armor can be chipped away

---

## 7. Shield System

### Shield Types

- **Omni shields**: Rotate to face cursor/threat direction. Typically narrower arcs, higher upkeep.
- **Front shields**: Locked to ship facing. Wider arcs, extend 2x faster, lower upkeep.
- **Phase cloak**: Replaces shields. Ship becomes untargetable; generates hard flux.
- **No shields**: Some ships (or Shield Shunt builds) have no shield capability.

### Shield Efficiency

Shield efficiency is a per-hull stat. Lower = better (less flux per damage blocked).

Modifiers stack **multiplicatively**:
- Base efficiency: hull-specific (e.g., Paragon 0.6, Onslaught 1.0)
- 100% CR bonus: ×0.85
- Hardened Shields hullmod: ×0.80
- Field Modulation skill: ×0.85

Example: 0.7 base × 0.85 (CR) × 0.80 (Hardened) × 0.85 (Field Mod) = **0.405 effective**

### Flux on Shields

- Shield upkeep: soft flux (continuous while shields are raised)
- Damage blocked: `flux = damage × shield_efficiency × damage_type_shield_multiplier`
  - Kinetic hits: 2x → generates lots of flux
  - HE hits: 0.5x → very flux-efficient to block
  - Beams: generate **soft flux** (can be vented by lowering shields)
  - Projectiles/missiles: generate **hard flux** (persists until shields lowered AND soft flux cleared)

---

## 8. Overload Mechanics

### Trigger

When blocking an attack would push flux above capacity. The attack is still fully blocked, then overload begins.

### Duration Formula

```
duration = min(15, max(min_duration, attack_strength / 25))
```

Minimum durations by hull size: Frigate 4s, Destroyer 6s, Cruiser 8s, Capital 10s. Maximum: 15s for all.

### Effects During Overload

- Cannot fire weapons, raise shields, use ship system, vent, or phase cloak
- Flux dissipates at **half** normal rate
- Ship can still move but is extremely vulnerable
- Field Modulation skill reduces duration by 25%

---

## 9. Hull Modifications

### Categories

| Category | Behavior |
|---|---|
| **Standard hullmods** | Install/remove freely; cost OP |
| **Logistics hullmods** | Only add/remove while docked; max 2 per ship |
| **Built-in hullmods** | Come with specific hulls; 0 OP; cannot be removed |
| **D-mods** | Negative effects from ship recovery; repairable |
| **S-mods** | Spend 1 Story Point to permanently build in; costs 0 OP afterward; often gains bonus. Max 2 per ship (3 with Best of the Best skill) |

### Installable Hullmod Count

~130 hullmods parsed after filtering non-installable entries. ~285 total CSV rows in hull_mods.csv including built-in, d-mods, and other non-installable entries.

### Hullmod OP Cost Scaling

Hullmod costs scale by hull size with multipliers 1x/2x/3x/5x (Frigate/Destroyer/Cruiser/Capital). This makes some hullmods proportionally cheaper on smaller ships.

### Known Incompatibilities

| Hullmod A | Hullmod B | Reason |
|---|---|---|
| Shield Shunt | Makeshift Shield Generator | Mutually exclusive |
| Shield Conversion - Front | Shield Conversion - Omni | Both modify shield type |
| Safety Overrides | Flux Shunt | Explicitly incompatible |
| Safety Overrides | Capital ships | SO cannot be installed on capitals |

### Key Synergy Packages

**Safety Overrides (SO) Brawler:**
- SO: 2x dissipation, +50/30/20 speed (by size), PPT ÷ 3, range cap ~450 units
- + Hardened Subsystems (offsets PPT reduction)
- + Heavy Armor or Shield Shunt (survivability at close range)
- + Short-range high-DPS weapons (Assault Chaingun, Heavy Blaster)

**Shield Shunt Tank:**
- Shield Shunt: +15% armor (+30% if S-modded)
- + Heavy Armor: +150/300/400/500 flat armor
- + Blast Doors, Reinforced Bulkheads, Armored Weapon Mounts
- + 360° PD coverage (critical — no shields to block missiles)

**Shield Stacking (High-Tech):**
- Hardened Shields (×0.80 efficiency)
- + Stabilized Shields (reduced upkeep)
- + Extended Shields or Shield Conversion - Front

**Range/Sniper:**
- Integrated Targeting Unit or Dedicated Targeting Core
- + Advanced Optics (beams), Ballistic Rangefinder (ballistics)

---

## 10. Ship Systems

Every ship has one unique built-in ship system (cannot be changed during fitting). Examples:

- **Burn Drive**: Massive forward speed burst
- **Phase Cloak**: Stealth + intangibility
- **Maneuvering Jets**: Omnidirectional mobility burst
- **Fortress Shield**: 360° shield at reduced dissipation
- **Entropy Amplifier**: Debuffs target's flux stats
- **Plasma Burn**: Fast forward dash

Ship systems are defined in `data/shipsystems/ship_systems.csv` and `.system` files.

---

## 11. Combat Readiness and Peak Performance

### CR Range: 0-100%

Baseline for well-supplied ships: 70%.

### Stat Bonuses at 100% CR (scaling linearly from 70%)

- +10% speed and maneuverability
- -10% damage taken
- +10% damage dealt
- +10% fighter refit improvement
- Excellent autofire accuracy
- Zero malfunction risk

### Penalties Below 50% CR (scaling to 0%)

- -10% speed/maneuverability
- +10% damage taken
- -10% damage dealt
- 40% CR: random temporary malfunctions
- 20% CR: critical malfunctions, permanent disables
- 0% CR: shields, phase cloak, ship system unusable

### Peak Performance Time (PPT)

After PPT expires, CR degrades at 0.25%/second (15%/minute).

| Size | Low-Tech | Midline | High-Tech |
|---|---|---|---|
| Capital | 12 min | 12 min | 12 min |
| Cruiser | 9 min | 8 min | 7 min |
| Destroyer | 7 min | 6 min | 5 min |
| Frigate | 5 min | 4 min | 3 min |

- Combat Endurance skill: +60 seconds
- Safety Overrides: **PPT ÷ 3** (massive reduction)

---

## 12. AI Behavior

### Target Selection

The AI evaluates: current flux level, weapon ranges, missile threats, target's flux/shield/overload state, proximity of other threats, friendly fire concentration, own integrity, ship classification tags.

### Engagement by Personality

| Personality | Engagement Range | Retreat Threshold | Behavior |
|---|---|---|---|
| Timid | Maximum range | Very low flux | Minimal initiative |
| Cautious | Longest weapon range | Moderate flux | Maintains distance |
| Steady (default) | Balanced | ~80% flux | Standard line combat |
| Aggressive | Shortest non-missile range | ~85% flux | Closes hard |
| Reckless | Minimum range | Extreme danger only | Ignores being outnumbered |
| Fearless (automated) | Extreme close | Near-death only | More aggressive than Reckless |

### Shield Management

AI raises shields against incoming threats. Ships at high flux (~80-85%) try to back off. AI can get "flux-locked" — refusing to lower shields when overextended.

### Weapon Firing

AI manages flux per weapon group, not per weapon. Evaluates flux cost vs benefit per group. Expensive weapons grouped with cheap ones cause hesitation.

### Implications for Optimization

- Build archetypes must match the AI's engagement style
- SO brawler builds paired with Aggressive/Reckless officers close range effectively
- Long-range builds with Cautious officers maintain standoff distance
- Weapon grouping affects AI behavior as much as raw weapon stats
- Combat outcomes are stochastic — AI decisions, weapon spread, and timing vary per run

---

## 13. Officer Skills

Officers can pick up to 5 skills (6 with Officer Training), with 1 elite skill.

| Skill | Effect | Elite Bonus |
|---|---|---|
| Helmsmanship | +50% maneuverability, +15% top speed | +10 su/s speed |
| Combat Endurance | +60s PPT, -25% CR degradation | Regen 0.5% hull/s |
| Impact Mitigation | -25% armor damage, -50% engine/weapon damage | +50%/+25% maneuverability |
| Damage Control | -25% hull damage, -50% crew loss | Repairs while under fire |
| Field Modulation | -15% shield damage, -25% phase flux | 20% hard flux dissipation while shields up |
| Point Defense | +50% fighter/missile damage | +200 PD range |
| Target Analysis | +15% dmg vs cruisers, +20% vs capitals | +100% weapon/engine damage |
| Ballistic Mastery | +10% ballistic damage, +10% range | +33% projectile speed |
| Systems Expertise | +1 system charge, +50% regen | -10% damage taken |
| Missile Specialization | +100% missile ammo, +25% missile HP | +25% missile RoF |

---

## 14. Fleet-Wide Player Skills

These affect all ships in the fleet during combat:

- **Tactical Drills**: +5% weapon damage fleet-wide (up to 240 DP)
- **Wolfpack Tactics**: Frigates +20% damage to larger ships; Destroyers +10% to capitals/cruisers
- **Fighter Uplink**: +10% fighter damage, +20% fighter speed
- **Carrier Group**: +50% fighter replacement rate
- **Electronic Warfare**: -1% per ship enemy weapon range (max -10%)
- **Flux Regulation**: +5 max cap/vent slots, +10% capacity and dissipation
- **Cybernetic Augmentation**: +1% damage dealt / -1% taken per elite skill

---

## 15. The Meta: What Makes Builds Good

### Community-Recognized Archetypes

1. **SO Brawler**: Safety Overrides + short-range high-DPS weapons. Dominates at close range.
2. **Long-Range Fire Support**: ITU/DTC + long-range beams/ballistics. Stays safe at range.
3. **Shield-Tank Anchor**: High shield efficiency + Hardened Shields + massive flux stats.
4. **Armor-Tank / Shield Shunt**: Shield Shunt + Heavy Armor + maxed hull/armor + PD coverage.
5. **Carrier**: Primary damage through fighter wings. Ship weapons are secondary/PD.
6. **Phase Striker**: Phase cloak for positioning; burst damage then phases out.
7. **Missile Platform**: Alpha strike via missile volleys. Limited by ammo.
8. **Skirmisher/Objective Runner**: Fast frigates that capture objectives and harass.

### Evaluation Metrics (Experienced Players)

| Metric | What It Measures |
|---|---|
| Flux efficiency (damage/flux) | How much damage per unit of flux spent |
| Sustained DPS at range | Total DPS deliverable at engagement range |
| Alpha strike potential | Max damage in one volley |
| Time-to-overload | How quickly can force enemy overload |
| PPT budget | How long the ship fights before CR degradation |
| Flux balance | Weapon flux gen vs dissipation |
| OP efficiency | Value per OP spent |

### Common Build Mistakes (Traps)

1. **Filling every weapon slot** — rear/awkward mounts waste OP
2. **Under-investing in vents/caps** — powerful weapons useless if ship flux-locks
3. **Mixing incompatible range profiles** — half the weapons always idle
4. **All beams, no hard flux pressure** — cannot force overloads
5. **No PD on shieldless builds** — destroyed by missiles
6. **AI-unfriendly weapon groups** — expensive + cheap in same group causes hesitation
7. **SO on slow ships** — lose range without gaining ability to close
8. **Ignoring missile threats** — AI "feels" threatened by missiles even if weak

### Notable Strong Builds (Community Consensus)

- **Onslaught**: Shield Shunt + Heavy Armor + TPCs/Devastators + Assault Chainguns
- **Paragon**: Hardened Shields + 4x Tachyon Lance or mixed beams
- **Conquest**: Gauss Cannons + Hephaestus/Mark IX + long-range missiles
- **Hammerhead (SO)**: Safety Overrides + 2x Heavy Mauler + medium energy
- **Tempest**: Built-in Terminator Drone + PD Laser + Ion Pulser
