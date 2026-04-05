package starsector.combatharness;

import org.json.JSONObject;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;

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
    void atomicWriteCreatesResultFile(@TempDir Path tempDir) throws Exception {
        JSONObject result = new JSONObject();
        result.put("matchup_id", "test_001");
        result.put("winner", "PLAYER");

        ResultWriter.atomicWrite(result, tempDir.toFile());

        File resultFile = new File(tempDir.toFile(), "result.json");
        assertTrue(resultFile.exists(), "result.json should exist");

        File tmpFile = new File(tempDir.toFile(), "result.json.tmp");
        assertFalse(tmpFile.exists(), "result.json.tmp should not exist after completion");

        String content = Files.readString(resultFile.toPath());
        JSONObject parsed = new JSONObject(content);
        assertEquals("test_001", parsed.getString("matchup_id"));
        assertEquals("PLAYER", parsed.getString("winner"));
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
}
