"""Tests for heuristic scorer."""

import pytest

from starsector_optimizer.models import (
    Build, HullSize, ShieldType, SlotSize, SlotType, MountType,
    WeaponSlot, ShipHull, Weapon, HullMod, DamageType, GameData,
    WeaponType, ScorerResult,
)
from starsector_optimizer.scorer import heuristic_score


def _hull(hull_size=HullSize.CRUISER, **kw):
    defaults = dict(
        id="test", name="Test", hull_size=hull_size, designation="Cruiser",
        tech_manufacturer="", system_id="", fleet_pts=10, hitpoints=5000,
        armor_rating=500, max_flux=5000, flux_dissipation=300, ordnance_points=100,
        fighter_bays=0, max_speed=60, shield_type=ShieldType.FRONT, shield_arc=270,
        shield_upkeep=0.4, shield_efficiency=0.8, phase_cost=0, phase_upkeep=0,
        peak_cr_sec=480, cr_loss_per_sec=0.25,
        weapon_slots=[
            WeaponSlot("WS1", SlotType.BALLISTIC, SlotSize.MEDIUM, MountType.TURRET, 0, 150, (0, 0)),
            WeaponSlot("WS2", SlotType.ENERGY, SlotSize.SMALL, MountType.TURRET, 0, 150, (0, 0)),
        ],
        built_in_mods=[], built_in_weapons={}, hints=[], tags=[],
    )
    defaults.update(kw)
    return ShipHull(**defaults)


def _weapon(wid, weapon_type=WeaponType.BALLISTIC, damage_type=DamageType.KINETIC,
            dps=200, flux=200, range_=700, op=10, size=SlotSize.MEDIUM, hints=None):
    return Weapon(wid, wid, size, weapon_type, dps, 0, damage_type, 0,
                  flux, 0, range_, op, 0, 1.0, 1, 0, 0, 0, 500, 30,
                  hints or [], [])


def _game_data(weapons=None, hullmods=None):
    return GameData(hulls={}, weapons=weapons or {}, hullmods=hullmods or {})


class TestHeuristicScore:
    def test_returns_scorer_result(self):
        build = Build("test", {}, frozenset(), 10, 5)
        result = heuristic_score(build, _hull(), _game_data())
        assert isinstance(result, ScorerResult)

    def test_empty_build_zero_dps(self):
        build = Build("test", {}, frozenset(), 0, 0)
        result = heuristic_score(build, _hull(), _game_data())
        assert result.total_dps == 0.0

    def test_empty_build_has_ehp(self):
        build = Build("test", {}, frozenset(), 0, 0)
        result = heuristic_score(build, _hull(hitpoints=5000, armor_rating=500), _game_data())
        assert result.effective_hp > 0

    def test_armed_build_scores_higher_than_empty(self):
        gd = _game_data(weapons={
            "w1": _weapon("w1", dps=200, flux=150, range_=700),
        })
        hull = _hull()
        empty = heuristic_score(Build("test", {}, frozenset(), 10, 5), hull, gd)
        armed = heuristic_score(Build("test", {"WS1": "w1"}, frozenset(), 10, 5), hull, gd)
        assert armed.composite_score > empty.composite_score

    def test_flux_balance_under_one(self):
        """Well-built ship should have flux balance < 1."""
        gd = _game_data(weapons={"w1": _weapon("w1", dps=100, flux=100)})
        hull = _hull(flux_dissipation=300)
        build = Build("test", {"WS1": "w1"}, frozenset(), 10, 0)
        result = heuristic_score(build, hull, gd)
        assert result.flux_balance < 1.0

    def test_range_coherence_uniform(self):
        """All weapons same range → coherence ~1.0."""
        gd = _game_data(weapons={
            "w1": _weapon("w1", range_=700),
            "w2": _weapon("w2", range_=700, weapon_type=WeaponType.ENERGY,
                          size=SlotSize.SMALL),
        })
        build = Build("test", {"WS1": "w1", "WS2": "w2"}, frozenset(), 0, 0)
        result = heuristic_score(build, _hull(), gd)
        assert result.range_coherence == pytest.approx(1.0, abs=0.01)

    def test_range_coherence_mixed(self):
        """Mixed ranges → coherence < 1.0."""
        gd = _game_data(weapons={
            "w1": _weapon("w1", range_=400),
            "w2": _weapon("w2", range_=1000, weapon_type=WeaponType.ENERGY,
                          size=SlotSize.SMALL),
        })
        build = Build("test", {"WS1": "w1", "WS2": "w2"}, frozenset(), 0, 0)
        result = heuristic_score(build, _hull(), gd)
        assert result.range_coherence < 0.9

    def test_pd_excluded_from_range_coherence(self):
        """PD weapons should not affect range coherence."""
        gd = _game_data(weapons={
            "w1": _weapon("w1", range_=700),
            "pd": _weapon("pd", range_=300, weapon_type=WeaponType.ENERGY,
                          size=SlotSize.SMALL, hints=["PD"]),
        })
        build = Build("test", {"WS1": "w1", "WS2": "pd"}, frozenset(), 0, 0)
        result = heuristic_score(build, _hull(), gd)
        # With PD excluded, only w1 remains → coherence should be 1.0
        assert result.range_coherence == pytest.approx(1.0, abs=0.01)

    def test_damage_mix_balanced_better(self):
        """Kinetic + HE mix should score better than mono-kinetic."""
        gd_mixed = _game_data(weapons={
            "kin": _weapon("kin", damage_type=DamageType.KINETIC),
            "he": _weapon("he", damage_type=DamageType.HIGH_EXPLOSIVE,
                          weapon_type=WeaponType.ENERGY, size=SlotSize.SMALL),
        })
        gd_mono = _game_data(weapons={
            "k1": _weapon("k1", damage_type=DamageType.KINETIC),
            "k2": _weapon("k2", damage_type=DamageType.KINETIC,
                          weapon_type=WeaponType.ENERGY, size=SlotSize.SMALL),
        })
        hull = _hull()
        mixed = heuristic_score(Build("test", {"WS1": "kin", "WS2": "he"}, frozenset(), 0, 0), hull, gd_mixed)
        mono = heuristic_score(Build("test", {"WS1": "k1", "WS2": "k2"}, frozenset(), 0, 0), hull, gd_mono)
        assert mixed.damage_mix > mono.damage_mix

    def test_effective_stats_present(self):
        build = Build("test", {}, frozenset(), 10, 5)
        result = heuristic_score(build, _hull(), _game_data())
        assert result.effective_stats is not None
        assert result.effective_stats.flux_dissipation > 0

    def test_all_fields_populated(self):
        build = Build("test", {}, frozenset(), 10, 5)
        result = heuristic_score(build, _hull(), _game_data())
        for field_name in ["composite_score", "total_dps", "flux_balance",
                           "flux_efficiency", "effective_hp", "range_coherence",
                           "damage_mix", "engagement_range", "op_efficiency"]:
            assert hasattr(result, field_name)
