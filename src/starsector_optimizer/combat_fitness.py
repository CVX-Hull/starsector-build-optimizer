"""Combat fitness — hierarchical composite score using full combat telemetry.

Replaces simple HP differential for optimization. Provides continuous gradient
even for timeout stalemates by analyzing damage exchange quality.

See spec 25 for design rationale and tier structure.
"""

from __future__ import annotations

from .models import CombatFitnessConfig, CombatResult

_DEFAULT_CONFIG = CombatFitnessConfig()


def _engagement_score(
    result: CombatResult,
    config: CombatFitnessConfig,
) -> float:
    """Score damage exchange quality. Range [-scale, +scale].

    Returns engagement_penalty if total permanent damage < threshold.
    Shield damage weighted at shield_damage_weight (recoverable).
    Armor + hull weighted 1.0x (permanent).
    """
    player_dmg = sum(
        s.damage_dealt.armor + s.damage_dealt.hull
        + s.damage_dealt.shield * config.shield_damage_weight
        for s in result.player_ships
    )
    enemy_dmg = sum(
        s.damage_dealt.armor + s.damage_dealt.hull
        + s.damage_dealt.shield * config.shield_damage_weight
        for s in result.enemy_ships
    )
    total = player_dmg + enemy_dmg

    if total < config.engagement_threshold:
        return config.engagement_penalty  # Ships never meaningfully engaged

    ratio = (player_dmg - enemy_dmg) / total  # [-1, +1]
    return ratio * config.engagement_scale


def _efficiency_bonus(result: CombatResult, config: CombatFitnessConfig) -> float:
    """Small bonus for HOW you win. Range [0.0, 0.1]. Only for wins."""
    if result.winner != "PLAYER":
        return 0.0

    # Faster kill
    speed = max(0.0, 1.0 - result.duration_seconds / config.time_limit) * config.speed_bonus_weight

    # HP preserved
    hp = (
        sum(s.hull_fraction for s in result.player_ships) / len(result.player_ships)
        if result.player_ships
        else 0.0
    )
    hp_bonus = hp * config.hp_bonus_weight

    # Low overloads (good flux management)
    overloads = sum(s.overload_count for s in result.player_ships)
    flux_bonus = max(0.0, config.overload_bonus_base - overloads * config.overload_penalty_per)

    # Armor preserved
    armor = (
        sum(s.armor_fraction for s in result.player_ships) / len(result.player_ships)
        if result.player_ships
        else 0.0
    )
    armor_bonus = armor * config.armor_bonus_weight

    return speed + hp_bonus + flux_bonus + armor_bonus


def combat_fitness(
    result: CombatResult,
    config: CombatFitnessConfig = _DEFAULT_CONFIG,
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
        return 1.0 + _efficiency_bonus(result, config)

    if result.winner == "ENEMY":
        eng = _engagement_score(result, config)
        return -1.0 + (eng + config.engagement_scale) * config.loss_engagement_scale

    # TIMEOUT or STOPPED
    return _engagement_score(result, config)


def aggregate_combat_fitness(
    results: list[CombatResult],
    mode: str = "mean",
    config: CombatFitnessConfig = _DEFAULT_CONFIG,
) -> float:
    """Aggregate combat fitness across multiple matchups.

    Args:
        results: Combat results from opponent pool evaluation.
        mode: "mean" (average) or "minimax" (minimum).
        config: Tunable coefficients for scoring.

    Raises:
        ValueError: If results is empty.
    """
    if not results:
        raise ValueError("Cannot compute fitness from empty results")

    scores = [combat_fitness(r, config=config) for r in results]
    if mode == "minimax":
        return min(scores)
    return sum(scores) / len(scores)
