package starsector.combatharness;

import org.apache.log4j.Logger;

import java.awt.Robot;
import java.awt.event.InputEvent;

/**
 * Uses java.awt.Robot to navigate Starsector menus from title screen to mission start.
 *
 * Coordinates are calibrated for 1920x1080 with screenScaleOverride=1,
 * windowed mode with title bar at y=37.
 *
 * Robot generates absolute screen coordinates (not window-relative).
 * java.awt.Robot is NOT blocked by Starsector's security sandbox.
 */
public class MenuNavigator {

    private static final Logger log = Logger.getLogger(MenuNavigator.class);

    // Calibrated empirically at 1920x1080, screenScaleOverride=1, windowed.
    // Robot uses absolute screen coordinates.
    // Recorded by tracking mouse position during manual navigation.

    // "Missions" button on main menu
    private static final int MISSIONS_X = 1417;
    private static final int MISSIONS_Y = 486;

    // "Optimizer Arena" — last item in mission list after scrolling to bottom
    private static final int ARENA_X = 635;
    private static final int ARENA_Y = 909;

    // "Play Mission" button
    private static final int PLAY_MISSION_X = 1311;
    private static final int PLAY_MISSION_Y = 941;

    /**
     * Navigate from main menu to Optimizer Arena mission start.
     * Called in a separate thread to avoid blocking the game's rendering loop.
     */
    public static void navigateToMission() {
        try {
            Robot robot = new Robot();
            robot.setAutoDelay(50);

            // Step 1: Click "Missions" on main menu
            log.info("MenuNavigator: clicking Missions at (" + MISSIONS_X + "," + MISSIONS_Y + ")...");
            robotClick(robot, MISSIONS_X, MISSIONS_Y);
            Thread.sleep(2000);

            // Step 2: Scroll mission list to bottom to reveal Optimizer Arena
            log.info("MenuNavigator: scrolling mission list to bottom...");
            // Move mouse to the mission list area first (use same x, mid-screen y)
            robot.mouseMove(ARENA_X, 600);
            robot.delay(200);
            for (int i = 0; i < 15; i++) {
                robot.mouseWheel(5);
                Thread.sleep(100);
            }
            Thread.sleep(500);

            // Step 3: Click Optimizer Arena (last item in list)
            log.info("MenuNavigator: clicking Optimizer Arena at (" + ARENA_X + "," + ARENA_Y + ")...");
            robotClick(robot, ARENA_X, ARENA_Y);
            Thread.sleep(1500);

            // Step 4: Click "Play Mission"
            log.info("MenuNavigator: clicking Play Mission at (" + PLAY_MISSION_X + "," + PLAY_MISSION_Y + ")...");
            robotClick(robot, PLAY_MISSION_X, PLAY_MISSION_Y);

            log.info("MenuNavigator: navigation complete");
        } catch (Exception e) {
            log.error("MenuNavigator: failed to navigate", e);
        }
    }

    private static void robotClick(Robot robot, int x, int y) {
        robot.mouseMove(x, y);
        robot.delay(150);
        robot.mousePress(InputEvent.BUTTON1_DOWN_MASK);
        robot.delay(50);
        robot.mouseRelease(InputEvent.BUTTON1_DOWN_MASK);
    }
}
