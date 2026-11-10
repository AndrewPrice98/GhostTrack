import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ZOOM_MAX, clampView, fitBox, glyphTransform, homeView, labelPlacement, makeProjection, normalizeTrack, panBy, polylineD,
  trailSegments, viewBoxOf, viewForBox, zoomAt,
} from './geo.js'

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`)
const base = { x: -100, y: -80, w: 200, h: 160 }

test('projection: region centre is the origin, one degree of latitude is 60 NM', () => {
  const p = makeProjection({ lat0: 33.94, lon0: -118.41 })
  const [x0, y0] = p(33.94, -118.41)
  near(x0, 0); near(y0, 0)
  const [, yNorth] = p(34.94, -118.41)
  near(yNorth, -60) // north is up (negative y)
  const [xEast] = p(33.94, -117.41)
  near(xEast, 60 * Math.cos((33.94 * Math.PI) / 180)) // longitude scaled by cos(lat0)
})

test('projection matches the base map build (LAX airport reference point)', async () => {
  const { readFile } = await import('node:fs/promises')
  const map = JSON.parse(await readFile(new URL('./assets/basemap-socal.json', import.meta.url), 'utf8'))
  const p = makeProjection(map.projection)
  for (const ap of map.airports) {
    const [x, y] = p(ap.lat, ap.lon)
    near(x, ap.x, 0.006); near(y, ap.y, 0.006)
  }
  assert.ok(map.land.length > 0 && map.coast.length > 0)
})

test('normalizeTrack wraps headings into [0, 360) and keeps missing as null', () => {
  assert.equal(normalizeTrack(0), 0)
  assert.equal(normalizeTrack(370), 10)
  assert.equal(normalizeTrack(-90), 270)
  assert.equal(normalizeTrack(720), 0)
  assert.equal(normalizeTrack(null), null)
  assert.equal(normalizeTrack(undefined), null)
  assert.equal(normalizeTrack('x'), null)
  assert.equal(normalizeTrack(Number.NaN), null)
})

test('glyphTransform rotates clockwise by the track and omits rotation without a heading', () => {
  assert.equal(glyphTransform(1, 2, 90, 3), 'translate(1 2) rotate(90) scale(3)')
  assert.equal(glyphTransform(1, 2, -45, 3), 'translate(1 2) rotate(315) scale(3)')
  assert.equal(glyphTransform(1, 2, 0, 3), 'translate(1 2) scale(3)')
  assert.equal(glyphTransform(1, 2, null, 3), 'translate(1 2) scale(3)')
  assert.equal(glyphTransform(1.23456, 2, 140.6312, 0.5), 'translate(1.235 2) rotate(140.631) scale(0.5)')
})

test('home view shows the whole map; viewBox reflects zoom', () => {
  const v = homeView(base)
  assert.deepEqual(v, { z: 1, cx: 0, cy: 0 })
  assert.equal(viewBoxOf(v, base), '-100 -80 200 160')
  assert.equal(viewBoxOf({ z: 2, cx: 0, cy: 0 }, base), '-50 -40 100 80')
})

test('clampView keeps zoom in range and the window inside the map', () => {
  assert.equal(clampView({ z: 0.2, cx: 0, cy: 0 }, base).z, 1)
  assert.equal(clampView({ z: 99, cx: 0, cy: 0 }, base).z, ZOOM_MAX)
  // at zoom 1 the centre cannot move
  assert.deepEqual(clampView({ z: 1, cx: 50, cy: -30 }, base), { z: 1, cx: 0, cy: 0 })
  // at zoom 2 the window is 100 x 80: centre limited to [-50, 50] x [-40, 40]
  assert.deepEqual(clampView({ z: 2, cx: 500, cy: -500 }, base), { z: 2, cx: 50, cy: -40 })
  assert.deepEqual(clampView({ z: Number.NaN, cx: Number.NaN, cy: 3 }, base), { z: 1, cx: 0, cy: 0 })
})

test('zoomAt keeps the point under the cursor fixed', () => {
  const v = zoomAt({ z: 1, cx: 0, cy: 0 }, 2, base, 20, 10)
  assert.equal(v.z, 2)
  // the point (20, 10) was 20 / 200 = 10 % right of centre; it still is after zooming
  const w = base.w / v.z
  near((20 - v.cx) / w, 20 / 200)
  // zooming out past 1 is clamped and recentred
  assert.deepEqual(zoomAt(v, 0.1, base, 20, 10), { z: 1, cx: 0, cy: 0 })
  // without a point, zoom about the centre
  assert.deepEqual(zoomAt({ z: 1, cx: 0, cy: 0 }, 4, base), { z: 4, cx: 0, cy: 0 })
})

test('panBy moves by a fraction of the visible window and stops at the edge', () => {
  const v = panBy({ z: 4, cx: 0, cy: 0 }, 0.25, 0, base)
  near(v.cx, 12.5) // a quarter of the 50-unit-wide window
  const far = [1, 2, 3, 4, 5, 6, 7, 8].reduce((acc) => panBy(acc, 1, 1, base), v)
  assert.deepEqual(far, { z: 4, cx: 75, cy: 60 })
})

test('trailSegments splits on gaps longer than 5 minutes and skips missing positions', () => {
  const id = (lat, lon) => [lon, lat]
  const pts = [
    { ts: 0, lat: 1, lon: 1 }, { ts: 10, lat: 2, lon: 2 }, { ts: 20, lat: null, lon: 3 },
    { ts: 30, lat: 3, lon: 3 }, { ts: 400, lat: 9, lon: 9 }, { ts: 410, lat: 10, lon: 10 },
    { ts: 1000, lat: 5, lon: 5 },
  ]
  const segs = trailSegments(pts, id)
  assert.deepEqual(segs, [[[1, 1], [2, 2], [3, 3]], [[9, 9], [10, 10]]]) // lone last point dropped
  assert.deepEqual(trailSegments([], id), [])
  assert.deepEqual(trailSegments(undefined, id), [])
})

test('polylineD and fitBox', () => {
  assert.equal(polylineD([[0, 0], [1.234, -2.5]]), 'M0 0 L1.23 -2.5')
  assert.equal(fitBox([]), null)
  const box = fitBox([[0, 0], [10, 0]], { pad: 0, minSpan: 1, aspect: 2 })
  assert.deepEqual(box, { x: 0, y: -2.5, w: 10, h: 5 })
  const tiny = fitBox([[5, 5]], { pad: 0, minSpan: 6, aspect: 1.5 })
  near(tiny.w, 6); near(tiny.h, 4); near(tiny.x, 2)
})

test('labelPlacement keeps the label off the line, on the side away from the neighbouring point', () => {
  const box = { x: 0, y: 0, w: 100, h: 60 }
  const opts = { box, textW: 20, gap: 4, lift: 6, size: 5 }
  // the track arrives from the left and from below: the label goes right of and above the end point
  const a = labelPlacement({ x: 50, y: 30 }, { x: 40, y: 38 }, opts)
  assert.equal(a.textAnchor, 'start')
  assert.ok(a.x > 50 && a.y < 30)
  // the track arrives from the right and from above: the label goes left of and below the point
  const b = labelPlacement({ x: 50, y: 30 }, { x: 60, y: 22 }, opts)
  assert.equal(b.textAnchor, 'end')
  assert.ok(b.x < 50 && b.y > 30)
  // label and neighbour lie on opposite sides of the anchor, both across and along the line
  assert.ok((a.x - 50) * (40 - 50) < 0 && (a.y - 30) * (38 - 30) < 0)
  assert.ok((b.x - 50) * (60 - 50) < 0 && (b.y - 30) * (22 - 30) < 0)
})

test('labelPlacement flips back inside the box and copes with a single point', () => {
  const box = { x: 0, y: 0, w: 100, h: 60 }
  const opts = { box, textW: 30, gap: 4, lift: 6, size: 5 }
  assert.equal(labelPlacement({ x: 90, y: 30 }, { x: 80, y: 30 }, opts).textAnchor, 'end') // wanted right, off the edge
  assert.equal(labelPlacement({ x: 8, y: 30 }, { x: 20, y: 30 }, opts).textAnchor, 'start') // wanted left, off the edge
  assert.equal(labelPlacement({ x: 50, y: 30 }, null, opts).textAnchor, 'start')
})

test('viewForBox zooms to a box and stays inside the base map', () => {
  const v = viewForBox({ x: -10, y: -8, w: 20, h: 16 }, base)
  near(v.z, 10)
  near(v.cx, 0); near(v.cy, 0)
  assert.equal(viewForBox({ x: -400, y: -400, w: 800, h: 800 }, base).z, 1)
  const corner = viewForBox({ x: 90, y: 70, w: 10, h: 8 }, base)
  assert.ok(corner.cx + base.w / corner.z / 2 <= base.x + base.w + 1e-9)
})
