# Search Space Specification

Per-hull search space builder. Defined in `src/starsector_optimizer/search_space.py`.

## Functions

### get_compatible_weapons(slot, weapons) → list[Weapon]
Filter weapons by slot type compatibility (from `SLOT_COMPATIBILITY` in hullmod_effects) AND matching size. Returns list of compatible weapons sorted by id.

### get_eligible_hullmods(hull, hullmods) → list[HullMod]
Exclude: hidden mods (`is_hidden=True`), mods already in `hull.built_in_mods`.

### build_search_space(hull, game_data) → SearchSpace
Creates per-hull parameter space.

## SearchSpace Dataclass

```python
@dataclass
class SearchSpace:
    hull_id: str
    weapon_options: dict[str, list[str]]  # slot_id → ["empty", weapon_id, ...]
    eligible_hullmods: list[str]          # hullmod ids
    max_vents: int
    max_capacitors: int
    incompatible_pairs: list[tuple[str, str]]
```

- `weapon_options` excludes slots with built-in weapons
- Each slot's options start with `"empty"` followed by compatible weapon IDs
- `incompatible_pairs` from `INCOMPATIBLE_PAIRS` in hullmod_effects
