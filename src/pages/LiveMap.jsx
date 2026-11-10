import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import AircraftPanel from '../components/AircraftPanel.jsx'
import AircraftTable from '../components/AircraftTable.jsx'
import RadarMap, { Legend } from '../components/RadarMap.jsx'
import {
  ErrorState, Icon, Loading, PageHead, usePageTitle, usePageVisible, useReducedMotion, useWide,
} from '../components/ui.jsx'
import { REFRESH_MS, aircraftName, fmtDuration, fmtTime, nextScenario, plural } from '../lib.js'

const MODE_KEY = 'ghosttrack.mode'
const HINT_KEY = 'ghosttrack.hint-dismissed'
const MAX_TRAILS = 8
const TRAIL_WINDOW_S = 15 * 60

function readStore(store, key) {
  try { return window[store].getItem(key) } catch { return null }
}

function writeStore(store, key, value) {
  try { window[store].setItem(key, value) } catch { /* private mode: not remembered */ }
}

function ModeToggle({ mode, onChange }) {
  return (
    <fieldset className="mode-toggle">
      <legend>Traffic</legend>
      <div className="segmented">
        {[['recorded', 'Recorded'], ['live', 'Live']].map(([value, label]) => (
          <label key={value} className={mode === value ? 'on' : ''}>
            <input type="radio" name="traffic-mode" value={value} checked={mode === value}
              onChange={() => onChange(value)} />
            {value === 'live' && <span className="live-dot" aria-hidden="true" />}
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function DataLine({ data, auto, pausedReason, onRefresh, busy }) {
  const live = data.source === 'live'
  return (
    <div className="dataline">
      <p className="dataline-source">
        <span className={`source-dot ${live ? 'live' : 'recorded'}`} aria-hidden="true" />
        <strong>{live ? 'Live from adsb.lol, ODbL' : 'Recorded traffic (replay)'}</strong>
        <span className="muted"> · Updated <time dateTime={new Date(data.ts * 1000).toISOString()}>{fmtTime(data.ts)}</time></span>
      </p>
      <p className="dataline-counts">
        {plural(data.aircraft.length, 'aircraft', 'aircraft')}
        {' · '}<span className={data.flagged_aircraft ? 'text-flagged' : ''}>{data.flagged_aircraft} flagged</span>
        {' · '}{data.injected_aircraft} injected
      </p>
      <p className="dataline-refresh">
        {auto ? (
          <span className="muted"><Icon name="refresh" size={14} /> Auto-refresh every {REFRESH_MS / 1000} s</span>
        ) : (
          <span className="muted"><Icon name="pause" size={14} /> Auto-refresh paused{pausedReason ? `: ${pausedReason}` : ''}</span>
        )}
        <button type="button" className="btn ghost small" onClick={onRefresh} aria-disabled={busy ? 'true' : undefined}>
          <Icon name="refresh" size={15} /> {busy ? 'Refreshing…' : 'Refresh'}
        </button>
      </p>
    </div>
  )
}

function StartHint({ data, scenarios, onSelect, onDismiss }) {
  const flagged = data.aircraft.filter((a) => a.flagged)
  const pick = flagged.find((a) => a.callsign) || flagged[0]
  const next = data.source === 'recorded' ? nextScenario(scenarios, data.ts) : null
  return (
    <section className="hint" aria-labelledby="hint-title">
      <div className="hint-icon" aria-hidden="true"><Icon name="info" size={18} /></div>
      <div className="hint-body">
        <h2 id="hint-title">Start here</h2>
        {pick ? (
          <p>
            <strong>{aircraftName(pick)}</strong> is flagged{pick.injected ? ' (injected test data)' : ''}.
            Select it to see which checks failed, the observed values against their thresholds, and the alert.
            {flagged.length > 1 && ` ${flagged.length - 1} other aircraft ${flagged.length === 2 ? 'is' : 'are'} flagged too.`}
          </p>
        ) : (
          <p>
            Nothing is flagged right now.
            {next && <> The next injected scenario, <strong>{next.scenario.title}</strong>, starts in about {fmtDuration(next.inSeconds)}.</>}
            {' '}Meanwhile, the <Link to="/alerts">alert queue</Link> holds the last 24 hours.
          </p>
        )}
        <div className="hint-actions">
          {pick && (
            <button type="button" className="btn primary small" onClick={() => onSelect(pick.icao)}>
              Show {aircraftName(pick)} <Icon name="arrow" size={15} />
            </button>
          )}
          <Link className="btn secondary small" to="/alerts">Alert queue</Link>
          <button type="button" className="btn ghost small" onClick={onDismiss}>Dismiss</button>
        </div>
      </div>
    </section>
  )
}

export default function LiveMap() {
  usePageTitle('Live map')
  const [params, setParams] = useSearchParams()
  const [mode, setMode] = useState(() => (readStore('sessionStorage', MODE_KEY) === 'live' ? 'live' : 'recorded'))
  const [state, setState] = useState({ data: null, error: null, busy: false })
  const [selected, setSelected] = useState(() => (/^[0-9a-f]{6}$/.test(params.get('select') || '') ? params.get('select') : null))
  const [lastSeen, setLastSeen] = useState(null)
  const [trails, setTrails] = useState({})
  const [scenarios, setScenarios] = useState([])
  const [hintOpen, setHintOpen] = useState(() => readStore('localStorage', HINT_KEY) !== '1')
  const reduced = useReducedMotion()
  const visible = usePageVisible()
  const wide = useWide(1024)
  const seq = useRef(0)
  const userSelected = useRef(false) // a selection from the URL on load does not move focus
  const lastFetch = useRef(0)

  const refresh = useCallback(async () => {
    const id = ++seq.current
    lastFetch.current = Date.now()
    setState((s) => ({ ...s, busy: true }))
    try {
      const data = await api.live(mode)
      if (id !== seq.current) return
      setState({ data, error: null, busy: false })
    } catch (error) {
      if (id !== seq.current) return
      setState((s) => ({ data: s.data, error, busy: false }))
    }
  }, [mode])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    api.scenarios().then((r) => setScenarios(r.scenarios || [])).catch(() => setScenarios([]))
  }, [])

  // Auto-refresh every 10 s, paused under reduced motion and while the tab is hidden.
  const auto = !reduced && visible
  useEffect(() => {
    if (!auto) return undefined
    if (Date.now() - lastFetch.current >= REFRESH_MS) refresh() // back from a hidden tab
    const t = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(t)
  }, [auto, refresh])

  const data = state.data
  const current = data?.aircraft.find((a) => a.icao === selected) || null
  useEffect(() => { if (current) setLastSeen({ ...current, seenAt: data.ts }) }, [current, data])

  // Trails: the selected aircraft and up to eight flagged ones, from their stored track.
  const trailKey = useMemo(() => {
    if (!data) return ''
    const flagged = data.aircraft.filter((a) => a.flagged).slice(0, MAX_TRAILS).map((a) => a.icao)
    const want = selected && !flagged.includes(selected) ? [...flagged, selected] : flagged
    return `${data.ts}|${want.join(',')}`
  }, [data, selected])

  useEffect(() => {
    if (!trailKey) return undefined
    const [ts, list] = trailKey.split('|')
    const icaos = list ? list.split(',') : []
    let live = true
    const flaggedSet = new Set(data.aircraft.filter((a) => a.flagged).map((a) => a.icao))
    Promise.all(icaos.map((icao) => api.aircraft(icao).then((r) => [icao, r]).catch(() => [icao, null])))
      .then((results) => {
        if (!live) return
        const next = {}
        for (const [icao, r] of results) {
          const points = (r?.track || []).filter((p) => p.ts >= Number(ts) - TRAIL_WINDOW_S && p.ts <= Number(ts) + 1)
          if (points.length > 1) next[icao] = { points, flagged: flaggedSet.has(icao) }
        }
        setTrails(next)
      })
    return () => { live = false }
    // data is read for the flagged set only; trailKey already changes with it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trailKey])

  const select = useCallback((icao) => {
    userSelected.current = true
    setSelected(icao)
    setParams((p) => {
      const next = new URLSearchParams(p)
      if (icao) next.set('select', icao)
      else next.delete('select')
      return next
    }, { replace: true })
  }, [setParams])

  function changeMode(m) {
    writeStore('sessionStorage', MODE_KEY, m)
    setMode(m)
  }

  function dismissHint() {
    writeStore('localStorage', HINT_KEY, '1')
    setHintOpen(false)
  }

  const shown = current || (selected && lastSeen?.icao === selected ? lastSeen : null)
  const panel = shown && (
    <AircraftPanel aircraft={current ? { ...current, seenAt: data.ts } : shown} gone={!current}
      onClose={() => select(null)} focusOnOpen={userSelected.current} />
  )
  const pausedReason = reduced ? 'reduced motion is on, use Refresh' : !visible ? 'tab hidden' : ''

  return (
    <div className="page live-page">
      <PageHead eyebrow="Southern California · 60 NM around LAX" title="Live map"
        lead="Every aircraft in the picture is run through ten plausibility checks. Flagged aircraft get a red ring; injected test aircraft are hatched and labelled.">
        <ModeToggle mode={mode} onChange={changeMode} />
      </PageHead>
      {mode === 'live' && (
        <p className="mode-note small muted">
          Live is opt-in: the GhostTrack server fetches adsb.lol at most once every 10 s and caches it. Your browser
          only talks to GhostTrack.
        </p>
      )}

      {!data && !state.error && <Loading label="Loading traffic…" />}
      {!data && state.error && <ErrorState error={state.error} onRetry={refresh} />}
      {data && (
        <>
          {hintOpen && <StartHint data={data} scenarios={scenarios} onSelect={select} onDismiss={dismissHint} />}
          <DataLine data={data} auto={auto} pausedReason={pausedReason} onRefresh={() => !state.busy && refresh()} busy={state.busy} />
          {data.notice && <p className="notice" role="status"><Icon name="info" size={16} /> {data.notice}</p>}
          {state.error && (
            <p className="state error small" role="alert">Refresh failed: {state.error.message}. Showing the last picture.</p>
          )}
          <div className="live-grid">
            <div className="map-col">
              <RadarMap aircraft={data.aircraft} selected={selected} onSelect={select} trails={trails}
                overlay={wide ? panel : null}
                summary={data.source === 'live' ? 'LIVE · adsb.lol' : 'REPLAY · recorded'} />
              {!wide && panel}
              <Legend />
            </div>
            <AircraftTable aircraft={data.aircraft} selected={selected} onSelect={select} />
          </div>
        </>
      )}
    </div>
  )
}
