// Pure helpers and vocabulary, with no React, so Node's test runner can import them.

export const GITHUB_URL = 'https://github.com/AndrewPrice98/GhostTrack'
export const DISCLAIMER = 'Plausibility analysis of decoded ADS-B data; not a certified surveillance system.'
export const ATTRIBUTION = 'ADS-B data from adsb.lol, available under the Open Database License (ODbL) 1.0.'
export const INJECTED_LABEL = 'Injected test data'
export const INJECTED_NOTICE = 'Injected test data: not real air traffic. It comes from a documented attack scenario replayed into recorded traffic so the detections can be shown working.'
export const REFRESH_MS = 10_000
export const DEMO_CYCLE_S = 3600
export const FLAG_WINDOW_MIN = 10

// -- browser storage (always guarded: private mode and blocked storage must not break the page) --

export function readStore(store, key) {
  try { return window[store].getItem(key) } catch { return null }
}

export function writeStore(store, key, value) {
  try { window[store].setItem(key, value) } catch { /* not remembered */ }
}

export const MODE_KEY = 'ghosttrack.mode'

// -- vocabulary: every value has a text label (colour is never the only cue) ------------------

/** Position source of a report (readsb `type`, `mlat` and `tisb` fields). */
export const SOURCES = {
  adsb: { label: 'ADS-B', long: 'reported by the aircraft from its own GNSS' },
  mlat: { label: 'MLAT', long: 'computed by ground receivers (multilateration)' },
  tisb: { label: 'TIS-B', long: 'rebroadcast from ground radar' },
  other: { label: 'Other', long: 'other or unknown source' },
}

export function sourceOf(value) {
  return SOURCES[value] ? value : 'other'
}

/** ADS-B emitter categories (DECISIONS.md, "ADS-B emitter categories"). */
export const CATEGORIES = {
  A0: 'No category information', A1: 'Light', A2: 'Small', A3: 'Large', A4: 'High vortex (B757)',
  A5: 'Heavy', A6: 'High performance', A7: 'Rotorcraft',
  B0: 'No category information', B1: 'Glider', B2: 'Lighter-than-air', B3: 'Parachutist',
  B4: 'Ultralight', B6: 'Unmanned aircraft', B7: 'Space vehicle',
  C0: 'No category information', C1: 'Surface emergency vehicle', C2: 'Surface service vehicle',
  C3: 'Ground obstruction',
}

export function categoryLabel(code) {
  if (!code) return 'Not reported'
  return CATEGORIES[code] ? `${code} · ${CATEGORIES[code]}` : code
}

/** Detection ids and titles (backend/ghosttrack/detections/catalog.yml), for the alert filters. */
export const DETECTIONS = [
  ['GT-001', 'Impossible ground speed'],
  ['GT-002', 'Impossible vertical rate'],
  ['GT-003', 'Impossible turn or acceleration'],
  ['GT-004', 'Position jump (teleport)'],
  ['GT-005', 'Duplicate ICAO address'],
  ['GT-006', 'ICAO allocation mismatch'],
  ['GT-007', 'Baro / geometric altitude divergence'],
  ['GT-008', 'Emergency squawk without matching behaviour'],
  ['GT-009', 'Ghost flood'],
  ['GT-010', 'Integrity indicators drop to zero'],
]

export const SEVERITY = {
  high: { label: 'High', tone: 'bad', glyph: '▲', rank: 3 },
  medium: { label: 'Medium', tone: 'warn', glyph: '◆', rank: 2 },
  low: { label: 'Low', tone: 'low', glyph: '●', rank: 1 },
  info: { label: 'Info', tone: 'info', glyph: 'i', rank: 0 },
}

export const STATUS = {
  new: { label: 'New', tone: 'accent', glyph: '●' },
  info: { label: 'Informational', tone: 'info', glyph: 'i' },
  acknowledged: { label: 'Acknowledged', tone: 'neutral', glyph: '✓' },
  dismissed: { label: 'Dismissed', tone: 'muted', glyph: '–' },
}

/** Statuses an admin can set (the API accepts these three). */
export const TRIAGE_OPTIONS = [
  ['new', 'New', 'Not looked at yet.'],
  ['acknowledged', 'Acknowledged', 'Seen and being followed up.'],
  ['dismissed', 'Dismissed', 'Explained by a benign cause, or not worth following up.'],
]

/**
 * Evidence fields: a plain-language name and what the comparison means. `kind` says how the
 * observed value relates to the threshold: 'ceiling' (suspicious above it), 'floor' (suspicious
 * below it), 'band' (suspicious outside +/- the threshold), 'steady' (the behaviour stayed under
 * the threshold, i.e. it looked normal), 'match' (the two values should agree) or 'fact'.
 */
export const FIELDS = {
  gs_kt: { label: 'Ground speed', kind: 'ceiling', help: 'Reported speed over the ground, against the ceiling for the declared aircraft category.' },
  implied_speed_kt: { label: 'Speed implied by the position change', kind: 'ceiling', help: 'Distance between consecutive positions divided by the time between them.' },
  alternating_jump_kt: { label: 'Speed implied by jumping between two places', kind: 'ceiling', help: 'The same address reported alternately at two positions; this is the speed needed to make each jump (Mach 3 ceiling).' },
  simultaneous_separation_nm: { label: 'Distance between simultaneous reports', kind: 'ceiling', help: 'Two positions for one address at effectively the same time.' },
  vertical_rate_fpm: { label: 'Vertical rate', kind: 'ceiling', help: 'Reported climb or descent rate, against the ceiling for the category.' },
  implied_vertical_rate_fpm: { label: 'Vertical rate implied by the altitude change', kind: 'ceiling', help: 'Altitude change between reports divided by the time between them.' },
  acceleration_g: { label: 'Along-track acceleration', kind: 'ceiling', help: 'Change of ground speed between reports, in g.' },
  lateral_acceleration_g: { label: 'Turn (lateral) acceleration', kind: 'ceiling', help: 'Acceleration needed for the observed turn, against the load-factor limit for the category.' },
  alt_geom_minus_baro_ft: { label: 'Geometric minus barometric altitude', kind: 'band', help: 'GNSS altitude and pressure altitude normally agree within a band set by weather and geoid effects.' },
  new_addresses: { label: 'New addresses appearing together', kind: 'ceiling', help: 'Aircraft addresses first seen in the same small area and time window with near-identical motion.' },
  appearance_span_s: { label: 'Time span of their appearance', kind: 'floor', help: 'How close together in time the new addresses appeared.' },
  gs_range_kt: { label: 'Spread of ground speeds', kind: 'steady', help: 'Difference between the fastest and slowest ground speed in the window.' },
  altitude_range_ft: { label: 'Spread of altitudes', kind: 'steady', help: 'Difference between the highest and lowest altitude in the window.' },
  track_change_deg: { label: 'Change of track', kind: 'steady', help: 'How far the direction of travel changed in the window.' },
  squawk: { label: 'Transponder code (squawk)', kind: 'fact', help: '7500 hijack, 7600 radio failure, 7700 emergency.' },
  address_state: { label: 'State of the ICAO address block', kind: 'match', help: 'The country the 24-bit address is allocated to (ICAO Annex 10), against the country of the registration mark.' },
  icao: { label: 'ICAO address', kind: 'match', help: 'The 24-bit address, against the one the registration converts to.' },
  addresses: { label: 'Addresses involved', kind: 'fact', help: '' },
  nic: { label: 'Navigation integrity category (NIC)', kind: 'fact', help: 'Dropped to 0 while the other fields stayed smooth.' },
  nac_p: { label: 'Navigation accuracy category (NACp)', kind: 'fact', help: 'Dropped to 0 while the other fields stayed smooth.' },
}

export function fieldMeta(field) {
  return FIELDS[field] || { label: String(field || 'Value').replaceAll('_', ' '), kind: 'fact', help: '' }
}

// -- formatting ------------------------------------------------------------------------------

const pad = (n) => String(n).padStart(2, '0')

/** "14:05:09Z" (UTC, as in aviation). */
export function fmtTime(ts) {
  if (!Number.isFinite(ts)) return '—'
  const d = new Date(ts * 1000)
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "10 Nov 14:05Z". */
export function fmtDateTime(ts, { seconds = false } = {}) {
  if (!Number.isFinite(ts)) return '—'
  const d = new Date(ts * 1000)
  const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${seconds ? `:${pad(d.getUTCSeconds())}` : ''}Z`
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${time}`
}

/** "2 min ago", "3 h ago" relative to `now` (both epoch seconds). */
export function fmtAgo(ts, now) {
  if (!Number.isFinite(ts) || !Number.isFinite(now)) return ''
  const s = Math.max(0, Math.round(now - ts))
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—'
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: 0 })
}

export function fmtAlt(alt, onGround) {
  if (onGround) return 'Ground'
  return Number.isFinite(alt) ? `${fmtNumber(alt)} ft` : '—'
}

export function fmtTrack(track) {
  return Number.isFinite(track) ? `${String(((Math.round(track) % 360) + 360) % 360).padStart(3, '0')}°` : '—'
}

export function fmtCoord(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return '—'
  return `${Math.abs(lat).toFixed(3)}° ${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(3)}° ${lon >= 0 ? 'E' : 'W'}`
}

/** An evidence value with its unit: "1,350 kt", "+8,000 ft", "United States". */
export function fmtValue(value, unit = '', { signed = false } = {}) {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'number') {
    const digits = Math.abs(value) < 10 && !Number.isInteger(value) ? 2 : 0
    const s = fmtNumber(value, digits)
    return `${signed && value > 0 ? '+' : ''}${s}${unit ? ` ${unit}` : ''}`
  }
  return String(value)
}

export function plural(n, one, many = `${one}s`) {
  return `${fmtNumber(n)} ${n === 1 ? one : many}`
}

/** Callsign, else the hex address in upper case; "Unknown" when neither is known. */
export function aircraftName(a) {
  const cs = (a?.callsign || '').trim()
  if (cs) return cs
  return a?.icao ? a.icao.toUpperCase() : 'Unknown'
}

// -- evidence ----------------------------------------------------------------------------------

/**
 * Evidence entries -> rows for the "what failed" table, one per field (the strongest value of
 * that field), with a plain-language comparison sentence.
 */
export function evidenceRows(evidence) {
  const byField = new Map()
  for (const e of evidence || []) {
    const prev = byField.get(e.field)
    const stronger = prev && typeof e.observed === 'number' && typeof prev.observed === 'number'
      && Math.abs(e.observed) > Math.abs(prev.observed)
    if (!prev || stronger) byField.set(e.field, { ...e, count: (prev?.count || 0) + 1 })
    else prev.count += 1
  }
  return [...byField.values()].map((e) => {
    const meta = fieldMeta(e.field)
    const unit = e.unit || ''
    const observed = fmtValue(e.observed, unit, { signed: meta.kind === 'band' })
    const threshold = fmtValue(e.threshold, unit, { signed: false })
    const numeric = typeof e.observed === 'number' && typeof e.threshold === 'number' && e.threshold !== 0
    const ratio = numeric ? Math.abs(e.observed) / Math.abs(e.threshold) : null
    let comparison
    if (meta.kind === 'ceiling') comparison = numeric ? `${ratio.toFixed(1)}× the ${threshold} ceiling` : `ceiling ${threshold}`
    else if (meta.kind === 'band') comparison = `outside the ±${threshold} band`
    else if (meta.kind === 'floor') comparison = `below the ${threshold} floor`
    else if (meta.kind === 'steady') comparison = `steady: under ${threshold}`
    else if (meta.kind === 'match') comparison = e.threshold == null ? '' : `expected ${threshold}`
    else comparison = e.threshold == null ? '' : `reference ${threshold}`
    return {
      field: e.field, label: meta.label, help: meta.help, kind: meta.kind, unit,
      observed, threshold: e.threshold == null ? '—' : threshold, comparison, ratio, ts: e.ts, count: e.count,
    }
  })
}

/** Who an alert is about: the callsign / hex, or the number of addresses for a ghost flood. */
export function alertSubject(alert) {
  if (alert?.icao || alert?.callsign) return aircraftName(alert)
  const list = (alert?.evidence || []).find((e) => e.field === 'addresses')?.observed
  if (Array.isArray(list)) return `${list.length} addresses`
  return alert?.detection_id === 'GT-009' ? 'Several addresses' : 'Unknown aircraft'
}

// -- aircraft list: filter and sort ----------------------------------------------------------------

export const AIRCRAFT_SORTS = {
  name: { label: 'Aircraft', value: (a) => aircraftName(a).toLowerCase() },
  alt: { label: 'Altitude', value: (a) => (a.on_ground ? 0 : a.alt) },
  gs: { label: 'Ground speed', value: (a) => a.gs },
  track: { label: 'Track', value: (a) => a.track },
  source: { label: 'Source', value: (a) => sourceOf(a.source) },
}

/**
 * Sort aircraft by a column. Flagged aircraft always come first, then injected ones; within each
 * group the column order applies, missing values last, and the hex address breaks ties.
 */
export function sortAircraft(list, key = 'name', dir = 'asc') {
  const get = (AIRCRAFT_SORTS[key] || AIRCRAFT_SORTS.name).value
  const sign = dir === 'desc' ? -1 : 1
  const missing = (v) => v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v))
  return [...(list || [])].sort((a, b) => {
    if (Boolean(b.flagged) !== Boolean(a.flagged)) return a.flagged ? -1 : 1
    if (Boolean(b.injected) !== Boolean(a.injected)) return a.injected ? -1 : 1
    const va = get(a)
    const vb = get(b)
    if (missing(va) !== missing(vb)) return missing(va) ? 1 : -1
    if (!missing(va) && va !== vb) {
      const cmp = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb))
      if (cmp) return cmp * sign
    }
    return String(a.icao || '').localeCompare(String(b.icao || ''))
  })
}

/** Case-insensitive match on callsign or hex address. */
export function matchesQuery(item, query) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return true
  return [item?.callsign, item?.icao].some((v) => String(v || '').toLowerCase().includes(q))
}

export function filterAircraft(list, { query = '', flaggedOnly = false } = {}) {
  return (list || []).filter((a) => (!flaggedOnly || a.flagged) && matchesQuery(a, query))
}

// -- demo schedule -------------------------------------------------------------------------------

/**
 * The demo replays a one-hour cycle aligned to the clock, with scenarios injected at fixed offsets.
 * Returns { scenario, inSeconds } for the next scenario to start after `ts`, or null.
 */
export function nextScenario(scenarios, ts, cycle = DEMO_CYCLE_S) {
  const scheduled = (scenarios || []).filter((s) => Number.isFinite(s.demo_offset_s))
    .sort((a, b) => a.demo_offset_s - b.demo_offset_s)
  if (!scheduled.length || !Number.isFinite(ts)) return null
  const offset = ((ts % cycle) + cycle) % cycle
  const next = scheduled.find((s) => s.demo_offset_s > offset) || scheduled[0]
  const wait = next.demo_offset_s > offset ? next.demo_offset_s - offset : next.demo_offset_s + cycle - offset
  return { scenario: next, inSeconds: Math.round(wait) }
}

/** Whether a file of `bytes` is over the server's upload limit (`maxMb`, from /api/health); unknown limit: false. */
export function fileTooLarge(bytes, maxMb) {
  return Number.isFinite(bytes) && Number.isFinite(maxMb) && bytes > maxMb * 1024 * 1024
}

/** Text of the injected tag: the API's label as it is when one is given; "Injected" (short) otherwise. */
export function injectedTagText(label, short) {
  if (short) return label || 'Injected'
  return label || INJECTED_LABEL
}

/** Time to detect, the same text everywhere (player, detections table, scenario cards): whole seconds. */
export function fmtTimeToDetect(seconds) {
  if (!Number.isFinite(seconds)) return '—'
  return `${fmtNumber(Math.round(seconds))} s`
}

export function fmtDuration(seconds) {
  if (!Number.isFinite(seconds)) return ''
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`
  return `${Math.round(seconds / 60)} min`
}
