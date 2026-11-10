"""GT-001 to GT-004: airborne kinematic plausibility per track segment.

Ground traffic (on_ground, C-category) and stale positions are never evaluated. MLAT/TIS-B
derived data gets the catalog's derived_source_factor on every threshold.
"""
from __future__ import annotations

import math

from ghosttrack.detections.base import (
    Context,
    Evaluation,
    Evidence,
    airborne_skip_reason,
    category_key,
    is_ground,
    group_violations,
    is_derived,
    limits,
    make_finding,
    margin_confidence,
    position_error_nm,
    source_factor,
)
from ghosttrack.geo import G_MS2, KT_TO_MS, angle_diff_deg, haversine_nm
from ghosttrack.tracker import Track


def _conf(ctx: Context, state, observed: float, threshold: float) -> float:
    common = ctx.catalog["common"]
    c = margin_confidence(observed, threshold, float(common["margin_confidence_base"]),
                          float(common["margin_confidence_cap"]))
    if is_derived(state, ctx.catalog):
        c = round(c * float(ctx.catalog["common"]["derived_source_confidence"]), 3)
    return c


def _emit(ev: Evaluation, det: dict, ctx: Context, icao: str, violations, min_samples: int, text) -> None:
    window = float(ctx.catalog["common"]["merge_window_s"])
    for group in group_violations(violations, window):
        if len(group) >= min_samples:
            ev.findings.append(make_finding(det, icao, group, text(group), catalog=ctx.catalog))


def _category_used(point_category: str | None, track: Track, ctx: Context) -> str:
    """The category whose limits were applied: the report's own, else the track's; shown as
    'A0 (unknown limits)' when it has no limits of its own."""
    raw = point_category or track.category
    key = category_key(raw, ctx.catalog)
    if raw is None:
        return "none declared (unknown limits)"
    return raw if key == raw else f"{raw} (unknown limits)"


def _categories(group) -> str:
    return " / ".join(dict.fromkeys(g[3] for g in group))


def _ceilings(group, unit: str) -> str:
    vals = sorted({round(g[1].threshold) for g in group})
    return " / ".join(f"{v} {unit}" for v in vals) + (" ceiling" if len(vals) == 1 else " ceilings")


# --------------------------------------------------------------------------- GT-001


def gt001_ground_speed(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-001"]
    ev = Evaluation("GT-001", track.icao)
    min_samples = int(det["thresholds"]["min_samples"])
    for seg in track.segments:
        viol = []
        for p in seg.points:
            reason = airborne_skip_reason(p, track)
            st = p.state
            if reason is None and st.gs_kt is None:
                reason = "missing_gs"
            lim = limits(st.category or track.category, ctx.catalog)["max_gs_kt"]
            if reason is None and lim is None:
                reason = "category_unbounded"
            if reason:
                ev.not_evaluated[reason] += 1
                continue
            ev.samples += 1
            thr = lim * source_factor(st, ctx.catalog)
            if st.gs_kt > thr:
                viol.append((p.ts, Evidence("gs_kt", st.gs_kt, thr, p.ts), _conf(ctx, st, st.gs_kt, thr),
                             _category_used(st.category, track, ctx)))
        _emit(ev, det, ctx, track.icao, viol, min_samples, lambda g: (
            f"Ground speed up to {max(x[1].observed for x in g):.0f} kt in {len(g)} reports, above the "
            f"{_ceilings(g, 'kt')} for emitter category {_categories(g)}."
        ))
    return ev


# --------------------------------------------------------------------------- GT-002


def gt002_vertical_rate(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-002"]
    th = det["thresholds"]
    ev = Evaluation("GT-002", track.icao)
    min_samples, min_dt, quantum = int(th["min_samples"]), float(th["min_dt_s"]), float(th["alt_quantum_ft"])
    for seg in track.segments:
        viol = []
        for p in seg.points:
            st = p.state
            reason = airborne_skip_reason(p, track)
            rate = st.baro_rate_fpm if st.baro_rate_fpm is not None else st.geom_rate_fpm
            if reason is None and rate is None:
                reason = "missing_vertical_rate"
            lim = limits(st.category or track.category, ctx.catalog)["max_vrate_fpm"]
            if reason is None and lim is None:
                reason = "category_unbounded"
            if reason:
                ev.not_evaluated[reason] += 1
                continue
            ev.samples += 1
            thr = lim * source_factor(st, ctx.catalog)
            if abs(rate) > thr:
                viol.append((p.ts, Evidence("vertical_rate_fpm", rate, thr, p.ts), _conf(ctx, st, abs(rate), thr),
                             _category_used(st.category, track, ctx)))
        # Rate implied by consecutive barometric altitudes at their position times. A violation
        # a->b only counts when the neighbouring pairs (point before a -> b, and a -> point after
        # b) also violate: a single corrupt altitude (a spike that returns) is then rejected
        # whichever side of the spike it sits on, while a sustained impossible climb is not.
        pts = [p for p in seg.position_points() if airborne_skip_reason(p, track) is None]
        for p in pts:
            if p.state.alt_baro_ft is None:
                ev.not_evaluated["missing_alt_baro"] += 1
        pts = [p for p in pts if p.state.alt_baro_ft is not None]
        if len(pts) == 1:
            ev.not_evaluated["insufficient_points"] += 1

        def implied(a, b):
            dt = b.t - a.t
            lim = limits(b.state.category or track.category, ctx.catalog)["max_vrate_fpm"]
            if dt < min_dt or lim is None:
                return None
            dalt = abs(b.state.alt_baro_ft - a.state.alt_baro_ft)
            thr = lim * max(source_factor(a.state, ctx.catalog), source_factor(b.state, ctx.catalog))
            return max(0.0, dalt - 2 * quantum) / dt * 60.0, thr

        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            r = implied(a, b)
            if r is None:
                continue
            ev.samples += 1
            rate, thr = r
            if rate <= thr:
                continue
            confirm = []
            if i > 0:
                confirm.append(implied(pts[i - 1], b))
            if i + 2 < len(pts):
                confirm.append(implied(a, pts[i + 2]))
            confirm = [c for c in confirm if c is not None]
            if confirm and all(c[0] > c[1] for c in confirm):
                viol.append((b.ts, Evidence("implied_vertical_rate_fpm", round(rate), thr, b.ts),
                             _conf(ctx, b.state, rate, thr), _category_used(b.state.category, track, ctx)))
        _emit(ev, det, ctx, track.icao, viol, min_samples, lambda g: (
            f"Vertical rate up to {max(abs(x[1].observed) for x in g):.0f} ft/min in {len(g)} checks, "
            f"above the {_ceilings(g, 'ft/min')} for emitter category {_categories(g)}."
        ))
    return ev


# --------------------------------------------------------------------------- GT-003


def gt003_turn_accel(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-003"]
    th = det["thresholds"]
    ev = Evaluation("GT-003", track.icao)
    tol, min_gs = float(th["tolerance"]), float(th["min_gs_kt_for_turn"])
    min_dt, max_dt = float(th["min_dt_s"]), float(th["max_dt_s"])
    takeoff_s, min_samples = float(th["takeoff_exclusion_s"]), int(th["min_samples"])
    for seg in track.segments:
        viol = []
        pts = []
        for p in seg.points:
            reason = airborne_skip_reason(p, track)
            if reason:
                ev.not_evaluated[reason] += 1
            else:
                pts.append(p)
        ground_times = [p.ts for p in seg.points if is_ground(p, track)]
        if len(pts) == 1:
            ev.not_evaluated["insufficient_points"] += 1
        for pa, pb in zip(pts, pts[1:]):
            a, b = pa.state, pb.state
            dt = pb.ts - pa.ts
            if dt < min_dt or dt > max_dt:
                ev.not_evaluated["dt_out_of_range"] += 1
                continue
            if a.gs_kt is None or b.gs_kt is None:
                ev.not_evaluated["missing_gs"] += 1
                continue
            lim = limits(b.category or track.category, ctx.catalog)
            f = max(source_factor(a, ctx.catalog), source_factor(b, ctx.catalog)) * tol
            # Along-track acceleration.
            if lim["max_accel_g"] is None:
                ev.not_evaluated["category_unbounded"] += 1
            elif any(0 <= pa.ts - g <= takeoff_s for g in ground_times):
                ev.not_evaluated["takeoff_transition"] += 1
            else:
                ev.samples += 1
                acc_g = abs(b.gs_kt - a.gs_kt) * KT_TO_MS / dt / G_MS2
                thr = lim["max_accel_g"] * f
                if acc_g > thr:
                    viol.append((pb.ts, Evidence("acceleration_g", round(acc_g, 2), round(thr, 2), pb.ts),
                                 _conf(ctx, b, acc_g, thr), pb))
            # Turn: lateral acceleration = turn rate x speed.
            if lim["max_load_g"] is None:
                ev.not_evaluated["no_turn_check_for_category"] += 1
                continue
            if a.track_deg is None or b.track_deg is None:
                ev.not_evaluated["missing_track"] += 1
                continue
            if min(a.gs_kt, b.gs_kt) < min_gs:
                ev.not_evaluated["too_slow_for_turn_check"] += 1
                continue
            ev.samples += 1
            omega = math.radians(abs(angle_diff_deg(a.track_deg, b.track_deg))) / dt
            v = (a.gs_kt + b.gs_kt) / 2 * KT_TO_MS
            lat_g = omega * v / G_MS2
            n = float(lim["max_load_g"])
            thr = math.sqrt(max(n * n - 1.0, 0.0)) * f
            if lat_g > thr:
                viol.append((pb.ts, Evidence("lateral_acceleration_g", round(lat_g, 2), round(thr, 2), pb.ts),
                             _conf(ctx, b, lat_g, thr), pb))
        _emit(ev, det, ctx, track.icao, viol, min_samples, lambda g: (
            f"{len(g)} consecutive-report pairs imply accelerations beyond what the emitter category "
            f"can pull (strongest {max(x[1].observed for x in g)} g vs limit {g[0][1].threshold} g)."
        ))
    return ev


# --------------------------------------------------------------------------- GT-004


def implied_speed_kt(pa, pb, ctx: Context, min_dt: float) -> tuple[float, float, float]:
    """(implied kt after error allowance, raw distance NM, dt s) between two track points."""
    a, b = pa.state, pb.state
    d = haversine_nm(a.lat, a.lon, b.lat, b.lon)
    d_eff = max(0.0, d - position_error_nm(a, ctx.catalog) - position_error_nm(b, ctx.catalog))
    dt = max(abs(pb.t - pa.t), min_dt)
    return d_eff / dt * 3600.0, d, pb.t - pa.t


def gt004_teleport(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-004"]
    th = det["thresholds"]
    ev = Evaluation("GT-004", track.icao)
    tol, min_jump, min_dt = float(th["tolerance"]), float(th["min_jump_nm"]), float(th["min_dt_s"])
    for seg in track.segments:
        for p in seg.points:
            if p.repeat_pos:
                continue
            if not p.position_usable:
                ev.not_evaluated["stale_position" if p.stale else "unknown_position_age"] += 1
            elif is_ground(p, track):
                ev.not_evaluated["on_ground" if p.state.on_ground else "ground_vehicle"] += 1
        pts = [p for p in seg.position_points() if not is_ground(p, track)]
        if len(pts) == 1:
            ev.not_evaluated["insufficient_points"] += 1
        viol = []
        for pa, pb in zip(pts, pts[1:]):
            lim = limits(pb.state.category or track.category, ctx.catalog)["max_gs_kt"]
            if lim is None:
                ev.not_evaluated["category_unbounded"] += 1
                continue
            ev.samples += 1
            v, d, dt = implied_speed_kt(pa, pb, ctx, min_dt)
            thr = lim * tol * max(source_factor(pa.state, ctx.catalog), source_factor(pb.state, ctx.catalog))
            if d >= min_jump and v > thr:
                viol.append((pb.ts, Evidence("implied_speed_kt", round(v), round(thr), pb.ts),
                             _conf(ctx, pb.state, v, thr), (pa, pb, d, dt)))
        for group in group_violations(viol, float(ctx.catalog["common"]["merge_window_s"])):
            biggest = max(group, key=lambda g: g[3][2])
            _, _, d, dt = biggest[3]
            text = (
                f"Position jumped {d:.1f} NM in {dt:.1f} s ({biggest[1].observed} kt implied, ceiling "
                f"{biggest[1].threshold} kt) within one track segment"
                + (f"; {len(group)} jumps in this episode." if len(group) > 1 else ".")
            )
            ev.findings.append(make_finding(det, track.icao, group, text, catalog=ctx.catalog))
    return ev
