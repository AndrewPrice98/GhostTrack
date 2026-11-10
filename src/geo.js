// Map geometry for the SVG radar map: projection, glyph rotation, the pan/zoom view and track
// trails. Pure functions with no React, so Node's test runner can import them.
//
// The projection matches backend/scripts/build_basemap.py, which wrote the base map: an
// equirectangular projection centred on the region, in nautical miles.
//   x = (lon - lon0) * 60 * cos(lat0)      y = -(lat - lat0) * 60

export const ZOOM_MIN = 1
export const ZOOM_MAX = 12
export const TRACK_GAP_S = 300 // a gap longer than this starts a new segment (as in the tracker)

/** project(lat, lon) -> [x, y] in nautical miles from the region centre (y grows southward). */
export function makeProjection({ lat0, lon0 }) {
  const k = Math.cos((lat0 * Math.PI) / 180)
  return (lat, lon) => [(lon - lon0) * 60 * k, -(lat - lat0) * 60]
}

/** A heading in degrees, normalised to [0, 360); null when there is none. */
export function normalizeTrack(deg) {
  if (deg === null || deg === undefined || deg === '' || !Number.isFinite(Number(deg))) return null
  const d = Number(deg) % 360
  return d < 0 ? d + 360 : d
}

/**
 * SVG transform for an aircraft glyph drawn pointing north (up) in a unit box: moved to (x, y),
 * rotated clockwise by the track and scaled to `size` map units. SVG's rotate() turns clockwise
 * on screen because y grows downward, which is the compass direction.
 */
export function glyphTransform(x, y, track, size) {
  const t = normalizeTrack(track)
  const r = (n) => Math.round(n * 1000) / 1000
  const rot = t === null || t === 0 ? '' : ` rotate(${r(t)})`
  return `translate(${r(x)} ${r(y)})${rot} scale(${r(size)})`
}

/** The full-extent view: zoom 1 centred on the base map. */
export function homeView(base) {
  return { z: 1, cx: base.x + base.w / 2, cy: base.y + base.h / 2 }
}

/** Keep the zoom within limits and the visible window inside the base map. */
export function clampView(view, base) {
  const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number.isFinite(view.z) ? view.z : 1))
  const w = base.w / z
  const h = base.h / z
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
  return {
    z,
    cx: clamp(Number.isFinite(view.cx) ? view.cx : base.x + base.w / 2, base.x + w / 2, base.x + base.w - w / 2),
    cy: clamp(Number.isFinite(view.cy) ? view.cy : base.y + base.h / 2, base.y + h / 2, base.y + base.h - h / 2),
  }
}

/** The visible window as an SVG viewBox string. */
export function viewBoxOf(view, base) {
  const w = base.w / view.z
  const h = base.h / view.z
  const r = (n) => Math.round(n * 1000) / 1000
  return `${r(view.cx - w / 2)} ${r(view.cy - h / 2)} ${r(w)} ${r(h)}`
}

/**
 * Zoom by `factor`, keeping the map point (ux, uy) where it is on screen (the cursor for wheel
 * zoom); without a point, zoom about the centre.
 */
export function zoomAt(view, factor, base, ux, uy) {
  const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.z * factor))
  if (ux === undefined || uy === undefined) return clampView({ ...view, z }, base)
  const k = view.z / z
  return clampView({ z, cx: ux - (ux - view.cx) * k, cy: uy - (uy - view.cy) * k }, base)
}

/** Pan by a fraction of the visible window (dx, dy in -1..1; positive is east / south). */
export function panBy(view, dx, dy, base) {
  return clampView({ ...view, cx: view.cx + (dx * base.w) / view.z, cy: view.cy + (dy * base.h) / view.z }, base)
}

/** Pan by a distance in map units (from a pointer drag). */
export function panUnits(view, dux, duy, base) {
  return clampView({ ...view, cx: view.cx + dux, cy: view.cy + duy }, base)
}

/**
 * Track points -> polylines of projected [x, y], split where consecutive reports are more than
 * `gapS` seconds apart (a new segment, never drawn as a jump). Points without a position are
 * skipped; segments of one point are dropped.
 */
export function trailSegments(points, project, gapS = TRACK_GAP_S) {
  const segments = []
  let current = []
  let lastTs = null
  for (const p of points || []) {
    if (!Number.isFinite(p?.lat) || !Number.isFinite(p?.lon)) continue
    if (lastTs !== null && Number.isFinite(p.ts) && p.ts - lastTs > gapS) {
      if (current.length > 1) segments.push(current)
      current = []
    }
    current.push(project(p.lat, p.lon))
    if (Number.isFinite(p.ts)) lastTs = p.ts
  }
  if (current.length > 1) segments.push(current)
  return segments
}

/** SVG path data for a polyline of [x, y]. */
export function polylineD(points) {
  const r = (n) => Math.round(n * 100) / 100
  return points.map(([x, y], i) => `${i ? 'L' : 'M'}${r(x)} ${r(y)}`).join(' ')
}

/**
 * A viewBox {x, y, w, h} around projected points with padding, at least `minSpan` units across
 * and stretched to the aspect ratio `aspect` (width / height). Null when there are no points.
 */
export function fitBox(points, { pad = 0.15, minSpan = 6, aspect = 1.6 } = {}) {
  if (!points.length) return null
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity
  for (const [x, y] of points) {
    x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y)
  }
  let w = Math.max(x1 - x0, minSpan)
  let h = Math.max(y1 - y0, minSpan / aspect)
  w *= 1 + 2 * pad
  h *= 1 + 2 * pad
  if (w / h > aspect) h = w / aspect
  else w = h * aspect
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  return { x: cx - w / 2, y: cy - h / 2, w, h }
}
