import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import {
  FLAG_WINDOW_MIN, INJECTED_NOTICE, SOURCES, aircraftName, categoryLabel, evidenceRows, fmtAlt, fmtCoord, fmtNumber,
  fmtTime, fmtTrack, sourceOf,
} from '../lib.js'
import { FlaggedTag, Icon, InjectedTag, SeverityChip } from './ui.jsx'

const MAX_ALERTS = 4

function FailingCheck({ alert }) {
  const rows = evidenceRows(alert.evidence).filter((r) => r.kind !== 'fact' || r.field !== 'addresses')
  return (
    <li className="fcheck">
      <div className="fcheck-head">
        <SeverityChip severity={alert.severity} />
        <span className="mono muted small">{alert.detection_id}</span>
      </div>
      <h4>{alert.title}</h4>
      <p>{alert.explanation}</p>
      {rows.length > 0 && (
        <dl className="obs">
          {rows.slice(0, 2).map((r) => (
            <div key={r.field}>
              <dt>{r.label}</dt>
              <dd><strong>{r.observed}</strong>{r.comparison && <span className="muted"> · {r.comparison}</span>}</dd>
            </div>
          ))}
        </dl>
      )}
      <Link className="more" to={`/alerts/${alert.id}`}>
        Open alert #{alert.id}<span className="sr-only">: {alert.title}</span> <Icon name="arrow" size={14} />
      </Link>
    </li>
  )
}

/**
 * Details of the selected aircraft and the checks that failed for it. Focus moves to the panel
 * heading when an aircraft is selected; Escape or the close button returns it.
 */
export default function AircraftPanel({ aircraft, gone, onClose, focusOnOpen = true, collapsed = false, onToggleCollapsed }) {
  const headingRef = useRef(null)
  const opener = useRef(null)
  const [alerts, setAlerts] = useState({ loading: false, items: [], error: null })
  const ids = (aircraft.alert_ids || []).slice(0, MAX_ALERTS)
  const idKey = ids.join(',')

  // Focus the heading when a new aircraft is selected; remember what had focus to return to it.
  useEffect(() => {
    const active = document.activeElement
    if (active && !active.closest?.('.ac-panel')) opener.current = active
    if (focusOnOpen) headingRef.current?.focus()
    // only when another aircraft is selected
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aircraft.icao])

  function close() {
    const back = opener.current
    onClose()
    requestAnimationFrame(() => { if (back?.isConnected) back.focus() })
  }

  useEffect(() => {
    if (!idKey) {
      setAlerts({ loading: false, items: [], error: null })
      return undefined
    }
    let live = true
    setAlerts((s) => ({ ...s, loading: true, error: null }))
    Promise.all(idKey.split(',').map((id) => api.alert(id)))
      .then((items) => live && setAlerts({ loading: false, items, error: null }))
      .catch((error) => live && setAlerts({ loading: false, items: [], error }))
    return () => { live = false }
  }, [idKey])

  const src = sourceOf(aircraft.source)
  const more = (aircraft.alert_ids || []).length - ids.length
  return (
    <aside className={`ac-panel card${collapsed ? ' collapsed' : ''}`} aria-labelledby="ac-panel-title"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); close() } }}>
      <header className="ac-panel-head">
        <div>
          <p className="eyebrow">Selected aircraft</p>
          <h2 id="ac-panel-title" ref={headingRef} tabIndex={-1}>{aircraftName(aircraft)}</h2>
          <div className="chips">
            {aircraft.flagged && <FlaggedTag />}
            {aircraft.injected && <InjectedTag />}
            {!aircraft.flagged && !aircraft.injected && <span className="chip neutral">No alerts</span>}
          </div>
        </div>
        <div className="panel-tools">
          {onToggleCollapsed && (
            <button type="button" className="btn ghost small icon-only panel-toggle" onClick={onToggleCollapsed}
              aria-expanded={!collapsed} aria-controls="ac-panel-body">
              <Icon name="chevron" /><span className="sr-only">{collapsed ? 'Expand aircraft details' : 'Collapse aircraft details'}</span>
            </button>
          )}
          <button type="button" className="btn ghost small icon-only" onClick={close}>
            <Icon name="close" /><span className="sr-only">Close aircraft details</span>
          </button>
        </div>
      </header>
      <div id="ac-panel-body" className="panel-body" hidden={collapsed}>
      {gone && <p className="state small" role="status">No longer in the feed. Showing the last report.</p>}
      {aircraft.injected && <p className="injected-banner small">{INJECTED_NOTICE}</p>}
      <dl className="kv">
        <div><dt>ICAO address</dt><dd className="mono">{aircraft.icao.toUpperCase()}</dd></div>
        <div><dt>Callsign</dt><dd>{aircraft.callsign || '—'}</dd></div>
        <div><dt>Altitude</dt><dd>{fmtAlt(aircraft.alt, aircraft.on_ground)}</dd></div>
        <div><dt>Ground speed</dt><dd>{Number.isFinite(aircraft.gs) ? `${fmtNumber(aircraft.gs)} kt` : '—'}</dd></div>
        <div><dt>Track</dt><dd>{fmtTrack(aircraft.track)}</dd></div>
        <div><dt>Category</dt><dd>{categoryLabel(aircraft.category)}</dd></div>
        <div>
          <dt>Position source</dt>
          <dd className="stack">
            <span><span className={`src-dot src-${src}`} aria-hidden="true" />{SOURCES[src].label}</span>
            <span className="muted small">{SOURCES[src].long}</span>
          </dd>
        </div>
        <div><dt>Position</dt><dd>{fmtCoord(aircraft.lat, aircraft.lon)}</dd></div>
        {aircraft.seenAt && <div><dt>Report time</dt><dd>{fmtTime(aircraft.seenAt)}</dd></div>}
      </dl>

      <section className="panel-checks" aria-labelledby="ac-checks-title">
        <h3 id="ac-checks-title">Failing checks</h3>
        {!aircraft.flagged && (
          <p className="muted small">
            No detection fired for this aircraft in the last {FLAG_WINDOW_MIN} minutes. Checks that lack their inputs
            (for example no altitude) are not evaluated rather than counted as clean.
          </p>
        )}
        {alerts.loading && !alerts.items.length && <p className="muted small" role="status">Loading the alerts…</p>}
        {alerts.error && <p className="state error small" role="alert">Could not load the alerts: {alerts.error.message}</p>}
        {alerts.items.length > 0 && (
          <ul className="checks">{alerts.items.map((al) => <FailingCheck key={al.id} alert={al} />)}</ul>
        )}
        {more > 0 && (
          <p className="small"><Link to={`/alerts?q=${encodeURIComponent(aircraft.icao)}`}>{more} more alert{more === 1 ? '' : 's'} for this aircraft</Link></p>
        )}
      </section>
      <p className="panel-foot small">
        <Link to={`/aircraft/${encodeURIComponent(aircraft.icao)}`}>Track history and profile <Icon name="arrow" size={14} /></Link>
      </p>
      </div>
    </aside>
  )
}
