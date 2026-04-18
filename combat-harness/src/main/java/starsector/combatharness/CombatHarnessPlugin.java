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
 * Single-matchup-per-mission combat harness.
 *
 * MissionDefinition adds placeholder ships via addToFleet() for proper deployment.
 * This plugin swaps the player ship's loadout in-place, runs one matchup,
 * writes results, and calls endCombat() to return to the mission screen.
 * Python + Robot-click automation restarts the mission with a new queue.
 */
public class CombatHarnessPlugin extends BaseEveryFrameCombatPlugin {

    private static final Logger log = Logger.getLogger(CombatHarnessPlugin.class);
    private static final String SHUTDOWN_FILE = MatchupConfig.COMMON_PREFIX + "shutdown";
    private static final int WAITING_TIMEOUT_FRAMES = 3600;
    private static final int HEARTBEAT_INTERVAL_FRAMES = 60;
    private static final float MAX_APPROACH_TIME = 30f;

    private enum State { INIT, SETUP, FIGHTING, DONE, WAITING }

    private CombatEngineAPI engine;
    private State state = State.INIT;
    private MatchupQueue queue;
    private MatchupConfig currentConfig;
    private DamageTracker currentTracker;
    private List<ShipAPI> playerShips = new ArrayList<ShipAPI>();
    private List<ShipAPI> enemyShips = new ArrayList<ShipAPI>();
    private float spawnTime;
    private float matchupStartTime;
    private boolean contactMade = false;
    private int waitingFrameCount = 0;
    // Phase 5D — engine-computed player SETUP stats, populated in doSetup()
    private float currentEffMaxFlux = Float.NaN;
    private float currentEffFluxDissipation = Float.NaN;
    private float currentEffArmorRating = Float.NaN;
    private int frameCount = 0;

    @Override
    public void init(CombatEngineAPI engine) {
        this.engine = engine;
    }

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        if (engine == null || engine.isPaused()) return;
        frameCount++;

        switch (state) {
            case INIT:    doInit(); break;
            case SETUP:   doSetup(); break;
            case FIGHTING: doFighting(); break;
            case DONE:    doDone(); break;
            case WAITING: doWaiting(); break;
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
        state = State.SETUP;
    }

    private void doSetup() {
        currentConfig = queue.get(0);  // Single matchup per mission
        log.info("Matchup: " + currentConfig.matchupId
                + " (time_mult=" + currentConfig.timeMult
                + ", time_limit=" + currentConfig.timeLimitSeconds + "s)");

        engine.getTimeMult().modifyMult("harness", currentConfig.timeMult);

        currentTracker = new DamageTracker();
        engine.getListenerManager().addListener(currentTracker);

        // Collect ships deployed by MissionDefinition (addToFleet — proper CR/AI)
        playerShips.clear();
        enemyShips.clear();
        for (ShipAPI ship : engine.getShips()) {
            if (ship.isFighter()) continue;
            if (ship.getOwner() == 0) playerShips.add(ship);
            else if (ship.getOwner() == 1) enemyShips.add(ship);
        }

        // Swap player ship loadout to the real build spec
        for (int i = 0; i < playerShips.size() && i < currentConfig.playerBuilds.length; i++) {
            ShipAPI ship = playerShips.get(i);
            MatchupConfig.BuildSpec spec = currentConfig.playerBuilds[i];

            com.fs.starfarer.api.combat.ShipVariantAPI v = ship.getVariant();
            v.clear();
            for (java.util.Map.Entry<String, String> e : spec.weaponAssignments.entrySet()) {
                v.addWeapon(e.getKey(), e.getValue());
            }
            for (String modId : spec.hullmods) {
                v.addMod(modId);
            }
            v.setNumFluxVents(spec.fluxVents);
            v.setNumFluxCapacitors(spec.fluxCapacitors);
            v.autoGenerateWeaponGroups();

            ship.setCurrentCR(spec.cr);
            ship.setCRAtDeployment(spec.cr);
            log.info("  Swapped loadout: " + spec.variantId
                    + " weapons=" + spec.weaponAssignments.size()
                    + " mods=" + spec.hullmods.length);
        }

        // Phase 5D — read engine-computed player SETUP stats after loadout swap.
        // These feed the A2′ EB shrinkage regression prior. Any null-path stores
        // NaN so the Python parser flags it as malformed (always-emit policy).
        currentEffMaxFlux = Float.NaN;
        currentEffFluxDissipation = Float.NaN;
        currentEffArmorRating = Float.NaN;
        if (!playerShips.isEmpty()) {
            ShipAPI p = playerShips.get(0);
            if (p.getMutableStats() != null) {
                currentEffMaxFlux = p.getMutableStats()
                        .getFluxCapacity().getModifiedValue();
                currentEffFluxDissipation = p.getMutableStats()
                        .getFluxDissipation().getModifiedValue();
                if (p.getHullSpec() != null) {
                    float baseArmor = p.getHullSpec().getArmorRating();
                    currentEffArmorRating = p.getMutableStats()
                            .getArmorBonus().computeEffective(baseArmor);
                }
            }
        }
        log.info("  setup_stats: flux=" + currentEffMaxFlux
                + " diss=" + currentEffFluxDissipation
                + " arm=" + currentEffArmorRating);

        spawnTime = engine.getTotalElapsedTime(false);
        matchupStartTime = 0f;
        contactMade = false;
        log.info("  Player ships: " + playerShips.size() + ", Enemy ships: " + enemyShips.size()
                + ", timeMult=" + engine.getTimeMult().getModifiedValue());
        state = State.FIGHTING;
    }

    private void doFighting() {
        updateCamera();

        if (frameCount % HEARTBEAT_INTERVAL_FRAMES == 0) {
            ResultWriter.writeHeartbeat(
                    engine.getTotalElapsedTime(false),
                    computeAggregateHp(playerShips),
                    computeAggregateHp(enemyShips),
                    countAlive(playerShips),
                    countAlive(enemyShips));
        }

        if (!contactMade) {
            if (engine.isFleetsInContact()) {
                contactMade = true;
                matchupStartTime = engine.getTotalElapsedTime(false);
                log.info("  Contact made for " + currentConfig.matchupId);
            } else if (engine.getTotalElapsedTime(false) - spawnTime > MAX_APPROACH_TIME) {
                contactMade = true;
                matchupStartTime = engine.getTotalElapsedTime(false);
                log.info("  Approach timeout for " + currentConfig.matchupId);
            }
        }

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
            JSONArray results = new JSONArray();
            try {
                results.put(ResultWriter.buildMatchupResult(
                        currentConfig, playerShips, enemyShips,
                        currentTracker, winner, elapsed,
                        currentEffMaxFlux, currentEffFluxDissipation,
                        currentEffArmorRating));
                ResultWriter.writeAllResults(results);
                ResultWriter.writeDoneSignal();
                log.info("Matchup " + currentConfig.matchupId
                        + " complete: winner=" + winner + ", duration=" + elapsed + "s");
            } catch (Exception e) {
                log.error("Failed to write result", e);
            }

            engine.getListenerManager().removeListener(currentTracker);

            // Launch Robot dismiss thread BEFORE endCombat — the engine may stop
            // calling advance() after endCombat, so doDone() might never execute.
            new Thread(new Runnable() {
                public void run() {
                    MenuNavigator.dismissResults();
                }
            }).start();
            log.info("Robot restart thread launched");

            // End combat — game shows mission results screen
            engine.setDoNotEndCombat(false);
            FleetSide winnerSide = "PLAYER".equals(winner) ? FleetSide.PLAYER : FleetSide.ENEMY;
            engine.endCombat(0f, winnerSide);
            log.info("endCombat called — awaiting mission results screen");

            waitingFrameCount = 0;
            state = State.DONE;
        }
    }

    private void doDone() {
        // Fallback: if advance() is still called after endCombat and Robot
        // hasn't been launched yet, this is a no-op since Robot is already running.
        state = State.WAITING;
    }

    private void doWaiting() {
        if (frameCount % HEARTBEAT_INTERVAL_FRAMES == 0) {
            ResultWriter.writeHeartbeat(engine.getTotalElapsedTime(false), 0f, 0f, 0, 0);
        }

        if (Global.getSettings().fileExistsInCommon(SHUTDOWN_FILE)) {
            try {
                Global.getSettings().deleteTextFileFromCommon(SHUTDOWN_FILE);
            } catch (Exception e) { /* ignore */ }
            log.info("Shutdown signal received, exiting.");
            System.exit(0);
        }

        waitingFrameCount++;
        if (waitingFrameCount > WAITING_TIMEOUT_FRAMES) {
            log.info("Waiting timeout, exiting.");
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

    private float computeAggregateHp(List<ShipAPI> ships) {
        if (ships.isEmpty()) return 0f;
        float total = 0f;
        for (ShipAPI s : ships) {
            total += s.getHullLevel();
        }
        return total / ships.size();
    }

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
