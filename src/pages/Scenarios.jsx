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
      ? <Chip tone="warn">Not detected: known blind spot</Chip>
      : <Chip tone="sev-high">Now detected: promote to a scenario</Chip>
  }
  return result.passed && !result.collateral
    ? <Chip tone="good">Detected{Number.isFinite(result.time_to_detect_s) ? ` in ${fmtTimeToDetect(result.time_to_detect_s)}` : ''}</Chip>
    : <Chip tone="sev-high">Validation failed</Chip>
}

function Row({ s, result }) {
  const gap = Boolean(s.gap)
  return (
    <tr>
      <th scope="row" className="scenario-main">
        <Link to={`/scenarios/${encodeURIComponent(s.id)}`} id={`sc-${s.id}`} className="alert-link">{s.title}</Link>
        <span className="block muted small">{s.simulates}</span>
        <span className="block muted small">Source: {s.reference}</span>
      </th>
      <td data-label="Data">
        <span className="tag-stack">
          <InjectedTag short label={s.label} />
          {gap && <Chip tone="warn">Documented gap</Chip>}
        </span>
      </td>
      <td data-label="Expected">
        {s.expected.length
          ? s.expected.map((id) => <Link key={id} to={`/detections#${id}`} className="mono id-link">{id}</Link>)
          : <span className="muted">none (nothing should fire)</span>}
      </td>
      <td data-label="Outcome"><Outcome result={result} gap={gap} /></td>
      <td className="num" data-label="In the demo">
        {Number.isFinite(s.demo_offset_s) ? `${fmtDuration(s.demo_offset_s)} into every hour` : '—'}
      </td>
      <td className="action-cell">
        <Link className="btn secondary small" to={`/scenarios/${encodeURIComponent(s.id)}`} aria-describedby={`sc-${s.id}`}>
          <Icon name="play" size={12} /> Open player<span className="sr-only">: {s.title}</span>
        </Link>
      </td>
    </tr>
  )
}

export default function Scenarios() {
  usePageTitle('Scenarios')
  const { data, loading, error, reload } = useAsync(loadAll, [])
  return (
    <div className="page">
      <PageHead title="Scenarios"
        lead="Documented attacks injected into recorded traffic to show the detections working. Open one to replay it on the map." />
      <p className="injected-banner">
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
          {data.scenarios.length > 0 && (
            <div className="panel table-card">
              <table className="data-table stack-table scenario-table">
                <caption className="sr-only">Injected attack scenarios</caption>
                <thead>
                  <tr>
                    <th scope="col">Scenario</th>
                    <th scope="col">Data</th>
                    <th scope="col">Expected detection</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">In the live demo</th>
                    <th scope="col"><span className="sr-only">Open</span></th>
                  </tr>
                </thead>
                <tbody>
                  {data.scenarios.map((s) => <Row key={s.id} s={s} result={data.results?.[s.id]} />)}
                </tbody>
              </table>
            </div>
          )}
          {data.scenarios.length === 0 && <div className="state">No scenarios are defined.</div>}
        </>
      )}
    </div>
  )
}
