"""GT-009 ghost flood: many new addresses appearing together with the same velocity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ghosttrack.detections.base import Context, Evaluation, Evidence, Finding, airborne_skip_reason
from ghosttrack.geo import angle_diff_deg, haversine_nm
from ghosttrack.tracker import Track

# A new address's first airborne report with speed and track must come this soon after it first
# appears, otherwise its "appearance" velocity is unknown.
_APPEARANCE_WINDOW_S = 30.0


@dataclass(frozen=True)
class Appearance:
    icao: str
    ts: float
    lat: float
    lon: float
    gs_kt: float
    track_deg: float


def _appearance(track: Track) -> Appearance | str:
    """The address's first airborne report with speed and track, or a not-evaluated reason."""
    first = track.ts_first
    reason = "no_airborne_velocity"
    for p in track.points:
        if p.ts - first > _APPEARANCE_WINDOW_S:
            break
        r = airborne_skip_reason(p, track)
        if r:
            reason = r
            continue
        st = p.state
        if st.gs_kt is None or st.track_deg is None:
            reason = "missing_velocity"
            continue
        return Appearance(track.icao, p.ts, st.lat, st.lon, st.gs_kt, st.track_deg)
    return reason


def gt009_ghost_flood(tracks: Mapping[str, Track], ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-009"]
    th = det["thresholds"]
    ev = Evaluation("GT-009", None)
    start = ctx.observation_start
    if start is None:
        start = min((t.ts_first for t in tracks.values()), default=0.0)
    warmup = float(th["warmup_s"])
    apps: list[Appearance] = []
    for t in tracks.values():
        if t.ts_first < start + warmup:
            ev.not_evaluated["present_at_start"] += 1
            continue
        a = _appearance(t)
        if isinstance(a, str):
            ev.not_evaluated[a] += 1
        else:
            apps.append(a)
    apps.sort(key=lambda a: a.ts)
    ev.samples = len(apps)
    window, radius = float(th["window_s"]), float(th["radius_nm"])
    gs_spread, trk_spread, min_new = float(th["gs_spread_kt"]), float(th["track_spread_deg"]), int(th["min_new"])

    clusters: list[set[str]] = []
    for anchor in apps:
        members = [
            a for a in apps
            if abs(a.ts - anchor.ts) <= window
            and haversine_nm(anchor.lat, anchor.lon, a.lat, a.lon) <= radius
            and abs(a.gs_kt - anchor.gs_kt) <= gs_spread
            and abs(angle_diff_deg(anchor.track_deg, a.track_deg)) <= trk_spread
        ]
        if len(members) >= min_new:
            ids = {m.icao for m in members}
            for c in clusters:
                if c & ids:
                    c |= ids
                    break
            else:
                clusters.append(ids)

    by_icao = {a.icao: a for a in apps}
    for ids in clusters:
        members = sorted((by_icao[i] for i in ids), key=lambda a: a.ts)
        t0, t1 = members[0].ts, members[-1].ts
        gs = [m.gs_kt for m in members]
        n = len(members)
        conf = round(min(0.95, 0.5 + 0.05 * (n - min_new)), 3)
        ev.findings.append(Finding(
            detection_id="GT-009",
            icao=None,
            ts_first=t0,
            ts_last=t1,
            severity=det["severity"],
            confidence=conf,
            evidence=(
                Evidence("new_addresses", n, min_new, t0),
                Evidence("addresses", [m.icao for m in members], None, t0),
                Evidence("appearance_span_s", round(t1 - t0, 1), window, t1),
                Evidence("gs_range_kt", round(max(gs) - min(gs), 1), gs_spread, t1),
            ),
            explanation=(
                f"{n} new addresses appeared within {t1 - t0:.0f} s and {radius:.0f} NM of each other with "
                f"near-identical speed (about {sum(gs) / n:.0f} kt) and track: a pattern typical of injected "
                "ghost aircraft."
            ),
            benign_causes=tuple(det["benign_causes"]),
        ))
    return ev
