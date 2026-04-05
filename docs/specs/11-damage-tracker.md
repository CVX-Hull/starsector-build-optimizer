# Damage Tracker Specification

Implements `DamageListener` to accumulate per-ship damage dealt and taken during combat. Defined in `combat-harness/src/main/java/starsector/combatharness/DamageTracker.java`.

## ShipDamageAccumulator

Inner class holding per-ship damage totals:

```java
public static class ShipDamageAccumulator {
    public float shieldDamageDealt;
    public float armorDamageDealt;
    public float hullDamageDealt;
    public float empDamageDealt;
    public float shieldDamageTaken;
    public float armorDamageTaken;
    public float hullDamageTaken;
    public float empDamageTaken;
    public int overloadCount;  // incremented externally by CombatHarnessPlugin
}
```

All fields start at 0. Accumulated additively via `reportDamageApplied()`.

## DamageListener Implementation

### `reportDamageApplied(Object source, CombatEntityAPI target, ApplyDamageResultAPI result)`

1. **Identify source ship:**
   - `source instanceof ShipAPI` → use directly
   - `source instanceof DamagingProjectileAPI` → `((DamagingProjectileAPI) source).getSource()`
   - `source instanceof BeamAPI` → `((BeamAPI) source).getSource()`
   - Otherwise → ignore (no source ship identified)

2. **Identify target ship:**
   - `target instanceof ShipAPI` → use directly
   - Otherwise → ignore (damage to non-ship entity)

3. **Skip if source or target is null**

4. **Skip if source and target are on the same side** (friendly fire not tracked)
   - Compare via `sourceShip.getOwner() == targetShip.getOwner()`

5. **Accumulate on source's accumulator:**
   - `shieldDamageDealt += result.getDamageToShields()`
   - `armorDamageDealt += result.getTotalDamageToArmor()`
   - `hullDamageDealt += result.getDamageToHull()`
   - `empDamageDealt += result.getEmpDamage()`

6. **Accumulate on target's accumulator:**
   - `shieldDamageTaken += result.getDamageToShields()`
   - `armorDamageTaken += result.getTotalDamageToArmor()`
   - `hullDamageTaken += result.getDamageToHull()`
   - `empDamageTaken += result.getEmpDamage()`

## Functions

### `Map<String, ShipDamageAccumulator> getAccumulators()`
Returns the full map of fleet_member_id → accumulator.

### `ShipDamageAccumulator getOrCreate(String fleetMemberId)`
Get existing accumulator or create a new zero-initialized one.
