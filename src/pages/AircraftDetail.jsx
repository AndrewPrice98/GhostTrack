import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, api } from '../api.js'
import LineChart from '../components/LineChart.jsx'
import TrackMini from '../components/TrackMini.jsx'
import {
  Empty, ErrorState, FlaggedTag, Icon, InjectedTag, Loading, OriginTag, PageHead, SeverityChip, StatusChip, useAsync,
  usePageTitle,
} from '../components/ui.jsx'
import { ATTRIBUTION, INJECTED_NOTICE, categoryLabel, fmtDateTime, fmtNumber, fmtTime, plural } from '../lib.js'

const FINDINGS_SHOWN = 10
const HEX6 = /^[0-9a-fA-F]{6}$/

function Back() {
  return <p className="back"><Link to="/aircraft"><Icon name="back" size={15} /> All aircraft</Link></p>
}

function Fact({ label, children }) {
  return <div><dt>{label}</dt><dd>{children ?? '—'}</dd></div>
}

export default function AircraftDetail() {
  const { icao = '' } = useParams()
  const valid = HEX6.test(icao)
  const hex = icao.toLowerCase()
  const { data, loading, error, reload } = useAsync(
    () => (valid ? api.aircraft(hex) : Promise.reject(new ApiError('aircraft not found', 404))), [hex, valid],
  )
  usePageTitle(valid ? `Aircraft ${hex.toUpperCase()}` : 'Aircraft')

  const [allFindings, setAllFindings] = useState(false)
  const track = data?.track
  const bands = useMemo(() => (data?.findings || [])
    .filter((f) => Number.isFinite(f.first_ts))
    .map((f) => ({ from: f.first_ts, to: Number.isFinite(f.last_ts) ? f.last_ts : f.first_ts, tone: f.severity })), [data])

  if (loading && !data) return <div className="page"><Back /><Loading label="Loading the track history…" /></div>
  if (error) {
    return (
      <div className="page">
        <Back />
        {error.status === 404 ? (
          <div className="state">
            <h1 tabIndex={-1} className="h-inline">Aircraft not found.</h1>
            {' '}No track history or findings are stored for {valid ? hex.toUpperCase() : 'that address'}. History is a rolling
            window, so aircraft that left the picture age out. <Link to="/aircraft">Browse aircraft in the current picture</Link>.
          </div>
        ) : <><h1 tabIndex={-1} className="sr-only">Aircraft {hex.toUpperCase()}</h1><ErrorState error={error} onRetry={reload} /></>}
      </div>
    )
  }

  const reg = data.registration || {}
  const findings = data.findings || []
  const points = track || []
  const first = points[0]
  const last = points.at(-1)
  const flagged = findings.some((f) => f.status !== 'dismissed' && (f.severity === 'medium' || f.severity === 'high'))
  const name = (reg.callsign || '').trim() || hex.toUpperCase()
  const mini = { track: points, evidence: [], icao: hex, callsign: reg.callsign, injected: data.injected }
  return (
    <div className="page aircraft-page">
      <header className="page-head detail-head">
        <div className="page-head-text">
          <h1 tabIndex={-1}>{name}<span className="head-meta mono">{hex.toUpperCase()}</span></h1>
          <div className="chips">
            {findings.length > 0 && flagged && <FlaggedTag />}
            <OriginTag item={data} />
            {findings.length === 0 && <span className="chip neutral">No findings</span>}
          </div>
        </div>
        <div className="page-head-actions">
          <Link className="btn secondary small" to={`/?select=${encodeURIComponent(hex)}`}><Icon name="map" size={14} /> Show on the live map</Link>
        </div>
      </header>
      {data.injected && <p className="injected-banner">{INJECTED_NOTICE}</p>}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="group" aria-labelledby="profile-title">
            <h2 id="profile-title">Altitude and speed profile</h2>
            <p className="small muted">
              {points.length
                ? <>{plural(points.length, 'report')} from {fmtTime(first.ts)} to {fmtTime(last.ts)}. Altitude is barometric; speed is the reported ground speed.</>
                : 'No track points are stored for this aircraft.'}
            </p>
            {points.length > 0 ? (
              <>
                <LineChart title="Altitude" unit="ft" field="alt" track={points} bands={bands} />
                <LineChart title="Ground speed" unit="kt" field="gs" track={points} bands={bands} zero />
              </>
            ) : (
              <Empty>Nothing to chart: history is a rolling window, so an aircraft that has left the picture has no points. Its findings are below.</Empty>
            )}
            <p className="small muted attribution">{ATTRIBUTION}</p>
          </section>

          <section className="group" aria-labelledby="track-title">
            <h2 id="track-title">Track history</h2>
            <TrackMini alert={mini} empty="No track points are stored for this aircraft (history keeps a rolling window)." />
          </section>

          <section className="group" aria-labelledby="findings-title">
            <h2 id="findings-title">Findings</h2>
            {findings.length === 0 ? (
              <Empty>No detection has fired for this aircraft. Checks whose inputs are missing are not evaluated, so a clean history is not proof the data is genuine.</Empty>
            ) : (
              <ul className="finding-list">
                {(allFindings ? findings : findings.slice(0, FINDINGS_SHOWN)).map((f) => (
                  <li key={f.id}>
                    <SeverityChip severity={f.severity} />
                    <Link to={`/alerts/${encodeURIComponent(f.id)}`} className="alert-link">{f.title}</Link>
                    <span className="muted small mono"> {f.detection_id} · #{f.id}</span>
                    <span className="finding-meta small muted">
                      {fmtDateTime(f.first_ts)} · <StatusChip status={f.status} /> <OriginTag item={f} short />
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {findings.length > FINDINGS_SHOWN && (
              <p className="small">
                <button type="button" className="link" onClick={() => setAllFindings((v) => !v)} aria-expanded={allFindings}>
                  {allFindings ? 'Show fewer' : `Show all ${findings.length} findings`}
                </button>
              </p>
            )}
          </section>
        </div>

        <div className="detail-side">
          <section className="group" aria-labelledby="ident-title">
            <h2 id="ident-title">Identity and registration</h2>
            <dl className="kv">
              <Fact label="ICAO address"><span className="mono">{hex.toUpperCase()}</span></Fact>
              <Fact label="Callsign">{reg.callsign || null}</Fact>
              <Fact label="Registration">{reg.registration || null}</Fact>
              <Fact label="Aircraft type">{reg.type_code || null}</Fact>
              <Fact label="Category">{categoryLabel(reg.category)}</Fact>
              <Fact label="Address block State">{reg.address_state || 'Unallocated or unknown'}</Fact>
              <Fact label="Registration mark State">{reg.registration_states?.length ? reg.registration_states.join(', ') : null}</Fact>
              <Fact label="US N-number for this address">{reg.n_number ? <span className="mono">{reg.n_number}</span> : 'None for this address'}</Fact>
            </dl>
            <p className="small muted">
              The address block comes from ICAO Annex 10 (Table 9-1). GT-006 compares it with the registration mark and the
              N-number the address converts to. <Link to="/detections#GT-006">How GT-006 decides</Link>.
            </p>
          </section>

          <section className="group" aria-labelledby="hist-title">
            <h2 id="hist-title">History</h2>
            <dl className="kv">
              <Fact label="Reports">{fmtNumber(points.length)}</Fact>
              <Fact label="First report">{first ? fmtDateTime(first.ts, { seconds: true }) : null}</Fact>
              <Fact label="Last report">{last ? fmtDateTime(last.ts, { seconds: true }) : null}</Fact>
              <Fact label="Findings">{fmtNumber(findings.length)}</Fact>
            </dl>
            {data.injected && <p><InjectedTag label={data.label} /></p>}
          </section>
        </div>
      </div>
    </div>
  )
}
