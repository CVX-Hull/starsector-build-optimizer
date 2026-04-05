# Starsector Game Data Reference

File formats, locations, schemas, and parsing notes for all game data files relevant to build optimization.

---

## File Locations

All vanilla data lives under the game installation's `data/` directory. Mods follow the same structure under `mods/<ModName>/data/`.

| File | Path | Format | Records (0.98a) |
|---|---|---|---|
| Ship stats | `data/hulls/ship_data.csv` | CSV (56 columns) | ~200 hulls parsed |
| Ship geometry | `data/hulls/*.ship` | Loose JSON | ~200 files |
| Weapon stats | `data/weapons/weapon_data.csv` | CSV (49 columns) | ~205 rows (~146 parsed) |
| Weapon visuals | `data/weapons/*.weapon` | Loose JSON | 1 per weapon |
| Hullmod definitions | `data/hullmods/hull_mods.csv` | CSV (20 columns) | ~285 rows (~130 parsed) |
| Ship systems | `data/shipsystems/ship_systems.csv` | CSV | ~30 systems |
| System behavior | `data/shipsystems/*.system` | Loose JSON | 1 per system |
| Pre-built loadouts | `data/variants/*.variant` | Loose JSON | 91 variants |
| Descriptions | `data/strings/Descriptions.csv` | CSV | Flavor text |

### "Loose JSON" Format

Starsector uses a relaxed JSON format:
- Trailing commas are allowed
- Comments are allowed (# and //)
- Unquoted keys are sometimes used
- Numeric values may lack quotes

Standard `json.loads()` will fail. Use a lenient parser (e.g., `hjson`, `json5`, or custom regex preprocessing to strip comments and fix trailing commas).

---

## ship_data.csv Schema

Key columns for optimization:

| Column | Type | Description | Example |
|---|---|---|---|
| `name` | string | Display name | "Eagle" |
| `id` | string | Internal ID (used in variants) | "eagle" |
| `designation` | string | Role description (e.g., "Battleship", "Carrier", "Advanced Gunship") | "Cruiser" |
| `tech/manufacturer` | string | Tech line / faction | "Midline" |
| `system id` | string | Ship system reference | "maneuveringjets" |
| `fleet pts` | int | Deployment points | 15 |
| `hitpoints` | float | Hull HP | 5000 |
| `armor rating` | float | Base armor | 600 |
| `max flux` | float | Flux capacity | 5000 |
| `8/6/5000` | float | Flux dissipation | 300 |
| `ordnance points` | int | OP budget | 150 |
| `max speed` | float | Top speed (su/s) | 60 |
| `acceleration` | float | Forward acceleration | 40 |
| `deceleration` | float | Braking | 30 |
| `max turn rate` | float | Turning speed (deg/s) | 30 |
| `turn acceleration` | float | Turn accel (deg/s²) | 20 |
| `shield type` | string | NONE / OMNI / FRONT / PHASE | "FRONT" |
| `shield arc` | float | Shield coverage (degrees) | 150 |
| `shield upkeep` | float | Shield upkeep flux/s factor | 0.4 |
| `shield efficiency` | float | Flux per damage blocked | 0.8 |
| `min crew` | int | Minimum crew | 100 |
| `max crew` | int | Maximum crew | 250 |
| `cargo` | float | Cargo capacity | 120 |
| `fuel` | float | Fuel capacity | 100 |
| `fuel/ly` | float | Fuel consumption | 5 |
| `max burn` | int | Map movement speed | 8 |
| `base value` | int | Ship value in credits | 100000 |
| `cr %/day` | float | CR recovery per day | 8 |
| `CR to deploy` | float | CR cost to deploy | 0.15 |
| `peak CR sec` | float | Peak performance time (sec) | 480 |
| `CR loss/sec` | float | CR loss rate after peak | 0.0042 |
| `supplies/rec` | float | Supplies to recover | 15 |
| `supplies/mo` | float | Monthly supply cost | 15 |
| `c/s` | float | Combat speed factor | — |
| `c/f` | float | Combat flux factor | — |
| `hints` | string | Comma-separated tags | "CARRIER" |
| `tags` | string | Additional tags | "midline" |
| `rarity` | float | Spawn rarity | 0.5 |
| `fighter bays` | int | Number of fighter bays | 0 |
| `number` | int | Internal ordering | — |

### Notes
- `8/6/5000` is the flux dissipation column (badly named in the CSV header)
- `shield upkeep` is a multiplier on `max flux` to get upkeep flux/s
- `shield efficiency` lower = better (0.6 means 0.6 flux per 1 damage blocked)
- `peak CR sec` combined with `CR loss/sec` determines combat endurance
- Ships with `shield type = PHASE` use phase cloak instead of shields
- `designation` is a free-form role string (e.g., "Battleship", "Carrier", "Advanced Gunship"), NOT hull size. Hull size (FRIGATE, DESTROYER, CRUISER, CAPITAL_SHIP) comes from the `hullSize` field in `.ship` JSON files.

---

## .ship File Schema (Weapon Slots)

Each `.ship` file defines a hull's geometry. The `weaponSlots` array is the critical section:

```json
{
    "hullName": "Eagle",
    "hullId": "eagle",
    "hullSize": "CRUISER",
    "style": "MIDLINE",
    "spriteName": "graphics/ships/eagle/eagle.png",
    "center": [128, 128],
    "collisionRadius": 150,
    "shieldCenter": [0, 0],
    "shieldRadius": 180,
    "weaponSlots": [
        {
            "id": "WS 001",
            "size": "MEDIUM",
            "type": "HYBRID",
            "mount": "TURRET",
            "arc": 150,
            "angle": 0,
            "locations": [80, 20]
        },
        {
            "id": "WS 002",
            "size": "SMALL",
            "type": "ENERGY",
            "mount": "HARDPOINT",
            "arc": 10,
            "angle": 0,
            "locations": [100, 0]
        }
    ],
    "engineSlots": [ ... ],
    "bounds": [ ... ],
    "builtInMods": ["targetingunit"],
    "builtInWeapons": {
        "WS 010": "eagle_left_system"
    }
}
```

### Weapon Slot Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | Slot identifier (referenced in .variant files) |
| `size` | string | SMALL, MEDIUM, LARGE |
| `type` | string | BALLISTIC, ENERGY, MISSILE, HYBRID, COMPOSITE, SYNERGY, UNIVERSAL |
| `mount` | string | TURRET, HARDPOINT |
| `arc` | float | Firing arc width (degrees) |
| `angle` | float | Center angle of arc (0 = forward, 180 = rear) |
| `locations` | [x, y] | Position on sprite (pixels from center) |

### Built-In Weapons and Mods

- `builtInMods`: Array of hullmod IDs that are permanently installed (0 OP cost)
- `builtInWeapons`: Map of slot_id → weapon_id for permanently installed weapons. These slots cannot be changed during fitting.

---

## weapon_data.csv Schema

Key columns:

| Column | Type | Description | Example |
|---|---|---|---|
| `name` | string | Display name | "Heavy Mauler" |
| `id` | string | Internal ID | "heavymauler" |
| `tier` | int | Tech tier (1-3) | 2 |
| `rarity` | float | Drop rarity | 0.5 |
| `base value` | int | Credits value | 10000 |
| `range` | float | Weapon range (units) | 700 |
| `damage/second` | float | DPS for beams only | — |
| `damage/shot` | float | Per-projectile damage | 200 |
| `emp` | float | EMP damage per shot | 0 |
| `impact` | float | Impact force | 200 |
| `turn rate` | float | Turret rotation (deg/s) | 30 |
| `OPs` | int | Ordnance point cost | 10 |
| `ammo` | int | Ammunition count (0=unlimited) | 0 |
| `ammo/sec` | float | Ammo regeneration rate | 0 |
| `reload size` | int | Ammo per reload | 0 |
| `type` | string | Damage type: KINETIC, HIGH_EXPLOSIVE, ENERGY, FRAGMENTATION | "KINETIC" |
| `energy/shot` | float | Flux cost per shot | 200 |
| `energy/second` | float | Flux cost per second (beams) | — |
| `chargeup` | float | Time before first shot (sec) | 0 |
| `chargedown` | float | Cooldown after firing (sec) | 0.5 |
| `burst size` | int | Shots per burst | 1 |
| `burst delay` | float | Time between burst shots | 0 |
| `min spread` | float | Base inaccuracy (degrees) | 0 |
| `max spread` | float | Maximum spread cap | 2 |
| `spread/shot` | float | Spread added per shot | 0.5 |
| `spread decay/sec` | float | Spread recovery rate | 5 |
| `beam speed` | float | Beam tracking speed | — |
| `proj speed` | float | Projectile velocity | 500 |
| `launch speed` | float | Missile launch velocity | — |
| `flight time` | float | Missile flight duration | — |
| `proj hitpoints` | float | Projectile/missile HP | 0 |
| `hints` | string | Tags: PD, ANTI_FTR, STRIKE, etc. | "PD" |
| `tags` | string | Additional tags | "kinetic" |
| `groupTag` | string | Weapon group behavior | — |
| `tech/manufacturer` | string | Associated faction | "Hegemony" |
| `primaryRoleStr` | string | Primary combat role | "Assault" |
| `speedStr` | string | Projectile speed category | "Fast" |
| `trackingStr` | string | Tracking quality | "Good" |
| `turnRateStr` | string | Turn rate category | "Moderate" |
| `accuracyStr` | string | Accuracy category | "Good" |
| `customPrimary` | string | Custom descriptions | — |
| `customPrimaryHL` | string | Custom description highlights | — |
| `customAncillary` | string | Additional custom text | — |
| `customAncillaryHL` | string | Additional highlight text | — |
| `noDPSInTooltip` | bool | Hide DPS in tooltip | false |
| `number` | int | Sort order | — |

### Weapon Mount Type

**Important:** The `type` column in weapon_data.csv is the **damage type** (KINETIC, HIGH_EXPLOSIVE, ENERGY, FRAGMENTATION), NOT the weapon mount type. The weapon mount type (BALLISTIC, ENERGY, MISSILE) that determines slot compatibility is defined in the weapon's `.wpn` file (e.g., `data/weapons/heavymauler.wpn`) under the `"type"` field. The parser must read `.wpn` files to determine which slots a weapon can fit into.

### Derived Metrics (Computed During Parsing)

```python
# Sustained DPS
if weapon.burst_size > 1:
    cycle_time = weapon.chargeup + (weapon.burst_size - 1) * weapon.burst_delay + weapon.chargedown
    sustained_dps = weapon.damage_per_shot * weapon.burst_size / cycle_time
else:
    cycle_time = weapon.chargeup + weapon.chargedown
    sustained_dps = weapon.damage_per_shot / cycle_time if cycle_time > 0 else weapon.damage_per_second

# Flux efficiency
flux_efficiency = sustained_dps / max(flux_per_second, 0.001)

# Shield DPS (accounting for damage type multiplier)
shield_multiplier = {"KINETIC": 2.0, "HIGH_EXPLOSIVE": 0.5, "ENERGY": 1.0, "FRAGMENTATION": 0.25}
shield_dps = sustained_dps * shield_multiplier[weapon.damage_type]

# Armor DPS (depends on target armor, but can estimate for "average" armor)
# damageMultiplier = max(0.15, hitStrength / (hitStrength + effectiveArmor))
armor_multiplier = {"KINETIC": 0.5, "HIGH_EXPLOSIVE": 2.0, "ENERGY": 1.0, "FRAGMENTATION": 0.25}
```

---

## hull_mods.csv Schema

| Column | Type | Description |
|---|---|---|
| `name` | string | Display name |
| `id` | string | Internal ID |
| `tier` | int | Tech tier |
| `rarity` | float | Drop rarity |
| `tech/manufacturer` | string | Faction association |
| `tags` | string | Comma-separated tags |
| `uiTags` | string | UI filter tags |
| `base value` | int | Credits value |
| `unlocked` | bool | Available from start |
| `hidden` | bool | Hidden from UI |
| `hiddenEverywhere` | bool | Completely hidden |
| `cost_frigate` | int | OP cost on frigates |
| `cost_destroyer` | int | OP cost on destroyers |
| `cost_cruiser` | int | OP cost on cruisers |
| `cost_capital` | int | OP cost on capitals |
| `script` | string | Java class implementing the hullmod |
| `desc` | string | Short description |

### Identifying Logistics Hullmods

Logistics hullmods have the tag `"logistics"` in their `tags` field. Maximum 2 logistics hullmods per ship (unlimited if S-modded).

### Hullmod Effects

Hullmod effects are implemented in Java classes (e.g., `data/scripts/hullmods/HeavyArmor.java`). The effects cannot be read from the CSV alone — they must be extracted from:
- The wiki (starsector.wiki.gg)
- The game's Java source (decompiled)
- Manual documentation

Key effects to hardcode in the optimizer:

| Hullmod | Key Effects |
|---|---|
| Heavy Armor | +150/300/400/500 flat armor (by hull size) |
| Hardened Shields | ×0.80 shield efficiency |
| Safety Overrides | ×2 dissipation, +speed, PPT÷3, range cap ~450+25% |
| Shield Shunt | Remove shields, +15% armor (+30% if S-modded) |
| Stabilized Shields | ×0.5 shield upkeep |
| Expanded Missile Racks | +100% missile ammo |
| Integrated Targeting Unit | +200 weapon range |
| Reinforced Bulkheads | +40% hull HP |
| Blast Doors | -50% crew casualties |
| Armored Weapon Mounts | +100% weapon HP |

---

## .variant File Schema

Pre-built ship loadouts. The optimizer generates these to define candidate builds.

```json
{
    "displayName": "Assault",
    "fluxCapacitors": 10,
    "fluxVents": 15,
    "goalVariant": false,
    "hullId": "eagle",
    "hullMods": [
        "heavyarmor",
        "hardenedshieldemitter"
    ],
    "permaMods": [],
    "sMods": [],
    "variantId": "eagle_optimizer_v001",
    "weaponGroups": [
        {
            "autofire": false,
            "mode": "LINKED",
            "weapons": {
                "WS 001": "heavymauler",
                "WS 003": "heavymauler"
            }
        },
        {
            "autofire": true,
            "mode": "LINKED",
            "weapons": {
                "WS 005": "pdlaser",
                "WS 006": "pdlaser"
            }
        }
    ],
    "wings": []
}
```

### Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `variantId` | string | Yes | Unique ID for this loadout |
| `hullId` | string | Yes | Hull ID from ship_data.csv |
| `displayName` | string | No | Human-readable name |
| `fluxVents` | int | Yes | Number of flux vents installed |
| `fluxCapacitors` | int | Yes | Number of capacitors installed |
| `hullMods` | string[] | Yes | Installed hullmod IDs |
| `permaMods` | string[] | No | Permanent (non-removable) mods |
| `sMods` | string[] | No | Story-point built-in mods |
| `goalVariant` | bool | No | If true, appears in autofit suggestions |
| `weaponGroups` | array | Yes | Weapon group definitions |
| `wings` | string[] | No | Fighter wing IDs for carrier bays |

### Weapon Groups

| Field | Type | Description |
|---|---|---|
| `autofire` | bool | Whether AI controls this group |
| `mode` | string | "LINKED" (all fire together) or "ALTERNATING" |
| `weapons` | object | Map of slot_id → weapon_id |

### Weapon Grouping Strategy for Optimizer

When generating variants, use sensible defaults:
1. **Group 1 (manual)**: Primary weapons (highest DPS, same range band)
2. **Group 2 (autofire)**: Secondary weapons (different range or role)
3. **Group 3 (autofire)**: PD weapons
4. **Group 4 (autofire)**: Missiles

Or simply: all weapons on autofire in individual groups (safe default for AI-controlled testing).

---

## Mod Data Loading

Mods can add, modify, or replace any game data. Load order:

1. Load vanilla data from the game's `data/` directory (project path: `game/starsector/data/`)
2. For each enabled mod (from `mods/` under the game root):
   - If mod has `data/hulls/ship_data.csv`, merge/override entries
   - If mod has `data/weapons/weapon_data.csv`, merge/override entries
   - If mod has new `*.ship` files, add to hull pool
   - If mod has new `*.variant` files, add to variant pool
3. Mod load order defined in `enabled_mods.json`

### Key Modding Conventions

- Mod weapon/hull IDs should be prefixed with mod name to avoid conflicts
- `mod_info.json` in each mod directory defines dependencies and load order
- Some mods replace vanilla content entirely (check `replace` tags)
