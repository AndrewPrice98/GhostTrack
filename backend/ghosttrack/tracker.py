"""Per-aircraft tracks keyed by ICAO 24-bit address, split into segments at gaps.

Timing. A snapshot is stamped with the upstream ``now``; each aircraft's position was last
updated ``seen_pos`` seconds earlier. With 5 s polling, using the snapshot time as the position
time would turn a repeated or late position into a fake jump, so every point carries its true
position time ``t_pos = snapshot ts - seen_pos``. Positions older than 60 s are "stale" and never
feed kinematic checks; a position without ``seen_pos`` has an unknown age and is treated the same
way for position-based checks.

Segments. A gap longer than 5 minutes (``GAP_S``) between consecutive appearances, or between
consecutive position times, starts a new segment. Detections never compare points across
segments, so a long gap is never treated as a teleport.

Repeats. adsb.lol returns the last known position until a new one arrives, so the same position
appears in several snapshots. A point whose position time and coordinates equal the previous
point's is marked ``repeat_pos``: position-based checks skip it, velocity-based checks may still
use its (possibly newer) speed and track.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from ghosttrack.model import Snapshot, State

GAP_S = 300.0
STALE_POS_S = 60.0
_SAME_TIME_S = 0.25


def is_ground_category(category: str | None) -> bool:
    """Emitter set C (surface vehicles, obstructions, reserved): never airborne kinematics."""
    return isinstance(category, str) and category.startswith("C")


@dataclass(frozen=True)
class TrackPoint:
    ts: float  # snapshot time (epoch s)
    state: State
    repeat_pos: bool = False

    @property
    def t_pos(self) -> float | None:
        sp = self.state.seen_pos_s
        return None if sp is None else self.ts - sp

    @property
    def t(self) -> float:
        """Best time for this point's position: position time when known, else snapshot time."""
        tp = self.t_pos
        return self.ts if tp is None else tp

    @property
    def stale(self) -> bool:
        sp = self.state.seen_pos_s
        return sp is not None and sp > STALE_POS_S

    @property
    def position_usable(self) -> bool:
        """Fresh position with a known age: usable for position-based kinematics."""
        return self.state.seen_pos_s is not None and not self.stale

    @property
    def ground(self) -> bool:
        return self.state.on_ground or is_ground_category(self.state.category)


@dataclass
class Segment:
    points: list[TrackPoint] = field(default_factory=list)

    @property
    def ts_first(self) -> float:
        return self.points[0].ts

    @property
    def ts_last(self) -> float:
        return self.points[-1].ts

    def position_points(self) -> list[TrackPoint]:
        """Points with a new, fresh position, in position-time order."""
        pts = [p for p in self.points if p.position_usable and not p.repeat_pos]
        return sorted(pts, key=lambda p: (p.t, p.ts))


@dataclass
class Track:
    icao: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def points(self) -> list[TrackPoint]:
        return [p for s in self.segments for p in s.points]

    def _mode(self, attr: str) -> str | None:
        c = Counter(getattr(p.state, attr) for p in self.points if getattr(p.state, attr))
        return c.most_common(1)[0][0] if c else None

    @property
    def category(self) -> str | None:
        return self._mode("category")

    @property
    def registration(self) -> str | None:
        return self._mode("registration")

    @property
    def callsign(self) -> str | None:
        return self._mode("callsign")

    @property
    def ts_first(self) -> float:
        return self.segments[0].ts_first

    @property
    def ts_last(self) -> float:
        return self.segments[-1].ts_last


class Tracker:
    """Incremental track builder. Feed snapshots in time order with ``update``."""

    def __init__(self) -> None:
        self.tracks: dict[str, Track] = {}
        self.first_ts: float | None = None
        self.last_ts: float | None = None

    def update(self, snap: Snapshot) -> None:
        if self.first_ts is None:
            self.first_ts = snap.ts
        self.last_ts = snap.ts if self.last_ts is None else max(self.last_ts, snap.ts)
        for st in snap.aircraft:
            self._add(snap.ts, st)

    def _add(self, ts: float, st: State) -> None:
        track = self.tracks.get(st.icao)
        if track is None:
            track = self.tracks[st.icao] = Track(st.icao)
        point = TrackPoint(ts, st)
        if not track.segments:
            track.segments.append(Segment([point]))
            return
        seg = track.segments[-1]
        prev = seg.points[-1]
        if ts - prev.ts > GAP_S or point.t - prev.t > GAP_S:
            track.segments.append(Segment([point]))
            return
        tp, ptp = point.t_pos, prev.t_pos
        if (
            tp is not None
            and ptp is not None
            and abs(tp - ptp) < _SAME_TIME_S
            and st.lat == prev.state.lat
            and st.lon == prev.state.lon
        ):
            point = TrackPoint(ts, st, repeat_pos=True)
        seg.points.append(point)


def build_tracks(snapshots: Iterable[Snapshot]) -> Tracker:
    tr = Tracker()
    for snap in sorted(snapshots, key=lambda s: s.ts):
        tr.update(snap)
    return tr
