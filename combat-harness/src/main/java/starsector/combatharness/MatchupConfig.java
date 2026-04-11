package starsector.combatharness;

import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Configuration for a single combat matchup. Used within MatchupQueue.
 * Parsed from a JSON object with fields: matchup_id, player_builds, enemy_variants, etc.
 */
public class MatchupConfig {

    /** Filename prefix within saves/common/ for all combat harness files. */
    public static final String COMMON_PREFIX = "combat_harness_";

    public final String matchupId;
    public final BuildSpec[] playerBuilds;
    public final String[] enemyVariants;
    public final float timeLimitSeconds;
    public final float timeMult;
    public final float mapWidth;
    public final float mapHeight;

    private MatchupConfig(String matchupId, BuildSpec[] playerBuilds, String[] enemyVariants,
                          float timeLimitSeconds, float timeMult,
                          float mapWidth, float mapHeight) {
        this.matchupId = matchupId;
        this.playerBuilds = playerBuilds;
        this.enemyVariants = enemyVariants;
        this.timeLimitSeconds = timeLimitSeconds;
        this.timeMult = timeMult;
        this.mapWidth = mapWidth;
        this.mapHeight = mapHeight;
    }

    public static MatchupConfig fromJSON(JSONObject json) throws JSONException {
        String matchupId = json.optString("matchup_id", "");
        if (matchupId.isEmpty()) {
            throw new IllegalArgumentException("matchup_id is required and must be non-empty");
        }

        JSONArray playerBuildsArr = json.optJSONArray("player_builds");
        if (playerBuildsArr == null || playerBuildsArr.length() == 0) {
            throw new IllegalArgumentException("player_builds is required and must be non-empty");
        }
        BuildSpec[] playerBuilds = new BuildSpec[playerBuildsArr.length()];
        for (int i = 0; i < playerBuildsArr.length(); i++) {
            playerBuilds[i] = BuildSpec.fromJSON(playerBuildsArr.getJSONObject(i));
        }

        String[] enemyVariants = toStringArray(json.optJSONArray("enemy_variants"));
        if (enemyVariants.length == 0) {
            throw new IllegalArgumentException("enemy_variants is required and must be non-empty");
        }

        float timeLimitSeconds = (float) json.optDouble("time_limit_seconds", 300.0);
        if (timeLimitSeconds <= 0) {
            throw new IllegalArgumentException("time_limit_seconds must be > 0");
        }

        float timeMult = (float) json.optDouble("time_mult", 3.0);
        timeMult = Math.max(1.0f, Math.min(5.0f, timeMult));

        float mapWidth = (float) json.optDouble("map_width", 24000.0);
        if (mapWidth <= 0) {
            throw new IllegalArgumentException("map_width must be > 0");
        }

        float mapHeight = (float) json.optDouble("map_height", 18000.0);
        if (mapHeight <= 0) {
            throw new IllegalArgumentException("map_height must be > 0");
        }

        return new MatchupConfig(matchupId, playerBuilds, enemyVariants,
                timeLimitSeconds, timeMult, mapWidth, mapHeight);
    }

    public JSONObject toJSON() throws JSONException {
        JSONObject json = new JSONObject();
        json.put("matchup_id", matchupId);
        JSONArray buildsArr = new JSONArray();
        for (BuildSpec spec : playerBuilds) {
            buildsArr.put(spec.toJSON());
        }
        json.put("player_builds", buildsArr);
        json.put("enemy_variants", new JSONArray(enemyVariants));
        json.put("time_limit_seconds", timeLimitSeconds);
        json.put("time_mult", timeMult);
        json.put("map_width", mapWidth);
        json.put("map_height", mapHeight);
        return json;
    }

    private static String[] toStringArray(JSONArray arr) throws JSONException {
        if (arr == null) return new String[0];
        String[] result = new String[arr.length()];
        for (int i = 0; i < arr.length(); i++) {
            result[i] = arr.getString(i);
        }
        return result;
    }

    /**
     * Build specification for programmatic variant construction.
     * Embedded in matchup queue JSON under player_builds.
     */
    public static class BuildSpec {
        /** Standard deployment CR (70%). Used when cr is not specified in JSON. */
        public static final float DEFAULT_CR = 0.7f;

        public final String variantId;
        public final String hullId;
        public final Map<String, String> weaponAssignments;
        public final String[] hullmods;
        public final int fluxVents;
        public final int fluxCapacitors;
        public final float cr;

        private BuildSpec(String variantId, String hullId,
                          Map<String, String> weaponAssignments, String[] hullmods,
                          int fluxVents, int fluxCapacitors, float cr) {
            this.variantId = variantId;
            this.hullId = hullId;
            this.weaponAssignments = Collections.unmodifiableMap(weaponAssignments);
            this.hullmods = hullmods;
            this.fluxVents = fluxVents;
            this.fluxCapacitors = fluxCapacitors;
            this.cr = cr;
        }

        public static BuildSpec fromJSON(JSONObject json) throws JSONException {
            String variantId = json.optString("variant_id", "");
            if (variantId.isEmpty()) {
                throw new IllegalArgumentException("variant_id is required and must be non-empty");
            }

            String hullId = json.optString("hull_id", "");
            if (hullId.isEmpty()) {
                throw new IllegalArgumentException("hull_id is required and must be non-empty");
            }

            JSONObject weaponsObj = json.optJSONObject("weapon_assignments");
            Map<String, String> weaponAssignments = new HashMap<String, String>();
            if (weaponsObj != null) {
                Iterator<String> keys = weaponsObj.keys();
                while (keys.hasNext()) {
                    String slotId = keys.next();
                    weaponAssignments.put(slotId, weaponsObj.getString(slotId));
                }
            }

            String[] hullmods = toStringArray(json.optJSONArray("hullmods"));

            int fluxVents = json.optInt("flux_vents", 0);
            if (fluxVents < 0) {
                throw new IllegalArgumentException("flux_vents must be >= 0");
            }

            int fluxCapacitors = json.optInt("flux_capacitors", 0);
            if (fluxCapacitors < 0) {
                throw new IllegalArgumentException("flux_capacitors must be >= 0");
            }

            float cr = (float) json.optDouble("cr", DEFAULT_CR);
            cr = Math.max(0f, Math.min(1f, cr));

            return new BuildSpec(variantId, hullId, weaponAssignments, hullmods,
                    fluxVents, fluxCapacitors, cr);
        }

        public JSONObject toJSON() throws JSONException {
            JSONObject json = new JSONObject();
            json.put("variant_id", variantId);
            json.put("hull_id", hullId);
            JSONObject weaponsObj = new JSONObject();
            for (Map.Entry<String, String> entry : weaponAssignments.entrySet()) {
                weaponsObj.put(entry.getKey(), entry.getValue());
            }
            json.put("weapon_assignments", weaponsObj);
            json.put("hullmods", new JSONArray(hullmods));
            json.put("flux_vents", fluxVents);
            json.put("flux_capacitors", fluxCapacitors);
            json.put("cr", cr);
            return json;
        }

        private static String[] toStringArray(JSONArray arr) throws JSONException {
            if (arr == null) return new String[0];
            String[] result = new String[arr.length()];
            for (int i = 0; i < arr.length(); i++) {
                result[i] = arr.getString(i);
            }
            return result;
        }
    }
}
