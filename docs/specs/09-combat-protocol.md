# Combat Protocol Specification

Defines the contract between the Python optimizer (Phase 1) and the Java combat harness mod (Phase 2). Communication is filesystem-based: Python writes input files, Java reads them via the game's SettingsAPI, runs combat, and writes output files.

## File I/O via saves/common/

Starsector's security sandbox blocks `java.io.File` from mod code. All file I/O uses `Global.getSettings()` methods which operate on `<starsector>/saves/common/`. The game **appends `.data`** to all filenames in this directory.

Files use a flat `combat_harness_` prefix (subdirectories may not work with SettingsAPI).

### File Locations

| File | Disk Path | SettingsAPI Name | Written By |
|------|-----------|-----------------|------------|
| Matchup config | `saves/common/combat_harness_matchup.json.data` | `combat_harness_matchup.json` | Python |
| Combat result | `saves/common/combat_harness_result.json.data` | `combat_harness_result.json` | Java |
| Heartbeat | `saves/common/combat_harness_heartbeat.txt.data` | `combat_harness_heartbeat.txt` | Java |

**Python must write files with the `.data` extension** for the game to find them.

## matchup.json Schema

```json
{
    "matchup_id": "eval_001",
    "player_variants": ["eagle_opt_0001_a3f2b1c4"],
    "enemy_variants": ["dominator_Assault"],
    "player_flagship": "eagle_opt_0001_a3f2b1c4",
    "time_limit_seconds": 300,
    "time_mult": 3.0,
    "map_width": 24000,
    "map_height": 18000
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `matchup_id` | string | yes | — | Unique identifier for this matchup |
| `player_variants` | string[] | yes | — | Variant IDs for player side |
| `enemy_variants` | string[] | yes | — | Variant IDs for enemy side |
| `player_flagship` | string\|null | no | null | Player variant to mark as flagship |
| `time_limit_seconds` | float | no | 300.0 | Max combat duration in game-time seconds |
| `time_mult` | float | no | 3.0 | Time acceleration (clamped to [1.0, 5.0]) |
| `map_width` | float | no | 24000.0 | Combat arena width |
| `map_height` | float | no | 18000.0 | Combat arena height |

Variant IDs must correspond to `.variant` files loadable by the game (either in `data/variants/` or the mod's variant directory).

## result.json Schema

```json
{
    "matchup_id": "eval_001",
    "winner": "PLAYER",
    "duration_seconds": 87.3,
    "player_ships": [
        {
            "fleet_member_id": "uuid-string",
            "variant_id": "eagle_opt_0001_a3f2b1c4",
            "hull_id": "eagle",
            "destroyed": false,
            "hull_fraction": 0.82,
            "armor_fraction": 0.45,
            "cr_remaining": 0.61,
            "peak_time_remaining": 142.0,
            "disabled_weapons": 0,
            "flameouts": 0,
            "damage_dealt": {"shield": 12450.0, "armor": 8230.0, "hull": 3100.0, "emp": 500.0},
            "damage_taken": {"shield": 6200.0, "armor": 2100.0, "hull": 1800.0, "emp": 0.0},
            "flux_stats": {"curr_flux": 2340.0, "hard_flux": 1200.0, "max_flux": 12000.0, "overload_count": 0}
        }
    ],
    "enemy_ships": [],
    "aggregate": {
        "player_total_damage_dealt": 23780.0,
        "enemy_total_damage_dealt": 10100.0,
        "player_ships_destroyed": 0,
        "enemy_ships_destroyed": 2,
        "player_ships_retreated": 0,
        "enemy_ships_retreated": 0
    }
}
```

Ship data is collected via `getAllEverDeployedCopy()` from the fleet manager, which includes destroyed/disabled ships (unlike `engine.getShips()` which may drop them).

### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `matchup_id` | string | Echoed from input |
| `winner` | string | `"PLAYER"`, `"ENEMY"`, or `"TIMEOUT"` |
| `duration_seconds` | float | Actual combat duration |
| `player_ships` | ShipResult[] | Per-ship results for player side |
| `enemy_ships` | ShipResult[] | Per-ship results for enemy side |
| `aggregate` | AggregateResult | Fleet-level summary |

### ShipResult Fields

| Field | Type | Description |
|-------|------|-------------|
| `fleet_member_id` | string | UUID assigned by the combat engine |
| `variant_id` | string | Variant ID used to spawn this ship |
| `hull_id` | string | Hull spec ID (e.g., "eagle") |
| `destroyed` | boolean | True if ship was destroyed |
| `hull_fraction` | float | Hull HP remaining (0.0-1.0) |
| `armor_fraction` | float | Average armor remaining (0.0-1.0) |
| `cr_remaining` | float | Combat readiness |
| `peak_time_remaining` | float | Seconds of peak performance time remaining |
| `disabled_weapons` | int | Number of disabled weapon slots |
| `flameouts` | int | Number of engine flameouts |
| `damage_dealt` | DamageBreakdown | Damage dealt TO enemies |
| `damage_taken` | DamageBreakdown | Damage received FROM enemies |
| `flux_stats` | FluxStats | Flux state at end of combat |

### DamageBreakdown / FluxStats / AggregateResult Fields

See `src/starsector_optimizer/models.py` for the corresponding Python dataclasses: `DamageBreakdown`, `ShipCombatResult`, `CombatResult`, `MatchupConfig`.

## heartbeat.txt Format

Updated every ~60 frames: `<timestamp_ms> <combat_elapsed_seconds>`

Phase 3 monitors this file for liveness (no update for >60s = instance hung).

## Game Exit Protocol

After writing result.json, the Java mod calls `System.exit(0)`. Phase 3 detects completion by process exit + presence of result file.

## Python Dataclasses

Defined in `src/starsector_optimizer/models.py`:

- `DamageBreakdown(frozen=True)` — shield, armor, hull, emp
- `ShipCombatResult(frozen=True)` — per-ship combat stats
- `CombatResult(frozen=True)` — full matchup result
- `MatchupConfig(frozen=True)` — matchup input configuration
