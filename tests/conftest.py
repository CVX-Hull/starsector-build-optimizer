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


@pytest.fixture(scope="session")
def manifest():
    """The committed GameManifest.

    Loaded once per test session — matches production: the orchestrator
    loads the manifest once at startup. Tests that mutate manifest state
    should construct their own local manifest, not modify this fixture.
    """
    from starsector_optimizer.game_manifest import GameManifest
    return GameManifest.load()


def attach_synthetic_hull(manifest, hull_id, applicable_mod_ids,
                          conditional_exclusions=None, *,
                          size=None, shield_type=None, is_carrier=False,
                          built_in_mods=()):
    """Test helper: return a copy of `manifest` with one synthetic
    `HullManifestEntry` added (or replaced) for the given hull_id.

    Tests that use synthetic `ShipHull` objects (not in the committed
    manifest) need a matching per-hull applicability entry. This helper
    builds a minimal HullManifestEntry + returns a new GameManifest that
    includes it. Does NOT mutate the session-scoped manifest fixture.
    """
    from starsector_optimizer.game_manifest import (
        GameManifest, HullManifestEntry,
    )
    from starsector_optimizer.models import HullSize, ShieldType

    entry = HullManifestEntry(
        id=hull_id,
        size=size if size is not None else HullSize.CRUISER,
        ordnance_points=100,
        hitpoints=5000.0,
        armor_rating=500.0,
        flux_capacity=5000.0,
        flux_dissipation=300.0,
        shield_type=shield_type if shield_type is not None else ShieldType.FRONT,
        ship_system_id="",
        built_in_mods=tuple(built_in_mods),
        built_in_weapons={},
        slots=(),
        is_d_hull=False,
        is_carrier=is_carrier,
        base_hull_id=None,
        applicable_hullmods=frozenset(applicable_mod_ids),
        conditional_exclusions={
            a: frozenset(bs) for a, bs in (conditional_exclusions or {}).items()
        },
    )
    new_hulls = dict(manifest.hulls)
    new_hulls[hull_id] = entry
    # Also ensure any synthetic mod id is discoverable in hullmods (the
    # load-time cross-ref invariant would normally drop dangling refs —
    # tests pass `applicable_mod_ids` that may not exist in the base
    # manifest, so we construct stub HullmodSpec entries for them).
    from starsector_optimizer.game_manifest import HullmodSpec
    new_hullmods = dict(manifest.hullmods)
    for mid in applicable_mod_ids:
        if mid not in new_hullmods:
            new_hullmods[mid] = HullmodSpec(
                id=mid, tier=0, hidden=False, hidden_everywhere=False,
                tags=frozenset(), ui_tags=frozenset(),
                op_cost_by_size={},
            )
    return GameManifest(
        weapons=manifest.weapons,
        hullmods=new_hullmods,
        hulls=new_hulls,
        constants=manifest.constants,
    )


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


@pytest.fixture
def workstation_tailnet_ip():
    """Canonical tailnet CGNAT RFC IP used in tests; avoids leaking a real one."""
    return "100.64.1.2"


@pytest.fixture
def smoke_env(monkeypatch, workstation_tailnet_ip):
    """Populate every env var the `run_optimizer.py --worker-pool cloud` path
    requires, so tests that exercise it start from a known-complete environment.
    Tests that want to trigger the `_require_env` failure path delete individual
    vars via monkeypatch.delenv AFTER this fixture runs.
    """
    monkeypatch.setenv("STARSECTOR_WORKSTATION_TAILNET_IP", workstation_tailnet_ip)
    monkeypatch.setenv("STARSECTOR_BEARER_TOKEN", "SMOKE_TEST_BEARER_e1a2")
    monkeypatch.setenv("STARSECTOR_TAILSCALE_AUTHKEY", "tskey-auth-SMOKE-TEST-44e7f9b3")
    monkeypatch.setenv("STARSECTOR_PROJECT_TAG", "starsector-smoke")
    return {
        "STARSECTOR_WORKSTATION_TAILNET_IP": workstation_tailnet_ip,
        "STARSECTOR_BEARER_TOKEN": "SMOKE_TEST_BEARER_e1a2",
        "STARSECTOR_TAILSCALE_AUTHKEY": "tskey-auth-SMOKE-TEST-44e7f9b3",
        "STARSECTOR_PROJECT_TAG": "starsector-smoke",
    }
