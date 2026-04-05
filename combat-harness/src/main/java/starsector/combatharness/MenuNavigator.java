package starsector.combatharness;

import org.apache.log4j.Logger;

import java.awt.Robot;
import java.awt.event.InputEvent;
import java.awt.event.KeyEvent;

/**
 * Uses java.awt.Robot to navigate Starsector menus from title screen to mission start.
 *
 * Coordinates are hardcoded for 1920x1080 with screenScaleOverride=1.
 * These must be calibrated empirically via screenshots.
 *
 * java.awt.Robot is NOT blocked by Starsector's security sandbox
 * (sandbox only blocks java.io, java.lang.reflect, javax.script, java.util.prefs).
 */
public class MenuNavigator {

    private static final Logger log = Logger.getLogger(MenuNavigator.class);

    // Coordinates for 1920x1080, screenScaleOverride=1
    // TODO: calibrate these via screenshots at target resolution
    private static final int MISSIONS_X = 1580;
    private static final int MISSIONS_Y = 360;

    // Last item in mission list (after scrolling to bottom)
    private static final int ARENA_X = 250;
    private static final int ARENA_Y = 710;

    // "Play Mission" button
    private static final int PLAY_MISSION_X = 1660;
    private static final int PLAY_MISSION_Y = 940;

    /**
     * Navigate from main menu to Optimizer Arena mission start.
     * Called in a separate thread to avoid blocking the game's rendering loop.
     */
    public static void navigateToMission() {
        try {
            Robot robot = new Robot();
            robot.setAutoDelay(50);

            log.info("MenuNavigator: clicking Missions...");
            robotClick(robot, MISSIONS_X, MISSIONS_Y);
            Thread.sleep(2000);

            // Scroll mission list to bottom
            log.info("MenuNavigator: scrolling to bottom of mission list...");
            for (int i = 0; i < 15; i++) {
                robot.mouseWheel(5);
                Thread.sleep(100);
            }
            Thread.sleep(500);

            // Click Optimizer Arena (last/bottom item)
            log.info("MenuNavigator: clicking Optimizer Arena...");
            robotClick(robot, ARENA_X, ARENA_Y);
            Thread.sleep(1500);

            // Click Play Mission
            log.info("MenuNavigator: clicking Play Mission...");
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
