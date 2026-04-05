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
