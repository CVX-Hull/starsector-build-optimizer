package starsector.combatharness;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.combat.ViewportAPI;
import com.fs.starfarer.api.input.InputEventAPI;
import com.fs.starfarer.api.mission.FleetSide;

import org.json.JSONArray;
import org.lwjgl.util.vector.Vector2f;

import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.List;

/**
 * State machine that cycles through a batch of matchups in a single combat session.
 *
 * States: INIT → SPAWNING → FIGHTING → CLEANING → SPAWNING → ... → DONE
 *
 * The first matchup's ships are added by MissionDefinition (required for the
 * deployment screen). Subsequent matchups use spawnShipOrWing() mid-combat.
 */
public class CombatHarnessPlugin extends BaseEveryFrameCombatPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessPlugin.class);
    private static final float SHIP_SPACING = 800f;
    private static final String STOP_FILE = MatchupConfig.COMMON_PREFIX + "stop";
    private static final String NEW_QUEUE_FILE = MatchupConfig.COMMON_PREFIX + "new_queue";
    private static final String SHUTDOWN_FILE = MatchupConfig.COMMON_PREFIX + "shutdown";
    private static final int WAITING_TIMEOUT_FRAMES = 3600;  // ~60s at 60fps
    private static final int HEARTBEAT_INTERVAL_FRAMES = 60;

    private enum State { INIT, SPAWNING, FIGHTING, CLEANING, DONE, WAITING }

    private CombatEngineAPI engine;
    private State state = State.INIT;
    private MatchupQueue queue;
    private int currentIndex = 0;
    private MatchupConfig currentConfig;
    private DamageTracker currentTracker;
    private List<ShipAPI> playerShips = new ArrayList<ShipAPI>();
    private List<ShipAPI> enemyShips = new ArrayList<ShipAPI>();
    private float spawnTime;              // when ships were spawned (for approach timeout)
    private float matchupStartTime;       // when fleets made contact (for combat timeout)
    private boolean contactMade = false;
    private boolean isFirstBatch = true;  // true only for very first batch; NOT reset on WAITING→INIT
    private int waitingFrameCount = 0;
    private static final float MAX_APPROACH_TIME = 30f;  // force timeout if no contact in 30s
    private JSONArray allResults = new JSONArray();
    private int frameCount = 0;
    private int cleanupFramesLeft = 0;

    @Override
    public void init(CombatEngineAPI engine) {
        this.engine = engine;
    }

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        if (engine == null || engine.isPaused()) return;

        frameCount++;

        switch (state) {
            case INIT:
                doInit();
                break;
            case SPAWNING:
                doSpawning();
                break;
            case FIGHTING:
                doFighting();
                break;
            case CLEANING:
                doCleaning();
                break;
            case DONE:
                doDone();
                break;
            case WAITING:
                doWaiting();
                break;
        }
    }

    private void doInit() {
        try {
            queue = MatchupQueue.loadFromCommon();
        } catch (Exception e) {
            log.error("Failed to load matchup queue", e);
            System.exit(1);
            return;
        }

        engine.setDoNotEndCombat(true);
        log.info("Combat Harness: loaded queue with " + queue.size() + " matchups");
        state = State.SPAWNING;
    }

    private void doSpawning() {
        currentConfig = queue.get(currentIndex);
        log.info("Matchup " + (currentIndex + 1) + "/" + queue.size()
                + ": " + currentConfig.matchupId
                + " (time_mult=" + currentConfig.timeMult
                + ", time_limit=" + currentConfig.timeLimitSeconds + "s)");

        // Apply time multiplier
        engine.getTimeMult().modifyMult("harness", currentConfig.timeMult);

        // Create and register damage tracker
        currentTracker = new DamageTracker();
        engine.getListenerManager().addListener(currentTracker);

        if (isFirstBatch) {
            // Very first matchup of game session: MissionDefinition added
            // placeholder ships for the deployment screen. Remove them.
            removePlaceholderShips();
            isFirstBatch = false;
        }
        spawnShips();

        spawnTime = engine.getTotalElapsedTime(false);
        matchupStartTime = 0f;
        contactMade = false;
        log.info("  Player ships: " + playerShips.size() + ", Enemy ships: " + enemyShips.size()
                + ", timeMult=" + engine.getTimeMult().getModifiedValue());
        state = State.FIGHTING;
    }

    private void removePlaceholderShips() {
        for (ShipAPI ship : engine.getShips()) {
            if (ship.isFighter()) continue;
            if (ship.getOwner() == 0) {
                engine.removeEntity(ship);
            }
        }
    }

    private void ensureCombatReady(ShipAPI ship, float cr) {
        if (ship.getCurrentCR() < cr) {
            ship.setCurrentCR(cr);
            ship.setCRAtDeployment(cr);
        }
    }

    private void spawnShips() {
        // Player ships: construct programmatically from build specs
        playerShips.clear();
        for (int i = 0; i < currentConfig.playerBuilds.length; i++) {
            MatchupConfig.BuildSpec spec = currentConfig.playerBuilds[i];
            float yOffset = (i - (currentConfig.playerBuilds.length - 1) / 2f) * SHIP_SPACING;
            try {
                com.fs.starfarer.api.fleet.FleetMemberAPI member =
                        VariantBuilder.createFleetMember(spec);
                ShipAPI ship = engine.getFleetManager(FleetSide.PLAYER)
                        .spawnFleetMember(member, new Vector2f(-2000f, yOffset), 0f, 0f);
                if (ship != null) {
                    playerShips.add(ship);
                    ensureCombatReady(ship, spec.cr);
                } else {
                    log.warn("Failed to spawn player build: " + spec.variantId);
                }
            } catch (Exception e) {
                log.error("Error spawning player build: " + spec.variantId, e);
            }
        }

        // Enemy ships: use stock variant IDs
        enemyShips.clear();
        for (int i = 0; i < currentConfig.enemyVariants.length; i++) {
            String variantId = currentConfig.enemyVariants[i];
            float yOffset = (i - (currentConfig.enemyVariants.length - 1) / 2f) * SHIP_SPACING;
            try {
                ShipAPI ship = engine.getFleetManager(FleetSide.ENEMY)
                        .spawnShipOrWing(variantId, new Vector2f(2000f, yOffset), 180f);
                if (ship != null) {
                    enemyShips.add(ship);
                } else {
                    log.warn("Failed to spawn enemy variant: " + variantId);
                }
            } catch (Exception e) {
                log.error("Error spawning enemy variant: " + variantId, e);
            }
        }
    }

    private void doFighting() {
        // Center camera on midpoint between player and enemy ships
        updateCamera();

        // Heartbeat every HEARTBEAT_INTERVAL_FRAMES (enriched with HP fractions)
        if (frameCount % HEARTBEAT_INTERVAL_FRAMES == 0) {
            ResultWriter.writeHeartbeat(
                    engine.getTotalElapsedTime(false),
                    computeAggregateHp(playerShips),
                    computeAggregateHp(enemyShips),
                    countAlive(playerShips),
                    countAlive(enemyShips));
        }

        // Check for stop signal from Python curtailment monitor
        if (Global.getSettings().fileExistsInCommon(STOP_FILE)) {
            try {
                Global.getSettings().deleteTextFileFromCommon(STOP_FILE);
            } catch (Exception e) {
                // Best effort cleanup
            }
            float elapsed = contactMade ? engine.getTotalElapsedTime(false) - matchupStartTime : 0f;
            try {
                allResults.put(ResultWriter.buildMatchupResult(
                        currentConfig, playerShips, enemyShips,
                        currentTracker, "STOPPED", elapsed));
                log.info("Matchup " + currentConfig.matchupId
                        + " stopped by curtailment, duration=" + elapsed + "s");
            } catch (Exception e) {
                log.error("Failed to build result for stopped " + currentConfig.matchupId, e);
            }
            state = State.CLEANING;
            cleanupFramesLeft = 3;
            return;
        }

        // Start timer only once fleets engage (approach time doesn't count)
        if (!contactMade) {
            if (engine.isFleetsInContact()) {
                contactMade = true;
                matchupStartTime = engine.getTotalElapsedTime(false);
                log.info("  Contact made for " + currentConfig.matchupId);
            } else if (engine.getTotalElapsedTime(false) - spawnTime > MAX_APPROACH_TIME) {
                // Evasive AI never engaged — force contact timer to start
                contactMade = true;
                matchupStartTime = engine.getTotalElapsedTime(false);
                log.info("  Approach timeout for " + currentConfig.matchupId + " — forcing combat timer start");
            }
        }

        // Custom win detection
        int playerAlive = countAlive(playerShips);
        int enemyAlive = countAlive(enemyShips);
        float elapsed = contactMade ? engine.getTotalElapsedTime(false) - matchupStartTime : 0f;
        boolean timedOut = contactMade && elapsed > currentConfig.timeLimitSeconds;

        String winner = null;
        if (playerAlive == 0 && enemyAlive > 0) {
            winner = "ENEMY";
        } else if (enemyAlive == 0 && playerAlive > 0) {
            winner = "PLAYER";
        } else if (playerAlive == 0 && enemyAlive == 0) {
            winner = "TIMEOUT";
        } else if (timedOut) {
            winner = "TIMEOUT";
        }

        if (winner != null) {
            try {
                allResults.put(ResultWriter.buildMatchupResult(
                        currentConfig, playerShips, enemyShips,
                        currentTracker, winner, elapsed));
                log.info("Matchup " + currentConfig.matchupId
                        + " complete: winner=" + winner + ", duration=" + elapsed + "s");
            } catch (Exception e) {
                log.error("Failed to build result for " + currentConfig.matchupId, e);
            }
            state = State.CLEANING;
            cleanupFramesLeft = 3;
        }
    }

    private void doCleaning() {
        if (cleanupFramesLeft > 0) {
            if (cleanupFramesLeft == 3) {
                // Remove all entities
                for (ShipAPI ship : new ArrayList<ShipAPI>(engine.getShips())) {
                    engine.removeEntity(ship);
                }
                for (Object proj : new ArrayList<Object>(engine.getProjectiles())) {
                    if (proj instanceof com.fs.starfarer.api.combat.CombatEntityAPI) {
                        engine.removeEntity((com.fs.starfarer.api.combat.CombatEntityAPI) proj);
                    }
                }
                for (Object missile : new ArrayList<Object>(engine.getMissiles())) {
                    if (missile instanceof com.fs.starfarer.api.combat.CombatEntityAPI) {
                        engine.removeEntity((com.fs.starfarer.api.combat.CombatEntityAPI) missile);
                    }
                }

                // Unregister damage tracker
                engine.getListenerManager().removeListener(currentTracker);
                playerShips.clear();
                enemyShips.clear();
            }
            cleanupFramesLeft--;
            return;
        }

        currentIndex++;
        if (currentIndex < queue.size()) {
            state = State.SPAWNING;
        } else {
            state = State.DONE;
        }
    }

    private void doDone() {
        try {
            ResultWriter.writeAllResults(allResults);
            ResultWriter.writeDoneSignal();
            log.info("All " + queue.size() + " matchups complete. Results written. Entering WAITING state.");
        } catch (Exception e) {
            log.error("Failed to write final results", e);
            System.exit(1);
            return;
        }
        waitingFrameCount = 0;
        state = State.WAITING;
    }

    private void doWaiting() {
        // Heartbeat to prove liveness (zeros = no ships in combat)
        if (frameCount % HEARTBEAT_INTERVAL_FRAMES == 0) {
            ResultWriter.writeHeartbeat(engine.getTotalElapsedTime(false), 0f, 0f, 0, 0);
        }

        // Check shutdown signal (priority over new queue)
        if (Global.getSettings().fileExistsInCommon(SHUTDOWN_FILE)) {
            try {
                Global.getSettings().deleteTextFileFromCommon(SHUTDOWN_FILE);
            } catch (Exception e) {
                log.warn("Failed to delete shutdown signal", e);
            }
            log.info("Shutdown signal received, exiting.");
            System.exit(0);
        }

        // Check new queue signal
        if (Global.getSettings().fileExistsInCommon(NEW_QUEUE_FILE)) {
            try {
                Global.getSettings().deleteTextFileFromCommon(NEW_QUEUE_FILE);
            } catch (Exception e) {
                log.warn("Failed to delete new queue signal", e);
            }
            allResults = new JSONArray();
            currentIndex = 0;
            state = State.INIT;
            log.info("New queue signal received, loading next batch.");
            return;
        }

        // Timeout — exit cleanly if no work arrives
        waitingFrameCount++;
        if (waitingFrameCount > WAITING_TIMEOUT_FRAMES) {
            log.info("Waiting timeout (" + WAITING_TIMEOUT_FRAMES + " frames), exiting.");
            System.exit(0);
        }
    }

    private int countAlive(List<ShipAPI> ships) {
        int count = 0;
        for (ShipAPI s : ships) {
            if (s.isAlive() && !s.isFighter()) count++;
        }
        return count;
    }

    /** Compute aggregate HP fraction across a list of ships (0.0-1.0). */
    private float computeAggregateHp(List<ShipAPI> ships) {
        if (ships.isEmpty()) return 0f;
        float total = 0f;
        for (ShipAPI s : ships) {
            total += s.getHullLevel();  // 0.0 (destroyed) to 1.0 (full HP)
        }
        return total / ships.size();
    }

    /** Center viewport on midpoint of all tracked ships so the fight is visible. */
    private void updateCamera() {
        float sumX = 0f, sumY = 0f;
        int count = 0;
        for (ShipAPI s : playerShips) {
            sumX += s.getLocation().x;
            sumY += s.getLocation().y;
            count++;
        }
        for (ShipAPI s : enemyShips) {
            sumX += s.getLocation().x;
            sumY += s.getLocation().y;
            count++;
        }
        if (count > 0) {
            ViewportAPI vp = engine.getViewport();
            vp.setExternalControl(true);
            float cx = sumX / count;
            float cy = sumY / count;
            vp.set(cx - vp.getVisibleWidth() / 2f, cy - vp.getVisibleHeight() / 2f,
                    vp.getVisibleWidth(), vp.getVisibleHeight());
        }
    }
}
