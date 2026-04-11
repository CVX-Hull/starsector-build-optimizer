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

The parser requires all 6 fields. Invalid formats raise `ValueError`.

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
| `player_hp` | `float` | Aggregate HP fraction (0.0-1.0) |
| `enemy_hp` | `float` | Aggregate HP fraction (0.0-1.0) |
| `player_alive` | `int` | Alive non-fighter ship count |
| `enemy_alive` | `int` | Alive non-fighter ship count |

### `CurtailmentMonitor`

```python
class CurtailmentMonitor:
    def __init__(
        self,
        min_time: float = 30.0,              # game-time seconds before TTD curtailment allowed
        ttd_ratio: float = 3.0,              # TTD ratio threshold for stopping
        window: int = 10,                    # heartbeat window for rate estimation
        max_ttd: float = 60.0,               # faster-dying side must die within this (game-time)
        stalemate_min_time: float = 60.0,    # game-time seconds before stalemate check activates
        stalemate_threshold: float = 0.01,   # max HP fraction loss rate per second per side
    ) -> None: ...

    def should_stop(self, heartbeats: list[Heartbeat]) -> tuple[bool, str | None]:
        """Returns (should_stop, predicted_winner).

        Winner is 'PLAYER', 'ENEMY', or None (stalemate — no predicted winner).
        The predicted_winner is for logging/curtailment only — the Java harness
        independently determines the actual CombatResult.winner ('PLAYER', 'ENEMY',
        'TIMEOUT', or 'STOPPED'). A None winner from stalemate detection means
        'no predicted winner' and the Java side will record 'STOPPED'.
        """

    @staticmethod
    def write_stop_signal(saves_common: Path) -> None:
        """Write stop signal file to instance's saves/common/."""
```

## Stalemate Detection

The TTD-ratio algorithm handles asymmetric fights (one side dying much faster). It misses symmetric stalemates where both sides have near-zero HP loss rates — e.g., shield/flux equilibrium where neither ship can overload the other, creating infinite disengage/vent/reengage cycles.

**Algorithm:** Using the same sliding window rates computed by the TTD-ratio check:

1. If `elapsed < stalemate_min_time`: skip (allow fights time to develop)
2. If BOTH `rate_player < stalemate_threshold` AND `rate_enemy < stalemate_threshold`: return `(True, None)`
3. The `None` winner signals a stalemate with no predicted winner

The stalemate check runs AFTER the TTD-ratio check fails. When both rates are below `eps` (0.001), both TTDs are infinity, so the TTD-ratio check never triggers — the two checks are complementary with no conflict.

**Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `stalemate_min_time` | `60.0` | Game-time seconds before stalemate detection activates. Protects fights that start slow but develop (e.g., long approach times). |
| `stalemate_threshold` | `0.01` | Max HP fraction loss rate per second per side. Below this, the side is considered "not taking meaningful damage." 0.01 = less than 1% of total HP per second. |

**Benchmark justification:** Replaying the 203-trial Eagle evaluation log (`experiments/eagle_200/timeout_strategy_benchmark.ipynb`), stalemate detection at 60s with threshold 0.01 saves **41.9% of combat time** while preserving **rho=0.987 Spearman rank correlation**, **10/10 top-10 overlap**, with only **2 decisive fights lost**.
