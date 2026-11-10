"""REST API for GhostTrack. All routes live under /api (conventions as in CMMC Ready).

- Reads are open to the read-only guest in demo mode; every write needs the admin (403 for guests).
- Every filter is allow-listed or validated; SQL is parameterized; unknown ids are 404.
- Injected scenario data carries ``injected: true`` and ``label: "Injected test data"`` everywhere.
- ``GET /api/live`` is the only route that sends the traffic ``Cache-Control`` header.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Iterator, Literal

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from ghosttrack import __version__, auth, config, db, demo, exports, fixtures, icao, live, uploads, upstream
from ghosttrack.catalog import load_catalog
from ghosttrack.fixtures import INJECTED_LABEL, scenario_out
from ghosttrack.scenarios import GAP_SCENARIOS, SCENARIOS, scenario_by_id

log = logging.getLogger("ghosttrack.api")

UPLOAD_LABEL = "Uploaded recording (unverified)"
UPLOAD_INJECTED_LABEL = "Injected test data (as declared by upload)"
SMALL_BODY_BYTES = 64 * 1024  # every POST/PUT/PATCH except the recording upload
SEED_FAILURE_TTL_S = 60.0
MAX_NOTES = 2000
MAX_LIMIT = 200
FLAG_WINDOW_S = 600.0  # an alert touching the last ten minutes marks its aircraft as flagged

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"),
}
# Downloaded HTML is also sandboxed with no scripts, should a browser ever render it.
DOWNLOAD_HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"


class DataUnavailable(Exception):
    """The demo database could not be seeded (recently); data routes answer 503 meanwhile."""


_seed_lock = threading.Lock()
_seeded: set[str] = set()  # database paths seeded by this process
_seed_failed: dict[str, float] = {}  # database path -> monotonic time of the last failure


def ensure_data() -> None:
    """Seed the demo database on first use (once per process and database, under a lock).

    A failure is logged once and remembered for SEED_FAILURE_TTL_S, during which data routes
    answer 503 without trying again. A no-op outside demo mode.
    """
    if not config.demo_mode():
        return
    key = str(config.db_path())
    if key in _seeded:
        return
    with _seed_lock:
        if key in _seeded:
            return
        failed = _seed_failed.get(key)
        if failed is not None and time.monotonic() - failed < SEED_FAILURE_TTL_S:
            raise DataUnavailable
        try:
            demo.ensure_seeded()
        except Exception:
            log.exception("Seeding the demo database failed; retrying in %.0f s", SEED_FAILURE_TTL_S)
            _seed_failed[key] = time.monotonic()
            raise DataUnavailable from None
        _seed_failed.pop(key, None)
        _seeded.add(key)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:  # a head start; the first request would seed otherwise
        await run_in_threadpool(ensure_data)
    except DataUnavailable:
        pass
    yield


app = FastAPI(
    lifespan=lifespan,
    title="GhostTrack API",
    version=__version__,
    description="Plausibility checks on decoded ADS-B data. Not a certified surveillance system.",
    docs_url=None, redoc_url=None, openapi_url=None,  # served below, never to guests
)


class SecurityHeaders:
    """Pure ASGI: adds the security headers (and ``Cache-Control: no-store`` unless the route set
    its own) to every response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def wrapped(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message["headers"]}
                extra = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()
                         if k.lower().encode() not in names]
                if b"cache-control" not in names:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message["headers"], *extra]}
            await send(message)

        await self.app(scope, receive, wrapped)


MULTIPART_OVERHEAD = 64 * 1024


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Refuse request bodies over the upload cap (plus multipart overhead) with 413.

    An announced Content-Length is checked before any of the body is read. Bytes are also
    counted as they arrive, so a chunked request with no Content-Length cannot stream past the
    cap either.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _limit(scope) -> int:
        """64 KiB everywhere except the recording upload, which gets the upload cap."""
        if scope["method"] == "POST" and scope["path"].rstrip("/") == "/api/recordings":
            return config.max_upload_bytes() + MULTIPART_OVERHEAD
        return SMALL_BODY_BYTES

    @staticmethod
    async def _reject(send, limit: int) -> None:
        if limit == SMALL_BODY_BYTES:
            message = f"Request too large (max {SMALL_BODY_BYTES // 1024} KiB)"
        else:
            message = f"Request too large (max {config.max_upload_bytes() // (1024 * 1024)} MB)"
        await JSONResponse({"detail": message}, status_code=413)({"type": "http"}, _noop_receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(send, limit)
            return

        received = 0
        exceeded = False
        sent_any = False

        async def counting_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message):
            nonlocal sent_any
            if exceeded and not sent_any:
                return  # the framework's own "bad body" answer is replaced by the 413 below
            sent_any = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            pass
        if exceeded and not sent_any:
            await self._reject(send, limit)


async def _noop_receive():
    return {"type": "http.disconnect"}


app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeaders)


@app.exception_handler(DataUnavailable)
async def data_unavailable(_request: Request, _exc: DataUnavailable):
    return JSONResponse({"detail": "Demo data is not available yet"}, status_code=503,
                        headers={"Retry-After": str(int(SEED_FAILURE_TTL_S))})


@app.exception_handler(fixtures.FixtureUnavailable)
async def fixture_unavailable(_request: Request, _exc: fixtures.FixtureUnavailable):
    log.error("Precomputed API fixture is missing or stale")
    return JSONResponse({"detail": "Precomputed data is not available"}, status_code=503)


@app.exception_handler(Exception)
async def last_resort(request: Request, exc: Exception):
    """Anything unexpected: a generic 500 with no detail, logged once with its traceback."""
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    # This response is built outside the user middleware, so it carries the headers itself.
    headers = {**SECURITY_HEADERS, "Cache-Control": "no-store"}
    return JSONResponse({"detail": "Internal server error"}, status_code=500, headers=headers)


# -- dependencies -----------------------------------------------------------------------------


def get_conn() -> Iterator[sqlite3.Connection]:
    ensure_data()
    conn = db.connect()
    try:
        if config.demo_mode():
            try:  # the replay's snapshots and alerts are stamped onto the clock as they fall due
                demo.catch_up(conn)
            except Exception:
                log.warning("Demo catch-up failed; serving what is stored", exc_info=True)
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Writer = Depends(auth.require_writer)


def get_feed() -> live.LiveFeed:
    return getattr(app.state, "feed", None) or live.default_feed()


# -- validation helpers -------------------------------------------------------------------------

_DIGITS = re.compile(r"[0-9]{1,9}")
_HEX6 = re.compile(r"[0-9a-fA-F]{6}")
_SCENARIO = re.compile(r"[a-z0-9_]{1,40}")


def alert_dep(alert_id: str, conn: Conn) -> dict:
    found = db.get_alert(conn, int(alert_id)) if _DIGITS.fullmatch(alert_id) else None  # junk ids: 404
    if found is None:
        raise HTTPException(404, "alert not found")
    return found


AlertDep = Annotated[dict, Depends(alert_dep)]


def _iso(value: str | None, name: str) -> float | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, OverflowError, OSError):
        raise HTTPException(422, f"{name} must be an ISO 8601 date or time") from None


@lru_cache(maxsize=1)
def _detection_ids() -> tuple[str, ...]:
    return tuple(d["id"] for d in load_catalog()["detections"])


# -- serializers ---------------------------------------------------------------------------------


def _title(detection_id: str) -> str:
    return load_catalog()["by_id"].get(detection_id, {}).get("title", detection_id)


def alert_label(source: str, injected: bool) -> str | None:
    """The UI label. Uploaded data is unverified, and its ``injected`` claim is the uploader's."""
    if source == "upload":
        return UPLOAD_INJECTED_LABEL if injected else UPLOAD_LABEL
    return INJECTED_LABEL if injected else None


def alert_out(a: dict) -> dict:
    det = load_catalog()["by_id"].get(a["detection_id"], {})
    return {
        "id": a["id"], "source": a["source"], "detection_id": a["detection_id"],
        "title": _title(a["detection_id"]), "severity": a["severity"], "icao": a["icao"],
        "callsign": a["callsign"], "first_ts": a["first_ts"], "last_ts": a["last_ts"],
        "confidence": a["confidence"], "explanation": a["explanation"], "status": a["status"],
        "notes": a["notes"], "injected": a["injected"],
        "label": alert_label(a["source"], a["injected"]),
        "benign_causes": exports._benign(a, det),
    }


def _point_out(p: dict, source: str = "recorded") -> dict:
    inj = bool(p.get("injected"))
    return {"ts": p["ts"], "icao": p["icao"], "lat": p["lat"], "lon": p["lon"], "alt": p.get("alt_baro_ft"),
            "gs": p.get("gs_kt"), "track": p.get("track_deg"), "injected": inj,
            "label": alert_label(source, inj)}


def alert_detail(conn: sqlite3.Connection, a: dict) -> dict:
    out = alert_out(a)
    out["evidence"] = [{**e, "unit": exports._unit(e.get("field", ""))} for e in a["evidence"]]
    out["track"] = [_point_out(p, a["source"]) for p in db.alert_track(conn, a)]
    return out


def _aircraft_out(a: dict, flags: dict[str, list[int]]) -> dict:
    ids = flags.get(a["icao"], [])
    return {
        "icao": a["icao"], "callsign": a["callsign"], "lat": a["lat"], "lon": a["lon"],
        "alt": a["alt_baro_ft"], "on_ground": a["on_ground"], "gs": a["gs_kt"], "track": a["track_deg"],
        "category": a["category"], "source": a["source"], "injected": bool(a["injected"]),
        "label": INJECTED_LABEL if a["injected"] else None, "flagged": bool(ids), "alert_ids": ids,
    }


# -- public endpoints -----------------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__, "auth_configured": auth.is_configured(),
            "demo": config.demo_mode(), "guest": config.guest_enabled(),
            "max_upload_mb": round(config.max_upload_bytes() / (1024 * 1024), 2)}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


def _set_session_cookie(response: Response, token: str, expires) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, token, expires=expires,
        httponly=True, secure=config.cookie_secure(), samesite="strict", path="/api",
    )


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request, response: Response):
    if not auth.is_configured():
        raise HTTPException(503, "Authentication is not configured. Run: python -m ghosttrack create-admin")
    ip = auth.client_ip(request)
    wait = auth.limiter.retry_after(ip)
    if wait:
        raise HTTPException(429, "Too many failed logins. Try again later.", headers={"Retry-After": str(wait)})
    if not auth.check_credentials(body.username, body.password):
        auth.limiter.record_failure(ip)
        raise HTTPException(401, "Invalid username or password")
    auth.limiter.reset(ip)
    token, expires = auth.issue_token(body.username)
    _set_session_cookie(response, token, expires)
    # The token travels only in the httpOnly cookie, never in the response body.
    return {"username": body.username, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ")}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/api", httponly=True,
                           secure=config.cookie_secure(), samesite="strict")
    return {"ok": True}


# -- authenticated endpoints --------------------------------------------------------------------------

protected = APIRouter(dependencies=[Depends(auth.require_user)])


@protected.get("/api/auth/me")
def me(user: Annotated[str, Depends(auth.require_user)]):
    return {"username": user, "guest": user == auth.GUEST_USER}


# Live traffic ------------------------------------------------------------------------------------


@protected.get("/api/live")
def get_live(
    response: Response,
    feed: Annotated[live.LiveFeed, Depends(get_feed)],
    region: str = Query(demo.REGION, max_length=32),
    mode: str = Query("recorded", max_length=16),
):
    if region not in upstream.REGIONS:
        raise HTTPException(422, f"unknown region (allowed: {', '.join(sorted(upstream.REGIONS))})")
    if mode not in live.MODES:
        raise HTTPException(422, f"unknown mode (allowed: {', '.join(live.MODES)})")
    if mode == "live" and not config.live_mode_enabled():
        raise HTTPException(403, "Live mode is disabled on this server (set GHOSTTRACK_LIVE=true)")
    view = live.get_traffic(mode, region, connect=db.connect, feed=feed)
    flags: dict[str, list[int]] = {}
    try:
        conn = db.connect()
        try:
            for al in db.flagged_alerts(conn, view["ts"] - FLAG_WINDOW_S):
                for member in db.alert_members(al):
                    flags.setdefault(member, []).append(al["id"])
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        log.warning("Could not read alerts for the live view", exc_info=True)
    for k, v in live.response_headers().items():  # the only route that carries this header
        response.headers[k] = v
    response.headers["Vary"] = "Cookie"  # a shared cache must not mix the guest and the admin
    return {
        "source": view["source"], "notice": view["notice"], "mode": view["mode"], "region": view["region"],
        "ts": view["ts"], "aircraft": [_aircraft_out(a, flags) for a in view["aircraft"]],
        "injected_aircraft": view["injected_aircraft"],
        "flagged_aircraft": sum(1 for a in view["aircraft"] if a["icao"] in flags),
        "attribution": exports.ATTRIBUTION,
    }


# Aircraft ----------------------------------------------------------------------------------------------


@protected.get("/api/aircraft/{address}")
def get_aircraft(address: str, conn: Conn):
    if not _HEX6.fullmatch(address):
        raise HTTPException(404, "aircraft not found")
    addr = address.lower()
    track = db.aircraft_track(conn, addr)
    findings, _ = db.query_alerts(conn, icao=addr, limit=MAX_LIMIT)
    if not track and not findings:
        raise HTTPException(404, "aircraft not found")
    last = track[-1] if track else {}
    reg = last.get("registration")
    injected = any(p.get("injected") for p in track) or any(f["injected"] for f in findings)
    return {
        "icao": addr,
        "registration": {
            "registration": reg, "type_code": last.get("type_code"), "callsign": last.get("callsign"),
            "category": last.get("category"),
            "address_state": icao.state_for_address(addr),
            "n_number": icao.icao_to_nnumber(addr),
            "registration_states": list(icao.states_for_registration(reg) or ()),
        },
        "injected": injected,
        "label": INJECTED_LABEL if injected else None,
        "track": [_point_out(p) for p in track],
        "findings": [alert_out(f) for f in findings],
    }


# Alerts -------------------------------------------------------------------------------------------------


@protected.get("/api/alerts")
def list_alerts(
    conn: Conn,
    detection: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=32)] = None,
    severity: Literal["info", "low", "medium", "high"] | None = None,
    injected: bool | None = None,
    status: Literal["new", "info", "acknowledged", "dismissed"] | None = None,
    start: Annotated[str | None, Query(max_length=64)] = None,
    end: Annotated[str | None, Query(max_length=64)] = None,
    sort: Literal["first_ts", "last_ts", "severity", "confidence"] = "first_ts",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    if detection is not None and detection not in _detection_ids():
        raise HTTPException(422, "unknown detection id")
    items, total = db.query_alerts(
        conn, detection=detection, q=q, severity=severity, injected=injected, status=status,
        start_ts=_iso(start, "start"), end_ts=_iso(end, "end"), sort=sort, limit=limit, offset=offset)
    return {"items": [alert_out(a) for a in items], "total": total, "limit": limit, "offset": offset}


@protected.get("/api/alerts/{alert_id}")
def get_alert(alert: AlertDep, conn: Conn):
    return alert_detail(conn, alert)


class TriageRequest(BaseModel):
    status: Literal["new", "acknowledged", "dismissed"] | None = None
    notes: str | None = Field(default=None, max_length=MAX_NOTES)


def _apply_triage(conn: sqlite3.Connection, alert: dict, body: TriageRequest) -> dict:
    db.set_alert_triage(conn, alert["id"], body.status or alert["status"], body.notes)
    return alert_detail(conn, db.get_alert(conn, alert["id"]))


@protected.patch("/api/alerts/{alert_id}")
async def patch_alert(_w: Annotated[str, Writer], alert: AlertDep, request: Request, conn: Conn):
    # The body is parsed here, after the writer check, so a guest gets 403 whatever they send.
    try:
        raw = await request.json()
        body = TriageRequest.model_validate(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:  # includes pydantic's ValidationError
        fields = sorted({str(e["loc"][-1]) for e in exc.errors()}) if isinstance(exc, ValidationError) else []
        raise HTTPException(422, "Invalid body" + (f" ({', '.join(fields)})" if fields else "")) from None
    if body.status is None and body.notes is None:
        raise HTTPException(422, "Give a status, notes, or both")
    if body.notes is not None and "\x00" in body.notes:
        raise HTTPException(422, "notes must be text")
    return await run_in_threadpool(_apply_triage, conn, alert, body)


# Validation and detections ---------------------------------------------------------------------------------

def validation_raw() -> dict[str, Any]:
    """Validation results and false-positive measurements, read from the precomputed fixture."""
    return fixtures.validation_raw()


def _result_out(r) -> dict:
    return {"scenario": r.scenario, "expected": list(r.expected), "fired": list(r.fired), "passed": r.passed,
            "time_to_detect_s": r.time_to_detect_s, "collateral": r.collateral, "gap": r.gap,
            "injected": True, "label": INJECTED_LABEL}


def validation_out() -> dict:
    raw = validation_raw()
    cat = load_catalog()["validation"]
    budget = float(cat["fp_budget_per_hour"])
    fps = [{"recording": r.recording, "hours": round(r.hours, 3), "tracks": r.tracks, "counted": r.counted,
            "per_hour": round(r.per_hour, 3), "within_budget": r.per_hour <= budget,
            "counts": [{"detection_id": d, "severity": s, "count": n} for (d, s), n in sorted(r.counts.items())]}
           for r in raw["fps"]]
    return {
        "scenarios": [_result_out(r) for r in raw["results"]],
        "gaps": [_result_out(r) for r in raw["gaps"]],
        "false_positives": {
            "budget_per_hour": budget, "min_severity": cat["budget_min_severity"], "recordings": fps,
            "verdict": "no measurements" if not fps else
                       "within budget" if all(f["within_budget"] for f in fps) else "over budget",
        },
        "all_scenarios_passed": all(r.passed and not r.collateral for r in raw["results"]),
    }


@protected.get("/api/detections")
def get_detections():
    cat = load_catalog()
    return {"version": cat["version"], "detections": cat["detections"], "validation": validation_out()}


@protected.get("/api/validation")
def get_validation():
    return validation_out()


# Scenarios ------------------------------------------------------------------------------------------------------


@protected.get("/api/scenarios")
def get_scenarios():
    scheduled = dict(demo.SCHEDULE)
    return {"scenarios": [{**scenario_out(s), "demo_offset_s": scheduled.get(s.id)}
                          for s in (*SCENARIOS, *GAP_SCENARIOS)]}


@protected.get("/api/scenarios/{name}/replay")
def get_replay(name: str):
    try:
        if not _SCENARIO.fullmatch(name):
            raise KeyError(name)
        scenario_by_id(name)
    except KeyError:
        raise HTTPException(404, "scenario not found") from None
    return fixtures.replay(name)


# Exports ----------------------------------------------------------------------------------------------------------


def _download(content: bytes, media_type: str, filename: str, *, html: bool = False) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if html:
        headers["Content-Security-Policy"] = DOWNLOAD_HTML_CSP
    return Response(content, media_type=media_type, headers=headers)


@protected.get("/api/alerts/{alert_id}/evidence.json")
def evidence_json(alert: AlertDep, conn: Conn):
    body = exports.evidence_json(alert, db.alert_track(conn, alert), now=time.time())
    return _download(body, "application/json", exports.evidence_filename(alert, "json"))


@protected.get("/api/alerts/{alert_id}/evidence.html")
def evidence_html(alert: AlertDep, conn: Conn):
    body = exports.evidence_html(alert, db.alert_track(conn, alert), now=time.time())
    return _download(body, "text/html; charset=utf-8", exports.evidence_filename(alert, "html"), html=True)


@protected.get("/api/reports/summary.html")
def summary_html(conn: Conn):
    raw = validation_raw()
    alerts = db.list_alerts(conn, sources=("recorded", "live", "upload"))
    body = exports.summary_report_html(alerts, raw["results"], raw["fps"], raw["gaps"], now=time.time())
    return _download(body, "text/html; charset=utf-8", "ghosttrack-summary.html", html=True)


@protected.get("/api/reports/summary.md")
def summary_md(conn: Conn):
    raw = validation_raw()
    alerts = db.list_alerts(conn, sources=("recorded", "live", "upload"))
    body = exports.summary_report_md(alerts, raw["results"], raw["fps"], raw["gaps"], now=time.time())
    return _download(body.encode("utf-8") if isinstance(body, str) else body,
                     "text/markdown; charset=utf-8", "ghosttrack-summary.md")


# Writes -------------------------------------------------------------------------------------------------------------


@protected.post("/api/recordings")
def upload_recording(_w: Annotated[str, Writer], conn: Conn,
                     file: Annotated[UploadFile, File(description="Recording (.jsonl or .jsonl.gz)")]):
    limit = config.max_upload_bytes()
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File too large (max {limit // (1024 * 1024)} MB)")
    try:
        return uploads.run_upload(conn, data, config.max_decompressed_bytes())
    except uploads.UploadError as exc:
        raise HTTPException(exc.status, str(exc)) from None


app.include_router(protected)


# API description: JSON for the admin only. There are no /docs or /redoc pages: they load Swagger UI and ReDoc
# from a CDN, which the Content-Security-Policy blocks (and a CDN script would defeat the policy).
# ------------------------------


@app.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(_w: Annotated[str, Writer]):
    return app.openapi()
