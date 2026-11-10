"""Loader for the detection catalog (``detections/catalog.yml``), the single source of thresholds.

Kept in its own module, outside the ``detections`` package, so the tracker can read its timing
rules from the catalog without a circular import.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CATALOG_PATH = Path(__file__).resolve().parent / "detections" / "catalog.yml"


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    """The parsed catalog (cached; treat as read-only, deep-copy before editing)."""
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["by_id"] = {d["id"]: d for d in data["detections"]}
    return data
