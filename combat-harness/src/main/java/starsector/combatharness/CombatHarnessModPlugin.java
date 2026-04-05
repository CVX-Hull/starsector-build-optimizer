package starsector.combatharness;

import com.fs.starfarer.api.BaseModPlugin;
import com.fs.starfarer.api.Global;

import org.apache.log4j.Logger;

/**
 * Mod entry point. Registered via mod_info.json "modPlugin" field.
 */
public class CombatHarnessModPlugin extends BaseModPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessModPlugin.class);

    @Override
    public void onApplicationLoad() throws Exception {
        log.info("Combat Harness v0.1.0 loaded");

        // Debug: write a test file to discover actual saves/common/ path
        try {
            Global.getSettings().writeTextFileToCommon("combat_harness_probe.txt", "probe");
            log.info("Wrote probe file to saves/common/combat_harness_probe.txt");
        } catch (Exception e) {
            log.error("Failed to write probe file: " + e.getMessage());
        }

        boolean exists = MatchupConfig.existsInCommon();
        log.info("fileExistsInCommon('" + MatchupConfig.COMMON_PREFIX + "matchup.json') = " + exists);

        if (exists) {
            log.info("matchup.json found — Optimizer Arena mission is ready");
        } else {
            log.info("No matchup.json found — write " + MatchupConfig.COMMON_PREFIX + "matchup.json to saves/common/");
        }
    }

    @Override
    public void onDevModeF8Reload() {
        log.info("Combat Harness reloaded via F8");
    }
}
