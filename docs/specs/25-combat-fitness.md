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

## Functions

### `combat_fitness(result, time_limit, engagement_threshold) -> float`

Single-matchup fitness from `CombatResult`.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `result` | `CombatResult` | required | Single matchup result |
| `time_limit` | `float` | `180.0` | Game-time limit (for speed bonus normalization) |
| `engagement_threshold` | `float` | `500.0` | Minimum total permanent damage to count as "engaged" |

**Engagement score** (for timeouts and loss gradient):

Computes weighted damage: `armor_dmg + hull_dmg + shield_dmg * 0.3`. Shield damage weighted at 0.3x because it regenerates (soft flux vents passively). Armor + hull damage is permanent.

If total weighted damage < `engagement_threshold`: return -0.5 (ships never meaningfully fought — build cannot close to range).

Otherwise: damage ratio `(player_dmg - enemy_dmg) / total_dmg` scaled to [-0.5, +0.5].

**Efficiency bonus** (wins only, range [0.0, 0.1]):

- Speed: `max(0, 1 - duration / time_limit) * 0.04` — faster kills
- HP preserved: `mean(hull_fraction for player ships) * 0.03` — margin of victory
- Low overloads: `max(0, 0.02 - player_overload_count * 0.005)` — flux management
- Armor preserved: `mean(armor_fraction for player ships) * 0.01` — efficient engagement

**Combined:**
- PLAYER win: `1.0 + efficiency_bonus` → [1.0, 1.1]
- ENEMY win: `-1.0 + (engagement_score + 0.5) * 0.3` → [-1.0, -0.85]
- TIMEOUT/STOPPED: `engagement_score` → [-0.5, +0.5]

### `aggregate_combat_fitness(results, mode, time_limit, engagement_threshold) -> float`

Aggregates `combat_fitness` across multiple matchup results.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results` | `list[CombatResult]` | required | Results from opponent pool |
| `mode` | `str` | `"mean"` | `"mean"` (average) or `"minimax"` (minimum) |
| `time_limit` | `float` | `180.0` | Passed to `combat_fitness` |
| `engagement_threshold` | `float` | `500.0` | Passed to `combat_fitness` |

Raises `ValueError` if `results` is empty.

## Design Rationale

**Why weight shield damage at 0.3x?** In Starsector, shield damage generates soft flux that dissipates passively. A build that only deals shield damage can never kill — the enemy just drops shields briefly and regenerates. Armor + hull damage is permanent and represents real progress. Weighting 0.3x ensures builds that penetrate armor/hull score higher than shield-only plinkers.

**Why penalize no-engagement at -0.5?** A build that survives 180s without dealing or receiving damage has failed to close to engagement range. This is strictly worse than a build that fights and loses, because the losing build at least demonstrates weapon capability. The -0.5 penalty ensures the optimizer learns to avoid passive/non-engaging builds.

**Why the 0.3 factor on loss engagement?** `(-1.0 + (engagement_score + 0.5) * 0.3)` compresses the loss range to [-1.0, -0.85]. This ensures losses with good damage exchange score higher than losses with no exchange, providing gradient among losing builds. But the compression ensures no loss ever scores above -0.85, maintaining the tier ordering.
