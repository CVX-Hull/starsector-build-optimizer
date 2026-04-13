---
name: starsector-modding
description: Hard-won knowledge about Starsector 0.98a Java modding — security sandbox, API quirks, file I/O, Janino limitations, and combat plugin patterns.
---

# Starsector Modding Knowledge

## Security Sandbox

Starsector's custom classloader (`com.fs.starfarer.loading.scripts.new`) blocks these from ALL mod code (both JARs and Janino scripts):

**Blocked:**
- `java.io.*` (except: BufferedReader, InputStream, InputStreamReader, Reader, Serializable, IOException, PrintStream, PrintWriter, ByteArrayInputStream, FilterInputStream, FilterOutputStream, OutputStream, Closeable, Flushable, StringReader, FileReader, InvalidClassException, ObjectStreamException)
- `java.nio.file.File*`
- `java.lang.reflect.*` (except: AnnotatedElement, InvocationTargetException, Type, GenericDeclaration)
- `java.lang.Class` (blocks reflection)
- `javax.script.*`
- `java.util.prefs.*`
- `sun.reflect.misc.MethodUtil`

**NOT blocked (verified):**
- `java.awt.Robot` — generates native OS input events, works for UI automation
- `java.nio.ByteBuffer`, `java.nio.CharBuffer`, `java.nio.IntBuffer` — whitelisted
- All Starsector API classes (`com.fs.starfarer.api.*`)
- LWJGL classes (`org.lwjgl.*`)

## File I/O — The Only Way

All file I/O must use `Global.getSettings()` methods operating on `<starsector>/saves/common/`:

```java
// Read
String text = Global.getSettings().readTextFileFromCommon("combat_harness_queue.json");
// Write (max 1MB)
Global.getSettings().writeTextFileToCommon("combat_harness_results.json", jsonStr);
// Check existence
boolean exists = Global.getSettings().fileExistsInCommon("combat_harness_queue.json");
// Delete
Global.getSettings().deleteTextFileFromCommon("combat_harness_done");
```

**Critical: The game appends `.data` to ALL filenames in `saves/common/`.** When Python writes `combat_harness_queue.json.data` on disk, the Java side reads it as `combat_harness_queue.json` via SettingsAPI.

**Subdirectories don't work** with SettingsAPI. Use flat filenames with a prefix (e.g., `combat_harness_`).

For reading mod data files (not saves/common):
```java
JSONObject json = Global.getSettings().loadJSON("data/config/myconfig.json", "my_mod_id");
String text = Global.getSettings().loadText("data/somefile.txt", "my_mod_id");
```

## org.json — Old Version with Checked Exceptions

The game bundles an ancient `json.jar` where `put()`, `getString()`, `getJSONObject()`, `new JSONObject(String)` ALL throw checked `JSONException`. Modern org.json made these unchecked.

**Always add `throws JSONException`** to methods using org.json, or wrap in try-catch.

## Janino Script Compilation

Starsector compiles loose `.java` files in `data/scripts/` and `data/missions/` at runtime via Janino. Limitations:

- **Cannot resolve classes from mod JARs.** If a mission script imports `starsector.mymod.MyClass`, Janino fails with `Cannot determine simple type name`. Solution: put the class in the JAR with the correct package (e.g., `data.missions.my_mission.MissionDefinition`) — the game detects "already loaded from jar file" and skips Janino.
- **No lambdas, no var, no records.** Keep Janino scripts simple.
- **Enhanced for-each on arrays** may cause issues in some Janino versions.

## Mission System

### MissionDefinitionPlugin
```java
api.initFleet(FleetSide.PLAYER, "PREFIX", FleetGoal.ATTACK, false);  // false = AI controlled
api.initFleet(FleetSide.ENEMY, "PREFIX", FleetGoal.ATTACK, true);    // true = standard enemy
api.addToFleet(FleetSide.PLAYER, variantId, FleetMemberType.SHIP, shipName, false);
api.addFleetMember(FleetSide.PLAYER, fleetMember);  // for programmatic variants
api.initMap(-hw, hw, -hh, hh);
api.addPlugin(new MyEveryFrameCombatPlugin());
```

**`useDefaultAI` parameter:** `false` for PLAYER side means all ships get AI (no human control). `true` means human controls flagship. Counter-intuitive but verified against API docs and all vanilla missions.

**`addToFleet` vs `addFleetMember`:** `addToFleet` takes a variant ID string (loaded from `.variant` files) and properly handles CR and deployment state. `addFleetMember` takes a `FleetMemberAPI` but ships enter combat in permanent retreat mode — the engine overrides `setRetreating()`. For programmatic variants, use `addToFleet` with a stock placeholder, then swap in the plugin.

### Mission files required:
- `descriptor.json` — MUST include `"icon": "icon.jpg"` field AND the actual icon.jpg file. Game crashes without it.
- `mission_list.csv` — registers the mission
- `mission_text.txt` — briefing text

## Combat Engine Patterns

### EveryFrameCombatPlugin Lifecycle
- `init(CombatEngineAPI)` — deprecated, may not fire before first `advance()`. Always null-check engine.
- `advance(float amount, List<InputEventAPI>)` — called every frame. `amount` = seconds since last frame.
- **`endCombat()` stops `advance()` immediately.** After calling `engine.endCombat()`, the engine stops invoking `advance()` within the same or next frame. Any post-combat work (launching threads, writing files, state transitions) must happen in the same frame, before the `endCombat()` call.
- Plugins registered via `data/config/settings.json` `"plugins"` section run on the title screen too (it's a combat scene).

### Programmatic Variant Construction
```java
ShipHullSpecAPI hull = Global.getSettings().getHullSpec("eagle");
ShipVariantAPI variant = Global.getSettings().createEmptyVariant("my_variant_id", hull);
variant.addWeapon("WS 001", "heavymauler");
variant.addMod("heavyarmor");
variant.setNumFluxVents(20);
variant.setNumFluxCapacitors(10);
variant.autoGenerateWeaponGroups();
FleetMemberAPI member = Global.getSettings().createFleetMember(FleetMemberType.SHIP, variant);
member.getRepairTracker().setCR(0.7f);  // Must set CR explicitly — defaults to 0
```

**CR pitfall:** `createFleetMember()` creates members at CR=0. `getRepairTracker().getMaxCR()` also returns 0 before the engine fully initializes the member. Use a hardcoded 0.7f (standard deployment CR). Without this, ships deploy disabled and are instantly destroyed.

**Retreat pitfall:** Ships added to missions via `api.addFleetMember(side, member)` enter combat in retreat mode (`isRetreating()=true`). The engine overrides `setRetreating(false, false)` on the next frame. Workaround: use `addToFleet()` with a stock placeholder variant for the deployment screen, then remove placeholders and spawn real builds via `spawnFleetMember()` in the plugin.

### Ship Spawning Mid-Combat
```java
// Stock variants by ID:
ShipAPI ship = engine.getFleetManager(FleetSide.PLAYER)
    .spawnShipOrWing(variantId, new Vector2f(x, y), facing);

// Programmatic variants by FleetMemberAPI:
FleetMemberAPI member = VariantBuilder.createFleetMember(spec);
ShipAPI ship = engine.getFleetManager(FleetSide.PLAYER)
    .spawnFleetMember(member, new Vector2f(x, y), facing, 0f);
// Must call ensureCombatReady(ship) after — CR may still be 0
```
Returns `ShipAPI` directly — track these references instead of using fleet manager queries (which accumulate across batched matchups).

### Entity Cleanup
```java
for (ShipAPI ship : new ArrayList<>(engine.getShips())) {
    engine.removeEntity(ship);
}
// Also clean projectiles and missiles
```

### Fleet Manager Accumulation
`getAllEverDeployedCopy()` returns ALL ships ever deployed in the combat session, including from previous matchups. **Don't use it** for per-matchup result collection — track spawned ShipAPIs directly.

### Combat End Control
```java
engine.setDoNotEndCombat(true);   // prevents auto-ending
engine.isCombatOver();            // stays false with setDoNotEndCombat
engine.isFleetsInContact();       // true when sides can see each other
engine.getTotalElapsedTime(false); // cumulative, not per-matchup
```

### Camera/Viewport Control
```java
ViewportAPI vp = engine.getViewport();
vp.setExternalControl(true);
vp.set(cx - vp.getVisibleWidth()/2, cy - vp.getVisibleHeight()/2,
       vp.getVisibleWidth(), vp.getVisibleHeight());
```
`setPlayerShipExternal()` makes camera trail behind the ship — use direct viewport control for centering on the action.

### Time Acceleration
```java
engine.getTimeMult().modifyMult("source_id", 3.0f);  // 3x speed
```
Keep ≤5x — higher values cause physics/collision issues.

### DamageListener
```java
public void reportDamageApplied(Object source, CombatEntityAPI target, ApplyDamageResultAPI result) {
    // source can be ShipAPI, DamagingProjectileAPI, or BeamAPI — use instanceof
    // target should be checked with instanceof ShipAPI
    // result has: getDamageToShields(), getTotalDamageToArmor(), getDamageToHull(), getEmpDamage()
}
```

### Inherited API Methods
`getHullLevel()` is on `CombatEntityAPI`, inherited by `ShipAPI`. IDE autocomplete may not show it directly on ShipAPI — check the full inheritance chain.

## Build System

- **Gradle 9.4+** with Java 26 JDK for compilation (game runtime is Java 17)
- **`compileOnly`** for starfarer.api.jar, json.jar, log4j-1.2.9.jar, lwjgl_util.jar
- **`testImplementation`** needs starfarer.api.jar too (for DamageListener etc.)
- Game's bundled JRE (`jre_linux/`) is a JRE, not JDK — can't compile with it
- `sourceCompatibility` / `targetCompatibility` = Java 17 (game runtime version)

## UI Automation via java.awt.Robot

Robot works from mod code (not blocked by sandbox). Generates native OS input events that LWJGL picks up. Coordinates are **absolute screen coordinates** — not window-relative. `Robot.createScreenCapture()` also works (returns `BufferedImage` from the Xvfb framebuffer) — useful for pixel-color polling to detect when UI elements have rendered before clicking.

Calibration: track mouse positions during manual navigation (`xdotool getmouselocation` in a loop), record where clicks land.

Robot calls with `Thread.sleep()` delays MUST run in a separate thread — otherwise they block the game's rendering loop.

## Global Plugin Registration

Register an EveryFrameCombatPlugin for ALL combat (including title screen) via mod's `data/config/settings.json`:
```json
{"plugins": {"myPluginKey": "com.mymod.MyPlugin"}}
```
Use `Global.getCurrentState() == GameState.TITLE` to detect the title screen. **Important**: If using a `triggered` flag to run one-shot logic on the title screen, reset it when `GameState != TITLE` — otherwise the plugin only fires once per game launch, breaking persistent session reuse across missions.

## Weapon Filtering

When building search spaces for optimization, filter out:
- Weapons with `SYSTEM` in hints (ship system payloads)
- Weapons with `restricted` in tags (faction-exclusive)
- Weapons with 0 OP cost and not beams (system payloads like `gorgon_payload`)

## Instance Orchestration (Phase 3+)

### Launcher vs Game Window
- **Launcher** = Java Swing window (597x373). `xdotool` synthetic events work on Swing.
- **Game** = LWJGL/OpenGL window. `xdotool` does NOT work on LWJGL windows — clicks don't register. Only `java.awt.Robot` (from inside the JVM) works for in-game UI interaction.
- Instance manager clicks launcher via xdotool, then TitleScreenPlugin/MenuNavigator handle game navigation via Robot.

### Ship Spawning — `spawnFleetMember()` Retreat Bug
- `spawnFleetMember()` mid-combat ALWAYS sets `directRetreat=true` on the spawned ship at a level below the public API.
- Tried and failed: `setDirectRetreat(false)`, `clearTasks()`, `reassign()`, `setPreventFullRetreat(true)`, `setCanForceShipsToEngageWhenBattleClearlyLost(true)`, no-op `AdmiralAIPlugin`, per-frame `setRetreating(false,false)`, `setMaxStrength()`. None override the internal retreat.
- `spawnShipOrWing(variantId)` does NOT work with programmatic variants — `createEmptyVariant()` does not register them for lookup.
- **Working approach**: `addToFleet()` in MissionDefinition (proper deployment, CR, AI behavior) + in-place variant swap via `variant.clear()` + `addWeapon()`/`addMod()`.
- **Consequence**: Single matchup per mission. After each fight: Robot dismiss thread launched → `endCombat()` → Robot dismisses results → TitleScreenPlugin restarts mission with new queue. Robot must launch before `endCombat()` because engine stops calling `advance()` immediately after.

### MenuNavigator Coordinates (1920x1080 Xvfb fullscreen)
- Missions button: (1401, 453)
- Optimizer Arena: (619, 876) — scroll to bottom first
- Play Mission: (1322, 906)
- Post-combat Continue: (963, 892)
- High score OK dialog: (1119, 611)

### Xvfb Virtual Display
- Must be exactly `1920x1080x24` to match Robot's hardcoded coordinates.
- Command: `Xvfb :<N> -screen 0 1920x1080x24 -nolisten tcp`
- Clean stale lock files before starting: `/tmp/.X{N}-lock`, `/tmp/.X11-unix/X{N}`
- Game's `resolutionOverride: "1920x1080"` in `data/config/settings.json` ensures consistent rendering.

### Per-Instance Work Directories
- `data/variants/` has **subdirectories** (e.g., `dominator/`, `eagle/`). Must symlink subdirs too, not just top-level files.
- `data/config/` is **written at runtime** (game persists settings.json). Must be copied per instance, not symlinked.
- `mods/` copied per instance (68KB). `enabled_mods.json` needs to exist per instance.

### Game Activation
- Stored in `~/.java/.userPrefs/com/fs/starfarer/prefs.xml` (user-global, NOT per-game-directory).
- The `serial` entry in prefs.xml contains the license key.
- All instances on same machine share activation automatically.
- For cloud: copy prefs.xml to the same path on the remote machine — transfers activation without interactive activation.

### Headless/Cloud Requirements
- **GPU required**: LWJGL rendering through Xvfb needs a real GPU driver. Software rendering (Mesa/llvmpipe) on CPU-only VMs is ~10-50x slower — unusable.
- **Native LWJGL deps**: `libxcursor1`, `libxxf86vm1`, `libxrender1`, `libxtst6`, `libxrandr2`, `libxi6` (Ubuntu package names). Missing libs cause `UnsatisfiedLinkError` crashes.
- **Audio disabled per-instance**: `ALSOFT_DRIVERS=null` env var disables OpenAL output (bypasses PulseAudio/ALSA). Null ALSA config (`asound_null.conf` via `ALSA_CONFIG_PATH`) as fallback. Both set in `instance_manager.py:_start_game()`. On cloud, also install `libopenal1` to prevent blocking error dialog.
- **System Java unnecessary**: Game bundles `jre_linux/` (Zulu 17.0.10). System `openjdk` can interfere via `JAVA_HOME`.
- **rsync `--delete`**: When syncing game dir, use `--delete` to remove stale files from different versions (e.g., leftover `jre_linux/lib/ext/` causes JRE crash).

### Heartbeat Protocol (Enriched)
Format: `<timestamp_ms> <elapsed_seconds> <player_hp_fraction> <enemy_hp_fraction> <player_alive> <enemy_alive>`
- Written by Java via SettingsAPI every ~60 frames
- Python polls file and parses content (not just mtime)
- Legacy 2-field format also supported for backward compatibility

### Stop Signal (Removed)
Mid-fight curtailment was removed in favor of between-trial statistical pruning (WilcoxonPruner). Fights run to completion or timeout.
