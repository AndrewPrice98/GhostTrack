"""Shared types for detections: Finding, Evidence, Evaluation, Context and the catalog."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ghosttrack.catalog import load_catalog  # noqa: F401  (re-exported for ghosttrack.detections)
from ghosttrack.model import State
from ghosttrack.tracker import TrackPoint, is_ground_category

SEVERITIES = ("info", "low", "medium", "high")


@dataclass(frozen=True)
class Evidence:
    field: str
    observed: Any
    threshold: Any
    ts: float


@dataclass(frozen=True)
class Finding:
    detection_id: str
    icao: str | None
    ts_first: float
    ts_last: float
    severity: str
    confidence: float
    evidence: tuple[Evidence, ...]
    explanation: str
    benign_causes: tuple[str, ...]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = [asdict(e) for e in self.evidence]
        d["benign_causes"] = list(self.benign_causes)
        return d


@dataclass
class Evaluation:
    """Result of one detection over one unit (a track, or the fleet).

    ``samples`` counts the individual checks performed; ``not_evaluated`` counts, by reason, the
    samples (or the whole unit) that could not be checked. Status is "flagged" when there is a
    finding, "clean" only when at least one check ran, and "not_evaluated" otherwise.
    """

    detection_id: str
    icao: str | None = None
    findings: list[Finding] = field(default_factory=list)
    samples: int = 0
    not_evaluated: Counter = field(default_factory=Counter)

    @property
    def status(self) -> str:
        if self.findings:
            return "flagged"
        return "clean" if self.samples else "not_evaluated"


@dataclass
class Context:
    catalog: dict = field(default_factory=load_catalog)
    # Start of the observation (first snapshot time). GT-009 treats addresses seen in the first
    # warm-up seconds as already present, not new.
    observation_start: float | None = None


# ------------------------------------------------------------------ helpers


def category_key(category: str | None, catalog: dict) -> str:
    cats = catalog["categories"]
    return category if category in cats else "unknown"


def limits(category: str | None, catalog: dict) -> dict:
    return catalog["categories"][category_key(category, catalog)]


def is_derived(state: State, catalog: dict) -> bool:
    return state.source in catalog["common"]["derived_sources"]


def source_factor(state: State, catalog: dict) -> float:
    return float(catalog["common"]["derived_source_factor"]) if is_derived(state, catalog) else 1.0


def position_error_nm(state: State, catalog: dict) -> float:
    pe = catalog["position_error_nm"]
    if state.source == "adsb":
        v = pe["adsb_by_nac_p"].get(state.nac_p) if state.nac_p is not None else None
        v = pe["adsb_unknown"] if v is None else v
    else:
        v = pe.get(state.source, pe["other"])
    return min(float(v), float(pe["max_nm"]))


def margin_confidence(observed: float, threshold: float, base: float, cap: float) -> float:
    """base at the threshold, rising linearly to cap at twice the threshold."""
    if threshold <= 0 or not math.isfinite(observed):
        return cap
    ratio = observed / threshold
    return round(min(cap, base + (cap - base) * max(0.0, ratio - 1.0)), 3)


def is_ground(p: TrackPoint, track=None) -> bool:
    """On-ground report, or a surface vehicle (C category). A point without a category inherits
    the track's most common category, since category is missing from some reports."""
    if p.ground:
        return True
    return track is not None and p.state.category is None and is_ground_category(track.category)


def airborne_skip_reason(p: TrackPoint, track=None) -> str | None:
    """Why a point cannot feed airborne kinematics, or None if it can."""
    if p.state.on_ground:
        return "on_ground"
    if is_ground(p, track):
        return "ground_vehicle"
    if p.stale:
        return "stale_position"
    return None


def group_violations(
    violations: list[tuple[float, Evidence, float, TrackPoint]], window_s: float
) -> list[list[tuple[float, Evidence, float, TrackPoint]]]:
    """Group (ts, evidence, confidence, point) tuples closer than window_s into episodes."""
    groups: list[list] = []
    for v in sorted(violations, key=lambda x: x[0]):
        if groups and v[0] - groups[-1][-1][0] <= window_s:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def make_finding(
    det: dict,
    icao: str | None,
    group: list[tuple[float, Evidence, float, Any]],
    explanation: str,
    *,
    catalog: dict,
    severity: str | None = None,
) -> Finding:
    max_evidence = int(catalog["common"]["max_evidence"])
    ev = [g[1] for g in group]
    if len(ev) > max_evidence:  # keep the first few and the strongest
        ev = ev[: max_evidence - 1] + [max(group, key=lambda g: g[2])[1]]
    return Finding(
        detection_id=det["id"],
        icao=icao,
        ts_first=group[0][0],
        ts_last=group[-1][0],
        severity=severity or det["severity"],
        confidence=max(g[2] for g in group),
        evidence=tuple(ev),
        explanation=explanation,
        benign_causes=tuple(det["benign_causes"]),
    )
