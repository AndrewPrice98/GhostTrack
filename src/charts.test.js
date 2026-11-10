import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chartSeries, nearestIndex, niceScale, runsPath, seriesRuns, seriesStats, timeTicks } from './charts.js'

test('niceScale rounds to whole steps that contain the data', () => {
  const s = niceScale(0, 1350, 5)
  assert.equal(s.min, 0)
  assert.ok(s.max >= 1350)
  assert.deepEqual(s.ticks.slice(0, 3), [0, 500, 1000])
  const alt = niceScale(31200, 34700, 5)
  assert.ok(alt.min <= 31200 && alt.max >= 34700)
  assert.ok(alt.ticks.every((t) => t % alt.step === 0))
})

test('niceScale gives a flat series a window and survives bad input', () => {
  const flat = niceScale(34000, 34000)
  assert.ok(flat.min < 34000 && flat.max > 34000)
  assert.deepEqual(niceScale(NaN, 3).ticks, [0, 1])
})

test('timeTicks lands on whole clock steps inside the range', () => {
  const t = timeTicks(1000.5, 1100, 5)
  assert.ok(t.length >= 2)
  assert.ok(t.every((x) => x >= 1000.5 && x <= 1100))
  assert.ok(t.every((x) => x % 5 === 0))
  assert.deepEqual(timeTicks(5, 5), [5])
  assert.deepEqual(timeTicks(NaN, 5), [])
})

test('chartSeries keeps points with a number, sorted, and flags injected ones', () => {
  const s = chartSeries([
    { ts: 3, alt: 300, injected: true }, { ts: 1, alt: 100 }, { ts: 2, alt: null }, { ts: 4, alt: 'x' }, { ts: NaN, alt: 1 },
  ], 'alt')
  assert.deepEqual(s, [{ ts: 1, v: 100, injected: false }, { ts: 3, v: 300, injected: true }])
  assert.deepEqual(chartSeries(undefined, 'alt'), [])
})

test('seriesRuns breaks the line at gaps over five minutes', () => {
  const s = [{ ts: 0, v: 1 }, { ts: 10, v: 2 }, { ts: 400, v: 3 }, { ts: 410, v: 4 }]
  assert.deepEqual(seriesRuns(s).map((r) => r.length), [2, 2])
  assert.deepEqual(seriesRuns(s, 1000).map((r) => r.length), [4])
  assert.deepEqual(seriesRuns([]), [])
})

test('seriesStats and nearestIndex', () => {
  const s = [{ ts: 0, v: 5 }, { ts: 10, v: 9 }, { ts: 20, v: 2 }]
  const st = seriesStats(s)
  assert.equal(st.min, 2)
  assert.equal(st.max, 9)
  assert.equal(st.n, 3)
  assert.equal(st.last.ts, 20)
  assert.equal(seriesStats([]), null)
  assert.equal(nearestIndex(s, 4), 0)
  assert.equal(nearestIndex(s, 6), 1)
  assert.equal(nearestIndex(s, 99), 2)
  assert.equal(nearestIndex([], 1), -1)
})

test('runsPath draws one subpath per run and keeps a lone point visible', () => {
  const d = runsPath([[{ x: 0, y: 0 }, { x: 5, y: 5 }], [{ x: 20, y: 20 }]])
  assert.equal(d.split('M').length - 1, 2)
  assert.ok(d.includes('L5 5'))
  assert.ok(d.includes('l0.01 0'))
})
