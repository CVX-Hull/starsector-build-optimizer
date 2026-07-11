"""Phase 7 flat feature extraction for contextual matchup surrogates."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from statistics import mean, pstdev
from typing import Iterable, Mapping

from .calibration import compute_build_features
from .game_manifest import GameManifest
from .models import (
    Build,
    DamageType,
    GameData,
    ShipHull,
    SlotType,
    SlotSize,
    Weapon,
)
from .variant import load_variant_file, variant_to_build


FeatureValue = float | int | str
FEATURE_SCHEMA_VERSION = 4
EMPTY_SENTINEL = "EMPTY"
UNKNOWN_SENTINEL = "UNKNOWN"
DEFAULT_FEATURE_PROFILE = "all"
FEATURE_PROFILES = (
    "all",
    "aggregate",
    "geometry",
    "opponent-parity",
    "sparse-component",
    "sparse-cross",
)
PROFILE_AGGREGATE_EXCLUDE_PARTS = (
    "_slot_",
    "_hullmod__",
)
PROFILE_GEOMETRY_INCLUDE_PARTS = (
    "_geometry_",
    "_arc_",
    "_angle_",
    "_x_norm",
    "_y_norm",
    "_forward_projection",
    "_lateral_offset",
    "_longitudinal_offset",
)
PROFILE_SPARSE_COMPONENT_INCLUDE_PARTS = (
    "_hullmod__",
    "_weapon_id",
    "_slot_id",
    "_slot_type",
    "_slot_size",
    "_mount_type",
    "_hull_id",
    "_variant_id",
)
PROFILE_SPARSE_CROSS_INCLUDE_PREFIXES = (
    "interaction_",
)
ARC_BUCKET_FRONT = "front"
ARC_BUCKET_AFT = "aft"
ARC_BUCKET_PORT = "port"
ARC_BUCKET_STARBOARD = "starboard"
ARC_BUCKETS = (
    ARC_BUCKET_FRONT,
    ARC_BUCKET_AFT,
    ARC_BUCKET_PORT,
    ARC_BUCKET_STARBOARD,
)
ARC_DIRECTION_COS_THRESHOLD = 0.5
ARC_DIRECTION_SIN_THRESHOLD = 0.5
GEOMETRY_SCALE_FLOOR = 1.0
PER_SLOT_FEATURE_RE = re.compile(r"^(build|opponent)_slot_\d{2}_")
logger = logging.getLogger(__name__)


def _damage_key(damage_type: DamageType) -> str:
    return damage_type.value.lower().replace("high_explosive", "he")


def _resolved_weapon_ids(build: Build, hull: ShipHull) -> tuple[str, ...]:
    ids: list[str] = []
    for slot in hull.weapon_slots:
        weapon_id = build.weapon_assignments.get(slot.id)
        if weapon_id is None:
            weapon_id = hull.built_in_weapons.get(slot.id)
        if weapon_id:
            ids.append(weapon_id)
    return tuple(ids)


def _weapons_for_build(build: Build, hull: ShipHull, game_data: GameData) -> tuple[Weapon, ...]:
    return tuple(
        game_data.weapons[wid]
        for wid in _resolved_weapon_ids(build, hull)
        if wid in game_data.weapons
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
    _add_tag_count_features(row, f"{prefix}_weapon_hint", tuple(tag for w in weapons for tag in w.hints))
    _add_tag_count_features(row, f"{prefix}_weapon_tag", tuple(tag for w in weapons for tag in w.tags))
    return row


def _add_tag_count_features(row: dict[str, FeatureValue], prefix: str, tags: Iterable[str]) -> None:
    for tag in tags:
        row[f"{prefix}__{_feature_key(tag)}_count"] = (
            int(row.get(f"{prefix}__{_feature_key(tag)}_count", 0)) + 1
        )


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
    width_scale = hull.geometry.width / 2.0 if hull.geometry.width > 0 else max_abs_x
    height_scale = hull.geometry.height / 2.0 if hull.geometry.height > 0 else max_abs_y
    return max(width_scale, GEOMETRY_SCALE_FLOOR), max(height_scale, GEOMETRY_SCALE_FLOOR)


def _hull_geometry_features(prefix: str, hull: ShipHull) -> dict[str, FeatureValue]:
    geometry = hull.geometry
    engine_slots = geometry.engine_slots
    return {
        f"{prefix}_hull_system_id": hull.system_id,
        f"{prefix}_hull_phase_cost": hull.phase_cost,
        f"{prefix}_hull_phase_upkeep": hull.phase_upkeep,
        f"{prefix}_hull_peak_cr_sec": hull.peak_cr_sec,
        f"{prefix}_hull_cr_loss_per_sec": hull.cr_loss_per_sec,
        f"{prefix}_geometry_width": geometry.width,
        f"{prefix}_geometry_height": geometry.height,
        f"{prefix}_geometry_collision_radius": geometry.collision_radius,
        f"{prefix}_geometry_center_x": geometry.center[0],
        f"{prefix}_geometry_center_y": geometry.center[1],
        f"{prefix}_geometry_shield_center_x": geometry.shield_center[0],
        f"{prefix}_geometry_shield_center_y": geometry.shield_center[1],
        f"{prefix}_geometry_shield_radius": geometry.shield_radius,
        f"{prefix}_geometry_style": geometry.style,
        f"{prefix}_geometry_engine_slot_count": len(engine_slots),
        f"{prefix}_geometry_engine_width_sum": sum(slot.width for slot in engine_slots),
        f"{prefix}_geometry_engine_length_mean": _safe_mean(slot.length for slot in engine_slots),
    }


def _arc_bucket(angle_degrees: float) -> str:
    radians = math.radians(angle_degrees)
    cos_value = math.cos(radians)
    sin_value = math.sin(radians)
    if cos_value >= ARC_DIRECTION_COS_THRESHOLD:
        return ARC_BUCKET_FRONT
    if cos_value <= -ARC_DIRECTION_COS_THRESHOLD:
        return ARC_BUCKET_AFT
    if sin_value >= ARC_DIRECTION_SIN_THRESHOLD:
        return ARC_BUCKET_PORT
    return ARC_BUCKET_STARBOARD


def _arc_pressure_features(
    prefix: str,
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> dict[str, FeatureValue]:
    row: dict[str, FeatureValue] = {}
    for bucket in ARC_BUCKETS:
        row[f"{prefix}_arc_{bucket}_slot_count"] = 0
        row[f"{prefix}_arc_{bucket}_weapon_dps"] = 0.0
        row[f"{prefix}_arc_{bucket}_weapon_range_weighted_dps"] = 0.0
        row[f"{prefix}_arc_{bucket}_pd_count"] = 0
    for slot in hull.weapon_slots:
        bucket = _arc_bucket(slot.angle)
        row[f"{prefix}_arc_{bucket}_slot_count"] = int(row[f"{prefix}_arc_{bucket}_slot_count"]) + 1
        assigned_weapon_id = build.weapon_assignments.get(slot.id) or hull.built_in_weapons.get(slot.id)
        weapon = game_data.weapons.get(assigned_weapon_id) if assigned_weapon_id else None
        if weapon is None:
            continue
        arc_weight = max(slot.arc / 360.0, 0.0)
        row[f"{prefix}_arc_{bucket}_weapon_dps"] = float(row[f"{prefix}_arc_{bucket}_weapon_dps"]) + weapon.sustained_dps
        row[f"{prefix}_arc_{bucket}_weapon_range_weighted_dps"] = (
            float(row[f"{prefix}_arc_{bucket}_weapon_range_weighted_dps"])
            + weapon.sustained_dps * weapon.range * arc_weight
        )
        row[f"{prefix}_arc_{bucket}_pd_count"] = int(row[f"{prefix}_arc_{bucket}_pd_count"]) + int(weapon.is_pd)
    row[f"{prefix}_arc_broadside_weapon_dps"] = (
        float(row[f"{prefix}_arc_port_weapon_dps"]) + float(row[f"{prefix}_arc_starboard_weapon_dps"])
    )
    row[f"{prefix}_arc_frontal_weapon_dps"] = float(row[f"{prefix}_arc_front_weapon_dps"])
    row[f"{prefix}_arc_aft_weapon_dps"] = float(row[f"{prefix}_arc_aft_weapon_dps"])
    return row


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
            f"{slot_prefix}_arc_bucket": _arc_bucket(slot.angle),
            f"{slot_prefix}_x": x,
            f"{slot_prefix}_y": y,
            f"{slot_prefix}_x_norm": x / x_scale,
            f"{slot_prefix}_y_norm": y / y_scale,
            f"{slot_prefix}_forward_projection": math.cos(angle_radians),
            f"{slot_prefix}_lateral_offset": abs(x) / x_scale,
            f"{slot_prefix}_longitudinal_offset": y / y_scale,
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


def _wing_aggregate(prefix: str, wing_ids: Iterable[str], game_data: GameData) -> dict[str, FeatureValue]:
    if not isinstance(wing_ids, tuple | list):
        raise ValueError(f"{prefix} wings must be a list")
    resolved = tuple(game_data.wings[wing_id] for wing_id in wing_ids if wing_id in game_data.wings)
    row: dict[str, FeatureValue] = {
        f"{prefix}_wing_count": len(resolved),
        f"{prefix}_wing_op": sum(wing.op_cost for wing in resolved),
        f"{prefix}_wing_fleet_points": sum(wing.fleet_points for wing in resolved),
        f"{prefix}_wing_size": sum(wing.num for wing in resolved),
        f"{prefix}_wing_range_mean": _safe_mean(wing.range for wing in resolved),
        f"{prefix}_wing_attack_run_range_mean": _safe_mean(wing.attack_run_range for wing in resolved),
        f"{prefix}_wing_refit_mean": _safe_mean(wing.refit for wing in resolved),
        f"{prefix}_unknown_wing_count": sum(1 for wing_id in wing_ids if wing_id not in game_data.wings),
    }
    _add_tag_count_features(row, f"{prefix}_wing_role", tuple(wing.role for wing in resolved if wing.role))
    _add_tag_count_features(row, f"{prefix}_wing_tag", tuple(tag for wing in resolved for tag in wing.tags))
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
    weapons = _weapons_for_build(build, hull, game_data)
    known_weapon_ids = {w.id for w in weapons}
    unknown_weapon_count = sum(
        1 for wid in _resolved_weapon_ids(build, hull)
        if wid is not None and wid not in known_weapon_ids
    )
    row: dict[str, FeatureValue] = {
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
    row.update(_hull_geometry_features("build", hull))
    row.update(_slot_counts("build", hull))
    row.update(_slot_feature_row("build", build, hull, game_data))
    row.update(_arc_pressure_features("build", build, hull, game_data))
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
    if "wings" in data and not isinstance(data["wings"], list):
        raise ValueError(f"opponent variant {variant_id!r} has malformed wings field")
    weapons = _weapons_for_build(build, hull, game_data)
    row: dict[str, FeatureValue] = {
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
        "opponent_flux_vents": build.flux_vents,
        "opponent_flux_capacitors": build.flux_capacitors,
        "opponent_hullmod_count": len(build.hullmods),
        "opponent_hullmod_op": sum(
            game_data.hullmods[hid].op_cost(hull.hull_size)
            for hid in build.hullmods if hid in game_data.hullmods
        ),
        "opponent_builtin_hullmod_overlap": len(set(build.hullmods) & set(hull.built_in_mods)),
        "opponent_empty_slot_count": sum(1 for wid in build.weapon_assignments.values() if wid is None),
        "opponent_unknown_weapon_count": sum(
            1 for wid in _resolved_weapon_ids(build, hull)
            if wid is not None and wid not in game_data.weapons
        ),
    }
    row.update(_hull_geometry_features("opponent", hull))
    row.update(_slot_counts("opponent", hull))
    row.update(_slot_feature_row("opponent", build, hull, game_data))
    row.update(_arc_pressure_features("opponent", build, hull, game_data))
    row.update(_hullmod_feature_row("opponent", build, hull, game_data))
    row.update(_small_weapon_composition("opponent", build, hull, game_data))
    row.update(_weapon_aggregate("opponent", weapons))
    row.update(_wing_aggregate("opponent", data.get("wings", ()), game_data))
    for key, value in compute_build_features(build, hull, game_data).items():
        row[f"opponent_scorer_{key}"] = value
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
        "interaction_front_dps_delta": (
            float(row["build_arc_front_weapon_dps"]) - float(row["opponent_arc_front_weapon_dps"])
        ),
        "interaction_broadside_dps_delta": (
            float(row["build_arc_broadside_weapon_dps"]) - float(row["opponent_arc_broadside_weapon_dps"])
        ),
        "interaction_pd_arc_vs_missile": (
            (
                float(row["build_arc_front_pd_count"])
                + float(row["build_arc_port_pd_count"])
                + float(row["build_arc_starboard_pd_count"])
            )
            / max(float(row["opponent_missile_count"]), 1.0)
        ),
        "interaction_wing_pressure_vs_pd": (
            float(row["opponent_wing_count"]) / max(float(row["build_pd_count"]), 1.0)
        ),
    })
    return row


def filter_feature_profile(
    row: Mapping[str, FeatureValue],
    feature_profile: str = DEFAULT_FEATURE_PROFILE,
) -> dict[str, FeatureValue]:
    """Return a deterministic subset of a feature row for ablation profiles."""
    if feature_profile not in FEATURE_PROFILES:
        raise ValueError(f"unknown feature profile {feature_profile!r}")
    if feature_profile == "all":
        return dict(row)
    if feature_profile == "aggregate":
        return {
            key: value for key, value in row.items()
            if not _is_sparse_component_key(key)
            and not key.startswith("interaction_")
        }
    if feature_profile == "geometry":
        return {
            key: value for key, value in row.items()
            if not _is_sparse_component_key(key)
            or any(part in key for part in PROFILE_GEOMETRY_INCLUDE_PARTS)
        }
    if feature_profile == "opponent-parity":
        return {
            key: value for key, value in row.items()
            if key.startswith(("build_", "opponent_"))
            and not _is_sparse_component_key(key)
            and not key.startswith("interaction_")
        }
    if feature_profile == "sparse-component":
        return {
            key: value for key, value in row.items()
            if _is_sparse_component_key(key)
            or (
                not _is_sparse_component_key(key)
                and not key.startswith("interaction_")
            )
        }
    return {
        key: value for key, value in row.items()
        if key.startswith(PROFILE_SPARSE_CROSS_INCLUDE_PREFIXES)
        or _is_sparse_component_key(key)
        or not _is_sparse_component_key(key)
    }


def _is_sparse_component_key(key: str) -> bool:
    return (
        PER_SLOT_FEATURE_RE.match(key) is not None
        or any(part in key for part in PROFILE_SPARSE_COMPONENT_INCLUDE_PARTS)
    )
