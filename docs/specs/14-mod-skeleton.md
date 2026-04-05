# Mod Skeleton Specification

Starsector mod entry point and mission definition. Defined in:
- `combat-harness/src/main/java/starsector/combatharness/CombatHarnessModPlugin.java`
- `combat-harness/src/main/java/data/missions/optimizer_arena/MissionDefinition.java`

## CombatHarnessModPlugin

Extends `BaseModPlugin`. Registered via `modPlugin` in `mod_info.json`.

### `onApplicationLoad()`
1. Log mod version
2. Check `MatchupConfig.existsInCommon()` (reads from `saves/common/` via SettingsAPI)
3. Log whether matchup.json is ready

### `onDevModeF8Reload()`
Log reload event.

## MissionDefinition

Package: `data.missions.optimizer_arena`. Implements `MissionDefinitionPlugin`.

**Compiled in the JAR** (not a Janino script). The game detects "already loaded from jar file" and skips runtime compilation. This is necessary because Janino cannot resolve imports from mod JAR classes.

### `defineMission(MissionDefinitionAPI api)`

1. Check `MatchupConfig.existsInCommon()` — if false, show error in briefing and return
2. Load config via `MatchupConfig.loadFromCommon()` (wrapped in try-catch)
3. Init both fleets: `api.initFleet(side, prefix, FleetGoal.ATTACK, true)` — both AI-controlled
4. Add player ships from `config.playerVariants` (flag one as flagship if `config.playerFlagship` set)
5. Add enemy ships from `config.enemyVariants`
6. Set up map from config dimensions
7. Attach plugin: `api.addPlugin(new CombatHarnessPlugin())`

## Mod Files

| File | Location | Purpose |
|------|----------|---------|
| `mod_info.json` | `mod/` | Mod metadata, JAR path, modPlugin class |
| `mission_list.csv` | `mod/data/missions/` | Registers optimizer_arena mission |
| `descriptor.json` | `mod/data/missions/optimizer_arena/` | Mission title, difficulty, icon |
| `mission_text.txt` | `mod/data/missions/optimizer_arena/` | Mission briefing |
| `icon.jpg` | `mod/data/missions/optimizer_arena/` | Mission icon (REQUIRED — game crashes without it) |
| `MissionDefinition.class` | In JAR at `data/missions/optimizer_arena/` | Fleet setup + plugin attachment |
