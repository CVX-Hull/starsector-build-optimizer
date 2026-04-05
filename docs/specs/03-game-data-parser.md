# Game Data Parser Specification

Parses Starsector game data files into model objects. Defined in `src/starsector_optimizer/parser.py`.

## Data Sources

| File | Format | Key Data |
|---|---|---|
| `data/hulls/ship_data.csv` | CSV (#=comment) | Ship stats (56 columns) |
| `data/hulls/*.ship` | Loose JSON | Weapon slots, builtInMods/Weapons, hullSize |
| `data/weapons/weapon_data.csv` | CSV (#=comment) | Weapon stats (49 columns) |
| `data/weapons/*.wpn` | Loose JSON | Weapon type (BALLISTIC/ENERGY/MISSILE), size |
| `data/hullmods/hull_mods.csv` | CSV | Hullmod definitions (20 columns) |

## Critical Discovery

The weapon_data.csv `type` column contains **damage type** (KINETIC, HIGH_EXPLOSIVE, ENERGY, FRAGMENTATION), NOT weapon type. The weapon type (BALLISTIC, ENERGY, MISSILE) — which determines slot compatibility — comes from `.wpn` files.

## Functions

### parse_loose_json(text: str) -> dict
Strip `#` and `//` comments via regex, fix trailing commas, then `json.loads()`. Works for `.ship` and `.variant` files (verified: 203 + 91 all parse).

### extract_wpn_metadata(path: Path) -> dict
Regex extraction from `.wpn` files (full JSON parse fails on 95/156 due to unquoted identifiers). Extracts `id`, `type`, `size` via pattern `"field"\s*:\s*"(value)"`. Verified: 156/156 success.

### parse_ship_csv(csv_path: Path) -> list[ShipHull]
Read via pandas `read_csv(comment='#')`. Column mapping:

| CSV Column | Model Field | Notes |
|---|---|---|
| `name` | name | |
| `id` | id | |
| `designation` | hull_size (via HullSize.from_str) | "Capital Ship" → CAPITAL_SHIP |
| `tech/manufacturer` | tech_manufacturer | |
| `system id` | system_id | |
| `fleet pts` | fleet_pts | |
| `hitpoints` | hitpoints | |
| `armor rating` | armor_rating | |
| `max flux` | max_flux | |
| `flux dissipation` | flux_dissipation | Column 11, NOT `8/6/5/4%` |
| `ordnance points` | ordnance_points | |
| `fighter bays` | fighter_bays | |
| `max speed` | max_speed | |
| `shield type` | shield_type (via ShieldType.from_str) | |
| `shield arc` | shield_arc | |
| `shield upkeep` | shield_upkeep | |
| `shield efficiency` | shield_efficiency | |
| `phase cost` | phase_cost | |
| `phase upkeep` | phase_upkeep | |
| `peak CR sec` | peak_cr_sec | |
| `CR loss/sec` | cr_loss_per_sec | |
| `hints` | hints (split comma, strip) | |
| `tags` | tags (split comma, strip) | |

Skip rows where `HullSize.from_str(designation)` returns None. weapon_slots, built_in_mods, built_in_weapons start empty (filled by merge).

### parse_ship_file(path: Path) -> dict
Parse `.ship` loose JSON. Return dict with keys: `hullId`, `hullSize`, `weaponSlots`, `builtInMods`, `builtInWeapons`.

### merge_ship_hull_data(hulls: list[ShipHull], ship_dir: Path) -> list[ShipHull]
For each hull, find `{ship_dir}/{hull.id}.ship`. If found, populate weapon_slots, built_in_mods, built_in_weapons, and override hull_size from .ship file's `hullSize` (authoritative source). Hulls without .ship files keep empty weapon_slots.

### parse_weapon_csv(csv_path: Path, wpn_dir: Path) -> list[Weapon]
Read CSV via pandas. Also parse .wpn files from wpn_dir for weapon type and size.

| CSV Column | Model Field | Notes |
|---|---|---|
| `name` | name | |
| `id` | id | |
| `type` | damage_type (via DamageType.from_str) | THIS IS DAMAGE TYPE, not weapon type! |
| `range` | range | |
| `damage/shot` | damage_per_shot | |
| `damage/second` | damage_per_second | Beams only |
| `emp` | emp | |
| `OPs` | op_cost | |
| `energy/shot` | flux_per_shot | |
| `energy/second` | flux_per_second | Beams only |
| `chargeup` | chargeup | |
| `chargedown` | chargedown | |
| `burst size` | burst_size | |
| `burst delay` | burst_delay | |
| `ammo` | ammo | |
| `ammo/sec` | ammo_per_sec | |
| `proj speed` | proj_speed | |
| `turn rate` | turn_rate | |
| `hints` | hints (split comma, strip) | |
| `tags` | tags (split comma, strip) | |

Weapon type and size come from .wpn files. For weapons without .wpn files, infer weapon_type: KINETIC/HIGH_EXPLOSIVE/FRAGMENTATION → BALLISTIC, ENERGY → ENERGY. Skip rows where damage_type is None.

### parse_hullmod_csv(csv_path: Path) -> list[HullMod]

| CSV Column | Model Field | Notes |
|---|---|---|
| `name` | name | |
| `id` | id | |
| `tier` | tier | |
| `tags` | tags (split comma, strip) | |
| `uiTags` | ui_tags (split comma, strip) | |
| `cost_frigate` | cost_frigate | |
| `cost_dest` | cost_destroyer | NOTE: column name mismatch |
| `cost_cruiser` | cost_cruiser | |
| `cost_capital` | cost_capital | |
| `hidden` | is_hidden | Combined: hidden OR hiddenEverywhere |
| `script` | script | |

### load_game_data(game_dir: Path, mod_dirs: list[Path] | None = None) -> GameData
1. Parse ship CSV from `{game_dir}/data/hulls/ship_data.csv`
2. Merge with .ship files from `{game_dir}/data/hulls/`
3. Parse weapon CSV + .wpn files from `{game_dir}/data/weapons/`
4. Parse hullmod CSV from `{game_dir}/data/hullmods/hull_mods.csv`
5. If mod_dirs: merge mod data (same structure, mod entries override vanilla)
6. Run `validate_registry(game_data)` and log warnings
7. Return GameData

## Edge Cases
- Empty CSV fields → default 0 for numeric, "" for string
- Ships without .ship files → keep with empty weapon_slots
- Weapons without .wpn files → infer weapon_type from damage_type
- `#`-prefixed CSV rows → skipped by pandas comment parameter
- Quotes in tag fields → strip surrounding quotes
