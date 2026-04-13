# Combat Fitness Specification

Hierarchical composite fitness score using hull-fraction-based game ground truth. Replaces damage-ratio heuristics with two metrics derived directly from the game's win/loss conditions. Defined in `src/starsector_optimizer/combat_fitness.py`.

## Motivation

The simple HP differential (`player_hp - enemy_hp`) returns 0.000 for all timeout stalemates, giving the optimizer zero gradient. Cross-domain research (drug discovery, protein engineering, compiler autotuning) unanimously recommends continuous reward signals for surrogate-based optimization.

### Design Philosophy (The Bitter Lesson Applied)

The opponent pool IS the computation that creates gradient. A build that "barely wins" against a weak opponent will lose against a strong one — the aggregate score across the pool captures this naturally. We don't need hand-crafted sub-scores (speed bonus, flux management, armor preservation) to differentiate wins — the opponents do that.

Two game ground-truth metrics provide all the signal needed:
- **Kill progress**: how close to the game's win condition (enemy fleet destruction)
- **Survival**: how far from the game's loss condition (player fleet destruction)

Everything else (combat speed, flux efficiency, armor quality) correlates with these metrics and is redundant with opponent-pool discrimination.

### Literature Backing

- **LTD2 (StarCraft)**: `sum(sqrt(hp) * dps)` for surviving units — continuous scoring that captures partial kills
- **Ng et al. 1999 (ICML)**: Potential-based reward shaping preserves optimal policy. Tier structure is safe; sub-components within tiers are the risk.
- **NeurIPS 2022 (Unpacking Reward Shaping)**: Dense shaping yields order-of-magnitude sample efficiency gains. Critical at 500–2000 eval budget where pure binary win/loss is catastrophically uninformative for TPE surrogate modeling.
- **FiveThirtyEight Elo**: Margin of victory with log compression prevents blowout domination while preserving gradient.
- **CoastRunners (OpenAI)**: Independently-maximizable sub-components get exploited. Fewer components = less attack surface for reward hacking.

## Score Architecture

Four tiers with non-overlapping ranges guarantee a total ordering:

| Tier | Range | What It Captures |
|------|-------|-----------------|
| Wins | [+1.0, +1.5] | Decisive victory + survival margin |
| Timeouts (engaged) | [-0.49, +0.49] | Kill progress vs death progress (margin) |
| Losses | [-1.0, -0.5] | Defeat + credit for kill progress |
| No engagement | -2.0 | Floor — ships never meaningfully fought |

No timeout score exceeds any win score. No loss score exceeds the worst timeout. No-engagement is strictly below all losses.

## Classes

### `CombatFitnessConfig`

Frozen dataclass in `models.py`. Externalizes all tunable coefficients (no magic numbers in function bodies).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `win_base` | `float` | `1.0` | Tier anchor for wins |
| `loss_base` | `float` | `-1.0` | Tier anchor for losses |
| `win_bonus_scale` | `float` | `0.5` | Scales survival into win bonus band [0, 0.5] |
| `loss_bonus_scale` | `float` | `0.5` | Scales kill progress into loss credit band [0, 0.5] |
| `timeout_scale` | `float` | `0.49` | Scales margin into timeout band [-0.49, +0.49] |
| `no_engagement_score` | `float` | `-2.0` | Floor score for non-engaging builds |
| `engagement_threshold` | `float` | `500.0` | Minimum total raw damage (all types, all ships) to count as "engaged" |

**Tier ordering invariant**: `win_base > timeout_scale > -(loss_base + loss_bonus_scale) > no_engagement_score`.

## Functions

### `combat_fitness(result, config) -> float`

Single-matchup fitness from `CombatResult`.

**Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `result` | `CombatResult` | required | Single matchup result |
| `config` | `CombatFitnessConfig` | `CombatFitnessConfig()` | All tunable coefficients |

**Kill progress** (enemy fleet destruction):
```
kill_progress = mean(1 - hull_fraction for each enemy ship)  # [0, 1]
```
For PLAYER wins, all enemies are destroyed so kill_progress = 1.0 always.

**Survival** (player fleet preservation):
```
survival = mean(hull_fraction for each player ship)  # [0, 1]
```
For ENEMY wins, all player ships are destroyed so survival = 0.0 always.

**Margin** (net combat progress, for timeouts):
```
margin = kill_progress - (1 - survival)  # [-1, +1]
```
Positive = player winning the attrition war. Negative = losing. Zero = even exchange.

**No-engagement detection**: Sums all raw damage dealt (shield + armor + hull) across all ships on both sides. If total < `config.engagement_threshold`, the fight is scored at `config.no_engagement_score` (-2.0). Uses unweighted damage: with hull-fraction-based scoring, shield-only exchanges should count as engagement — the margin calculation handles scoring them appropriately.

**Combined:**
- PLAYER win: `config.win_base + survival * config.win_bonus_scale` → [1.0, 1.5]
- ENEMY win: `config.loss_base + kill_progress * config.loss_bonus_scale` → [-1.0, -0.5]
- TIMEOUT (engaged): `margin * config.timeout_scale` → [-0.49, +0.49]
- TIMEOUT (no engagement): `config.no_engagement_score` → -2.0

### `aggregate_combat_fitness(results, mode, config) -> float`

Standalone utility for simple aggregation of `combat_fitness` scores. The optimizer uses TWFE decomposition (spec 28) for fitness aggregation instead of this function.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `results` | `list[CombatResult]` | required | Results from opponent pool |
| `mode` | `str` | `"mean"` | `"mean"` (average) or `"minimax"` (minimum) |
| `config` | `CombatFitnessConfig` | `CombatFitnessConfig()` | Passed to `combat_fitness` |

Raises `ValueError` if `results` is empty.

## Design Rationale

**Why hull-fraction over damage-ratio?** Hull fraction is the game's ground truth — it directly measures proximity to the win/loss condition. Shield damage that doesn't penetrate armor produces zero hull fraction change, so shield-only plinkers are penalized implicitly without needing an explicit shield damage weight. Kill progress naturally credits partial kills: destroying 2/3 enemy ships yields kill_progress ≈ 0.67 regardless of how damage was distributed.

**Why no efficiency sub-components?** The original design had 4 hand-tuned sub-components (speed, HP, armor, overloads) that together contributed a 0.1 range win bonus. Per the bitter lesson, the opponent pool provides the discrimination: a build that wins fast and cleanly also beats harder opponents that a slow build draws against. The aggregate score across 10 opponents captures win quality without hand-crafted proxies. Fewer sub-components also means less surface area for reward hacking (CoastRunners problem).

**Why is no-engagement the floor at -2.0?** A build that survives 300s without dealing or receiving meaningful damage has failed to engage. Under the old design, this scored -0.5 (mid-timeout), which was ABOVE all losses — creating a perverse incentive where "never fighting" outscored "fighting and losing." A losing build demonstrates weapon capability; a non-engaging build demonstrates nothing. The -2.0 floor ensures non-engagement is strictly worse than everything, including the worst loss (-1.0). The `failure_score` in `OptimizerConfig` is also set to -2.0 to prevent infrastructure crashes from scoring above non-engagement.

**Why 7 config fields instead of 11?** Each field has exactly one role: tier anchors (`win_base`, `loss_base`), band widths (`win_bonus_scale`, `loss_bonus_scale`, `timeout_scale`), floor (`no_engagement_score`), and engagement detection (`engagement_threshold`). No redundant or correlated parameters. All numeric thresholds are in config per the "no magic numbers" invariant.
