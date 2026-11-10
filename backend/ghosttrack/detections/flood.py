"""GT-009 ghost flood: many new addresses appearing together with the same velocity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ghosttrack.detections.base import Context, Evaluation, Evidence, Finding, airborne_skip_reason
from ghosttrack.geo import angle_diff_deg, haversine_nm
from ghosttrack.tracker import Track

@dataclass(frozen=True)
class Appearance:
    icao: str
    ts: float
    lat: float
    lon: float
    gs_kt: float
    track_deg: float


def _appearance(track: Track, window_s: float) -> Appearance | str:
    """The address's first airborne report with speed and track within window_s of first sight,
    or a not-evaluated reason."""
    first = track.ts_first
    reason = "no_airborne_velocity"
    for p in track.points:
        if p.ts - first > window_s:
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
        a = _appearance(t, float(th["appearance_window_s"]))
        if isinstance(a, str):
            ev.not_evaluated[a] += 1
        else:
            apps.append(a)
    apps.sort(key=lambda a: a.ts)
    ev.samples = len(apps)
    window, radius = float(th["window_s"]), float(th["radius_nm"])
    gs_spread, trk_spread, min_new = float(th["gs_spread_kt"]), float(th["track_spread_deg"]), int(th["min_new"])

    def compatible(a: Appearance, b: Appearance) -> bool:
        return (
            abs(a.ts - b.ts) <= window
            and haversine_nm(a.lat, a.lon, b.lat, b.lon) <= radius
            and abs(a.gs_kt - b.gs_kt) <= gs_spread
            and abs(angle_diff_deg(a.track_deg, b.track_deg)) <= trk_spread
        )

    # Greedy clique per anchor: add the anchor's neighbours (closest in time first) only when
    # compatible with every member already in, so every pair meets all four limits.
    candidates: list[list[Appearance]] = []
    for anchor in apps:
        neighbours = sorted((a for a in apps if a is not anchor and compatible(anchor, a)),
                            key=lambda a: (abs(a.ts - anchor.ts), a.icao))
        members = [anchor]
        for a in neighbours:
            if all(compatible(a, m) for m in members):
                members.append(a)
        if len(members) >= min_new:
            candidates.append(members)
    # Largest clusters first; an address is reported in at most one cluster.
    clusters: list[set[str]] = []
    used: set[str] = set()
    for members in sorted(candidates, key=lambda m: (-len(m), min(a.ts for a in m))):
        ids = {a.icao for a in members}
        if ids & used:
            continue
        used |= ids
        clusters.append(ids)

    by_icao = {a.icao: a for a in apps}
    for ids in clusters:
        members = sorted((by_icao[i] for i in ids), key=lambda a: a.ts)
        t0, t1 = members[0].ts, members[-1].ts
        gs = [m.gs_kt for m in members]
        n = len(members)
        conf = round(min(float(th["confidence_cap"]),
                         float(th["confidence_base"]) + float(th["confidence_step"]) * (n - min_new)), 3)
        ev.findings.append(Finding(
            detection_id="GT-009",
            icao=None,
            ts_first=t0,
            ts_last=t1,
            severity=det["severity"],
            confidence=conf,
            evidence=(
                Evidence("new_addresses", n, min_new, t0),
                Evidence("addresses", sorted(m.icao for m in members), None, t0),
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
