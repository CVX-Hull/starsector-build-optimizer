"""Tests for opponent pool — diverse opponent selection and fitness computation."""

import pytest

from starsector_optimizer.models import (
    CombatResult,
    DamageBreakdown,
    HullSize,
    MatchupConfig,
    ShipCombatResult,
)
from starsector_optimizer.opponent_pool import (
    DEFAULT_OPPONENT_POOL,
    OpponentPool,
    compute_fitness,
    generate_matchups,
    get_opponents,
    hp_differential,
)


# --- Fixtures ---


def _make_ship(hull_fraction: float, destroyed: bool = False) -> ShipCombatResult:
    """Helper to create a minimal ShipCombatResult."""
    return ShipCombatResult(
        fleet_member_id="ship_0",
        variant_id="test_variant",
        hull_id="wolf",
        destroyed=destroyed,
        hull_fraction=hull_fraction,
        armor_fraction=1.0,
        cr_remaining=0.7,
        peak_time_remaining=200.0,
        disabled_weapons=0,
        flameouts=0,
        damage_dealt=DamageBreakdown(),
        damage_taken=DamageBreakdown(),
        overload_count=0,
    )


def _make_result(
    player_hp: float | list[float],
    enemy_hp: float | list[float],
    winner: str = "PLAYER",
) -> CombatResult:
    """Helper to create a CombatResult with given HP fractions."""
    if isinstance(player_hp, (int, float)):
        player_hp = [player_hp]
    if isinstance(enemy_hp, (int, float)):
        enemy_hp = [enemy_hp]

    player_ships = tuple(
        _make_ship(hp, destroyed=(hp == 0.0)) for hp in player_hp
    )
    enemy_ships = tuple(
        _make_ship(hp, destroyed=(hp == 0.0)) for hp in enemy_hp
    )
    return CombatResult(
        matchup_id="test_001",
        winner=winner,
        duration_seconds=60.0,
        player_ships=player_ships,
        enemy_ships=enemy_ships,
        player_ships_destroyed=sum(1 for hp in player_hp if hp == 0.0),
        enemy_ships_destroyed=sum(1 for hp in enemy_hp if hp == 0.0),
        player_ships_retreated=0,
        enemy_ships_retreated=0,
    )


# --- OpponentPool Tests ---


class TestOpponentPool:

    def test_default_pools_cover_all_combat_sizes(self):
        """Every combat HullSize has at least 3 opponents."""
        for size in [HullSize.FRIGATE, HullSize.DESTROYER, HullSize.CRUISER, HullSize.CAPITAL_SHIP]:
            opponents = get_opponents(DEFAULT_OPPONENT_POOL, size)
            assert len(opponents) >= 3, f"{size} has only {len(opponents)} opponents"

    def test_get_opponents_returns_tuple(self):
        opponents = get_opponents(DEFAULT_OPPONENT_POOL, HullSize.CRUISER)
        assert isinstance(opponents, tuple)
        assert all(isinstance(o, str) for o in opponents)

    def test_get_opponents_unknown_size_raises(self):
        pool = OpponentPool(pools={})
        with pytest.raises(KeyError):
            get_opponents(pool, HullSize.FRIGATE)


# --- Generate Matchups Tests ---


class TestGenerateMatchups:

    def test_one_matchup_per_opponent(self):
        opponents = ("dominator_Assault", "medusa_CS", "eagle_Assault")
        matchups = generate_matchups("my_build", opponents, "trial_42")
        assert len(matchups) == 3

    def test_matchup_ids_unique(self):
        opponents = ("dominator_Assault", "medusa_CS")
        matchups = generate_matchups("my_build", opponents, "trial_42")
        ids = [m.matchup_id for m in matchups]
        assert len(ids) == len(set(ids))

    def test_player_variant_set_correctly(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups("my_build", opponents, "t1")
        assert matchups[0].player_variants == ("my_build",)

    def test_enemy_variant_set_correctly(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups("my_build", opponents, "t1")
        assert matchups[0].enemy_variants == ("dominator_Assault",)

    def test_time_mult_propagated(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups("my_build", opponents, "t1", time_mult=3.0)
        assert matchups[0].time_mult == 3.0

    def test_matchup_id_format(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups("my_build", opponents, "trial_42")
        assert "trial_42" in matchups[0].matchup_id
        assert "dominator_Assault" in matchups[0].matchup_id


# --- HP Differential Tests ---


class TestHpDifferential:

    def test_player_wins_positive(self):
        result = _make_result(player_hp=0.8, enemy_hp=0.0, winner="PLAYER")
        diff = hp_differential(result)
        assert diff == pytest.approx(0.8)

    def test_enemy_wins_negative(self):
        result = _make_result(player_hp=0.0, enemy_hp=0.6, winner="ENEMY")
        diff = hp_differential(result)
        assert diff == pytest.approx(-0.6)

    def test_timeout_draw_based_on_hp(self):
        result = _make_result(player_hp=0.5, enemy_hp=0.3, winner="TIMEOUT")
        diff = hp_differential(result)
        assert diff == pytest.approx(0.2)

    def test_multiple_ships_averaged(self):
        result = _make_result(player_hp=[0.8, 0.6], enemy_hp=[0.0, 0.4])
        diff = hp_differential(result)
        # player avg: 0.7, enemy avg: 0.2, diff: 0.5
        assert diff == pytest.approx(0.5)

    def test_all_destroyed_is_zero(self):
        result = _make_result(player_hp=0.0, enemy_hp=0.0, winner="TIMEOUT")
        diff = hp_differential(result)
        assert diff == pytest.approx(0.0)


# --- Compute Fitness Tests ---


class TestComputeFitness:

    def test_mean_mode(self):
        r1 = _make_result(player_hp=0.8, enemy_hp=0.0)  # diff = 0.8
        r2 = _make_result(player_hp=0.0, enemy_hp=0.6)  # diff = -0.6
        fitness = compute_fitness([r1, r2], mode="mean")
        assert fitness == pytest.approx(0.1)

    def test_minimax_mode(self):
        r1 = _make_result(player_hp=0.8, enemy_hp=0.0)  # diff = 0.8
        r2 = _make_result(player_hp=0.0, enemy_hp=0.6)  # diff = -0.6
        fitness = compute_fitness([r1, r2], mode="minimax")
        assert fitness == pytest.approx(-0.6)

    def test_empty_results_raises(self):
        with pytest.raises(ValueError):
            compute_fitness([], mode="mean")

    def test_single_result(self):
        r = _make_result(player_hp=0.5, enemy_hp=0.3)
        fitness = compute_fitness([r], mode="mean")
        assert fitness == pytest.approx(0.2)
