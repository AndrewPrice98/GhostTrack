import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { DemoNotice, FormMessage, Icon, PageHead, usePageTitle, useSession } from '../components/ui.jsx'
import { fileTooLarge, fmtNumber, plural } from '../lib.js'

const EXAMPLE = '{"ts": 1794316000.0, "aircraft": [{"icao": "a061d9", "callsign": "SWA123", "lat": 33.94, "lon": -118.41, "alt_baro_ft": 12000.0, "on_ground": false, "gs_kt": 310.0, "track_deg": 90.0, "category": "A3", "source": "adsb", "seen_pos_s": 0.1}]}'

const REASONS = {
  military: 'military aircraft (excluded)',
  invalid_hex: 'invalid ICAO address',
  invalid_position: 'missing or out-of-range position',
  duplicate: 'duplicate address in one snapshot',
}

function explain(err, maxMb) {
  const platform = Number.isFinite(maxMb) ? ` If the file is larger than ${fmtNumber(maxMb, 1)} MB, the hosting platform may have refused it before the app saw it.` : ''
  switch (err.status) {
    case 403: return 'Only the admin can upload recordings. Sign in as admin first.'
    case 413: return `${err.message}. Cut the recording into shorter parts, or gzip it, and try again.${platform}`
    case 415: return `${err.message}. Upload a text recording (.jsonl) or a gzip of one (.jsonl.gz).`
    case 429: return 'Too many requests. Wait a moment and try again.'
    case 503: return `${err.message}. The service is not ready; try again shortly.`
    case 0: return `${err.message}${platform} Try a smaller or gzipped file.`
    default: return `Not uploaded: ${err.message}`
  }
}

const MB = 1024 * 1024
const tooLarge = (file, maxMb) => fileTooLarge(file?.size, maxMb)

function sizeMessage(file, maxMb) {
  return `This file is ${fmtNumber(file.size / MB, 1)} MB; the limit is ${fmtNumber(maxMb, 1)} MB. Cut the recording into shorter parts, or gzip it, and try again.`
}

function Result({ r }) {
  const dropped = Object.entries(r.dropped_by_reason || {})
  return (
    <section className="card upload-result" aria-labelledby="res-title">
      <h2 id="res-title">Upload processed</h2>
      <dl className="kv">
        <div><dt>Snapshots kept</dt><dd>{fmtNumber(r.snapshots_kept)}</dd></div>
        <div><dt>Lines rejected</dt><dd>{fmtNumber(r.lines_rejected)} of {fmtNumber(r.lines)}</dd></div>
        <div><dt>Aircraft records kept</dt><dd>{fmtNumber(r.aircraft_kept)} of {fmtNumber(r.aircraft_in)}</dd></div>
        <div><dt>Alerts created</dt><dd>{fmtNumber(r.alerts_inserted)} new, {fmtNumber(r.alerts_updated)} updated</dd></div>
        {r.injected_alerts > 0 && <div><dt>Declared as injected</dt><dd>{fmtNumber(r.injected_alerts)} (as declared by the upload)</dd></div>}
      </dl>
      {dropped.length > 0 && (
        <p className="small muted">
          Dropped records: {dropped.map(([k, n]) => `${fmtNumber(n)} ${REASONS[k] || k.replaceAll('_', ' ')}`).join(', ')}.
        </p>
      )}
      {r.alert_ids?.length > 0 ? (
        <>
          <p className="small">
            Alerts from this upload are labelled <strong>Uploaded recording (unverified)</strong>: the file is untrusted, and a
            declared “injected” flag is the uploader’s claim.
          </p>
          <ul className="id-links">
            {r.alert_ids.slice(0, 12).map((id) => (
              <li key={id}><Link to={`/alerts/${encodeURIComponent(id)}`}>Alert #{id}</Link></li>
            ))}
          </ul>
          {r.alert_ids.length > 12 && <p className="small muted">and {plural(r.alert_ids.length - 12, 'more alert')}.</p>}
        </>
      ) : <p className="state small">No detection fired on this recording.</p>}
      <p><Link className="btn secondary small" to="/alerts">Open the alert queue <Icon name="arrow" size={14} /></Link></p>
    </section>
  )
}

export default function Upload() {
  usePageTitle('Upload a recording')
  const { guest, signIn, maxUploadMb } = useSession()
  const input = useRef(null)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [result, setResult] = useState(null)

  if (guest) {
    return (
      <div className="page">
        <PageHead eyebrow="Admin" title="Upload a recording" />
        <section className="card" aria-labelledby="admin-only">
          <h2 id="admin-only">Admin only</h2>
          <p className="muted">
            The demo is read-only: only the admin can upload recordings.
            {signIn && <> <button type="button" className="link" onClick={signIn}>Sign in as admin</button>.</>}
          </p>
        </section>
      </div>
    )
  }

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    if (!file) {
      setMessage({ tone: 'error', text: 'Choose a recording file first.' })
      input.current?.focus()
      return
    }
    if (tooLarge(file, maxUploadMb)) {
      setMessage({ tone: 'error', text: sizeMessage(file, maxUploadMb) })
      input.current?.focus()
      return
    }
    setBusy(true)
    setMessage(null)
    setResult(null)
    try {
      const r = await api.upload(file)
      setResult(r)
      setMessage({ tone: 'ok', text: 'Uploaded.' })
      setFile(null)
      if (input.current) input.current.value = ''
    } catch (err) {
      setMessage({ tone: 'error', text: explain(err, maxUploadMb) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page upload-page">
      <PageHead eyebrow="Admin" title="Upload a recording"
        lead="Run the detections over a recording of your own. The file is untrusted: every record is validated like the live feed, and the alerts are labelled as unverified." />
      <div className="upload-grid">
        <form className="card upload-form" onSubmit={submit} aria-labelledby="up-title">
          <h2 id="up-title">Recording file</h2>
          <label className="field">
            <span>JSON Lines recording (.jsonl or .jsonl.gz)</span>
            <input ref={input} type="file" accept=".jsonl,.gz,application/gzip"
              onChange={(e) => {
                const f = e.target.files?.[0] || null
                setFile(f)
                setMessage(f && tooLarge(f, maxUploadMb) ? { tone: 'error', text: sizeMessage(f, maxUploadMb) } : null)
              }} />
          </label>
          {file && <p className="small muted">{file.name} · {fmtNumber(file.size / 1024, 1)} KB</p>}
          <div className="form-actions">
            <button className="btn primary" aria-disabled={busy ? 'true' : undefined}>
              <Icon name="upload" size={16} /> {busy ? 'Uploading…' : 'Upload and analyse'}
            </button>
            <FormMessage message={message} />
          </div>
          <DemoNotice />
          <p className="small muted">
            {Number.isFinite(maxUploadMb)
              ? <>The upload limit on this server is {fmtNumber(maxUploadMb, 1)} MB. A larger file is refused before it is sent.</>
              : <>There is an upload size limit. A larger file is refused with a message that names the limit.</>}
            {' '}Nothing is stored when a file is refused.
          </p>
        </form>

        <section className="card" aria-labelledby="fmt-title">
          <h2 id="fmt-title">Format</h2>
          <p className="small">
            One snapshot per line: a timestamp in epoch seconds and the aircraft seen at that moment. Lines that are not valid
            are counted and skipped; a file with no valid snapshot is refused. Military aircraft are excluded.
          </p>
          <pre className="code-sample" tabIndex={0} aria-label="Example snapshot line">{EXAMPLE}</pre>
          <p className="small muted">
            GhostTrack’s own recordings use this format, so a file recorded with <code>scripts/record.py</code> uploads as it is.
          </p>
        </section>
      </div>
      {result && <Result r={result} />}
    </div>
  )
}
