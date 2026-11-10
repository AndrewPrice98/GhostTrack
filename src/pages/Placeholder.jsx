import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { Icon, PageHead, usePageTitle } from '../components/ui.jsx'

const PAGES = {
  aircraft: {
    title: 'Aircraft',
    eyebrow: 'Track history',
    lead: 'Per-aircraft track history with altitude and speed profiles, its findings and registration checks.',
  },
  detections: {
    title: 'Detections',
    eyebrow: 'Catalog and validation',
    lead: 'The ten plausibility checks with their rationale, thresholds, false-positive notes and benign causes, plus the scenario validation and the false-positive budget.',
  },
  scenarios: {
    title: 'Scenarios',
    eyebrow: 'Injected attacks',
    lead: 'Documented attack scenarios injected into recorded traffic, each replayed step by step with the detection firing.',
  },
  reports: {
    title: 'Reports',
    eyebrow: 'Exports',
    lead: 'A summary of alerts by detection over time, the validation results and the false-positive measurements.',
  },
}

/** Sections whose full page arrives with the next part of the dashboard; never a dead end. */
export default function Placeholder({ page }) {
  const meta = PAGES[page]
  const { icao } = useParams()
  usePageTitle(meta.title)
  return (
    <div className="page">
      <PageHead eyebrow={meta.eyebrow} title={icao ? `Aircraft ${icao.toUpperCase()}` : meta.title} lead={meta.lead} />
      <section className="card placeholder" aria-labelledby="ph-title">
        <h2 id="ph-title">This view is being built</h2>
        <p className="muted">Until it lands, the same data is available here:</p>
        <ul className="ph-links">
          {page === 'aircraft' && icao && (
            <li><Link to={`/?select=${encodeURIComponent(icao)}`}><Icon name="map" size={15} /> Show {icao.toUpperCase()} on the live map</Link></li>
          )}
          {page === 'aircraft' && icao && (
            <li><Link to={`/alerts?q=${encodeURIComponent(icao)}`}><Icon name="alert" size={15} /> Alerts for {icao.toUpperCase()}</Link></li>
          )}
          {page === 'reports' && (
            <>
              <li><a href={api.reportUrl('html')} download><Icon name="download" size={15} /> Summary report (HTML)</a></li>
              <li><a href={api.reportUrl('md')} download><Icon name="download" size={15} /> Summary report (Markdown)</a></li>
            </>
          )}
          <li><Link to="/alerts"><Icon name="alert" size={15} /> Alert queue, with the failing checks explained</Link></li>
          <li><Link to="/"><Icon name="map" size={15} /> Live map</Link></li>
        </ul>
      </section>
    </div>
  )
}
