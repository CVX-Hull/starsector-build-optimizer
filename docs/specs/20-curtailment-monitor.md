# Curtailment Monitor Specification

Monitors mid-fight HP trajectories via enriched heartbeat data and signals the combat harness to stop a matchup early when the outcome is effectively determined. Defined in `src/starsector_optimizer/curtailment.py`.

## Enriched Heartbeat Format

The Java combat harness writes heartbeat data to `saves/common/combat_harness_heartbeat.txt.data` every ~60 frames (~1 second at 60fps).

**New format (6 fields, space-separated):**
```
<timestamp_ms> <elapsed_seconds> <player_hp_fraction> <enemy_hp_fraction> <player_alive> <enemy_alive>
```

- `timestamp_ms`: wall-clock milliseconds (System.currentTimeMillis)
- `elapsed_seconds`: game-time elapsed since matchup start
- `player_hp_fraction`: aggregate HP fraction across all player ships (0.0-1.0)
- `enemy_hp_fraction`: aggregate HP fraction across all enemy ships (0.0-1.0)
- `player_alive`: number of alive non-fighter player ships
- `enemy_alive`: number of alive non-fighter enemy ships

**Legacy format (2 fields, backward compatible):**
```
<timestamp_ms> <elapsed_seconds>
```

The parser handles both formats gracefully. Legacy heartbeats have HP fractions set to `None`.

## Stop Signal Protocol

**File:** `saves/common/combat_harness_stop.data`

- **Written by:** Python (CurtailmentMonitor) via direct file write to instance's `saves/common/`
- **Read by:** Java (CombatHarnessPlugin) via `Global.getSettings().fileExistsInCommon("combat_harness_stop")`
- **Content:** timestamp string (for debugging)
- **Lifecycle:** Java checks once per frame. When detected, Java deletes the file and ends the current matchup with winner="STOPPED".

## TTD-Ratio Extrapolation Algorithm

**Why not Lanchester:** Lanchester's laws (1916) assume homogeneous forces, constant attrition, no shields/armor/phase mechanics. Fails empirical validation even in real warfare. Starsector violates every assumption.

**Our approach is model-free.** At each heartbeat:

1. **Read** current HP fractions for each side
2. **Compute** HP loss rate over a sliding window of `w` heartbeats:
   ```
   rate_player = (hp_player[t-w] - hp_player[t]) / (elapsed[t] - elapsed[t-w])
   rate_enemy  = (hp_enemy[t-w]  - hp_enemy[t])  / (elapsed[t] - elapsed[t-w])
   ```
3. **Estimate** time-to-death for each side:
   ```
   ttd_player = hp_player[t] / rate_player  (if rate_player > 0, else infinity)
   ttd_enemy  = hp_enemy[t]  / rate_enemy   (if rate_enemy > 0, else infinity)
   ```
4. **Stop** when:
   - `min(ttd_player, ttd_enemy) / max(ttd_player, ttd_enemy) > ttd_ratio` (default 3.0)
   - AND the faster-dying side has `TTD < 60s` (game-time)
   - AND `elapsed[t] > min_time` (default 30s game-time, protects phase ships)

**Predicted winner:** the side with the higher TTD.

## Simulation Verification

- 0% false positives across 500 simulated phase-ship fights (min_time=30s)
- 12-24% time savings on decisive and close fights
- No effect on stomps (<10s fights) — those end before curtailment can trigger

## Classes

### `Heartbeat`

Frozen dataclass parsed from heartbeat file content.

| Field | Type | Notes |
|-------|------|-------|
| `timestamp_ms` | `int` | Wall-clock time |
| `elapsed` | `float` | Game-time elapsed |
| `player_hp` | `float \| None` | None for legacy 2-field format |
| `enemy_hp` | `float \| None` | None for legacy 2-field format |
| `player_alive` | `int \| None` | None for legacy format |
| `enemy_alive` | `int \| None` | None for legacy format |

### `CurtailmentMonitor`

```python
class CurtailmentMonitor:
    def __init__(
        self,
        min_time: float = 30.0,      # game-time seconds before curtailment allowed
        ttd_ratio: float = 3.0,      # TTD ratio threshold for stopping
        window: int = 10,            # heartbeat window for rate estimation
        max_ttd: float = 60.0,       # faster-dying side must die within this (game-time)
    ) -> None: ...

    def should_stop(self, heartbeats: list[Heartbeat]) -> tuple[bool, str | None]:
        """Returns (should_stop, predicted_winner). Winner is 'PLAYER' or 'ENEMY'."""

    @staticmethod
    def write_stop_signal(saves_common: Path) -> None:
        """Write stop signal file to instance's saves/common/."""
```

## Future: Empirical Probability Calibration

After accumulating 500+ completed fight results with heartbeat trajectories, the ad-hoc TTD ratio threshold can be upgraded to a statistically calibrated stopping rule:

1. At each `(time_fraction, ttd_ratio)` pair from historical fights, record the actual outcome
2. Build a lookup table: `P(winner=A | ttd_ratio=R, time_fraction=T)`
3. Stop when `P(outcome unchanged) > 0.95`

This is a Phase 2 improvement — the initial TTD ratio heuristic is sufficient for Phase 3.5.
