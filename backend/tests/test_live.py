"""Live view: throttle, lock, cache headers, fallback on upstream failure, injected labelling."""
from __future__ import annotations

import threading
import time

import pytest

from ghosttrack import db, demo, live, upstream
from ghosttrack.model import Snapshot
from ghosttrack.scenarios import scenario_by_id
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording

HOUR = 1_794_300_000.0 // 3600 * 3600


class Clock:
    def __init__(self, t=HOUR + 1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeUpstream:
    """Stands in for fetch_snapshot: serves recorded snapshots (as an upstream, never injected)."""

    def __init__(self, snaps=None, clock=None):
        self.snaps = snaps or load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
        self.clock = clock
        self.calls = 0
        self.fail: Exception | None = None
        self.delay = 0.0

    def __call__(self, region):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        snap = self.snaps[self.calls % len(self.snaps)]
        if self.clock is not None:  # the upstream's "now" is the (fake) wall clock
            snap = Snapshot(self.clock(), snap.aircraft)
        return snap, {"records_in": 0}


@pytest.fixture
def feed_env(db_file):
    clock = Clock()
    fake = FakeUpstream(clock=clock)
    feed = live.LiveFeed(fetch=fake, clock=clock)
    return feed, fake, clock


# ------------------------------------------------------------------ throttle


def test_at_most_one_upstream_request_per_ten_seconds(feed_env):
    feed, fake, clock = feed_env
    for _ in range(50):
        assert feed.current("socal").snapshot is not None
    assert fake.calls == 1
    clock.t += 9.9
    feed.current("socal")
    assert fake.calls == 1
    clock.t += 0.2  # 10.1 s after the first request
    assert feed.current("socal").from_cache is False
    assert fake.calls == 2


def test_configured_interval_cannot_go_below_the_floor(db_file, monkeypatch):
    monkeypatch.setenv("GHOSTTRACK_LIVE_INTERVAL_S", "1")
    clock = Clock()
    fake = FakeUpstream(clock=clock)
    feed = live.LiveFeed(fetch=fake, clock=clock)
    assert feed.interval_s == 10.0
    assert live.LiveFeed(fetch=fake, clock=clock, interval_s=0.5).interval_s == 10.0
    feed.current("socal")
    clock.t += 3
    feed.current("socal")
    assert fake.calls == 1
    monkeypatch.setenv("GHOSTTRACK_LIVE_INTERVAL_S", "30")
    assert live.LiveFeed(fetch=fake, clock=clock).interval_s == 30.0


def test_unknown_region_never_reaches_the_upstream(feed_env):
    feed, fake, _ = feed_env
    with pytest.raises(ValueError):
        feed.current("../../etc")
    assert fake.calls == 0


# ------------------------------------------------------------------ fallback


def test_view_falls_back_to_recorded_when_the_upstream_fails(conn, feed_env):
    feed, fake, clock = feed_env
    fake.fail = upstream.UpstreamError("timeout", status=None)
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["source"] == "recorded"
    assert view["notice"] == "Live feed unavailable; showing recorded traffic"
    assert view["aircraft"], "the fallback still shows traffic"
    assert view["injected_aircraft"] == sum(1 for a in view["aircraft"] if a["injected"])
    # A recent failure is cached: no hammering of a struggling upstream.
    for _ in range(5):
        live.traffic_view(conn, "live", feed=feed)
    assert fake.calls == 1
    # Once the interval passed and the upstream is back, the live view returns.
    fake.fail = None
    clock.t += 10.5
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["source"] == "live" and view["notice"] is None
    assert fake.calls == 2


@pytest.mark.parametrize("exc", [
    upstream.UpstreamError("HTTP 429", status=429, retry_after=30),
    upstream.UpstreamError("body too large"),
    TimeoutError("timed out"),
    OSError("network down"),
    ValueError("bad json"),
])
def test_every_kind_of_upstream_failure_falls_back(conn, feed_env, exc):
    feed, fake, _ = feed_env
    fake.fail = exc
    view = live.traffic_view(conn, "live", feed=feed)
    assert (view["source"], view["notice"]) == ("recorded", live.FALLBACK_NOTICE)


def test_recorded_mode_never_touches_the_upstream(conn, feed_env):
    feed, fake, _ = feed_env
    view = live.traffic_view(conn, "recorded", feed=feed)
    assert fake.calls == 0 and view["source"] == "recorded" and view["notice"] is None


def test_unknown_mode_is_rejected(conn, feed_env):
    with pytest.raises(ValueError):
        live.traffic_view(conn, "satellite", feed=feed_env[0])


# ------------------------------------------------------------------ headers


def test_responses_carry_the_cache_headers(conn, feed_env):
    feed, fake, _ = feed_env
    assert live.CACHE_CONTROL == "s-maxage=10, stale-while-revalidate=30"
    for mode in ("recorded", "live"):
        assert live.traffic_view(conn, mode, feed=feed)["headers"] == {"Cache-Control": live.CACHE_CONTROL}
    fake.fail = OSError("down")
    clock_view = live.traffic_view(conn, "live", feed=live.LiveFeed(fetch=fake, clock=feed.clock))
    assert clock_view["headers"]["Cache-Control"] == live.CACHE_CONTROL
    assert live.response_headers() == {"Cache-Control": live.CACHE_CONTROL}


# ------------------------------------------------------------------ labelling


def test_live_view_overlays_labelled_injected_aircraft_on_unlabelled_live_traffic(conn, feed_env):
    feed, fake, clock = feed_env
    clock.t = HOUR + 330.0  # the single ghost (injected at 300 s) is on screen
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["source"] == "live"
    live_icaos = {a.icao for a in fake.snaps[fake.calls % len(fake.snaps)].aircraft}
    from_upstream = [a for a in view["aircraft"] if a["icao"] in live_icaos]
    overlay = [a for a in view["aircraft"] if a["icao"] not in live_icaos]
    assert from_upstream and not any(a["injected"] for a in from_upstream)
    assert overlay and all(a["injected"] for a in overlay)
    assert {a["callsign"] for a in overlay} >= {"GHO1"}
    assert view["injected_aircraft"] == len(overlay)


def test_a_visitor_always_sees_injected_aircraft_in_both_modes(conn, feed_env):
    feed, fake, clock = feed_env
    clock.t = HOUR + 340.0
    for mode in ("recorded", "live"):
        view = live.traffic_view(conn, mode, feed=feed)
        assert view["injected_aircraft"] > 0, mode
    fake.fail = OSError("down")
    clock.t += 11
    assert live.traffic_view(conn, "live", feed=feed)["injected_aircraft"] > 0


# ------------------------------------------------------------------ live detection


def test_each_fetch_runs_detection_and_never_duplicates_alerts(db_file):
    """A ghost arrives in live traffic: one alert, however many fetches see it."""
    clean = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    run = scenario_by_id("single_ghost").apply(clean)
    frames = [s for s in run.snapshots if s.ts >= run.inject_ts - 60][:30]
    seq = iter(frames)
    wall, mono = Clock(frames[0].ts), Clock(0.0)  # the wall clock follows the data, the throttle clock ticks 10 s

    def fetch(region):
        snap = next(seq)
        wall.t = snap.ts
        return snap, {}

    feed = live.LiveFeed(fetch=fetch, clock=wall, monotonic=mono)
    for _ in range(len(frames)):
        assert feed.current("socal").snapshot is not None
        mono.t += 10.0
    c = db.connect(db_file)
    alerts = db.list_alerts(c, sources=["live"])
    keys = [(a["detection_id"], a["icao"]) for a in alerts]
    assert len(keys) == len(set(keys)) and ("GT-001", next(iter(run.addresses))) in keys
    assert all(a["injected"] for a in alerts)  # the ghost states were labelled injected
    assert db.list_alerts(c, sources=["recorded"]) == []
    assert db.snapshot_count(c, "live") == len(frames)
    c.close()


def test_live_snapshots_are_stored_in_the_rolling_window_only(db_file):
    clock = Clock()
    fake = FakeUpstream(clock=clock)
    feed = live.LiveFeed(fetch=fake, clock=clock)
    for _ in range(40):
        feed.current("socal")
        clock.t += 100.0  # far longer than the window in total
    c = db.connect(db_file)
    stored = db.snapshot_count(c, "live")
    c.close()
    assert 0 < stored <= 40
    assert stored <= 19  # 30 min / 100 s: only the window survives


def test_default_feed_is_a_singleton():
    assert live.default_feed() is live.default_feed()
    assert demo.REGION in upstream.REGIONS
