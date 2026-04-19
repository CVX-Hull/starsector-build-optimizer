package starsector.combatharness;

import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ResultWriterTest {

    @Test
    void damageToJSONContainsAllFields() throws Exception {
        JSONObject json = ResultWriter.damageToJSON(100f, 200f, 50f, 10f);

        assertEquals(100f, (float) json.getDouble("shield"), 0.01f);
        assertEquals(200f, (float) json.getDouble("armor"), 0.01f);
        assertEquals(50f, (float) json.getDouble("hull"), 0.01f);
        assertEquals(10f, (float) json.getDouble("emp"), 0.01f);
    }

    @Test
    void damageToJSONWithZeros() throws Exception {
        JSONObject json = ResultWriter.damageToJSON(0f, 0f, 0f, 0f);

        assertEquals(0f, (float) json.getDouble("shield"), 0.01f);
        assertEquals(0f, (float) json.getDouble("armor"), 0.01f);
    }

    @Test
    void fluxStatsToJSONContainsAllFields() throws Exception {
        JSONObject json = ResultWriter.fluxStatsToJSON(5000f, 2000f, 12000f, 3);

        assertEquals(5000f, (float) json.getDouble("curr_flux"), 0.01f);
        assertEquals(2000f, (float) json.getDouble("hard_flux"), 0.01f);
        assertEquals(12000f, (float) json.getDouble("max_flux"), 0.01f);
        assertEquals(3, json.getInt("overload_count"));
    }

    @Test
    void formatHeartbeatHas6Fields() {
        String heartbeat = ResultWriter.formatHeartbeat(10.5f, 0.85f, 0.42f, 2, 1);
        String[] parts = heartbeat.split(" ");
        assertEquals(6, parts.length, "Heartbeat should have 6 space-separated fields");
        // Field 0: timestamp (long)
        assertTrue(Long.parseLong(parts[0]) > 0);
        // Field 1: elapsed
        assertEquals(10.5f, Float.parseFloat(parts[1]), 0.01f);
        // Field 2: player HP fraction
        assertEquals(0.85f, Float.parseFloat(parts[2]), 0.01f);
        // Field 3: enemy HP fraction
        assertEquals(0.42f, Float.parseFloat(parts[3]), 0.01f);
        // Field 4: player alive count
        assertEquals(2, Integer.parseInt(parts[4]));
        // Field 5: enemy alive count
        assertEquals(1, Integer.parseInt(parts[5]));
    }

    @Test
    void aggregateToJSONContainsAllFields() throws Exception {
        JSONObject json = ResultWriter.aggregateToJSON(15000f, 8000f, 0, 2, 0, 1);

        assertEquals(15000f, (float) json.getDouble("player_total_damage_dealt"), 0.01f);
        assertEquals(8000f, (float) json.getDouble("enemy_total_damage_dealt"), 0.01f);
        assertEquals(0, json.getInt("player_ships_destroyed"));
        assertEquals(2, json.getInt("enemy_ships_destroyed"));
        assertEquals(0, json.getInt("player_ships_retreated"));
        assertEquals(1, json.getInt("enemy_ships_retreated"));
    }

    @Test
    void buildSetupStatsJSONContainsAllSixPlayerFields() throws Exception {
        JSONObject json = ResultWriter.buildSetupStatsJSON(
                12000f, 800f, 1050f, 1.4f, 300f, 0.75f);

        assertTrue(json.has("player"));
        JSONObject player = json.getJSONObject("player");
        assertEquals(12000f, (float) player.getDouble("eff_max_flux"), 0.01f);
        assertEquals(800f, (float) player.getDouble("eff_flux_dissipation"), 0.01f);
        assertEquals(1050f, (float) player.getDouble("eff_armor_rating"), 0.01f);
        assertEquals(1.4f, (float) player.getDouble("eff_hull_hp_pct"), 0.01f);
        assertEquals(300f, (float) player.getDouble("ballistic_range_bonus"), 0.01f);
        assertEquals(0.75f, (float) player.getDouble("shield_damage_taken_mult"), 0.01f);
    }

    @Test
    void buildSetupStatsJSONPropagatesZeros() throws Exception {
        JSONObject json = ResultWriter.buildSetupStatsJSON(
                0f, 0f, 0f, 0f, 0f, 0f);

        JSONObject player = json.getJSONObject("player");
        assertEquals(0f, (float) player.getDouble("eff_max_flux"), 0.01f);
        assertEquals(0f, (float) player.getDouble("eff_flux_dissipation"), 0.01f);
        assertEquals(0f, (float) player.getDouble("eff_armor_rating"), 0.01f);
        assertEquals(0f, (float) player.getDouble("eff_hull_hp_pct"), 0.01f);
        assertEquals(0f, (float) player.getDouble("ballistic_range_bonus"), 0.01f);
        assertEquals(0f, (float) player.getDouble("shield_damage_taken_mult"), 0.01f);
    }

    @Test
    void buildSetupStatsJSONRejectsNaNInAnyField() {
        // Game's bundled org.json rejects NaN in put(). Caller must check.
        // Any of the 6 fields being NaN must fail fast.
        assertThrows(Exception.class, () ->
                ResultWriter.buildSetupStatsJSON(Float.NaN, 800f, 1050f, 1.4f, 300f, 0.75f));
        assertThrows(Exception.class, () ->
                ResultWriter.buildSetupStatsJSON(12000f, 800f, 1050f, Float.NaN, 300f, 0.75f));
        assertThrows(Exception.class, () ->
                ResultWriter.buildSetupStatsJSON(12000f, 800f, 1050f, 1.4f, 300f, Float.NaN));
    }
}
