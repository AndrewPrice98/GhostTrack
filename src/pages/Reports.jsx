import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import Markdown from '../components/Markdown.jsx'
import { ErrorState, Icon, Loading, PageHead, useAsync, usePageTitle, useSession } from '../components/ui.jsx'
import { ATTRIBUTION } from '../lib.js'

const FILES = [
  { ext: 'html', label: 'Printable HTML', file: 'ghosttrack-summary.html', note: 'Opens in any browser; print it to PDF.' },
  { ext: 'md', label: 'Markdown', file: 'ghosttrack-summary.md', note: 'Plain text with tables, for notes and pull requests.' },
]

export default function Reports() {
  usePageTitle('Reports')
  const { guest } = useSession()
  const { data: text, loading, error, reload } = useAsync(() => api.summaryText(), [])
  const [raw, setRaw] = useState(false)
  return (
    <div className="page">
      <PageHead title="Reports"
        lead="Alerts by detection over the last 24 hours, the scenario validation and the false-positive measurements." />

      <div className="reports-grid">
        <section className="group" aria-labelledby="dl-title">
          <h2 id="dl-title">Summary report</h2>
          <p className="muted small">
            Generated when you download it, from the alerts and validation results the dashboard shows. Alerts from injected
            scenarios are counted and labelled apart from real traffic.
          </p>
          <ul className="file-list">
            {FILES.map((f) => (
              <li key={f.ext}>
                <a className="btn secondary small" href={api.reportUrl(f.ext)} download={f.file}>
                  <Icon name="download" size={14} /> {f.label}<span className="sr-only"> summary report ({f.ext})</span>
                </a>
                <span className="small muted">{f.note}</span>
              </li>
            ))}
          </ul>
          <p className="small muted attribution">{ATTRIBUTION}</p>
        </section>

        <section className="group" aria-labelledby="ev-title">
          <h2 id="ev-title">Evidence packs</h2>
          <p className="muted small">
            Each alert has its own evidence pack (JSON and printable HTML) with the track points, failing checks, units and
            the ODbL notice. Open an alert to download it.
          </p>
          <p><Link className="btn secondary small" to="/alerts">Open the alert queue <Icon name="arrow" size={14} /></Link></p>
          {!guest && <p className="small muted">Have a capture of your own? <Link to="/admin/upload">Upload a recording</Link> to get alerts and packs for it.</p>}
        </section>
      </div>

      <section className="group report-preview" aria-labelledby="pv-title">
        <div className="preview-head">
          <h2 id="pv-title">Preview</h2>
          <label className="check">
            <input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} /> Show the Markdown source
          </label>
        </div>
        <p className="small muted">This is the Markdown download, rendered. The HTML download has the same content, styled for printing.</p>
        {loading && !text && <Loading label="Building the preview…" />}
        {error && <ErrorState error={error} onRetry={reload} />}
        {text && (
          <div className="preview-body" tabIndex={0} role="region" aria-label="Summary report preview">
            {raw ? <pre className="md-raw">{text}</pre> : <Markdown source={text} />}
          </div>
        )}
      </section>
    </div>
  )
}
