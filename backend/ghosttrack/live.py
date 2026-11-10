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
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ghosttrack import config, db, demo, pipeline, upstream
from ghosttrack.model import Snapshot

log = logging.getLogger("ghosttrack.live")

CACHE_CONTROL = "s-maxage=10, stale-while-revalidate=30"
FALLBACK_NOTICE = "Live feed unavailable; showing recorded traffic"
MODES = ("recorded", "live")


def response_headers() -> dict[str, str]:
    """Headers every traffic response carries (live, recorded and fallback alike)."""
    return {"Cache-Control": CACHE_CONTROL}


@dataclass(frozen=True)
class LiveResult:
    snapshot: Snapshot | None  # None: the upstream is unavailable
    fetched_at: float | None
    from_cache: bool
    error: str | None = None


class LiveFeed:
    """Per-instance upstream gateway. One instance per process; tests make their own."""

    def __init__(
        self,
        fetch: Callable[[str], Any] | None = None,
        clock: Callable[[], float] = time.time,
        interval_s: float | None = None,
        connect: Callable[[], Any] = db.connect,
    ) -> None:
        self._fetch = fetch or upstream.fetch_snapshot
        self.clock = clock
        self._interval = interval_s
        self._connect = connect
        self._lock = threading.Lock()
        self._last_attempt: dict[str, float] = {}
        self._cache: dict[str, tuple[Snapshot, float]] = {}
        self._error: dict[str, str | None] = {}
        self.upstream_requests = 0

    @property
    def interval_s(self) -> float:
        # The 10 s floor applies to whatever is configured or passed.
        return max(config.MIN_LIVE_INTERVAL_S, self._interval if self._interval is not None else config.live_interval_s())

    def current(self, region: str = demo.REGION) -> LiveResult:
        if region not in upstream.REGIONS:
            raise ValueError(f"unknown region {region!r}")
        with self._lock:
            now = self.clock()
            last = self._last_attempt.get(region)
            fetched_now = False
            if last is None or now - last >= self.interval_s or now < last:
                self._last_attempt[region] = now
                fetched_now = True
                self.upstream_requests += 1
                try:
                    result = self._fetch(region)
                    snap = result[0] if isinstance(result, tuple) else result
                    self._store(region, snap, now)
                    self._cache[region] = (snap, now)
                    self._error[region] = None
                except Exception as exc:  # any failure falls back to the recorded replay
                    if not isinstance(exc, (upstream.UpstreamError, OSError, ValueError)):
                        log.exception("Unexpected live fetch failure")
                    self._error[region] = f"{type(exc).__name__}: {exc}"
            err = self._error.get(region)
            cached = self._cache.get(region)
            if err is None and cached is not None:
                return LiveResult(cached[0], cached[1], not fetched_now)
            return LiveResult(None, None, not fetched_now, err or "no data yet")

    def _store(self, region: str, snap: Snapshot, now: float) -> None:
        conn = self._connect()
        try:
            db.store_snapshot(conn, "live", region, snap)
            pipeline.detect_window(conn, "live", region, snap.ts)
            db.prune(conn, now)
        finally:
            conn.close()


def traffic_view(
    conn, mode: str = "recorded", region: str = demo.REGION, *, feed: LiveFeed | None = None, now_ts: float | None = None
) -> dict:
    """The traffic picture for one request.

    ``mode="recorded"`` (the default) is the clock-aligned replay. ``mode="live"`` is the adsb.lol
    view (throttled and cached) with the replay's injected aircraft overlaid; if the upstream is
    unavailable the result is the replay with ``source: "recorded"`` and a notice.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if now_ts is None:
        now_ts = feed.clock() if feed is not None else time.time()
    demo.catch_up(conn, now_ts, region=region)
    replay = demo.recorded_snapshot(conn, now_ts, region)
    replay_aircraft = replay.aircraft if replay else []
    notice = None
    source = "recorded"
    ts = replay.ts if replay else now_ts
    aircraft = [a.to_dict() for a in replay_aircraft]
    if mode == "live":
        result = (feed or default_feed()).current(region)
        if result.snapshot is not None:
            source = "live"
            ts = result.snapshot.ts
            live_icaos = {a.icao for a in result.snapshot.aircraft}
            aircraft = [a.to_dict() for a in result.snapshot.aircraft]
            aircraft += [a.to_dict() for a in replay_aircraft if a.injected and a.icao not in live_icaos]
        else:
            notice = FALLBACK_NOTICE
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


_default_feed: LiveFeed | None = None
_default_feed_lock = threading.Lock()


def default_feed() -> LiveFeed:
    global _default_feed
    with _default_feed_lock:
        if _default_feed is None:
            _default_feed = LiveFeed()
        return _default_feed
