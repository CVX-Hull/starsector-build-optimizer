package starsector.combatharness;

import com.fs.starfarer.api.combat.AdmiralAIPlugin;

/**
 * Minimal admiral AI that never issues retreat orders.
 *
 * Ships retain their individual combat AI (approach, engage, manage flux,
 * use system) — this only removes fleet-level retreat decisions. Used by
 * CombatHarnessPlugin to prevent spawnFleetMember retreat-on-spawn behavior
 * where the default admiral AI continuously re-sets directRetreat=true.
 */
public class AttackAdmiralAI implements AdmiralAIPlugin {
    @Override
    public void preCombat() {
        // No fleet-level pre-combat orders
    }

    @Override
    public void advance(float amount) {
        // No fleet-level orders — ships fight using their individual combat AI
    }
}
