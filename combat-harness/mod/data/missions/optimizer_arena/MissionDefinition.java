package data.missions.optimizer_arena;

import java.io.File;

import com.fs.starfarer.api.fleet.FleetGoal;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.mission.FleetSide;
import com.fs.starfarer.api.mission.MissionDefinitionAPI;
import com.fs.starfarer.api.mission.MissionDefinitionPlugin;

import starsector.combatharness.CombatHarnessPlugin;
import starsector.combatharness.MatchupConfig;

public class MissionDefinition implements MissionDefinitionPlugin {

    public void defineMission(MissionDefinitionAPI api) {
        File workdir = new File("mods/combat-harness/workdir");
        MatchupConfig config = MatchupConfig.fromFile(new File(workdir, "matchup.json"));

        // Init fleets — both AI-controlled, both attacking
        api.initFleet(FleetSide.PLAYER, "OPT", FleetGoal.ATTACK, true);
        api.initFleet(FleetSide.ENEMY, "ENM", FleetGoal.ATTACK, true);

        api.setFleetTagline(FleetSide.PLAYER, "Optimizer Candidate");
        api.setFleetTagline(FleetSide.ENEMY, "Test Opponent");

        // Add player ships
        for (int i = 0; i < config.playerVariants.length; i++) {
            String variantId = config.playerVariants[i];
            boolean isFlagship = variantId.equals(config.playerFlagship);
            api.addToFleet(FleetSide.PLAYER, variantId,
                    FleetMemberType.SHIP, variantId, isFlagship);
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
        api.addPlugin(new CombatHarnessPlugin(workdir));
    }
}
