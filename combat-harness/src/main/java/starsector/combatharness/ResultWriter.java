package starsector.combatharness;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ArmorGridAPI;
import com.fs.starfarer.api.combat.FluxTrackerAPI;
import com.fs.starfarer.api.combat.ShipAPI;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.List;

/**
 * Constructs result JSON from tracked ship data and writes batch results via SettingsAPI.
 */
public class ResultWriter {

    /**
     * Build a single matchup result from directly-tracked ShipAPI references.
     * Does NOT use fleet manager (which accumulates across matchups in batched sessions).
     */
    public static JSONObject buildMatchupResult(MatchupConfig config,
                                                 List<ShipAPI> playerShips,
                                                 List<ShipAPI> enemyShips,
                                                 DamageTracker tracker,
                                                 String winner,
                                                 float duration,
                                                 float effMaxFlux,
                                                 float effFluxDissipation,
                                                 float effArmorRating,
                                                 float effHullHpPct,
                                                 float ballisticRangeBonus,
                                                 float shieldDamageTakenMult)
            throws JSONException {
        JSONArray playerArr = new JSONArray();
        JSONArray enemyArr = new JSONArray();

        float playerTotalDealt = 0f;
        float enemyTotalDealt = 0f;
        int playerDestroyed = 0;
        int enemyDestroyed = 0;

        for (ShipAPI ship : playerShips) {
            JSONObject shipJson = shipToJSON(ship, tracker);
            playerArr.put(shipJson);
            JSONObject dealt = shipJson.getJSONObject("damage_dealt");
            playerTotalDealt += dealt.getDouble("shield") + dealt.getDouble("armor")
                    + dealt.getDouble("hull") + dealt.getDouble("emp");
            if (!ship.isAlive()) playerDestroyed++;
        }

        for (ShipAPI ship : enemyShips) {
            JSONObject shipJson = shipToJSON(ship, tracker);
            enemyArr.put(shipJson);
            JSONObject dealt = shipJson.getJSONObject("damage_dealt");
            enemyTotalDealt += dealt.getDouble("shield") + dealt.getDouble("armor")
                    + dealt.getDouble("hull") + dealt.getDouble("emp");
            if (!ship.isAlive()) enemyDestroyed++;
        }

        JSONObject result = new JSONObject();
        result.put("matchup_id", config.matchupId);
        result.put("winner", winner);
        result.put("duration_seconds", duration);
        result.put("player_ships", playerArr);
        result.put("enemy_ships", enemyArr);
        result.put("aggregate", aggregateToJSON(playerTotalDealt, enemyTotalDealt,
                playerDestroyed, enemyDestroyed, 0, 0));
        // Phase 5D + Phase-7-prep: emit 6 engine-truth player SETUP stats
        // only when all reads succeeded. The game's bundled org.json rejects
        // NaN in put(), so on a failed read (any value is NaN — should never
        // happen in production after a successful loadout swap) we omit the
        // key; Python parser treats absence as engine_stats=None, matching
        // pre-5D log replay semantics.
        if (!Float.isNaN(effMaxFlux)
                && !Float.isNaN(effFluxDissipation)
                && !Float.isNaN(effArmorRating)
                && !Float.isNaN(effHullHpPct)
                && !Float.isNaN(ballisticRangeBonus)
                && !Float.isNaN(shieldDamageTakenMult)) {
            result.put("setup_stats",
                    buildSetupStatsJSON(effMaxFlux, effFluxDissipation, effArmorRating,
                            effHullHpPct, ballisticRangeBonus, shieldDamageTakenMult));
        }
        return result;
    }

    /**
     * Phase 5D + Phase-7-prep — wrap post-SETUP engine-computed player stats
     * for EB shrinkage. Matches the individual-primitive parameter style of
     * damageToJSON / fluxStatsToJSON. Caller must ensure all six values are
     * finite (org.json rejects NaN).
     */
    public static JSONObject buildSetupStatsJSON(float effMaxFlux,
                                                  float effFluxDissipation,
                                                  float effArmorRating,
                                                  float effHullHpPct,
                                                  float ballisticRangeBonus,
                                                  float shieldDamageTakenMult)
            throws JSONException {
        JSONObject player = new JSONObject();
        player.put("eff_max_flux", effMaxFlux);
        player.put("eff_flux_dissipation", effFluxDissipation);
        player.put("eff_armor_rating", effArmorRating);
        player.put("eff_hull_hp_pct", effHullHpPct);
        player.put("ballistic_range_bonus", ballisticRangeBonus);
        player.put("shield_damage_taken_mult", shieldDamageTakenMult);
        JSONObject wrapper = new JSONObject();
        wrapper.put("player", player);
        return wrapper;
    }

    /** Write batch results array to saves/common/. */
    public static void writeAllResults(JSONArray results) throws JSONException {
        try {
            Global.getSettings().writeTextFileToCommon(
                    MatchupConfig.COMMON_PREFIX + "results.json",
                    results.toString(2));
        } catch (IOException e) {
            throw new RuntimeException("Failed to write results to saves/common/", e);
        }
    }

    /** Write done signal to saves/common/. */
    public static void writeDoneSignal() {
        try {
            Global.getSettings().writeTextFileToCommon(
                    MatchupConfig.COMMON_PREFIX + "done",
                    String.valueOf(System.currentTimeMillis()));
        } catch (IOException e) {
            throw new RuntimeException("Failed to write done signal to saves/common/", e);
        }
    }

    /**
     * Write enriched heartbeat to saves/common/. Non-fatal on failure.
     * Format: timestamp_ms elapsed player_hp enemy_hp player_alive enemy_alive
     */
    public static void writeHeartbeat(float elapsedTime,
                                       float playerHpFraction, float enemyHpFraction,
                                       int playerAlive, int enemyAlive) {
        try {
            Global.getSettings().writeTextFileToCommon(
                    MatchupConfig.COMMON_PREFIX + "heartbeat.txt",
                    formatHeartbeat(elapsedTime, playerHpFraction, enemyHpFraction,
                                   playerAlive, enemyAlive));
        } catch (IOException e) {
            // Non-fatal
        }
    }

    /** Format heartbeat string with 6 fields. Package-visible for testing. */
    static String formatHeartbeat(float elapsedTime,
                                   float playerHpFraction, float enemyHpFraction,
                                   int playerAlive, int enemyAlive) {
        return System.currentTimeMillis() + " " + elapsedTime
                + " " + playerHpFraction + " " + enemyHpFraction
                + " " + playerAlive + " " + enemyAlive;
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
}
