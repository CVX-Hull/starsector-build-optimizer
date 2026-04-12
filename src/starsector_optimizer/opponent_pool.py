"""Opponent pool — diverse opponent sets per hull size for robust fitness evaluation.

Starsector has strong RPS dynamics (kinetic vs shields, HE vs armor). A diverse
opponent pool produces robust builds that don't exploit one weakness.

The opponent pool is discovered dynamically from all stock variants in the game
data directory, providing a natural difficulty gradient without hand-curation.

See spec 23 for design rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .models import BuildSpec, CombatResult, GameData, HullSize, MatchupConfig, ShipHull

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpponentPool:
    """Maps hull sizes to stock variant IDs for opponent evaluation."""

    pools: dict[HullSize, tuple[str, ...]]


# Hints that indicate a hull cannot function as a standalone 1v1 opponent.
# STATION: orbital stations/battlestations (stationary, composed of modules)
# HIDE_IN_CODEX: station modules, Ziggurat shards, hidden entities
# MODULE: station sub-modules
_NON_OPPONENT_HINTS = frozenset({"STATION", "HIDE_IN_CODEX", "MODULE"})

# Tags for special entities with non-standard AI that don't produce meaningful
# combat signal in 1v1 evaluation.
# threat: Remnant threat units (skirmish/assault/standoff/hive/fabricator/overseer)
# dweller: Shrouded entities (tendril/eye/maelstrom/maw/vortex/ejecta)
_NON_OPPONENT_TAGS = frozenset({"threat", "dweller"})


def _is_valid_opponent(hull: ShipHull) -> bool:
    """Check if a hull can function as a standalone 1v1 opponent.

    Excludes:
    - Fighters/drones (fleet_pts == 0): require carriers, can't fight alone
    - Stations and modules (STATION/HIDE_IN_CODEX/MODULE hints): not standalone ships
    - Threat units and shrouded entities (threat/dweller tags): non-standard AI
    """
    if hull.fleet_pts <= 0:
        return False
    if _NON_OPPONENT_HINTS.intersection(hull.hints):
        return False
    if _NON_OPPONENT_TAGS.intersection(hull.tags):
        return False
    return True


def discover_opponent_pool(game_dir: Path, game_data: GameData) -> OpponentPool:
    """Build opponent pool from all stock variants in game data.

    Discovers every .variant file under game_dir/data/variants/, groups by hull
    size, and returns an OpponentPool covering all hull sizes found. Filters out
    fighters, drones, and station modules that cannot function as standalone
    opponents. Warns if any hull size has fewer than 2 opponents.
    """
    from .variant import discover_stock_variant_ids

    variant_ids = discover_stock_variant_ids(game_dir)
    pools: dict[HullSize, list[str]] = {size: [] for size in HullSize}

    for variant_id, hull_id in variant_ids:
        hull = game_data.hulls.get(hull_id)
        if hull is None:
            continue  # Unknown hull — modded or special content
        if not _is_valid_opponent(hull):
            continue
        pools[hull.hull_size].append(variant_id)

    for size, variants in pools.items():
        if 0 < len(variants) < 2:
            logger.warning(
                "Hull size %s has only %d opponent variant(s) — "
                "pruning requires at least 2 opponents for meaningful comparison",
                size.name, len(variants),
            )

    return OpponentPool(pools={
        size: tuple(sorted(variants))
        for size, variants in pools.items()
        if variants
    })


def get_opponents(pool: OpponentPool, hull_size: HullSize) -> tuple[str, ...]:
    """Return opponent variant IDs for a hull size. Raises KeyError if not found."""
    return pool.pools[hull_size]


def generate_matchups(
    player_build: BuildSpec,
    opponents: tuple[str, ...],
    matchup_id_prefix: str,
    time_limit_seconds: float = 300.0,
    time_mult: float = 5.0,
) -> list[MatchupConfig]:
    """Create one MatchupConfig per opponent."""
    return [
        MatchupConfig(
            matchup_id=f"{matchup_id_prefix}_vs_{opp}",
            player_builds=(player_build,),
            enemy_variants=(opp,),
            time_limit_seconds=time_limit_seconds,
            time_mult=time_mult,
        )
        for opp in opponents
    ]


def hp_differential(result: CombatResult) -> float:
    """Compute normalized HP differential from a single combat result.

    Returns mean(player hull_fractions) - mean(enemy hull_fractions).
    Range: -1.0 (total loss) to +1.0 (total victory).
    Returns 0.0 if either side has no ships.
    """
    if not result.player_ships or not result.enemy_ships:
        return 0.0
    player_hp = sum(s.hull_fraction for s in result.player_ships) / len(result.player_ships)
    enemy_hp = sum(s.hull_fraction for s in result.enemy_ships) / len(result.enemy_ships)
    return player_hp - enemy_hp


def compute_fitness(results: list[CombatResult], mode: str = "mean") -> float:
    """Aggregate HP differentials across opponent matchups.

    Args:
        results: Combat results from opponent pool evaluation.
        mode: "mean" for average, "minimax" for minimum.

    Raises:
        ValueError: If results is empty.
    """
    if not results:
        raise ValueError("Cannot compute fitness from empty results")

    diffs = [hp_differential(r) for r in results]
    if mode == "minimax":
        return min(diffs)
    return sum(diffs) / len(diffs)
