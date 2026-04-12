# Timeout Tuner Specification

Self-tuning combat timeout prediction using survival analysis on right-censored duration data. Sets per-matchup timeout ceilings that preserve optimizer signal quality while avoiding premature cutoffs. Defined in `src/starsector_optimizer/timeout_tuner.py`.

## Why Timeouts Matter for Optimizer Quality

Simulation verified: bad timeouts corrupt the optimizer's fitness signal more than they save in wall-clock time.

- 60s timeout: 100% timeout rate → flat fitness landscape → optimizer blind
- 120s timeout: 94% timeout rate → rank correlation 0.65 → 7% convergence penalty
- 180s timeout: 46% timeout rate → still corrupted → 2% convergence penalty
- 300s timeout: 0.3% timeout rate → clean signal → baseline

Carrier builds (speed=35) show 0% winrate at 60-120s timeout despite being strong. Short timeouts systematically bias the optimizer against slow archetypes.

## Data-Driven Cold-Start Priors (NO magic numbers)

Timeout priors are derived from `GameData` at runtime — they auto-adjust when game data changes (new version, balance patch):

```python
def compute_default_timeout(
    player_hull: ShipHull, enemy_hull: ShipHull,
    game_data: GameData, spawn_distance: float = 4000.0,
    safety_multiplier: float = 2.5,
) -> float:
    # Approach time from ship speeds
    approach = spawn_distance / (player_hull.max_speed + enemy_hull.max_speed)

    # Combat estimate from EHP and DPS
    player_ehp = player_hull.hitpoints + player_hull.armor_rating * 10
    enemy_ehp = enemy_hull.hitpoints + enemy_hull.armor_rating * 10
    median_dps = median(w.damage_per_second for w in game_data.weapons.values() if w.dps > 0)
    player_slots = len(assignable_slots(player_hull))
    enemy_slots = len(assignable_slots(enemy_hull))
    est_dps_player = player_slots * median_dps * 0.5  # 50% efficiency
    est_dps_enemy = enemy_slots * median_dps * 0.5
    combat_estimate = max(player_ehp / est_dps_enemy, enemy_ehp / est_dps_player)

    return min(approach + combat_estimate * safety_multiplier, 600.0)  # cap at 10 min
```

Verified against 0.98a data: FRGvFRG=46s, CRUvCRU=60s, CAPvCAP=111s.

## Tiered Approach

### Cold start (0-50 observations)
Use data-driven priors from GameData. No model fitting needed.

### Warm (50+ observations)
Fit `lifelines.WeibullAFTFitter` across all accumulated data:
- Duration column: actual fight duration (game-time seconds)
- Event column: 1 = fight completed (PLAYER/ENEMY win), 0 = censored (TIMEOUT)
- Features: max hull size per side (one-hot), ship count per side, median speed per side
- Prediction: `aft.predict_percentile(features, p=0.98)` → per-matchup timeout

Compare Weibull vs LogNormal AFT via AIC. Pick the better fit.

### Blended transition
```python
weight = min(1.0, n_observations / 100)
timeout = (1 - weight) * prior + weight * model_prediction
```

Smooth transition avoids abrupt jumps when switching from prior to model.

## Persistence

```
data/evaluation_log.jsonl       # Shared with Phase 7 surrogate
```

**JSONL format (one line per matchup result):**
```json
{"matchup_id": "eval_001", "player_builds": ["eagle_opt_001"],
 "enemy_variants": ["dominator_Assault"],
 "hull_sizes": ["CRUISER", "CRUISER"], "ship_counts": [1, 1],
 "winner": "PLAYER", "duration": 72.5, "completed": true,
 "heartbeat_trajectory": [[0.95, 0.98], [0.82, 0.91], ...],
 "time_limit": 180, "time_mult": 5.0}
```

- Append-only: each `record_result()` call appends one line
- Shared with Phase 7 neural surrogate (same data, no duplication)
- TimeoutTuner reads: duration, completed, hull_sizes, ship_counts
- Phase 7 reads: all fields including heartbeat_trajectory

**Model persistence:**
```
data/timeout_model/
├── model.pkl                 # Fitted lifelines AFT model
└── metadata.json             # {n_observations, last_refit, model_type, aic}
```

Refit when `n_new_observations >= refit_threshold` (default 50).

## Classes

### `TimeoutTuner`

```python
class TimeoutTuner:
    def __init__(
        self,
        data_dir: Path,                    # directory for evaluation_log.jsonl + timeout_model/
        refit_threshold: int = 50,         # refit model after this many new observations
        blend_scale: int = 100,            # n_obs at which weight reaches 1.0
        target_percentile: float = 0.98,   # survival percentile for timeout
        spawn_distance: float = 4000.0,    # for approach time calculation
        safety_multiplier: float = 2.5,    # for cold-start combat estimate
    ) -> None: ...

    def predict_timeout(self, matchup: MatchupConfig, game_data: GameData) -> float:
        """Predict optimal timeout (game-time seconds) for a matchup."""

    def record_result(
        self, matchup: MatchupConfig, result: CombatResult,
        game_data: GameData, heartbeat_trajectory: list[list[float]] | None = None,
    ) -> None:
        """Append result to JSONL log. Trigger refit if threshold reached."""

    def refit(self) -> None:
        """Refit survival model from accumulated data."""

    @staticmethod
    def compute_default_timeout(
        player_hull: ShipHull, enemy_hull: ShipHull,
        game_data: GameData, spawn_distance: float = 4000.0,
        safety_multiplier: float = 2.5,
    ) -> float:
        """Data-driven cold-start timeout from GameData. No magic numbers."""
```

## Dependencies

- `lifelines` (WeibullAFTFitter, LogNormalAFTFitter)
- `numpy` (median, percentile calculations)
