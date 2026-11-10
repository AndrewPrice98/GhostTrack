"""Attack scenarios: determinism, labelling of injected data, and detection on recorded traffic."""
from __future__ import annotations

import copy
import gzip
import json
from dataclasses import replace

import pytest

from ghosttrack import validate as V
from ghosttrack.__main__ import main
from ghosttrack.detections import Context, load_catalog
from ghosttrack.geo import haversine_nm
from ghosttrack.model import Snapshot, State
from ghosttrack.scenarios import SCENARIOS, SYNTH_FIRST, SYNTH_LAST, scenario_by_id
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording


@pytest.fixture(scope="module")
def clean():
    return load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)


@pytest.fixture(scope="module")
def results(clean):
    return V.run_validation(clean)


def test_there_are_seven_documented_scenarios():
    assert [s.id for s in SCENARIOS] == [
        "single_ghost", "ghost_flood", "icao_hijack", "position_offset", "teleport",
        "altitude_spoof", "registration_mismatch",
    ]
    for s in SCENARIOS:
        assert s.simulates and s.reference and s.expected
        assert len(s.simulates) > 40 and len(s.reference) > 20


def test_all_scenarios_pass(results):
    assert len(results) == 7
    for r in results:
        assert r.passed, r
        assert set(r.expected) <= set(r.fired)
        assert r.time_to_detect_s is not None and r.time_to_detect_s >= 0
        assert r.collateral == 0, f"{r.scenario} caused findings on untouched aircraft"


def test_validation_can_fail(clean):
    """Loosening a threshold must turn the matching scenario red (the check is not vacuous)."""
    cat = copy.deepcopy(load_catalog())
    cat["by_id"]["GT-007"]["thresholds"]["pressure_band_ft"] = 50000
    r = V.run_scenario(scenario_by_id("altitude_spoof"), clean, Context(catalog=cat))
    assert not r.passed and r.time_to_detect_s is None


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_is_deterministic(scenario, clean):
    a, b = scenario.apply(clean), scenario.apply(clean)
    assert [s.to_dict() for s in a.snapshots] == [s.to_dict() for s in b.snapshots]
    assert (a.inject_ts, a.addresses, a.injected_states) == (b.inject_ts, b.addresses, b.injected_states)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_every_injected_state_is_labelled_and_nothing_else_changes(scenario, clean):
    run = scenario.apply(clean)
    assert len(run.snapshots) == len(clean)
    original = {(s.ts, a) for s in clean for a in s.aircraft}
    assert not any(a.injected for s in clean for a in s.aircraft)
    new_states = 0
    for orig, out in zip(clean, run.snapshots):
        assert orig.ts == out.ts
        for a in out.aircraft:
            if (out.ts, a) in original:
                assert not a.injected
            else:
                new_states += 1
                assert a.injected, "a state not in the recording must be labelled injected"
        assert {a.icao for a in orig.aircraft} <= {a.icao for a in out.aircraft}
    assert new_states == run.injected_states > 0


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_synthetic_addresses_are_in_range_and_never_collide(scenario, clean):
    recorded = {a.icao for s in clean for a in s.aircraft}
    run = scenario.apply(clean)
    if scenario.id in ("icao_hijack", "position_offset"):
        assert run.addresses <= recorded  # deliberately real addresses
        return
    for icao in run.addresses:
        assert SYNTH_FIRST <= int(icao, 16) <= SYNTH_LAST
        assert icao not in recorded


def test_recorded_addresses_avoid_the_synthetic_range():
    for path in V.manifest_recordings():
        for snap in load_recording(path):
            for a in snap.aircraft:
                assert not SYNTH_FIRST <= int(a.icao, 16) <= SYNTH_LAST, (path.name, a.icao)


def test_ghost_flood_is_thirty_aircraft(clean):
    run = scenario_by_id("ghost_flood").apply(clean)
    assert len(run.addresses) == 30
    snap = next(s for s in run.snapshots if s.ts >= run.inject_ts)
    assert sum(1 for a in snap.aircraft if a.icao in run.addresses and a.injected) == 30


def test_hijack_alternates_a_real_address(clean):
    run = scenario_by_id("icao_hijack").apply(clean)
    (target,) = run.addresses
    seen = [
        (a.lat, a.lon, a.injected)
        for s in run.snapshots if s.ts >= run.inject_ts
        for a in s.aircraft if a.icao == target
    ]
    flags = [p[2] for p in seen[:9]]
    assert flags == [True, False, True, False, True, False, True, False, True]  # clone first, then alternating
    clone = next(p for p in seen if p[2])
    real = next(p for p in seen if not p[2])
    assert haversine_nm(clone[0], clone[1], real[0], real[1]) > 30


def test_position_offset_is_20_nm(clean):
    run = scenario_by_id("position_offset").apply(clean)
    (target,) = run.addresses
    shifted = 0
    for orig, out in zip(clean, run.snapshots):
        o = [a for a in orig.aircraft if a.icao == target]
        n = [a for a in out.aircraft if a.icao == target]
        for a, b in zip(o, n):
            if b.injected:
                shifted += 1
                assert haversine_nm(a.lat, a.lon, b.lat, b.lon) == pytest.approx(20.0, abs=0.05)
    assert shifted > 10


def test_altitude_spoof_beyond_band(clean):
    cat = load_catalog()["by_id"]["GT-007"]["thresholds"]
    run = scenario_by_id("altitude_spoof").apply(clean)
    n = 0
    for s in run.snapshots:
        for a in s.aircraft:
            if a.injected:
                n += 1
                tol = cat["pressure_band_ft"] + cat["temperature_fraction"] * a.alt_baro_ft + cat["geoid_ft"]
                assert abs(a.alt_geom_ft - a.alt_baro_ft) > tol
    assert n >= 3


def test_registration_mismatch_fires_for_both_aircraft(clean):
    run = scenario_by_id("registration_mismatch").apply(clean)
    an = V.analyse(run.snapshots)
    flagged = {f.icao for f in an.findings if f.detection_id == "GT-006"}
    assert flagged == set(run.addresses) and len(flagged) == 2


# ------------------------------------------------------------------ model and CLI


def test_state_injected_defaults_false_and_round_trips(clean):
    d = clean[0].aircraft[0].to_dict()
    d.pop("injected")
    assert State.from_dict(d).injected is False  # old recordings lack the key
    s = replace(clean[0].aircraft[0], injected=True)
    assert State.from_dict(s.to_dict()).injected is True
    snap = Snapshot(1.0, [s])
    assert Snapshot.from_dict(json.loads(json.dumps(snap.to_dict()))) == snap


def test_cli_validate_prints_tables_and_exits_zero(capsys):
    assert main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "single_ghost" in out and "PASS" in out and "socal-a.jsonl.gz" in out
    assert "time to detect" in out and "per hour" in out


def test_cli_validate_exits_one_on_failure(monkeypatch, capsys):
    bad = V.ValidationResult("x", ("GT-001",), (), False, None)
    monkeypatch.setattr("ghosttrack.__main__.run_validation", lambda: [bad])
    assert main(["validate"]) == 1
    assert "FAIL" in capsys.readouterr().out


# ------------------------------------------------------------------ false positives


def test_false_positive_budget():
    """Findings of severity >= medium on every clean recording stay within the catalog budget."""
    cat = load_catalog()["validation"]
    budget = float(cat["fp_budget_per_hour"])
    assert budget >= float(cat["budget_floor_per_hour"])
    fps = V.measure_false_positives()
    assert len(fps) >= 3
    for r in fps:
        assert r.hours > 0.2
        assert r.per_hour <= budget, f"{r.recording}: {r.counted} findings, {r.per_hour:.2f}/h > {budget}/h: {r.counts}"


def test_budget_rule_and_counting(tmp_path, clean):
    cat = load_catalog()
    fp = V.RecordingFP("x", 0.5, 10, {("GT-001", "medium"): 2, ("GT-008", "info"): 9}, 2)
    assert fp.per_hour == 4.0
    assert V.fp_budget([fp], cat) == 6.0  # 4.0 x 1.5
    assert V.fp_budget([], cat) == float(cat["validation"]["budget_floor_per_hour"])
    assert V.budget_exceeded([fp], cat) == [fp]
    # a recording with an injected ghost counts medium+ findings and would blow the budget
    run = scenario_by_id("single_ghost").apply(clean)
    path = tmp_path / "dirty.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for s in run.snapshots:
            fh.write(json.dumps(s.to_dict()) + "\n")
    dirty = V.measure_recording(path)
    assert dirty.counted >= 1 and dirty.per_hour > float(cat["validation"]["fp_budget_per_hour"])
