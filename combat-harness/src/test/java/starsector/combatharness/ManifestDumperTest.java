package starsector.combatharness;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

/**
 * ManifestDumper unit tests. Exercise the pure JSON-building helpers
 * (constants block, simple getters) that don't require actual
 * SpecAPI instances. Runtime integration with Global.getSettings()
 * is smoke-tested at deploy time via scripts/update_manifest.py.
 *
 * Schema v2 (Commit G): applicable_hull_sizes + incompatible_with on
 * hullmods are gone; applicable_hullmods + conditional_exclusions live
 * on hulls. Damage multipliers come from the DamageType API.
 */
class ManifestDumperTest {

    @Test
    void schemaVersionIsV2() {
        assertEquals(2, ManifestDumper.SCHEMA_VERSION,
                "Schema v2 introduced per-hull applicability; bump to v3 requires deliberate code-review.");
    }

    @Test
    void engineConstantsMatchDocumentedGameCaps() {
        assertEquals(30, ManifestDumper.MAX_VENTS_PER_SHIP);
        assertEquals(30, ManifestDumper.MAX_CAPACITORS_PER_SHIP);
        assertEquals(0.7f, ManifestDumper.DEFAULT_CR, 0.001f);
    }

    @Test
    void buildConstantsJsonHasAllFields() throws Exception {
        Set<String> statefulMods = new HashSet<String>(Arrays.asList("foo_mod"));
        JSONObject c = ManifestDumper.buildConstantsJson(
                "0.98a-RC8", "abc1234", statefulMods);
        assertEquals("0.98a-RC8", c.getString("game_version"));
        assertEquals(ManifestDumper.SCHEMA_VERSION, c.getInt("manifest_schema_version"));
        assertEquals("abc1234", c.getString("mod_commit_sha"));
        assertTrue(c.getString("generated_at").length() > 0);
        assertEquals(30, c.getInt("max_vents_per_ship"));
        assertEquals(30, c.getInt("max_capacitors_per_ship"));
        assertEquals(0.7, c.getDouble("default_cr"), 0.001);

        // Engine economy constants — fallbacks fire here because
        // Global.getSettings() is not available in the unit-test JVM.
        assertEquals(200.0, c.getDouble("flux_per_capacitor"), 0.001);
        assertEquals(10.0, c.getDouble("dissipation_per_vent"), 0.001);
        assertEquals(2, c.getInt("max_logistics_hullmods"));

        // Damage-mult blocks come from DamageType API — runtime enum
        // iteration covers all declared values. In a unit-test JVM the
        // DamageType enum is loaded via starfarer.api.jar (direct class
        // access, no Global.getSettings dependency).
        JSONObject shieldMult = c.getJSONObject("shield_damage_mult_by_type");
        assertEquals(2.0, shieldMult.getDouble("KINETIC"), 0.001);
        assertEquals(0.5, shieldMult.getDouble("HIGH_EXPLOSIVE"), 0.001);
        assertEquals(1.0, shieldMult.getDouble("ENERGY"), 0.001);
        assertEquals(0.25, shieldMult.getDouble("FRAGMENTATION"), 0.001);

        JSONObject armorMult = c.getJSONObject("armor_damage_mult_by_type");
        assertEquals(0.5, armorMult.getDouble("KINETIC"), 0.001);
        assertEquals(2.0, armorMult.getDouble("HIGH_EXPLOSIVE"), 0.001);
        assertEquals(1.0, armorMult.getDouble("ENERGY"), 0.001);
        assertEquals(0.25, armorMult.getDouble("FRAGMENTATION"), 0.001);

        // Commit G: hull mult emitted too. All vanilla damage types deal
        // 100% to hull (FRAGMENTATION is "100% vs hull" per DamageType
        // description — the 0.25 appears only on shield + armor layers).
        JSONObject hullMult = c.getJSONObject("hull_damage_mult_by_type");
        assertEquals(1.0, hullMult.getDouble("KINETIC"), 0.001);
        assertEquals(1.0, hullMult.getDouble("HIGH_EXPLOSIVE"), 0.001);
        assertEquals(1.0, hullMult.getDouble("ENERGY"), 0.001);
        assertEquals(1.0, hullMult.getDouble("FRAGMENTATION"), 0.001);

        // Stateful-mods canary round-trips.
        JSONArray stateful = c.getJSONArray("stateful_hullmods");
        assertEquals(1, stateful.length());
        assertEquals("foo_mod", stateful.getString(0));
    }

    @Test
    void buildConstantsJsonHandlesNullInputs() throws Exception {
        JSONObject c = ManifestDumper.buildConstantsJson(null, null, null);
        assertEquals("", c.getString("game_version"));
        assertEquals("", c.getString("mod_commit_sha"));
        assertEquals(0, c.getJSONArray("stateful_hullmods").length());
    }

    @Test
    void buildWeaponsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        JSONObject w = ManifestDumper.buildWeaponsJson(
                Collections.<com.fs.starfarer.api.loading.WeaponSpecAPI>emptyList());
        assertEquals(0, w.length());
    }

    @Test
    void buildHullmodsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        // v2 signature: no probe map param — pairwise compat lives on hulls now.
        JSONObject h = ManifestDumper.buildHullmodsJson(
                Collections.<com.fs.starfarer.api.loading.HullModSpecAPI>emptyList());
        assertEquals(0, h.length());
    }

    @Test
    void buildHullsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        // v2 signature: also takes per-hull applicability + cond-excl maps.
        Map<String, Set<String>> emptyApp =
                Collections.<String, Set<String>>emptyMap();
        Map<String, Map<String, Set<String>>> emptyCond =
                Collections.<String, Map<String, Set<String>>>emptyMap();
        JSONObject h = ManifestDumper.buildHullsJson(
                Collections.<com.fs.starfarer.api.combat.ShipHullSpecAPI>emptyList(),
                emptyApp, emptyCond);
        assertEquals(0, h.length());
    }

    @Test
    void safeStringReturnsEmptyOnNull() {
        assertEquals("", ManifestDumper.safeString(null));
        assertEquals("hello", ManifestDumper.safeString("hello"));
    }

    @Test
    void damageTypeToStringHandlesNull() {
        assertEquals("UNKNOWN", ManifestDumper.damageTypeToString(null));
        assertEquals("KINETIC",
                ManifestDumper.damageTypeToString(
                        com.fs.starfarer.api.combat.DamageType.KINETIC));
        assertEquals("HIGH_EXPLOSIVE",
                ManifestDumper.damageTypeToString(
                        com.fs.starfarer.api.combat.DamageType.HIGH_EXPLOSIVE));
    }

    @Test
    void shieldTypeToStringHandlesNull() {
        assertEquals("UNKNOWN", ManifestDumper.shieldTypeToString(null));
        assertEquals("FRONT",
                ManifestDumper.shieldTypeToString(
                        com.fs.starfarer.api.combat.ShieldAPI.ShieldType.FRONT));
    }

    @Test
    void toSortedJsonArraySortsAlphabetically() throws Exception {
        HashSet<String> set = new HashSet<String>(
                Arrays.asList("gamma", "alpha", "beta"));
        JSONArray arr = ManifestDumper.toSortedJsonArray(set);
        assertEquals(3, arr.length());
        assertEquals("alpha", arr.getString(0));
        assertEquals("beta", arr.getString(1));
        assertEquals("gamma", arr.getString(2));
    }

    @Test
    void toSortedJsonArrayHandlesNull() {
        JSONArray arr = ManifestDumper.toSortedJsonArray(null);
        assertEquals(0, arr.length());
    }

    @Test
    void listToJsonArrayHandlesNull() {
        JSONArray arr = ManifestDumper.listToJsonArray(null);
        assertEquals(0, arr.length());
    }

    @Test
    void listToJsonArrayConvertsToStrings() throws Exception {
        JSONArray arr = ManifestDumper.listToJsonArray(
                Arrays.asList("alpha", "beta"));
        assertEquals(2, arr.length());
        assertEquals("alpha", arr.getString(0));
        assertEquals("beta", arr.getString(1));
    }

    @Test
    void manifestFilenamesFollowCombatHarnessPrefix() {
        assertTrue(ManifestDumper.MANIFEST_REQUEST_FILE.startsWith("combat_harness_"));
        assertTrue(ManifestDumper.MANIFEST_DONE_FILE.startsWith("combat_harness_"));
        assertTrue(ManifestDumper.MANIFEST_CONSTANTS_FILE.startsWith("combat_harness_"));
        assertTrue(ManifestDumper.MANIFEST_WEAPONS_FILE.startsWith("combat_harness_"));
        assertTrue(ManifestDumper.MANIFEST_HULLMODS_FILE.startsWith("combat_harness_"));
        assertTrue(ManifestDumper.MANIFEST_HULLS_PART_FMT.startsWith("combat_harness_"));
    }
}
