import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import basemap from '../assets/basemap-socal.json'
import {
  ZOOM_MAX, ZOOM_MIN, clampView, glyphTransform, homeView, makeProjection, normalizeTrack, panBy, panUnits,
  polylineD, trailSegments, viewBoxOf, zoomAt,
} from '../geo.js'
import { SOURCES, aircraftName, sourceOf } from '../lib.js'
import { Icon } from './ui.jsx'

export const BASE = basemap.view
export const project = makeProjection(basemap.projection)

// An airliner silhouette in a unit box, nose up (north); rotated by the track when drawn.
export const PLANE = 'M0 -1L.13 -.62L.14 -.22L.92 .2L.92 .38L.14 .2L.1 .66L.36 .84L.36 .96L0 .88L-.36 .96L-.36 .84L-.1 .66L-.14 .2L-.92 .38L-.92 .2L-.14 -.22L-.13 -.62Z'
const DIAMOND = 'M0 -.7L.7 0L0 .7L-.7 0Z' // no reported track
const GROUND = 'M-.45 -.45H.45V.45H-.45Z' // on the ground

// Glyph and label sizes in screen pixels, a little smaller on narrow maps.
const glyphPx = (width) => Math.max(15, Math.min(22, width / 34))
const LABEL_PX = 11.5
const DRAG_PX = 4

function graticule() {
  const { lat_min: la0, lat_max: la1, lon_min: lo0, lon_max: lo1 } = basemap.bounds
  const lines = []
  for (let lat = Math.ceil(la0 * 2) / 2; lat <= la1; lat += 0.5) {
    const [x0, y] = project(lat, lo0)
    const [x1] = project(lat, lo1)
    lines.push(`M${x0.toFixed(2)} ${y.toFixed(2)}H${x1.toFixed(2)}`)
  }
  for (let lon = Math.ceil(lo0 * 2) / 2; lon <= lo1; lon += 0.5) {
    const [x, y0] = project(la1, lon)
    const [, y1] = project(la0, lon)
    lines.push(`M${x.toFixed(2)} ${y0.toFixed(2)}V${y1.toFixed(2)}`)
  }
  return lines.join('')
}
const GRATICULE = graticule()

/** The base map layers, shared with the mini track map on the alert page. */
export function BaseLayers({ unit, rings = true, airports = true }) {
  return (
    <g className="basemap">
      <rect className="sea" x={BASE.x} y={BASE.y} width={BASE.w} height={BASE.h} />
      <path className="graticule" d={GRATICULE} vectorEffect="non-scaling-stroke" />
      {basemap.land.map((d, i) => <path key={`l${i}`} className="land" d={d} vectorEffect="non-scaling-stroke" />)}
      {basemap.borders.map((d, i) => <path key={`b${i}`} className="border" d={d} vectorEffect="non-scaling-stroke" />)}
      {basemap.coast.map((d, i) => <path key={`c${i}`} className="coast" d={d} vectorEffect="non-scaling-stroke" />)}
      {rings && basemap.range_rings_nm.map((r) => (
        <g key={r} className="ring">
          <circle cx="0" cy="0" r={r} vectorEffect="non-scaling-stroke" />
          <text x={unit * 4} y={-r - unit * 4} fontSize={unit * 10}>{r} NM</text>
        </g>
      ))}
      <rect className="extent" x={BASE.x} y={BASE.y} width={BASE.w} height={BASE.h} vectorEffect="non-scaling-stroke" />
      {airports && basemap.airports.map((a) => (
        <g key={a.code} className="airport">
          <rect x={a.x - unit * 3} y={a.y - unit * 3} width={unit * 6} height={unit * 6} vectorEffect="non-scaling-stroke" />
          <text x={a.x + unit * 6} y={a.y + unit * 4} fontSize={unit * 10.5}>{a.code}</text>
        </g>
      ))}
    </g>
  )
}

/** Hatch patterns for injected aircraft, one per source colour (patterns do not inherit colour). */
export function HatchDefs({ prefix }) {
  return (
    <defs>
      {Object.keys(SOURCES).map((s) => (
        <pattern key={s} id={`${prefix}-hatch-${s}`} className={`hatch src-${s}`} patternUnits="userSpaceOnUse"
          width=".34" height=".34" patternTransform="rotate(45)">
          <rect width=".34" height=".34" className="hatch-bg" />
          <rect width=".14" height=".34" className="hatch-line" />
        </pattern>
      ))}
    </defs>
  )
}

export function glyphPath(a) {
  if (a.on_ground) return GROUND
  return normalizeTrack(a.track) === null ? DIAMOND : PLANE
}

function Aircraft({ a, size, unit, selected, onSelect, prefix, showLabel }) {
  const [x, y] = project(a.lat, a.lon)
  const src = sourceOf(a.source)
  const name = aircraftName(a)
  const labelSize = unit * LABEL_PX
  const lx = x + size * 1.35
  const ly = y - size * 0.35
  const label = showLabel || a.flagged || selected || a.injected
  const tagW = labelSize * 0.66 * 8.6
  return (
    <g className={`ac src-${src}${a.flagged ? ' flagged' : ''}${a.injected ? ' injected' : ''}${selected ? ' selected' : ''}`}
      onClick={(e) => { e.stopPropagation(); onSelect(a.icao) }} data-icao={a.icao}>
      <circle className="hit" cx={x} cy={y} r={size * 1.3} />
      {selected && <circle className="sel-ring" cx={x} cy={y} r={size * 1.55} vectorEffect="non-scaling-stroke" />}
      {a.flagged && <circle className="flag-ring" cx={x} cy={y} r={size * 1.12} vectorEffect="non-scaling-stroke" />}
      <path className="glyph" d={glyphPath(a)} transform={glyphTransform(x, y, a.on_ground ? null : a.track, size * 0.5)}
        fill={a.injected ? `url(#${prefix}-hatch-${src})` : undefined} vectorEffect="non-scaling-stroke" />
      {label && (
        <g className="ac-label" fontSize={labelSize}>
          <text x={lx} y={ly} strokeWidth={unit * 3.2}>{name}</text>
          {a.injected && (
            <g className="inj-tag">
              <rect x={lx - unit * 2} y={ly + labelSize * 0.45} width={tagW} height={labelSize * 1.25}
                rx={unit * 2} vectorEffect="non-scaling-stroke" />
              <text x={lx + unit * 2} y={ly + labelSize * 1.42} fontSize={labelSize * 0.82}>INJECTED</text>
            </g>
          )}
        </g>
      )}
    </g>
  )
}

/**
 * The SVG traffic map: base map, range rings, airports, trails and aircraft glyphs, with pan and
 * zoom by buttons, keyboard (+ / - / arrows / 0) and wheel, and drag with a mouse. Aircraft are
 * selected by clicking a glyph; the aircraft list next to the map offers the same by keyboard.
 */
export default function RadarMap({ aircraft, selected, onSelect, trails = {}, summary, overlay, initialView }) {
  const prefix = useId().replace(/:/g, '')
  const wrapRef = useRef(null)
  const svgRef = useRef(null)
  const home = useMemo(() => initialView || homeView(BASE), [initialView])
  const [view, setView] = useState(home)
  const [width, setWidth] = useState(800)
  const drag = useRef(null)
  const helpId = `${prefix}-help`

  useEffect(() => {
    const el = svgRef.current
    if (!el) return undefined
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width
      if (w > 0) setWidth(w)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Map units per screen pixel at the current zoom: glyphs and labels keep a constant pixel size.
  const unit = BASE.w / (width * view.z)
  const size = glyphPx(width) * unit

  const toMap = useCallback((clientX, clientY) => {
    const rect = svgRef.current.getBoundingClientRect()
    const w = BASE.w / view.z
    const h = BASE.h / view.z
    return [view.cx - w / 2 + ((clientX - rect.left) / rect.width) * w, view.cy - h / 2 + ((clientY - rect.top) / rect.height) * h]
  }, [view])

  // Wheel zoom about the cursor. A native listener, because React's wheel listener is passive.
  useEffect(() => {
    const el = svgRef.current
    if (!el) return undefined
    const onWheel = (e) => {
      e.preventDefault()
      const factor = Math.exp(Math.max(-1, Math.min(1, -e.deltaY * 0.0025)))
      const [ux, uy] = toMap(e.clientX, e.clientY)
      setView((v) => zoomAt(v, factor, BASE, ux, uy))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [toMap])

  // Keep a newly selected aircraft in view when zoomed in.
  useEffect(() => {
    const a = aircraft.find((x) => x.icao === selected)
    if (!a || !Number.isFinite(a.lat)) return
    setView((v) => {
      if (v.z <= 1) return v
      const [x, y] = project(a.lat, a.lon)
      const w = BASE.w / v.z
      const h = BASE.h / v.z
      const inside = Math.abs(x - v.cx) < w * 0.42 && Math.abs(y - v.cy) < h * 0.42
      return inside ? v : clampView({ ...v, cx: x, cy: y }, BASE)
    })
    // only when the selection changes, not on every refresh
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  const zoom = (f) => setView((v) => zoomAt(v, f, BASE))
  const reset = () => setView(home)
  const atHome = Math.abs(view.z - home.z) < 1e-6 && Math.abs(view.cx - home.cx) < 1e-6 && Math.abs(view.cy - home.cy) < 1e-6

  function onKeyDown(e) {
    if (e.target.closest?.('.ac-panel')) return // keys inside the details panel are its own
    const step = 0.15
    const actions = {
      '+': () => zoom(1.5), '=': () => zoom(1.5), '-': () => zoom(1 / 1.5), _: () => zoom(1 / 1.5),
      ArrowLeft: () => setView((v) => panBy(v, -step, 0, BASE)),
      ArrowRight: () => setView((v) => panBy(v, step, 0, BASE)),
      ArrowUp: () => setView((v) => panBy(v, 0, -step, BASE)),
      ArrowDown: () => setView((v) => panBy(v, 0, step, BASE)),
      0: reset, Home: reset,
    }
    const act = actions[e.key]
    if (act && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault()
      act()
    }
  }

  function onPointerDown(e) {
    if (e.pointerType === 'touch' || e.button !== 0) return // touch scrolls the page; use the buttons
    drag.current = { x: e.clientX, y: e.clientY, view, moved: false, id: e.pointerId }
  }

  function onPointerMove(e) {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return
    if (!d.moved) {
      d.moved = true
      svgRef.current.setPointerCapture?.(e.pointerId)
    }
    const rect = svgRef.current.getBoundingClientRect()
    const k = BASE.w / d.view.z / rect.width
    setView(panUnits(d.view, -dx * k, -dy * k, BASE))
  }

  function onPointerUp(e) {
    const d = drag.current
    if (d?.moved) {
      svgRef.current.releasePointerCapture?.(e.pointerId)
      // swallow the click that follows a drag
      const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault() }
      window.addEventListener('click', swallow, { capture: true, once: true })
      setTimeout(() => window.removeEventListener('click', swallow, { capture: true }), 0)
    }
    drag.current = null
  }

  // Draw order: ordinary traffic, then injected, flagged and finally the selected aircraft.
  const ordered = useMemo(() => {
    const rank = (a) => (a.icao === selected ? 3 : a.flagged ? 2 : a.injected ? 1 : 0)
    return aircraft.filter((a) => Number.isFinite(a.lat) && Number.isFinite(a.lon))
      .sort((a, b) => rank(a) - rank(b))
  }, [aircraft, selected])

  const trailPaths = useMemo(() => Object.entries(trails).map(([icao, t]) => ({
    icao, flagged: t.flagged, d: trailSegments(t.points, project).map(polylineD).join(' '),
  })).filter((t) => t.d), [trails])

  const pct = Math.round(view.z * 100)
  return (
    <div className="radar">
      <div className="radar-frame" ref={wrapRef} tabIndex={0} role="group" onKeyDown={onKeyDown}
        aria-label="Traffic map" aria-describedby={helpId}>
        <svg ref={svgRef} className="radar-svg" viewBox={viewBoxOf(view, BASE)} preserveAspectRatio="xMidYMid meet"
          style={{ aspectRatio: `${BASE.w} / ${BASE.h}` }} aria-hidden="true" focusable="false"
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { drag.current = null }}>
          <HatchDefs prefix={prefix} />
          <BaseLayers unit={unit} />
          <g className="trails">
            {trailPaths.map((t) => (
              <path key={t.icao} className={`trail${t.flagged ? ' flagged' : ''}${t.icao === selected ? ' selected' : ''}`}
                d={t.d} vectorEffect="non-scaling-stroke" />
            ))}
          </g>
          <g className="traffic">
            {ordered.map((a) => (
              <Aircraft key={a.icao} a={a} size={size} unit={unit} selected={a.icao === selected} onSelect={onSelect}
                prefix={prefix} showLabel={view.z >= 3} />
            ))}
          </g>
        </svg>
        <div className="radar-controls">
          <button type="button" className="map-btn" onClick={() => zoom(1.5)} disabled={view.z >= ZOOM_MAX}
            aria-label="Zoom in" title="Zoom in (+)"><Icon name="plus" /></button>
          <button type="button" className="map-btn" onClick={() => zoom(1 / 1.5)} disabled={view.z <= ZOOM_MIN}
            aria-label="Zoom out" title="Zoom out (−)"><Icon name="minus" /></button>
          <button type="button" className="map-btn" onClick={reset} disabled={atHome}
            aria-label="Reset view" title="Reset view (0)"><Icon name="home" /></button>
        </div>
        <div className="radar-north" aria-hidden="true"><span>N</span></div>
        {summary && <div className="radar-summary" aria-hidden="true">{summary}</div>}
        <p className="radar-attrib">ADS-B data: adsb.lol, ODbL · Map: Natural Earth</p>
        {overlay}
        <span className="sr-only" role="status">{atHome ? '' : `Zoom ${pct} percent`}</span>
      </div>
      <p id={helpId} className="radar-help">
        <span className="kbd-hint">Focus the map, then <kbd>+</kbd> <kbd>−</kbd> to zoom, arrow keys to pan, <kbd>0</kbd> to reset.</span>{' '}
        <span>Rings at 20, 40 and 60 NM. The aircraft list has the same traffic.</span>
      </p>
    </div>
  )
}

/** The legend: colour by position source, plus the flagged ring and the injected hatching. */
export function Legend() {
  const prefix = 'legend'
  const item = (cls, label, inj = false) => (
    <li key={label}>
      <svg width="22" height="22" viewBox="-1.4 -1.4 2.8 2.8" aria-hidden="true" focusable="false" className={`ac ${cls}`}>
        {cls.includes('flagged') && <circle className="flag-ring" r="1.2" />}
        <path className="glyph" d={PLANE} transform="rotate(45) scale(.8)"
          fill={inj ? `url(#${prefix}-hatch-${cls.split(' ')[0].replace('src-', '')})` : undefined} />
      </svg>
      {label}
    </li>
  )
  return (
    <div className="legend">
      <svg width="0" height="0" className="sr-only" aria-hidden="true" focusable="false"><HatchDefs prefix={prefix} /></svg>
      <p className="legend-title">Legend</p>
      <ul>
        {Object.entries(SOURCES).map(([k, s]) => item(`src-${k}`, s.label))}
        {item('src-adsb flagged', 'Flagged (red ring)')}
        {item('src-adsb injected', 'Injected test data (hatched)', true)}
      </ul>
    </div>
  )
}
