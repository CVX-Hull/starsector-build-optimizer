package starsector.combatharness;

import com.fs.starfarer.api.combat.ArmorGridAPI;
import com.fs.starfarer.api.combat.CombatEngineAPI;
import com.fs.starfarer.api.combat.CombatFleetManagerAPI;
import com.fs.starfarer.api.combat.FluxTrackerAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.mission.FleetSide;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.List;

/**
 * Collects final combat state and writes result.json atomically.
 */
public class ResultWriter {

    public static void writeResult(CombatEngineAPI engine, DamageTracker tracker,
                                   MatchupConfig config, File outputDir, boolean timedOut)
            throws JSONException {
        String winner;
        if (timedOut) {
            winner = "TIMEOUT";
        } else {
            int winningSide = engine.getWinningSideId();
            winner = winningSide == 0 ? "PLAYER" : "ENEMY";
        }

        float duration = engine.getTotalElapsedTime(false);

        JSONArray playerShips = new JSONArray();
        JSONArray enemyShips = new JSONArray();

        for (ShipAPI ship : engine.getShips()) {
            if (ship.isFighter()) continue;
            JSONObject shipJson = shipToJSON(ship, tracker);
            if (ship.getOwner() == 0) {
                playerShips.put(shipJson);
            } else if (ship.getOwner() == 1) {
                enemyShips.put(shipJson);
            }
        }

        // Aggregate stats
        CombatFleetManagerAPI playerFM = engine.getFleetManager(FleetSide.PLAYER);
        CombatFleetManagerAPI enemyFM = engine.getFleetManager(FleetSide.ENEMY);

        float playerTotalDealt = 0f;
        float enemyTotalDealt = 0f;
        // Sum damage dealt from per-ship JSON objects
        for (int i = 0; i < playerShips.length(); i++) {
            JSONObject s = playerShips.getJSONObject(i);
            JSONObject dealt = s.getJSONObject("damage_dealt");
            playerTotalDealt += dealt.getDouble("shield") + dealt.getDouble("armor")
                    + dealt.getDouble("hull") + dealt.getDouble("emp");
        }
        for (int i = 0; i < enemyShips.length(); i++) {
            JSONObject s = enemyShips.getJSONObject(i);
            JSONObject dealt = s.getJSONObject("damage_dealt");
            enemyTotalDealt += dealt.getDouble("shield") + dealt.getDouble("armor")
                    + dealt.getDouble("hull") + dealt.getDouble("emp");
        }

        int playerDestroyed = countDestroyed(playerFM);
        int enemyDestroyed = countDestroyed(enemyFM);
        int playerRetreated = safeSize(playerFM.getRetreatedCopy());
        int enemyRetreated = safeSize(enemyFM.getRetreatedCopy());

        JSONObject result = new JSONObject();
        result.put("matchup_id", config.matchupId);
        result.put("winner", winner);
        result.put("duration_seconds", duration);
        result.put("player_ships", playerShips);
        result.put("enemy_ships", enemyShips);
        result.put("aggregate", aggregateToJSON(playerTotalDealt, enemyTotalDealt,
                playerDestroyed, enemyDestroyed, playerRetreated, enemyRetreated));

        atomicWrite(result, outputDir);
    }

    static JSONObject shipToJSON(ShipAPI ship, DamageTracker tracker)
            throws JSONException {
        String fleetMemberId = ship.getFleetMemberId();
        String variantId = ship.getVariant() != null ? ship.getVariant().getHullVariantId() : "unknown";
        String hullId = ship.getHullSpec() != null ? ship.getHullSpec().getHullId() : "unknown";

        DamageTracker.ShipDamageAccumulator acc = tracker.getOrCreate(fleetMemberId);
        FluxTrackerAPI flux = ship.getFluxTracker();

        JSONObject json = new JSONObject();
        json.put("fleet_member_id", fleetMemberId);
        json.put("variant_id", variantId);
        json.put("hull_id", hullId);
        json.put("destroyed", !ship.isAlive());
        json.put("hull_fraction", ship.getHullLevel());
        json.put("armor_fraction", computeArmorFraction(ship));
        json.put("cr_remaining", ship.getCurrentCR());
        json.put("peak_time_remaining", ship.getPeakTimeRemaining());
        json.put("disabled_weapons", ship.getDisabledWeapons() != null ? ship.getDisabledWeapons().size() : 0);
        json.put("flameouts", ship.getNumFlameouts());
        json.put("damage_dealt", damageToJSON(acc.shieldDamageDealt, acc.armorDamageDealt,
                acc.hullDamageDealt, acc.empDamageDealt));
        json.put("damage_taken", damageToJSON(acc.shieldDamageTaken, acc.armorDamageTaken,
                acc.hullDamageTaken, acc.empDamageTaken));
        if (flux != null) {
            json.put("flux_stats", fluxStatsToJSON(
                    flux.getCurrFlux(), flux.getHardFlux(), flux.getMaxFlux(), acc.overloadCount));
        } else {
            json.put("flux_stats", fluxStatsToJSON(0f, 0f, 0f, acc.overloadCount));
        }
        return json;
    }

    static float computeArmorFraction(ShipAPI ship) {
        ArmorGridAPI grid = ship.getArmorGrid();
        if (grid == null) return 0f;
        float maxPerCell = grid.getMaxArmorInCell();
        if (maxPerCell <= 0) return 0f;
        float[][] cells = grid.getGrid();
        float total = 0f;
        int count = 0;
        for (float[] row : cells) {
            for (float cell : row) {
                total += cell / maxPerCell;
                count++;
            }
        }
        return count > 0 ? total / count : 0f;
    }

    public static JSONObject damageToJSON(float shield, float armor, float hull, float emp)
            throws JSONException {
        JSONObject json = new JSONObject();
        json.put("shield", shield);
        json.put("armor", armor);
        json.put("hull", hull);
        json.put("emp", emp);
        return json;
    }

    public static JSONObject fluxStatsToJSON(float currFlux, float hardFlux, float maxFlux,
                                             int overloadCount) throws JSONException {
        JSONObject json = new JSONObject();
        json.put("curr_flux", currFlux);
        json.put("hard_flux", hardFlux);
        json.put("max_flux", maxFlux);
        json.put("overload_count", overloadCount);
        return json;
    }

    public static JSONObject aggregateToJSON(float playerTotalDealt, float enemyTotalDealt,
                                             int playerDestroyed, int enemyDestroyed,
                                             int playerRetreated, int enemyRetreated)
            throws JSONException {
        JSONObject json = new JSONObject();
        json.put("player_total_damage_dealt", playerTotalDealt);
        json.put("enemy_total_damage_dealt", enemyTotalDealt);
        json.put("player_ships_destroyed", playerDestroyed);
        json.put("enemy_ships_destroyed", enemyDestroyed);
        json.put("player_ships_retreated", playerRetreated);
        json.put("enemy_ships_retreated", enemyRetreated);
        return json;
    }

    public static void atomicWrite(JSONObject json, File outputDir) throws JSONException {
        File tmp = new File(outputDir, "result.json.tmp");
        File result = new File(outputDir, "result.json");
        try (FileWriter fw = new FileWriter(tmp)) {
            fw.write(json.toString(2));
        } catch (IOException e) {
            throw new RuntimeException("Failed to write result file", e);
        }
        if (!tmp.renameTo(result)) {
            throw new RuntimeException("Failed to rename result.json.tmp to result.json");
        }
    }

    private static int countDestroyed(CombatFleetManagerAPI fm) {
        List<FleetMemberAPI> destroyed = fm.getDestroyedCopy();
        List<FleetMemberAPI> disabled = fm.getDisabledCopy();
        return safeSize(destroyed) + safeSize(disabled);
    }

    private static int safeSize(List<?> list) {
        return list != null ? list.size() : 0;
    }
}
