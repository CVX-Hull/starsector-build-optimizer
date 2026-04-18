"""Calibration pipeline — diverse build generation and feature extraction."""

from __future__ import annotations

import numpy as np

from .models import REGIME_ENDGAME, Build, GameData, RegimeConfig, ShipHull
from .repair import repair_build
from .scorer import heuristic_score, DEFAULT_WEIGHTS
from .search_space import build_search_space


def generate_random_build(
    hull: ShipHull,
    game_data: GameData,
    rng: np.random.Generator | None = None,
    regime: RegimeConfig = REGIME_ENDGAME,
) -> Build:
    """Generate a random build for a hull, then repair to ensure feasibility.

    Default `regime=REGIME_ENDGAME` preserves the pre-5F unfiltered catalogue
    for callers that were not regime-aware (warm-start random sampling, etc.).
    Pass an explicit regime to sample within a masked component set.
    """
    if rng is None:
        rng = np.random.default_rng()

    space = build_search_space(hull, game_data, regime)

    # Random weapon per slot: 70% fill, 30% empty
    weapons: dict[str, str | None] = {}
    for slot_id, options in space.weapon_options.items():
        if rng.random() < 0.3 or len(options) <= 1:
            weapons[slot_id] = None
        else:
            # Skip "empty" (index 0), pick from actual weapons
            weapons[slot_id] = rng.choice(options[1:])

    # Random hullmods: 20% chance each
    hullmods = frozenset(
        m for m in space.eligible_hullmods
        if rng.random() < 0.2
    )

    # Random vent fraction
    vent_fraction = float(rng.random())

    raw_build = Build(
        hull_id=hull.id,
        weapon_assignments=weapons,
        hullmods=hullmods,
        flux_vents=0,
        flux_capacitors=0,
    )

    return repair_build(raw_build, hull, game_data, vent_fraction=vent_fraction)


def generate_diverse_builds(
    hull: ShipHull,
    game_data: GameData,
    n: int,
    seed: int = 42,
    regime: RegimeConfig = REGIME_ENDGAME,
) -> list[Build]:
    """Generate n diverse, feasible builds under `regime` (default: endgame)."""
    rng = np.random.default_rng(seed)
    builds = []
    for _ in range(n):
        build = generate_random_build(hull, game_data, rng, regime=regime)
        builds.append(build)
    return builds


def compute_build_features(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> dict[str, float]:
    """Extract all heuristic metrics as a flat feature dict."""
    result = heuristic_score(build, hull, game_data)

    n_weapons = sum(1 for v in build.weapon_assignments.values() if v is not None)

    return {
        "total_dps": result.total_dps,
        "kinetic_dps": result.kinetic_dps,
        "he_dps": result.he_dps,
        "energy_dps": result.energy_dps,
        "flux_balance": result.flux_balance,
        "flux_efficiency": result.flux_efficiency,
        "effective_hp": result.effective_hp,
        "armor_ehp": result.armor_ehp,
        "shield_ehp": result.shield_ehp,
        "range_coherence": result.range_coherence,
        "damage_mix": result.damage_mix,
        "engagement_range": result.engagement_range,
        "op_efficiency": result.op_efficiency,
        "composite_score": result.composite_score,
        "n_weapons": float(n_weapons),
        "n_hullmods": float(len(build.hullmods)),
        "vents": float(build.flux_vents),
        "caps": float(build.flux_capacitors),
    }


def calibrate_weights(
    features: dict[str, list[float]],
    scores: list[float],
) -> dict[str, float]:
    """Fit linear regression from features to simulation scores.

    Stub: returns default weights until simulation data is available.
    """
    return dict(DEFAULT_WEIGHTS)
