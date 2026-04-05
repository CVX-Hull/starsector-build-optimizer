package data.missions.optimizer_arena;

import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import starsector.combatharness.CombatHarnessPlugin;
import starsector.combatharness.MatchupConfig;

public class MissionDefinition implements MissionDefinitionPlugin {

    public void defineMission(MissionDefinitionAPI api) {
        if (!MatchupConfig.existsInCommon()) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: No matchup.json found in saves/common/combat_harness/");
            api.addBriefingItem("Place a matchup.json before launching this mission.");
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        MatchupConfig config;
        try {
            config = MatchupConfig.loadFromCommon();
        } catch (Exception e) {
            api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
            api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);
            api.addBriefingItem("ERROR: Failed to parse matchup.json: " + e.getMessage());
            api.initMap(-8000f, 8000f, -6000f, 6000f);
            return;
        }

        // Init fleets — both AI-controlled, both attacking
        // PLAYER side: useDefaultAI=false (tells engine no human is controlling, all ships get AI)
        // ENEMY side: useDefaultAI=true (standard for enemy)
        api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, false);
        api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);

        api.setFleetTagline(FleetSide.PLAYER, "Optimizer Candidate");
        api.setFleetTagline(FleetSide.ENEMY, "Test Opponent");

        // Add player ships — no flagship (no human control)
        for (int i = 0; i < config.playerVariants.length; i++) {
            String variantId = config.playerVariants[i];
            api.addToFleet(FleetSide.PLAYER, variantId,
                    FleetMemberType.SHIP, variantId, false);
        }

        // Add enemy ships
        for (int i = 0; i < config.enemyVariants.length; i++) {
            String variantId = config.enemyVariants[i];
            api.addToFleet(FleetSide.ENEMY, variantId,
                    FleetMemberType.SHIP, variantId, false);
        }

        // Map setup
        float hw = config.mapWidth / 2f;
        float hh = config.mapHeight / 2f;
        api.initMap(-hw, hw, -hh, hh);

        // Attach combat harness plugin
        api.addPlugin(new CombatHarnessPlugin());
    }
}
