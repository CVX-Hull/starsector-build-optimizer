package starsector.combatharness;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.DamageType;
import com.fs.starfarer.api.combat.ShieldAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipVariantAPI;
import com.fs.starfarer.api.combat.WeaponAPI;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.loading.HullModSpecAPI;
import com.fs.starfarer.api.loading.WeaponSlotAPI;
import com.fs.starfarer.api.loading.WeaponSpecAPI;

import org.apache.log4j.Logger;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * Dumps an authoritative JSON manifest of all game rules:
 * weapons, hullmods, hulls (incl. skins), and engine constants.
 *
 * Python reads this manifest as the single source of truth for game
 * mechanics, replacing hand-coded registries that drift on every game
 * update. See docs/specs/29-game-manifest.md.
 *
 * Output file: saves/common/combat_harness_manifest.json.data
 * (follows the existing combat_harness_ filename convention).
 *
 * Trigger: presence of saves/common/combat_harness_manifest_request.data
 * (handled by TitleScreenPlugin). One-shot: writes manifest + done
 * signal + System.exit(0).
 */
public final class ManifestDumper {

    private static final Logger log = Logger.getLogger(ManifestDumper.class);

    // Manifest is split across 4 files because the game's
    // writeTextFileToCommon caps each write at 1 MiB and the compact
    // manifest is ~1.3 MiB on vanilla 0.98a. Python loader reads all
    // four and merges into a single GameManifest.
    public static final String MANIFEST_CONSTANTS_FILE = MatchupConfig.COMMON_PREFIX + "manifest_constants.json";
    public static final String MANIFEST_WEAPONS_FILE   = MatchupConfig.COMMON_PREFIX + "manifest_weapons.json";
    public static final String MANIFEST_HULLMODS_FILE  = MatchupConfig.COMMON_PREFIX + "manifest_hullmods.json";
    public static final String MANIFEST_HULLS_FILE     = MatchupConfig.COMMON_PREFIX + "manifest_hulls.json";
    public static final String MANIFEST_REQUEST_FILE = MatchupConfig.COMMON_PREFIX + "manifest_request";
    public static final String MANIFEST_DONE_FILE    = MatchupConfig.COMMON_PREFIX + "manifest_done";

    /**
     * Schema version. MUST equal Python GameManifest.EXPECTED_SCHEMA_VERSION.
     * Bump when the manifest format changes in a non-backward-compatible way.
     */
    public static final int SCHEMA_VERSION = 1;

    /** Engine caps that Python used to hardcode. Authoritative values
     *  documented by the game engine. */
    public static final int MAX_VENTS_PER_SHIP = 30;
    public static final int MAX_CAPACITORS_PER_SHIP = 30;
    public static final float DEFAULT_CR = 0.7f;

    /** Damage-type multipliers against shield / armor layers.
     *  Starsector exposes these via engine code, NOT settings.json —
     *  hardcoded here against Starsector 0.98a-RC8. Any engine patch
     *  that touches damage calculation demands a manifest regen AND a
     *  re-verification of these numbers (they are NOT stable-by-contract;
     *  this is an indie game actively evolving). */
    static final float SHIELD_MULT_KINETIC = 2.0f;
    static final float SHIELD_MULT_HIGH_EXPLOSIVE = 0.5f;
    static final float SHIELD_MULT_ENERGY = 1.0f;
    static final float SHIELD_MULT_FRAGMENTATION = 0.25f;
    static final float ARMOR_MULT_KINETIC = 0.5f;
    static final float ARMOR_MULT_HIGH_EXPLOSIVE = 2.0f;
    static final float ARMOR_MULT_ENERGY = 1.0f;
    static final float ARMOR_MULT_FRAGMENTATION = 0.25f;

    private ManifestDumper() {}

    /** Top-level entry point. Reads game state, writes manifest + done.
     *
     *  Output is split across 4 files (each < 1 MiB to fit the engine's
     *  writeTextFileToCommon cap). Files are written in order so the
     *  done-sentinel appears LAST — the Python driver polling the
     *  done file is guaranteed all 4 part files are present.
     */
    /** Probe-driven dump. Callers supply a map of live probe ships (one per
     *  hull size); an empty / null map produces a manifest with empty
     *  applicable_hull_sizes + incompatible_with on every hullmod — useful
     *  for CI / bootstrap contexts that cannot start a combat engine. */
    public static void dumpToCommon(
            String gameVersion, String modCommitSha,
            Map<ShipAPI.HullSize, ShipAPI> probeShips)
            throws JSONException, IOException {
        JSONObject constants = buildConstantsJson(gameVersion, modCommitSha);
        JSONObject weapons = buildWeaponsJson(Global.getSettings().getAllWeaponSpecs());

        Map<ShipAPI.HullSize, ShipAPI> usedProbes =
                probeShips == null ? Collections.<ShipAPI.HullSize, ShipAPI>emptyMap()
                                   : probeShips;
        JSONObject hullmods = buildHullmodsJson(
                Global.getSettings().getAllHullModSpecs(), usedProbes);
        JSONObject hulls = buildHullsJson(Global.getSettings().getAllShipHullSpecs());

        // Compact JSON — Python loader ignores whitespace; fits 1 MiB cap.
        Global.getSettings().writeTextFileToCommon(MANIFEST_CONSTANTS_FILE, constants.toString());
        Global.getSettings().writeTextFileToCommon(MANIFEST_WEAPONS_FILE, weapons.toString());
        Global.getSettings().writeTextFileToCommon(MANIFEST_HULLMODS_FILE, hullmods.toString());
        Global.getSettings().writeTextFileToCommon(MANIFEST_HULLS_FILE, hulls.toString());
        Global.getSettings().writeTextFileToCommon(
                MANIFEST_DONE_FILE, String.valueOf(System.currentTimeMillis()));
        log.info("Manifest written: "
                + weapons.length() + " weapons, "
                + hullmods.length() + " hullmods, "
                + hulls.length() + " hulls");
    }

    /* ----------------------------------------------------------------------
     * Constants
     * -------------------------------------------------------------------- */

    static JSONObject buildConstantsJson(String gameVersion, String modCommitSha)
            throws JSONException {
        JSONObject c = new JSONObject();
        c.put("game_version", gameVersion == null ? "" : gameVersion);
        c.put("manifest_schema_version", SCHEMA_VERSION);
        c.put("mod_commit_sha", modCommitSha == null ? "" : modCommitSha);
        c.put("generated_at", java.time.Instant.now().toString());
        c.put("max_vents_per_ship", MAX_VENTS_PER_SHIP);
        c.put("max_capacitors_per_ship", MAX_CAPACITORS_PER_SHIP);
        c.put("default_cr", DEFAULT_CR);

        // Engine constants read from data/config/settings.json.
        // Python used to hardcode these; they are now authoritative-via-manifest
        // so a game-update + manifest regen catches any drift (indie game —
        // Alex is free to retune flux economy or logistics caps anytime).
        c.put("flux_per_capacitor", readSettingsFloat("fluxPerCapacitor", 200f));
        c.put("dissipation_per_vent", readSettingsFloat("dissipationPerVent", 10f));
        c.put("max_logistics_hullmods", readSettingsInt("maxLogisticsHullmods", 2));

        // Damage-type multipliers against shield / armor layers. Engine rule,
        // NOT exposed via settings.json — hardcoded in this mod against the
        // `game_version` stamped above. Re-verify on every engine patch.
        JSONObject shieldMult = new JSONObject();
        shieldMult.put("KINETIC", SHIELD_MULT_KINETIC);
        shieldMult.put("HIGH_EXPLOSIVE", SHIELD_MULT_HIGH_EXPLOSIVE);
        shieldMult.put("ENERGY", SHIELD_MULT_ENERGY);
        shieldMult.put("FRAGMENTATION", SHIELD_MULT_FRAGMENTATION);
        c.put("shield_damage_mult_by_type", shieldMult);

        JSONObject armorMult = new JSONObject();
        armorMult.put("KINETIC", ARMOR_MULT_KINETIC);
        armorMult.put("HIGH_EXPLOSIVE", ARMOR_MULT_HIGH_EXPLOSIVE);
        armorMult.put("ENERGY", ARMOR_MULT_ENERGY);
        armorMult.put("FRAGMENTATION", ARMOR_MULT_FRAGMENTATION);
        c.put("armor_damage_mult_by_type", armorMult);
        return c;
    }

    /** Read a float from settings.json; fall back to `fallback` on missing key
     *  / exception. Defensive against settings.json schema drift between patches. */
    static float readSettingsFloat(String key, float fallback) {
        try {
            return Global.getSettings().getFloat(key);
        } catch (Throwable t) {
            log.warn("settings.json missing float " + key + "; using fallback " + fallback);
            return fallback;
        }
    }

    /** Same for int settings. */
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
        // Sort by id for deterministic manifest output (git diff readability).
        Map<String, WeaponSpecAPI> sorted = new TreeMap<String, WeaponSpecAPI>();
        for (WeaponSpecAPI s : specs) sorted.put(s.getWeaponId(), s);
        for (Map.Entry<String, WeaponSpecAPI> e : sorted.entrySet()) {
            out.put(e.getKey(), weaponSpecToJson(e.getValue()));
        }
        return out;
    }

    static JSONObject weaponSpecToJson(WeaponSpecAPI spec) throws JSONException {
        // Minimal essential field set — the manifest is size-constrained
        // (1 MiB per writeTextFileToCommon cap, even when split into 4
        // part files). Python side only consumes: id, type, size,
        // mount_type, op_cost, damage_type, max_range, is_beam,
        // sustained_dps, tags. Everything else would add ~500 KiB across
        // 200 weapons without a consumer.
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

        // Flattened sustained DPS (the only DerivedStats field actually
        // consumed by _build_covariate_vector's scorer path). Coerced to
        // 0 on non-finite / exception (e.g. some beam / station-module
        // specs throw NPE in getDerivedStats outside a combat context).
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

    /** Coerce non-finite values to 0 before putting — org.json rejects NaN/Infinity. */
    static void putFiniteOrZero(JSONObject j, String key, float value) throws JSONException {
        if (Float.isFinite(value)) {
            j.put(key, value);
        } else {
            j.put(key, 0f);
        }
    }

    /** Same as putFiniteOrZero for doubles. */
    static void putFiniteOrZero(JSONObject j, String key, double value) throws JSONException {
        if (Double.isFinite(value)) {
            j.put(key, value);
        } else {
            j.put(key, 0.0);
        }
    }

    /* ----------------------------------------------------------------------
     * Hullmods
     * -------------------------------------------------------------------- */

    static JSONObject buildHullmodsJson(
            List<HullModSpecAPI> specs,
            Map<ShipAPI.HullSize, ShipAPI> probeShips) throws JSONException {
        JSONObject out = new JSONObject();
        Map<String, HullModSpecAPI> sorted = new TreeMap<String, HullModSpecAPI>();
        for (HullModSpecAPI s : specs) sorted.put(s.getId(), s);

        log.info("ManifestDumper: buildHullmodsJson — " + sorted.size()
                + " hullmods, probeShips=" + probeShips.size() + " "
                + probeShips.keySet());

        // First pass: applicable_hull_sizes for every mod (needed by the
        // incompatibility probe, which only mutates variants of sizes where
        // both partners start out applicable).
        Map<String, Set<ShipAPI.HullSize>> applicableMap =
                new HashMap<String, Set<ShipAPI.HullSize>>();
        long t0 = System.currentTimeMillis();
        for (Map.Entry<String, HullModSpecAPI> e : sorted.entrySet()) {
            applicableMap.put(e.getKey(), probeApplicableSizes(e.getValue(), probeShips));
        }
        int modsWithApplicable = 0;
        for (Set<ShipAPI.HullSize> s : applicableMap.values()) {
            if (!s.isEmpty()) modsWithApplicable++;
        }
        log.info("ManifestDumper: applicable-size probe finished in "
                + (System.currentTimeMillis() - t0) + "ms; "
                + modsWithApplicable + "/" + sorted.size()
                + " mods with non-empty applicable_hull_sizes");

        // Second pass: O(N²) incompatibility probe. For each (a, b) with
        // overlapping applicability, install `a` onto a probe ship of a
        // shared size, then ask `b.getEffect().isApplicableToShip(ship)`
        // — if it changed from applicable-alone to inapplicable-with-A,
        // that is the asymmetric evidence we record symmetrically below.
        long t1 = System.currentTimeMillis();
        Map<String, Set<String>> incompatMap = probeIncompatibilities(
                sorted, applicableMap, probeShips);
        int edgeCount = 0;
        for (Set<String> s : incompatMap.values()) edgeCount += s.size();
        log.info("ManifestDumper: incompatibility probe finished in "
                + (System.currentTimeMillis() - t1) + "ms; "
                + (edgeCount / 2) + " distinct incompatibility edges");

        for (Map.Entry<String, HullModSpecAPI> e : sorted.entrySet()) {
            Set<ShipAPI.HullSize> applicable = applicableMap.get(e.getKey());
            Set<String> incompat = incompatMap.get(e.getKey());
            out.put(e.getKey(), hullmodSpecToJson(e.getValue(), applicable, incompat));
        }
        return out;
    }

    static JSONObject hullmodSpecToJson(
            HullModSpecAPI spec,
            Set<ShipAPI.HullSize> applicableSizes,
            Set<String> incompatibleWith) throws JSONException {
        // Minimal essential field set. Python consumers: id, tier,
        // op_cost_by_size, tags, hidden. ui_tags kept for regime-filter
        // heuristics. Metadata fields (manufacturer, base_value, rarity,
        // effect_class) dropped — not consumed and add bulk.
        JSONObject j = new JSONObject();
        j.put("id", spec.getId());
        j.put("tier", spec.getTier());
        j.put("hidden", spec.isHidden());
        j.put("hidden_everywhere", spec.isHiddenEverywhere());
        j.put("tags", toSortedJsonArray(spec.getTags()));
        j.put("ui_tags", toSortedJsonArray(spec.getUITags()));

        // Per-hull-size OP costs via the authoritative getCostFor() getter.
        // The frigate/destroyer/cruiser/capital getters also exist but
        // getCostFor() matches what the engine charges at variant-compose time.
        JSONObject opCostBySize = new JSONObject();
        opCostBySize.put("FRIGATE", spec.getCostFor(ShipAPI.HullSize.FRIGATE));
        opCostBySize.put("DESTROYER", spec.getCostFor(ShipAPI.HullSize.DESTROYER));
        opCostBySize.put("CRUISER", spec.getCostFor(ShipAPI.HullSize.CRUISER));
        opCostBySize.put("CAPITAL_SHIP", spec.getCostFor(ShipAPI.HullSize.CAPITAL_SHIP));
        j.put("op_cost_by_size", opCostBySize);

        // Applicable hull sizes and incompatibility sets were probed
        // empirically against freshly-factoried ships of each hull size
        // (see probeApplicableSizes / probeIncompatibilities). Empty set
        // means either "truly inapplicable to every size" (rare, usually
        // hidden builtin-only mods) or "the probe crashed and we skipped"
        // — in both cases Python treats the mod as usable-anywhere with a
        // WARN log so the operator can spot probe breakage on git diff.
        JSONArray applicable = new JSONArray();
        if (applicableSizes != null) {
            // Stable sort by declaration order so git diffs stay clean.
            if (applicableSizes.contains(ShipAPI.HullSize.FRIGATE)) applicable.put("FRIGATE");
            if (applicableSizes.contains(ShipAPI.HullSize.DESTROYER)) applicable.put("DESTROYER");
            if (applicableSizes.contains(ShipAPI.HullSize.CRUISER)) applicable.put("CRUISER");
            if (applicableSizes.contains(ShipAPI.HullSize.CAPITAL_SHIP)) applicable.put("CAPITAL_SHIP");
        }
        j.put("applicable_hull_sizes", applicable);

        JSONArray incompat = new JSONArray();
        if (incompatibleWith != null) {
            TreeSet<String> sortedIncompat = new TreeSet<String>(incompatibleWith);
            for (String id : sortedIncompat) incompat.put(id);
        }
        j.put("incompatible_with", incompat);
        return j;
    }

    /* ----------------------------------------------------------------------
     * Probe helpers (ship factoring + effect-method queries outside combat)
     * -------------------------------------------------------------------- */

    /** Vanilla hulls that represent each HullSize bucket. Chosen to be
     *  first-tier, non-phase, non-civilian ships so base hullmod-effect
     *  pattern-matching doesn't get blocked by incidental ship attributes. */
    private static final String[][] PROBE_HULLS = {
        {"FRIGATE", "wolf"},
        {"DESTROYER", "hammerhead"},
        {"CRUISER", "eagle"},
        {"CAPITAL_SHIP", "onslaught"},
    };

    static Map<ShipAPI.HullSize, ShipAPI> createProbeShips() {
        // Legacy path — callers inside a live combat engine now pass
        // probe ships in directly via dumpToCommon(..., probeShips).
        // This method is kept for title-screen-direct callers and
        // always returns empty (see tryCreateProbeShip comment).
        Map<ShipAPI.HullSize, ShipAPI> out = new HashMap<ShipAPI.HullSize, ShipAPI>();
        for (String[] row : PROBE_HULLS) {
            ShipAPI.HullSize size;
            try {
                size = ShipAPI.HullSize.valueOf(row[0]);
            } catch (IllegalArgumentException ex) {
                continue;
            }
            ShipAPI ship = tryCreateProbeShip(row[1]);
            if (ship != null) out.put(size, ship);
        }
        log.info("ManifestDumper: createProbeShips returned " + out.size()
                + " ships (0 is normal for title-screen fallback path)");
        return out;
    }

    static ShipAPI tryCreateProbeShip(String hullId) {
        // FleetMemberAPI.createShip(String) doesn't exist in this Starsector
        // version, and Global.getCombatEngine() is null outside a live mission.
        // The probe is therefore driven from the ManifestProbePlugin inside a
        // running combat SETUP — see the `data.missions.manifest_probe`
        // package. This path is only reached when dumpToCommon is called
        // without ready-made probe ships, which happens in legacy boot paths
        // that skip the probe mission entirely.
        log.warn("probe: no ready-made probe ship — manifest will emit empty "
                + "applicable_hull_sizes for " + hullId);
        return null;
    }

    /** Probe each (mod, hullSize) pair to discover which sizes accept it.
     *  Uses HullModEffect.isApplicableToShip(ShipAPI) as the primary
     *  check per the official API contract — getUnapplicableReason() is
     *  only meaningful AFTER isApplicableToShip returns false and is
     *  documented-wise allowed to return stale fallback strings (several
     *  vanilla mods like ShieldShunt and ConvertedHangar exploit this by
     *  only gating the reason string behind isApplicableToShip). */
    static Set<ShipAPI.HullSize> probeApplicableSizes(
            HullModSpecAPI spec, Map<ShipAPI.HullSize, ShipAPI> probeShips) {
        Set<ShipAPI.HullSize> result = new java.util.HashSet<ShipAPI.HullSize>();
        for (Map.Entry<ShipAPI.HullSize, ShipAPI> e : probeShips.entrySet()) {
            ShipAPI ship = e.getValue();
            if (ship == null) continue;
            try {
                if (spec.getEffect().isApplicableToShip(ship)) {
                    result.add(e.getKey());
                }
            } catch (Throwable t) {
                log.warn("probe: applicable(" + spec.getId() + ", "
                        + e.getKey() + ") threw: " + t);
            }
        }
        return result;
    }

    /** For every hullmod pair (a, b) with overlapping hull-size applicability,
     *  check whether installing `a` makes `b` inapplicable on any shared size.
     *  Any asymmetry is recorded symmetrically (if a→!b, we also mark b→!a).
     *
     *  Complexity: O(N²·S) where N≈130 vanilla hullmods and S=4 sizes — about
     *  68k getUnapplicableReason() calls. Seconds of wall-clock at dump time;
     *  fast enough to run every manifest regen. */
    static Map<String, Set<String>> probeIncompatibilities(
            Map<String, HullModSpecAPI> sorted,
            Map<String, Set<ShipAPI.HullSize>> applicableMap,
            Map<ShipAPI.HullSize, ShipAPI> probeShips) {
        Map<String, Set<String>> out = new HashMap<String, Set<String>>();
        for (String id : sorted.keySet()) {
            out.put(id, new java.util.HashSet<String>());
        }
        String[] ids = sorted.keySet().toArray(new String[0]);
        for (int i = 0; i < ids.length; i++) {
            String aId = ids[i];
            HullModSpecAPI a = sorted.get(aId);
            Set<ShipAPI.HullSize> aSizes = applicableMap.get(aId);
            if (aSizes == null || aSizes.isEmpty()) continue;
            for (int jj = i + 1; jj < ids.length; jj++) {
                String bId = ids[jj];
                HullModSpecAPI b = sorted.get(bId);
                Set<ShipAPI.HullSize> bSizes = applicableMap.get(bId);
                if (bSizes == null || bSizes.isEmpty()) continue;

                boolean incompat = false;
                for (ShipAPI.HullSize size : aSizes) {
                    if (!bSizes.contains(size)) continue;
                    ShipAPI ship = probeShips.get(size);
                    if (ship == null) continue;
                    ShipVariantAPI variant = ship.getVariant();
                    if (variant == null) continue;
                    try {
                        variant.addMod(aId);
                        // Same contract as the applicability probe: use
                        // isApplicableToShip directly. If b became
                        // inapplicable after installing a, that's
                        // empirical proof of incompatibility.
                        if (!b.getEffect().isApplicableToShip(ship)) incompat = true;
                    } catch (Throwable t) {
                        log.debug("probe: incompat(" + aId + "," + bId + "," + size
                                + ") threw — " + t);
                    } finally {
                        try { variant.removeMod(aId); } catch (Throwable ignored) {}
                    }
                    if (incompat) break;
                }
                if (incompat) {
                    out.get(aId).add(bId);
                    out.get(bId).add(aId);
                }
            }
        }
        return out;
    }

    /* ----------------------------------------------------------------------
     * Hulls
     * -------------------------------------------------------------------- */

    static JSONObject buildHullsJson(List<ShipHullSpecAPI> specs) throws JSONException {
        JSONObject out = new JSONObject();
        Map<String, ShipHullSpecAPI> sorted = new TreeMap<String, ShipHullSpecAPI>();
        for (ShipHullSpecAPI s : specs) sorted.put(s.getHullId(), s);
        for (Map.Entry<String, ShipHullSpecAPI> e : sorted.entrySet()) {
            out.put(e.getKey(), hullSpecToJson(e.getValue()));
        }
        return out;
    }

    static JSONObject hullSpecToJson(ShipHullSpecAPI spec) throws JSONException {
        // Python consumers: id, size, ordnance_points, hitpoints,
        // armor_rating, flux_capacity, flux_dissipation, shield_type,
        // built_in_mods, built_in_weapons, slots, is_d_hull, is_carrier,
        // ship_system_id. Metadata (name, designation, manufacturer,
        // base_value, peak_cr, cr_to_deploy, hints, collision_radius,
        // engine spec, fighter_bays) dropped to fit 1 MiB cap.
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
            // Sort by slot id
            Map<String, String> sortedBiw = new TreeMap<String, String>(biw);
            for (Map.Entry<String, String> e : sortedBiw.entrySet()) {
                biwJ.put(e.getKey(), e.getValue());
            }
        }
        j.put("built_in_weapons", biwJ);

        j.put("slots", slotsToJsonArray(spec.getAllWeaponSlotsCopy()));
        return j;
    }

    static JSONArray slotsToJsonArray(List<WeaponSlotAPI> slots) throws JSONException {
        // Python consumes: id, type, size, mount_type, is_decorative
        // (skip decoratives during repair), is_built_in (skip built-ins
        // for user weapon-assignment). angle/arc/is_hidden/is_system/
        // is_station_module not used — dropped to fit 1 MiB.
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

    static <E extends Enum<E>> JSONArray toSortedStringJsonArray(java.util.Collection<E> items) {
        JSONArray arr = new JSONArray();
        if (items == null) return arr;
        TreeSet<String> sorted = new TreeSet<String>();
        for (E e : items) sorted.add(e.name());
        for (String s : sorted) arr.put(s);
        return arr;
    }
}
