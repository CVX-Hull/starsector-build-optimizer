package starsector.combatharness;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.EnumSet;
import java.util.HashSet;

import static org.junit.jupiter.api.Assertions.*;

/**
 * ManifestDumper unit tests. Exercise the pure JSON-building helpers
 * (constants block, simple getters) that don't require actual
 * SpecAPI instances. Runtime integration with Global.getSettings()
 * is smoke-tested at deploy time via scripts/update_manifest.py.
 */
class ManifestDumperTest {

    @Test
    void schemaVersionIsPositive() {
        assertTrue(ManifestDumper.SCHEMA_VERSION >= 1,
                "SCHEMA_VERSION must be >=1 and bumped deliberately");
    }

    @Test
    void engineConstantsMatchDocumentedGameCaps() {
        // These are engine constants (per the Starsector wiki + source inspection).
        // Changing them requires a deliberate code-review event.
        assertEquals(30, ManifestDumper.MAX_VENTS_PER_SHIP);
        assertEquals(30, ManifestDumper.MAX_CAPACITORS_PER_SHIP);
        assertEquals(0.7f, ManifestDumper.DEFAULT_CR, 0.001f);
    }

    @Test
    void buildConstantsJsonHasAllFields() throws Exception {
        JSONObject c = ManifestDumper.buildConstantsJson("0.98a-RC7", "abc1234");
        assertEquals("0.98a-RC7", c.getString("game_version"));
        assertEquals(ManifestDumper.SCHEMA_VERSION, c.getInt("manifest_schema_version"));
        assertEquals("abc1234", c.getString("mod_commit_sha"));
        assertTrue(c.getString("generated_at").length() > 0);
        assertEquals(30, c.getInt("max_vents_per_ship"));
        assertEquals(30, c.getInt("max_capacitors_per_ship"));
        assertEquals(0.7, c.getDouble("default_cr"), 0.001);

        // Engine economy constants — fallbacks fire here because
        // Global.getSettings() is not available in the unit-test JVM
        // (no real game boot). Values match the vanilla 0.98a defaults.
        assertEquals(200.0, c.getDouble("flux_per_capacitor"), 0.001);
        assertEquals(10.0, c.getDouble("dissipation_per_vent"), 0.001);
        assertEquals(2, c.getInt("max_logistics_hullmods"));

        // Damage-mult blocks present with all four DamageType keys.
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
    }

    @Test
    void buildConstantsJsonHandlesNullInputs() throws Exception {
        JSONObject c = ManifestDumper.buildConstantsJson(null, null);
        assertEquals("", c.getString("game_version"));
        assertEquals("", c.getString("mod_commit_sha"));
    }

    @Test
    void buildWeaponsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        JSONObject w = ManifestDumper.buildWeaponsJson(
                Collections.<com.fs.starfarer.api.loading.WeaponSpecAPI>emptyList());
        assertEquals(0, w.length());
    }

    @Test
    void buildHullmodsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        JSONObject h = ManifestDumper.buildHullmodsJson(
                Collections.<com.fs.starfarer.api.loading.HullModSpecAPI>emptyList(),
                Collections.<com.fs.starfarer.api.combat.ShipAPI.HullSize,
                            com.fs.starfarer.api.combat.ShipAPI>emptyMap());
        assertEquals(0, h.length());
    }

    @Test
    void buildHullsJsonWithEmptyListReturnsEmptyObject() throws Exception {
        JSONObject h = ManifestDumper.buildHullsJson(
                Collections.<com.fs.starfarer.api.combat.ShipHullSpecAPI>emptyList());
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
    void toSortedStringJsonArrayWorksOnEnumSet() throws Exception {
        EnumSet<com.fs.starfarer.api.combat.WeaponAPI.AIHints> set = EnumSet.of(
                com.fs.starfarer.api.combat.WeaponAPI.AIHints.PD,
                com.fs.starfarer.api.combat.WeaponAPI.AIHints.ANTI_FTR);
        JSONArray arr = ManifestDumper.toSortedStringJsonArray(set);
        assertEquals(2, arr.length());
        // Alphabetical: ANTI_FTR < PD
        assertEquals("ANTI_FTR", arr.getString(0));
        assertEquals("PD", arr.getString(1));
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
        assertTrue(ManifestDumper.MANIFEST_HULLS_FILE.startsWith("combat_harness_"));
    }
}
