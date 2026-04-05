"""Tests for instance manager — work dir creation, process management, health monitoring.

All tests use tmp_path and mocked subprocess (no real game needed).
"""

import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from starsector_optimizer.models import MatchupConfig
from starsector_optimizer.instance_manager import (
    GameInstance,
    InstanceConfig,
    InstanceError,
    InstancePool,
    InstanceState,
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
        """Old queue/results/done/heartbeat files are removed before new batch."""
        pool.setup()
        inst = pool._instances[0]
        common = inst.saves_common

        # Create old files
        for name in [
            "combat_harness_queue.json.data",
            "combat_harness_results.json.data",
            "combat_harness_done.data",
            "combat_harness_heartbeat.txt.data",
        ]:
            (common / name).write_text("old")

        pool._clean_protocol_files(inst)

        for name in [
            "combat_harness_queue.json.data",
            "combat_harness_results.json.data",
            "combat_harness_done.data",
            "combat_harness_heartbeat.txt.data",
        ]:
            assert not (common / name).exists()

    def test_write_queue(self, pool, config):
        """MatchupConfig list serializes to correct JSON at saves/common/."""
        pool.setup()
        inst = pool._instances[0]
        matchups = [
            MatchupConfig(
                matchup_id="test_001",
                player_variants=("eagle_test",),
                enemy_variants=("dominator_Assault",),
            )
        ]
        pool._write_queue(inst, matchups)

        data = json.loads(inst.queue_path.read_text())
        assert len(data) == 1
        assert data[0]["matchup_id"] == "test_001"


# --- Matchup Distribution Tests ---


class TestMatchupDistribution:

    def test_distribute_matchups(self, config):
        """N matchups split across M instances with batch_size B."""
        config_small = InstanceConfig(
            game_dir=config.game_dir,
            instance_root=config.instance_root,
            num_instances=2,
            batch_size=3,
        )
        pool = InstancePool(config_small)

        matchups = [
            MatchupConfig(matchup_id=f"m{i}", player_variants=("a",), enemy_variants=("b",))
            for i in range(6)
        ]
        chunks = pool._split_into_chunks(matchups)
        assert len(chunks) == 2  # 6 / 3 = 2 chunks
        assert len(chunks[0]) == 3
        assert len(chunks[1]) == 3

    def test_distribute_uneven(self, config):
        """Remainder matchups form a smaller final chunk."""
        config_small = InstanceConfig(
            game_dir=config.game_dir,
            instance_root=config.instance_root,
            num_instances=2,
            batch_size=4,
        )
        pool = InstancePool(config_small)

        matchups = [
            MatchupConfig(matchup_id=f"m{i}", player_variants=("a",), enemy_variants=("b",))
            for i in range(7)
        ]
        chunks = pool._split_into_chunks(matchups)
        assert len(chunks) == 2  # ceil(7/4) = 2
        assert len(chunks[0]) == 4
        assert len(chunks[1]) == 3


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

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # exited with error
        inst.game_process = mock_proc

        # No done file exists
        assert pool._is_process_exited(inst)
        assert not pool._is_done(inst)

    def test_process_still_running(self, pool, config):
        """Process poll() returns None → still running."""
        pool.setup()
        inst = pool._instances[0]

        mock_proc = MagicMock()
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
        """Teardown kills all processes."""
        pool.setup()
        for inst in pool._instances:
            mock_game = MagicMock()
            mock_game.poll.return_value = None
            inst.game_process = mock_game
            mock_xvfb = MagicMock()
            mock_xvfb.poll.return_value = None
            inst.xvfb_process = mock_xvfb
            inst.state = InstanceState.RUNNING

        pool.teardown()

        for inst in pool._instances:
            inst.game_process.terminate.assert_called_once()
            inst.xvfb_process.terminate.assert_called_once()
            assert inst.state == InstanceState.STOPPED


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

    def test_heartbeat_content_validation_legacy(self, pool, config):
        """Parse 2-field legacy heartbeat."""
        pool.setup()
        inst = pool._instances[0]
        inst.heartbeat_path.write_text(f"{int(time.time() * 1000)} 10.0")

        assert pool._is_heartbeat_fresh(inst)
