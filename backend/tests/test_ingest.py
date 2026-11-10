import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from ghosttrack import upstream
from ghosttrack.model import Snapshot
from ghosttrack.upstream import UpstreamError, normalize_record, parse_response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import record as recmod  # noqa: E402

REC_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"


def rec(**kw):
    base = {"hex": "a1b2c3", "type": "adsb_icao", "flight": "UAL123  ", "lat": 33.9, "lon": -118.4,
            "alt_baro": 12000, "gs": 250.5, "track": 90.0, "baro_rate": -500, "squawk": "1200",
            "category": "A3", "t": "B738", "r": "N12345", "nic": 8, "nac_p": 9, "seen_pos": 0.4,
            "rssi": -12.5}
    base.update(kw)
    return base


def body(*records, now=1794299503501):
    return json.dumps({"ac": list(records), "now": now, "msg": "No error"}).encode()


class FakeResp:
    def __init__(self, data: bytes, headers=None):
        self.data, self.pos, self.headers, self.closed = data, 0, headers or {}, False

    def read(self, n=-1):
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


# --- normalization and units -------------------------------------------------

def test_normalization_and_units():
    snap, stats = parse_response(body(rec()))
    s = snap.aircraft[0]
    assert snap.ts == 1794299503.501  # milliseconds -> seconds
    assert (s.icao, s.callsign, s.lat, s.lon) == ("a1b2c3", "UAL123", 33.9, -118.4)
    assert s.alt_baro_ft == 12000 and s.gs_kt == 250.5 and s.baro_rate_fpm == -500
    assert s.on_ground is False and s.source == "adsb" and s.type_code == "B738"
    assert stats == {"records_in": 1, "records_kept": 1, "dropped_by_reason": {}}


def test_ground_altitude():
    s = parse_response(body(rec(alt_baro="ground", gs=3)))[0].aircraft[0]
    assert s.on_ground is True and s.alt_baro_ft is None


def test_missing_fields_stay_none():
    s = parse_response(body({"hex": "abcdef", "lat": 1, "lon": 2}))[0].aircraft[0]
    assert s.alt_baro_ft is None and s.gs_kt is None and s.track_deg is None
    assert s.callsign is None and s.squawk is None and s.on_ground is False


@pytest.mark.parametrize("extra,expected", [
    ({"type": "adsb_icao"}, "adsb"), ({"type": "adsb_icao_nt"}, "adsb"), ({"type": "adsr_icao"}, "adsb"),
    ({"type": "mlat"}, "mlat"), ({"type": "tisb_icao"}, "tisb"), ({"type": "tisb_other"}, "tisb"),
    ({"type": "mode_s"}, "other"), ({"type": "adsc"}, "other"), ({"type": 5}, "other"),
    ({"type": "adsb_icao", "mlat": ["lat", "lon"]}, "mlat"),
    ({"type": "adsb_icao", "tisb": ["lat"]}, "tisb"),
    ({"type": "adsb_icao", "mlat": ["gs"]}, "adsb"),
])
def test_source_mapping(extra, expected):
    assert normalize_record(rec(**extra), Counter()).source == expected


# --- hostile upstream --------------------------------------------------------

def test_hostile_upstream_records():
    hostile = [
        rec(hex="a00001", lat=900),                 # absurd latitude
        rec(hex="a00002", lon=-1e9),                # absurd longitude
        rec(hex="a00003", gs=1e9),                  # absurd speed -> field cleared, record kept
        rec(hex="a00004", alt_baro=999999),         # absurd altitude -> cleared
        rec(hex="a00005", alt_baro="12000"),        # string altitude
        rec(hex="a00006", lat=float("nan")),        # NaN (json.dumps emits NaN)
        rec(hex="zzzzzz"), rec(hex="12345"), rec(hex=None), rec(hex="~a00007"),
        rec(hex="a00008", lat="33.9"), rec(hex="a00009", lat=True),
        "string", 42, None, [1, 2],
        {"hex": "a0000a", "lat": 1, "lon": 2, "evil": "x" * 10000, "flight": "<script>"},
        rec(hex="a0000b", squawk="9999", category="Z9", t="B738;DROP", r="N 1"),
        rec(hex="a0000c"), rec(hex="a0000c"),       # duplicate
        rec(hex="a0000d", gs=-5, track=720, nic=99, rssi=1e400),
    ]
    snap, stats = parse_response(body(*hostile))
    kept = {a.icao: a for a in snap.aircraft}
    assert stats["records_in"] == len(hostile)
    assert stats["records_kept"] == len(snap.aircraft)
    r = stats["dropped_by_reason"]
    assert r["bad_position"] >= 5 and r["bad_hex"] == 4 and r["not_an_object"] == 4
    assert r["duplicate_icao"] == 1
    assert "a00001" not in kept and "a00002" not in kept and "a00006" not in kept
    assert kept["a00003"].gs_kt is None and kept["a00004"].alt_baro_ft is None
    assert kept["a00005"].alt_baro_ft is None
    assert kept["a0000a"].callsign is None            # '<script>' rejected
    b = kept["a0000b"]
    assert (b.squawk, b.category, b.type_code, b.registration) == (None, None, None, None)
    d = kept["a0000d"]
    assert d.gs_kt is None and d.track_deg is None and d.nic is None and d.rssi is None
    for a in snap.aircraft:  # every kept value is in range
        assert -90 <= a.lat <= 90 and -180 <= a.lon <= 180
        assert a.gs_kt is None or 0 <= a.gs_kt <= 2000


@pytest.mark.parametrize("raw", [b"", b"not json", b"<html>", b"null", b"[]", b'{"ac": 5}', b'{"x":1}',
                                 b"[" * 100000, b"\xff\xfe"])
def test_non_json_or_wrong_shape_raises_upstream_error(raw):
    with pytest.raises(UpstreamError):
        parse_response(raw)


def test_bad_now_falls_back():
    snap, _ = parse_response(body(rec(), now="soon"), fallback_ts=123.0)
    assert snap.ts == 123.0


def test_huge_body_rejected_streaming():
    big = FakeResp(b"x" * (upstream.MAX_BODY_BYTES + 1))  # no Content-Length header
    with pytest.raises(UpstreamError, match="too large"):
        upstream.fetch_snapshot("socal", opener=lambda req, timeout: big)
    assert big.closed


def test_huge_declared_content_length_rejected_without_reading():
    r = FakeResp(b"{}", headers={"Content-Length": str(10**9)})
    with pytest.raises(UpstreamError, match="too large"):
        upstream.fetch_snapshot("socal", opener=lambda req, timeout: r)
    assert r.pos == 0


def test_network_failure_wrapped():
    def boom(req, timeout):
        raise TimeoutError("slow")
    with pytest.raises(UpstreamError):
        upstream.fetch_snapshot("socal", opener=boom)


def test_fetch_uses_fixed_host_and_timeout():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["timeout"], seen["ua"] = req.full_url, timeout, req.get_header("User-agent")
        return FakeResp(body(rec()))

    snap, stats = upstream.fetch_snapshot("socal", opener=opener)
    assert seen["url"] == "https://api.adsb.lol/v2/point/33.9400/-118.4100/60"
    assert seen["timeout"] == 3.0 and "GhostTrack" in seen["ua"]
    assert len(snap.aircraft) == 1


@pytest.mark.parametrize("region", ["../etc", "socal/../../x", "http://evil", "", "SOCAL", None])
def test_region_is_allow_listed(region):
    with pytest.raises((ValueError, TypeError)):
        upstream.build_url(region)


def test_all_regions_within_api_limits():
    for name, (lat, lon, radius) in upstream.REGIONS.items():
        url = upstream.build_url(name)
        assert url.startswith("https://api.adsb.lol/v2/point/") and isinstance(radius, int) and radius <= 250


# --- military exclusion ------------------------------------------------------

def test_military_dbflags_excluded():
    snap, stats = parse_response(body(rec(hex="a11111", dbFlags=1), rec(hex="a22222", dbFlags=2),
                                      rec(hex="a33333", dbFlags=3), rec(hex="a44444")))
    assert {a.icao for a in snap.aircraft} == {"a22222", "a44444"}  # bit 1 only; 2 = interesting
    assert stats["dropped_by_reason"]["military"] == 2


def test_military_dod_block_excluded_when_dbflags_absent():
    recs = [rec(hex=h) for h in ("adffff", "ae0000", "ae1234", "afffff", "b00000")]
    snap, stats = parse_response(body(*recs))
    assert {a.icao for a in snap.aircraft} == {"adffff", "b00000"}
    assert stats["dropped_by_reason"]["military"] == 3


# --- recordings --------------------------------------------------------------

def test_record_roundtrip_and_manifest(tmp_path):
    p = tmp_path / "x.jsonl.gz"
    snaps = [parse_response(body(rec(), rec(hex="b00001"), now=1794299503501 + i * 5000))[0] for i in range(3)]
    with gzip.open(p, "wt", encoding="utf-8") as f:
        for s in snaps:
            f.write(json.dumps(s.to_dict()) + "\n")
    entry = recmod.summarize(p)
    assert entry["snapshots"] == 3 and entry["aircraft"] == 2
    assert entry["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    entries = recmod.update_manifest(tmp_path)
    assert json.loads((tmp_path / "manifest.json").read_text()) == entries
    assert Snapshot.from_dict(json.loads(gzip.open(p, "rt").readline())) == snaps[0]


def test_committed_recordings_match_manifest():
    manifest = json.loads((REC_DIR / "manifest.json").read_text())
    assert len(manifest) >= 3
    on_disk = {p.name for p in REC_DIR.glob("*.jsonl.gz")}
    assert on_disk == {m["name"] for m in manifest}
    for m in manifest:
        path = REC_DIR / m["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == m["sha256"]
        assert recmod.summarize(path) == m
        assert m["snapshots"] > 0 and m["aircraft"] > 0


def test_committed_recordings_are_clean():
    for path in REC_DIR.glob("*.jsonl.gz"):
        last = None
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                snap = Snapshot.from_dict(json.loads(line))
                assert last is None or snap.ts > last
                last = snap.ts
                for a in snap.aircraft:
                    assert not upstream.is_military(a.icao, None)
                    assert -90 <= a.lat <= 90 and -180 <= a.lon <= 180


def test_license_notice_present():
    text = (REC_DIR.parent / "LICENSE-ODbL.md").read_text(encoding="utf-8")
    assert "adsb.lol" in text and "Open Database License" in text


def test_committed_manifest_matches_the_recordings():
    assert recmod.check_manifest(REC_DIR) == []


def test_manifest_check_names_the_problem(tmp_path):
    import shutil
    for p in REC_DIR.glob("*"):
        shutil.copy(p, tmp_path / p.name)
    assert recmod.check_manifest(tmp_path) == []
    extra = tmp_path / "socal-z.jsonl.gz"
    shutil.copy(tmp_path / "socal-a.jsonl.gz", extra)
    assert any("socal-z.jsonl.gz: on disk but not in the manifest" in m for m in recmod.check_manifest(tmp_path))
    extra.unlink()
    (tmp_path / "socal-b.jsonl.gz").write_bytes((tmp_path / "socal-a.jsonl.gz").read_bytes())
    assert any("socal-b.jsonl.gz: manifest entry differs" in m for m in recmod.check_manifest(tmp_path))
    (tmp_path / "manifest.json").write_text("not json")
    assert "unreadable" in recmod.check_manifest(tmp_path)[0]
