# Mod Skeleton Specification

Starsector mod entry point, mission definition, and global plugins. Defined in:
- `combat-harness/src/main/java/starsector/combatharness/CombatHarnessModPlugin.java`
- `combat-harness/src/main/java/data/missions/optimizer_arena/MissionDefinition.java`
- `combat-harness/src/main/java/starsector/combatharness/TitleScreenPlugin.java`

## CombatHarnessModPlugin

Extends `BaseModPlugin`. Registered via `modPlugin` in `mod_info.json`.

### `onApplicationLoad()`
1. Log mod version
2. Check `MatchupQueue.existsInCommon()` — log queue size if found, or "no queue" message

### `onDevModeF8Reload()`
Log reload event.

## MissionDefinition

Package: `data.missions.optimizer_arena`. Implements `MissionDefinitionPlugin`. **Compiled in JAR** (not Janino).

### `defineMission(MissionDefinitionAPI api)`
1. Check `MatchupQueue.existsInCommon()` — if false, show error in briefing
2. Init both fleets: PLAYER with `useDefaultAI=false`, ENEMY with `useDefaultAI=true`
3. Init map with default dimensions (24000x18000)
4. Attach `new CombatHarnessPlugin()`

Ships are NOT spawned by MissionDefinition — the CombatHarnessPlugin spawns them per-matchup via `spawnShipOrWing()`.

## TitleScreenPlugin

Global `EveryFrameCombatPlugin`. Registered via `mod/data/config/settings.json`:
```json
{"plugins": {"combatHarnessTitleScreen": "starsector.combatharness.TitleScreenPlugin"}}
```

Runs on the title screen (which is a combat scene). Detects queue file, uses `MenuNavigator` to auto-navigate to mission. See spec 16 for details.

## Mod Files

| File | Location | Purpose |
|------|----------|---------|
| `mod_info.json` | `mod/` | Mod metadata, JAR, modPlugin |
| `data/config/settings.json` | `mod/data/config/` | Register TitleScreenPlugin |
| `mission_list.csv` | `mod/data/missions/` | Register optimizer_arena |
| `descriptor.json` | `mod/data/missions/optimizer_arena/` | Mission title, difficulty, icon |
| `mission_text.txt` | `mod/data/missions/optimizer_arena/` | Briefing text |
| `icon.jpg` | `mod/data/missions/optimizer_arena/` | Required by game |
| `MissionDefinition.class` | In JAR | Fleet setup + plugin attachment |
| `TitleScreenPlugin.class` | In JAR | Title screen auto-navigation |
