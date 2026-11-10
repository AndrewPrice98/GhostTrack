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
    for needle in ("GT-001", "a061d9", "TEST1", "gs_kt", "912.5", "650", "UTC", "Possible benign causes",
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
    assert r"| GT\-001 |" in md and "Not detected (known blind spot)" in md and "PASS" in md and r"0\.67" in md


def test_deterministic():
    a, p = make_alert(), pts()
    assert exports.evidence_html(a, p, now=NOW) == exports.evidence_html(a, p, now=NOW)
    assert exports.evidence_json(a, p, now=NOW) == exports.evidence_json(a, p, now=NOW)
    assert exports.summary_report_md([a], now=NOW) == exports.summary_report_md([a], now=NOW)
    assert exports.summary_report_html([a], now=NOW) == exports.summary_report_html([a], now=NOW)


def test_no_points_and_unknown_detection():
    out = exports.evidence_html(make_alert(detection_id="GT-999", evidence=[]), [], now=NOW).decode()
    assert "No track points" in out and "GT-999" in out


def _run_all_findings():
    from ghosttrack.detections import Context, analyse
    from ghosttrack.scenarios import GAP_SCENARIOS, SCENARIOS
    from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording
    snaps = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    out = []
    for sc in [*SCENARIOS, *GAP_SCENARIOS]:
        out += analyse(sc.apply(snaps).snapshots, Context()).findings
    return out


NO_UNIT = {"new_addresses", "nic", "nac_p"}


def test_units_over_real_emitted_evidence():
    seen = {}
    for f in _run_all_findings():
        for e in f.evidence:
            seen.setdefault(e.field, e)
    assert len(seen) >= 8
    for name, e in seen.items():
        numeric = isinstance(e.observed, (int, float)) and not isinstance(e.observed, bool)
        unit = exports._unit(name)
        if numeric and name not in NO_UNIT:
            assert unit, f"numeric evidence field {name} has no unit"
        if not numeric or name in NO_UNIT:
            assert unit == "", name
    assert exports._unit("gs_kt") == "kt" and exports._unit("icao") == "" and exports._unit("squawk") == ""


def test_markdown_hostile_names():
    names = ["<img src=x onerror=alert(1)>", "[x](javascript:alert(1))", "a`b`c", "p|q|r", "line1\r\nline2", "*_#"]
    val = [ValidationResult(n, ("GT-001",), ("GT-001",), True, 1.0) for n in names]
    fp = [RecordingFP(n, 1.0, 1, {("GT-001", "high"): 1}, 1) for n in names]
    md = exports.summary_report_md([make_alert()], val, fp, [], now=NOW)
    assert "<img" not in md and "&lt;img" in md
    assert "](javascript" not in md and "\\[x\\]\\(javascript" in md
    assert "a\\`b\\`c" in md and "p\\|q\\|r" in md and "\r" not in md
    for line in md.split("\n"):
        if "p\\|q" in line:  # escaped pipes must not add columns
            assert line.replace("\\|", "").count("|") == 6 or line.replace("\\|", "").count("|") == 8


def test_fp_verdicts_and_gap_wording():
    fp = [RecordingFP("ok.gz", 10.0, 5, {}, 0), RecordingFP("bad.gz", 1.0, 5, {("GT-001", "high"): 5}, 5)]
    gaps = [ValidationResult("g1", (), (), True, None, gap=True),
            ValidationResult("g2", (), ("GT-004",), False, None, gap=True)]
    h = exports.summary_report_html([], [], fp, gaps, now=NOW).decode()
    md = exports.summary_report_md([], [], fp, gaps, now=NOW)
    for blob in (h, md):
        assert "within budget" in blob and "over budget" in blob and "Overall" in blob
        assert "Not detected (known blind spot)" in blob and "Now detected" in blob
    assert "**over budget**" in md
    ok = exports.summary_report_md([], [], fp[:1], [], now=NOW)
    assert "**within budget**" in ok and "over budget" not in ok


def test_fallbacks_and_missing_values():
    out = exports.evidence_html(make_alert(benign_causes=[], source=None), [], now=NOW).decode()
    assert "None" not in out and "Source of this alert: unknown" in out
    assert "opendatacommons.org/licenses/odbl/1-0/" in out and "data/LICENSE-ODbL.md" in out
    assert "<li>" in out.split("Possible benign causes")[1]  # catalog fallback
    unknown = exports.summary_report_md([make_alert(detection_id="GT-777")], now=NOW)
    assert "GT\\-777" in unknown and "1 alerts" in unknown


def test_nan_inf_and_bad_timestamps():
    a = make_alert(first_ts=float("nan"), last_ts=1e30, evidence=[
        {"field": "gs_kt", "observed": float("nan"), "threshold": float("inf"), "ts": float("inf")}])
    pts_ = [{"ts": 1e30, "lat": float("nan"), "lon": 1.0}, {"ts": 2.0, "lat": 1.0, "lon": 2.0}]
    out = exports.evidence_html(a, pts_, now=float("nan")).decode()
    assert "n/a" in out and "NaN" not in out
    doc = exports.evidence_json(a, [], now=NOW).decode()

    def boom(c):
        raise ValueError(c)
    json.loads(doc, parse_constant=boom)
    assert exports.evidence_html(make_alert(callsign=chr(0xD800)), [], now=NOW)
    assert exports.evidence_json(make_alert(callsign=chr(0xD800)), [], now=NOW)


def test_injected_title_and_filename():
    inj, real = make_alert(injected=True), make_alert()
    assert "<title>[INJECTED] " in exports.evidence_html(inj, [], now=NOW).decode()
    assert "<title>[INJECTED]" not in exports.evidence_html(real, [], now=NOW).decode()
    assert "injected" in exports.evidence_filename(inj) and "injected" not in exports.evidence_filename(real)
    assert exports.evidence_filename(make_alert(icao="../x/y"), "html").endswith(".html")
    assert "/" not in exports.evidence_filename(make_alert(icao="../x/y"))


def test_hostile_severity_icao_detection_id():
    h = '"><script>x</script>'
    out = exports.evidence_html(make_alert(severity=h, icao=h, detection_id=h), [], now=NOW).decode()
    assert "<script" not in out.lower() and 'class="sev-&quot;&gt;' in out
    title = out.split("<title>")[1].split("</title>")[0]
    assert "<" not in title and '"' not in title
    assert "<script" not in exports.summary_report_html([make_alert(detection_id=h)], now=NOW).decode().lower()


# ------------------------------------------------------------------ uploaded data keeps its provenance


def test_upload_evidence_is_labelled_unverified_and_claims_no_adsb_lol_source():
    alert = make_alert(source="upload", injected=False)
    doc = json.loads(exports.evidence_json(alert, pts(), now=NOW))
    assert doc["origin_label"] == "Uploaded recording (unverified)" and doc["unverified_upload"] is True
    assert doc["origin_notice"] and doc["injected_test_data"] is False
    assert "adsb.lol" not in doc["data_source"] and "ODbL" not in doc["data_source"]
    assert "provenance not known" in doc["data_source"]
    html = exports.evidence_html(alert, pts(), now=NOW).decode()
    assert "UPLOADED RECORDING (UNVERIFIED)" in html and "adsb.lol" not in html
    assert "INJECTED TEST DATA" not in html and "[UPLOADED, UNVERIFIED]" in html


def test_declared_injected_upload_says_as_declared_by_upload():
    alert = make_alert(source="upload", injected=True)
    doc = json.loads(exports.evidence_json(alert, pts(), now=NOW))
    assert doc["origin_label"] == "Injected test data (as declared by upload)"
    assert doc["injected_notice"] == exports.UPLOAD_INJECTED_BANNER and "as declared by upload" in doc["injected_notice"]
    html = exports.evidence_html(alert, pts(), now=NOW).decode()
    assert "INJECTED TEST DATA (as declared by upload)" in html and "adsb.lol" not in html
    assert exports.INJECTED_BANNER not in html  # the unqualified "not real air traffic" claim is not made for an upload


def test_recorded_alerts_are_unchanged_by_the_upload_rules():
    doc = json.loads(exports.evidence_json(make_alert(injected=True), pts(), now=NOW))
    assert doc["origin_label"] == "Injected test data" and doc["unverified_upload"] is False
    assert doc["injected_notice"] == exports.INJECTED_BANNER and "adsb.lol" in doc["data_source"]
    assert json.loads(exports.evidence_json(make_alert(), pts(), now=NOW))["origin_label"] is None


def test_summary_separates_uploads_and_never_counts_them_as_real():
    alerts = [
        make_alert(id=1),                                    # recorded, real
        make_alert(id=2, injected=True),                     # recorded, injected
        make_alert(id=3, source="upload"),                   # an upload claiming nothing
        make_alert(id=4, source="upload", injected=True),    # an upload declaring injected data
        make_alert(id=5, source="upload"),
    ]
    md = exports.summary_report_md(alerts, now=NOW)
    assert "5 alerts: 1 from real/recorded traffic, 1 from **injected test data**" in md
    assert "3 from **uploaded recordings (unverified)**" in md and "never counted as real traffic" in md
    html = exports.summary_report_html(alerts, now=NOW).decode()
    assert "5 alerts: 1 from real/recorded traffic, 1 from" in html and "3 from <strong>uploaded recordings (unverified)" in html
    only_uploads = exports.summary_report_md([make_alert(source="upload")], now=NOW)
    assert "1 alerts: 0 from real/recorded traffic" in only_uploads


def test_evidence_filename_marks_uploads():
    assert exports.evidence_filename(make_alert(source="upload")).endswith("-upload.json")
    assert "-injected" in exports.evidence_filename(make_alert(source="upload", injected=True))
