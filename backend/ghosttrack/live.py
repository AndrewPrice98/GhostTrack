"""Live adsb.lol view: throttled, locked, cached, with a fallback to the recorded replay.

Etiquette. At most one upstream request per region per ``config.live_interval_s()`` (10 s floor)
per instance, serialised by a lock; requests inside the interval are served from the cache
(including a recent failure, so an outage is not retried on every request). Responses carry
``Cache-Control: s-maxage=10, stale-while-revalidate=30`` so an edge cache absorbs most traffic.

Each successful fetch is stored in the rolling window, the detections run over that window and
the alerts are upserted (fingerprinted, so a fetch never duplicates one).

Fallback. When the upstream fails or times out the view is the recorded replay with
``source: "recorded"`` and a notice. A visitor always sees injected-labelled aircraft: the live
view overlays the replay's currently injected states, and every one of them keeps ``injected``.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ghosttrack import config, db, demo, pipeline, upstream
from ghosttrack.model import Snapshot

log = logging.getLogger("ghosttrack.live")

CACHE_CONTROL = "s-maxage=10, stale-while-revalidate=30"
FALLBACK_NOTICE = "Live feed unavailable; showing recorded traffic"
STORAGE_NOTICE = "Storage unavailable; showing recorded traffic"
LOADING_NOTICE = "Loading live data; showing recorded traffic"
MAX_UPSTREAM_SKEW_S = 300.0  # an upstream "now" further than this from our clock is not trusted
MODES = ("recorded", "live")
REPLAY_MAX_AGE_S = 30.0  # a stored replay snapshot older than this (about 3 intervals) is not trusted
STALE_WHILE_REVALIDATE_S = 30.0  # matches the stale-while-revalidate in CACHE_CONTROL


def response_headers() -> dict[str, str]:
    """Headers every traffic response carries (live, recorded and fallback alike)."""
    return {"Cache-Control": CACHE_CONTROL}


@dataclass(frozen=True)
class LiveResult:
    snapshot: Snapshot | None  # None: no usable live picture
    fetched_mono: float | None  # monotonic time of the fetch (an internal reference, not an epoch)
    from_cache: bool
    error: str | None = None
    stale: bool = False  # an older good snapshot served inside the stale-while-revalidate window
    age_s: float = 0.0  # seconds since that snapshot was fetched
    loading: bool = False  # nothing fetched yet and another request is fetching it


class LiveFeed:
    """Per-instance upstream gateway. One instance per process; tests make their own.

    ``clock`` is the wall clock (used for ages and by tests); the throttle uses ``monotonic`` so a
    wall-clock step cannot cause a burst of requests or a long freeze.
    """

    def __init__(
        self,
        fetch: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
        interval_s: float | None = None,
        connect: Callable[[], Any] = db.connect,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._fetch = fetch or upstream.fetch_snapshot
        self.clock = clock
        # A custom wall clock (tests) drives the throttle too, unless a monotonic one is given.
        self._mono = monotonic or (time.monotonic if clock is time.time else clock)
        self._interval = interval_s
        self._connect = connect
        self._lock = threading.Lock()
        self._last_attempt: dict[str, float] = {}
        self._cache: dict[str, tuple[Snapshot, float]] = {}  # region -> (snapshot, monotonic time)
        self._error: dict[str, str | None] = {}
        self.upstream_requests = 0

    @property
    def interval_s(self) -> float:
        # The 10 s floor applies to whatever is configured or passed.
        return max(config.MIN_LIVE_INTERVAL_S, self._interval if self._interval is not None else config.live_interval_s())

    def _serve(self, region: str, now: float, fetched_now: bool) -> LiveResult:
        err = self._error.get(region)
        cached = self._cache.get(region)
        if cached is not None:
            age = max(0.0, now - cached[1])
            if err is None:
                return LiveResult(cached[0], cached[1], not fetched_now, age_s=age)
            if age <= STALE_WHILE_REVALIDATE_S:  # the last good picture is still fresh enough
                return LiveResult(cached[0], cached[1], True, err, stale=True, age_s=age)
        return LiveResult(None, None, not fetched_now, err or "no data yet", loading=err is None)

    def current(self, region: str = demo.REGION) -> LiveResult:
        if region not in upstream.REGIONS:
            raise ValueError(f"unknown region {region!r}")
        if not self._lock.acquire(blocking=False):
            # Another request is fetching: do not queue behind an 8 s upstream call, serve what
            # there is (the cache, or the replay when there is none).
            return self._serve(region, self._mono(), False)
        try:
            now = self._mono()
            last = self._last_attempt.get(region)
            fetched_now = False
            if last is None or now - last >= self.interval_s or now < last:
                self._last_attempt[region] = now
                fetched_now = True
                self.upstream_requests += 1
                try:
                    result = self._fetch(region)
                    snap = result[0] if isinstance(result, tuple) else result
                except Exception as exc:  # any failure falls back (stale cache, else the replay)
                    if not isinstance(exc, (upstream.UpstreamError, OSError, ValueError)):
                        log.exception("Unexpected live fetch failure")
                    self._error[region] = f"{type(exc).__name__}: {exc}"
                else:
                    snap = self._trusted(snap)
                    self._cache[region] = (snap, now)
                    self._error[region] = None
                    self._store(region, snap)
            return self._serve(region, now, fetched_now)
        finally:
            self._lock.release()

    def _trusted(self, snap: Snapshot) -> Snapshot:
        """The upstream clock is untrusted input: a snapshot whose time is far from ours is
        stored and shown with our receipt time instead."""
        local = self.clock()
        if abs(snap.ts - local) > MAX_UPSTREAM_SKEW_S:
            log.warning("Upstream time %.0f is %.0f s from the local clock; using receipt time", snap.ts, snap.ts - local)
            return Snapshot(local, snap.aircraft)
        return snap

    def _store(self, region: str, snap: Snapshot) -> None:
        """Persist the snapshot, detect and upsert. Storage trouble never hides live data."""
        try:
            conn = self._connect()
            try:
                db.store_snapshot(conn, "live", region, snap)
                pipeline.detect_window(conn, "live", region, snap.ts)
                db.prune(conn, min(snap.ts, self.clock()))  # never ahead of the local clock
            finally:
                conn.close()
        except Exception:  # sqlite3.Error or a detection bug: the picture is still good
            log.exception("Could not store or analyse the live snapshot")


def traffic_view(
    conn, mode: str = "recorded", region: str = demo.REGION, *, feed: LiveFeed | None = None, now_ts: float | None = None
) -> dict:
    """The traffic picture for one request, on an open connection (``None`` means no database).

    ``mode="recorded"`` (the default) is the clock-aligned replay. ``mode="live"`` is the adsb.lol
    view (throttled and cached) with the replay's injected aircraft overlaid. If the upstream is
    unavailable the result is the replay with ``source: "recorded"`` and a notice; if the
    database is unusable the replay comes straight from the in-memory cycle.

    The API layer should call ``get_traffic``, which also covers failing to open the database.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if now_ts is None:
        now_ts = feed.clock() if feed is not None else time.time()
    notice = None
    try:
        if conn is None:
            raise sqlite3.OperationalError("no database connection")
        demo.catch_up(conn, now_ts, region=region)
        replay = demo.recorded_snapshot(conn, now_ts, region)
        if replay is None or now_ts - replay.ts > REPLAY_MAX_AGE_S:
            # Nothing stored, or the catch-up has been failing: do not show a stale picture.
            replay = demo.cycle_snapshot_at(now_ts)
            notice = STORAGE_NOTICE
    except (sqlite3.Error, OSError):
        # The database is unusable (read-only or missing /tmp, locked, corrupt): the replay is a
        # pure function of the clock, so serve it straight from the in-memory cycle.
        log.warning("Database unavailable; serving the replay from memory", exc_info=conn is not None)
        replay = demo.cycle_snapshot_at(now_ts)
        notice = STORAGE_NOTICE
    replay_aircraft = replay.aircraft if replay else []
    source = "recorded"
    ts = replay.ts if replay else now_ts
    aircraft = [a.to_dict() for a in replay_aircraft]
    if mode == "live":
        result = (feed or default_feed()).current(region)
        if result.snapshot is not None:
            source = "live"
            notice = f"Showing live data from {int(result.age_s)} s ago" if result.stale else None
            ts = result.snapshot.ts
            live_icaos = {a.icao for a in result.snapshot.aircraft}
            aircraft = [a.to_dict() for a in result.snapshot.aircraft]
            aircraft += [a.to_dict() for a in replay_aircraft if a.injected and a.icao not in live_icaos]
        else:
            notice = LOADING_NOTICE if result.loading else FALLBACK_NOTICE
    return {
        "source": source,
        "notice": notice,
        "mode": mode,
        "region": region,
        "ts": ts,
        "aircraft": aircraft,
        "injected_aircraft": sum(1 for a in aircraft if a["injected"]),
        "headers": response_headers(),
    }


def get_traffic(
    mode: str = "recorded",
    region: str = demo.REGION,
    *,
    connect: Callable[[], Any] = db.connect,
    feed: LiveFeed | None = None,
    now_ts: float | None = None,
) -> dict:
    """The entry point for the API layer: opens (and closes) the database itself, so a failure to
    open it (unwritable path, missing directory, locked file) also falls back to the in-memory
    replay with a notice instead of an error."""
    try:
        conn = connect()
    except (sqlite3.Error, OSError):
        log.warning("Could not open the database; serving the replay from memory", exc_info=True)
        conn = None
    try:
        return traffic_view(conn, mode, region, feed=feed, now_ts=now_ts)
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


_default_feed: LiveFeed | None = None
_default_feed_lock = threading.Lock()


def default_feed() -> LiveFeed:
    global _default_feed
    with _default_feed_lock:
        if _default_feed is None:
            _default_feed = LiveFeed()
        return _default_feed
