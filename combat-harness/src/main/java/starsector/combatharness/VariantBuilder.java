package starsector.combatharness;

import java.util.Map;

import com.fs.starfarer.api.Global;
import com.fs.starfarer.api.combat.ShipVariantAPI;
import com.fs.starfarer.api.fleet.FleetMemberAPI;
import com.fs.starfarer.api.fleet.FleetMemberType;
import com.fs.starfarer.api.loading.HullModSpecAPI;
import com.fs.starfarer.api.loading.WeaponSpecAPI;
import com.fs.starfarer.api.combat.ShipHullSpecAPI;

/**
 * Constructs ShipVariantAPI objects programmatically from BuildSpec data.
 * Eliminates the need for .variant file I/O for optimizer-generated builds.
 */
public class VariantBuilder {

    /**
     * Create a ShipVariantAPI in memory from a build specification.
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

    /**
     * Create a FleetMemberAPI from a build specification.
     *
     * @param spec build specification
     * @return FleetMemberAPI wrapping the programmatically created variant
     */
    public static FleetMemberAPI createFleetMember(MatchupConfig.BuildSpec spec) {
        ShipVariantAPI variant = createVariant(spec);
        return Global.getSettings().createFleetMember(FleetMemberType.SHIP, variant);
    }
}
