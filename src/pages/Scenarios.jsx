import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { Chip, ErrorState, Icon, InjectedTag, Loading, PageHead, useAsync, usePageTitle } from '../components/ui.jsx'
import { fmtDuration, fmtTimeToDetect } from '../lib.js'

async function loadAll() {
  const [scenarios, validation] = await Promise.allSettled([api.scenarios(), api.validation()])
  if (scenarios.status === 'rejected') throw scenarios.reason
  return {
    scenarios: scenarios.value.scenarios || [],
    results: validation.status === 'fulfilled'
      ? Object.fromEntries([...validation.value.scenarios, ...validation.value.gaps].map((r) => [r.scenario, r]))
      : null,
    resultsError: validation.status === 'rejected' ? validation.reason : null,
  }
}

function Outcome({ result, gap }) {
  if (!result) return null
  if (gap) {
    return result.passed
      ? <Chip tone="warn" glyph="!">Not detected: known blind spot</Chip>
      : <Chip tone="sev-high" glyph="✕">Now detected: promote to a scenario</Chip>
  }
  return result.passed && !result.collateral
    ? <Chip tone="good" glyph="✓">Detected{Number.isFinite(result.time_to_detect_s) ? ` in ${fmtTimeToDetect(result.time_to_detect_s)}` : ''}</Chip>
    : <Chip tone="sev-high" glyph="✕">Validation failed</Chip>
}

function Card({ s, result }) {
  const gap = Boolean(s.gap)
  return (
    <li className="card scenario-card">
      <div className="chips">
        <InjectedTag short label={s.label} />
        {gap && <Chip tone="warn" glyph="!">Documented gap</Chip>}
        <Outcome result={result} gap={gap} />
      </div>
      <h2 id={`sc-${s.id}`}><Link to={`/scenarios/${encodeURIComponent(s.id)}`}>{s.title}</Link></h2>
      <p>{s.simulates}</p>
      <dl className="kv">
        <div>
          <dt>Expected detection</dt>
          <dd>
            {s.expected.length
              ? s.expected.map((id) => <Link key={id} to={`/detections#${id}`} className="mono id-pill">{id}</Link>)
              : <span className="muted">none (nothing should fire)</span>}
          </dd>
        </div>
        {Number.isFinite(s.demo_offset_s) && (
          <div><dt>In the live demo</dt><dd>{fmtDuration(s.demo_offset_s)} into every hour</dd></div>
        )}
      </dl>
      <p className="small muted ref">Source: {s.reference}</p>
      <Link className="btn primary small play-link" to={`/scenarios/${encodeURIComponent(s.id)}`} aria-describedby={`sc-${s.id}`}>
        <Icon name="play" size={14} /> Open player<span className="sr-only">: {s.title}</span>
      </Link>
    </li>
  )
}

export default function Scenarios() {
  usePageTitle('Scenarios')
  const { data, loading, error, reload } = useAsync(loadAll, [])
  return (
    <div className="page">
      <PageHead eyebrow="Injected attacks" title="Scenarios"
        lead="Documented attacks injected into recorded traffic to show the detections working. Open a scenario to replay it on the map and watch each detection fire." />
      <p className="injected-banner small">
        Everything here is injected test data: not real air traffic. It comes from documented attack scenarios replayed into recorded traffic.
      </p>
      {loading && !data && <Loading label="Loading scenarios…" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <>
          {data.resultsError && (
            <p className="state small" role="status">
              Validation results are not available right now ({data.resultsError.message}), so the outcomes are left out.{' '}
              <button type="button" className="link" onClick={reload}>Try again</button>
            </p>
          )}
          <ul className="scenario-grid">
            {data.scenarios.map((s) => <Card key={s.id} s={s} result={data.results?.[s.id]} />)}
          </ul>
          {data.scenarios.length === 0 && <div className="state">No scenarios are defined.</div>}
        </>
      )}
    </div>
  )
}
