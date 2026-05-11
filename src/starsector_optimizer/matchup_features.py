"""Phase 7 flat feature extraction for contextual matchup surrogates."""

from __future__ import annotations

import json
import logging
import math
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
FEATURE_SCHEMA_VERSION = 2
EMPTY_SENTINEL = "EMPTY"
UNKNOWN_SENTINEL = "UNKNOWN"
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


def _feature_key(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
    )


def _slot_counts(prefix: str, hull: ShipHull) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {}
    for size in SlotSize:
        row[f"{prefix}_slot_{size.value.lower()}_count"] = sum(
            1 for slot in hull.weapon_slots if slot.slot_size == size
        )
    row[f"{prefix}_slot_count"] = len(hull.weapon_slots)
    row[f"{prefix}_slot_arc_mean"] = _safe_mean(slot.arc for slot in hull.weapon_slots)
    return row


def _slot_geometry_scale(hull: ShipHull) -> tuple[float, float]:
    max_abs_x = max((abs(slot.position[0]) for slot in hull.weapon_slots), default=1.0)
    max_abs_y = max((abs(slot.position[1]) for slot in hull.weapon_slots), default=1.0)
    return max(max_abs_x, 1.0), max(max_abs_y, 1.0)


def _slot_feature_row(
    prefix: str,
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {}
    x_scale, y_scale = _slot_geometry_scale(hull)
    for idx, slot in enumerate(sorted(hull.weapon_slots, key=lambda item: item.id)):
        slot_prefix = f"{prefix}_slot_{idx:02d}"
        assigned_weapon_id = build.weapon_assignments.get(slot.id)
        if assigned_weapon_id is None and slot.id in hull.built_in_weapons:
            assigned_weapon_id = hull.built_in_weapons[slot.id]
        weapon = game_data.weapons.get(assigned_weapon_id) if assigned_weapon_id else None
        weapon_id = assigned_weapon_id if assigned_weapon_id else EMPTY_SENTINEL
        if assigned_weapon_id and weapon is None:
            weapon_id = UNKNOWN_SENTINEL
        angle_radians = math.radians(slot.angle)
        x, y = slot.position
        row.update({
            f"{slot_prefix}_slot_id": slot.id,
            f"{slot_prefix}_slot_type": slot.slot_type.value,
            f"{slot_prefix}_slot_size": slot.slot_size.value,
            f"{slot_prefix}_mount_type": slot.mount_type.value,
            f"{slot_prefix}_angle_degrees": slot.angle,
            f"{slot_prefix}_angle_sin": math.sin(angle_radians),
            f"{slot_prefix}_angle_cos": math.cos(angle_radians),
            f"{slot_prefix}_arc_degrees": slot.arc,
            f"{slot_prefix}_arc_fraction": slot.arc / 360.0,
            f"{slot_prefix}_x": x,
            f"{slot_prefix}_y": y,
            f"{slot_prefix}_x_norm": x / x_scale,
            f"{slot_prefix}_y_norm": y / y_scale,
            f"{slot_prefix}_forward_projection": math.cos(angle_radians),
            f"{slot_prefix}_weapon_id": weapon_id,
            f"{slot_prefix}_weapon_known": int(weapon is not None),
            f"{slot_prefix}_weapon_type": (
                weapon.weapon_type.value if weapon else UNKNOWN_SENTINEL if assigned_weapon_id else EMPTY_SENTINEL
            ),
            f"{slot_prefix}_weapon_size": (
                weapon.size.value if weapon else UNKNOWN_SENTINEL if assigned_weapon_id else EMPTY_SENTINEL
            ),
            f"{slot_prefix}_damage_type": (
                weapon.damage_type.value if weapon else UNKNOWN_SENTINEL if assigned_weapon_id else EMPTY_SENTINEL
            ),
            f"{slot_prefix}_weapon_op": weapon.op_cost if weapon else 0,
            f"{slot_prefix}_weapon_dps": weapon.sustained_dps if weapon else 0.0,
            f"{slot_prefix}_weapon_flux": weapon.sustained_flux if weapon else 0.0,
            f"{slot_prefix}_weapon_range": weapon.range if weapon else 0.0,
            f"{slot_prefix}_weapon_ammo": weapon.ammo if weapon else 0,
            f"{slot_prefix}_weapon_proj_speed": weapon.proj_speed if weapon else 0.0,
            f"{slot_prefix}_weapon_turn_rate": weapon.turn_rate if weapon else 0.0,
            f"{slot_prefix}_weapon_is_pd": int(weapon.is_pd) if weapon else 0,
            f"{slot_prefix}_weapon_is_beam": int(weapon.is_beam) if weapon else 0,
        })
    return row


def _hullmod_feature_row(prefix: str, build: Build, hull: ShipHull, game_data: GameData) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {}
    unknown_count = 0
    for hullmod_id in sorted(build.hullmods):
        hullmod = game_data.hullmods.get(hullmod_id)
        if hullmod is None:
            unknown_count += 1
            continue
        row[f"{prefix}_hullmod__{_feature_key(hullmod_id)}"] = 1
        row[f"{prefix}_hullmod_tier_{hullmod.tier}_count"] = (
            int(row.get(f"{prefix}_hullmod_tier_{hullmod.tier}_count", 0)) + 1
        )
        for tag in hullmod.tags:
            row[f"{prefix}_hullmod_tag__{_feature_key(tag)}_count"] = (
                int(row.get(f"{prefix}_hullmod_tag__{_feature_key(tag)}_count", 0)) + 1
            )
        for tag in hullmod.ui_tags:
            row[f"{prefix}_hullmod_ui_tag__{_feature_key(tag)}_count"] = (
                int(row.get(f"{prefix}_hullmod_ui_tag__{_feature_key(tag)}_count", 0)) + 1
            )
    row[f"{prefix}_unknown_hullmod_count"] = unknown_count
    row[f"{prefix}_builtin_hullmod_count"] = len(hull.built_in_mods)
    return row


def _small_weapon_composition(prefix: str, build: Build, hull: ShipHull, game_data: GameData) -> dict[str, FeatureValue]:
    small_slots = tuple(slot for slot in hull.weapon_slots if slot.slot_size == SlotSize.SMALL)
    resolved_weapon_ids = tuple(
        build.weapon_assignments.get(slot.id) or hull.built_in_weapons.get(slot.id)
        for slot in small_slots
    )
    weapons = tuple(game_data.weapons[wid] for wid in resolved_weapon_ids if wid in game_data.weapons)
    row = _weapon_aggregate(f"{prefix}_small", weapons)
    row[f"{prefix}_small_empty_count"] = sum(
        1 for wid in resolved_weapon_ids if wid is None
    )
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
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "build_hull_id": build.hull_id,
        "build_hull_size": hull.hull_size.value,
        "build_hull_designation": hull.designation,
        "build_hull_tech_manufacturer": hull.tech_manufacturer,
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
    row.update(_slot_feature_row("build", build, hull, game_data))
    row.update(_hullmod_feature_row("build", build, hull, game_data))
    row.update(_small_weapon_composition("build", build, hull, game_data))
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
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "opponent_variant_id": variant_id,
        "opponent_hull_id": hull.id,
        "opponent_hull_size": hull.hull_size.value,
        "opponent_hull_designation": hull.designation,
        "opponent_hull_tech_manufacturer": hull.tech_manufacturer,
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
    row.update(_slot_feature_row("opponent", build, hull, game_data))
    row.update(_hullmod_feature_row("opponent", build, hull, game_data))
    row.update(_small_weapon_composition("opponent", build, hull, game_data))
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
        "interaction_small_pd_vs_missile": (
            float(row["build_small_pd_count"]) / max(float(row["opponent_missile_count"]), 1.0)
        ),
        "interaction_small_range_delta": (
            float(row["build_small_range_mean"]) - float(row["opponent_range_mean"])
        ),
        "interaction_weapon_flux_pressure_delta": (
            float(row["build_weapon_flux"]) - float(row["opponent_weapon_flux"])
        ),
    })
    return row
