# Hullmod Effects Specification

Single source of truth for all hardcoded game knowledge. Defined in `src/starsector_optimizer/hullmod_effects.py`.

## Game Constants

All game constants that are not in data files are defined here:

```python
SLOT_COMPATIBILITY: dict[SlotType, set[WeaponType]]  # slot→accepted weapon types
SHIELD_DAMAGE_MULT: dict[DamageType, float]           # defined in models.py
ARMOR_DAMAGE_MULT: dict[DamageType, float]            # defined in models.py
```

Note: MAX_VENTS, MAX_CAPACITORS, FLUX_PER_CAPACITOR, DISSIPATION_PER_VENT, and damage multipliers are defined in `models.py` since they're needed by model properties.

## Slot Compatibility

```python
SLOT_COMPATIBILITY = {
    SlotType.BALLISTIC: {WeaponType.BALLISTIC},
    SlotType.ENERGY:    {WeaponType.ENERGY},
    SlotType.MISSILE:   {WeaponType.MISSILE},
    SlotType.HYBRID:    {WeaponType.BALLISTIC, WeaponType.ENERGY},
    SlotType.COMPOSITE: {WeaponType.BALLISTIC, WeaponType.MISSILE},
    SlotType.SYNERGY:   {WeaponType.ENERGY, WeaponType.MISSILE},
    SlotType.UNIVERSAL: {WeaponType.BALLISTIC, WeaponType.ENERGY, WeaponType.MISSILE},
}
```

## HullModEffect Registry

```python
@dataclass(frozen=True)
class HullModEffect:
    armor_flat_bonus: dict[HullSize, float]    # flat armor added by hull size
    shield_efficiency_mult: float = 1.0        # multiplier on shield efficiency
    dissipation_mult: float = 1.0              # multiplier on flux dissipation
    speed_bonus: dict[HullSize, float]         # flat speed added by hull size
    range_bonus: float = 0.0                   # flat weapon range bonus
    range_cap: float | None = None             # weapon range hard cap
    hull_hp_mult: float = 1.0                  # multiplier on hull hitpoints
    armor_mult: float = 1.0                    # multiplier on armor rating
    removes_shields: bool = False
    ppt_mult: float = 1.0                      # multiplier on peak performance time
    missile_ammo_mult: float = 1.0
    shield_upkeep_mult: float = 1.0
    custom_effects: dict[str, Any] = {}        # extensibility for novel mechanics
```

### Registry Contents (verified against 0.98a)

| Hullmod ID | Name | Key Effects |
|---|---|---|
| `heavyarmor` | Heavy Armor | armor_flat_bonus: F=150, D=300, C=400, Cap=500 |
| `hardenedshieldemitter` | Hardened Shields | shield_efficiency_mult=0.80 |
| `safetyoverrides` | Safety Overrides | dissipation_mult=2.0, speed_bonus: F=50,D=30,C=20, range_cap=450, ppt_mult=0.333 |
| `shield_shunt` | Shield Shunt | removes_shields=True, armor_mult=1.15 |
| `reinforcedhull` | Reinforced Bulkheads | hull_hp_mult=1.40 |
| `stabilizedshieldemitter` | Stabilized Shields | shield_upkeep_mult=0.50 |
| `targetingunit` | Integrated Targeting Unit | range_bonus=200 |
| `magazines` | Expanded Magazines | missile_ammo_mult=2.0 |

## Constraint Constants

### Incompatible Pairs
```python
INCOMPATIBLE_PAIRS = [
    ("shield_shunt", "frontshield"),        # Shield Shunt ↔ Makeshift Shield Generator
    ("frontemitter", "adaptiveshields"),     # Shield Conversion Front ↔ Omni
    ("safetyoverrides", "fluxshunt"),        # Safety Overrides ↔ Flux Shunt
]
```

### Hull Size Restrictions
```python
HULL_SIZE_RESTRICTIONS = {
    "safetyoverrides": {FRIGATE, DESTROYER, CRUISER},  # forbidden on CAPITAL_SHIP
}
```

### Shield-Dependent Mods
```python
SHIELD_DEPENDENT_MODS = {
    "hardenedshieldemitter", "stabilizedshieldemitter",
    "adaptiveshields", "frontemitter", "extendedshieldemitter",
}
```
Excluded when hull has shield_type == NONE or Shield Shunt is installed.

## Functions

### compute_effective_stats(hull, build, game_data) → EffectiveStats
Applies all hullmod modifiers from the registry. For each hullmod in build.hullmods, looks up HULLMOD_EFFECTS and applies multipliers/bonuses to base hull stats.

### get_effective_weapon_range(weapon, effective_stats) → float
Returns weapon.range + effective_stats.weapon_range_bonus, capped by effective_stats.weapon_range_cap if set.

### validate_registry(game_data) → list[str]
Checks all hullmod IDs in HULLMOD_EFFECTS, INCOMPATIBLE_PAIRS, HULL_SIZE_RESTRICTIONS, and SHIELD_DEPENDENT_MODS exist in game_data.hullmods. Returns list of warning messages.
