"""Documented attack scenarios injected into recorded real traffic.

GhostTrack never transmits or decodes RF. A scenario is data only: it takes a list of recorded
snapshots, adds or alters aircraft states, and returns new snapshots in which **every injected
or altered state carries ``injected=True``**. Untouched recorded states are shared, unchanged.

Injected aircraft use invented addresses from the synthetic range ``SYNTH_FIRST..SYNTH_LAST``
(the unallocated tail of the US block, above the highest N-number address and below the DoD
block), which is never a real aircraft and is checked not to collide with the recording. Two
scenarios deliberately start from a real recorded aircraft: ICAO hijack (the same address at a
second position) and position offset (a real track shifted mid-flight); their altered states are
marked ``injected=True`` and keep the real address.

Every scenario is deterministic: the only randomness is a ``random.Random(seed)``.

References (public research on ADS-B insecurity; GhostTrack reimplements none of the attacks):
  - Costin and Francillon, "Ghost in the Air (Traffic): On insecurity of ADS-B protocol and
    practical attacks on ADS-B devices", Black Hat USA 2012.
  - Schäfer, Lenders and Martinovic, "Experimental Analysis of Attacks on Next Generation Air
    Traffic Communication", ACNS 2013 (experimental analysis of attacks on ADS-B).
  - Strohmeier, Lenders and Martinovic, "On the Security of the Automatic Dependent
    Surveillance-Broadcast Protocol", IEEE Communications Surveys & Tutorials 17(2), 2015.
  - Humphreys, Ledvina, Psiaki, O'Hanlon and Kintner, "Assessing the Spoofing Threat:
    Development of a Portable GPS Civilian Spoofer", ION GNSS 2008.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from ghosttrack.geo import destination
from ghosttrack.model import Snapshot, State

SYNTH_FIRST = 0xADF800  # first invented address
SYNTH_LAST = 0xADFFFF  # last (US block tail: above N99999 = ADF7C7, below the DoD block AE0000)

# Injection starts this long after the start of the recording; later than GT-009's warm-up.
INJECT_AFTER_S = 240.0


def synth_hex(i: int) -> str:
    n = SYNTH_FIRST + i
    if not SYNTH_FIRST <= n <= SYNTH_LAST:
        raise ValueError("synthetic address out of range")
    return f"{n:06x}"


@dataclass(frozen=True)
class ScenarioRun:
    """The result of applying a scenario to a recording."""

    scenario_id: str
    snapshots: list[Snapshot]
    inject_ts: float  # when the injected behaviour starts
    addresses: frozenset[str]  # addresses whose findings count for this scenario
    injected_states: int


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    simulates: str
    reference: str
    expected: tuple[str, ...]  # detection ids that must fire on the scenario's addresses
    build: Callable[[Sequence[Snapshot], random.Random], ScenarioRun]
    seed: int
    # A documented gap: the behaviour is a known blind spot, so no detection is expected to fire
    # (validation passes when none does). Gap scenarios are not part of ``SCENARIOS``.
    gap: bool = False

    def apply(self, snapshots: Sequence[Snapshot]) -> ScenarioRun:
        run = self.build(sorted(snapshots, key=lambda s: s.ts), random.Random(self.seed))
        return replace(run, scenario_id=self.id)


# ------------------------------------------------------------------ helpers


def _check_free(snaps: Sequence[Snapshot], addresses) -> None:
    seen = {a.icao for s in snaps for a in s.aircraft}
    clash = seen & set(addresses)
    if clash:
        raise ValueError(f"synthetic addresses collide with the recording: {sorted(clash)}")


def _inject_index(snaps: Sequence[Snapshot]) -> int:
    t0 = snaps[0].ts
    for i, s in enumerate(snaps):
        if s.ts - t0 >= INJECT_AFTER_S:
            return i
    raise ValueError("recording too short for injection")


def _ghost_state(icao: str, lat: float, lon: float, **kw) -> State:
    base = dict(
        icao=icao, callsign=None, lat=lat, lon=lon, alt_baro_ft=34000.0, on_ground=False,
        alt_geom_ft=34200.0, gs_kt=440.0, track_deg=270.0, baro_rate_fpm=0.0, geom_rate_fpm=None,
        squawk="1200", category="A3", type_code=None, registration=None, source="adsb",
        nic=8, nac_p=9, seen_pos_s=0.5, rssi=-22.0, injected=True,
    )
    base.update(kw)
    return State(**base)


def _flight_states(icao, lat, lon, track, gs, n, dt, **kw) -> list[State]:
    """A smooth, self-consistent run of n states dt seconds apart."""
    out, la, lo = [], lat, lon
    for _ in range(n):
        out.append(_ghost_state(icao, la, lo, track_deg=track, gs_kt=gs, **kw))
        la, lo = destination(la, lo, track, gs * dt / 3600.0)
    return out


def _dt(snaps: Sequence[Snapshot]) -> float:
    return (snaps[-1].ts - snaps[0].ts) / max(1, len(snaps) - 1)


def _add(snaps: Sequence[Snapshot], start: int, states_by_icao: dict[str, list[State]]):
    """New snapshots with each address's states appended from snapshot index ``start``."""
    out, count = [], 0
    for i, s in enumerate(snaps):
        extra = [seq[i - start] for seq in states_by_icao.values() if 0 <= i - start < len(seq)]
        count += len(extra)
        out.append(Snapshot(s.ts, [*s.aircraft, *extra]) if extra else s)
    return out, count


def _candidates(snaps: Sequence[Snapshot], start: int, n: int, *, min_gs: float = 200.0) -> list[str]:
    """Real fixed-wing addresses with fresh ADS-B positions, airborne and unique in every one of
    the n snapshots from ``start`` (sorted, so the seeded choice is deterministic)."""
    window = snaps[start : start + n]
    if len(window) < n:
        return []
    common: set[str] | None = None
    for s in window:
        ok = {
            a.icao for a in s.aircraft
            if not a.on_ground and a.source == "adsb" and a.seen_pos_s is not None and a.seen_pos_s < 2.0
            and (a.gs_kt or 0) >= min_gs and a.category and a.category[0] == "A" and a.category != "A7"
            and sum(b.icao == a.icao for b in s.aircraft) == 1
        }
        common = ok if common is None else common & ok
    return sorted(common or [])


# ------------------------------------------------------------------ scenarios


def _single_ghost(snaps, rng):
    start = _inject_index(snaps)
    n = min(24, len(snaps) - start)
    icao = synth_hex(rng.randrange(0, 0x100))
    _check_free(snaps, [icao])
    lat, lon = 33.2 + rng.random() * 0.4, -118.0 + rng.random() * 0.4
    seq = _flight_states(icao, lat, lon, 270.0, 1350.0, n, _dt(snaps), callsign="GHO1")
    out, count = _add(snaps, start, {icao: seq})
    return ScenarioRun("", out, snaps[start].ts, frozenset({icao}), count)


def _ghost_flood(snaps, rng):
    start = _inject_index(snaps)
    n = min(12, len(snaps) - start)
    icaos = [synth_hex(0x100 + i) for i in range(30)]
    _check_free(snaps, icaos)
    # A 6 x 5 grid at 1.5 NM spacing (about 9.6 NM corner to corner), common speed and track.
    lat0, lon0 = 33.35 + rng.random() * 0.1, -118.15 + rng.random() * 0.1
    seqs = {}
    for k, icao in enumerate(icaos):
        row, col = divmod(k, 6)
        la, lo = destination(lat0, lon0, 0.0, row * 1.5)
        la, lo = destination(la, lo, 90.0, col * 1.5)
        seqs[icao] = _flight_states(
            icao, la, lo, 270.0 + rng.uniform(-2, 2), 420.0 + rng.uniform(-3, 3), n, _dt(snaps),
            alt_baro_ft=30000.0 + 100.0 * row, alt_geom_ft=30200.0 + 100.0 * row,
            callsign=f"FRM{k:02d}",
        )
    out, count = _add(snaps, start, seqs)
    return ScenarioRun("", out, snaps[start].ts, frozenset(icaos), count)


def _icao_hijack(snaps, rng):
    start = _inject_index(snaps)
    n = min(10, len(snaps) - start)
    cands = _candidates(snaps, start, n)
    if not cands:
        raise ValueError("no suitable real aircraft for the hijack scenario")
    target = rng.choice(cands)
    bearing = rng.uniform(0, 360)
    out, count = [], 0
    for i, s in enumerate(snaps):
        k = i - start
        if 0 <= k < n and k % 2 == 1:
            # The aggregator keeps one report per address and poll, so the merged track alternates
            # real, clone, real, clone: the clone is the same state 45 NM away.
            fixed = []
            for a in s.aircraft:
                if a.icao == target:
                    la, lo = destination(a.lat, a.lon, bearing, 45.0)
                    a = replace(a, lat=la, lon=lo, injected=True)
                    count += 1
                fixed.append(a)
            out.append(Snapshot(s.ts, fixed))
        else:
            out.append(s)
    return ScenarioRun("", out, snaps[start + 1].ts, frozenset({target}), count)


def _position_offset(snaps, rng):
    start = _inject_index(snaps)
    n = len(snaps) - start
    cands = _candidates(snaps, start, min(n, 30))
    if not cands:
        raise ValueError("no suitable real aircraft for the offset scenario")
    target = rng.choice(cands)
    bearing = rng.uniform(0, 360)
    out, count = [], 0
    for i, s in enumerate(snaps):
        if i >= start:
            fixed = []
            for a in s.aircraft:
                if a.icao == target:
                    la, lo = destination(a.lat, a.lon, bearing, 20.0)
                    a = replace(a, lat=la, lon=lo, injected=True)
                    count += 1
                fixed.append(a)
            out.append(Snapshot(s.ts, fixed))
        else:
            out.append(s)
    return ScenarioRun("", out, snaps[start].ts, frozenset({target}), count)


# Per-poll drift for the gradual-drift gap scenario, and the total shift it stops at.
DRIFT_NM_PER_POLL = 0.1
DRIFT_MAX_NM = 20.0


def _gradual_drift(snaps, rng):
    """A real track dragged away from the truth a little each poll (a "drag-off")."""
    start = _inject_index(snaps)
    cands = _candidates(snaps, start, min(len(snaps) - start, 60))
    if not cands:
        raise ValueError("no suitable real aircraft for the drift scenario")
    target = rng.choice(cands)
    bearing = rng.uniform(0, 360)
    out, count = [], 0
    for i, s in enumerate(snaps):
        if i >= start:
            fixed = []
            for a in s.aircraft:
                if a.icao == target:
                    shift = min(DRIFT_MAX_NM, DRIFT_NM_PER_POLL * (i - start + 1))
                    la, lo = destination(a.lat, a.lon, bearing, shift)
                    a = replace(a, lat=la, lon=lo, injected=True)
                    count += 1
                fixed.append(a)
            out.append(Snapshot(s.ts, fixed))
        else:
            out.append(s)
    return ScenarioRun("", out, snaps[start].ts, frozenset({target}), count)


def _teleport(snaps, rng):
    start = _inject_index(snaps)
    n = min(24, len(snaps) - start)
    icao = synth_hex(0x200 + rng.randrange(0, 0x100))
    _check_free(snaps, [icao])
    half = n // 2
    lat, lon = 33.5 + rng.random() * 0.3, -118.6 + rng.random() * 0.3
    a = _flight_states(icao, lat, lon, 90.0, 430.0, half, _dt(snaps), callsign="TEL1")
    la, lo = destination(a[-1].lat, a[-1].lon, 0.0, 150.0)
    b = _flight_states(icao, la, lo, 90.0, 430.0, n - half, _dt(snaps), callsign="TEL1")
    out, count = _add(snaps, start, {icao: a + b})
    # Detection time is measured from the jump, not from the aircraft's first report.
    return ScenarioRun("", out, snaps[start + half].ts, frozenset({icao}), count)


def _altitude_spoof(snaps, rng):
    start = _inject_index(snaps)
    n = min(24, len(snaps) - start)
    icao = synth_hex(0x300 + rng.randrange(0, 0x100))
    _check_free(snaps, [icao])
    # Allowed band at 20,000 ft pressure altitude: 1,500 + 0.08 x 20,000 + 350 = 3,450 ft.
    lat, lon = 33.0 + rng.random() * 0.3, -118.9 + rng.random() * 0.3
    seq = _flight_states(icao, lat, lon, 45.0, 400.0, n, _dt(snaps), callsign="ALT1",
                         alt_baro_ft=20000.0, alt_geom_ft=28000.0)
    out, count = _add(snaps, start, {icao: seq})
    return ScenarioRun("", out, snaps[start].ts, frozenset({icao}), count)


def _registration_mismatch(snaps, rng):
    start = _inject_index(snaps)
    n = min(12, len(snaps) - start)
    us = synth_hex(0x400 + rng.randrange(0, 0x100))
    uk = synth_hex(0x500 + rng.randrange(0, 0x100))
    _check_free(snaps, [us, uk])
    a = _flight_states(us, 33.1, -118.2, 180.0, 300.0, n, _dt(snaps), callsign="REG1",
                       registration="N12345", category="A2", alt_baro_ft=18000.0, alt_geom_ft=18150.0)
    b = _flight_states(uk, 33.3, -118.7, 150.0, 310.0, n, _dt(snaps), callsign="REG2",
                       registration="G-ZZZA", category="A2", alt_baro_ft=19000.0, alt_geom_ft=19150.0)
    out, count = _add(snaps, start, {us: a, uk: b})
    return ScenarioRun("", out, snaps[start].ts, frozenset({us, uk}), count)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "single_ghost", "Single ghost aircraft",
        "One fabricated airliner that looks ordinary (category A3, squawk 1200, NIC 8) but reports "
        "a 1,350 kt ground speed, with positions that move consistently at that speed.",
        "Ghost aircraft injection: Costin and Francillon, Black Hat USA 2012; Schäfer et al., ACNS 2013.",
        ("GT-001",), _single_ghost, seed=1001,
    ),
    Scenario(
        "ghost_flood", "Ghost flood (30 in formation)",
        "Thirty fabricated aircraft appear at once inside a 10 NM area with near-identical speed "
        "and track, as a tool that fills a receiver's picture with fake traffic would produce.",
        "Flooding a display with injected aircraft: Costin and Francillon, Black Hat USA 2012; "
        "Strohmeier et al., IEEE Commun. Surveys & Tutorials 2015.",
        ("GT-009",), _ghost_flood, seed=1002,
    ),
    Scenario(
        "icao_hijack", "ICAO address hijack",
        "A real, recorded aircraft's address is also transmitted from a second position 45 NM "
        "away; the merged track alternates between the two (real, clone, real, clone, ...).",
        "Address impersonation, since ADS-B carries no authentication: Strohmeier et al., "
        "IEEE Commun. Surveys & Tutorials 2015.",
        ("GT-005",), _icao_hijack, seed=1003,
    ),
    Scenario(
        "position_offset", "Position step (20 NM)",
        "A real, recorded aircraft's reported position steps 20 NM sideways in a single report and "
        "stays shifted, as if its messages had been modified in transit. This is a step, not a "
        "gradual drag-off: it is caught at the instant of the jump (see the gradual_drift gap).",
        "Message modification (attack background: Schäfer et al., ACNS 2013). GNSS spoofing of position sources: "
        "Humphreys et al., ION GNSS 2008.",
        ("GT-004",), _position_offset, seed=1004,
    ),
    Scenario(
        "teleport", "Teleport (150 NM jump)",
        "A fabricated aircraft flies a smooth track, then its next report is 150 NM away and it "
        "continues from there.",
        "Position injection (attack background: Schäfer et al., ACNS 2013).",
        ("GT-004",), _teleport, seed=1005,
    ),
    Scenario(
        "altitude_spoof", "Altitude spoof (baro/geometric divergence)",
        "A fabricated aircraft reports a geometric altitude 8,000 ft above its pressure "
        "altitude for every report, beyond the physical pressure, temperature and geoid band.",
        "Manipulated ADS-B fields: Strohmeier et al., IEEE Commun. Surveys & Tutorials 2015.",
        ("GT-007",), _altitude_spoof, seed=1006,
    ),
    Scenario(
        "registration_mismatch", "Registration mismatch",
        "Two fabricated aircraft whose registration does not belong to their address: a valid "
        "US N-number that converts to a different address, and a British mark on a US-allocated address.",
        "Address and registration consistency per ICAO Annex 10 Vol III (Table 9-1) and Annex 7 "
        "nationality marks; no attack paper is cited for this one.",
        ("GT-006",), _registration_mismatch, seed=1007,
    ),
)


GAP_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "gradual_drift", "Gradual drift (documented gap)",
        f"A real, recorded aircraft's reported position is dragged away by {DRIFT_NM_PER_POLL} NM "
        f"per poll up to {DRIFT_MAX_NM:g} NM. Each step is small enough to stay inside the "
        "kinematic limits, so no detection fires: a slow drag-off by a consistent spoofer is a "
        "known blind spot of single-receiver plausibility checks.",
        "GNSS drag-off spoofing: Humphreys et al., ION GNSS 2008; the need for independent "
        "multilateration or receiver cross-checks: Strohmeier et al., IEEE Commun. Surveys & "
        "Tutorials 2015.",
        (), _gradual_drift, seed=1008, gap=True,
    ),
)


def scenario_by_id(scenario_id: str) -> Scenario:
    for s in (*SCENARIOS, *GAP_SCENARIOS):
        if s.id == scenario_id:
            return s
    raise KeyError(scenario_id)
