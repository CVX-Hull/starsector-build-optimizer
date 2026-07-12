"""Starsector Ship Build Optimizer."""

from .models import (
    Build,
    BuildSpec,
    CombatResult,
    DamageBreakdown,
    DamageType,
    GameData,
    Heartbeat,
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
from .game_manifest import GameManifest
from .parser import load_game_data
from .search_space import build_search_space, SearchSpace
from .repair import repair_build, is_feasible
from .scorer import heuristic_score
from .variant import build_to_build_spec, generate_variant, write_variant_file, load_variant_file, variant_to_build, load_stock_builds, discover_stock_variant_ids
from .calibration import generate_diverse_builds, compute_build_features
from .result_parser import parse_combat_result, parse_results_file, write_queue_file
from .instance_manager import InstanceConfig, LocalInstancePool
from .combat_fitness import combat_fitness, aggregate_combat_fitness
from .opponent_pool import (
    OpponentPool, discover_opponent_pool, get_opponents,
    generate_matchups, compute_fitness, hp_differential,
)
from .optimizer import (
    OptimizerConfig, BuildCache, StagedEvaluator, optimize_hull, warm_start,
    preflight_check, validate_build_spec,
)

__all__ = [
    "Build",
    "BuildCache",
    "BuildSpec",
    "CombatResult",
    "DamageBreakdown",
    "DamageType",
    "GameData",
    "GameManifest",
    "Heartbeat",
    "HullMod",
    "HullSize",
    "InstanceConfig",
    "LocalInstancePool",
    "MatchupConfig",
    "MountType",
    "OpponentPool",
    "OptimizerConfig",
    "ScorerResult",
    "SearchSpace",
    "ShieldType",
    "ShipCombatResult",
    "ShipHull",
    "SlotSize",
    "SlotType",
    "StagedEvaluator",
    "Weapon",
    "WeaponSlot",
    "WeaponType",
    "aggregate_combat_fitness",
    "build_search_space",
    "build_to_build_spec",
    "combat_fitness",
    "compute_build_features",
    "compute_fitness",
    "discover_opponent_pool",
    "discover_stock_variant_ids",
    "generate_diverse_builds",
    "generate_matchups",
    "generate_variant",
    "get_opponents",
    "heuristic_score",
    "hp_differential",
    "is_feasible",
    "load_game_data",
    "load_stock_builds",
    "load_variant_file",
    "optimize_hull",
    "parse_combat_result",
    "parse_results_file",
    "preflight_check",
    "repair_build",
    "validate_build_spec",
    "variant_to_build",
    "warm_start",
    "write_queue_file",
    "write_variant_file",
]
