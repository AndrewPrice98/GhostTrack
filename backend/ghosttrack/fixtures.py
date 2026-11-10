"""Precomputed API data: validation results and scenario replay frames.

Computing these takes seconds (scenario validation, false-positive measurement over the
recordings, one analysis per scenario), which a serverless cold start must not pay. They are
built offline by ``scripts/build_demo.py`` into ``data/api_fixture.json``, checked in CI, and the
API only reads them. Computing on the fly is a dev/test escape hatch behind
``GHOSTTRACK_COMPUTE_FIXTURES=true``; without it a missing or stale fixture is an error.
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from typing import Any

from ghosttrack import config, demo, exports, validate
from ghosttrack.catalog import load_catalog
from ghosttrack.detections import analyse
from ghosttrack.geo import haversine_nm
from ghosttrack.scenarios import GAP_SCENARIOS, SCENARIOS, scenario_by_id

INJECTED_LABEL = exports.INJECTED_LABEL
FIXTURE_VERSION = 1
REPLAY_MAX_FRAMES = 400
REPLAY_NEARBY_NM = 40.0
REPLAY_NEARBY_MAX = 12

_lock = threading.Lock()


class FixtureUnavailable(RuntimeError):
    """The committed fixture is missing or out of date and computing it is not enabled."""


def scenario_out(s) -> dict:
    return {"id": s.id, "title": s.title, "simulates": s.simulates, "reference": s.reference,
            "expected": list(s.expected), "gap": s.gap, "injected": True, "label": INJECTED_LABEL}


def _title(detection_id: str) -> str:
    return load_catalog()["by_id"].get(detection_id, {}).get("title", detection_id)


def _frame_aircraft(a, injected: bool) -> dict:
    return {"icao": a.icao, "callsign": a.callsign, "lat": round(a.lat, 4), "lon": round(a.lon, 4),
            "alt": a.alt_baro_ft, "gs": a.gs_kt, "track": a.track_deg, "injected": injected,
            "label": INJECTED_LABEL if injected else None}


def compute_replay(name: str) -> dict:
    """Frames for the scenario player: the scenario injected into the first clean recording,
    keeping only the scenario's aircraft and a few neighbours, at most REPLAY_MAX_FRAMES frames."""
    scenario = scenario_by_id(name)
    base = validate.load_recording(validate.RECORDINGS_DIR / validate.DEFAULT_RECORDING)
    run = scenario.apply(base)
    mine = set(run.addresses)
    snaps = [s for s in run.snapshots if s.ts >= run.inject_ts - 120.0]
    stride = max(1, -(-len(snaps) // REPLAY_MAX_FRAMES))
    snaps = snaps[::stride]
    t0 = snaps[0].ts
    frames = []
    for s in snaps:
        ours = [a for a in s.aircraft if a.icao in mine or a.injected]
        pts = [(a.lat, a.lon) for a in ours]
        ranked = sorted(
            ((min((haversine_nm(a.lat, a.lon, la, lo) for la, lo in pts), default=float("inf")), a)
             for a in s.aircraft if a not in ours), key=lambda x: x[0])
        nearby = [a for d, a in ranked[:REPLAY_NEARBY_MAX] if d <= REPLAY_NEARBY_NM]
        frames.append({"t": round(s.ts - t0, 1),
                       "aircraft": [_frame_aircraft(a, True) for a in ours]
                       + [_frame_aircraft(a, bool(a.injected)) for a in nearby]})
    analysis = analyse(run.snapshots)
    events = [{"t": round(f.ts_first - t0, 1), "detection_id": f.detection_id, "title": _title(f.detection_id),
               "severity": f.severity, "icao": f.icao, "confidence": f.confidence, "explanation": f.explanation}
              for f in validate._attributed(analysis.findings, run.addresses)]
    return {"scenario": scenario_out(scenario), "injected": True, "label": INJECTED_LABEL,
            "notice": exports.INJECTED_BANNER, "inject_t": round(run.inject_ts - t0, 1),
            "addresses": sorted(mine), "frames": frames, "events": events}


def _result_json(r: validate.ValidationResult) -> dict:
    return {"scenario": r.scenario, "expected": list(r.expected), "fired": list(r.fired), "passed": r.passed,
            "time_to_detect_s": r.time_to_detect_s, "collateral": r.collateral, "gap": r.gap}


def _fp_json(r: validate.RecordingFP) -> dict:
    return {"recording": r.recording, "hours": r.hours, "tracks": r.tracks, "counted": r.counted,
            "counts": [[d, s, n] for (d, s), n in sorted(r.counts.items())]}


def build() -> dict:
    """Everything the API serves from precomputed data (deterministic)."""
    return {
        "version": FIXTURE_VERSION,
        "inputs_digest": demo.inputs_digest(),
        "validation": {
            "results": [_result_json(r) for r in validate.run_validation()],
            "gaps": [_result_json(r) for r in validate.run_gap_validation()],
            "fps": [_fp_json(r) for r in validate.measure_false_positives()],
        },
        "replays": {s.id: compute_replay(s.id) for s in (*SCENARIOS, *GAP_SCENARIOS)},
    }


def fixture_json(fixture: dict) -> str:
    return json.dumps(fixture, separators=(",", ":"), sort_keys=True) + "\n"


def _compute_enabled() -> bool:
    return config.compute_fixtures()


@lru_cache(maxsize=1)
def _load_committed() -> dict | None:
    try:
        data = json.loads(config.API_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == FIXTURE_VERSION else None


_computed: dict | None = None


def fixture() -> dict:
    """The precomputed data: the committed file when current; otherwise computed once per process
    if ``GHOSTTRACK_COMPUTE_FIXTURES`` is on; otherwise ``FixtureUnavailable``."""
    global _computed
    data = _load_committed()
    if data is not None and data.get("inputs_digest") == demo.inputs_digest():
        return data
    if not _compute_enabled():
        raise FixtureUnavailable("api fixture missing or stale; run scripts/build_demo.py")
    with _lock:
        if _computed is None:
            _computed = json.loads(json.dumps(build()))
        return _computed


def validation_raw() -> dict[str, Any]:
    """The validation objects the exports take: results, gaps and false-positive measurements."""
    v = fixture()["validation"]
    results = lambda rows: [validate.ValidationResult(  # noqa: E731
        r["scenario"], tuple(r["expected"]), tuple(r["fired"]), r["passed"], r["time_to_detect_s"],
        r["collateral"], r["gap"]) for r in rows]
    fps = [validate.RecordingFP(r["recording"], r["hours"], r["tracks"],
                                {(d, s): n for d, s, n in r["counts"]}, r["counted"]) for r in v["fps"]]
    return {"results": results(v["results"]), "gaps": results(v["gaps"]), "fps": fps}


def replay(name: str) -> dict:
    return fixture()["replays"][name]
