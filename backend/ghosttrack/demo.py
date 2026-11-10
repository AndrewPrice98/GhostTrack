"""Demo mode: a clock-aligned replay of recorded traffic with scheduled injected scenarios.

The committed recordings are concatenated into one cycle (about an hour) and the seven attack
scenarios are injected into it on a fixed schedule, every few minutes. The cycle repeats every
``CYCLE_S`` seconds aligned to the wall clock: the snapshot at cycle offset ``o`` is stamped
``floor(now / CYCLE_S) * CYCLE_S + o``. Stamping is a pure function of the clock, so a catch-up
is idempotent across the hour boundary and across restarts.

Every injected or altered state keeps ``injected=True`` end to end (snapshots, alerts, views).

Detection for the replay is precomputed: ``scripts/build_demo.py`` runs the tracker and every
detection over one full cycle offline and commits the alert templates (offsets within the cycle)
to ``data/demo_fixture.json``. A cold start loads the templates and stamps them onto wall-clock
cycles; CI checks the fixture is current (``build_demo.py --check``). If the fixture is missing
or stale (its input digest differs) the templates are computed on the fly instead.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ghosttrack import config, db
from ghosttrack.catalog import CATALOG_PATH
from ghosttrack.model import Snapshot
from ghosttrack.pipeline import alert_rows
from ghosttrack.scenarios import scenario_by_id
from ghosttrack.validate import load_recording, manifest_recordings

log = logging.getLogger("ghosttrack.demo")

REGION = "socal"
CYCLE_S = 3600.0
FIXTURE_VERSION = 1
DEFAULT_THROTTLE_SECONDS = 5.0

# (scenario id, cycle offset in seconds at which the injected behaviour starts). Spaced 7 minutes
# apart, the first after GT-009's warm-up. The persistent position step comes last so its shifted
# aircraft stays displaced only until the cycle ends.
SCHEDULE: tuple[tuple[str, float], ...] = (
    ("single_ghost", 300.0),
    ("ghost_flood", 720.0),
    ("icao_hijack", 1140.0),
    ("teleport", 1560.0),
    ("altitude_spoof", 1980.0),
    ("registration_mismatch", 2400.0),
    ("position_offset", 2820.0),
)

_lock = threading.Lock()  # one catch-up at a time per process
_seed_lock = threading.Lock()
_last_run: float | None = None  # time.monotonic() of the last catch-up


# ------------------------------------------------------------------ the cycle


@lru_cache(maxsize=1)
def _recorded_cycle() -> tuple[Snapshot, ...]:
    """The recordings concatenated in manifest order, re-timed to start at offset 0."""
    snaps: list[Snapshot] = []
    for path in manifest_recordings(config.RECORDINGS_DIR):
        snaps.extend(load_recording(path))
    snaps.sort(key=lambda s: s.ts)
    t0 = snaps[0].ts
    return tuple(Snapshot(s.ts - t0, s.aircraft) for s in snaps)


@lru_cache(maxsize=1)
def cycle_snapshots() -> tuple[Snapshot, ...]:
    """One cycle of snapshots (ts = offset in seconds) with every scheduled scenario injected.

    Scenarios are applied in schedule order, each to the remainder of the cycle starting
    240 s (the scenarios' own lead-in) before its injection time.
    """
    from ghosttrack.scenarios.library import INJECT_AFTER_S

    snaps = list(_recorded_cycle())
    for sid, inject_at in SCHEDULE:
        start = next(i for i, s in enumerate(snaps) if s.ts >= inject_at - INJECT_AFTER_S)
        run = scenario_by_id(sid).apply(snaps[start:])
        snaps = snaps[:start] + run.snapshots
    return tuple(snaps)


def cycle_offsets() -> list[float]:
    return [s.ts for s in cycle_snapshots()]


def inputs_digest() -> str:
    """Digest of everything the fixture depends on besides code (the offline check covers code)."""
    h = hashlib.sha256()
    h.update(f"v{FIXTURE_VERSION}".encode())
    for path in manifest_recordings(config.RECORDINGS_DIR):
        h.update(path.name.encode())
        h.update(hashlib.sha256(path.read_bytes()).digest())
    h.update(b"\n".join(CATALOG_PATH.read_bytes().splitlines()))  # same digest on CRLF checkouts
    h.update(json.dumps(SCHEDULE).encode())
    return h.hexdigest()


# ------------------------------------------------------------------ fixture


def _round(value: Any, ndigits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, ndigits)
    if isinstance(value, dict):
        return {k: _round(v, ndigits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(v, ndigits) for v in value]
    return value


def build_fixture() -> dict:
    """Run the tracker and every detection over one full cycle; return the alert templates."""
    from ghosttrack.detections import analyse

    snaps = cycle_snapshots()
    rows = alert_rows(analyse(snaps), "recorded")
    return _round({
        "version": FIXTURE_VERSION,
        "inputs_digest": inputs_digest(),
        "region": REGION,
        "cycle_s": CYCLE_S,
        "snapshots": len(snaps),
        "schedule": [{"scenario": s, "inject_offset_s": t} for s, t in SCHEDULE],
        "alerts": rows,
    })


def fixture_json(fixture: dict) -> str:
    return json.dumps(fixture, indent=1, sort_keys=True) + "\n"


@lru_cache(maxsize=1)
def load_templates() -> tuple[dict, ...]:
    """Alert templates: from the committed fixture when current, else computed now."""
    fixture = None
    try:
        fixture = json.loads(config.DEMO_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("Demo fixture missing or unreadable; computing alert templates now")
    if fixture is not None and fixture.get("inputs_digest") != inputs_digest():
        log.warning("Demo fixture is stale (inputs changed); computing alert templates now")
        fixture = None
    if fixture is None:
        fixture = build_fixture()
    return tuple(fixture["alerts"])


def stamp_alert(template: dict, cycle_start: float) -> dict:
    row = dict(template)
    row["first_ts"] = template["first_ts"] + cycle_start
    row["last_ts"] = template["last_ts"] + cycle_start
    row["evidence"] = [{**e, "ts": e["ts"] + cycle_start} for e in template["evidence"]]
    return row


# ------------------------------------------------------------------ stamping onto the clock


def _cycles(lo: float, hi: float) -> range:
    return range(int(lo // CYCLE_S), int(hi // CYCLE_S) + 1)


def _stamp(conn, now_ts: float, region: str) -> int:
    """Insert due snapshots and alerts without committing. Returns new snapshots stored."""
    offs = cycle_offsets()
    snaps = cycle_snapshots()
    win_lo = now_ts - config.WINDOW_MINUTES * 60.0
    last = conn.execute(
        "SELECT MAX(ts) FROM snapshots WHERE source = 'recorded' AND region = ?", (region,)
    ).fetchone()[0]
    lo = max(win_lo, last) if last is not None else win_lo
    stored = 0
    for c in _cycles(lo, now_ts):
        base = c * CYCLE_S
        i = bisect.bisect_right(offs, lo - base)
        j = bisect.bisect_right(offs, now_ts - base)
        for snap in snaps[i:j]:
            if db.store_snapshot(conn, "recorded", region, Snapshot(base + snap.ts, snap.aircraft), commit=False):
                stored += 1
    # Alerts for the retention period whose detection time (end of the episode) has passed.
    alert_lo = now_ts - config.ALERT_RETENTION_HOURS * 3600.0
    have = {r[0] for r in conn.execute("SELECT fingerprint FROM alerts WHERE source = 'recorded'")}
    for c in _cycles(alert_lo, now_ts):
        base = c * CYCLE_S
        for t in load_templates():
            row = stamp_alert(t, base)
            if alert_lo <= row["last_ts"] <= now_ts:
                if db.fingerprint("recorded", row["detection_id"], row["icao"], row["first_ts"]) not in have:
                    db.upsert_alert(conn, row, commit=False)
    db.prune(conn, now_ts, commit=False)
    return stored


def catch_up(
    conn, now_ts: float | None = None, *, region: str = REGION, throttle_seconds: float = DEFAULT_THROTTLE_SECONDS
) -> int:
    """Stamp every recorded snapshot and alert that is due by ``now_ts`` (default: now).

    Idempotent: stamped times depend only on the clock, rows are unique on them, and alerts are
    fingerprinted. At most one catch-up runs at a time per process, and calls closer together
    than ``throttle_seconds`` return 0 without doing anything.
    """
    global _last_run
    now_ts = time.time() if now_ts is None else now_ts
    with _lock:
        mono = time.monotonic()
        if throttle_seconds and _last_run is not None and mono - _last_run < throttle_seconds:
            return 0
        _last_run = mono
        conn.commit()  # no transaction may be open when taking the write lock
        conn.execute("BEGIN IMMEDIATE")
        try:
            stored = _stamp(conn, now_ts, region)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return stored


# ------------------------------------------------------------------ seeding


def seed(conn, now_ts: float | None = None, *, region: str = REGION) -> bool:
    """Fill an empty database with the last window of replay and the last 24 h of alerts, in one
    transaction: everything is stored or nothing is. Returns False (and does nothing) if this
    database is already seeded for the current inputs, so calling it twice is safe."""
    now_ts = time.time() if now_ts is None else now_ts
    digest = inputs_digest()
    if db.get_meta(conn, "demo_seed") == digest:
        return False
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-check under the write lock: another process may have seeded meanwhile.
        row = conn.execute("SELECT value FROM meta WHERE key = 'demo_seed'").fetchone()
        if row is not None and row[0] == digest:
            conn.rollback()
            return False
        # A database seeded for different inputs: replace the replay rows, keep live data.
        conn.execute("DELETE FROM snapshots WHERE source = 'recorded'")
        conn.execute("DELETE FROM alerts WHERE source = 'recorded'")
        _stamp(conn, now_ts, region)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('demo_seed', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (digest,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return True


def ensure_seeded(now_ts: float | None = None) -> None:
    """Create and seed this instance's database if it has none yet (serverless cold start)."""
    with _seed_lock:
        conn = db.connect()
        try:
            if seed(conn, now_ts):
                log.info("Seeded demo database at %s", config.db_path())
        finally:
            conn.close()


# ------------------------------------------------------------------ views


@dataclass(frozen=True)
class ScheduledScenario:
    scenario: str
    title: str
    inject_ts: float
    active: bool


def scheduled_scenarios(now_ts: float) -> list[ScheduledScenario]:
    """The scenarios of the current cycle with their wall-clock injection times."""
    base = (now_ts // CYCLE_S) * CYCLE_S
    out = []
    for sid, t in SCHEDULE:
        sc = scenario_by_id(sid)
        out.append(ScheduledScenario(sid, sc.title, base + t, base + t <= now_ts))
    return out


def recorded_snapshot(conn, now_ts: float, region: str = REGION) -> Snapshot | None:
    """The replay snapshot current at ``now_ts`` (call ``catch_up`` first)."""
    return db.latest_snapshot(conn, "recorded", region, now_ts)
