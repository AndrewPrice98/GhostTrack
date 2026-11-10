"""Validation: run every attack scenario against recorded traffic and measure false positives.

Scenario validation. Each scenario is injected into a recording; the detections must fire on the
scenario's own addresses. ``time_to_detect_s`` is measured by replaying the snapshots in prefixes:
the time of the first snapshot at which the expected detections have all fired, minus the time
the injected behaviour started (a binary search, valid because findings are only added as data
arrives).

False-positive measurement. Every recording listed in ``data/recordings/manifest.json`` is run
without injection. Only findings of severity >= ``validation.budget_min_severity`` (medium) count
toward the budget, expressed per hour of recorded traffic. The budget is
``max(observed rate) x budget_headroom`` with a floor, recorded in the catalog as
``validation.fp_budget_per_hour``.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ghosttrack.catalog import load_catalog
from ghosttrack.detections import Context, analyse
from ghosttrack.detections.base import SEVERITIES, Finding
from ghosttrack.model import Snapshot
from ghosttrack.scenarios import GAP_SCENARIOS, SCENARIOS, Scenario

RECORDINGS_DIR = Path(__file__).resolve().parents[1] / "data" / "recordings"
DEFAULT_RECORDING = "socal-a.jsonl.gz"


def load_recording(path: Path) -> list[Snapshot]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [Snapshot.from_dict(json.loads(line)) for line in fh if line.strip()]


def manifest_recordings(directory: Path = RECORDINGS_DIR) -> list[Path]:
    """Recordings named in the manifest (so a new recording is measured once listed there)."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return [directory / m["name"] for m in manifest]


# ------------------------------------------------------------------ scenarios


@dataclass(frozen=True)
class ValidationResult:
    scenario: str
    expected: tuple[str, ...]
    fired: tuple[str, ...]  # every detection that fired on the scenario's addresses
    passed: bool
    time_to_detect_s: float | None
    collateral: int = 0  # medium+ findings on addresses the scenario did not touch
    gap: bool = False  # documented blind spot: passes only while NOTHING fires


def _attributed(findings: Sequence[Finding], addresses: frozenset[str]) -> list[Finding]:
    out = []
    for f in findings:
        if f.icao in addresses:
            out.append(f)
        elif f.icao is None:  # fleet finding: attributed when its member addresses are the scenario's
            members = next((e.observed for e in f.evidence if e.field == "addresses"), [])
            if set(members) & addresses:
                out.append(f)
    return out


def _fired_ids(snaps: Sequence[Snapshot], addresses: frozenset[str], ctx: Context) -> set[str]:
    an = analyse(snaps, Context(catalog=ctx.catalog))
    return {f.detection_id for f in _attributed(an.findings, addresses)}


def _time_to_detect(run, expected: tuple[str, ...], ctx: Context) -> float | None:
    snaps = sorted(run.snapshots, key=lambda s: s.ts)
    first = next(i for i, s in enumerate(snaps) if s.ts >= run.inject_ts)

    def ok(i: int) -> bool:
        return set(expected) <= _fired_ids(snaps[: i + 1], run.addresses, ctx)

    lo, hi = first, len(snaps) - 1
    if not ok(hi):
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if ok(mid):
            hi = mid
        else:
            lo = mid + 1
    return round(snaps[lo].ts - run.inject_ts, 1)


def run_scenario(scenario: Scenario, snapshots: Sequence[Snapshot], ctx: Context | None = None) -> ValidationResult:
    ctx = ctx or Context()
    run = scenario.apply(snapshots)
    an = analyse(run.snapshots, Context(catalog=ctx.catalog))
    attributed = _attributed(an.findings, run.addresses)
    fired = tuple(sorted({f.detection_id for f in attributed}))
    if scenario.gap:
        # A documented blind spot passes while nothing fires. If a detection starts to catch it
        # the gap is closed and the scenario must be promoted into SCENARIOS with an expectation.
        passed = not fired
    else:
        passed = set(scenario.expected) <= set(fired)
    ttd = _time_to_detect(run, scenario.expected, ctx) if passed and not scenario.gap else None
    min_sev = SEVERITIES.index(ctx.catalog["validation"]["budget_min_severity"])
    mine = {id(f) for f in attributed}
    collateral = sum(1 for f in an.findings if id(f) not in mine and SEVERITIES.index(f.severity) >= min_sev)
    return ValidationResult(scenario.id, scenario.expected, fired, passed, ttd, collateral, scenario.gap)


def run_validation(
    snapshots: Sequence[Snapshot] | None = None, ctx: Context | None = None
) -> list[ValidationResult]:
    """Run every scenario on a clean recording (default: ``socal-a``)."""
    if snapshots is None:
        snapshots = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    ctx = ctx or Context()
    return [run_scenario(s, snapshots, ctx) for s in SCENARIOS]


def run_gap_validation(
    snapshots: Sequence[Snapshot] | None = None, ctx: Context | None = None
) -> list[ValidationResult]:
    """Run the documented-gap scenarios: each passes only while no detection fires on it."""
    if snapshots is None:
        snapshots = load_recording(RECORDINGS_DIR / DEFAULT_RECORDING)
    ctx = ctx or Context()
    return [run_scenario(s, snapshots, ctx) for s in GAP_SCENARIOS]


# ------------------------------------------------------------------ false positives


@dataclass(frozen=True)
class RecordingFP:
    recording: str
    hours: float
    tracks: int
    counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (detection, severity) -> findings
    counted: int = 0  # findings at or above the budget severity

    @property
    def per_hour(self) -> float:
        return self.counted / self.hours if self.hours else 0.0


def measure_recording(path: Path, ctx: Context | None = None) -> RecordingFP:
    ctx = ctx or Context()
    snaps = load_recording(path)
    an = analyse(snaps, Context(catalog=ctx.catalog))
    min_sev = SEVERITIES.index(ctx.catalog["validation"]["budget_min_severity"])
    counts = Counter((f.detection_id, f.severity) for f in an.findings)
    counted = sum(n for (_, sev), n in counts.items() if SEVERITIES.index(sev) >= min_sev)
    hours = (snaps[-1].ts - snaps[0].ts) / 3600.0
    return RecordingFP(path.name, hours, len(an.tracker.tracks), dict(sorted(counts.items())), counted)


def measure_false_positives(
    paths: Sequence[Path] | None = None, ctx: Context | None = None
) -> list[RecordingFP]:
    return [measure_recording(p, ctx) for p in (paths or manifest_recordings())]


def fp_budget(results: Sequence[RecordingFP], catalog: dict | None = None) -> float:
    """The budget the rule gives for these measurements: max rate x headroom, at least the floor."""
    v = (catalog or load_catalog())["validation"]
    worst = max((r.per_hour for r in results), default=0.0)
    return max(float(v["budget_floor_per_hour"]), worst * float(v["budget_headroom"]))


def budget_exceeded(results: Sequence[RecordingFP], catalog: dict | None = None) -> list[RecordingFP]:
    budget = float((catalog or load_catalog())["validation"]["fp_budget_per_hour"])
    return [r for r in results if r.per_hour > budget]


def budget_looser_than_rule(results: Sequence[RecordingFP], catalog: dict | None = None) -> tuple[float, float] | None:
    """(catalog budget, budget the rule gives) when the catalog is looser than the rule, else None."""
    cat = catalog or load_catalog()
    stored, rule = float(cat["validation"]["fp_budget_per_hour"]), fp_budget(results, cat)
    return (stored, rule) if stored > rule + 1e-9 else None


# ------------------------------------------------------------------ report


def format_report(
    results: Sequence[ValidationResult],
    fps: Sequence[RecordingFP],
    catalog: dict | None = None,
    gaps: Sequence[ValidationResult] = (),
) -> str:
    cat = catalog or load_catalog()
    lines = ["Scenario validation (injected test data)", ""]
    head = f"{'scenario':<24}{'expected':<10}{'fired':<30}{'result':<8}{'time to detect':>15}"
    lines += [head, "-" * len(head)]
    for r in results:
        ttd = "-" if r.time_to_detect_s is None else f"{r.time_to_detect_s:.0f} s"
        lines.append(
            f"{r.scenario:<24}{','.join(r.expected):<10}{','.join(r.fired) or '-':<30}"
            f"{'PASS' if r.passed else 'FAIL':<8}{ttd:>15}"
        )
    if gaps:
        lines += ["", "Documented gaps (expected NOT to fire: known blind spots)", ""]
        head = f"{'scenario':<24}{'fired':<30}{'result':<8}"
        lines += [head, "-" * len(head)]
        for r in gaps:
            lines.append(f"{r.scenario:<24}{','.join(r.fired) or 'none':<30}{'GAP HOLDS' if r.passed else 'GAP CLOSED':<8}")
    budget = float(cat["validation"]["fp_budget_per_hour"])
    sev = cat["validation"]["budget_min_severity"]
    lines += ["", f"Clean recordings: findings (budget counts severity >= {sev}: {budget:g} per hour)", ""]
    head = f"{'recording':<20}{'hours':>7}{'tracks':>8}{'counted':>9}{'per hour':>10}  findings by detection/severity"
    lines += [head, "-" * len(head)]
    for r in fps:
        detail = ", ".join(f"{d}/{s}={n}" for (d, s), n in r.counts.items()) or "none"
        lines.append(f"{r.recording:<20}{r.hours:>7.2f}{r.tracks:>8}{r.counted:>9}{r.per_hour:>10.2f}  {detail}")
    loose = budget_looser_than_rule(fps, cat)
    if loose:
        lines += ["", f"NOTE: catalog budget {loose[0]:g}/h is looser than the rule gives ({loose[1]:g}/h)."]
    return "\n".join(lines)
