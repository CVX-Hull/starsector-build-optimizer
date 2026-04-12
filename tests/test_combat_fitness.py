"""Tests for combat fitness — hull-fraction-based hierarchical composite score."""

import pytest

from starsector_optimizer.models import (
    CombatFitnessConfig,
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
        """PLAYER win returns score in [1.0, 1.5]."""
        r = _result(winner="PLAYER", duration=60.0)
        score = combat_fitness(r)
        assert 1.0 <= score <= 1.5

    def test_enemy_win_in_range(self):
        """ENEMY win returns score in [-1.0, -0.5]."""
        r = _result(
            winner="ENEMY",
            player_ships=(_ship(hull_fraction=0.0, destroyed=True),),
            enemy_ships=(_ship(hull_fraction=0.8),),
        )
        score = combat_fitness(r)
        assert -1.0 <= score <= -0.5

    def test_timeout_no_engagement_penalized(self):
        """TIMEOUT with zero damage returns no_engagement_score (-2.0)."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )
        score = combat_fitness(r)
        assert score == pytest.approx(-2.0)

    def test_timeout_player_damage_advantage_positive(self):
        """TIMEOUT where player made more kill progress is positive."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.9,
                damage_dealt=DamageBreakdown(armor=800, hull=200),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.3,
                damage_dealt=DamageBreakdown(armor=100),
            ),),
        )
        score = combat_fitness(r)
        assert score > 0

    def test_timeout_enemy_damage_advantage_negative(self):
        """TIMEOUT where enemy made more kill progress is negative."""
        r = _result(
            winner="TIMEOUT",
            duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.3,
                damage_dealt=DamageBreakdown(armor=100),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.9,
                damage_dealt=DamageBreakdown(armor=800, hull=200),
            ),),
        )
        score = combat_fitness(r)
        assert score < 0

    def test_decisive_win_beats_close_win(self):
        """Win with full survival beats win with low survival."""
        dominant = _result(
            winner="PLAYER", duration=30.0,
            player_ships=(_ship(hull_fraction=1.0, armor_fraction=1.0),),
            enemy_ships=(_ship(hull_fraction=0.0, destroyed=True),),
        )
        scrappy = _result(
            winner="PLAYER", duration=170.0,
            player_ships=(_ship(hull_fraction=0.1, armor_fraction=0.1),),
            enemy_ships=(_ship(hull_fraction=0.0, destroyed=True),),
        )
        assert combat_fitness(dominant) > combat_fitness(scrappy)

    def test_no_tier_violation(self):
        """Strict tier ordering: win > timeout > loss > no_engagement."""
        worst_win = _result(
            winner="PLAYER", duration=179.0,
            player_ships=(_ship(hull_fraction=0.01),),
            enemy_ships=(_ship(hull_fraction=0.0, destroyed=True),),
        )
        best_timeout = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(
                hull_fraction=1.0,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.01,
                damage_dealt=DamageBreakdown(),
            ),),
        )
        best_loss = _result(
            winner="ENEMY",
            player_ships=(_ship(
                hull_fraction=0.0, destroyed=True,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.01,
                damage_dealt=DamageBreakdown(armor=8000, hull=5000),
            ),),
        )
        no_engagement = _result(
            winner="TIMEOUT", duration=300.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )

        assert combat_fitness(worst_win) > combat_fitness(best_timeout)
        assert combat_fitness(best_timeout) > combat_fitness(best_loss)
        assert combat_fitness(best_loss) > combat_fitness(no_engagement)

    def test_stopped_treated_as_timeout(self):
        """STOPPED (legacy) treated same as TIMEOUT."""
        stopped = _result(
            winner="STOPPED", duration=90.0,
            player_ships=(_ship(
                hull_fraction=0.8,
                damage_dealt=DamageBreakdown(armor=800),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.4,
                damage_dealt=DamageBreakdown(armor=200),
            ),),
        )
        score = combat_fitness(stopped)
        assert -0.49 <= score <= 0.49

    def test_partial_kill_credit(self):
        """TIMEOUT with partial kills scores higher than uniform damage."""
        # 2/3 enemies destroyed + 1 at 50% hull = kill_progress ≈ 0.833
        partial_kills = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.5,
                damage_dealt=DamageBreakdown(armor=3000, hull=2000),
            ),),
            enemy_ships=(
                _ship(hull_fraction=0.0, destroyed=True,
                      damage_dealt=DamageBreakdown(armor=500)),
                _ship(hull_fraction=0.0, destroyed=True,
                      damage_dealt=DamageBreakdown(armor=500)),
                _ship(hull_fraction=0.5,
                      damage_dealt=DamageBreakdown(armor=500)),
            ),
        )
        # All 3 enemies at 80% hull = kill_progress = 0.2
        uniform_damage = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.5,
                damage_dealt=DamageBreakdown(armor=600),
            ),),
            enemy_ships=(
                _ship(hull_fraction=0.8,
                      damage_dealt=DamageBreakdown(armor=500)),
                _ship(hull_fraction=0.8,
                      damage_dealt=DamageBreakdown(armor=500)),
                _ship(hull_fraction=0.8,
                      damage_dealt=DamageBreakdown(armor=500)),
            ),
        )
        assert combat_fitness(partial_kills) > combat_fitness(uniform_damage)

    def test_no_engagement_below_all_losses(self):
        """No-engagement score is strictly below the worst possible loss."""
        worst_loss = _result(
            winner="ENEMY",
            player_ships=(_ship(hull_fraction=0.0, destroyed=True),),
            enemy_ships=(_ship(hull_fraction=1.0),),
        )
        no_engagement = _result(
            winner="TIMEOUT", duration=300.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )
        assert combat_fitness(no_engagement) < combat_fitness(worst_loss)

    def test_win_differentiated_by_survival(self):
        """Wins with different survival levels are clearly distinguishable."""
        full_hp = _result(
            winner="PLAYER",
            player_ships=(_ship(hull_fraction=1.0),),
            enemy_ships=(_ship(hull_fraction=0.0, destroyed=True),),
        )
        low_hp = _result(
            winner="PLAYER",
            player_ships=(_ship(hull_fraction=0.1),),
            enemy_ships=(_ship(hull_fraction=0.0, destroyed=True),),
        )
        diff = combat_fitness(full_hp) - combat_fitness(low_hp)
        assert diff > 0.3  # Wide band, not the old 0.1 max

    def test_loss_differentiated_by_kill_progress(self):
        """Losses with different kill progress are clearly distinguishable."""
        near_win = _result(
            winner="ENEMY",
            player_ships=(_ship(
                hull_fraction=0.0, destroyed=True,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.1,
                damage_dealt=DamageBreakdown(armor=8000, hull=5000),
            ),),
        )
        blowout = _result(
            winner="ENEMY",
            player_ships=(_ship(
                hull_fraction=0.0, destroyed=True,
                damage_dealt=DamageBreakdown(armor=100),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.95,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
        )
        diff = combat_fitness(near_win) - combat_fitness(blowout)
        assert diff > 0.3  # Wide band

    def test_timeout_margin_symmetry(self):
        """Timeout margin is symmetric around zero for equal exchange."""
        even = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.5,
                damage_dealt=DamageBreakdown(armor=1000),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.5,
                damage_dealt=DamageBreakdown(armor=1000),
            ),),
        )
        score = combat_fitness(even)
        assert score == pytest.approx(0.0)

    def test_mutual_destruction_is_neutral_timeout(self):
        """Both sides destroyed (TIMEOUT from harness) scores near 0."""
        mutual = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(
                hull_fraction=0.0, destroyed=True,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
            enemy_ships=(_ship(
                hull_fraction=0.0, destroyed=True,
                damage_dealt=DamageBreakdown(armor=5000, hull=3000),
            ),),
        )
        score = combat_fitness(mutual)
        assert score == pytest.approx(0.0)


# --- Aggregate Fitness Tests ---


class TestAggregateFitness:

    def test_mean_mode(self):
        """Average of combat_fitness across results."""
        r1 = _result(winner="PLAYER")  # ~1.0+
        r2 = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )  # -2.0 (no engagement)
        fitness = aggregate_combat_fitness([r1, r2], mode="mean")
        assert -1.0 < fitness < 1.0

    def test_minimax_mode(self):
        """Minimum of combat_fitness across results."""
        r1 = _result(winner="PLAYER")
        r2 = _result(
            winner="TIMEOUT", duration=180.0,
            player_ships=(_ship(damage_dealt=DamageBreakdown()),),
            enemy_ships=(_ship(damage_dealt=DamageBreakdown()),),
        )
        fitness = aggregate_combat_fitness([r1, r2], mode="minimax")
        assert fitness == pytest.approx(-2.0)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            aggregate_combat_fitness([], mode="mean")
