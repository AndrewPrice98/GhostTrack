// Pure helpers for the hand-rolled SVG charts (aircraft altitude and speed profiles, the
// false-positive bars). No React, so Node's test runner can import them.

/** A gap longer than this between reports starts a new line segment (as in the tracker). */
export const CHART_GAP_S = 300

/**
 * A "nice" axis for values between min and max: a round step (1, 2 or 5 times a power of ten),
 * the axis extended to whole steps, and the tick values. A flat series gets a window around it.
 */
export function niceScale(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1, step: 1, ticks: [0, 1] }
  let lo = min
  let hi = max
  if (hi - lo < 1e-9) {
    const pad = Math.max(1, Math.abs(lo) * 0.1)
    lo -= pad
    hi += pad
  }
  const raw = (hi - lo) / Math.max(1, count - 1)
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag
  const start = Math.floor(lo / step + 1e-9) * step
  const end = Math.ceil(hi / step - 1e-9) * step
  const ticks = []
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v / step) * step)
  return { min: start, max: end, step, ticks }
}

const TIME_STEPS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]

/** Tick times (epoch seconds, on whole steps of the clock) between t0 and t1, about `count` of them. */
export function timeTicks(t0, t1, count = 5) {
  if (!Number.isFinite(t0)) return []
  if (!Number.isFinite(t1) || t1 <= t0) return [t0]
  const want = (t1 - t0) / Math.max(1, count - 1)
  const step = TIME_STEPS.find((s) => s >= want) || TIME_STEPS.at(-1)
  const out = []
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t)
  return out
}

/** Track points -> [{ts, v, injected}] for one numeric field, skipping points without it. */
export function chartSeries(track, key) {
  return (track || [])
    .filter((p) => Number.isFinite(p?.ts) && Number.isFinite(p?.[key]))
    .map((p) => ({ ts: p.ts, v: p[key], injected: Boolean(p.injected) }))
    .sort((a, b) => a.ts - b.ts)
}

/** Split a series into runs where consecutive points are at most `gapS` apart. */
export function seriesRuns(series, gapS = CHART_GAP_S) {
  const runs = []
  let run = []
  for (const p of series || []) {
    if (run.length && p.ts - run.at(-1).ts > gapS) {
      runs.push(run)
      run = []
    }
    run.push(p)
  }
  if (run.length) runs.push(run)
  return runs
}

export function seriesStats(series) {
  if (!series?.length) return null
  let min = Infinity
  let max = -Infinity
  for (const p of series) {
    min = Math.min(min, p.v)
    max = Math.max(max, p.v)
  }
  return { n: series.length, min, max, first: series[0], last: series.at(-1) }
}

/** The index of the point nearest to time `ts` (series sorted by time); -1 for an empty series. */
export function nearestIndex(series, ts) {
  if (!series?.length) return -1
  let lo = 0
  let hi = series.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (series[mid].ts < ts) lo = mid
    else hi = mid
  }
  return Math.abs(series[lo].ts - ts) <= Math.abs(series[hi].ts - ts) ? lo : hi
}

/** SVG path data for runs of {x, y} pixel points: one subpath per run, a lone point as a dot-length stroke. */
export function runsPath(runs) {
  const r = (n) => Math.round(n * 10) / 10
  return runs.map((run) => run.map((p, i) => `${i ? 'L' : 'M'}${r(p.x)} ${r(p.y)}`).join('')
    + (run.length === 1 ? `l0.01 0` : '')).join(' ')
}
