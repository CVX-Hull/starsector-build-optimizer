"""Shared test fixtures for game data + Phase 6 cloud-worker-federation mocks."""

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


@pytest.fixture
def fake_redis():
    """Function-scoped fakeredis client. Fresh per test to avoid cross-test leakage."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    yield client
    client.flushall()


@pytest.fixture
def aws_mocked():
    """moto-backed AWS. Use @pytest.mark.usefixtures('aws_mocked') on tests
    that construct AWSProvider. Credentials are stubbed to dummy values.
    """
    moto = pytest.importorskip("moto")
    import os
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        yield


@pytest.fixture
def flask_test_client_factory():
    """Returns a factory that wraps a CloudWorkerPool's Flask app in a test client.

    Usage:
        def test_post(flask_test_client_factory):
            pool = CloudWorkerPool(...)
            client = flask_test_client_factory(pool.app)
            response = client.post('/result', json={...})
    """
    def _factory(app):
        app.config["TESTING"] = True
        return app.test_client()
    return _factory
