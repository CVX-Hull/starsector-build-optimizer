package starsector.combatharness;

import java.util.Map;
import java.util.UUID;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipVariantAPI;

/**
 * Constructs ShipVariantAPI objects programmatically from BuildSpec data.
 * Eliminates the need for .variant file I/O for optimizer-generated builds.
 */
public class VariantBuilder {

    /**
     * Hex chars taken from a UUID to disambiguate the cached
     * {@code ShipVariantAPI} per call. 8 hex chars = 32 bits = 4G+
     * unique values, which dwarfs the {@code clean_restart_matchups}
     * bound (a few thousand per JVM lifetime). See {@link
     * #uniqueVariantId(String)}.
     */
    static final int UNIQUE_VARIANT_SUFFIX_HEX_CHARS = 8;

    /**
     * Create a ShipVariantAPI in memory from a build specification.
     *
     * <p>Used by MissionDefinition's V2 setup path: a stock variant placeholder
     * is added via addToFleet, then the returned FleetMemberAPI's variant is
     * swapped to this custom one via setVariant before deployment. The earlier
     * createFleetMember + addFleetMember helper is gone because addFleetMember
     * triggers an internal retreat=true on the deployed ship that no public API
     * call overrides — see MissionDefinition.defineMission for the rationale.
     *
     * <p><b>Cross-matchup variant cache fix (2026-05-10)</b>: appends a random
     * UUID suffix to spec.variantId so each call produces a UNIQUE variantId
     * passed to {@code createEmptyVariant}. Without this, persistent-session
     * game instances reusing the same variantId across multiple matchups
     * (e.g. the same trial run vs different opponents) could receive a
     * cached/stale {@code ShipVariantAPI} where prior matchup's weapon
     * assignments leaked into slots not specified by the new build spec.
     * Wave 1 surfaced this as 0.6-19% LOADOUT_MISMATCH rates depending on
     * cell config; the cloud_worker_pool band-aid retries mismatches but
     * costs 7-19% wall-clock overhead. This fix eliminates the root cause.
     * Suffix is 8 hex chars from a UUID (4G+ unique per instance lifetime,
     * which dwarfs the {@code clean_restart_matchups} bound).
     *
     * @param spec build specification with hull, weapons, hullmods, flux
     * @return fully configured ShipVariantAPI
     * @throws IllegalArgumentException if hull ID is unknown or variant creation fails
     */
    public static ShipVariantAPI createVariant(MatchupConfig.BuildSpec spec) {
        ShipHullSpecAPI hullSpec = Global.getSettings().getHullSpec(spec.hullId);
        if (hullSpec == null) {
            throw new IllegalArgumentException("Unknown hull: " + spec.hullId);
        }

        String uniqueVariantId = uniqueVariantId(spec.variantId);

        ShipVariantAPI variant = Global.getSettings().createEmptyVariant(uniqueVariantId, hullSpec);
        if (variant == null) {
            throw new IllegalArgumentException("Failed to create variant: " + uniqueVariantId);
        }

        for (Map.Entry<String, String> entry : spec.weaponAssignments.entrySet()) {
            variant.addWeapon(entry.getKey(), entry.getValue());
        }

        for (String modId : spec.hullmods) {
            variant.addMod(modId);
        }

        variant.setNumFluxVents(spec.fluxVents);
        variant.setNumFluxCapacitors(spec.fluxCapacitors);
        variant.autoGenerateWeaponGroups();

        return variant;
    }

    /**
     * Append an 8-hex-char UUID suffix to {@code baseVariantId} so each
     * call returns a globally-unique id even for the same input. This is the
     * cross-matchup variant cache fix — see class javadoc.
     *
     * <p>Package-private for unit tests (no engine dependency).
     */
    static String uniqueVariantId(String baseVariantId) {
        String suffix = UUID.randomUUID().toString().replace("-", "")
                .substring(0, UNIQUE_VARIANT_SUFFIX_HEX_CHARS);
        return baseVariantId + "__" + suffix;
    }
}
