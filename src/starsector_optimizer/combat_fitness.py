"""Combat fitness — hierarchical composite score using full combat telemetry.

Replaces simple HP differential for optimization. Provides continuous gradient
even for timeout stalemates by analyzing damage exchange quality.

See spec 25 for design rationale and tier structure.
"""

from __future__ import annotations

from .models import CombatResult


def _engagement_score(
    result: CombatResult,
    threshold: float,
) -> float:
    """Score damage exchange quality. Range [-0.5, +0.5].

    Returns -0.5 (active penalty) if total permanent damage < threshold.
    Shield damage weighted at 0.3x (recoverable). Armor + hull weighted 1.0x (permanent).
    """
    player_dmg = sum(
        s.damage_dealt.armor + s.damage_dealt.hull + s.damage_dealt.shield * 0.3
        for s in result.player_ships
    )
    enemy_dmg = sum(
        s.damage_dealt.armor + s.damage_dealt.hull + s.damage_dealt.shield * 0.3
        for s in result.enemy_ships
    )
    total = player_dmg + enemy_dmg

    if total < threshold:
        return -0.5  # Ships never meaningfully engaged

    ratio = (player_dmg - enemy_dmg) / total  # [-1, +1]
    return ratio * 0.5  # Scale to [-0.5, +0.5]


def _efficiency_bonus(result: CombatResult, time_limit: float) -> float:
    """Small bonus for HOW you win. Range [0.0, 0.1]. Only for wins."""
    if result.winner != "PLAYER":
        return 0.0

    # Faster kill
    speed = max(0.0, 1.0 - result.duration_seconds / time_limit) * 0.04

    # HP preserved
    hp = (
        sum(s.hull_fraction for s in result.player_ships) / len(result.player_ships)
        if result.player_ships
        else 0.0
    )
    hp_bonus = hp * 0.03

    # Low overloads (good flux management)
    overloads = sum(s.overload_count for s in result.player_ships)
    flux_bonus = max(0.0, 0.02 - overloads * 0.005)

    # Armor preserved
    armor = (
        sum(s.armor_fraction for s in result.player_ships) / len(result.player_ships)
        if result.player_ships
        else 0.0
    )
    armor_bonus = armor * 0.01

    return speed + hp_bonus + flux_bonus + armor_bonus


def combat_fitness(
    result: CombatResult,
    time_limit: float = 180.0,
    engagement_threshold: float = 500.0,
) -> float:
    """Hierarchical composite fitness for a single combat matchup.

    Tier 1: Outcome (dominant ranges)
      - PLAYER win: [1.0, 1.1]
      - TIMEOUT/STOPPED: [-0.5, +0.5]
      - ENEMY win: [-1.0, -0.85]

    Tier 2: Engagement quality (adjusts within tier)
    Tier 3: Efficiency bonus (small, wins only)
    """
    if result.winner == "PLAYER":
        return 1.0 + _efficiency_bonus(result, time_limit)

    if result.winner == "ENEMY":
        eng = _engagement_score(result, engagement_threshold)
        return -1.0 + (eng + 0.5) * 0.3  # [-1.0, -0.85]

    # TIMEOUT or STOPPED
    return _engagement_score(result, engagement_threshold)


def aggregate_combat_fitness(
    results: list[CombatResult],
    mode: str = "mean",
    time_limit: float = 180.0,
    engagement_threshold: float = 500.0,
) -> float:
    """Aggregate combat fitness across multiple matchups.

    Args:
        results: Combat results from opponent pool evaluation.
        mode: "mean" (average) or "minimax" (minimum).

    Raises:
        ValueError: If results is empty.
    """
    if not results:
        raise ValueError("Cannot compute fitness from empty results")

    scores = [
        combat_fitness(r, time_limit=time_limit, engagement_threshold=engagement_threshold)
        for r in results
    ]
    if mode == "minimax":
        return min(scores)
    return sum(scores) / len(scores)
