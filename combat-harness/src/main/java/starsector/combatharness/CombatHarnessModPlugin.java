package starsector.combatharness;

import com.fs.starfarer.api.BaseModPlugin;

import org.apache.log4j.Logger;

import java.io.File;

/**
 * Mod entry point. Registered via mod_info.json "modPlugin" field.
 */
public class CombatHarnessModPlugin extends BaseModPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessModPlugin.class);

    @Override
    public void onApplicationLoad() throws Exception {
        log.info("Combat Harness v0.1.0 loaded");

        File workdir = new File("mods/combat-harness/workdir");
        if (!workdir.exists()) {
            log.warn("Workdir not found: " + workdir.getAbsolutePath());
        } else {
            File matchup = new File(workdir, "matchup.json");
            if (matchup.exists()) {
                log.info("matchup.json found — Optimizer Arena mission is ready");
            } else {
                log.info("No matchup.json in workdir — place one before running Optimizer Arena");
            }
        }
    }

    @Override
    public void onDevModeF8Reload() {
        log.info("Combat Harness reloaded via F8");
    }
}
