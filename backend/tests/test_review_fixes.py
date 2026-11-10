"""Detection-time replay alerts, severity upgrades, storage-free fallback, resilient live feed."""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from ghosttrack import db, demo, live, upstream
from ghosttrack.scenarios import SCENARIOS
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording, run_scenario
from test_live import Clock, FakeUpstream, feed_env  # noqa: F401  (fixture)
from test_storage import BASE, row

HOUR = 1_794_300_000.0 // 3600 * 3600


# ------------------------------------------------------------------ detection-time alerts


def test_alert_appears_at_detection_time_not_at_episode_end(conn):
    # The single ghost is injected at 300 s; GT-001 needs a few reports and fires at 315 s,
    # long before the episode (to 445 s) ends.
    t = {(x["detection_id"], x["icao"]): x for x in demo.load_templates()}[("GT-001", "adf81e")]
    assert t["first_ts"] == 300.0 and t["detected_at"] < t["last_ts"] - 100
    demo.catch_up(conn, HOUR + t["detected_at"] - 1.0, throttle_seconds=0)
    assert not [a for a in db.list_alerts(conn) if a["detection_id"] == "GT-001" and a["first_ts"] >= HOUR]
    demo.catch_up(conn, HOUR + t["detected_at"], throttle_seconds=0)
    (a,) = [a for a in db.list_alerts(conn) if a["detection_id"] == "GT-001" and a["first_ts"] >= HOUR]
    assert a["last_ts"] <= HOUR + t["detected_at"] and a["status"] == "new"
    assert all(e["ts"] <= HOUR + t["detected_at"] for e in a["evidence"])
    # Later catch-ups extend the same alert up to the end of the episode, never duplicating it.
    for now in (HOUR + 350.0, HOUR + 400.0, HOUR + 600.0, HOUR + 600.0):
        demo.catch_up(conn, now, throttle_seconds=0)
    (a,) = [a for a in db.list_alerts(conn) if a["detection_id"] == "GT-001" and a["first_ts"] >= HOUR]
    assert a["last_ts"] == pytest.approx(HOUR + t["last_ts"])


def test_complete_alerts_are_not_rewritten_by_idle_catch_ups(conn, monkeypatch):
    demo.catch_up(conn, HOUR + 3000.0, throttle_seconds=0)
    calls = []
    real = db.upsert_alert
    monkeypatch.setattr(db, "upsert_alert", lambda *a, **k: calls.append(1) or real(*a, **k))
    demo.catch_up(conn, HOUR + 3000.0, throttle_seconds=0)
    demo.catch_up(conn, HOUR + 3001.0, throttle_seconds=0)
    assert calls == []


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_each_scenarios_alert_is_visible_at_inject_plus_ttd_and_not_before(scenario, conn):
    inject_at = dict(demo.SCHEDULE)[scenario.id]
    mine = [t for t in demo.load_templates() if t["detection_id"] in scenario.expected
            and inject_at - 30 <= t["first_ts"] <= inject_at + 100]
    assert mine, scenario.id
    first = min(mine, key=lambda t: t["detected_at"])
    detected = first["detected_at"]
    ttd = detected - first["first_ts"]  # from the start of the injected behaviour, as in validation
    assert 0 <= ttd <= 40  # a poll or a few, as in the validation table
    ref = run_scenario(scenario, load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)).time_to_detect_s
    assert abs(ttd - ref) <= 6, (ttd, ref)

    def visible():
        return [a for a in db.list_alerts(conn) if a["detection_id"] in scenario.expected
                and HOUR + inject_at - 30 <= a["first_ts"] <= HOUR + inject_at + 100]

    demo.catch_up(conn, HOUR + detected - 0.5, throttle_seconds=0)
    assert not visible()
    demo.catch_up(conn, HOUR + detected, throttle_seconds=0)
    assert visible()


def test_catch_up_throttle_starts_only_after_success(conn, monkeypatch):
    assert demo.catch_up(conn, HOUR + 500.0, throttle_seconds=60) > 0
    assert demo.catch_up(conn, HOUR + 900.0, throttle_seconds=60) == 0
    assert "socal" in demo._last_run
    demo._last_run.clear()

    def boom(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(demo, "_stamp", boom)
    with pytest.raises(RuntimeError):
        demo.catch_up(conn, HOUR + 500.0, throttle_seconds=60)
    assert demo._last_run == {}  # a failed catch-up does not start the throttle
    monkeypatch.undo()
    # The throttle is per region.
    demo.catch_up(conn, HOUR + 500.0, throttle_seconds=60)
    demo.catch_up(conn, HOUR + 500.0, region="elsewhere", throttle_seconds=60)
    assert set(demo._last_run) == {"socal", "elsewhere"}


# ------------------------------------------------------------------ storage


def test_upgrade_from_info_to_medium_becomes_new_but_triage_is_never_overwritten(conn):
    db.upsert_alert(conn, row(severity="low"))
    assert db.list_alerts(conn)[0]["status"] == "info"
    db.upsert_alert(conn, row(severity="medium", last_ts=BASE + 160))
    (a,) = db.list_alerts(conn)
    assert (a["severity"], a["status"]) == ("medium", "new")
    db.set_alert_triage(conn, a["id"], "dismissed")
    db.upsert_alert(conn, row(severity="high", last_ts=BASE + 190))
    (a,) = db.list_alerts(conn)
    assert (a["severity"], a["status"]) == ("high", "dismissed")
    db.upsert_alert(conn, row(icao="a00009", severity="info"))
    db.upsert_alert(conn, row(icao="a00009", severity="low", last_ts=BASE + 140))
    assert [x["status"] for x in db.list_alerts(conn) if x["icao"] == "a00009"] == ["info"]


def test_fleet_alert_without_address_is_deduplicated_across_sliding_windows(conn):
    members = [{"field": "addresses", "observed": ["adf900", "adf901"], "threshold": None, "ts": BASE}]
    fleet = dict(detection_id="GT-009", icao=None, callsign=None, evidence=members)
    assert db.upsert_alert(conn, row(first_ts=BASE + 100, last_ts=BASE + 400, **fleet)) == "inserted"
    # The window slid and the start moved into another fingerprint bucket: same members, touching.
    assert db.upsert_alert(conn, row(first_ts=BASE + 450, last_ts=BASE + 480, **fleet)) == "updated"
    assert len(db.list_alerts(conn)) == 1
    other = dict(fleet, evidence=[{"field": "addresses", "observed": ["adfa00"], "threshold": None, "ts": BASE}])
    assert db.upsert_alert(conn, row(first_ts=BASE + 460, last_ts=BASE + 470, **other)) == "inserted"
    assert len(db.list_alerts(conn)) == 2


# ------------------------------------------------------------------ live feed resilience


def test_concurrent_requests_share_one_fetch_and_waiters_do_not_queue(feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    fake.delay = 0.3
    results, times = [], []

    def go():
        t0 = time.monotonic()
        results.append(feed.current("socal"))
        times.append(time.monotonic() - t0)

    threads = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert fake.calls == 1 and len(results) == 12
    # One request fetched; the 11 that arrived meanwhile were served at once (no data yet, so the
    # caller falls back to the replay) instead of queueing behind the upstream call.
    assert sum(1 for r in results if r.snapshot is not None) == 1
    assert sorted(times)[-2] < 0.25
    # Warm cache: waiters are served the cached picture while one refresh is in flight.
    clock.t += 11
    results.clear()
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert fake.calls == 2
    assert all(r.snapshot is not None for r in results)


def test_last_good_picture_is_served_within_stale_while_revalidate(conn, feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    assert feed.current("socal").snapshot is not None
    fake.fail = upstream.UpstreamError("HTTP 503", status=503)
    clock.t += 10.5  # the refresh fails
    r = feed.current("socal")
    assert r.snapshot is not None and r.stale and fake.calls == 2
    view = live.traffic_view(conn, "live", feed=feed)
    assert view["source"] == "live" and view["notice"] is None
    clock.t += 25  # 35.5 s after the good fetch: beyond the stale window
    r = feed.current("socal")
    assert r.snapshot is None
    assert live.traffic_view(conn, "live", feed=feed)["source"] == "recorded"


def test_throttle_uses_a_monotonic_clock(db_file):
    wall, mono = Clock(), Clock(0.0)
    fake = FakeUpstream(clock=wall)
    feed = live.LiveFeed(fetch=fake, clock=wall, monotonic=mono)
    feed.current("socal")
    wall.t -= 3600  # the wall clock steps back an hour
    feed.current("socal")
    wall.t += 7200  # and forward two
    feed.current("socal")
    assert fake.calls == 1
    mono.t += 10.1
    feed.current("socal")
    assert fake.calls == 2


def test_broken_database_still_serves_the_replay_from_memory(feed_env):  # noqa: F811
    feed, fake, clock = feed_env
    broken = sqlite3.connect(":memory:")
    broken.close()  # every use raises sqlite3.ProgrammingError
    clock.t = HOUR + 330.0
    view = live.traffic_view(broken, "recorded", feed=feed)
    assert view["source"] == "recorded" and view["notice"] == live.STORAGE_NOTICE
    assert view["injected_aircraft"] > 0 and view["headers"]["Cache-Control"] == live.CACHE_CONTROL
    offs = demo.cycle_offsets()
    expect = demo.cycle_snapshots()[max(i for i, o in enumerate(offs) if o <= 330.0)]
    assert [a["icao"] for a in view["aircraft"]] == [a.icao for a in expect.aircraft]
    assert view["ts"] == HOUR + expect.ts
    # Live mode, dead database and dead upstream: the replay with the live notice.
    fake.fail = OSError("down")
    view = live.traffic_view(broken, "live", feed=feed)
    assert view["source"] == "recorded" and view["notice"] == live.FALLBACK_NOTICE and view["aircraft"]
    # Live mode, dead database, working upstream: live data is still shown.
    fake.fail = None
    clock.t += 11
    view = live.traffic_view(broken, "live", feed=feed)
    assert view["source"] == "live" and view["notice"] is None


def test_cycle_snapshot_at_wraps_around_the_hour():
    assert demo.cycle_snapshot_at(HOUR + 1.0).ts == HOUR
    assert demo.cycle_snapshot_at(HOUR + 3599.0).ts == HOUR + demo.cycle_snapshots()[-1].ts


def test_live_storage_failure_does_not_hide_live_data(db_file):
    def broken_connect():
        raise sqlite3.OperationalError("unable to open database file")

    clock = Clock()
    feed = live.LiveFeed(fetch=FakeUpstream(clock=clock), clock=clock, connect=broken_connect)
    assert feed.current("socal").snapshot is not None


def test_a_clean_live_feed_raises_no_medium_alerts(db_file):
    """Cold start on the clean recording through the live path: nothing at medium or above."""
    clean = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    seq = iter(clean[:120])
    clock = Clock()
    feed = live.LiveFeed(fetch=lambda region: (next(seq), {}), clock=clock)
    for _ in range(120):
        assert feed.current("socal").snapshot is not None
        clock.t += 10.0
    c = db.connect(db_file)
    assert [a for a in db.list_alerts(c) if a["severity"] in ("medium", "high")] == []
    c.close()
