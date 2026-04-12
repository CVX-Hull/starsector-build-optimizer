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
}
