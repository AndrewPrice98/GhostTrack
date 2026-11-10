"""Evidence packs and summary reports: structure, escaping, labelling, determinism."""
import json
import re
from dataclasses import replace

from ghosttrack import exports
from ghosttrack.validate import RecordingFP, ValidationResult

from factories import T0, flight

NOW = T0 + 3600.0
HOSTILE = "<script>alert(1)</script>&\"'\U0001F6EB" + "x" * 5000


def make_alert(**kw):
    a = dict(
        id=7, source="recorded", detection_id="GT-001", severity="high", icao="a061d9", callsign="TEST1",
        first_ts=T0, last_ts=T0 + 60, injected=False, confidence=0.9, status="new",
        explanation="Speed 900 kt exceeds the category ceiling.",
        benign_causes=["Wrong emitter category."],
        evidence=[{"field": "gs_kt", "observed": 912.5, "threshold": 650, "ts": T0 + 10}],
    )
    a.update(kw)
    return a


def pts():
    return flight(n=6)


def test_html_structure():
    out = exports.evidence_html(make_alert(), pts(), now=NOW).decode()
    assert out.startswith("<!DOCTYPE html>")
    assert "<script" not in out.lower() and "javascript:" not in out
    for needle in ("GT-001", "a061d9", "TEST1", "gs_kt", "912.5", "650", "kt", "UTC", "Possible benign causes",
                   "Wrong emitter category", "Track points", exports.DISCLAIMER, "<svg", "<style>"):
        assert needle in out
    assert "http" not in out.replace("http://www.w3.org/2000/svg", "")  # no external resources


def test_json_structure():
    doc = json.loads(exports.evidence_json(make_alert(), pts(), now=NOW))
    assert doc["alert"]["detection_id"] == "GT-001" and doc["alert"]["icao"] == "a061d9"
    assert doc["failing_checks"][0]["unit"] == "kt" and doc["failing_checks"][0]["threshold"] == 650
    assert len(doc["track_points"]) == 6 and doc["injected_test_data"] is False
    assert "ODbL" in doc["data_source"] and doc["disclaimer"] == exports.DISCLAIMER


def test_injected_banner_iff_injected():
    for inj in (True, False):
        h = exports.evidence_html(make_alert(injected=inj), pts(), now=NOW).decode()
        j = json.loads(exports.evidence_json(make_alert(injected=inj), pts(), now=NOW))
        assert ("INJECTED TEST DATA" in h) is inj
        assert j["injected_test_data"] is inj and (j["injected_notice"] is not None) is inj
    assert "injected test data" in exports.summary_report_md([make_alert(injected=True)], now=NOW)


def test_attribution_everywhere():
    for blob in (
        exports.evidence_html(make_alert(), pts(), now=NOW).decode(),
        exports.evidence_json(make_alert(), pts(), now=NOW).decode(),
        exports.summary_report_html([make_alert()], now=NOW).decode(),
        exports.summary_report_md([make_alert()], now=NOW),
    ):
        assert "Open Database License (ODbL) 1.0" in blob and "adsb.lol" in blob
        assert "not a certified surveillance system" in blob


def test_escaping():
    alert = make_alert(callsign=HOSTILE, explanation=HOSTILE, benign_causes=[HOSTILE],
                       evidence=[{"field": HOSTILE, "observed": HOSTILE, "threshold": HOSTILE, "ts": T0}])
    points = [(ts, replace(s, callsign=HOSTILE, source="<b>")) for ts, s in pts()]
    out = exports.evidence_html(alert, points, now=NOW).decode()
    assert "<script" not in out.lower() and "<b>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&amp;" in out and "\U0001F6EB" in out
    json.loads(exports.evidence_json(alert, points, now=NOW))  # still valid JSON


def test_summary_escapes_and_counts():
    old = make_alert(id=1, last_ts=T0 - 2 * 86400, first_ts=T0 - 2 * 86400)
    alerts = [make_alert(), make_alert(id=2, injected=True, callsign=HOSTILE), old]
    val = [ValidationResult("spoof<script>", ("GT-001",), ("GT-001",), True, 12.0)]
    fp = [RecordingFP("a&b.gz", 1.5, 10, {("GT-001", "high"): 1}, 1)]
    gap = [ValidationResult("gap1", (), (), True, None, gap=True)]
    h = exports.summary_report_html(alerts, val, fp, gap, now=NOW).decode()
    assert "<script" not in h.lower() and "spoof&lt;script&gt;" in h and "a&amp;b.gz" in h
    assert "2 alerts: 1 from real/recorded traffic, 1 from" in h  # the 2-day-old alert is excluded
    md = exports.summary_report_md(alerts, val, fp, gap, now=NOW)
    assert "| GT-001 |" in md and "GAP HOLDS" in md and "PASS" in md and "0.67" in md


def test_deterministic():
    a, p = make_alert(), pts()
    assert exports.evidence_html(a, p, now=NOW) == exports.evidence_html(a, p, now=NOW)
    assert exports.evidence_json(a, p, now=NOW) == exports.evidence_json(a, p, now=NOW)
    assert exports.summary_report_md([a], now=NOW) == exports.summary_report_md([a], now=NOW)
    assert exports.summary_report_html([a], now=NOW) == exports.summary_report_html([a], now=NOW)


def test_no_points_and_unknown_detection():
    out = exports.evidence_html(make_alert(detection_id="GT-999", evidence=[]), [], now=NOW).decode()
    assert "No track points" in out and "GT-999" in out
