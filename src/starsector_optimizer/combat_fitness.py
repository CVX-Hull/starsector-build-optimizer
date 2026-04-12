"""Combat fitness — hull-fraction-based hierarchical composite score.

Uses two game ground-truth metrics (kill progress, survival) instead of
hand-crafted damage-ratio heuristics. The opponent pool provides between-trial
discrimination; this function provides within-trial gradient.

See spec 25 for design rationale, literature backing, and tier structure.
"""

from __future__ import annotations

from .models import CombatFitnessConfig, CombatResult

_DEFAULT_CONFIG = CombatFitnessConfig()


def _no_engagement(result: CombatResult, config: CombatFitnessConfig) -> bool:
    """Check if ships never meaningfully engaged (total raw damage below threshold).

    Uses unweighted sum of all damage types (shield + armor + hull) across all
    ships on both sides. With hull-fraction-based scoring, shield-only exchanges
    should still count as engagement — the margin calculation handles scoring.
    """
    total = sum(
        s.damage_dealt.shield + s.damage_dealt.armor + s.damage_dealt.hull
        for s in (*result.player_ships, *result.enemy_ships)
    )
    return total < config.engagement_threshold


def combat_fitness(
    result: CombatResult,
    config: CombatFitnessConfig = _DEFAULT_CONFIG,
) -> float:
    """Hierarchical composite fitness for a single combat matchup.

    Tier 1: Outcome (dominant ranges)
      - PLAYER win: [win_base, win_base + win_bonus_scale]  (default [1.0, 1.5])
      - TIMEOUT:    [-timeout_scale, +timeout_scale]         (default [-0.49, +0.49])
      - ENEMY win:  [loss_base, loss_base + loss_bonus_scale] (default [-1.0, -0.5])
      - No engagement: no_engagement_score                   (default -2.0)

    Tier 2: Kill progress / survival (adjusts within tier)
    """
    # Game ground truth: how close to winning, how far from losing
    kill = (
        sum(1.0 - s.hull_fraction for s in result.enemy_ships)
        / len(result.enemy_ships)
        if result.enemy_ships
        else 0.0
    )
    surv = (
        sum(s.hull_fraction for s in result.player_ships)
        / len(result.player_ships)
        if result.player_ships
        else 0.0
    )

    if result.winner == "PLAYER":
        return config.win_base + surv * config.win_bonus_scale

    if result.winner == "ENEMY":
        return config.loss_base + kill * config.loss_bonus_scale

    # TIMEOUT (includes legacy STOPPED)
    if _no_engagement(result, config):
        return config.no_engagement_score

    margin = kill - (1.0 - surv)  # [-1, +1]
    return margin * config.timeout_scale


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
