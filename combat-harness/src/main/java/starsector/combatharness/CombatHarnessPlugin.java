package starsector.combatharness;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.BaseEveryFrameCombatPlugin;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.combat.CombatFleetManagerAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipHullSpecAPI.ShipTypeHints;
import com.fs.starfarer.api.combat.ShipVariantAPI;
import com.fs.starfarer.api.combat.ViewportAPI;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.input.InputEventAPI;
import com.fs.starfarer.api.loading.HullModSpecAPI;
import com.fs.starfarer.api.mission.FleetSide;

import org.json.JSONArray;
import org.lwjgl.util.vector.Vector2f;

import org.apache.log4j.Logger;

import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

import java.io.IOException;
import java.io.InputStream;

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

    // Commit G: PROBE_RUN replaced by PROBE_ITERATE (two-phase:
    // base-applicability per-hull, then conditional-exclusions per-hull).
    private enum State { INIT, SETUP, FIGHTING, DONE, WAITING,
                         PROBE_WAIT, PROBE_ITERATE }
    private enum ProbePhase { BASE, CONDITIONAL }

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
        if (state == State.PROBE_ITERATE) { doProbeIterate(); return; }

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
     * Manifest-probe mode — two-phase per-hull probe (Commit G)
     *
     * Phase 1 (BASE): spawn each non-skip hull off-map in batches of
     *   HULLS_PER_FRAME_BASE, call isApplicableToShip for every mod on
     *   that ship, record the yes-set. Double-probe each ship for
     *   determinism; any mod whose two probes diverge is recorded in
     *   `statefulMods` (determinism invariant per audit R10). After
     *   probe: `engine.removeEntity(ship)` + `fm.removeDeployed` —
     *   documented despawn path (CombatEngineAPI.java:63), not the
     *   weaker setHitpoints(0f) (audit R3).
     *
     * Phase 2 (CONDITIONAL): for each hull whose base-applicable set is
     *   non-empty, spawn a FRESH probe ship, and for each mod A install
     *   A on the variant then re-probe every B in the base set — any B
     *   that drops applicability with A present is recorded as a
     *   conditional exclusion. removeMod(A) before next iteration (wrapped
     *   in try/finally so a thrown removeMod doesn't leave state behind).
     *   Fresh variants per hull prevent cross-talk with the determinism
     *   double-probe (audit R10).
     *
     * Skip-filter (audit R2): HullSize.FIGHTER, HullSize.DEFAULT sentinel,
     *   ShipTypeHints.{STATION, MODULE, SHIP_WITH_MODULES, HIDE_IN_CODEX}.
     *   Matches opponent_pool.py:34 + the two module-hints the Python
     *   side missed. D-hulls KEPT — pass all four hint filters and the
     *   optimizer may eventually propose on salvaged hulls; a hard-coded
     *   blacklist defeats manifest-as-oracle.
     * ------------------------------------------------------------------ */

    // Commit G constants (no magic numbers — .claude/skills/design-invariants.md).
    private static final int HULLS_PER_FRAME_BASE = 10;
    private static final int HULLS_PER_FRAME_CONDITIONAL = 1;
    private static final float PROBE_OFFMAP_X = -50000f;   // outside collision grid
    private static final float PROBE_Y_SPACING = 1000f;    // vertical stride per in-batch ship
    private static final int PROBE_MAX_FRAMES = 1200;      // ≈20s @ 60fps, hard cap
    private static final int PROBE_WAIT_MAX_FRAMES = 300;  // ≈5s stub-deploy wait

    private int probeWaitFrames = 0;
    private int probeFrameCount = 0;
    private ProbePhase probePhase = ProbePhase.BASE;

    // Base-phase ordered iterator (deterministic — sorted by hullId).
    private Iterator<ShipHullSpecAPI> probeHullIter;
    // Phase 1 output: hullId → applicable mod IDs.
    private final Map<String, Set<String>> applicableByHull =
            new TreeMap<String, Set<String>>();
    // Phase 2 output: hullId → (installed mod A → mods that drop out).
    private final Map<String, Map<String, Set<String>>> condExclByHull =
            new TreeMap<String, Map<String, Set<String>>>();
    // Determinism canary: mod IDs whose two probes disagreed.
    private final Set<String> statefulMods = new TreeSet<String>();
    // Phase 2 work queue (filled after Phase 1 completes).
    private Iterator<String> condHullQueueIter;

    /** Wait for the MissionDefinition stub (wolf + lasher) to deploy,
     *  then transition to PROBE_ITERATE. Normal: 1-2s. Force-unpauses
     *  every frame because useDefaultAI=false keeps the deploy screen
     *  paused waiting for a human that isn't there. */
    private void doProbeWait() {
        probeWaitFrames++;
        try { engine.setPaused(false); } catch (Throwable ignored) {}

        int deployed = 0;
        try {
            deployed = engine.getFleetManager(0).getDeployedCopy().size();
        } catch (Throwable ignored) {}

        if (probeWaitFrames % HEARTBEAT_INTERVAL_FRAMES == 0) {
            log.info("ManifestProbe: wait frame=" + probeWaitFrames
                    + " stubDeployed=" + deployed);
        }

        // Stub wolf deployed? ready to start iteration.
        if (deployed >= 1) {
            log.info("ManifestProbe: stub deployed after "
                    + probeWaitFrames + " frames; entering PROBE_ITERATE");
            beginProbeIterate();
            state = State.PROBE_ITERATE;
            return;
        }
        if (probeWaitFrames >= PROBE_WAIT_MAX_FRAMES) {
            log.warn("ManifestProbe: stub-deploy timeout after "
                    + probeWaitFrames + " frames; proceeding anyway");
            beginProbeIterate();
            state = State.PROBE_ITERATE;
        }
    }

    /** One-shot setup at BASE-phase entry. Pre-filters + sorts hull specs
     *  so iteration order is deterministic across regens (audit concern
     *  #10: JVM hash-seed shouldn't change manifest output bytes). */
    private void beginProbeIterate() {
        List<ShipHullSpecAPI> hulls = new ArrayList<ShipHullSpecAPI>(
                Global.getSettings().getAllShipHullSpecs());
        java.util.Collections.sort(hulls, new java.util.Comparator<ShipHullSpecAPI>() {
            public int compare(ShipHullSpecAPI a, ShipHullSpecAPI b) {
                return a.getHullId().compareTo(b.getHullId());
            }
        });
        List<ShipHullSpecAPI> filtered = new ArrayList<ShipHullSpecAPI>();
        for (ShipHullSpecAPI h : hulls) {
            if (shouldSkipHull(h)) continue;
            filtered.add(h);
        }
        log.info("ManifestProbe: begin iterate — " + filtered.size()
                + " hulls after skip filter (from " + hulls.size() + " total)");
        this.probeHullIter = filtered.iterator();
        this.probePhase = ProbePhase.BASE;
        this.probeFrameCount = 0;
    }

    /** Skip-filter per audit R2. Excludes fighter + station + module +
     *  ship-with-modules + codex-hidden hulls. */
    private static boolean shouldSkipHull(ShipHullSpecAPI h) {
        ShipAPI.HullSize size = h.getHullSize();
        if (size == null) return true;
        if (size == ShipAPI.HullSize.FIGHTER || size == ShipAPI.HullSize.DEFAULT) {
            return true;
        }
        EnumSet<ShipTypeHints> hints = h.getHints();
        if (hints == null) return false;
        return hints.contains(ShipTypeHints.STATION)
            || hints.contains(ShipTypeHints.MODULE)
            || hints.contains(ShipTypeHints.SHIP_WITH_MODULES)
            || hints.contains(ShipTypeHints.HIDE_IN_CODEX);
    }

    /** Per-frame dispatcher — drives BASE or CONDITIONAL sub-phase. */
    private void doProbeIterate() {
        probeFrameCount++;
        if (probeFrameCount > PROBE_MAX_FRAMES) {
            log.error("PROBE_ITERATE frame cap exceeded (PROBE_MAX_FRAMES="
                    + PROBE_MAX_FRAMES + "); dumping partial ("
                    + applicableByHull.size() + " base + "
                    + condExclByHull.size() + " cond hulls)");
            finishAndExit();
            return;
        }
        try { engine.setPaused(false); } catch (Throwable ignored) {}
        if (probePhase == ProbePhase.BASE) {
            doProbeBaseBatch();
        } else {
            doProbeConditionalBatch();
        }
    }

    /** One frame of Phase-1 base probing: spawn up to HULLS_PER_FRAME_BASE
     *  hulls, probe each twice (determinism), record yes-sets, despawn. */
    private void doProbeBaseBatch() {
        CombatFleetManagerAPI fm = engine.getFleetManager(0);
        int spawned = 0;
        while (spawned < HULLS_PER_FRAME_BASE && probeHullIter.hasNext()) {
            ShipHullSpecAPI hullSpec = probeHullIter.next();
            ShipAPI ship = spawnProbeShip(fm, hullSpec, spawned);
            if (ship == null) {
                log.warn("ManifestProbe: spawn returned null for "
                        + hullSpec.getHullId() + "; skipping");
                continue;
            }
            spawned++;
            Set<String> yes1 = probeModsOnShip(ship);
            Set<String> yes2 = probeModsOnShip(ship);
            if (!yes1.equals(yes2)) {
                Set<String> diff = new HashSet<String>();
                for (String id : yes1) if (!yes2.contains(id)) diff.add(id);
                for (String id : yes2) if (!yes1.contains(id)) diff.add(id);
                log.error("ManifestProbe: STATEFUL mods on "
                        + hullSpec.getHullId() + ": " + diff);
                statefulMods.addAll(diff);
            }
            applicableByHull.put(hullSpec.getHullId(), yes1);
            despawnProbeShip(fm, ship, hullSpec.getHullId());
        }
        if (!probeHullIter.hasNext()) {
            log.info("ManifestProbe: base phase complete — "
                    + applicableByHull.size() + " hulls probed, "
                    + statefulMods.size() + " stateful mods flagged");
            this.condHullQueueIter = applicableByHull.keySet().iterator();
            this.probePhase = ProbePhase.CONDITIONAL;
        }
    }

    /** One frame of Phase-2 conditional-exclusion probing: pick one hull,
     *  spawn fresh, for each A install-probe-all-B-remove, record drops. */
    private void doProbeConditionalBatch() {
        CombatFleetManagerAPI fm = engine.getFleetManager(0);
        int processed = 0;
        while (processed < HULLS_PER_FRAME_CONDITIONAL && condHullQueueIter.hasNext()) {
            String hullId = condHullQueueIter.next();
            Set<String> baseApplicable = applicableByHull.get(hullId);
            if (baseApplicable == null || baseApplicable.size() < 2) continue;

            ShipHullSpecAPI hullSpec = Global.getSettings().getHullSpec(hullId);
            if (hullSpec == null) {
                log.warn("ManifestProbe: hullSpec lookup null for " + hullId);
                continue;
            }
            ShipAPI ship = spawnProbeShip(fm, hullSpec, processed);
            if (ship == null) continue;
            processed++;

            Map<String, Set<String>> condThisHull =
                    new TreeMap<String, Set<String>>();
            List<String> baseList = new ArrayList<String>(baseApplicable);
            java.util.Collections.sort(baseList);
            ShipVariantAPI variant = ship.getVariant();
            for (String a : baseList) {
                boolean installed = false;
                try {
                    variant.addMod(a);
                    installed = true;
                    Set<String> dropouts = new TreeSet<String>();
                    for (String b : baseList) {
                        if (b.equals(a)) continue;
                        HullModSpecAPI bSpec = Global.getSettings().getHullModSpec(b);
                        if (bSpec == null) continue;
                        try {
                            if (!bSpec.getEffect().isApplicableToShip(ship)) {
                                dropouts.add(b);
                            }
                        } catch (Throwable t) {
                            log.warn("cond-probe(" + a + " on " + hullId
                                    + ", probe " + b + "): " + t);
                        }
                    }
                    if (!dropouts.isEmpty()) condThisHull.put(a, dropouts);
                } catch (Throwable t) {
                    log.warn("cond-probe: addMod(" + a + ", " + hullId
                            + ") failed: " + t);
                } finally {
                    if (installed) {
                        try { variant.removeMod(a); } catch (Throwable ignored) {}
                    }
                }
            }
            condExclByHull.put(hullId, condThisHull);
            despawnProbeShip(fm, ship, hullId);
        }
        if (!condHullQueueIter.hasNext()) {
            log.info("ManifestProbe: conditional phase complete — "
                    + condExclByHull.size() + " hulls have exclusion entries");
            finishAndExit();
        }
    }

    /** Create an empty-variant FleetMember + spawn off-map. Built-ins
     *  inherit automatically via the hullSpec link (ShipVariantAPI.java:30–33). */
    private ShipAPI spawnProbeShip(CombatFleetManagerAPI fm,
                                   ShipHullSpecAPI hullSpec, int batchSlot) {
        try {
            ShipVariantAPI v = Global.getSettings()
                    .createEmptyVariant(hullSpec.getHullId() + "_probe", hullSpec);
            if (v == null) return null;
            FleetMemberAPI member = Global.getSettings()
                    .createFleetMember(FleetMemberType.SHIP, v);
            if (member == null) return null;
            member.getRepairTracker().setCR(ManifestDumper.DEFAULT_CR);
            Vector2f pos = new Vector2f(PROBE_OFFMAP_X, batchSlot * PROBE_Y_SPACING);
            return fm.spawnFleetMember(member, pos, 0f, 0f);
        } catch (Throwable t) {
            log.warn("spawnProbeShip(" + hullSpec.getHullId() + "): " + t);
            return null;
        }
    }

    /** Documented despawn path — `engine.removeEntity` + `removeDeployed`
     *  (audit R3). setHitpoints(0f) leaves the ship in the
     *  death-animation state; these two calls retire it immediately. */
    private void despawnProbeShip(CombatFleetManagerAPI fm, ShipAPI ship, String hullId) {
        try { engine.removeEntity(ship); } catch (Throwable t) {
            log.warn("removeEntity(" + hullId + "): " + t);
        }
        try { fm.removeDeployed(ship, false); } catch (Throwable t) {
            log.warn("removeDeployed(" + hullId + "): " + t);
        }
    }

    /** Probe every hullmod's isApplicableToShip against the given ship.
     *  Returns a fresh HashSet of applicable mod IDs. Exceptions from
     *  individual mod effects are swallowed + logged (mod bugs ≠ probe bug). */
    private Set<String> probeModsOnShip(ShipAPI ship) {
        Set<String> yes = new HashSet<String>();
        for (HullModSpecAPI m : Global.getSettings().getAllHullModSpecs()) {
            try {
                if (m.getEffect().isApplicableToShip(ship)) yes.add(m.getId());
            } catch (Throwable t) {
                log.warn("probeModsOnShip(" + m.getId() + ", "
                        + ship.getHullSpec().getHullId() + "): " + t);
            }
        }
        return yes;
    }

    /** Read the git SHA baked into this jar at build time. Gradle's
     *  `generateBuildInfo` task writes this resource; if it's missing
     *  the jar was built outside the supported workflow and the
     *  manifest it produces would fail the preflight dual-check. Fail
     *  loudly rather than silently embed a bogus SHA. */
    private static String readModCommitSha() {
        String resource = "/combat-harness-build-info.properties";
        try (InputStream in = CombatHarnessPlugin.class.getResourceAsStream(resource)) {
            if (in == null) {
                throw new IllegalStateException(
                        resource + " missing from jar. Rebuild via "
                        + "`./gradlew clean jar` so generateBuildInfo runs.");
            }
            Properties p = new Properties();
            p.load(in);
            String sha = p.getProperty("gitSha");
            if (sha == null || sha.isEmpty()) {
                throw new IllegalStateException(
                        resource + " has empty gitSha. Rebuild from a "
                        + "clean git checkout.");
            }
            return sha;
        } catch (IOException e) {
            throw new IllegalStateException(
                    "Failed to read " + resource, e);
        }
    }

    /** Dump the manifest + exit. Always exits the JVM — never leaves
     *  the game hung regardless of success or failure. */
    private void finishAndExit() {
        try {
            String gv = Global.getSettings().getVersionString();
            String sha = readModCommitSha();
            ManifestDumper.dumpToCommon(gv, sha,
                    applicableByHull, condExclByHull, statefulMods);
            try {
                Global.getSettings().deleteTextFileFromCommon(
                        ManifestDumper.MANIFEST_REQUEST_FILE);
            } catch (Exception e) {
                log.warn("Failed to delete manifest request sentinel: " + e);
            }
        } catch (Throwable t) {
            log.error("ManifestProbe: dump failed", t);
        } finally {
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
