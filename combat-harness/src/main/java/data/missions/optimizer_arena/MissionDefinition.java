package data.missions.optimizer_arena;

import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import starsector.combatharness.CombatHarnessPlugin;
import starsector.combatharness.MatchupConfig;
import starsector.combatharness.MatchupQueue;
import starsector.combatharness.VariantBuilder;

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

        // Player ships: construct programmatically from build specs
        for (int i = 0; i < first.playerBuilds.length; i++) {
            FleetMemberAPI member = VariantBuilder.createFleetMember(first.playerBuilds[i]);
            api.addFleetMember(FleetSide.PLAYER, member);
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
}
