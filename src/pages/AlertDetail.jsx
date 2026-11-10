import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import TrackMini from '../components/TrackMini.jsx'
import {
  DemoNotice, ErrorState, FormMessage, Icon, Loading, OriginTag, SeverityChip, StatusChip, useAsync, usePageTitle, useSession,
} from '../components/ui.jsx'
import {
  ATTRIBUTION, INJECTED_NOTICE, STATUS, TRIAGE_OPTIONS, alertSubject, evidenceRows, fmtDateTime, fmtTime,
} from '../lib.js'

const SOURCE_LABELS = { recorded: 'Recorded capture (demo replay)', live: 'Live feed (adsb.lol)', upload: 'Uploaded recording' }

function Triage({ alert, onSaved }) {
  const { guest, signIn } = useSession()
  const [status, setStatus] = useState(alert.status === 'info' ? 'new' : alert.status)
  const [notes, setNotes] = useState(alert.notes || '')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const original = alert.status === 'info' ? 'new' : alert.status
  const dirty = status !== original || notes !== (alert.notes || '')

  useEffect(() => {
    setStatus(alert.status === 'info' ? 'new' : alert.status)
    setNotes(alert.notes || '')
  }, [alert.id, alert.status, alert.notes])

  if (guest) {
    return (
      <section className="card triage" aria-labelledby="triage-title">
        <h2 id="triage-title">Triage</h2>
        <dl className="kv">
          <div><dt>Status</dt><dd><StatusChip status={alert.status} /></dd></div>
          <div><dt>Notes</dt><dd>{alert.notes ? <span className="notes">{alert.notes}</span> : <span className="muted">None</span>}</dd></div>
        </dl>
        <p className="readonly-hint small">
          <Icon name="lock" size={14} /> The demo is read-only.{' '}
          {signIn && <button type="button" className="link" onClick={signIn}>Sign in as admin to triage</button>}
        </p>
      </section>
    )
  }

  async function save(e) {
    e.preventDefault()
    if (busy || !dirty) return
    setBusy(true)
    setMessage(null)
    try {
      const body = {}
      if (status !== original) body.status = status
      if (notes !== (alert.notes || '')) body.notes = notes
      const updated = await api.triage(alert.id, body)
      onSaved(updated)
      setMessage({ tone: 'ok', text: 'Saved.' })
    } catch (err) {
      setMessage({ tone: 'error', text: err.status === 403 ? 'Only the admin can triage alerts.' : `Not saved: ${err.message}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card triage" aria-labelledby="triage-title" onSubmit={save}>
      <h2 id="triage-title">Triage</h2>
      <fieldset className="radio-list">
        <legend>Status</legend>
        {TRIAGE_OPTIONS.map(([value, label, help]) => (
          <label key={value} className={status === value ? 'on' : ''}>
            <input type="radio" name="triage-status" value={value} checked={status === value}
              onChange={() => setStatus(value)} />
            <span><strong>{label}</strong> <span className="muted small">{help}</span></span>
          </label>
        ))}
      </fieldset>
      <label className="field">
        <span>Notes</span>
        <textarea rows={4} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)}
          placeholder="What you checked and what you concluded" />
      </label>
      <DemoNotice />
      <div className="form-actions">
        <button className="btn primary small" aria-disabled={!dirty || busy ? 'true' : undefined}>
          {busy ? 'Saving…' : 'Save triage'}
        </button>
        <FormMessage message={message} />
      </div>
    </form>
  )
}

export default function AlertDetail() {
  const { alertId } = useParams()
  const { data: alert, loading, error, reload, setData } = useAsync(() => api.alert(alertId), [alertId])
  usePageTitle(alert ? `Alert #${alert.id}` : 'Alert')

  if (loading && !alert) return <div className="page"><Loading label="Loading the alert…" /></div>
  if (error) {
    return (
      <div className="page">
        <p className="back"><Link to="/alerts"><Icon name="back" size={15} /> All alerts</Link></p>
        {error.status === 404
          ? <div className="state"><h1 tabIndex={-1} className="h-inline">Alert not found.</h1> It may have aged out of the 24-hour window.</div>
          : <><h1 tabIndex={-1} className="sr-only">Alert</h1><ErrorState error={error} onRetry={reload} /></>}
      </div>
    )
  }

  const rows = evidenceRows(alert.evidence)
  const addresses = (alert.evidence || []).find((e) => e.field === 'addresses')?.observed
  const tableRows = rows.filter((r) => r.field !== 'addresses')
  return (
    <div className="page alert-page">
      <p className="back"><Link to="/alerts"><Icon name="back" size={15} /> All alerts</Link></p>
      <header className="page-head detail-head">
        <div className="page-head-text">
          <p className="eyebrow">Alert #{alert.id} · {alert.detection_id}</p>
          <h1 tabIndex={-1}>{alert.title}</h1>
          <div className="chips">
            <SeverityChip severity={alert.severity} />
            <StatusChip status={alert.status} />
            <OriginTag item={alert} />
          </div>
        </div>
      </header>
      {alert.source === 'upload' ? (
        <p className="injected-banner upload-banner">
          {alert.label}: this alert comes from a recording the admin uploaded. The file is not verified
          {alert.injected ? ', and its injected-test-data flag is the uploader’s own claim' : ''}.
        </p>
      ) : alert.injected && <p className="injected-banner">{INJECTED_NOTICE}</p>}

      <div className="detail-grid">
        <div className="detail-main">
          <section className="card" aria-labelledby="failed-title">
            <h2 id="failed-title">What failed</h2>
            <p className="explanation">{alert.explanation}</p>
            {tableRows.length > 0 && (
              <div className="table-scroll">
                <table className="evidence-table">
                  <caption className="sr-only">Observed values against thresholds</caption>
                  <thead>
                    <tr>
                      <th scope="col">Check</th>
                      <th scope="col" className="num">Observed</th>
                      <th scope="col" className="num">Threshold</th>
                      <th scope="col">Comparison</th>
                      <th scope="col">Report (UTC)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tableRows.map((r) => (
                      <tr key={r.field}>
                        <th scope="row">
                          {r.label}
                          {r.help && <span className="help">{r.help}</span>}
                        </th>
                        <td className="num observed" data-label="Observed">{r.observed}</td>
                        <td className="num" data-label="Threshold">{r.threshold}</td>
                        <td data-label="Comparison">{r.comparison || '—'}</td>
                        <td data-label="Report">{fmtTime(r.ts)}{r.count > 1 && <span className="muted small"> ({r.count} reports)</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {Array.isArray(addresses) && (
              <details className="addresses">
                <summary>{addresses.length} addresses involved</summary>
                <p className="mono small">{addresses.map((a) => a.toUpperCase()).join(' ')}</p>
              </details>
            )}
            <p className="small muted">
              Thresholds come from the detection catalog. A check whose inputs are missing is not evaluated, so it can
              never raise an alert.
            </p>
          </section>

          <section className="card" aria-labelledby="benign-title">
            <h2 id="benign-title">Possible benign causes</h2>
            {alert.benign_causes?.length ? (
              <ul className="benign">{alert.benign_causes.map((c) => <li key={c}>{c}</li>)}</ul>
            ) : <p className="muted">None documented for this detection.</p>}
            <p className="small muted">Rule these out before treating the alert as spoofing.</p>
          </section>

          <section className="card" aria-labelledby="track-title">
            <h2 id="track-title">Track</h2>
            <TrackMini alert={alert} />
          </section>
        </div>

        <div className="detail-side">
          <section className="card" aria-labelledby="facts-title">
            <h2 id="facts-title">Details</h2>
            <dl className="kv">
              <div><dt>Aircraft</dt><dd>{alertSubject(alert)}{alert.icao && alert.callsign && <span className="mono muted"> {alert.icao.toUpperCase()}</span>}</dd></div>
              <div><dt>First seen</dt><dd>{fmtDateTime(alert.first_ts, { seconds: true })}</dd></div>
              <div><dt>Last seen</dt><dd>{fmtDateTime(alert.last_ts, { seconds: true })}</dd></div>
              <div><dt>Confidence</dt><dd>{Number.isFinite(alert.confidence) ? alert.confidence.toFixed(2) : '—'}</dd></div>
              <div><dt>Status</dt><dd>{STATUS[alert.status]?.label || alert.status}</dd></div>
              <div><dt>Data source</dt><dd>{SOURCE_LABELS[alert.source] || alert.source}</dd></div>
            </dl>
            {alert.icao && (
              <p className="small links">
                <Link to={`/?select=${encodeURIComponent(alert.icao)}`}><Icon name="map" size={14} /> Find on the live map</Link>
                <Link to={`/aircraft/${encodeURIComponent(alert.icao)}`}><Icon name="plane" size={14} /> Aircraft history</Link>
              </p>
            )}
          </section>

          <section className="card" aria-labelledby="pack-title">
            <h2 id="pack-title">Evidence pack</h2>
            <p className="small muted">Track points, failing checks with units, data source, timestamps and the ODbL attribution.</p>
            <div className="downloads">
              <a className="btn secondary small" href={api.evidenceUrl(alert.id, 'json')} download>
                <Icon name="download" size={15} /> JSON<span className="sr-only"> evidence pack for alert {alert.id}</span>
              </a>
              <a className="btn secondary small" href={api.evidenceUrl(alert.id, 'html')} download>
                <Icon name="download" size={15} /> Printable HTML<span className="sr-only"> evidence pack for alert {alert.id}</span>
              </a>
            </div>
            <p className="small muted attribution">{ATTRIBUTION}</p>
          </section>

          <Triage alert={alert} onSaved={setData} />
        </div>
      </div>
    </div>
  )
}
