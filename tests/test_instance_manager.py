"""Tests for instance manager — work dir creation, process management, health monitoring.

All tests use tmp_path and mocked subprocess (no real game needed).
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from starsector_optimizer.models import BuildSpec, MatchupConfig
from starsector_optimizer.instance_manager import (
    GameInstance,
    InstanceConfig,
    InstanceError,
    InstancePool,
    InstanceState,
    PROTOCOL_FILES,
)


# --- Fixtures ---


@pytest.fixture
def fake_game_dir(tmp_path):
    """Create a minimal fake game directory for testing work dir creation."""
    game = tmp_path / "game"
    game.mkdir()

    # Top-level files that get symlinked
    (game / "starsector.sh").write_text("#!/bin/sh\necho fake")
    (game / "compiler_directives.txt").write_text("{}")
    (game / "starfarer.api.jar").write_bytes(b"fake")
    (game / "json.jar").write_bytes(b"fake")

    # Directories that get symlinked
    (game / "jre_linux").mkdir()
    (game / "jre_linux" / "bin").mkdir()
    (game / "native").mkdir()
    (game / "native" / "linux").mkdir()
    (game / "graphics").mkdir()
    (game / "sounds").mkdir()

    # data/ with subdirs
    (game / "data").mkdir()
    (game / "data" / "config").mkdir()
    (game / "data" / "config" / "settings.json").write_text('{"resolutionOverride":"1920x1080"}')
    (game / "data" / "hulls").mkdir()
    (game / "data" / "weapons").mkdir()
    (game / "data" / "hullmods").mkdir()
    (game / "data" / "variants").mkdir()
    # Stock variant files
    (game / "data" / "variants" / "eagle_Assault.variant").write_text("{}")
    (game / "data" / "variants" / "dominator_Assault.variant").write_text("{}")

    # mods/
    (game / "mods").mkdir()
    (game / "mods" / "enabled_mods.json").write_text('{"enabledMods":["combat_harness"]}')
    mod_dir = game / "mods" / "combat-harness"
    mod_dir.mkdir()
    (mod_dir / "mod_info.json").write_text('{"id":"combat_harness"}')
    (mod_dir / "jars").mkdir()
    (mod_dir / "jars" / "combat-harness.jar").write_bytes(b"fake")

    # saves/common/ (should NOT be symlinked)
    (game / "saves").mkdir()
    (game / "saves" / "common").mkdir()

    return game


@pytest.fixture
def config(fake_game_dir, tmp_path):
    return InstanceConfig(
        game_dir=fake_game_dir,
        instance_root=tmp_path / "instances",
        num_instances=2,
        xvfb_base_display=200,
    )


@pytest.fixture
def pool(config):
    return InstancePool(config)


# --- Work Directory Tests ---


class TestWorkDirCreation:

    def test_work_dir_structure(self, pool, config):
        """Verify symlinks + real dirs created correctly."""
        pool.setup()

        for i in range(config.num_instances):
            wd = config.instance_root / f"instance_{i:03d}"
            assert wd.exists()

            # Top-level files are symlinked
            assert (wd / "starsector.sh").is_symlink()
            assert (wd / "starfarer.api.jar").is_symlink()

            # Directories are symlinked
            assert (wd / "jre_linux").is_symlink()
            assert (wd / "native").is_symlink()
            assert (wd / "graphics").is_symlink()
            assert (wd / "sounds").is_symlink()

            # Real directories
            assert (wd / "saves" / "common").is_dir()
            assert not (wd / "saves").is_symlink()
            assert (wd / "screenshots").is_dir()
            assert (wd / "mods").is_dir()
            assert not (wd / "mods").is_symlink()

    def test_work_dir_data_structure(self, pool, config):
        """data/ is real with symlinked subdirs except config/ and variants/."""
        pool.setup()
        wd = config.instance_root / "instance_000"

        assert (wd / "data").is_dir()
        assert not (wd / "data").is_symlink()

        # config/ is copied (real), not symlinked
        assert (wd / "data" / "config").is_dir()
        assert not (wd / "data" / "config").is_symlink()
        assert (wd / "data" / "config" / "settings.json").exists()

        # variants/ is real
        assert (wd / "data" / "variants").is_dir()
        assert not (wd / "data" / "variants").is_symlink()

        # Other data subdirs are symlinked
        assert (wd / "data" / "hulls").is_symlink()
        assert (wd / "data" / "weapons").is_symlink()

    def test_work_dir_variants_symlinked(self, pool, config):
        """Stock .variant files are symlinked individually."""
        pool.setup()
        wd = config.instance_root / "instance_000"
        variants = wd / "data" / "variants"

        assert (variants / "eagle_Assault.variant").is_symlink()
        assert (variants / "dominator_Assault.variant").is_symlink()
        # Symlinks resolve to real files
        assert (variants / "eagle_Assault.variant").read_text() == "{}"

    def test_work_dir_mod_copied(self, pool, config):
        """combat-harness dir is copied, enabled_mods.json present."""
        pool.setup()
        wd = config.instance_root / "instance_000"

        assert (wd / "mods" / "combat-harness" / "mod_info.json").exists()
        assert not (wd / "mods" / "combat-harness").is_symlink()
        assert (wd / "mods" / "enabled_mods.json").exists()

        data = json.loads((wd / "mods" / "enabled_mods.json").read_text())
        assert "combat_harness" in data["enabledMods"]


# --- Queue/File Management Tests ---


class TestFileManagement:

    def test_clean_protocol_files(self, pool, config):
        """All protocol + signal files are removed before new batch."""
        pool.setup()
        inst = pool._instances[0]
        common = inst.saves_common

        # Create all 6 protocol files (including new signal files)
        for name in PROTOCOL_FILES:
            (common / name).write_text("old")

        pool._clean_protocol_files(inst)

        for name in PROTOCOL_FILES:
            assert not (common / name).exists()

    def test_write_queue(self, pool, config):
        """MatchupConfig list serializes to correct JSON at saves/common/."""
        pool.setup()
        inst = pool._instances[0]
        matchups = [
            MatchupConfig(
                matchup_id="test_001",
                player_builds=(BuildSpec(variant_id="eagle_test", hull_id="eagle", weapon_assignments={}, hullmods=(), flux_vents=0, flux_capacitors=0),),
                enemy_variants=("dominator_Assault",),
            )
        ]
        pool._write_queue(inst, matchups)

        data = json.loads(inst.queue_path.read_text())
        assert len(data) == 1
        assert data[0]["matchup_id"] == "test_001"


# --- Instance Pool Properties ---


class TestInstancePoolProperties:

    def test_num_instances(self, pool, config):
        """num_instances property returns correct count after setup."""
        pool.setup()
        assert pool.num_instances == config.num_instances


# --- Health Monitoring Tests ---


class TestHealthMonitoring:

    def test_heartbeat_fresh(self, pool, config):
        """Recent heartbeat file mtime → instance considered alive."""
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING
        inst.last_heartbeat_time = time.monotonic()

        # Write a fresh heartbeat file
        inst.heartbeat_path.write_text(f"{int(time.time() * 1000)} 10.0")

        assert pool._is_heartbeat_fresh(inst)

    def test_heartbeat_stale(self, pool, config):
        """Old mtime > timeout → considered stale."""
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING
        inst.last_heartbeat_time = time.monotonic() - 200  # 200s ago

        # No heartbeat file at all
        assert not pool._is_heartbeat_fresh(inst)

    def test_startup_timeout(self, pool, config):
        """No heartbeat within startup_timeout → should be detected as timeout."""
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.STARTING
        inst.launch_time = time.monotonic() - 100  # 100s ago, beyond 90s default

        assert pool._is_startup_timed_out(inst)

    def test_startup_not_timed_out(self, pool, config):
        """Within startup timeout → not timed out."""
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.STARTING
        inst.launch_time = time.monotonic() - 10  # only 10s ago

        assert not pool._is_startup_timed_out(inst)

    def test_done_signal_detected(self, pool, config):
        """Done file exists → detected."""
        pool.setup()
        inst = pool._instances[0]
        assert not pool._is_done(inst)

        inst.done_path.write_text("1712345678000")
        assert pool._is_done(inst)


# --- Process Management Tests ---


class TestProcessManagement:

    def test_crash_detection(self, pool, config):
        """Process exited + no done signal → FAILED."""
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = 1  # exited with error
        inst.game_process = mock_proc

        # No done file exists
        assert pool._is_process_exited(inst)
        assert not pool._is_done(inst)

    def test_process_still_running(self, pool, config):
        """Process poll() returns None → still running."""
        pool.setup()
        inst = pool._instances[0]

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.poll.return_value = None
        inst.game_process = mock_proc

        assert not pool._is_process_exited(inst)

    def test_restart_increments_count(self, pool, config):
        """Restart increments restart_count."""
        pool.setup()
        inst = pool._instances[0]
        assert inst.restart_count == 0

        inst.restart_count += 1
        assert inst.restart_count == 1

    def test_max_restarts_exceeded(self, pool, config):
        """restart_count >= max_restarts → should not allow restart."""
        pool.setup()
        inst = pool._instances[0]
        inst.restart_count = config.max_restarts

        assert not pool._can_restart(inst)

    def test_can_restart(self, pool, config):
        """restart_count < max_restarts → can restart."""
        pool.setup()
        inst = pool._instances[0]
        inst.restart_count = 0

        assert pool._can_restart(inst)

    def test_graceful_shutdown(self, pool, config):
        """Teardown kills all processes and waits for them."""
        pool.setup()
        for inst in pool._instances:
            mock_game = MagicMock(spec=subprocess.Popen)
            mock_game.poll.return_value = None
            inst.game_process = mock_game
            mock_xvfb = MagicMock(spec=subprocess.Popen)
            mock_xvfb.poll.return_value = None
            inst.xvfb_process = mock_xvfb
            inst._game_log_file = MagicMock(closed=False)
            inst.state = InstanceState.RUNNING

        pool.teardown()

        for inst in pool._instances:
            inst.game_process.terminate.assert_called_once()
            inst.game_process.wait.assert_called_once()
            inst.xvfb_process.terminate.assert_called_once()
            inst.xvfb_process.wait.assert_called_once()
            inst._game_log_file.close.assert_called_once()
            assert inst.state == InstanceState.STOPPED

    def test_kill_instance_waits_for_xvfb(self, pool, config):
        """_kill_instance terminates Xvfb and waits for it to exit."""
        pool.setup()
        inst = pool._instances[0]
        mock_xvfb = MagicMock(spec=subprocess.Popen)
        mock_xvfb.poll.return_value = None
        mock_game = MagicMock(spec=subprocess.Popen)
        mock_game.poll.return_value = None
        inst.xvfb_process = mock_xvfb
        inst.game_process = mock_game
        pool._kill_instance(inst)
        mock_xvfb.terminate.assert_called_once()
        mock_xvfb.wait.assert_called_once()


# --- Display Numbering Tests ---


class TestDisplayNumbering:

    def test_xvfb_display_numbers(self, config):
        """Instances get base+0, base+1, ..."""
        pool = InstancePool(config)
        pool.setup()

        displays = [inst.display_num for inst in pool._instances]
        assert displays == [200, 201]

    def test_custom_base_display(self, fake_game_dir, tmp_path):
        """Custom base display works."""
        cfg = InstanceConfig(
            game_dir=fake_game_dir,
            instance_root=tmp_path / "inst",
            num_instances=3,
            xvfb_base_display=50,
        )
        pool = InstancePool(cfg)
        pool.setup()

        displays = [inst.display_num for inst in pool._instances]
        assert displays == [50, 51, 52]


# --- Context Manager Tests ---


class TestContextManager:

    def test_context_manager_calls_teardown(self, config):
        """__exit__ calls teardown."""
        pool = InstancePool(config)
        pool.setup()

        with patch.object(pool, "teardown") as mock_td:
            with pool:
                pass
            mock_td.assert_called_once()


# --- Enriched Heartbeat Tests ---


class TestEnrichedHeartbeat:

    def test_heartbeat_content_validation_6_field(self, pool, config):
        """Parse 6-field enriched heartbeat content."""
        pool.setup()
        inst = pool._instances[0]
        inst.heartbeat_path.write_text(f"{int(time.time() * 1000)} 30.0 0.85 0.42 2 1")

        assert pool._is_heartbeat_fresh(inst)


# --- Curtailment Integration Tests ---


class TestCurtailmentIntegration:

    def test_pool_works_without_curtailment(self, pool, config):
        """InstancePool(config) works without curtailment parameter."""
        pool.setup()
        assert pool._curtailment is None

    def test_pool_accepts_curtailment(self, config):
        """InstancePool(config, curtailment=monitor) stores monitor."""
        from starsector_optimizer.curtailment import CurtailmentMonitor
        monitor = CurtailmentMonitor()
        pool = InstancePool(config, curtailment=monitor)
        assert pool._curtailment is monitor

    def test_heartbeats_start_empty(self, pool, config):
        """GameInstance.heartbeats starts as empty list."""
        pool.setup()
        inst = pool._instances[0]
        assert inst.heartbeats == []

    def test_read_and_check_curtailment_parses(self, config):
        """_read_and_check_curtailment parses heartbeat content."""
        from starsector_optimizer.curtailment import CurtailmentMonitor
        monitor = CurtailmentMonitor()
        pool = InstancePool(config, curtailment=monitor)
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING
        inst.heartbeat_path.write_text("1712345678000 45.5 0.85 0.42 2 1")
        pool._read_and_check_curtailment(inst)
        assert len(inst.heartbeats) == 1
        assert inst.heartbeats[0].player_hp == pytest.approx(0.85)
        assert inst.heartbeats[0].enemy_hp == pytest.approx(0.42)

    def test_curtailment_noop_without_monitor(self, pool, config):
        """_read_and_check_curtailment does nothing when curtailment is None."""
        pool.setup()
        inst = pool._instances[0]
        inst.heartbeat_path.write_text("1712345678000 45.5 0.85 0.42 2 1")
        pool._read_and_check_curtailment(inst)
        assert inst.heartbeats == []

    def test_heartbeat_dedup_same_timestamp(self, config):
        """Same heartbeat content read twice → only 1 heartbeat accumulated."""
        from starsector_optimizer.curtailment import CurtailmentMonitor
        monitor = CurtailmentMonitor()
        pool = InstancePool(config, curtailment=monitor)
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING
        inst.heartbeat_path.write_text("1712345678000 45.5 0.85 0.42 2 1")
        pool._read_and_check_curtailment(inst)
        pool._read_and_check_curtailment(inst)  # Same content, same timestamp
        assert len(inst.heartbeats) == 1

    def test_heartbeat_different_timestamps_both_added(self, config):
        """Different timestamps → both heartbeats accumulated."""
        from starsector_optimizer.curtailment import CurtailmentMonitor
        monitor = CurtailmentMonitor()
        pool = InstancePool(config, curtailment=monitor)
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING
        inst.heartbeat_path.write_text("1712345678000 45.5 0.85 0.42 2 1")
        pool._read_and_check_curtailment(inst)
        inst.heartbeat_path.write_text("1712345679000 46.5 0.83 0.38 2 1")
        pool._read_and_check_curtailment(inst)
        assert len(inst.heartbeats) == 2

    def test_stop_signal_written_when_curtailment_triggers(self, config):
        """When should_stop returns True, stop signal file is created."""
        from starsector_optimizer.curtailment import CurtailmentMonitor
        # min_time=0 and window=3 to trigger quickly
        monitor = CurtailmentMonitor(min_time=0.0, window=3, ttd_ratio=2.0, max_ttd=200.0)
        pool = InstancePool(config, curtailment=monitor)
        pool.setup()
        inst = pool._instances[0]
        inst.state = InstanceState.RUNNING

        # Write a series of one-sided heartbeats — player barely damaged, enemy dying fast
        for i in range(5):
            elapsed = 35.0 + i
            player_hp = 0.95 - i * 0.005
            enemy_hp = 0.5 - i * 0.08
            inst.heartbeat_path.write_text(
                f"1712345678{i:03d} {elapsed} {player_hp} {enemy_hp} 1 1"
            )
            pool._read_and_check_curtailment(inst)

        stop_file = inst.saves_common / "combat_harness_stop.data"
        assert stop_file.exists()


# --- Persistent Session Tests ---


def _make_matchups(n):
    """Helper: create n minimal MatchupConfig objects."""
    return [
        MatchupConfig(
            matchup_id=f"m{i}",
            player_builds=(BuildSpec(variant_id="a", hull_id="a",
                                     weapon_assignments={}, hullmods=(),
                                     flux_vents=0, flux_capacitors=0),),
            enemy_variants=("b",),
        )
        for i in range(n)
    ]


class TestPersistentSession:

    def test_new_queue_signal_path_property(self, pool, config):
        """new_queue_signal_path returns correct saves/common path."""
        pool.setup()
        inst = pool._instances[0]
        expected = inst.saves_common / "combat_harness_new_queue.data"
        assert inst.new_queue_signal_path == expected

    def test_shutdown_signal_path_property(self, pool, config):
        """shutdown_signal_path returns correct saves/common path."""
        pool.setup()
        inst = pool._instances[0]
        expected = inst.saves_common / "combat_harness_shutdown.data"
        assert inst.shutdown_signal_path == expected

    def test_protocol_files_includes_new_signals(self):
        """PROTOCOL_FILES list has all 6 files including new signals."""
        assert "combat_harness_new_queue.data" in PROTOCOL_FILES
        assert "combat_harness_shutdown.data" in PROTOCOL_FILES
        assert len(PROTOCOL_FILES) == 6

    def test_clean_protocol_files_removes_new_signals(self, pool, config):
        """_clean_protocol_files removes new signal files too."""
        pool.setup()
        inst = pool._instances[0]
        inst.new_queue_signal_path.write_text("1")
        inst.shutdown_signal_path.write_text("1")

        pool._clean_protocol_files(inst)

        assert not inst.new_queue_signal_path.exists()
        assert not inst.shutdown_signal_path.exists()

    def test_assign_next_batch_writes_queue_and_signal(self, pool, config):
        """_assign_next_batch writes queue file + new_queue signal, no process creation."""
        pool.setup()
        inst = pool._instances[0]
        matchups = _make_matchups(3)

        pool._assign_next_batch(inst, matchups)

        assert inst.queue_path.exists()
        assert inst.new_queue_signal_path.exists()
        data = json.loads(inst.queue_path.read_text())
        assert len(data) == 3

    def test_assign_next_batch_cleans_protocol_files(self, pool, config):
        """_assign_next_batch removes old done/heartbeat/results files first."""
        pool.setup()
        inst = pool._instances[0]
        inst.done_path.write_text("old")
        inst.heartbeat_path.write_text("old")
        inst.results_path.write_text("old")

        pool._assign_next_batch(inst, _make_matchups(1))

        assert not inst.done_path.exists()
        assert not inst.heartbeat_path.exists()
        assert not inst.results_path.exists()

    def test_assign_next_batch_preserves_processes(self, pool, config):
        """_assign_next_batch does not touch game or xvfb processes."""
        pool.setup()
        inst = pool._instances[0]
        mock_game = MagicMock(spec=subprocess.Popen)
        mock_xvfb = MagicMock(spec=subprocess.Popen)
        inst.game_process = mock_game
        inst.xvfb_process = mock_xvfb

        pool._assign_next_batch(inst, _make_matchups(1))

        # Processes should be the exact same objects, untouched
        assert inst.game_process is mock_game
        assert inst.xvfb_process is mock_xvfb
        mock_game.terminate.assert_not_called()
        mock_xvfb.terminate.assert_not_called()

    def test_assign_next_batch_resets_instance_state(self, pool, config):
        """_assign_next_batch clears results, heartbeats, restart_count; sets RUNNING."""
        pool.setup()
        inst = pool._instances[0]
        inst.results = [MagicMock()]
        inst.heartbeats = [MagicMock()]
        inst.restart_count = 2

        pool._assign_next_batch(inst, _make_matchups(2))

        assert inst.results == []
        assert inst.heartbeats == []
        assert inst.restart_count == 0
        assert inst.state == InstanceState.RUNNING
        assert inst.assigned_matchups == _make_matchups(2)

    def test_is_instance_reusable_running(self, pool, config):
        """True when both game and Xvfb processes are alive."""
        pool.setup()
        inst = pool._instances[0]
        mock_game = MagicMock(spec=subprocess.Popen)
        mock_game.poll.return_value = None
        mock_xvfb = MagicMock(spec=subprocess.Popen)
        mock_xvfb.poll.return_value = None
        inst.game_process = mock_game
        inst.xvfb_process = mock_xvfb

        assert pool._is_instance_reusable(inst)

    def test_is_instance_reusable_dead_game(self, pool, config):
        """False when game process has exited."""
        pool.setup()
        inst = pool._instances[0]
        mock_game = MagicMock(spec=subprocess.Popen)
        mock_game.poll.return_value = 1  # exited
        mock_xvfb = MagicMock(spec=subprocess.Popen)
        mock_xvfb.poll.return_value = None
        inst.game_process = mock_game
        inst.xvfb_process = mock_xvfb

        assert not pool._is_instance_reusable(inst)

    def test_is_instance_reusable_no_process(self, pool, config):
        """False when game_process is None (never launched)."""
        pool.setup()
        inst = pool._instances[0]
        assert inst.game_process is None
        assert not pool._is_instance_reusable(inst)

    def test_total_matchups_tracking(self, pool, config):
        """total_matchups_processed starts at 0 and is tracked per instance."""
        pool.setup()
        inst = pool._instances[0]
        assert inst.total_matchups_processed == 0

    def test_clean_restart_after_threshold(self, config):
        """When total >= clean_restart_matchups, needs_restart is true."""
        cfg = InstanceConfig(
            game_dir=config.game_dir,
            instance_root=config.instance_root,
            num_instances=1,
            clean_restart_matchups=10,
        )
        pool = InstancePool(cfg)
        pool.setup()
        inst = pool._instances[0]
        inst.total_matchups_processed = 10

        # Should trigger clean restart
        assert inst.total_matchups_processed >= cfg.clean_restart_matchups

    def test_clean_restart_resets_counter(self, pool, config):
        """After a full restart, total_matchups_processed resets to 0."""
        pool.setup()
        inst = pool._instances[0]
        inst.total_matchups_processed = 150
        # Simulate what evaluate() does on clean restart
        inst.total_matchups_processed = 0
        assert inst.total_matchups_processed == 0

    def test_write_shutdown_signal(self, pool, config):
        """_write_shutdown_signal creates the shutdown signal file."""
        pool.setup()
        inst = pool._instances[0]
        pool._write_shutdown_signal(inst)
        assert inst.shutdown_signal_path.exists()
        content = inst.shutdown_signal_path.read_text()
        assert content  # non-empty timestamp

    def test_teardown_writes_shutdown_signals(self, pool, config):
        """teardown writes shutdown signals before killing running instances."""
        pool.setup()
        for inst in pool._instances:
            mock_game = MagicMock(spec=subprocess.Popen)
            mock_game.poll.return_value = None
            inst.game_process = mock_game
            mock_xvfb = MagicMock(spec=subprocess.Popen)
            mock_xvfb.poll.return_value = None
            inst.xvfb_process = mock_xvfb
            inst.state = InstanceState.RUNNING

        pool.teardown()

        for inst in pool._instances:
            # Shutdown signal should have been written
            assert inst.shutdown_signal_path.exists()
            # Processes should still be terminated
            inst.game_process.terminate.assert_called_once()
            assert inst.state == InstanceState.STOPPED

