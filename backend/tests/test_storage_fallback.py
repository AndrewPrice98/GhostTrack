"""Hardening of the replay path: failing catch-ups hold back, and stale storage is not shown."""
from __future__ import annotations

import logging
import sqlite3

import pytest

from ghosttrack import db, demo, live
from ghosttrack.model import Snapshot

NOW = demo.CYCLE_S * 500 + 1000.0


class LockedConn:
    """A connection whose write lock cannot be taken (locked or read-only database)."""

    def __init__(self):
        self.executed = []

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, sql, *a):
        self.executed.append(sql)
        raise sqlite3.OperationalError("database is locked")


def test_failed_lock_sets_hold_back_and_logs_once(caplog):
    conn = LockedConn()
    with caplog.at_level(logging.ERROR, logger="ghosttrack.demo"):
        with pytest.raises(sqlite3.OperationalError):
            demo.catch_up(conn, NOW)
        assert demo.REGION in demo._last_fail
        for _ in range(5):  # held back: no retry, no raise, no further log
            assert demo.catch_up(conn, NOW) == 0
    assert len(conn.executed) == 1
    assert len([r for r in caplog.records if r.name == "ghosttrack.demo"]) == 1


def test_failed_commit_before_the_lock_is_also_held_back():
    class BadCommit(LockedConn):
        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError):
        demo.catch_up(BadCommit(), NOW)
    assert demo.REGION in demo._last_fail


def test_stale_stored_replay_is_not_served(conn, monkeypatch):
    monkeypatch.setattr(demo, "catch_up", lambda *a, **k: 0)  # the catch-up is failing
    stale = demo.cycle_snapshot_at(NOW - 600)
    db.store_snapshot(conn, "recorded", demo.REGION, Snapshot(NOW - 600, stale.aircraft))
    view = live.traffic_view(conn, "recorded", demo.REGION, now_ts=NOW)
    expected = demo.cycle_snapshot_at(NOW)
    assert view["notice"] == live.STORAGE_NOTICE
    assert view["ts"] == expected.ts
    assert len(view["aircraft"]) == len(expected.aircraft)


def test_empty_storage_serves_the_in_memory_cycle(conn, monkeypatch):
    monkeypatch.setattr(demo, "catch_up", lambda *a, **k: 0)  # nothing gets stored
    view = live.traffic_view(conn, "recorded", demo.REGION, now_ts=NOW)
    assert view["notice"] == live.STORAGE_NOTICE and view["aircraft"]


def test_fresh_stored_replay_is_served_without_a_notice(conn):
    demo.seed(conn, NOW)
    view = live.traffic_view(conn, "recorded", demo.REGION, now_ts=NOW)
    assert view["notice"] is None and view["ts"] > NOW - live.REPLAY_MAX_AGE_S


def test_old_database_without_the_upload_source_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA.replace("'recorded','live','upload'", "'recorded','live'"))
    raw.execute(
        "INSERT INTO alerts (fingerprint, source, detection_id, severity, first_ts, last_ts) "
        "VALUES ('f', 'live', 'GT-001', 'high', 1, 2)")
    raw.commit()
    raw.close()
    c = db.connect(path)
    try:
        assert c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
        c.execute("INSERT INTO alerts (fingerprint, source, detection_id, severity, first_ts, last_ts) "
                  "VALUES ('g', 'upload', 'GT-001', 'high', 1, 2)")
        assert {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")} >= {"idx_alerts_last"}
    finally:
        c.close()
