// Pure helpers for the scenario player, the detection catalog display and the report preview.
// No React, so Node's test runner can import them.

/** Seconds relative to the injection: "T+15 s", "T−20 s", "T+0 s". */
export function relTime(t, injectT) {
  if (!Number.isFinite(t) || !Number.isFinite(injectT)) return ''
  const d = Math.round(t - injectT)
  return `T${d < 0 ? '−' : '+'}${Math.abs(d)} s`
}

/**
 * The frames worth playing: from `margin` frames before the first one that holds an aircraft to
 * `margin` after the last, so the replay does not open on an empty map. Inclusive indices.
 */
export function replayWindow(frames, margin = 3) {
  const list = frames || []
  const used = list.map((f, i) => (f.aircraft?.length ? i : -1)).filter((i) => i >= 0)
  if (!used.length) return { start: 0, end: Math.max(0, list.length - 1) }
  return { start: Math.max(0, used[0] - margin), end: Math.min(list.length - 1, used.at(-1) + margin) }
}

/**
 * When each detection fires in the replay. The detection a scenario expects fires at the validated
 * time to detect after injection (the persistence rules need more than one report); any other
 * finding fires at its first flagged report. Events come back sorted by `fireT`.
 */
export function replayEvents(events, { injectT, expected = [], timeToDetect = null } = {}) {
  return (events || [])
    .map((e) => {
      const validated = expected.includes(e.detection_id) && Number.isFinite(timeToDetect) && Number.isFinite(injectT)
      const fireT = validated ? Math.max(e.t, injectT + timeToDetect) : e.t
      // `derived`: the time comes from the validated time to detect, not from a report in the replay.
      return { ...e, fireT, derived: validated && fireT !== e.t }
    })
    .sort((a, b) => a.fireT - b.fireT || String(a.detection_id).localeCompare(String(b.detection_id)))
}

/** Frame aircraft -> the shape the map draws; `flagged` is the set of addresses whose detection has fired. */
export function frameAircraft(frame, flagged) {
  return (frame?.aircraft || []).map((a) => ({
    icao: a.icao, callsign: a.callsign, lat: a.lat, lon: a.lon, alt: a.alt, gs: a.gs, track: a.track,
    on_ground: false, category: null, source: 'adsb', injected: Boolean(a.injected),
    flagged: flagged.has(a.icao), alert_ids: [],
  }))
}

/** Addresses flagged by the events fired by time `t`; a fleet event (no address) flags all `members`. */
export function flaggedAt(events, t, members = []) {
  const out = new Set()
  for (const e of events) {
    if (e.fireT > t) continue
    if (e.icao) out.add(e.icao)
    else members.forEach((m) => out.add(m))
  }
  return out
}

/** Trail points per aircraft over frames `from` to `index` inclusive. */
export function replayTrails(frames, from, index) {
  const trails = {}
  for (let i = from; i <= index && i < frames.length; i += 1) {
    for (const a of frames[i].aircraft) {
      if (!Number.isFinite(a.lat) || !Number.isFinite(a.lon)) continue
      ;(trails[a.icao] ||= []).push({ ts: frames[i].t, lat: a.lat, lon: a.lon })
    }
  }
  return trails
}

/** Every [lat, lon] the scenario's own aircraft report over the window, for fitting the map. */
export function replayExtent(frames, start, end, addresses) {
  const mine = new Set(addresses || [])
  const pts = []
  for (let i = start; i <= end; i += 1) {
    for (const a of frames[i]?.aircraft || []) {
      if ((mine.size === 0 || mine.has(a.icao) || a.injected) && Number.isFinite(a.lat) && Number.isFinite(a.lon)) pts.push([a.lat, a.lon])
    }
  }
  return pts
}

// -- detection catalog display ----------------------------------------------------------------------

/** "gs_ceiling" -> "gs ceiling". */
export function humanKey(key) {
  return String(key).replaceAll('_', ' ')
}

const num = (n) => Number(n).toLocaleString('en-US', { maximumFractionDigits: 3 })

/** A catalog threshold value as text: numbers with separators, lists joined, objects as key: value. */
export function fmtThreshold(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return num(value)
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (Array.isArray(value)) return value.map(fmtThreshold).join(', ')
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${k}: ${fmtThreshold(v)}`).join(', ')
  return String(value)
}

// -- the small markdown subset of the summary report ----------------------------------------------------

const ESCAPABLE = '\\`*_{}[]()#+-.!|~<>'

function unescapeMd(s) {
  let out = ''
  for (let i = 0; i < s.length; i += 1) {
    if (s[i] === '\\' && i + 1 < s.length && ESCAPABLE.includes(s[i + 1])) { out += s[i + 1]; i += 1 } else out += s[i]
  }
  return out
}

/** Inline markdown -> [{type: 'text' | 'strong' | 'em' | 'code', text}]; backslash escapes are removed. */
export function parseInline(text) {
  const out = []
  const re = /\*\*((?:\\.|[^*\\])+)\*\*|`([^`]+)`|(?<![\w\\])_((?:\\.|[^_\\])+)_(?![\w])/g
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push({ type: 'text', text: unescapeMd(text.slice(last, m.index)) })
    if (m[1] !== undefined) out.push({ type: 'strong', text: unescapeMd(m[1]) })
    else if (m[2] !== undefined) out.push({ type: 'code', text: m[2] })
    else out.push({ type: 'em', text: unescapeMd(m[3]) })
    last = re.lastIndex
  }
  if (last < text.length) out.push({ type: 'text', text: unescapeMd(text.slice(last)) })
  return out
}

function splitRow(line) {
  const body = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  const cells = []
  let cur = ''
  for (let i = 0; i < body.length; i += 1) {
    if (body[i] === '\\' && i + 1 < body.length) { cur += body[i] + body[i + 1]; i += 1 } else if (body[i] === '|') { cells.push(cur.trim()); cur = '' } else cur += body[i]
  }
  cells.push(cur.trim())
  return cells
}

const isRule = (line) => /^\s*\|?[\s:|-]*-[\s:|-]*$/.test(line || '') && (line || '').includes('|')

/**
 * Markdown -> blocks: heading {level, inline}, paragraph {inline}, list {items}, table {head, rows}.
 * Only the constructs the summary report uses; anything else is a paragraph. Never produces HTML.
 */
export function parseMarkdown(source) {
  const lines = String(source || '').replaceAll('\r\n', '\n').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const heading = /^(#{1,4})\s+(.*)$/.exec(line)
    if (!line.trim()) {
      i += 1
    } else if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, inline: parseInline(heading[2]) })
      i += 1
    } else if (line.trim().startsWith('|') && isRule(lines[i + 1])) {
      const head = splitRow(line).map(parseInline)
      i += 2
      const rows = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(splitRow(lines[i]).map(parseInline)); i += 1 }
      blocks.push({ type: 'table', head, rows })
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(parseInline(lines[i].replace(/^\s*[-*]\s+/, ''))); i += 1 }
      blocks.push({ type: 'list', items })
    } else {
      const para = []
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*]\s|\s*\|)/.test(lines[i])) { para.push(lines[i].trim()); i += 1 }
      if (!para.length) { para.push(line.trim()); i += 1 }
      blocks.push({ type: 'paragraph', inline: parseInline(para.join(' ')) })
    }
  }
  return blocks
}
