"""Core data models for the Starsector Ship Build Optimizer.

Defines all dataclasses, enums, and computed properties used across modules.
"""

from __future__ import annotations

import logging
import dataclasses
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
class BuildSpec:
    """Build specification for matchup queue serialization. Transfer object."""
    variant_id: str
    hull_id: str
    weapon_assignments: dict[str, str]
    hullmods: tuple[str, ...]
    flux_vents: int
    flux_capacitors: int
    cr: float = 0.7  # Combat readiness at deployment (0.0–1.0)


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
class EngineStats:
    """Java-engine-computed effective stats read at end of SETUP.

    Populated by `CombatHarnessPlugin.doSetup()` via `MutableShipStats` accessors
    after hullmod effects are applied. Used by Phase 5D EB shrinkage to regress
    TWFE α̂ on authoritative engine values rather than Python recomputed ones.
    """
    eff_max_flux: float
    eff_flux_dissipation: float
    eff_armor_rating: float


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
    engine_stats: EngineStats | None = None


@dataclass(frozen=True)
class MatchupConfig:
    """Configuration for a single combat matchup. Used within a matchup queue."""
    matchup_id: str
    player_builds: tuple[BuildSpec, ...]
    enemy_variants: tuple[str, ...]
    time_limit_seconds: float = 300.0
    time_mult: float = 3.0
    map_width: float = 24000.0
    map_height: float = 18000.0


@dataclass(frozen=True)
class Heartbeat:
    """Parsed heartbeat from the combat harness (6-field enriched format)."""
    timestamp_ms: int
    elapsed: float
    player_hp: float
    enemy_hp: float
    player_alive: int
    enemy_alive: int


@dataclass(frozen=True)
class CombatFitnessConfig:
    """Tunable coefficients for the hierarchical combat fitness function.

    Tier ranges (at defaults): wins [1.0, 1.5], timeouts [-0.49, +0.49],
    losses [-1.0, -0.5], no engagement = -2.0.
    Invariant: win_base > timeout_scale > -(loss_base + loss_bonus_scale) > no_engagement_score.
    """
    win_base: float = 1.0
    loss_base: float = -1.0
    win_bonus_scale: float = 0.5
    loss_bonus_scale: float = 0.5
    timeout_scale: float = 0.49
    no_engagement_score: float = -2.0
    engagement_threshold: float = 500.0


@dataclass(frozen=True)
class TWFEConfig:
    """Two-Way Fixed Effects deconfounding + opponent selection parameters.

    Controls the additive decomposition score_ij = α_i + β_j + ε_ij that
    separates build quality (α) from opponent difficulty (β). Also configures
    anchor-first opponent ordering and incumbent overlap for comparability.
    See spec 28 for algorithm details.
    """
    ridge: float = 0.01
    n_iters: int = 20
    trim_worst: int = 2
    n_incumbent_overlap: int = 5
    n_anchors: int = 3
    anchor_burn_in: int = 30
    min_disc_samples: int = 5


@dataclass(frozen=True)
class EBShrinkageConfig:
    """Empirical-Bayes shrinkage parameters for the Phase 5D A2′ stage.

    Controls fusion of TWFE α̂ with a 7-covariate regression prior:
        α̂_EB_i = w_i · α̂_i + (1 − w_i) · γ̂ᵀ[1, X_i],  w_i = τ̂² / (τ̂² + σ̂_i²)
    See spec 28 §EB Shrinkage (A2′).
    """
    tau2_floor_frac: float = 0.05
    triple_goal: bool = True
    eb_min_builds: int = 8
    ols_ridge: float = 1e-4


@dataclass(frozen=True)
class ShapeConfig:
    """Phase 5E A3 Box-Cox output-warping parameters.

    `min_samples` is the floor below which the A3 step falls through to
    min-max scaling (Box-Cox MLE destabilises under ~8 samples; chosen by
    analogy to `EBShrinkageConfig.eb_min_builds`).
    `positivise_epsilon` guards the `shift = min - eps` positivise step
    and the constant-population `ptp < eps` fallback.
    See spec 24 §A3 Box-Cox Output Warping.
    """
    min_samples: int = 8
    positivise_epsilon: float = 1e-6


@dataclass(frozen=True)
class RegimeConfig:
    """Phase 5F loadout regime — CMDP feasibility alignment of the search space.

    Hard-masks hullmods and weapons at `search_space.py` construction time
    so Optuna optimizes over a component set the target user can actually
    field on their save. Hull choice is orthogonal and controlled by the
    caller (e.g. `--hull`); opponents stay drawn from the full hull-size-
    matched pool (open-world framing — any build can face any opponent).
    Four presets below; see docs/reference/phase5f-regime-segmented-optimization.md.
    """
    name: str                             # "early" | "mid" | "late" | "endgame"
    max_hullmod_tier: int                 # inclusive ceiling on HullMod.tier; 3 = no filter
    exclude_hullmod_tags: frozenset[str]
    exclude_weapon_tags: frozenset[str]


REGIME_EARLY = RegimeConfig(
    name="early",
    max_hullmod_tier=1,
    exclude_hullmod_tags=frozenset({"no_drop", "no_drop_salvage", "codex_unlockable"}),
    exclude_weapon_tags=frozenset({"rare_bp", "codex_unlockable"}),
)

REGIME_MID = RegimeConfig(
    name="mid",
    max_hullmod_tier=3,
    exclude_hullmod_tags=frozenset({"no_drop", "no_drop_salvage"}),
    exclude_weapon_tags=frozenset({"rare_bp"}),
)

REGIME_LATE = RegimeConfig(
    name="late",
    max_hullmod_tier=3,
    exclude_hullmod_tags=frozenset({"no_drop"}),
    exclude_weapon_tags=frozenset(),
)

REGIME_ENDGAME = RegimeConfig(
    name="endgame",
    max_hullmod_tier=3,
    exclude_hullmod_tags=frozenset(),
    exclude_weapon_tags=frozenset(),
)

REGIME_PRESETS: dict[str, RegimeConfig] = {
    "early": REGIME_EARLY,
    "mid": REGIME_MID,
    "late": REGIME_LATE,
    "endgame": REGIME_ENDGAME,
}


# ---- Phase 6: Cloud Worker Federation ----------------------------------------


@dataclass(frozen=True)
class StudyConfig:
    """One row in the campaign YAML's studies: list.

    A StudyConfig with seeds=(0, 1, 2) fans out into three Optuna studies
    (one subprocess each) sharing hull/regime/sampler/budget settings.
    See docs/specs/22-cloud-deployment.md.
    """
    hull: str
    regime: str
    seeds: tuple[int, ...]
    budget_per_study: int
    workers_per_study: int
    sampler: str


@dataclass(frozen=True)
class GlobalAutoStopConfig:
    """Mirrors the YAML global_auto_stop: nested block."""
    on_budget: str = "hard"              # "hard" | "soft"
    on_plateau: bool = True


@dataclass(frozen=True)
class CampaignConfig:
    """Top-level campaign descriptor loaded from YAML.

    Immutable after `load_campaign_config`. __repr__ redacts the Tailscale
    auth key so accidental logging does not leak it. Never pickled across
    subprocesses; child processes re-parse the YAML path + pick up secrets
    via env vars. See docs/specs/22-cloud-deployment.md.
    """
    name: str
    budget_usd: float
    provider: str                                   # "aws"
    regions: tuple[str, ...]
    instance_types: tuple[str, ...]
    spot_allocation_strategy: str                   # "price-capacity-optimized"
    capacity_rebalancing: bool
    max_concurrent_workers: int
    min_workers_to_start: int
    partial_fleet_policy: str                       # "proceed_half_speed" | "abort"
    ami_ids_by_region: dict[str, str]
    ssh_key_name: str
    tailscale_authkey_secret: str
    studies: tuple[StudyConfig, ...]
    global_auto_stop: GlobalAutoStopConfig = field(default_factory=GlobalAutoStopConfig)
    max_lifetime_hours: float = 6.0
    visibility_timeout_seconds: float = 120.0
    janitor_interval_seconds: float = 60.0
    worker_poll_margin_seconds: float = 5.0
    fleet_provision_timeout_seconds: float = 600.0
    result_timeout_seconds: float = 900.0
    ledger_heartbeat_interval_seconds: float = 60.0
    ledger_warn_thresholds: tuple[float, ...] = (0.5, 0.8, 0.95)
    base_flask_port: int = 9000
    teardown_retry_delay_seconds: float = 10.0
    teardown_thread_join_seconds: float = 5.0
    redis_port: int = 6379
    redis_preflight_timeout_seconds: float = 2.0
    matchup_slots_per_worker: int = 2
    # Flask port ceiling per study index. Matches the ACL range
    # `tcp:9000-9099` documented in .claude/skills/cloud-worker-ops.md —
    # single source of truth for the port budget per study.
    flask_ports_per_study: int = 100
    # Orchestrator-side path to the Starsector install. Study subprocesses
    # load game data here for constraint-aware sampling + opponent-pool
    # construction (they never run the JVM; workers do). Default matches
    # the workstation convention `game/starsector` used by local runs.
    game_dir: str = "game/starsector"

    def __repr__(self) -> str:
        fields = []
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if f.name == "tailscale_authkey_secret":
                value = "***REDACTED***"
            fields.append(f"{f.name}={value!r}")
        return f"CampaignConfig({', '.join(fields)})"


@dataclass(frozen=True)
class WorkerConfig:
    """Per-worker config injected by cloud-init env vars at VM boot.

    Immutable; the worker reads once at startup and never re-reads. __repr__
    redacts bearer_token. Never serialized into the cost ledger or study DB.

    `worker_id` is last because its default is a placeholder: render-time
    emits worker_id="" into /etc/starsector-worker.env, and the cloud-init
    script overwrites it via IMDSv2 before `systemctl start`. The required
    fields come first so dataclass positional ordering holds.
    """
    campaign_id: str
    study_id: str
    project_tag: str              # scopes Redis queue + heartbeat keys
    redis_host: str
    redis_port: int
    http_endpoint: str
    bearer_token: str
    max_lifetime_hours: float = 6.0
    http_retry_count: int = 3
    http_retry_base_seconds: float = 1.0
    http_retry_max_seconds: float = 30.0
    http_retry_backoff_multiplier: float = 2.0
    http_post_timeout_seconds: float = 30.0
    worker_poll_margin_seconds: float = 5.0
    matchup_slots_per_worker: int = 2
    worker_id: str = ""           # placeholder; IMDSv2 override wins at VM boot

    def __repr__(self) -> str:
        fields = []
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if f.name == "bearer_token":
                value = "***REDACTED***"
            fields.append(f"{f.name}={value!r}")
        return f"WorkerConfig({', '.join(fields)})"


@dataclass(frozen=True)
class CostLedgerEntry:
    """One JSONL row in ~/starsector-campaigns/<name>/ledger.jsonl.

    All fields primitive and secret-free. timestamp is ISO-8601 UTC.
    """
    timestamp: str
    event_type: str                                 # "worker_heartbeat" | "worker_terminated" | "campaign_end"
    worker_id: str
    region: str
    instance_type: str
    hours_elapsed: float
    delta_usd: float
    cumulative_usd: float


# ---- End Phase 6 -------------------------------------------------------------


@dataclass(frozen=True)
class ImportanceResult:
    """Parameter importance analysis result from fANOVA."""
    importances: dict[str, float]  # param_name -> importance (0.0–1.0, sums to ~1.0)
