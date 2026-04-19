# Search Space Specification

Per-hull search space builder. Defined in `src/starsector_optimizer/search_space.py`.

## Functions

### get_compatible_weapons(slot, weapons, manifest) → list[Weapon]
Filter weapons by slot-type compatibility (read from
`game_manifest.SLOT_WEAPON_COMPATIBILITY` — engine-level invariant,
see spec 29) AND matching size. Returns list sorted by id.

### get_eligible_hullmods(hull, hullmods, manifest) → list[HullMod]
Returns the sorted list of hullmods whose IDs appear in
`manifest.hulls[hull.id].applicable_hullmods` and are not marked
`hidden` in `manifest.hullmods[hm.id]`. The per-hull applicable set
is engine-probed (schema v2, spec 29) against an empty variant of
this specific hull — which inherits the hull's built-in mods via
`createEmptyVariant` — so hull-size, shield-type, carrier-only,
phase-only, civilian-only, AND built-in-induced exclusions are all
captured automatically. No Python-side hull-size or shield-dependency
re-derivation.

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
- `weapon_options` also excludes slots whose type is absent from
  `SLOT_WEAPON_COMPATIBILITY` — concretely `SYSTEM`, `BUILT_IN`,
  `DECORATIVE`, and `LAUNCH_BAY`. Fighter-bay population is out of
  the optimizer's decision space (the manifest does not enumerate
  `FighterWingSpecAPI`, `BuildSpec` has no wing assignments, and
  `VariantBuilder` does not populate wings). Carrier hulls are
  optimizable for their non-bay slots only; their bays deploy empty.
  Documented as a fidelity floor in
  `docs/reference/phase7-search-space-compression.md` §2.10.
- Each slot's options start with `"empty"` followed by compatible
  weapon IDs.
- `incompatible_pairs` is built by walking
  `manifest.hulls[hull.id].conditional_exclusions` (schema v2).
  For each `(A → {B, C, …})` entry where `A` is eligible on this
  hull, emits `(A, B)` for every `B` that is also eligible. The
  probe records exclusions directionally (A-installed blocks B),
  and `_collect_incompatible_pairs` canonicalises each edge to
  `(min, max)` ordering with a `seen` set so every edge appears
  exactly once.

## Invariants

- `hullmod_effects.py` is gone; no Python-side registry of hullmod
  effects, incompatibilities, or hull-size restrictions exists
  post-manifest (spec 29).
- Schema v2: per-hull applicability replaces the v1 hullmod-level
  `applicable_hull_sizes` / `incompatible_with` fields. Those
  fields are deleted from `HullmodSpec`; `HullManifestEntry.applicable_hullmods`
  and `HullManifestEntry.conditional_exclusions` are authoritative.
- All game constants (vent/capacitor caps, damage multipliers,
  default CR) are in `manifest.constants`, not scattered in Python.
