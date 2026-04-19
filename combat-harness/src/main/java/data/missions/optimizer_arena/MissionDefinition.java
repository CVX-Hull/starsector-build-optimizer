package data.missions.optimizer_arena;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipVariantAPI;
import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import org.apache.log4j.Logger;

import starsector.combatharness.CombatHarnessPlugin;
import starsector.combatharness.ManifestDumper;
import starsector.combatharness.MatchupConfig;
import starsector.combatharness.MatchupQueue;

/**
 * Sets up the first matchup's ships and attaches the CombatHarnessPlugin.
 * The plugin handles subsequent matchups by spawning/removing ships mid-combat.
 *
 * The first matchup's ships MUST be added here (via addToFleet/addFleetMember) because
 * the game shows a "No ships deployed" screen for empty fleets and won't start combat.
 * spawnShipOrWing()/spawnFleetMember() only work once combat is already running.
 */
public class MissionDefinition implements MissionDefinitionPlugin {

    private static final Logger log = Logger.getLogger(MissionDefinition.class);

    public void defineMission(MissionDefinitionAPI api) {
        log.info("optimizer_arena.MissionDefinition: defineMission entered");
        // Manifest-probe branch: triggered by the Python update_manifest.py
        // driver writing combat_harness_manifest_request.data. We need one
        // real ShipAPI per vanilla hull-size bucket (FRIGATE/DESTROYER/CRUISER/
        // CAPITAL_SHIP) so HullModEffect.getUnapplicableReason() can inspect
        // a live ship — the probe cannot run outside a CombatEngine (see
        // ManifestDumper.tryCreateProbeShip comment). The plugin detects
        // probe mode on its own, runs the probe in SETUP, writes the
        // manifest, and exits the process.
        if (Global.getSettings().fileExistsInCommon(
                ManifestDumper.MANIFEST_REQUEST_FILE)) {
            log.info("optimizer_arena.MissionDefinition: probe-mode branch taken");
            // Probe has 4 ships on the player side — with useDefaultAI=true
            // (human controls flagship), Starsector blocks on the deployment
            // screen waiting for the player to deploy 4 ships manually. The
            // normal matchup flow has 1 ship per side which auto-deploys,
            // masking this gotcha. `false` means "AI controls all" → game
            // auto-deploys → combat proceeds → probe can observe ShipAPIs.
            api.initFleet(FleetSide.PLAYER, "PROBE", FleetGoal.ATTACK, false);
            api.initFleet(FleetSide.ENEMY, "DUMMY", FleetGoal.ATTACK, false);
            api.setFleetTagline(FleetSide.PLAYER, "Manifest probe — auto-exits");
            api.setFleetTagline(FleetSide.ENEMY, "Dummy target");

            // CRITICAL: probe ships must use EMPTY variants (no pre-installed
            // hullmods) — stock variants like onslaught_Standard come with
            // dedicated_targeting_core pre-installed, which would flag ITU
            // as incompatible even though ITU is perfectly applicable to a
            // clean Onslaught. createEmptyVariant + addFleetMember gives a
            // stripped baseline where probe results reflect HULL-STRUCTURAL
            // rules only, not stock-variant artifacts.
            addProbeShipEmptyVariant(api, FleetSide.PLAYER, "wolf");
            addProbeShipEmptyVariant(api, FleetSide.PLAYER, "hammerhead");
            addProbeShipEmptyVariant(api, FleetSide.PLAYER, "eagle");
            addProbeShipEmptyVariant(api, FleetSide.PLAYER, "onslaught");

            // Enemy side: single cheap ship — required for combat to start
            // (engine refuses a single-sided deployment). Empty variant too
            // for consistency and to avoid any pollution.
            addProbeShipEmptyVariant(api, FleetSide.ENEMY, "lasher");

            api.initMap(-8000f, 8000f, -6000f, 6000f);
            api.addPlugin(new CombatHarnessPlugin());
            return;
        }

        if (!MatchupQueue.existsInCommon()) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: No queue file found in saves/common/");
            api.addBriefingItem("Write combat_harness_queue.json.data before launching.");
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        MatchupQueue queue;
        try {
            queue = MatchupQueue.loadFromCommon();
        } catch (Exception e) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: Failed to parse queue: " + e.getMessage());
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        // Both sides fully AI-controlled with ATTACK goal
        // useDefaultAI=true required — false means "player controls" which causes
        // retreat behavior when no human commander is present
        api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
        api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);

        api.setFleetTagline(FleetSide.PLAYER, "Optimizer Candidate");
        api.setFleetTagline(FleetSide.ENEMY, "Test Opponent");

        // Add first matchup's ships — required for the deployment screen to work.
        // Use addToFleet() with stock variants so the game handles CR/deployment
        // correctly. The plugin will swap the loadout to the real build at combat start.
        MatchupConfig first = queue.get(0);

        for (int i = 0; i < first.playerBuilds.length; i++) {
            String hullId = first.playerBuilds[i].hullId;
            String stockVariant = findAnyVariantForHull(hullId);
            api.addToFleet(FleetSide.PLAYER, stockVariant,
                    FleetMemberType.SHIP, stockVariant, false);
        }

        // Enemy ships: use stock variant IDs
        for (int i = 0; i < first.enemyVariants.length; i++) {
            String variantId = first.enemyVariants[i];
            api.addToFleet(FleetSide.ENEMY, variantId,
                    FleetMemberType.SHIP, variantId, false);
        }

        // Arena map
        float hw = 12000f;
        float hh = 9000f;
        api.initMap(-hw, hw, -hh, hh);

        // Attach plugin — handles combat monitoring and subsequent matchups
        api.addPlugin(new CombatHarnessPlugin());
    }

    /**
     * Find any stock variant ID for a given hull ID.
     * Used to create placeholder ships for the deployment screen.
     */
    private static String findAnyVariantForHull(String hullId) {
        for (String vid : Global.getSettings().getAllVariantIds()) {
            if (vid.startsWith(hullId + "_")) {
                return vid;
            }
        }
        // Fallback: use hull_id + "_Standard" convention
        return hullId + "_Standard";
    }

    /**
     * Probe-mode helper: add a ship with an EMPTY variant (no hullmods, no
     * weapons) to the given side. Uses createEmptyVariant + createFleetMember
     * + addFleetMember so the probe sees a pristine hull without stock-variant
     * contamination (e.g. onslaught_Standard's built-in dedicated_targeting_core
     * which would falsely mark ITU as inapplicable to all capitals).
     *
     * Retreat-mode pitfall noted in .claude/skills/starsector-modding.md is
     * irrelevant here — the plugin exits the JVM via System.exit(0) before
     * any ship can execute its first AI tick.
     */
    private static void addProbeShipEmptyVariant(
            MissionDefinitionAPI api, FleetSide side, String hullId) {
        try {
            ShipHullSpecAPI hullSpec = Global.getSettings().getHullSpec(hullId);
            if (hullSpec == null) {
                log.warn("addProbeShipEmptyVariant: hullSpec not found for "
                        + hullId + "; skipping");
                return;
            }
            ShipVariantAPI variant = Global.getSettings().createEmptyVariant(
                    hullId + "_manifest_probe", hullSpec);
            if (variant == null) {
                log.warn("addProbeShipEmptyVariant: createEmptyVariant returned null for "
                        + hullId + "; skipping");
                return;
            }
            FleetMemberAPI member = Global.getSettings().createFleetMember(
                    FleetMemberType.SHIP, variant);
            if (member == null) {
                log.warn("addProbeShipEmptyVariant: createFleetMember returned null for "
                        + hullId + "; skipping");
                return;
            }
            // CR=0 causes ships to deploy disabled and insta-die. 0.7 is the
            // vanilla standard deployment CR and is what the skill
            // (.claude/skills/starsector-modding.md) mandates.
            member.getRepairTracker().setCR(0.7f);
            api.addFleetMember(side, member);
            log.info("addProbeShipEmptyVariant: hull=" + hullId
                    + " side=" + side + " added empty variant");
        } catch (Throwable t) {
            log.error("addProbeShipEmptyVariant: failed for " + hullId, t);
        }
    }
}
