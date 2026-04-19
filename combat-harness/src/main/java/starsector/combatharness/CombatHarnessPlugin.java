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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

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

    private enum State { INIT, SETUP, FIGHTING, DONE, WAITING, PROBE_WAIT, PROBE_RUN }

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
    // Phase 7-prep relaunch — 3 new pre-matchup engine-truth covariates
    private float currentEffHullHpPct = Float.NaN;
    private float currentBallisticRangeBonus = Float.NaN;
    private float currentShieldDamageTakenMult = Float.NaN;
    private int frameCount = 0;

    @Override
    public void init(CombatEngineAPI engine) {
        this.engine = engine;
        log.info("CombatHarnessPlugin.init() called; engine="
                + (engine != null));
    }

    @Override
    public void advance(float amount, List<InputEventAPI> events) {
        if (engine == null) return;
        // Probe states + INIT run regardless of pause — the deployment
        // screen keeps the engine paused while waiting for the AI or
        // player to deploy, and our probe state machine is independent
        // of combat actually advancing (we only need ShipAPI references,
        // which exist once the MissionDefinition has spawned them). The
        // normal matchup states gate on isPaused() because they make
        // decisions based on combat state (flux, damage, winner).
        if (state == State.INIT) { doInit(); return; }
        if (state == State.PROBE_WAIT) { doProbeWait(); return; }
        if (state == State.PROBE_RUN)  { doProbeRun();  return; }

        if (engine.isPaused()) return;
        frameCount++;

        switch (state) {
            case SETUP:      doSetup(); break;
            case FIGHTING:   doFighting(); break;
            case DONE:       doDone(); break;
            case WAITING:    doWaiting(); break;
            default: break;   // INIT/PROBE_* handled above
        }
    }

    private void doInit() {
        // Probe-mode branch: MissionDefinition has spawned one ship per hull
        // size. Skip queue loading entirely, wait a short grace period for
        // the engine to finish deployment, then run the probe and exit.
        if (Global.getSettings().fileExistsInCommon(ManifestDumper.MANIFEST_REQUEST_FILE)) {
            engine.setDoNotEndCombat(true);
            log.info("Combat Harness: manifest-probe request detected; "
                    + "entering PROBE_WAIT (skipping normal queue load)");
            state = State.PROBE_WAIT;
            return;
        }

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

    /* --------------------------------------------------------------------
     * Manifest-probe mode
     * ------------------------------------------------------------------ */

    // Wait up to 30 seconds (1800 frames) for deployment — the engine
    // may sit on the deployment screen until AI auto-deploys all ships.
    // The normal case is <5s, but cap at 30s so a broken mission can't
    // hang the probe indefinitely.
    private static final int PROBE_WAIT_MAX_FRAMES = 1800;
    // Expect one ShipAPI per HullSize bucket we asked MissionDefinition
    // to spawn (FRIGATE / DESTROYER / CRUISER / CAPITAL_SHIP on the
    // player side). Once we see all 4, ships are live enough to probe.
    private static final int PROBE_REQUIRED_HULL_SIZES = 4;
    private int probeWaitFrames = 0;

    /** Advance the probe-wait counter and, once the probe ships have all
     *  materialised in engine.getShips(), transition to PROBE_RUN.
     *  Runs regardless of pause (see advance()) because Starsector keeps
     *  the deployment screen paused until AI auto-deploy completes, and
     *  our frame cadence is our only clock for timing out a stuck probe. */
    private void doProbeWait() {
        probeWaitFrames++;

        // Force-unpause — Starsector's deployment screen stays paused
        // when more than a single flagship is queued up, and no human
        // is here to click "Start". Setting paused=false every frame
        // overrides the engine's pause-on-deployment-screen behaviour.
        try { engine.setPaused(false); } catch (Throwable ignored) {}

        // First frame: force-deploy all player reserves into combat.
        // AI fleet deployment is based on fleet-point economy — with a
        // 4-ship player vs 1-ship enemy, the AI keeps 3 of our ships in
        // reserve because there's nothing for them to fight. We don't
        // need them to fight — we just need live ShipAPI references for
        // the probe. spawnFleetMember from the reserves pool gives us
        // exactly that. Retreat-bug noted in skill is irrelevant here:
        // we exit the JVM before any ship can actually retreat or die.
        if (probeWaitFrames == 1) {
            forceDeployProbeReserves();
        }

        int hullSizesSeen = countPlayerHullSizesInCombat();
        // Heartbeat log every second so a stuck probe surfaces visibly
        // in the game log rather than silently hitting the timeout.
        if (probeWaitFrames % 60 == 0) {
            int totalShips = engine.getShips() == null ? 0 : engine.getShips().size();
            int playerDeployed = 0, playerReserves = 0, enemyDeployed = 0;
            try {
                playerDeployed = engine.getFleetManager(0).getDeployedCopy().size();
                playerReserves = engine.getFleetManager(0).getReservesCopy().size();
                enemyDeployed = engine.getFleetManager(1).getDeployedCopy().size();
            } catch (Throwable ignored) {}
            log.info("ManifestProbe: wait frame=" + probeWaitFrames
                    + " paused=" + engine.isPaused()
                    + " combatOver=" + engine.isCombatOver()
                    + " engineShips=" + totalShips
                    + " playerDeployed=" + playerDeployed
                    + " playerReserves=" + playerReserves
                    + " enemyDeployed=" + enemyDeployed
                    + " hullSizesSeen=" + hullSizesSeen);
        }
        if (hullSizesSeen >= PROBE_REQUIRED_HULL_SIZES) {
            log.info("ManifestProbe: " + hullSizesSeen
                    + " hull sizes deployed after " + probeWaitFrames + " frames");
            state = State.PROBE_RUN;
            return;
        }
        if (probeWaitFrames >= PROBE_WAIT_MAX_FRAMES) {
            log.warn("ManifestProbe: deployment timeout after "
                    + probeWaitFrames + " frames with only " + hullSizesSeen
                    + " hull sizes — running probe with whatever is available");
            state = State.PROBE_RUN;
        }
    }

    private int countPlayerHullSizesInCombat() {
        java.util.HashSet<ShipAPI.HullSize> seen = new java.util.HashSet<ShipAPI.HullSize>();
        if (engine == null) return 0;
        for (ShipAPI ship : engine.getShips()) {
            if (ship.getOwner() != 0) continue;
            if (ship.isFighter()) continue;
            ShipAPI.HullSize size = ship.getHullSize();
            if (size != null) seen.add(size);
        }
        return seen.size();
    }

    /** One-shot helper: take every FleetMemberAPI from the player reserves
     *  and spawn it into combat at spread-out positions. The positions are
     *  spread wide enough that ships don't overlap on deploy, but stay
     *  inside the 16000x12000 combat arena. */
    private void forceDeployProbeReserves() {
        com.fs.starfarer.api.combat.CombatFleetManagerAPI fm =
                engine.getFleetManager(0);
        java.util.List<com.fs.starfarer.api.fleet.FleetMemberAPI> reserves =
                new java.util.ArrayList<com.fs.starfarer.api.fleet.FleetMemberAPI>(
                        fm.getReservesCopy());
        log.info("ManifestProbe: forceDeployProbeReserves — " + reserves.size()
                + " reserve FleetMembers to spawn");
        float y = -4000f;
        for (com.fs.starfarer.api.fleet.FleetMemberAPI fleetMember : reserves) {
            try {
                Vector2f pos = new Vector2f(-6000f, y);
                ShipAPI spawned = fm.spawnFleetMember(fleetMember, pos, 0f, 0f);
                log.info("ManifestProbe:   spawned " + fleetMember.getHullId()
                        + " at y=" + y + " → shipAPI=" + (spawned != null)
                        + " hullSize=" + (spawned == null ? "null"
                                                          : spawned.getHullSize()));
                y += 2000f;
            } catch (Throwable t) {
                log.warn("ManifestProbe: spawnFleetMember failed for "
                        + fleetMember.getHullId() + ": " + t);
            }
        }
    }

    /** Collect one ShipAPI per HullSize from the player side, call
     *  ManifestDumper.dumpToCommon with the map, delete the request
     *  sentinel, endCombat, and exit the JVM. Always exits — success or
     *  failure of the probe must not leave the game hung.
     *
     *  The outer try/finally guarantees System.exit(0) runs even if the
     *  dump throws, because leaving the game running after a probe
     *  request would pin a Starsector JVM on the dev machine forever. */
    private void doProbeRun() {
        try {
            Map<ShipAPI.HullSize, ShipAPI> probeShips =
                    new HashMap<ShipAPI.HullSize, ShipAPI>();
            for (ShipAPI ship : engine.getShips()) {
                if (ship.getOwner() != 0) continue;     // player side only
                if (ship.isFighter()) continue;
                ShipAPI.HullSize size = ship.getHullSize();
                if (size == null) continue;
                // First ship per size wins — deterministic under the
                // MissionDefinition order (wolf, hammerhead, eagle, onslaught).
                if (!probeShips.containsKey(size)) {
                    probeShips.put(size, ship);
                }
            }
            log.info("ManifestProbe: collected " + probeShips.size()
                    + " probe ships: " + probeShips.keySet());

            String gv = Global.getSettings().getVersionString();
            String sha = System.getProperty(
                    "starsector.combatharness.modCommitSha", "unknown");
            ManifestDumper.dumpToCommon(gv, sha, probeShips);

            // Remove the sentinel so a subsequent game boot doesn't re-enter
            // probe mode by accident.
            try {
                Global.getSettings().deleteTextFileFromCommon(
                        ManifestDumper.MANIFEST_REQUEST_FILE);
            } catch (Exception e) {
                log.warn("Failed to delete manifest request sentinel: " + e);
            }
        } catch (Throwable t) {
            log.error("ManifestProbe: dump failed", t);
        } finally {
            // Exit unconditionally — the update_manifest.py driver polls
            // the done sentinel and the process termination together.
            // endCombat(delay, winningSide) — 0 delay, winner irrelevant
            // since the process is about to exit.
            try { engine.endCombat(0f, FleetSide.PLAYER); } catch (Throwable ignored) {}
            System.exit(0);
        }
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

        // Phase 5D + Phase-7-prep — read engine-computed player SETUP stats
        // after loadout swap. These feed the A2′ EB shrinkage regression prior.
        // Any null-path stores NaN so the Python parser flags it as malformed
        // (always-emit policy).
        currentEffMaxFlux = Float.NaN;
        currentEffFluxDissipation = Float.NaN;
        currentEffArmorRating = Float.NaN;
        currentEffHullHpPct = Float.NaN;
        currentBallisticRangeBonus = Float.NaN;
        currentShieldDamageTakenMult = Float.NaN;
        if (!playerShips.isEmpty()) {
            ShipAPI p = playerShips.get(0);
            com.fs.starfarer.api.combat.MutableShipStatsAPI stats = p.getMutableStats();
            com.fs.starfarer.api.combat.ShipHullSpecAPI hull = p.getHullSpec();
            if (stats != null) {
                currentEffMaxFlux = stats.getFluxCapacity().getModifiedValue();
                currentEffFluxDissipation = stats.getFluxDissipation().getModifiedValue();
                if (hull != null) {
                    float baseArmor = hull.getArmorRating();
                    currentEffArmorRating = stats.getArmorBonus().computeEffective(baseArmor);
                    // eff_hull_hp_pct = effective hull HP / base hull HP (ratio form
                    // so per-hull mean cancels across the study, leaving only the
                    // between-build variance — bimodal on Reinforced Bulkheads
                    // / Blast Doors presence).
                    float baseHp = hull.getHitpoints();
                    if (baseHp > 0f) {
                        currentEffHullHpPct =
                                stats.getHullBonus().computeEffective(baseHp) / baseHp;
                    }
                }
                // ballistic_range_bonus: probe with a 1000-unit baseline — the
                // returned value minus 1000 is the additive bonus the engine
                // would apply to a weapon with range=1000. Captures ITU / DTC /
                // Unstable Injector effects authoritatively.
                currentBallisticRangeBonus =
                        stats.getBallisticWeaponRangeBonus().computeEffective(1000f) - 1000f;
                // shield_damage_taken_mult: MutableStat, multiplicative. 1.0 = no
                // hullmods; <1.0 = Hardened Shields / S-mod Front Shield Emitter.
                currentShieldDamageTakenMult =
                        stats.getShieldDamageTakenMult().getModifiedValue();
            }
        }
        log.info("  setup_stats: flux=" + currentEffMaxFlux
                + " diss=" + currentEffFluxDissipation
                + " arm=" + currentEffArmorRating
                + " hp_pct=" + currentEffHullHpPct
                + " ball_range=" + currentBallisticRangeBonus
                + " shield_dmg=" + currentShieldDamageTakenMult);

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
                        currentEffArmorRating,
                        currentEffHullHpPct, currentBallisticRangeBonus,
                        currentShieldDamageTakenMult));
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
