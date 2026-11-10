"""Recording uploads: limits, hostile files, validation counters and the end-to-end flow."""
from __future__ import annotations

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from ghosttrack import db, exports, uploads
from ghosttrack.api import app
from ghosttrack.scenarios import scenario_by_id
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording

from conftest import TEST_PASSWORD, TEST_USER


def jsonl(snaps) -> bytes:
    return ("\n".join(json.dumps(s.to_dict(), separators=(",", ":")) for s in snaps) + "\n").encode()


@pytest.fixture(scope="module")
def injected_recording() -> bytes:
    """The first clean recording with an injected teleport scenario (states keep injected=True)."""
    run = scenario_by_id("teleport").apply(load_recording(RECORDINGS_DIR / DEFAULT_RECORDING))
    return jsonl(run.snapshots)


@pytest.fixture(scope="module")
def clean_recording() -> bytes:
    return jsonl(load_recording(RECORDINGS_DIR / DEFAULT_RECORDING))


@pytest.fixture
def admin(db_file):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}).status_code == 200
    return c


def post(c, data: bytes, name="r.jsonl"):
    return c.post("/api/recordings", files={"file": (name, data, "application/octet-stream")})


# ------------------------------------------------------------------ end to end on an empty database


def test_end_to_end_on_an_empty_db(admin, db_file, injected_recording):
    assert admin.get("/api/alerts").json()["total"] == 0
    r = post(admin, gzip.compress(injected_recording), "teleport.jsonl.gz")
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["alerts_inserted"] > 0 and res["alert_ids"] and res["injected_alerts"] > 0
    assert res["lines_rejected"] == 0 and res["snapshots_kept"] > 100

    listed = admin.get("/api/alerts?injected=true&limit=200").json()
    assert listed["total"] >= 1
    a = next(x for x in listed["items"] if x["detection_id"] in scenario_by_id("teleport").expected)
    assert a["source"] == "upload" and a["label"] == "Injected test data (as declared by upload)"

    detail = admin.get(f"/api/alerts/{a['id']}").json()
    assert detail["evidence"] and detail["track"], "an uploaded alert keeps its track for the evidence pack"
    assert any(p["injected"] for p in detail["track"])

    pack = admin.get(f"/api/alerts/{a['id']}/evidence.json")
    assert pack.status_code == 200
    body = pack.json()
    assert body["injected_test_data"] is True and body["alert"]["source"] == "upload"
    assert body["track_points"] and body["failing_checks"]
    stored = db.get_alert(db.connect(db_file), a["id"])
    assert pack.headers["content-disposition"] == f'attachment; filename="{exports.evidence_filename(stored)}"'
    html = admin.get(f"/api/alerts/{a['id']}/evidence.html")
    assert html.status_code == 200 and "INJECTED TEST DATA" in html.text and "uploaded recording" in html.text

    # The aircraft page and the report see it too, and triage works on it.
    assert a["icao"] is None or admin.get(f"/api/aircraft/{a['icao']}").status_code in (200, 404)
    assert "GhostTrack" in admin.get("/api/reports/summary.md").text
    assert admin.patch(f"/api/alerts/{a['id']}", json={"status": "acknowledged"}).json()["status"] == "acknowledged"


def test_uploading_twice_does_not_duplicate_alerts(admin, injected_recording):
    first = post(admin, injected_recording).json()
    total = admin.get("/api/alerts").json()["total"]
    second = post(admin, injected_recording).json()
    assert second["alerts_inserted"] == 0 and second["alerts_updated"] == first["alerts_inserted"] + first["alerts_updated"]
    assert admin.get("/api/alerts").json()["total"] == total


def test_clean_recording_makes_no_injected_alerts(admin, clean_recording):
    res = post(admin, clean_recording).json()
    assert res["injected_alerts"] == 0
    items = admin.get("/api/alerts?limit=200").json()["items"]
    assert all(not a["injected"] and a["label"] == "Uploaded recording (unverified)" for a in items)


def test_uploaded_alerts_survive_pruning(admin, db_file, injected_recording):
    post(admin, injected_recording)
    total = admin.get("/api/alerts").json()["total"]
    conn = db.connect(db_file)
    db.prune(conn, 4_000_000_000.0)  # far in the future: everything with a time limit is gone
    conn.close()
    assert admin.get("/api/alerts").json()["total"] == total


# ------------------------------------------------------------------ limits and hostile files


def test_too_large_is_413(admin, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "0.1")
    assert post(admin, b"x" * (120 * 1024)).status_code == 413


def test_demo_limit_is_4_mb(admin, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    line = json.dumps({"ts": 1.0, "aircraft": []}).encode() + b"\n"
    assert post(admin, line * (4 * 1024 * 1024 // len(line) + 10)).status_code == 413


def test_gzip_bomb_is_refused_not_inflated(admin, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_MAX_UPLOAD_MB", "0.5")  # inflate cap = 2 MB
    bomb = gzip.compress(b"\n" * (60 * 1024 * 1024), compresslevel=9)
    assert len(bomb) < 512 * 1024
    r = post(admin, bomb, "bomb.jsonl.gz")
    assert r.status_code == 413 and "expands" in r.json()["detail"]


def test_decode_upload_never_reads_past_the_cap():
    bomb = gzip.compress(b"A" * (50 * 1024 * 1024), compresslevel=9)
    with pytest.raises(uploads.UploadError) as e:
        uploads.decode_upload(bomb, 1024 * 1024)
    assert e.value.status == 413


@pytest.mark.parametrize("data, status", [
    (b'{"ts":1,"aircraft":[]}\n\x00{"ts":2,"aircraft":[]}\n', 415),
    (b'{"ts":1,"aircraft":[]}\n\xff\xfe\x80\n', 415),
    (b"\x1f\x8b" + b"not really gzip", 400),
    (gzip.compress(b'{"ts":1,"aircraft":[]}\n')[:-8], 400),  # truncated
    (gzip.compress(b'\x00{"ts":1,"aircraft":[]}\n'), 415),
    (b"", 400),
    (b"\n\n  \n", 400),
    (b"this is not json\n{broken\n[1,2,3]\n", 400),
    (b'{"ts":"x","aircraft":[]}\n{"ts":1}\n{"aircraft":[]}\n', 400),
])
def test_hostile_files(admin, data, status):
    r = post(admin, data)
    assert r.status_code == status, r.text
    assert "Traceback" not in r.text


def test_malformed_lines_are_counted_not_fatal(admin, clean_recording):
    lines = clean_recording.split(b"\n")[:30]
    junk = [b"{not json", b'{"ts":"x","aircraft":[]}', b"[1]", b'{"ts":1e999,"aircraft":[]}', b"x" * (uploads.MAX_LINE_CHARS + 1)]
    data = b"\n".join(lines[:10] + junk + lines[10:]) + b"\n"
    r = post(admin, data)
    assert r.status_code == 200
    res = r.json()
    assert res["lines_rejected"] == len(junk) and res["snapshots_kept"] >= 29
    assert res["dropped_by_reason"]["bad_json"] >= 1 and res["dropped_by_reason"]["line_too_long"] == 1
    assert res["dropped_by_reason"]["bad_snapshot"] >= 1 and res["dropped_by_reason"]["bad_ts"] >= 1


def test_aircraft_records_go_through_upstream_validation(admin):
    good = {"icao": "a061d9", "callsign": "TEST1", "lat": 33.9, "lon": -118.4, "alt_baro_ft": 30000, "gs_kt": 400,
            "track_deg": 90, "category": "A3", "seen_pos_s": 0.5, "source": "adsb"}
    records = [
        good,
        {**good, "icao": "a061d9"},                        # duplicate address in one snapshot
        {**good, "icao": "ae1234"},                        # US military block: excluded
        {**good, "icao": "a00001", "dbFlags": 1},          # military flag: excluded
        {**good, "icao": "zzzzzz"},                        # bad address
        {**good, "icao": "a0b2c3", "lat": 900},            # absurd position
        {**good, "icao": "a0b2c4", "gs_kt": 1e9},          # absurd speed: field cleared, record kept
        "not an object",
    ]
    data = "\n".join(json.dumps({"ts": 1_794_300_000 + i * 5, "aircraft": records}) for i in range(3)).encode()
    parsed = uploads.parse_recording(data.decode())
    assert len(parsed.snapshots) == 3
    kept = {a.icao for a in parsed.snapshots[0].aircraft}
    assert kept == {"a061d9", "a0b2c4"}
    assert next(a for a in parsed.snapshots[0].aircraft if a.icao == "a0b2c4").gs_kt is None
    d = parsed.dropped
    assert d["military"] == 6 and d["bad_hex"] == 3 and d["duplicate_icao"] == 3 and d["bad_position"] == 3
    assert d["not_an_object"] == 3 and d["bad_gs"] == 3
    assert not any(a.injected for s in parsed.snapshots for a in s.aircraft)  # no claim, no label


def test_injected_flag_is_boolean_true_only(admin):
    base = {"icao": "a061d9", "lat": 33.9, "lon": -118.4, "alt_baro_ft": 30000, "gs_kt": 400, "seen_pos_s": 0.5}
    recs = [{**base, "injected": True}, {**base, "icao": "a0b2c3", "injected": "yes"},
            {**base, "icao": "a0b2c4", "injected": 1}]
    parsed = uploads.parse_recording(json.dumps({"ts": 5, "aircraft": recs}))
    flags = {a.icao: a.injected for a in parsed.snapshots[0].aircraft}
    assert flags == {"a061d9": True, "a0b2c3": False, "a0b2c4": False}


def test_limits_on_shape(admin):
    too_many = {"ts": 1, "aircraft": [{"icao": f"a{i:05x}", "lat": 1.0, "lon": 1.0} for i in range(uploads.MAX_AIRCRAFT_PER_SNAPSHOT + 50)]}
    parsed = uploads.parse_recording(json.dumps(too_many))
    assert parsed.aircraft_kept <= uploads.MAX_AIRCRAFT_PER_SNAPSHOT and parsed.dropped["too_many_aircraft"] == 50
    dup = "\n".join(json.dumps({"ts": 7, "aircraft": []}) for _ in range(3))
    assert uploads.parse_recording(dup).dropped["duplicate_ts"] == 2
    with pytest.raises(uploads.UploadError):
        uploads.parse_recording("\n".join(json.dumps({"ts": i, "aircraft": []}) for i in range(uploads.MAX_SNAPSHOTS + 1)))


def test_filename_and_content_type_do_not_matter(admin, clean_recording):
    # Format is decided by content (gzip magic), never by the client-supplied name.
    assert post(admin, gzip.compress(clean_recording), "innocent.txt").status_code == 200
    assert post(admin, clean_recording, "../../evil.jsonl.gz").status_code == 200


def test_missing_file_field_is_422(admin):
    assert admin.post("/api/recordings", data={"other": "x"}).status_code == 422


def test_failed_upload_stores_nothing(admin, db_file):
    post(admin, b"\x00garbage")
    assert admin.get("/api/alerts").json()["total"] == 0


# ------------------------------------------------------------------ exports and labels keep the upload's provenance


def test_exports_of_an_uploaded_alert_say_unverified_through_the_api(admin, injected_recording):
    post(admin, injected_recording)
    a = next(x for x in admin.get("/api/alerts?limit=200").json()["items"] if x["source"] == "upload")
    pack = admin.get(f"/api/alerts/{a['id']}/evidence.json").json()
    assert pack["origin_label"] == a["label"] == "Injected test data (as declared by upload)"
    assert "adsb.lol" not in pack["data_source"]
    html = admin.get(f"/api/alerts/{a['id']}/evidence.html").text
    assert "as declared by upload" in html and "adsb.lol" not in html
    summary = admin.get("/api/reports/summary.md").text
    assert "from **uploaded recordings (unverified)**" in summary
    assert "0 from real/recorded traffic" in summary  # nothing else was loaded: uploads are not real traffic


def test_aircraft_label_follows_the_upload_origin(admin, injected_recording):
    post(admin, injected_recording)
    items = admin.get("/api/alerts?limit=200").json()["items"]
    addr = next(x["icao"] for x in items if x["icao"] and x["source"] == "upload")
    body = admin.get(f"/api/aircraft/{addr}").json()
    assert body["label"] == "Injected test data (as declared by upload)"
    assert all(p["label"] in (None, "Injected test data (as declared by upload)", "Uploaded recording (unverified)")
               for p in body["track"])


def test_aircraft_label_rules():
    from ghosttrack import api

    rec = {"snapshot_source": "recorded", "injected": False}
    up = {"snapshot_source": "upload", "injected": False}
    assert api._aircraft_label([rec], []) is None
    assert api._aircraft_label([rec, {**rec, "injected": True}], []) == "Injected test data"
    assert api._aircraft_label([up], []) == "Uploaded recording (unverified)"
    assert api._aircraft_label([{**up, "injected": True}], []) == "Injected test data (as declared by upload)"
    assert api._aircraft_label([], [{"source": "upload", "injected": False}]) == "Uploaded recording (unverified)"
    assert api._aircraft_label([], [{"source": "upload", "injected": True}]) == "Injected test data (as declared by upload)"
    assert api._aircraft_label([], [{"source": "recorded", "injected": True}]) == "Injected test data"


def test_guest_upload_is_refused_before_the_body_is_parsed(db_file, monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    parsed = []
    original = Request.form

    def spy(self, *a, **k):
        parsed.append(1)
        return original(self, *a, **k)

    monkeypatch.setattr(Request, "form", spy)
    guest = TestClient(app)
    assert guest.get("/api/health").status_code == 200
    r = post(guest, b"x" * (1024 * 1024))
    assert r.status_code == 403
    assert parsed == [], "the multipart body was parsed for a guest"
    # The same request from the admin does parse the form.
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}).status_code == 200
    assert post(c, b"x").status_code in (400, 415)
    assert parsed == [1]
