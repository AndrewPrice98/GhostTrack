import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  aircraftName, alertSubject, categoryLabel, evidenceRows, filterAircraft, fmtAgo, fmtAlt, fmtDateTime, fmtTime,
  fmtTrack, fmtValue, nextScenario, readStore, sortAircraft, sourceOf, writeStore,
} from './lib.js'

const fleet = [
  { icao: 'a1', callsign: 'SWA12', alt: 12000, gs: 300, track: 90, source: 'adsb', flagged: false, injected: false },
  { icao: 'a2', callsign: 'ALT1', alt: 20000, gs: 400, track: 45, source: 'adsb', flagged: true, injected: true },
  { icao: 'a3', callsign: null, alt: null, gs: 10, track: null, source: 'mlat', flagged: false, injected: false, on_ground: true },
  { icao: 'a4', callsign: 'UAL9', alt: 35000, gs: 480, track: 270, source: 'tisb', flagged: true, injected: false },
  { icao: 'a5', callsign: 'GHO2', alt: 30000, gs: 1350, track: 10, source: 'adsb', flagged: false, injected: true },
]
const ids = (list) => list.map((a) => a.icao)

test('sortAircraft puts flagged first, then injected, then the column order', () => {
  assert.deepEqual(ids(sortAircraft(fleet)), ['a2', 'a4', 'a5', 'a3', 'a1']) // a3 has no callsign: "A3"
  assert.deepEqual(ids(sortAircraft(fleet, 'alt', 'desc')), ['a2', 'a4', 'a5', 'a1', 'a3'])
  assert.deepEqual(ids(sortAircraft(fleet, 'gs', 'asc')), ['a2', 'a4', 'a5', 'a3', 'a1'])
})

test('sortAircraft puts missing values last in both directions and does not mutate', () => {
  const copy = [...fleet]
  const asc = ids(sortAircraft(fleet, 'track', 'asc'))
  const desc = ids(sortAircraft(fleet, 'track', 'desc'))
  assert.equal(asc.at(-1), 'a3')
  assert.equal(desc.at(-1), 'a3')
  assert.deepEqual(fleet, copy)
  assert.deepEqual(sortAircraft(undefined), [])
})

test('filterAircraft searches callsign and hex, case-insensitively, and can show flagged only', () => {
  assert.deepEqual(ids(filterAircraft(fleet, { query: 'swa' })), ['a1'])
  assert.deepEqual(ids(filterAircraft(fleet, { query: 'A3' })), ['a3'])
  assert.deepEqual(ids(filterAircraft(fleet, { query: '  ' })), ids(fleet))
  assert.deepEqual(ids(filterAircraft(fleet, { flaggedOnly: true })), ['a2', 'a4'])
  assert.deepEqual(ids(filterAircraft(fleet, { query: 'ual', flaggedOnly: true })), ['a4'])
})

test('names and labels', () => {
  assert.equal(aircraftName({ callsign: ' SWA12 ', icao: 'a1' }), 'SWA12')
  assert.equal(aircraftName({ callsign: '', icao: 'abc123' }), 'ABC123')
  assert.equal(aircraftName({}), 'Unknown')
  assert.equal(alertSubject({ icao: null, callsign: null, evidence: [{ field: 'addresses', observed: ['a', 'b', 'c'] }] }), '3 addresses')
  assert.equal(categoryLabel('A3'), 'A3 · Large')
  assert.equal(categoryLabel(null), 'Not reported')
  assert.equal(sourceOf('mlat'), 'mlat')
  assert.equal(sourceOf('weird'), 'other')
})

test('formatting: UTC times, altitude, track and values with units', () => {
  const ts = Date.UTC(2026, 10, 10, 14, 5, 9) / 1000
  assert.equal(fmtTime(ts), '14:05:09Z')
  assert.equal(fmtDateTime(ts), '10 Nov 14:05Z')
  assert.equal(fmtTime(undefined), '—')
  assert.equal(fmtAgo(100, 130), '30 s ago')
  assert.equal(fmtAgo(0, 7200), '2 h ago')
  assert.equal(fmtAlt(10875), '10,875 ft')
  assert.equal(fmtAlt(null, true), 'Ground')
  assert.equal(fmtAlt(null), '—')
  assert.equal(fmtTrack(5), '005°')
  assert.equal(fmtTrack(359.6), '000°')
  assert.equal(fmtValue(1350, 'kt'), '1,350 kt')
  assert.equal(fmtValue(8000, 'ft', { signed: true }), '+8,000 ft')
  assert.equal(fmtValue('United States'), 'United States')
  assert.equal(fmtValue(['a', 'b']), 'a, b')
  assert.equal(fmtValue(null), '—')
})

test('evidenceRows: one row per field with the strongest value and a plain comparison', () => {
  const rows = evidenceRows([
    { field: 'gs_kt', observed: 1200, threshold: 800, ts: 1, unit: 'kt' },
    { field: 'gs_kt', observed: 1350, threshold: 800, ts: 2, unit: 'kt' },
    { field: 'alt_geom_minus_baro_ft', observed: 8000, threshold: 3450, ts: 3, unit: 'ft' },
    { field: 'address_state', observed: 'United States', threshold: 'United Kingdom', ts: 4, unit: '' },
  ])
  assert.equal(rows.length, 3)
  const [gs, alt, state] = rows
  assert.equal(gs.label, 'Ground speed')
  assert.equal(gs.observed, '1,350 kt')
  assert.equal(gs.threshold, '800 kt')
  assert.equal(gs.comparison, '1.7× the 800 kt ceiling')
  assert.equal(gs.count, 2)
  assert.equal(gs.ts, 2)
  assert.equal(alt.observed, '+8,000 ft')
  assert.equal(alt.comparison, 'outside the ±3,450 ft band')
  assert.equal(state.comparison, 'expected United Kingdom')
  assert.deepEqual(evidenceRows(undefined), [])
})

test('nextScenario finds the next injected scenario in the hourly demo cycle', () => {
  const scenarios = [
    { id: 'b', demo_offset_s: 720 }, { id: 'a', demo_offset_s: 300 }, { id: 'gap', demo_offset_s: null },
  ]
  const hour = 1794312000 // a whole hour
  assert.deepEqual(nextScenario(scenarios, hour + 100), { scenario: scenarios[1], inSeconds: 200 })
  assert.deepEqual(nextScenario(scenarios, hour + 400), { scenario: scenarios[0], inSeconds: 320 })
  // after the last one, wrap to the first of the next hour
  assert.deepEqual(nextScenario(scenarios, hour + 3000), { scenario: scenarios[1], inSeconds: 900 })
  assert.equal(nextScenario([], hour), null)
  assert.equal(nextScenario(scenarios, undefined), null)
})

test('browser storage helpers never throw when storage is missing or blocked', () => {
  const had = globalThis.window
  try {
    globalThis.window = undefined // no window at all
    assert.equal(readStore('localStorage', 'k'), null)
    assert.doesNotThrow(() => writeStore('localStorage', 'k', '1'))
    globalThis.window = { localStorage: { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } } }
    assert.equal(readStore('localStorage', 'k'), null)
    assert.doesNotThrow(() => writeStore('localStorage', 'k', '1'))
    const mem = new Map()
    globalThis.window = { localStorage: { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) } }
    writeStore('localStorage', 'k', '1')
    assert.equal(readStore('localStorage', 'k'), '1')
  } finally {
    globalThis.window = had
  }
})
