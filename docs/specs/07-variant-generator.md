# Variant Generator Specification

Build → .variant JSON generation. Defined in `src/starsector_optimizer/variant.py`.

## Functions

### generate_variant(build, hull, game_data, variant_id=None) → dict
Produces a dict matching the `.variant` JSON schema. Default variant_id: `{hull_id}_opt_{random_hex}`.

### assign_weapon_groups(build, hull, game_data) → list[dict]
Default: all weapons on autofire in individual groups. hull parameter needed to exclude built-in weapon slots.

### write_variant_file(variant, path) → None
Write JSON with indent=4.

### load_variant_file(path) → dict
Parse loose JSON (reuse `parse_loose_json`).

### build_to_build_spec(build, hull, game_data, variant_id) → BuildSpec
Convert a `Build` to a `BuildSpec` for matchup queue serialization. Filters out `None` weapon slots, built-in weapon slots, and weapons not found in `game_data.weapons` (same filtering logic as `assign_weapon_groups()`). Hullmods are sorted alphabetically in the output tuple.

## Output Schema (.variant JSON)
```json
{
    "variantId": "...",
    "hullId": "...",
    "displayName": "Optimizer Build",
    "fluxVents": N,
    "fluxCapacitors": N,
    "hullMods": [...],
    "permaMods": [],
    "sMods": [],
    "goalVariant": false,
    "weaponGroups": [...],
    "wings": []
}
```
