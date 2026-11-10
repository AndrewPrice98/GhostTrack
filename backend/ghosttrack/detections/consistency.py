"""GT-007 altitude divergence, GT-008 emergency squawk behaviour, GT-010 integrity drop."""
from __future__ import annotations

from ghosttrack.detections.base import (
    Context,
    Evaluation,
    Evidence,
    airborne_skip_reason,
    is_ground,
    group_violations,
    limits,
    make_finding,
    margin_confidence,
    source_factor,
)
from ghosttrack.detections.kinematics import implied_speed_kt
from ghosttrack.geo import angle_diff_deg
from ghosttrack.tracker import Track

# --------------------------------------------------------------------------- GT-007


def altitude_tolerance_ft(alt_baro_ft: float, th: dict) -> float:
    return float(th["pressure_band_ft"]) + float(th["temperature_fraction"]) * abs(alt_baro_ft) + float(th["geoid_ft"])


def gt007_altitude_divergence(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-007"]
    th = det["thresholds"]
    ev = Evaluation("GT-007", track.icao)
    min_samples, min_dur = int(th["min_samples"]), float(th["min_duration_s"])
    conf_base, conf_cap = float(th["confidence_base"]), float(th["confidence_cap"])
    window = float(ctx.catalog["common"]["merge_window_s"])
    for seg in track.segments:
        viol = []
        for p in seg.points:
            st = p.state
            reason = airborne_skip_reason(p, track)
            if reason is None and st.alt_baro_ft is None:
                reason = "missing_alt_baro"
            if reason is None and st.alt_geom_ft is None:
                reason = "missing_alt_geom"
            if reason:
                ev.not_evaluated[reason] += 1
                continue
            ev.samples += 1
            diff = st.alt_geom_ft - st.alt_baro_ft
            tol = altitude_tolerance_ft(st.alt_baro_ft, th)
            if abs(diff) > tol:
                viol.append((p.ts, Evidence("alt_geom_minus_baro_ft", diff, round(tol), p.ts),
                             margin_confidence(abs(diff), tol, conf_base, conf_cap), p))
        for group in group_violations(viol, window):
            if len(group) < min_samples or group[-1][0] - group[0][0] < min_dur:
                continue
            worst = max(group, key=lambda g: abs(g[1].observed))
            text = (
                f"Geometric altitude differed from pressure altitude by up to {worst[1].observed:+.0f} ft "
                f"over {group[-1][0] - group[0][0]:.0f} s ({len(group)} reports), beyond the "
                f"{worst[1].threshold} ft band allowed for pressure, temperature and geoid effects."
            )
            ev.findings.append(make_finding(det, track.icao, group, text, catalog=ctx.catalog))
    return ev


# --------------------------------------------------------------------------- GT-008


def gt008_emergency_squawk(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-008"]
    th = det["thresholds"]
    ev = Evaluation("GT-008", track.icao)
    codes = {str(c) for c in th["codes"]}
    window = float(ctx.catalog["common"]["merge_window_s"])
    conf = float(th["confidence"])
    any_emergency = False
    for seg in track.segments:
        hits = [(p.ts, None, 0.0, p) for p in seg.points if p.state.squawk in codes]
        if not hits:
            continue
        any_emergency = True
        for group in group_violations(hits, window):
            pts = [g[3] for g in group]
            code = pts[0].state.squawk
            t0, t1 = group[0][0], group[-1][0]
            ground_vehicle = [p for p in pts if is_ground(p, track) and not p.state.on_ground]
            if ground_vehicle:
                if len(ground_vehicle) < int(th["min_surface_reports"]):
                    ev.not_evaluated["insufficient_points"] += 1
                    continue
                ev.samples += 1
                cat = ground_vehicle[0].state.category or track.category
                e = Evidence("squawk", code, "not expected from a surface vehicle", t0)
                text = (f"Squawk {code} transmitted by a surface vehicle (category {cat}) in "
                        f"{len(ground_vehicle)} reports. Informational only.")
                group = [(t0, e, conf, None), (t1, e, conf, None)]
                ev.findings.append(make_finding(det, track.icao, group, text, catalog=ctx.catalog))
                continue
            if t1 - t0 < float(th["min_duration_s"]):
                ev.not_evaluated["emergency_code_too_short"] += 1
                continue
            air = [p for p in pts if airborne_skip_reason(p, track) is None]
            alts = [p.state.alt_baro_ft for p in air if p.state.alt_baro_ft is not None]
            gss = [p.state.gs_kt for p in air if p.state.gs_kt is not None]
            trks = [p.state.track_deg for p in air if p.state.track_deg is not None]
            need = int(th["min_behaviour_samples"])
            if len(alts) < need or len(gss) < need or len(trks) < need:
                ev.not_evaluated["insufficient_behaviour_data"] += 1
                continue
            ev.samples += 1
            alt_range = max(alts) - min(alts)
            gs_range = max(gss) - min(gss)
            trk_dev = max(abs(angle_diff_deg(trks[0], t)) for t in trks)
            steady = (
                alt_range < float(th["steady_alt_ft"])
                and trk_dev < float(th["steady_track_deg"])
                and gs_range < float(th["steady_gs_kt"])
            )
            if steady:
                evid = [
                    (t0, Evidence("squawk", code, sorted(codes), t0), conf, None),
                    (t1, Evidence("altitude_range_ft", alt_range, th["steady_alt_ft"], t1), conf, None),
                    (t1, Evidence("track_change_deg", round(trk_dev, 1), th["steady_track_deg"], t1), conf, None),
                    (t1, Evidence("gs_range_kt", round(gs_range, 1), th["steady_gs_kt"], t1), conf, None),
                ]
                text = (
                    f"Squawk {code} for {t1 - t0:.0f} s with no visible change in behaviour (altitude within "
                    f"{alt_range:.0f} ft, track within {trk_dev:.0f} deg, speed within {gs_range:.0f} kt). "
                    "Informational only: many genuine emergencies look like this."
                )
                ev.findings.append(make_finding(det, track.icao, evid, text, catalog=ctx.catalog))
    if not any_emergency:
        # Nothing to check: no emergency code was transmitted. Counted as evaluated-clean,
        # since the input (squawk) was examined; a track without any squawk is not evaluated.
        if any(p.state.squawk for p in track.points):
            ev.samples += 1
        else:
            ev.not_evaluated["missing_squawk"] += 1
    return ev


# --------------------------------------------------------------------------- GT-010


def gt010_integrity_drop(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-010"]
    th = det["thresholds"]
    ev = Evaluation("GT-010", track.icao)
    healthy, conf = int(th["healthy_min"]), float(th["confidence"])
    t4 = ctx.catalog["by_id"]["GT-004"]["thresholds"]
    window = float(ctx.catalog["common"]["merge_window_s"])
    for seg in track.segments:
        pts = []
        for p in seg.position_points():
            reason = airborne_skip_reason(p, track)
            if reason:
                ev.not_evaluated[reason] += 1
            elif p.state.nic is None and p.state.nac_p is None:
                ev.not_evaluated["missing_integrity"] += 1
            else:
                pts.append(p)
        viol = []
        if len(pts) == 1:
            ev.not_evaluated["insufficient_points"] += 1
        for pa, pb in zip(pts, pts[1:]):
            ev.samples += 1
            dropped = [
                f for f in ("nic", "nac_p")
                if getattr(pa.state, f) is not None and getattr(pb.state, f) is not None
                and getattr(pa.state, f) >= healthy and getattr(pb.state, f) == 0
            ]
            if not dropped:
                continue
            lim = limits(pb.state.category or track.category, ctx.catalog)["max_gs_kt"]
            v, _, _ = implied_speed_kt(pa, pb, ctx, float(t4["min_dt_s"]))
            smooth = lim is not None and v <= lim * float(t4["tolerance"]) * source_factor(pb.state, ctx.catalog)
            if not smooth:
                ev.not_evaluated["not_smooth_see_gt004"] += 1
                continue
            for f in dropped:
                viol.append((pb.ts, Evidence(f, 0, f"previous {getattr(pa.state, f)} (>= {healthy})", pb.ts), conf, pb))
        for group in group_violations(viol, window):
            fields = sorted({g[1].field for g in group})
            text = (
                f"{' and '.join(f.upper() for f in fields)} dropped from a healthy value to 0 while the "
                "track stayed smooth. Supporting indicator only (GNSS interference or a navigation "
                "source change can cause this)."
            )
            ev.findings.append(make_finding(det, track.icao, group, text, catalog=ctx.catalog))
    return ev
