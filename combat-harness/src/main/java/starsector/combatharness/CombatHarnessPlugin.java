package starsector.combatharness;

import com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.combat.ShipAPI;
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

    private enum State { INIT, SPAWNING, FIGHTING, CLEANING, DONE }

    private CombatEngineAPI engine;
    private State state = State.INIT;
    private MatchupQueue queue;
    private int currentIndex = 0;
    private MatchupConfig currentConfig;
    private DamageTracker currentTracker;
    private List<ShipAPI> playerShips = new ArrayList<ShipAPI>();
    private List<ShipAPI> enemyShips = new ArrayList<ShipAPI>();
    private float matchupStartTime;
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

        if (currentIndex == 0) {
            // First matchup: ships already deployed by MissionDefinition.
            // Discover them from the engine's ship list.
            discoverDeployedShips();
        } else {
            // Subsequent matchups: spawn ships mid-combat.
            spawnShips();
        }

        // Point camera at first player ship so the combat is visible
        if (!playerShips.isEmpty()) {
            engine.setPlayerShipExternal(playerShips.get(0));
        }

        matchupStartTime = engine.getTotalElapsedTime(false);
        log.info("  Player ships: " + playerShips.size() + ", Enemy ships: " + enemyShips.size()
                + ", timeMult=" + engine.getTimeMult().getModifiedValue());
        state = State.FIGHTING;
    }

    private void discoverDeployedShips() {
        playerShips.clear();
        enemyShips.clear();
        for (ShipAPI ship : engine.getShips()) {
            if (ship.isFighter()) continue;
            if (ship.getOwner() == 0) {
                playerShips.add(ship);
            } else if (ship.getOwner() == 1) {
                enemyShips.add(ship);
            }
        }
    }

    private void spawnShips() {
        playerShips.clear();
        for (int i = 0; i < currentConfig.playerVariants.length; i++) {
            String variantId = currentConfig.playerVariants[i];
            float yOffset = (i - (currentConfig.playerVariants.length - 1) / 2f) * SHIP_SPACING;
            try {
                ShipAPI ship = engine.getFleetManager(FleetSide.PLAYER)
                        .spawnShipOrWing(variantId, new Vector2f(-2000f, yOffset), 0f);
                if (ship != null) {
                    playerShips.add(ship);
                } else {
                    log.warn("Failed to spawn player variant: " + variantId);
                }
            } catch (Exception e) {
                log.error("Error spawning player variant: " + variantId, e);
            }
        }

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
        // Heartbeat every 60 frames
        if (frameCount % 60 == 0) {
            ResultWriter.writeHeartbeat(engine.getTotalElapsedTime(false));
        }

        // Custom win detection
        int playerAlive = countAlive(playerShips);
        int enemyAlive = countAlive(enemyShips);
        float elapsed = engine.getTotalElapsedTime(false) - matchupStartTime;
        boolean timedOut = elapsed > currentConfig.timeLimitSeconds;

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
            log.info("All " + queue.size() + " matchups complete. Results written.");
        } catch (Exception e) {
            log.error("Failed to write final results", e);
        }
        System.exit(0);
    }

    private int countAlive(List<ShipAPI> ships) {
        int count = 0;
        for (ShipAPI s : ships) {
            if (s.isAlive() && !s.isFighter()) count++;
        }
        return count;
    }
}
