"""Variant file generator — Build → .variant JSON."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from .parser import parse_loose_json
from .models import Build, GameData, ShipHull


def assign_weapon_groups(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
) -> list[dict]:
    """Group weapons for the .variant file. Default: all autofire, individual groups."""
    builtin_slots = set(hull.built_in_weapons.keys())
    groups = []
    for slot_id, weapon_id in sorted(build.weapon_assignments.items()):
        if not weapon_id or slot_id in builtin_slots:
            continue
        if weapon_id not in game_data.weapons:
            continue
        groups.append({
            "autofire": True,
            "mode": "LINKED",
            "weapons": {slot_id: weapon_id},
        })
    return groups


def generate_variant(
    build: Build,
    hull: ShipHull,
    game_data: GameData,
    variant_id: str | None = None,
) -> dict:
    """Convert a Build to a .variant JSON dict."""
    if variant_id is None:
        variant_id = f"{build.hull_id}_opt_{secrets.token_hex(4)}"

    return {
        "variantId": variant_id,
        "hullId": build.hull_id,
        "displayName": "Optimizer Build",
        "fluxVents": build.flux_vents,
        "fluxCapacitors": build.flux_capacitors,
        "hullMods": sorted(build.hullmods),
        "permaMods": [],
        "sMods": [],
        "goalVariant": False,
        "weaponGroups": assign_weapon_groups(build, hull, game_data),
        "wings": [],
    }


def write_variant_file(variant: dict, path: Path) -> None:
    """Write a .variant JSON file."""
    path.write_text(json.dumps(variant, indent=4))


def load_variant_file(path: Path) -> dict:
    """Load a .variant file (loose JSON format)."""
    return parse_loose_json(path.read_text())


def variant_to_build(variant: dict, hull_id: str) -> Build:
    """Convert a loaded .variant JSON dict to a Build dataclass.

    The reverse of generate_variant(). Extracts weapon assignments from
    weaponGroups, hullmods from hullMods list, and flux from fluxVents/fluxCapacitors.
    """
    weapons: dict[str, str | None] = {}
    for group in variant.get("weaponGroups", []):
        for slot_id, weapon_id in group.get("weapons", {}).items():
            weapons[slot_id] = weapon_id

    return Build(
        hull_id=hull_id,
        weapon_assignments=weapons,
        hullmods=frozenset(variant.get("hullMods", [])),
        flux_vents=variant.get("fluxVents", 0),
        flux_capacitors=variant.get("fluxCapacitors", 0),
    )


def load_stock_builds(game_dir: Path, hull_id: str) -> list[Build]:
    """Load all stock .variant files for a hull from the game data.

    Searches recursively under game_dir/data/variants/ for {hull_id}_*.variant files.
    """
    variants_dir = game_dir / "data" / "variants"
    if not variants_dir.exists():
        return []

    builds = []
    for path in sorted(variants_dir.rglob(f"{hull_id}_*.variant")):
        try:
            variant = load_variant_file(path)
            builds.append(variant_to_build(variant, hull_id))
        except Exception:
            pass  # Skip malformed variant files
    return builds
