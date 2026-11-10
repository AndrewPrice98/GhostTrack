"""Plausibility detections: pure functions over tracks (or the whole fleet) with a context.

Each detection returns an ``Evaluation`` (findings plus coverage: samples checked and
not-evaluated reasons). Thresholds and documentation live in ``catalog.yml``.
"""
from ghosttrack.detections.base import Context, Evaluation, Evidence, Finding, load_catalog
from ghosttrack.detections.engine import ALL_IDS, Analysis, analyse, analyse_tracker

__all__ = [
    "ALL_IDS",
    "Analysis",
    "Context",
    "Evaluation",
    "Evidence",
    "Finding",
    "analyse",
    "analyse_tracker",
    "load_catalog",
]
