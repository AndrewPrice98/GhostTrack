import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api.js'
import {
  Empty, EmptyCta, ErrorState, Icon, Loading, OriginTag, PageHead, SeverityChip, StatusChip, useDebounced, usePageTitle,
} from '../components/ui.jsx'
import { DETECTIONS, SEVERITY, STATUS, alertSubject, fmtAgo, fmtDateTime, plural } from '../lib.js'

const PAGE = 50
const FILTERS = ['severity', 'detection', 'status', 'injected', 'sort']
const SEARCH_MAX = 32 // the API's limit for q
const SEARCH_DEBOUNCE_MS = 250

function Select({ label, name, value, onChange, options }) {
  return (
    <label className="field compact">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(name, e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}

export default function Alerts() {
  usePageTitle('Alerts')
  const [params, setParams] = useSearchParams()
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) || '']))
  const query = (params.get('q') || '').slice(0, SEARCH_MAX) // the searched text, as kept in the URL
  const [text, setText] = useState(query) // what is typed; the URL (and the request) follow it after a pause
  const typed = useDebounced(text.trim(), SEARCH_DEBOUNCE_MS)
  const [state, setState] = useState({ loading: true, error: null, items: [], total: 0 })
  const [tick, setTick] = useState(0)
  const [pages, setPages] = useState(1)
  const filterKey = [...FILTERS.map((k) => filters[k]), query].join('|')

  useEffect(() => { setPages(1) }, [filterKey])

  // The URL is the source of truth for the search: typing updates it after a pause, and a change
  // from elsewhere (Clear filters, a link from the map) is shown in the box.
  useEffect(() => {
    if (typed !== query) setFilter('q', typed)
    // only when the typed text settles
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed])
  useEffect(() => { setText((t) => (t.trim() === query ? t : query)) }, [query])

  useEffect(() => {
    let live = true
    const [severity, detection, status, injected, sort] = filterKey.split('|')
    setState((s) => ({ ...s, loading: true, error: null }))
    api.alerts({ q: query, severity, detection, status, injected, sort: sort || 'first_ts', limit: PAGE * pages, offset: 0 })
      .then((r) => live && setState({ loading: false, error: null, items: r.items, total: r.total }))
      .catch((error) => live && setState((s) => ({ ...s, loading: false, error })))
    return () => { live = false }
    // query is part of filterKey
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, pages, tick])

  function setFilter(name, value) {
    setParams((p) => {
      const next = new URLSearchParams(p)
      if (value) next.set(name, value)
      else next.delete(name)
      return next
    }, { replace: true })
  }

  const shown = state.items
  const now = Date.now() / 1000
  const counts = useMemo(() => {
    const c = { high: 0, new: 0, injected: 0 }
    for (const a of state.items) {
      if (a.severity === 'high') c.high += 1
      if (a.status === 'new') c.new += 1
      if (a.injected) c.injected += 1
    }
    return c
  }, [state.items])
  const anyFilter = FILTERS.some((k) => filters[k]) || query
  const more = state.items.length < state.total

  return (
    <div className="page">
      <PageHead eyebrow="Triage queue" title="Alerts"
        lead="Each alert names the detection that fired and the values that failed it. Alerts from injected scenarios are labelled as test data." />

      <section className="filters card" aria-label="Filter alerts">
        <label className="field compact grow">
          <span>Search</span>
          <span className="input-icon">
            <Icon name="search" size={15} />
            <input type="search" placeholder="Callsign or hex, e.g. ALT1 or adfbb1" value={text} maxLength={SEARCH_MAX}
              onChange={(e) => setText(e.target.value)} />
          </span>
        </label>
        <Select label="Severity" name="severity" value={filters.severity} onChange={setFilter}
          options={[['', 'All'], ...Object.entries(SEVERITY).map(([k, v]) => [k, v.label])]} />
        <Select label="Detection" name="detection" value={filters.detection} onChange={setFilter}
          options={[['', 'All'], ...DETECTIONS.map(([id, t]) => [id, `${id} ${t}`])]} />
        <Select label="Status" name="status" value={filters.status} onChange={setFilter}
          options={[['', 'All'], ...Object.entries(STATUS).map(([k, v]) => [k, v.label])]} />
        <Select label="Data" name="injected" value={filters.injected} onChange={setFilter}
          options={[['', 'All'], ['true', 'Injected test data'], ['false', 'Real traffic only']]} />
        <Select label="Sort" name="sort" value={filters.sort} onChange={setFilter}
          options={[['', 'Newest first'], ['last_ts', 'Most recently active'], ['severity', 'Severity'], ['confidence', 'Confidence']]} />
        {anyFilter && (
          <button type="button" className="btn ghost small clear-btn" onClick={() => { setText(''); setParams(new URLSearchParams(), { replace: true }) }}>
            Clear filters
          </button>
        )}
      </section>

      {state.error && <ErrorState error={state.error} onRetry={() => setTick((t) => t + 1)} />}
      {!state.error && state.loading && !state.items.length && <Loading label="Loading alerts…" />}
      {!state.error && !(state.loading && !state.items.length) && (
        <>
          <div className="queue-summary" aria-live="polite">
            <p>
              <strong>{plural(state.total, 'alert')}</strong>{anyFilter ? ' match' : ' in the queue'}
              {query && <> for callsign or hex “{query}”</>}
            </p>
            {state.items.length > 0 && (
              <ul className="mini-stats" aria-label="Loaded alerts">
                <li><span className="n">{counts.high}</span> high</li>
                <li><span className="n">{counts.new}</span> new</li>
                <li><span className="n">{counts.injected}</span> injected</li>
              </ul>
            )}
          </div>
          {!shown.length && !anyFilter ? (
            <EmptyCta what="alerts">Alerts appear as the detections fire on the traffic.</EmptyCta>
          ) : !shown.length ? (
            <Empty>
              {anyFilter ? 'No alerts match. ' : 'No alerts yet. '}
              {anyFilter
                ? <button type="button" className="link" onClick={() => setParams(new URLSearchParams(), { replace: true })}>Clear the filters</button>
                : <>Alerts appear as the detections fire on the traffic. <Link to="/">Watch the live map</Link>.</>}
            </Empty>
          ) : (
            <div className="card flush table-card">
              <table className="alert-table">
                <caption className="sr-only">Alerts, {filters.sort ? 'sorted' : 'newest first'}</caption>
                <thead>
                  <tr>
                    <th scope="col">Severity</th>
                    <th scope="col">Alert</th>
                    <th scope="col">Aircraft</th>
                    <th scope="col">First seen (UTC)</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((a) => (
                    <tr key={a.id} className={`sev-row-${a.severity}`}>
                      <td className="sev-cell"><SeverityChip severity={a.severity} /></td>
                      <td className="title-cell">
                        <Link to={`/alerts/${encodeURIComponent(a.id)}`} className="alert-link">{a.title}</Link>
                        <span className="muted small mono"> {a.detection_id} · #{a.id}</span>
                      </td>
                      <td className="ac-cell" data-label="Aircraft">
                        <span className="ac-subject">{alertSubject(a)}</span>
                        {a.icao && a.callsign && <span className="muted small mono"> {a.icao.toUpperCase()}</span>}
                        {(a.injected || a.source === 'upload') && <> <OriginTag item={a} short /></>}
                      </td>
                      <td className="time-cell" data-label="First seen">
                        {fmtDateTime(a.first_ts)} <span className="muted small">{fmtAgo(a.first_ts, now)}</span>
                      </td>
                      <td className="status-cell" data-label="Status"><StatusChip status={a.status} /></td>
                      <td className="num conf-cell" data-label="Confidence">{Number.isFinite(a.confidence) ? a.confidence.toFixed(2) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {more && (
            <p className="load-more">
              <button type="button" className="btn secondary" onClick={() => { if (!state.loading) setPages((n) => n + 1) }}
                aria-disabled={state.loading ? 'true' : undefined}>
                {state.loading ? 'Loading…' : `Load ${Math.min(PAGE, state.total - state.items.length)} more`}
              </button>
              <span className="muted small"> Showing {state.items.length} of {state.total}.</span>
            </p>
          )}
        </>
      )}
    </div>
  )
}
