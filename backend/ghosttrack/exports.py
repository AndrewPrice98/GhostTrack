"""Evidence packs and summary reports.

- ``evidence_json`` / ``evidence_html``: one alert as a JSON document or a self-contained,
  printable HTML page (inline CSS, no scripts, every dynamic string escaped).
- ``summary_report_html`` / ``summary_report_md``: alerts over the last 24 h, validation results
  and the false-positive measurements.

Everything is deterministic given its inputs: the caller passes ``now`` (epoch seconds).
Injected scenario data is always labelled; ADS-B data is always attributed (ODbL).
"""
from __future__ import annotations

import html
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from ghosttrack import __version__
from ghosttrack.catalog import load_catalog
from ghosttrack.model import State

ATTRIBUTION = "ADS-B data from adsb.lol, available under the Open Database License (ODbL) 1.0."
DISCLAIMER = "Plausibility analysis of decoded ADS-B data; not a certified surveillance system."
INJECTED_BANNER = "INJECTED TEST DATA \u2014 not real air traffic"
SOURCE_LABELS = {"recorded": "recorded capture (demo replay)", "live": "live feed"}
SUMMARY_WINDOW_S = 24 * 3600.0

_UNITS = {
    "gs_kt": "kt", "speed_kt": "kt", "alt_baro_ft": "ft", "alt_geom_ft": "ft", "altitude_ft": "ft",
    "baro_rate_fpm": "ft/min", "geom_rate_fpm": "ft/min", "vrate_fpm": "ft/min",
    "distance_nm": "NM", "jump_nm": "NM", "dt_s": "s", "gap_s": "s", "track_deg": "deg",
    "turn_rate_dps": "deg/s", "accel_g": "g", "divergence_ft": "ft",
}


def _unit(field: str) -> str:
    if field in _UNITS:
        return _UNITS[field]
    for suffix, unit in (("_kt", "kt"), ("_ft", "ft"), ("_fpm", "ft/min"), ("_nm", "NM"),
                         ("_s", "s"), ("_deg", "deg"), ("_dps", "deg/s"), ("_g", "g")):
        if field.endswith(suffix):
            return unit
    return ""


def _utc(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    return str(value)


def _points(track_points: Iterable) -> list[tuple[float, dict]]:
    """Normalise (ts, State) pairs or dicts with a ``ts`` key into (ts, dict) sorted by time."""
    out = []
    for p in track_points:
        if isinstance(p, tuple):
            ts, s = p
            out.append((float(ts), s.to_dict() if isinstance(s, State) else dict(s)))
        else:
            d = dict(p)
            out.append((float(d.pop("ts")), d))
    out.sort(key=lambda x: x[0])
    return out


def _detection(alert: dict) -> tuple[str, dict]:
    det = load_catalog()["by_id"].get(alert["detection_id"], {})
    return det.get("title", alert["detection_id"]), det


def _benign(alert: dict, det: dict) -> list[str]:
    return list(alert.get("benign_causes") or det.get("benign_causes") or [])


def _source_line(alert: dict) -> str:
    label = SOURCE_LABELS.get(alert.get("source"), alert.get("source"))
    return f"{ATTRIBUTION} Source of this alert: {label}."


# ------------------------------------------------------------------ per-alert pack


def evidence_json(alert: dict, track_points: Iterable, *, now: float) -> bytes:
    title, det = _detection(alert)
    pts = _points(track_points)
    doc = {
        "format": "ghosttrack-evidence-1",
        "generated_utc": _utc(now),
        "ghosttrack_version": __version__,
        "injected_test_data": bool(alert.get("injected")),
        "injected_notice": INJECTED_BANNER if alert.get("injected") else None,
        "alert": {
            "id": alert.get("id"),
            "detection_id": alert["detection_id"],
            "title": title,
            "severity": alert["severity"],
            "confidence": alert.get("confidence"),
            "icao": alert.get("icao"),
            "callsign": alert.get("callsign"),
            "first_seen_utc": _utc(alert["first_ts"]),
            "last_seen_utc": _utc(alert["last_ts"]),
            "first_ts": alert["first_ts"],
            "last_ts": alert["last_ts"],
            "source": alert.get("source"),
            "status": alert.get("status"),
            "explanation": alert.get("explanation", ""),
            "possible_benign_causes": _benign(alert, det),
        },
        "failing_checks": [
            {"field": e["field"], "unit": _unit(e["field"]), "observed": e["observed"],
             "threshold": e["threshold"], "ts": e["ts"], "time_utc": _utc(e["ts"])}
            for e in alert.get("evidence", [])
        ],
        "track_points": [{"ts": ts, "time_utc": _utc(ts), **d} for ts, d in pts],
        "data_source": _source_line(alert),
        "disclaimer": DISCLAIMER,
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


_CSS = """
body{font:14px/1.5 system-ui,Segoe UI,Arial,sans-serif;color:#111;background:#fff;margin:24px auto;max-width:980px;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ccc;padding-bottom:2px}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{border:1px solid #bbb;padding:3px 6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#eee}.banner{background:#fff3cd;border:3px solid #b8860b;color:#5c4400;font-weight:700;font-size:18px;padding:10px 14px;margin:12px 0;text-align:center}
.meta td:first-child{width:160px;font-weight:600;background:#f6f6f6}.small{font-size:12px;color:#444}
.sev-high{color:#b00020;font-weight:700}.sev-medium{color:#b35900;font-weight:700}
svg{border:1px solid #bbb;background:#fafafa;max-width:100%;height:auto}
@media print{body{margin:0;max-width:none}}
"""


def _page(title: str, body: str) -> bytes:
    doc = (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(title)}</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<style>{_CSS}</style></head><body>\n{body}\n</body></html>\n"
    )
    return doc.encode("utf-8")


def _sketch(pts: list[tuple[float, dict]], w: int = 480, h: int = 240) -> str:
    """Tiny inline SVG of the track (equirectangular, fitted to the box). Empty if < 2 points."""
    ll = [(d.get("lat"), d.get("lon")) for _, d in pts if d.get("lat") is not None and d.get("lon") is not None]
    if len(ll) < 2:
        return ""
    lats, lons = [p[0] for p in ll], [p[1] for p in ll]
    kx = math.cos(math.radians(sum(lats) / len(lats)))
    xs = [(lo - min(lons)) * kx for lo in lons]
    ys = [max(lats) - la for la in lats]
    span = max(max(xs), max(ys), 1e-9)
    pad = 12
    scale = (min(w, h) - 2 * pad) / span
    coords = [(pad + x * scale, pad + y * scale) for x, y in zip(xs, ys)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    sx, sy = coords[0]
    ex, ey = coords[-1]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        'role="img" aria-label="Track sketch (north up, not to scale)">'
        f'<polyline points="{path}" fill="none" stroke="#1a56a0" stroke-width="1.5"/>'
        f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="4" fill="#2e7d32"/>'
        f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="#b00020"/>'
        f'<text x="6" y="{h - 6}" font-size="10" fill="#444">Track sketch, north up, not to scale. '
        "green = first point, red = last point</text></svg>"
    )


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def evidence_html(alert: dict, track_points: Iterable, *, now: float) -> bytes:
    title, det = _detection(alert)
    pts = _points(track_points)
    injected = bool(alert.get("injected"))
    parts = [f"<h1>{_e(alert['detection_id'])} &mdash; {_e(title)}</h1>"]
    parts.append('<div class="small">GhostTrack evidence pack</div>')
    if injected:
        parts.append(f'<div class="banner" role="alert">{_e(INJECTED_BANNER)}</div>')
    sev = _e(alert["severity"])
    conf = alert.get("confidence")
    meta = [
        ("Detection", f"{_e(alert['detection_id'])} {_e(title)}"),
        ("Severity", f'<span class="sev-{sev}">{sev}</span>'),
        ("Confidence", _e(f"{conf:.2f}" if isinstance(conf, (int, float)) else conf)),
        ("ICAO hex", _e(alert.get("icao"))),
        ("Callsign", _e(alert.get("callsign"))),
        ("First seen", _e(_utc(alert["first_ts"]))),
        ("Last seen", _e(_utc(alert["last_ts"]))),
        ("Status", _e(alert.get("status"))),
    ]
    parts.append('<table class="meta">' + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in meta) + "</table>")

    parts.append("<h2>Failing checks</h2>")
    ev = alert.get("evidence", [])
    parts.append(_table(
        ["Check", "Observed", "Threshold", "Unit", "Time (UTC)"],
        [(e["field"], _fmt(e["observed"]), _fmt(e["threshold"]), _unit(e["field"]), _utc(e["ts"])) for e in ev],
    ) if ev else "<p>No evidence entries recorded.</p>")

    parts.append("<h2>What this means</h2>")
    parts.append(f"<p>{_e(alert.get('explanation') or det.get('rationale', ''))}</p>")
    benign = _benign(alert, det)
    parts.append("<h2>Possible benign causes</h2>")
    parts.append("<ul>" + "".join(f"<li>{_e(b)}</li>" for b in benign) + "</ul>" if benign else "<p>None listed.</p>")

    parts.append("<h2>Track points</h2>")
    sketch = _sketch(pts)
    if sketch:
        parts.append(sketch)
    if pts:
        parts.append(_table(
            ["Time (UTC)", "Lat", "Lon", "Alt baro ft", "Alt geo ft", "GS kt", "Track deg", "Source", "NIC", "NACp"],
            [(_utc(ts), _fmt(d.get("lat"), 5), _fmt(d.get("lon"), 5), _fmt(d.get("alt_baro_ft"), 0),
              _fmt(d.get("alt_geom_ft"), 0), _fmt(d.get("gs_kt")), _fmt(d.get("track_deg")),
              d.get("source"), d.get("nic"), d.get("nac_p")) for ts, d in pts],
        ))
    else:
        parts.append("<p>No track points available (outside the retained window).</p>")

    parts.append("<h2>Data source</h2>")
    parts.append(f"<p>{_e(_source_line(alert))}</p>")
    parts.append(
        f'<p class="small">Generated {_e(_utc(now))} by GhostTrack {_e(__version__)}.<br>{_e(DISCLAIMER)}</p>'
    )
    return _page(f"GhostTrack evidence {alert['detection_id']} {alert.get('icao') or ''}", "\n".join(parts))


# ------------------------------------------------------------------ summary report


def _summary_data(alerts: Sequence[dict], validation: Sequence[Any], gaps: Sequence[Any],
                  fps: Sequence[Any], now: float) -> dict:
    cat = load_catalog()
    recent = [a for a in alerts if a["last_ts"] >= now - SUMMARY_WINDOW_S]
    by_det: Counter = Counter(a["detection_id"] for a in recent)
    rows = [(d["id"], d["title"], by_det.get(d["id"], 0)) for d in cat["detections"]]
    inj = sum(1 for a in recent if a.get("injected"))
    v = cat["validation"]
    return {
        "generated": _utc(now), "version": __version__, "window": "last 24 hours",
        "total": len(recent), "injected": inj, "real": len(recent) - inj, "by_detection": rows,
        "scenarios": [(r.scenario, ",".join(r.expected) or "-", ",".join(r.fired) or "-",
                       "PASS" if r.passed else "FAIL",
                       "-" if r.time_to_detect_s is None else f"{r.time_to_detect_s:.0f} s") for r in validation],
        "gaps": [(r.scenario, ",".join(r.fired) or "none", "GAP HOLDS" if r.passed else "GAP CLOSED") for r in gaps],
        "fps": [(r.recording, f"{r.hours:.2f}", r.tracks, r.counted, f"{r.per_hour:.2f}",
                 ", ".join(f"{d}/{s}={n}" for (d, s), n in r.counts.items()) or "none") for r in fps],
        "budget": float(v["fp_budget_per_hour"]), "budget_sev": v["budget_min_severity"],
    }


def summary_report_html(alerts: Sequence[dict], validation: Sequence[Any] = (), fps: Sequence[Any] = (),
                        gaps: Sequence[Any] = (), *, now: float) -> bytes:
    s = _summary_data(alerts, validation, gaps, fps, now)
    p = ["<h1>GhostTrack summary report</h1>",
         f'<div class="small">Generated {_e(s["generated"])}, GhostTrack {_e(s["version"])}</div>']
    p.append(f"<h2>Alerts, {_e(s['window'])}</h2>")
    p.append(f"<p>{s['total']} alerts: {s['real']} from real/recorded traffic, {s['injected']} from "
             "<strong>injected test data</strong> (not real air traffic).</p>")
    p.append(_table(["Detection", "Title", "Alerts"], s["by_detection"]))
    p.append("<h2>Validation: injected scenarios</h2>")
    p.append(_table(["Scenario", "Expected", "Fired", "Result", "Time to detect"], s["scenarios"])
             if s["scenarios"] else "<p>No validation results available.</p>")
    if s["gaps"]:
        p.append("<h3>Documented gaps (known blind spots)</h3>")
        p.append(_table(["Scenario", "Fired", "Result"], s["gaps"]))
    p.append("<h2>False-positive measurement (clean recordings)</h2>")
    p.append(f"<p>Budget: {s['budget']:g} findings per hour at severity {_e(s['budget_sev'])} or higher.</p>")
    p.append(_table(["Recording", "Hours", "Tracks", "Counted", "Per hour", "Findings"], s["fps"])
             if s["fps"] else "<p>No measurements available.</p>")
    p.append("<h2>Data sources and licences</h2><ul>"
             f"<li>{_e(ATTRIBUTION)}</li>"
             "<li>Committed recorded captures ship under ODbL; code is MIT.</li>"
             "<li>Natural Earth outlines: public domain.</li>"
             "<li>ICAO address allocation: ICAO Annex 10 Vol III.</li></ul>")
    p.append(f'<p class="small">{_e(DISCLAIMER)}</p>')
    return _page("GhostTrack summary report", "\n".join(p))


def _md(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_md(c) for c in r) + " |" for r in rows]
    return out


def summary_report_md(alerts: Sequence[dict], validation: Sequence[Any] = (), fps: Sequence[Any] = (),
                      gaps: Sequence[Any] = (), *, now: float) -> str:
    s = _summary_data(alerts, validation, gaps, fps, now)
    out = ["# GhostTrack summary report", "", f"Generated {s['generated']}, GhostTrack {s['version']}", "",
           f"## Alerts, {s['window']}", "",
           f"{s['total']} alerts: {s['real']} from real/recorded traffic, {s['injected']} from "
           "**injected test data** (not real air traffic).", ""]
    out += _md_table(["Detection", "Title", "Alerts"], s["by_detection"])
    out += ["", "## Validation: injected scenarios", ""]
    out += (_md_table(["Scenario", "Expected", "Fired", "Result", "Time to detect"], s["scenarios"])
            if s["scenarios"] else ["No validation results available."])
    if s["gaps"]:
        out += ["", "### Documented gaps (known blind spots)", ""]
        out += _md_table(["Scenario", "Fired", "Result"], s["gaps"])
    out += ["", "## False-positive measurement (clean recordings)", "",
            f"Budget: {s['budget']:g} findings per hour at severity {s['budget_sev']} or higher.", ""]
    out += (_md_table(["Recording", "Hours", "Tracks", "Counted", "Per hour", "Findings"], s["fps"])
            if s["fps"] else ["No measurements available."])
    out += ["", "## Data sources and licences", "", f"- {ATTRIBUTION}",
            "- Committed recorded captures ship under ODbL; code is MIT.",
            "- Natural Earth outlines: public domain.", "- ICAO address allocation: ICAO Annex 10 Vol III.", "",
            f"_{DISCLAIMER}_", ""]
    return "\n".join(out)
