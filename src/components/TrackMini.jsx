import { useEffect, useId, useRef, useState } from 'react'
import { fitBox, glyphTransform, labelPlacement, polylineD, trailSegments } from '../geo.js'
import { aircraftName, fmtTime } from '../lib.js'
import { BaseLayers, HatchDefs, PLANE, project } from './RadarMap.jsx'

const ASPECT = 1.7

/**
 * A small static map of an alert's track: the base map zoomed to the track, the track drawn as a
 * line, start and end marked, and the reports cited as evidence highlighted in red.
 */
export default function TrackMini({ alert, empty }) {
  const prefix = useId().replace(/:/g, '')
  const ref = useRef(null)
  const [width, setWidth] = useState(640) // drawn width in px: glyphs and labels keep their pixel size
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const ro = new ResizeObserver(([entry]) => { if (entry.contentRect.width > 0) setWidth(entry.contentRect.width) })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const points = (alert.track || []).filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
  if (points.length === 0) {
    return (
      <p className="state small">
        {empty || 'No track points are stored for this alert (the replay keeps a rolling 30-minute window). The failing checks above are still the evidence.'}
      </p>
    )
  }
  const xy = points.map((p) => project(p.lat, p.lon))
  const box = fitBox(xy, { pad: 0.18, minSpan: 8, aspect: ASPECT })
  const unit = box.w / width
  const segs = trailSegments(points, project)
  const evTimes = new Set((alert.evidence || []).map((e) => e.ts))
  const hits = points.filter((p) => evTimes.has(p.ts))
  const first = points[0]
  const last = points[points.length - 1]
  const [lx, ly] = project(last.lat, last.lon)
  const [fx, fy] = project(first.lat, first.lon)
  const name = aircraftName(alert)
  // Labels sit off the line, on the side away from the neighbouring report, and inside the map.
  const textW = (text) => text.length * 12 * 0.62 * unit
  const endLabel = `${name} ${fmtTime(last.ts)}`
  const startLabel = `Start ${fmtTime(first.ts)}`
  const pt = ([x, y]) => ({ x, y })
  const place = (anchor, neighbour, text, gap, lift) => labelPlacement(pt(anchor), neighbour ? pt(neighbour) : null,
    { box, textW: textW(text), gap: unit * gap, lift: unit * lift, size: unit * 12 })
  const endAt = place([lx, ly], xy.length > 1 ? xy[xy.length - 2] : null, endLabel, 17, 18)
  const startAt = place([fx, fy], xy.length > 1 ? xy[1] : null, startLabel, 9, 12)
  const injected = alert.injected
  const desc = `Track of ${name}: ${points.length} reports from ${fmtTime(first.ts)} to ${fmtTime(last.ts)}`
    + `${hits.length ? `, ${hits.length} of them cited as evidence (red)` : ''}.${injected ? ' Injected test data.' : ''}`
  return (
    <figure className="track-mini">
      <svg ref={ref} viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} role="img" aria-label={desc} focusable="false"
        preserveAspectRatio="xMidYMid slice">
        <HatchDefs prefix={prefix} />
        <BaseLayers unit={unit} />
        {segs.map((s, i) => (
          <path key={i} className={`mini-track${injected ? ' injected' : ''}`} d={polylineD(s)} vectorEffect="non-scaling-stroke" />
        ))}
        {points.map((p) => {
          const [x, y] = project(p.lat, p.lon)
          return <circle key={`${p.ts}-${x}`} className={evTimes.has(p.ts) ? 'mini-pt hit' : 'mini-pt'} cx={x} cy={y}
            r={unit * (evTimes.has(p.ts) ? 4.5 : 2.5)} vectorEffect="non-scaling-stroke" />
        })}
        <circle className="mini-start" cx={fx} cy={fy} r={unit * 6} vectorEffect="non-scaling-stroke" />
        <g className={`ac src-adsb flagged${injected ? ' injected' : ''}`}>
          <circle className="flag-ring" cx={lx} cy={ly} r={unit * 14} vectorEffect="non-scaling-stroke" />
          <path className="glyph" d={PLANE} transform={glyphTransform(lx, ly, last.track, unit * 10)}
            fill={injected ? `url(#${prefix}-hatch-adsb)` : undefined} vectorEffect="non-scaling-stroke" />
        </g>
        <text className="mini-label" x={startAt.x} y={startAt.y} fontSize={unit * 12}
          strokeWidth={unit * 3} textAnchor={startAt.textAnchor}>{startLabel}</text>
        <text className="mini-label" x={endAt.x} y={endAt.y} fontSize={unit * 12}
          strokeWidth={unit * 3} textAnchor={endAt.textAnchor}>{endLabel}</text>
      </svg>
      <figcaption className="small muted">
        {points.length} reports, {fmtTime(first.ts)} to {fmtTime(last.ts)}. Ring: last position. Hollow circle: first report.
        {hits.length > 0 && ' Red dots: reports cited as evidence.'}
      </figcaption>
    </figure>
  )
}
