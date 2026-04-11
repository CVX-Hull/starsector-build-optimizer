# Combat Fitness Specification

Hierarchical composite fitness score using full combat telemetry. Replaces simple HP differential for optimization. Defined in `src/starsector_optimizer/combat_fitness.py`.

## Motivation

The simple HP differential (`player_hp - enemy_hp`) returns 0.000 for all timeout stalemates, giving the optimizer zero gradient. Cross-domain research (drug discovery, protein engineering, compiler autotuning) unanimously recommends continuous reward signals for surrogate-based optimization. This module provides a tiered score that creates gradient everywhere.

## Score Architecture

Three tiers with non-overlapping ranges guarantee a total ordering:

| Tier | Range | What It Captures |
|------|-------|-----------------|
| Wins | [+1.0, +1.1] | Decisive victory + efficiency bonuses |
| Timeouts (engaged) | [-0.5, +0.5] | Damage exchange quality |
| Timeouts (no engagement) | -0.5 | Active penalty — never engaged |
| Losses | [-1.0, -0.85] | Defeat + credit for damage dealt |

No timeout score exceeds any win score. No loss score exceeds the worst timeout.

## Classes

### `CombatFitnessConfig`

Frozen dataclass in `models.py`. Externalizes all tunable coefficients (no magic numbers in function bodies).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `shield_damage_weight` | `float` | `0.3` | Weight for recoverable shield damage (vs 1.0 for armor/hull) |
| `engagement_threshold` | `float` | `500.0` | Minimum total weighted damage to count as "engaged" |
| `engagement_penalty` | `float` | `-0.5` | Score when ships never meaningfully fought |
| `engagement_scale` | `float` | `0.5` | Scales damage ratio to [-scale, +scale] |
| `loss_engagement_scale` | `float` | `0.3` | Compresses engagement score into loss tier |
| `speed_bonus_weight` | `float` | `0.04` | Weight for kill speed bonus |
| `hp_bonus_weight` | `float` | `0.03` | Weight for hull HP preserved bonus |
| `overload_bonus_base` | `float` | `0.02` | Base bonus for low overloads |
| `overload_penalty_per` | `float` | `0.005` | Penalty per player overload |
| `armor_bonus_weight` | `float` | `0.01` | Weight for armor preserved bonus |
| `time_limit` | `float` | `180.0` | Game-time limit for speed bonus normalization |

## Functions

### `combat_fitness(result, config) -> float`

Single-matchup fitness from `CombatResult`.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `result` | `CombatResult` | required | Single matchup result |
| `config` | `CombatFitnessConfig` | `CombatFitnessConfig()` | All tunable coefficients |

**Engagement score** (for timeouts and loss gradient):

Computes weighted damage: `armor_dmg + hull_dmg + shield_dmg * config.shield_damage_weight`. Shield damage weighted at 0.3x because it regenerates (soft flux vents passively). Armor + hull damage is permanent.

If total weighted damage < `config.engagement_threshold`: return `config.engagement_penalty` (ships never meaningfully fought — build cannot close to range).

Otherwise: damage ratio `(player_dmg - enemy_dmg) / total_dmg` scaled to [-scale, +scale].

**Efficiency bonus** (wins only, range [0.0, 0.1]):

- Speed: `max(0, 1 - duration / config.time_limit) * config.speed_bonus_weight` — faster kills
- HP preserved: `mean(hull_fraction for player ships) * config.hp_bonus_weight` — margin of victory
- Low overloads: `max(0, config.overload_bonus_base - player_overload_count * config.overload_penalty_per)` — flux management
- Armor preserved: `mean(armor_fraction for player ships) * config.armor_bonus_weight` — efficient engagement

**Combined:**
- PLAYER win: `1.0 + efficiency_bonus` → [1.0, 1.1]
- ENEMY win: `-1.0 + (engagement_score + 0.5) * config.loss_engagement_scale` → [-1.0, -0.85]
- TIMEOUT/STOPPED: `engagement_score` → [-0.5, +0.5]

### `aggregate_combat_fitness(results, mode, config) -> float`

Aggregates `combat_fitness` across multiple matchup results.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results` | `list[CombatResult]` | required | Results from opponent pool |
| `mode` | `str` | `"mean"` | `"mean"` (average) or `"minimax"` (minimum) |
| `config` | `CombatFitnessConfig` | `CombatFitnessConfig()` | Passed to `combat_fitness` |

Raises `ValueError` if `results` is empty.

## Design Rationale

**Why weight shield damage at 0.3x?** In Starsector, shield damage generates soft flux that dissipates passively. A build that only deals shield damage can never kill — the enemy just drops shields briefly and regenerates. Armor + hull damage is permanent and represents real progress. Weighting 0.3x ensures builds that penetrate armor/hull score higher than shield-only plinkers.

**Why penalize no-engagement at -0.5?** A build that survives 180s without dealing or receiving damage has failed to close to engagement range. This is strictly worse than a build that fights and loses, because the losing build at least demonstrates weapon capability. The -0.5 penalty ensures the optimizer learns to avoid passive/non-engaging builds.

**Why the 0.3 factor on loss engagement?** `(-1.0 + (engagement_score + 0.5) * 0.3)` compresses the loss range to [-1.0, -0.85]. This ensures losses with good damage exchange score higher than losses with no exchange, providing gradient among losing builds. But the compression ensures no loss ever scores above -0.85, maintaining the tier ordering.
