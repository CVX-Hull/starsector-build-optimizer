package starsector.combatharness;

import com.fs.starfarer.api.combat.BeamAPI;
import com.fs.starfarer.api.combat.CombatEntityAPI;
import com.fs.starfarer.api.combat.DamagingProjectileAPI;
import com.fs.starfarer.api.combat.ShipAPI;
import com.fs.starfarer.api.combat.listeners.ApplyDamageResultAPI;
import com.fs.starfarer.api.combat.listeners.DamageListener;

import java.util.HashMap;
import java.util.Map;

/**
 * Accumulates per-ship damage dealt and taken during combat.
 */
public class DamageTracker implements DamageListener {

    public static class ShipDamageAccumulator {
        public float shieldDamageDealt;
        public float armorDamageDealt;
        public float hullDamageDealt;
        public float empDamageDealt;
        public float shieldDamageTaken;
        public float armorDamageTaken;
        public float hullDamageTaken;
        public float empDamageTaken;
        public int overloadCount;
    }

    private final Map<String, ShipDamageAccumulator> accumulators = new HashMap<>();

    public Map<String, ShipDamageAccumulator> getAccumulators() {
        return accumulators;
    }

    /** Clear all accumulators. Called between matchups in a batched session. */
    public void reset() {
        accumulators.clear();
    }

    public ShipDamageAccumulator getOrCreate(String fleetMemberId) {
        return accumulators.computeIfAbsent(fleetMemberId, k -> new ShipDamageAccumulator());
    }

    /**
     * Record damage directly by fleet member IDs. Used by tests and internally.
     */
    public void recordDamage(String sourceId, String targetId,
                             float shieldDmg, float armorDmg, float hullDmg, float empDmg) {
        ShipDamageAccumulator sourceAcc = getOrCreate(sourceId);
        sourceAcc.shieldDamageDealt += shieldDmg;
        sourceAcc.armorDamageDealt += armorDmg;
        sourceAcc.hullDamageDealt += hullDmg;
        sourceAcc.empDamageDealt += empDmg;

        ShipDamageAccumulator targetAcc = getOrCreate(targetId);
        targetAcc.shieldDamageTaken += shieldDmg;
        targetAcc.armorDamageTaken += armorDmg;
        targetAcc.hullDamageTaken += hullDmg;
        targetAcc.empDamageTaken += empDmg;
    }

    @Override
    public void reportDamageApplied(Object source, CombatEntityAPI target,
                                    ApplyDamageResultAPI result) {
        ShipAPI sourceShip = resolveSourceShip(source);
        if (sourceShip == null) return;
        if (!(target instanceof ShipAPI)) return;

        ShipAPI targetShip = (ShipAPI) target;

        // Skip friendly fire
        if (sourceShip.getOwner() == targetShip.getOwner()) return;

        recordDamage(
                sourceShip.getFleetMemberId(),
                targetShip.getFleetMemberId(),
                result.getDamageToShields(),
                result.getTotalDamageToArmor(),
                result.getDamageToHull(),
                result.getEmpDamage()
        );
    }

    private ShipAPI resolveSourceShip(Object source) {
        if (source instanceof ShipAPI) {
            return (ShipAPI) source;
        }
        if (source instanceof DamagingProjectileAPI) {
            return ((DamagingProjectileAPI) source).getSource();
        }
        if (source instanceof BeamAPI) {
            return ((BeamAPI) source).getSource();
        }
        return null;
    }
}
