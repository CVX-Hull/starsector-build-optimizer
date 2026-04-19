"""Per-hull search space builder — manifest-driven.

All game-rule filtering (slot compatibility, hullmod incompatibilities,
hull-size applicability) reads from `GameManifest`, not hand-coded tables.
The old `hullmod_effects.py` registry has been deleted; see spec 29 for
the manifest-as-oracle contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .game_manifest import GameManifest, SLOT_WEAPON_COMPATIBILITY
from .models import (
    GameData,
    HullMod,
    RegimeConfig,
    ShipHull,
    Weapon,
    WeaponSlot,
)

logger = logging.getLogger(__name__)


@dataclass
class SearchSpace:
    hull_id: str
    weapon_options: dict[str, list[str]]  # slot_id → ["empty", weapon_id, ...]
    eligible_hullmods: list[str]
    max_vents: int
    max_capacitors: int
    incompatible_pairs: list[tuple[str, str]] = field(default_factory=list)


def _is_assignable_weapon(w: Weapon) -> bool:
    """Filter out system weapons, fighter-only weapons, and restricted weapons."""
    if "SYSTEM" in w.hints:
        return False
    if "restricted" in w.tags:
        return False
    if w.op_cost <= 0 and not w.is_beam:
        # 0-OP non-beam weapons are typically system payloads (e.g., gorgon_payload)
        return False
    return True


def _regime_admits_hullmod(hm: HullMod, regime: RegimeConfig) -> bool:
    """Phase 5F mask: admit iff tier is within ceiling AND no excluded tag is present."""
    if hm.tier > regime.max_hullmod_tier:
        return False
    return regime.exclude_hullmod_tags.isdisjoint(hm.tags)


def _regime_admits_weapon(w: Weapon, regime: RegimeConfig) -> bool:
    """Phase 5F mask: admit iff no excluded blueprint/availability tag is present."""
    return regime.exclude_weapon_tags.isdisjoint(w.tags)


def get_compatible_weapons(
    slot: WeaponSlot,
    weapons: dict[str, Weapon],
) -> list[Weapon]:
    """Get weapons compatible with a slot (matching type, size, and assignability)."""
    allowed_types = SLOT_WEAPON_COMPATIBILITY.get(slot.slot_type, frozenset())
    return sorted(
        [w for w in weapons.values()
         if w.weapon_type in allowed_types and w.size == slot.slot_size
         and _is_assignable_weapon(w)],
        key=lambda w: w.id,
    )


def get_eligible_hullmods(
    hull: ShipHull,
    hullmods: dict[str, HullMod],
    manifest: GameManifest,
) -> list[HullMod]:
    """Get hullmods eligible for installation on this hull.

    Filters out:
    - Hidden mods (ui-invisible or story-only).
    - Built-in mods (already installed permanently on the hull).
    - Mods whose manifest-probed `applicable_hull_sizes` excludes this
      hull's size (authoritative engine-rule from the probe; empty
      `applicable_hull_sizes` means probe-unknown → treat as applicable
      rather than silently exclude, so unprobed mods remain candidates).
    """
    builtin = set(hull.built_in_mods)
    eligible: list[HullMod] = []
    for hm in hullmods.values():
        if hm.is_hidden:
            continue
        if hm.id in builtin:
            continue
        spec = manifest.hullmods.get(hm.id)
        if spec is not None and spec.applicable_hull_sizes:
            if hull.hull_size not in spec.applicable_hull_sizes:
                continue
        eligible.append(hm)
    return sorted(eligible, key=lambda m: m.id)


def _collect_incompatible_pairs(
    eligible_ids: set[str], manifest: GameManifest,
) -> list[tuple[str, str]]:
    """Enumerate every (a, b) pair from manifest-probed incompatibility edges.

    Each undirected edge is emitted once with the canonical ordering a<b so
    downstream consumers (repair + is_feasible) don't need to deduplicate.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for hm_id in eligible_ids:
        spec = manifest.hullmods.get(hm_id)
        if spec is None:
            continue
        for other in spec.incompatible_with:
            if other not in eligible_ids:
                continue
            a, b = (hm_id, other) if hm_id < other else (other, hm_id)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            pairs.append((a, b))
    return pairs


def build_search_space(
    hull: ShipHull,
    game_data: GameData,
    regime: RegimeConfig,
    manifest: GameManifest,
) -> SearchSpace:
    """Build the optimization search space for a given hull under the given loadout regime.

    `regime` is a mandatory positional argument — Phase 5F treats the mask as
    a load-bearing boundary between the global component catalogue and the
    per-run feasible set; a silent default would let the mask drift out of
    sync with the Optuna study's per-(hull, regime) identity. Pass
    `REGIME_ENDGAME` to preserve pre-5F unfiltered behaviour.

    `manifest` supplies authoritative game-rule data (hullmod applicable_hull_sizes,
    hullmod incompatible_with edges, engine caps on vents/capacitors). Loaded
    once at orchestrator startup via `GameManifest.load()`.
    """
    weapon_options: dict[str, list[str]] = {}
    total_weapons_admitted = 0
    total_weapons_considered = 0
    for slot in hull.weapon_slots:
        if slot.id in hull.built_in_weapons:
            continue
        if slot.slot_type not in SLOT_WEAPON_COMPATIBILITY:
            continue  # SYSTEM, BUILT_IN, DECORATIVE, LAUNCH_BAY, etc.
        compatible = get_compatible_weapons(slot, game_data.weapons)
        total_weapons_considered += len(compatible)
        admitted = [w for w in compatible if _regime_admits_weapon(w, regime)]
        total_weapons_admitted += len(admitted)
        weapon_options[slot.id] = ["empty"] + [w.id for w in admitted]

    eligible_pre = get_eligible_hullmods(hull, game_data.hullmods, manifest)
    eligible = [m for m in eligible_pre if _regime_admits_hullmod(m, regime)]
    eligible_ids = {m.id for m in eligible}
    pairs = _collect_incompatible_pairs(eligible_ids, manifest)

    logger.info(
        "Regime '%s' admits %d/%d hullmods, %d/%d weapons for hull=%s "
        "(%d incompatibility edges)",
        regime.name,
        len(eligible), len(eligible_pre),
        total_weapons_admitted, total_weapons_considered,
        hull.id, len(pairs),
    )

    return SearchSpace(
        hull_id=hull.id,
        weapon_options=weapon_options,
        eligible_hullmods=[m.id for m in eligible],
        max_vents=manifest.constants.max_vents_per_ship,
        max_capacitors=manifest.constants.max_capacitors_per_ship,
        incompatible_pairs=pairs,
    )
