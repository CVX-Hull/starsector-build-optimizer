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

### `static void dismissResults()`

Dismisses post-combat results screen to return to title screen. Uses pixel-color polling to wait for the Continue button to render (the `endCombat()` white-flash transition takes ~1.5s).

1. Poll 40x40 pixel region around Continue button via `Robot.createScreenCapture()`
2. Check cyan pixel ratio: HSB hue 185-210, sat >= 0.25, bri >= 0.35
3. When ratio >= 30% (button rendered), click Continue
4. Retry click after 500ms for safety
5. Click High Score OK (harmless if absent)
6. Falls back to blind click after 15s timeout

### Button Detection

`waitForButton(Robot, cx, cy)` polls a region around a button location for Starsector's cyan UI color. `computeCyanRatio(BufferedImage)` computes the fraction of pixels matching. Constants:

- `BUTTON_DETECT_HALF_SIZE = 20` (40x40 region)
- `BUTTON_POLL_INTERVAL_MS = 200`
- `BUTTON_POLL_TIMEOUT_MS = 15000`
- `BUTTON_HUE_MIN/MAX = 185/210`, `BUTTON_SAT_MIN = 0.25`, `BUTTON_BRI_MIN = 0.35`
- `BUTTON_MATCH_THRESHOLD = 0.30`

### Coordinate Calibration

**Two sets of coordinates exist:** one for Xvfb 1920x1080 fullscreen (production/headless), one for windowed on a physical display (development).

**Xvfb 1920x1080 fullscreen** (current, for headless operation):
```java
private static final int MISSIONS_X = 1401, MISSIONS_Y = 453;
private static final int ARENA_X = 619, ARENA_Y = 876;
private static final int PLAY_MISSION_X = 1322, PLAY_MISSION_Y = 906;
private static final int CONTINUE_X = 963, CONTINUE_Y = 892;
private static final int HIGH_SCORE_OK_X = 1119, HIGH_SCORE_OK_Y = 611;
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
  // Reset when leaving title screen — enables re-triggering on return
  // (persistent session: combat → results → title → next mission)
  if Global.getCurrentState() != GameState.TITLE:
    triggered = false
    frameCount = 0
    return

  if triggered: return
  if frameCount++ < TITLE_STABILIZE_FRAMES: return  // 120 frames ≈ 2s
  if !MatchupQueue.existsInCommon(): return
  
  triggered = true
  // Navigate in a separate thread (Robot delays would block the rendering thread)
  new Thread(() -> MenuNavigator.navigateToMission()).start()
```

### Key Design Decisions

- **Separate thread for Robot calls.** `Thread.sleep()` delays in `navigateToMission()` would freeze the game's rendering loop if called from `advance()`.
- **`triggered` flag prevents re-triggering within a single title screen visit.** Once navigation starts, don't try again until the game leaves and returns to the title screen.
- **`triggered`/`frameCount` reset when `GameState != TITLE`.** Enables persistent session reuse: after combat ends and Robot dismisses results, game returns to title screen, plugin re-triggers for the next queue. Without this reset, the plugin only fires once per game launch.
- **Frame count delay (120 frames ≈ 2s).** The title screen needs time to fully render before clicks register.
- **Queue file as trigger.** TitleScreenPlugin only acts when a queue file exists. Normal game sessions (no queue) are unaffected.

### Not JUnit-Testable

Both classes depend on `java.awt.Robot` and game state. Tested via live game launches only.
