"""Great-circle helpers on a spherical Earth. Distances in nautical miles, angles in degrees.

Haversine is used everywhere (no flat-earth shortcuts), so distances stay correct across the
antimeridian and near the poles. The spherical model differs from WGS84 by well under 0.5 %,
which is far below every detection tolerance.
"""
from __future__ import annotations

import math

# IUGG mean Earth radius 6,371,008.8 m; 1 NM = 1,852 m exactly.
EARTH_RADIUS_NM = 6371008.8 / 1852.0
KT_TO_MS = 1852.0 / 3600.0
G_MS2 = 9.80665


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    a = min(1.0, max(0.0, a))
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, 0-360 degrees true."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def angle_diff_deg(a: float, b: float) -> float:
    """Signed smallest difference b - a in [-180, 180)."""
    return (b - a + 180.0) % 360.0 - 180.0


def destination(lat: float, lon: float, bearing: float, dist_nm: float) -> tuple[float, float]:
    """Point reached from (lat, lon) after dist_nm along an initial bearing. Longitude in [-180, 180)."""
    d = dist_nm / EARTH_RADIUS_NM
    p1, l1, th = math.radians(lat), math.radians(lon), math.radians(bearing)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    lon2 = (math.degrees(l2) + 180.0) % 360.0 - 180.0
    return math.degrees(p2), lon2
