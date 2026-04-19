"""Game manifest — authoritative Starsector game-rule oracle.

The Java combat-harness mod dumps an authoritative JSON manifest of every
weapon, hullmod, hull, and relevant engine constant (see
`combat-harness/src/main/java/starsector/combatharness/ManifestDumper.java`).
Python reads from this manifest and does zero game-rule re-derivation — the
old hand-coded `hullmod_effects.py` surface is gone (14 drift bugs with it).

Regeneration: `uv run python scripts/update_manifest.py` boots a headless
Starsector, waits for the mod to emit the four-part manifest, merges it
into a single `game/starsector/manifest.json`, and the operator commits it.

Schema-version contract: `EXPECTED_SCHEMA_VERSION` must equal
`manifest.constants.manifest_schema_version`. Mismatch is a hard error
that halts preflight — see spec 29 §Schema versioning.

Forward-compat on enum members: unknown values (a future Starsector
version introducing a new `SlotType` / `MountType` / etc.) log a WARN and
the whole spec owning that field is skipped — partial deserialization is
safer than crashing the orchestrator on an irrelevant new weapon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from .models import DamageType, HullSize, ShieldType, SlotSize, SlotType, WeaponType

logger = logging.getLogger(__name__)

EXPECTED_SCHEMA_VERSION: int = 2

# Vanilla 0.98 baselines (one-time empirical dump 2026-04-19):
# 118 weapons, 129 hullmods, 532 hulls. Pinned as lower bounds — modded
# installs only add. New vanilla bases require a deliberate bump + rationale.
MIN_VANILLA_WEAPON_COUNT: int = 100
MIN_VANILLA_HULLMOD_COUNT: int = 60
MIN_VANILLA_HULL_COUNT: int = 200

# Commit G R8 floor: every non-skipped hull emitted by the probe must have
# at least one applicable mod. An empty set means either the probe crashed
# mid-iteration (dropped hull mid-probe) or the probe sprite wasn't live
# in the combat engine when we asked — either way, un-interpretable data
# that corrupts downstream optimization. Fail loud at load-time.
MIN_APPLICABLE_HULLMODS_PER_HULL: int = 1

# Known game-rule vocabulary for hull `size` we deliberately filter out
# rather than warn on. Fighter wings appear in `getAllShipHullSpecs()` but
# are not part of the ship-optimization domain. Silent skip — NOT forward-
# compat (that behavior is reserved for truly unknown future enum members).
_IGNORED_HULL_SIZES: frozenset[str] = frozenset({"FIGHTER"})

_DEFAULT_MANIFEST_PATH: Path = Path("game/starsector/manifest.json")


# Slot → weapon-type compatibility, encoding the Starsector engine rule
# "which WeaponType categories fit which SlotType". These are ENGINE-LEVEL
# invariants (the slot-type taxonomy has been stable since Starsector 0.7;
# e.g., COMPOSITE slots accepting BALLISTIC+MISSILE is fundamental to how
# hulls are differentiated in the game). The rule is NOT exposed via
# settings.json and is hardcoded in the game engine.
#
# Distinct from hullmod rules (which drift between patches and go through
# the probe). Slot compatibility rules could in principle change if Alex
# added a new slot type, but that would be a bigger-than-patch change —
# spotted immediately by the manifest-schema-version bump that such a
# change would force.
#
# Used by search_space.py for filtering legal (slot, weapon) pairs and by
# repair.py for is_feasible() violation detection.
SLOT_WEAPON_COMPATIBILITY: dict[SlotType, frozenset[WeaponType]] = {
    SlotType.BALLISTIC: frozenset({WeaponType.BALLISTIC}),
    SlotType.ENERGY:    frozenset({WeaponType.ENERGY}),
    SlotType.MISSILE:   frozenset({WeaponType.MISSILE}),
    SlotType.HYBRID:    frozenset({WeaponType.BALLISTIC, WeaponType.ENERGY}),
    SlotType.COMPOSITE: frozenset({WeaponType.BALLISTIC, WeaponType.MISSILE}),
    SlotType.SYNERGY:   frozenset({WeaponType.ENERGY, WeaponType.MISSILE}),
    SlotType.UNIVERSAL: frozenset({
        WeaponType.BALLISTIC, WeaponType.ENERGY, WeaponType.MISSILE,
    }),
}

_ParseT = TypeVar("_ParseT", bound=Enum)


# --- Manifest-only enums (game-rule vocabulary that differs from SlotType) ----


class WeaponMountType(str, Enum):
    """Slot-type a weapon requires.

    Distinct from the weapon's damage/ammo `type` — a weapon with
    `type=MISSILE` can have `mount_type=SYNERGY` if it fits synergy slots.
    """
    BALLISTIC = "BALLISTIC"
    ENERGY = "ENERGY"
    MISSILE = "MISSILE"
    HYBRID = "HYBRID"
    SYNERGY = "SYNERGY"
    COMPOSITE = "COMPOSITE"
    UNIVERSAL = "UNIVERSAL"


class SlotMountType(str, Enum):
    """Physical mount kind on a hull slot — how a weapon is attached.

    Engine emits TURRET / HARDPOINT for assignable slots and OTHER for
    everything else (built-ins, decoratives, system slots).
    """
    TURRET = "TURRET"
    HARDPOINT = "HARDPOINT"
    OTHER = "OTHER"


# --- Parsed manifest dataclasses ---------------------------------------------


@dataclass(frozen=True)
class WeaponSpec:
    id: str
    type: WeaponType
    size: SlotSize
    mount_type: WeaponMountType
    op_cost: int
    damage_type: DamageType
    max_range: float
    sustained_dps: float
    is_beam: bool
    tags: frozenset[str]


@dataclass(frozen=True)
class HullmodSpec:
    """Schema v2: no `applicable_hull_sizes` or `incompatible_with` —
    applicability is per-hull (see `HullManifestEntry.applicable_hullmods`)
    and conditional conflicts are per-hull (see
    `HullManifestEntry.conditional_exclusions`). Single source of truth."""
    id: str
    tier: int
    hidden: bool
    hidden_everywhere: bool
    tags: frozenset[str]
    ui_tags: frozenset[str]
    op_cost_by_size: dict[HullSize, int]

    def op_cost(self, hull_size: HullSize) -> int:
        return self.op_cost_by_size.get(hull_size, 0)


@dataclass(frozen=True)
class HullSlot:
    id: str
    type: SlotType
    size: SlotSize
    mount_type: SlotMountType
    is_built_in: bool
    is_decorative: bool


@dataclass(frozen=True)
class HullManifestEntry:
    """Schema v2: `applicable_hullmods` + `conditional_exclusions` hold
    the authoritative per-hull applicability answer (engine-probed by
    PROBE_ITERATE). Python consumers NEVER re-derive applicability."""
    id: str
    size: HullSize
    ordnance_points: int
    hitpoints: float
    armor_rating: float
    flux_capacity: float
    flux_dissipation: float
    shield_type: ShieldType
    ship_system_id: str
    built_in_mods: tuple[str, ...]
    built_in_weapons: dict[str, str]
    slots: tuple[HullSlot, ...]
    is_d_hull: bool
    is_carrier: bool
    base_hull_id: str | None
    # Schema v2 — per-hull applicability.
    applicable_hullmods: frozenset[str]
    # Schema v2 — installed-mod A → set of mods B that drop out of
    # applicability when A is present on THIS hull (engine-probed).
    conditional_exclusions: dict[str, frozenset[str]]


@dataclass(frozen=True)
class GameConstants:
    game_version: str
    manifest_schema_version: int
    mod_commit_sha: str
    generated_at: str
    max_vents_per_ship: int
    max_capacitors_per_ship: int
    default_cr: float
    flux_per_capacitor: float
    dissipation_per_vent: float
    max_logistics_hullmods: int
    shield_damage_mult_by_type: dict[DamageType, float]
    armor_damage_mult_by_type: dict[DamageType, float]
    # Schema v2 — hull multiplier (FRAGMENTATION / KINETIC / HE / ENERGY /
    # OTHER, all 1.0 in vanilla 0.98a but structurally present for future
    # balance-patch drift detection).
    hull_damage_mult_by_type: dict[DamageType, float]
    # Schema v2 — determinism canary: IDs of hullmods whose
    # isApplicableToShip returned different values on back-to-back probes
    # of the same ShipAPI. Zero in vanilla 0.98a-RC8; non-empty is a
    # hard test-suite failure (the mod surface is unreliable).
    stateful_hullmods: frozenset[str]


@dataclass(frozen=True)
class GameManifest:
    weapons: dict[str, WeaponSpec]
    hullmods: dict[str, HullmodSpec]
    hulls: dict[str, HullManifestEntry]
    constants: GameConstants

    @classmethod
    def load(cls, path: Path | str | None = None) -> GameManifest:
        manifest_path = Path(path) if path is not None else _DEFAULT_MANIFEST_PATH
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"manifest not found at {manifest_path!s}; run "
                "`uv run python scripts/update_manifest.py` to regenerate."
            )
        data = json.loads(manifest_path.read_text())
        got = data["constants"]["manifest_schema_version"]
        if got != EXPECTED_SCHEMA_VERSION:
            raise ValueError(
                f"manifest schema mismatch: file={got} "
                f"expected={EXPECTED_SCHEMA_VERSION}; regenerate via "
                "`uv run python scripts/update_manifest.py` and rebake AMI."
            )

        constants = _parse_constants(data["constants"])
        weapons: dict[str, WeaponSpec] = {}
        for wid, raw in data["weapons"].items():
            spec = _parse_weapon(raw)
            if spec is not None:
                weapons[wid] = spec
        hullmods: dict[str, HullmodSpec] = {}
        for hid, raw in data["hullmods"].items():
            spec = _parse_hullmod(raw)
            if spec is not None:
                hullmods[hid] = spec
        # Parse hulls with cross-ref filtering against `hullmods` — any mod
        # ID in a hull's applicable_hullmods or conditional_exclusions that
        # doesn't resolve to a hullmods key gets dropped with a WARN (per
        # CLAUDE.md Principle #5: warn-and-skip, not hard-fail on unknown).
        hulls: dict[str, HullManifestEntry] = {}
        known_hullmod_ids = set(hullmods.keys())
        for hid, raw in data["hulls"].items():
            spec = _parse_hull(raw, known_hullmod_ids=known_hullmod_ids)
            if spec is not None:
                hulls[hid] = spec
        # Load-time floor invariant (audit R8): every non-skipped hull must
        # have ≥ MIN_APPLICABLE_HULLMODS_PER_HULL applicable mods. An empty
        # set means the probe crashed mid-iteration or the ship wasn't
        # live — either way the manifest is not safe to optimize against.
        empty_hulls = [
            hid for hid, h in hulls.items()
            if len(h.applicable_hullmods) < MIN_APPLICABLE_HULLMODS_PER_HULL
        ]
        if empty_hulls:
            raise ValueError(
                f"{len(empty_hulls)} hulls have no applicable_hullmods — "
                f"probe output is corrupt. Examples: {empty_hulls[:5]}. "
                f"Regenerate via `uv run python scripts/update_manifest.py`."
            )
        return cls(
            weapons=weapons,
            hullmods=hullmods,
            hulls=hulls,
            constants=constants,
        )


# --- Parsing helpers ---------------------------------------------------------


def _parse_enum(
    cls: type[_ParseT], raw: Any, *, field_name: str, spec_id: str
) -> _ParseT | None:
    """Parse an enum value, logging WARN on unknown members.

    Returns None when the raw value is absent from the enum. Callers skip
    the surrounding spec — partial deserialization beats a hard crash on a
    future Starsector version introducing a new enum member we do not yet
    care about (CLAUDE.md Principle #5).
    """
    if raw is None:
        logger.warning(
            "manifest: missing %s on spec %r; skipping", field_name, spec_id
        )
        return None
    try:
        return cls(raw)
    except ValueError:
        logger.warning(
            "manifest: unknown %s=%r on spec %r; skipping spec",
            field_name, raw, spec_id,
        )
        return None


def _parse_weapon(raw: dict[str, Any]) -> WeaponSpec | None:
    wid = raw.get("id", "<unknown>")
    wtype = _parse_enum(WeaponType, raw.get("type"), field_name="type", spec_id=wid)
    wsize = _parse_enum(SlotSize, raw.get("size"), field_name="size", spec_id=wid)
    wmount = _parse_enum(
        WeaponMountType, raw.get("mount_type"), field_name="mount_type", spec_id=wid
    )
    wdamage = _parse_enum(
        DamageType, raw.get("damage_type"), field_name="damage_type", spec_id=wid
    )
    if None in (wtype, wsize, wmount, wdamage):
        return None
    return WeaponSpec(
        id=wid,
        type=wtype,
        size=wsize,
        mount_type=wmount,
        op_cost=int(raw.get("op_cost", 0)),
        damage_type=wdamage,
        max_range=float(raw.get("max_range", 0.0)),
        sustained_dps=float(raw.get("sustained_dps", 0.0)),
        is_beam=bool(raw.get("is_beam", False)),
        tags=frozenset(raw.get("tags", [])),
    )


def _parse_hullmod(raw: dict[str, Any]) -> HullmodSpec | None:
    hid = raw.get("id", "<unknown>")
    op_cost_by_size: dict[HullSize, int] = {}
    for size_name, cost in (raw.get("op_cost_by_size") or {}).items():
        size = _parse_enum(HullSize, size_name, field_name="op_cost_by_size", spec_id=hid)
        if size is None:
            continue
        op_cost_by_size[size] = int(cost)
    return HullmodSpec(
        id=hid,
        tier=int(raw.get("tier", 0)),
        hidden=bool(raw.get("hidden", False)),
        hidden_everywhere=bool(raw.get("hidden_everywhere", False)),
        tags=frozenset(raw.get("tags", [])),
        ui_tags=frozenset(raw.get("ui_tags", [])),
        op_cost_by_size=op_cost_by_size,
    )


def _parse_slot(raw: dict[str, Any], *, hull_id: str) -> HullSlot | None:
    sid = raw.get("id", "<unknown>")
    stype = _parse_enum(
        SlotType, raw.get("type"), field_name="slot.type", spec_id=f"{hull_id}.{sid}"
    )
    ssize = _parse_enum(
        SlotSize, raw.get("size"), field_name="slot.size", spec_id=f"{hull_id}.{sid}"
    )
    smount = _parse_enum(
        SlotMountType,
        raw.get("mount_type"),
        field_name="slot.mount_type",
        spec_id=f"{hull_id}.{sid}",
    )
    if None in (stype, ssize, smount):
        return None
    return HullSlot(
        id=sid,
        type=stype,
        size=ssize,
        mount_type=smount,
        is_built_in=bool(raw.get("is_built_in", False)),
        is_decorative=bool(raw.get("is_decorative", False)),
    )


def _parse_hull(
    raw: dict[str, Any],
    *,
    known_hullmod_ids: frozenset[str] | set[str],
) -> HullManifestEntry | None:
    hid = raw.get("id", "<unknown>")
    raw_size = raw.get("size")
    if raw_size in _IGNORED_HULL_SIZES:
        return None
    hsize = _parse_enum(HullSize, raw_size, field_name="size", spec_id=hid)
    shield = _parse_enum(
        ShieldType, raw.get("shield_type"), field_name="shield_type", spec_id=hid
    )
    if hsize is None or shield is None:
        return None
    slots: list[HullSlot] = []
    for slot_raw in raw.get("slots") or []:
        slot = _parse_slot(slot_raw, hull_id=hid)
        if slot is not None:
            slots.append(slot)
    base_hull_id = raw.get("base_hull_id")
    if isinstance(base_hull_id, str) and not base_hull_id:
        base_hull_id = None

    # Schema v2 — per-hull applicable_hullmods + conditional_exclusions.
    # Drop any mod ID that isn't in the known hullmods set (WARN + skip
    # per CLAUDE.md Principle #5). Dangling refs indicate the probe saw
    # a hullmod that our loader subsequently rejected due to an unknown
    # enum value; letting it through would trip downstream `hullmods[x]`
    # lookups with KeyError.
    applicable_raw = raw.get("applicable_hullmods") or []
    applicable_filtered: set[str] = set()
    for m in applicable_raw:
        if m in known_hullmod_ids:
            applicable_filtered.add(m)
        else:
            logger.warning(
                "manifest: hull %r applicable_hullmods references unknown mod %r; skipping",
                hid, m,
            )

    cond_raw = raw.get("conditional_exclusions") or {}
    cond_filtered: dict[str, frozenset[str]] = {}
    for a, b_list in cond_raw.items():
        if a not in known_hullmod_ids:
            logger.warning(
                "manifest: hull %r conditional_exclusions key %r not in known hullmods; skipping",
                hid, a,
            )
            continue
        filtered_b: set[str] = set()
        for b in b_list or []:
            if b in known_hullmod_ids:
                filtered_b.add(b)
            else:
                logger.warning(
                    "manifest: hull %r conditional_exclusions[%r] lists unknown mod %r; skipping",
                    hid, a, b,
                )
        if filtered_b:
            cond_filtered[a] = frozenset(filtered_b)

    return HullManifestEntry(
        id=hid,
        size=hsize,
        ordnance_points=int(raw.get("ordnance_points", 0)),
        hitpoints=float(raw.get("hitpoints", 0.0)),
        armor_rating=float(raw.get("armor_rating", 0.0)),
        flux_capacity=float(raw.get("flux_capacity", 0.0)),
        flux_dissipation=float(raw.get("flux_dissipation", 0.0)),
        shield_type=shield,
        ship_system_id=str(raw.get("ship_system_id", "")),
        built_in_mods=tuple(raw.get("built_in_mods") or []),
        built_in_weapons=dict(raw.get("built_in_weapons") or {}),
        slots=tuple(slots),
        is_d_hull=bool(raw.get("is_d_hull", False)),
        is_carrier=bool(raw.get("is_carrier", False)),
        base_hull_id=base_hull_id,
        applicable_hullmods=frozenset(applicable_filtered),
        conditional_exclusions=cond_filtered,
    )


def _parse_damage_mult(
    raw: dict[str, Any] | None, *, field_name: str
) -> dict[DamageType, float]:
    """Parse a {DamageType_name: multiplier} block.

    Unknown damage-type keys WARN and skip (forward-compat per
    CLAUDE.md Principle #5). Missing recognized keys raise KeyError at
    downstream call sites — better fail loudly than silently default.
    """
    result: dict[DamageType, float] = {}
    for key, value in (raw or {}).items():
        dtype = _parse_enum(DamageType, key, field_name=field_name, spec_id="constants")
        if dtype is None:
            continue
        result[dtype] = float(value)
    return result


def _parse_constants(raw: dict[str, Any]) -> GameConstants:
    return GameConstants(
        game_version=str(raw.get("game_version", "")),
        manifest_schema_version=int(raw["manifest_schema_version"]),
        mod_commit_sha=str(raw.get("mod_commit_sha", "")),
        generated_at=str(raw.get("generated_at", "")),
        max_vents_per_ship=int(raw.get("max_vents_per_ship", 0)),
        max_capacitors_per_ship=int(raw.get("max_capacitors_per_ship", 0)),
        default_cr=float(raw.get("default_cr", 0.0)),
        flux_per_capacitor=float(raw["flux_per_capacitor"]),
        dissipation_per_vent=float(raw["dissipation_per_vent"]),
        max_logistics_hullmods=int(raw["max_logistics_hullmods"]),
        shield_damage_mult_by_type=_parse_damage_mult(
            raw.get("shield_damage_mult_by_type"),
            field_name="shield_damage_mult_by_type",
        ),
        armor_damage_mult_by_type=_parse_damage_mult(
            raw.get("armor_damage_mult_by_type"),
            field_name="armor_damage_mult_by_type",
        ),
        hull_damage_mult_by_type=_parse_damage_mult(
            raw.get("hull_damage_mult_by_type"),
            field_name="hull_damage_mult_by_type",
        ),
        stateful_hullmods=frozenset(raw.get("stateful_hullmods") or []),
    )
