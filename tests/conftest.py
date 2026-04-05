"""Shared test fixtures for game data."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def game_dir():
    """Path to the Starsector game installation directory."""
    path = Path(__file__).parent.parent / "game" / "starsector"
    if not (path / "data" / "hulls" / "ship_data.csv").exists():
        pytest.skip("Game data not found at game/starsector/data/")
    return path


@pytest.fixture(scope="session")
def game_data(game_dir):
    """Fully parsed game data."""
    from starsector_optimizer.parser import load_game_data
    return load_game_data(game_dir)
