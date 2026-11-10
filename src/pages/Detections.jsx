import { Link } from 'react-router-dom'
import { api } from '../api.js'
import {
  Chip, ErrorState, Icon, InjectedTag, Loading, PageHead, SeverityChip, useAsync, usePageTitle,
} from '../components/ui.jsx'
import { fmtNumber } from '../lib.js'
import { fmtThreshold, humanKey } from '../replay.js'

const SCOPES = { track: 'Per aircraft track', fleet: 'Across all aircraft' }

function List({ items }) {
  return <ul className="plain-list">{items.map((x) => <li key={x}>{x}</li>)}</ul>
}

function DetectionCard({ d }) {
  const entries = Object.entries(d.thresholds || {})
  return (
    <article className="card det-card" id={d.id} aria-labelledby={`${d.id}-title`}>
      <header className="det-head">
        <div>
          <p className="eyebrow mono">{d.id}</p>
          <h3 id={`${d.id}-title`}>{d.title}</h3>
        </div>
        <div className="chips">
          <SeverityChip severity={d.severity} />
          <Chip>{SCOPES[d.scope] || d.scope}</Chip>
        </div>
      </header>
      <p className="det-rationale">{d.rationale}</p>
      <div className="det-cols">
        <section aria-labelledby={`${d.id}-th`}>
          <h4 id={`${d.id}-th`}>Thresholds</h4>
          {entries.length ? (
            <dl className="thresholds">
              {entries.map(([k, v]) => (
                <div key={k}><dt>{humanKey(k)}</dt><dd>{fmtThreshold(v)}</dd></div>
              ))}
            </dl>
          ) : <p className="muted small">No numeric thresholds.</p>}
          <h4>Inputs</h4>
          <p className="chips tight">{(d.inputs || []).map((i) => <code key={i}>{i}</code>)}</p>
        </section>
        <section>
          <h4>False-positive notes</h4>
          <p className="small">{d.false_positive_notes || 'None recorded.'}</p>
          <h4>Possible benign causes</h4>
          {d.benign_causes?.length ? <List items={d.benign_causes} /> : <p className="muted small">None documented.</p>}
        </section>
      </div>
      <div className="det-foot small">
        {d.references?.length > 0 && (
          <details>
            <summary>References ({d.references.length})</summary>
            <List items={d.references} />
          </details>
        )}
        <Link to={`/alerts?detection=${encodeURIComponent(d.id)}`}>Alerts from {d.id} <Icon name="arrow" size={14} /></Link>
      </div>
    </article>
  )
}

function ResultChip({ r }) {
  if (r.gap) {
    return r.passed
      ? <Chip tone="warn" glyph="!">Known blind spot</Chip>
      : <Chip tone="sev-high" glyph="✕">Now detected: promote</Chip>
  }
  return r.passed && !r.collateral
    ? <Chip tone="good" glyph="✓">Pass</Chip>
    : <Chip tone="sev-high" glyph="✕">Fail</Chip>
}

function Ids({ ids, expected = [] }) {
  if (!ids.length) return <span className="muted">none</span>
  return (
    <span className="id-list">
      {ids.map((id) => (
        <Link key={id} to={`/detections#${id}`} className={`mono id-pill${expected.includes(id) ? ' expected' : ''}`}>{id}</Link>
      ))}
    </span>
  )
}

function ValidationTable({ rows, gap }) {
  return (
    <div className="card flush table-card">
      <table className="data-table stack-table validation-table">
        <caption className="sr-only">{gap ? 'Documented gaps' : 'Scenario validation results'}</caption>
        <thead>
          <tr>
            <th scope="col">Scenario</th>
            <th scope="col">Expected</th>
            <th scope="col">Fired</th>
            <th scope="col" className="num">{gap ? 'Time to detect' : 'Time to detect'}</th>
            <th scope="col" className="num">Collateral</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.scenario}>
              <th scope="row">
                <Link to={`/scenarios/${encodeURIComponent(r.scenario)}`} className="mono">{r.scenario}</Link>
                <span className="block"><InjectedTag short label={r.label} /></span>
              </th>
              <td data-label="Expected"><Ids ids={r.expected} /></td>
              <td data-label="Fired"><Ids ids={r.fired} expected={r.expected} /></td>
              <td className="num" data-label="Time to detect">{Number.isFinite(r.time_to_detect_s) ? `${fmtNumber(r.time_to_detect_s)} s` : '—'}</td>
              <td className="num" data-label="Collateral">{fmtNumber(r.collateral)}</td>
              <td data-label="Result"><ResultChip r={r} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Measured false positives per hour against the budget, as horizontal bars (hand-rolled SVG). */
function BudgetBars({ budget, rows }) {
  const w = 560
  const row = 34
  const left = 132
  const right = 16
  const top = 22
  const max = Math.max(budget * 1.25, ...rows.map((r) => r.per_hour), 0.01)
  const x = (v) => left + (v / max) * (w - left - right)
  const h = top + rows.length * row + 18
  const label = rows.map((r) => `${r.recording}: ${r.per_hour.toFixed(2)} per hour`).join('; ')
  return (
    <figure className="budget-chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" width="100%" focusable="false"
        aria-label={`Findings per hour against the budget of ${fmtNumber(budget, 2)} per hour. ${label}.`}>
        <line className="bud-axis" x1={left} x2={left} y1={top - 6} y2={h - 14} />
        {rows.map((r, i) => (
          <g key={r.recording} transform={`translate(0 ${top + i * row})`}>
            <text className="bud-label" x={left - 8} y="14" textAnchor="end">{r.recording.replace('.jsonl.gz', '')}</text>
            <rect className={`bud-bar${r.within_budget ? '' : ' over'}`} x={left} y="3" height="18" width={Math.max(2, x(r.per_hour) - left)} rx="3" />
            <text className="bud-value" x={x(r.per_hour) + 6} y="16">{r.per_hour.toFixed(2)} / h</text>
          </g>
        ))}
        <line className="bud-line" x1={x(budget)} x2={x(budget)} y1={top - 10} y2={h - 14} />
        <text className="bud-cap" x={x(budget)} y="10" textAnchor="middle">Budget {fmtNumber(budget, 2)} / h</text>
      </svg>
    </figure>
  )
}

function Validation({ v }) {
  const fp = v.false_positives
  const hours = fp.recordings.reduce((s, r) => s + r.hours, 0)
  const tracks = fp.recordings.reduce((s, r) => s + r.tracks, 0)
  return (
    <>
      <section aria-labelledby="val-title" className="section">
        <h2 id="val-title">Validation: injected attacks</h2>
        <p className="muted">
          Each scenario is injected into a recording and the detections must fire on it. {v.all_scenarios_passed
            ? 'All scenarios passed with no collateral findings.' : 'Not every scenario passed; see the table.'}{' '}
          This is injected test data, not real air traffic. Open a scenario to watch it replay.
        </p>
        <ValidationTable rows={v.scenarios} />
        {v.gaps.length > 0 && (
          <>
            <h3 className="sub">Documented gaps</h3>
            <p className="muted small">Attacks the checks are known to miss. A gap passes only while nothing fires; if a detection starts catching it, it is promoted to a scenario with an expectation.</p>
            <ValidationTable rows={v.gaps} gap />
          </>
        )}
      </section>

      <section aria-labelledby="fp-title" className="section">
        <h2 id="fp-title">False-positive budget</h2>
        <p className="muted">
          Findings of severity {fp.min_severity} or higher on clean recordings, per hour, against a budget of{' '}
          <strong>{fmtNumber(fp.budget_per_hour, 2)} per hour</strong>. Verdict:{' '}
          <Chip tone={fp.verdict === 'within budget' ? 'good' : fp.verdict === 'over budget' ? 'sev-high' : 'neutral'}
            glyph={fp.verdict === 'within budget' ? '✓' : fp.verdict === 'over budget' ? '✕' : 'i'}>{fp.verdict}</Chip>
        </p>
        {fp.recordings.length > 0 && (
          <>
            <BudgetBars budget={fp.budget_per_hour} rows={fp.recordings} />
            <div className="card flush table-card">
              <table className="data-table stack-table fp-table">
                <caption className="sr-only">False-positive measurements per recording</caption>
                <thead>
                  <tr>
                    <th scope="col">Recording</th>
                    <th scope="col" className="num">Hours</th>
                    <th scope="col" className="num">Tracks</th>
                    <th scope="col" className="num">Counted</th>
                    <th scope="col" className="num">Per hour</th>
                    <th scope="col">Budget</th>
                    <th scope="col">Findings by detection</th>
                  </tr>
                </thead>
                <tbody>
                  {fp.recordings.map((r) => (
                    <tr key={r.recording}>
                      <th scope="row" className="mono">{r.recording}</th>
                      <td className="num" data-label="Hours">{fmtNumber(r.hours, 2)}</td>
                      <td className="num" data-label="Tracks">{fmtNumber(r.tracks)}</td>
                      <td className="num" data-label="Counted">{fmtNumber(r.counted)}</td>
                      <td className="num" data-label="Per hour">{fmtNumber(r.per_hour, 2)}</td>
                      <td data-label="Budget">{r.within_budget ? <Chip tone="good" glyph="✓">Within</Chip> : <Chip tone="sev-high" glyph="✕">Over</Chip>}</td>
                      <td data-label="Findings">
                        {r.counts.length
                          ? r.counts.map((c) => <span key={`${c.detection_id}${c.severity}`} className="mono small">{c.detection_id} {c.severity} ×{c.count} </span>)
                          : <span className="muted">none</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              Measured over {fmtNumber(hours, 2)} hours and {fmtNumber(tracks)} tracks in total. A short, quiet sample is a thin
              baseline: it shows the thresholds are not tuned to alert on ordinary traffic, not what a busy day looks like.
            </p>
          </>
        )}
      </section>
    </>
  )
}

export default function Detections() {
  usePageTitle('Detections')
  const { data, loading, error, reload } = useAsync(() => api.detections(), [])
  return (
    <div className="page">
      <PageHead eyebrow="Catalog and validation" title="Detections"
        lead="Ten plausibility checks on decoded ADS-B data. None proves spoofing on its own: each states why it can fire, what usually explains it, and how it was measured." />
      {loading && !data && <Loading label="Loading the detection catalog…" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <>
          <p className="muted small catalog-note">
            Catalog version {data.version}. Every threshold on this page is read from the detection catalog through the API;
            the detections and this page use the same numbers. A check that lacks its inputs is reported as not evaluated, never as clean.
          </p>
          <nav aria-label="Detections" className="det-jump">
            <ul>
              {data.detections.map((d) => <li key={d.id}><a href={`#${d.id}`} className="mono">{d.id}</a></li>)}
            </ul>
          </nav>
          <section aria-labelledby="cat-title" className="section">
            <h2 id="cat-title">Catalog</h2>
            <div className="det-list">{data.detections.map((d) => <DetectionCard key={d.id} d={d} />)}</div>
          </section>
          <Validation v={data.validation} />
        </>
      )}
    </div>
  )
}
