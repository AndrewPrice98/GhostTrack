"""GT-005 duplicate ICAO address and GT-006 ICAO allocation mismatch."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ghosttrack import icao as icaofacts
from ghosttrack.detections.base import Context, Evaluation, Evidence, group_violations, is_derived, make_finding
from ghosttrack.detections.kinematics import implied_speed_kt
from ghosttrack.tracker import Track

# --------------------------------------------------------------------------- GT-005


def gt005_duplicate_icao(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-005"]
    th = det["thresholds"]
    ev = Evaluation("GT-005", track.icao)
    mach3, min_sep, min_dt = float(th["mach3_kt"]), float(th["min_separation_nm"]), float(th["min_dt_s"])
    min_alt = int(th["min_alternations"])
    conf_sim, conf_alt = float(th["confidence_simultaneous"]), float(th["confidence_alternating"])

    def conf(base: float, *points) -> float:
        if any(is_derived(p.state, ctx.catalog) for p in points):
            return round(base * float(ctx.catalog["common"]["derived_source_confidence"]), 3)
        return base

    for seg in track.segments:
        for p in seg.points:
            if not p.repeat_pos and not p.position_usable:
                ev.not_evaluated["stale_position" if p.stale else "unknown_position_age"] += 1
        pts = seg.position_points()
        samples_before = ev.samples
        viol = []
        # (1) Two reports of the address in the same snapshot, far apart. One bad decode cannot
        # produce two reports, so a single occurrence is enough.
        by_ts = defaultdict(list)
        for p in pts:
            by_ts[p.ts].append(p)
        for ts, group in by_ts.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    ev.samples += 1
                    a, b = group[i], group[j]
                    v, d, _ = implied_speed_kt(a, b, ctx, min_dt)
                    if d >= min_sep and v > mach3:
                        viol.append((ts, Evidence("simultaneous_separation_nm", round(d, 1), min_sep, ts),
                                     conf(conf_sim, a, b), ("simultaneous", d)))
        # (2) Out-and-back: A -> B -> A with both legs faster than Mach 3 and A -> A plausible.
        # One out-and-back is a single outlier (GT-004); an episode needs min_alternations.
        for i in range(len(pts) - 2):
            p0, p1, p2 = pts[i], pts[i + 1], pts[i + 2]
            if p0.ts == p1.ts or p1.ts == p2.ts:
                continue  # same-snapshot pairs are handled above
            ev.samples += 1
            v01, d01, _ = implied_speed_kt(p0, p1, ctx, min_dt)
            v12, d12, _ = implied_speed_kt(p1, p2, ctx, min_dt)
            v02, _, _ = implied_speed_kt(p0, p2, ctx, min_dt)
            if d01 >= min_sep and d12 >= min_sep and v01 > mach3 and v12 > mach3 and v02 <= mach3:
                viol.append((p1.ts, Evidence("alternating_jump_kt", round(min(v01, v12)), mach3, p1.ts),
                             conf(conf_alt, p0, p1, p2), ("alternating", d01)))
        if pts and ev.samples == samples_before:
            ev.not_evaluated["insufficient_points"] += 1
        for group in group_violations(viol, float(ctx.catalog["common"]["merge_window_s"])):
            simultaneous = sum(1 for g in group if g[3][0] == "simultaneous")
            alternations = len(group) - simultaneous
            if not simultaneous and alternations < min_alt:
                continue
            dmax = max(g[3][1] for g in group)
            how = []
            if simultaneous:
                how.append(f"{simultaneous} time(s) in the same snapshot")
            if alternations:
                how.append(f"{alternations} out-and-back jump(s) faster than Mach 3")
            text = (
                f"Address {track.icao} was reported at two positions up to {dmax:.1f} NM apart at "
                f"effectively the same time ({'; '.join(how)}): two transmitters appear to share "
                "this address."
            )
            ev.findings.append(make_finding(det, track.icao, group, text, catalog=ctx.catalog))
    return ev


# --------------------------------------------------------------------------- GT-006


def gt006_allocation_mismatch(track: Track, ctx: Context) -> Evaluation:
    det = ctx.catalog["by_id"]["GT-006"]
    th = det["thresholds"]
    ev = Evaluation("GT-006", track.icao)
    hex_ = track.icao.strip().lower()
    reg = track.registration
    if not reg:
        ev.not_evaluated["missing_registration"] += 1
        return ev
    ts0, ts1 = track.ts_first, track.ts_last
    block = icaofacts.block_for_address(hex_)
    addr_state = None if block is None or block.special else block.state
    reg_up = reg.strip().upper()
    if icaofacts.is_valid_nnumber(reg_up):
        expected = icaofacts.nnumber_to_icao(reg_up)
        ev.samples += 1
        if expected != hex_:
            where = f"a {addr_state} block" if addr_state else "no allocated block"
            text = (
                f"Registration {reg_up} is a US N-number, whose Mode S address is {expected}, but the "
                f"aircraft transmits {hex_} ({where})."
            )
            e = Evidence("icao", hex_, expected, ts0)
            group = [(ts0, e, float(th["confidence_nnumber"]), None)]
            ev.findings.append(_span(make_finding(det, track.icao, group, text, catalog=ctx.catalog), ts0, ts1))
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
            f"Address {hex_} is in the {addr_state} allocation (ICAO Annex 10 Table 9-1) but "
            f"registration {reg_up} carries the nationality mark of {', '.join(reg_states)}."
        )
        e = Evidence("address_state", addr_state, " / ".join(reg_states), ts0)
        group = [(ts0, e, float(th["confidence_state"]), None)]
        ev.findings.append(_span(make_finding(det, track.icao, group, text, catalog=ctx.catalog), ts0, ts1))
    return ev


def _span(f, ts0: float, ts1: float):
    return replace(f, ts_first=ts0, ts_last=ts1)
