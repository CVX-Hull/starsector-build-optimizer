"""Game data parser for Starsector CSV and loose JSON files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from .hullmod_effects import validate_registry
from .models import (
    DamageType,
    GameData,
    HullMod,
    HullSize,
    ShieldType,
    ShipHull,
    SlotSize,
    SlotType,
    MountType,
    Weapon,
    WeaponSlot,
    WeaponType,
)

logger = logging.getLogger(__name__)


def parse_loose_json(text: str) -> dict:
    """Parse Starsector's loose JSON format (comments, trailing commas)."""
    text = re.sub(r"#.*", "", text)
    text = re.sub(r"//.*", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def extract_wpn_metadata(path: Path) -> dict | None:
    """Extract id, type, size from a .wpn file via regex (full JSON parse unreliable)."""
    text = path.read_text()
    id_m = re.search(r'"id"\s*:\s*"([^"]+)"', text)
    type_m = re.search(r'"type"\s*:\s*"([^"]+)"', text)
    size_m = re.search(r'"size"\s*:\s*"([^"]+)"', text)
    if id_m and type_m and size_m:
        return {
            "id": id_m.group(1),
            "type": type_m.group(1),
            "size": size_m.group(1),
        }
    return None


def _safe_float(val, default: float = 0.0) -> float:
    try:
        if pd.isna(val) or val == "":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 0) -> int:
    try:
        if pd.isna(val) or val == "":
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _parse_tags(val) -> list[str]:
    if pd.isna(val) or not isinstance(val, str) or val.strip() == "":
        return []
    return [t.strip().strip('"') for t in val.split(",") if t.strip()]


def parse_ship_csv(csv_path: Path) -> list[ShipHull]:
    """Parse ship_data.csv into ShipHull objects.

    hull_size is initially set from the designation column as a best guess,
    but will be overridden by the authoritative hullSize from .ship files
    during merge_ship_hull_data.
    """
    df = pd.read_csv(csv_path, comment="#", dtype=str, keep_default_na=False)
    hulls = []
    for _, row in df.iterrows():
        sid = row.get("id", "").strip()
        if not sid:
            continue
        # Try to get hull_size from designation; will be overridden by .ship file
        hull_size = HullSize.from_str(row.get("designation", ""))
        if hull_size is None:
            hull_size = HullSize.FRIGATE  # placeholder, overridden by .ship merge
        shield_type = ShieldType.from_str(row.get("shield type", "NONE"))
        if shield_type is None:
            shield_type = ShieldType.NONE

        hulls.append(ShipHull(
            id=row.get("id", "").strip(),
            name=row.get("name", "").strip(),
            hull_size=hull_size,
            designation=row.get("designation", "").strip(),
            tech_manufacturer=row.get("tech/manufacturer", "").strip(),
            system_id=row.get("system id", "").strip(),
            fleet_pts=_safe_int(row.get("fleet pts")),
            hitpoints=_safe_float(row.get("hitpoints")),
            armor_rating=_safe_float(row.get("armor rating")),
            max_flux=_safe_float(row.get("max flux")),
            flux_dissipation=_safe_float(row.get("flux dissipation")),
            ordnance_points=_safe_int(row.get("ordnance points")),
            fighter_bays=_safe_int(row.get("fighter bays")),
            max_speed=_safe_float(row.get("max speed")),
            shield_type=shield_type,
            shield_arc=_safe_float(row.get("shield arc")),
            shield_upkeep=_safe_float(row.get("shield upkeep")),
            shield_efficiency=_safe_float(row.get("shield efficiency")),
            phase_cost=_safe_float(row.get("phase cost")),
            phase_upkeep=_safe_float(row.get("phase upkeep")),
            peak_cr_sec=_safe_float(row.get("peak CR sec")),
            cr_loss_per_sec=_safe_float(row.get("CR loss/sec")),
            hints=_parse_tags(row.get("hints")),
            tags=_parse_tags(row.get("tags")),
        ))
    return hulls


def parse_ship_file(path: Path) -> dict:
    """Parse a .ship loose JSON file for weapon slots and built-in data."""
    data = parse_loose_json(path.read_text())
    return {
        "hullId": data.get("hullId", ""),
        "hullSize": data.get("hullSize", ""),
        "weaponSlots": data.get("weaponSlots", []),
        "builtInMods": data.get("builtInMods", []),
        "builtInWeapons": data.get("builtInWeapons", {}),
    }


def merge_ship_hull_data(hulls: list[ShipHull], ship_dir: Path) -> list[ShipHull]:
    """Enrich ShipHull objects with weapon slot data from .ship files."""
    for hull in hulls:
        ship_path = ship_dir / f"{hull.id}.ship"
        if not ship_path.exists():
            continue
        try:
            ship_data = parse_ship_file(ship_path)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse %s: %s", ship_path, e)
            continue

        # Override hull_size from .ship file (authoritative)
        hs = HullSize.from_str(ship_data.get("hullSize", ""))
        if hs is not None:
            hull.hull_size = hs

        # Parse weapon slots
        slots = []
        for ws in ship_data.get("weaponSlots", []):
            st = SlotType.from_str(ws.get("type", ""))
            ss = SlotSize.from_str(ws.get("size", ""))
            mt = MountType.from_str(ws.get("mount", ""))
            if st is None or ss is None or mt is None:
                logger.debug("Skipping weapon slot %s in %s (unknown type/size/mount)",
                               ws.get("id"), hull.id)
                continue
            loc = ws.get("locations", [0, 0])
            slots.append(WeaponSlot(
                id=ws.get("id", ""),
                slot_type=st,
                slot_size=ss,
                mount_type=mt,
                angle=float(ws.get("angle", 0)),
                arc=float(ws.get("arc", 0)),
                position=(float(loc[0]) if len(loc) > 0 else 0.0,
                          float(loc[1]) if len(loc) > 1 else 0.0),
            ))
        hull.weapon_slots = slots
        hull.built_in_mods = ship_data.get("builtInMods", [])
        hull.built_in_weapons = ship_data.get("builtInWeapons", {})

    return hulls


_DAMAGE_TYPE_TO_WEAPON_TYPE: dict[str, WeaponType] = {
    "KINETIC": WeaponType.BALLISTIC,
    "HIGH_EXPLOSIVE": WeaponType.BALLISTIC,
    "FRAGMENTATION": WeaponType.BALLISTIC,
    "ENERGY": WeaponType.ENERGY,
}


def parse_weapon_csv(csv_path: Path, wpn_dir: Path) -> list[Weapon]:
    """Parse weapon_data.csv + .wpn files into Weapon objects."""
    # First, build wpn metadata lookup
    wpn_meta: dict[str, dict] = {}
    if wpn_dir.exists():
        for wpn_path in wpn_dir.glob("*.wpn"):
            meta = extract_wpn_metadata(wpn_path)
            if meta:
                wpn_meta[meta["id"]] = meta

    df = pd.read_csv(csv_path, comment="#", dtype=str, keep_default_na=False)
    weapons = []
    for _, row in df.iterrows():
        wid = row.get("id", "").strip()
        if not wid:
            continue

        damage_type = DamageType.from_str(row.get("type", ""))
        if damage_type is None:
            logger.debug("Skipping weapon %s: unknown damage type %s", wid, row.get("type"))
            continue

        # Get weapon type and size from .wpn file, fallback to inference
        meta = wpn_meta.get(wid)
        if meta:
            weapon_type = WeaponType.from_str(meta["type"])
            size = SlotSize.from_str(meta["size"])
        else:
            weapon_type = _DAMAGE_TYPE_TO_WEAPON_TYPE.get(damage_type.value)
            size = None

        if weapon_type is None or size is None:
            logger.debug("Skipping weapon %s: missing weapon_type or size", wid)
            continue

        weapons.append(Weapon(
            id=wid,
            name=row.get("name", "").strip(),
            size=size,
            weapon_type=weapon_type,
            damage_per_shot=_safe_float(row.get("damage/shot")),
            damage_per_second=_safe_float(row.get("damage/second")),
            damage_type=damage_type,
            emp=_safe_float(row.get("emp")),
            flux_per_shot=_safe_float(row.get("energy/shot")),
            flux_per_second=_safe_float(row.get("energy/second")),
            range=_safe_float(row.get("range")),
            op_cost=_safe_int(row.get("OPs")),
            chargeup=_safe_float(row.get("chargeup")),
            chargedown=_safe_float(row.get("chargedown")),
            burst_size=_safe_int(row.get("burst size"), 1),
            burst_delay=_safe_float(row.get("burst delay")),
            ammo=_safe_int(row.get("ammo")),
            ammo_per_sec=_safe_float(row.get("ammo/sec")),
            proj_speed=_safe_float(row.get("proj speed")),
            turn_rate=_safe_float(row.get("turn rate")),
            hints=_parse_tags(row.get("hints")),
            tags=_parse_tags(row.get("tags")),
        ))
    return weapons


def parse_hullmod_csv(csv_path: Path) -> list[HullMod]:
    """Parse hull_mods.csv into HullMod objects."""
    df = pd.read_csv(csv_path, comment="#", dtype=str, keep_default_na=False)
    mods = []
    for _, row in df.iterrows():
        mid = row.get("id", "").strip()
        if not mid:
            continue
        hidden = row.get("hidden", "").strip().lower() in ("true", "1")
        hidden_everywhere = row.get("hiddenEverywhere", "").strip().lower() in ("true", "1")
        mods.append(HullMod(
            id=mid,
            name=row.get("name", "").strip(),
            tier=_safe_int(row.get("tier")),
            tags=_parse_tags(row.get("tags")),
            ui_tags=_parse_tags(row.get("uiTags")),
            cost_frigate=_safe_int(row.get("cost_frigate")),
            cost_destroyer=_safe_int(row.get("cost_dest")),
            cost_cruiser=_safe_int(row.get("cost_cruiser")),
            cost_capital=_safe_int(row.get("cost_capital")),
            is_hidden=hidden or hidden_everywhere,
            script=row.get("script", "").strip(),
        ))
    return mods


def load_game_data(
    game_dir: Path,
    mod_dirs: list[Path] | None = None,
) -> GameData:
    """Load and parse all game data from a Starsector installation.

    Args:
        game_dir: Path to game root (containing data/ directory)
        mod_dirs: Optional list of mod directories to merge
    """
    data_dir = game_dir / "data"

    # Parse ships
    hulls = parse_ship_csv(data_dir / "hulls" / "ship_data.csv")
    hulls = merge_ship_hull_data(hulls, data_dir / "hulls")

    # Parse weapons
    weapons = parse_weapon_csv(
        data_dir / "weapons" / "weapon_data.csv",
        data_dir / "weapons",
    )

    # Parse hullmods
    hullmods = parse_hullmod_csv(data_dir / "hullmods" / "hull_mods.csv")

    # Build GameData
    game_data = GameData(
        hulls={h.id: h for h in hulls if h.id},
        weapons={w.id: w for w in weapons if w.id},
        hullmods={m.id: m for m in hullmods if m.id},
    )

    # Validation summary
    hulls_with_slots = sum(1 for h in game_data.hulls.values() if h.weapon_slots)
    hidden_mods = sum(1 for m in game_data.hullmods.values() if m.is_hidden)
    logger.info(
        "Loaded game data: %d hulls (%d with weapon slots), "
        "%d weapons, %d hullmods (%d hidden)",
        len(game_data.hulls), hulls_with_slots,
        len(game_data.weapons),
        len(game_data.hullmods), hidden_mods,
    )

    # Validate hullmod registry against parsed data
    warnings = validate_registry(game_data)
    for w in warnings:
        logger.warning(w)

    return game_data
