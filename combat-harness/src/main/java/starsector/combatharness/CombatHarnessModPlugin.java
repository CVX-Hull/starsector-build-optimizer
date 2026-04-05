package starsector.combatharness;

import com.fs.starfarer.api.BaseModPlugin;

import org.apache.log4j.Logger;

/**
 * Mod entry point. Registered via mod_info.json "modPlugin" field.
 */
public class CombatHarnessModPlugin extends BaseModPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessModPlugin.class);

    @Override
    public void onApplicationLoad() throws Exception {
        log.info("Combat Harness v0.2.0 loaded");

        if (MatchupQueue.existsInCommon()) {
            try {
                MatchupQueue queue = MatchupQueue.loadFromCommon();
                log.info("Matchup queue found with " + queue.size() + " matchups — Optimizer Arena mission is ready");
            } catch (Exception e) {
                log.warn("Matchup queue found but failed to parse: " + e.getMessage());
            }
        } else {
            log.info("No matchup queue in saves/common/ — write combat_harness_queue.json.data before running Optimizer Arena");
        }
    }

    @Override
    public void onDevModeF8Reload() {
        log.info("Combat Harness reloaded via F8");
    }
}
