# Damage Tracker Specification

Implements `DamageListener` to accumulate per-ship damage dealt and taken during combat. Defined in `combat-harness/src/main/java/starsector/combatharness/DamageTracker.java`.

## ShipDamageAccumulator

Inner class holding per-ship damage totals:

```java
public static class ShipDamageAccumulator {
    public float shieldDamageDealt, armorDamageDealt, hullDamageDealt, empDamageDealt;
    public float shieldDamageTaken, armorDamageTaken, hullDamageTaken, empDamageTaken;
    public int overloadCount;
}
```

All fields start at 0. Accumulated additively via `reportDamageApplied()`.

## DamageListener Implementation

### `reportDamageApplied(Object source, CombatEntityAPI target, ApplyDamageResultAPI result)`

1. Identify source ship: `instanceof ShipAPI` → direct; `instanceof DamagingProjectileAPI` → `.getSource()`; `instanceof BeamAPI` → `.getSource()`
2. Target must be `instanceof ShipAPI`; skip otherwise
3. Skip friendly fire (`sourceShip.getOwner() == targetShip.getOwner()`)
4. Accumulate damage on source's `*Dealt` fields and target's `*Taken` fields

## Functions

### `Map<String, ShipDamageAccumulator> getAccumulators()`
Returns the full map of fleetMemberId → accumulator.

### `ShipDamageAccumulator getOrCreate(String fleetMemberId)`
Get existing or create zero-initialized accumulator.

### `void recordDamage(String sourceId, String targetId, float shield, float armor, float hull, float emp)`
Direct damage recording (used by tests and internally by `reportDamageApplied`).

### `void reset()`
Clear all accumulators. Called between matchups in a batched combat session.
