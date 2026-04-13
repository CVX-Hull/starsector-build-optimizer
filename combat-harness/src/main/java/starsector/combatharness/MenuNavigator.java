package starsector.combatharness;

import org.apache.log4j.Logger;

import java.awt.Color;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.event.InputEvent;
import java.awt.image.BufferedImage;

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

    // "Play Starsector" button on launcher (597x373 Swing window at 0,0)
    private static final int LAUNCHER_X = 297;
    private static final int LAUNCHER_Y = 255;

    // "Missions" button on main menu (calibrated for 1920x1080 Xvfb fullscreen)
    private static final int MISSIONS_X = 1401;
    private static final int MISSIONS_Y = 453;

    // "Optimizer Arena" — last item in mission list after scrolling to bottom
    private static final int ARENA_X = 619;
    private static final int ARENA_Y = 876;

    // "Play Mission" button
    private static final int PLAY_MISSION_X = 1322;
    private static final int PLAY_MISSION_Y = 906;

    // Post-combat results screen: "Continue" button
    private static final int CONTINUE_X = 963;
    private static final int CONTINUE_Y = 892;

    // High score dialog "OK" button (may not always appear)
    private static final int HIGH_SCORE_OK_X = 1119;
    private static final int HIGH_SCORE_OK_Y = 611;

    // Navigation timing delays (milliseconds)
    private static final int MISSIONS_LOAD_DELAY_MS = 2000;
    private static final int SCROLL_SETTLE_DELAY_MS = 500;
    private static final int SCROLL_INTER_DELAY_MS = 100;
    private static final int ARENA_SELECT_DELAY_MS = 1500;
    private static final int SCROLL_WHEEL_COUNT = 15;
    private static final int SCROLL_WHEEL_AMOUNT = 5;
    private static final int MISSION_LIST_SCROLL_Y = 600;
    private static final int DISMISS_POST_CONTINUE_DELAY_MS = 500;
    private static final int DISMISS_POST_RETRY_DELAY_MS = 1000;
    private static final int ROBOT_AUTO_DELAY_MS = 50;

    // Button detection via pixel color polling.
    // Starsector's cyan/teal UI buttons have hue ~193-205 in HSB space.
    // Calibrated from screenshots: 65% cyan when button present, 0% during
    // white flash or combat. Threshold 30% cleanly separates the states.
    private static final int BUTTON_DETECT_HALF_SIZE = 20;
    private static final int BUTTON_POLL_INTERVAL_MS = 200;
    private static final int BUTTON_POLL_TIMEOUT_MS = 15000;
    private static final float BUTTON_HUE_MIN = 185f;
    private static final float BUTTON_HUE_MAX = 210f;
    private static final float BUTTON_SAT_MIN = 0.25f;
    private static final float BUTTON_BRI_MIN = 0.35f;
    private static final float BUTTON_MATCH_THRESHOLD = 0.30f;

    /**
     * Navigate from main menu to Optimizer Arena mission start.
     * Called in a separate thread to avoid blocking the game's rendering loop.
     */
    public static void navigateToMission() {
        try {
            Robot robot = new Robot();
            robot.setAutoDelay(ROBOT_AUTO_DELAY_MS);

            // Step 1: Click "Missions" on main menu
            log.info("MenuNavigator: clicking Missions at (" + MISSIONS_X + "," + MISSIONS_Y + ")...");
            robotClick(robot, MISSIONS_X, MISSIONS_Y);
            Thread.sleep(MISSIONS_LOAD_DELAY_MS);

            // Step 2: Scroll mission list to bottom to reveal Optimizer Arena
            log.info("MenuNavigator: scrolling mission list to bottom...");
            robot.mouseMove(ARENA_X, MISSION_LIST_SCROLL_Y);
            robot.delay(200);
            for (int i = 0; i < SCROLL_WHEEL_COUNT; i++) {
                robot.mouseWheel(SCROLL_WHEEL_AMOUNT);
                Thread.sleep(SCROLL_INTER_DELAY_MS);
            }
            Thread.sleep(SCROLL_SETTLE_DELAY_MS);

            // Step 3: Click Optimizer Arena (last item in list)
            log.info("MenuNavigator: clicking Optimizer Arena at (" + ARENA_X + "," + ARENA_Y + ")...");
            robotClick(robot, ARENA_X, ARENA_Y);
            Thread.sleep(ARENA_SELECT_DELAY_MS);

            // Step 4: Click "Play Mission"
            log.info("MenuNavigator: clicking Play Mission at (" + PLAY_MISSION_X + "," + PLAY_MISSION_Y + ")...");
            robotClick(robot, PLAY_MISSION_X, PLAY_MISSION_Y);

            log.info("MenuNavigator: navigation complete");
        } catch (Exception e) {
            log.error("MenuNavigator: failed to navigate", e);
        }
    }

    /**
     * Dismiss post-combat results screen to return to mission select.
     *
     * Waits for the "Continue" button to render by polling pixel colors in
     * a region around the button location. Starsector's cyan UI elements are
     * absent during the endCombat white-flash transition (0% cyan) and present
     * once the results dialog renders (~65% cyan). This replaces blind timing
     * delays with visual confirmation.
     *
     * After dismissal, TitleScreenPlugin detects the queue and auto-navigates
     * to a fresh mission.
     */
    public static void dismissResults() {
        try {
            Robot robot = new Robot();
            robot.setAutoDelay(ROBOT_AUTO_DELAY_MS);

            // Poll for Continue button to appear via pixel color detection.
            boolean detected = waitForButton(robot, CONTINUE_X, CONTINUE_Y);

            if (detected) {
                log.info("MenuNavigator: Continue button detected, clicking...");
            } else {
                log.warn("MenuNavigator: Continue button not detected after "
                        + BUTTON_POLL_TIMEOUT_MS + "ms, clicking anyway");
            }

            // Click Continue — one click + one retry for safety
            robotClick(robot, CONTINUE_X, CONTINUE_Y);
            Thread.sleep(DISMISS_POST_CONTINUE_DELAY_MS);
            robotClick(robot, CONTINUE_X, CONTINUE_Y);
            Thread.sleep(DISMISS_POST_RETRY_DELAY_MS);

            // Dismiss high score dialog if present (click is harmless if absent)
            log.info("MenuNavigator: clicking high score OK...");
            robotClick(robot, HIGH_SCORE_OK_X, HIGH_SCORE_OK_Y);

            log.info("MenuNavigator: results dismissed");
        } catch (Exception e) {
            log.error("MenuNavigator: failed to dismiss results", e);
        }
    }

    /**
     * Poll a region around a button location for Starsector's cyan UI color.
     * Returns true once the cyan pixel ratio exceeds the threshold, indicating
     * the button has rendered and is ready to click.
     */
    private static boolean waitForButton(Robot robot, int cx, int cy) {
        int rx = Math.max(0, cx - BUTTON_DETECT_HALF_SIZE);
        int ry = Math.max(0, cy - BUTTON_DETECT_HALF_SIZE);
        int size = BUTTON_DETECT_HALF_SIZE * 2;
        Rectangle rect = new Rectangle(rx, ry, size, size);
        int maxAttempts = BUTTON_POLL_TIMEOUT_MS / BUTTON_POLL_INTERVAL_MS;

        for (int attempt = 0; attempt < maxAttempts; attempt++) {
            try {
                BufferedImage img = robot.createScreenCapture(rect);
                float ratio = computeCyanRatio(img);

                if (attempt % 10 == 0) {
                    log.info("MenuNavigator: pixel poll " + (attempt + 1)
                            + "/" + maxAttempts
                            + " cyan=" + String.format("%.1f%%", ratio * 100));
                }

                if (ratio >= BUTTON_MATCH_THRESHOLD) {
                    return true;
                }
            } catch (Exception e) {
                // Screen capture can fail transiently during transitions
            }

            try {
                Thread.sleep(BUTTON_POLL_INTERVAL_MS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    /**
     * Compute the fraction of pixels matching Starsector's cyan/teal UI color.
     * Uses HSB color space for robust hue detection regardless of brightness.
     */
    private static float computeCyanRatio(BufferedImage img) {
        int w = img.getWidth();
        int h = img.getHeight();
        int total = w * h;
        int match = 0;

        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int rgb = img.getRGB(x, y);
                int r = (rgb >> 16) & 0xFF;
                int g = (rgb >> 8) & 0xFF;
                int b = rgb & 0xFF;

                float[] hsb = Color.RGBtoHSB(r, g, b, null);
                float hue = hsb[0] * 360f;
                float sat = hsb[1];
                float bri = hsb[2];

                if (hue >= BUTTON_HUE_MIN && hue <= BUTTON_HUE_MAX
                        && sat >= BUTTON_SAT_MIN && bri >= BUTTON_BRI_MIN) {
                    match++;
                }
            }
        }
        return (float) match / total;
    }

    private static void robotClick(Robot robot, int x, int y) {
        robot.mouseMove(x, y);
        robot.delay(150);
        robot.mousePress(InputEvent.BUTTON1_DOWN_MASK);
        robot.delay(50);
        robot.mouseRelease(InputEvent.BUTTON1_DOWN_MASK);
    }
}
