---
type: spec
status: shipped
last-validated: unvalidated
---

# Variant Builder Specification

Java utility for in-memory variant construction from build specifications. Defined in `combat-harness/src/main/java/starsector/combatharness/VariantBuilder.java`.

Eliminates `.variant` file I/O for optimizer-generated builds. The Java harness constructs `ShipVariantAPI` objects programmatically using the Starsector API.

## Functions

### `static ShipVariantAPI createVariant(MatchupConfig.BuildSpec spec)`

Constructs a ship variant in memory from a build specification.

| Step | Operation |
|------|-----------|
| 1 | Fetch hull spec: `Global.getSettings().getHullSpec(spec.hullId)` |
| 2 | Disambiguate cache key: `String uniqueId = uniqueVariantId(spec.variantId)` (appends a random `__<8 hex chars>` suffix; see §`uniqueVariantId`) |
| 3 | Create empty variant: `Global.getSettings().createEmptyVariant(uniqueId, hullSpec)` |
| 4 | Assign weapons: for each `(slotId, weaponId)` in `spec.weaponAssignments`, call `variant.addWeapon(slotId, weaponId)` |
| 5 | Add hullmods: for each `modId` in `spec.hullmods`, call `variant.addMod(modId)` |
| 6 | Set flux: `variant.setNumFluxVents(spec.fluxVents)`, `variant.setNumFluxCapacitors(spec.fluxCapacitors)` |
| 7 | Auto-group weapons: `variant.autoGenerateWeaponGroups()` |
| 8 | Return `ShipVariantAPI` |

**Null-safety:** Per combat-harness/CLAUDE.md caveat #6:
- Null-check `getHullSpec()` return — throw `IllegalArgumentException("Unknown hull: " + spec.hullId)` if null
- Null-check `createEmptyVariant()` return — throw `IllegalArgumentException("Failed to create variant: " + uniqueId)` if null

### `static String uniqueVariantId(String baseVariantId)`

Cross-matchup variant cache fix (2026-05-10). Appends `"__"` + an `UNIQUE_VARIANT_SUFFIX_HEX_CHARS`-long substring of a fresh `UUID.randomUUID()` to disambiguate the cache key inside `createEmptyVariant`. Without this, a persistent-session JVM that reused `spec.variantId` across matchups received a cached `ShipVariantAPI` with the previous matchup's weapon assignments still bound to slots not specified by the new spec — surfaced empirically as 0.6%–19% LOADOUT_MISMATCH rates on Wave 1 cells C2/C3. Suffix length 8 hex chars = 32 bits = 4G+ unique values per JVM lifetime.

### Pre-2026-05-10 helper (removed)

`createFleetMember(BuildSpec)` was removed when MissionDefinition migrated to the V2 placeholder-then-swap path: `addToFleet(side, stockVariantId, ...)` + `member.setVariant(VariantBuilder.createVariant(spec), false, true)` before deployment. The V1 `spawnFleetMember(member, ...)` path triggered the engine's internal `directRetreat=true` flag (see combat-harness/CLAUDE.md §"Why single-matchup-per-mission").

## Usage

- **MissionDefinition**: `addToFleet(side, stockVariantId, ...)` returns `FleetMemberAPI`; immediately swap with `member.setVariant(VariantBuilder.createVariant(spec), false, true)` for player ships.
- Enemy ships are unchanged — still use stock variant IDs via `addToFleet()` / `spawnShipOrWing()`.

## Design Rationale

Starsector's `ShipVariantAPI` supports full programmatic construction via `createEmptyVariant()` + `addWeapon()` + `addMod()` + `setNumFluxVents/Capacitors()`. This eliminates the need to write `.variant` files to disk and avoids variant file caching issues that would block persistent game sessions (Phase T2).
