package starsector.combatharness;

import com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.input.InputEventAPI;

import org.apache.log4j.Logger;

import java.io.File;
import java.io.FileWriter;
import java.util.List;

/**
 * EveryFrameCombatPlugin that monitors combat, tracks damage, and writes results.
 *
 * One matchup per game launch. After combat ends (or time limit), writes result.json
 * and calls System.exit(0).
 */
public class CombatHarnessPlugin extends BaseEveryFrameCombatPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessPlugin.class);

    private CombatEngineAPI engine;
    private MatchupConfig config;
    private DamageTracker damageTracker;
    private boolean resultsWritten = false;
    private int frameCount = 0;
    private final File workdir;

    public CombatHarnessPlugin(File workdir) {
        this.workdir = workdir;
    }

    @Override
    public void init(CombatEngineAPI engine) {
        this.engine = engine;

        try {
            config = MatchupConfig.fromFile(new File(workdir, "matchup.json"));
        } catch (Exception e) {
            log.error("Failed to load matchup config", e);
            return;
        }

        // Apply time acceleration
        engine.getTimeMult().modifyMult("harness", config.timeMult);

        // Register damage tracker
        damageTracker = new DamageTracker();
        engine.getListenerManager().addListener(damageTracker);

        log.info("Combat Harness initialized for matchup " + config.matchupId
                + " (time_mult=" + config.timeMult + ", time_limit=" + config.timeLimitSeconds + "s)");
    }

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        if (engine == null || engine.isPaused()) return;
        if (config == null) return;  // config failed to load

        frameCount++;

        // Update heartbeat
        if (frameCount % 60 == 0) {
            writeHeartbeat();
        }

        // Check combat end
        boolean combatOver = engine.isCombatOver();
        float elapsed = engine.getTotalElapsedTime(false);
        boolean timedOut = elapsed > config.timeLimitSeconds;

        if ((combatOver || timedOut) && !resultsWritten) {
            try {
                ResultWriter.writeResult(engine, damageTracker, config, workdir, timedOut);
                resultsWritten = true;
                log.info("Results written for matchup " + config.matchupId
                        + " (winner=" + (timedOut ? "TIMEOUT" : (engine.getWinningSideId() == 0 ? "PLAYER" : "ENEMY"))
                        + ", duration=" + elapsed + "s)");
            } catch (Exception e) {
                log.error("Failed to write results for matchup " + config.matchupId, e);
            }

            System.exit(resultsWritten ? 0 : 1);
        }
    }

    private void writeHeartbeat() {
        try {
            File heartbeat = new File(workdir, "heartbeat.txt");
            try (FileWriter fw = new FileWriter(heartbeat)) {
                fw.write(System.currentTimeMillis() + " " + engine.getTotalElapsedTime(false));
            }
        } catch (Exception e) {
            // Heartbeat failure is non-fatal
            log.debug("Failed to write heartbeat", e);
        }
    }
}
