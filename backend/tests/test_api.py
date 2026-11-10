"""REST API: shapes and status codes, guest read-only, hostile input, live fallback, headers."""
from __future__ import annotations

import logging
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from ghosttrack import db, demo, exports, live, upstream
from ghosttrack.api import app
from ghosttrack.model import Snapshot
from ghosttrack.scenarios import SCENARIOS

from conftest import TEST_PASSWORD, TEST_USER
from factories import st

INJECTION = [
    "' OR 1=1 --", "1; DROP TABLE alerts;--", '"><script>alert(1)</script>', "../../etc/passwd",
    "%00", "\u0000", "a" * 5000, "高", "{{7*7}}", "${jndi:ldap://x}",
]


@pytest.fixture
def seeded(db_file):
    demo.ensure_seeded()
    return db_file


@pytest.fixture
def guest(seeded, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    return TestClient(app)


@pytest.fixture
def admin(seeded):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return c


@pytest.fixture
def alert(guest):
    """An injected alert and a non-injected one, from the seeded replay."""
    items = guest.get("/api/alerts?limit=200").json()["items"]
    assert any(a["injected"] for a in items)
    return next(a for a in items if a["injected"] and a["icao"])


def alert_count(path) -> int:
    conn = db.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ shapes


def test_health_is_public(db_file):
    r = TestClient(app).get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_reads_need_auth_without_guest(seeded):
    c = TestClient(app)
    assert c.get("/api/alerts").status_code == 401
    assert c.get("/api/live").status_code == 401
    assert c.get("/api/alerts/1/evidence.json").status_code == 401


def test_admin_login_and_me(admin):
    assert admin.get("/api/auth/me").json() == {"username": TEST_USER, "guest": False}
    assert admin.post("/api/auth/login", json={"username": TEST_USER, "password": "wrong"}).status_code == 401


def test_live_shape(guest):
    r = guest.get("/api/live")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "recorded" and body["region"] == "socal" and body["mode"] == "recorded"
    assert "adsb.lol" in body["attribution"]
    assert body["aircraft"]
    a = body["aircraft"][0]
    for key in ("icao", "callsign", "lat", "lon", "alt", "gs", "track", "category", "source", "injected",
                "label", "flagged", "alert_ids"):
        assert key in a


def test_live_labels_injected_and_flags(db_file, monkeypatch):
    """At a moment inside an active scenario: injected aircraft are labelled and flagged."""
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    first = next(x.ts for x in demo.cycle_snapshots() if any(a.injected for a in x.aircraft))
    now = demo.CYCLE_S * 500 + first + 20.0  # shortly after the first injected state appears
    conn = db.connect()
    demo.seed(conn, now)
    conn.close()
    app.state.feed = live.LiveFeed(clock=lambda: now)
    try:
        body = TestClient(app).get("/api/live").json()
    finally:
        del app.state.feed
    injected = [a for a in body["aircraft"] if a["injected"]]
    assert injected and body["injected_aircraft"] == len(injected)
    for a in body["aircraft"]:
        assert a["label"] == ("Injected test data" if a["injected"] else None)
        assert a["flagged"] == bool(a["alert_ids"])
    assert any(a["flagged"] for a in injected)


def test_alerts_list_shape_and_labels(guest):
    body = guest.get("/api/alerts?limit=200").json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] >= len(body["items"]) > 0
    for a in body["items"]:
        assert a["label"] == ("Injected test data" if a["injected"] else None)
        assert a["title"] and a["benign_causes"]


def test_alert_detail_has_evidence_and_track(guest, alert):
    d = guest.get(f"/api/alerts/{alert['id']}").json()
    assert d["id"] == alert["id"] and d["evidence"]
    assert all("unit" in e for e in d["evidence"])
    assert isinstance(d["track"], list)
    assert d["injected"] and d["label"] == "Injected test data"


def test_unknown_and_junk_alert_ids_are_404(guest):
    for bad in ("999999999", "0", "abc", "1e3", "-1", "%27", "1%20OR%201=1"):
        assert guest.get(f"/api/alerts/{bad}").status_code == 404
        assert guest.get(f"/api/alerts/{bad}/evidence.json").status_code == 404


def test_detections_and_validation(guest):
    d = guest.get("/api/detections").json()
    assert [x["id"] for x in d["detections"]][0] == "GT-001"
    v = guest.get("/api/validation").json()
    assert d["validation"] == v
    assert len(v["scenarios"]) == len(SCENARIOS) and all(s["passed"] for s in v["scenarios"])
    assert all(s["label"] == "Injected test data" for s in v["scenarios"])
    assert v["false_positives"]["verdict"] == "within budget"
    assert v["false_positives"]["recordings"]
    assert v["all_scenarios_passed"] is True


def test_scenarios_list(guest):
    s = guest.get("/api/scenarios").json()["scenarios"]
    ids = {x["id"] for x in s}
    assert {"single_ghost", "teleport", "gradual_drift"} <= ids
    assert next(x for x in s if x["id"] == "teleport")["demo_offset_s"] is not None
    assert next(x for x in s if x["id"] == "gradual_drift")["gap"] is True


@pytest.mark.parametrize("name", [s.id for s in SCENARIOS] + ["gradual_drift"])
def test_scenario_replay(guest, name):
    r = guest.get(f"/api/scenarios/{name}/replay")
    assert r.status_code == 200
    d = r.json()
    assert 0 < len(d["frames"]) <= 400
    assert d["label"] == "Injected test data" and "INJECTED" in d["notice"]
    assert len(r.content) < 600_000
    injected = [a for f in d["frames"] for a in f["aircraft"] if a["injected"]]
    assert injected and all(a["label"] == "Injected test data" for a in injected)
    if name != "gradual_drift":
        assert d["events"]
    else:
        assert d["events"] == []  # a documented blind spot: nothing fires


def test_unknown_scenario_is_404(guest):
    for bad in ("nope", "../x", "TELEPORT", "a" * 200, "%00"):
        assert guest.get(f"/api/scenarios/{bad}/replay").status_code == 404


def test_reports_are_downloads(guest):
    for path, ctype, name in (("/api/reports/summary.html", "text/html", "ghosttrack-summary.html"),
                              ("/api/reports/summary.md", "text/markdown", "ghosttrack-summary.md")):
        r = guest.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
        assert r.headers["content-disposition"] == f'attachment; filename="{name}"'
        assert "GhostTrack" in r.text
    assert "sandbox" in guest.get("/api/reports/summary.html").headers["content-security-policy"]


def test_evidence_downloads(guest, alert):
    for ext, ctype in (("json", "application/json"), ("html", "text/html")):
        r = guest.get(f"/api/alerts/{alert['id']}/evidence.{ext}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
        stored = db.get_alert(db.connect(), alert["id"])
        name = exports.evidence_filename(stored, ext)
        assert r.headers["content-disposition"] == f'attachment; filename="{name}"'
        assert "injected" in name
    pack = guest.get(f"/api/alerts/{alert['id']}/evidence.json").json()
    assert pack["injected_test_data"] is True and pack["failing_checks"]
    assert "INJECTED" in guest.get(f"/api/alerts/{alert['id']}/evidence.html").text


# ------------------------------------------------------------------ filters


def test_filters(guest):
    everything = guest.get("/api/alerts?limit=200").json()
    items = everything["items"]
    high = guest.get("/api/alerts?severity=high&limit=200").json()
    assert high["items"] and all(a["severity"] == "high" for a in high["items"])
    inj = guest.get("/api/alerts?injected=true&limit=200").json()["items"]
    real = guest.get("/api/alerts?injected=false&limit=200").json()["items"]
    assert inj and all(a["injected"] for a in inj) and all(not a["injected"] for a in real)
    det = items[0]["detection_id"]
    by_det = guest.get(f"/api/alerts?detection={det}&limit=200").json()["items"]
    assert by_det and all(a["detection_id"] == det for a in by_det)
    st_ = guest.get("/api/alerts?status=new&limit=200").json()["items"]
    assert all(a["status"] == "new" for a in st_)
    last = max(a["last_ts"] for a in items)
    ranged = guest.get("/api/alerts", params={"start": "2000-01-01", "end": "2001-01-01"}).json()
    assert ranged["total"] == 0
    recent = guest.get("/api/alerts", params={"start": "2020-01-01T00:00:00Z", "limit": 200}).json()
    assert recent["total"] == everything["total"] and last > 0
    page = guest.get("/api/alerts?limit=2&offset=1").json()
    assert len(page["items"]) <= 2 and page["total"] == everything["total"]
    assert [a["id"] for a in page["items"]] == [a["id"] for a in items[1:3]]


def test_sort_is_allow_listed(guest):
    sev = guest.get("/api/alerts?sort=severity&limit=200").json()["items"]
    order = ["high", "medium", "low", "info"]
    ranks = [order.index(a["severity"]) for a in sev]
    assert ranks == sorted(ranks)
    assert guest.get("/api/alerts?sort=id;DROP TABLE alerts").status_code == 422


@pytest.mark.parametrize("bad", ["limit=0", "limit=201", "limit=-1", "limit=abc", "offset=-1", "offset=x",
                                 "severity=critical", "status=open", "injected=maybe", "start=yesterday",
                                 "end=2020-13-45", "detection=GT-999"])
def test_bad_filter_values_are_422(guest, bad):
    assert guest.get(f"/api/alerts?{bad}").status_code == 422


@pytest.mark.parametrize("payload", INJECTION)
def test_injection_in_every_filter(guest, seeded, payload):
    before = alert_count(seeded)
    for name in ("detection", "severity", "injected", "status", "start", "end", "sort", "limit", "offset"):
        r = guest.get("/api/alerts", params={name: payload})
        assert r.status_code in (200, 422), (name, r.status_code)
        assert "Traceback" not in r.text
    q = quote(payload, safe="")
    for path in (f"/api/aircraft/{q}", f"/api/scenarios/{q}/replay", f"/api/alerts/{q}"):
        assert guest.get(path).status_code in (404, 405, 422, 400, 414)
    for name in ("region", "mode"):
        assert guest.get("/api/live", params={name: payload}).status_code == 422
    assert alert_count(seeded) == before
    assert guest.get("/api/alerts").status_code == 200  # the table is intact


# ------------------------------------------------------------------ aircraft


def test_aircraft_validation_and_lookup(guest, alert):
    addr = alert["icao"]
    r = guest.get(f"/api/aircraft/{addr.upper()}")
    assert r.status_code == 200
    body = r.json()
    assert body["icao"] == addr and body["findings"]
    assert set(body["registration"]) >= {"registration", "address_state", "n_number"}
    assert body["injected"] and body["label"] == "Injected test data"
    for bad in ("zzzzzz", "12345", "1234567", "abc12", "abc12g", "%20abc12", "000000", "ffffff"):
        assert guest.get(f"/api/aircraft/{bad}").status_code == 404


# ------------------------------------------------------------------ writes


def test_patch_triage(admin, alert):
    r = admin.patch(f"/api/alerts/{alert['id']}", json={"status": "acknowledged", "notes": "looked at it"})
    assert r.status_code == 200
    assert r.json()["status"] == "acknowledged" and r.json()["notes"] == "looked at it"
    r = admin.patch(f"/api/alerts/{alert['id']}", json={"notes": "only notes"})
    assert r.json()["status"] == "acknowledged" and r.json()["notes"] == "only notes"
    assert admin.get(f"/api/alerts/{alert['id']}").json()["status"] == "acknowledged"


@pytest.mark.parametrize("body", [{}, {"status": "info"}, {"status": "x"}, {"status": ["new"]},
                                  {"notes": "x" * 2001}, {"notes": "a\u0000b"}, {"notes": 5}, {"status": None}])
def test_patch_rejects_bad_bodies(admin, alert, body):
    assert admin.patch(f"/api/alerts/{alert['id']}", json=body).status_code == 422


def test_patch_unknown_alert_is_404(admin):
    assert admin.patch("/api/alerts/999999", json={"status": "new"}).status_code == 404
    assert admin.patch("/api/alerts/abc", json={"status": "new"}).status_code == 404


def test_notes_are_stored_as_text_not_executed(admin, alert):
    note = "<script>alert(1)</script>' OR 1=1 --"
    admin.patch(f"/api/alerts/{alert['id']}", json={"notes": note})
    assert admin.get(f"/api/alerts/{alert['id']}").json()["notes"] == note
    assert note not in admin.get(f"/api/alerts/{alert['id']}/evidence.html").text or "&lt;script&gt;" in admin.get(
        f"/api/alerts/{alert['id']}/evidence.html").text


WRITES = [
    ("patch", "/api/alerts/1", {"json": {"status": "dismissed"}}),
    ("patch", "/api/alerts/1", {"json": {"status": "nonsense"}}),
    ("patch", "/api/alerts/1", {"content": b"{not json", "headers": {"content-type": "application/json"}}),
    ("post", "/api/recordings", {"files": {"file": ("r.jsonl", b"{}")}}),
    ("post", "/api/recordings", {"content": b"not multipart"}),
    ("post", "/api/recordings", {}),
]


@pytest.mark.parametrize("method, path, kwargs", WRITES)
def test_guest_cannot_write(guest, seeded, method, path, kwargs):
    before = alert_count(seeded)
    assert getattr(guest, method)(path, **kwargs).status_code == 403
    assert alert_count(seeded) == before


def test_guest_write_changes_nothing(guest, alert):
    guest.patch(f"/api/alerts/{alert['id']}", json={"status": "dismissed"})
    assert guest.get(f"/api/alerts/{alert['id']}").json()["status"] != "dismissed"


# ------------------------------------------------------------------ live route


class FakeFeedFetch:
    def __init__(self):
        self.calls = 0
        self.fail: Exception | None = None
        self.snap = Snapshot(0.0, [])

    def __call__(self, region):
        self.calls += 1
        if self.fail:
            raise self.fail
        return self.snap, {}


@pytest.fixture
def fake_feed(guest):
    import time

    fetch = FakeFeedFetch()
    feed = live.LiveFeed(fetch=fetch)
    fetch.snap = Snapshot(time.time(), [st(icao="a061d9", lat=33.9, lon=-118.4), st(icao="a0b2c3", lat=34.0, lon=-118.3)])
    app.state.feed = feed
    yield fetch
    del app.state.feed


def test_live_mode_uses_the_feed(guest, fake_feed):
    r = guest.get("/api/live?mode=live")
    body = r.json()
    assert r.status_code == 200 and body["source"] == "live" and body["notice"] is None
    assert {"a061d9", "a0b2c3"} <= {a["icao"] for a in body["aircraft"]}
    assert r.headers["cache-control"] == live.CACHE_CONTROL
    guest.get("/api/live?mode=live")
    assert fake_feed.calls == 1  # throttled: the second request came from the cache


def test_live_falls_back_when_the_feed_fails(guest, fake_feed):
    fake_feed.fail = upstream.UpstreamError("down")
    r = guest.get("/api/live?mode=live")
    body = r.json()
    assert r.status_code == 200
    assert body["source"] == "recorded" and body["notice"] == live.FALLBACK_NOTICE and body["aircraft"]
    assert r.headers["cache-control"] == live.CACHE_CONTROL


def test_live_validates_region_and_mode(guest):
    for q in ("region=mars", "region=", "region=SOCAL", "mode=weird", "mode=LIVE", "region=socal&mode="):
        assert guest.get(f"/api/live?{q}").status_code == 422
    assert guest.get("/api/live?region=socal&mode=recorded").status_code == 200


def test_live_survives_unusable_storage(guest, monkeypatch):
    def broken(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(db, "connect", broken)
    r = guest.get("/api/live")
    assert r.status_code == 200 and r.json()["notice"] == live.STORAGE_NOTICE and r.json()["aircraft"]


# ------------------------------------------------------------------ headers


GET_PATHS = ["/api/health", "/api/auth/me", "/api/alerts", "/api/alerts/1", "/api/detections", "/api/validation",
             "/api/scenarios", "/api/scenarios/teleport/replay", "/api/reports/summary.md",
             "/api/reports/summary.html", "/api/aircraft/zzzzzz", "/api/alerts/1/evidence.json",
             "/api/alerts/1/evidence.html", "/api/nope"]


@pytest.mark.parametrize("path", GET_PATHS)
def test_cache_control_only_on_live(guest, path):
    r = guest.get(path)
    assert r.headers["cache-control"] == "no-store"
    assert "s-maxage" not in r.headers["cache-control"]
    for k, v in {"x-content-type-options": "nosniff", "x-frame-options": "DENY"}.items():
        assert r.headers[k] == v
    assert r.headers["content-security-policy"].startswith(("default-src 'self'", "default-src 'none'"))


def test_csp_matches_cmmc_ready(guest):
    csp = guest.get("/api/health").headers["content-security-policy"]
    assert csp == ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                   "form-action 'self'")


def test_unexpected_error_is_a_generic_500(guest, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("secret internals C:\\Users\\x\\file.py")

    monkeypatch.setattr(live, "get_traffic", boom)
    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="ghosttrack.api"):
        r = c.get("/api/live")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "secret" not in r.text and "Traceback" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    logged = [x for x in caplog.records if x.name == "ghosttrack.api"]
    assert len(logged) == 1 and logged[0].exc_info is not None


# ------------------------------------------------------------------ body limit


def test_declared_oversize_body_is_413_before_reading(admin):
    r = admin.post("/api/recordings", content=b"x", headers={"content-length": str(50 * 1024 * 1024)})
    assert r.status_code == 413


def test_streamed_oversize_body_is_413(admin, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "0.1")

    def chunks():
        for _ in range(40):
            yield b"x" * 8192

    r = admin.post("/api/recordings", content=chunks(), headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 413


def test_demo_upload_cap_is_4_mb(monkeypatch):
    from ghosttrack import config

    assert config.max_upload_bytes() == 10 * 1024 * 1024
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    assert config.max_upload_bytes() == 4 * 1024 * 1024
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "2")
    assert config.max_upload_bytes() == 2 * 1024 * 1024


