"""From snapshots to alert rows: shared by live detection, the demo fixture build and the tests.

``alert_rows`` turns an ``Analysis`` into rows for ``db.upsert_alert``. A row is labelled
``injected`` when any state behind the finding is injected test data (the address's states in
the finding's time range, or, for a fleet finding, any member's).
"""
from __future__ import annotations

import sqlite3
from typing import Sequence

from ghosttrack import db
from ghosttrack.detections import Analysis, Context, analyse
from ghosttrack.detections.base import Finding
from ghosttrack.model import Snapshot
from ghosttrack.tracker import Track

# Position and snapshot times differ by up to the stale-position limit; look this far around a
# finding for injected states.
INJECTED_SLACK_S = 65.0


def _injected(finding: Finding, tracks: dict[str, Track]) -> bool:
    members = [finding.icao] if finding.icao else []
    if not members:  # fleet finding: its member addresses are carried as evidence
        for e in finding.evidence:
            if e.field == "addresses" and isinstance(e.observed, (list, tuple)):
                members = [str(a) for a in e.observed]
    for icao in members:
        track = tracks.get(icao)
        if track is None:
            continue
        for p in track.points:
            if p.state.injected and finding.ts_first - INJECTED_SLACK_S <= p.ts <= finding.ts_last + INJECTED_SLACK_S:
                return True
    return False


def alert_rows(analysis: Analysis, source: str) -> list[dict]:
    rows = []
    tracks = analysis.tracker.tracks
    for f in analysis.findings:
        track = tracks.get(f.icao) if f.icao else None
        rows.append({
            "source": source,
            "detection_id": f.detection_id,
            "severity": f.severity,
            "icao": f.icao,
            "callsign": track.callsign if track else None,
            "first_ts": f.ts_first,
            "last_ts": f.ts_last,
            "injected": _injected(f, tracks),
            "confidence": f.confidence,
            "explanation": f.explanation,
            "benign_causes": list(f.benign_causes),
            "evidence": [{"field": e.field, "observed": e.observed, "threshold": e.threshold, "ts": e.ts}
                         for e in f.evidence],
        })
    return rows


def detect_window(
    conn: sqlite3.Connection, source: str, region: str, now_ts: float, ctx: Context | None = None
) -> dict[str, int]:
    """Run every detection over the rolling window ending at ``now_ts`` and upsert the alerts.

    Safe to repeat: alerts are fingerprinted, so overlapping windows never duplicate one.
    """
    snaps: Sequence[Snapshot] = db.window_snapshots(conn, source, region, now_ts)
    counts = {"inserted": 0, "updated": 0}
    if not snaps:
        return counts
    for row in alert_rows(analyse(snaps, ctx), source):
        counts[db.upsert_alert(conn, row, commit=False)] += 1
    conn.commit()
    return counts
