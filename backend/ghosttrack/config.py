"""Runtime configuration, read from GHOSTTRACK_* environment variables.

Values are read through functions (not module constants) so tests can override them at any
time. For local development, variables can live in backend/.env (git-ignored); real
environment variables always win.
"""
from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
RECORDINGS_DIR = BACKEND_DIR / "data" / "recordings"
DEMO_FIXTURE = BACKEND_DIR / "data" / "demo_fixture.json"
API_FIXTURE = BACKEND_DIR / "data" / "api_fixture.json"
ENV_FILE = BACKEND_DIR / ".env"

# Etiquette towards adsb.lol: never more than one upstream request per region per 10 s per
# instance. The floor cannot be configured away.
MIN_LIVE_INTERVAL_S = 10.0
WINDOW_MINUTES = 30
ALERT_RETENTION_HOURS = 24


def load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, optional quotes. Never overrides."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_env_file()


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def db_path() -> Path:
    return Path(os.environ.get("GHOSTTRACK_DB", BACKEND_DIR / "data" / "ghosttrack.db"))


def admin_user() -> str:
    return os.environ.get("GHOSTTRACK_ADMIN_USER", "admin")


def admin_password_hash() -> str:
    return os.environ.get("GHOSTTRACK_ADMIN_PASSWORD_HASH", "")


def secret_key() -> str:
    """Key that signs session tokens. Must be long and random; rotating it logs everyone out."""
    return os.environ.get("GHOSTTRACK_SECRET_KEY", "")


def session_hours() -> float:
    return float(os.environ.get("GHOSTTRACK_SESSION_HOURS", "8"))


def cookie_secure() -> bool:
    """Send the session cookie over HTTPS only (default on)."""
    return _flag("GHOSTTRACK_COOKIE_SECURE", True)


def demo_mode() -> bool:
    """Hosted demo: recorded replay with scheduled injected scenarios, read-only for guests."""
    return _flag("GHOSTTRACK_DEMO", False)


def guest_enabled() -> bool:
    """Anonymous read-only access. On in demo mode, off everywhere else."""
    return _flag("GHOSTTRACK_GUEST", demo_mode())


def trust_proxy() -> bool:
    """Use X-Forwarded-For for the client IP. Enable only behind a proxy you control."""
    return _flag("GHOSTTRACK_TRUST_PROXY", False)


def live_interval_s() -> float:
    """Seconds between upstream requests per region; values below the 10 s floor are raised."""
    try:
        value = float(os.environ.get("GHOSTTRACK_LIVE_INTERVAL_S", MIN_LIVE_INTERVAL_S))
    except ValueError:
        value = MIN_LIVE_INTERVAL_S
    return max(MIN_LIVE_INTERVAL_S, value)


def max_upload_bytes() -> int:
    """Largest accepted upload: 10 MB locally, at most 4 MB on the hosted demo or on Vercel."""
    try:
        mb = float(os.environ.get("GHOSTTRACK_MAX_UPLOAD_MB", "10"))
    except ValueError:
        mb = 10.0
    if demo_mode() or os.environ.get("VERCEL"):
        mb = min(mb, 4.0)  # Vercel Functions refuse request bodies over 4.5 MB before the app sees them
    return int(max(0.1, mb) * 1024 * 1024)


def max_decompressed_bytes() -> int:
    """Cap on what a gzip upload may expand to (a gzip bomb is refused, not inflated)."""
    return 4 * max_upload_bytes()


def live_mode_enabled() -> bool:
    """Whether ``mode=live`` (the adsb.lol view) may be requested. Default: on in demo mode only,
    so a self-hosted install makes no upstream requests until the operator opts in."""
    return _flag("GHOSTTRACK_LIVE", demo_mode())


def compute_fixtures() -> bool:
    """Dev/test escape hatch: compute the API fixture on the fly when the committed one is stale."""
    return _flag("GHOSTTRACK_COMPUTE_FIXTURES", False)
