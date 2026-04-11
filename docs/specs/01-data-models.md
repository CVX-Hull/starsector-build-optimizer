# Data Models Specification

Core dataclasses and enums used by all modules. Defined in `src/starsector_optimizer/models.py`.

## Enums

All enums are `StrEnum` subclasses with a `from_str(value: str) -> Self | None` classmethod for forward-compatible parsing. Returns `None` for unknown values instead of raising.

### HullSize
`FRIGATE`, `DESTROYER`, `CRUISER`, `CAPITAL_SHIP`

Mapping from CSV `designation` column: `"Frigate"` → FRIGATE, `"Destroyer"` → DESTROYER, `"Cruiser"` → CRUISER, `"Capital Ship"` → CAPITAL_SHIP.

### SlotType
`BALLISTIC`, `ENERGY`, `MISSILE`, `HYBRID`, `COMPOSITE`, `SYNERGY`, `UNIVERSAL`

### SlotSize
`SMALL`, `MEDIUM`, `LARGE`

### MountType
`TURRET`, `HARDPOINT`

### ShieldType
`NONE`, `OMNI`, `FRONT`, `PHASE`

### DamageType
`KINETIC`, `HIGH_EXPLOSIVE`, `ENERGY`, `FRAGMENTATION`

### WeaponType
`BALLISTIC`, `ENERGY`, `MISSILE`

---

## Dataclasses

### WeaponSlot

```python
@dataclass(frozen=True)
class WeaponSlot:
    id: str                    # e.g. "WS 001"
    slot_type: SlotType        # BALLISTIC, ENERGY, MISSILE, HYBRID, etc.
    slot_size: SlotSize        # SMALL, MEDIUM, LARGE
    mount_type: MountType      # TURRET, HARDPOINT
    angle: float               # Center angle of arc (degrees, 0=forward)
    arc: float                 # Firing arc width (degrees)
    position: tuple[float, float]  # (x, y) on sprite
```

### ShipHull

```python
@dataclass
class ShipHull:
    id: str                    # e.g. "eagle"
    name: str                  # e.g. "Eagle"
    hull_size: HullSize
    designation: str           # Raw CSV value, e.g. "Cruiser"
    tech_manufacturer: str     # e.g. "Midline"
    system_id: str             # e.g. "maneuveringjets"
    fleet_pts: int
    hitpoints: float
    armor_rating: float
    max_flux: float
    flux_dissipation: float
    ordnance_points: int
    fighter_bays: int
    max_speed: float
    shield_type: ShieldType
    shield_arc: float
    shield_upkeep: float       # Multiplier on max_flux for upkeep flux/s
    shield_efficiency: float   # Lower = better (flux per damage blocked)
    phase_cost: float
    phase_upkeep: float
    peak_cr_sec: float
    cr_loss_per_sec: float
    weapon_slots: list[WeaponSlot]       # From .ship file (empty if no .ship)
    built_in_mods: list[str]             # Hullmod IDs from .ship file
    built_in_weapons: dict[str, str]     # slot_id → weapon_id from .ship file
    hints: list[str]                     # e.g. ["CARRIER"]
    tags: list[str]                      # e.g. ["rare_bp", "merc"]
```

**Properties:**
- `max_vents: int` — max flux vents by hull size (from game constants)
- `max_capacitors: int` — max flux capacitors by hull size (same values as max_vents)

### Weapon

```python
@dataclass
class Weapon:
    id: str                    # e.g. "heavymauler"
    name: str                  # e.g. "Heavy Mauler"
    size: SlotSize             # SMALL, MEDIUM, LARGE
    weapon_type: WeaponType    # BALLISTIC, ENERGY, MISSILE
    damage_per_shot: float
    damage_per_second: float   # For beams only
    damage_type: DamageType
    emp: float
    flux_per_shot: float
    flux_per_second: float     # For beams only
    range: float
    op_cost: int
    chargeup: float
    chargedown: float
    burst_size: int
    burst_delay: float
    ammo: int                  # 0 = unlimited
    ammo_per_sec: float
    proj_speed: float
    turn_rate: float
    hints: list[str]           # e.g. ["PD", "ANTI_FTR"]
    tags: list[str]            # e.g. ["kinetic3", "SR"]
```

**Properties (computed, not stored):**
- `sustained_dps: float` — from burst/cycle formula (see below)
- `sustained_flux: float` — flux per second during sustained fire
- `flux_efficiency: float` — `sustained_dps / sustained_flux` (inf if no flux)
- `shield_dps: float` — `sustained_dps × SHIELD_DAMAGE_MULT[damage_type]`
- `armor_dps: float` — `sustained_dps × ARMOR_DAMAGE_MULT[damage_type]`
- `is_pd: bool` — `"PD" in hints`
- `is_beam: bool` — `damage_per_second > 0 and damage_per_shot == 0`

**Sustained DPS formula:**
```python
if is_beam:
    return damage_per_second
if burst_size > 1:
    cycle_time = chargeup + (burst_size - 1) * burst_delay + chargedown
else:
    cycle_time = chargeup + chargedown
if cycle_time > 0:
    return damage_per_shot * max(burst_size, 1) / cycle_time
return 0.0
```

### HullMod

```python
@dataclass
class HullMod:
    id: str                    # e.g. "heavyarmor"
    name: str                  # e.g. "Heavy Armor"
    tier: int
    tags: list[str]            # e.g. ["defensive", "armor"]
    ui_tags: list[str]         # e.g. ["Armor"]
    cost_frigate: int
    cost_destroyer: int
    cost_cruiser: int
    cost_capital: int
    is_hidden: bool
    script: str                # Java class path
```

**Properties:**
- `is_logistics: bool` — `"logistics" in tags`
- `op_cost(hull_size: HullSize) -> int` — returns cost by hull size

### Build

```python
@dataclass(frozen=True)
class Build:
    hull_id: str
    weapon_assignments: dict[str, str | None]  # slot_id → weapon_id or None
    hullmods: frozenset[str]                    # hullmod IDs
    flux_vents: int
    flux_capacitors: int
```

Immutable (frozen). `hullmods` is `frozenset` for immutability and hashability. No `vent_fraction` — that is an optimizer-space parameter consumed by `repair_build()`.

### BuildSpec

```python
@dataclass(frozen=True)
class BuildSpec:
    """Serialization-oriented build specification for matchup queue JSON."""
    variant_id: str
    hull_id: str
    weapon_assignments: dict[str, str]  # slot_id -> weapon_id, empty slots omitted
    hullmods: tuple[str, ...]           # sorted alphabetically
    flux_vents: int
    flux_capacitors: int
```

Transfer object for the Python-Java boundary. Unlike `Build` (which uses `frozenset` for hullmods and `None` for empty weapon slots), `BuildSpec` uses a sorted `tuple` for hullmods and omits empty slots entirely — optimized for deterministic JSON serialization.

Created by `build_to_build_spec()` in `variant.py`. Embedded in `MatchupConfig.player_builds` for the matchup queue.

### MatchupConfig

```python
@dataclass(frozen=True)
class MatchupConfig:
    matchup_id: str
    player_builds: tuple[BuildSpec, ...]   # optimizer-generated build specs
    enemy_variants: tuple[str, ...]        # stock variant IDs
    time_limit_seconds: float = 300.0
    time_mult: float = 3.0
    map_width: float = 24000.0
    map_height: float = 18000.0
```

Player ships are specified as inline `BuildSpec` objects (constructed programmatically by the Java harness). Enemy ships remain as stock variant ID strings (loaded from `.variant` files at game startup).

### EffectiveStats

```python
@dataclass(frozen=True)
class EffectiveStats:
    flux_dissipation: float
    flux_capacity: float
    armor_rating: float
    hull_hitpoints: float
    shield_efficiency: float     # 0 if no shields
    shield_upkeep: float
    has_shields: bool
    max_speed: float
    weapon_range_bonus: float
    weapon_range_cap: float | None
    peak_performance_time: float
```

Computed by `compute_effective_stats()` in `hullmod_effects.py`.

### ScorerResult

```python
@dataclass(frozen=True)
class ScorerResult:
    composite_score: float
    total_dps: float
    kinetic_dps: float
    he_dps: float
    energy_dps: float
    flux_balance: float
    flux_efficiency: float
    effective_hp: float
    armor_ehp: float
    shield_ehp: float
    range_coherence: float
    damage_mix: float
    engagement_range: float
    op_efficiency: float
    effective_stats: EffectiveStats
```

### GameData

```python
@dataclass
class GameData:
    hulls: dict[str, ShipHull]     # keyed by hull id
    weapons: dict[str, Weapon]     # keyed by weapon id
    hullmods: dict[str, HullMod]   # keyed by hullmod id
```
