"""Allow-listed adsb.lol fetch, size cap, JSON validation and per-record sanitisation.

Only api.adsb.lol is ever contacted; the path template takes numeric parameters only.
Everything in the response is untrusted: bad records are dropped or fields cleared and
counted in ``stats["dropped_by_reason"]``; nothing here raises on hostile content.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.request
from collections import Counter
from typing import Callable

from ghosttrack.model import Snapshot, State

HOST = "api.adsb.lol"
BASE_URL = "https://" + HOST
PATH_TEMPLATE = "/v2/point/{lat:.4f}/{lon:.4f}/{radius:d}"
TIMEOUT_S = 3.0
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_RADIUS_NM = 250
USER_AGENT = "GhostTrack/0.1 (non-commercial portfolio demo; read-only)"

# Allow-listed regions: name -> (lat, lon, radius NM). Region names never reach the URL.
REGIONS: dict[str, tuple[float, float, int]] = {
    "socal": (33.94, -118.41, 60),
}

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")
_SQUAWK_RE = re.compile(r"^[0-7]{4}$")
_CAT_RE = re.compile(r"^[A-D][0-7]$")
_CALLSIGN_RE = re.compile(r"^[A-Za-z0-9 \-]+$")
_TYPE_RE = re.compile(r"^[A-Za-z0-9]+$")
_REG_RE = re.compile(r"^[A-Za-z0-9\-]+$")
# US DoD ICAO block AE0000-AFFFFF (best-effort military exclusion; see DECISIONS.md).
_DOD_LO, _DOD_HI = 0xAE0000, 0xAFFFFF
DBFLAG_MILITARY = 1


class UpstreamError(Exception):
    """Fetch or envelope failure (network, size, non-JSON, wrong shape)."""


def build_url(region: str) -> str:
    if region not in REGIONS:
        raise ValueError(f"unknown region {region!r}")
    lat, lon, radius = REGIONS[region]
    radius = int(radius)
    if not (0 < radius <= MAX_RADIUS_NM and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("region parameters out of range")
    return BASE_URL + PATH_TEMPLATE.format(lat=float(lat), lon=float(lon), radius=radius)


def _read_capped(resp, limit: int = MAX_BODY_BYTES) -> bytes:
    headers = getattr(resp, "headers", None)
    declared = headers.get("Content-Length") if headers is not None else None
    if declared is not None:
        try:
            if int(declared) > limit:
                raise UpstreamError("body too large")
        except ValueError:
            pass
    chunks, total = [], 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise UpstreamError("body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_raw(region: str, opener: Callable | None = None) -> bytes:
    url = build_url(region)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = opener or urllib.request.urlopen
    try:
        resp = opener(req, timeout=TIMEOUT_S)
        try:
            return _read_capped(resp)
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()
    except UpstreamError:
        raise
    except Exception as exc:  # network, timeout, HTTP error
        raise UpstreamError(f"fetch failed: {type(exc).__name__}") from exc


def _num(v, lo: float, hi: float) -> float | None:
    """Finite number within [lo, hi] or None. Rejects bool and strings."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    if not math.isfinite(f) or f < lo or f > hi:
        return None
    return f


def _int(v, lo: int, hi: int) -> int | None:
    f = _num(v, lo, hi)
    return None if f is None or f != int(f) else int(f)


def _source(rec: dict) -> str:
    t = rec.get("type")
    t = t if isinstance(t, str) else ""
    mlat, tisb = rec.get("mlat"), rec.get("tisb")
    if t == "mlat" or (isinstance(mlat, list) and "lat" in mlat):
        return "mlat"
    if t.startswith("tisb") or (isinstance(tisb, list) and "lat" in tisb):
        return "tisb"
    if t in ("adsb_icao", "adsb_icao_nt", "adsr_icao"):
        return "adsb"
    return "other"


def is_military(hex_: str, db_flags) -> bool:
    """Best-effort: dbFlags bit 1 when present, or the US DoD ICAO block."""
    if isinstance(db_flags, int) and not isinstance(db_flags, bool) and db_flags & DBFLAG_MILITARY:
        return True
    try:
        return _DOD_LO <= int(hex_, 16) <= _DOD_HI
    except ValueError:
        return False


def _str(v, maxlen: int, pattern) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > maxlen or not pattern.match(s):
        return None
    return s


def _opt(rec: dict, key: str, lo: float, hi: float, reason: str, stats: Counter) -> float | None:
    v = rec.get(key)
    if v is None:
        return None
    f = _num(v, lo, hi)
    if f is None:
        stats[reason] += 1
    return f


def normalize_record(rec, stats: Counter) -> State | None:
    """Return a sanitised State or None (reason counted in stats). Never raises."""
    if not isinstance(rec, dict):
        stats["not_an_object"] += 1
        return None
    hex_ = rec.get("hex")
    hex_ = hex_.strip().lower() if isinstance(hex_, str) else ""
    if not _HEX_RE.match(hex_):
        stats["bad_hex"] += 1  # also drops non-ICAO '~' addresses
        return None
    if is_military(hex_, rec.get("dbFlags")):
        stats["military"] += 1
        return None
    lat = _num(rec.get("lat"), -90, 90)
    lon = _num(rec.get("lon"), -180, 180)
    if lat is None or lon is None:
        stats["bad_position" if ("lat" in rec or "lon" in rec) else "no_position"] += 1
        return None

    ab = rec.get("alt_baro")
    on_ground = False
    alt_baro = None
    if isinstance(ab, str) and ab.strip().lower() == "ground":
        on_ground = True
    elif ab is not None:
        alt_baro = _num(ab, -2000, 60000)
        if alt_baro is None:
            stats["bad_alt_baro"] += 1

    return State(
        icao=hex_,
        callsign=_str(rec.get("flight"), 8, _CALLSIGN_RE),
        lat=lat,
        lon=lon,
        alt_baro_ft=alt_baro,
        on_ground=on_ground,
        alt_geom_ft=_opt(rec, "alt_geom", -2000, 60000, "bad_alt_geom", stats),
        gs_kt=_opt(rec, "gs", 0, 2000, "bad_gs", stats),
        track_deg=_opt(rec, "track", 0, 360, "bad_track", stats),
        baro_rate_fpm=_opt(rec, "baro_rate", -30000, 30000, "bad_baro_rate", stats),
        geom_rate_fpm=_opt(rec, "geom_rate", -30000, 30000, "bad_geom_rate", stats),
        squawk=_str(rec.get("squawk"), 4, _SQUAWK_RE),
        category=_str(rec.get("category"), 2, _CAT_RE),
        type_code=_str(rec.get("t"), 8, _TYPE_RE),
        registration=_str(rec.get("r"), 12, _REG_RE),
        source=_source(rec),
        nic=_int(rec.get("nic"), 0, 11),
        nac_p=_int(rec.get("nac_p"), 0, 11),
        seen_pos_s=_opt(rec, "seen_pos", 0, 86400, "bad_seen_pos", stats),
        rssi=_opt(rec, "rssi", -200, 50, "bad_rssi", stats),
    )


def parse_response(body: bytes, fallback_ts: float | None = None) -> tuple[Snapshot, dict]:
    """Validate an adsb.lol point response body into (Snapshot, stats)."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise UpstreamError("response is not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("ac"), list):
        raise UpstreamError("response envelope missing 'ac' list")
    now_ms = _num(data.get("now"), 0, 4e12)  # adsb.lol 'now' is epoch MILLISECONDS
    if now_ms is not None:
        ts = now_ms / 1000.0
    else:
        ts = fallback_ts if fallback_ts is not None else time.time()
    counts: Counter = Counter()
    out: list[State] = []
    seen: set[str] = set()
    for rec in data["ac"]:
        st = normalize_record(rec, counts)
        if st is None:
            continue
        if st.icao in seen:
            counts["duplicate_icao"] += 1
            continue
        seen.add(st.icao)
        out.append(st)
    return Snapshot(ts=ts, aircraft=out), {
        "records_in": len(data["ac"]),
        "records_kept": len(out),
        "dropped_by_reason": dict(counts),
    }


def fetch_snapshot(region: str = "socal", opener: Callable | None = None) -> tuple[Snapshot, dict]:
    return parse_response(fetch_raw(region, opener))
