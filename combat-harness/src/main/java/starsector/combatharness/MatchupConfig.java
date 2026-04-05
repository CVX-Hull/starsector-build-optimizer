package starsector.combatharness;

import com.fs.starfarer.api.Global;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Configuration for a single combat matchup, parsed from matchup.json.
 *
 * Files are read from saves/common/combat_harness/ via the game's SettingsAPI
 * (direct java.io.File access is blocked by Starsector's security sandbox).
 */
public class MatchupConfig {

    /** Filename prefix within saves/common/ for all combat harness files. */
    public static final String COMMON_PREFIX = "combat_harness_";

    public final String matchupId;
    public final String[] playerVariants;
    public final String[] enemyVariants;
    public final String playerFlagship;
    public final float timeLimitSeconds;
    public final float timeMult;
    public final float mapWidth;
    public final float mapHeight;

    private MatchupConfig(String matchupId, String[] playerVariants, String[] enemyVariants,
                          String playerFlagship, float timeLimitSeconds, float timeMult,
                          float mapWidth, float mapHeight) {
        this.matchupId = matchupId;
        this.playerVariants = playerVariants;
        this.enemyVariants = enemyVariants;
        this.playerFlagship = playerFlagship;
        this.timeLimitSeconds = timeLimitSeconds;
        this.timeMult = timeMult;
        this.mapWidth = mapWidth;
        this.mapHeight = mapHeight;
    }

    /**
     * Load matchup config from saves/common/combat_harness/matchup.json.
     * Uses the game's SettingsAPI to comply with the security sandbox.
     */
    public static MatchupConfig loadFromCommon() throws JSONException {
        try {
            String content = Global.getSettings().readTextFileFromCommon(COMMON_PREFIX + "matchup.json");
            return fromJSON(new JSONObject(content));
        } catch (Exception e) {
            throw new RuntimeException("Failed to read matchup config from saves/common/" + COMMON_PREFIX + "matchup.json", e);
        }
    }

    /** Check if matchup.json exists in saves/common/. */
    public static boolean existsInCommon() {
        return Global.getSettings().fileExistsInCommon(COMMON_PREFIX + "matchup.json");
    }

    public static MatchupConfig fromJSON(JSONObject json) throws JSONException {
        String matchupId = json.optString("matchup_id", "");
        if (matchupId.isEmpty()) {
            throw new IllegalArgumentException("matchup_id is required and must be non-empty");
        }

        String[] playerVariants = toStringArray(json.optJSONArray("player_variants"));
        if (playerVariants.length == 0) {
            throw new IllegalArgumentException("player_variants is required and must be non-empty");
        }

        String[] enemyVariants = toStringArray(json.optJSONArray("enemy_variants"));
        if (enemyVariants.length == 0) {
            throw new IllegalArgumentException("enemy_variants is required and must be non-empty");
        }

        String playerFlagship = json.optString("player_flagship", null);
        if ("".equals(playerFlagship)) {
            playerFlagship = null;
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

        return new MatchupConfig(matchupId, playerVariants, enemyVariants,
                playerFlagship, timeLimitSeconds, timeMult, mapWidth, mapHeight);
    }

    public JSONObject toJSON() throws JSONException {
        JSONObject json = new JSONObject();
        json.put("matchup_id", matchupId);
        json.put("player_variants", new JSONArray(playerVariants));
        json.put("enemy_variants", new JSONArray(enemyVariants));
        if (playerFlagship != null) {
            json.put("player_flagship", playerFlagship);
        }
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
}
