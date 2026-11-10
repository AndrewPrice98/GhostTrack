"""Recording uploads: byte-level checks, per-line validation and detection.

A recording is JSON Lines (plain or gzip), one snapshot per line in the format GhostTrack's own
recordings use: ``{"ts": <epoch s>, "aircraft": [{"icao": "a061d9", "lat": ..., ...}, ...]}``.

Everything in the file is untrusted. Every aircraft record is converted to the shape adsb.lol
returns and passed through ``upstream.normalize_record``, so the same per-field validation,
clamping and military exclusion apply as for the live feed. A line that is not valid is counted
and skipped; a file with no valid snapshot is refused. The only field taken from the file
beyond the upstream rules is a boolean ``injected``, which lets a recording exported with
injected test data keep its label (it can only ever add the label, never remove one).
"""
from __future__ import annotations

import gzip
import json
import math
import sqlite3
import zlib
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO

from ghosttrack import db
from ghosttrack.detections import analyse
from ghosttrack.model import Snapshot, State
from ghosttrack.pipeline import alert_rows
from ghosttrack.upstream import normalize_record

MAX_LINE_CHARS = 1_000_000
MAX_SNAPSHOTS = 3000
MAX_AIRCRAFT_PER_SNAPSHOT = 1500
MAX_STATES = 300_000  # aircraft records kept, bounds the detection work
MAX_TS = 4.1e9
TRACK_POINTS_PER_ALERT = 300
TRACK_PAD_S = 120.0
GZIP_MAGIC = b"\x1f\x8b"


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def decode_upload(data: bytes, max_decompressed: int) -> str:
    """Bytes of an upload to text. Gzip is detected by its magic bytes and inflated with a hard
    cap, so a small bomb is refused (413) instead of expanded. NUL bytes and invalid UTF-8
    give 415."""
    if data[:2] == GZIP_MAGIC:
        try:
            with gzip.GzipFile(fileobj=BytesIO(data)) as fh:
                data = fh.read(max_decompressed + 1)
        except (OSError, EOFError, zlib.error):
            raise UploadError(400, "File is not a valid gzip stream") from None
        if len(data) > max_decompressed:
            raise UploadError(413, f"File expands to more than {max_decompressed // (1024 * 1024)} MB")
    if b"\x00" in data:
        raise UploadError(415, "File looks binary, expected JSON Lines text")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError(415, "File is not UTF-8 text") from None


_SOURCE_TYPES = {"adsb": "adsb_icao", "mlat": "mlat", "tisb": "tisb_other"}


def _raw_record(a: dict) -> dict:
    """A recording's State-shaped dict as an adsb.lol-shaped record for ``normalize_record``."""
    ground = a.get("on_ground") is True
    rec = {
        "hex": a.get("icao"), "flight": a.get("callsign"),
        "alt_baro": "ground" if ground else a.get("alt_baro_ft"),
        "alt_geom": a.get("alt_geom_ft"), "gs": a.get("gs_kt"), "track": a.get("track_deg"),
        "baro_rate": a.get("baro_rate_fpm"), "geom_rate": a.get("geom_rate_fpm"),
        "squawk": a.get("squawk"), "category": a.get("category"), "t": a.get("type_code"),
        "r": a.get("registration"), "lat": a.get("lat"), "lon": a.get("lon"),
        "nic": a.get("nic"), "nac_p": a.get("nac_p"), "seen_pos": a.get("seen_pos_s"),
        "rssi": a.get("rssi"), "dbFlags": a.get("dbFlags"),
    }
    t = _SOURCE_TYPES.get(a.get("source"))
    if t:
        rec["type"] = t
    return {k: v for k, v in rec.items() if v is not None}


@dataclass
class ParsedRecording:
    snapshots: list[Snapshot]
    lines: int = 0
    rejected_lines: int = 0
    aircraft_in: int = 0
    aircraft_kept: int = 0
    dropped: Counter = field(default_factory=Counter)  # reason -> count (lines and aircraft)


def parse_recording(text: str) -> ParsedRecording:
    out = ParsedRecording([])
    seen_ts: set[float] = set()
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        out.lines += 1

        def reject(reason: str) -> None:
            out.rejected_lines += 1
            out.dropped[reason] += 1

        if len(line) > MAX_LINE_CHARS:
            reject("line_too_long")
            continue
        try:
            d = json.loads(line)
        except (ValueError, RecursionError):
            reject("bad_json")
            continue
        if not isinstance(d, dict) or not isinstance(d.get("aircraft"), list):
            reject("bad_snapshot")
            continue
        ts = d.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts) or not 0 <= ts <= MAX_TS:
            reject("bad_ts")
            continue
        ts = float(ts)
        if ts in seen_ts:
            reject("duplicate_ts")
            continue
        if len(out.snapshots) >= MAX_SNAPSHOTS:
            raise UploadError(413, f"Too many snapshots (max {MAX_SNAPSHOTS})")
        recs = d["aircraft"]
        out.aircraft_in += len(recs)
        if len(recs) > MAX_AIRCRAFT_PER_SNAPSHOT:
            out.dropped["too_many_aircraft"] += len(recs) - MAX_AIRCRAFT_PER_SNAPSHOT
            recs = recs[:MAX_AIRCRAFT_PER_SNAPSHOT]
        states: list[State] = []
        icaos: set[str] = set()
        for a in recs:
            st = normalize_record(_raw_record(a) if isinstance(a, dict) else a, out.dropped)
            if st is None:
                continue
            if st.icao in icaos:
                out.dropped["duplicate_icao"] += 1
                continue
            icaos.add(st.icao)
            if a.get("injected") is True:
                st = State(**{**st.to_dict(), "injected": True})
            states.append(st)
        out.aircraft_kept += len(states)
        if out.aircraft_kept > MAX_STATES:
            raise UploadError(413, f"Too many aircraft records (max {MAX_STATES})")
        seen_ts.add(ts)
        out.snapshots.append(Snapshot(ts, states))
    out.snapshots.sort(key=lambda s: s.ts)
    return out


def _track_points(analysis, row: dict) -> list[dict]:
    members = db.alert_members({"icao": row["icao"], "evidence": row["evidence"]})
    lo, hi = row["first_ts"] - TRACK_PAD_S, row["last_ts"] + TRACK_PAD_S
    pts: list[dict] = []
    for icao in sorted(members):
        track = analysis.tracker.tracks.get(icao)
        if track is None:
            continue
        pts.extend({"ts": p.ts, **p.state.to_dict()} for p in track.points if lo <= p.ts <= hi)
    pts.sort(key=lambda p: p["ts"])
    return pts[:TRACK_POINTS_PER_ALERT * 2]


def run_upload(conn: sqlite3.Connection, data: bytes, max_decompressed: int) -> dict:
    """Validate an uploaded recording, detect over it and store the alerts (source "upload")."""
    parsed = parse_recording(decode_upload(data, max_decompressed))
    if not parsed.snapshots:
        raise UploadError(400, f"No valid snapshots in the file ({parsed.rejected_lines} of {parsed.lines} lines rejected)")
    analysis = analyse(parsed.snapshots)
    inserted = updated = 0
    ids: set[int] = set()
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in alert_rows(analysis, "upload"):
            result, alert_id = db.upsert_alert_id(conn, row, commit=False)
            db.store_alert_track(conn, alert_id, _track_points(analysis, row), commit=False)
            inserted += result == "inserted"
            updated += result == "updated"
            ids.add(alert_id)
        db.prune_uploads(conn, commit=False)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    marks = ",".join("?" for _ in ids)
    stored = conn.execute(f"SELECT id, injected FROM alerts WHERE id IN ({marks})", sorted(ids)).fetchall() if ids else []
    return {
        "snapshots_kept": len(parsed.snapshots),
        "lines": parsed.lines,
        "lines_rejected": parsed.rejected_lines,
        "aircraft_in": parsed.aircraft_in,
        "aircraft_kept": parsed.aircraft_kept,
        "dropped_by_reason": dict(parsed.dropped),
        "alerts_inserted": inserted,
        "alerts_updated": updated,
        "alert_ids": sorted(r["id"] for r in stored),
        "injected_alerts": sum(1 for r in stored if r["injected"]),
    }
