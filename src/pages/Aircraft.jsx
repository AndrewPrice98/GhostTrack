import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import { Chip, EmptyCta, ErrorState, Icon, InjectedTag, Loading, PageHead, useAsync, usePageTitle } from '../components/ui.jsx'
import {
  ATTRIBUTION, MODE_KEY, SOURCES, aircraftName, filterAircraft, fmtAlt, fmtNumber, fmtTime, fmtTrack, plural, readStore,
  sortAircraft, sourceOf,
} from '../lib.js'

const HEX6 = /^[0-9a-f]{6}$/i

/** The aircraft in the current picture, each linking to its track history. */
export default function Aircraft() {
  usePageTitle('Aircraft')
  const navigate = useNavigate()
  const mode = readStore('sessionStorage', MODE_KEY) === 'live' ? 'live' : 'recorded'
  const { data, loading, error, reload } = useAsync(() => api.live(mode), [mode])
  const [query, setQuery] = useState('')
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const rows = useMemo(
    () => sortAircraft(filterAircraft(data?.aircraft, { query, flaggedOnly })),
    [data, query, flaggedOnly],
  )
  const q = query.trim()
  const known = data?.aircraft.some((a) => a.icao === q.toLowerCase())

  function lookup(e) {
    e.preventDefault()
    if (HEX6.test(q)) navigate(`/aircraft/${encodeURIComponent(q.toLowerCase())}`)
  }

  return (
    <div className="page">
      <PageHead eyebrow="Track history" title="Aircraft"
        lead="Pick an aircraft to see its altitude and speed profile, track, registration facts and findings. The list is the current picture; any six-digit hex address can be looked up." />

      <form className="filters card" onSubmit={lookup} role="search" aria-label="Find an aircraft">
        <label className="field compact grow">
          <span>Callsign or hex address</span>
          <span className="input-icon">
            <Icon name="search" size={15} />
            <input type="search" placeholder="e.g. GHO1 or adf81e" value={query} maxLength={16}
              onChange={(e) => setQuery(e.target.value)} />
          </span>
        </label>
        <label className="check filter-check">
          <input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} />
          Flagged only
        </label>
        {HEX6.test(q) && !known && (
          <button className="btn secondary small">Look up {q.toUpperCase()} <Icon name="arrow" size={14} /></button>
        )}
      </form>

      {error && <ErrorState error={error} onRetry={reload} />}
      {loading && !data && <Loading label="Loading aircraft…" />}
      {data && data.aircraft.length === 0 && (
        <EmptyCta what="aircraft">The traffic picture is empty, so there are no tracks to show.</EmptyCta>
      )}
      {data && data.aircraft.length > 0 && (
        <>
          <p className="muted small dataline-note">
            {plural(data.aircraft.length, 'aircraft', 'aircraft')} in the {data.source === 'live' ? 'live feed' : 'recorded replay'},
            updated {fmtTime(data.ts)}; flagged first. {ATTRIBUTION}
          </p>
          {rows.length === 0 ? (
            <div className="state">No aircraft match. {HEX6.test(q) && <>Try the look-up button to open its stored history.</>}</div>
          ) : (
            <div className="card flush table-card">
              <table className="data-table stack-table ac-index">
                <caption className="sr-only">Aircraft in the current picture, flagged first</caption>
                <thead>
                  <tr>
                    <th scope="col">Aircraft</th>
                    <th scope="col" className="num">Altitude</th>
                    <th scope="col" className="num">Speed</th>
                    <th scope="col" className="num">Track</th>
                    <th scope="col">Source</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => {
                    const src = sourceOf(a.source)
                    return (
                      <tr key={a.icao} className={a.flagged ? 'sev-row-high' : ''}>
                        <th scope="row" className="name-cell">
                          <Link to={`/aircraft/${encodeURIComponent(a.icao)}`} className="alert-link ac-subject">
                            {aircraftName(a)}<span className="sr-only"> track history</span>
                          </Link>
                          <span className="muted small mono"> {a.icao.toUpperCase()}</span>
                        </th>
                        <td className="num" data-label="Altitude">{fmtAlt(a.alt, a.on_ground)}</td>
                        <td className="num" data-label="Speed">{Number.isFinite(a.gs) ? `${fmtNumber(a.gs)} kt` : '—'}</td>
                        <td className="num" data-label="Track">{fmtTrack(a.track)}</td>
                        <td data-label="Source"><span className={`src-dot src-${src}`} aria-hidden="true" />{SOURCES[src].label}</td>
                        <td data-label="Status" className="status-cell">
                          {a.flagged && <Chip tone="flagged" glyph="◎">Flagged</Chip>}
                          {a.injected && <> <InjectedTag short /></>}
                          {!a.flagged && !a.injected && <span className="muted small">{a.alert_ids?.length ? 'Low severity only' : 'No alerts'}</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
