"""Phase 7 flat feature extraction for contextual matchup surrogates."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import mean, pstdev
from typing import Iterable

from .calibration import compute_build_features
from .game_manifest import GameManifest
from .models import (
    Build,
    DamageType,
    GameData,
    ShipHull,
    SlotSize,
    Weapon,
)
from .variant import load_variant_file, variant_to_build


FeatureValue = float | int | str
logger = logging.getLogger(__name__)


def _damage_key(damage_type: DamageType) -> str:
    return damage_type.value.lower().replace("high_explosive", "he")


def _weapons_for_build(build: Build, game_data: GameData) -> tuple[Weapon, ...]:
    return tuple(
        game_data.weapons[wid]
        for wid in build.weapon_assignments.values()
        if wid is not None and wid in game_data.weapons
    )


def _safe_mean(values: Iterable[float]) -> float:
    items = list(values)
    return mean(items) if items else 0.0


def _safe_std(values: Iterable[float]) -> float:
    items = list(values)
    return pstdev(items) if len(items) > 1 else 0.0


def _weapon_aggregate(prefix: str, weapons: tuple[Weapon, ...]) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {
        f"{prefix}_weapon_count": len(weapons),
        f"{prefix}_weapon_op": sum(w.op_cost for w in weapons),
        f"{prefix}_weapon_flux": sum(w.sustained_flux for w in weapons),
        f"{prefix}_total_dps": sum(w.sustained_dps for w in weapons),
        f"{prefix}_emp": sum(w.emp for w in weapons),
        f"{prefix}_pd_count": sum(1 for w in weapons if w.is_pd),
        f"{prefix}_missile_count": sum(1 for w in weapons if w.weapon_type.value == "MISSILE"),
        f"{prefix}_beam_count": sum(1 for w in weapons if w.is_beam),
        f"{prefix}_range_mean": _safe_mean(w.range for w in weapons),
        f"{prefix}_range_min": min((w.range for w in weapons), default=0.0),
        f"{prefix}_range_max": max((w.range for w in weapons), default=0.0),
        f"{prefix}_range_std": _safe_std(w.range for w in weapons),
    }
    for damage_type in DamageType:
        key = _damage_key(damage_type)
        row[f"{prefix}_damage_{key}_dps"] = sum(
            w.sustained_dps for w in weapons if w.damage_type == damage_type
        )
    return row


def _slot_counts(prefix: str, hull: ShipHull) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {}
    for size in SlotSize:
        row[f"{prefix}_slot_{size.value.lower()}_count"] = sum(
            1 for slot in hull.weapon_slots if slot.slot_size == size
        )
    row[f"{prefix}_slot_count"] = len(hull.weapon_slots)
    row[f"{prefix}_slot_arc_mean"] = _safe_mean(slot.arc for slot in hull.weapon_slots)
    return row


def _find_variant_path(variant_id: str, game_dir: Path) -> Path:
    variants_dir = game_dir / "data" / "variants"
    direct = variants_dir / f"{variant_id}.variant"
    if direct.exists():
        return direct
    for path in variants_dir.rglob(f"{variant_id}.variant"):
        return path
    for path in variants_dir.rglob("*.variant"):
        try:
            data = load_variant_file(path)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("failed to parse variant while searching %s: %s", path, exc)
            continue
        if data.get("variantId") == variant_id:
            return path
    raise FileNotFoundError(f"opponent variant {variant_id!r} not found under {variants_dir}")


def build_feature_row(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
    manifest: GameManifest,
) -> dict[str, FeatureValue]:
    """Return flat player-build features for Phase 7 baseline models."""
    weapons = _weapons_for_build(build, game_data)
    known_weapon_ids = {w.id for w in weapons}
    unknown_weapon_count = sum(
        1 for wid in build.weapon_assignments.values()
        if wid is not None and wid not in known_weapon_ids
    )
    row: dict[str, FeatureValue] = {
        "build_hull_id": build.hull_id,
        "build_hull_size": hull.hull_size.value,
        "build_hull_op": hull.ordnance_points,
        "build_hull_hitpoints": hull.hitpoints,
        "build_hull_armor": hull.armor_rating,
        "build_hull_flux": hull.max_flux,
        "build_hull_dissipation": hull.flux_dissipation,
        "build_hull_speed": hull.max_speed,
        "build_hull_shield_type": hull.shield_type.value,
        "build_hull_shield_arc": hull.shield_arc,
        "build_hull_shield_efficiency": hull.shield_efficiency,
        "build_fighter_bays": hull.fighter_bays,
        "build_flux_vents": build.flux_vents,
        "build_flux_capacitors": build.flux_capacitors,
        "build_flux_from_vents": build.flux_vents * manifest.constants.dissipation_per_vent,
        "build_flux_from_capacitors": build.flux_capacitors * manifest.constants.flux_per_capacitor,
        "build_hullmod_count": len(build.hullmods),
        "build_hullmod_op": sum(
            game_data.hullmods[hid].op_cost(hull.hull_size)
            for hid in build.hullmods if hid in game_data.hullmods
        ),
        "build_builtin_hullmod_overlap": len(set(build.hullmods) & set(hull.built_in_mods)),
        "build_empty_slot_count": sum(1 for wid in build.weapon_assignments.values() if wid is None),
        "build_unknown_weapon_count": unknown_weapon_count,
    }
    row.update(_slot_counts("build", hull))
    row.update(_weapon_aggregate("build", weapons))
    for key, value in compute_build_features(build, hull, game_data).items():
        row[f"build_scorer_{key}"] = value
    return row


def opponent_feature_row(
    variant_id: str,
    game_dir: Path,
    game_data: GameData,
) -> dict[str, FeatureValue]:
    """Return flat opponent features from a stock variant id."""
    path = _find_variant_path(variant_id, game_dir)
    try:
        data = load_variant_file(path)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise ValueError(f"opponent variant {variant_id!r} at {path} is malformed: {exc}") from exc
    hull_id = data.get("hullId")
    if hull_id not in game_data.hulls:
        raise ValueError(f"opponent variant {variant_id!r} references unknown hull {hull_id!r}")
    hull = game_data.hulls[hull_id]
    build = variant_to_build(data, hull_id)
    weapons = _weapons_for_build(build, game_data)
    row: dict[str, FeatureValue] = {
        "opponent_variant_id": variant_id,
        "opponent_hull_id": hull.id,
        "opponent_hull_size": hull.hull_size.value,
        "opponent_hull_hitpoints": hull.hitpoints,
        "opponent_hull_armor": hull.armor_rating,
        "opponent_hull_flux": hull.max_flux,
        "opponent_hull_dissipation": hull.flux_dissipation,
        "opponent_hull_speed": hull.max_speed,
        "opponent_hull_shield_type": hull.shield_type.value,
        "opponent_hull_shield_efficiency": hull.shield_efficiency,
        "opponent_hull_shield_arc": hull.shield_arc,
        "opponent_fighter_bays": hull.fighter_bays,
        "opponent_hullmod_count": len(build.hullmods),
    }
    row.update(_slot_counts("opponent", hull))
    row.update(_weapon_aggregate("opponent", weapons))
    return row


def matchup_feature_row(
    build: Build,
    opponent_variant_id: str,
    game_dir: Path,
    game_data: GameData,
    manifest: GameManifest,
) -> dict[str, FeatureValue]:
    """Return combined player/opponent features plus simple interactions."""
    if build.hull_id not in game_data.hulls:
        raise ValueError(f"build references unknown hull {build.hull_id!r}")
    hull = game_data.hulls[build.hull_id]
    row = build_feature_row(build, hull, game_data, manifest)
    opponent = opponent_feature_row(opponent_variant_id, game_dir, game_data)
    row.update(opponent)
    row.update({
        "interaction_range_delta": float(row["build_range_mean"]) - float(row["opponent_range_mean"]),
        "interaction_speed_delta": float(row["build_hull_speed"]) - float(row["opponent_hull_speed"]),
        "interaction_armor_delta": float(row["build_hull_armor"]) - float(row["opponent_hull_armor"]),
        "interaction_flux_delta": float(row["build_hull_flux"]) - float(row["opponent_hull_flux"]),
        "interaction_shield_efficiency_delta": (
            float(row["opponent_hull_shield_efficiency"])
            - float(row["build_hull_shield_efficiency"])
        ),
        "interaction_kinetic_vs_shield": (
            float(row["build_damage_kinetic_dps"])
            / max(float(row["opponent_hull_shield_efficiency"]), 1.0)
        ),
        "interaction_he_vs_armor": (
            float(row["build_damage_he_dps"])
            / max(float(row["opponent_hull_armor"]), 1.0)
        ),
        "interaction_pd_vs_missile": (
            float(row["build_pd_count"]) / max(float(row["opponent_missile_count"]), 1.0)
        ),
    })
    return row
