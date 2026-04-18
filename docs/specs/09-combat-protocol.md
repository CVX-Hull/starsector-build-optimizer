# Combat Protocol Specification

Defines the contract between the Python optimizer and the Java combat harness mod. Communication is filesystem-based via `<starsector>/saves/common/`, using the game's SettingsAPI. The game appends `.data` to all filenames — Python must write files with the `.data` extension.

All filenames use a flat `combat_harness_` prefix (subdirectories don't work with SettingsAPI).

## File Protocol

| File | Disk Path | SettingsAPI Name | Written By | Purpose |
|------|-----------|-----------------|------------|---------|
| Queue | `saves/common/combat_harness_queue.json.data` | `combat_harness_queue.json` | Python | Array of matchup configs |
| Results | `saves/common/combat_harness_results.json.data` | `combat_harness_results.json` | Java | Array of combat results |
| Done | `saves/common/combat_harness_done.data` | `combat_harness_done` | Java | Signal: all matchups complete |
| Heartbeat | `saves/common/combat_harness_heartbeat.txt.data` | `combat_harness_heartbeat.txt` | Java | Liveness: timestamp + elapsed |
| Shutdown | `saves/common/combat_harness_shutdown.data` | `combat_harness_shutdown` | Python | Signal: exit cleanly |

## Lifecycle

### First Matchup
1. Python writes queue (1 matchup) to `saves/common/` with build specs embedded in `player_builds` field
2. Game launches (Xvfb + launcher click + ~30s startup)
3. TitleScreenPlugin detects queue → auto-navigates to Optimizer Arena mission
4. MissionDefinition adds placeholder ships via `addToFleet()`, CombatHarnessPlugin swaps player variant in-place
5. After matchup: writes results + done signal, calls `endCombat()`
6. Robot dismisses results screen → game returns to title screen

### Subsequent Matchups (Mission Restart Cycle)
1. Python detects done signal, reads results
2. Python cleans protocol files, writes new queue (1 matchup)
3. Robot finishes dismissing results → game reaches title screen
4. TitleScreenPlugin (fresh instance) detects queue → auto-navigates to mission
5. New MissionDefinition + CombatHarnessPlugin cycle runs the matchup
6. After matchup: writes results + done signal, calls `endCombat()`, Robot dismisses

### Clean Restart (After N Matchups)
1. Python kills game process (memory accumulation threshold)
2. Python launches fresh game instance (back to First Matchup flow)

### Graceful Shutdown
1. Python writes `combat_harness_shutdown.data`
2. Java detects shutdown signal in WAITING state, calls `System.exit(0)`

### Idle Timeout
1. CombatHarnessPlugin WAITING state exits after ~60s without activity
2. Python detects process exit
3. Next `run_matchup()` call launches fresh game instance

## Queue Input Schema

`combat_harness_queue.json.data` — JSON array of matchup config objects:

```json
[
    {
        "matchup_id": "eval_001",
        "player_builds": [
            {
                "variant_id": "eagle_opt_0001",
                "hull_id": "eagle",
                "weapon_assignments": {"WS 001": "heavymauler", "WS 002": "hveldriver"},
                "hullmods": ["hardenedshieldemitter", "heavyarmor"],
                "flux_vents": 20,
                "flux_capacitors": 10
            }
        ],
        "enemy_variants": ["dominator_Assault"],
        "time_limit_seconds": 180,
        "time_mult": 3.0,
        "map_width": 24000,
        "map_height": 18000
    }
]
```

Each element follows the MatchupConfig schema (see spec 10). The array must be non-empty.

## Results Output Schema

`combat_harness_results.json.data` — JSON array of result objects, one per matchup:

```json
[
    {
        "matchup_id": "eval_001",
        "winner": "ENEMY",
        "duration_seconds": 56.8,
        "player_ships": [
            {
                "fleet_member_id": "uuid",
                "variant_id": "eagle_opt_0001",
                "hull_id": "eagle",
                "destroyed": true,
                "hull_fraction": 0.0,
                "armor_fraction": 0.36,
                "cr_remaining": 0.7,
                "peak_time_remaining": 472.0,
                "disabled_weapons": 8,
                "flameouts": 0,
                "damage_dealt": {"shield": 4300.0, "armor": 0.0, "hull": 0.0, "emp": 0.0},
                "damage_taken": {"shield": 0.0, "armor": 5098.0, "hull": 24588.0, "emp": 0.0},
                "flux_stats": {"curr_flux": 0.0, "hard_flux": 0.0, "max_flux": 12900.0, "overload_count": 0}
            }
        ],
        "enemy_ships": [...],
        "aggregate": {
            "player_total_damage_dealt": 4300.0,
            "enemy_total_damage_dealt": 29686.0,
            "player_ships_destroyed": 1,
            "enemy_ships_destroyed": 0,
            "player_ships_retreated": 0,
            "enemy_ships_retreated": 0
        }
    }
]
```

Results are ordered by matchup execution order (same order as input queue).

Ship data is collected from tracked ShipAPI references (not fleet manager, which accumulates across matchups in a batched session).

## Done Signal

`combat_harness_done.data` — written after all results are written. Contains a timestamp. Python should poll for this file (not the results file) to detect completion.

## Heartbeat

`combat_harness_heartbeat.txt.data` — updated every ~60 frames with 6 space-separated fields: `<timestamp_ms> <elapsed_seconds> <player_hp_fraction> <enemy_hp_fraction> <player_alive_count> <enemy_alive_count>`. Used for liveness monitoring. During WAITING state, HP fractions and alive counts are all zero.

## Python Dataclasses

Defined in `src/starsector_optimizer/models.py`:

- `MatchupConfig(frozen=True)` — single matchup configuration
- `DamageBreakdown(frozen=True)` — shield, armor, hull, emp
- `ShipCombatResult(frozen=True)` — per-ship combat stats
- `CombatResult(frozen=True)` — full matchup result

## Python I/O Functions

Defined in `src/starsector_optimizer/result_parser.py`:

- `write_queue_file(matchups, path)` — serialize `list[MatchupConfig]` to JSON array at given path
- `parse_combat_result(data)` — parse a single result dict → `CombatResult`
- `parse_results_file(path)` — read results JSON file → `list[CombatResult]`

See spec 19 for detailed field mapping. Note: `overload_count` lives under `flux_stats` in the Java JSON but is a top-level field on `ShipCombatResult`.

## Instance Management

Defined in `src/starsector_optimizer/instance_manager.py`:

- `LocalInstancePool.run_matchup(matchup)` — run single matchup on a pool-chosen instance, return result. Concrete implementation of the `EvaluatorPool` ABC (spec 18).
- Handles per-instance work directories, Xvfb displays, health monitoring, crash recovery

See spec 18 for full design, and spec 22 for the parallel `CloudWorkerPool` implementation used by Phase 6 cloud campaigns.
