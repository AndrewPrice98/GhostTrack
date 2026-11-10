"""Build the dashboard's base map (run once; the output is committed).

    python scripts/build_basemap.py --coastline ne_10m_coastline.shp --land ne_10m_land.shp         --borders ne_10m_admin_0_boundary_lines_land.shp

Reads Natural Earth 1:10m coastline, land and country-boundary shapefiles (public domain, see
DECISIONS.md, "Base map"), clips them to the Southern California demo region, projects them and
simplifies them (Douglas-Peucker), and writes ``src/assets/basemap-socal.json`` for the SVG map.
The shapefiles themselves are not committed; download them to a scratch directory.

Projection: equirectangular centred on the region centre, in nautical miles:
    x = (lon - lon0) * 60 * cos(lat0)      y = -(lat - lat0) * 60
so the dashboard can draw 20/40/60 NM range rings as plain circles. ``src/geo.js`` uses the
same formula with the parameters stored in the output file.

Standard library only (a minimal .shp reader for polyline and polygon shapes).
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "src" / "assets" / "basemap-socal.json"

# Region centre (ghosttrack.upstream.REGIONS["socal"]) and the drawn extent, which is wider than
# the 60 NM query radius so San Diego (SAN) is on the map for orientation.
LAT0, LON0 = 33.94, -118.41
BOUNDS = {"lat_min": 32.55, "lat_max": 35.35, "lon_min": -120.35, "lon_max": -116.45}
TOLERANCE_NM = 0.06  # Douglas-Peucker tolerance (about 110 m)
DECIMALS = 2

# Public airport reference points (FAA airport data), for orientation only.
AIRPORTS = [
    ("LAX", "Los Angeles Intl", 33.9425, -118.4081),
    ("SAN", "San Diego Intl", 32.7336, -117.1897),
    ("BUR", "Hollywood Burbank", 34.2006, -118.3587),
    ("LGB", "Long Beach", 33.8177, -118.1516),
    ("SNA", "John Wayne (Orange County)", 33.6757, -117.8682),
    ("ONT", "Ontario Intl", 34.0560, -117.6012),
]

K = math.cos(math.radians(LAT0))


def project(lat: float, lon: float) -> tuple[float, float]:
    return ((lon - LON0) * 60.0 * K, -(lat - LAT0) * 60.0)


# -- shapefile reading ----------------------------------------------------------------------


def read_shp(path: Path) -> list[tuple[int, list[list[tuple[float, float]]]]]:
    """(shape type, parts as lists of (lon, lat)) for each record; null shapes are skipped."""
    data = path.read_bytes()
    out = []
    pos = 100
    while pos + 8 <= len(data):
        _num, words = struct.unpack(">ii", data[pos:pos + 8])
        content = data[pos + 8:pos + 8 + words * 2]
        pos += 8 + words * 2
        (shape_type,) = struct.unpack("<i", content[:4])
        if shape_type == 0:
            out.append((0, []))
            continue
        if shape_type not in (3, 5):
            raise SystemExit(f"{path.name}: unsupported shape type {shape_type}")
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", content[44:44 + 4 * n_parts]))
        base = 44 + 4 * n_parts
        pts = [struct.unpack("<dd", content[base + 16 * i:base + 16 * i + 16]) for i in range(n_points)]
        bounds = parts + [n_points]
        out.append((shape_type, [pts[bounds[i]:bounds[i + 1]] for i in range(n_parts)]))
    return out


# -- clipping and simplification -----------------------------------------------------------------


def clip_segment(p, q, box):
    """Liang-Barsky: the part of segment p-q inside box (x0, y0, x1, y1), or None."""
    x0, y0, x1, y1 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for pv, qv in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if pv == 0:
            if qv < 0:
                return None
            continue
        t = qv / pv
        if pv < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (p[0] + t0 * dx, p[1] + t0 * dy), (p[0] + t1 * dx, p[1] + t1 * dy)


def clip_polyline(points, box):
    runs, run = [], []
    for p, q in zip(points, points[1:]):
        seg = clip_segment(p, q, box)
        if seg is None:
            if len(run) > 1:
                runs.append(run)
            run = []
            continue
        a, b = seg
        if not run or run[-1] != a:
            if len(run) > 1:
                runs.append(run)
            run = [a]
        run.append(b)
        if b != q:  # left the box
            runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    return runs


def clip_polygon(ring, box):
    """Sutherland-Hodgman against an axis-aligned box."""
    x0, y0, x1, y1 = box
    edges = (
        (lambda p: p[0] >= x0, lambda p, q: _at_x(p, q, x0)),
        (lambda p: p[0] <= x1, lambda p, q: _at_x(p, q, x1)),
        (lambda p: p[1] >= y0, lambda p, q: _at_y(p, q, y0)),
        (lambda p: p[1] <= y1, lambda p, q: _at_y(p, q, y1)),
    )
    out = list(ring)
    for inside, cross in edges:
        src, out = out, []
        if not src:
            break
        prev = src[-1]
        for cur in src:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
    return out


def _at_x(p, q, x):
    t = (x - p[0]) / (q[0] - p[0])
    return (x, p[1] + t * (q[1] - p[1]))


def _at_y(p, q, y):
    t = (y - p[1]) / (q[1] - p[1])
    return (p[0] + t * (q[0] - p[0]), y)


def simplify(points, tol):
    """Douglas-Peucker (iterative), keeping the end points."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = points[i]
            d = math.hypot(px - ax, py - ay) if norm == 0 else abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def path_d(points, closed=False):
    fmt = f"{{:.{DECIMALS}f}}"
    coords = []
    last = None
    for x, y in points:
        cell = (fmt.format(x), fmt.format(y))
        if cell != last:
            coords.append(cell)
            last = cell
    if len(coords) < 2:
        return None
    d = "M" + " L".join(f"{x} {y}" for x, y in coords)
    return d + "Z" if closed else d


def _bbox_hits(parts) -> bool:
    for part in parts:
        for lon, lat in part:
            if BOUNDS["lon_min"] - 1 <= lon <= BOUNDS["lon_max"] + 1 and BOUNDS["lat_min"] - 1 <= lat <= BOUNDS["lat_max"] + 1:
                return True
    return False


def build(coastline: Path, land_shp: Path, borders_shp: Path) -> dict:
    x0, y0 = project(BOUNDS["lat_max"], BOUNDS["lon_min"])
    x1, y1 = project(BOUNDS["lat_min"], BOUNDS["lon_max"])
    box = (x0, y0, x1, y1)

    coast = []
    for _t, parts in read_shp(coastline):
        if not _bbox_hits(parts):
            continue
        for part in parts:
            pts = [project(lat, lon) for lon, lat in part]
            for run in clip_polyline(pts, box):
                d = path_d(simplify(run, TOLERANCE_NM))
                if d:
                    coast.append(d)

    # Land fill: every ring of the land polygons is an outer ring (mainland or island).
    land = []
    for _t, parts in read_shp(land_shp):
        if not _bbox_hits(parts):
            continue
        for part in parts:
            clipped = clip_polygon([project(lat, lon) for lon, lat in part], box)
            if len(clipped) >= 3:
                d = path_d(simplify(clipped + [clipped[0]], TOLERANCE_NM), closed=True)
                if d:
                    land.append(d)

    # Country borders (in this box, the US-Mexico border) as lines.
    borders = []
    for _t, parts in read_shp(borders_shp):
        if not _bbox_hits(parts):
            continue
        for part in parts:
            for run in clip_polyline([project(lat, lon) for lon, lat in part], box):
                d = path_d(simplify(run, TOLERANCE_NM))
                if d:
                    borders.append(d)

    airports = []
    for code, name, lat, lon in AIRPORTS:
        x, y = project(lat, lon)
        airports.append({"code": code, "name": name, "lat": lat, "lon": lon, "x": round(x, 2), "y": round(y, 2)})

    return {
        "region": "socal",
        "source": "Natural Earth 1:10m coastline, land and country boundary lines (public domain). "
                  "Made with Natural Earth.",
        "projection": {"type": "equirectangular", "lat0": LAT0, "lon0": LON0, "units": "NM",
                       "x": "(lon - lon0) * 60 * cos(lat0)", "y": "-(lat - lat0) * 60"},
        "bounds": BOUNDS,
        "view": {"x": round(x0, 2), "y": round(y0, 2), "w": round(x1 - x0, 2), "h": round(y1 - y0, 2)},
        "range_rings_nm": [20, 40, 60],
        "land": land,
        "borders": borders,
        "coast": coast,
        "airports": airports,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--coastline", type=Path, required=True, help="ne_10m_coastline.shp")
    ap.add_argument("--land", type=Path, required=True, help="ne_10m_land.shp")
    ap.add_argument("--borders", type=Path, required=True, help="ne_10m_admin_0_boundary_lines_land.shp")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    result = build(args.coastline, args.land, args.borders)
    text = json.dumps(result, separators=(",", ":")) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({len(text) // 1024} KB; {len(result['land'])} land, "
          f"{len(result['borders'])} border, {len(result['coast'])} coast paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
