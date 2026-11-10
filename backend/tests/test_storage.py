"""Storage: snapshot window, fingerprinted alerts, atomic seeding, the committed fixture."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from ghosttrack import config, db, demo, pipeline
from ghosttrack.model import Snapshot
from ghosttrack.scenarios import scenario_by_id
from ghosttrack.validate import DEFAULT_RECORDING, RECORDINGS_DIR, load_recording

BASE = 1_794_300_000.0 // 3600 * 3600  # an hour boundary


def row(**kw):
    base = dict(
        source="live", detection_id="GT-004", severity="high", icao="abcdef", callsign="TEST1",
        first_ts=BASE + 100, last_ts=BASE + 130, injected=False, confidence=0.9, explanation="x",
        benign_causes=["a"], evidence=[{"field": "f", "observed": 1, "threshold": 2, "ts": BASE + 100}],
    )
    base.update(kw)
    return base


def test_schema_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"snapshots", "alerts", "runs", "meta"} <= names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)")}
    assert {"id", "detection_id", "severity", "icao", "callsign", "first_ts", "last_ts", "injected",
            "evidence_json", "status", "notes"} <= cols


def test_snapshot_roundtrip_keeps_injected_and_ignores_duplicates(conn):
    clean = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    run = scenario_by_id("single_ghost").apply(clean)
    snap = next(s for s in run.snapshots if any(a.injected for a in s.aircraft))
    assert db.store_snapshot(conn, "recorded", "socal", snap)
    assert not db.store_snapshot(conn, "recorded", "socal", snap)  # same (source, region, ts)
    back = db.latest_snapshot(conn, "recorded", "socal")
    assert back == snap
    assert any(a.injected for a in back.aircraft) and not all(a.injected for a in back.aircraft)


def test_window_keeps_only_recent_snapshots(conn):
    for i in range(0, 4000, 100):
        db.store_snapshot(conn, "live", "socal", Snapshot(BASE + i, []))
    db.prune(conn, BASE + 3900)
    rows = db.window_snapshots(conn, "live", "socal", BASE + 3900)
    assert rows and min(s.ts for s in rows) > BASE + 3900 - config.WINDOW_MINUTES * 60
    assert db.snapshot_count(conn) == len(rows)


def test_upsert_is_idempotent_and_keeps_triage(conn):
    assert db.upsert_alert(conn, row()) == "inserted"
    assert db.upsert_alert(conn, row()) == "updated"
    (a,) = db.list_alerts(conn)
    assert a["status"] == "new"
    db.set_alert_triage(conn, a["id"], "acknowledged", "looked at it")
    db.upsert_alert(conn, row(last_ts=BASE + 160))
    (a,) = db.list_alerts(conn)
    assert (a["status"], a["notes"], a["last_ts"]) == ("acknowledged", "looked at it", BASE + 160)


def test_overlapping_episode_with_shifted_start_is_extended_not_duplicated(conn):
    db.upsert_alert(conn, row(first_ts=BASE + 100, last_ts=BASE + 400))
    # The window slid: the same episode now starts later, in a different fingerprint bucket.
    assert db.fingerprint("live", "GT-004", "abcdef", BASE + 100) != db.fingerprint("live", "GT-004", "abcdef", BASE + 350)
    assert db.upsert_alert(conn, row(first_ts=BASE + 350, last_ts=BASE + 460)) == "updated"
    (a,) = db.list_alerts(conn)
    assert (a["first_ts"], a["last_ts"]) == (BASE + 100, BASE + 460)
    # A different episode (far apart) is a new alert.
    assert db.upsert_alert(conn, row(first_ts=BASE + 2000, last_ts=BASE + 2010)) == "inserted"
    assert len(db.list_alerts(conn)) == 2


def test_status_new_for_medium_and_above_info_below(conn):
    for i, sev in enumerate(("info", "low", "medium", "high")):
        db.upsert_alert(conn, row(severity=sev, icao=f"a0000{i}"))
    got = {a["severity"]: a["status"] for a in db.list_alerts(conn)}
    assert got == {"info": "info", "low": "info", "medium": "new", "high": "new"}


def test_alerts_older_than_24h_are_pruned(conn):
    db.upsert_alert(conn, row(first_ts=BASE, last_ts=BASE + 10))
    db.upsert_alert(conn, row(icao="a00001", first_ts=BASE + 80000, last_ts=BASE + 80010))
    db.prune(conn, BASE + 25 * 3600)
    assert [a["icao"] for a in db.list_alerts(conn)] == ["a00001"]


def test_sliding_window_detection_never_duplicates_alerts(conn):
    """Run detection after each snapshot over a rolling window: one alert per episode."""
    clean = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    run = scenario_by_id("single_ghost").apply(clean)
    counts = []
    for snap in run.snapshots:
        db.store_snapshot(conn, "live", "socal", snap)
        if snap.ts >= run.inject_ts - 30:
            pipeline.detect_window(conn, "live", "socal", snap.ts)
            counts.append(len(db.list_alerts(conn)))
    alerts = db.list_alerts(conn)
    keys = [(a["detection_id"], a["icao"]) for a in alerts]
    assert len(keys) == len(set(keys)), keys
    assert {a["detection_id"] for a in alerts} >= {"GT-001"}
    assert all(a["injected"] for a in alerts)
    assert counts == sorted(counts)  # the number of alerts only ever grows


def test_runs_store_validation_results(conn):
    rid = db.record_run(conn, "validation", True, {"passed": 7})
    got = db.latest_run(conn, "validation")
    assert got["id"] == rid and got["ok"] is True and got["results"] == {"passed": 7}


# ------------------------------------------------------------------ atomic seeding


def test_seed_is_all_or_nothing(conn, monkeypatch):
    real = demo._stamp

    def failing(c, now_ts, region):
        real(c, now_ts, region)  # writes snapshots and alerts...
        raise RuntimeError("boom")  # ...then dies before the commit

    monkeypatch.setattr(demo, "_stamp", failing)
    with pytest.raises(RuntimeError):
        demo.seed(conn, BASE + 1000)
    assert db.snapshot_count(conn) == 0 and db.list_alerts(conn) == [] and db.get_meta(conn, "demo_seed") is None
    monkeypatch.setattr(demo, "_stamp", real)
    assert demo.seed(conn, BASE + 1000) is True
    assert db.snapshot_count(conn) > 0 and db.list_alerts(conn)


def test_seed_twice_is_a_no_op(conn):
    assert demo.seed(conn, BASE + 1000) is True
    n, a = db.snapshot_count(conn), len(db.list_alerts(conn))
    assert demo.seed(conn, BASE + 1500) is False
    assert (db.snapshot_count(conn), len(db.list_alerts(conn))) == (n, a)


def test_concurrent_seeding_seeds_exactly_once(db_file):
    results = []

    def go():
        c = db.connect(db_file)
        try:
            results.append(demo.seed(c, BASE + 1000))
        finally:
            c.close()

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False] * 5 + [True]
    c = db.connect(db_file)
    assert len(db.list_alerts(c)) == 240
    c.close()


def test_ensure_seeded_uses_configured_database(db_file):
    demo.ensure_seeded(BASE + 1000)
    c = sqlite3.connect(db_file)
    assert c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 240
    c.close()


# ------------------------------------------------------------------ the committed fixture


def test_committed_fixture_is_current():
    """What CI checks: the committed fixture equals a fresh offline build."""
    committed = config.DEMO_FIXTURE.read_text(encoding="utf-8")
    assert committed == demo.fixture_json(demo.build_fixture())


def _build_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_demo", config.BACKEND_DIR / "scripts" / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_script_check_mode(capsys, tmp_path, monkeypatch):
    mod = _build_script()
    assert mod.main(["--check"]) == 0
    stale = tmp_path / "demo_fixture.json"
    stale.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "DEMO_FIXTURE", stale)
    assert mod.main(["--check"]) == 1
    assert "out of date" in capsys.readouterr().err


def test_stale_fixture_falls_back_to_computing_templates(tmp_path, monkeypatch):
    demo.load_templates.cache_clear()
    committed = demo.load_templates()
    bad = tmp_path / "demo_fixture.json"
    bad.write_text('{"inputs_digest": "stale", "alerts": []}', encoding="utf-8")
    monkeypatch.setattr(config, "DEMO_FIXTURE", bad)
    demo.load_templates.cache_clear()
    try:
        assert len(demo.load_templates()) == len(committed) > 0
    finally:
        demo.load_templates.cache_clear()


def test_fixture_is_deterministic_across_hash_seeds():
    import os
    import subprocess
    import sys

    outs = set()
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(config.BACKEND_DIR)}
        r = subprocess.run([sys.executable, str(config.BACKEND_DIR / "scripts" / "build_demo.py"), "--check"],
                           env=env, capture_output=True, text=True)
        outs.add((r.returncode, r.stdout.strip()))
    assert outs == {(0, "demo_fixture.json is current\napi_fixture.json is current")}
