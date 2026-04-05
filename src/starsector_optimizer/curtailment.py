"""Stochastic curtailment monitor — stops combat early when outcome is clear.

Uses model-free TTD-ratio extrapolation on enriched heartbeat HP trajectories.
See spec 20 for design rationale (NOT Lanchester — model-free, simulation-verified).
"""

from __future__ import annotations

import time
from pathlib import Path

from .models import Heartbeat


def parse_heartbeat(line: str) -> Heartbeat:
    """Parse a 6-field heartbeat line."""
    parts = line.strip().split()
    if len(parts) < 6:
        raise ValueError(f"Invalid heartbeat format (expected 6 fields): {line!r}")
    return Heartbeat(
        timestamp_ms=int(parts[0]),
        elapsed=float(parts[1]),
        player_hp=float(parts[2]),
        enemy_hp=float(parts[3]),
        player_alive=int(parts[4]),
        enemy_alive=int(parts[5]),
    )


class CurtailmentMonitor:
    """Monitor mid-fight HP trajectories and decide when to stop early.

    Uses TTD-ratio extrapolation:
    1. Compute HP loss rate per side over a sliding window
    2. Estimate time-to-death (TTD) = current_hp / loss_rate
    3. Stop when TTD ratio > ttd_ratio AND faster-dying side TTD < max_ttd
    4. Never stop before min_time (protects phase ships)
    """

    def __init__(
        self,
        min_time: float = 30.0,
        ttd_ratio: float = 3.0,
        window: int = 10,
        max_ttd: float = 60.0,
    ) -> None:
        self.min_time = min_time
        self.ttd_ratio = ttd_ratio
        self.window = window
        self.max_ttd = max_ttd

    def should_stop(self, heartbeats: list[Heartbeat]) -> tuple[bool, str | None]:
        """Decide whether to stop the current matchup.

        Returns (should_stop, predicted_winner). Winner is "PLAYER" or "ENEMY".
        """
        if len(heartbeats) < self.window + 1:
            return False, None

        latest = heartbeats[-1]

        # Don't stop before min_time (protects phase ships)
        if latest.elapsed < self.min_time:
            return False, None

        # Compute HP loss rates over the window
        old = heartbeats[-(self.window + 1)]
        dt = latest.elapsed - old.elapsed
        if dt <= 0:
            return False, None

        rate_player = (old.player_hp - latest.player_hp) / dt  # positive = losing HP
        rate_enemy = (old.enemy_hp - latest.enemy_hp) / dt

        # Estimate time-to-death for each side
        eps = 0.001
        ttd_player = latest.player_hp / rate_player if rate_player > eps else float("inf")
        ttd_enemy = latest.enemy_hp / rate_enemy if rate_enemy > eps else float("inf")

        # Stop when one side dies 3x sooner AND within max_ttd
        if ttd_enemy < ttd_player and ttd_player > 0:
            ratio = ttd_player / ttd_enemy if ttd_enemy > 0 else float("inf")
            if ratio >= self.ttd_ratio and ttd_enemy < self.max_ttd:
                return True, "PLAYER"

        if ttd_player < ttd_enemy and ttd_enemy > 0:
            ratio = ttd_enemy / ttd_player if ttd_player > 0 else float("inf")
            if ratio >= self.ttd_ratio and ttd_player < self.max_ttd:
                return True, "ENEMY"

        return False, None

    @staticmethod
    def write_stop_signal(saves_common: Path) -> None:
        """Write stop signal file to instance's saves/common/."""
        stop_path = saves_common / "combat_harness_stop.data"
        stop_path.write_text(str(int(time.time() * 1000)))
