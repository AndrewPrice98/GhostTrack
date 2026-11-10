"""Builders for crafted states, flights and snapshots used by the detection tests."""
from __future__ import annotations

from collections import defaultdict

from ghosttrack.geo import destination
from ghosttrack.model import Snapshot, State

T0 = 1_794_300_000.0


def st(icao="a061d9", lat=33.9, lon=-118.4, alt=35000.0, **kw) -> State:
    base = dict(
        icao=icao, callsign="TEST1", lat=lat, lon=lon, alt_baro_ft=alt, on_ground=False,
        alt_geom_ft=None if alt is None else alt + 200.0, gs_kt=450.0, track_deg=90.0,
        baro_rate_fpm=0.0, geom_rate_fpm=None, squawk="1200", category="A3", type_code="A320",
        registration="N12345", source="adsb", nic=8, nac_p=9, seen_pos_s=0.5, rssi=-20.0,
    )
    base.update(kw)
    return State(**base)


def flight(icao="a061d9", n=20, dt=5.0, t0=T0, lat=33.9, lon=-118.4, gs=450.0, track=90.0,
           alt=35000.0, vrate=0.0, turn_dps=0.0, per_point=None, **kw) -> list[tuple[float, State]]:
    """A smooth flight: (snapshot ts, State) pairs. per_point(i, dict) may edit fields."""
    out = []
    la, lo, trk, a = lat, lon, (90.0 if track is None else track), alt
    move_gs = 450.0 if gs is None else gs
    for i in range(n):
        ts = t0 + i * dt
        fields = dict(lat=la, lon=lo, gs_kt=gs, track_deg=None if track is None else trk % 360, alt=a,
                      baro_rate_fpm=vrate, **kw)
        if a is not None and "alt_geom_ft" not in kw:
            fields["alt_geom_ft"] = a + 200.0
        if per_point:
            per_point(i, fields)
        out.append((ts, st(icao=icao, **fields)))
        la, lo = destination(la, lo, trk, move_gs * dt / 3600.0)
        trk += turn_dps * dt
        if a is not None and vrate:
            a += vrate * dt / 60.0
    return out


def snapshots(*flights) -> list[Snapshot]:
    by_ts = defaultdict(list)
    for fl in flights:
        for ts, s in fl:
            by_ts[ts].append(s)
    return [Snapshot(ts=ts, aircraft=by_ts[ts]) for ts in sorted(by_ts)]
