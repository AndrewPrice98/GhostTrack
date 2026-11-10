"""Run every detection over a set of snapshots and summarise coverage and findings."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ghosttrack.detections.base import Context, Evaluation, Finding
from ghosttrack.detections.consistency import gt007_altitude_divergence, gt008_emergency_squawk, gt010_integrity_drop
from ghosttrack.detections.flood import gt009_ghost_flood
from ghosttrack.detections.identity import gt005_duplicate_icao, gt006_allocation_mismatch
from ghosttrack.detections.kinematics import (
    gt001_ground_speed,
    gt002_vertical_rate,
    gt003_turn_accel,
    gt004_teleport,
)
from ghosttrack.model import Snapshot
from ghosttrack.tracker import Track, Tracker, TrackerSettings, build_tracks

TRACK_DETECTIONS: dict[str, Callable[[Track, Context], Evaluation]] = {
    "GT-001": gt001_ground_speed,
    "GT-002": gt002_vertical_rate,
    "GT-003": gt003_turn_accel,
    "GT-004": gt004_teleport,
    "GT-005": gt005_duplicate_icao,
    "GT-006": gt006_allocation_mismatch,
    "GT-007": gt007_altitude_divergence,
    "GT-008": gt008_emergency_squawk,
    "GT-010": gt010_integrity_drop,
}
FLEET_DETECTIONS = {"GT-009": gt009_ghost_flood}
ALL_IDS = tuple(sorted([*TRACK_DETECTIONS, *FLEET_DETECTIONS]))


@dataclass
class DetectionSummary:
    detection_id: str
    findings: int = 0
    flagged: int = 0  # units (tracks, or the fleet) with at least one finding
    clean: int = 0
    not_evaluated: int = 0  # units where nothing could be checked
    samples: int = 0
    not_evaluated_reasons: Counter = field(default_factory=Counter)


@dataclass
class Analysis:
    tracker: Tracker
    evaluations: list[Evaluation]
    summary: dict[str, DetectionSummary]

    @property
    def findings(self) -> list[Finding]:
        out = [f for e in self.evaluations for f in e.findings]
        return sorted(out, key=lambda f: (f.ts_first, f.detection_id, f.icao or ""))

    def counts(self) -> dict[str, int]:
        return {k: v.findings for k, v in self.summary.items()}


def analyse_tracker(tracker: Tracker, ctx: Context | None = None) -> Analysis:
    """Run every detection over an existing tracker (built with the same catalog's settings)."""
    ctx = ctx or Context()
    if ctx.observation_start is None:
        ctx.observation_start = tracker.first_ts
    evals: list[Evaluation] = []
    for track in tracker.tracks.values():
        for fn in TRACK_DETECTIONS.values():
            evals.append(fn(track, ctx))
    for fn in FLEET_DETECTIONS.values():
        evals.append(fn(tracker.tracks, ctx))
    summary = {i: DetectionSummary(i) for i in ALL_IDS}
    for e in evals:
        s = summary[e.detection_id]
        s.findings += len(e.findings)
        s.samples += e.samples
        s.not_evaluated_reasons.update(e.not_evaluated)
        setattr(s, e.status, getattr(s, e.status) + 1)
    return Analysis(tracker, evals, summary)


def analyse(snapshots: Iterable[Snapshot], ctx: Context | None = None) -> Analysis:
    """Build tracks with the context catalog's timing rules, then run every detection."""
    ctx = ctx or Context()
    return analyse_tracker(build_tracks(snapshots, TrackerSettings.from_catalog(ctx.catalog)), ctx)
