# Search Space Specification

Per-hull search space builder. Defined in `src/starsector_optimizer/search_space.py`.

## Functions

### get_compatible_weapons(slot, weapons) → list[Weapon]
Filter weapons by slot type compatibility (from `SLOT_COMPATIBILITY` in hullmod_effects) AND matching size. Returns list of compatible weapons sorted by id.

### get_eligible_hullmods(hull, hullmods) → list[HullMod]
Exclude: hidden mods (`is_hidden=True`), mods already in `hull.built_in_mods`.

### build_search_space(hull, game_data, regime) → SearchSpace
Creates per-hull parameter space, masked by the user-selected loadout regime.

`regime: RegimeConfig` (from `models.py`, see spec 24) is a **mandatory positional argument** — no default. Every caller supplies one explicitly; silent defaults would let the mask drift out of sync with the Optuna study's per-`(hull, regime)` identity. For tests and scripts that pre-date the regime concept, pass `REGIME_ENDGAME` to preserve pre-5F unfiltered behaviour.

## Regime mask semantics

Phase 5F filters the loadout catalogues (hullmods + weapons) at construction time. Hull is **not** filtered by regime — hull choice is a separate axis controlled by the caller (e.g. `--hull` in `run_optimizer.py`).

- **Hullmod admitted** iff `hm.tier <= regime.max_hullmod_tier` AND `regime.exclude_hullmod_tags.isdisjoint(hm.tags)`.
- **Weapon admitted** iff `regime.exclude_weapon_tags.isdisjoint(w.tags)`, AND the existing `_is_assignable_weapon` slot-compat / size check still passes.

The mask is applied once per `build_search_space` call; downstream (`repair_build`, the optimizer's ask-tell loop) works against the already-masked `SearchSpace` and has no regime awareness. One INFO log line is emitted per construction reporting the admit counts for traceability.

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
