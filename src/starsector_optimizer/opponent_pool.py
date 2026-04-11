"""Opponent pool — diverse opponent sets per hull size for robust fitness evaluation.

Starsector has strong RPS dynamics (kinetic vs shields, HE vs armor). A diverse
opponent pool produces robust builds that don't exploit one weakness.

See spec 23 for design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import BuildSpec, CombatResult, HullSize, MatchupConfig


@dataclass(frozen=True)
class OpponentPool:
    """Maps hull sizes to stock variant IDs for opponent evaluation."""

    pools: dict[HullSize, tuple[str, ...]]


# Variant IDs match filenames in data/variants/ (without .variant extension)
DEFAULT_OPPONENT_POOL = OpponentPool(
    pools={
        HullSize.FRIGATE: (
            "wolf_Assault",
            "lasher_Assault",
            "hyperion_Attack",
            "shade_Assault",
        ),
        HullSize.DESTROYER: (
            "hammerhead_Elite",
            "medusa_Attack",
            "enforcer_Assault",
            "sunder_Assault",
        ),
        HullSize.CRUISER: (
            "dominator_Assault",
            "dominator_XIV_Elite",
            "aurora_Assault",
            "doom_Strike",
            "eagle_Assault",
        ),
        HullSize.CAPITAL_SHIP: (
            "onslaught_Standard",
            "onslaught_xiv_Elite",
            "legion_xiv_Elite",
            "astral_Elite",
            "conquest_Elite",
        ),
    }
)


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
