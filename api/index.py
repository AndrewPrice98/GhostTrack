"""Vercel entry point: serves the GhostTrack FastAPI app as a Python serverless function.

vercel.json rewrites every /api/* request here; FastAPI routes on the original path (the
BodyLimitMiddleware matches ``/api/recordings`` on that same path, and a test checks it).
Demo data is precomputed offline into backend/data/api_fixture.json and demo_fixture.json by
scripts/build_demo.py; the function only reads them. Each function instance builds its own
SQLite database in /tmp, seeded lazily from the committed recordings on the first request.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# The admin variables (GHOSTTRACK_ADMIN_PASSWORD_HASH, GHOSTTRACK_SECRET_KEY) are optional here:
# without them visitors browse as a read-only guest and nobody can sign in.


def _default(name: str, value: str) -> None:
    if not os.environ.get(name, "").strip():  # a blank dashboard value counts as unset
        os.environ[name] = value


# Serverless defaults; any of these can be overridden in Vercel's environment settings.
_default("GHOSTTRACK_DB", "/tmp/ghosttrack.db")  # /tmp is the only writable path
_default("GHOSTTRACK_DEMO", "true")
_default("GHOSTTRACK_TRUST_PROXY", "true")  # Vercel sets X-Forwarded-For itself
_default("GHOSTTRACK_MAX_UPLOAD_MB", "4")  # Vercel caps request bodies at 4.5 MB

from ghosttrack.api import app  # noqa: E402

__all__ = ["app"]
