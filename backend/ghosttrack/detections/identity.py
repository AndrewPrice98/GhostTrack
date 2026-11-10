"""GT-005 duplicate ICAO address and GT-006 ICAO allocation mismatch."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ghosttrack import icao as icaofacts
from ghosttrack.detections.base import Context, Evaluation, Evidence, group_violations, make_finding
from ghosttrack.detections.kinematics import implied_speed_kt
from ghosttrack.tracker import Track

# --------------------------------------------------------------------------- GT-005


def gt005_duplicate_icao(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-005"]
    th = det["thresholds"]
    ev = Evaluation("GT-005", track.icao)
    mach3, min_sep, min_dt = float(th["mach3_kt"]), float(th["min_separation_nm"]), float(th["min_dt_s"])
    for seg in track.segments:
        for p in seg.points:
            if not p.repeat_pos and not p.position_usable:
                ev.not_evaluated["stale_position" if p.stale else "unknown_position_age"] += 1
        pts = seg.position_points()
        viol = []
        # (1) Two reports of the address in the same snapshot, far apart.
        by_ts = defaultdict(list)
        for p in pts:
            by_ts[p.ts].append(p)
        for ts, group in by_ts.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    ev.samples += 1
                    v, d, _ = implied_speed_kt(group[i], group[j], ctx, min_dt)
                    if d >= min_sep and v > mach3:
                        viol.append((ts, Evidence("simultaneous_separation_nm", round(d, 1), min_sep, ts), 0.9,
                                     (group[i], group[j], d)))
        # (2) Out-and-back: A -> B -> A with both legs faster than Mach 3 and A -> A plausible.
        for i in range(len(pts) - 2):
            p0, p1, p2 = pts[i], pts[i + 1], pts[i + 2]
            if p0.ts == p1.ts or p1.ts == p2.ts:
                continue  # same-snapshot pairs are handled above
            ev.samples += 1
            v01, d01, _ = implied_speed_kt(p0, p1, ctx, min_dt)
            v12, d12, _ = implied_speed_kt(p1, p2, ctx, min_dt)
            v02, _, _ = implied_speed_kt(p0, p2, ctx, min_dt)
            if d01 >= min_sep and d12 >= min_sep and v01 > mach3 and v12 > mach3 and v02 <= mach3:
                viol.append((p1.ts, Evidence("alternating_jump_kt", round(min(v01, v12)), mach3, p1.ts), 0.8,
                             (p0, p1, d01)))
        for group in group_violations(viol, float(ctx.catalog["common"]["merge_window_s"])):
            dmax = max(g[3][2] for g in group)
            text = (
                f"Address {track.icao} was reported at two positions up to {dmax:.1f} NM apart at "
                f"effectively the same time (faster than Mach 3 between them) {len(group)} time(s): "
                "two transmitters appear to share this address."
            )
            ev.findings.append(make_finding(det, track.icao, group, text))
    return ev


# --------------------------------------------------------------------------- GT-006


def gt006_allocation_mismatch(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-006"]
    ev = Evaluation("GT-006", track.icao)
    reg = track.registration
    if not reg:
        ev.not_evaluated["missing_registration"] += 1
        return ev
    ts0, ts1 = track.ts_first, track.ts_last
    block = icaofacts.block_for_address(track.icao)
    addr_state = None if block is None or block.special else block.state
    reg_up = reg.strip().upper()
    if icaofacts.is_valid_nnumber(reg_up):
        expected = icaofacts.nnumber_to_icao(reg_up)
        ev.samples += 1
        if expected != track.icao:
            where = f"a {addr_state} block" if addr_state else "no allocated block"
            text = (
                f"Registration {reg_up} is a US N-number, whose Mode S address is {expected}, but the "
                f"aircraft transmits {track.icao} ({where})."
            )
            e = Evidence("icao", track.icao, expected, ts0)
            ev.findings.append(_span(make_finding(det, track.icao, [(ts0, e, 0.6, None)], text), ts0, ts1))
        return ev
    reg_states = icaofacts.states_for_registration(reg_up)
    if reg_states is None:
        ev.not_evaluated["unknown_registration_prefix"] += 1
        return ev
    if block is None:
        ev.not_evaluated["address_not_in_table"] += 1
        return ev
    if block.special:
        ev.not_evaluated["icao_special_block"] += 1
        return ev
    ev.samples += 1
    if addr_state not in reg_states:
        text = (
            f"Address {track.icao} is in the {addr_state} allocation (ICAO Annex 10 Table 9-1) but "
            f"registration {reg_up} carries the nationality mark of {', '.join(reg_states)}."
        )
        e = Evidence("address_state", addr_state, " / ".join(reg_states), ts0)
        ev.findings.append(_span(make_finding(det, track.icao, [(ts0, e, 0.5, None)], text), ts0, ts1))
    return ev


def _span(f, ts0: float, ts1: float):
    return replace(f, ts_first=ts0, ts_last=ts1)
