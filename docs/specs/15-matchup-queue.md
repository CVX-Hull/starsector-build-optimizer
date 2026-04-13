# Matchup Queue Specification

Container for a batch of matchup configs. Reads a JSON array from `saves/common/` via SettingsAPI. Defined in `combat-harness/src/main/java/starsector/combatharness/MatchupQueue.java`.

## Fields

```java
public static final String QUEUE_FILE = "combat_harness_queue.json";
private final List<MatchupConfig> matchups;  // immutable after construction
```

## Functions

### `static MatchupQueue loadFromCommon()`
Read `combat_harness_queue.json` from `saves/common/` via `Global.getSettings().readTextFileFromCommon()`. Parse as JSONArray, delegate each element to `MatchupConfig.fromJSON()`. Throws `RuntimeException` on I/O or parse errors.

### `static boolean existsInCommon()`
Check if queue file exists via `Global.getSettings().fileExistsInCommon()`.

### `static MatchupQueue fromJSON(JSONArray array)`
Parse each element via `MatchupConfig.fromJSON()`. Throws `IllegalArgumentException` if array is empty (empty queue is a bug).

### `JSONArray toJSON()`
Serialize back to JSONArray for round-trip testing.

### `int size()`
Number of matchups in the queue.

### `MatchupConfig get(int index)`
Get matchup config at index.

## Validation

- JSON must be a valid array
- Array must be non-empty
- Each element validated by `MatchupConfig.fromJSON()` rules (matchup_id required, player_builds and enemy_variants non-empty, etc.)
- Player builds contain inline `BuildSpec` objects (see spec 10), not variant ID strings
- If any element fails validation, the entire queue load fails

## Single-Matchup-Per-Mission

Each queue contains exactly one matchup. `loadFromCommon()` is called once per mission by CombatHarnessPlugin INIT. For subsequent matchups, Python writes a new queue file and TitleScreenPlugin auto-navigates to a fresh mission (see spec 13).

## Relationship to MatchupConfig

`MatchupQueue` is a container of `MatchupConfig` objects. `MatchupConfig` remains the single-matchup POJO. The `loadFromCommon()`/`existsInCommon()` static methods on `MatchupConfig` are superseded by `MatchupQueue`'s equivalents.
