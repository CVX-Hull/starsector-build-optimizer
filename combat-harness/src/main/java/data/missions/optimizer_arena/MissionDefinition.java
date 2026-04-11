package data.missions.optimizer_arena;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import starsector.combatharness.CombatHarnessPlugin;
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

    public void defineMission(MissionDefinitionAPI api) {
        if (!MatchupQueue.existsInCommon()) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, false);
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
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, false);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: Failed to parse queue: " + e.getMessage());
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        // PLAYER useDefaultAI=false: all ships get AI (no human control)
        // ENEMY useDefaultAI=true: standard enemy AI
        api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, false);
        api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);

        api.setFleetTagline(FleetSide.PLAYER, "Optimizer Candidate");
        api.setFleetTagline(FleetSide.ENEMY, "Test Opponent");

        // Add first matchup's ships — required for the deployment screen to work.
        // Subsequent matchups are handled by the plugin via spawnFleetMember()/spawnShipOrWing().
        MatchupConfig first = queue.get(0);

        // Player ships: add placeholders so the deployment screen has something to show.
        // The plugin will remove these and spawn the real builds via spawnFleetMember().
        for (int i = 0; i < first.playerBuilds.length; i++) {
            String hullId = first.playerBuilds[i].hullId;
            String placeholderVariant = findAnyVariantForHull(hullId);
            api.addToFleet(FleetSide.PLAYER, placeholderVariant,
                    FleetMemberType.SHIP, placeholderVariant, false);
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
}
