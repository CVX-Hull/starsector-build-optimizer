package starsector.combatharness;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class MatchupConfigTest {

    private JSONObject validBuildSpecJSON() throws Exception {
        JSONObject spec = new JSONObject();
        spec.put("variant_id", "eagle_opt_001");
        spec.put("hull_id", "eagle");
        JSONObject weapons = new JSONObject();
        weapons.put("WS 001", "heavymauler");
        spec.put("weapon_assignments", weapons);
        spec.put("hullmods", new JSONArray().put("heavyarmor"));
        spec.put("flux_vents", 15);
        spec.put("flux_capacitors", 10);
        return spec;
    }

    private JSONObject validJSON() throws Exception {
        JSONObject json = new JSONObject();
        json.put("matchup_id", "eval_001");
        json.put("player_builds", new JSONArray().put(validBuildSpecJSON()));
        json.put("enemy_variants", new JSONArray().put("dominator_Standard"));
        return json;
    }

    @Test
    void parseValidJSONWithAllFields() throws Exception {
        JSONObject json = validJSON();
        json.put("time_limit_seconds", 120.0);
        json.put("time_mult", 5.0);
        json.put("map_width", 16000.0);
        json.put("map_height", 12000.0);

        MatchupConfig config = MatchupConfig.fromJSON(json);

        assertEquals("eval_001", config.matchupId);
        assertEquals(1, config.playerBuilds.length);
        assertEquals("eagle_opt_001", config.playerBuilds[0].variantId);
        assertEquals("eagle", config.playerBuilds[0].hullId);
        assertEquals("heavymauler", config.playerBuilds[0].weaponAssignments.get("WS 001"));
        assertArrayEquals(new String[]{"heavyarmor"}, config.playerBuilds[0].hullmods);
        assertEquals(15, config.playerBuilds[0].fluxVents);
        assertEquals(10, config.playerBuilds[0].fluxCapacitors);
        assertArrayEquals(new String[]{"dominator_Standard"}, config.enemyVariants);
        assertEquals(120.0f, config.timeLimitSeconds, 0.01f);
        assertEquals(5.0f, config.timeMult, 0.01f);
        assertEquals(16000.0f, config.mapWidth, 0.01f);
        assertEquals(12000.0f, config.mapHeight, 0.01f);
    }

    @Test
    void parseJSONWithOnlyRequiredFields() throws Exception {
        MatchupConfig config = MatchupConfig.fromJSON(validJSON());

        assertEquals("eval_001", config.matchupId);
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
    void emptyPlayerBuildsThrows() throws Exception {
        JSONObject json = validJSON();
        json.put("player_builds", new JSONArray());
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
    void multiplePlayerBuilds() throws Exception {
        JSONObject json = validJSON();
        JSONObject spec2 = validBuildSpecJSON();
        spec2.put("variant_id", "wolf_opt_002");
        spec2.put("hull_id", "wolf");
        json.put("player_builds", new JSONArray().put(validBuildSpecJSON()).put(spec2));

        MatchupConfig config = MatchupConfig.fromJSON(json);

        assertEquals(2, config.playerBuilds.length);
        assertEquals("eagle_opt_001", config.playerBuilds[0].variantId);
        assertEquals("wolf_opt_002", config.playerBuilds[1].variantId);
    }

    @Test
    void buildSpecEmptyWeapons() throws Exception {
        JSONObject spec = validBuildSpecJSON();
        spec.put("weapon_assignments", new JSONObject());
        JSONObject json = new JSONObject();
        json.put("matchup_id", "test");
        json.put("player_builds", new JSONArray().put(spec));
        json.put("enemy_variants", new JSONArray().put("enemy"));

        MatchupConfig config = MatchupConfig.fromJSON(json);
        assertTrue(config.playerBuilds[0].weaponAssignments.isEmpty());
    }

    @Test
    void buildSpecFluxDefaults() throws Exception {
        JSONObject spec = new JSONObject();
        spec.put("variant_id", "test");
        spec.put("hull_id", "eagle");
        spec.put("weapon_assignments", new JSONObject());
        spec.put("hullmods", new JSONArray());
        // Omit flux_vents and flux_capacitors — should default to 0

        JSONObject json = new JSONObject();
        json.put("matchup_id", "test");
        json.put("player_builds", new JSONArray().put(spec));
        json.put("enemy_variants", new JSONArray().put("enemy"));

        MatchupConfig config = MatchupConfig.fromJSON(json);
        assertEquals(0, config.playerBuilds[0].fluxVents);
        assertEquals(0, config.playerBuilds[0].fluxCapacitors);
    }

    @Test
    void buildSpecMissingHullIdThrows() throws Exception {
        JSONObject spec = validBuildSpecJSON();
        spec.remove("hull_id");
        JSONObject json = new JSONObject();
        json.put("matchup_id", "test");
        json.put("player_builds", new JSONArray().put(spec));
        json.put("enemy_variants", new JSONArray().put("enemy"));

        assertThrows(IllegalArgumentException.class, () -> MatchupConfig.fromJSON(json));
    }

    @Test
    void roundTrip() throws Exception {
        JSONObject json = validJSON();
        json.put("time_mult", 4.0);

        MatchupConfig config = MatchupConfig.fromJSON(json);
        JSONObject roundTripped = config.toJSON();
        MatchupConfig config2 = MatchupConfig.fromJSON(roundTripped);

        assertEquals(config.matchupId, config2.matchupId);
        assertEquals(config.playerBuilds[0].variantId, config2.playerBuilds[0].variantId);
        assertEquals(config.playerBuilds[0].hullId, config2.playerBuilds[0].hullId);
        assertEquals(config.timeMult, config2.timeMult, 0.01f);
    }
}
