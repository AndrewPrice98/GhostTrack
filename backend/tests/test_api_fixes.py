"""Serverless readiness: precomputed fixtures, lazy seeding, path-aware body cap, labels, q search."""
from __future__ import annotations

import json
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from ghosttrack import api, config, demo, fixtures, live, validate
from ghosttrack.api import app

from conftest import TEST_PASSWORD, TEST_USER


@pytest.fixture
def demo_client(db_file, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    return TestClient(app)


@pytest.fixture
def admin(db_file):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}).status_code == 200
    return c


# ------------------------------------------------------------------ I1: precomputed fixtures


def test_committed_api_fixture_equals_a_fresh_build():
    fresh = fixtures.fixture_json(fixtures.build())
    assert config.API_FIXTURE.read_text(encoding="utf-8") == fresh, "run scripts/build_demo.py and commit"


def test_api_reads_the_fixture_and_never_computes(demo_client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("computed at request time")

    for name in ("run_validation", "run_gap_validation", "measure_false_positives"):
        monkeypatch.setattr(validate, name, boom)
    monkeypatch.setattr(fixtures, "compute_replay", boom)
    monkeypatch.setattr(fixtures, "build", boom)
    assert demo_client.get("/api/validation").json()["all_scenarios_passed"] is True
    assert demo_client.get("/api/detections").status_code == 200
    assert demo_client.get("/api/reports/summary.md").status_code == 200
    assert demo_client.get("/api/scenarios/teleport/replay").json()["frames"]
    assert not any(t.name == "warm-validation" for t in threading.enumerate())


def test_fixture_validation_matches_live_computation():
    raw = fixtures.validation_raw()
    fresh = validate.run_validation()
    assert [(r.scenario, r.passed, r.fired, r.time_to_detect_s) for r in raw["results"]] == \
        [(r.scenario, r.passed, r.fired, r.time_to_detect_s) for r in fresh]
    assert [r.per_hour for r in raw["fps"]] == [r.per_hour for r in validate.measure_false_positives()]


def test_stale_fixture_is_503_unless_compute_is_enabled(demo_client, monkeypatch):
    committed = json.loads(config.API_FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr(fixtures, "_load_committed", lambda: {**committed, "inputs_digest": "stale"})
    assert demo_client.get("/api/validation").status_code == 503
    assert demo_client.get("/api/scenarios/teleport/replay").status_code == 503
    monkeypatch.setenv("GHOSTTRACK_COMPUTE_FIXTURES", "true")
    monkeypatch.setattr(fixtures, "_computed", None)
    monkeypatch.setattr(fixtures, "build", lambda: committed)
    assert demo_client.get("/api/validation").status_code == 200


# ------------------------------------------------------------------ I2: lazy, idempotent seeding


def test_seeding_happens_once_across_requests(demo_client, monkeypatch):
    calls = []
    real = demo.ensure_seeded

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(demo, "ensure_seeded", counting)
    for _ in range(6):
        assert demo_client.get("/api/alerts").status_code == 200
    assert len(calls) == 1


def test_seeding_is_not_attempted_outside_demo_mode(db_file, monkeypatch):
    monkeypatch.setattr(demo, "ensure_seeded", lambda *a, **k: pytest.fail("seeded outside demo"))
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert c.get("/api/alerts").json()["total"] == 0


def test_seed_failure_is_503_logged_once_and_cached(demo_client, monkeypatch, caplog):
    calls = []

    def failing(*a, **k):
        calls.append(1)
        raise OSError("read-only file system")

    monkeypatch.setattr(demo, "ensure_seeded", failing)
    with caplog.at_level(logging.ERROR, logger="ghosttrack.api"):
        for _ in range(5):
            r = demo_client.get("/api/alerts")
            assert r.status_code == 503 and r.json() == {"detail": "Demo data is not available yet"}
            assert "read-only" not in r.text
    assert len(calls) == 1
    assert len([x for x in caplog.records if x.name == "ghosttrack.api"]) == 1
    # After the TTL it tries again, and a success clears the failure.
    api._seed_failed[str(config.db_path())] -= api.SEED_FAILURE_TTL_S + 1
    monkeypatch.setattr(demo, "ensure_seeded", lambda *a, **k: calls.append(2))
    assert demo_client.get("/api/alerts").status_code == 200
    assert calls == [1, 2]


def test_live_route_still_serves_when_seeding_failed(demo_client, monkeypatch):
    def failing(*a, **k):
        raise OSError("x")

    monkeypatch.setattr(demo, "ensure_seeded", failing)
    assert demo_client.get("/api/alerts").status_code == 503
    r = demo_client.get("/api/live")
    assert r.status_code == 200 and r.json()["aircraft"]


# ------------------------------------------------------------------ body cap


def test_one_megabyte_to_login_is_413(admin):
    r = admin.post("/api/auth/login", content=b"x" * (1024 * 1024), headers={"content-type": "application/json"})
    assert r.status_code == 413
    r = admin.post("/api/auth/login", content=b"x", headers={"content-length": str(1024 * 1024)})
    assert r.status_code == 413


def test_small_cap_applies_to_patch_but_not_to_the_upload(admin):
    assert admin.patch("/api/alerts/1", content=b"{" + b" " * 70_000 + b"}",
                       headers={"content-type": "application/json"}).status_code == 413
    # The upload route keeps its (larger) cap: 1 MB of junk gets as far as the file checks.
    r = admin.post("/api/recordings", files={"file": ("r.jsonl", b"x\n" * 500_000)})
    assert r.status_code == 400


def test_small_cap_applies_to_other_paths_with_the_recordings_prefix(admin):
    assert admin.post("/api/recordings/extra", content=b"x" * 100_000).status_code == 413


# ------------------------------------------------------------------ minors


@pytest.mark.parametrize("path", ["/api/alerts/1%0A", "/api/aircraft/a061d9%0A", "/api/scenarios/teleport%0A/replay"])
def test_trailing_newline_is_not_a_valid_id(demo_client, path):
    assert demo_client.get(path).status_code == 404


def test_live_response_varies_on_cookie(demo_client):
    assert demo_client.get("/api/live").headers["vary"] == "Cookie"


def test_live_mode_needs_the_switch(db_file, monkeypatch):
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert config.live_mode_enabled() is False
    assert c.get("/api/live?mode=live").status_code == 403
    assert c.get("/api/live?mode=recorded").status_code == 200
    monkeypatch.setenv("GHOSTTRACK_LIVE", "true")

    def down(region):
        raise OSError("down")

    app.state.feed = live.LiveFeed(fetch=down)
    try:
        assert c.get("/api/live?mode=live").status_code == 200
    finally:
        del app.state.feed


def test_live_switch_defaults_on_in_demo(monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    assert config.live_mode_enabled() is True
    monkeypatch.setenv("GHOSTTRACK_LIVE", "false")
    assert config.live_mode_enabled() is False


def test_api_docs_are_off_everywhere_and_openapi_is_writer_only(db_file, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    c = TestClient(app)
    for path in ("/docs", "/redoc"):
        assert c.get(path).status_code == 404
    assert c.get("/api/openapi.json").status_code == 403  # a guest
    assert c.get("/openapi.json").status_code == 404
    c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert c.get("/api/openapi.json").json()["info"]["title"] == "GhostTrack API"
    assert c.get("/docs").status_code == 404  # still off for the admin in demo


def test_openapi_needs_login_outside_demo(db_file):
    c = TestClient(app)
    assert c.get("/api/openapi.json").status_code == 401
    # The CDN-backed Swagger/ReDoc pages are removed outright: the CSP would block their scripts.
    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404


def test_q_searches_callsign_and_address(demo_client):
    items = demo_client.get("/api/alerts?limit=200").json()["items"]
    one = next(a for a in items if a["callsign"] and a["icao"])
    cs, ic = one["callsign"], one["icao"]
    needle = cs[1:4].lower()
    by_cs = demo_client.get("/api/alerts", params={"q": needle, "limit": 200}).json()
    assert any(a["id"] == one["id"] for a in by_cs["items"])
    assert all(needle in (a["callsign"] or "").lower() or needle in (a["icao"] or "") for a in by_cs["items"])
    by_hex = demo_client.get("/api/alerts", params={"q": ic[2:5].upper(), "limit": 200}).json()
    assert any(a["id"] == one["id"] for a in by_hex["items"])
    assert demo_client.get("/api/alerts", params={"q": "zzzz-none"}).json()["total"] == 0


@pytest.mark.parametrize("q", ["%", "_", "\\", "%%", "a%b", "' OR 1=1 --"])
def test_q_wildcards_are_literal_and_safe(demo_client, q):
    r = demo_client.get("/api/alerts", params={"q": q})
    assert r.status_code == 200
    assert r.json()["total"] == 0  # a wildcard does not match everything


def test_q_length_is_limited(demo_client):
    assert demo_client.get("/api/alerts", params={"q": "x" * 33}).status_code == 422


def test_health_reports_the_upload_limit(monkeypatch):
    c = TestClient(app)
    assert c.get("/api/health").json()["max_upload_mb"] == 10
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "2")
    assert c.get("/api/health").json()["max_upload_mb"] == 2
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "50")
    assert c.get("/api/health").json()["max_upload_mb"] == 4  # the demo is capped
