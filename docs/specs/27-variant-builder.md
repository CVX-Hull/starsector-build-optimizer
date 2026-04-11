# Variant Builder Specification

Java utility for in-memory variant construction from build specifications. Defined in `combat-harness/src/main/java/starsector/combatharness/VariantBuilder.java`.

Eliminates `.variant` file I/O for optimizer-generated builds. The Java harness constructs `ShipVariantAPI` objects programmatically using the Starsector API.

## Functions

### `static ShipVariantAPI createVariant(MatchupConfig.BuildSpec spec)`

Constructs a ship variant in memory from a build specification.

| Step | Operation |
|------|-----------|
| 1 | Fetch hull spec: `Global.getSettings().getHullSpec(spec.hullId)` |
| 2 | Create empty variant: `Global.getSettings().createEmptyVariant(spec.variantId, hullSpec)` |
| 3 | Assign weapons: for each `(slotId, weaponId)` in `spec.weaponAssignments`, call `variant.addWeapon(slotId, weaponId)` |
| 4 | Add hullmods: for each `modId` in `spec.hullmods`, call `variant.addMod(modId)` |
| 5 | Set flux: `variant.setNumFluxVents(spec.fluxVents)`, `variant.setNumFluxCapacitors(spec.fluxCapacitors)` |
| 6 | Auto-group weapons: `variant.autoGenerateWeaponGroups()` |
| 7 | Return `ShipVariantAPI` |

**Null-safety:** Per combat-harness/CLAUDE.md caveat #6:
- Null-check `getHullSpec()` return — throw `IllegalArgumentException("Unknown hull: " + spec.hullId)` if null
- Null-check `createEmptyVariant()` return — throw `IllegalArgumentException("Failed to create variant: " + spec.variantId)` if null

### `static FleetMemberAPI createFleetMember(MatchupConfig.BuildSpec spec)`

| Step | Operation |
|------|-----------|
| 1 | Call `createVariant(spec)` |
| 2 | `Global.getSettings().createFleetMember(FleetMemberType.SHIP, variant)` |
| 3 | `member.getRepairTracker().setCR(spec.cr)` — set CR from build spec |
| 4 | Return `FleetMemberAPI` |

## Usage

- **MissionDefinition** (first matchup): adds stock placeholder via `addToFleet()` for the deployment screen
- **CombatHarnessPlugin** (all matchups): `VariantBuilder.createFleetMember(spec)` → `fleetManager.spawnFleetMember(member, location, facing, 0f)` → `ensureCombatReady(ship, spec.cr)`
- Enemy ships are unchanged — still use stock variant IDs via `addToFleet()` / `spawnShipOrWing()`

## Design Rationale

Starsector's `ShipVariantAPI` supports full programmatic construction via `createEmptyVariant()` + `addWeapon()` + `addMod()` + `setNumFluxVents/Capacitors()`. This eliminates the need to write `.variant` files to disk and avoids variant file caching issues that would block persistent game sessions (Phase T2).
