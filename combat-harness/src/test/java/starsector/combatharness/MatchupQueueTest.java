package starsector.combatharness;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class MatchupQueueTest {

    private JSONObject validBuildSpec() throws Exception {
        JSONObject spec = new JSONObject();
        spec.put("variant_id", "eagle_test");
        spec.put("hull_id", "eagle");
        spec.put("weapon_assignments", new JSONObject());
        spec.put("hullmods", new JSONArray());
        spec.put("flux_vents", 0);
        spec.put("flux_capacitors", 0);
        return spec;
    }

    private JSONObject validMatchup(String id) throws Exception {
        JSONObject json = new JSONObject();
        json.put("matchup_id", id);
        json.put("player_builds", new JSONArray().put(validBuildSpec()));
        json.put("enemy_variants", new JSONArray().put("dominator_Assault"));
        return json;
    }

    @Test
    void parseSingleMatchupQueue() throws Exception {
        JSONArray arr = new JSONArray();
        arr.put(validMatchup("eval_001"));

        MatchupQueue queue = MatchupQueue.fromJSON(arr);

        assertEquals(1, queue.size());
        assertEquals("eval_001", queue.get(0).matchupId);
    }

    @Test
    void parseMultipleMatchups() throws Exception {
        JSONArray arr = new JSONArray();
        arr.put(validMatchup("eval_001"));
        arr.put(validMatchup("eval_002"));
        arr.put(validMatchup("eval_003"));

        MatchupQueue queue = MatchupQueue.fromJSON(arr);

        assertEquals(3, queue.size());
        assertEquals("eval_001", queue.get(0).matchupId);
        assertEquals("eval_002", queue.get(1).matchupId);
        assertEquals("eval_003", queue.get(2).matchupId);
    }

    @Test
    void emptyArrayThrows() throws Exception {
        JSONArray arr = new JSONArray();
        assertThrows(IllegalArgumentException.class, () -> MatchupQueue.fromJSON(arr));
    }

    @Test
    void elementValidationApplied() throws Exception {
        JSONArray arr = new JSONArray();
        arr.put(validMatchup("eval_001"));
        // Second element has empty matchup_id — should fail
        JSONObject bad = new JSONObject();
        bad.put("matchup_id", "");
        bad.put("player_builds", new JSONArray().put(validBuildSpec()));
        bad.put("enemy_variants", new JSONArray().put("dominator_Assault"));
        arr.put(bad);

        assertThrows(IllegalArgumentException.class, () -> MatchupQueue.fromJSON(arr));
    }

    @Test
    void roundTrip() throws Exception {
        JSONArray arr = new JSONArray();
        arr.put(validMatchup("eval_001"));
        arr.put(validMatchup("eval_002"));

        MatchupQueue queue = MatchupQueue.fromJSON(arr);
        JSONArray roundTripped = queue.toJSON();
        MatchupQueue queue2 = MatchupQueue.fromJSON(roundTripped);

        assertEquals(queue.size(), queue2.size());
        assertEquals(queue.get(0).matchupId, queue2.get(0).matchupId);
        assertEquals(queue.get(1).matchupId, queue2.get(1).matchupId);
    }

    @Test
    void matchupConfigFieldsPreserved() throws Exception {
        JSONObject m = validMatchup("eval_001");
        m.put("time_mult", 5.0);
        m.put("time_limit_seconds", 120.0);

        JSONArray arr = new JSONArray();
        arr.put(m);

        MatchupQueue queue = MatchupQueue.fromJSON(arr);
        MatchupConfig config = queue.get(0);

        assertEquals(5.0f, config.timeMult, 0.01f);
        assertEquals(120.0f, config.timeLimitSeconds, 0.01f);
    }
}
