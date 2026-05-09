package starsector.combatharness;

import java.util.Map;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;
import com.fs.starfarer.api.combat.ShipVariantAPI;

/**
 * Constructs ShipVariantAPI objects programmatically from BuildSpec data.
 * Eliminates the need for .variant file I/O for optimizer-generated builds.
 */
public class VariantBuilder {

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
     * @param spec build specification with hull, weapons, hullmods, flux
     * @return fully configured ShipVariantAPI
     * @throws IllegalArgumentException if hull ID is unknown or variant creation fails
     */
    public static ShipVariantAPI createVariant(MatchupConfig.BuildSpec spec) {
        ShipHullSpecAPI hullSpec = Global.getSettings().getHullSpec(spec.hullId);
        if (hullSpec == null) {
            throw new IllegalArgumentException("Unknown hull: " + spec.hullId);
        }

        ShipVariantAPI variant = Global.getSettings().createEmptyVariant(spec.variantId, hullSpec);
        if (variant == null) {
            throw new IllegalArgumentException("Failed to create variant: " + spec.variantId);
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
}
