"""Core data models for the Starsector Ship Build Optimizer.

Defines all dataclasses, enums, and computed properties used across modules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# --- Game constants (single source of truth) ---

MAX_VENTS: dict[str, int] = {
    "FRIGATE": 10,
    "DESTROYER": 20,
    "CRUISER": 30,
    "CAPITAL_SHIP": 50,
}

MAX_CAPACITORS: dict[str, int] = MAX_VENTS

FLUX_PER_CAPACITOR: int = 200
DISSIPATION_PER_VENT: int = 10

SHIELD_DAMAGE_MULT: dict[str, float] = {
    "KINETIC": 2.0,
    "HIGH_EXPLOSIVE": 0.5,
    "ENERGY": 1.0,
    "FRAGMENTATION": 0.25,
}

ARMOR_DAMAGE_MULT: dict[str, float] = {
    "KINETIC": 0.5,
    "HIGH_EXPLOSIVE": 2.0,
    "ENERGY": 1.0,
    "FRAGMENTATION": 0.25,
}

MAX_LOGISTICS_HULLMODS: int = 2


# --- Enums ---


class _ParseableEnum(StrEnum):
    """Base enum with forward-compatible from_str parsing."""

    @classmethod
    def from_str(cls, value: str) -> _ParseableEnum | None:
        """Parse string to enum member. Returns None for unknown values."""
        # Try direct match first
        for member in cls:
            if member.value == value:
                return member
        # Try case-insensitive match
        normalized = value.upper().replace(" ", "_")
        for member in cls:
            if member.value == normalized:
                return member
        return None


class HullSize(_ParseableEnum):
    FRIGATE = "FRIGATE"
    DESTROYER = "DESTROYER"
    CRUISER = "CRUISER"
    CAPITAL_SHIP = "CAPITAL_SHIP"


class SlotType(_ParseableEnum):
    BALLISTIC = "BALLISTIC"
    ENERGY = "ENERGY"
    MISSILE = "MISSILE"
    HYBRID = "HYBRID"
    COMPOSITE = "COMPOSITE"
    SYNERGY = "SYNERGY"
    UNIVERSAL = "UNIVERSAL"
    # Non-assignable slot types (player can't change these)
    BUILT_IN = "BUILT_IN"
    DECORATIVE = "DECORATIVE"
    LAUNCH_BAY = "LAUNCH_BAY"
    STATION_MODULE = "STATION_MODULE"
    SYSTEM = "SYSTEM"


class SlotSize(_ParseableEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


class MountType(_ParseableEnum):
    TURRET = "TURRET"
    HARDPOINT = "HARDPOINT"
    HIDDEN = "HIDDEN"  # Non-assignable (built-in weapons only)


class ShieldType(_ParseableEnum):
    NONE = "NONE"
    OMNI = "OMNI"
    FRONT = "FRONT"
    PHASE = "PHASE"


class DamageType(_ParseableEnum):
    KINETIC = "KINETIC"
    HIGH_EXPLOSIVE = "HIGH_EXPLOSIVE"
    ENERGY = "ENERGY"
    FRAGMENTATION = "FRAGMENTATION"


class WeaponType(_ParseableEnum):
    BALLISTIC = "BALLISTIC"
    ENERGY = "ENERGY"
    MISSILE = "MISSILE"


# --- Dataclasses ---


@dataclass(frozen=True)
class WeaponSlot:
    id: str
    slot_type: SlotType
    slot_size: SlotSize
    mount_type: MountType
    angle: float
    arc: float
    position: tuple[float, float]


@dataclass
class ShipHull:
    id: str
    name: str
    hull_size: HullSize
    designation: str
    tech_manufacturer: str
    system_id: str
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
    shield_upkeep: float
    shield_efficiency: float
    phase_cost: float
    phase_upkeep: float
    peak_cr_sec: float
    cr_loss_per_sec: float
    weapon_slots: list[WeaponSlot] = field(default_factory=list)
    built_in_mods: list[str] = field(default_factory=list)
    built_in_weapons: dict[str, str] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def max_vents(self) -> int:
        return MAX_VENTS[self.hull_size.value]

    @property
    def max_capacitors(self) -> int:
        return MAX_CAPACITORS[self.hull_size.value]


@dataclass
class Weapon:
    id: str
    name: str
    size: SlotSize
    weapon_type: WeaponType
    damage_per_shot: float
    damage_per_second: float
    damage_type: DamageType
    emp: float
    flux_per_shot: float
    flux_per_second: float
    range: float
    op_cost: int
    chargeup: float
    chargedown: float
    burst_size: int
    burst_delay: float
    ammo: int
    ammo_per_sec: float
    proj_speed: float
    turn_rate: float
    hints: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def is_beam(self) -> bool:
        return self.damage_per_second > 0 and self.damage_per_shot == 0

    @property
    def is_pd(self) -> bool:
        return "PD" in self.hints

    @property
    def sustained_dps(self) -> float:
        if self.is_beam:
            return self.damage_per_second
        if self.burst_size > 1:
            cycle_time = (self.chargeup
                          + (self.burst_size - 1) * self.burst_delay
                          + self.chargedown)
        else:
            cycle_time = self.chargeup + self.chargedown
        if cycle_time > 0:
            return self.damage_per_shot * max(self.burst_size, 1) / cycle_time
        return 0.0

    @property
    def sustained_flux(self) -> float:
        if self.is_beam:
            return self.flux_per_second
        if self.burst_size > 1:
            cycle_time = (self.chargeup
                          + (self.burst_size - 1) * self.burst_delay
                          + self.chargedown)
        else:
            cycle_time = self.chargeup + self.chargedown
        if cycle_time > 0:
            return self.flux_per_shot * max(self.burst_size, 1) / cycle_time
        return 0.0

    @property
    def flux_efficiency(self) -> float:
        flux = self.sustained_flux
        if flux <= 0:
            return float("inf")
        return self.sustained_dps / flux

    @property
    def shield_dps(self) -> float:
        return self.sustained_dps * SHIELD_DAMAGE_MULT[self.damage_type.value]

    @property
    def armor_dps(self) -> float:
        return self.sustained_dps * ARMOR_DAMAGE_MULT[self.damage_type.value]


@dataclass
class HullMod:
    id: str
    name: str
    tier: int
    tags: list[str]
    ui_tags: list[str]
    cost_frigate: int
    cost_destroyer: int
    cost_cruiser: int
    cost_capital: int
    is_hidden: bool
    script: str

    @property
    def is_logistics(self) -> bool:
        return "Logistics" in self.ui_tags or "logistics" in self.tags

    def op_cost(self, hull_size: HullSize) -> int:
        costs = {
            HullSize.FRIGATE: self.cost_frigate,
            HullSize.DESTROYER: self.cost_destroyer,
            HullSize.CRUISER: self.cost_cruiser,
            HullSize.CAPITAL_SHIP: self.cost_capital,
        }
        return costs[hull_size]


@dataclass(frozen=True)
class Build:
    hull_id: str
    weapon_assignments: dict[str, str | None]
    hullmods: frozenset[str]
    flux_vents: int
    flux_capacitors: int


@dataclass(frozen=True)
class EffectiveStats:
    flux_dissipation: float
    flux_capacity: float
    armor_rating: float
    hull_hitpoints: float
    shield_efficiency: float
    shield_upkeep: float
    has_shields: bool
    max_speed: float
    weapon_range_bonus: float
    weapon_range_threshold: float | None   # SO: ranges above this are compressed
    weapon_range_compression: float        # SO: multiplier for range above threshold
    peak_performance_time: float


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


@dataclass
class GameData:
    hulls: dict[str, ShipHull]
    weapons: dict[str, Weapon]
    hullmods: dict[str, HullMod]


# --- Phase 2: Combat protocol dataclasses ---


@dataclass(frozen=True)
class DamageBreakdown:
    """Damage breakdown by target layer (shield/armor/hull/emp)."""
    shield: float = 0.0
    armor: float = 0.0
    hull: float = 0.0
    emp: float = 0.0


@dataclass(frozen=True)
class ShipCombatResult:
    """Per-ship combat result from a single matchup."""
    fleet_member_id: str
    variant_id: str
    hull_id: str
    destroyed: bool
    hull_fraction: float
    armor_fraction: float
    cr_remaining: float
    peak_time_remaining: float
    disabled_weapons: int
    flameouts: int
    damage_dealt: DamageBreakdown
    damage_taken: DamageBreakdown
    overload_count: int


@dataclass(frozen=True)
class CombatResult:
    """Full result from a single combat matchup."""
    matchup_id: str
    winner: str  # "PLAYER", "ENEMY", or "TIMEOUT"
    duration_seconds: float
    player_ships: tuple[ShipCombatResult, ...]
    enemy_ships: tuple[ShipCombatResult, ...]
    player_ships_destroyed: int
    enemy_ships_destroyed: int
    player_ships_retreated: int
    enemy_ships_retreated: int


@dataclass(frozen=True)
class MatchupConfig:
    """Configuration for a single combat matchup. Used within a matchup queue."""
    matchup_id: str
    player_variants: tuple[str, ...]
    enemy_variants: tuple[str, ...]
    time_limit_seconds: float = 300.0
    time_mult: float = 3.0
    map_width: float = 24000.0
    map_height: float = 18000.0
