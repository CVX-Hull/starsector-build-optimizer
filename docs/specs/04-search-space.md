# Search Space Specification

Per-hull search space builder. Defined in `src/starsector_optimizer/search_space.py`.

## Functions

### get_compatible_weapons(slot, weapons, manifest) → list[Weapon]
Filter weapons by slot-type compatibility (read from
`game_manifest.SLOT_WEAPON_COMPATIBILITY` — engine-level invariant,
see spec 29) AND matching size. Returns list sorted by id.

### get_eligible_hullmods(hull, hullmods, manifest) → list[HullMod]
Exclude: `manifest.hullmods[hm.id].hidden_everywhere`, mods already
in `hull.built_in_mods`, mods whose
`manifest.hullmods[hm.id].applicable_hull_sizes` doesn't contain
`hull.hull_size`.

### build_search_space(hull, game_data, manifest, regime) → SearchSpace
Creates per-hull parameter space, masked by the user-selected
loadout regime (spec 24 §5F). `manifest: GameManifest` is a
**mandatory** positional argument (not keyword) — it supplies
every hullmod/weapon/hull-size constraint; silent defaults would
collapse this spec's single-source-of-truth invariant (spec 29).

`regime: RegimeConfig` is also mandatory — tests / legacy scripts
pass `REGIME_ENDGAME` to preserve pre-5F unfiltered behaviour.

## Regime mask semantics

Phase 5F filters the loadout catalogues (hullmods + weapons) at
construction time. Hull is not filtered by regime — hull choice is
a separate axis controlled by the caller.

- **Hullmod admitted** iff
  `manifest.hullmods[hm.id].tier <= regime.max_hullmod_tier`
  AND `regime.exclude_hullmod_tags.isdisjoint(manifest.hullmods[hm.id].tags)`
  AND `hm.id` in `get_eligible_hullmods(hull, ..., manifest)` (so
  applicability + shield-dependency filters apply first).
- **Weapon admitted** iff
  `regime.exclude_weapon_tags.isdisjoint(manifest.weapons[w.id].tags)`,
  AND the manifest-driven slot-compat / size check still passes.

One INFO log line is emitted per construction reporting admit counts.

## SearchSpace dataclass

```python
@dataclass
class SearchSpace:
    hull_id: str
    weapon_options: dict[str, list[str]]  # slot_id → ["empty", weapon_id, ...]
    eligible_hullmods: list[str]          # hullmod ids
    max_vents: int                        # manifest.constants.max_vents_per_ship
    max_capacitors: int                   # manifest.constants.max_capacitors_per_ship
    incompatible_pairs: list[tuple[str, str]]
```

- `weapon_options` excludes slots with built-in weapons.
- Each slot's options start with `"empty"` followed by compatible
  weapon IDs.
- `incompatible_pairs` is built by walking
  `manifest.hullmods[X].incompatible_with` for every eligible `X`
  and emitting one (X, Y) pair per Y — the manifest's probed
  symmetry invariant means the pair appears exactly once per
  direction, and dedup happens in `repair.py`.

## Invariants

- `hullmod_effects.HULLMOD_EFFECTS` is gone; no registry exists in
  Python post-manifest (spec 29).
- `hullmod_effects.INCOMPATIBLE_PAIRS` is gone; pairs are probed
  and cached in the manifest.
- `hullmod_effects.HULL_SIZE_RESTRICTIONS` is gone; applicability
  is per-hullmod in `manifest.hullmods[hm.id].applicable_hull_sizes`.
