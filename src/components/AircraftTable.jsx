import { useMemo, useState } from 'react'
import {
  AIRCRAFT_SORTS, SOURCES, aircraftName, filterAircraft, fmtAlt, fmtNumber, fmtTrack, plural, sortAircraft, sourceOf,
} from '../lib.js'
import { Chip, InjectedTag } from './ui.jsx'

const COLUMNS = [
  ['name', 'Aircraft', 'name-col'],
  ['alt', 'Altitude', 'num'],
  ['gs', 'Speed', 'num'],
  ['track', 'Track', 'num'],
  ['source', 'Source', 'src-col'],
]

/**
 * The accessible list of the same aircraft as the map: sortable columns, flagged aircraft first,
 * a search box and a "flagged only" filter. Each callsign is a button that selects the aircraft.
 */
export default function AircraftTable({ aircraft, selected, onSelect }) {
  const [sort, setSort] = useState({ key: 'name', dir: 'asc' })
  const [query, setQuery] = useState('')
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const rows = useMemo(
    () => sortAircraft(filterAircraft(aircraft, { query, flaggedOnly }), sort.key, sort.dir),
    [aircraft, query, flaggedOnly, sort],
  )
  const flagged = aircraft.filter((a) => a.flagged).length

  function toggle(key) {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: key === 'name' || key === 'source' ? 'asc' : 'desc' }))
  }

  return (
    <section className="ac-list card flush" aria-labelledby="ac-list-title">
      <div className="ac-list-head">
        <h2 id="ac-list-title">Aircraft list</h2>
        <p className="muted small">{plural(aircraft.length, 'aircraft', 'aircraft')} · {plural(flagged, 'flagged', 'flagged')} · flagged listed first</p>
        <div className="ac-list-filters">
          <label className="field compact grow">
            <span className="sr-only">Search callsign or hex</span>
            <input type="search" placeholder="Search callsign or hex" value={query} maxLength={16}
              onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label className="check">
            <input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} />
            Flagged only
          </label>
        </div>
      </div>
      <div className="ac-scroll">
        <table className="ac-table">
          <caption className="sr-only">
            Aircraft in the current picture, sorted by {AIRCRAFT_SORTS[sort.key].label.toLowerCase()} ({sort.dir === 'asc' ? 'ascending' : 'descending'}), flagged first
          </caption>
          <thead>
            <tr>
              {COLUMNS.map(([key, label, cls]) => (
                <th key={key} scope="col" className={cls}
                  aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className="sort-btn" onClick={() => toggle(key)}>
                    {label}
                    <span className="sort-ind" aria-hidden="true">{sort.key === key ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const src = sourceOf(a.source)
              const isSel = a.icao === selected
              return (
                <tr key={a.icao} className={`${a.flagged ? 'is-flagged' : ''}${isSel ? ' is-selected' : ''}`}>
                  <th scope="row" className="name-col">
                    <button type="button" className="ac-select" aria-pressed={isSel} onClick={() => onSelect(isSel ? null : a.icao)}>
                      <span className="ac-name">{aircraftName(a)}</span>
                      <span className="ac-hex mono">{a.icao.toUpperCase()}</span>
                    </button>
                    {(a.flagged || a.injected) && (
                      <span className="ac-tags">
                        {a.flagged && <Chip tone="flagged" glyph="◎">Flagged</Chip>}
                        {a.injected && <InjectedTag short />}
                      </span>
                    )}
                  </th>
                  <td className="num" data-label="Alt">{fmtAlt(a.alt, a.on_ground)}</td>
                  <td className="num" data-label="GS">{Number.isFinite(a.gs) ? `${fmtNumber(a.gs)} kt` : '—'}</td>
                  <td className="num" data-label="Trk">{fmtTrack(a.track)}</td>
                  <td className="src-col" data-label="Src">
                    <span className={`src-dot src-${src}`} aria-hidden="true" />{SOURCES[src].label}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!rows.length && <p className="state small">No aircraft match.</p>}
      </div>
    </section>
  )
}
