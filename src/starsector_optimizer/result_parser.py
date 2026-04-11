"""Parse combat result JSON and write matchup queue JSON.

Bridges the file-based protocol between the Python optimizer and the Java
combat harness mod. See spec 19 for field mapping details.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import (
    CombatResult,
    DamageBreakdown,
    MatchupConfig,
    ShipCombatResult,
)


def _parse_ship(data: dict) -> ShipCombatResult:
    return ShipCombatResult(
        fleet_member_id=data["fleet_member_id"],
        variant_id=data["variant_id"],
        hull_id=data["hull_id"],
        destroyed=data["destroyed"],
        hull_fraction=data["hull_fraction"],
        armor_fraction=data["armor_fraction"],
        cr_remaining=data["cr_remaining"],
        peak_time_remaining=data["peak_time_remaining"],
        disabled_weapons=data["disabled_weapons"],
        flameouts=data["flameouts"],
        damage_dealt=DamageBreakdown(**data["damage_dealt"]),
        damage_taken=DamageBreakdown(**data["damage_taken"]),
        overload_count=data["flux_stats"]["overload_count"],
    )


def parse_combat_result(data: dict) -> CombatResult:
    """Parse a single result dict from Java JSON into CombatResult."""
    return CombatResult(
        matchup_id=data["matchup_id"],
        winner=data["winner"],
        duration_seconds=data["duration_seconds"],
        player_ships=tuple(_parse_ship(s) for s in data["player_ships"]),
        enemy_ships=tuple(_parse_ship(s) for s in data["enemy_ships"]),
        player_ships_destroyed=data["aggregate"]["player_ships_destroyed"],
        enemy_ships_destroyed=data["aggregate"]["enemy_ships_destroyed"],
        player_ships_retreated=data["aggregate"]["player_ships_retreated"],
        enemy_ships_retreated=data["aggregate"]["enemy_ships_retreated"],
    )


def parse_results_file(path: Path) -> list[CombatResult]:
    """Read and parse a combat_harness_results.json.data file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [parse_combat_result(item) for item in data]


def _matchup_to_dict(mc: MatchupConfig) -> dict:
    return {
        "matchup_id": mc.matchup_id,
        "player_builds": [
            {
                "variant_id": b.variant_id,
                "hull_id": b.hull_id,
                "weapon_assignments": dict(b.weapon_assignments),
                "hullmods": list(b.hullmods),
                "flux_vents": b.flux_vents,
                "flux_capacitors": b.flux_capacitors,
            }
            for b in mc.player_builds
        ],
        "enemy_variants": list(mc.enemy_variants),
        "time_limit_seconds": mc.time_limit_seconds,
        "time_mult": mc.time_mult,
        "map_width": mc.map_width,
        "map_height": mc.map_height,
    }


def write_queue_file(matchups: list[MatchupConfig], path: Path) -> None:
    """Write matchup configs as JSON array to the given path."""
    data = [_matchup_to_dict(mc) for mc in matchups]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
