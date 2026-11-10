"""Each detection fires on its crafted scenario and stays silent on matching benign data,
plus the Review Focus tests (missing fields, MLAT tolerance, ground traffic, segment gaps)."""
import copy
import dataclasses
import gzip
import json
from pathlib import Path

import pytest
from factories import T0, flight, snapshots, st

from ghosttrack.detections import ALL_IDS, Context, analyse, load_catalog
from ghosttrack.geo import destination
from ghosttrack.model import Snapshot
from ghosttrack.tracker import build_tracks

REC_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"


def run(*flights, ctx=None):
    return analyse(snapshots(*flights), ctx)


def ev(an, det_id, icao="a061d9"):
    for e in an.evaluations:
        if e.detection_id == det_id and e.icao == icao:
            return e
    raise AssertionError(f"no evaluation {det_id} {icao}")


def fired(an, det_id):
    return [f for f in an.findings if f.detection_id == det_id]


def shift(fl, from_i, nm, bearing=0.0):
    out = []
    for i, (ts, s) in enumerate(fl):
        if i >= from_i:
            lat, lon = destination(s.lat, s.lon, bearing, nm)
            s = dataclasses.replace(s, lat=lat, lon=lon)
        out.append((ts, s))
    return out


# ------------------------------------------------------------------ catalog


def test_catalog_documents_every_detection():
    cat = load_catalog()
    ids = [d["id"] for d in cat["detections"]]
    assert sorted(ids) == list(ALL_IDS) == [f"GT-{i:03d}" for i in range(1, 11)]
    keys = {"id", "title", "severity", "rationale", "inputs", "thresholds", "false_positive_notes",
            "benign_causes", "references"}
    for d in cat["detections"]:
        assert keys <= set(d), d["id"]
        assert d["severity"] in ("info", "low", "medium", "high")
        assert d["benign_causes"] and d["references"]
    for name, c in cat["categories"].items():
        assert {"max_gs_kt", "max_vrate_fpm", "max_load_g", "max_accel_g", "rationale"} <= set(c), name


def test_thresholds_come_from_catalog():
    fl = flight(gs=700.0)  # under the A3 ceiling (800) ...
    assert not fired(run(fl), "GT-001")
    cat = copy.deepcopy(load_catalog())
    cat["categories"]["A3"]["max_gs_kt"] = 600  # ... but over an edited one
    assert fired(run(fl, ctx=Context(catalog=cat)), "GT-001")


def test_finding_shape():
    an = run(flight(gs=1200.0))
    f = fired(an, "GT-001")[0]
    d = f.to_dict()
    assert set(d) == {"detection_id", "icao", "ts_first", "ts_last", "severity", "confidence", "evidence",
                      "explanation", "benign_causes"}
    assert d["evidence"][0] == {"field": "gs_kt", "observed": 1200.0, "threshold": 800.0, "ts": T0}
    assert 0 < f.confidence <= 1 and f.benign_causes and f.ts_first <= f.ts_last


# ------------------------------------------------------------------ GT-001


def test_gt001_fires_on_impossible_speed_and_is_silent_on_airliner():
    assert fired(run(flight(gs=1200.0)), "GT-001")
    assert fired(run(flight(gs=350.0, category="A7")), "GT-001")  # helicopter at 350 kt
    an = run(flight(gs=480.0))
    assert not fired(an, "GT-001") and ev(an, "GT-001").status == "clean"
    assert not fired(run(flight(gs=140.0, category="A7")), "GT-001")


def test_gt001_needs_two_samples():
    def spike(i, f):
        if i == 5:
            f["gs_kt"] = 1500.0
    assert not fired(run(flight(per_point=spike)), "GT-001")


# ------------------------------------------------------------------ GT-002


def test_gt002_fires_on_impossible_vertical_rate():
    an = run(flight(alt=20000.0, vrate=-30000.0, gs=300.0, n=8))
    f = fired(an, "GT-002")
    assert f and any(e.field == "implied_vertical_rate_fpm" for e in f[0].evidence)


def test_gt002_silent_on_normal_climb_and_single_altitude_glitch():
    assert not fired(run(flight(alt=5000.0, vrate=3500.0, gs=250.0)), "GT-002")

    def glitch(i, f):
        if i == 7:
            f["alt"] = f["alt"] + 4000.0  # one corrupt altitude reply, then back to normal
    assert not fired(run(flight(alt=30000.0, per_point=glitch)), "GT-002")


# ------------------------------------------------------------------ GT-003


def test_gt003_fires_on_impossible_turns():
    def zigzag(i, f):
        f["track_deg"] = 0.0 if i % 2 else 120.0
    assert fired(run(flight(gs=450.0, per_point=zigzag)), "GT-003")


def test_gt003_fires_on_impossible_acceleration():
    def surge(i, f):
        f["gs_kt"] = 300.0 if i % 2 else 600.0  # +-300 kt in 5 s, about 3 g
    assert fired(run(flight(per_point=surge)), "GT-003")


def test_gt003_silent_on_standard_rate_turn_and_helicopter_orbit():
    assert not fired(run(flight(gs=250.0, turn_dps=3.0, n=40)), "GT-003")
    an = run(flight(gs=70.0, turn_dps=20.0, category="A7", n=40))
    assert not fired(an, "GT-003")
    assert ev(an, "GT-003").not_evaluated["no_turn_check_for_category"] > 0


# ------------------------------------------------------------------ GT-004


def test_gt004_fires_on_mid_flight_offset():
    an = run(shift(flight(), 10, 20.0, bearing=180.0))
    f = fired(an, "GT-004")
    assert len(f) == 1 and f[0].severity == "high"
    assert f[0].evidence[0].field == "implied_speed_kt" and f[0].evidence[0].observed > 10000


def test_gt004_silent_on_smooth_track_and_across_antimeridian():
    assert not fired(run(flight()), "GT-004")
    an = run(flight(lat=10.0, lon=179.8, track=90.0, gs=480.0, n=30))
    lons = [p.state.lon for p in an.tracker.tracks["a061d9"].points]
    assert min(lons) < -179 and max(lons) > 179  # really crossed
    assert not fired(an, "GT-004") and not fired(an, "GT-001")


def test_position_time_uses_seen_pos():
    # A helicopter at 150 kt. Its positions were taken 20 s apart, but the snapshots that carry
    # them are only 5 s apart (the first position was 15 s old). Snapshot-time math would give
    # 600 kt and fire; position-time math gives 150 kt.
    a = st(category="A7", gs_kt=150.0, alt=1000.0, seen_pos_s=15.0)
    lat2, lon2 = destination(a.lat, a.lon, 90.0, 150.0 * 20 / 3600)
    b = dataclasses.replace(a, lat=lat2, lon=lon2, seen_pos_s=0.0)
    an = analyse([Snapshot(T0 + 15, [a]), Snapshot(T0 + 20, [b])])
    assert ev(an, "GT-004").samples == 1 and not fired(an, "GT-004")


# ------------------------------------------------------------------ GT-005


def test_gt005_fires_on_interleaved_duplicate_address():
    real = flight(n=20)
    ghost = flight(n=20, lat=34.9, lon=-117.0, t0=T0 + 2.5)  # same address about 90 NM away
    an = run(real, ghost)
    f = fired(an, "GT-005")
    assert len(f) == 1 and f[0].icao == "a061d9"


def test_gt005_fires_on_same_snapshot_duplicate():
    a = st(seen_pos_s=0.2)
    b = st(lat=35.5, lon=-116.0, seen_pos_s=0.3)
    an = analyse([Snapshot(T0, [a, b])])
    assert fired(an, "GT-005")


def test_gt005_silent_on_single_aircraft_and_on_one_way_offset():
    assert not fired(run(flight()), "GT-005")
    an = run(shift(flight(), 10, 20.0))
    assert not fired(an, "GT-005") and fired(an, "GT-004")


# ------------------------------------------------------------------ GT-006


@pytest.mark.parametrize("icao_hex,reg,flag", [
    ("a061d9", "N12345", False),
    ("a061da", "N12345", True),   # N-number converts to a different address
    ("3c70c2", "N12345", True),   # German address, US registration
    ("3c70c2", "D-ALFB", False),
    ("a061d9", "D-ALFB", True),   # US address, German nationality mark
    ("06a1c9", "A7-CGD", False),
    ("c00123", "C-FABC", False),
])
def test_gt006_allocation(icao_hex, reg, flag):
    an = run(flight(icao=icao_hex, registration=reg, n=3))
    assert bool(fired(an, "GT-006")) is flag
    assert ev(an, "GT-006", icao_hex).status == ("flagged" if flag else "clean")


@pytest.mark.parametrize("icao_hex,reg,reason", [
    # Taiwan registrations ("B-" + 5 digits) use addresses in an ICAO-administered block (Table 9-1
    # "Other allocations"), which has no State.
    ("899100", "B-18201", "icao_special_block"),
    ("c40123", "D-ALFB", "address_not_in_table"),  # unallocated, just above Canada's block
    ("3c70c2", "ZZ-ABC", "unknown_registration_prefix"),
    ("f00010", "D-ALFB", "icao_special_block"),
    ("3c70c2", None, "missing_registration"),
])
def test_gt006_is_conservative(icao_hex, reg, reason):
    an = run(flight(icao=icao_hex, registration=reg, n=3))
    e = ev(an, "GT-006", icao_hex)
    assert e.status == "not_evaluated" and e.not_evaluated[reason] == 1 and not e.findings


# ------------------------------------------------------------------ GT-007


def test_gt007_fires_on_altitude_divergence():
    an = run(flight(alt=10000.0, alt_geom_ft=15000.0, n=10))
    f = fired(an, "GT-007")
    assert f and f[0].evidence[0].field == "alt_geom_minus_baro_ft"


def test_gt007_silent_within_pressure_and_temperature_band():
    assert not fired(run(flight(alt=39000.0, alt_geom_ft=40400.0)), "GT-007")
    assert not fired(run(flight(alt=3000.0, alt_geom_ft=1500.0)), "GT-007")

    def startup(i, f):  # GNSS height wrong for two reports while the receiver acquires
        if i < 2:
            f["alt_geom_ft"], f["nic"] = f["alt"] - 2500.0, 1
    assert not fired(run(flight(alt=1100.0, per_point=startup)), "GT-007")


# ------------------------------------------------------------------ GT-008


def test_gt008_fires_on_steady_emergency_squawk():
    an = run(flight(squawk="7700", n=40))
    f = fired(an, "GT-008")
    assert len(f) == 1 and f[0].severity == "info" and f[0].confidence <= 0.3


def test_gt008_silent_when_behaviour_matches():
    an = run(flight(squawk="7700", vrate=-3000.0, alt=30000.0, n=40))  # emergency descent
    assert not fired(an, "GT-008") and ev(an, "GT-008").status == "clean"
    assert not fired(run(flight(squawk="1200", n=40)), "GT-008")


def test_gt008_short_code_not_evaluated():
    def blip(i, f):
        f["squawk"] = "7600" if 3 <= i <= 5 else "1200"
    e = ev(run(flight(per_point=blip, n=40)), "GT-008")
    assert not e.findings and e.not_evaluated["emergency_code_too_short"] == 1


# ------------------------------------------------------------------ GT-009


def _background():
    return [flight(icao=f"a0{i:04x}", n=80, lat=33.0 + i * 0.3, lon=-119.0, track=45.0 * i) for i in range(4)]


def test_gt009_fires_on_ghost_flood():
    ghosts = []
    for k in range(8):
        lat, lon = destination(34.0, -118.0, 90.0, 0.8 * k)
        ghosts.append(flight(icao=f"c1{k:04x}", t0=T0 + 300 + 3 * k, n=20, lat=lat, lon=lon, gs=420.0,
                             track=270.0, registration=None))
    an = run(*_background(), *ghosts)
    f = fired(an, "GT-009")
    assert len(f) == 1 and f[0].icao is None
    assert f[0].evidence[0].observed == 8 and len(f[0].evidence[1].observed) == 8


def test_gt009_silent_on_dispersed_arrivals_and_at_startup():
    arrivals = [flight(icao=f"c2{k:04x}", t0=T0 + 300 + 3 * k, n=20, lat=34.0 + 0.05 * k, lon=-118.0,
                       gs=250.0 + 40 * k, track=30.0 * k) for k in range(8)]
    assert not fired(run(*_background(), *arrivals), "GT-009")
    # The same formation present from the first snapshot is not "new".
    formation = [flight(icao=f"c3{k:04x}", n=20, lat=34.0, lon=-118.0 + 0.01 * k, gs=420.0, track=270.0)
                 for k in range(8)]
    an = run(*formation)
    assert not fired(an, "GT-009") and ev(an, "GT-009", None).not_evaluated["present_at_start"] == 8


# ------------------------------------------------------------------ GT-010


def test_gt010_fires_on_integrity_drop_with_smooth_track():
    def drop(i, f):
        if i >= 10:
            f["nic"], f["nac_p"] = 0, 0
    f = fired(run(flight(per_point=drop)), "GT-010")
    assert len(f) == 1 and f[0].severity == "low"


def test_gt010_silent_on_steady_integrity_and_not_evaluated_without_it():
    assert not fired(run(flight()), "GT-010")
    e = ev(run(flight(nic=None, nac_p=None)), "GT-010")
    assert e.status == "not_evaluated" and e.not_evaluated["missing_integrity"] > 0


# ------------------------------------------------------------------ Review Focus


def test_missing_fields_not_evaluated():
    bare = dict(gs=None, track=None, alt=None, alt_geom_ft=None, vrate=None, geom_rate_fpm=None, squawk=None,
                nic=None, nac_p=None, registration=None)
    an = run(flight(**bare))
    for det in ("GT-001", "GT-002", "GT-003", "GT-006", "GT-007", "GT-008", "GT-010"):
        e = ev(an, det)
        assert e.status == "not_evaluated" and not e.findings, det
        assert sum(e.not_evaluated.values()) > 0, det

    # alt_baro "ground" (on_ground) with an absurd speed: excluded, never flagged.
    e = ev(run(flight(gs=900.0, alt=None, on_ground=True)), "GT-001")
    assert e.status == "not_evaluated" and e.not_evaluated["on_ground"] == 20

    # Stale positions (seen_pos > 60 s) never produce kinematic alerts, even with huge jumps.
    def jumpy(i, f):
        f["lat"] = 33.0 + (i % 2) * 0.5
    an = run(flight(seen_pos_s=75.0, gs=2000.0, per_point=jumpy))
    for det in ("GT-001", "GT-002", "GT-003", "GT-004", "GT-005", "GT-007", "GT-010"):
        e = ev(an, det)
        assert not e.findings and e.status == "not_evaluated", det
    assert ev(an, "GT-004").not_evaluated["stale_position"] == 20

    # Unknown position age: not used for jumps.
    e = ev(run(shift(flight(seen_pos_s=None), 10, 20.0)), "GT-004")
    assert not e.findings and e.not_evaluated["unknown_position_age"] == 20


def test_mlat_tolerance():
    # Jitter: every other position 1.5 NM off the line (typical of a poor MLAT geometry).
    def jitter(i, f):
        if i % 2:
            f["lat"], f["lon"] = destination(f["lat"], f["lon"], 0.0, 1.5)
    adsb = run(flight(per_point=jitter, nac_p=9))
    mlat = run(flight(per_point=jitter, source="mlat", nac_p=None))
    assert fired(adsb, "GT-004")  # from a NACp 9 ADS-B source this is a real anomaly
    assert not fired(mlat, "GT-004")
    # Speed ceilings get the derived-source factor (A3: 800 kt, MLAT 1,200 kt).
    assert fired(run(flight(gs=1000.0)), "GT-001")
    assert not fired(run(flight(gs=1000.0, source="mlat")), "GT-001")
    # A real teleport still fires from MLAT, at reduced confidence.
    f = fired(run(shift(flight(source="mlat", nac_p=None), 10, 20.0)), "GT-004")
    g = fired(run(shift(flight(), 10, 20.0)), "GT-004")
    assert f and g and f[0].confidence < g[0].confidence


def test_ground_vehicles_excluded():
    def wild(i, f):
        f["lat"] = 33.94 + (i % 2) * 0.05  # 3 NM hops every 5 s
    for kw in (dict(category="C2"), dict(category="C1"), dict(category="A3", on_ground=True)):
        an = run(flight(gs=300.0, alt=None if kw.get("on_ground") else 0.0, per_point=wild,
                        **{"track": 0.0, **kw}))
        for det in ("GT-001", "GT-002", "GT-003", "GT-004", "GT-007"):
            e = ev(an, det)
            assert not e.findings and e.status == "not_evaluated", (kw, det)
        reasons = ev(an, "GT-001").not_evaluated
        assert reasons["on_ground" if kw.get("on_ground") else "ground_vehicle"] == 20


def test_gap_starts_new_segment():
    first = flight(n=10)
    # Same address reappears 301 s later, 100 NM away: new segment, not a teleport.
    last_ts = first[-1][0]
    lat, lon = destination(first[-1][1].lat, first[-1][1].lon, 0.0, 100.0)
    second = flight(n=10, t0=last_ts + 301.0, lat=lat, lon=lon)
    an = run(first, second)
    assert len(an.tracker.tracks["a061d9"].segments) == 2
    assert not fired(an, "GT-004") and not fired(an, "GT-005")
    # Control: the same 100 NM jump after only a 200 s gap stays in one segment and fires.
    second = flight(n=10, t0=last_ts + 200.0, lat=lat, lon=lon)
    an = run(first, second)
    assert len(an.tracker.tracks["a061d9"].segments) == 1
    assert fired(an, "GT-004")


def test_repeated_positions_are_skipped():
    a = st(seen_pos_s=0.5)
    b = dataclasses.replace(a, seen_pos_s=5.5, gs_kt=455.0)  # same position, 5 s later
    tr = build_tracks([Snapshot(T0, [a]), Snapshot(T0 + 5, [b])])
    pts = tr.tracks["a061d9"].points
    assert [p.repeat_pos for p in pts] == [False, True]
    assert len(tr.tracks["a061d9"].segments[0].position_points()) == 1


# ------------------------------------------------------------------ committed recordings


@pytest.mark.parametrize("path", sorted(REC_DIR.glob("*.jsonl.gz")), ids=lambda p: p.name)
def test_recordings_are_evaluated(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        snaps = [Snapshot.from_dict(json.loads(line)) for line in fh]
    an = analyse(snaps)
    for det_id, s in an.summary.items():
        assert s.samples > 0, det_id
    # Raw alert counts feed the false-positive budget (Task 4/5); see DECISIONS.md "Task 3".
    assert sum(an.counts().values()) <= 10


def test_points_without_category_inherit_the_track_category():
    # Category is missing from some real reports. A surface vehicle stays excluded ...
    def drop_cat(i, f):
        if i % 3 == 0:
            f["category"] = None

    def wild(i, f):
        drop_cat(i, f)
        f["lat"] = 33.94 + (i % 2) * 0.05
    an = run(flight(category="C2", alt=0.0, gs=300.0, per_point=wild))
    for det in ("GT-001", "GT-004"):
        assert ev(an, det).status == "not_evaluated", det
    # ... and a helicopter keeps rotorcraft limits (350 kt is impossible for A7, not for "unknown").
    assert fired(run(flight(category="A7", gs=350.0, per_point=drop_cat)), "GT-001")
