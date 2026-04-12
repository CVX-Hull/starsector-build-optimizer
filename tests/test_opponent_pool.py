"""Tests for opponent pool — diverse opponent selection and fitness computation."""

import json
import logging
from pathlib import Path

import pytest

from starsector_optimizer.models import (
    BuildSpec,
    CombatResult,
    DamageBreakdown,
    HullSize,
    MatchupConfig,
    ShipCombatResult,
)
from starsector_optimizer.opponent_pool import (
    OpponentPool,
    compute_fitness,
    discover_opponent_pool,
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

    def test_get_opponents_returns_tuple(self):
        pool = OpponentPool(pools={HullSize.CRUISER: ("dom_Assault", "eagle_Assault")})
        opponents = get_opponents(pool, HullSize.CRUISER)
        assert isinstance(opponents, tuple)
        assert all(isinstance(o, str) for o in opponents)

    def test_get_opponents_unknown_size_raises(self):
        pool = OpponentPool(pools={})
        with pytest.raises(KeyError):
            get_opponents(pool, HullSize.FRIGATE)


# --- Discover Opponent Pool Tests ---


class TestDiscoverOpponentPool:

    @pytest.fixture(scope="module")
    def game_data(self):
        from starsector_optimizer.parser import load_game_data
        return load_game_data(Path("game/starsector"))

    def test_discover_finds_stock_variants(self, game_data):
        """discover_opponent_pool returns non-empty pools for all combat hull sizes."""
        pool = discover_opponent_pool(Path("game/starsector"), game_data)
        for size in [HullSize.FRIGATE, HullSize.DESTROYER, HullSize.CRUISER, HullSize.CAPITAL_SHIP]:
            opponents = get_opponents(pool, size)
            assert len(opponents) >= 2, f"{size.name} has only {len(opponents)} opponents"

    def test_discover_stock_variant_ids_returns_pairs(self, game_data):
        """discover_stock_variant_ids returns (variant_id, hull_id) pairs from real game data."""
        from starsector_optimizer.variant import discover_stock_variant_ids

        result = discover_stock_variant_ids(Path("game/starsector"))
        assert len(result) > 0, "Expected to find stock variants"
        # Each entry is a (variant_id, hull_id) tuple with string values
        for variant_id, hull_id in result:
            assert isinstance(variant_id, str)
            assert isinstance(hull_id, str)
        # At least some hull_ids should be in game data (faction skins may not be)
        known = [hid for _, hid in result if hid in game_data.hulls]
        assert len(known) > 10, f"Expected many known hulls, got {len(known)}"

    def test_discover_excludes_optimizer_variants(self, game_data, tmp_path):
        """Optimizer-generated variant files are excluded from discovery."""
        from starsector_optimizer.variant import discover_stock_variant_ids

        # Create a temp variants dir with one real and one optimizer-generated variant
        variants_dir = tmp_path / "data" / "variants"
        variants_dir.mkdir(parents=True)

        real_variant = {"variantId": "wolf_Test", "hullId": "wolf", "weaponGroups": [], "hullMods": []}
        opt_variant = {"variantId": "wolf_opt_000001", "hullId": "wolf", "weaponGroups": [], "hullMods": []}

        (variants_dir / "wolf_Test.variant").write_text(json.dumps(real_variant))
        (variants_dir / "wolf_opt_000001.variant").write_text(json.dumps(opt_variant))

        result = discover_stock_variant_ids(tmp_path)
        variant_ids = [vid for vid, _ in result]
        assert "wolf_Test" in variant_ids
        assert "wolf_opt_000001" not in variant_ids

    def test_discover_excludes_unknown_hulls(self, game_data, tmp_path):
        """Variants referencing hulls not in game_data are excluded."""
        from starsector_optimizer.variant import discover_stock_variant_ids

        variants_dir = tmp_path / "data" / "variants"
        variants_dir.mkdir(parents=True)
        unknown = {"variantId": "alien_Assault", "hullId": "alien_ship", "weaponGroups": [], "hullMods": []}
        (variants_dir / "alien_Assault.variant").write_text(json.dumps(unknown))

        pool = discover_opponent_pool(tmp_path, game_data)
        # alien_ship is not in game_data, so it should not appear in any pool
        all_opponents = []
        for size in HullSize:
            try:
                all_opponents.extend(get_opponents(pool, size))
            except KeyError:
                pass
        assert "alien_Assault" not in all_opponents

    def test_discover_warns_sparse_pool(self, tmp_path, caplog):
        """Warning emitted if a hull size has fewer than 2 opponents."""
        from starsector_optimizer.models import GameData, ShipHull, ShieldType

        # Minimal game data with one hull
        hull = ShipHull(
            id="wolf", name="Wolf", hull_size=HullSize.FRIGATE, designation="Fast Attack",
            tech_manufacturer="", system_id="", fleet_pts=5, hitpoints=2000,
            armor_rating=200, max_flux=2500, flux_dissipation=200, ordnance_points=55,
            fighter_bays=0, max_speed=150, shield_type=ShieldType.OMNI, shield_arc=360,
            shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0, phase_upkeep=0,
            peak_cr_sec=300, cr_loss_per_sec=0.25,
            weapon_slots=[], built_in_mods=frozenset(), built_in_weapons={},
        )
        game_data = GameData(
            hulls={"wolf": hull}, weapons={}, hullmods={},
        )

        # Create one variant
        variants_dir = tmp_path / "data" / "variants"
        variants_dir.mkdir(parents=True)
        variant = {"variantId": "wolf_Solo", "hullId": "wolf", "weaponGroups": [], "hullMods": []}
        (variants_dir / "wolf_Solo.variant").write_text(json.dumps(variant))

        with caplog.at_level(logging.WARNING):
            discover_opponent_pool(tmp_path, game_data)

        assert any("only 1 opponent" in msg for msg in caplog.messages)

    def test_discover_skips_malformed_variants(self, game_data, tmp_path):
        """Malformed .variant files are skipped silently."""
        from starsector_optimizer.variant import discover_stock_variant_ids

        variants_dir = tmp_path / "data" / "variants"
        variants_dir.mkdir(parents=True)
        (variants_dir / "broken.variant").write_text("not valid json {{{")

        # Should not raise
        result = discover_stock_variant_ids(tmp_path)
        assert isinstance(result, list)


# --- Generate Matchups Tests ---


class TestGenerateMatchups:

    @staticmethod
    def _build_spec():
        return BuildSpec(
            variant_id="my_build",
            hull_id="eagle",
            weapon_assignments={"WS1": "heavymauler"},
            hullmods=("heavyarmor",),
            flux_vents=15,
            flux_capacitors=10,
        )

    def test_one_matchup_per_opponent(self):
        opponents = ("dominator_Assault", "medusa_CS", "eagle_Assault")
        matchups = generate_matchups(self._build_spec(), opponents, "trial_42")
        assert len(matchups) == 3

    def test_matchup_ids_unique(self):
        opponents = ("dominator_Assault", "medusa_CS")
        matchups = generate_matchups(self._build_spec(), opponents, "trial_42")
        ids = [m.matchup_id for m in matchups]
        assert len(ids) == len(set(ids))

    def test_player_build_set_correctly(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups(self._build_spec(), opponents, "t1")
        assert matchups[0].player_builds[0].variant_id == "my_build"

    def test_enemy_variant_set_correctly(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups(self._build_spec(), opponents, "t1")
        assert matchups[0].enemy_variants == ("dominator_Assault",)

    def test_time_mult_propagated(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups(self._build_spec(), opponents, "t1", time_mult=3.0)
        assert matchups[0].time_mult == 3.0

    def test_matchup_id_format(self):
        opponents = ("dominator_Assault",)
        matchups = generate_matchups(self._build_spec(), opponents, "trial_42")
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

    def test_empty_player_ships(self):
        """Empty player_ships returns 0.0."""
        result = CombatResult(
            matchup_id="test", winner="ENEMY", duration_seconds=60.0,
            player_ships=(), enemy_ships=(_make_ship(0.5),),
            player_ships_destroyed=0, enemy_ships_destroyed=0,
            player_ships_retreated=0, enemy_ships_retreated=0,
        )
        assert hp_differential(result) == 0.0

    def test_empty_enemy_ships(self):
        """Empty enemy_ships returns 0.0."""
        result = CombatResult(
            matchup_id="test", winner="PLAYER", duration_seconds=60.0,
            player_ships=(_make_ship(0.8),), enemy_ships=(),
            player_ships_destroyed=0, enemy_ships_destroyed=0,
            player_ships_retreated=0, enemy_ships_retreated=0,
        )
        assert hp_differential(result) == 0.0


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
