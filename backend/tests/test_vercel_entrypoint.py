"""The Vercel entry point (api/index.py): serverless defaults, a real route through it, and the
upload cap still applying to the recording upload by its path."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

ENTRY = Path(__file__).resolve().parents[2] / "api" / "index.py"
KEYS = ("GHOSTTRACK_DB", "GHOSTTRACK_DEMO", "GHOSTTRACK_TRUST_PROXY", "GHOSTTRACK_MAX_UPLOAD_MB",
        "GHOSTTRACK_GUEST", "GHOSTTRACK_ADMIN_PASSWORD_HASH", "GHOSTTRACK_SECRET_KEY")


def load_entry(db_path: str | None):
    """Run api/index.py as Vercel would, from a clean serverless environment (the module sets
    process-wide defaults, so callers wrap this in ``mock.patch.dict(os.environ)``)."""
    for k in KEYS:
        os.environ.pop(k, None)
    if db_path:
        os.environ["GHOSTTRACK_DB"] = db_path  # /tmp is not a Windows path; Vercel's default is tested below
    spec = importlib.util.spec_from_file_location("vercel_index", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def entry(tmp_path):
    from ghosttrack import api

    with mock.patch.dict(os.environ):
        mod = load_entry(str(tmp_path / "fn.db"))
        yield mod
    api._seeded.clear()


def test_entry_exposes_the_app_and_sets_serverless_defaults():
    with mock.patch.dict(os.environ):
        mod = load_entry(None)
        assert mod.app.title == "GhostTrack API"
        assert os.environ["GHOSTTRACK_DB"] == "/tmp/ghosttrack.db"
        assert os.environ["GHOSTTRACK_DEMO"] == "true"
        assert os.environ["GHOSTTRACK_TRUST_PROXY"] == "true"
        assert os.environ["GHOSTTRACK_MAX_UPLOAD_MB"] == "4"
        assert "GHOSTTRACK_ADMIN_PASSWORD_HASH" not in os.environ  # admin login is the owner's choice


def test_blank_dashboard_values_do_not_defeat_the_defaults():
    """A variable left empty in the hosting dashboard must not stop the serverless defaults."""
    with mock.patch.dict(os.environ):
        for k in KEYS:
            os.environ[k] = ""
        spec = importlib.util.spec_from_file_location("vercel_index", ENTRY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert os.environ["GHOSTTRACK_DB"] == "/tmp/ghosttrack.db"
        assert os.environ["GHOSTTRACK_DEMO"] == "true" and os.environ["GHOSTTRACK_MAX_UPLOAD_MB"] == "4"
        from ghosttrack import config

        assert str(config.db_path()) != "." and config.demo_mode() is True


def test_validation_and_replay_routes_work_through_the_entry_point(entry):
    c = TestClient(entry.app)  # a guest: the demo is read-only but browsable
    v = c.get("/api/validation")
    assert v.status_code == 200
    body = v.json()
    assert len(body["scenarios"]) == 7 and body["all_scenarios_passed"] is True
    r = c.get("/api/scenarios/teleport/replay")
    assert r.status_code == 200
    assert r.json()["frames"] and r.json()["label"] == "Injected test data"
    health = c.get("/api/health").json()
    assert health["demo"] is True and health["guest"] is True and health["max_upload_mb"] == 4
    assert c.get("/api/alerts?limit=1").status_code == 200  # the lazily seeded demo database


def test_precomputed_fixtures_are_bundled_and_current():
    from ghosttrack import config, demo, fixtures

    for path in (config.API_FIXTURE, config.DEMO_FIXTURE, config.RECORDINGS_DIR / "manifest.json"):
        assert path.is_file(), f"{path} must ship with the function"
    assert fixtures.fixture()["inputs_digest"] == demo.inputs_digest()  # current, not stale
    assert not fixtures._compute_enabled()  # the committed fixture is read, not computed


def test_upload_route_keeps_its_cap_through_the_entry_point(entry):
    c = TestClient(entry.app)
    cap = 4 * 1024 * 1024
    too_big = b"x" * (cap + 128 * 1024)
    r = c.post("/api/recordings", files={"file": ("big.jsonl", too_big)})
    assert r.status_code == 413
    assert "max 4 MB" in r.json()["detail"]  # the upload cap, not the 64 KiB one
    # Just over the small-body limit but under the upload cap: the upload route lets it through to
    # its own checks (a guest gets 403), while any other route refuses it at 64 KiB.
    medium = b"x" * (200 * 1024)
    assert c.post("/api/recordings", files={"file": ("m.jsonl", medium)}).status_code == 403
    other = c.post("/api/auth/login", content=medium, headers={"content-type": "application/json"})
    assert other.status_code == 413
    assert "64 KiB" in other.json()["detail"]
