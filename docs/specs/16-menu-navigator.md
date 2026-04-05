# Menu Navigator and Title Screen Plugin Specification

Automates navigation from the game's title screen to the Optimizer Arena mission. Defined in:
- `combat-harness/src/main/java/starsector/combatharness/MenuNavigator.java`
- `combat-harness/src/main/java/starsector/combatharness/TitleScreenPlugin.java`

## MenuNavigator

Static utility class using `java.awt.Robot` to generate native OS input events. Robot events are indistinguishable from real hardware input — LWJGL picks them up normally.

`java.awt.Robot` is NOT blocked by Starsector's security sandbox (verified by decompiling the script classloader: it blocks `java.io.*`, `java.lang.reflect.*`, `javax.script.*`, `java.util.prefs.*`, `java.lang.Class` — but not `java.awt`).

### Resolution Requirement

Coordinates are hardcoded for **1920x1080** with `screenScaleOverride: 1`. The game's `data/config/settings.json` must have:
```json
"resolutionOverride": "1920x1080",
"screenScaleOverride": 1
```

These settings ensure deterministic button positions on any machine (local or cloud/Xvfb).

### `static void navigateToMission()`

1. Click "Missions" button on main menu (hardcoded coords)
2. Wait 2s for mission list to load
3. Scroll mission list to bottom (mouse wheel events)
4. Wait 500ms
5. Click "Optimizer Arena" (last item in mission list)
6. Wait 1s for mission details
7. Click "Play Mission" button

Each click: `Robot.mouseMove(x, y)` → delay → `mousePress(BUTTON1_DOWN_MASK)` → delay → `mouseRelease(BUTTON1_DOWN_MASK)`.

### Coordinate Calibration

**Two sets of coordinates exist:** one for Xvfb 1920x1080 fullscreen (production/headless), one for windowed on a physical display (development).

**Xvfb 1920x1080 fullscreen** (current, for headless operation):
```java
private static final int MISSIONS_X = 1401, MISSIONS_Y = 453;
private static final int ARENA_X = 619, ARENA_Y = 876;
private static final int PLAY_MISSION_X = 1295, PLAY_MISSION_Y = 908;
```

**Launcher button** (handled by Python instance manager via xdotool, NOT Robot):
```
Launcher "Play Starsector" button: (297, 255)
```
The launcher is Java Swing — xdotool synthetic events work. The game itself is LWJGL — only Robot works.

**Re-calibration for new game versions or display setups:**
1. Start Xvfb at 1920x1080: `Xvfb :100 -screen 0 1920x1080x24 -nolisten tcp`
2. Launch game on `:100`, click Play Starsector via xdotool
3. Screenshot: `DISPLAY=:100 import -window root /tmp/screenshot.png`
4. Open in an image viewer with coordinate display, annotate button centers
5. Update coordinates in `MenuNavigator.java` and instance manager

## TitleScreenPlugin

Global `EveryFrameCombatPlugin` registered via `mod/data/config/settings.json`:
```json
{"plugins": {"combatHarnessTitleScreen": "starsector.combatharness.TitleScreenPlugin"}}
```

The title screen background is a combat scene — global plugins run on it.

### Behavior

```
advance(amount, events):
  if triggered: return
  if Global.getCurrentState() != GameState.TITLE: return
  if frameCount++ < 120: return  // wait ~2s for title screen to stabilize
  if !MatchupQueue.existsInCommon(): return
  
  triggered = true
  // Navigate in a separate thread (Robot delays would block the rendering thread)
  new Thread(() -> MenuNavigator.navigateToMission()).start()
```

### Key Design Decisions

- **Separate thread for Robot calls.** `Thread.sleep()` delays in `navigateToMission()` would freeze the game's rendering loop if called from `advance()`.
- **`triggered` flag prevents re-triggering.** Once navigation starts, don't try again.
- **Frame count delay (120 frames ≈ 2s).** The title screen needs time to fully render before clicks register.
- **Queue file as trigger.** TitleScreenPlugin only acts when a queue file exists. Normal game sessions (no queue) are unaffected.

### Not JUnit-Testable

Both classes depend on `java.awt.Robot` and game state. Tested via live game launches only.
