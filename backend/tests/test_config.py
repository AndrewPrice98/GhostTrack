"""Configuration: blank environment values count as unset, and .env.example loads verbatim."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

from ghosttrack import config

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
NAMES = (
    "GHOSTTRACK_ADMIN_USER", "GHOSTTRACK_ADMIN_PASSWORD_HASH", "GHOSTTRACK_SECRET_KEY", "GHOSTTRACK_SESSION_HOURS",
    "GHOSTTRACK_COOKIE_SECURE", "GHOSTTRACK_DEMO", "GHOSTTRACK_GUEST", "GHOSTTRACK_LIVE", "GHOSTTRACK_LIVE_INTERVAL_S",
    "GHOSTTRACK_DB", "GHOSTTRACK_MAX_UPLOAD_MB", "GHOSTTRACK_TRUST_PROXY", "GHOSTTRACK_COMPUTE_FIXTURES",
)


def snapshot() -> dict:
    return {
        "db": config.db_path(), "user": config.admin_user(), "hash": config.admin_password_hash(),
        "secret": config.secret_key(), "hours": config.session_hours(), "secure": config.cookie_secure(),
        "demo": config.demo_mode(), "guest": config.guest_enabled(), "live": config.live_mode_enabled(),
        "interval": config.live_interval_s(), "upload": config.max_upload_bytes(), "proxy": config.trust_proxy(),
        "compute": config.compute_fixtures(),
    }


def clear_env(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def test_blank_values_are_treated_as_unset(monkeypatch):
    clear_env(monkeypatch)
    unset = snapshot()
    for name in NAMES:
        monkeypatch.setenv(name, "")
    assert snapshot() == unset
    assert config.db_path() == config.BACKEND_DIR / "data" / "ghosttrack.db"  # never Path("")
    assert config.session_hours() == 8.0 and config.admin_user() == "admin"
    for name in NAMES:
        monkeypatch.setenv(name, "   ")  # whitespace only is blank too
    assert snapshot() == unset


def test_values_still_apply_when_set(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("GHOSTTRACK_DB", "/tmp/x.db")
    monkeypatch.setenv("GHOSTTRACK_SESSION_HOURS", "2.5")
    monkeypatch.setenv("GHOSTTRACK_DEMO", "true")
    assert config.db_path() == Path("/tmp/x.db") and config.session_hours() == 2.5 and config.demo_mode()


def test_env_example_loads_verbatim_and_changes_nothing(tmp_path, monkeypatch):
    clear_env(monkeypatch)
    unset = snapshot()
    target = tmp_path / ".env"
    shutil.copyfile(ENV_EXAMPLE, target)  # copied as the README says, with no edits
    with mock.patch.dict(os.environ):
        config.load_env_file(target)
        assert not [k for k in os.environ if k.startswith("GHOSTTRACK_")]
        assert snapshot() == unset


def test_every_example_line_uncommented_is_also_harmless(tmp_path, monkeypatch):
    """Blank lines such as ``GHOSTTRACK_SECRET_KEY=`` must not turn into empty settings."""
    clear_env(monkeypatch)
    unset = snapshot()
    lines = []
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        body = raw[2:] if raw.startswith("# GHOSTTRACK_") else raw
        if body.startswith(("GHOSTTRACK_DB=", "GHOSTTRACK_GUEST=", "GHOSTTRACK_LIVE=")):
            continue  # placeholders and opt-in examples, not defaults
        lines.append(body)
    target = tmp_path / ".env"
    target.write_text("\n".join(lines), encoding="utf-8")
    with mock.patch.dict(os.environ):
        config.load_env_file(target)
        assert "GHOSTTRACK_ADMIN_PASSWORD_HASH" not in os.environ and "GHOSTTRACK_SECRET_KEY" not in os.environ
        assert snapshot() == unset


@pytest.mark.parametrize("name", ["GHOSTTRACK_SESSION_HOURS", "GHOSTTRACK_LIVE_INTERVAL_S", "GHOSTTRACK_MAX_UPLOAD_MB"])
def test_garbage_numbers_fall_back_to_the_default(monkeypatch, name):
    clear_env(monkeypatch)
    unset = snapshot()
    monkeypatch.setenv(name, "lots")
    assert snapshot() == unset
