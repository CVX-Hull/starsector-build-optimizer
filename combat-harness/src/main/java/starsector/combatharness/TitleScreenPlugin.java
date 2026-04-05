package starsector.combatharness;

import com.fs.starfarer.api.GameState;
import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.input.InputEventAPI;

import org.apache.log4j.Logger;

import java.util.List;

/**
 * Global EveryFrameCombatPlugin registered via data/config/settings.json.
 * Runs on the title screen (which is a combat scene in Starsector).
 *
 * When a matchup queue is detected in saves/common/, uses MenuNavigator
 * to automatically click through menus and start the Optimizer Arena mission.
 */
public class TitleScreenPlugin extends BaseEveryFrameCombatPlugin {

    private static final Logger log = Logger.getLogger(TitleScreenPlugin.class);

    private boolean triggered = false;
    private int frameCount = 0;

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        if (triggered) return;

        // Only act on title screen
        if (Global.getCurrentState() != GameState.TITLE) return;

        frameCount++;
        // Wait ~2s for title screen to fully stabilize (120 frames at 60fps)
        if (frameCount < 120) return;

        // Check for queue file
        if (!MatchupQueue.existsInCommon()) return;

        triggered = true;
        log.info("TitleScreenPlugin: queue detected, auto-navigating to Optimizer Arena...");

        // Navigate in a separate thread (Robot delays would block the rendering thread)
        new Thread(new Runnable() {
            public void run() {
                MenuNavigator.navigateToMission();
            }
        }).start();
    }
}
