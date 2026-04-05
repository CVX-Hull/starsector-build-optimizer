# Combat Protocol Specification

Defines the contract between the Python optimizer (Phase 1) and the Java combat harness mod (Phase 2). Communication is filesystem-based: Python writes input files, Java reads them, runs combat, and writes output files.

## Workdir Layout

```
mods/combat-harness/workdir/
├── matchup.json      # Input: written by Python before game launch
├── result.json       # Output: written by Java mod after combat ends
└── heartbeat.txt     # Health: updated by Java mod every ~60 frames
```

The workdir path is relative to the mod directory within the game installation. Phase 3 (Instance Manager) creates per-instance workdirs.

## matchup.json Schema

```json
{
    "matchup_id": "eval_001",
    "player_variants": ["eagle_opt_0001_a3f2b1c4"],
    "enemy_variants": ["dominator_Standard", "enforcer_Assault"],
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
| `player_variants` | string[] | yes | — | Variant IDs for player side (must exist in data/variants/) |
| `enemy_variants` | string[] | yes | — | Variant IDs for enemy side |
| `player_flagship` | string\|null | no | null | Player variant to mark as flagship |
| `time_limit_seconds` | float | no | 300.0 | Max combat duration in game-time seconds |
| `time_mult` | float | no | 3.0 | Time acceleration (clamped to [1.0, 5.0]) |
| `map_width` | float | no | 24000.0 | Combat arena width |
| `map_height` | float | no | 18000.0 | Combat arena height |

## result.json Schema

```json
{
    "matchup_id": "eval_001",
    "winner": "PLAYER",
    "duration_seconds": 87.3,
    "player_ships": [
        {
            "fleet_member_id": "0",
            "variant_id": "eagle_opt_0001_a3f2b1c4",
            "hull_id": "eagle",
            "destroyed": false,
            "hull_fraction": 0.82,
            "armor_fraction": 0.45,
            "cr_remaining": 0.61,
            "peak_time_remaining": 142.0,
            "disabled_weapons": 0,
            "flameouts": 0,
            "damage_dealt": {
                "shield": 12450.0,
                "armor": 8230.0,
                "hull": 3100.0,
                "emp": 500.0
            },
            "damage_taken": {
                "shield": 6200.0,
                "armor": 2100.0,
                "hull": 1800.0,
                "emp": 0.0
            },
            "flux_stats": {
                "curr_flux": 2340.0,
                "hard_flux": 1200.0,
                "max_flux": 12000.0,
                "overload_count": 0
            }
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

### Top-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `matchup_id` | string | Echoed from input matchup.json |
| `winner` | string | `"PLAYER"`, `"ENEMY"`, or `"TIMEOUT"` |
| `duration_seconds` | float | Actual combat duration in game-time seconds |
| `player_ships` | ShipResult[] | Per-ship results for player side |
| `enemy_ships` | ShipResult[] | Per-ship results for enemy side |
| `aggregate` | AggregateResult | Fleet-level summary |

### ShipResult Fields

| Field | Type | Description |
|-------|------|-------------|
| `fleet_member_id` | string | Ship's fleet member ID from the combat engine |
| `variant_id` | string | Variant ID used to spawn this ship |
| `hull_id` | string | Hull spec ID (e.g., "eagle") |
| `destroyed` | boolean | True if ship was destroyed |
| `hull_fraction` | float | Hull HP remaining as fraction (0.0-1.0) |
| `armor_fraction` | float | Average armor remaining as fraction (0.0-1.0) |
| `cr_remaining` | float | Combat readiness at end of combat |
| `peak_time_remaining` | float | Seconds of peak performance time remaining |
| `disabled_weapons` | int | Number of disabled weapon slots |
| `flameouts` | int | Number of engine flameouts |
| `damage_dealt` | DamageBreakdown | Damage dealt TO enemies |
| `damage_taken` | DamageBreakdown | Damage received FROM enemies |
| `flux_stats` | FluxStats | Flux state at end of combat |

### DamageBreakdown Fields

| Field | Type | Description |
|-------|------|-------------|
| `shield` | float | Damage to shields |
| `armor` | float | Damage to armor |
| `hull` | float | Damage to hull |
| `emp` | float | EMP damage |

### FluxStats Fields

| Field | Type | Description |
|-------|------|-------------|
| `curr_flux` | float | Current flux level |
| `hard_flux` | float | Current hard flux level |
| `max_flux` | float | Maximum flux capacity |
| `overload_count` | int | Number of times ship was overloaded |

### AggregateResult Fields

| Field | Type | Description |
|-------|------|-------------|
| `player_total_damage_dealt` | float | Sum of all damage dealt by player ships |
| `enemy_total_damage_dealt` | float | Sum of all damage dealt by enemy ships |
| `player_ships_destroyed` | int | Player ships destroyed |
| `enemy_ships_destroyed` | int | Enemy ships destroyed |
| `player_ships_retreated` | int | Player ships that retreated |
| `enemy_ships_retreated` | int | Enemy ships that retreated |

## heartbeat.txt Format

Updated every ~60 frames by the combat harness plugin:

```
<timestamp_ms> <combat_elapsed_seconds>
```

Example: `1712345678000 45.2`

Phase 3 monitors this file. If no update for >60 seconds, the instance is considered hung.

## Atomic Write Protocol

Result files are written atomically to prevent partial reads:
1. Write to `result.json.tmp`
2. Rename to `result.json`

Phase 3 should only read `result.json` (not `.tmp`).

## Game Exit Protocol

After writing `result.json`, the Java mod calls `System.exit(0)`. Phase 3 detects completion by:
1. Process exit (primary signal)
2. Presence of `result.json` (confirmation)

## Python Dataclasses

Defined in `src/starsector_optimizer/models.py`:

- `DamageBreakdown(frozen=True)` — shield, armor, hull, emp floats
- `ShipCombatResult(frozen=True)` — per-ship combat stats
- `CombatResult(frozen=True)` — full matchup result with player/enemy ships
- `MatchupConfig(frozen=True)` — matchup input configuration
