import { useEffect, useId, useRef, useState } from 'react'
import { chartSeries, nearestIndex, niceScale, runsPath, seriesRuns, seriesStats, timeTicks } from '../charts.js'
import { fmtNumber, fmtTime } from '../lib.js'

const M = { left: 58, right: 14, top: 12, bottom: 30 }

/**
 * A hand-rolled SVG line chart of one numeric field against time (UTC). Gaps longer than five
 * minutes break the line; injected points are drawn hollow with a dashed ring; `bands` shade the
 * time window of a finding. The chart has a text summary, a value read-out that follows the pointer or
 * the left and right arrow keys, and a data table for screen readers and copying.
 */
export default function LineChart({ title, unit, field, track, bands = [], zero = false, height = 220 }) {
  const id = useId().replace(/:/g, '')
  const wrapRef = useRef(null)
  const [width, setWidth] = useState(640)
  const [cursor, setCursor] = useState(null)
  const series = chartSeries(track, field)

  const hasData = series.length > 0
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return undefined
    const ro = new ResizeObserver(([entry]) => { if (entry.contentRect.width > 0) setWidth(Math.round(entry.contentRect.width)) })
    ro.observe(el)
    return () => ro.disconnect()
  }, [hasData])

  const stats = seriesStats(series)
  if (!stats) {
    return (
      <figure className="chart">
        <figcaption><strong>{title}</strong></figcaption>
        <p className="state small">No {title.toLowerCase()} reports in the stored history for this aircraft.</p>
      </figure>
    )
  }

  const t0 = stats.first.ts
  const t1 = stats.last.ts
  const tMin = t1 > t0 ? t0 : t0 - 30
  const tMax = t1 > t0 ? t1 : t0 + 30
  const y = niceScale(zero ? Math.min(0, stats.min) : stats.min, stats.max, 5)
  const compact = width < 480
  const h = compact ? Math.min(height, 200) : height
  const pw = Math.max(40, width - M.left - M.right)
  const ph = h - M.top - M.bottom
  const sx = (t) => M.left + ((t - tMin) / (tMax - tMin)) * pw
  const sy = (v) => M.top + (1 - (v - y.min) / (y.max - y.min)) * ph
  const runs = seriesRuns(series).map((run) => run.map((p) => ({ x: sx(p.ts), y: sy(p.v) })))
  const xt = timeTicks(tMin, tMax, compact ? 3 : 5)
  const showDots = series.length <= 150
  const pick = cursor !== null && series[cursor] ? series[cursor] : null
  const fmt = (v) => `${fmtNumber(v)} ${unit}`

  function onMove(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const t = tMin + ((e.clientX - rect.left - M.left) / pw) * (tMax - tMin)
    setCursor(nearestIndex(series, t))
  }

  function onKeyDown(e) {
    const last = series.length - 1
    const at = cursor ?? (e.key === 'ArrowLeft' ? last + 1 : -1)
    const next = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: last }[e.key]
    if (next === undefined) return
    e.preventDefault()
    setCursor(Math.max(0, Math.min(last, next)))
  }

  const summary = `${title}: ${series.length} reports from ${fmtTime(t0)} to ${fmtTime(t1)}, between ${fmt(stats.min)} and ${fmt(stats.max)}, `
    + `last ${fmt(stats.last.v)}.`
  const injectedCount = series.filter((p) => p.injected).length

  return (
    <figure className="chart">
      <figcaption>
        <strong>{title}</strong>
        <span className="muted small"> · {unit} against time (UTC)</span>
      </figcaption>
      <div className="chart-wrap" ref={wrapRef} tabIndex={0} role="group" onKeyDown={onKeyDown}
        aria-label={`${title} chart. ${summary} Use the left and right arrow keys to read single reports.`}
        onBlur={() => setCursor(null)}>
        <svg width={width} height={h} viewBox={`0 0 ${width} ${h}`} role="img" aria-label={summary} focusable="false"
          className="chart-svg">
          <defs>
            <clipPath id={`${id}-clip`}><rect x={M.left} y={M.top - 2} width={pw} height={ph + 4} /></clipPath>
          </defs>
          {y.ticks.map((v) => (
            <g key={v} className="chart-grid">
              <line x1={M.left} x2={M.left + pw} y1={sy(v)} y2={sy(v)} />
              <text x={M.left - 8} y={sy(v) + 4} textAnchor="end">{fmtNumber(v)}</text>
            </g>
          ))}
          {xt.map((t) => (
            <g key={t} className="chart-xtick">
              <line x1={sx(t)} x2={sx(t)} y1={M.top + ph} y2={M.top + ph + 4} />
              <text x={sx(t)} y={M.top + ph + 18} textAnchor="middle">{fmtTime(t)}</text>
            </g>
          ))}
          <g clipPath={`url(#${id}-clip)`}>
            {bands.map((b, i) => {
              const x0 = Math.max(M.left, sx(b.from))
              const x1 = Math.min(M.left + pw, sx(b.to))
              return <rect key={i} className={`chart-band ${b.tone || ''}`} x={x0} y={M.top} width={Math.max(3, x1 - x0)} height={ph} />
            })}
            <path className="chart-line" d={runsPath(runs)} />
            {showDots && series.map((p) => (
              <circle key={p.ts} className={`chart-pt${p.injected ? ' injected' : ''}`} cx={sx(p.ts)} cy={sy(p.v)} r={p.injected ? 3.4 : 2.4} />
            ))}
          </g>
          <line className="chart-axis" x1={M.left} x2={M.left} y1={M.top} y2={M.top + ph} />
          <line className="chart-axis" x1={M.left} x2={M.left + pw} y1={M.top + ph} y2={M.top + ph} />
          {pick && (
            <g className="chart-cursor">
              <line x1={sx(pick.ts)} x2={sx(pick.ts)} y1={M.top} y2={M.top + ph} />
              <circle cx={sx(pick.ts)} cy={sy(pick.v)} r="5" />
            </g>
          )}
          <rect className="chart-hit" x={M.left} y={M.top} width={pw} height={ph} onPointerMove={onMove}
            onPointerLeave={() => setCursor(null)} />
        </svg>
      </div>
      <p className="chart-readout small" aria-live="polite">
        {pick
          ? <><strong>{fmt(pick.v)}</strong> at {fmtTime(pick.ts)}{pick.injected ? ' (injected test data)' : ''}</>
          : <span className="muted">Point at the chart, or focus it and use the arrow keys, to read single reports.</span>}
      </p>
      <p className="chart-legend small muted">
        {injectedCount > 0 && <span><span className="lg-pt injected" aria-hidden="true" /> Hollow dots: injected test data. </span>}
        {bands.length > 0 && <span><span className="lg-band" aria-hidden="true" /> Shaded: time window of a finding. </span>}
        A gap of more than five minutes breaks the line.
      </p>
      <details className="chart-table">
        <summary>Data table ({series.length} reports)</summary>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">{title} reports</caption>
            <thead><tr><th scope="col">Time (UTC)</th><th scope="col" className="num">{title} ({unit})</th><th scope="col">Data</th></tr></thead>
            <tbody>
              {series.map((p) => (
                <tr key={p.ts}><td>{fmtTime(p.ts)}</td><td className="num">{fmtNumber(p.v)}</td><td>{p.injected ? 'Injected test data' : 'Recorded'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  )
}
