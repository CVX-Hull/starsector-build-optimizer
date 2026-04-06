"""Tests for combat fitness — hierarchical composite score with engagement quality."""

import pytest

from starsector_optimizer.models import (
    CombatResult,
    DamageBreakdown,
    ShipCombatResult,
)
from starsector_optimizer.combat_fitness import (
    aggregate_combat_fitness,
    combat_fitness,
)


# --- Fixtures ---


def _ship(
    hull_fraction: float = 1.0,
    armor_fraction: float = 1.0,
    destroyed: bool = False,
    overload_count: int = 0,
    damage_dealt: DamageBreakdown | None = None,
    damage_taken: DamageBreakdown | None = None,
) -> ShipCombatResult:
    return ShipCombatResult(
        fleet_member_id="ship_0",
        variant_id="test",
        hull_id="eagle",
        destroyed=destroyed,
        hull_fraction=hull_fraction,
        armor_fraction=armor_fraction,
        cr_remaining=0.7,
        peak_time_remaining=200.0,
        disabled_weapons=0,
        flameouts=0,
        damage_dealt=damage_dealt or DamageBreakdown(),
        damage_taken=damage_taken or DamageBreakdown(),
        overload_count=overload_count,
    )


def _result(
    winner: str = "PLAYER",
    duration: float = 60.0,
    player_ships: tuple[ShipCombatResult, ...] | None = None,
    enemy_ships: tuple[ShipCombatResult, ...] | None = None,
) -> CombatResult:
    if player_ships is None:
        player_ships = (_ship(),)
    if enemy_ships is None:
        enemy_ships = (_ship(hull_fraction=0.0, destroyed=True),)
    return CombatResult(
        matchup_id="test_001",
        winner=winner,
        duration_seconds=duration,
        player_ships=player_ships,
        enemy_ships=enemy_ships,
        player_ships_destroyed=sum(1 for s in player_ships if s.destroyed),
        enemy_ships_destroyed=sum(1 for s in enemy_ships if s.destroyed),
        player_ships_retreated=0,
        enemy_ships_retreated=0,
    )


# --- Combat Fitness Tests ---


class TestCombatFitness:

    def test_player_win_in_range(self):
        """PLAYER win returns score in [1.0, 1.1]."""
        r = _result(winner="PLAYER", duration=60.0)
        score = combat_fitness(r)
        assert 1.0 <= score <= 1.1

    def test_enemy_win_in_range(self):
        """ENEMY win returns score in [-1.0, -0.85]."""
        r = _result(
            winner="ENEMY",
            player_ships=(_ship(hull_fraction=0.0, destroyed=True,
                                damage_dealt=DamageBreakdown(shield=100)),),
            enemy_ships=(_ship(hull_fraction=0.8,
                               damage_dealt=DamageBreakdown(armor=500, hull=300)),),
        )
        score = combat_fitness(r)
        assert -1.0 <= score <= -0.85

    def test_timeout_no_engagement_penalized(self):
        """TIMEOUT with zero damage returns -0.5."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )
        score = combat_fitness(r)
        assert score == pytest.approx(-0.5)

    def test_timeout_player_damage_advantage_positive(self):
        """TIMEOUT where player dealt more permanent damage is positive."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(armor=800, hull=200)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown(armor=100)),),
        )
        score = combat_fitness(r)
        assert score > 0

    def test_timeout_enemy_damage_advantage_negative(self):
        """TIMEOUT where enemy dealt more damage is negative."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(armor=100)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown(armor=800, hull=200)),),
        )
        score = combat_fitness(r)
        assert score < 0

    def test_decisive_win_beats_close_win(self):
        """Fast win with full HP > slow win with low HP."""
        fast = _result(winner="PLAYER", duration=30.0,
                       player_ships=(_ship(hull_fraction=1.0, armor_fraction=1.0),))
        slow = _result(winner="PLAYER", duration=170.0,
                       player_ships=(_ship(hull_fraction=0.3, armor_fraction=0.2),))
        assert combat_fitness(fast) > combat_fitness(slow)

    def test_no_tier_violation(self):
        """No timeout exceeds any win. No loss exceeds worst timeout."""
        best_timeout = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(armor=5000, hull=3000)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )
        worst_win = _result(winner="PLAYER", duration=179.0,
                            player_ships=(_ship(hull_fraction=0.01, overload_count=10),))
        best_loss = _result(
            winner="ENEMY",
            player_ships=(_ship(hull_fraction=0.0, destroyed=True,
                                damage_dealt=DamageBreakdown(armor=5000, hull=3000)),),
            enemy_ships=(_ship(hull_fraction=0.1,
                               damage_dealt=DamageBreakdown(armor=8000, hull=5000)),),
        )

        assert combat_fitness(worst_win) > combat_fitness(best_timeout)
        assert combat_fitness(best_timeout) > combat_fitness(best_loss)

    def test_armor_hull_weighted_over_shield(self):
        """Armor+hull damage scores higher than same total as shield only."""
        armor_build = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(armor=500, hull=500)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown(armor=500)),),
        )
        shield_build = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(shield=1000)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown(shield=1000)),),
        )
        assert combat_fitness(armor_build) > combat_fitness(shield_build)

    def test_low_overloads_better(self):
        """Win with fewer player overloads scores higher."""
        clean_win = _result(winner="PLAYER", duration=60.0,
                            player_ships=(_ship(overload_count=0),))
        sloppy_win = _result(winner="PLAYER", duration=60.0,
                             player_ships=(_ship(overload_count=5),))
        assert combat_fitness(clean_win) > combat_fitness(sloppy_win)

    def test_stopped_treated_as_timeout(self):
        """STOPPED (curtailment) treated same as TIMEOUT."""
        stopped = _result(
            winner="STOPPED", duration=90.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown(armor=800)),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown(armor=200)),),
        )
        score = combat_fitness(stopped)
        assert -0.5 <= score <= 0.5


# --- Aggregate Fitness Tests ---


class TestAggregateFitness:

    def test_mean_mode(self):
        """Average of combat_fitness across results."""
        r1 = _result(winner="PLAYER")  # ~1.0+
        r2 = _result(winner="TIMEOUT", duration=180.0,
                     player_ships=(_ship(damage_dealt=DamageBreakdown()),),
                     enemy_ships=(_ship(damage_dealt=DamageBreakdown()),))  # -0.5
        fitness = aggregate_combat_fitness([r1, r2], mode="mean")
        assert 0.0 < fitness < 1.0  # average of ~1.0 and -0.5

    def test_minimax_mode(self):
        """Minimum of combat_fitness across results."""
        r1 = _result(winner="PLAYER")
        r2 = _result(winner="TIMEOUT", duration=180.0,
                     player_ships=(_ship(damage_dealt=DamageBreakdown()),),
                     enemy_ships=(_ship(damage_dealt=DamageBreakdown()),))
        fitness = aggregate_combat_fitness([r1, r2], mode="minimax")
        assert fitness == pytest.approx(-0.5)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            aggregate_combat_fitness([], mode="mean")
