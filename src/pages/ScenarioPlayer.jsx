import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api.js'
import RadarMap, { BASE, Legend, project } from '../components/RadarMap.jsx'
import {
  Chip, ErrorState, Icon, InjectedTag, Loading, PageHead, SeverityChip, usePageTitle, useReducedMotion,
} from '../components/ui.jsx'
import { fitBox, viewForBox } from '../geo.js'
import { aircraftName, fmtDuration, fmtNumber, fmtTimeToDetect } from '../lib.js'
import {
  flaggedAt, frameAircraft, relTime, replayEvents, replayExtent, replayTrails, replayWindow,
} from '../replay.js'

const FRAME_MS = 450 // one replay frame per this long at 1x
const FRAME_MS_REDUCED = 1500 // under reduced motion: slower, one still picture at a time
const SPEEDS = [1, 2, 4]

async function load(name) {
  const [replay, validation] = await Promise.allSettled([api.replay(name), api.validation()])
  if (replay.status === 'rejected') throw replay.reason
  const results = validation.status === 'fulfilled' ? [...validation.value.scenarios, ...validation.value.gaps] : []
  return { replay: replay.value, result: results.find((r) => r.scenario === name) || null }
}

function Controls({ idx, win, playing, speed, reduced, rel, onPlay, onStep, onSeek, onRestart, onSpeed }) {
  const count = win.end - win.start + 1
  const pos = idx - win.start + 1
  const finished = idx >= win.end
  return (
    <div className="player-controls" role="group" aria-label="Replay controls">
      <div className="player-buttons">
        <button type="button" className="btn ghost small icon-only" onClick={onRestart} aria-label="Restart from the beginning">
          <Icon name="reset" size={16} />
        </button>
        <button type="button" className="btn secondary small icon-only" onClick={() => onStep(-1)} disabled={idx <= win.start}
          aria-label="Previous step">
          <Icon name="stepback" size={16} />
        </button>
        <button type="button" className="btn primary play-btn" onClick={onPlay}>
          <Icon name={playing ? 'pause' : 'play'} size={16} />
          {playing ? 'Pause' : finished ? 'Replay' : 'Play'}
        </button>
        <button type="button" className="btn secondary small icon-only" onClick={() => onStep(1)} disabled={finished}
          aria-label="Next step">
          <Icon name="step" size={16} />
        </button>
        <label className="speed">
          <span>Speed</span>
          <select value={speed} onChange={(e) => onSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </label>
      </div>
      <div className="player-scrub">
        <input type="range" min={win.start} max={win.end} step={1} value={idx} aria-label="Replay position"
          aria-valuetext={`Step ${pos} of ${count}, ${rel}`} onChange={(e) => onSeek(Number(e.target.value))} />
        <p className="small mono player-time" aria-hidden="true">{rel} · step {pos}/{count}</p>
      </div>
      {reduced && (
        <p className="small muted">
          Reduced motion is on, so the replay does not start by itself and moves one still picture at a time, slowly.
          Use Play, or the step buttons.
        </p>
      )}
    </div>
  )
}

function EventItem({ e, fired, current, injectT }) {
  return (
    <li className={`event${fired ? ' fired' : ''}${current ? ' current' : ''}`} data-detection={e.detection_id}>
      <div className="event-head">
        {fired ? <span className="status status-fired"><span className="status-dot" aria-hidden="true" />Fired</span> : <span className="status status-waiting"><span className="status-dot" aria-hidden="true" />Waiting</span>}
        <SeverityChip severity={e.severity} />
        <span className="mono small muted">{relTime(e.fireT, injectT)}{e.derived && <span className="validated"> (validated)</span>}</span>
      </div>
      <h3>
        <Link to={`/detections#${e.detection_id}`} className="mono">{e.detection_id}</Link>{' '}
        {e.title}
      </h3>
      {fired ? (
        <p className="small">
          {e.explanation} <span className="muted">Confidence {Number.isFinite(e.confidence) ? e.confidence.toFixed(2) : '—'}.</span>
        </p>
      ) : (
        <p className="small muted">Not fired yet. It fires when the checks have seen enough anomalous reports.</p>
      )}
    </li>
  )
}

function Player({ replay, result }) {
  const reduced = useReducedMotion()
  const win = useMemo(() => replayWindow(replay.frames), [replay])
  const [idx, setIdx] = useState(win.start)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [selected, setSelected] = useState(null)
  const started = useRef(false)

  // Start by itself unless the person prefers reduced motion; Pause is always available.
  useEffect(() => {
    if (started.current) return
    started.current = true
    if (!reduced) setPlaying(true)
  }, [reduced])

  useEffect(() => {
    if (!playing) return undefined
    const t = setTimeout(() => {
      if (idx >= win.end) setPlaying(false)
      else setIdx(idx + 1)
    }, (reduced ? FRAME_MS_REDUCED : FRAME_MS) / speed)
    return () => clearTimeout(t)
  }, [playing, idx, speed, win.end, reduced])

  const injectT = replay.inject_t
  const events = useMemo(() => replayEvents(replay.events, {
    injectT, expected: replay.scenario.expected, timeToDetect: result?.time_to_detect_s,
  }), [replay, injectT, result])
  const frame = replay.frames[idx]
  const flagged = useMemo(() => flaggedAt(events, frame.t, replay.addresses), [events, frame.t, replay.addresses])
  const aircraft = useMemo(() => frameAircraft(frame, flagged), [frame, flagged])
  const trails = useMemo(() => {
    const raw = replayTrails(replay.frames, win.start, idx)
    return Object.fromEntries(Object.entries(raw).map(([icao, points]) => [icao, { points, flagged: flagged.has(icao) }]))
  }, [replay.frames, win.start, idx, flagged])
  const initialView = useMemo(() => {
    const pts = replayExtent(replay.frames, win.start, win.end, replay.addresses).map(([la, lo]) => project(la, lo))
    const box = fitBox(pts, { pad: 0.3, minSpan: 14, aspect: BASE.w / BASE.h })
    return box ? viewForBox(box, BASE) : undefined
  }, [replay, win])

  const fired = events.filter((e) => e.fireT <= frame.t)
  const latest = fired.at(-1)
  const rel = relTime(frame.t, injectT) || `t ${fmtNumber(frame.t)} s`
  const sel = aircraft.find((a) => a.icao === selected)
  const finished = idx >= win.end

  const togglePlay = useCallback(() => {
    if (playing) setPlaying(false)
    else {
      if (idx >= win.end) setIdx(win.start)
      setPlaying(true)
    }
  }, [playing, idx, win])
  const step = (d) => { setPlaying(false); setIdx((i) => Math.max(win.start, Math.min(win.end, i + d))) }
  const seek = (v) => { setPlaying(false); setIdx(v) }
  const restart = () => { setIdx(win.start); setPlaying(false) }

  const sc = replay.scenario
  const ttd = result?.time_to_detect_s
  return (
    <div className="player-grid">
      <div className="map-col">
        <RadarMap aircraft={aircraft} selected={selected} onSelect={(icao) => setSelected((s) => (s === icao ? null : icao))}
          trails={trails} initialView={initialView} summary={`SCENARIO · ${rel}`} />
        <Controls idx={idx} win={win} playing={playing} speed={speed} reduced={reduced} rel={rel}
          onPlay={togglePlay} onStep={step} onSeek={seek} onRestart={restart} onSpeed={setSpeed} />
        <p className="sr-only" role="status" aria-live="polite">
          {latest ? `${latest.detection_id} fired at ${relTime(latest.fireT, injectT)}${latest.derived ? ' (validated time to detect)' : ''}: ${latest.title}.` : ''}
          {finished ? ' Replay finished.' : ''}
        </p>
        {sel && (
          <p className="small selected-line">
            <strong className="mono">{aircraftName(sel)}</strong> {sel.injected && <InjectedTag short />}
            {sel.flagged && <> <Chip tone="flagged">Flagged</Chip></>}
            {' '}{Number.isFinite(sel.alt) ? `${fmtNumber(sel.alt)} ft` : ''}{Number.isFinite(sel.gs) ? ` · ${fmtNumber(sel.gs)} kt` : ''}
          </p>
        )}
        <Legend />
      </div>

      <aside className="player-side" aria-label="Scenario details and detections">
        <section className="group" aria-labelledby="what-title">
          <h2 id="what-title">What is being replayed</h2>
          <p>{sc.simulates}</p>
          <dl className="kv">
            <div>
              <dt>Expected detection</dt>
              <dd>{sc.expected.length ? sc.expected.map((id) => <Link key={id} to={`/detections#${id}`} className="mono id-pill">{id}</Link>) : <span className="muted">none</span>}</dd>
            </div>
            <div><dt>Injected at</dt><dd className="mono">{relTime(injectT, injectT)} (after {fmtDuration(injectT)} of recorded traffic)</dd></div>
            {Number.isFinite(ttd) && <div><dt>Validated time to detect</dt><dd>{fmtTimeToDetect(ttd)} after injection</dd></div>}
          </dl>
          <p className="small muted">Source: {sc.reference}</p>
        </section>

        <section className="group" aria-labelledby="fire-title">
          <h2 id="fire-title">Detections firing</h2>
          {events.length === 0 ? (
            <p className="state small">
              {sc.gap
                ? 'Nothing fires. This is a documented gap: each step stays inside the kinematic limits, so single-receiver plausibility checks miss it.'
                : 'No detection fired for this replay.'}
            </p>
          ) : (
            <>
              <p className="small muted" aria-live="polite">{fired.length} of {events.length} fired by {rel}.</p>
              <ol className="events">
                {events.map((e, i) => (
                  <EventItem key={`${e.detection_id}${e.icao}${i}`} e={e} fired={e.fireT <= frame.t} injectT={injectT}
                    current={latest === e && !finished} />
                ))}
              </ol>
              <p className="small muted">
                The expected detection fires at its validated time to detect (the checks need more than one report);
                other findings fire at their first flagged report.
              </p>
            </>
          )}
        </section>
      </aside>
    </div>
  )
}

export default function ScenarioPlayer() {
  const { name = '' } = useParams()
  const [tick, setTick] = useState(0)
  const [state, setState] = useState({ loading: true, error: null, data: null })
  usePageTitle(state.data ? state.data.replay.scenario.title : 'Scenario')

  useEffect(() => {
    let live = true
    setState({ loading: true, error: null, data: null })
    load(name)
      .then((data) => live && setState({ loading: false, error: null, data }))
      .catch((error) => live && setState({ loading: false, error, data: null }))
    return () => { live = false }
  }, [name, tick])

  const { data, loading, error } = state
  return (
    <div className="page player-page">
      {loading && <Loading label="Loading the replay…" />}
      {error && (error.status === 404
        ? <div className="state"><h1 tabIndex={-1} className="h-inline">Scenario not found.</h1> <Link to="/scenarios">See the available scenarios</Link>.</div>
        : <><h1 tabIndex={-1} className="sr-only">Scenario replay</h1><ErrorState error={error} onRetry={() => setTick((t) => t + 1)} /></>)}
      {data && (
        <>
          <PageHead title={data.replay.scenario.title}>
            <InjectedTag label={data.replay.label} />
          </PageHead>
          <p className="injected-banner">{data.replay.notice}. This replay is injected test data produced by a documented attack scenario, not real air traffic.</p>
          <Player key={data.replay.scenario.id} replay={data.replay} result={data.result} />
        </>
      )}
    </div>
  )
}
