"""Replay alerts hold no future state, connection-failure fallback, untrusted upstream time, live notices."""
from __future__ import annotations

import re
import sqlite3

import pytest

from ghosttrack import db, demo, live, upstream
from ghosttrack.model import Snapshot
from test_live import Clock, FakeUpstream, feed_env  # noqa: F401  (fixture)

HOUR = 1_794_300_000.0 // 3600 * 3600


def templates():
    return {(t["detection_id"], t["icao"]): t for t in demo.load_templates()}


def recorded_alerts(conn, det, base=HOUR):
    return [a for a in db.list_alerts(conn) if a["detection_id"] == det and a["first_ts"] >= base]


# ------------------------------------------------------------------ detection-time content


def test_detection_time_alert_describes_only_what_has_been_observed(conn):
    t = templates()[("GT-001", "adf81e")]
    det = HOUR + t["detected_at"]
    ghost_reports = [s for s in demo.cycle_snapshots() if t["first_ts"] <= s.ts <= t["detected_at"]
                     for a in s.aircraft if a.icao == "adf81e"]
    demo.catch_up(conn, det, throttle_seconds=0)
    (a,) = recorded_alerts(conn, "GT-001")
    n = int(re.search(r"in (\d+) reports", a["explanation"]).group(1))
    assert 2 <= n <= len(ghost_reports) < 10, (n, len(ghost_reports))
    assert "24 reports" not in a["explanation"]
    assert all(e["ts"] <= det for e in a["evidence"])
    assert a["last_ts"] == det and a["confidence"] == t["initial"]["confidence"]
    assert a["explanation"] == t["initial"]["explanation"]


def test_final_version_appears_only_at_or_after_the_end_of_the_episode(conn):
    t = templates()[("GT-001", "adf81e")]
    end = HOUR + t["last_ts"]
    for now in (HOUR + t["detected_at"], end - 20.0, end - 0.5):
        demo.catch_up(conn, now, throttle_seconds=0)
        (a,) = recorded_alerts(conn, "GT-001")
        assert a["explanation"] == t["initial"]["explanation"]
        assert a["last_ts"] == now  # grows, but is never ahead of the clock
        assert all(e["ts"] <= now for e in a["evidence"])
    demo.catch_up(conn, end, throttle_seconds=0)
    (a,) = recorded_alerts(conn, "GT-001")
    assert a["explanation"] == t["explanation"] and "24 reports" in a["explanation"]
    assert a["last_ts"] == pytest.approx(end)
    assert len(a["evidence"]) == len(t["evidence"])


def test_every_alert_at_detection_time_contains_nothing_from_the_future(conn):
    for t in demo.load_templates():
        for c in (conn,):
            c.execute("DELETE FROM alerts")
            c.commit()
        det = HOUR + t["detected_at"]
        demo._last_fail.clear()
        demo.catch_up(conn, det, throttle_seconds=0)
        for a in db.list_alerts(conn):
            assert a["last_ts"] <= det
            assert all(e["ts"] <= det + 1e-6 for e in a["evidence"]), (t["detection_id"], t["icao"])
    for t in demo.load_templates():
        if t["initial"]:
            assert all(e["ts"] <= t["detected_at"] + 1e-6 for e in t["initial"]["evidence"])
            assert t["initial"]["severity"] in ("medium", "high")


def test_fixture_initial_state_differs_where_the_episode_grows():
    t = templates()
    assert "2 reports" in t[("GT-001", "adf81e")]["initial"]["explanation"]
    assert "24 reports" in t[("GT-001", "adf81e")]["explanation"]
    assert len(t[("GT-007", "adfbb1")]["initial"]["evidence"]) < len(t[("GT-007", "adfbb1")]["evidence"])


# ------------------------------------------------------------------ connection failures


@pytest.mark.parametrize("exc", [OSError("read-only file system"), sqlite3.OperationalError("unable to open database file")])
def test_failure_to_open_the_database_falls_back_to_the_in_memory_replay(exc, feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    clock.t = HOUR + 330.0

    def connect():
        raise exc

    view = live.get_traffic("recorded", connect=connect, feed=feed)
    assert view["source"] == "recorded" and view["notice"] == live.STORAGE_NOTICE
    assert view["aircraft"] and view["injected_aircraft"] > 0
    assert view["headers"]["Cache-Control"] == live.CACHE_CONTROL
    fake.fail = OSError("down")
    view = live.get_traffic("live", connect=connect, feed=feed)
    assert view["source"] == "recorded" and view["notice"] == live.FALLBACK_NOTICE and view["aircraft"]
    fake.fail = None
    clock.t += 11
    view = live.get_traffic("live", connect=connect, feed=feed)
    assert view["source"] == "live" and view["notice"] is None


def test_get_traffic_opens_and_closes_its_own_connection(db_file, feed_env):  # noqa: F811
    feed, _, clock = feed_env
    opened = []

    def connect():
        c = db.connect(db_file)
        opened.append(c)
        return c

    view = live.get_traffic("recorded", connect=connect, feed=feed)
    assert view["notice"] is None and view["aircraft"]
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")  # closed


def test_a_connection_that_fails_midway_also_falls_back():
    class Broken:
        def commit(self):
            raise OSError("disk I/O error")

    view = live.traffic_view(Broken(), "recorded", now_ts=HOUR + 400.0)
    assert view["notice"] == live.STORAGE_NOTICE and view["aircraft"]


# ------------------------------------------------------------------ untrusted upstream time


@pytest.mark.parametrize("skew", [1e9, 4000.0, -4000.0, -1e9])
def test_a_bad_upstream_time_cannot_wipe_or_poison_stored_data(db_file, skew):
    clock = Clock()
    good = FakeUpstream(clock=clock)
    feed = live.LiveFeed(fetch=good, clock=clock)
    for _ in range(5):
        feed.current("socal")
        clock.t += 10.0
    c = db.connect(db_file)
    before = db.snapshot_count(c, "live")
    assert before == 5
    c.close()

    def hostile(region):
        snap = good.snaps[7]
        return Snapshot(clock.t + skew, snap.aircraft), {}

    feed2 = live.LiveFeed(fetch=hostile, clock=clock, connect=lambda: db.connect(db_file))
    r = feed2.current("socal")
    assert r.snapshot is not None and r.snapshot.ts == clock.t  # shown with the local receipt time
    c = db.connect(db_file)
    assert db.snapshot_count(c, "live") == before + 1  # nothing was pruned away
    newest = db.latest_snapshot(c, "live", "socal")
    assert newest.ts == clock.t
    c.close()


def test_a_small_clock_difference_keeps_the_upstream_time(db_file):
    clock = Clock()
    feed = live.LiveFeed(fetch=lambda r: (Snapshot(clock.t - 4.0, []), {}), clock=clock)
    assert feed.current("socal").snapshot.ts == clock.t - 4.0


# ------------------------------------------------------------------ notices


def test_stale_live_data_says_how_old_it_is(conn, feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    live.traffic_view(conn, "live", feed=feed)
    fake.fail = upstream.UpstreamError("HTTP 503", status=503)
    clock.t += 12.0
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["source"] == "live" and view["notice"] == "Showing live data from 12 s ago"
    r = feed.current("socal")
    assert r.stale and r.age_s == pytest.approx(12.0) and r.fetched_mono is not None


def test_a_request_waiting_on_the_first_fetch_is_told_it_is_loading(conn, feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    assert feed._lock.acquire(blocking=False)  # another request is mid-fetch
    try:
        view = live.traffic_view(conn, "live", feed=feed)
    finally:
        feed._lock.release()
    assert view["source"] == "recorded" and view["notice"] == "Loading live data; showing recorded traffic"
    assert fake.calls == 0
    fake.fail = OSError("down")
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["notice"] == live.FALLBACK_NOTICE  # a real failure keeps the failure notice
