package starsector.combatharness;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class MatchupConfigTest {

    private JSONObject validJSON() throws Exception {
        JSONObject json = new JSONObject();
        json.put("matchup_id", "eval_001");
        json.put("player_variants", new JSONArray().put("eagle_test"));
        json.put("enemy_variants", new JSONArray().put("dominator_Standard"));
        return json;
    }

    @Test
    void parseValidJSONWithAllFields() throws Exception {
        JSONObject json = validJSON();
        json.put("player_flagship", "eagle_test");
        json.put("time_limit_seconds", 120.0);
        json.put("time_mult", 5.0);
        json.put("map_width", 16000.0);
        json.put("map_height", 12000.0);

        MatchupConfig config = MatchupConfig.fromJSON(json);

        assertEquals("eval_001", config.matchupId);
        assertArrayEquals(new String[]{"eagle_test"}, config.playerVariants);
        assertArrayEquals(new String[]{"dominator_Standard"}, config.enemyVariants);
        assertEquals("eagle_test", config.playerFlagship);
        assertEquals(120.0f, config.timeLimitSeconds, 0.01f);
        assertEquals(5.0f, config.timeMult, 0.01f);
        assertEquals(16000.0f, config.mapWidth, 0.01f);
        assertEquals(12000.0f, config.mapHeight, 0.01f);
    }

    @Test
    void parseJSONWithOnlyRequiredFields() throws Exception {
        MatchupConfig config = MatchupConfig.fromJSON(validJSON());

        assertEquals("eval_001", config.matchupId);
        assertNull(config.playerFlagship);
        assertEquals(300.0f, config.timeLimitSeconds, 0.01f);
        assertEquals(3.0f, config.timeMult, 0.01f);
        assertEquals(24000.0f, config.mapWidth, 0.01f);
        assertEquals(18000.0f, config.mapHeight, 0.01f);
    }

    @Test
    void missingMatchupIdThrows() throws Exception {
        JSONObject json = validJSON();
        json.remove("matchup_id");
        assertThrows(IllegalArgumentException.class, () -> MatchupConfig.fromJSON(json));
    }

    @Test
    void emptyMatchupIdThrows() throws Exception {
        JSONObject json = validJSON();
        json.put("matchup_id", "");
        assertThrows(IllegalArgumentException.class, () -> MatchupConfig.fromJSON(json));
    }

    @Test
    void emptyPlayerVariantsThrows() throws Exception {
        JSONObject json = validJSON();
        json.put("player_variants", new JSONArray());
        assertThrows(IllegalArgumentException.class, () -> MatchupConfig.fromJSON(json));
    }

    @Test
    void emptyEnemyVariantsThrows() throws Exception {
        JSONObject json = validJSON();
        json.put("enemy_variants", new JSONArray());
        assertThrows(IllegalArgumentException.class, () -> MatchupConfig.fromJSON(json));
    }

    @Test
    void timeMultClampedAboveFive() throws Exception {
        JSONObject json = validJSON();
        json.put("time_mult", 10.0);
        MatchupConfig config = MatchupConfig.fromJSON(json);
        assertEquals(5.0f, config.timeMult, 0.01f);
    }

    @Test
    void timeMultClampedBelowOne() throws Exception {
        JSONObject json = validJSON();
        json.put("time_mult", 0.5);
        MatchupConfig config = MatchupConfig.fromJSON(json);
        assertEquals(1.0f, config.timeMult, 0.01f);
    }

    @Test
    void multipleVariants() throws Exception {
        JSONObject json = validJSON();
        json.put("player_variants", new JSONArray().put("eagle_test").put("wolf_test"));
        json.put("enemy_variants", new JSONArray().put("dominator_Standard").put("enforcer_Assault"));

        MatchupConfig config = MatchupConfig.fromJSON(json);

        assertEquals(2, config.playerVariants.length);
        assertEquals(2, config.enemyVariants.length);
        assertEquals("wolf_test", config.playerVariants[1]);
    }

    @Test
    void roundTrip() throws Exception {
        JSONObject json = validJSON();
        json.put("player_flagship", "eagle_test");
        json.put("time_mult", 4.0);

        MatchupConfig config = MatchupConfig.fromJSON(json);
        JSONObject roundTripped = config.toJSON();
        MatchupConfig config2 = MatchupConfig.fromJSON(roundTripped);

        assertEquals(config.matchupId, config2.matchupId);
        assertArrayEquals(config.playerVariants, config2.playerVariants);
        assertEquals(config.playerFlagship, config2.playerFlagship);
        assertEquals(config.timeMult, config2.timeMult, 0.01f);
    }
}
