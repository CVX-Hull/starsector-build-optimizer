package starsector.combatharness;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.DamageType;
import com.fs.starfarer.api.combat.ShieldAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.WeaponAPI;
import com.fs.starfarer.api.loading.HullModSpecAPI;
import com.fs.starfarer.api.loading.WeaponSlotAPI;
import com.fs.starfarer.api.loading.WeaponSpecAPI;

import org.apache.log4j.Logger;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * Dumps an authoritative JSON manifest of all game rules:
 * weapons, hullmods, hulls (with per-hull applicability + conditional
 * exclusions), and engine constants.
 *
 * Schema v2 (Commit G) — per-hull applicability, built-in-aware by
 * construction. Deletes the old v1 schema shape (`applicable_hull_sizes`
 * on hullmods + `incompatible_with` on hullmods) in favor of two
 * per-hull fields:
 *   - `hull.applicable_hullmods`: engine-probed yes-set when probed
 *     against an empty variant of that specific hull (built-ins
 *     inherit automatically via ShipVariantAPI.java:30-33).
 *   - `hull.conditional_exclusions`: map installed-mod → set of mods
 *     that drop applicability when that installed mod is present.
 *     Captures user-induced pair conflicts (ITU vs DTC) AND
 *     built-in-induced conflicts (Paragon's advancedcore blocks ITU).
 * Single source of truth (the engine's `isApplicableToShip`), zero
 * Python-side game knowledge.
 *
 * Damage-type multipliers come from `DamageType.getShieldMult()` /
 * `getArmorMult()` / `getHullMult()` — not hardcoded (Commit G R?,
 * audit finding).
 *
 * Output split across 4 files because `writeTextFileToCommon` caps at
 * ~1 MiB; Python loader reads all four and merges into a GameManifest.
 */
public final class ManifestDumper {

    private static final Logger log = Logger.getLogger(ManifestDumper.class);

    public static final String MANIFEST_CONSTANTS_FILE = MatchupConfig.COMMON_PREFIX + "manifest_constants.json";
    public static final String MANIFEST_WEAPONS_FILE   = MatchupConfig.COMMON_PREFIX + "manifest_weapons.json";
    public static final String MANIFEST_HULLMODS_FILE  = MatchupConfig.COMMON_PREFIX + "manifest_hullmods.json";
    // Hulls split across N part files — v2 adds applicable_hullmods (~3 KB/hull)
    // + conditional_exclusions (~1 KB/hull) which pushes the full hulls blob
    // past the 1 MiB writeTextFileToCommon cap. Part file names are
    // manifest_hulls_000.json.data, _001, etc. — Python loader globs.
    public static final String MANIFEST_HULLS_PART_FMT = MatchupConfig.COMMON_PREFIX + "manifest_hulls_%03d.json";
    public static final int HULLS_PART_MAX_BYTES = 900_000;  // under 1 MiB with safety margin
    public static final String MANIFEST_REQUEST_FILE = MatchupConfig.COMMON_PREFIX + "manifest_request";
    public static final String MANIFEST_DONE_FILE    = MatchupConfig.COMMON_PREFIX + "manifest_done";

    /** Schema version. MUST equal Python GameManifest.EXPECTED_SCHEMA_VERSION.
     *  v2 (Commit G): per-hull applicability + conditional exclusions. */
    public static final int SCHEMA_VERSION = 2;

    /** Engine caps that Python used to hardcode. Authoritative values
     *  documented by the game engine. */
    public static final int MAX_VENTS_PER_SHIP = 30;
    public static final int MAX_CAPACITORS_PER_SHIP = 30;
    public static final float DEFAULT_CR = 0.7f;

    private ManifestDumper() {}

    /** Top-level entry point. Writes the 4 part files + done sentinel.
     *  Done sentinel is written LAST — Python driver polling the done
     *  file is guaranteed all 4 part files are present.
     *
     *  @param applicableByHull  hullId → set of applicable-standalone mod IDs
     *  @param condExclByHull    hullId → installed-mod A → set of mods B
     *                           that drop applicability when A is present
     *  @param statefulMods      mod IDs whose determinism double-probe diverged;
     *                           zero-length on vanilla 0.98a-RC8, hard-fails
     *                           `tests/test_game_manifest.py` if non-empty
     */
    public static void dumpToCommon(
            String gameVersion, String modCommitSha,
            Map<String, Set<String>> applicableByHull,
            Map<String, Map<String, Set<String>>> condExclByHull,
            Set<String> statefulMods)
            throws JSONException, IOException {
        JSONObject constants = buildConstantsJson(gameVersion, modCommitSha, statefulMods);
        JSONObject weapons = buildWeaponsJson(Global.getSettings().getAllWeaponSpecs());
        JSONObject hullmods = buildHullmodsJson(Global.getSettings().getAllHullModSpecs());
        JSONObject hulls = buildHullsJson(
                Global.getSettings().getAllShipHullSpecs(),
                applicableByHull, condExclByHull);

        Global.getSettings().writeTextFileToCommon(MANIFEST_CONSTANTS_FILE, constants.toString());
        Global.getSettings().writeTextFileToCommon(MANIFEST_WEAPONS_FILE, weapons.toString());
        Global.getSettings().writeTextFileToCommon(MANIFEST_HULLMODS_FILE, hullmods.toString());
        int hullsPartCount = writeHullsSplit(hulls);
        Global.getSettings().writeTextFileToCommon(
                MANIFEST_DONE_FILE, String.valueOf(System.currentTimeMillis()));
        log.info("Manifest v" + SCHEMA_VERSION + " written: "
                + weapons.length() + " weapons, "
                + hullmods.length() + " hullmods, "
                + hulls.length() + " hulls across " + hullsPartCount + " part file(s), "
                + applicableByHull.size() + " per-hull-applicability entries, "
                + condExclByHull.size() + " conditional-exclusion entries, "
                + statefulMods.size() + " stateful mods flagged");
    }

    /** Write the hulls JSONObject across N part files, each ≤ HULLS_PART_MAX_BYTES.
     *  Keys iterated in sorted order so split boundaries are deterministic
     *  across regens (JSONObject.keys() hash-ordering would break reproducibility).
     *  @return part count written. */
    static int writeHullsSplit(JSONObject hulls) throws JSONException, IOException {
        java.util.List<String> sortedKeys = new java.util.ArrayList<String>();
        java.util.Iterator<String> it = hulls.keys();
        while (it.hasNext()) sortedKeys.add(it.next());
        java.util.Collections.sort(sortedKeys);

        java.util.List<JSONObject> chunks = new java.util.ArrayList<JSONObject>();
        JSONObject cur = new JSONObject();
        int curBytes = 2;  // "{}"
        for (String k : sortedKeys) {
            JSONObject v = hulls.getJSONObject(k);
            int entryBytes = k.length() + v.toString().length() + 6;  // "key":val,
            if (curBytes + entryBytes > HULLS_PART_MAX_BYTES && cur.length() > 0) {
                chunks.add(cur);
                cur = new JSONObject();
                curBytes = 2;
            }
            cur.put(k, v);
            curBytes += entryBytes;
        }
        if (cur.length() > 0) chunks.add(cur);

        for (int i = 0; i < chunks.size(); i++) {
            String name = String.format(MANIFEST_HULLS_PART_FMT, i);
            Global.getSettings().writeTextFileToCommon(name, chunks.get(i).toString());
        }
        return chunks.size();
    }

    /* ----------------------------------------------------------------------
     * Constants
     * -------------------------------------------------------------------- */

    static JSONObject buildConstantsJson(
            String gameVersion, String modCommitSha,
            Set<String> statefulMods) throws JSONException {
        JSONObject c = new JSONObject();
        c.put("game_version", gameVersion == null ? "" : gameVersion);
        c.put("manifest_schema_version", SCHEMA_VERSION);
        c.put("mod_commit_sha", modCommitSha == null ? "" : modCommitSha);
        c.put("generated_at", java.time.Instant.now().toString());
        c.put("max_vents_per_ship", MAX_VENTS_PER_SHIP);
        c.put("max_capacitors_per_ship", MAX_CAPACITORS_PER_SHIP);
        c.put("default_cr", DEFAULT_CR);

        // Engine constants read from data/config/settings.json.
        c.put("flux_per_capacitor", readSettingsFloat("fluxPerCapacitor", 200f));
        c.put("dissipation_per_vent", readSettingsFloat("dissipationPerVent", 10f));
        c.put("max_logistics_hullmods", readSettingsInt("maxLogisticsHullmods", 2));

        // Damage-type multipliers sourced from the DamageType enum's
        // engine-exposed getters (Commit G — replaces hardcoded constants).
        // Mod authors who retune multipliers at mod-load time surface
        // automatically; the GameVersion + ModCommitSha preflight pair
        // catches cross-bake drift.
        JSONObject shieldMult = new JSONObject();
        JSONObject armorMult  = new JSONObject();
        JSONObject hullMult   = new JSONObject();
        for (DamageType dt : DamageType.values()) {
            shieldMult.put(dt.name(), dt.getShieldMult());
            armorMult.put(dt.name(),  dt.getArmorMult());
            hullMult.put(dt.name(),   dt.getHullMult());
        }
        c.put("shield_damage_mult_by_type", shieldMult);
        c.put("armor_damage_mult_by_type",  armorMult);
        c.put("hull_damage_mult_by_type",   hullMult);

        // Determinism canary. Zero on vanilla 0.98a-RC8; non-empty is a
        // probe-level failure, surfaced in test_game_manifest.py.
        c.put("stateful_hullmods", toSortedJsonArray(statefulMods));
        return c;
    }

    static float readSettingsFloat(String key, float fallback) {
        try {
            return Global.getSettings().getFloat(key);
        } catch (Throwable t) {
            log.warn("settings.json missing float " + key + "; using fallback " + fallback);
            return fallback;
        }
    }

    static int readSettingsInt(String key, int fallback) {
        try {
            return Global.getSettings().getInt(key);
        } catch (Throwable t) {
            log.warn("settings.json missing int " + key + "; using fallback " + fallback);
            return fallback;
        }
    }

    /* ----------------------------------------------------------------------
     * Weapons
     * -------------------------------------------------------------------- */

    static JSONObject buildWeaponsJson(List<WeaponSpecAPI> specs) throws JSONException {
        JSONObject out = new JSONObject();
        Map<String, WeaponSpecAPI> sorted = new TreeMap<String, WeaponSpecAPI>();
        for (WeaponSpecAPI s : specs) sorted.put(s.getWeaponId(), s);
        for (Map.Entry<String, WeaponSpecAPI> e : sorted.entrySet()) {
            out.put(e.getKey(), weaponSpecToJson(e.getValue()));
        }
        return out;
    }

    static JSONObject weaponSpecToJson(WeaponSpecAPI spec) throws JSONException {
        JSONObject j = new JSONObject();
        j.put("id", spec.getWeaponId());
        j.put("type", String.valueOf(spec.getType()));
        j.put("size", String.valueOf(spec.getSize()));
        j.put("mount_type", String.valueOf(spec.getMountType()));
        putFiniteOrZero(j, "op_cost", spec.getOrdnancePointCost(null));
        j.put("damage_type", damageTypeToString(spec.getDamageType()));
        putFiniteOrZero(j, "max_range", spec.getMaxRange());
        j.put("is_beam", spec.isBeam());
        j.put("tags", toSortedJsonArray(spec.getTags()));

        float sdps = 0f;
        try {
            WeaponAPI.DerivedWeaponStatsAPI d = spec.getDerivedStats();
            if (d != null && Float.isFinite(d.getSustainedDps())) {
                sdps = d.getSustainedDps();
            }
        } catch (Throwable t) {
            log.warn("getDerivedStats failed for " + spec.getWeaponId()
                    + "; defaulting sustained_dps=0");
        }
        j.put("sustained_dps", sdps);
        return j;
    }

    static void putFiniteOrZero(JSONObject j, String key, float value) throws JSONException {
        if (Float.isFinite(value)) j.put(key, value);
        else j.put(key, 0f);
    }

    /* ----------------------------------------------------------------------
     * Hullmods — v2 schema: applicable_hull_sizes + incompatible_with DELETED.
     * Applicability lives per-hull in hulls[*].applicable_hullmods; pairwise
     * conflicts live per-hull in hulls[*].conditional_exclusions.
     * -------------------------------------------------------------------- */

    static JSONObject buildHullmodsJson(List<HullModSpecAPI> specs) throws JSONException {
        JSONObject out = new JSONObject();
        Map<String, HullModSpecAPI> sorted = new TreeMap<String, HullModSpecAPI>();
        for (HullModSpecAPI s : specs) sorted.put(s.getId(), s);
        for (Map.Entry<String, HullModSpecAPI> e : sorted.entrySet()) {
            out.put(e.getKey(), hullmodSpecToJson(e.getValue()));
        }
        return out;
    }

    static JSONObject hullmodSpecToJson(HullModSpecAPI spec) throws JSONException {
        JSONObject j = new JSONObject();
        j.put("id", spec.getId());
        j.put("tier", spec.getTier());
        j.put("hidden", spec.isHidden());
        j.put("hidden_everywhere", spec.isHiddenEverywhere());
        j.put("tags", toSortedJsonArray(spec.getTags()));
        j.put("ui_tags", toSortedJsonArray(spec.getUITags()));

        JSONObject opCostBySize = new JSONObject();
        opCostBySize.put("FRIGATE", spec.getCostFor(ShipAPI.HullSize.FRIGATE));
        opCostBySize.put("DESTROYER", spec.getCostFor(ShipAPI.HullSize.DESTROYER));
        opCostBySize.put("CRUISER", spec.getCostFor(ShipAPI.HullSize.CRUISER));
        opCostBySize.put("CAPITAL_SHIP", spec.getCostFor(ShipAPI.HullSize.CAPITAL_SHIP));
        j.put("op_cost_by_size", opCostBySize);
        return j;
    }

    /* ----------------------------------------------------------------------
     * Hulls — v2 schema: per-hull applicable_hullmods + conditional_exclusions
     * injected from the plugin's PROBE_ITERATE output.
     * -------------------------------------------------------------------- */

    static JSONObject buildHullsJson(
            List<ShipHullSpecAPI> specs,
            Map<String, Set<String>> applicableByHull,
            Map<String, Map<String, Set<String>>> condExclByHull) throws JSONException {
        // Emit ONLY hulls we probed (present in applicableByHull). Skipped
        // hulls (fighters, stations, modules, hidden) are intentionally
        // excluded from the manifest — they're not user-facing and carry
        // no meaningful applicability data. This makes the Python floor
        // invariant (every hull has ≥1 applicable_hullmod) safe: a hull
        // in the manifest is by construction a probed, usable hull.
        JSONObject out = new JSONObject();
        Map<String, ShipHullSpecAPI> sorted = new TreeMap<String, ShipHullSpecAPI>();
        for (ShipHullSpecAPI s : specs) sorted.put(s.getHullId(), s);
        for (Map.Entry<String, ShipHullSpecAPI> e : sorted.entrySet()) {
            Set<String> applicable = applicableByHull.get(e.getKey());
            if (applicable == null) continue;  // skipped by probe, not a user-hull
            Map<String, Set<String>> condExcl = condExclByHull.get(e.getKey());
            out.put(e.getKey(), hullSpecToJson(e.getValue(), applicable, condExcl));
        }
        return out;
    }

    static JSONObject hullSpecToJson(
            ShipHullSpecAPI spec,
            Set<String> applicableHullmods,
            Map<String, Set<String>> conditionalExclusions) throws JSONException {
        JSONObject j = new JSONObject();
        j.put("id", spec.getHullId());
        j.put("size", String.valueOf(spec.getHullSize()));
        j.put("ordnance_points", spec.getOrdnancePoints(null));
        putFiniteOrZero(j, "hitpoints", spec.getHitpoints());
        putFiniteOrZero(j, "armor_rating", spec.getArmorRating());
        putFiniteOrZero(j, "flux_capacity", spec.getFluxCapacity());
        putFiniteOrZero(j, "flux_dissipation", spec.getFluxDissipation());
        j.put("shield_type", shieldTypeToString(spec.getShieldType()));
        j.put("is_d_hull", spec.isDHull());
        j.put("base_hull_id", safeString(spec.getBaseHullId()));
        j.put("ship_system_id", safeString(spec.getShipSystemId()));
        j.put("is_carrier", spec.isCarrier());

        j.put("built_in_mods", listToJsonArray(spec.getBuiltInMods()));

        HashMap<String, String> biw = spec.getBuiltInWeapons();
        JSONObject biwJ = new JSONObject();
        if (biw != null) {
            Map<String, String> sortedBiw = new TreeMap<String, String>(biw);
            for (Map.Entry<String, String> e : sortedBiw.entrySet()) {
                biwJ.put(e.getKey(), e.getValue());
            }
        }
        j.put("built_in_weapons", biwJ);

        j.put("slots", slotsToJsonArray(spec.getAllWeaponSlotsCopy()));

        // Commit G per-hull probe output. Empty arrays when the hull was
        // skip-filtered (fighter/station/module/hidden) — no entry in the
        // probe maps → Python loader treats the set as empty; canary
        // tests enforce non-empty for every non-skipped hull (floor
        // invariant per plan R8).
        JSONArray applicable = new JSONArray();
        if (applicableHullmods != null) {
            TreeSet<String> sortedApp = new TreeSet<String>(applicableHullmods);
            for (String id : sortedApp) applicable.put(id);
        }
        j.put("applicable_hullmods", applicable);

        JSONObject condJson = new JSONObject();
        if (conditionalExclusions != null) {
            Map<String, Set<String>> sortedCond =
                    new TreeMap<String, Set<String>>(conditionalExclusions);
            for (Map.Entry<String, Set<String>> e : sortedCond.entrySet()) {
                condJson.put(e.getKey(), toSortedJsonArray(e.getValue()));
            }
        }
        j.put("conditional_exclusions", condJson);

        return j;
    }

    static JSONArray slotsToJsonArray(List<WeaponSlotAPI> slots) throws JSONException {
        JSONArray arr = new JSONArray();
        if (slots == null) return arr;
        for (WeaponSlotAPI slot : slots) {
            JSONObject s = new JSONObject();
            s.put("id", slot.getId());
            s.put("type", String.valueOf(slot.getWeaponType()));
            s.put("size", String.valueOf(slot.getSlotSize()));
            s.put("mount_type", slot.isHardpoint() ? "HARDPOINT"
                    : (slot.isTurret() ? "TURRET" : "OTHER"));
            s.put("is_decorative", slot.isDecorative());
            s.put("is_built_in", slot.isBuiltIn());
            arr.put(s);
        }
        return arr;
    }

    /* ----------------------------------------------------------------------
     * Helpers
     * -------------------------------------------------------------------- */

    static String damageTypeToString(DamageType d) {
        return d == null ? "UNKNOWN" : d.name();
    }

    static String shieldTypeToString(ShieldAPI.ShieldType t) {
        return t == null ? "UNKNOWN" : t.name();
    }

    static String safeString(String s) {
        return s == null ? "" : s;
    }

    static <T> JSONArray listToJsonArray(List<T> items) {
        JSONArray arr = new JSONArray();
        if (items == null) return arr;
        for (T it : items) arr.put(String.valueOf(it));
        return arr;
    }

    static JSONArray toSortedJsonArray(Set<String> set) {
        JSONArray arr = new JSONArray();
        if (set == null) return arr;
        TreeSet<String> sorted = new TreeSet<String>(set);
        for (String s : sorted) arr.put(s);
        return arr;
    }
}
