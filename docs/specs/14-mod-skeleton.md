# Mod Skeleton Specification

Starsector mod entry point and mission definition. Defined in:
- `combat-harness/src/main/java/starsector/combatharness/CombatHarnessModPlugin.java`
- `combat-harness/mod/data/missions/optimizer_arena/MissionDefinition.java`

## CombatHarnessModPlugin

Extends `BaseModPlugin`. Registered via `modPlugin` in `mod_info.json`.

### `onApplicationLoad()`
1. Log mod version: "Combat Harness v0.1.0 loaded"
2. Verify workdir exists: `mods/combat-harness/workdir/`
3. Log whether `matchup.json` exists (info, not error — user may just be browsing missions)

### `onDevModeF8Reload()`
Log reload event.

## MissionDefinition (Janino script)

Package: `data.missions.optimizer_arena`. Implements `MissionDefinitionPlugin`.

Compiled at runtime by Starsector's Janino compiler. Must be kept simple — no advanced Java features, no lambdas, no generics.

### `defineMission(MissionDefinitionAPI api)`

1. Resolve workdir: `new File("mods/combat-harness/workdir")`
2. Load config: `MatchupConfig.fromFile(new File(workdir, "matchup.json"))`
3. Init player fleet: `api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true)`
4. Init enemy fleet: `api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true)`
5. Add player ships from `config.playerVariants`
6. Add enemy ships from `config.enemyVariants`
7. Set up map: `api.initMap(-hw, hw, -hh, hh)` from config dimensions
8. Attach plugin: `api.addPlugin(new CombatHarnessPlugin(workdir))`

Both sides use `FleetGoal.ATTACK` and `useDefaultAI=true` for fully automated combat.

## Mod Files

| File | Purpose |
|------|---------|
| `mod_info.json` | Mod metadata, JAR path, modPlugin class |
| `data/missions/mission_list.csv` | Registers optimizer_arena mission |
| `data/missions/optimizer_arena/descriptor.json` | Mission title/difficulty |
| `data/missions/optimizer_arena/mission_text.txt` | Mission briefing |
| `data/missions/optimizer_arena/MissionDefinition.java` | Fleet setup + plugin attachment |
