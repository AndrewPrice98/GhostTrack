"""Demo replay: clock alignment, idempotency across the hour boundary, injected labelling."""
from __future__ import annotations

import bisect

import pytest

from ghosttrack import config, db, demo
from ghosttrack.scenarios import SCENARIOS, scenario_by_id
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording

HOUR = 1_794_300_000.0 // 3600 * 3600  # a wall-clock hour boundary (UTC)


def state_of(conn):
    snaps = [r[0] for r in conn.execute("SELECT ts FROM snapshots WHERE source = 'recorded' ORDER BY ts")]
    alerts = sorted(
        (a["detection_id"], a["icao"], a["first_ts"], a["last_ts"], a["status"], a["injected"])
        for a in db.list_alerts(conn)
    )
    return snaps, alerts


def test_cycle_covers_an_hour_and_has_seven_scheduled_scenarios():
    cyc = demo.cycle_snapshots()
    assert cyc[0].ts == 0 and 3500 < cyc[-1].ts <= demo.CYCLE_S
    assert all(a.ts < b.ts for a, b in zip(cyc, cyc[1:]))
    ids = [s for s, _ in demo.SCHEDULE]
    assert sorted(ids) == sorted(s.id for s in SCENARIOS) and len(set(ids)) == 7
    gaps = [b - a for (_, a), (_, b) in zip(demo.SCHEDULE, demo.SCHEDULE[1:])]
    assert all(180 <= g <= 600 for g in gaps)  # every few minutes
    assert demo.SCHEDULE[0][1] >= 240  # after GT-009's warm-up


def test_cycle_only_adds_labelled_states_to_the_recordings():
    recorded = {(s.ts, a) for s in demo._recorded_cycle() for a in s.aircraft}
    assert not any(a.injected for _, a in recorded)
    injected = 0
    for s in demo.cycle_snapshots():
        for a in s.aircraft:
            if (s.ts, a) not in recorded:
                injected += 1
                assert a.injected
    assert injected > 100


def test_fixture_templates_cover_every_scenario_and_are_all_injected():
    templates = demo.load_templates()
    assert len(templates) == 10
    assert all(t["injected"] for t in templates), "a clean recorded aircraft was flagged"
    fired = {t["detection_id"] for t in templates}
    for s in SCENARIOS:
        assert set(s.expected) <= fired
    for t in templates:
        assert t["source"] == "recorded" and t["severity"] in ("medium", "high")


def test_replay_is_aligned_to_the_clock(conn):
    now = HOUR + 1234.0
    demo.catch_up(conn, now, throttle_seconds=0)
    cyc = demo.cycle_snapshots()
    offs = [s.ts for s in cyc]
    rows = conn.execute("SELECT ts FROM snapshots WHERE source = 'recorded' ORDER BY ts").fetchall()
    assert rows
    for (ts,) in rows[::25]:
        offset = ts % demo.CYCLE_S
        i = bisect.bisect_left(offs, offset)
        assert offs[i] == pytest.approx(offset)
        snap = db.latest_snapshot(conn, "recorded", "socal", ts)
        assert snap.aircraft == list(cyc[i].aircraft)
    cur = demo.recorded_snapshot(conn, now)
    assert cur.ts <= now and now - cur.ts < 30


def test_catch_up_is_idempotent(conn):
    now = HOUR + 900.0
    assert demo.catch_up(conn, now, throttle_seconds=0) > 0
    before = state_of(conn)
    assert demo.catch_up(conn, now, throttle_seconds=0) == 0
    assert state_of(conn) == before


def test_hour_boundary_catch_up_is_idempotent_and_matches_a_cold_start(db_file, tmp_path):
    """Catching up across the hour boundary in steps gives exactly what a cold start at that time gives."""
    stepped = db.connect(db_file)
    for now in (HOUR - 40.0, HOUR - 10.0, HOUR + 5.0, HOUR + 5.0, HOUR + 60.0):
        demo.catch_up(stepped, now, throttle_seconds=0)
    cold = db.connect(tmp_path / "cold.db")
    demo.catch_up(cold, HOUR + 60.0, throttle_seconds=0)
    assert state_of(stepped) == state_of(cold)
    snaps, _ = state_of(stepped)
    assert len(snaps) == len(set(snaps))
    assert any(ts < HOUR for ts in snaps) and any(ts >= HOUR for ts in snaps)  # spans the boundary
    assert max(snaps) <= HOUR + 60.0
    # The last snapshot of the old cycle and the first of the new one are both there, each once.
    assert HOUR in snaps and any(HOUR - 10 < ts < HOUR for ts in snaps)
    stepped.close()
    cold.close()


def test_alerts_are_not_duplicated_across_cycles_or_catch_ups(conn):
    t = HOUR + 3500.0
    for now in (t, t + 100.0, t + 700.0, t + 3600.0, t + 3600.0):
        demo.catch_up(conn, now, throttle_seconds=0)
    alerts = db.list_alerts(conn)
    keys = [(a["detection_id"], a["icao"], a["first_ts"]) for a in alerts]
    assert len(keys) == len(set(keys))
    # Every cycle contributes the same ten templates (with their own wall-clock times).
    per_cycle = {}
    for a in alerts:
        per_cycle.setdefault(a["first_ts"] // demo.CYCLE_S, []).append(a)
    assert all(len(v) <= 10 for v in per_cycle.values())


def test_window_is_rolling_and_alerts_are_retained_24h(conn):
    demo.catch_up(conn, HOUR + 100.0, throttle_seconds=0)
    demo.catch_up(conn, HOUR + 5 * 3600.0, throttle_seconds=0)
    snaps, alerts = state_of(conn)
    assert min(snaps) > HOUR + 5 * 3600.0 - config.WINDOW_MINUTES * 60
    assert max(snaps) <= HOUR + 5 * 3600.0
    assert all(a[3] >= HOUR + 5 * 3600.0 - 24 * 3600.0 for a in alerts)
    demo.catch_up(conn, HOUR + 40 * 3600.0, throttle_seconds=0)
    assert all(a[3] >= HOUR + 40 * 3600.0 - 24 * 3600.0 for a in state_of(conn)[1])


def test_alert_appears_only_after_its_episode(conn):
    # The single ghost (offset 300 s) is not alerted before its episode has happened.
    demo.catch_up(conn, HOUR + 200.0, throttle_seconds=0)
    assert not [a for a in db.list_alerts(conn) if HOUR <= a["first_ts"] < HOUR + 3600]
    demo.catch_up(conn, HOUR + 600.0, throttle_seconds=0)
    got = [a for a in db.list_alerts(conn) if HOUR <= a["first_ts"] < HOUR + 3600]
    assert {a["detection_id"] for a in got} >= {"GT-001"}


def test_injected_labelling_survives_replay_storage_and_alerts(conn):
    demo.catch_up(conn, HOUR + 1000.0, throttle_seconds=0)
    # Within the active scenario window the stored snapshots carry injected aircraft.
    ghost_window = db.window_snapshots(conn, "recorded", "socal", HOUR + 1000.0)
    inj = [a for s in ghost_window for a in s.aircraft if a.injected]
    assert inj, "a visitor must see injected aircraft"
    assert {a.callsign for a in inj} >= {"GHO1"}
    # Everything a scenario added is injected; every recorded state is not.
    recorded_icaos = {a.icao for s in demo._recorded_cycle() for a in s.aircraft}
    for s in ghost_window:
        for a in s.aircraft:
            if a.icao not in recorded_icaos:
                assert a.injected
    alerts = [a for a in db.list_alerts(conn) if a["first_ts"] >= HOUR]
    assert alerts and all(a["injected"] for a in alerts)
    # Evidence timestamps were stamped onto the same wall clock as the alert.
    for a in alerts:
        assert all(HOUR <= e["ts"] <= HOUR + 3600 for e in a["evidence"] if e["ts"])


def test_no_alert_ever_blames_untouched_traffic(conn):
    demo.seed(conn, HOUR + 3599.0)
    assert db.list_alerts(conn)
    assert not [a for a in db.list_alerts(conn) if not a["injected"]]


def test_catch_up_throttle(conn):
    assert demo.catch_up(conn, HOUR + 500.0, throttle_seconds=60) > 0
    assert demo.catch_up(conn, HOUR + 900.0, throttle_seconds=60) == 0  # too soon: nothing done
    assert demo.catch_up(conn, HOUR + 900.0, throttle_seconds=0) > 0


def test_scheduled_scenarios_listing():
    items = demo.scheduled_scenarios(HOUR + 1000.0)
    assert [i.scenario for i in items] == [s for s, _ in demo.SCHEDULE]
    assert [i.active for i in items][:2] == [True, True] and not items[-1].active
    assert items[0].inject_ts == HOUR + 300.0
    assert items[0].title == scenario_by_id("single_ghost").title


def test_recordings_used_are_the_committed_ones():
    first = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)[0]
    assert demo._recorded_cycle()[0].aircraft == first.aircraft
