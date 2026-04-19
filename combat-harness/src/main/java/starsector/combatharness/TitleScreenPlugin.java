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

    // Wait for title screen to stabilize before checking queue (~2s at 60fps)
    private static final int TITLE_STABILIZE_FRAMES = 120;

    private boolean triggered = false;
    private int frameCount = 0;

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        // Reset when leaving title screen so we can re-trigger on return
        if (Global.getCurrentState() != GameState.TITLE) {
            triggered = false;
            frameCount = 0;
            return;
        }

        if (triggered) return;

        frameCount++;
        if (frameCount < TITLE_STABILIZE_FRAMES) return;

        // Either a queued matchup OR a manifest-probe request funnels through
        // the same Optimizer Arena navigation path — the MissionDefinition
        // branches on the manifest-request sentinel so the probe runs inside
        // a real CombatEngine where ShipAPI instances are live. Doing it at
        // title-screen level fails because HullModEffect.getUnapplicableReason
        // needs a real ship, not a factoried stub.
        boolean probeRequested = Global.getSettings().fileExistsInCommon(
                ManifestDumper.MANIFEST_REQUEST_FILE);
        boolean queuePresent = MatchupQueue.existsInCommon();
        if (!probeRequested && !queuePresent) return;

        triggered = true;
        if (probeRequested) {
            log.info("TitleScreenPlugin: manifest request detected, navigating "
                    + "to Optimizer Arena for combat-context probe...");
        } else {
            log.info("TitleScreenPlugin: queue detected, auto-navigating to Optimizer Arena...");
        }

        // Navigate in a separate thread (Robot delays would block the rendering thread)
        new Thread(new Runnable() {
            public void run() {
                MenuNavigator.navigateToMission();
            }
        }).start();
    }
}
