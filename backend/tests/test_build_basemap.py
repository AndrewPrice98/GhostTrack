"""The base-map build script's geometry helpers and the committed output."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_basemap", ROOT / "backend" / "scripts" / "build_basemap.py")
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

BOX = (0.0, 0.0, 10.0, 10.0)


def test_projection_centre_and_scale():
    assert bb.project(bb.LAT0, bb.LON0) == (0.0, 0.0)
    x, y = bb.project(bb.LAT0 + 1, bb.LON0)
    assert x == 0.0 and y == pytest.approx(-60.0)


def test_clip_segment():
    assert bb.clip_segment((-5, 5), (5, 5), BOX) == ((0.0, 5.0), (5.0, 5.0))
    assert bb.clip_segment((-5, -5), (-1, -1), BOX) is None
    assert bb.clip_segment((2, 2), (3, 3), BOX) == ((2.0, 2.0), (3.0, 3.0))


def test_clip_polyline_splits_where_the_line_leaves_the_box():
    runs = bb.clip_polyline([(1, 5), (15, 5), (15, 6), (1, 6)], BOX)
    assert runs == [[(1, 5), (10.0, 5.0)], [(10.0, 6.0), (1, 6)]]


def test_clip_polygon_keeps_the_inside():
    out = bb.clip_polygon([(-5, -5), (5, -5), (5, 5), (-5, 5)], BOX)
    xs = [p[0] for p in out]
    ys = [p[1] for p in out]
    assert min(xs) == 0 and max(xs) == 5 and min(ys) == 0 and max(ys) == 5


def test_simplify_drops_points_within_tolerance():
    line = [(0, 0), (1, 0.01), (2, 0), (3, 2), (4, 0)]
    assert bb.simplify(line, 0.1) == [(0, 0), (2, 0), (3, 2), (4, 0)]
    assert bb.simplify([(0, 0), (1, 1)], 0.1) == [(0, 0), (1, 1)]


def test_committed_basemap_matches_the_projection():
    data = json.loads((ROOT / "src" / "assets" / "basemap-socal.json").read_text(encoding="utf-8"))
    assert data["projection"]["lat0"] == bb.LAT0 and data["projection"]["lon0"] == bb.LON0
    assert data["land"] and data["coast"]
    for ap in data["airports"]:
        x, y = bb.project(ap["lat"], ap["lon"])
        assert (round(x, 2), round(y, 2)) == (ap["x"], ap["y"])
    assert len(json.dumps(data)) < 150_000
