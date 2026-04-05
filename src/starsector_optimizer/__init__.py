"""Starsector Ship Build Optimizer."""

from .models import (
    Build,
    CombatResult,
    DamageBreakdown,
    DamageType,
    EffectiveStats,
    GameData,
    HullMod,
    HullSize,
    MatchupConfig,
    MountType,
    ScorerResult,
    ShieldType,
    ShipCombatResult,
    ShipHull,
    SlotSize,
    SlotType,
    Weapon,
    WeaponSlot,
    WeaponType,
)
from .hullmod_effects import compute_effective_stats, HULLMOD_EFFECTS
from .parser import load_game_data
from .search_space import build_search_space, SearchSpace
from .repair import repair_build, is_feasible
from .scorer import heuristic_score
from .variant import generate_variant, write_variant_file, load_variant_file
from .calibration import generate_diverse_builds, compute_build_features
from .result_parser import parse_combat_result, parse_results_file, write_queue_file
from .instance_manager import InstanceConfig, InstancePool
from .curtailment import CurtailmentMonitor, parse_heartbeat
from .timeout_tuner import TimeoutTuner
from .opponent_pool import (
    OpponentPool, DEFAULT_OPPONENT_POOL, get_opponents,
    generate_matchups, compute_fitness, hp_differential,
)
from .optimizer import (
    OptimizerConfig, BuildCache, optimize_hull, evaluate_build, warm_start,
)

__all__ = [
    "Build", "CombatResult", "DamageBreakdown", "DamageType", "EffectiveStats",
    "GameData", "HullMod", "HullSize", "MatchupConfig", "MountType", "ScorerResult",
    "ShieldType", "ShipCombatResult", "ShipHull", "SlotSize", "SlotType",
    "Weapon", "WeaponSlot", "WeaponType",
    "compute_effective_stats", "HULLMOD_EFFECTS",
    "load_game_data",
    "build_search_space", "SearchSpace",
    "repair_build", "is_feasible",
    "heuristic_score",
    "generate_variant", "write_variant_file", "load_variant_file",
    "generate_diverse_builds", "compute_build_features",
    "parse_combat_result", "parse_results_file", "write_queue_file",
    "InstanceConfig", "InstancePool",
    "CurtailmentMonitor", "parse_heartbeat",
    "TimeoutTuner",
    "OpponentPool", "DEFAULT_OPPONENT_POOL", "get_opponents",
    "generate_matchups", "compute_fitness", "hp_differential",
    "OptimizerConfig", "BuildCache", "optimize_hull", "evaluate_build", "warm_start",
]
