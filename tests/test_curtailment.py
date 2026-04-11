"""Tests for curtailment monitor — heartbeat parsing, TTD-ratio stopping, and stalemate detection."""

from pathlib import Path

import pytest

from starsector_optimizer.models import Heartbeat
from starsector_optimizer.curtailment import (
    CurtailmentMonitor,
    parse_heartbeat,
)


# --- Heartbeat parsing tests ---


class TestParseHeartbeat:

    def test_parse_6_field(self):
        hb = parse_heartbeat("1712345678000 45.5 0.85 0.42 2 1")
        assert hb.timestamp_ms == 1712345678000
        assert hb.elapsed == pytest.approx(45.5)
        assert hb.player_hp == pytest.approx(0.85)
        assert hb.enemy_hp == pytest.approx(0.42)
        assert hb.player_alive == 2
        assert hb.enemy_alive == 1

    def test_parse_invalid_format_raises(self):
        with pytest.raises(ValueError, match="expected 6 fields"):
            parse_heartbeat("1712345678000 10.0")

    def test_read_heartbeat_file(self, tmp_path):
        path = tmp_path / "combat_harness_heartbeat.txt.data"
        path.write_text("1712345678000 30.0 0.75 0.60 1 1")
        hb = parse_heartbeat(path.read_text().strip())
        assert hb.player_hp == pytest.approx(0.75)


# --- Curtailment decision tests ---


def _make_heartbeats(hp_pairs: list[tuple[float, float]], start_elapsed: float = 0.0,
                     interval: float = 1.0) -> list[Heartbeat]:
    """Create heartbeats from (player_hp, enemy_hp) pairs."""
    return [
        Heartbeat(
            timestamp_ms=1712345678000 + int(i * interval * 1000),
            elapsed=start_elapsed + i * interval,
            player_hp=php,
            enemy_hp=ehp,
            player_alive=1 if php > 0 else 0,
            enemy_alive=1 if ehp > 0 else 0,
        )
        for i, (php, ehp) in enumerate(hp_pairs)
    ]


class TestCurtailmentDecisions:

    def test_no_stop_before_min_time(self):
        """Never stop before min_time even if outcome looks clear."""
        monitor = CurtailmentMonitor(min_time=30.0)
        # One-sided fight at t=20 (before min_time=30)
        hbs = _make_heartbeats(
            [(1.0, 1.0)] * 5 + [(0.95, 0.5)] * 5 + [(0.90, 0.2)] * 5,
            start_elapsed=5.0,  # all before 30s
        )
        stop, winner = monitor.should_stop(hbs)
        assert not stop

    def test_no_stop_even_fight(self):
        """Both sides losing HP at similar rate → don't stop."""
        monitor = CurtailmentMonitor(min_time=0.0)  # disable min_time for test
        hbs = _make_heartbeats(
            [(1.0 - i * 0.02, 1.0 - i * 0.025) for i in range(20)],
            start_elapsed=30.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert not stop

    def test_stop_when_ttd_ratio_extreme(self):
        """One side dying 3x faster → stop."""
        monitor = CurtailmentMonitor(min_time=0.0, window=5)
        # Player losing HP slowly, enemy losing fast
        hbs = _make_heartbeats(
            [(1.0 - i * 0.005, 0.5 - i * 0.03) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner == "PLAYER"

    def test_stop_requires_trend_window(self):
        """Need enough heartbeats for rate estimation."""
        monitor = CurtailmentMonitor(min_time=0.0, window=10)
        # Only 3 heartbeats — too few for window of 10
        hbs = _make_heartbeats(
            [(0.9, 0.1), (0.88, 0.05), (0.86, 0.02)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert not stop

    def test_no_stop_when_close(self):
        """TTD ratio ~1.5:1 → don't stop (could go either way)."""
        monitor = CurtailmentMonitor(min_time=0.0, window=5)
        # Both losing HP, enemy slightly faster
        hbs = _make_heartbeats(
            [(1.0 - i * 0.02, 1.0 - i * 0.03) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert not stop

    def test_min_time_configurable(self):
        """Higher min_time protects phase ships."""
        # Same one-sided fight, but at t=40
        hbs = _make_heartbeats(
            [(1.0 - i * 0.005, 0.5 - i * 0.03) for i in range(15)],
            start_elapsed=40.0,
        )
        # min_time=30 → allowed
        m30 = CurtailmentMonitor(min_time=30.0, window=5)
        stop30, _ = m30.should_stop(hbs)
        assert stop30

        # min_time=60 → not allowed (elapsed < 60)
        m60 = CurtailmentMonitor(min_time=60.0, window=5)
        stop60, _ = m60.should_stop(hbs)
        assert not stop60

    def test_enemy_winning(self):
        """Correctly identifies enemy as winner when player dying faster."""
        monitor = CurtailmentMonitor(min_time=0.0, window=5)
        hbs = _make_heartbeats(
            [(0.5 - i * 0.03, 1.0 - i * 0.005) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner == "ENEMY"



# --- Stalemate detection tests ---


class TestStalemateDetection:

    def test_stalemate_detected_when_both_sides_flat(self):
        """Both sides losing < 0.01 HP/s over window, elapsed > 60s → stalemate."""
        monitor = CurtailmentMonitor(stalemate_min_time=60.0, stalemate_threshold=0.01)
        # Both sides lose 0.001/s — well below 0.01 threshold
        hbs = _make_heartbeats(
            [(0.95 - i * 0.001, 0.90 - i * 0.001) for i in range(15)],
            start_elapsed=55.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner is None

    def test_no_stalemate_before_min_time(self):
        """Same flat rates but elapsed < 60s → no stalemate."""
        monitor = CurtailmentMonitor(stalemate_min_time=60.0, stalemate_threshold=0.01)
        hbs = _make_heartbeats(
            [(0.95 - i * 0.001, 0.90 - i * 0.001) for i in range(15)],
            start_elapsed=40.0,  # all before 60s
        )
        stop, winner = monitor.should_stop(hbs)
        assert not stop

    def test_no_stalemate_when_one_side_losing(self):
        """Player losing HP significantly → not a stalemate."""
        monitor = CurtailmentMonitor(
            min_time=0.0, stalemate_min_time=0.0, stalemate_threshold=0.01,
        )
        # Player losing 0.03/s (above threshold), enemy losing 0.001/s
        hbs = _make_heartbeats(
            [(0.95 - i * 0.03, 0.90 - i * 0.001) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        # Either TTD-ratio triggers (with a winner) or no stop at all
        if stop:
            assert winner is not None  # TTD-ratio, not stalemate

    def test_stalemate_min_time_configurable(self):
        """Custom stalemate_min_time=90 → no trigger at 70s, trigger at 95s."""
        flat_pairs = [(0.95 - i * 0.001, 0.90 - i * 0.001) for i in range(15)]

        m90 = CurtailmentMonitor(stalemate_min_time=90.0, stalemate_threshold=0.01)

        # At elapsed=70 → should not trigger
        hbs_early = _make_heartbeats(flat_pairs, start_elapsed=60.0)
        stop_early, _ = m90.should_stop(hbs_early)
        assert not stop_early

        # At elapsed=95 → should trigger
        hbs_late = _make_heartbeats(flat_pairs, start_elapsed=90.0)
        stop_late, winner_late = m90.should_stop(hbs_late)
        assert stop_late
        assert winner_late is None

    def test_stalemate_threshold_configurable(self):
        """Tighter threshold=0.005 catches slower stalemates."""
        monitor = CurtailmentMonitor(
            stalemate_min_time=0.0, stalemate_threshold=0.005,
        )
        # Rate of 0.003/s — below 0.005 threshold
        hbs = _make_heartbeats(
            [(0.95 - i * 0.003, 0.90 - i * 0.003) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner is None

    def test_stalemate_returns_none_winner(self):
        """Stalemate winner is None, not 'PLAYER' or 'ENEMY'."""
        monitor = CurtailmentMonitor(
            stalemate_min_time=0.0, stalemate_threshold=0.01,
        )
        hbs = _make_heartbeats(
            [(0.95, 0.90)] * 15,  # perfectly flat
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner is None

    def test_ttd_ratio_takes_priority(self):
        """One side dying 3x faster → TTD-ratio fires first with a winner."""
        monitor = CurtailmentMonitor(
            min_time=0.0, window=5,
            stalemate_min_time=0.0, stalemate_threshold=0.01,
        )
        hbs = _make_heartbeats(
            [(1.0 - i * 0.005, 0.5 - i * 0.03) for i in range(15)],
            start_elapsed=35.0,
        )
        stop, winner = monitor.should_stop(hbs)
        assert stop
        assert winner is not None  # TTD-ratio gives PLAYER, not None


# --- Stop signal tests ---


class TestStopSignal:

    def test_write_stop_signal(self, tmp_path):
        saves_common = tmp_path / "saves" / "common"
        saves_common.mkdir(parents=True)
        CurtailmentMonitor.write_stop_signal(saves_common)
        stop_file = saves_common / "combat_harness_stop.data"
        assert stop_file.exists()

    def test_stop_signal_content(self, tmp_path):
        saves_common = tmp_path / "saves" / "common"
        saves_common.mkdir(parents=True)
        CurtailmentMonitor.write_stop_signal(saves_common)
        content = (saves_common / "combat_harness_stop.data").read_text()
        assert len(content) > 0  # contains timestamp
