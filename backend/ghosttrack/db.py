"""SQLite storage: the rolling snapshot window, alerts and validation runs.

- ``snapshots`` holds only the rolling window (about the last 30 minutes) per source and
  region; older rows are pruned. ``source`` is ``recorded`` (demo replay, restamped onto the
  wall clock) or ``live`` (adsb.lol).
- ``alerts`` are fingerprinted, so re-running detection over an overlapping window updates an
  alert instead of duplicating it. ``injected`` is 1 when any state behind the alert is
  injected test data. Triage fields (``status``, ``notes``) survive re-detection.
- ``runs`` holds validation results.

All SQL is parameterized.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ghosttrack import config
from ghosttrack.model import Snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL CHECK (source IN ('recorded','live')),
    region    TEXT NOT NULL,
    ts        REAL NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE (source, region, ts)
);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint    TEXT NOT NULL UNIQUE,
    source         TEXT NOT NULL CHECK (source IN ('recorded','live')),
    detection_id   TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('info','low','medium','high')),
    icao           TEXT,
    callsign       TEXT,
    first_ts       REAL NOT NULL,
    last_ts        REAL NOT NULL,
    injected       INTEGER NOT NULL DEFAULT 0 CHECK (injected IN (0,1)),
    confidence     REAL NOT NULL DEFAULT 0,
    explanation    TEXT NOT NULL DEFAULT '',
    benign_json    TEXT NOT NULL DEFAULT '[]',
    evidence_json  TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','info','acknowledged','dismissed')),
    notes          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alerts_last   ON alerts(last_ts);
CREATE INDEX IF NOT EXISTS idx_alerts_match  ON alerts(source, detection_id, icao);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    ok           INTEGER NOT NULL,
    results_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

BUSY_TIMEOUT_SECONDS = 15.0
FINGERPRINT_BUCKET_S = 300.0  # episodes starting in the same 5-minute bucket share a fingerprint
MERGE_GAP_S = 120.0  # same detection and address within this gap of an existing alert: same episode
MAX_RUNS = 50
SEVERITIES = ("info", "low", "medium", "high")
TRIAGE_SEVERITIES = ("medium", "high")  # these start as "new"; the rest are "info"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the database, creating it and its schema if needed."""
    path = Path(path or config.db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _use_wal(conn)
    conn.executescript(SCHEMA)
    return conn


def _use_wal(conn: sqlite3.Connection) -> None:
    """Switch a new database to WAL (needs an exclusive lock that SQLite does not wait for, so
    concurrent first connections retry briefly)."""
    deadline = time.monotonic() + BUSY_TIMEOUT_SECONDS
    while True:
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


# ------------------------------------------------------------------ snapshots


def store_snapshot(conn: sqlite3.Connection, source: str, region: str, snap: Snapshot, *, commit: bool = True) -> bool:
    """Store one snapshot; a second one with the same (source, region, ts) is ignored."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO snapshots (source, region, ts, data_json) VALUES (?, ?, ?, ?)",
        (source, region, snap.ts, json.dumps(snap.to_dict(), separators=(",", ":"))),
    )
    if commit:
        conn.commit()
    return cur.rowcount == 1


def window_snapshots(
    conn: sqlite3.Connection, source: str, region: str, end_ts: float, minutes: float = config.WINDOW_MINUTES
) -> list[Snapshot]:
    rows = conn.execute(
        "SELECT data_json FROM snapshots WHERE source = ? AND region = ? AND ts > ? AND ts <= ? ORDER BY ts",
        (source, region, end_ts - minutes * 60.0, end_ts),
    ).fetchall()
    return [Snapshot.from_dict(json.loads(r["data_json"])) for r in rows]


def latest_snapshot(
    conn: sqlite3.Connection, source: str, region: str, at_or_before: float | None = None
) -> Snapshot | None:
    if at_or_before is None:
        row = conn.execute(
            "SELECT data_json FROM snapshots WHERE source = ? AND region = ? ORDER BY ts DESC LIMIT 1",
            (source, region),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT data_json FROM snapshots WHERE source = ? AND region = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
            (source, region, at_or_before),
        ).fetchone()
    return Snapshot.from_dict(json.loads(row["data_json"])) if row else None


def snapshot_count(conn: sqlite3.Connection, source: str | None = None) -> int:
    if source is None:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM snapshots WHERE source = ?", (source,)).fetchone()[0]


# ------------------------------------------------------------------ alerts


def fingerprint(source: str, detection_id: str, icao: str | None, first_ts: float) -> str:
    """Stable identity of an episode: detection, address and the bucket its start falls in."""
    bucket = int(first_ts // FINGERPRINT_BUCKET_S)
    return f"{source}:{detection_id}:{icao or '-'}:{bucket}"


def default_status(severity: str) -> str:
    return "new" if severity in TRIAGE_SEVERITIES else "info"


def upsert_alert(conn: sqlite3.Connection, row: dict[str, Any], *, commit: bool = True) -> str:
    """Insert an alert, or extend the one it continues. Returns "inserted" or "updated".

    ``row`` keys: source, detection_id, severity, icao, callsign, first_ts, last_ts, injected,
    confidence, explanation, benign_causes, evidence. Identity is the fingerprint (detection,
    address, start bucket). In addition, an existing alert for the same source, detection and
    address whose interval touches this one (within MERGE_GAP_S) is the same episode seen
    through a window that has since slid, so it is extended rather than duplicated. ``status``
    and ``notes`` of an existing alert are never overwritten.
    """
    fp = fingerprint(row["source"], row["detection_id"], row["icao"], row["first_ts"])
    existing = conn.execute("SELECT id, severity FROM alerts WHERE fingerprint = ?", (fp,)).fetchone()
    if existing is None and row["icao"] is not None:
        existing = conn.execute(
            "SELECT id, severity FROM alerts WHERE source = ? AND detection_id = ? AND icao = ? "
            "AND last_ts >= ? AND first_ts <= ? ORDER BY first_ts LIMIT 1",
            (row["source"], row["detection_id"], row["icao"],
             row["first_ts"] - MERGE_GAP_S, row["last_ts"] + MERGE_GAP_S),
        ).fetchone()
    evidence = json.dumps(row["evidence"], separators=(",", ":"))
    benign = json.dumps(list(row["benign_causes"]), separators=(",", ":"))
    if existing is None:
        conn.execute(
            "INSERT INTO alerts (fingerprint, source, detection_id, severity, icao, callsign, first_ts, last_ts, "
            "injected, confidence, explanation, benign_json, evidence_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fp, row["source"], row["detection_id"], row["severity"], row["icao"], row["callsign"],
             row["first_ts"], row["last_ts"], int(bool(row["injected"])), row["confidence"],
             row["explanation"], benign, evidence, default_status(row["severity"])),
        )
        result = "inserted"
    else:
        severity = max(existing["severity"], row["severity"], key=SEVERITIES.index)
        conn.execute(
            "UPDATE alerts SET first_ts = MIN(first_ts, ?), last_ts = MAX(last_ts, ?), "
            "injected = MAX(injected, ?), confidence = MAX(confidence, ?), severity = ?, "
            "callsign = COALESCE(?, callsign), explanation = ?, benign_json = ?, evidence_json = ? WHERE id = ?",
            (row["first_ts"], row["last_ts"], int(bool(row["injected"])), row["confidence"], severity,
             row["callsign"], row["explanation"], benign, evidence, existing["id"]),
        )
        result = "updated"
    if commit:
        conn.commit()
    return result


def list_alerts(
    conn: sqlite3.Connection,
    *,
    sources: Iterable[str] = ("recorded", "live"),
    since_ts: float | None = None,
    status: str | None = None,
) -> list[dict]:
    srcs = list(sources)
    sql = f"SELECT * FROM alerts WHERE source IN ({','.join('?' for _ in srcs)})"
    args: list[Any] = [*srcs]
    if since_ts is not None:
        sql += " AND last_ts >= ?"
        args.append(since_ts)
    if status is not None:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY first_ts DESC, id DESC"
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(r)
        d["injected"] = bool(d["injected"])
        d["benign_causes"] = json.loads(d.pop("benign_json"))
        d["evidence"] = json.loads(d.pop("evidence_json"))
        out.append(d)
    return out


def set_alert_triage(conn: sqlite3.Connection, alert_id: int, status: str, notes: str | None = None) -> bool:
    cur = conn.execute("UPDATE alerts SET status = ?, notes = COALESCE(?, notes) WHERE id = ?", (status, notes, alert_id))
    conn.commit()
    return cur.rowcount == 1


# ------------------------------------------------------------------ pruning, meta, runs


def prune(
    conn: sqlite3.Connection,
    now_ts: float,
    *,
    window_minutes: float = config.WINDOW_MINUTES,
    alert_hours: float = config.ALERT_RETENTION_HOURS,
    commit: bool = True,
) -> None:
    conn.execute("DELETE FROM snapshots WHERE ts <= ?", (now_ts - window_minutes * 60.0,))
    conn.execute("DELETE FROM alerts WHERE last_ts < ?", (now_ts - alert_hours * 3600.0,))
    if commit:
        conn.commit()


def record_run(conn: sqlite3.Connection, kind: str, ok: bool, results: Any) -> int:
    cur = conn.execute(
        "INSERT INTO runs (kind, started_at, ok, results_json) VALUES (?, ?, ?, ?)",
        (kind, now_utc(), int(ok), json.dumps(results, separators=(",", ":"))),
    )
    conn.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?)", (MAX_RUNS,))
    conn.commit()
    return int(cur.lastrowid)


def latest_run(conn: sqlite3.Connection, kind: str) -> dict | None:
    r = conn.execute("SELECT * FROM runs WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "started_at": r["started_at"], "ok": bool(r["ok"]),
            "results": json.loads(r["results_json"])}


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    r = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
