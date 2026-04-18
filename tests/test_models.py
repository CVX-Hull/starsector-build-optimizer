"""Tests for data models: enums, dataclasses, computed properties."""

import pytest

from starsector_optimizer.models import (
    Build,
    BuildSpec,
    CombatResult,
    DamageBreakdown,
    DamageType,
    EffectiveStats,
    GameData,
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


# --- Enum tests ---


class TestHullSize:
    def test_members(self):
        assert HullSize.FRIGATE == "FRIGATE"
        assert HullSize.DESTROYER == "DESTROYER"
        assert HullSize.CRUISER == "CRUISER"
        assert HullSize.CAPITAL_SHIP == "CAPITAL_SHIP"

    def test_from_str_known(self):
        assert HullSize.from_str("FRIGATE") == HullSize.FRIGATE
        assert HullSize.from_str("CRUISER") == HullSize.CRUISER
        assert HullSize.from_str("CAPITAL_SHIP") == HullSize.CAPITAL_SHIP

    def test_from_str_designation(self):
        """CSV designation column uses title case."""
        assert HullSize.from_str("Frigate") == HullSize.FRIGATE
        assert HullSize.from_str("Destroyer") == HullSize.DESTROYER
        assert HullSize.from_str("Cruiser") == HullSize.CRUISER
        assert HullSize.from_str("Capital Ship") == HullSize.CAPITAL_SHIP

    def test_from_str_unknown(self):
        assert HullSize.from_str("BATTLESHIP") is None
        assert HullSize.from_str("") is None


class TestSlotType:
    def test_members(self):
        assert SlotType.HYBRID == "HYBRID"
        assert SlotType.UNIVERSAL == "UNIVERSAL"

    def test_from_str_unknown(self):
        assert SlotType.from_str("TACTICAL") is None


class TestDamageType:
    def test_members(self):
        assert DamageType.KINETIC == "KINETIC"
        assert DamageType.HIGH_EXPLOSIVE == "HIGH_EXPLOSIVE"
        assert DamageType.ENERGY == "ENERGY"
        assert DamageType.FRAGMENTATION == "FRAGMENTATION"

    def test_from_str_known(self):
        assert DamageType.from_str("KINETIC") == DamageType.KINETIC

    def test_from_str_unknown(self):
        assert DamageType.from_str("PLASMA") is None


class TestWeaponType:
    def test_from_str_known(self):
        assert WeaponType.from_str("BALLISTIC") == WeaponType.BALLISTIC
        assert WeaponType.from_str("ENERGY") == WeaponType.ENERGY
        assert WeaponType.from_str("MISSILE") == WeaponType.MISSILE

    def test_from_str_unknown(self):
        assert WeaponType.from_str("HYBRID") is None


class TestShieldType:
    def test_from_str_known(self):
        assert ShieldType.from_str("FRONT") == ShieldType.FRONT
        assert ShieldType.from_str("NONE") == ShieldType.NONE

    def test_from_str_unknown(self):
        assert ShieldType.from_str("BUBBLE") is None


# --- WeaponSlot tests ---


class TestWeaponSlot:
    def test_construction(self):
        slot = WeaponSlot(
            id="WS 001",
            slot_type=SlotType.BALLISTIC,
            slot_size=SlotSize.MEDIUM,
            mount_type=MountType.HARDPOINT,
            angle=0.0,
            arc=5.0,
            position=(80.0, 20.0),
        )
        assert slot.id == "WS 001"
        assert slot.slot_type == SlotType.BALLISTIC
        assert slot.slot_size == SlotSize.MEDIUM
        assert slot.mount_type == MountType.HARDPOINT

    def test_frozen(self):
        slot = WeaponSlot("WS 001", SlotType.BALLISTIC, SlotSize.MEDIUM,
                          MountType.HARDPOINT, 0.0, 5.0, (80.0, 20.0))
        with pytest.raises(AttributeError):
            slot.id = "WS 002"


# --- ShipHull tests ---


def _make_hull(hull_size=HullSize.CRUISER, **kwargs):
    defaults = dict(
        id="eagle", name="Eagle", hull_size=hull_size, designation="Cruiser",
        tech_manufacturer="Midline", system_id="maneuveringjets", fleet_pts=14,
        hitpoints=8000.0, armor_rating=1000.0, max_flux=11000.0,
        flux_dissipation=700.0, ordnance_points=155, fighter_bays=0,
        max_speed=60.0, shield_type=ShieldType.FRONT, shield_arc=270.0,
        shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0.0,
        phase_upkeep=0.0, peak_cr_sec=480.0, cr_loss_per_sec=0.25,
        weapon_slots=[], built_in_mods=[], built_in_weapons={},
        hints=[], tags=["rare_bp", "merc"],
    )
    defaults.update(kwargs)
    return ShipHull(**defaults)


class TestShipHull:
    def test_construction(self):
        hull = _make_hull()
        assert hull.id == "eagle"
        assert hull.hull_size == HullSize.CRUISER
        assert hull.ordnance_points == 155

    def test_max_vents_frigate(self):
        hull = _make_hull(hull_size=HullSize.FRIGATE)
        assert hull.max_vents == 10

    def test_max_vents_destroyer(self):
        hull = _make_hull(hull_size=HullSize.DESTROYER)
        assert hull.max_vents == 20

    def test_max_vents_cruiser(self):
        hull = _make_hull(hull_size=HullSize.CRUISER)
        assert hull.max_vents == 30

    def test_max_vents_capital(self):
        hull = _make_hull(hull_size=HullSize.CAPITAL_SHIP)
        assert hull.max_vents == 50

    def test_max_capacitors_equals_max_vents(self):
        for size in HullSize:
            hull = _make_hull(hull_size=size)
            assert hull.max_capacitors == hull.max_vents


# --- Weapon tests ---


def _make_weapon(**kwargs):
    defaults = dict(
        id="heavymauler", name="Heavy Mauler", size=SlotSize.MEDIUM,
        weapon_type=WeaponType.BALLISTIC, damage_per_shot=200.0,
        damage_per_second=0.0, damage_type=DamageType.KINETIC, emp=0.0,
        flux_per_shot=200.0, flux_per_second=0.0, range=700.0, op_cost=10,
        chargeup=0.0, chargedown=0.75, burst_size=1, burst_delay=0.0,
        ammo=0, ammo_per_sec=0.0, proj_speed=500.0, turn_rate=30.0,
        hints=[], tags=["kinetic3"],
    )
    defaults.update(kwargs)
    return Weapon(**defaults)


class TestWeaponSustainedDps:
    def test_single_shot_weapon(self):
        """Heavy Mauler: 200 damage, 0.75s chargedown = 266.67 DPS."""
        w = _make_weapon(damage_per_shot=200.0, chargeup=0.0, chargedown=0.75,
                         burst_size=1)
        assert pytest.approx(w.sustained_dps, rel=0.01) == 200.0 / 0.75

    def test_burst_weapon(self):
        """Burst weapon: 5 shots, 0.1s delay, 0.5s chargedown."""
        w = _make_weapon(damage_per_shot=25.0, chargeup=0.0, chargedown=0.5,
                         burst_size=5, burst_delay=0.1)
        # cycle = 0 + (5-1)*0.1 + 0.5 = 0.9s, total damage = 125
        expected = 25.0 * 5 / 0.9
        assert pytest.approx(w.sustained_dps, rel=0.01) == expected

    def test_beam_weapon(self):
        """Beam weapon: uses damage_per_second directly."""
        w = _make_weapon(damage_per_shot=0.0, damage_per_second=300.0,
                         chargeup=0.0, chargedown=0.0, burst_size=1)
        assert w.sustained_dps == 300.0

    def test_zero_cycle_non_beam(self):
        """Non-beam with zero cycle time returns 0."""
        w = _make_weapon(damage_per_shot=100.0, damage_per_second=0.0,
                         chargeup=0.0, chargedown=0.0, burst_size=1)
        assert w.sustained_dps == 0.0


class TestWeaponSustainedFlux:
    def test_projectile_weapon(self):
        w = _make_weapon(flux_per_shot=200.0, chargeup=0.0, chargedown=0.75,
                         burst_size=1)
        # flux/s = flux_per_shot * burst_size / cycle_time = 200/0.75
        assert pytest.approx(w.sustained_flux, rel=0.01) == 200.0 / 0.75

    def test_beam_weapon(self):
        w = _make_weapon(damage_per_shot=0.0, damage_per_second=300.0,
                         flux_per_shot=0.0, flux_per_second=250.0)
        assert w.sustained_flux == 250.0


class TestWeaponDerivedMetrics:
    def test_flux_efficiency(self):
        w = _make_weapon()
        expected = w.sustained_dps / w.sustained_flux
        assert pytest.approx(w.flux_efficiency, rel=0.01) == expected

    def test_flux_efficiency_no_flux(self):
        """Weapon with zero flux cost has infinite efficiency (capped or handled)."""
        w = _make_weapon(flux_per_shot=0.0, flux_per_second=0.0)
        assert w.flux_efficiency == float("inf") or w.flux_efficiency > 1e6

    def test_shield_dps_kinetic(self):
        w = _make_weapon(damage_type=DamageType.KINETIC)
        assert pytest.approx(w.shield_dps, rel=0.01) == w.sustained_dps * 2.0

    def test_shield_dps_he(self):
        w = _make_weapon(damage_type=DamageType.HIGH_EXPLOSIVE)
        assert pytest.approx(w.shield_dps, rel=0.01) == w.sustained_dps * 0.5

    def test_armor_dps_kinetic(self):
        w = _make_weapon(damage_type=DamageType.KINETIC)
        assert pytest.approx(w.armor_dps, rel=0.01) == w.sustained_dps * 0.5

    def test_armor_dps_he(self):
        w = _make_weapon(damage_type=DamageType.HIGH_EXPLOSIVE)
        assert pytest.approx(w.armor_dps, rel=0.01) == w.sustained_dps * 2.0

    def test_is_pd(self):
        w = _make_weapon(hints=["PD", "ANTI_FTR"])
        assert w.is_pd is True

    def test_is_not_pd(self):
        w = _make_weapon(hints=[])
        assert w.is_pd is False

    def test_is_beam(self):
        w = _make_weapon(damage_per_shot=0.0, damage_per_second=300.0)
        assert w.is_beam is True

    def test_is_not_beam(self):
        w = _make_weapon(damage_per_shot=200.0, damage_per_second=0.0)
        assert w.is_beam is False


# --- HullMod tests ---


def _make_hullmod(**kwargs):
    defaults = dict(
        id="heavyarmor", name="Heavy Armor", tier=1,
        tags=["defensive", "armor"], ui_tags=["Armor"],
        cost_frigate=8, cost_destroyer=12, cost_cruiser=16, cost_capital=24,
        is_hidden=False, script="data.hullmods.HeavyArmor",
    )
    defaults.update(kwargs)
    return HullMod(**defaults)


class TestHullMod:
    def test_op_cost_by_size(self):
        mod = _make_hullmod()
        assert mod.op_cost(HullSize.FRIGATE) == 8
        assert mod.op_cost(HullSize.DESTROYER) == 12
        assert mod.op_cost(HullSize.CRUISER) == 16
        assert mod.op_cost(HullSize.CAPITAL_SHIP) == 24

    def test_is_logistics_false(self):
        mod = _make_hullmod(tags=["defensive", "armor"])
        assert mod.is_logistics is False

    def test_is_logistics_true_via_tags(self):
        mod = _make_hullmod(tags=["logistics", "special"])
        assert mod.is_logistics is True

    def test_is_logistics_true_via_ui_tags(self):
        mod = _make_hullmod(tags=[], ui_tags=["Logistics", "Requires Dock"])
        assert mod.is_logistics is True


# --- Build tests ---


class TestBuild:
    def test_construction(self):
        build = Build(
            hull_id="eagle",
            weapon_assignments={"WS 001": "heavymauler", "WS 002": None},
            hullmods=frozenset(["heavyarmor", "hardenedshields"]),
            flux_vents=15,
            flux_capacitors=10,
        )
        assert build.hull_id == "eagle"
        assert build.weapon_assignments["WS 001"] == "heavymauler"
        assert "heavyarmor" in build.hullmods
        assert build.flux_vents == 15

    def test_frozen(self):
        build = Build("eagle", {}, frozenset(), 0, 0)
        with pytest.raises(AttributeError):
            build.hull_id = "wolf"

    def test_hullmods_is_frozenset(self):
        build = Build("eagle", {}, frozenset(["a", "b"]), 0, 0)
        assert isinstance(build.hullmods, frozenset)


# --- EffectiveStats and ScorerResult tests ---


class TestEffectiveStats:
    def test_construction(self):
        stats = EffectiveStats(
            flux_dissipation=700.0, flux_capacity=11000.0,
            armor_rating=1000.0, hull_hitpoints=8000.0,
            shield_efficiency=0.8, shield_upkeep=0.4,
            has_shields=True, max_speed=60.0,
            weapon_range_bonus=0.0, weapon_range_threshold=None,
            weapon_range_compression=1.0,
            peak_performance_time=480.0,
        )
        assert stats.flux_dissipation == 700.0
        assert stats.has_shields is True


class TestScorerResult:
    def test_construction(self):
        stats = EffectiveStats(700, 11000, 1000, 8000, 0.8, 0.4,
                               True, 60, 0, None, 1.0, 480)
        result = ScorerResult(
            composite_score=0.75, total_dps=500.0, kinetic_dps=300.0,
            he_dps=200.0, energy_dps=0.0, flux_balance=0.7,
            flux_efficiency=1.5, effective_hp=20000.0, armor_ehp=5000.0,
            shield_ehp=10000.0, range_coherence=0.9, damage_mix=0.8,
            engagement_range=700.0, op_efficiency=3.5,
            effective_stats=stats,
        )
        assert result.composite_score == 0.75
        assert result.effective_stats.flux_dissipation == 700


# --- GameData tests ---


class TestGameData:
    def test_construction(self):
        gd = GameData(hulls={}, weapons={}, hullmods={})
        assert len(gd.hulls) == 0


# --- Phase 2: Combat protocol dataclass tests ---


class TestDamageBreakdown:
    def test_defaults_are_zero(self):
        db = DamageBreakdown()
        assert db.shield == 0.0
        assert db.armor == 0.0
        assert db.hull == 0.0
        assert db.emp == 0.0

    def test_construction(self):
        db = DamageBreakdown(shield=100.0, armor=200.0, hull=50.0, emp=10.0)
        assert db.shield == 100.0
        assert db.armor == 200.0

    def test_frozen(self):
        db = DamageBreakdown()
        with pytest.raises(AttributeError):
            db.shield = 1.0


class TestShipCombatResult:
    def test_construction(self):
        scr = ShipCombatResult(
            fleet_member_id="0",
            variant_id="eagle_opt_test",
            hull_id="eagle",
            destroyed=False,
            hull_fraction=0.82,
            armor_fraction=0.45,
            cr_remaining=0.61,
            peak_time_remaining=142.0,
            disabled_weapons=0,
            flameouts=0,
            damage_dealt=DamageBreakdown(shield=1000.0, armor=500.0),
            damage_taken=DamageBreakdown(hull=200.0),
            overload_count=1,
        )
        assert scr.hull_id == "eagle"
        assert not scr.destroyed
        assert scr.damage_dealt.shield == 1000.0
        assert scr.damage_taken.hull == 200.0
        assert scr.overload_count == 1

    def test_frozen(self):
        scr = ShipCombatResult(
            fleet_member_id="0", variant_id="x", hull_id="x",
            destroyed=False, hull_fraction=1.0, armor_fraction=1.0,
            cr_remaining=1.0, peak_time_remaining=0.0,
            disabled_weapons=0, flameouts=0,
            damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
            overload_count=0,
        )
        with pytest.raises(AttributeError):
            scr.destroyed = True


class TestCombatResult:
    def test_construction(self):
        ship = ShipCombatResult(
            fleet_member_id="0", variant_id="eagle_test", hull_id="eagle",
            destroyed=False, hull_fraction=0.9, armor_fraction=0.7,
            cr_remaining=0.5, peak_time_remaining=100.0,
            disabled_weapons=0, flameouts=0,
            damage_dealt=DamageBreakdown(), damage_taken=DamageBreakdown(),
            overload_count=0,
        )
        cr = CombatResult(
            matchup_id="eval_001",
            winner="PLAYER",
            duration_seconds=87.3,
            player_ships=(ship,),
            enemy_ships=(),
            player_ships_destroyed=0,
            enemy_ships_destroyed=1,
            player_ships_retreated=0,
            enemy_ships_retreated=0,
        )
        assert cr.matchup_id == "eval_001"
        assert cr.winner == "PLAYER"
        assert len(cr.player_ships) == 1
        assert cr.enemy_ships_destroyed == 1
        assert cr.player_ships_retreated == 0

    def test_frozen(self):
        cr = CombatResult(
            matchup_id="x", winner="PLAYER", duration_seconds=0.0,
            player_ships=(), enemy_ships=(),
            player_ships_destroyed=0, enemy_ships_destroyed=0,
            player_ships_retreated=0, enemy_ships_retreated=0,
        )
        with pytest.raises(AttributeError):
            cr.winner = "ENEMY"


def _build_spec(variant_id="eagle_test", hull_id="eagle"):
    return BuildSpec(variant_id=variant_id, hull_id=hull_id, weapon_assignments={},
                     hullmods=(), flux_vents=0, flux_capacitors=0)


class TestMatchupConfig:
    def test_construction_with_defaults(self):
        mc = MatchupConfig(
            matchup_id="eval_001",
            player_builds=(_build_spec(),),
            enemy_variants=("dominator_Standard",),
        )
        assert mc.matchup_id == "eval_001"
        assert mc.time_limit_seconds == 300.0
        assert mc.time_mult == 3.0
        assert mc.map_width == 24000.0
        assert mc.map_height == 18000.0

    def test_construction_with_all_fields(self):
        mc = MatchupConfig(
            matchup_id="eval_002",
            player_builds=(_build_spec("eagle_test"), _build_spec("wolf_test", "wolf")),
            enemy_variants=("dominator_Standard",),
            time_limit_seconds=120.0,
            time_mult=5.0,
            map_width=16000.0,
            map_height=12000.0,
        )
        assert len(mc.player_builds) == 2
        assert mc.time_mult == 5.0

    def test_frozen(self):
        mc = MatchupConfig(
            matchup_id="x",
            player_builds=(_build_spec("a"),),
            enemy_variants=("b",),
        )
        with pytest.raises(AttributeError):
            mc.time_mult = 5.0


# --- Phase 5F: RegimeConfig tests ---


class TestRegimeConfig:
    def test_regime_config_frozen(self):
        from starsector_optimizer.models import RegimeConfig

        cfg = RegimeConfig(
            name="x",
            max_hullmod_tier=3,
            exclude_hullmod_tags=frozenset(),
            exclude_weapon_tags=frozenset(),
        )
        with pytest.raises(AttributeError):
            cfg.name = "y"

    def test_regime_presets_exist(self):
        from starsector_optimizer.models import (
            REGIME_EARLY,
            REGIME_ENDGAME,
            REGIME_LATE,
            REGIME_MID,
            REGIME_PRESETS,
            RegimeConfig,
        )

        for preset in (REGIME_EARLY, REGIME_MID, REGIME_LATE, REGIME_ENDGAME):
            assert isinstance(preset, RegimeConfig)
        assert set(REGIME_PRESETS.keys()) == {"early", "mid", "late", "endgame"}
        assert REGIME_PRESETS["early"] is REGIME_EARLY
        assert REGIME_PRESETS["endgame"] is REGIME_ENDGAME

    def test_regime_preset_values(self):
        """Pins every field of every preset — catches accidental drift."""
        from starsector_optimizer.models import (
            REGIME_EARLY,
            REGIME_ENDGAME,
            REGIME_LATE,
            REGIME_MID,
        )

        assert REGIME_EARLY.name == "early"
        assert REGIME_EARLY.max_hullmod_tier == 1
        assert REGIME_EARLY.exclude_hullmod_tags == frozenset(
            {"no_drop", "no_drop_salvage", "codex_unlockable"}
        )
        assert REGIME_EARLY.exclude_weapon_tags == frozenset(
            {"rare_bp", "codex_unlockable"}
        )

        assert REGIME_MID.name == "mid"
        assert REGIME_MID.max_hullmod_tier == 3
        assert REGIME_MID.exclude_hullmod_tags == frozenset(
            {"no_drop", "no_drop_salvage"}
        )
        assert REGIME_MID.exclude_weapon_tags == frozenset({"rare_bp"})

        assert REGIME_LATE.name == "late"
        assert REGIME_LATE.max_hullmod_tier == 3
        assert REGIME_LATE.exclude_hullmod_tags == frozenset({"no_drop"})
        assert REGIME_LATE.exclude_weapon_tags == frozenset()

        assert REGIME_ENDGAME.name == "endgame"
        assert REGIME_ENDGAME.max_hullmod_tier == 3
        assert REGIME_ENDGAME.exclude_hullmod_tags == frozenset()
        assert REGIME_ENDGAME.exclude_weapon_tags == frozenset()

    def test_optimizer_config_default_regime(self):
        """Default regime = REGIME_EARLY (most conservative component-availability baseline)."""
        from starsector_optimizer.models import REGIME_EARLY
        from starsector_optimizer.optimizer import OptimizerConfig

        cfg = OptimizerConfig()
        assert cfg.regime is REGIME_EARLY
        assert cfg.warm_start_from_regime is None

    def test_regime_endgame_is_unfiltered(self):
        """REGIME_ENDGAME preserves pre-5F behaviour as an opt-in."""
        from starsector_optimizer.models import REGIME_ENDGAME

        assert REGIME_ENDGAME.max_hullmod_tier == 3
        assert REGIME_ENDGAME.exclude_hullmod_tags == frozenset()
        assert REGIME_ENDGAME.exclude_weapon_tags == frozenset()
