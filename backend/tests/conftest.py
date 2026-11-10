"""Shared pytest fixtures: known admin credentials, a throwaway database, a clean demo state."""
import pytest

from ghosttrack import demo
from ghosttrack.auth import hash_password, limiter

TEST_USER = "admin"
TEST_PASSWORD = "correct horse battery staple"
# Few iterations keep the suite fast; production hashes use auth.DEFAULT_ITERATIONS.
TEST_HASH = hash_password(TEST_PASSWORD, iterations=1_000)


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """Known admin credentials for every test, regardless of any local backend/.env."""
    monkeypatch.setenv("GHOSTTRACK_ADMIN_USER", TEST_USER)
    monkeypatch.setenv("GHOSTTRACK_ADMIN_PASSWORD_HASH", TEST_HASH)
    monkeypatch.setenv("GHOSTTRACK_SECRET_KEY", "test-secret-" + "x" * 40)
    monkeypatch.setenv("GHOSTTRACK_COOKIE_SECURE", "false")  # TestClient talks plain http
    monkeypatch.setenv("GHOSTTRACK_TRUST_PROXY", "false")
    monkeypatch.delenv("GHOSTTRACK_DEMO", raising=False)
    monkeypatch.delenv("GHOSTTRACK_GUEST", raising=False)
    monkeypatch.delenv("GHOSTTRACK_LIVE_INTERVAL_S", raising=False)
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def fresh_catch_up_throttle():
    demo._last_run.clear()
    demo._last_fail.clear()
    demo._last_fail_log.clear()
    yield
    demo._last_run.clear()
    demo._last_fail.clear()
    demo._last_fail_log.clear()


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("GHOSTTRACK_DB", str(path))
    return path


@pytest.fixture
def conn(db_file):
    from ghosttrack import db

    c = db.connect(db_file)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def fresh_api_state():
    """The API remembers which databases it seeded (and failures); each test starts clean."""
    from ghosttrack import api

    api._seeded.clear()
    api._seed_failed.clear()
    yield
    api._seeded.clear()
    api._seed_failed.clear()
